"""Finding the repository, and refusing to touch a real Git one.

This is the safety-critical core. jGit's object format is not Git's, so it
must never read or write a real `.git`. The whole guarantee rests on a marker
file jGit drops on init (see data.MARKER) plus a check for tell-tale real-Git
artifacts. Everything that decides "is this directory mine to write to?" lives
here, on purpose, so it is small enough to audit in one sitting.
"""

import os
import sys

from collections import namedtuple

from . import data
from . import gitcmd

# Commands that work without being inside a repository.
NO_REPO_COMMANDS = {"init", "alias", "shell-init"}

REAL_GIT_MESSAGE = (
    "this looks like a real Git repository (its .git has no jGit marker). "
    "jGit won't operate on it - jGit's object format is not Git's, so writing "
    "here would corrupt your Git data. Work in another folder, or run "
    "'jgit init --jgit' to keep jGit's data in a separate .jgit directory."
)

# Artifacts that a real 'git init' creates and jgit never does. If any of
# these sit in a .git, real Git has been here - hands off, even if jgit's
# marker is also present (e.g. someone ran 'git init' in a jgit folder).
_REAL_GIT_ARTIFACTS = ("objects/pack", "hooks", "description", "index")


def fatal(message):
    print(f"fatal: {message}", file=sys.stderr)
    sys.exit(1)


def forced_dir_name():
    """The JGIT_DIR override, validated. Must be a plain directory name like
    .git or .jgit - never a path, or os.path.join would silently escape the
    repo and point jgit at the wrong place."""
    forced = os.environ.get("JGIT_DIR")
    if not forced:
        return None
    if os.path.isabs(forced) or "/" in forced or "\\" in forced:
        fatal("JGIT_DIR must be a plain directory name (like .git or .jgit), not a path")
    return forced


def candidate_dir_names():
    """Directory names to look for at each level, in priority order. JGIT_DIR
    pins it. Otherwise .jgit wins over .git at the same level, so jgit can sit
    beside a real Git repo (.git real + .jgit ours) without colliding."""
    forced = forced_dir_name()
    return (forced,) if forced else (".jgit", ".git")


def is_real_git(repo_dir_path):
    """True if this .git belongs to real Git, not jgit. A .jgit dir is always
    ours; a .git is ours only if it carries jgit's marker AND shows no sign of
    real Git having written there."""
    if os.path.basename(repo_dir_path) != ".git":
        return False
    if not os.path.isfile(os.path.join(repo_dir_path, data.MARKER)):
        return True
    return any(os.path.exists(os.path.join(repo_dir_path, a)) for a in _REAL_GIT_ARTIFACTS)


def find_repo():
    """Walk up from the cwd to the nearest jgit repo. Returns (root, dirname)
    or None. A real Git .git met along the way is skipped, not returned, so a
    jgit repo one level up still wins over a nested real-git checkout."""
    path = os.getcwd()
    while True:
        for name in candidate_dir_names():
            candidate = os.path.join(path, name)
            if os.path.isdir(candidate) and not is_real_git(candidate):
                return path, name
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def nearest_real_git():
    """The closest real Git .git above the cwd, if any. Only used to give a
    clearer error when no jgit repo was found."""
    path = os.getcwd()
    while True:
        candidate = os.path.join(path, ".git")
        if os.path.isdir(candidate) and is_real_git(candidate):
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def enter_repo():
    """Find the repo, chdir to its root, point data at it. find_repo already
    skips real Git, so anything it returns is safe to write to."""
    found = find_repo()
    if found:
        root, dirname = found
        os.chdir(root)
        data.GIT_DIR = dirname
        return
    if nearest_real_git():
        fatal(REAL_GIT_MESSAGE)
    fatal("not a jgit repository (no .git or .jgit here or in any parent). "
          "Run 'jgit init' first.")


# How jGit should behave here:
#   native - a .jgit (or jgit-marked .git): jGit's own from-scratch VCS
#   git    - a real Git repo: drive the git binary, with smart commands on top
#   none   - not in any repository
Mode = namedtuple("Mode", ["kind", "root", "dirname"])


def detect_mode():
    """Decide which world we're in. A native jGit repo wins over a real Git
    one at the same place (jGit prefers its own .jgit), so the two can sit
    side by side; otherwise a real Git working tree means git mode."""
    found = find_repo()
    if found:
        root, dirname = found
        return Mode("native", root, dirname)

    top = gitcmd.toplevel()
    if top:
        return Mode("git", top, ".git")

    return Mode("none", None, None)
