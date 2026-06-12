"""The one place that decides whether a risky action is allowed to proceed.

Every guardrail funnels through here so the rules can't drift apart. The
governing principle is fail-closed: when in doubt, refuse. In particular,
with no terminal attached (a script, CI, a pipe) a destructive action never
prompts - it requires an explicit --yes, or it stops. We never call input()
on a stream that can't answer, so jGit can't hang or crash a pipeline.
"""

import sys

from . import config
from . import gitcmd


# Branches where a mistake hurts everyone. main/master always; more via config.
_BUILTIN_PROTECTED = ("main", "master")


def protected_branches():
    extra = config.get("git", "protected", "")
    names = [n.strip() for n in extra.split(",") if n.strip()]
    return set(_BUILTIN_PROTECTED) | set(names)


def is_protected(branch):
    return bool(branch) and branch in protected_branches()


def take_flag(argv, *names):
    """Pull an owned boolean flag out of argv. Returns (present, remaining).
    We match whole tokens only - never a substring of a message or path, so
    `commit -m 'document --yes'` can't smuggle a --yes past a guard."""
    wanted = set(names)
    present = any(tok in wanted for tok in argv)
    remaining = [tok for tok in argv if tok not in wanted]
    return present, remaining


def wants_yes(argv):
    return take_flag(argv, "-y", "--yes")


def has_lease(argv):
    return any(tok == "--force-with-lease" or tok.startswith("--force-with-lease=")
               or tok == "--force-if-includes" for tok in argv)


def split_out_force(argv):
    """Find and remove every plain (unsafe) force spelling, returning
    (found, remaining). Handles '-f', '--force', '--force=...', AND combined
    short bundles like '-qf' or '-fq' (git reads '-qf' as '-q -f') - if we
    only matched the exact '-f', a force hidden in a bundle would sail past the
    guard and reach the wire unconfirmed. A '--force-with-lease' is left alone."""
    found = False
    remaining = []
    for tok in argv:
        if tok in ("-f", "--force") or tok.startswith("--force="):
            found = True
            continue
        # a short bundle: single dash, all letters, contains 'f' (so not --…)
        if len(tok) >= 2 and tok[0] == "-" and tok[1] != "-" and tok[1:].isalpha() and "f" in tok[1:]:
            found = True
            kept = "-" + tok[1:].replace("f", "")
            if kept != "-":
                remaining.append(kept)
            continue
        remaining.append(tok)
    return found, remaining


def confirm(question, assume_yes, default_no=True):
    """Ask the user to confirm a risky action. Returns True only on a clear
    yes. assume_yes (from a parsed --yes flag) short-circuits to True. With
    no TTY and no --yes, refuse and explain - never prompt."""
    if assume_yes:
        return True
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(f"Refusing: {question}\n"
              f"  No terminal to confirm at. Re-run with --yes if you're sure.",
              file=sys.stderr)
        return False
    suffix = " [y/N] " if default_no else " [Y/n] "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        # stdin closed, Ctrl-C, or the terminal vanished mid-prompt: fail closed.
        print(file=sys.stderr)
        return False
    if not answer:
        return not default_no
    return answer in ("y", "yes")
