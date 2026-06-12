import fnmatch
import getpass
import itertools
import operator
import os
import re
import string
import time

from collections import deque, namedtuple
from . import config
from . import data


def init():
    data.init()
    data.update_ref("HEAD", data.RefValue(symbolic=True, value="refs/heads/main"))


def write_tree(directory="."):
    entries = []
    with os.scandir(directory) as it:
        for entry in it:
            full = f"{directory}/{entry.name}"
            if is_ignored(full):
                continue

            if entry.is_file(follow_symlinks=False):
                type_ = "blob"
                with open(full, "rb") as f:
                    oid = data.hash_object(f.read())
            elif entry.is_dir(follow_symlinks=False):
                type_ = "tree"
                oid = write_tree(full)
            entries.append((entry.name, oid, type_))

    tree = "".join(f"{type_} {oid} {name}\n" for name, oid, type_ in sorted(entries))
    return data.hash_object(tree.encode(), "tree")


def _iter_tree_entries(oid):
    if not oid:
        return
    tree = data.get_object(oid, "tree")
    for entry in tree.decode().splitlines():
        type_, oid, name = entry.split(" ", 2)
        yield type_, oid, name


def get_tree(oid, base_path=""):
    result = {}
    for type_, oid, name in _iter_tree_entries(oid):
        assert "/" not in name
        assert name not in ("..", ".")
        path = base_path + name
        if type_ == "blob":
            result[path] = oid
        elif type_ == "tree":
            result.update(get_tree(oid, f"{path}/"))
        else:
            assert False, f"Unknown tree entry {type_}"
    return result


def _empty_current_directory():
    for root, dirnames, filenames in os.walk(".", topdown=False):
        for filename in filenames:
            path = os.path.relpath(f"{root}/{filename}")
            if is_ignored(path) or not os.path.isfile(path):
                continue
            os.remove(path)
        for dirname in dirnames:
            path = os.path.relpath(f"{root}/{dirname}")
            if is_ignored(path):
                continue
            try:
                os.rmdir(path)
            except (FileNotFoundError, OSError):
                pass


def read_tree(tree_oid):
    _empty_current_directory()
    for path, oid in get_tree(tree_oid, base_path="./").items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data.get_object(oid))


def get_author():
    name = config.get("user", "name")
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _make_commit(tree, parent, message):
    commit = f"tree {tree}\n"
    if parent:
        commit += f"parent {parent}\n"
    commit += f"author {get_author()} {int(time.time())}\n"
    commit += "\n"
    commit += f"{message}\n"
    return data.hash_object(commit.encode(), "commit")


def commit(message):
    tree = write_tree()
    HEAD = data.get_ref("HEAD").value

    if HEAD and get_commit(HEAD).tree == tree:
        return None  # nothing changed since HEAD

    oid = _make_commit(tree, HEAD, message)
    data.update_ref("HEAD", data.RefValue(symbolic=False, value=oid))
    return oid


def commit_amend(message=None):
    HEAD = data.get_ref("HEAD").value
    old = get_commit(HEAD)
    oid = _make_commit(write_tree(), old.parent, message or old.message)
    data.update_ref("HEAD", data.RefValue(symbolic=False, value=oid))
    return oid


def checkout(name):
    oid = get_oid(name)
    commit = get_commit(oid)
    read_tree(commit.tree)

    prev = data.get_ref("HEAD", deref=False)

    if is_branch(name):
        HEAD = data.RefValue(symbolic=True, value=f"refs/heads/{name}")
    else:
        HEAD = data.RefValue(symbolic=False, value=oid)

    data.update_ref("HEAD", HEAD, deref=False)
    if prev.value:
        data.update_ref("PREV_HEAD", prev, deref=False)


def previous_head_name():
    """What 'checkout -' should go back to: a branch name or an oid."""
    prev = data.get_ref("PREV_HEAD", deref=False)
    if not prev.value:
        return None
    if prev.symbolic and prev.value.startswith("refs/heads/"):
        return prev.value[len("refs/heads/"):]
    return prev.value


def reset(oid, hard=False):
    data.update_ref("HEAD", data.RefValue(symbolic=False, value=oid))
    if hard:
        read_tree(get_commit(oid).tree)


