"""#119 verifier cases for `actl schedule plan|run` (test plan's verifier list).

Oracles are the recording fakes' call logs, the ticket files the fakes write and the inbox pages /
events on disk - never schedule.py's own output parsed back into a plan. The fakes replace
auto-fix.sh and land.sh in the per-test A copy (`own_ac_home`), like test_land's gate_stub.
Nothing here signals a process: liveness belongs to the scheduler (/proc), fakes just exit.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox, TIMEOUT  # noqa: E402


FAKE_AUTO_FIX = r'''
import json, os, subprocess, sys, time
repo, log, home, n = sys.argv[1:5]
env = dict(os.environ, GIT_AUTHOR_NAME="fake", GIT_AUTHOR_EMAIL="f@example.invalid",
           GIT_COMMITTER_NAME="fake", GIT_COMMITTER_EMAIL="f@example.invalid")

def note(text):
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")

def state():
    path = os.path.join(repo, "reports", "schedule", "state.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return "missing"
    except ValueError:
        return "bad"
    if not isinstance(data, dict) or "run_id" not in data or "batches" not in data:
        return "bad-shape"
    return "ok"

note("%s start %.3f" % (n, time.time()))
seen = set()
for _ in range(20):
    seen.add(state())
    time.sleep(0.1)
note("%s state %s" % (n, ",".join(sorted(seen))))
rc_file = os.path.join(home, "afrc_%s" % n)
rc = int(open(rc_file).read()) if os.path.exists(rc_file) else 0
if rc == 0:
    path = os.path.join(repo, "tickets", "%s.json" % n)
    with open(path, encoding="utf-8") as handle:
        row = json.load(handle)
    branch = row.get("branch") or "t%s" % n
    ref = "refs/heads/" + branch
    if subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", ref],
                      capture_output=True, env=env).returncode != 0:
        subprocess.run(["git", "-C", repo, "branch", branch, "main"], check=True, env=env)
    head = subprocess.run(["git", "-C", repo, "rev-parse", ref], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()
    row["state"] = "InReview"
    row["gate"] = {"rc": 0, "sha": head, "run_id": "fake-auto-fix"}
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
note("%s end %.3f" % (n, time.time()))
sys.exit(rc)
'''

FAKE_LAND = r'''
import json, os, subprocess, sys, time
repo, log, home = sys.argv[1:4]
branches = sys.argv[4:]
env = dict(os.environ, GIT_AUTHOR_NAME="fake", GIT_AUTHOR_EMAIL="f@example.invalid",
           GIT_COMMITTER_NAME="fake", GIT_COMMITTER_EMAIL="f@example.invalid")

def git(*args):
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()

calls = 0
if os.path.exists(log):
    with open(log, encoding="utf-8") as handle:
        calls = sum(1 for line in handle if line.startswith("land "))
with open(log, "a", encoding="utf-8") as handle:
    handle.write("land %s %.3f\n" % (" ".join(branches), time.time()))
plan_file = os.path.join(home, "land_plan.json")
plan = json.load(open(plan_file)) if os.path.exists(plan_file) else []
action = plan[min(calls, len(plan) - 1)] if plan else {}
action = dict({"land": True, "fix": False, "block": [], "rc": 0}, **action)

def ticket_of(branch):
    folder = os.path.join(repo, "tickets")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                row = json.load(handle)
            if row.get("branch") == branch:
                return row["id"]
    return branch[1:].split("-")[0]

def update(ident, **fields):
    path = os.path.join(repo, "tickets", "%s.json" % ident)
    with open(path, encoding="utf-8") as handle:
        row = json.load(handle)
    row.update(fields)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, indent=2) + "\n")

for branch in branches:
    ident = ticket_of(branch)
    if action["fix"]:
        new = git("commit-tree", branch + "^{tree}", "-p", branch, "-m", "land fix " + branch)
        git("update-ref", "refs/heads/" + branch, new)
        update(ident, gate={"rc": 0, "sha": new, "run_id": "fake-land-fix"})
    if ident in action["block"]:
        update(ident, state="Blocked")
    elif action["land"]:
        new = git("commit-tree", "main^{tree}", "-p", "main", "-m", "land " + branch)
        git("update-ref", "refs/heads/main", new)
        update(ident, state="Done", landed_sha=new)
sys.exit(action["rc"])
'''


class ScheduleVerifierCases(Sandbox):
    """#119 test plan, verifier list: one case per listed rule."""

    A, G, X, B = "11", "12", "13", "14"

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "schedule-calls.log")
        for name, body in (("fake_auto_fix.py", FAKE_AUTO_FIX), ("fake_land.py", FAKE_LAND)):
            self.write(name, body, where=self.home)
        self.stub_script("auto-fix.sh", '#!/bin/sh\nexec python3 %s %s %s %s "$@"\n' % (
            os.path.join(self.home, "fake_auto_fix.py"), self.repo, self.calls, self.home))
        self.stub_script("land.sh", '#!/bin/sh\nexec python3 %s %s %s %s "$@"\n' % (
            os.path.join(self.home, "fake_land.py"), self.repo, self.calls, self.home))
        self.stub_script("review.sh", '#!/bin/sh\necho "review $*" >> %s\nexit 0\n' % self.calls)

    # ------------------------------------------------------------ helpers (class-local)

    def schedule_config(self, **schedule):
        """`schedule` key of board/config.json; no kwargs = key absent (defaults)."""
        conf = json.loads(self.read("board/config.json"))
        conf.pop("schedule", None)
        if schedule:
            conf["schedule"] = schedule
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def batches(self, *groups):
        self.write("board/batches.json", json.dumps({"schema": 1, "batches": [
            {"id": "b%d" % (i + 1), "tickets": list(g)} for i, g in enumerate(groups)]}))

    def green(self, ident, files=None):
        """InReview with a gate pass on the branch head (a branch is made when `files`)."""
        branch = "t%s" % ident
        if files is not None:
            self.ticket_branch_commit(ident, files)
        else:
            self.git("branch", branch, "main")
        head = self.git("rev-parse", branch).strip()
        self.make_ticket(ident, state="InReview",
                         gate={"rc": 0, "sha": head, "run_id": "test"})

    def schedule(self, *args):
        return self.run_sh("bin/actl", "schedule", *args)

    def call_lines(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    def land_argv(self):
        return [" ".join(line.split()[1:-1]) for line in self.call_lines()
                if line.startswith("land ")]

    def starts(self):
        return [line.split()[0] for line in self.call_lines() if " start " in line]

    def intervals(self):
        begin, out = {}, []
        for line in self.call_lines():
            parts = line.split()
            if len(parts) == 3 and parts[1] == "start":
                begin[parts[0]] = float(parts[2])
            elif len(parts) == 3 and parts[1] == "end":
                out.append((begin.pop(parts[0]), float(parts[2])))
        return out

    @staticmethod
    def max_overlap(intervals):
        points = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals],
                        key=lambda p: (p[0], p[1]))
        best = now = 0
        for _, step in points:
            now += step
            best = max(best, now)
        return best

    def pages(self, kind=None):
        rows = [row for row in self.inbox_rows() if kind is None or row.get("kind") == kind]
        return [(row, self.read(row["page"])) for row in rows]

    @staticmethod
    def names(ident, text):
        return re.search(r"(?<![0-9A-Za-z])#?%s(?![0-9])" % re.escape(ident), text) is not None

    def lines_naming(self, ident, text):
        return [line for line in text.splitlines() if self.names(ident, line)]

    def explain(self, done):
        return "rc=%s\nstdout:\n%s\nstderr:\n%s\ncalls:\n%s" % (
            done.returncode, done.stdout, done.stderr, "\n".join(self.call_lines()))

    # ------------------------------------------------------------ cases

    def test_done_dependency_off_main_is_not_satisfied(self):
        """'Done alone is not enough': K Done with landed_sha on a side branch (in the repo,
        not an ancestor of main) -> J waits, never dispatched, never landed."""
        side = self.worktree("side")
        self.commit_in(side, "side.txt", "side commit")
        off_main = self.git("rev-parse", "side").strip()
        self.make_ticket(self.A)
        self.make_ticket("18", state="Done", landed_sha=off_main)
        self.make_ticket("17", depends_on=[{"id": "18", "condition": "landed"}])
        self.schedule_config(max_workers=2)
        plan = self.schedule("plan")
        self.assertEqual(plan.returncode, 0, self.explain(plan))
        batch = [line for line in plan.stdout.splitlines() if line.startswith("batch 1:")]
        self.assertEqual(len(batch), 1, self.explain(plan))
        self.assertEqual([t.lstrip("#") for t in batch[0].split(":", 1)[1].split()], [self.A])
        waiting = [line for line in self.lines_naming("17", plan.stdout) if "waiting" in line]
        self.assertTrue(waiting, self.explain(plan))
        self.assertIn("landed_sha not on main", " ".join(waiting))
        self.assertEqual(self.call_lines(), [], "plan spawned something")
        run = self.schedule("run")
        self.assertNotIn("17", self.starts(), self.explain(run))
        self.assertTrue(all("t17" not in argv.split() for argv in self.land_argv()),
                        self.explain(run))
        self.assertEqual(self.load_ticket("17")["state"], "Ready")

    def test_failed_land_blocks_later_batch_dependant(self):
        self.make_ticket(self.A)
        self.make_ticket(self.B, depends_on=[{"id": self.A, "condition": "landed"}])
        self.batches([self.A], [self.B])
        self.schedule_config(max_workers=2)
        self.write("land_plan.json", json.dumps([{"land": False, "rc": 1}]), where=self.home)
        run = self.schedule("run")
        self.assertEqual(run.returncode, 1, self.explain(run))
        self.assertEqual(self.land_argv(), ["t%s" % self.A], self.explain(run))
        self.assertEqual(self.starts(), [self.A], self.explain(run))
        self.assertEqual(self.load_ticket(self.B)["state"], "Ready")
        self.assertEqual(len(self.pages("decision")), 1, self.explain(run))

    def test_legacy_landed_condition_on_done_dependency_dispatches(self):
        head = self.git("rev-parse", "main").strip()
        self.make_ticket("15", state="Done", landed_sha=head)
        self.make_ticket("16", depends_on=[{"id": "15", "condition": "落地(%s)" % head[:7]}])
        self.schedule_config(max_workers=2)
        run = self.schedule("run")
        self.assertEqual(run.returncode, 0, self.explain(run))
        self.assertEqual(self.starts(), ["16"], self.explain(run))
        self.assertEqual(self.land_argv(), ["t16"], self.explain(run))

    def test_max_workers_absent_defaults_to_two(self):
        for ident in (self.A, self.G, self.X):
            self.make_ticket(ident)
        self.batches([self.A, self.G, self.X])
        self.schedule_config()
        self.assertNotIn("schedule", json.loads(self.read("board/config.json")))
        run = self.schedule("run")
        self.assertEqual(run.returncode, 0, self.explain(run))
        spans = self.intervals()
        self.assertEqual(len(spans), 3, self.explain(run))
        self.assertEqual(self.max_overlap(spans), 2, self.explain(run))

    def test_two_runs_started_together_only_one_proceeds(self):
        for ident in (self.A, self.G):
            self.make_ticket(ident)
        self.batches([self.A, self.G])
        self.schedule_config(max_workers=2)
        cmd = ["sh", self.script("bin/actl"), "schedule", "run"]
        procs = [subprocess.Popen(cmd, cwd=self.repo, env=self.env(), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(2)]
        outs = [proc.communicate(timeout=TIMEOUT) for proc in procs]
        rcs = sorted(proc.returncode for proc in procs)
        detail = "rcs=%s\n%s\ncalls:\n%s" % (rcs, outs, "\n".join(self.call_lines()))
        self.assertEqual(rcs, [0, 2], detail)
        self.assertEqual(sorted(self.starts()), [self.A, self.G], detail)
        self.assertEqual(self.land_argv(), ["t%s t%s" % (self.A, self.G)], detail)
        self.assertEqual(self.kinds().count("schedule.start"), 1, detail)

    def test_overlap_is_symmetric_and_measured_against_main(self):
        """tA deletes f2, tG changes f2: diffs against main both name f2 -> two land calls,
        in batch order, whichever ticket comes first."""
        self.write("f2", "base\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "f2 on main")
        self.green(self.A, {"f2": None})
        self.green(self.G, {"f2": "changed\n"})
        self.schedule_config(max_workers=2)
        self.batches([self.A, self.G])
        plan = self.schedule("plan")
        self.assertEqual(plan.returncode, 0, self.explain(plan))
        pair = [line for line in plan.stdout.splitlines() if "overlap" in line
                and self.names(self.A, line) and self.names(self.G, line) and "f2" in line]
        self.assertTrue(pair, self.explain(plan))
        before = {i: self.read("tickets/%s.json" % i) for i in (self.A, self.G)}
        self.batches([self.G, self.A])
        run = self.schedule("run")
        self.assertEqual(run.returncode, 0, self.explain(run))
        self.assertEqual(self.land_argv(), ["t%s" % self.G, "t%s" % self.A], self.explain(run))
        self.assertEqual(self.call_lines()[0].split()[0], "land", "something was dispatched")
        self.assertNotEqual(before[self.A], self.read("tickets/%s.json" % self.A))

    def test_ticket_without_branch_is_never_no_overlap_in_plan(self):
        self.ticket_branch_commit(self.A, {"f1": "a\n"})
        self.make_ticket(self.A)
        self.make_ticket(self.G)
        self.schedule_config(max_workers=2)
        self.batches([self.A, self.G])
        plan = self.schedule("plan")
        self.assertEqual(plan.returncode, 0, self.explain(plan))
        mine = self.lines_naming(self.G, plan.stdout)
        self.assertTrue(any("files unknown" in line for line in mine), self.explain(plan))
        self.assertFalse(any(re.search(r"no overlap|overlap: none|no files", line)
                             for line in mine), self.explain(plan))
        self.assertEqual(self.call_lines(), [])

    def test_no_reland_when_a_ticket_of_the_failed_call_is_blocked(self):
        """First land call 'fixes' both (new gated heads) but leaves G Blocked -> no re-land."""
        self.green(self.A, {"fa": "a\n"})
        self.green(self.G, {"fg": "g\n"})
        self.batches([self.A, self.G])
        self.schedule_config(max_workers=2, reland_max=2)
        self.write("land_plan.json", json.dumps(
            [{"land": False, "fix": True, "block": [self.G], "rc": 1}]), where=self.home)
        run = self.schedule("run")
        self.assertEqual(run.returncode, 1, self.explain(run))
        self.assertEqual(self.land_argv(), ["t%s t%s" % (self.A, self.G)], self.explain(run))
        decisions = self.pages("decision")
        self.assertEqual(len(decisions), 1, self.explain(run))
        self.assertTrue(self.names(self.G, decisions[0][1]), decisions[0][1])

    def test_state_json_is_whole_at_every_snapshot(self):
        for ident in (self.A, self.G, self.X):
            self.make_ticket(ident)
        self.batches([self.A, self.G, self.X])
        self.schedule_config(max_workers=2)
        run = self.schedule("run")
        self.assertEqual(run.returncode, 0, self.explain(run))
        seen = [line for line in self.call_lines() if " state " in line]
        self.assertEqual(len(seen), 3, self.explain(run))
        self.assertEqual({line.split()[2] for line in seen}, {"ok"}, self.explain(run))
        with open(os.path.join(self.repo, "reports", "schedule", "state.json"),
                  encoding="utf-8") as handle:
            final = json.load(handle)
        self.assertEqual((final.get("stop") or {}).get("reason"), "done")

    def test_reland_max_absent_defaults_to_two(self):
        self.green(self.A, {"fa": "a\n"})
        self.batches([self.A])
        self.schedule_config()
        self.write("land_plan.json", json.dumps(
            [{"land": False, "fix": True, "rc": 1}]), where=self.home)
        run = self.schedule("run")
        self.assertEqual(run.returncode, 1, self.explain(run))
        self.assertEqual(self.land_argv(), ["t%s" % self.A] * 3, self.explain(run))
        decisions = self.pages("decision")
        self.assertEqual(len(decisions), 1, self.explain(run))
        self.assertIn("reland_max", decisions[0][1])


if __name__ == "__main__":
    import unittest
    unittest.main()
