"""the pool, DAG and git rules, the git-isolated test base (#54) and the classes #54 runs nested"""

import calendar
import glob
import html
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import AGENT, HERE, HOME, LOOP, ROLES, TESTS, GitIsolatedCase, LoopCase, git, own_origin  # noqa: E402,F401
from core import dag as dagmod  # noqa: E402
from core import osx, pages  # noqa: E402
from core import pool as pool_mod  # noqa: E402
from core.pool import Pool, shown_state, TICKET_STATES  # noqa: E402


class TheHappyPath(LoopCase):
    def test_two_dependent_tickets_land_together_and_release_once(self):
        res = self.run_py("ticket_creator.py", "--request", "build two things")
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertEqual(sorted(snap.tickets), ["1", "2"])
        self.assertEqual(snap.dag["2"]["depends_on"], ["1"])
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.state(2), "landed")
        groups = list(self.snap().groups.values())
        self.assertEqual(len(groups), 1, "together tickets share one integration branch")
        self.assertEqual(groups[0]["members"], ["1", "2"])
        self.assertTrue(self.main_has("impl_1.txt") and self.main_has("impl_2.txt"))
        with open(self.released) as fh:
            self.assertEqual(fh.read().split(), ["g1", "1,2"])
        self.assertIn("landed", self.inbox_kinds())
        self.assertIn("created", self.inbox_kinds())

    def test_an_alone_ticket_gets_its_own_integration_branch(self):
        self.add(1)
        self.add(2)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        groups = {g["id"]: g["members"] for g in self.snap().groups.values()}
        self.assertEqual(sorted(groups.values()), [["1"], ["2"]])
        self.assertEqual(self.state(2), "landed")


class FirstTicketId(unittest.TestCase):
    """dag.json's first_ticket_id: a pool restarted after a loss starts its numbers there."""

    def test_floor_applies_until_higher_numbers_exist(self):
        from core.pool import Snapshot

        def snap(dag, tickets=()):
            return Snapshot({"dag": dag, "tickets": {i: {} for i in tickets}, "groups": {}})
        self.assertEqual(snap({"tickets": {}}).next_ticket_id(), "1")
        self.assertEqual(snap({"tickets": {}, "first_ticket_id": 100}).next_ticket_id(), "100")
        self.assertEqual(snap({"tickets": {}, "first_ticket_id": 100}, ["100", "101"]).next_ticket_id(), "102")
        self.assertEqual(snap({"tickets": {}, "first_ticket_id": 100}, ["150"]).next_ticket_id(), "151")
        for bad in (0, -5, "100", True, None):
            self.assertEqual(snap({"tickets": {}, "first_ticket_id": bad}).next_ticket_id(), "1")


