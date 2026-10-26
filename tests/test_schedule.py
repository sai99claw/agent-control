"""`scripts/schedule.py` (#119): the depends_on DAG, board/batches.json, dispatch, land chaining,
file overlap, re-land after land's own fix, state.json / resume, the lock; `actl schedule`.

The sandbox's own copy of A gets recording fakes for `auto-fix.sh`, `land.sh` and `review.sh`
(`FAKE` below, configured per test through `fake.json`). Oracles are the fakes' call log, the ticket
files the fakes write, git, and strings written here - never schedule.py's output parsed back into
an expected value. Liveness is faked by writing pids absent from /proc or another boot_id into the
lock / state files; nothing here signals a process.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, TIMEOUT, Sandbox, write_executable  # noqa: E402

# The fakes: one python file, three sh entry points. Every call appends to calls.log
# ("start <n> <epoch> <pid>", "end <n> <epoch>", "land <argv…>", "review <n>").
FAKE = r'''
import json, os, shutil, subprocess, sys, time
FAKE = os.environ["AC_TEST_FAKE"]
ROOT = os.environ["AC_ROOT"]


def conf():
    try:
        with open(os.path.join(FAKE, "fake.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except OSError:
        return {}


def log(line):
    with open(os.path.join(FAKE, "calls.log"), "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def path(n):
    return os.path.join(ROOT, "tickets", "%s.json" % n)


def load(n):
    with open(path(n), encoding="utf-8") as handle:
        return json.load(handle)


def save(row):
    tmp = path(row["id"]) + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(row, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path(row["id"]))


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def head(branch):
    done = subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/" + branch],
                          cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def branch_of(n):
    return load(n).get("branch") or "t%s" % n


def ticket_of(branch):
    for name in os.listdir(os.path.join(ROOT, "tickets")):
        if name.endswith(".json"):
            row = load(name[:-5])
            if (row.get("branch") or "t%s" % row["id"]) == branch:
                return row["id"]
    raise SystemExit("fake land: no ticket for " + branch)


def land_on_main(ids, label):
    git("commit", "-q", "--allow-empty", "-m", "fake land " + label)
    sha = git("rev-parse", "HEAD")
    for n in ids:
        row = load(n)
        row["state"], row["landed_sha"] = "Done", sha
        save(row)


def auto_fix(n):
    c = conf()
    log("start %s %.3f %d" % (n, time.time(), os.getpid()))
    if c.get("snapshot"):
        shutil.copy(os.path.join(ROOT, "reports", "schedule", "state.json"),
                    os.path.join(FAKE, "state-%s.json" % n))
    time.sleep(c.get("sleep", 0.2))
    rc = (c.get("autofix_rc") or {}).get(n, 0)
    if rc == 0:
        row = load(n)
        row["state"] = "InReview"
        row["gate"] = {"rc": 0, "sha": head(branch_of(n)) or "f" * 40, "run_id": "fake"}
        save(row)
    log("end %s %.3f" % (n, time.time()))
    return rc


def land(branches):
    c = conf()
    calls = len([l for l in open(os.path.join(FAKE, "calls.log"), encoding="utf-8")
                 if l.startswith("land ")]) if os.path.exists(os.path.join(FAKE, "calls.log")) else 0
    log("land " + " ".join(branches))
    plan = c.get("land") or []
    act = plan[calls] if calls < len(plan) else c.get("land_default", "land")
    act = act if isinstance(act, dict) else {"do": act}
    ids = [ticket_of(b) for b in branches]
    if act["do"] == "land":
        land_on_main(ids, " ".join(branches))
        return 0
    if act["do"] == "fix":
        for n in act.get("tickets") or ids:
            b = branch_of(n)
            new = git("commit-tree", b + "^{tree}", "-p", b, "-m", "land's own fix")
            git("update-ref", "refs/heads/" + b, new)
            row = load(n)
            row["gate"] = {"rc": 0, "sha": new, "run_id": "fake-fix"}
            if act.get("drop_review"):
                row.pop("review", None)
            save(row)
        if act.get("land_also"):
            land_on_main(act["land_also"], "batch_fix")
        return 1
    if act["do"] == "same":
        for n in ids:
            row = load(n)
            row["gate"] = {"rc": 0, "sha": head(branch_of(n)), "run_id": "fake-same"}
            save(row)
        return 1
    return int(act.get("rc", 1))


verb, args = sys.argv[1], sys.argv[2:]
if verb == "auto-fix":
    sys.exit(auto_fix(args[0]))
if verb == "land":
    sys.exit(land(args))
log("review " + " ".join(args))
sys.exit(0)
'''

KILL_WORDS = r"os\." + "kill|kill" + "pg|p" + "kill|kill" + " -"


def absent_pid():
    """A pid with no /proc entry right now (liveness is read from /proc, never probed)."""
    for pid in range(4194000, 4000000, -1):
        if not os.path.exists("/proc/%d" % pid):
            return pid
    raise AssertionError("no free pid found")


def current_boot():
    with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as handle:
        return handle.read().strip()


def max_overlap(intervals):
    points = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals],
                    key=lambda p: (p[0], p[1]))
    best = now = 0
    for _, step in points:
        now += step
        best = max(best, now)
    return best


class ScheduleBase(Sandbox):

    def setUp(self):
        super().setUp()
        self.fake_dir = os.path.join(self.home, "fake")
        os.makedirs(self.fake_dir)
        with open(os.path.join(self.fake_dir, "fake.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE)
        for name, verb in (("auto-fix.sh", "auto-fix"), ("land.sh", "land"),
                           ("review.sh", "review")):
            self.stub_script(name, '#!/bin/sh\nexec python3 "$AC_TEST_FAKE/fake.py" %s "$@"\n'
                             % verb)
        self.fake()

    # ---------------------------------------------------------------- setup helpers

    def env(self, **extra):
        base = super().env(**extra)
        if getattr(self, "fake_dir", None):          # Sandbox.setUp calls git before it exists
            base["AC_TEST_FAKE"] = self.fake_dir
        return base

    def fake(self, **conf):
        with open(os.path.join(self.fake_dir, "fake.json"), "w", encoding="utf-8") as handle:
            json.dump(conf, handle)

    def schedule_conf(self, **values):
        conf = json.loads(self.read("board/config.json"))
        conf["schedule"] = values
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def batches(self, *rows, schema=1):
        self.write("board/batches.json", json.dumps(
            {"schema": schema, "batches": [{"id": bid, "tickets": list(ids)} for bid, ids in rows]}))

    def dep(self, ident, condition="landed"):
        return {"id": ident, "condition": condition}

    def green(self, ident, complex_=False, review=True, **fields):
        """InReview with a gate pass on the branch head (or a stand-in sha without a branch)."""
        branch = fields.get("branch") or "t%s" % ident
        head = subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/" + branch],
                              cwd=self.repo, env=self.env(), capture_output=True, text=True)
        row = dict(state="InReview",
                   gate={"rc": 0, "sha": head.stdout.strip() or "e" * 40, "run_id": "test"})
        if complex_:
            row["complexity"] = "complex"
            if review:
                row["review"] = {"verdict": "pass", "by": "main", "sha": "", "note": "test"}
        row.update(fields)
        return self.make_ticket(ident, **row)

    def branch(self, ident, files):
        """Branch t<ident> from main with `files` ({name: text}) committed on it."""
        self.ticket_branch_commit(ident, files)

    # ---------------------------------------------------------------- running

    def actl(self, *args, env=None):
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=self.repo, env=env or self.env(), capture_output=True,
                              text=True, timeout=TIMEOUT)

    def plan(self):
        return self.actl("schedule", "plan")

    def run_(self):
        return self.actl("schedule", "run")

    # ---------------------------------------------------------------- reading back

    def calls(self):
        path = os.path.join(self.fake_dir, "calls.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    def lands(self):
        return [line[len("land "):] for line in self.calls() if line.startswith("land ")]

    def starts(self):
        return [line.split()[1] for line in self.calls() if line.startswith("start ")]

    def intervals(self):
        begin, out = {}, []
        for line in self.calls():
            parts = line.split()
            if parts[0] == "start":
                begin[parts[1]] = float(parts[2])
            elif parts[0] == "end":
                out.append((begin.pop(parts[1]), float(parts[2])))
        self.assertEqual(begin, {}, "a fake auto-fix started and never ended")
        return out

    def pages(self, kind=None):
        rows = self.inbox_rows()
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def page_text(self, row):
        return self.read(row["page"])

    def schedule_events(self):
        return [e for e in self.events() if e["kind"].startswith("schedule.")]

    def state(self):
        return json.loads(self.read(os.path.join("reports", "schedule", "state.json")))

    def ticket_bytes(self):
        where = os.path.join(self.repo, "tickets")
        out = {}
        for name in sorted(os.listdir(where)):
            with open(os.path.join(where, name), "rb") as handle:
                out[name] = handle.read()
        return out

    def ok(self, done, rc):
        self.assertEqual(done.returncode, rc, done.stdout + done.stderr)


# ===================================================================== A1


class A1PlanAndDag(ScheduleBase):

    def setUp(self):
        super().setUp()
        main = self.git("rev-parse", "main").strip()
        self.make_ticket("A")
        self.make_ticket("B", depends_on=[self.dep("A")])
        self.make_ticket("C", depends_on=[self.dep("999")])
        self.make_ticket("D", depends_on=[self.dep("E")])
        self.make_ticket("E", depends_on=[self.dep("D")])
        self.make_ticket("F", state="Done", landed_sha=main)
        self.make_ticket("G", depends_on=[self.dep("F")])
        self.make_ticket("H", depends_on=[self.dep("F", "閘門綠")])
        self.make_ticket("J", depends_on=[self.dep("K")])
        self.make_ticket("K", state="Done", landed_sha="0123456789abcdef0123456789abcdef01234567")

    def test_a1_cycle_refuses_then_frontier_and_waiting_reasons(self):
        before = self.ticket_bytes()
        done = self.plan()
        self.ok(done, 2)
        self.assertIn("D -> E -> D", done.stdout)
        self.assertFalse([l for l in done.stdout.splitlines() if l.startswith("batch")], done.stdout)
        for name in ("D", "E"):
            os.remove(os.path.join(self.repo, "tickets", "%s.json" % name))
        before = {k: v for k, v in before.items() if k not in ("D.json", "E.json")}
        done = self.plan()
        self.ok(done, 0)
        lines = done.stdout.splitlines()
        self.assertIn("batch 1: A G", lines)
        expect = {"B": "waiting dep #A not landed", "C": "waiting missing #999",
                  "H": "waiting unknown condition '閘門綠'", "J": "waiting dep #K landed_sha not on main"}
        for ident, text in expect.items():
            with self.subTest(ident):
                self.assertTrue([l for l in lines if l.strip().startswith("#%s " % ident)
                                 and text in l], done.stdout)
        self.assertEqual(self.calls(), [], "plan spawned something")
        self.assertEqual(self.pages(), [])
        self.assertEqual(self.ticket_bytes(), before, "plan wrote a ticket")
        self.assertEqual(self.events(), [], "plan emitted an event")

    def test_a1_run_refuses_the_cycle_before_anything(self):
        done = self.run_()
        self.ok(done, 2)
        self.assertIn("D -> E -> D", done.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.pages(), [])
        self.assertFalse(self.exists(os.path.join("reports", "schedule", "state.json")))


# ===================================================================== A2


class A2BatchFile(ScheduleBase):

    def setUp(self):
        super().setUp()
        self.make_ticket("A")
        self.make_ticket("B", depends_on=[self.dep("A")])

    def refused(self, *words):
        done = self.run_()
        self.ok(done, 2)
        for word in words:
            self.assertIn(word, done.stdout)
        self.assertEqual(self.calls(), [], "a refused run spawned something")
        self.assertEqual(self.pages(), [], "a refused run wrote a page")
        self.assertEqual(self.schedule_events(), [])

    def test_a2_dependency_in_the_same_batch(self):
        self.batches(("b1", ["A", "B"]))
        self.refused("#B", "#A", "same batch")

    def test_a2_dependency_in_a_later_batch(self):
        self.batches(("b1", ["B"]), ("b2", ["A"]))
        self.refused("#B", "#A", "later batch")

    def test_a2_unlisted_ticket_is_unscheduled(self):
        self.batches(("b1", ["A"]))
        done = self.plan()
        self.ok(done, 0)
        lines = done.stdout.splitlines()
        self.assertIn("b1: A", lines)
        self.assertIn("unscheduled: B", lines)
        self.assertLess(lines.index("b1: A"), lines.index("unscheduled: B"))

    def test_a2_unknown_id(self):
        self.batches(("b1", ["A", "777"]))
        self.refused("777")

    def test_a2_listed_twice(self):
        self.batches(("b1", ["A"]), ("b2", ["A"]))
        self.refused("twice")

    def test_a2_invalid_json(self):
        self.write("board/batches.json", "{not json")
        self.refused("board/batches.json")

    def test_a2_schema_2(self):
        self.batches(("b1", ["A"]), schema=2)
        self.refused("schema")


# ===================================================================== A3


class A3DispatchAndConcurrency(ScheduleBase):

    def setUp(self):
        super().setUp()
        for ident in ("A", "G", "X"):
            self.make_ticket(ident)
        self.batches(("b1", ["A", "G", "X"]))

    def test_a3_max_workers_2_overlaps_exactly_two(self):
        self.fake(sleep=2)
        self.schedule_conf(max_workers=2)
        self.ok(self.run_(), 0)
        spans = self.intervals()
        self.assertEqual(len(spans), 3, self.calls())
        self.assertEqual(max_overlap(spans), 2, self.calls())

    def test_a3_max_workers_1_is_serial_in_batch_order(self):
        self.fake(sleep=1)
        self.schedule_conf(max_workers=1)
        self.ok(self.run_(), 0)
        self.assertEqual(max_overlap(self.intervals()), 1, self.calls())
        self.assertEqual(self.starts(), ["A", "G", "X"])

    def test_a3_already_green_is_not_dispatched(self):
        self.green("A")
        self.ok(self.run_(), 0)
        self.assertEqual(sorted(self.starts()), ["G", "X"])   # concurrent: log order is racy
        self.assertIn("tA", self.lands()[0].split())

    def test_a3_already_green_complex_with_review_is_not_dispatched(self):
        self.green("A", complex_=True)
        self.ok(self.run_(), 0)
        self.assertEqual(sorted(self.starts()), ["G", "X"])   # concurrent: log order is racy
        self.assertIn("tA", self.lands()[0].split())

    def test_a3_complex_without_review_waits_and_pages_review(self):
        self.green("A", complex_=True, review=False)
        done = self.run_()
        self.ok(done, 1)
        self.assertNotIn("A", self.starts())
        self.assertEqual(sorted(self.starts()), ["G", "X"])
        self.assertEqual(self.lands(), [])
        self.assertEqual(self.load_ticket("A")["state"], "InReview")
        pages = self.pages()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual(pages[0]["kind"], "decision")
        text = self.page_text(pages[0])
        self.assertIn("review.sh", text)
        self.assertIn("#A", text)
        self.assertFalse([c for c in self.calls() if c.startswith("review")], "scheduler ran review")

    def test_a3_running_ticket_is_another_driver(self):
        self.make_ticket("X", state="Running")
        done = self.run_()
        self.ok(done, 2)
        self.assertIn("X", done.stdout)
        self.assertIn("another driver", done.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.pages(), [])


class A3FrontierIgnoresOtherDrivers(ScheduleBase):

    def test_a3_running_elsewhere_is_not_in_the_frontier(self):
        """Without a batch file the frontier holds Ready / green tickets only: a Running ticket
        nobody listed is printed, not refused (it is not part of this run's batch)."""
        self.make_ticket("A")
        self.make_ticket("Z", state="Running")
        plan = self.plan()
        self.assertTrue([l for l in plan.stdout.splitlines()
                         if "#Z" in l and "another driver" in l], plan.stdout)
        self.ok(self.run_(), 0)
        self.assertEqual(self.starts(), ["A"])
        self.assertEqual(self.lands(), ["tA"])
        self.assertEqual(self.load_ticket("Z")["state"], "Running")


# ===================================================================== A4


class A4LandChaining(ScheduleBase):

    def test_a4_two_batches_land_in_order_and_page_once(self):
        for ident in ("A", "G"):
            self.make_ticket(ident)
        self.make_ticket("X", branch="tX-renamed")
        self.make_ticket("B", depends_on=[self.dep("A")])
        self.batches(("b1", ["A", "G", "X"]), ("b2", ["B"]))
        done = self.run_()
        self.ok(done, 0)
        self.assertEqual(self.lands(), ["tA tG tX-renamed", "tB"])
        calls = self.calls()
        self.assertLess(calls.index("land tA tG tX-renamed"),
                        [i for i, c in enumerate(calls) if c.startswith("start B ")][0])
        pages = self.pages()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual((pages[0]["kind"], str(pages[0]["ticket"])), ("done", "B"))
        text = self.page_text(pages[0])
        for word in ("b1", "b2", self.load_ticket("A")["landed_sha"],
                     self.load_ticket("B")["landed_sha"]):
            self.assertIn(word, text)
        self.assertNotEqual(self.load_ticket("A")["landed_sha"], self.load_ticket("B")["landed_sha"])
        kinds = [e["kind"] for e in self.schedule_events()]
        self.assertEqual(kinds, ["schedule.start"] + ["schedule.dispatch"] * 3 + ["schedule.land",
                                 "schedule.dispatch", "schedule.land", "schedule.stop"])
        self.assertEqual(kinds.count("schedule.dispatch"), 4)
        self.assertEqual(self.schedule_events()[-1]["reason"], "done")
        self.assertEqual([e["branches"] for e in self.schedule_events()
                          if e["kind"] == "schedule.land"], ["tA tG tX-renamed", "tB"])


# ===================================================================== A5


class A5Stops(ScheduleBase):

    def test_a5_red_auto_fix_stops_the_batch(self):
        self.schedule_conf(max_workers=1)
        self.green("A")
        self.make_ticket("G")
        self.make_ticket("X")
        self.batches(("b1", ["A", "G", "X"]))
        self.fake(autofix_rc={"G": 1})
        done = self.run_()
        self.ok(done, 1)
        self.assertEqual(self.starts(), ["G"])
        self.assertEqual(self.lands(), [])
        pages = self.pages()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual((pages[0]["kind"], str(pages[0]["ticket"])), ("decision", "G"))
        text = self.page_text(pages[0])
        for word in ("#G", "actl schedule run", "board/batches.json"):
            self.assertIn(word, text)
        self.assertEqual(self.load_ticket("A")["state"], "InReview")
        stop = self.schedule_events()[-1]
        self.assertEqual((stop["kind"], stop["reason"]), ("schedule.stop", "red"))

    def test_a5_land_failure_without_new_head_stops(self):
        for ident in ("A", "G", "X"):
            self.green(ident)
        self.make_ticket("B", depends_on=[self.dep("A")])
        self.batches(("b1", ["A", "G", "X"]), ("b2", ["B"]))
        self.fake(land=["fail"])
        done = self.run_()
        self.ok(done, 1)
        self.assertEqual(self.lands(), ["tA tG tX"])
        self.assertEqual(self.starts(), [])
        pages = self.pages()
        self.assertEqual(len(pages), 1, pages)
        text = self.page_text(pages[0])
        self.assertIn("b1", text)
        self.assertIn("land", text)

    def test_a5_everything_waits_is_rc_3_without_page(self):
        self.make_ticket("K", state="Done")
        self.make_ticket("A", depends_on=[self.dep("K")])
        self.make_ticket("G", depends_on=[self.dep("K")])
        done = self.run_()
        self.ok(done, 3)
        for ident in ("A", "G"):
            self.assertTrue([l for l in done.stdout.splitlines()
                             if "#%s " % ident in l and "waiting" in l], done.stdout)
        self.assertEqual(self.pages(), [])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.schedule_events(), [])

    def test_a5_everything_done_is_rc_3(self):
        main = self.git("rev-parse", "main").strip()
        self.make_ticket("A", state="Done", landed_sha=main)
        self.make_ticket("G", state="Done", landed_sha=main)
        done = self.run_()
        self.ok(done, 3)
        self.assertIn("nothing to dispatch or land", done.stdout)
        self.assertEqual(self.pages(), [])
        self.batches(("b1", ["A", "G"]))
        done = self.run_()
        self.ok(done, 3)
        self.assertIn("nothing to dispatch or land", done.stdout)
        self.assertEqual(self.pages(), [])


