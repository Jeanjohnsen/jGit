"""The commands that make jGit smarter than plain git + aliases.

Each handler takes the arguments after the command word and returns an exit
code (0 = success), so scripts and CI see the truth. Anything genuinely
destructive routes through safety.confirm, which refuses rather than prompts
when there's no terminal. None of this writes git's object store directly -
it all drives the git binary through gitcmd.
"""

import re
import sys
import webbrowser

from . import gitcmd
from . import safety


_STATUS_WORDS = {
    "M": "modified", "A": "new file", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "conflicted", "T": "typechange",
}


def _require_branch():
    branch = gitcmd.current_branch()
    if not branch:
        print("fatal: HEAD is detached - check out a branch first", file=sys.stderr)
    return branch


def status(rest):
    branch = gitcmd.current_branch()
    if branch:
        print(f"On branch {branch}")
    elif gitcmd.has_commits():
        print(f"HEAD detached at {gitcmd.short_head()}  {gitcmd.subject('HEAD')}")
    else:
        print("No commits yet")

    op = gitcmd.in_progress()
    if op:
        print(f"  ({op} in progress - resolve, then 'git {op} --continue' or 'jgit undo')")

    if branch:
        up = gitcmd.upstream()
        if up:
            ahead, behind = gitcmd.ahead_behind()
            if ahead and behind:
                print(f"Diverged from {up}: {ahead} ahead, {behind} behind - 'jgit sync' to reconcile.")
            elif ahead:
                print(f"Ahead of {up} by {ahead} - publish with 'jgit ps'.")
            elif behind:
                print(f"Behind {up} by {behind} - update with 'jgit sync'.")
            else:
                print(f"Up to date with {up}.")
        elif gitcmd.has_commits():
            print("No upstream set - first push with 'jgit ps'.")

    staged, unstaged, untracked = [], [], []
    for line in gitcmd.out(["status", "--porcelain"]).splitlines():
        if len(line) < 3:
            continue
        x, y, path = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x not in " ?":
            staged.append((x, path))
        if y not in " ?":
            unstaged.append((y, path))

    def show(title, items):
        print(f"\n{title}")
        for code, path in items:
            print(f"    {_STATUS_WORDS.get(code, code) + ':':<12} {path}")

    if staged:
        show("Staged for commit:", staged)
    if unstaged:
        show("Not staged:", unstaged)
    if untracked:
        print("\nUntracked:")
        for path in untracked:
            print(f"    {path}")
    if not (staged or unstaged or untracked) and gitcmd.has_commits():
        print("\nnothing to commit, working tree clean")
    return 0


def push(rest):
    yes, rest = safety.wants_yes(rest)
    branch = _require_branch()
    if not branch:
        return 1

    if not gitcmd.remotes():
        print("fatal: no remote configured. Add one first:\n"
              "  git remote add origin <url>", file=sys.stderr)
        return 1

    # Neutralise an unsafe --force (in any spelling, including bundles like -qf)
    # before it can reach the wire.
    had_force, rest = safety.split_out_force(rest)
    if had_force and not safety.has_lease(rest):
        rest = rest + ["--force-with-lease"]
        print("note: rewrote --force to --force-with-lease (safer; refuses if you'd clobber new commits)")
    forcing = had_force or safety.has_lease(rest)

    if safety.is_protected(branch):
        what = "force-push" if forcing else "push"
        if not safety.confirm(f"'{branch}' is a protected branch. Really {what} to it?", yes):
            return 1
    elif forcing:
        if not safety.confirm(f"Force-push '{branch}'?", yes):
            return 1

    positionals = [t for t in rest if not t.startswith("-")]
    up = gitcmd.upstream()
    if up or positionals:
        args = ["push"] + rest
    else:
        remote = gitcmd.default_remote()
        print(f"No upstream yet - setting it to {remote}/{branch}.")
        args = ["push", "-u", remote, branch] + rest

    return gitcmd.passthrough(args)


def pf(rest):
    # Force-push, the safe way. push() adds the lease, the protected guard,
    # and the confirm.
    return push(list(rest) + ["--force-with-lease"])