def restore(paths, oid):
    """Restore files (or whole directories) from a commit. Returns the
    restored paths; raises KeyError for a path the commit doesn't have."""
    tree = get_tree(get_commit(oid).tree)
    restored = []
    for path in paths:
        norm = os.path.normpath(path).replace("\\", "/")
        matches = [t for t in tree if t == norm or t.startswith(f"{norm}/")]
        if not matches:
            raise KeyError(path)
        for t in matches:
            dirname = os.path.dirname(t)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(t, "wb") as f:
                f.write(data.get_object(tree[t]))
            restored.append(t)
    return restored


def create_tag(name, oid):
    data.update_ref(f"refs/tags/{name}", data.RefValue(symbolic=False, value=oid))


def delete_tag(name):
    data.delete_ref(f"refs/tags/{name}", deref=False)


def iter_tags():
    for refname, _ in data.iter_refs("refs/tags/"):
        yield refname[len("refs/tags/"):]


Commit = namedtuple("Commit", ["tree", "parent", "author", "time", "message"])


def get_commit(oid):
    parent = None
    author = None
    timestamp = None

    commit = data.get_object(oid, "commit").decode()
    lines = iter(commit.splitlines())
    for line in itertools.takewhile(operator.truth, lines):
        key, value = line.split(" ", 1)
        if key == "tree":
            tree = value
        elif key == "parent":
            parent = value
        elif key == "author":
            author, _, ts = value.rpartition(" ")
            try:
                timestamp = int(ts)
            except ValueError:
                author, timestamp = value, None
        # unknown headers are skipped so old and new commits coexist

    message = "\n".join(lines)
    return Commit(tree=tree, parent=parent, author=author, time=timestamp, message=message)


def create_branch(name, oid):
    data.update_ref(f"refs/heads/{name}", data.RefValue(symbolic=False, value=oid))


def delete_branch(name):
    data.delete_ref(f"refs/heads/{name}", deref=False)


def is_branch(branch):
    return data.get_ref(f"refs/heads/{branch}").value is not None


def iter_branches():
    for refname, _ in data.iter_refs("refs/heads/"):
        yield refname[len("refs/heads/"):]


def iter_commits_and_parents(oids):
    oids = deque(oids)
    visited = set()

    while oids:
        oid = oids.popleft()
        if not oid or oid in visited:
            continue
        visited.add(oid)
        yield oid

        commit = get_commit(oid)
        oids.appendleft(commit.parent)


def _resolve_name(name):
    if name == "@":
        name = "HEAD"

    refs_to_try = [
        f"{name}",
        f"refs/{name}",
        f"refs/tags/{name}",
        f"refs/heads/{name}",
    ]

    for ref in refs_to_try:
        if data.get_ref(ref, deref=False).value:
            return data.get_ref(ref).value

    is_hex = all(char in string.hexdigits for char in name)
    if len(name) == 40 and is_hex:
        return name
    if 4 <= len(name) < 40 and is_hex:
        return data.resolve_prefix(name.lower())


def get_oid(name):
    """Resolve a name to an oid. Understands HEAD, @, branches, tags, full
    hashes, and the ~N / ^ ancestry suffixes (e.g. @~2, main^)."""
    match = re.fullmatch(r"(.+?)((?:~\d*|\^)*)", name)
    if not match:
        return None

    oid = _resolve_name(match.group(1))

    for op in re.findall(r"~\d*|\^", match.group(2)):
        steps = 1 if op in ("^", "~") else int(op[1:])
        for _ in range(steps):
            if not oid:
                return None
            oid = get_commit(oid).parent

    return oid


def get_branch_name():
    HEAD = data.get_ref("HEAD", deref=False)
    if not HEAD.symbolic:
        return None
    HEAD = HEAD.value
    assert HEAD.startswith("refs/heads/")
    return HEAD[len("refs/heads/"):]


_ignore_patterns = None


def _load_ignore_patterns():
    global _ignore_patterns
    if _ignore_patterns is None:
        _ignore_patterns = []
        # .gitignore is the natural name when jgit lives in .git; .jgitignore
        # is honoured too so old repos and side-by-side setups keep working.
        for fname in (".gitignore", ".jgitignore"):
            if os.path.isfile(fname):
                with open(fname, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip().rstrip("/")
                        if line and not line.startswith("#"):
                            _ignore_patterns.append(line)
    return _ignore_patterns


def is_ignored(path):
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".jgit" in parts or ".git" in parts:
        return True

    rel = "/".join(parts)
    for pattern in _load_ignore_patterns():
        if fnmatch.fnmatch(rel, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False
