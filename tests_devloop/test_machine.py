"""machine slots, the exclusive regression and the host sampler"""

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
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import AGENT, HERE, HOME, LOOP, ROLES, TESTS, GitIsolatedCase, LoopCase, git, own_origin  # noqa: E402,F401
from core import dag as dagmod  # noqa: E402
from core import osx, pages  # noqa: E402
from core import pool as pool_mod  # noqa: E402
from core.pool import Pool, shown_state, TICKET_STATES  # noqa: E402



def run_worker_in_process(case, timeout):
    """Issue #7: run a worker (the opener takes no slot) in this process with the worker command
    T30Acceptance.done_worker sets; returns the path of the hand-in it wrote."""
    from unittest import mock
    from core import agents
    from core.config import Config
    patch = mock.patch.dict(os.environ)
    patch.start()
    case.addCleanup(patch.stop)
    for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
        del os.environ[key]
    os.environ["AC_ROOT"] = case.proj
    with open(os.path.join(case.proj, "board", "config.json")) as fh:
        agents_cfg = json.load(fh)["devloop"]["agents"]
    script = "cat >/dev/null; echo '{\"kind\": \"done\"}' > \"$DEVLOOP_RESULT\""
    case.config(agents=dict(agents_cfg, worker=["sh", "-c", script]))
    run_dir = os.path.join(case.tmp, "run-worker")
    hand = agents.run(Config(case.proj), "worker", "go", case.proj, run_dir, timeout)
    case.assertIsNone(hand.problem())
    return os.path.join(run_dir, "result.json")