def pull(rest):
    op = gitcmd.in_progress()
    if op:
        print(f"fatal: a {op} is already in progress - finish it first "
              f"('git {op} --continue' or 'jgit undo')", file=sys.stderr)
        return 1
    return gitcmd.passthrough(["pull", "--rebase", "--autostash"] + list(rest))


def sync(rest):
    op = gitcmd.in_progress()
    if op:
        print(f"fatal: a {op} is in progress - finish it before syncing", file=sys.stderr)
        return 1

    remote = gitcmd.default_remote()
    if not remote:
        print("fatal: no remote to sync with (add one: git remote add origin <url>)",
              file=sys.stderr)
        return 1

    before = gitcmd.out(["rev-parse", "HEAD"]) if gitcmd.has_commits() else ""

    print(f"Fetching {remote}...")
    rc, _, ferr = gitcmd.capture(["fetch", "--prune", remote])
    if rc != 0:
        print(f"fatal: fetch from {remote} failed:\n{ferr}", file=sys.stderr)
        return rc
    pruned = ferr.count("[deleted]")
    if pruned:
        print(f"Pruned {pruned} stale remote branch(es).")

    if gitcmd.upstream():
        print("Rebasing onto upstream...")
        rc = gitcmd.passthrough(["pull", "--rebase", "--autostash"])
        if rc != 0:
            return rc
    else:
        print("No upstream set for this branch - fetched only.")
        return 0

    after = gitcmd.out(["rev-parse", "HEAD"])
    if before and after and before != after:
        n = gitcmd.out(["rev-list", "--count", f"{before}..{after}"], default="?")
        print(f"Updated: {n} new commit(s) pulled.")
    else:
        print("Already up to date.")
    return 0


def undo(rest):
    yes, rest = safety.wants_yes(rest)
    action = gitcmd.last_reflog_action()
    if not action:
        print("Nothing to undo - no history yet.")
        return 1

    low = action.lower()
    if low.startswith("commit (amend)"):
        return _undo_soft("amend", yes)
    if low.startswith("commit"):
        return _undo_soft("commit", yes)
    if low.startswith("merge"):
        return _undo_hard(f"merge ({action})", yes)
    if low.startswith("rebase"):
        return _undo_hard(f"rebase ({action})", yes)
    if low.startswith("reset"):
        return _undo_hard(f"reset ({action})", yes)
    if low.startswith("pull"):
        return _undo_hard(f"pull ({action})", yes)
    if low.startswith("cherry-pick"):
        return _undo_hard(f"cherry-pick ({action})", yes)
    if low.startswith("revert"):
        return _undo_hard(f"revert ({action})", yes)
    if low.startswith("checkout"):
        print("Last action was a branch switch - going back.")
        return gitcmd.passthrough(["checkout", "-"])

    print(f"Not sure how to undo '{action}' safely. Recent history:")
    for line in gitcmd.reflog(8):
        print("  " + line)
    print("\nUndo by hand with:  git reset --soft HEAD@{1}   (your commits stay in the reflog)")
    return 1


def _undo_soft(kind, yes):
    has_parent = gitcmd.ok(["rev-parse", "--verify", "--quiet", "HEAD~1"])
    if kind == "commit" and not has_parent:
        # The very first commit: there's no earlier state to reset to.
        print("This is the first commit - jGit will remove it but keep your files.")
        if not safety.confirm("Remove the initial commit (files kept)?", yes):
            return 1
        return gitcmd.passthrough(["update-ref", "-d", "HEAD"])

    target = gitcmd.out(["rev-parse", "HEAD@{1}"]) or ("HEAD~1" if has_parent else "")
    if not target:
        print("fatal: can't find a previous state to undo to", file=sys.stderr)
        return 1
    print(f"Undoing last {kind}: moving HEAD back but keeping your changes (soft reset).")
    return gitcmd.passthrough(["reset", "--soft", target])