# ===================================================================== A6


class A6Lock(ScheduleBase):

    def setUp(self):
        super().setUp()
        self.make_ticket("A")
        self.batches(("b1", ["A"]))

    def hold(self, text):
        os.makedirs(os.path.join(self.repo, ".schedule.lock"))
        self.write(os.path.join(".schedule.lock", "holder"), text + "\n")

    def test_a6_live_holder_refuses(self):
        for text in ("pid=1 boot_id=%s run_id=other" % current_boot(), "pid=1"):
            with self.subTest(text):
                self.hold(text)
                done = self.run_()
                self.ok(done, 2)
                self.assertIn("pid 1", done.stdout)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.exists(os.path.join("reports", "schedule", "state.json")))
                self.assertTrue(self.exists(".schedule.lock"), "a live lock was removed")
                shutil.rmtree(os.path.join(self.repo, ".schedule.lock"))

    def test_a6_stale_holder_is_taken_over(self):
        for text in ("pid=%d boot_id=%s" % (absent_pid(), current_boot()),
                     "pid=1 boot_id=00000000-dead"):
            with self.subTest(text):
                self.make_ticket("A")
                for leftover in ("calls.log",):
                    path = os.path.join(self.fake_dir, leftover)
                    if os.path.exists(path):
                        os.remove(path)
                self.hold(text)
                done = self.run_()
                self.ok(done, 0)
                self.assertIn("stale .schedule.lock", done.stdout)
                self.assertEqual(self.starts(), ["A"])
                self.assertFalse(self.exists(".schedule.lock"), "lock not released")

    def test_a6_rerun_after_red_uses_facts_and_a_new_run_id(self):
        self.schedule_conf(max_workers=1)
        self.green("A")
        self.make_ticket("G")
        self.make_ticket("X")
        self.batches(("b1", ["A", "G", "X"]))
        self.fake(autofix_rc={"G": 1})
        self.ok(self.run_(), 1)
        old = self.state()["run_id"]
        self.green("G")
        self.fake()
        os.remove(os.path.join(self.fake_dir, "calls.log"))
        done = self.run_()
        self.ok(done, 0)
        self.assertNotEqual(self.state()["run_id"], old)
        self.assertTrue(self.exists(os.path.join("reports", "schedule", old, "state.json")))
        self.assertEqual(self.starts(), ["X"])
        self.assertEqual(self.lands(), ["tA tG tX"])


