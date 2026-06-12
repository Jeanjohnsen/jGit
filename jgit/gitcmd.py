"""The seam between jGit and the real git binary.

Every call to git goes through here. Two rules, both load-bearing:

  1. Arguments are always a list, never a shell string. We never pass
     shell=True. A branch named '; rm -rf ~' is just a weird branch name,
     not a command.
  2. Passthrough inherits the real stdin/stdout/stderr so interactive git
     (rebase -i, an editor for a commit message, add -p, the pager, colour)
     behaves exactly as if you'd typed `git` yourself, and git's exit code
     is handed straight back.

State readers (current_branch, ahead_behind, ...) capture output instead,
and are deliberately forgiving: a missing upstream or an empty repo returns
a tidy "nothing" rather than raising.
"""

import os
import shutil
import subprocess
import sys


class GitMissing(Exception):
    pass


def tool_path(name):
    path = shutil.which(name)
    if not path:
        where = "https://git-scm.com" if name == "git" else "https://cli.github.com"
        raise GitMissing(
            f"{name} was not found on your PATH. jGit drives {name} for this - "
            f"install it from {where}"
        )
    return path


def git_path():
    return tool_path("git")


def passthrough(args, exe="git"):
    """Run `<exe> <args>` wired straight to the terminal. Returns its exit
    code. Use for anything jGit doesn't specialise - the user gets the real
    git experience, editors and pagers included. `exe` lets us also drive
    'gh' for pull-request commands."""
    # Flush our own buffered output first so jGit's notes and git's output
    # appear in the order they happened, even when stdout is a pipe.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (ValueError, OSError):
            pass
    try:
        proc = subprocess.run([tool_path(exe), *args])
    except GitMissing as e:
        print(f"fatal: {e}", file=sys.stderr)
        return 1
    return proc.returncode


def run(args, check=False):
    """Run git for its side effects, letting its output reach the terminal,
    but return the exit code (and optionally raise on failure)."""
    code = passthrough(args)
    if check and code != 0:
        raise subprocess.CalledProcessError(code, ["git", *args])
    return code


def capture(args):
    """Run git and capture its output. Returns (exit_code, stdout, stderr),
    both streams stripped. Never raises on a non-zero exit - callers decide
    what a failure means."""
    try:
        proc = subprocess.run(
            [git_path(), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except GitMissing as e:
        return 1, "", str(e)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def ok(args):
    """True if `git <args>` exits 0. For yes/no probes."""
    return capture(args)[0] == 0


def out(args, default=""):
    """stdout of `git <args>`, or default if it failed."""
    code, stdout, _ = capture(args)
    return stdout if code == 0 else default


# --- repository state, all read-only and failure-tolerant ---------------

def has_tool(name):
    return shutil.which(name) is not None


def toplevel():
    """Absolute path to the working-tree root, or "" if not in a git repo."""
    return out(["rev-parse", "--show-toplevel"])


def is_inside_work_tree():
    return out(["rev-parse", "--is-inside-work-tree"]) == "true"


def git_dir():
    # --absolute-git-dir so callers can os.path.join safely from any cwd.
    return out(["rev-parse", "--absolute-git-dir"], default=".git")


def has_commits():
    return ok(["rev-parse", "--verify", "--quiet", "HEAD"])


def current_branch():
    """The checked-out branch name, or "" when HEAD is detached."""
    return out(["branch", "--show-current"])


def short_head():
    return out(["rev-parse", "--short", "HEAD"])


def upstream():
    """The tracking branch (e.g. 'origin/main'), or "" if none is set."""
    return out(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])


def ahead_behind(ref="@{u}"):
    """(ahead, behind) relative to ref. (0, 0) if ref is unknown."""
    code, stdout, _ = capture(["rev-list", "--left-right", "--count", f"{ref}...HEAD"])
    if code != 0 or "\t" not in stdout:
        return 0, 0
    behind, ahead = stdout.split("\t")
    return int(ahead), int(behind)


def is_dirty():
    return bool(out(["status", "--porcelain"]))


def remotes():
    listing = out(["remote"])
    return listing.split("\n") if listing else []


def default_remote():
    rs = remotes()
    if not rs:
        return ""
    return "origin" if "origin" in rs else rs[0]


def default_branch():
    """The remote's default branch name (without remote prefix), e.g. 'main'.
    Falls back to whatever local main/master exists, else 'main'."""
    head = out(["symbolic-ref", "--short", f"refs/remotes/{default_remote()}/HEAD"])
    if head and "/" in head:
        return head.split("/", 1)[1]
    for name in ("main", "master"):
        if ok(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"]):
            return name
    return "main"


def remote_branch_exists(remote, branch):
    return ok(["ls-remote", "--exit-code", "--heads", remote, branch])


def last_reflog_action():
    """The git operation at HEAD@{0}, e.g. 'commit: add tests', 'merge x',
    'rebase (finish)', 'reset: moving to HEAD~1', 'pull', or "" if no reflog."""
    return out(["reflog", "-n", "1", "--format=%gs"])


def reflog(n=10):
    listing = out(["reflog", "-n", str(n), "--format=%h %gd %gs"])
    return listing.split("\n") if listing else []


def stash_count():
    listing = out(["stash", "list"])
    return len(listing.split("\n")) if listing else 0


def subject(ref):
    return out(["log", "-1", "--format=%s", ref])


def in_progress():
    """Name of an interrupted operation ('rebase', 'merge', 'cherry-pick',
    'revert'), or "" if the tree is calm."""
    g = git_dir()
    if os.path.exists(os.path.join(g, "rebase-merge")) or os.path.exists(
        os.path.join(g, "rebase-apply")
    ):
        return "rebase"
    if os.path.exists(os.path.join(g, "MERGE_HEAD")):
        return "merge"
    if os.path.exists(os.path.join(g, "CHERRY_PICK_HEAD")):
        return "cherry-pick"
    if os.path.exists(os.path.join(g, "REVERT_HEAD")):
        return "revert"
    return ""
