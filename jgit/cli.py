import argparse
import os
import sys

from difflib import get_close_matches

from . import aliases
from . import base
from . import config
from . import data
from . import gitcmd
from . import gitmode
from . import native
from . import repo
from .repo import (
    fatal,
    enter_repo,
    find_repo,
    forced_dir_name,
    is_real_git,
    nearest_real_git,
    NO_REPO_COMMANDS,
    REAL_GIT_MESSAGE,
)

COMMAND_NAMES = set()

# Meta commands that manage jGit itself - they behave the same whether you're
# in a native repo, a git repo, or none, so they bypass mode routing.
META_COMMANDS = {"init", "alias", "shell-init"}


def _make_output_resilient():
    """git's output (branch names, commit subjects) can carry characters the
    Windows console code page can't encode. Replace rather than crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    _make_output_resilient()
    parser = build_parser()

    raw = sys.argv[1:]
    if not raw:
        parser.print_help()
        return

    cmd0 = raw[0]

    # 'init' is special: default is a real `git init`; --native makes jGit's
    # own from-scratch repository. Handle it before any mode routing.
    if cmd0 == "init":
        raise SystemExit(cmd_init(raw[1:]))

    # Route by repository kind. The other meta commands (alias, shell-init)
    # manage jGit itself and fall through to the native parser below, which
    # already treats them as not-needing-a-repo.
    if cmd0 not in META_COMMANDS and not cmd0.startswith("-"):
        mode = repo.detect_mode()
        if mode.kind == "git":
            raise SystemExit(gitmode.run(raw))
        if mode.kind == "none":
            # Not in any repo: act as a thin git front-end so `jgit clone <url>`
            # and the like work. No smart handling - there's nothing to be
            # smart about yet.
            if cmd0 not in COMMAND_NAMES and cmd0 not in aliases.all_aliases("native"):
                raise SystemExit(gitmode.passthrough_only(raw))

    # Native flow: jGit's own VCS, plus the alias/shell-init meta commands.
    argv = aliases.expand(raw, reserved=COMMAND_NAMES, mode="native")
    cmd = argv[0]
    if not cmd.startswith("-") and cmd not in COMMAND_NAMES:
        candidates = sorted(COMMAND_NAMES | set(aliases.all_aliases("native")))
        suggestions = get_close_matches(cmd, candidates, n=3)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        fatal(f"'{cmd}' is not a jgit command or alias.{hint} See 'jgit --help'.")

    # Remember where the user ran us (native handlers map relative paths from
    # here), then work from the repo root so jgit behaves the same anywhere.
    native.ORIG_CWD = os.getcwd()
    if not cmd.startswith("-") and cmd not in NO_REPO_COMMANDS:
        enter_repo()

    args = parser.parse_args(argv)
    args.func(args)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jgit",
        description="A small content-addressed version control system.",
    )

    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.required = True

    p = commands.add_parser("init", help="create a repository here (real git; --native for jGit's own)")
    p.set_defaults(func=lambda a: None)  # intercepted in main()
    p.add_argument("--native", "--jgit", dest="native", action="store_true",
                   help="make jGit's own from-scratch repository instead of a git one")

    p = commands.add_parser("status", help="current branch and changed files")
    p.set_defaults(func=native.status)

    p = commands.add_parser("commit", help="snapshot the working directory")
    p.set_defaults(func=native.commit)
    p.add_argument("-m", "--message")
    p.add_argument("--amend", action="store_true", help="replace the last commit")

    p = commands.add_parser("log", help="walk the commit history")
    p.set_defaults(func=native.log)
    p.add_argument("oid", default=None, type=native.oid_arg, nargs="?")
    p.add_argument("--oneline", action="store_true", help="one commit per line")
    p.add_argument("-n", "--max-count", type=int, help="stop after N commits")
    p.add_argument("--grep", help="only commits whose message contains this")

    p = commands.add_parser("diff", help="compare working dir or commits")
    p.set_defaults(func=native.diff_cmd)
    p.add_argument("commits", type=native.oid_arg, nargs="*",
                   help="none: @ vs working dir, one: it vs working dir, two: A vs B")

    p = commands.add_parser("checkout", help="switch branches or visit a commit")
    p.set_defaults(func=native.checkout)
    p.add_argument("target", nargs="?", help="branch, tag, commit, or '-' for previous")
    p.add_argument("-b", dest="new_branch", metavar="branch",
                   help="create the branch first, then switch to it")

    p = commands.add_parser("branch", help="list, create or delete branches")
    p.set_defaults(func=native.branch)
    p.add_argument("name", nargs="?")
    p.add_argument("start_point", default=None, type=native.oid_arg, nargs="?")
    p.add_argument("-d", "--delete", action="store_true")

    p = commands.add_parser("tag", help="list, create or delete tags")
    p.set_defaults(func=native.tag)
    p.add_argument("name", nargs="?")
    p.add_argument("oid", default=None, type=native.oid_arg, nargs="?")
    p.add_argument("-d", "--delete", action="store_true")

    p = commands.add_parser("reset", help="move the current branch to another commit")
    p.set_defaults(func=native.reset)
    p.add_argument("commit", default=None, type=native.oid_arg, nargs="?")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--soft", action="store_true", help="keep the working dir (default)")
    mode.add_argument("--hard", action="store_true", help="also overwrite the working dir")

    p = commands.add_parser("restore", help="bring files back from a commit")
    p.set_defaults(func=native.restore)
    p.add_argument("paths", nargs="+")
    p.add_argument("--source", default="@", metavar="commit")

    p = commands.add_parser("alias", help="list, inspect, set or remove aliases")
    p.set_defaults(func=alias)
    p.add_argument("name", nargs="?")
    p.add_argument("expansion", nargs="*")
    p.add_argument("--unset", action="store_true")
    p.add_argument("--repo", action="store_true",
                   help="store in this repo's .jgit/config instead of ~/.jgitconfig")

    p = commands.add_parser("shell-init",
                            help="print shell functions (j, jcm, jst, ...) for your profile")
    p.set_defaults(func=shell_init)
    p.add_argument("--bash", action="store_true",
                   help="emit bash/zsh functions instead of PowerShell")

    p = commands.add_parser("k", help="draw the commit graph (graphviz)")
    p.set_defaults(func=native.k)

    p = commands.add_parser("hash-object", help="store one file, print its id")
    p.set_defaults(func=native.hash_object)
    p.add_argument("file")

    p = commands.add_parser("cat-file", help="print a stored object")
    p.set_defaults(func=native.cat_file)
    p.add_argument("object", type=native.oid_arg)

    p = commands.add_parser("write-tree", help="store the working dir as a tree")
    p.set_defaults(func=native.write_tree)

    p = commands.add_parser("read-tree", help="replace the working dir with a tree")
    p.set_defaults(func=native.read_tree)
    p.add_argument("tree", type=native.oid_arg)

    COMMAND_NAMES.clear()
    COMMAND_NAMES.update(commands.choices)
    return parser


def cmd_init(rest):
    """`jgit init` makes a real Git repo (so it's GitHub-ready and jGit drives
    it); `jgit init --native` makes jGit's own from-scratch repository."""
    native = "--native" in rest or "--jgit" in rest
    extra = [a for a in rest if a not in ("--native", "--jgit")]

    if not native:
        rc = gitcmd.passthrough(["init", *extra])
        if rc == 0:
            print("jGit will drive this Git repository. Try: jgit st  /  jgit save \"msg\"  /  jgit ps")
        return rc

    dirname = forced_dir_name() or ".jgit"
    data.GIT_DIR = dirname
    if os.path.isdir(dirname):
        if is_real_git(os.path.abspath(dirname)):
            fatal("a real Git repository already exists here - jGit won't overwrite .git.")
        fatal(f"'{dirname}' already exists - this is already a jgit repository")
    base.init()
    print(f"Initialized empty native jgit repository in {os.path.join(os.getcwd(), dirname)}")
    return 0