class PoolRules(GitIsolatedCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="loop-pool-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        seed = Pool(os.path.join(self.tmp, "seed"), remote=None).init()
        self.bare = os.path.join(self.tmp, "remote.git")
        git(self.tmp, "clone", "-q", "--bare", seed.path, self.bare)
        self.a = self._clone("a")
        self.b = self._clone("b")

    def _clone(self, name):
        path = os.path.join(self.tmp, name)
        git(self.tmp, "clone", "-q", self.bare, path)
        git(path, "config", "user.name", name)
        git(path, "config", "user.email", name + "@t")
        return Pool(path, remote="origin")

    def test_a_rejected_push_reapplies_the_change_on_fresh_data(self):
        calls = []

        def change_b(tx):
            calls.append(1)
            if len(calls) == 1:   # someone else writes first
                self.a.update(lambda t2: t2.write("tickets/1.json", {"id": "1", "state": "ready"}), "a")
            t = tx.data.tickets.get("1")
            tx.write("tickets/2.json", {"id": "2", "saw_1": t is not None})
        self.b.update(change_b, "b")
        self.assertEqual(len(calls), 2)
        snap = self.a.snapshot()
        self.assertEqual(sorted(snap.tickets), ["1", "2"])
        self.assertTrue(snap.tickets["2"]["saw_1"])

    def test_a_write_counts_only_once_pushed(self):
        self.a.update(lambda tx: tx.write("tickets/5.json", {"id": "5"}), "a")
        self.assertIn("5", self.b.snapshot().tickets)


class ShownState(unittest.TestCase):
    def test_merged_and_landed_have_labels_others_pass_through(self):
        self.assertEqual(shown_state("merged"), "功能完成")
        self.assertEqual(shown_state("landed"), "驗證完成")
        self.assertEqual(shown_state("ready"), "ready")
        self.assertIsNone(shown_state(None))
        for st in ("ready", "running", "stuck", "cancelled"):
            self.assertEqual(shown_state(st), st)
        self.assertIn("merged", TICKET_STATES)
        self.assertIn("landed", TICKET_STATES)


class GitRules(GitIsolatedCase):
    def test_a_merge_blocked_by_an_untracked_file_is_reported_not_raised(self):
        tmp = tempfile.mkdtemp(prefix="loop-git-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        git(tmp, "init", "-q", "-b", "main")
        git(tmp, "config", "user.name", "t")
        git(tmp, "config", "user.email", "t@t")
        open(os.path.join(tmp, "a.txt"), "w").write("a")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-q", "-m", "a")
        git(tmp, "checkout", "-q", "-b", "other")
        open(os.path.join(tmp, "cache.pyc"), "w").write("from other")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-q", "-m", "cache")
        git(tmp, "checkout", "-q", "main")
        open(os.path.join(tmp, "cache.pyc"), "w").write("untracked here")
        from core.gitx import Git
        ok, why = Git(tmp).merge("other")
        self.assertFalse(ok)
        self.assertIn("overwritten", why)
        self.assertEqual(git(tmp, "status", "--porcelain"), "?? cache.pyc")


class ReplayUnits(GitIsolatedCase):
    """Git.own_commits and Git.replay (D-098): a ticket branch replayed on its target's head."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="loop-replay-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        git(self.tmp, "init", "-q", "-b", "main")
        self.commit("a.txt", "a", "init")
        from core.gitx import Git
        self.g = Git(self.tmp)

    def commit(self, path, text, subject, author=None):
        with open(os.path.join(self.tmp, path), "w") as fh:
            fh.write(text)
        git(self.tmp, "add", "-A")
        extra = ["--author", author] if author else []
        git(self.tmp, "commit", "-q", "-m", subject, *extra)
        return git(self.tmp, "rev-parse", "HEAD")

    def subjects(self, rng):
        return git(self.tmp, "log", "--format=%s", rng).splitlines()

    def test_own_commits_skip_merges_and_what_they_brought_in(self):
        git(self.tmp, "checkout", "-q", "-b", "target")
        self.commit("t.txt", "t", "target 1")
        git(self.tmp, "checkout", "-q", "-b", "t/1")
        mine = self.commit("m.txt", "m", "mine 1")
        git(self.tmp, "checkout", "-q", "target")
        self.commit("o.txt", "o", "other ticket")
        git(self.tmp, "checkout", "-q", "t/1")
        git(self.tmp, "merge", "-q", "--no-ff", "--no-edit", "target")
        mine2 = self.commit("m.txt", "m2", "mine 2")
        self.assertEqual(self.g.own_commits("t/1", ["target", "main"]), [mine, mine2])

    def test_a_linear_branch_on_its_start_is_not_rewritten(self):
        start = git(self.tmp, "rev-parse", "HEAD")
        git(self.tmp, "checkout", "-q", "-b", "t/1")
        self.commit("m.txt", "m", "mine 1")
        head = self.commit("m.txt", "m2", "mine 2")
        own = self.g.own_commits("t/1", [start])
        self.assertEqual(self.g.replay(start, own), (True, ""))
        self.assertEqual(git(self.tmp, "rev-parse", "HEAD"), head)

    def test_no_own_commits_ends_at_the_start(self):
        git(self.tmp, "checkout", "-q", "-b", "target")
        start = self.commit("t.txt", "t", "target 1")
        git(self.tmp, "checkout", "-q", "-b", "t/1", "main")
        self.assertEqual(self.g.replay(start, []), (True, ""))
        self.assertEqual(git(self.tmp, "rev-parse", "t/1"), start)
        self.assertEqual(git(self.tmp, "status", "--porcelain"), "")

    def test_replay_drops_a_change_already_in_the_target_and_keeps_authors(self):
        git(self.tmp, "checkout", "-q", "-b", "target")
        start = self.commit("same.txt", "same", "target has it")
        git(self.tmp, "checkout", "-q", "-b", "t/1", "main")
        self.commit("same.txt", "same", "already there")
        self.commit("m.txt", "m", "mine", author="Ann <ann@x>")
        own = self.g.own_commits("t/1", [start])
        self.assertEqual(len(own), 2)
        self.assertEqual(self.g.replay(start, own), (True, ""))
        self.assertEqual(self.subjects(start + "..HEAD"), ["mine"])
        self.assertEqual(git(self.tmp, "log", "-1", "--format=%an <%ae>|%cn <%ce>"),
                         "Ann <ann@x>|devloop <devloop@localhost>")

    def test_a_conflict_is_reported_and_the_branch_is_put_back(self):
        git(self.tmp, "checkout", "-q", "-b", "target")
        start = self.commit("a.txt", "target", "target a")
        git(self.tmp, "checkout", "-q", "-b", "t/1", "main")
        head = self.commit("a.txt", "mine", "mine a")
        ok, why = self.g.replay(start, self.g.own_commits("t/1", [start]))
        self.assertFalse(ok)
        self.assertIn("a.txt", why)
        self.assertEqual(git(self.tmp, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.tmp, "symbolic-ref", "--short", "HEAD"), "t/1")
        self.assertEqual(git(self.tmp, "status", "--porcelain"), "")


class DiffLinesUnits(GitIsolatedCase):
    """Git.diff_lines: added and removed lines of base...head with the file each belongs to."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="loop-difflines-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        git(self.tmp, "init", "-q", "-b", "main")
        self.write("mod.txt", "a\nb\nc\n")
        self.write("gone.txt", "g1\ng2\n")
        self.write("tail.txt", "t1\nt2\n")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "base")
        git(self.tmp, "checkout", "-q", "-b", "t/1")
        from core.gitx import Git
        self.g = Git(self.tmp)

    def write(self, path, text):
        with open(os.path.join(self.tmp, path), "w") as fh:
            fh.write(text)

    def change(self):
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "change")
        return self.g.diff_lines("main")

    def test_modified_file(self):
        self.write("mod.txt", "a\nB\nc\nd\n")
        self.assertEqual(self.change(), [("-", "mod.txt", "b"), ("+", "mod.txt", "B"), ("+", "mod.txt", "d")])

    def test_new_file(self):
        self.write("new.txt", "n1\nn2\n")
        self.assertEqual(self.change(), [("+", "new.txt", "n1"), ("+", "new.txt", "n2")])

    def test_deleted_file_takes_its_old_path(self):
        os.remove(os.path.join(self.tmp, "gone.txt"))
        self.assertEqual(self.change(), [("-", "gone.txt", "g1"), ("-", "gone.txt", "g2")])

    def test_path_with_a_space(self):
        self.write("with space.txt", "s\n")
        self.write("mod.txt", "a\nc\n")
        self.assertEqual(self.change(), [("-", "mod.txt", "b"), ("+", "with space.txt", "s")])

    def test_no_newline_marker_is_not_content(self):
        self.write("tail.txt", "t1\nt2\nt3")
        self.assertEqual(self.change(), [("+", "tail.txt", "t3")])
        self.write("tail.txt", "t1\n")
        git(self.tmp, "commit", "-q", "-am", "again")
        self.assertEqual(self.g.diff_lines("HEAD~1"), [("-", "tail.txt", "t2"), ("-", "tail.txt", "t3")])

    def test_content_starting_with_plus_plus_or_minus_minus(self):
        self.write("mod.txt", "-- a\n++ b\n--- c\n+++ d\n")
        self.assertEqual(self.change(), [
            ("-", "mod.txt", "a"), ("-", "mod.txt", "b"), ("-", "mod.txt", "c"),
            ("+", "mod.txt", "-- a"), ("+", "mod.txt", "++ b"), ("+", "mod.txt", "--- c"), ("+", "mod.txt", "+++ d")])
        self.write("mod.txt", "x\n")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "again")
        self.assertEqual(self.g.diff_lines("HEAD~1"), [
            ("-", "mod.txt", "-- a"), ("-", "mod.txt", "++ b"), ("-", "mod.txt", "--- c"), ("-", "mod.txt", "+++ d"),
            ("+", "mod.txt", "x")])


