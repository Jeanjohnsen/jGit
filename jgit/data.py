import hashlib
import os

from collections import namedtuple


# The on-disk directory. Defaults to ".git" but the CLI may switch it to
# ".jgit" (via --jgit or the JGIT_DIR env var) before anything touches disk.
GIT_DIR = ".git"

# Dropped inside GIT_DIR on init so jGit can tell its own repositories apart
# from a real Git repo that also lives in ".git". Real Git never creates it,
# which is the whole safety guarantee: no marker, hands off.
MARKER = "jgit-format"
FORMAT_VERSION = "2"

RefValue = namedtuple("RefValue", ["symbolic", "value"])


def init():
    os.makedirs(GIT_DIR)
    os.makedirs(f"{GIT_DIR}/objects")
    with open(f"{GIT_DIR}/{MARKER}", "w", encoding="utf-8") as f:
        f.write(FORMAT_VERSION + "\n")


def update_ref(ref, value, deref=True):
    ref = get_ref_internal(ref, deref)[0]

    assert value.value
    if value.symbolic:
        value = f"ref: {value.value}"
    else:
        value = value.value

    ref_path = f"{GIT_DIR}/{ref}"
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    with open(ref_path, "w") as f:
        f.write(value)


def delete_ref(ref, deref=True):
    ref = get_ref_internal(ref, deref)[0]
    ref_path = f"{GIT_DIR}/{ref}"
    if os.path.isfile(ref_path):
        os.remove(ref_path)


def get_ref(ref, deref=True):
    return get_ref_internal(ref, deref)[1]


def get_ref_internal(ref, deref=True):
    ref_path = f"{GIT_DIR}/{ref}"
    value = None
    if os.path.isfile(ref_path):
        with open(ref_path) as f:
            value = f.read().strip()

    symbolic = bool(value) and value.startswith("ref: ")

    if symbolic:
        value = value.split(":", 1)[1].strip()
        if deref:
            return get_ref_internal(value, deref=True)

    return ref, RefValue(symbolic=symbolic, value=value)


def iter_refs(prefix="", deref=True):
    refs = ["HEAD"]

    for root, _, filenames in os.walk(f"{GIT_DIR}/refs/"):
        # os.walk hands back OS-specific separators; refs always use "/"
        root = os.path.relpath(root, GIT_DIR).replace("\\", "/")
        refs.extend(f"{root}/{name}" for name in filenames)

    for refname in refs:
        if not refname.startswith(prefix):
            continue
        ref = get_ref(refname, deref=deref)
        if ref.value:
            yield refname, ref


def resolve_prefix(prefix):
    """Expand an abbreviated object id. None if missing or ambiguous."""
    try:
        matches = [o for o in os.listdir(f"{GIT_DIR}/objects") if o.startswith(prefix)]
    except FileNotFoundError:
        return None
    return matches[0] if len(matches) == 1 else None


def object_id(data, type_="blob"):
    obj = type_.encode() + b"\x00" + data
    return hashlib.sha1(obj).hexdigest()


def hash_object(data, type_="blob"):
    obj = type_.encode() + b"\x00" + data
    oid = hashlib.sha1(obj).hexdigest()
    with open(f"{GIT_DIR}/objects/{oid}", "wb") as out:
        out.write(obj)
    return oid


def get_object(oid, expected="blob"):
    with open(f"{GIT_DIR}/objects/{oid}", "rb") as f:
        obj = f.read()

    type_, _, content = obj.partition(b"\x00")
    type_ = type_.decode()

    if expected is not None:
        assert type_ == expected, f"Expected {expected}, got {type_}"
    return content
