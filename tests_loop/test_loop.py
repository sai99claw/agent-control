"""End-to-end tests of the loop: a throwaway project, a throwaway pool, fake agents and fake
tests behind the real test interface. Run: python3 -m unittest discover tests_loop"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(HERE)
LOOP = os.path.join(HOME, "loop")
sys.path.insert(0, LOOP)

from core import dag as dagmod  # noqa: E402
from core import osx  # noqa: E402
from core.pool import Pool  # noqa: E402

AGENT = ["python3", os.path.join(HERE, "fake_agent.py")]
TESTS = "python3 %s" % os.path.join(HERE, "fake_tests.py")


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class LoopCase(unittest.TestCase):
    release = None
    loop_extra = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, "board"))
        self.pool_path = os.path.join(self.tmp, "pool")
        self.released = os.path.join(self.tmp, "released.txt")
        loop = {"pool": self.pool_path, "worktrees": os.path.join(self.tmp, "wt"), "remote": None,
                "max_parallel": 2, "poll_seconds": 0.2, "issuer_timeout_seconds": 120,
                "agent_timeout_seconds": 60, "pool_remote": None,
                "release": self.release or "echo $LOOP_GROUP $LOOP_TICKETS >> %s" % self.released,
                "agents": {r: AGENT for r in ("creator", "worker", "verifier", "reviewer", "fixer")}}
        loop.update(self.loop_extra)
        config = {"config_version": 2,
                  "tests": {"preflight": TESTS + " preflight", "run": TESTS + " run", "full": TESTS + " full"},
                  "hard_block": {"paths": ["docs/DECISIONS.md"], "dirs": [], "patterns": []},
                  "loop": loop}
        with open(os.path.join(self.proj, "board", "config.json"), "w") as fh:
            json.dump(config, fh)
        with open(os.path.join(self.proj, "README.md"), "w") as fh:
            fh.write("demo\n")
        git(self.proj, "init", "-q", "-b", "main")
        git(self.proj, "config", "user.name", "t")
        git(self.proj, "config", "user.email", "t@t")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "init")
        self.pool = Pool(self.pool_path, remote=None).init()
        self.plan_path = os.path.join(self.tmp, "plan", "plan.json")
        os.makedirs(os.path.dirname(self.plan_path))
        self.env = dict(os.environ, AC_ROOT=self.proj, FAKE_PLAN=self.plan_path,
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")

    # ------------------------------------------------------------ helpers

    def plan(self, **roles):
        with open(self.plan_path, "w") as fh:
            json.dump(roles, fh)

    def add(self, ident, deps=(), verify="together", needs_verifier=True, needs_review=False, **extra):
        def change(tx):
            t = {"id": str(ident), "title": "ticket %s" % ident, "objective": "o", "acceptance": ["a"],
                 "needs_verifier": needs_verifier, "needs_review": needs_review, "state": "ready",
                 "round": 0, "feedback": [], "cases": None}
            t.update(extra)
            tx.put_ticket(t)
            dag = tx.data.dag
            node = {"depends_on": [str(d) for d in deps], "verify": verify}
            if verify == "alone":
                node["why_alone"] = "systemic"
            dag[str(ident)] = node
            tx.put_dag({"tickets": dag})
        self.pool.update(change, "test: add #%s" % ident)

    def run_py(self, script, *args, timeout=240):
        return subprocess.run(["python3", os.path.join(LOOP, script)] + list(args), cwd=self.proj,
                              env=self.env, capture_output=True, text=True, timeout=timeout)

    def controller(self, timeout=240):
        res = self.run_py("ticket_controller.py", timeout=timeout)
        return res

    def snap(self):
        return self.pool.snapshot()

    def state(self, ident):
        return self.snap().ticket(str(ident)).get("state")

    def inbox_kinds(self):
        return sorted(row["kind"] for row in self.snap().inbox.values())

    def main_has(self, name):
        return git(self.proj, "ls-tree", "--name-only", "main", name) == name


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
        self.add(2, verify="alone")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        groups = {g["id"]: g["members"] for g in self.snap().groups.values()}
        self.assertEqual(sorted(groups.values()), [["1"], ["2"]])
        self.assertEqual(self.state(2), "landed")


class RoundsAndFeedback(LoopCase):
    def test_failing_cases_send_the_worker_back_and_round_two_lands(self):
        self.add(1)
        self.plan(worker={"1": ["wrong", "ok"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["round"], 2)

    def test_out_of_rounds_is_stuck_and_the_rest_of_the_group_lands(self):
        self.add(1)
        self.add(2)
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")
        self.assertEqual(self.snap().ticket("1")["stuck"]["reason"], "rounds")
        self.assertEqual(self.state(2), "landed")
        self.assertIn("decision", self.inbox_kinds())

    def test_a_dependent_of_a_stuck_ticket_is_never_dispatched(self):
        self.add(1)
        self.add(2, deps=[1])
        self.plan(worker={"1": ["wrong"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "stuck")
        self.assertEqual(self.state(2), "ready")

    def test_review_fail_is_feedback_like_any_other(self):
        self.add(1, needs_review=True)
        self.plan(reviewer={"1": ["fail", "pass"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["round"]), ("landed", 2))
        fed = [h for h in t["history"] if h["event"] == "feedback"]
        self.assertEqual([h.get("round") for h in fed], [1])

    def test_a_worker_that_needs_the_user_stops_the_ticket(self):
        self.add(1)
        self.plan(worker={"1": ["blocked"]})
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "decision"))

    def test_touching_hard_block_stops_before_merging(self):
        self.add(1)
        self.plan(worker={"1": ["touch_decisions"]})
        self.controller()
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "hard_block"))
        self.assertFalse(self.main_has("impl_1.txt"))


class Regression(LoopCase):
    def test_red_regression_is_fixed_on_the_integration_branch(self):
        self.add(1)
        self.plan(worker={"1": ["red"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        g = self.snap().groups["g1"]
        self.assertEqual(g["fix_round"], 1)
        self.assertFalse(self.main_has("red.txt"))

    def test_environment_stops_the_controller_with_one_page(self):
        self.add(1)
        open(os.path.join(self.proj, ".env-bad"), "w").close()
        res = self.controller()
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertEqual(self.state(1), "merged")
        self.assertEqual(self.inbox_kinds(), ["env"])
        os.unlink(os.path.join(self.proj, ".env-bad"))
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")

    def test_main_moving_during_verification_is_merged_in(self):
        self.add(1)
        self.plan(worker={"1": ["move_main"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertTrue(self.main_has("NOTES.md") and self.main_has("impl_1.txt"))
        self.assertEqual(self.snap().groups["g1"]["fix_round"], 1)


class ReleaseFails(LoopCase):
    release = "exit 7"

    def test_merged_but_not_released_is_a_decision_not_a_retry(self):
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        self.assertTrue(self.main_has("impl_1.txt"))
        self.assertIn("decision", self.inbox_kinds())


class Timeout(LoopCase):
    loop_extra = {"issuer_timeout_seconds": 4}

    def test_an_issuer_past_its_time_is_stuck(self):
        self.add(1)
        self.plan(worker={"1": ["sleep"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["stuck"]["reason"]), ("stuck", "timeout"))


class Revise(LoopCase):
    def test_a_worker_can_have_the_ticket_revised_and_cases_rewritten(self):
        self.add(1)
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["acceptance"], ["a possible thing"])
        self.assertTrue(any(h["event"] == "revised" for h in t["history"]))

    def test_a_question_from_the_opener_opens_nothing(self):
        self.plan(creator={"-": ["question"]})
        res = self.run_py("ticket_creator.py", "--request", "make it nicer")
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertEqual(self.snap().tickets, {})
        self.assertEqual(self.inbox_kinds(), ["decision"])

    def test_an_invalid_hand_in_is_retried_then_reported(self):
        self.plan(creator={"-": ["bad"]})
        res = self.run_py("ticket_creator.py", "--request", "x")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(self.snap().tickets, {})


class Recovery(LoopCase):
    def test_a_running_ticket_whose_issuer_died_is_dispatched_again(self):
        self.add(1)

        def change(tx):
            t = tx.ticket("1")
            t.update(state="running", target="int/g1", group="g1",
                     run={"pid": 999999, "host": osx.host(), "boot": osx.boot_id(), "started": osx.now()})
            tx.put_ticket(t)
            tx.put_group({"id": "g1", "branch": "int/g1", "base": git(self.proj, "rev-parse", "main"),
                          "state": "open", "alone": False, "members": ["1"], "fix_round": 0, "job": None})
        self.pool.update(change, "test: orphan")
        git(self.proj, "branch", "int/g1", "main")
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")


class PoolRules(unittest.TestCase):
    def setUp(self):
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


class GitRules(unittest.TestCase):
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


class DagRules(unittest.TestCase):
    def test_problems(self):
        dag = {"1": {"depends_on": ["2"]}, "2": {"depends_on": ["1"]}, "3": {"depends_on": ["9"]},
               "4": {"verify": "alone"}, "5": {"verify": "sometimes"}}
        text = "\n".join(dagmod.problems(dag))
        self.assertIn("cycle", text)
        self.assertIn("#3 depends on #9", text)
        self.assertIn("#4: verify alone needs why_alone", text)
        self.assertIn("#5: verify must be together or alone", text)

    def test_topo_puts_dependencies_first(self):
        dag = {"3": {"depends_on": ["1", "2"]}, "2": {"depends_on": ["1"]}, "1": {}}
        self.assertEqual(dagmod.topo(dag), ["1", "2", "3"])


if __name__ == "__main__":
    unittest.main()