class NotifyUnits(LoopCase):
    def test_absent_notify_never_spawns(self):
        from unittest import mock
        from core.config import Config, pool_for
        pool = pool_for(Config(self.proj)).init()
        with mock.patch("core.osx.spawn") as spawn:
            pool.report("decision", "5", "first\nsecond")
        spawn.assert_not_called()

    def test_notify_must_be_null_or_a_non_empty_string(self):
        from core import osx
        from core.config import Config
        for bad in (5, "", ["x"]):
            self.config(notify=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                Config(self.proj)
            self.assertIn("devloop.notify", str(ctx.exception))
        for good in (None, "true"):
            self.config(notify=good)
            Config(self.proj)


class RemindUnits(LoopCase):
    """Pool.remind and Controller.remind (#161): reminders for unread decision and env pages."""

    def pool_with(self, rows):
        from core.config import Config, pool_for
        self.config(notify="true")
        pool = pool_for(Config(self.proj)).init()

        def change(tx):
            for name, row in rows.items():
                tx.write("inbox/%s.json" % name, row)
        pool.update(change, "test pages")
        return pool

    @staticmethod
    def ago(seconds):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))

    def page(self, kind="decision", age=3 * 3600, **extra):
        row = {"kind": kind, "ref": "5", "text": "one\ntwo", "at": self.ago(age), "acked": False}
        row.update(extra)
        return row

    def test_due_rule(self):
        pool = self.pool_with({
            "a-due": self.page(),
            "b-env": self.page("env"),
            "c-idle": self.page("idle"),
            "d-acked": self.page(acked=True),
            "e-young": self.page(age=3600),
            "f-bad-at": self.page(at="yesterday"),
            "g-full": self.page(reminded=[self.ago(9 * 3600)] * 3),
            "h-recent": self.page(reminded=[self.ago(1800)]),
            "i-spaced": self.page(age=9 * 3600, reminded=[self.ago(5 * 3600)]),
        })
        with mock.patch("core.osx.spawn") as spawn:
            names = pool.remind(2, 3)
        self.assertEqual(names, ["a-due", "b-env", "i-spaced"])
        texts = [c.kwargs["env"]["DEVLOOP_PAGE_TEXT"] for c in spawn.call_args_list]
        self.assertEqual(texts[0], "waiting 3h 0m (reminder 1 of 3): one")
        self.assertEqual(texts[2], "waiting 9h 0m (reminder 2 of 3): one")
        inbox = pool.snapshot().inbox
        self.assertEqual(len(inbox["i-spaced"]["reminded"]), 2)
        self.assertNotIn("reminded", inbox["e-young"])
        before = dict(self.page(), reminded=None)
        after = dict(inbox["a-due"], reminded=None)
        self.assertEqual(sorted(after), sorted(before))
        self.assertFalse(inbox["a-due"]["acked"])

    def test_no_notify_or_no_hours_writes_nothing(self):
        pool = self.pool_with({"a": self.page()})
        with mock.patch.object(pool, "update") as update:
            self.assertEqual(pool.remind(None, 3), [])
            pool.notify = None
            self.assertEqual(pool.remind(2, 3), [])
        update.assert_not_called()

    def test_settings_are_checked(self):
        from core.config import Config
        cfg = Config(self.proj)
        self.assertEqual((cfg.notify_remind_hours, cfg.notify_remind_max), (2, 3))
        for bad in (0, -1, True, "2", [2]):
            self.config(notify_remind_hours=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                Config(self.proj)
            self.assertIn("devloop.notify_remind_hours", str(ctx.exception))
        for good in (None, 2, 0.5):
            self.config(notify_remind_hours=good)
            self.assertEqual(Config(self.proj).values["notify_remind_hours"], good)
        for bad in (0, -1, True, 1.5, "3", None):
            self.config(notify_remind_max=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                Config(self.proj)
            self.assertIn("devloop.notify_remind_max", str(ctx.exception))
        for good in (1, 3):
            self.config(notify_remind_max=good)
            Config(self.proj)

    def test_controller_survives_a_failed_reminder_write(self):
        import ticket_controller
        ctl = ticket_controller.Controller.__new__(ticket_controller.Controller)
        ctl.cfg = mock.Mock(notify_remind_hours=2, notify_remind_max=3, poll_seconds=0)
        ctl.pool = mock.Mock()
        ctl.pool.remind.side_effect = osx.OSFailure("disk full")
        ctl.stopping = False
        ticks = iter([True, False])
        with mock.patch.object(ctl, "tick", side_effect=lambda: next(ticks)), \
                mock.patch.object(ctl, "report_idle") as idle, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err, \
                mock.patch("time.sleep"):
            self.assertEqual(ctl.run(), 0)
        self.assertEqual(ctl.pool.remind.call_count, 2)
        ctl.pool.remind.assert_called_with(2, 3)
        idle.assert_called_once_with()
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("ticket_controller: reminder") for line in lines))


