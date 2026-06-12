"""Shared harness for git-mode tests.

Each test gets a real git working repo and, on request, a bare 'origin' to
push to - all in throwaway directories. Git's global and system config are
redirected to empty temp files so tests never read or write the developer's
real ~/.gitconfig, and identity/autocrlf are pinned for reproducibility.
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GitModeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self._gitconfig = os.path.join(self._home.name, "isolated.gitconfig")
        open(self._gitconfig, "w").close()
        self.cwd = os.path.join(self._tmp.name, "work")
        os.makedirs(self.cwd)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "Tester")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")

    # --- environment --------------------------------------------------

    def _env(self, extra=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = REPO_ROOT
        env["PYTHONIOENCODING"] = "utf-8"
        env["HOME"] = self._home.name
        env["USERPROFILE"] = self._home.name
        env["GIT_CONFIG_GLOBAL"] = self._gitconfig
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_TERMINAL_PROMPT"] = "0"
        env.pop("JGIT_DIR", None)
        if extra:
            env.update(extra)
        return env

    # --- running git and jgit ----------------------------------------

    def git(self, *args, cwd=None, check=True):
        proc = subprocess.run(
            ["git", *args], cwd=cwd or self.cwd, env=self._env(),
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def jgit(self, *args, cwd=None, check=True, env_extra=None, stdin=None):
        proc = subprocess.run(
            [sys.executable, "-m", "jgit", *args],
            cwd=cwd or self.cwd, env=self._env(env_extra),
            input=stdin, capture_output=True, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"jgit {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )
        return proc

    # --- convenience --------------------------------------------------

    def write(self, relpath, text, cwd=None):
        path = os.path.join(cwd or self.cwd, relpath)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, relpath, text, message, cwd=None):
        self.write(relpath, text, cwd=cwd)
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)
        return self.git("rev-parse", "--short", "HEAD", cwd=cwd).stdout.strip()

    def make_bare_origin(self):
        """Create a bare repo and wire it as 'origin'. Returns its path."""
        bare = os.path.join(self._tmp.name, "origin.git")
        subprocess.run(["git", "init", "-q", "-b", "main", "--bare", bare],
                       env=self._env(), check=True)
        self.git("remote", "add", "origin", bare)
        return bare

    def log_subjects(self, cwd=None):
        out = self.git("log", "--format=%s", cwd=cwd).stdout.strip()
        return out.splitlines() if out else []

    def current_branch(self, cwd=None):
        return self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).stdout.strip()
