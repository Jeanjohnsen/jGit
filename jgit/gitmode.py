"""Dispatch when jGit is driving a real Git repository.

The order matters and was the subtlest part of the whole design:

  1. Expand the user's aliases - but NEVER expand a word that is itself a
     smart command. Otherwise a leftover alias like undo -> 'reset --soft @~1'
     would shadow the flagship `undo` before it ever runs.
  2. A smart command (st, ps, sync, undo, ...) gets jGit's intelligent
     handler.
  3. Anything else is handed verbatim to the git binary, which also resolves
     the user's own git aliases. So `jgit stash pop`, `jgit rebase -i`,
     `jgit cherry-pick` all just work, with full interactivity.
"""

from . import aliases
from . import gitcmd
from . import smart


# command word -> smart handler. Aliases to these (e.g. pushb -> ps) resolve
# through the expander; the words themselves are reserved so an alias can't
# redefine them.
SMART = {
    "st": smart.status,
    "status": smart.status,
    "ps": smart.push,
    "push": smart.push,
    "pl": smart.pull,
    "pull": smart.pull,
    "sync": smart.sync,
    "undo": smart.undo,
    "pf": smart.pf,
    "save": smart.save,
    "wip": smart.save,
    "branches": smart.branches,
    "new": smart.new,
    "reset": smart.guarded_reset,
    "clean": smart.guarded_clean,
    "pr": smart.pr,
    "open": smart.open_remote,
}

SMART_NAMES = frozenset(SMART)


def run(raw):
    """Dispatch raw argv (without the program name) in git mode. Returns an
    exit code."""
    argv = aliases.expand(raw, reserved=SMART_NAMES, mode="git")
    if not argv:
        return gitcmd.passthrough(["status"])

    cmd, rest = argv[0], argv[1:]
    handler = SMART.get(cmd)
    if handler:
        return handler(rest)
    return gitcmd.passthrough(argv)


def passthrough_only(raw):
    """For when there's no repository at all: expand git aliases and hand off
    to git (so `jgit clone <url>` works), but apply no smart handling - there
    is no repo to be smart about."""
    argv = aliases.expand(raw, reserved=(), mode="git")
    return gitcmd.passthrough(argv)