def _undo_hard(what, yes):
    # Resolve the target to a fixed sha NOW, before any stash can shift the
    # reflog out from under us.
    target = gitcmd.out(["rev-parse", "HEAD@{1}"])
    if not target:
        print("fatal: can't find the previous state in the reflog", file=sys.stderr)
        return 1

    print(f"Undoing {what}: HEAD goes back to {target[:10]} and the result is discarded.")

    stashed = False
    if gitcmd.is_dirty():
        if safety.confirm("You have uncommitted changes. Stash them first (recommended)?",
                          yes, default_no=False):
            before = gitcmd.stash_count()
            rc = gitcmd.passthrough(["stash", "push", "-u", "-m", "jgit undo safety stash"])
            # Three checks: stash exited clean, a new stash entry appeared, AND
            # the tree is now actually clean. A partial stash (e.g. a locked
            # file on Windows) must not let the hard reset eat what it missed.
            if rc != 0 or gitcmd.stash_count() <= before or gitcmd.is_dirty():
                print("fatal: the stash didn't fully take - stopping so nothing is lost",
                      file=sys.stderr)
                return 1
            stashed = True
            print("Stashed your changes. Bring them back any time with: git stash pop")
        elif not safety.confirm("Proceed and permanently DISCARD those changes?", yes):
            return 1
    elif not safety.confirm(f"Hard-reset to {target[:10]}?", yes):
        return 1

    rc = gitcmd.passthrough(["reset", "--hard", target])
    if rc != 0 and stashed:
        # The reset failed after we stashed - put the work back so the user
        # isn't left mid-operation with their changes squirreled away.
        print("The reset failed; restoring your stashed changes.", file=sys.stderr)
        gitcmd.passthrough(["stash", "pop"])
    return rc


def save(rest):
    yes, rest = safety.wants_yes(rest)
    message = " ".join(rest) if rest else "WIP"
    rc = gitcmd.passthrough(["add", "-A"])
    if rc != 0:
        return rc
    if not gitcmd.out(["status", "--porcelain"]):
        print("Nothing to save - working tree clean.")
        return 0
    return gitcmd.passthrough(["commit", "-m", message])


def branches(rest):
    cur = gitcmd.current_branch()
    out = gitcmd.out([
        "for-each-ref", "--sort=-committerdate",
        "--format=%(refname:short)\t%(objectname:short)\t%(contents:subject)",
        "refs/heads/",
    ])
    if not out:
        print("No branches yet.")
        return 0
    for line in out.splitlines():
        parts = line.split("\t")
        name = parts[0]
        sha = parts[1] if len(parts) > 1 else ""
        subj = parts[2] if len(parts) > 2 else ""
        mark = "*" if name == cur else " "
        print(f"{mark} {name:<28} {sha:<10} {subj[:58]}")
    return 0


def new(rest):
    if not rest:
        print("usage: jgit new <branch> [start-point]", file=sys.stderr)
        return 1
    return gitcmd.passthrough(["checkout", "-b"] + list(rest))


def guarded_reset(rest):
    yes, rest = safety.wants_yes(rest)
    if "--hard" in rest:
        if gitcmd.is_dirty():
            question = "reset --hard will discard ALL uncommitted changes. Continue?"
        else:
            question = "reset --hard moves HEAD and rewrites the work tree. Continue?"
        if not safety.confirm(question, yes):
            return 1
    return gitcmd.passthrough(["reset"] + rest)


def guarded_clean(rest):
    yes, rest = safety.wants_yes(rest)
    dry = "-n" in rest or "--dry-run" in rest
    if not dry and not safety.confirm(
        "clean permanently deletes untracked files. Continue?", yes
    ):
        return 1
    return gitcmd.passthrough(["clean"] + rest)


def pr(rest):
    if not gitcmd.has_tool("gh"):
        print("fatal: GitHub CLI (gh) not found - install from https://cli.github.com",
              file=sys.stderr)
        return 1
    return gitcmd.passthrough(["pr", "create"] + list(rest), exe="gh")


def open_remote(rest):
    url = gitcmd.out(["remote", "get-url", gitcmd.default_remote() or "origin"])
    if not url:
        print("fatal: no remote to open", file=sys.stderr)
        return 1
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    url = re.sub(r"\.git$", "", url)
    print(f"Opening {url}")
    webbrowser.open(url)
    return 0
