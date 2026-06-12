# jGit

A git client that tries to be smart about the things git makes you think too hard about.

Under the hood it drives the real `git` binary, so push, pull, GitHub, rebase, stash - everything git can do, jGit can do, because it *is* git underneath. What jGit adds is a layer on top: your aliases travel with it, common operations get sensible defaults, and the handful of commands that can ruin your afternoon ask first. Type `jcm "fix"`, `jps`, `jundo` and get on with your day.

There's also a second jGit hiding inside the first: a complete version control system written from scratch in Python - its own object database, refs, commits, the works. That's the `--native` mode. It doesn't touch git at all and exists mostly because building git from scratch is the best way to finally understand it. More on that near the end.

## Why this exists

I've used the same dozen git aliases for years - `st`, `cm`, `lg`, a `wip` that stages and commits in one go. They lived in a PowerShell profile, which meant reinstalling them on every machine and editing a script every time I wanted a new one. And they were dumb: `git push` is `git push`, alias or not. It still won't set an upstream for a new branch, still won't blink before you force-push over a teammate's work.

jGit moves the aliases into the tool, where they can travel and be listed and be overridden per project - and then it goes further. `jps` sets the upstream for you. `jundo` reads the reflog and figures out what you actually did so it can undo *that*. `jsync` pulls, prunes, and tells you what changed. `reset --hard` and force-push stop and ask. None of that is reimplementing git; it's the thin, opinionated layer git never shipped.

## Install

Python 3.9+ and git. That's it - no third-party packages.

```
pip install -e .
```

On Windows, pip drops `jgit.exe` in your per-user Scripts folder (`%APPDATA%\Python\Python3xx\Scripts`). If `jgit` isn't found afterward, that folder isn't on your PATH; add it once and new terminals will see it. Prefer not to install? `python -m jgit <command>` works from the repo.

### The one-letter habit

Typing `jgit` every time gets old. `jgit shell-init` prints a shell function per command and alias, each prefixed with `j`, so `jgit commit -m` collapses to `jcm`. Add one line to your PowerShell `$PROFILE`:

```powershell
jgit shell-init | Out-String | Invoke-Expression
```

Now `jst`, `jcm "msg"`, `jps`, `jsync`, `jundo`, `jsave`, and a bare `j` for plain `jgit` all exist, regenerated from your live alias table every shell start - add an alias today, get its shortcut tomorrow. Bash or zsh: `eval "$(jgit shell-init --bash)"` in `.bashrc`. The `j` prefix sits next to git's usual `g` shortcuts without colliding.

## Five minutes with it

```
> jgit init                      # a real git repo - GitHub-ready
jGit will drive this Git repository. Try: jgit st  /  jgit save "msg"  /  jgit ps

> echo "hello" > app.py
> jgit st
On branch main
No upstream set - first push with 'jgit ps'.

Untracked:
    app.py

> jgit save "first version"      # add -A + commit, in one
> jgit st
On branch main
No upstream set - first push with 'jgit ps'.

nothing to commit, working tree clean

> git remote add origin git@github.com:me/app.git
> jgit ps                        # sets upstream automatically
No upstream yet - setting it to origin/main.
'main' is a protected branch. Really push to it? [y/N] y
...

> jgit new feature/login         # branch + switch
> echo "more" >> app.py
> jgit save "wip"
> jgit ps                        # feature branch: no nagging, just sets upstream and pushes

> jgit undo                      # reads the reflog: last thing was a commit -> soft reset
Undoing last commit: moving HEAD back but keeping your changes (soft reset).
```

If you know git, none of this needs explaining, which is the point.

## What makes it smarter than git plus aliases

These are real commands with logic behind them, not string substitutions.

| command | what it actually does |
|---|---|
| `jgit ps` / `push` | Sets the upstream on a new branch (`push -u origin <branch>`). Reports what happened. Asks before pushing to a protected branch. Rewrites a bare `--force` to `--force-with-lease` so you can't clobber commits you haven't seen. |
| `jgit undo` | Reads the reflog, classifies the last operation (commit, amend, merge, rebase, reset, pull, cherry-pick, checkout) and undoes *that* - a soft reset for a commit, a guarded hard reset for a merge, a branch switch back for a checkout. Your work survives in the reflog regardless. |
| `jgit sync` | `fetch --prune` then `pull --rebase --autostash`, then tells you how many commits came in and how many dead branches it pruned. Refuses if a rebase or merge is half-finished. |
| `jgit st` / `status` | The branch, the upstream relationship in plain words ("Ahead of origin/main by 2 - publish with jgit ps"), and your changes grouped into staged / not staged / untracked. |
| `jgit save` / `wip` | Stage everything and commit, message optional (defaults to `WIP`). The quick-save you reach for fifty times a day. |
| `jgit pf` | Force-push, but always `--force-with-lease`, with the protected-branch question attached. |
| `jgit branches` | Every branch, most-recent first, current one starred, with the short hash and subject. |
| `jgit new <branch>` | `checkout -b`. |

Anything not in that list - `commit`, `rebase -i`, `stash`, `cherry-pick`, `diff`, `log`, `merge`, `clone` - passes straight through to git, untouched, with full interactivity. Editors open, pagers page, colors color. jGit only steps in where it has something to add.

## The guardrails

The whole reason to put a layer over git is to catch the handful of commands that don't have an undo. jGit's rule is **fail-closed**: when an action is destructive and it can't be sure you meant it, it stops.