def alias(args):
    if args.repo:
        # Resolve the repo ourselves (this command runs without auto-entering
        # one). find_repo skips real Git, so a repo-scoped alias can never land
        # in a real Git config.
        found = find_repo()
        if not found:
            if nearest_real_git():
                fatal(REAL_GIT_MESSAGE)
            fatal("--repo only works inside a repository")
        root, dirname = found
        os.chdir(root)
        data.GIT_DIR = dirname
        scope_path = config.repo_path()
    else:
        scope_path = config.user_path()

    if args.unset:
        if not args.name:
            fatal("alias --unset needs a name")
        if config.unset_value(scope_path, "alias", args.name):
            print(f"Removed alias '{args.name}'")
        else:
            scope = "repo" if args.repo else "user"
            fatal(f"no {scope} alias named '{args.name}'")
    elif args.name and args.expansion:
        if not aliases.valid_name(args.name):
            fatal(f"'{args.name}' is not a valid alias name")
        if args.name in COMMAND_NAMES:
            fatal(f"'{args.name}' is a built-in command and can't be shadowed")
        expansion = " ".join(args.expansion)
        config.set_value(scope_path, "alias", args.name, expansion)
        print(f"{args.name} = {expansion}")
    else:
        alias_mode = "git" if repo.detect_mode().kind == "git" else "native"
        table = aliases.all_aliases(alias_mode)
        if args.name:
            if args.name not in table:
                fatal(f"no alias named '{args.name}'")
            value, source = table[args.name]
            print(f"{args.name} = {value}  [{source}]")
        else:
            width = max(len(name) for name in table)
            for name in sorted(table):
                value, source = table[name]
                print(f"{name:<{width}}  {value}  [{source}]")