class AgentControlRelease(GitIsolatedCase):
    """scripts/release.sh, agent-control's own devloop.release: tags the landed commit."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ac-release-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.git("init", "-q", "-b", "main")
        self.git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "c")
        self.sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.tmp, check=True, capture_output=True,
                              text=True).stdout

    def release(self, **env):
        full = dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", **env)
        full.pop("DEVLOOP_REMOTE", None)
        full.update(env)
        return subprocess.run(["sh", os.path.join(HOME, "scripts", "release.sh")], cwd=self.tmp,
                              env=full, capture_output=True, text=True)

    def test_tags_the_landed_commit_once(self):
        res = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g1", DEVLOOP_TICKETS="1,2")
        self.assertEqual(res.returncode, 0, res.stderr)
        tags = self.git("tag", "--points-at", self.sha).split()
        self.assertEqual(len(tags), 1)
        self.assertIn("g1", tags[0])
        self.assertIn("tickets: 1,2", self.git("tag", "-l", "-n9", tags[0]))
        again = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g1")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already exists", again.stderr)

    def test_refuses_without_a_sha(self):
        self.assertNotEqual(self.release().returncode, 0)

    def test_a_failed_push_fails_the_release(self):
        res = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g2", DEVLOOP_REMOTE="nowhere")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not pushed", res.stderr)

    def test_agent_control_config_names_it(self):
        with open(os.path.join(HOME, "board", "config.json")) as fh:
            self.assertEqual(json.load(fh)["devloop"]["release"], "sh scripts/release.sh")


class T110Acceptance(GitIsolatedCase):
    """release.sh exits 86 (the environment) when its tag exists or its push is refused."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="ac-release-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.git("init", "-q", "-b", "main")
        self.git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "c")
        self.sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.tmp, check=True, capture_output=True,
                              text=True).stdout

    def release(self, **env):
        full = dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        full.pop("DEVLOOP_REMOTE", None)
        full.update(env)
        return subprocess.run(["sh", os.path.join(HOME, "scripts", "release.sh")], cwd=self.tmp,
                              env=full, capture_output=True, text=True)

    def test_refused_push_exits_86(self):
        res = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g2", DEVLOOP_REMOTE="nowhere")
        self.assertEqual(res.returncode, 86, res.stderr)
        self.assertIn("not pushed", res.stderr)

    def test_existing_tag_exits_86(self):
        first = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g2")
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self.release(DEVLOOP_RELEASE_SHA=self.sha, DEVLOOP_GROUP="g2")
        self.assertEqual(again.returncode, 86, again.stderr)
        self.assertIn("already exists", again.stderr)


