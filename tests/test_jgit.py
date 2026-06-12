"""End-to-end tests for jgit. Run with:  python tests/test_jgit.py

Each test gets a throwaway directory and drives jgit the same way a user
would: through the CLI. HOME/USERPROFILE are redirected so alias-config
tests never touch the real ~/.jgitconfig.
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class JgitTestCase(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.cwd = self._work.name
        self.addCleanup(self._work.cleanup)
        self.addCleanup(self._home.cleanup)

    def jgit(self, *args, check=True, cwd=None, env_extra=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = REPO_ROOT
        env["PYTHONIOENCODING"] = "utf-8"
        env["HOME"] = self._home.name
        env["USERPROFILE"] = self._home.name
        env.pop("JGIT_DIR", None)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, "-m", "jgit", *args],
            capture_output=True,
            encoding="utf-8",
            cwd=cwd or self.cwd,
            env=env,
        )
        if check:
            self.assertEqual(
                proc.returncode, 0,
                f"jgit {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}",
            )
        return proc

    def write(self, relpath, text):
        path = os.path.join(self.cwd, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, relpath):
        with open(os.path.join(self.cwd, relpath), encoding="utf-8") as f:
            return f.read()

    def init_repo(self):
        # These tests exercise jGit's own from-scratch VCS, which is now the
        # opt-in '--native' mode. (Plain 'jgit init' makes a real git repo.)
        self.jgit("init", "--native")

    def commit_file(self, relpath, text, message):
        self.write(relpath, text)
        out = self.jgit("commit", "-m", message).stdout.strip()
        return out.splitlines()[-1]


class TestBasics(JgitTestCase):
    def test_init_creates_repo_on_main(self):
        out = self.jgit("init", "--native").stdout
        self.assertIn(".jgit", out)
        self.assertTrue(os.path.isdir(os.path.join(self.cwd, ".jgit")))
        self.assertTrue(os.path.isfile(os.path.join(self.cwd, ".jgit", "jgit-format")))
        status = self.jgit("status").stdout
        self.assertIn("On branch main", status)

    def test_init_twice_fails_cleanly(self):
        self.init_repo()
        proc = self.jgit("init", "--native", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already", (proc.stdout + proc.stderr).lower())

    def test_not_a_repo_is_a_friendly_error(self):
        proc = self.jgit("status", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a jgit repository", (proc.stdout + proc.stderr).lower())

    def test_no_args_prints_help(self):
        proc = self.jgit()
        self.assertIn("usage", proc.stdout.lower())

    def test_hash_object_cat_file_roundtrip(self):
        self.init_repo()
        self.write("blob.txt", "round trip\n")
        oid = self.jgit("hash-object", "blob.txt").stdout.strip()
        self.assertEqual(len(oid), 40)
        content = self.jgit("cat-file", oid).stdout
        self.assertIn("round trip", content)

    def test_works_from_subdirectory(self):
        self.init_repo()
        self.commit_file("a.txt", "hello\n", "first")
        sub = os.path.join(self.cwd, "sub", "deeper")
        os.makedirs(sub)
        out = self.jgit("log", "--oneline", cwd=sub).stdout
        self.assertIn("first", out)


class TestCommitAndLog(JgitTestCase):
    def test_commit_prints_oid_and_log_shows_history(self):
        self.init_repo()
        oid1 = self.commit_file("a.txt", "one\n", "first")
        self.assertEqual(len(oid1), 40)
        oid2 = self.commit_file("a.txt", "two\n", "second")
        log = self.jgit("log").stdout
        self.assertLess(log.index("second"), log.index("first"))
        self.assertIn("Author:", log)
        self.assertIn("Date:", log)
        self.assertIn("(HEAD -> main)", log)

    def test_empty_commit_is_refused(self):
        self.init_repo()
        self.commit_file("a.txt", "one\n", "first")
        proc = self.jgit("commit", "-m", "again", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nothing to commit", (proc.stdout + proc.stderr).lower())

    def test_log_oneline_n_and_grep(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.commit_file("a.txt", "2\n", "second thing")
        self.commit_file("a.txt", "3\n", "third")
        lines = self.jgit("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(l.split()[0]) == 10 for l in lines))
        one = self.jgit("log", "-n", "1").stdout
        self.assertIn("third", one)
        self.assertNotIn("second", one)
        grepped = self.jgit("log", "--grep", "second").stdout
        self.assertIn("second thing", grepped)
        self.assertNotIn("third", grepped)

    def test_log_decorates_tags(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("tag", "v1")
        log = self.jgit("log", "--oneline").stdout
        self.assertIn("tag: v1", log)

    def test_amend_replaces_last_commit(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "frist")
        self.write("a.txt", "1 fixed\n")
        self.jgit("commit", "--amend", "-m", "first")
        lines = self.jgit("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("first", lines[0])
        self.assertNotIn("frist", lines[0])

    def test_amend_without_message_keeps_old_one(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "keep me")
        self.write("a.txt", "2\n")
        self.jgit("commit", "--amend")
        log = self.jgit("log", "--oneline").stdout
        self.assertIn("keep me", log)


class TestRevisions(JgitTestCase):
    def test_parent_suffixes(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.commit_file("a.txt", "2\n", "second")
        self.commit_file("a.txt", "3\n", "third")
        for rev, expected in [("@~1", "second"), ("@~2", "first"), ("@^", "second"), ("@^^", "first")]:
            out = self.jgit("log", "-n", "1", rev).stdout
            self.assertIn(expected, out, f"revision {rev}")

    def test_abbreviated_hashes_resolve(self):
        self.init_repo()
        oid = self.commit_file("a.txt", "1\n", "first")
        out = self.jgit("log", "-n", "1", oid[:8]).stdout
        self.assertIn("first", out)

    def test_unknown_revision_is_a_friendly_error(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        proc = self.jgit("log", "-n", "1", "nonsense", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown revision", (proc.stdout + proc.stderr).lower())


class TestBranchesAndCheckout(JgitTestCase):
    def test_branch_create_list_and_switch(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("branch", "dev")
        out = self.jgit("branch").stdout
        self.assertIn("* main", out)
        self.assertIn("dev", out)
        self.jgit("checkout", "dev")
        self.assertIn("On branch dev", self.jgit("status").stdout)

    def test_checkout_b_creates_and_switches(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("checkout", "-b", "feature")
        self.assertIn("On branch feature", self.jgit("status").stdout)

    def test_checkout_dash_returns_to_previous_branch(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("checkout", "-b", "feature")
        self.jgit("checkout", "main")
        self.jgit("checkout", "-")
        self.assertIn("On branch feature", self.jgit("status").stdout)

    def test_checkout_restores_file_contents(self):
        self.init_repo()
        self.commit_file("a.txt", "old\n", "first")
        self.jgit("checkout", "-b", "feature")
        self.commit_file("a.txt", "new\n", "second")
        self.jgit("checkout", "main")
        self.assertEqual(self.read("a.txt"), "old\n")

    def test_branch_delete_guards(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("branch", "dev")
        self.jgit("branch", "-d", "dev")
        self.assertNotIn("dev", self.jgit("branch").stdout)
        proc = self.jgit("branch", "-d", "main", check=False)
        self.assertNotEqual(proc.returncode, 0)
        proc = self.jgit("branch", "-d", "ghost", check=False)
        self.assertNotEqual(proc.returncode, 0)


class TestTags(JgitTestCase):
    def test_tag_create_list_delete(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("tag", "v1")
        self.assertIn("v1", self.jgit("tag").stdout)
        self.jgit("tag", "-d", "v1")
        self.assertNotIn("v1", self.jgit("tag").stdout)

    def test_duplicate_tag_is_refused(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("tag", "v1")
        proc = self.jgit("tag", "v1", check=False)
        self.assertNotEqual(proc.returncode, 0)


class TestDiffStatusRestore(JgitTestCase):
    def test_diff_worktree_against_head(self):
        self.init_repo()
        self.commit_file("a.txt", "hello\n", "first")
        self.write("a.txt", "hello world\n")
        diff = self.jgit("diff").stdout
        self.assertIn("-hello", diff)
        self.assertIn("+hello world", diff)

    def test_diff_between_commits(self):
        self.init_repo()
        self.commit_file("a.txt", "one\n", "first")
        self.commit_file("a.txt", "two\n", "second")
        diff = self.jgit("diff", "@~1", "@").stdout
        self.assertIn("-one", diff)
        self.assertIn("+two", diff)

    def test_status_lists_changes(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.write("c.txt", "gone soon\n")
        self.jgit("commit", "-m", "add c")
        self.write("a.txt", "changed\n")
        self.write("b.txt", "brand new\n")
        os.remove(os.path.join(self.cwd, "c.txt"))
        status = self.jgit("status").stdout
        self.assertIn("modified", status)
        self.assertIn("a.txt", status)
        self.assertIn("new", status)
        self.assertIn("b.txt", status)
        self.assertIn("deleted", status)
        self.assertIn("c.txt", status)

    def test_clean_status(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.assertIn("working tree clean", self.jgit("status").stdout)

    def test_restore_single_file(self):
        self.init_repo()
        self.commit_file("a.txt", "v1\n", "first")
        self.write("a.txt", "v2\n")
        self.jgit("restore", "a.txt")
        self.assertEqual(self.read("a.txt"), "v1\n")

    def test_restore_unknown_path_fails(self):
        self.init_repo()
        self.commit_file("a.txt", "v1\n", "first")
        proc = self.jgit("restore", "ghost.txt", check=False)
        self.assertNotEqual(proc.returncode, 0)


class TestReset(JgitTestCase):
    def test_soft_reset_keeps_worktree(self):
        self.init_repo()
        self.commit_file("a.txt", "one\n", "first")
        self.commit_file("a.txt", "two\n", "second")
        self.jgit("reset", "--soft", "@~1")
        lines = self.jgit("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.read("a.txt"), "two\n")

    def test_hard_reset_restores_worktree(self):
        self.init_repo()
        self.commit_file("a.txt", "one\n", "first")
        self.commit_file("a.txt", "two\n", "second")
        self.jgit("reset", "--hard", "@~1")
        self.assertEqual(self.read("a.txt"), "one\n")


class TestAliases(JgitTestCase):
    def test_builtin_aliases_work(self):
        self.init_repo()
        self.assertIn("On branch main", self.jgit("st").stdout)
        self.write("a.txt", "1\n")
        self.jgit("cm", "first")
        self.assertIn("first", self.jgit("lg").stdout)
        self.write("a.txt", "2\n")
        self.jgit("wip")
        self.assertIn("WIP", self.jgit("last").stdout)

    def test_undo_alias_soft_resets(self):
        self.init_repo()
        self.commit_file("a.txt", "one\n", "first")
        self.commit_file("a.txt", "two\n", "second")
        self.jgit("undo")
        lines = self.jgit("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.read("a.txt"), "two\n")

    def test_alias_list_set_use_unset(self):
        self.init_repo()
        listing = self.jgit("alias").stdout
        self.assertIn("st", listing)
        self.assertIn("status", listing)
        self.jgit("alias", "zz", "log --oneline")
        self.commit_file("a.txt", "1\n", "first")
        self.assertIn("first", self.jgit("zz").stdout)
        self.assertIn("log --oneline", self.jgit("alias", "zz").stdout)
        self.jgit("alias", "--unset", "zz")
        proc = self.jgit("zz", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_alias_cannot_shadow_command(self):
        self.init_repo()
        proc = self.jgit("alias", "status", "log", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_user_alias_overrides_builtin(self):
        self.init_repo()
        self.commit_file("a.txt", "1\n", "first")
        self.jgit("alias", "st", "log --oneline")
        self.assertIn("first", self.jgit("st").stdout)


class TestErgonomics(JgitTestCase):
    def test_did_you_mean(self):
        self.init_repo()
        proc = self.jgit("statsu", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("status", proc.stdout + proc.stderr)

    def test_jgitignore_is_respected(self):
        self.init_repo()
        self.write(".jgitignore", "*.log\nbuild\n")
        self.commit_file("a.txt", "1\n", "first")
        self.write("noise.log", "shh\n")
        self.write("build/out.bin", "bin\n")
        status = self.jgit("status").stdout
        self.assertNotIn("noise.log", status)
        self.assertNotIn("out.bin", status)
        self.assertIn("working tree clean", status)

    def test_gitignore_is_respected(self):
        self.init_repo()
        self.write(".gitignore", "*.log\n")
        self.commit_file("a.txt", "1\n", "first")
        self.write("noise.log", "shh\n")
        self.assertIn("working tree clean", self.jgit("status").stdout)


class TestRepoDirectory(JgitTestCase):
    def test_default_init_makes_a_real_git_repo(self):
        # 'jgit init' now creates a real Git repo (GitHub-ready), not the
        # native format - so there's no jgit-format marker.
        self.jgit("init")
        self.assertTrue(os.path.isdir(os.path.join(self.cwd, ".git")))
        self.assertFalse(os.path.isfile(os.path.join(self.cwd, ".git", "jgit-format")))
        self.assertFalse(os.path.isdir(os.path.join(self.cwd, ".jgit")))

    def test_native_init_makes_dot_jgit(self):
        out = self.jgit("init", "--native").stdout
        self.assertIn(".jgit", out)
        self.assertTrue(os.path.isdir(os.path.join(self.cwd, ".jgit")))
        self.assertFalse(os.path.isdir(os.path.join(self.cwd, ".git")))
        self.assertIn("On branch main", self.jgit("status").stdout)

    def test_init_over_real_git_reinitialises_without_planting_marker(self):
        self.jgit("init")  # real git repo
        proc = self.jgit("init", check=False)  # again
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(os.path.isfile(os.path.join(self.cwd, ".git", "jgit-format")))

    def test_jgit_dir_env_override(self):
        env = {"JGIT_DIR": ".jgit"}
        self.jgit("init", "--native", env_extra=env)
        self.assertTrue(os.path.isdir(os.path.join(self.cwd, ".jgit")))
        self.write("a.txt", "1\n")
        self.jgit("commit", "-m", "first", env_extra=env)
        self.assertIn("first", self.jgit("log", "--oneline", env_extra=env).stdout)

    def test_absolute_jgit_dir_is_rejected(self):
        env = {"JGIT_DIR": os.path.join(self.cwd, ".jgit")}
        proc = self.jgit("status", check=False, env_extra=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("JGIT_DIR", proc.stdout + proc.stderr)

    def test_jgit_dir_with_separator_is_rejected(self):
        proc = self.jgit("status", check=False, env_extra={"JGIT_DIR": "foo/bar"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("JGIT_DIR", proc.stdout + proc.stderr)

    def test_nested_real_git_does_not_block_outer_native_jgit(self):
        # Outer native repo (.jgit), with a real-git checkout nested below it.
        self.jgit("init", "--native")
        self.commit_file("a.txt", "1\n", "first")
        self.write("sub/.git/description", "a real nested git repo\n")
        out = self.jgit("status", cwd=os.path.join(self.cwd, "sub")).stdout
        self.assertIn("On branch main", out)

    def test_native_objects_stay_out_of_a_real_git(self):
        # The safety guarantee: with both a real .git and jGit's own .jgit,
        # native commands write only to .jgit and never touch .git's objects.
        self.jgit("init")              # real git repo (.git)
        self.jgit("init", "--native")  # native repo (.jgit) beside it
        before = sorted(os.listdir(os.path.join(self.cwd, ".git", "objects")))
        self.commit_file("a.txt", "1\n", "native commit")
        self.assertIn("native commit", self.jgit("log", "--oneline").stdout)
        after = sorted(os.listdir(os.path.join(self.cwd, ".git", "objects")))
        self.assertEqual(before, after)  # real .git untouched
        self.assertFalse(os.path.isfile(os.path.join(self.cwd, ".git", "jgit-format")))


class TestShellInit(JgitTestCase):
    def test_powershell_functions(self):
        out = self.jgit("shell-init").stdout
        self.assertIn("function j ", out)
        self.assertIn("function jcm", out)
        self.assertIn("jgit cm @args", out)
        self.assertIn("function jst", out)
        self.assertNotIn("jshell-init", out)
        self.assertNotIn("jshellinit", out)

    def test_bash_functions(self):
        out = self.jgit("shell-init", "--bash").stdout
        self.assertIn("j() {", out)
        self.assertIn("jcm() {", out)
        self.assertIn('jgit cm "$@"', out)
        # hyphenated command names collapse for bash
        self.assertIn("jcatfile() {", out)

    def test_works_outside_a_repository(self):
        proc = self.jgit("shell-init", check=False)
        self.assertEqual(proc.returncode, 0)

    def write_user_config(self, text):
        with open(os.path.join(self._home.name, ".jgitconfig"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_dangerous_alias_names_are_not_emitted(self):
        # A hand-edited config whose alias name is actually a shell payload.
        # shell-init output is piped through Invoke-Expression / eval, so the
        # payload must never appear in the output.
        self.write_user_config(
            "[alias]\n"
            "co = checkout\n"
            "x}$(echo PWNED){ = status\n"
            "evil;touch HACKED = log\n"
        )
        out = self.jgit("shell-init").stdout
        self.assertIn("jco", out)            # the safe one survives
        self.assertNotIn("PWNED", out)       # payloads are dropped
        self.assertNotIn("HACKED", out)
        self.assertNotIn("$(", out)
        self.assertIn("skipped", out)
        # bash flavor must be clean too (ignoring the documented header comment)
        bash = self.jgit("shell-init", "--bash").stdout
        body = "\n".join(l for l in bash.splitlines() if not l.startswith("#"))
        self.assertIn("jco()", body)
        self.assertNotIn("PWNED", body)
        self.assertNotIn("HACKED", body)
        self.assertNotIn("$(", body)

    def test_colliding_jnames_emitted_once(self):
        # A user alias 'catfile' collides with the built-in 'cat-file' -> jcatfile.
        self.write_user_config("[alias]\ncatfile = log\n")
        out = self.jgit("shell-init").stdout
        self.assertEqual(out.count("function jcatfile "), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