def shell_init(args):
    """Emit one short function per command and alias, prefixed with 'j', so
    'jgit cm "msg"' becomes 'jcm "msg"'. Hyphens are dropped from names so
    'cat-file' becomes 'jcatfile' (shells dislike '-' in function names).

    This output is meant to be piped through Invoke-Expression / eval at shell
    startup, so it must be safe even when the alias table has been hand-edited.
    Names are interpolated into shell code, so anything that isn't a plain
    alias-shaped token is dropped - otherwise a config key like 'x}$(rm){'
    would run arbitrary code. Colliding j-names are emitted once."""
    # Cover every name jgit might dispatch in any mode: native commands, both
    # alias tables, and the smart git commands. The j-shortcut just calls
    # `jgit <name>`, which routes itself at runtime.
    names = (set(COMMAND_NAMES)
             | set(aliases.all_aliases("native"))
             | set(aliases.all_aliases("git"))
             | set(gitmode.SMART_NAMES))
    raw = sorted(names)

    funcs = []
    seen = set()
    skipped = 0
    for name in raw:
        if name == "shell-init" or not aliases.valid_name(name):
            skipped += 1 if name != "shell-init" else 0
            continue
        fn = "j" + name.replace("-", "")
        if fn in seen:
            continue
        seen.add(fn)
        funcs.append((fn, name))

    if args.bash:
        print('# jgit shortcuts - add to ~/.bashrc:  eval "$(jgit shell-init --bash)"')
        print('j() { jgit "$@"; }')
        for fn, name in funcs:
            print(f'{fn}() {{ jgit {name} "$@"; }}')
    else:
        print("# jgit shortcuts - add to your PowerShell $PROFILE:")
        print("#   jgit shell-init | Out-String | Invoke-Expression")
        print("function j { jgit @args }")
        for fn, name in funcs:
            print(f"function {fn} {{ jgit {name} @args }}")

    if skipped:
        print(f"# (skipped {skipped} alias name(s) that aren't safe as shell function names)")