class DagRules(unittest.TestCase):
    def test_problems(self):
        dag = {"1": {"depends_on": ["2"]}, "2": {"depends_on": ["1"]}, "3": {"depends_on": ["9"]}}
        text = "\n".join(dagmod.problems(dag))
        self.assertIn("cycle", text)
        self.assertIn("#3 depends on #9", text)

    def test_topo_puts_dependencies_first(self):
        dag = {"3": {"depends_on": ["1", "2"]}, "2": {"depends_on": ["1"]}, "1": {}}
        self.assertEqual(dagmod.topo(dag), ["1", "2", "3"])


class T54Acceptance(unittest.TestCase):
    """#54: git-using test classes run git with an isolated configuration (empty global, no
    system, identity t / t@t), so a machine's global or system git settings never reach them."""

    NESTED = ("TheHappyPath", "PoolRules", "GitRules", "AgentControlRelease")

    def base(self):
        cls = globals().get("GitIsolatedCase")
        self.assertIsNotNone(cls, "tests_devloop/loop_harness.py defines no class GitIsolatedCase")
        return cls

    def tmpdir(self):
        tmp = tempfile.mkdtemp(prefix="t54-")
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    @staticmethod
    def summary(result):
        rows = []
        for kind, items in (("error", result.errors), ("failure", result.failures)):
            for test, tb in items:
                last = [ln for ln in tb.strip().splitlines() if ln.strip()][-1:] or [""]
                name = last[0].split(":", 1)[0].strip()
                rows.append("%s %s (%s)" % (kind, test.id(), name))
        return "; ".join(rows)

    def test_item1_base_class_and_subclasses(self):
        cls = self.base()
        self.assertTrue(issubclass(cls, unittest.TestCase), "GitIsolatedCase is not a unittest.TestCase")
        for name in ("LoopCase", "PoolRules", "GitRules", "AgentControlRelease"):
            self.assertTrue(issubclass(globals()[name], cls), "%s is not a subclass of GitIsolatedCase" % name)

    def test_items2_3_environment_inside_and_restored_after(self):
        cls = self.base()
        from unittest import mock
        own = self.tmpdir()
        other = os.path.join(own, "other-gitconfig")
        with open(other, "w") as fh:
            fh.write("[user]\n\tname = someone\n")
        seen = {}

        class Probe(cls):
            def test_probe(self):
                env = dict(os.environ)
                seen["env"] = env
                path = env.get("GIT_CONFIG_GLOBAL", "")
                seen["isfile"] = bool(path) and os.path.isfile(path)
                seen["size"] = os.path.getsize(path) if seen["isfile"] else None

        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": other}):
            before = dict(os.environ)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
            after = dict(os.environ)
        self.assertTrue(result.wasSuccessful() and result.testsRun == 1,
                        "probe run failed: %s" % self.summary(result))
        env = seen["env"]
        path = env.get("GIT_CONFIG_GLOBAL")
        # item 2
        self.assertNotEqual(path, other, "GIT_CONFIG_GLOBAL kept the file named before the test")
        self.assertTrue(seen["isfile"], "GIT_CONFIG_GLOBAL does not name an existing file")
        self.assertEqual(seen["size"], 0, "GIT_CONFIG_GLOBAL file is not empty")
        self.assertNotEqual(os.path.realpath(os.path.dirname(path)), os.path.realpath(own))
        self.assertFalse(os.path.realpath(path).startswith(os.path.realpath(own) + os.sep))
        self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1")
        self.assertEqual(env.get("GIT_AUTHOR_NAME"), "t")
        self.assertEqual(env.get("GIT_COMMITTER_NAME"), "t")
        self.assertEqual(env.get("GIT_AUTHOR_EMAIL"), "t@t")
        self.assertEqual(env.get("GIT_COMMITTER_EMAIL"), "t@t")
        # item 3
        self.assertEqual(after, before, "os.environ differs after the test from before it")
        self.assertFalse(os.path.exists(path), "the test's GIT_CONFIG_GLOBAL file still exists")

    def hostile_run(self, var):
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if (key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_NOSYSTEM",
                        "DEVLOOP_SLOT", "DEVLOOP_TICKET", "DEVLOOP_GROUP", "DEVLOOP_ROLE")
                    or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_")):
                del os.environ[key]
        tmp = self.tmpdir()
        signer = os.path.join(tmp, "signer.sh")
        with open(signer, "w") as fh:
            fh.write("#!/bin/sh\necho 'signer refused' >&2\nexit 1\n")
        os.chmod(signer, 0o755)
        hostile = os.path.join(tmp, "hostile-gitconfig")
        with open(hostile, "w") as fh:
            fh.write("[commit]\n\tgpgsign = true\n[gpg]\n\tformat = openpgp\n\tprogram = %s\n" % signer)
        if var == "GIT_CONFIG_SYSTEM":
            empty = os.path.join(tmp, "empty-gitconfig")
            open(empty, "w").close()
            os.environ["GIT_CONFIG_GLOBAL"] = empty
        os.environ[var] = hostile
        loader = unittest.defaultTestLoader
        suite = unittest.TestSuite(loader.loadTestsFromTestCase(globals()[n]) for n in self.NESTED)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful(), "nested run not green: %s" % self.summary(result))

    def test_item4_hostile_global_config(self):
        self.hostile_run("GIT_CONFIG_GLOBAL")

    def test_item5_hostile_system_config(self):
        self.hostile_run("GIT_CONFIG_SYSTEM")


if __name__ == "__main__":
    unittest.main()
