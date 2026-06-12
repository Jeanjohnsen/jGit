"""Alias expansion, git-style.

Resolution order (later wins):
  1. built-in defaults for the active mode (native VCS vs real git)
  2. [alias] in ~/.jgitconfig
  3. [alias] in the repo config

Reserved words (real native commands, or smart git commands) always beat
aliases, so neither `commit` nor the flagship `undo` can be redefined into
something surprising. Aliases may point at other aliases; expansion stops
after a few hops to keep a typo from looping forever.
"""

import re
import shlex

from . import config


# Native-mode aliases: for jGit's own from-scratch VCS. No remote/stash/index
# aliases here - native jGit has none of those to talk to.
DEFAULTS = {
    # looking around
    "st": "status",
    "s": "status",
    "lg": "log --oneline",
    "ll": "log --oneline",
    "last": "log -n 1",
    "find": "log --grep",
    "graph": "k",
    # branches and movement
    "co": "checkout",
    "br": "branch",
    "ba": "branch",
    "new": "checkout -b",
    "back": "checkout -",
    # committing
    "cm": "commit -m",
    "ca": "commit -m",
    "amend": "commit --amend",
    "wip": "commit -m WIP",
    # diffing
    "d": "diff",
    "df": "diff",
    # undoing mistakes
    "undo": "reset --soft @~1",
    "discard": "restore",
    # tags
    "tags": "tag",
}

# Git-mode aliases: shorthands that expand to real git commands, ported from
# the JRJ PowerShell set. The smart commands (ps, pl, sync, undo, st, save,
# pf, branches, new, pr, open) are NOT here - they're handled directly so an
# alias can never shadow them. Anything git already understands (stash, rebase,
# cherry-pick) needs no alias; it passes straight through.
GIT_DEFAULTS = {
    # staging & commit
    "aa": "add -A",
    "cm": "commit -m",
    "ca": "commit -am",
    "amend": "commit --amend --no-edit",
    "unstage": "restore --staged",
    "discard": "restore --",
    "ap": "add -p",
    # inspect
    "lg": "log --oneline --graph --decorate --all",
    "ll": "log --graph --date=relative "
          "--pretty=format:'%C(auto)%h%C(reset) %C(green)(%ad)%C(reset) %s %C(dim white)<%an>%C(reset)'",
    "last": "log -1 HEAD",
    "d": "diff",
    "ds": "diff --staged",
    "who": "shortlog -sn --all",
    "tags": "tag -l",
    "remotes": "remote -v",
    # branch & move
    "co": "checkout",
    "br": "branch",
    "ba": "branch -a",
    "back": "checkout -",
    "del": "branch -d",
    # history
    "ri": "rebase -i",
    "cp": "cherry-pick",
    "fetch": "fetch --all --prune",
    "reflog": "reflog",
    # remote shorthand -> the smart push (gets upstream + guardrails)
    "pushb": "ps",
}

MAX_DEPTH = 10
# \Z, not $: $ also matches just before a trailing newline, which would let a
# name like "co\n" slip through. valid_name gates both alias creation and the
# shell-shortcut generator, so it has to be airtight.
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


def _defaults(mode):
    return GIT_DEFAULTS if mode == "git" else DEFAULTS


def all_aliases(mode="native"):
    """name -> (expansion, source) for the given mode, config overriding built-ins."""
    out = {name: (value, "builtin") for name, value in _defaults(mode).items()}
    for source, path in (("user", config.user_path()), ("repo", config.repo_path())):
        for name, value in config.section(path, "alias").items():
            out[name] = (value, source)
    return out


def expand(argv, reserved=(), mode="native"):
    """Rewrite argv[0] through the alias table for `mode`. A word in
    `reserved` (real commands, or smart git commands) is never expanded, so it
    can't be shadowed by an alias of the same name."""
    reserved = set(reserved)
    table = all_aliases(mode)
    for _ in range(MAX_DEPTH):
        if not argv or argv[0] in reserved or argv[0].startswith("-"):
            return argv
        if argv[0] not in table:
            return argv
        expansion, _source = table[argv[0]]
        argv = shlex.split(expansion) + list(argv[1:])
    return argv


def valid_name(name):
    return bool(NAME_RE.match(name))
