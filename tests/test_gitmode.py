"""Git-mode tests: jGit driving a real git repository.

Run with:  python tests/test_gitmode.py

Subprocess stdio is piped, so isatty() is False - which means every
destructive guardrail is exercised in its fail-closed (non-terminal) form by
default. Pass --yes to take the confirmed path.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gitmode_harness import GitModeTestCase


class TestModeAndPassthrough(GitModeTestCase):
    def test_real_git_repo_is_driven_not_refused(self):
        self.commit("a.txt", "1\n", "first")
        out = self.jgit("log", "--oneline").stdout
        self.assertIn("first", out)
        # jGit must not have created its native store in a real git repo.
        self.assertFalse(os.path.isdir(os.path.join(self.cwd, ".jgit")))
        self.assertFalse(os.path.isfile(os.path.join(self.cwd, ".git", "jgit-format")))

    def test_unknown_command_passes_through_to_git(self):
        self.commit("a.txt", "1\n", "first")
        # 'rev-parse' isn't a jgit command; git handles it.
        out = self.jgit("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(out, "main")

    def test_git_subcommands_work(self):
        self.commit("a.txt", "1\n", "first")
        self.write("a.txt", "2\n")
        self.jgit("stash")
        self.assertEqual(self.read_work("a.txt"), "1\n")
        self.jgit("stash", "pop")
        self.assertEqual(self.read_work("a.txt"), "2\n")

    def read_work(self, rel):
        with open(os.path.join(self.cwd, rel), encoding="utf-8") as f:
            return f.read()


class TestAliasesGitMode(GitModeTestCase):
    def test_cm_alias_commits_via_git(self):
        self.write("a.txt", "1\n")
        self.jgit("aa")                      # add -A
        self.jgit("cm", "via alias")         # commit -m
        self.assertEqual(self.log_subjects(), ["via alias"])

    def test_lg_alias_passes_through(self):
        self.commit("a.txt", "1\n", "first")
        out = self.jgit("lg").stdout
        self.assertIn("first", out)


class TestStatus(GitModeTestCase):
    def test_status_reports_branch_and_untracked(self):
        self.commit("a.txt", "1\n", "first")
        self.write("new.txt", "x\n")
        out = self.jgit("st").stdout
        self.assertIn("On branch main", out)
        self.assertIn("new.txt", out)

    def test_status_clean(self):
        self.commit("a.txt", "1\n", "first")
        self.assertIn("working tree clean", self.jgit("st").stdout)

    def test_status_shows_no_upstream_hint(self):
        self.commit("a.txt", "1\n", "first")
        self.assertIn("No upstream", self.jgit("st").stdout)


class TestSave(GitModeTestCase):
    def test_save_adds_and_commits(self):
        self.write("a.txt", "1\n")
        self.jgit("save", "snapshot")
        self.assertEqual(self.log_subjects(), ["snapshot"])

    def test_save_defaults_to_wip(self):
        self.write("a.txt", "1\n")
        self.jgit("save")
        self.assertEqual(self.log_subjects(), ["WIP"])

    def test_save_clean_tree_is_a_noop(self):
        self.commit("a.txt", "1\n", "first")
        out = self.jgit("save").stdout
        self.assertIn("Nothing to save", out)
        self.assertEqual(self.log_subjects(), ["first"])


class TestPush(GitModeTestCase):
    def test_push_feature_sets_upstream(self):
        bare = self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        self.git("checkout", "-qb", "feature")
        self.commit("a.txt", "2\n", "feature work")
        self.jgit("ps")  # no confirm: feature isn't protected
        self.assertEqual(
            self.git("rev-parse", "--abbrev-ref", "feature@{u}").stdout.strip(),
            "origin/feature",
        )

    def test_push_to_protected_main_refuses_without_yes(self):
        self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        proc = self.jgit("ps", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("protected", (proc.stdout + proc.stderr).lower())

    def test_push_to_protected_main_with_yes(self):
        bare = self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        self.jgit("ps", "--yes")
        self.assertIn("first", self.git("--no-pager", "log", "--oneline", cwd=bare).stdout)

    def test_push_no_remote_errors(self):
        self.commit("a.txt", "1\n", "first")
        self.git("checkout", "-qb", "feature")  # avoid protected guard masking it
        proc = self.jgit("ps", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no remote", (proc.stdout + proc.stderr).lower())

    def test_push_detached_head_errors(self):
        self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.git("checkout", "-q", "HEAD~1")
        proc = self.jgit("ps", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("detached", (proc.stdout + proc.stderr).lower())

    def test_plain_force_is_refused_on_protected_without_yes(self):
        self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        proc = self.jgit("ps", "--force", check=False)
        self.assertNotEqual(proc.returncode, 0)
        # rewrote to a lease, and still refused on the protected branch
        self.assertIn("force-with-lease", proc.stdout + proc.stderr)

    def test_combined_short_force_flag_does_not_bypass_guard(self):
        # '-qf' is git's '-q -f'. A force hidden in a bundle must still be
        # caught and confirmed, even on a non-protected branch in non-TTY.
        self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        self.git("checkout", "-qb", "feature")
        self.commit("a.txt", "2\n", "feature")
        proc = self.jgit("ps", "-qf", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("force-with-lease", proc.stdout + proc.stderr)


class TestSync(GitModeTestCase):
    def test_sync_pulls_new_commits(self):
        bare = self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        self.jgit("ps", "--yes")
        # a second clone advances origin
        other = os.path.join(self._tmp.name, "other")
        self.git("clone", "-q", bare, other, cwd=self._tmp.name)
        self.git("config", "user.email", "o@e.com", cwd=other)
        self.git("config", "user.name", "Other", cwd=other)
        self.commit("a.txt", "2\n", "from elsewhere", cwd=other)
        self.git("push", "-q", "origin", "main", cwd=other)
        # sync brings it home
        self.jgit("sync")
        self.assertIn("from elsewhere", self.log_subjects())

    def test_sync_refuses_mid_rebase(self):
        self.make_bare_origin()
        self.commit("a.txt", "1\n", "first")
        # fake a rebase in progress
        os.makedirs(os.path.join(self.cwd, ".git", "rebase-merge"))
        proc = self.jgit("sync", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("in progress", (proc.stdout + proc.stderr).lower())


class TestUndo(GitModeTestCase):
    def test_undo_commit_is_soft_reset(self):
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.jgit("undo")
        self.assertEqual(self.log_subjects(), ["first"])
        self.assertEqual(self.read_a(), "2\n")  # change kept

    def test_undo_initial_commit(self):
        self.commit("a.txt", "1\n", "only")
        self.jgit("undo", "--yes")
        # branch is now unborn (no commits), but the file is kept
        self.assertNotEqual(self.git("rev-parse", "--verify", "HEAD", check=False).returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "a.txt")))

    def test_undo_a_reset_is_guarded_and_recovers(self):
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.git("reset", "--hard", "HEAD~1")          # drop 'second'
        self.assertEqual(self.log_subjects(), ["first"])
        self.jgit("undo", "--yes")                      # bring it back
        self.assertEqual(self.log_subjects(), ["second", "first"])

    def test_undo_a_revert(self):
        self.commit("a.txt", "first\n", "first")
        self.commit("a.txt", "second\n", "second")
        self.git("revert", "--no-edit", "HEAD")     # makes a revert commit
        self.assertEqual(self.read_a(), "first\n")
        self.jgit("undo", "--yes")                   # undo the revert
        self.assertEqual(self.read_a(), "second\n")
        self.assertEqual(self.log_subjects(), ["second", "first"])

    def read_a(self):
        with open(os.path.join(self.cwd, "a.txt"), encoding="utf-8") as f:
            return f.read()


class TestBranchesAndNew(GitModeTestCase):
    def test_branches_lists_with_current_marked(self):
        self.commit("a.txt", "1\n", "first")
        self.git("branch", "feature")
        out = self.jgit("branches").stdout
        self.assertIn("* main", out)
        self.assertIn("feature", out)

    def test_new_creates_and_switches(self):
        self.commit("a.txt", "1\n", "first")
        self.jgit("new", "feature")
        self.assertEqual(self.current_branch(), "feature")


class TestGuardrails(GitModeTestCase):
    def test_reset_hard_refuses_without_yes(self):
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.write("a.txt", "dirty\n")
        proc = self.jgit("reset", "--hard", "HEAD~1", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.log_subjects(), ["second", "first"])  # nothing moved

    def test_reset_hard_proceeds_with_yes(self):
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.jgit("reset", "--hard", "HEAD~1", "--yes")
        self.assertEqual(self.log_subjects(), ["first"])

    def test_soft_reset_is_not_guarded(self):
        self.commit("a.txt", "1\n", "first")
        self.commit("a.txt", "2\n", "second")
        self.jgit("reset", "--soft", "HEAD~1")  # no --yes needed
        self.assertEqual(self.log_subjects(), ["first"])

    def test_clean_refuses_without_yes(self):
        self.commit("a.txt", "1\n", "first")
        self.write("junk.txt", "x\n")
        proc = self.jgit("clean", "-fd", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "junk.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
