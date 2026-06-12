"""Command handlers for jGit's own from-scratch VCS (native mode).

These run only when jGit is operating on its own `.jgit` store - never on a
real git repo (cli routes a real `.git` to git mode instead). They sit on top
of base.py / data.py / diff.py. cli.build_parser() wires them to the parser;
cli.main() sets ORIG_CWD before dispatching so resolve_path can map a path the
user typed in a subdirectory back to the repo root.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

from . import base
from . import data
from . import diff
from .repo import fatal

ORIG_CWD = os.getcwd()


def oid_arg(name):
    oid = base.get_oid(name)
    if not oid:
        raise argparse.ArgumentTypeError(f"unknown revision '{name}'")
    return oid


def require_head(what):
    oid = base.get_oid("@")
    if not oid:
        fatal(f"no commits yet - nothing to {what}")
    return oid


def resolve_path(path):
    """Arguments were typed relative to where the user ran jgit, which may
    be a subdirectory; we have since moved to the repo root."""
    return os.path.relpath(os.path.join(ORIG_CWD, path)).replace("\\", "/")


def oid_of(name):
    oid = base.get_oid(name)
    if not oid:
        fatal(f"unknown revision '{name}'")
    return oid


def status(args):
    head_oid = base.get_oid("@")
    branch_name = base.get_branch_name()

    if branch_name:
        print(f"On branch {branch_name}")
    else:
        print(f"HEAD detached at {head_oid[:10]}")

    if not head_oid:
        print("\nNo commits yet")

    changes = list(diff.iter_changes(diff.commit_contents(head_oid), diff.working_contents()))
    if changes:
        print("\nChanges since last commit:")
        for path, action in changes:
            print(f"  {action + ':':<10} {path}")
    elif head_oid:
        print("\nnothing to commit, working tree clean")


def commit(args):
    if args.amend:
        if not base.get_oid("@"):
            fatal("no commits yet - nothing to amend")
        print(base.commit_amend(args.message))
        return

    if not args.message:
        fatal('commit needs a message: jgit commit -m "what changed"')

    oid = base.commit(args.message)
    if oid is None:
        fatal("nothing to commit, working tree clean")
    print(oid)


def _decorations():
    """oid -> ['HEAD -> main', 'tag: v1', ...] for the log output."""
    head = data.get_ref("HEAD", deref=False)
    branch_name = None
    if head.symbolic and head.value.startswith("refs/heads/"):
        branch_name = head.value[len("refs/heads/"):]

    refs = {}
    for refname, ref in data.iter_refs():
        if refname == "HEAD":
            if branch_name:
                continue  # represented as 'HEAD -> branch' below
            name = "HEAD"
        elif refname.startswith("refs/heads/"):
            name = refname[len("refs/heads/"):]
        elif refname.startswith("refs/tags/"):
            name = f"tag: {refname[len('refs/tags/'):]}"
        else:
            continue
        refs.setdefault(ref.value, []).append(name)

    if branch_name:
        head_oid = data.get_ref("HEAD").value
        if head_oid:
            names = refs.setdefault(head_oid, [])
            if branch_name in names:
                names.remove(branch_name)
            names.insert(0, f"HEAD -> {branch_name}")
    return refs


def log(args):
    start = args.oid or base.get_oid("@")
    refs = _decorations()

    shown = 0
    for oid in base.iter_commits_and_parents({start}):
        commit = base.get_commit(oid)
        if args.grep and args.grep.lower() not in commit.message.lower():
            continue

        names = refs.get(oid, [])
        decor = f" ({', '.join(names)})" if names else ""

        if args.oneline:
            subject = commit.message.splitlines()[0] if commit.message else ""
            print(f"{oid[:10]}{decor} {subject}")
        else:
            print(f"commit {oid}{decor}")
            if commit.author:
                print(f"Author: {commit.author}")
            if commit.time:
                print(f"Date:   {time.ctime(commit.time)}")
            print()
            print(textwrap.indent(commit.message, "    "))
            print()

        shown += 1
        if args.max_count and shown >= args.max_count:
            break


def diff_cmd(args):
    if len(args.commits) > 2:
        fatal("diff takes at most two commits")

    if len(args.commits) == 2:
        d_from = diff.commit_contents(args.commits[0])
        d_to = diff.commit_contents(args.commits[1])
    else:
        source = args.commits[0] if args.commits else base.get_oid("@")
        d_from = diff.commit_contents(source)
        d_to = diff.working_contents()

    sys.stdout.write(diff.diff_contents(d_from, d_to))


def checkout(args):
    if args.new_branch:
        name = args.new_branch
        if base.is_branch(name):
            fatal(f"branch '{name}' already exists")
        oid = oid_of(args.target) if args.target else base.get_oid("@")
        if oid:
            base.create_branch(name, oid)
            base.checkout(name)
        else:
            # No commits yet: just point HEAD at the new branch name.
            data.update_ref("HEAD", data.RefValue(symbolic=True, value=f"refs/heads/{name}"),
                            deref=False)
        print(f"Switched to a new branch '{name}'")
        return

    target = args.target
    if not target:
        fatal("checkout needs a branch, tag, commit, or '-'")
    if target == "-":
        target = base.previous_head_name()
        if not target:
            fatal("no previous branch to go back to")

    oid = oid_of(target)
    base.checkout(target)
    if base.is_branch(target):
        print(f"Switched to branch '{target}'")
    else:
        print(f"HEAD is now detached at {oid[:10]}")


def branch(args):
    if args.delete:
        if not args.name:
            fatal("branch -d needs a branch name")
        if args.name == base.get_branch_name():
            fatal(f"cannot delete '{args.name}' while it is checked out")
        if not base.is_branch(args.name):
            fatal(f"branch '{args.name}' not found")
        base.delete_branch(args.name)
        print(f"Deleted branch {args.name}")
    elif not args.name:
        current = base.get_branch_name()
        for name in sorted(base.iter_branches()):
            prefix = "*" if name == current else " "
            print(f"{prefix} {name}")
    else:
        if base.is_branch(args.name):
            fatal(f"branch '{args.name}' already exists")
        oid = args.start_point or require_head("branch from")
        base.create_branch(args.name, oid)
        print(f"Branch {args.name} created at {oid[:10]}")


def tag(args):
    existing = set(base.iter_tags())

    if args.delete:
        if not args.name:
            fatal("tag -d needs a tag name")
        if args.name not in existing:
            fatal(f"tag '{args.name}' not found")
        base.delete_tag(args.name)
        print(f"Deleted tag '{args.name}'")
    elif args.name:
        if args.name in existing:
            fatal(f"tag '{args.name}' already exists (remove it first: jgit tag -d {args.name})")
        oid = args.oid or require_head("tag")
        base.create_tag(args.name, oid)
        print(f"Tagged {oid[:10]} as '{args.name}'")
    else:
        for name in sorted(existing):
            print(name)


def reset(args):
    oid = args.commit or require_head("reset to")
    base.reset(oid, hard=args.hard)
    subject = base.get_commit(oid).message.splitlines()[0]
    print(f"HEAD is now at {oid[:10]} {subject}")


def restore(args):
    oid = oid_of(args.source)
    paths = [resolve_path(p) for p in args.paths]
    try:
        restored = base.restore(paths, oid)
    except KeyError as e:
        fatal(f"'{e.args[0]}' did not match any file in that commit")
    for path in restored:
        print(f"Restored {path}")


def k(args):
    dot = "digraph commits {\n"

    oids = set()
    for refname, ref in data.iter_refs(deref=False):
        dot += f'"{refname}" [shape=note]\n'
        dot += f'"{refname}" -> "{ref.value}"\n'
        if not ref.symbolic:
            oids.add(ref.value)

    for oid in base.iter_commits_and_parents(oids):
        commit = base.get_commit(oid)
        subject = commit.message.splitlines()[0] if commit.message else ""
        label = f"{oid[:10]}\\n{subject}".replace('"', '\\"')
        dot += f'"{oid}" [shape=box style=filled label="{label}"]\n'
        if commit.parent:
            dot += f'"{oid}" -> "{commit.parent}"\n'

    dot += "}"
    print(dot)

    if not shutil.which("dot"):
        print("\n(graphviz not found - install it from https://graphviz.org to render this)",
              file=sys.stderr)
        return

    svg = os.path.join(tempfile.gettempdir(), "jgit-graph.svg")
    with open(svg, "wb") as f:
        subprocess.run(["dot", "-Tsvg"], input=dot.encode(), stdout=f, check=True)
    print(f"Rendered to {svg}", file=sys.stderr)

    if sys.platform == "win32":
        os.startfile(svg)
    elif sys.platform == "darwin":
        subprocess.run(["open", svg])
    else:
        subprocess.run(["xdg-open", svg])


def hash_object(args):
    with open(resolve_path(args.file), "rb") as f:
        print(data.hash_object(f.read()))


def cat_file(args):
    sys.stdout.flush()
    sys.stdout.buffer.write(data.get_object(args.object, expected=None))
    sys.stdout.buffer.flush()


def write_tree(args):
    print(base.write_tree())


def read_tree(args):
    base.read_tree(args.tree)