class ExclusiveRegression(LoopCase):
    """devloop.regression_exclusive: the implementer's tests (gate and whole-machine take in core/slots.py)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("DEVLOOP_SLOT", None)     # this process is not nested under a slot
        os.environ.pop("DEVLOOP_MACHINE", None)  # nor under the holder of the machine
        os.environ["AC_ROOT"] = self.proj
        self.slot_dir = os.path.join(self.tmp, "wt", "slots")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def test_off_by_default_and_only_a_bool(self):
        import time
        from core import slots
        self.config(machine_slots=1)
        self.assertIs(self.cfg().regression_exclusive, False)
        slot = slots.take(self.cfg(), time.time() + 1)
        slot.release()
        self.assertEqual(os.listdir(self.slot_dir), ["slot-0"])     # no gate file when off
        for bad in (1, 0, "true", None, [True]):
            self.config(machine_slots=1, regression_exclusive=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                self.cfg()
            self.assertIn("devloop.regression_exclusive", str(ctx.exception))
        self.config(machine_slots=None, regression_exclusive=False)
        self.assertIs(self.cfg().regression_exclusive, False)

    def test_exclusive_needs_slots(self):
        self.config(machine_slots=None, regression_exclusive=True)
        with self.assertRaises(osx.OSFailure) as ctx:
            self.cfg()
        self.assertIn("devloop.regression_exclusive", str(ctx.exception))
        self.assertIn("devloop.machine_slots", str(ctx.exception))

    def test_the_machine_take_blocks_new_test_runs_but_not_agents(self):
        import time
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        cfg = self.cfg()
        machine = slots.take_machine(cfg, time.time() + 2)
        self.assertTrue(machine and machine.held)
        self.assertEqual(machine.path, os.path.join(self.slot_dir, "exclusive"))
        began = time.time()
        self.assertIsNone(slots.take_test_run(cfg, time.time() + 0.6))   # mark held: no test run
        self.assertGreaterEqual(time.time() - began, 0.5)
        agent = slots.take(cfg, time.time() + 1)                         # an agent still gets its slot
        self.assertTrue(agent and agent.held)
        self.addCleanup(agent.release)
        machine.release()
        machine.release()                                                # idempotent
        run = slots.take_test_run(cfg, time.time() + 1)
        self.assertTrue(run and run.held)
        self.assertEqual(os.path.basename(run.path), "slot-1")
        run.release()
        self.assertEqual(sorted(os.listdir(self.slot_dir)), ["exclusive", "slot-0", "slot-1", "tests"])

    def test_a_machine_take_waits_for_test_runs_in_flight_and_gives_up_cleanly(self):
        import fcntl
        import time
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        cfg = self.cfg()
        held = slots.take_test_run(cfg, time.time() + 1)
        self.assertEqual(os.path.basename(held.path), "slot-0")
        self.assertIsNone(slots.take_machine(cfg, time.time() + 0.6))
        # nothing left behind: the mark and slot-1 can be locked exclusively again
        for name in ("exclusive", "slot-1"):
            with open(os.path.join(self.slot_dir, name), "a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.release()
        machine = slots.take_machine(cfg, time.time() + 1)
        self.assertTrue(machine and machine.held)
        machine.release()

    def test_a_shut_gate_alone_keeps_test_runs_off_free_slots(self):
        import fcntl
        import time
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        os.makedirs(self.slot_dir, exist_ok=True)
        fh = open(os.path.join(self.slot_dir, "exclusive"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        began = time.time()
        self.assertIsNone(slots.take_test_run(self.cfg(), time.time() + 0.6))
        self.assertGreaterEqual(time.time() - began, 0.5)
        agent = slots.take(self.cfg(), time.time() + 1)     # an agent's slot is still taken
        self.assertTrue(agent and agent.held)
        agent.release()
        fcntl.flock(fh, fcntl.LOCK_UN)
        slot = slots.take_test_run(self.cfg(), time.time() + 1)
        self.assertTrue(slot and slot.held)
        slot.release()

    def test_a_nested_run_ignores_the_gate(self):
        import fcntl
        import time
        from core import slots
        self.config(machine_slots=1, regression_exclusive=True)
        os.makedirs(self.slot_dir, exist_ok=True)
        fh = open(os.path.join(self.slot_dir, "exclusive"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        os.environ["DEVLOOP_SLOT"] = os.path.join(self.slot_dir, "slot-0")
        began = time.time()
        self.assertFalse(slots.take(self.cfg(), time.time() + 5).held)
        self.assertLess(time.time() - began, 0.5)

    def test_a_regression_holding_the_mark_that_dies_blocks_nobody(self):
        import time
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        code = ("import sys, time; sys.path.insert(0, %r)\n"
                "from core.config import Config\nfrom core import slots\n"
                "m = slots.take_machine(Config(%r), time.time() + 10)\n"
                "print('held' if m and m.held else 'none', flush=True)\ntime.sleep(60)\n" % (LOOP, self.proj))
        child = subprocess.Popen(["python3", "-c", code], stdout=subprocess.PIPE, text=True,
                                 env=os.environ.copy())
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "held")
        cfg = self.cfg()
        self.assertIsNone(slots.take_test_run(cfg, time.time() + 0.6))
        child.terminate()
        child.wait(timeout=10)
        began = time.time()
        slot = slots.take_test_run(cfg, time.time() + 3)
        self.assertTrue(slot and slot.held)
        self.assertLess(time.time() - began, 3.0)
        slot.release()

    def test_a_regression_without_the_whole_machine_is_unmeasured_and_releases(self):
        import fcntl
        import time
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=2)
        held = slots.take_test_run(self.cfg(), time.time() + 1)     # a test run in flight
        self.addCleanup(held.release)
        out = os.path.join(self.tmp, "regression.json")
        began = time.time()
        res = self.run_py("regression.py", "--branch", "main", "--out", out, timeout=120)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertGreaterEqual(time.time() - began, 2.0)
        with open(out) as fh:
            got = json.load(fh)
        self.assertEqual(got["status"], "unmeasured")
        self.assertIn("the whole machine (devloop.regression_exclusive: no other test run) did not come "
                      "free within", got["summary"])
        for name in ("exclusive", "slot-1"):
            with open(os.path.join(self.slot_dir, name), "a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_readme_describes_it(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            section = fh.read().split("## Machine slots", 1)[1].split("\n## ", 1)[0]
        for words in ("regression_exclusive", "slots/exclusive", "needs `machine_slots`"):
            self.assertIn(words, section)


class T4Acceptance(LoopCase):
    """Ticket #4: devloop.regression_exclusive - a regression takes the whole machine."""

    def recorded(self):
        with open(self.log) as fh:
            rows = [line.split() for line in fh if line.strip()]
        return [(" ".join(r[:-2]), float(r[-2]), float(r[-1])) for r in rows]

    def test_v1_no_regression_runs_beside_an_issuer_test_run(self):
        """Items 3-4, as #143 left them: with the machine taken, a regression overlaps no test run
        (agents may run beside it)."""
        self.config(machine_slots=2, regression_exclusive=True, max_parallel=2)
        self.log = os.path.join(self.tmp, "intervals.txt")
        self.env.update(FAKE_INTERVALS=self.log, FAKE_HOLD="1.5")
        self.add(1)
        self.add(2)
        self.plan(worker={"2": ["slow"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual((self.state(1), self.state(2)), ("landed", "landed"))
        runs = self.recorded()
        names = [r[0] for r in runs]
        self.assertIn("tests full", names)
        self.assertIn("tests preflight", names)
        self.assertIn("agent worker 2", names)
        self.assertIn("tests run", names)
        regressions = [r for r in runs if r[0] in ("tests preflight", "tests full")]
        others = [r for r in runs if r[0] == "tests run"]
        overlaps = [(g, o) for g in regressions for o in others if g[1] < o[2] and o[1] < g[2]]
        self.assertEqual(overlaps, [], runs)

    def test_v2_a_held_mark_keeps_a_test_run_waiting_but_not_a_worker(self):
        """Item 3 as #143 reversed it: while another process holds LOCK_EX on slots/exclusive, a
        worker still takes its slot and runs; a test run waits until the mark is released."""
        import fcntl
        import threading
        from core import slots as slotmod
        from core.config import Config
        slots = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slots, exist_ok=True)
        fh = open(os.path.join(slots, "exclusive"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        released = []
        timer = threading.Timer(3, lambda: (released.append(time.time()), fh.close()))
        self.addCleanup(timer.cancel)
        self.config(machine_slots=2, regression_exclusive=True, agent_timeout_seconds=2)
        timer.start()
        result = run_worker_in_process(self, 2)     # also strips DEVLOOP_* from os.environ
        self.assertEqual(released, [], "the worker waited for the exclusive mark")
        self.assertTrue(os.path.exists(result))
        run = slotmod.take_test_run(Config(self.proj), None)
        self.addCleanup(run.release)
        self.assertTrue(released, "the test run did not wait for the mark")
        self.assertTrue(run.held)

    def test_v3_exclusive_without_machine_slots_stops_every_script(self):
        """Item 2: regression_exclusive true needs machine_slots."""
        for absent in (True, False):
            if absent:
                self.config(regression_exclusive=True)
            else:
                self.config(regression_exclusive=True, machine_slots=None)
            res = self.run_py("tickets.py", "list")
            self.assertEqual(res.returncode, 2, "machine_slots %s: %s" % (
                "absent" if absent else "null", res.stdout + res.stderr))
            self.assertIn("devloop.regression_exclusive", res.stderr)
            self.assertIn("devloop.machine_slots", res.stderr)


class T143Acceptance(LoopCase):
    """Ticket #143: while an exclusive regression holds the machine, agents keep running and only
    test runs wait (core/slots.take_test_run, DEVLOOP_MACHINE)."""

    BUSY = ("run_tests: machine busy (a full test run holds the machine or every machine slot is "
            "taken); nothing was run")
    RULE = ("Run tests only with that command, never with another test runner or the project's own "
            "test scripts. When it prints machine busy it ran nothing: do not wait for it or retry it "
            "in a loop; go on, and name in your hand-in the checks that were not measured (machine busy).")

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        os.environ["AC_ROOT"] = self.proj
        self.slot_dir = os.path.join(self.tmp, "wt", "slots")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def exclusive(self, **extra):
        self.config(machine_slots=2, regression_exclusive=True, **extra)

    def hold_mark(self, seconds):
        """Another process (this test process) holds LOCK_EX on slots/exclusive for `seconds`;
        returns the list the release time is appended to."""
        import fcntl
        import threading
        os.makedirs(self.slot_dir, exist_ok=True)
        fh = open(os.path.join(self.slot_dir, "exclusive"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        released = []
        timer = threading.Timer(seconds, lambda: (released.append(time.time()), fh.close()))
        self.addCleanup(timer.cancel)
        timer.start()
        return released

    def set_tests(self, **commands):
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["tests"].update(commands)
        with open(path, "w") as fh:
            json.dump(cfg, fh)

    def regression(self, timeout=120):
        out = os.path.join(self.tmp, "regression.json")
        began = time.time()
        res = self.run_py("regression.py", "--branch", "main", "--out", out, timeout=timeout)
        elapsed = time.time() - began
        self.assertTrue(os.path.exists(out), res.stdout + res.stderr)
        with open(out) as fh:
            return res, json.load(fh), elapsed

    def intervals(self):
        path = os.path.join(self.tmp, "intervals.txt")
        self.env["FAKE_INTERVALS"] = path
        return path

    # ---- item 1
    def test_item1_a_nested_run_tests_says_machine_busy_while_the_mark_is_held(self):
        self.exclusive()
        self.hold_mark(20)
        log = self.intervals()
        self.env["DEVLOOP_SLOT"] = os.path.join(self.slot_dir, "slot-0")
        began = time.time()
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        elapsed = time.time() - began
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertEqual(res.stdout, "")
        self.assertEqual(res.stderr.strip(), self.BUSY)
        self.assertLess(elapsed, 5)
        if os.path.exists(log):
            with open(log) as fh:
                self.assertEqual(fh.read(), "", "a test command was started")

    # ---- item 3
    def test_item3_a_worker_hands_in_while_the_mark_is_held_and_takes_one_slot(self):
        from core import agents
        self.exclusive()
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            agents_cfg = json.load(fh)["devloop"]["agents"]
        script = ("cat >/dev/null; echo \"{\\\"kind\\\": \\\"done\\\", \\\"slot\\\": \\\"$DEVLOOP_SLOT\\\"}\" "
                  "> \"$DEVLOOP_RESULT\"")
        self.config(machine_slots=2, regression_exclusive=True,
                    agents=dict(agents_cfg, worker=["sh", "-c", script]))
        released = self.hold_mark(8)
        run_dir = os.path.join(self.tmp, "run-worker")
        hand = agents.run(self.cfg(), "worker", "go", self.proj, run_dir, 30)
        self.assertIsNone(hand.problem())
        result = os.path.join(run_dir, "result.json")
        self.assertTrue(released == [] or os.path.getmtime(result) < released[0],
                        "the worker waited for the exclusive mark")
        self.assertEqual(released, [], "the mark was released before the worker handed in")
        self.assertIn(hand.result["slot"], [os.path.join(self.slot_dir, "slot-%d" % i) for i in range(2)])

    # ---- item 4
    def test_item4_an_exclusive_regression_does_not_wait_for_an_agent_slot(self):
        import threading
        from core import slots
        self.exclusive(regression_timeout_seconds=10)
        slot = slots.take(self.cfg(), time.time() + 2)
        self.assertTrue(slot and slot.held)
        self.assertTrue(os.path.exists(os.path.join(self.slot_dir, "slot-0")))
        self.addCleanup(slot.release)
        timer = threading.Timer(20, slot.release)
        self.addCleanup(timer.cancel)
        timer.start()
        res, got, elapsed = self.regression()
        self.assertEqual(got["status"], "green", got)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertLess(elapsed, 9, "the regression waited for the agent's slot")

    # ---- item 5
    def test_item5_take_machine_waits_for_a_test_run_in_flight(self):
        from core import slots
        self.exclusive()
        marker = os.path.join(self.tmp, "started")
        self.set_tests(run="touch %s; sleep 4; %s run" % (marker, TESTS))
        env = dict(self.env, DEVLOOP_SLOT=os.path.join(self.slot_dir, "slot-1"))
        child = subprocess.Popen(["python3", os.path.join(LOOP, "run_tests.py"), "case_1"], cwd=self.proj,
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait)
        self.addCleanup(lambda: child.poll() is None and child.terminate())
        limit = time.time() + 10
        while not os.path.exists(marker) and child.poll() is None and time.time() < limit:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(marker), "the child's test command never started")
        machine = slots.take_machine(self.cfg(), time.time() + 1)
        if machine is not None:
            machine.release()
        self.assertIsNone(machine, "the machine was taken beside a test run in flight")
        child.wait(timeout=30)
        machine = slots.take_machine(self.cfg(), time.time() + 2)
        self.assertTrue(machine is not None and machine.held, "the machine stayed busy after the run")
        machine.release()

    # ---- item 7
    def test_item7_the_regression_gives_its_test_commands_devloop_machine(self):
        self.exclusive()
        seen = os.path.join(self.tmp, "machine-seen.txt")
        self.set_tests(full='printf %%s "$DEVLOOP_MACHINE" > %s; %s full' % (seen, TESTS))
        res, got, _ = self.regression()
        self.assertEqual(got["status"], "green", got)
        self.assertTrue(os.path.exists(seen), res.stdout + res.stderr)
        with open(seen) as fh:
            self.assertEqual(fh.read(), os.path.join(self.slot_dir, "exclusive"))

    def test_item7_a_run_with_devloop_machine_is_a_free_pass(self):
        self.exclusive()
        self.hold_mark(20)
        log = self.intervals()
        self.env["DEVLOOP_MACHINE"] = os.path.join(self.slot_dir, "exclusive")
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertNotIn("machine busy", res.stderr)
        self.assertTrue(os.path.exists(log), "no test command was started")

    def test_item7_loop_case_drops_devloop_machine_from_its_env(self):
        class Probe(LoopCase):
            def runTest(self):
                pass
        os.environ["DEVLOOP_MACHINE"] = "/somewhere/slots/exclusive"
        os.environ["DEVLOOP_SLOT"] = "/somewhere/slots/slot-0"
        probe = Probe()
        try:
            probe.setUp()
            self.assertFalse("DEVLOOP_SLOT" in probe.env, "LoopCase kept DEVLOOP_SLOT in self.env")
            self.assertFalse("DEVLOOP_MACHINE" in probe.env, "LoopCase kept DEVLOOP_MACHINE in self.env")
        finally:
            probe.doCleanups()

    # ---- item 9
    def test_item9_agent_prompts_say_run_tests_only_with_the_command(self):
        for name, line in (("worker.md", "Run them with: {test_cmd}"),
                           ("verifier.md", "Run them with: {test_cmd}"),
                           ("fixer.md", "Run the failing tests with: {test_cmd}")):
            with self.subTest(prompt=name):
                with open(os.path.join(LOOP, "prompts", name)) as fh:
                    text = fh.read()
                self.assertIn(line, text)
                rest = " ".join(text.split(line, 1)[1].split())
                self.assertTrue(rest.startswith(self.RULE), rest[:300])

    # ---- item 10
    def test_item10_slot_waits_no_longer_give_way_to_the_exclusive_regression(self):
        import board
        from core import inflight

        def alive(started):
            p = subprocess.Popen(["sleep", "60"])
            self.addCleanup(p.wait)
            self.addCleanup(p.terminate)
            return {"pid": p.pid, "host": osx.host(), "boot": osx.boot_id(), "started": started}
        self.exclusive()
        inflight.put(self.cfg(), "agent-w", dict(
            kind="agent", stamp=alive("2026-11-06T10:00:00Z"), parent=None, role="worker", ticket=None,
            group="g1", round=1, run_dir=None, log=None, command="c", model=None, effort=None, state="slot",
            since="2026-11-06T10:00:00Z", until=None, result=None))
        inflight.put(self.cfg(), "regression-x", dict(
            kind="regression", stamp=alive("2026-11-06T09:30:00Z"), group="g7", branch="b", sha="a" * 40,
            exclusive=True, stage="full", since="2026-11-06T09:30:00Z", result=None))
        rows = [r for r in board.live_rows(self.cfg(), self.snap()) if r["name"] == "agent-w"]
        self.assertEqual(len(rows), 1)
        self.assertIn("等機器名額", rows[0]["text"])
        self.assertNotIn("讓位", rows[0]["text"])
        self.assertNotIn("data-yield", rows[0]["attrs"])
        page = board.render(self.cfg(), self.snap(), query={"tab": "live"})
        self.assertIn("等機器名額", page)
        self.assertNotIn("讓位", page)
        self.assertNotIn("data-yield", page)


class TestHostSampler(LoopCase):
    """#44, implementer's own: the sampler thread's failure handling and stop, and the README section."""

    def setUp(self):
        super().setUp()
        self.env.pop("DEVLOOP_ROLE", None)
        self.env.pop("DEVLOOP_RUN_DIR", None)

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def test_sampler_writes_at_start_and_stops_its_thread(self):
        import io
        from core import host
        self.config(host_sample_seconds=1)
        err = io.StringIO()
        sampler = host.Sampler(self.cfg(), out=err).start()
        thread = sampler._thread
        time.sleep(1.5)
        sampler.stop()
        self.assertFalse(thread.is_alive())
        path = os.path.join(self.tmp, "wt", "runs", "host",
                            __import__("socket").gethostname().replace(os.sep, "_") + ".jsonl")
        with open(path) as fh:
            rows = [json.loads(line) for line in fh.read().splitlines()]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(sorted(rows[0]), sorted(host.KEYS))
        self.assertEqual(err.getvalue(), "")
        count = len(rows)
        time.sleep(1.3)
        with open(path) as fh:
            self.assertEqual(len(fh.read().splitlines()), count, "a stopped sampler wrote on")

    def test_null_never_starts_a_thread(self):
        from core import host
        self.config(host_sample_seconds=None)
        sampler = host.Sampler(self.cfg()).start()
        self.assertIsNone(sampler._thread)
        sampler.stop()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "runs", "host")))

    def test_failed_samples_print_one_line_each_and_sampling_goes_on(self):
        import io
        from core import host
        self.config(host_sample_seconds=1)
        os.makedirs(os.path.join(self.tmp, "wt", "runs"))
        with open(os.path.join(self.tmp, "wt", "runs", "host"), "w") as fh:
            fh.write("a file where the folder belongs\n")
        err = io.StringIO()
        sampler = host.Sampler(self.cfg(), out=err).start()
        time.sleep(1.5)
        sampler.stop()
        lines = err.getvalue().splitlines()
        self.assertGreaterEqual(len(lines), 2, err.getvalue())
        for line in lines:
            self.assertTrue(line.startswith("ticket_controller: host sample failed: "), line)

    def test_controller_lands_although_every_sample_fails(self):
        self.config(host_sample_seconds=1)
        os.makedirs(os.path.join(self.tmp, "wt", "runs"))
        with open(os.path.join(self.tmp, "wt", "runs", "host"), "w") as fh:
            fh.write("x\n")
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().ticket("1")["state"], "landed")
        failed = [l for l in res.stderr.splitlines() if "host sample failed" in l]
        self.assertGreaterEqual(len(failed), 1, res.stderr)

    def test_sample_before_the_worktree_root_exists_measures_its_parent(self):
        import shutil
        from core import host
        self.config()
        root = os.path.join(self.tmp, "wt")
        self.assertFalse(os.path.exists(root))
        self.assertEqual(host.disk_free(os.path.join(root, "a", "b")) > 0, True)
        row = host.sample(self.cfg())
        self.assertIsInstance(row["disk_free"], int)
        self.assertFalse(os.path.exists(root), "sampling must not create the root")
        os.makedirs(root)
        self.assertGreater(host.disk_free(root), 0)
        self.assertLessEqual(abs(host.disk_free(root) - shutil.disk_usage(root).free), 1 << 24)

    def test_readme_section_sits_before_dashboard_and_names_the_rules(self):
        with open(os.path.join(LOOP, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        heads = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertIn("## Host samples", heads)
        self.assertEqual(heads[heads.index("## Host samples") + 1], "## Dashboard")
        section = text.split("## Host samples", 1)[1].split("## Dashboard", 1)[0]
        flat = " ".join(section.split())
        for words in ("runs/host/<hostname>.jsonl", "devloop.host_sample_seconds", "default 60",
                      "null = off", "7 days", "8 days", "DEVLOOP_RUN_DIR", "DEVLOOP_ROLE",
                      "non-blocking", "mem_available", "disk_free", "slots_used", "load1"):
            self.assertIn(words, flat)


class TestMemoryHold(LoopCase):
    """#145, implementer's own: the memory_short reading, the idle line's format, the setting's
    validation, the hold record and that a running issuer is left alone."""

    GIB = 2 ** 30

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)

    def ctl(self, **devloop):
        import ticket_controller
        from core.config import Config, pool_for
        self.config(**devloop)
        cfg = Config(self.proj)
        return ticket_controller.Controller(cfg, pool_for(cfg))

    def reading(self, value):
        from core import host
        return mock.patch.object(host, "memory_available", return_value=value)

    def test_unknown_reading_is_never_short(self):
        ctl = self.ctl(min_free_memory_gib=1)
        with self.reading(None):
            self.assertIsNone(ctl.memory_short())

    def test_null_setting_is_never_short_and_does_not_read(self):
        ctl = self.ctl(min_free_memory_gib=None)
        with self.reading(0) as read:
            self.assertIsNone(ctl.memory_short())
        read.assert_not_called()

    def test_float_setting_and_zero_bytes(self):
        ctl = self.ctl(min_free_memory_gib=1.5)
        with self.reading(self.GIB):
            self.assertEqual(ctl.memory_short(), self.GIB)
        with self.reading(int(1.5 * self.GIB)):
            self.assertIsNone(ctl.memory_short(), "exactly the setting is not short")
        with self.reading(0):
            self.assertEqual(ctl.memory_short(), 0)

    def test_idle_line_formats_available_and_setting(self):
        self.assertEqual(self.ctl(min_free_memory_gib=1.5).memory_line(self.GIB // 2),
                         "memory short: 0.5 GiB available, below devloop.min_free_memory_gib 1.5: "
                         "no new agent or regression is started until more is free")
        self.assertIn("1.0 GiB available, below devloop.min_free_memory_gib 2:",
                      self.ctl(min_free_memory_gib=2).memory_line(self.GIB + 1))

    def test_setting_validation_and_default(self):
        from core import osx
        from core.config import Config
        for bad in (0, -1, True, "1", []):
            self.config(min_free_memory_gib=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                Config(self.proj)
            self.assertIn("devloop.min_free_memory_gib", str(ctx.exception))
        for good in (None, 0.25, 3):
            self.config(min_free_memory_gib=good)
            self.assertEqual(Config(self.proj).values["min_free_memory_gib"], good)
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["devloop"].pop("min_free_memory_gib")
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        self.assertEqual(Config(self.proj).values["min_free_memory_gib"], 1)

    def test_hold_record_keeps_since_and_goes_when_memory_is_free(self):
        from core import inflight, osx
        ctl = self.ctl(min_free_memory_gib=1)
        path = os.path.join(self.tmp, "wt", "runs", "live", "hold-memory.json")
        with self.reading(self.GIB // 2):
            ctl.keep_hold_record()
            first = osx.read_json(path)
            ctl.keep_hold_record()
            second = osx.read_json(path)
        self.assertEqual(first["since"], second["since"])
        self.assertEqual((second["kind"], second["reason"], second["available"], second["threshold"],
                          second["setting"], second["result"]),
                         ("hold", "memory", self.GIB // 2, self.GIB, 1, None))
        self.assertEqual(second["stamp"]["pid"], os.getpid())
        self.assertEqual(inflight.status(second), "running")
        with self.reading(None):
            ctl.keep_hold_record()
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(ctl.hold_since)

    def test_fix_round_due_while_short_waits_as_fix_retry(self):
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "fixing", "branch": "int/g1",
                                                  "members": [], "fix_round": 2, "history": []}),
                         "test: g1")
        ctl = self.ctl(min_free_memory_gib=1)
        with self.reading(self.GIB // 2):
            ctl.start_fixer("g1", "/r.json", 3)
        g = self.snap().groups["g1"]
        self.assertEqual(ctl.children, {})
        self.assertEqual((g["state"], g["fix_retry"], g["fix_round"], g["fix_report"], g.get("job")),
                         ("pr", True, 2, "/r.json", None))
        self.assertEqual(g["history"][-1]["event"], "fix round 3 waits: memory short (0.5 GiB available)")

    def test_running_issuer_is_not_stopped_and_counts_as_busy(self):
        from core import osx
        self.add(1)
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.wait)
        self.addCleanup(osx.kill_tree, proc.pid)

        def change(tx):
            t = tx.ticket("1")
            t["state"], t["run"], t["claimed_at"] = "running", None, osx.now()
            tx.put_ticket(t)
        self.pool.update(change, "test: #1 running")
        ctl = self.ctl(min_free_memory_gib=1)
        ctl.children["issuer:1"] = proc
        with self.reading(self.GIB // 2):
            self.assertTrue(ctl.tick())
        self.assertIsNone(proc.poll())
        self.assertIs(ctl.children.get("issuer:1"), proc)
        self.assertEqual(self.snap().ticket("1")["state"], "running")


class T44Acceptance(LoopCase):
    """Ticket #44: while it runs, the controller samples the machine into
    <worktrees>/runs/host/<hostname>.jsonl."""

    KEYS = {"at", "host", "load1", "load5", "cpus", "mem_available", "disk_free", "agents",
            "slots_used", "slots"}
    X = 1767225600            # 2026-01-01T00:00:00Z
    DAY = 86400

    def setUp(self):
        super().setUp()
        from unittest import mock
        for name in ("DEVLOOP_ROLE", "DEVLOOP_RUN_DIR", "AC_PROC_ROOT", "AC_PS", "AC_CGROUP_ROOT",
                     "AC_MEMINFO", "AC_VM_STAT", "AC_SYSCTL"):
            self.env.pop(name, None)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for name in ("DEVLOOP_SLOT", "AC_PROC_ROOT", "AC_PS", "AC_CGROUP_ROOT", "AC_MEMINFO",
                     "AC_VM_STAT", "AC_SYSCTL"):
            os.environ.pop(name, None)
        os.environ["AC_ROOT"] = self.proj

    # ------------------------------------------------------------ helpers

    def host(self):
        self.assertTrue(os.path.isfile(os.path.join(LOOP, "core", "host.py")), "no devloop/core/host.py")
        from core import host
        return host

    def has(self, mod, name, *params):
        self.assertTrue(hasattr(mod, name), "no %s" % name)
        import inspect
        got = inspect.signature(getattr(mod, name)).parameters
        for p in params:
            self.assertIn(p, got, "%s has no parameter %s" % (name, p))
        return getattr(mod, name)

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    @staticmethod
    def ts(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)
        return path

    def host_file(self):
        import socket
        return os.path.join(self.tmp, "wt", "runs", "host",
                            socket.gethostname().replace(os.sep, "_") + ".jsonl")

    def check_row(self, row):
        self.assertEqual(set(row), self.KEYS, row)
        import socket
        self.assertEqual(row["host"], socket.gethostname())
        self.assertEqual(row["cpus"], os.cpu_count())
        for key in ("load1", "load5"):
            self.assertTrue(row[key] is None or isinstance(row[key], (int, float)), row)
            self.assertNotIsInstance(row[key], bool)
        for key in ("mem_available", "disk_free", "agents", "slots_used"):
            self.assertTrue(row[key] is None or (isinstance(row[key], int)
                                                 and not isinstance(row[key], bool)), (key, row))
        self.assertIsInstance(row["disk_free"], int)

    def proc_tree(self):
        root = os.path.join(self.tmp, "proc")

        def env(name, pairs):
            os.makedirs(os.path.join(root, name))
            with open(os.path.join(root, name, "environ"), "wb") as fh:
                fh.write(b"".join(("%s=%s" % kv).encode() + b"\0" for kv in pairs))
        env("10", [("HOME", "/h"), ("DEVLOOP_ROLE", "worker"), ("DEVLOOP_RUN_DIR", "/r/1")])
        env("11", [("DEVLOOP_ROLE", "worker"), ("DEVLOOP_RUN_DIR", "/r/1"), ("PATH", "/bin")])
        env("12", [("DEVLOOP_RUN_DIR", "/r/2"), ("DEVLOOP_ROLE", "verifier")])
        env("13", [("DEVLOOP_RUN_DIR", "/r/3"), ("HOME", "/h")])
        os.makedirs(os.path.join(root, "14"))
        env("self", [("DEVLOOP_ROLE", "worker"), ("DEVLOOP_RUN_DIR", "/r/9")])
        return root

    def fake_ps(self, code=0):
        log = os.path.join(self.tmp, "ps-args.txt")
        body = ('printf "%%s\\n" "$@" > %s\n' % log
                + "printf '%s\\n' 'python3 a.py DEVLOOP_ROLE=worker DEVLOOP_RUN_DIR=/r/1 HOME=/h'\n"
                + "printf '%s\\n' 'claude -p DEVLOOP_RUN_DIR=/r/1 DEVLOOP_ROLE=worker'\n"
                + "printf '%s\\n' 'claude -p DEVLOOP_ROLE=verifier DEVLOOP_RUN_DIR=/r/2 PATH=/bin'\n"
                + "printf '%s\\n' 'bash DEVLOOP_RUN_DIR=/r/3 HOME=/h'\n"
                + "exit %d\n" % code)
        return self.script("ps-%d" % code, body), log

    def write_lines(self, rows):
        path = self.host_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for r in rows:
                fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        return path

    def read_ats(self, path):
        with open(path) as fh:
            return [json.loads(line)["at"] for line in fh if line.strip()]

    # ------------------------------------------------------------ items 1 and 2

    def test_sample_has_exactly_the_keys_and_at_is_now(self):
        host = self.host()
        sample = self.has(host, "sample", "cfg", "now")
        self.config(machine_slots=None)
        row = sample(self.cfg(), now=self.X)
        self.check_row(row)
        self.assertEqual(row["at"], self.ts(self.X))
        self.assertEqual(row["at"], "2026-01-01T00:00:00Z")
        self.assertIsNone(row["slots"])
        self.assertIsNone(row["slots_used"])
        try:
            load = os.getloadavg()
        except OSError:
            load = None
        if load is None:
            self.assertIsNone(row["load1"])
            self.assertIsNone(row["load5"])
        else:
            self.assertAlmostEqual(row["load1"], load[0], delta=2.0)
            self.assertAlmostEqual(row["load5"], load[1], delta=2.0)
        self.assertAlmostEqual(row["disk_free"], shutil.disk_usage(self.tmp).free, delta=512 * 2 ** 20)
        self.assertEqual(json.loads(json.dumps(row)), row)
        self.config(machine_slots=3)
        row = sample(self.cfg(), now=self.X + 61)
        self.check_row(row)
        self.assertEqual(row["at"], "2026-01-01T00:01:01Z")
        self.assertEqual(row["slots"], 3)
        self.assertEqual(row["slots_used"], 0)

    # ------------------------------------------------------------ item 3

    def test_mem_available_from_meminfo(self):
        host = self.host()
        sample = self.has(host, "sample", "cfg", "now")
        cg = os.path.join(self.tmp, "cgroup")
        os.makedirs(cg)
        meminfo = os.path.join(self.tmp, "meminfo")
        with open(meminfo, "w") as fh:
            fh.write("MemTotal:       8192 kB\nMemAvailable:    2048 kB\n")
        os.environ["AC_CGROUP_ROOT"] = cg
        os.environ["AC_MEMINFO"] = meminfo
        self.assertEqual(sample(self.cfg(), now=self.X)["mem_available"], 2097152)

    def test_mem_available_from_vm_stat(self):
        host = self.host()
        sample = self.has(host, "sample", "cfg", "now")
        cg = os.path.join(self.tmp, "cgroup")
        os.makedirs(cg)
        os.environ["AC_CGROUP_ROOT"] = cg
        os.environ["AC_MEMINFO"] = os.path.join(self.tmp, "no-meminfo")
        os.environ["AC_VM_STAT"] = self.script(
            "vm_stat", "printf '%s\\n' 'Mach Virtual Memory Statistics: (page size of 4096 bytes)' "
            "'Pages free: 10.' 'Pages inactive: 5.'\n")
        os.environ["AC_SYSCTL"] = self.script("sysctl", "printf '%s\\n' 99999999\n")
        self.assertEqual(sample(self.cfg(), now=self.X)["mem_available"], 61440)

    # ------------------------------------------------------------ item 4

    def test_memory_helpers_moved_to_hostinfo(self):
        import ast
        scripts = os.path.join(HOME, "scripts")
        hostinfo = os.path.join(scripts, "hostinfo.py")
        self.assertTrue(os.path.isfile(hostinfo), "no scripts/hostinfo.py")
        names = ("read_text", "run_tool", "vm_stat_bytes", "available_bytes")
        with open(hostinfo) as fh:
            tree = ast.parse(fh.read())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in names:
            self.assertIn(name, defined)
        with open(os.path.join(scripts, "adapter-unittest.py")) as fh:
            text = fh.read()
        for line in text.splitlines():
            for name in names:
                self.assertFalse(line.startswith("def %s(" % name) or line.startswith("def %s " % name),
                                 line)
        imported = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "hostinfo":
                imported.update(a.name for a in node.names)
        for name in names:
            self.assertIn(name, imported, "adapter-unittest.py does not import %s from hostinfo" % name)

    # ------------------------------------------------------------ item 5

    def test_agents_running_from_proc(self):
        host = self.host()
        agents_running = self.has(host, "agents_running", "proc_root", "ps")
        root = self.proc_tree()
        self.assertEqual(agents_running(proc_root=root), 2)
        os.environ["AC_PROC_ROOT"] = root
        self.assertEqual(agents_running(), 2)

    def test_agents_running_from_ps(self):
        host = self.host()
        agents_running = self.has(host, "agents_running", "proc_root", "ps")
        missing = os.path.join(self.tmp, "no-proc")
        ps, log = self.fake_ps(0)
        self.assertEqual(agents_running(proc_root=missing, ps=ps), 2)
        with open(log) as fh:
            self.assertEqual(fh.read().split("\n")[:5], ["-A", "-E", "-ww", "-o", "command="])
        bad, _ = self.fake_ps(1)
        self.assertIsNone(agents_running(proc_root=missing, ps=bad))
        os.environ["AC_PROC_ROOT"] = missing
        os.environ["AC_PS"] = ps
        self.assertEqual(agents_running(), 2)
        os.environ["AC_PS"] = bad
        self.assertIsNone(agents_running())

    # ------------------------------------------------------------ item 6

    def test_slots_in_use_probes_without_holding(self):
        import fcntl
        host = self.host()
        slots_in_use = self.has(host, "slots_in_use", "cfg")
        self.config(machine_slots=3)
        folder = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(folder, exist_ok=True)
        paths = [os.path.join(folder, "slot-%d" % i) for i in range(3)]
        for p in paths:
            open(p, "a").close()
        self.assertEqual(slots_in_use(self.cfg()), 0)
        fd = os.open(paths[1], os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.assertEqual(slots_in_use(self.cfg()), 1)
        finally:
            os.close(fd)
        self.assertEqual(slots_in_use(self.cfg()), 0)
        fds = []
        try:
            for p in paths:
                fds.append(os.open(p, os.O_RDWR | os.O_CREAT, 0o644))
                fcntl.flock(fds[-1], fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises if the probe left one locked
        finally:
            for f in fds:
                os.close(f)
        self.config(machine_slots=None)
        self.assertIsNone(slots_in_use(self.cfg()))

    # ------------------------------------------------------------ item 7

    def test_append_prunes_an_old_file(self):
        host = self.host()
        append = self.has(host, "append", "cfg", "row", "now")
        X, D = self.X, self.DAY
        path = self.write_lines([{"at": self.ts(X - 9 * D), "host": "h"},
                                 {"at": self.ts(X - 7.5 * D), "host": "h"},
                                 {"at": self.ts(X - 6 * D), "host": "h"},
                                 {"at": self.ts(X - 1 * D), "host": "h"}])
        row = {"at": self.ts(X), "host": "h", "load1": 0.5}
        append(self.cfg(), row, now=X)
        self.assertEqual(self.read_ats(path), [self.ts(X - 6 * D), self.ts(X - D), self.ts(X)])
        with open(path) as fh:
            self.assertEqual(json.loads(fh.read().splitlines()[-1]), row)

    def test_append_rewrites_when_the_first_line_is_unreadable(self):
        host = self.host()
        append = self.has(host, "append", "cfg", "row", "now")
        X, D = self.X, self.DAY
        path = self.write_lines(["not json {", {"at": self.ts(X - 7.5 * D)}, {"at": self.ts(X - D)}])
        append(self.cfg(), {"at": self.ts(X)}, now=X)
        self.assertEqual(self.read_ats(path), [self.ts(X - D), self.ts(X)])

    def test_append_without_rewrite_when_the_first_line_is_recent_enough(self):
        host = self.host()
        append = self.has(host, "append", "cfg", "row", "now")
        X, D = self.X, self.DAY
        path = self.write_lines([{"at": self.ts(X - 7.5 * D)}, {"at": self.ts(X - 6 * D)}])
        inode = os.stat(path).st_ino
        append(self.cfg(), {"at": self.ts(X)}, now=X)
        self.assertEqual(self.read_ats(path), [self.ts(X - 7.5 * D), self.ts(X - 6 * D), self.ts(X)])
        self.assertEqual(os.stat(path).st_ino, inode, "the file was rewritten")

    # ------------------------------------------------------------ item 8

    def test_controller_samples_while_it_runs(self):
        self.config(host_sample_seconds=1, machine_slots=2)
        self.add(1)
        self.plan(worker={"1": ["slow"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        path = self.host_file()
        self.assertTrue(os.path.isfile(path), "no %s" % path)
        with open(path) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertGreaterEqual(len(rows), 3, rows)
        for row in rows:
            self.check_row(row)
            self.assertEqual(row["slots"], 2)
        self.assertTrue(any((r["slots_used"] or 0) >= 1 and (r["agents"] or 0) >= 1 for r in rows), rows)

    # ------------------------------------------------------------ item 9

    def test_setting_defaults_to_60_and_null_is_off(self):
        self.assertEqual(getattr(self.cfg(), "host_sample_seconds", "unset"), 60)
        self.config(host_sample_seconds=None, machine_slots=2)
        self.assertIsNone(self.cfg().host_sample_seconds)
        self.add(1)
        self.plan(worker={"1": ["slow"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertFalse(os.path.exists(self.host_file()))

    def test_bad_setting_stops_the_scripts(self):
        for bad in (0, -1, 1.5, "60", True):
            self.config(host_sample_seconds=bad)
            res = self.run_py("tickets.py", "list")
            self.assertEqual(res.returncode, 2, (bad, res.stdout, res.stderr))
            self.assertIn("devloop.host_sample_seconds", res.stderr, bad)


class T3Acceptance(LoopCase):
    """Ticket #3: devloop.machine_slots bounds the heavy runs (agents, devloop-started test runs)."""

    def intervals(self, slots, hold="1.5"):
        self.config(machine_slots=slots)
        self.log = os.path.join(self.tmp, "intervals.txt")
        self.env.update(FAKE_INTERVALS=self.log, FAKE_HOLD=hold)

    def recorded(self):
        with open(self.log) as fh:
            rows = [line.split() for line in fh if line.strip()]
        return [(" ".join(r[:-2]), float(r[-2]), float(r[-1])) for r in rows]

    def most_at_once(self):
        """The most runs going at the start of any run (itself included; touching is not overlapping)."""
        runs = self.recorded()
        return max(1 + sum(1 for j, (_, a, b) in enumerate(runs) if j != i and a <= s < b)
                   for i, (_, s, _) in enumerate(runs))

    def hold_slot_zero(self):
        """Another devloop process holding the only slot, played by this test process."""
        import fcntl
        slots = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slots, exist_ok=True)
        fh = open(os.path.join(slots, "slot-0"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)

    def test_one_slot_runs_everything_one_after_the_other_and_still_lands(self):
        self.intervals(1)
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        names = [r[0] for r in self.recorded()]
        self.assertIn("agent worker 1", names)
        self.assertIn("agent verifier 1", names)
        self.assertTrue(any(n.startswith("tests ") for n in names), names)
        self.assertEqual(self.most_at_once(), 1, self.recorded())
        self.assertEqual(os.listdir(os.path.join(self.tmp, "wt", "slots")), ["slot-0"])

    def two_tickets(self, slots):
        self.intervals(slots)
        self.add(1)
        self.add(2)
        self.plan(worker={"2": ["slow"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((self.state(1), self.state(2)), ("landed", "landed"))
        names = [r[0] for r in self.recorded()]
        self.assertIn("tests full", names)
        self.assertIn("agent worker 2", names)
        return self.most_at_once()

    def test_two_slots_never_run_more_than_two_at_once(self):
        self.assertLessEqual(self.two_tickets(2), 2, self.recorded())
        self.assertTrue(set(os.listdir(os.path.join(self.tmp, "wt", "slots"))) <= {"slot-0", "slot-1"})

    def test_one_slot_keeps_a_regression_apart_from_the_other_groups_agents(self):
        self.assertEqual(self.two_tickets(1), 1, self.recorded())

    def test_an_agent_waits_for_the_slot_and_runs_once_it_is_freed(self):
        """D-091 item 6, with a worker since issue #7 (the opener takes no slot): the worker waits
        past its own timeout for the held slot and starts only after it is freed."""
        import fcntl
        import threading
        slot_dir = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slot_dir, exist_ok=True)
        fh = open(os.path.join(slot_dir, "slot-0"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        released = []
        timer = threading.Timer(3, lambda: (released.append(time.time()), fh.close()))
        self.addCleanup(timer.cancel)
        self.config(machine_slots=1, agent_timeout_seconds=2)
        timer.start()
        result = run_worker_in_process(self, 2)
        self.assertTrue(released)
        self.assertGreaterEqual(os.path.getmtime(result), released[0] - 0.05,
                                "the worker ran while the slot was held")

    def test_a_regression_without_a_slot_in_its_time_is_unmeasured(self):
        self.hold_slot_zero()
        self.config(machine_slots=1, regression_timeout_seconds=2)
        out = os.path.join(self.tmp, "regression.json")
        res = self.run_py("regression.py", "--branch", "main", "--out", out, timeout=120)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        with open(out) as fh:
            got = json.load(fh)
        self.assertEqual(got["status"], "unmeasured")
        self.assertIn("machine slot", got["summary"])


    def test_no_slot_roles_start_at_once_and_a_worker_waits(self):
        """Issue #7, own unit test: NO_SLOT_ROLES never touch a held slot."""
        from core import agents
        self.assertEqual(agents.NO_SLOT_ROLES, ("creator", "consolidator", "consolidator_peer"))
        for role in agents.NO_SLOT_ROLES:
            self.assertNotIn(role, ("worker", "verifier", "reviewer", "fixer"))


class T121Acceptance(LoopCase):
    """Ticket #121 (RegressionDeadlineAcceptance): a regression's time limit starts when it has
    the slot or the machine; preflight shares that limit."""

    def set_tests(self, **commands):
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        cfg["tests"].update(commands)
        with open(path, "w") as fh:
            json.dump(cfg, fh)

    def hold_slot_for(self, seconds, test_run=False):
        """Another process holding slot-0 (an agent's, or with `test_run` a test run in flight),
        played by this test process, released after `seconds`."""
        import threading
        from core import slots
        from core.config import Config
        patch = mock.patch.dict(os.environ)
        patch.start()
        try:
            os.environ.pop("DEVLOOP_SLOT", None)
            os.environ.pop("DEVLOOP_MACHINE", None)
            os.environ["AC_ROOT"] = self.proj
            take = slots.take_test_run if test_run else slots.take
            slot = take(Config(self.proj), time.time() + 2)
        finally:
            patch.stop()
        self.assertTrue(slot and slot.held)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "wt", "slots", "slot-0")))
        timer = threading.Timer(seconds, slot.release)
        self.addCleanup(timer.cancel)
        timer.start()
        return timer

    def regression(self):
        out = os.path.join(self.tmp, "regression.json")
        res = self.run_py("regression.py", "--branch", "main", "--out", out, timeout=120)
        self.assertTrue(os.path.exists(out), res.stdout + res.stderr)
        with open(out) as fh:
            got = json.load(fh)
        return res, got

    def test_a_regression_that_waited_for_its_slot_gets_its_full_time(self):
        self.config(machine_slots=1, regression_timeout_seconds=10)
        self.set_tests(full="sleep 4; " + TESTS + " full")
        self.hold_slot_for(7)
        res, got = self.regression()
        self.assertEqual(got["status"], "green", got)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_a_regression_that_waited_for_the_whole_machine_gets_its_full_time(self):
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=10)
        self.set_tests(full="sleep 4; " + TESTS + " full")
        self.hold_slot_for(7, test_run=True)
        res, got = self.regression()
        self.assertEqual(got["status"], "green", got)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_preflight_shares_the_runs_time_limit(self):
        self.config(regression_timeout_seconds=3)
        self.set_tests(preflight="sleep 6; " + TESTS + " preflight")
        res, got = self.regression()
        self.assertEqual(got["status"], "unmeasured", got)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("tests.preflight", got["summary"])
        self.assertIn("timeout", got["summary"])


class T144Acceptance(LoopCase):
    """Ticket #144: a release (here through `tickets.py release`) takes what a regression takes - one
    machine slot, or with devloop.regression_exclusive the whole machine - before its command starts."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        os.environ["AC_ROOT"] = self.proj
        self.slot_dir = os.path.join(self.tmp, "wt", "slots")
        self.live_dir = os.path.join(self.tmp, "wt", "runs", "live")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def landed_group(self):
        """A landed group g1 whose release failed (as test_live.py T65Acceptance.landed_group writes it)."""
        sha = git(self.proj, "rev-parse", "main")
        self.add(1, state="landed", group="g1")
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "landed", "landed_sha": sha,
                                                  "branch": "int/g1", "members": ["1"],
                                                  "release": "failed"}),
                         "test: landed group")
        return sha

    def hold_machine(self, seconds):
        """A child process that takes the whole machine (core/slots.take_machine), holds it `seconds`,
        then prints the time it lets go and releases. Returns (child, read_release_time)."""
        code = ("import sys, time; sys.path.insert(0, %r)\n"
                "from core.config import Config\nfrom core import slots\n"
                "m = slots.take_machine(Config(%r), time.time() + 10)\n"
                "print('held' if m and m.held else 'none', flush=True)\n"
                "time.sleep(%r)\n"
                "print(repr(time.time()), flush=True)\n"
                "m.release()\n" % (LOOP, self.proj, float(seconds)))
        child = subprocess.Popen(["python3", "-c", code], stdout=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(child.stdout.close)
        self.addCleanup(lambda: (child.poll() is None and child.terminate(), child.wait(30)))
        self.assertEqual(child.stdout.readline().strip(), "held")
        return child, lambda: float(child.stdout.readline().strip())

    def popen_release(self):
        out = open(os.path.join(self.tmp, "release-%d.out" % (time.time() * 1000)), "w+")
        self.addCleanup(out.close)
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "tickets.py"), "release", "g1"],
                                cwd=self.proj, env=self.env, stdout=out, stderr=subprocess.STDOUT, text=True)

        def reap():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(60)
        self.addCleanup(reap)
        return proc, out

    def output(self, out):
        out.flush()
        out.seek(0)
        return out.read()

    def wait_until(self, cond, timeout, proc=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = cond()
            if got:
                return got
            if proc is not None and proc.poll() is not None:
                return cond()
            time.sleep(0.1)
        return cond()

    def release_record(self):
        try:
            with open(os.path.join(self.live_dir, "release-g1.json")) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            return None
        return rec if isinstance(rec, dict) else None

    def group(self):
        return self.snap().groups["g1"]

    # ---- item 1
    def test_item1_release_starts_only_after_the_machine_holder_lets_go(self):
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=60)
        self.landed_group()
        start = os.path.join(self.tmp, "start.txt")
        self.config(release="python3 -c 'import time; print(repr(time.time()))' > %s" % start)
        child, released_at = self.hold_machine(4)
        res = self.run_py("tickets.py", "release", "g1", timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(os.path.exists(start), "the release command never ran")
        with open(start) as fh:
            began = float(fh.read().strip())
        let_go = released_at()
        self.assertGreaterEqual(began, let_go - 0.1,
                                "the release command started %.2f s before the holder let go" % (let_go - began))
        self.assertEqual(self.group().get("release"), "ok")

    # ---- item 2
    def test_item2_a_release_holds_the_whole_machine(self):
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=60)
        self.landed_group()
        marker = os.path.join(self.tmp, "release-running")
        self.config(release="touch %s; sleep 4" % marker)
        proc, out = self.popen_release()
        self.assertTrue(self.wait_until(lambda: os.path.exists(marker), 30, proc),
                        "the release command never started: " + self.output(out))
        machine = slots.take_machine(self.cfg(), time.time() + 1)
        if machine is not None:
            self.addCleanup(machine.release)
        self.assertIsNone(machine, "the whole machine was free while the release ran")
        env = dict(self.env, DEVLOOP_SLOT=os.path.join(self.slot_dir, "slot-0"))
        res = subprocess.run(["python3", os.path.join(LOOP, "run_tests.py"), "case_1"], cwd=self.proj,
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("machine busy", res.stderr)
        self.assertEqual(proc.wait(60), 0, self.output(out))

    # ---- item 3
    def test_item3_a_release_that_gets_no_machine_in_time_fails_without_running(self):
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=2)
        self.landed_group()
        marker = os.path.join(self.tmp, "release-ran")
        self.config(release="touch %s" % marker)
        self.hold_machine(10)
        res = self.run_py("tickets.py", "release", "g1", timeout=60)
        self.assertFalse(os.path.exists(marker), "the release command ran while the machine was held")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(self.group().get("release"), "failed")
        inbox = self.snap().inbox
        about = [inbox[k] for k in sorted(inbox) if inbox[k].get("kind") == "decision" and inbox[k].get("ref") == "g1"]
        self.assertTrue(about, "no decision page about g1")
        self.assertTrue([l for l in str(about[-1].get("text")).split("\n") if "did not come free within" in l],
                        about[-1].get("text"))
        from core import release
        with open(release.log_path(self.cfg(), "g1")) as fh:
            log = fh.read()
        self.assertTrue([l for l in log.split("\n") if "did not come free within" in l], log)

    # ---- item 7
    def test_item7_a_waiting_release_has_a_machine_stage_record_and_board_row(self):
        import board
        import test_live
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=60)
        self.landed_group()
        self.config(release="sleep 3")
        self.hold_machine(6)
        proc, out = self.popen_release()
        rec = self.wait_until(lambda: (lambda r: r if r and r.get("stage") == "machine" else None)(
            self.release_record()), 5, proc)
        self.assertIsNotNone(rec, "no release-g1 record with stage 'machine' while waiting; record: %r"
                             % (self.release_record(),))
        self.assertEqual((rec.get("stamp") or {}).get("pid"), proc.pid, rec)
        page = board.render(self.cfg(), self.snap(), query={"tab": "live"}, now=int(time.time()))
        rows = test_live.T66Acceptance.parse(self, page)["rows"]
        mine = [r for r in rows if r["attrs"].get("data-pid") == str(proc.pid)]
        self.assertEqual(len(mine), 1, [r["attrs"] for r in rows])
        self.assertIn("等整台機器", " ".join(mine[0]["cells"].values()), mine[0]["cells"])
        spawned = self.wait_until(lambda: (lambda r: r if r and (r.get("stamp") or {}).get("pid") not in
                                           (None, proc.pid) else None)(self.release_record()), 20, proc)
        self.assertIsNotNone(spawned, "no record with the command's stamp once it was spawned")
        self.assertNotIn("stage", spawned, spawned)
        with open("/proc/%d/cmdline" % spawned["stamp"]["pid"]) as fh:
            self.assertEqual(fh.read().split("\0")[:2], ["sh", "-c"])
        self.assertEqual(proc.wait(60), 0, self.output(out))

    # test_live.T66Acceptance.parse reads self.VOID
    VOID = {"area", "base", "br", "col", "embed", "hr", "img",
                                                       "input", "link", "meta", "param", "source",
                                                       "track", "wbr"}

    # ---- item 8
    def test_item8_the_release_command_gets_devloop_machine(self):
        self.config(machine_slots=2, regression_exclusive=True, regression_timeout_seconds=60)
        self.landed_group()
        seen = os.path.join(self.tmp, "machine-seen.txt")
        self.config(release='printf %%s "$DEVLOOP_MACHINE" > %s' % seen)
        res = self.run_py("tickets.py", "release", "g1", timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        with open(seen) as fh:
            self.assertEqual(fh.read(), os.path.join(self.slot_dir, "exclusive"))

    def test_item8_the_release_command_gets_devloop_slot(self):
        self.config(machine_slots=1, regression_timeout_seconds=60)
        self.landed_group()
        seen = os.path.join(self.tmp, "slot-seen.txt")
        self.config(release='printf %%s "$DEVLOOP_SLOT" > %s' % seen)
        res = self.run_py("tickets.py", "release", "g1", timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        with open(seen) as fh:
            self.assertEqual(fh.read(), os.path.join(self.slot_dir, "slot-0"))


class T30Acceptance(LoopCase):
    """Ticket #30: waiting for a machine slot does not count against an agent's or an issuer's
    time limit; the clocks start when the slot is taken (D-091 item 6)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("DEVLOOP_SLOT", None)     # this process is not nested under a slot
        os.environ.pop("DEVLOOP_MACHINE", None)
        os.environ["AC_ROOT"] = self.proj

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def done_worker(self, **devloop):
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            agents_cfg = json.load(fh)["devloop"]["agents"]
        script = "cat >/dev/null; echo '{\"kind\": \"done\"}' > \"$DEVLOOP_RESULT\""
        self.config(agents=dict(agents_cfg, worker=["sh", "-c", script]), **devloop)

    def hold_slot_for(self, seconds):
        """Another holder (this test process) takes the only slot and frees it after `seconds`."""
        import threading
        from core import slots
        slot = slots.take(self.cfg(), time.time() + 5)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        timer = threading.Timer(seconds, slot.release)
        timer.start()
        self.addCleanup(timer.cancel)
        return slot

    def assert_ran(self, hand, began):
        self.assertGreaterEqual(time.time() - began, 2.5)
        self.assertEqual(hand.cost["outcome"], "done", hand.cost)
        self.assertIsNone(hand.problem())
        self.assertFalse(hand.timed_out)

    def test_an_agent_waits_for_a_slot_past_its_timeout_and_then_runs(self):
        from core import agents
        self.done_worker(machine_slots=1)
        self.hold_slot_for(3)
        began = time.time()
        hand = agents.run(self.cfg(), "worker", "go", self.proj, os.path.join(self.tmp, "run"), 2)
        self.assert_ran(hand, began)

    def test_an_issuer_test_run_waits_for_the_exclusive_mark_past_the_issuer_timeout(self):
        """#143 reversed this case: an agent no longer waits for the mark, an issuer test run does."""
        import fcntl
        import threading
        import ticket_issuer
        self.config(machine_slots=2, regression_exclusive=True, issuer_timeout_seconds=2)
        slot_dir = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slot_dir, exist_ok=True)
        fh = open(os.path.join(slot_dir, "exclusive"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        released = []
        timer = threading.Timer(3, lambda: (released.append(time.time()), fh.close()))
        timer.start()
        self.addCleanup(timer.cancel)
        issuer = ticket_issuer.Issuer(self.cfg(), self.pool, "1", "int/g1")
        issuer.wt = self.proj           # the fake tests run here; case_1 fails (no impl_1.txt)
        try:
            out = issuer.run_cases({"cases": {"run": ["case_1"]}})
        except ticket_issuer.Stuck as e:
            self.fail("issuer stuck while waiting for the exclusive mark: %s %s" % (e.reason, e.text))
        self.assertTrue(released, "the issuer's test run did not wait for the mark")
        self.assertTrue(out.startswith("verifier cases:"), out)

    def test_an_issuer_test_run_waits_for_its_slot_past_the_issuer_timeout(self):
        import ticket_issuer
        self.config(machine_slots=1, issuer_timeout_seconds=5)
        issuer = ticket_issuer.Issuer(self.cfg(), self.pool, "1", "int/g1")
        issuer.wt = self.proj           # the fake tests run here; case_1 fails (no impl_1.txt)
        self.hold_slot_for(7)
        try:
            out = issuer.run_cases({"cases": {"run": ["case_1"]}})
        except ticket_issuer.Stuck as e:
            self.fail("issuer stuck while waiting for a test slot: %s %s" % (e.reason, e.text))
        self.assertIsInstance(out, str)
        self.assertTrue(out.startswith("verifier cases:"), out)

    def test_end_to_end_a_ticket_lands_though_the_only_slot_is_held_past_the_issuer_timeout(self):
        import fcntl
        import threading
        self.config(machine_slots=1, issuer_timeout_seconds=6)
        self.add(1)
        slot_dir = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slot_dir, exist_ok=True)
        fh = open(os.path.join(slot_dir, "slot-0"), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        timer = threading.Timer(8, fh.close)
        timer.start()
        self.addCleanup(timer.cancel)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual((t["state"], t["round"]), ("landed", 1), t.get("stuck") or t.get("history"))


class RegressionWaitReadme(LoopCase):
    def test_the_slot_section_gives_the_run_its_own_time_from_the_slot(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        flat = " ".join(text.split("## Machine slots", 1)[1].split("\n## ", 1)[0].split())
        for words in ("A regression waits for its slot at most `regression_timeout_seconds`",
                      "its run gets `regression_timeout_seconds` from when the slot is taken",
                      "from when the machine is taken",
                      "another group's regression runs at most `regression_timeout_seconds`"):
            self.assertIn(words, flat)
        self.assertNotIn("still counts against `regression_timeout_seconds`", flat)


class ReleaseTakesWhatARegressionTakes(LoopCase):
    """Ticket #144, the implementer's tests: core/release.run takes a slot or the whole machine first."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            del os.environ[key]
        os.environ["AC_ROOT"] = self.proj
        self.sha = git(self.proj, "rev-parse", "main").strip()
        self.group = {"id": "g1", "members": ["1"]}
        self.stamp = os.path.join(self.tmp, "started.txt")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def stamping(self, then=""):
        return "python3 -c 'import time; print(time.time())' > %s%s" % (self.stamp, then)

    def started(self):
        with open(self.stamp) as fh:
            return float(fh.read())

    def release_after(self, hold, seconds):
        """Let go of `hold` from a thread after `seconds`; returns a dict that gets the release time."""
        out = {}

        def let_go():
            time.sleep(seconds)
            out["at"] = time.time()
            hold.release()
        thread = threading.Thread(target=let_go)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(hold.release)
        return out

    def test_item4_the_release_limit_counts_from_when_the_machine_is_taken(self):
        from core import release, slots
        self.config(machine_slots=2, regression_exclusive=True, release_timeout_seconds=4,
                    release=self.stamping("; sleep 2"))
        hold = slots.take_machine(self.cfg(), time.time() + 1)
        self.assertTrue(hold and hold.held)
        freed = self.release_after(hold, 3)
        ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertTrue(ok, text)
        self.assertGreaterEqual(self.started(), freed["at"] - 0.1)

    def test_item5_without_exclusive_the_release_takes_one_slot(self):
        from core import release, slots
        self.config(machine_slots=1, release=self.stamping())
        hold = slots.take(self.cfg(), time.time() + 1)
        self.assertTrue(hold and hold.held)
        self.assertTrue(hold.path.endswith("slot-0"))
        freed = self.release_after(hold, 1.5)
        ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertTrue(ok, text)
        self.assertGreaterEqual(self.started(), freed["at"] - 0.1)

    def test_item5_a_slot_that_does_not_come_free_is_a_failed_release_with_the_busy_line(self):
        from core import release, slots
        self.config(machine_slots=1, regression_timeout_seconds=1, release=self.stamping())
        hold = slots.take(self.cfg(), time.time() + 1)
        self.addCleanup(hold.release)
        ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertFalse(ok)
        self.assertTrue(text.startswith("no machine slot came free within"), text)
        self.assertFalse(release.product_failed(text))
        self.assertFalse(os.path.exists(self.stamp))
        with open(release.log_path(self.cfg(), "g1")) as fh:
            self.assertIn("no machine slot came free within", fh.read())

    def test_item6_without_slots_nothing_is_taken_and_the_record_is_the_commands(self):
        from core import inflight, release, slots
        self.config(machine_slots=None, release=self.stamping())
        puts = []
        real_put = inflight.put
        with mock.patch.object(slots, "take", side_effect=AssertionError("take")), \
                mock.patch.object(slots, "take_machine", side_effect=AssertionError("take_machine")), \
                mock.patch.object(release.inflight, "put",
                                  side_effect=lambda cfg, name, rec: (puts.append(rec), real_put(cfg, name, rec))):
            ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertTrue(ok, text)
        self.assertEqual(len(puts), 1)
        self.assertNotIn("stage", puts[0])
        self.assertNotEqual(puts[0]["stamp"]["pid"], os.getpid())

    def test_item7_the_waiting_record_then_the_commands_record(self):
        from core import inflight, release
        self.config(machine_slots=2, regression_exclusive=True, release=self.stamping())
        puts = []
        real_put = inflight.put
        with mock.patch.object(release.inflight, "put",
                               side_effect=lambda cfg, name, rec: (puts.append(rec), real_put(cfg, name, rec))):
            ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertTrue(ok, text)
        self.assertEqual([p.get("stage") for p in puts], ["machine", None])
        self.assertEqual(puts[0]["stamp"]["pid"], os.getpid())
        self.assertNotEqual(puts[1]["stamp"]["pid"], os.getpid())

    def check_made_inside_the_hold(self, command, detached=None):
        from core import release, slots, worktrees
        self.config(machine_slots=1, regression_exclusive=True, release=command)
        seen = []
        real = worktrees.detached_tree

        def tree(*a, **kw):
            seen.append(slots.take_machine(self.cfg(), time.time() + 0.2))   # None: the release holds it
            if detached:
                raise detached
            return real(*a, **kw)
        with mock.patch.object(release.worktrees, "detached_tree", side_effect=tree):
            ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertEqual(seen, [None])
        after = slots.take_machine(self.cfg(), time.time() + 0.5)
        self.assertTrue(after and after.held, "the hold outlived release.run")
        after.release()
        return ok, text

    def test_item9_the_worktree_is_made_inside_the_hold_and_the_hold_is_released(self):
        ok, text = self.check_made_inside_the_hold("true")
        self.assertTrue(ok, text)

    def test_item9_the_hold_is_released_after_a_failed_command(self):
        ok, text = self.check_made_inside_the_hold("exit 1")
        self.assertFalse(ok)
        self.assertTrue(text.startswith("rc 1"), text)

    def test_item9_the_hold_is_released_after_a_worktree_failure(self):
        from core import osx as osx_mod
        ok, text = self.check_made_inside_the_hold("true", detached=osx_mod.OSFailure("no tree"))
        self.assertFalse(ok)
        self.assertEqual(text, "no tree")

    def test_item8_the_command_gets_its_slot(self):
        from core import release
        out = os.path.join(self.tmp, "env.txt")
        self.config(machine_slots=1, release='printf "%%s|%%s" "$DEVLOOP_SLOT" "$DEVLOOP_MACHINE" > %s' % out)
        ok, text = release.run(self.cfg(), self.group, self.sha)
        self.assertTrue(ok, text)
        with open(out) as fh:
            self.assertEqual(fh.read(), os.path.join(self.tmp, "wt", "slots", "slot-0") + "|")

    def test_a_release_waiting_for_the_machine_is_not_numbered_in_the_slot_queue(self):
        import board
        rows = [{"kind": "release", "state": "slot", "text": "等整台機器", "attrs": {},
                 "rec": {"stage": "machine"}, "waiter": ("release-g1", {"since": None})},
                {"kind": "release", "state": "slot", "text": "等機器名額", "attrs": {},
                 "rec": {"stage": "slot"}, "waiter": ("release-g2", {"since": None})}]
        board.live_queue(rows)
        self.assertEqual(rows[0]["text"], "等整台機器")
        self.assertIn("等機器名額", rows[1]["text"])
        self.assertEqual(rows[1]["attrs"]["data-queue"], "1/1")

    def test_item10_the_readme_names_the_release(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        slots_part = " ".join(text.split("## Machine slots", 1)[1].split("\n## ", 1)[0].split())
        for words in ("A release (`core/release.py`, at landing and through `tickets.py release`) takes what a "
                      "regression takes: one slot, or with `regression_exclusive` the whole machine",
                      "It waits at most `regression_timeout_seconds`",
                      "its run gets `release_timeout_seconds` from when the slot or machine is taken"):
            self.assertIn(words, slots_part)
        live = " ".join(text.split("## Live records", 1)[1].split("\n## ", 1)[0].split())
        bullet = live.split("`release-<group>.json`", 1)[1].split(" - `", 1)[0]
        self.assertIn("`stage` `machine` (`regression_exclusive`) or `slot`", bullet)


class T116Acceptance(LoopCase):
    """Ticket #116 (issue #7): the opener and memory consolidation start without a machine slot;
    run_tests.py outside a slot never waits for one."""

    BUSY = ("run_tests: machine busy (a full test run holds the machine or every machine slot is "
            "taken); nothing was run")

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        os.environ["AC_ROOT"] = self.proj

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def agents_cfg(self):
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            return json.load(fh)["devloop"]["agents"]

    def hold_file(self, name, seconds):
        """Another holder (this test process) takes LOCK_EX on <worktrees>/slots/<name> and frees it
        after `seconds`; returns the list the release time is appended to."""
        import fcntl
        import threading
        slot_dir = os.path.join(self.tmp, "wt", "slots")
        os.makedirs(slot_dir, exist_ok=True)
        fh = open(os.path.join(slot_dir, name), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        released = []
        timer = threading.Timer(seconds, lambda: (released.append(time.time()), fh.close()))
        self.addCleanup(timer.cancel)
        timer.start()
        return released

    def hold_slot_for(self, seconds):
        import threading
        from core import slots
        slot = slots.take(self.cfg(), time.time() + 5)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        timer = threading.Timer(seconds, slot.release)
        self.addCleanup(timer.cancel)
        timer.start()
        return slot

    def assert_opener_did_not_wait(self, released):
        began = time.time()
        res = self.run_py("ticket_creator.py", "--request", "x", timeout=60)
        elapsed = time.time() - began
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertLess(elapsed, 15, "the opener waited for the machine")
        self.assertEqual(released, [], "the holder released before the opener was done")
        ran = glob.glob(os.path.join(os.path.dirname(self.plan_path), "count-creator-*"))
        self.assertTrue(ran, "the creator never ran")

    def test_1_the_opener_does_not_wait_for_a_held_slot(self):
        self.config(machine_slots=1)
        self.assert_opener_did_not_wait(self.hold_file("slot-0", 20))

    def test_2_the_opener_does_not_wait_for_a_held_exclusive_mark(self):
        self.config(machine_slots=2, regression_exclusive=True)
        self.assert_opener_did_not_wait(self.hold_file("exclusive", 20))

    SCRIPT = ('echo "{\\"kind\\": \\"done\\", \\"slot\\": \\"$DEVLOOP_SLOT\\"}" '
              '> "$DEVLOOP_RESULT"; exit 0')
    NO_SLOT = ("creator", "consolidator", "consolidator_peer")

    def no_slot_agents(self):
        cmd = ["sh", "-c", self.SCRIPT]
        self.config(machine_slots=1, agents=dict(self.agents_cfg(), **{r: cmd for r in self.NO_SLOT}))

    def test_3_no_slot_roles_start_at_once_and_see_no_slot(self):
        from core import agents
        self.no_slot_agents()
        self.hold_slot_for(8)
        for role in self.NO_SLOT:
            with self.subTest(role=role):
                began = time.time()
                hand = agents.run(self.cfg(), role, "go", self.proj,
                                  os.path.join(self.tmp, "run-" + role), 30)
                self.assertLess(time.time() - began, 4, "%s waited for the held slot" % role)
                self.assertIsNone(hand.problem())
                self.assertEqual(hand.result["slot"], "")

    def test_4_the_opener_live_record_never_says_slot(self):
        from core import agents, inflight
        self.no_slot_agents()
        self.hold_slot_for(8)
        states = []
        with mock.patch.object(inflight, "put", side_effect=lambda cfg, name, rec: states.append(rec.get("state"))):
            hand = agents.run(self.cfg(), "creator", "go", self.proj,
                              os.path.join(self.tmp, "run-creator"), 30)
        self.assertIsNone(hand.problem())
        self.assertNotIn("slot", states)
        self.assertIn("running", states)
        self.assertEqual(set(states), {"running"}, states)

    def assert_busy(self):
        intervals = os.path.join(self.tmp, "intervals.txt")
        self.env["FAKE_INTERVALS"] = intervals
        began = time.time()
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        elapsed = time.time() - began
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertEqual(res.stderr.strip(), self.BUSY)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertEqual(res.stdout, "")
        self.assertLess(elapsed, 5)
        if os.path.exists(intervals):
            with open(intervals) as fh:
                self.assertEqual(fh.read(), "", "the test interface was started")

    def test_6_run_tests_says_machine_busy_when_every_slot_is_held(self):
        self.config(machine_slots=1)
        self.hold_file("slot-0", 20)
        self.assert_busy()

    def test_6_run_tests_says_machine_busy_when_the_exclusive_mark_is_held(self):
        self.config(machine_slots=2, regression_exclusive=True)
        self.hold_file("exclusive", 20)
        self.assert_busy()

    def test_7_run_tests_runs_under_its_own_slot_and_frees_it(self):
        from core import slots
        self.config(machine_slots=1)
        seen = os.path.join(self.tmp, "slot-seen.txt")
        self.env["FAKE_SLOT_SEEN"] = seen
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertTrue(os.path.exists(seen), "the fake test run never recorded its slot")
        with open(seen) as fh:
            self.assertEqual(fh.read(), os.path.join(self.tmp, "wt", "slots", "slot-0"))
        began = time.time()
        slot = slots.take(self.cfg(), time.time() + 1)
        self.assertTrue(slot and slot.held, "run_tests.py left its slot held")
        slot.release()
        self.assertLess(time.time() - began, 1)

    def test_9_the_opener_prompt_says_how_to_run_tests_and_what_busy_means(self):
        import ticket_creator
        text = ticket_creator.chain()
        for words in ("run_tests.py", "machine busy", "not measured (machine busy)"):
            self.assertIn(words, text)

    def test_10_readme_slot_section_names_the_slotless_roles_and_machine_busy(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        section = text.split("## Machine slots", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        for words in ("creator", "consolidator_peer", "machine busy",
                      "agent", "red check", "regression", "slots/slot-", "own limit", "DEVLOOP_SLOT",
                      "parent's slot", "wait for a slot without a limit", "from when the slot is taken",
                      "A regression waits for its slot at most `regression_timeout_seconds`", "regression_exclusive",
                      "slots/exclusive", "needs `machine_slots`"):
            self.assertIn(words, flat)
        self.assertNotIn('"no slot"', flat)


class TestRunTake(LoopCase):
    """#143: core/slots.take_test_run (test runs) against take (agents) and take_machine."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in [k for k in os.environ if k.startswith("DEVLOOP_")]:
            os.environ.pop(key)
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            self.env.pop(key)
        os.environ["AC_ROOT"] = self.proj
        self.slot_dir = os.path.join(self.tmp, "wt", "slots")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def hold(self, name):
        import fcntl
        os.makedirs(self.slot_dir, exist_ok=True)
        fh = open(os.path.join(self.slot_dir, name), "a")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    def test_no_slots_or_inside_the_machine_holder_is_a_free_pass(self):
        from core import slots
        self.config(machine_slots=None)
        self.assertFalse(slots.take_test_run(self.cfg(), time.time()).held)
        self.config(machine_slots=1, regression_exclusive=True)
        self.hold("exclusive")
        self.hold("slot-0")
        os.environ[slots.MACHINE_ENV] = os.path.join(self.slot_dir, "exclusive")
        run = slots.take_test_run(self.cfg(), time.time())
        self.assertFalse(run.held)
        self.assertIsNone(run.path)
        self.assertEqual(sorted(os.listdir(self.slot_dir)), ["exclusive", "slot-0"])   # no tests file

    def test_without_exclusive_it_is_the_agent_slot_and_nested_takes_nothing(self):
        from core import slots
        self.config(machine_slots=1)
        run = slots.take_test_run(self.cfg(), time.time())
        self.assertEqual(os.path.basename(run.path), "slot-0")
        self.assertIsNone(slots.take_test_run(self.cfg(), time.time()))
        self.assertIsNone(slots.take(self.cfg(), time.time()))
        os.environ[slots.ENV] = run.path
        self.assertFalse(slots.take_test_run(self.cfg(), time.time()).held)
        run.release()
        self.assertEqual(os.listdir(self.slot_dir), ["slot-0"])

    def test_a_nested_run_takes_no_slot_but_counts_as_a_test_run(self):
        from core import slots
        self.config(machine_slots=1, regression_exclusive=True)
        self.hold("slot-0")                                     # the agent's own slot
        os.environ[slots.ENV] = os.path.join(self.slot_dir, "slot-0")
        run = slots.take_test_run(self.cfg(), time.time())
        self.assertTrue(run and run.held)
        self.assertIsNone(run.path)
        os.environ.pop(slots.ENV)
        self.assertIsNone(slots.take_machine(self.cfg(), time.time() + 0.6))
        run.release()
        run.release()                                           # idempotent
        machine = slots.take_machine(self.cfg(), time.time() + 1)
        self.assertTrue(machine and machine.held)
        os.environ[slots.ENV] = os.path.join(self.slot_dir, "slot-0")
        self.assertIsNone(slots.take_test_run(self.cfg(), time.time()))   # nested, but the mark is held
        self.assertFalse(slots.take(self.cfg(), time.time()).held)        # a nested agent take: nothing
        machine.release()

    def test_the_machine_holds_the_mark_while_it_waits_so_new_test_runs_cannot_starve_it(self):
        import threading
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        first = slots.take_test_run(self.cfg(), time.time())
        got = {}
        th = threading.Thread(target=lambda: got.setdefault("m", slots.take_machine(self.cfg(), time.time() + 5)))
        th.start()
        try:
            time.sleep(0.5)                                     # the machine waits with the mark held
            self.assertIsNone(slots.take_test_run(self.cfg(), time.time()))
            first.release()
            th.join(5)
            self.assertTrue(got["m"] and got["m"].held)
            got["m"].release()
        finally:
            first.release()
            th.join(5)
        self.assertTrue(slots.take_test_run(self.cfg(), time.time()).held)

    def test_run_tests_nested_runs_the_case_and_inside_the_holder_ignores_the_mark(self):
        self.config(machine_slots=2, regression_exclusive=True)
        self.env["DEVLOOP_SLOT"] = os.path.join(self.slot_dir, "slot-0")
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.hold("exclusive")
        self.env["DEVLOOP_MACHINE"] = os.path.join(self.slot_dir, "exclusive")
        res = self.run_py("run_tests.py", "case_1", timeout=60)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)

    def test_the_busy_sentence_names_the_whole_machine(self):
        from core import slots
        self.config(machine_slots=2, regression_exclusive=True)
        self.assertEqual(slots.machine_busy(self.cfg(), 9.6),
                         "the whole machine (devloop.regression_exclusive: no other test run) "
                         "did not come free within 10 s")


class MachineSlots(LoopCase):
    """devloop.machine_slots: the implementer's tests (core/slots.py and its call sites)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("DEVLOOP_SLOT", None)     # this process is not nested under a slot
        os.environ["AC_ROOT"] = self.proj
        self.slot_dir = os.path.join(self.tmp, "wt", "slots")

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def agents_cfg(self):
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            return json.load(fh)["devloop"]["agents"]

    def hold_slot(self):
        """This test process takes the only slot."""
        import time
        from core import slots
        slot = slots.take(self.cfg(), time.time() + 5)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        return slot

    def test_no_setting_takes_no_slot(self):
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertFalse(os.path.exists(self.slot_dir))
        self.config(machine_slots=None)
        self.assertIsNone(self.cfg().machine_slots)

    def test_bad_values_stop_every_script(self):
        for bad in (0, -1, True, "2", 1.5):
            self.config(machine_slots=bad)
            with self.assertRaises(osx.OSFailure) as ctx:
                self.cfg()
            self.assertIn("devloop.machine_slots", str(ctx.exception))
        res = self.run_py("tickets.py", "list")
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("devloop.machine_slots", res.stderr)
        self.config(machine_slots=3)
        self.assertEqual(self.cfg().machine_slots, 3)

    def test_slot_files_are_kept_and_a_held_slot_blocks_the_next_taker(self):
        import time
        from core import slots
        self.config(machine_slots=2)
        cfg = self.cfg()
        a = slots.take(cfg, time.time() + 1)
        b = slots.take(cfg, time.time() + 1)
        self.assertEqual(sorted(os.path.basename(s.path) for s in (a, b)), ["slot-0", "slot-1"])
        began = time.time()
        self.assertIsNone(slots.take(cfg, time.time() + 1.2))
        self.assertGreaterEqual(time.time() - began, 1.0)
        inode = os.stat(a.path).st_ino
        a.release()
        a.release()                                     # idempotent
        c = slots.take(cfg, time.time() + 1)
        self.assertEqual(c.path, a.path)
        self.assertEqual(os.stat(c.path).st_ino, inode)  # never replaced
        b.release()
        c.release()
        self.assertEqual(sorted(os.listdir(self.slot_dir)), ["slot-0", "slot-1"])

    def test_a_nested_taker_neither_waits_nor_takes(self):
        import time
        from core import slots
        self.config(machine_slots=1)
        held = self.hold_slot()
        os.environ["DEVLOOP_SLOT"] = held.path
        began = time.time()
        free = slots.take(self.cfg(), time.time() + 5)
        self.assertLess(time.time() - began, 0.5)
        self.assertFalse(free.held)
        self.assertIsNone(free.path)

    def test_an_agent_slot_is_free_when_it_exits_though_its_background_child_lives(self):
        import time
        from core import agents, slots
        script = ('sleep 20 & echo "{\\"kind\\": \\"done\\", \\"slot\\": \\"$DEVLOOP_SLOT\\"}" '
                  '> "$DEVLOOP_RESULT"; exit 0')
        self.config(machine_slots=1, agents=dict(self.agents_cfg(), worker=["sh", "-c", script]))
        cfg = self.cfg()
        p = agents.start(cfg, "worker", "go", self.proj, os.path.join(self.tmp, "run"), 30)
        self.addCleanup(p.stop)          # ends the background sleep (its process group)
        began = time.time()
        slot = slots.take(cfg, time.time() + 3)     # the worker was never collected
        self.assertTrue(slot and slot.held, "the agent's slot was not freed within 3 s of its exit")
        self.assertLess(time.time() - began, 3.5)
        slot.release()
        hand = p.finish()
        self.assertIsNone(hand.problem())
        self.assertEqual(hand.result["slot"], os.path.join(self.slot_dir, "slot-0"))

    def test_a_holder_that_dies_frees_its_slot(self):
        import time
        from core import slots
        self.config(machine_slots=1)
        code = ("import sys, time; sys.path.insert(0, %r)\n"
                "from core.config import Config\nfrom core import slots\n"
                "s = slots.take(Config(%r), time.time() + 10)\n"
                "print('held' if s and s.held else 'none', flush=True)\ntime.sleep(60)\n" % (LOOP, self.proj))
        child = subprocess.Popen(["python3", "-c", code], stdout=subprocess.PIPE, text=True,
                                 env=os.environ.copy())
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "held")
        cfg = self.cfg()
        self.assertIsNone(slots.take(cfg, time.time() + 0.6))
        child.terminate()
        child.wait(timeout=10)
        began = time.time()
        slot = slots.take(cfg, time.time() + 3)
        self.assertTrue(slot and slot.held)
        self.assertLess(time.time() - began, 3.5)
        slot.release()

    def test_a_worker_that_has_its_ticket_revised_lands_with_one_slot(self):
        self.config(machine_slots=1)
        self.add(1)
        self.plan(worker={"1": ["revise", "ok"]}, creator={"1": ["revised"]})
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        t = self.snap().ticket("1")
        self.assertEqual(t["state"], "landed")
        self.assertEqual(t["acceptance"], ["a possible thing"])
        self.assertEqual(sorted(os.listdir(self.slot_dir)), ["slot-0"])

    def test_readme_has_the_section(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            text = fh.read()
        self.assertLess(text.index("## Project settings"), text.index("## Machine slots"))
        self.assertLess(text.index("## Machine slots"), text.index("## Agents, models and tests"))
        section = text.split("## Machine slots", 1)[1].split("\n## ", 1)[0]
        for words in ("agent", "red check", "regression", "slots/slot-", "own limit", "DEVLOOP_SLOT",
                      "parent's slot"):
            self.assertIn(words, section)

    def test_the_slot_section_has_no_no_slot_outcome_and_says_the_clocks_start_at_the_slot(self):
        with open(os.path.join(LOOP, "README.md")) as fh:
            section = fh.read().split("## Machine slots", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        self.assertNotIn('"no slot"', flat)
        self.assertIn("wait for a slot without a limit", flat)
        self.assertIn("from when the slot is taken", flat)
        self.assertIn("A regression waits for its slot at most `regression_timeout_seconds`", flat)

    def test_take_without_a_deadline_waits_until_the_slot_is_freed(self):
        import threading
        from core import slots
        self.config(machine_slots=1)
        held = self.hold_slot()
        timer = threading.Timer(1.5, held.release)
        self.addCleanup(timer.cancel)
        timer.start()
        began = time.time()
        slot = slots.take(self.cfg(), None)
        self.addCleanup(slot.release)
        self.assertTrue(slot.held)
        self.assertGreaterEqual(time.time() - began, 1.3)

    def test_an_agent_has_no_no_slot_state_and_counts_its_wait(self):
        import threading
        from core import agents
        self.config(machine_slots=1, agents=dict(self.agents_cfg(), worker=["true"]))
        held = self.hold_slot()
        timer = threading.Timer(1.5, held.release)
        self.addCleanup(timer.cancel)
        timer.start()
        p = agents.start(self.cfg(), "worker", "go", self.proj, os.path.join(self.tmp, "run"), 30)
        self.addCleanup(p.stop)
        self.assertFalse(hasattr(p, "no_slot"))
        self.assertFalse(hasattr(agents.Pending, "_unstarted"))
        self.assertGreaterEqual(p.waited, 1.3)
        with open(agents.__file__) as fh:
            self.assertNotIn('"no slot"', fh.read())

    def test_an_agent_past_its_timeout_after_taking_a_free_slot_is_timed_out(self):
        from core import agents
        self.config(machine_slots=1, agents=dict(self.agents_cfg(), worker=["sleep", "30"]))
        began = time.time()
        hand = agents.run(self.cfg(), "worker", "go", self.proj, os.path.join(self.tmp, "run"), 1.5)
        self.assertTrue(hand.timed_out)
        self.assertEqual(hand.cost["outcome"], "timeout")
        self.assertLess(time.time() - began, 15)

    def test_the_agent_timeout_counts_only_time_under_a_slot(self):
        """A relaunch after an 'out of quota' fallback adds its wait too; _finish subtracts the
        sum. Simulated: waited set by hand makes the wait for the process longer."""
        from core import agents
        from unittest import mock
        self.config(machine_slots=1, agents=dict(self.agents_cfg(), worker=["sleep", "30"]))
        p = agents.start(self.cfg(), "worker", "go", self.proj, os.path.join(self.tmp, "run"), 5)
        self.addCleanup(p.stop)
        p.started -= 100            # as if it had waited 100 s for its slot
        p.waited += 100
        with mock.patch.object(agents.osx, "wait", return_value=(None, True)) as wait:
            p.finish()
        self.assertGreater(wait.call_args[0][1], 4)

    def test_the_issuer_deadline_adds_every_slot_wait(self):
        import threading
        import ticket_issuer
        self.config(machine_slots=1, issuer_timeout_seconds=100)
        issuer = ticket_issuer.Issuer(self.cfg(), None, "1", "int/g1")
        base = issuer.deadline
        self.assertAlmostEqual(base, issuer.started + 100, places=3)
        held = self.hold_slot()
        timer = threading.Timer(1.2, held.release)
        self.addCleanup(timer.cancel)
        timer.start()
        issuer.test_slot().release()
        self.assertGreaterEqual(issuer.deadline - base, 1.0)

        class P:
            waited = 7.0
        issuer.started_agents.append(P())
        issuer.pending = []         # cleared each round; the waits stay counted
        self.assertGreaterEqual(issuer.deadline - base, 8.0)


if __name__ == "__main__":
    unittest.main()