- **Force-push is always a lease.** `jgit ps --force` becomes `--force-with-lease` - git refuses the push if the remote has commits you haven't pulled. The unsafe plain force never reaches the wire.
- **Protected branches ask first.** Push or force-push to `main`/`master` (plus anything you list under `[git] protected` in your config) and jGit confirms - including the first push that *creates* `origin/main`, which is exactly when it matters.
- **`reset --hard` and `clean` confirm**, and `undo`'s destructive cases offer to stash your uncommitted work first - and verify the stash actually took before they reset, so a failed stash can't eat your changes.
- **No terminal, no guessing.** In a script, a pipe, or CI - anywhere there's no TTY to answer a prompt - a destructive action doesn't hang and doesn't proceed. It refuses and tells you to pass `--yes` if you're sure. `--yes` is parsed as a real flag, never matched as a substring, so a commit message that happens to contain `--yes` can't authorize anything.

Soft resets, reading commands, ordinary commits - none of that is gated. The friction is spent only where a mistake is expensive.

## Aliases

The built-in git-mode table covers the everyday shorthands, ported from years of muscle memory:

| alias | runs |
|---|---|
| `aa` | `add -A` |
| `cm` / `ca` | `commit -m` / `commit -am` |
| `amend` | `commit --amend --no-edit` |
| `unstage` / `discard` | `restore --staged` / `restore --` |
| `lg` / `ll` | a compact graph log / a prettier dated one |
| `co` / `br` / `ba` / `back` | `checkout` / `branch` / `branch -a` / `checkout -` |
| `d` / `ds` / `ap` | `diff` / `diff --staged` / `add -p` |
| `ri` / `cp` | `rebase -i` / `cherry-pick` |
| `pushb` | the smart push (`ps`) |

Add your own - they're stored as plain INI in `~/.jgitconfig` (everywhere) or the repo's config (`--repo`):

```
jgit alias yard "log --oneline --since=yesterday"
jgit alias yard --repo "log -n 20"     # this project only
jgit alias yard                        # show what it expands to
jgit alias --unset yard
```

Repo config beats user config beats built-ins. A smart command can never be redefined by an alias - `jgit alias undo "log"` won't shadow the real `undo` - and aliases can chain into other aliases without looping forever. Anything git already understands (`stash pop`, `rebase --continue`) needs no alias; it just passes through.

## The two modes

jGit decides what kind of repository it's in and acts accordingly:

- A real **`.git`** - git mode: everything above. jGit drives git and adds the smart layer. It never writes git's object store itself; it shells out, every time, so there's no way for jGit to corrupt a real repo.
- A **`.jgit`** directory (made by `jgit init --native`) - native mode: jGit's own from-scratch VCS, which knows nothing about git.
- Neither - jGit acts as a thin git front-end, so `jgit clone <url>` works before any repo exists.

When both a `.git` and a `.jgit` sit in the same folder, the `.jgit` wins, so the two never fight. The practical upshot: `jgit init` gives you a normal git repo that's ready to push to GitHub, and `jgit init --native` gives you the toy. You'll almost always want the former.

## The other jGit: a version control system from scratch

`jgit init --native` opens a different world. No git anywhere - jGit stores everything itself, in a `.jgit` directory you can read with a text editor.

This exists because git stops being magic the moment you implement it. A branch is a 41-byte file holding a commit hash. HEAD is a file pointing at that file. A commit is a snapshot plus a parent pointer. Detached HEAD is just HEAD holding a hash instead of a `ref:` line. Build it once and those stop being scary.

The object database is content-addressed: hash the bytes with SHA-1, write them to `objects/<hash>`, done. Blobs are file contents, trees are directory listings pointing at blobs and other trees, commits point at a tree and a parent. `jgit log` walks the parent chain. Native mode implements `commit`, `log`, `diff`, `checkout`, `branch`, `tag`, `reset`, `restore`, `status`, the `~`/`^` revision syntax, abbreviated hashes, and a Graphviz `k` graph - all in roughly 900 lines of dependency-free Python following Nikita Leshenko's ugit tutorial.

It is deliberately not git: no staging area (commit snapshots the whole tree), no remotes, no merge, no packfiles or compression (every object is a loose, readable file). It's for a scratch project, a notes folder, or an evening of finally understanding `.git`. For anything you'll push to GitHub, use the default git mode - that's the whole point of it.

## What it doesn't do, honestly

- **Native mode has no network, no merge, no staging, no compression.** Those are chosen limits, not bugs (see above). It's a learning tool and a local scratch VCS, not a git replacement.
- **jGit is opinionated in git mode.** `jgit pull` rebases. `jgit ps` sets upstreams and questions protected pushes. If you want git's exact unopinionated behavior, `git` is still right there - jGit drives it, it doesn't hide it.
- **It assumes git is on your PATH** for everything in a real repo. No git, no git mode (it tells you where to get it).
- **The smart `st` is its own rendering**, not `git status` verbatim. Pass flags (`jgit status --porcelain`) and it steps aside to real git.

## Tests

```
python tests/test_jgit.py        # native VCS: the from-scratch engine
python tests/test_gitmode.py     # git mode: smart commands, guardrails, passthrough
```

Seventy-nine end-to-end tests that drive the real CLI in throwaway directories - every native command, the git-mode smart commands against real bare remotes, every guardrail in its fail-closed form, the reflog-driven undo, the alias and shell-shortcut machinery, the mode routing, and the shell-injection defense. They isolate HOME and git's global config so your real settings are never touched. No framework to install; it's stdlib `unittest`.
