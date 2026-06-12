"""Tree and working-directory comparison.

Everything works on plain {path: bytes} dicts, so a commit and the working
directory compare through the same code path. Fine at the scale jgit is
meant for; a real VCS would compare object ids instead of contents.
"""

import difflib
import os

from . import base
from . import data


def commit_contents(oid):
    if not oid:
        return {}
    tree = base.get_tree(base.get_commit(oid).tree)
    return {path: data.get_object(blob) for path, blob in tree.items()}


def working_contents():
    result = {}
    for root, dirnames, filenames in os.walk("."):
        for filename in filenames:
            path = os.path.relpath(f"{root}/{filename}").replace("\\", "/")
            if base.is_ignored(path) or not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                result[path] = f.read()
        dirnames[:] = [d for d in dirnames if not base.is_ignored(f"{root}/{d}")]
    return result


def iter_changes(d_from, d_to):
    for path in sorted(set(d_from) | set(d_to)):
        if d_from.get(path) == d_to.get(path):
            continue
        if path not in d_from:
            action = "new file"
        elif path not in d_to:
            action = "deleted"
        else:
            action = "modified"
        yield path, action


def _is_binary(content):
    return b"\x00" in content[:8000]


def diff_contents(d_from, d_to):
    output = []
    for path, action in iter_changes(d_from, d_to):
        a = d_from.get(path, b"")
        b = d_to.get(path, b"")
        if _is_binary(a) or _is_binary(b):
            output.append(f"Binary files a/{path} and b/{path} differ ({action})\n")
            continue
        lines = difflib.unified_diff(
            a.decode("utf-8", errors="replace").splitlines(keepends=True),
            b.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        output.append("".join(lines))
    return "".join(output)