# ===================================================================== A8


class A8DogfoodAndStatic(ScheduleBase):

    def test_a8_plan_in_a(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("AC_") and k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_REMOTE")}
        done = subprocess.run(["sh", os.path.join(ROOT, "bin", "actl"), "schedule", "plan"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("batch", done.stdout)

    def test_a8_no_subcommand_and_the_table_row(self):
        done = self.actl("schedule")
        self.ok(done, 2)
        done = self.actl("--help")
        self.ok(done, 0)
        self.assertTrue([l for l in done.stdout.splitlines() if l.strip().startswith("schedule ")],
                        done.stdout)

    def test_a8_config_and_schema(self):
        with open(os.path.join(ROOT, "board", "config.json"), encoding="utf-8") as handle:
            conf = json.load(handle)
        self.assertEqual(conf["schedule"], {"max_workers": 2, "reland_max": 2})
        done = subprocess.run(["python3", os.path.join(ROOT, "scripts", "project_config.py"),
                               "check", "."], cwd=ROOT, capture_output=True, text=True,
                              timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "", "schedule is not in the schema table")

    def test_a8_the_scheduler_never_signals(self):
        with open(os.path.join(ROOT, "scripts", "schedule.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(len(re.findall(KILL_WORDS, text)), 0)
        self.assertIn("def pid_alive", text)


# ===================================================================== A9


class A9Overlap(ScheduleBase):

    def setUp(self):
        super().setUp()
        self.branch("A", {"f1": "a1\n", "f2": "a2\n"})
        self.branch("G", {"f2": "g2\n"})
        self.branch("X", {"f3": "x3\n"})
        self.branch("Y", {"f1": "y1\n"})

    def test_a9_overlapping_tickets_land_in_separate_calls(self):
        for ident in ("A", "G", "X"):
            self.green(ident)
        self.batches(("b1", ["A", "G", "X"]))
        plan = self.plan()
        self.ok(plan, 0)
        lines = [l for l in plan.stdout.splitlines() if l.startswith("overlap")]
        self.assertTrue([l for l in lines if "#A" in l and "#G" in l and "f2" in l], plan.stdout)
        self.assertFalse([l for l in lines if "#X" in l], plan.stdout)
        self.ok(self.run_(), 0)
        self.assertEqual(self.lands(), ["tA tX", "tG"])

    def test_a9_first_call_that_holds_no_overlap(self):
        for ident in ("A", "G", "Y"):
            self.green(ident)
        self.batches(("b1", ["A", "G", "Y"]))
        self.ok(self.run_(), 0)
        self.assertEqual(self.lands(), ["tA", "tG tY"])

    def test_a9_same_without_batches_json(self):
        for ident in ("A", "G", "X"):
            self.green(ident)
        self.ok(self.run_(), 0)
        self.assertEqual(self.lands(), ["tA tX", "tG"])

    def test_a9_existing_branch_waits_for_the_overlapping_mate(self):
        self.make_ticket("A")
        self.make_ticket("G")
        self.schedule_conf(max_workers=2)
        self.batches(("b1", ["A", "G"]))
        plan = self.plan()
        self.assertTrue([l for l in plan.stdout.splitlines()
                         if "#G" in l and "waiting(overlap #A)" in l], plan.stdout)
        self.ok(self.run_(), 0)
        calls = self.calls()
        self.assertEqual(self.lands(), ["tA", "tG"])
        self.assertGreater([i for i, c in enumerate(calls) if c.startswith("start G ")][0],
                           calls.index("land tA"))

    def test_a9_no_branch_is_files_unknown_and_dispatched_together(self):
        self.make_ticket("A")
        self.make_ticket("N")
        self.schedule_conf(max_workers=2)
        self.batches(("b1", ["A", "N"]))
        self.fake(sleep=2)
        plan = self.plan()
        self.assertTrue([l for l in plan.stdout.splitlines()
                         if "#N" in l and "files unknown" in l], plan.stdout)
        self.ok(self.run_(), 0)
        self.assertEqual(max_overlap(self.intervals()), 2, self.calls())

    def test_a9_conflict_on_the_later_call_stops(self):
        for ident in ("A", "G", "X"):
            self.green(ident)
        self.batches(("b1", ["A", "G", "X"]))
        self.fake(land=["land", {"do": "conflict", "rc": 3}])
        done = self.run_()
        self.ok(done, 1)
        self.assertEqual(self.lands(), ["tA tX", "tG"])
        pages = self.pages()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual(str(pages[0]["ticket"]), "G")
        text = self.page_text(pages[0])
        for word in ("#G", "#A", "f2"):
            self.assertIn(word, text)
        self.assertEqual(self.schedule_events()[-1]["reason"], "conflict")


# ===================================================================== A10


class A10Reland(ScheduleBase):

    def setUp(self):
        super().setUp()
        self.branch("A", {"a.txt": "a\n"})
        self.branch("G", {"g.txt": "g\n"})

    def test_a10_land_fix_on_a_new_head_relands(self):
        self.green("A")
        self.batches(("b1", ["A"]))
        self.fake(land=[{"do": "fix"}, "land"])
        done = self.run_()
        self.ok(done, 0)
        self.assertEqual(self.lands(), ["tA", "tA"])
        lands = [e for e in self.schedule_events() if e["kind"] == "schedule.land"]
        self.assertEqual(len(lands), 2)
        self.assertNotIn("reland", lands[0])
        self.assertEqual(lands[1]["reland"], "1")
        self.assertEqual([p["kind"] for p in self.pages()], ["done"])

    def test_a10_complex_without_review_on_the_new_head_stops(self):
        self.green("A", complex_=True)
        self.batches(("b1", ["A"]))
        self.fake(land=[{"do": "fix", "drop_review": True}, "land"])
        done = self.run_()
        self.ok(done, 1)
        self.assertEqual(self.lands(), ["tA"])
        pages = self.pages()
        self.assertEqual(len(pages), 1)
        self.assertIn("review.sh", self.page_text(pages[0]))

    def test_a10_same_head_is_not_relanded(self):
        self.green("A")
        self.batches(("b1", ["A"]))
        self.fake(land=[{"do": "same"}, "land"])
        self.ok(self.run_(), 1)
        self.assertEqual(self.lands(), ["tA"])

    def test_a10_reland_max(self):
        self.schedule_conf(reland_max=1)
        self.green("A")
        self.batches(("b1", ["A"]))
        self.fake(land_default={"do": "fix"})
        self.ok(self.run_(), 1)
        self.assertEqual(self.lands(), ["tA", "tA"])
        self.assertIn("reland_max", self.page_text(self.pages()[0]))

    def test_a10_reland_only_what_did_not_land(self):
        self.green("A")
        self.green("G")
        self.batches(("b1", ["A", "G"]))
        self.fake(land=[{"do": "fix", "tickets": ["A"], "land_also": ["G"]}, "land"])
        self.ok(self.run_(), 0)
        self.assertEqual(self.lands(), ["tA tG", "tA"])


# ===================================================================== A11


class A11StateAndResume(ScheduleBase):

    def test_a11_state_is_written_before_each_spawn(self):
        for ident in ("A", "G", "X"):
            self.make_ticket(ident)
        self.batches(("b1", ["A", "G", "X"]))
        self.schedule_conf(max_workers=1)
        self.fake(snapshot=True)
        self.ok(self.run_(), 0)
        with open(os.path.join(self.fake_dir, "state-G.json"), encoding="utf-8") as handle:
            snap = json.load(handle)
        pid_g = [int(c.split()[3]) for c in self.calls() if c.startswith("start G ")][0]
        self.assertEqual(snap["children"]["G"]["pid"], pid_g)
        self.assertNotIn("A", snap["children"])
        self.assertEqual(snap["batches"][0]["id"], "b1")
        self.assertEqual(snap["batches"][0]["status"], "dispatching")
        self.assertIsNone(snap["stop"])
        self.assertEqual(self.state()["stop"]["reason"], "done")

    def interrupted(self, child, run_id="20261026-000000-1", status="dispatching"):
        self.green("A")
        self.make_ticket("G", state="Running")
        self.batches(("b1", ["A", "G"]))
        state = {"schema": 1, "run_id": run_id, "pid": absent_pid(), "boot_id": current_boot(),
                 "started": "2026-10-26T00:00:00+00:00",
                 "batches": [{"id": "b1", "tickets": ["A", "G"], "status": status,
                              "pre_land_heads": {}, "relands": 0}],
                 "children": {"G": child} if child else {}, "stop": None}
        self.write(os.path.join("reports", "schedule", "state.json"), json.dumps(state))
        self.write(os.path.join("reports", "schedule", run_id, "schedule.log"), "earlier line\n")
        return run_id

    def test_a11_resume_respawns_the_orphan(self):
        for child in ({"pid": absent_pid(), "boot_id": current_boot(), "started": "x"},
                      {"pid": 1, "boot_id": "00000000-dead", "started": "x"}):
            with self.subTest(child=child):
                for leftover in ("calls.log",):
                    path = os.path.join(self.fake_dir, leftover)
                    if os.path.exists(path):
                        os.remove(path)
                if os.path.exists(os.path.join(self.repo, "board", "events.jsonl")):
                    os.remove(os.path.join(self.repo, "board", "events.jsonl"))
                run_id = self.interrupted(child)
                done = self.run_()
                self.ok(done, 0)
                resumes = [e for e in self.schedule_events() if e["kind"] == "schedule.resume"]
                self.assertEqual([e["run_id"] for e in resumes], [run_id])
                log = self.read(os.path.join("reports", "schedule", run_id, "schedule.log"))
                self.assertTrue(log.startswith("earlier line\n"))
                self.assertGreater(len(log.splitlines()), 1)
                self.assertEqual(self.starts(), ["G"])
                self.assertEqual(self.lands(), ["tA tG"])

    def test_a11_resume_after_everything_landed_meanwhile_is_done(self):
        run_id = self.interrupted(None)
        main = self.git("rev-parse", "main").strip()
        self.make_ticket("A", state="Done", landed_sha=main)
        self.make_ticket("G", state="Done", landed_sha=main)
        done = self.run_()
        self.ok(done, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual([p["kind"] for p in self.pages()], ["done"])
        self.assertEqual(self.state()["run_id"], run_id)
        self.assertEqual(self.state()["batches"][0]["status"], "landed")

    def test_a11_running_not_in_children_is_another_driver(self):
        self.interrupted(None)
        done = self.run_()
        self.ok(done, 2)
        self.assertIn("G", done.stdout)
        self.assertIn("another driver", done.stdout)
        self.assertEqual(self.calls(), [])

    def test_a11_landing_with_land_lock_present_stops(self):
        self.interrupted(None, status="landing")
        self.green("G")
        os.makedirs(os.path.join(self.repo, ".land.lock"))
        done = self.run_()
        self.ok(done, 1)
        self.assertEqual(self.lands(), [])
        pages = self.pages()
        self.assertEqual(len(pages), 1)
        self.assertIn(".land.lock", self.page_text(pages[0]))
        self.assertTrue(self.exists(".land.lock"))

    def test_a11_landing_without_land_lock_lands_again(self):
        self.interrupted(None, status="landing")
        self.green("G")
        self.ok(self.run_(), 0)
        self.assertEqual(self.lands(), ["tA tG"])
        lands = [e for e in self.schedule_events() if e["kind"] == "schedule.land"]
        self.assertEqual(lands[0]["reland"], "1")


if __name__ == "__main__":
    unittest.main()
