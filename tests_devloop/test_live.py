"""live records (#64, #65) and the 進行中 tab end to end (#66-#68)"""

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


class StopRecordsCost(LoopCase):
    """Pending.stop() returns the stopped run's cost row; Issuer.interrupted_costs() collects a
    round's rows exactly once (the implementer's tests of #22)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("DEVLOOP_SLOT", None)
        os.environ["AC_ROOT"] = self.proj

    def cfg(self, worker):
        from core.config import Config
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            agents_cfg = json.load(fh)["devloop"]["agents"]
        self.config(agents=dict(agents_cfg, worker=worker))
        return Config(self.proj)

    def test_stop_after_finish_returns_none(self):
        from core import agents
        script = 'cat >/dev/null; echo \'{"kind": "done"}\' > "$DEVLOOP_RESULT"'
        p = agents.start(self.cfg(["sh", "-c", script]), "worker", "go", self.proj,
                         os.path.join(self.tmp, "run"), 30, round_no=2)
        hand = p.finish()
        self.assertEqual(hand.cost["outcome"], "done")
        self.assertIs(p.hand, hand)
        self.assertIsNone(p.stop())

    def test_stop_of_a_running_agent_returns_an_interrupted_row(self):
        from core import agents
        p = agents.start(self.cfg(["sh", "-c", "cat >/dev/null; sleep 30"]), "worker", "go",
                         self.proj, os.path.join(self.tmp, "run"), 30, round_no=3)
        time.sleep(0.3)
        row = p.stop()
        self.assertEqual(p.proc.wait(5), p.proc.returncode)    # the process is gone
        self.assertEqual((row["outcome"], row["round"], row["role"], row["command"]),
                         ("interrupted", 3, "worker", "sh"))
        self.assertGreater(row["seconds"], 0)
        self.assertEqual(row["run_dir"], os.path.join(self.tmp, "run"))
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                  "usd", "session_id"):
            self.assertIsNone(row[k], k)

    def test_interrupted_costs_counts_each_run_once(self):
        import ticket_issuer

        class Fake:
            def __init__(self, row, hand_cost=None):
                self.row, self.stopped = row, False
                self.hand = type("H", (), {"cost": hand_cost})() if hand_cost else None

            def stop(self):
                self.stopped = True
                return self.row

        done = {"role": "worker", "outcome": "done"}
        late = {"role": "verifier", "outcome": "cases"}     # finished, row not yet in round_costs
        cut = {"role": "reviewer", "outcome": "interrupted"}
        issuer = ticket_issuer.Issuer.__new__(ticket_issuer.Issuer)
        issuer.round_costs = [done]
        issuer.pending = [Fake(None, done), Fake(None, late), Fake(cut)]
        pending = list(issuer.pending)
        rows = issuer.interrupted_costs()
        self.assertEqual(rows, [done, late, cut])
        self.assertTrue(all(p.stopped for p in pending))
        self.assertEqual((issuer.pending, issuer.round_costs), ([], []))

class LiveRecordUnits(StopRecordsCost):
    """core/inflight.py and the agent record Pending writes (the implementer's tests of #64)."""

    def live(self):
        return os.path.join(self.tmp, "wt", "runs", "live")

    def test_folder_records_drop_and_put(self):
        from core import inflight
        from core.config import Config
        cfg = Config(self.proj)
        self.assertEqual(inflight.folder(cfg), self.live())
        self.assertEqual(inflight.records(cfg), [])
        self.assertFalse(os.path.exists(self.live()))
        inflight.drop(cfg, "nothing")
        st = {"stamp": osx.stamp()}     # this process: running, so put() keeps it
        inflight.put(cfg, "b", dict(st, x=1))
        inflight.put(cfg, "a", dict(st, y=[2]))
        with open(os.path.join(self.live(), "bad.json"), "w") as fh:
            fh.write("{not json")
        with open(os.path.join(self.live(), "list.json"), "w") as fh:
            fh.write("[1]")
        self.assertEqual(inflight.records(cfg), [("a", dict(st, y=[2])), ("b", dict(st, x=1))])
        inflight.drop(cfg, "a")
        self.assertEqual([n for n, _ in inflight.records(cfg)], ["b"])

    def test_status_and_stale_cleanup(self):
        from core import inflight
        from core.config import Config
        cfg = Config(self.proj)
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 25 * 3600))
        hour = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        live = {"pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(), "started": old}
        dead = dict(live, boot="other-boot")
        result = os.path.join(self.tmp, "result.json")
        with open(result, "w") as fh:
            fh.write("{}")
        self.assertEqual(inflight.status({"stamp": live}), "running")
        self.assertEqual(inflight.status({"stamp": dead, "result": result}), "handed_in")
        self.assertEqual(inflight.status({"stamp": dead, "result": result + ".no"}), "ended")
        self.assertEqual(inflight.status({}), "ended")
        inflight.put(cfg, "old-running", {"stamp": live})
        inflight.put(cfg, "old-dead", {"stamp": dead})
        inflight.put(cfg, "hour-dead", {"stamp": dict(dead, started=hour)})
        inflight.put(cfg, "new", {"stamp": None})
        self.assertEqual([n for n, _ in inflight.records(cfg)], ["hour-dead", "new", "old-running"])

    def test_title_of(self):
        from core import inflight
        for text, want in (("intro\n## Board: tab\nbody", "Board: tab"), ("first line\nsecond", "first line"),
                           ("#\n\nplain", "plain"), ("##\n# \n", "##"), ("x" * 200, "x" * 120), ("", None), ("  \n\n", None)):
            self.assertEqual(inflight.title_of(text), want, text)

    def test_agent_record_while_running_and_gone_after_finish(self):
        from core import agents, inflight
        cfg = self.cfg(["sh", "-c", 'cat >/dev/null; sleep 1; echo \'{"kind": "done"}\' > "$DEVLOOP_RESULT"'])
        run_dir = os.path.join(self.tmp, "run")
        p = agents.start(cfg, "worker", "go", self.proj, run_dir, 30, round_no=2, chain_role="worker_simple",
                         extra_env={"DEVLOOP_TICKET": "7"})
        recs = inflight.records(cfg)
        self.assertEqual(len(recs), 1)
        name, rec = recs[0]
        self.assertTrue(name.startswith("agent-") and len(name) == len("agent-") + 16, name)
        self.assertEqual((rec["kind"], rec["state"], rec["role"], rec["ticket"], rec["group"], rec["round"]),
                         ("agent", "running", "worker_simple", "7", None, 2))
        self.assertEqual((rec["stamp"]["pid"], rec["parent"], rec["command"]), (p.proc.pid, os.getpid(), os.path.basename(p.argv[0])))
        self.assertEqual((rec["log"], rec["result"], rec["until"]),
                         (p.log, os.path.join(run_dir, "result.json"), None))
        self.assertEqual(inflight.status(rec), "running")
        p.finish()
        self.assertEqual(inflight.records(cfg), [])

    def test_agent_record_gone_after_stop_and_none_without_cfg(self):
        from core import agents, inflight
        cfg = self.cfg(["sh", "-c", "cat >/dev/null; sleep 30"])
        p = agents.start(cfg, "worker", "go", self.proj, os.path.join(self.tmp, "run"), 30)
        self.assertEqual(len(inflight.records(cfg)), 1)
        p.stop()
        self.assertEqual(inflight.records(cfg), [])
        bare = agents.Pending("worker", [["sh", "-c", "cat >/dev/null"]], "go", self.proj, dict(os.environ),
                              os.path.join(self.tmp, "run2"), os.path.join(self.tmp, "run2", "result.json"), 30, None)
        os.makedirs(os.path.join(self.tmp, "run2"))
        bare.launch()
        bare.finish()
        self.assertEqual(inflight.records(cfg), [])

    def test_agent_record_while_waiting_for_a_slot(self):
        import threading
        from core import agents, inflight, slots
        self.cfg(["sh", "-c", 'cat >/dev/null; echo \'{"kind": "done"}\' > "$DEVLOOP_RESULT"'])
        self.config(machine_slots=1)
        from core.config import Config
        cfg = Config(self.proj)
        held = slots.take(cfg, None)
        got = {}
        th = threading.Thread(target=lambda: got.setdefault("p", agents.start(
            cfg, "worker", "go", self.proj, os.path.join(self.tmp, "run"), 30)))
        th.start()
        try:
            deadline = time.time() + 10
            while not inflight.records(cfg) and time.time() < deadline:
                time.sleep(0.05)
            (name, rec), = inflight.records(cfg)
            self.assertEqual((rec["state"], rec["stamp"]["pid"], rec["log"], rec["command"]),
                             ("slot", os.getpid(), None, "sh"))
        finally:
            held.release()
            th.join(20)
        (name2, rec), = inflight.records(cfg)
        self.assertEqual((name2, rec["state"], rec["stamp"]["pid"]), (name, "running", got["p"].proc.pid))
        got["p"].finish()
        self.assertEqual(inflight.records(cfg), [])

    def test_readme_has_live_records_before_host_samples(self):
        with open(os.path.join(LOOP, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Live records", text)
        section = text.split("## Live records", 1)[1].split("\n## ", 1)
        self.assertTrue(section[1].startswith("Host samples"))
        for word in ("runs/live/", "opener", "issuer", "agent", "stamp", "running", "handed_in", "ended",
                     "Pending.finish()", "Pending.stop()", "SIGTERM"):
            self.assertIn(word, section[0], word)


class ReleaseKeptOutput(unittest.TestCase):
    """core.release helpers: where the kept log is and what the environment carries."""

    class Cfg:
        def __init__(self, root):
            self.worktree_root = root

        def runs_dir(self, *parts):
            return osx.makedirs(os.path.join(self.worktree_root, "runs", *parts))

        def env(self, **extra):
            return {k: str(v) for k, v in extra.items() if v is not None}

        values = {}
        machine_slots = None

    def test_log_path_is_absolute_under_runs_release(self):
        from core import release
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        path = release.log_path(self.Cfg(root), "g7")
        self.assertEqual(path, os.path.join(os.path.abspath(root), "runs", "release", "g7.log"))

    def test_env_carries_release_dir(self):
        from core import release
        env = release.env(self.Cfg("/x"), {"id": "g1"}, "abc", "/x/runs/release/g1")
        self.assertEqual(env["DEVLOOP_RELEASE_DIR"], "/x/runs/release/g1")


class LogtailUnits(unittest.TestCase):
    """core.logtail: lines / tail / stage, and the board aliases on top of them."""

    def log(self, text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "x.log")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_missing_and_none_paths(self):
        from core import logtail
        for path in (None, "", "/nonexistent/none.log"):
            self.assertIsNone(logtail.lines(path))
            self.assertEqual(logtail.tail(path, 5), "")
            self.assertIsNone(logtail.stage(path))

    def test_empty_and_short_log(self):
        from core import logtail
        self.assertEqual(logtail.tail(self.log(""), 3), "")
        self.assertIsNone(logtail.stage(self.log("")))
        path = self.log("a\nb\nc\n")
        self.assertEqual(logtail.tail(path, 2), "b\nc")
        self.assertEqual(logtail.tail(path, 10), "a\nb\nc")

    def test_stage_is_the_last_marked_line(self):
        from core import logtail
        mark = "devloop-" + "stage:"
        path = self.log("%s build\nx\n  %s no\n%s  upload \ny\n" % (mark, mark, mark))
        self.assertEqual(logtail.stage(path), "upload")

    def test_big_log_reads_only_the_end(self):
        from core import logtail
        body = "".join("line%07d\n" % i for i in range(150000))
        self.assertGreater(len(body), 1 << 20)
        path = self.log(body)
        got = logtail.lines(path)
        self.assertLess(len(got), 150000)
        self.assertEqual(got[-1], "line0149999")
        self.assertTrue(got[0].startswith("line"))
        self.assertEqual(len(got[0]), 11)           # the cut first line is dropped
        self.assertEqual(logtail.tail(path, 2), "line0149998\nline0149999")

    def test_board_aliases(self):
        import board
        mark = "devloop-" + "stage:"
        path = self.log("".join("r%d\n" % i for i in range(30)) + mark + " go\n")
        self.assertEqual(board.live_log_lines(path)[-1], mark + " go")
        self.assertEqual(board.live_tail(path).split("\n")[0], "r11")
        self.assertEqual(len(board.live_tail(path).split("\n")), 20)
        self.assertEqual(board.live_release_step(path), "go")
        self.assertEqual(board.live_tail(None), "")

    def test_failure_text_block(self):
        from core import release
        path = self.log("".join("L%02d\n" % i for i in range(1, 31)))
        out = release.failure_text(path, "rc 3\nmore")
        lines = out.split("\n")
        self.assertEqual(lines[:3], ["rc 3", "last step: (none printed)", "last 15 lines of the release log:"])
        self.assertEqual(lines[3:], ["L%02d" % i for i in range(16, 31)])


class LiveRecordUnits(unittest.TestCase):
    """The pieces behind the regression and release live records."""

    class Cfg(ReleaseKeptOutput.Cfg):
        regression_exclusive = True

    def test_regression_put_live_shape(self):
        import regression
        from core import inflight
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        cfg = self.Cfg(root)
        regression.put_live(cfg, "int/g4", "abc", "/o.json", "slot")
        regression.put_live(cfg, "int/g4", "abc", "/o.json", "full")
        recs = inflight.records(cfg)
        self.assertEqual([n for n, _ in recs], ["regression-int-g4"])
        rec = recs[0][1]
        self.assertEqual((rec["kind"], rec["group"], rec["stage"], rec["exclusive"]),
                         ("regression", "g4", "full", True))
        self.assertEqual(rec["stamp"]["pid"], os.getpid())
        regression.put_live(cfg, "feature", "abc", "/o.json", "full")
        self.assertEqual(dict(inflight.records(cfg))["regression-feature"]["group"], "feature")

    def test_release_record_is_gone_after_run(self):
        from unittest import mock
        from core import release, inflight
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        cfg = self.Cfg(root)
        cfg.require_release = lambda: "true"
        cfg.release_timeout_seconds = 5
        cfg.root = root
        seen = []
        real_wait = release.osx.wait

        def wait(proc, timeout):
            seen.append(inflight.records(cfg))
            return real_wait(proc, timeout)

        def detached(cfg_, repo, tree, sha):
            os.makedirs(tree)

        with mock.patch.object(release, "Git"), mock.patch.object(release.worktrees, "detached_tree", detached), \
                mock.patch.object(release.osx, "wait", wait):
            ok, _ = release.run(cfg, {"id": "g9"}, "abc")
        self.assertTrue(ok)
        self.assertEqual([n for n, _ in seen[0]], ["release-g9"])
        self.assertEqual(seen[0][0][1]["kind"], "release")
        self.assertEqual(inflight.records(cfg), [])


class T64Acceptance(LoopCase):
    """Acceptance of #64: live records under <worktrees>/runs/live/ (core/inflight.py, the opener,
    the issuer and every agent devloop starts)."""

    FMT = "%Y-%m-%dT%H:%M:%SZ"
    STAMP_RE = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if key.startswith("DEVLOOP_"):
                del os.environ[key]
        for key in list(self.env):
            if key.startswith("DEVLOOP_"):
                del self.env[key]
        os.environ["AC_ROOT"] = self.proj
        self.live_dir = os.path.join(self.tmp, "wt", "runs", "live")

    # ------------------------------------------------------------ helpers

    def mod(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("core.inflight"), "devloop/core/inflight.py is missing")
        from core import inflight
        return inflight

    def cfg(self):
        from core.config import Config
        return Config(self.proj)

    def epoch(self, ts):
        return calendar.timegm(time.strptime(ts, self.FMT))

    def sleeper(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.poll() is None and p.terminate(), p.wait()))
        return p

    def live_stamp(self, started=None):
        p = self.sleeper()
        return {"pid": p.pid, "host": osx.host(), "boot": osx.boot_id(), "started": started or osx.now()}

    def dead_stamp(self, started=None):
        return dict(self.live_stamp(started), boot="other-boot")

    def ago(self, seconds):
        return time.strftime(self.FMT, time.gmtime(time.time() - seconds))

    def live(self):
        """[(file name without .json, record)] of every JSON object in the live folder."""
        out = []
        for path in sorted(glob.glob(os.path.join(self.live_dir, "*.json"))):
            try:
                with open(path) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict):
                out.append((os.path.basename(path)[:-len(".json")], rec))
        return out

    def find(self, pred):
        for name, rec in self.live():
            if pred(rec):
                return name, rec
        return None

    def wait_for(self, pred, timeout=20, proc=None):
        """(name, record) of the first record matching pred, polled every 0.2 s; None on timeout
        or when proc exited first."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hit = self.find(pred)
            if hit:
                return hit
            if proc is not None and proc.poll() is not None:
                return self.find(pred)
            time.sleep(0.2)
        return None

    def popen(self, script, *args, hold=8):
        env = dict(self.env, FAKE_HOLD=str(hold))
        out = open(os.path.join(self.tmp, "%s-%d.out" % (script, time.time() * 1000)), "w")
        self.addCleanup(out.close)
        proc = subprocess.Popen(["python3", os.path.join(LOOP, script)] + list(args), cwd=self.proj,
                                env=env, stdout=out, stderr=subprocess.STDOUT, text=True)

        def reap():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(60)
        self.addCleanup(reap)
        return proc

    def kill_agent_later(self, rec):
        pid = (rec.get("stamp") or {}).get("pid")
        if pid:
            self.addCleanup(lambda: osx.alive(pid) and osx.kill_tree(pid))

    def pid_is(self, kind, pid):
        return lambda r: r.get("kind") == kind and (r.get("stamp") or {}).get("pid") == pid

    def proc_started(self, pid):
        with open("/proc/%d/stat" % pid) as fh:
            data = fh.read()
        ticks = int(data[data.rindex(")") + 2:].split()[19])
        with open("/proc/stat") as fh:
            btime = int([ln.split()[1] for ln in fh if ln.startswith("btime ")][0])
        return btime + ticks / os.sysconf("SC_CLK_TCK")

    def assert_here(self, st):
        self.assertEqual(st.get("host"), osx.host(), st)
        self.assertEqual(st.get("boot"), osx.boot_id(), st)

    def running_ticket(self):
        """Ticket 1 claimed for int/g1, ready for ticket_issuer.py started directly."""
        self.add(1, state="running", group="g1", target="int/g1", claimed_at=osx.now(), needs_verifier=False)
        git(self.proj, "branch", "int/g1", "main")

    def set_agent(self, role, argv):
        with open(os.path.join(self.proj, "board", "config.json")) as fh:
            agents_cfg = json.load(fh)["devloop"]["agents"]
        self.config(agents=dict(agents_cfg, **{role: argv}))

    # ------------------------------------------------------------ items 1-5: core/inflight.py

    def test_item1_module_and_folder(self):
        inflight = self.mod()
        for fn in ("folder", "put", "drop", "records", "status", "title_of"):
            self.assertTrue(callable(getattr(inflight, fn, None)), fn)
        cfg = self.cfg()
        self.assertEqual(inflight.folder(cfg), os.path.join(cfg.worktree_root, "runs", "live"))
        self.assertEqual(inflight.folder(cfg), self.live_dir)
        self.assertFalse(os.path.exists(self.live_dir))
        self.assertEqual(inflight.records(cfg), [])
        self.assertFalse(os.path.exists(self.live_dir))

    def test_item2_put_records_drop(self):
        inflight = self.mod()
        cfg = self.cfg()
        rb = {"kind": "agent", "stamp": self.live_stamp(), "n": [1, 2], "x": None}
        ra = {"kind": "opener", "stamp": self.live_stamp(), "title": "t"}
        inflight.put(cfg, "b-rec", rb)
        with open(os.path.join(self.live_dir, "b-rec.json")) as fh:
            self.assertEqual(json.load(fh), rb)
        inflight.put(cfg, "a-rec", ra)
        with open(os.path.join(self.live_dir, "a-rec.json")) as fh:
            self.assertEqual(json.load(fh), ra)
        with open(os.path.join(self.live_dir, "broken.json"), "w") as fh:
            fh.write("{not json")
        with open(os.path.join(self.live_dir, "list.json"), "w") as fh:
            json.dump([1, 2], fh)
        with open(os.path.join(self.live_dir, "other.txt"), "w") as fh:
            json.dump({"kind": "agent"}, fh)
        self.assertEqual(inflight.records(cfg), [("a-rec", ra), ("b-rec", rb)])
        inflight.drop(cfg, "a-rec")
        self.assertFalse(os.path.exists(os.path.join(self.live_dir, "a-rec.json")))
        inflight.drop(cfg, "a-rec")             # missing: no exception
        inflight.drop(cfg, "never-there")
        self.assertEqual(inflight.records(cfg), [("b-rec", rb)])

    def test_item3_status(self):
        inflight = self.mod()
        handed = os.path.join(self.tmp, "result.json")
        with open(handed, "w") as fh:
            fh.write("{}")
        missing = os.path.join(self.tmp, "nothing.json")
        live, dead = self.live_stamp(), self.dead_stamp()
        self.assertEqual(inflight.status({"stamp": live}), "running")
        self.assertEqual(inflight.status({"stamp": live, "result": handed}), "running")
        self.assertEqual(inflight.status({"stamp": dead, "result": handed}), "handed_in")
        self.assertEqual(inflight.status({"stamp": dead, "result": missing}), "ended")
        self.assertEqual(inflight.status({"stamp": dead, "result": None}), "ended")
        self.assertEqual(inflight.status({"stamp": dead}), "ended")
        self.assertEqual(inflight.status({}), "ended")
        self.assertEqual(inflight.status({"kind": "agent", "result": None}), "ended")

    def test_item4_put_sweeps_old_dead_records(self):
        inflight = self.mod()
        cfg = self.cfg()
        handed = os.path.join(self.tmp, "result.json")
        with open(handed, "w") as fh:
            fh.write("{}")
        os.makedirs(self.live_dir)
        files = {
            "old-dead": {"stamp": self.dead_stamp(self.ago(25 * 3600))},
            "old-handed": {"stamp": self.dead_stamp(self.ago(25 * 3600)), "result": handed},
            "recent-dead": {"stamp": self.dead_stamp(self.ago(3600))},
            "old-running": {"stamp": self.live_stamp(self.ago(2 * 86400))},
        }
        for name, rec in files.items():
            with open(os.path.join(self.live_dir, name + ".json"), "w") as fh:
                json.dump(rec, fh)
        new = {"kind": "agent", "stamp": self.live_stamp()}
        inflight.put(cfg, "new", new)
        left = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(self.live_dir, "*.json")))
        self.assertEqual(left, ["new", "old-running", "recent-dead"])

    def test_item5_title_of(self):
        inflight = self.mod()
        cases = [("intro\n## Board: tab\nbody", "Board: tab"), ("first line\nsecond", "first line"),
                 ("#\n\nplain", "plain"), ("x" * 200, "x" * 120), ("", None),
                 ("\n   \n\t\n", None), ("  ###  Spaced title  \nx", "Spaced title"),
                 ("\n\n  lead  \n# Head", "Head"), ("# " + "y" * 300, "y" * 120)]
        for text, want in cases:
            self.assertEqual(inflight.title_of(text), want, repr(text))

    # ------------------------------------------------------------ items 6, 7: the opener

    def check_opener(self, args, text, via):
        inflight = self.mod()
        from core import agents
        cfg = self.cfg()
        t0 = time.time()
        proc = self.popen("ticket_creator.py", *args, hold=8)
        hit = self.wait_for(self.pid_is("opener", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no opener record for pid %d; folder: %s" % (proc.pid, self.live()))
        _, rec = hit
        self.assertEqual(len([r for _, r in self.live() if r.get("kind") == "opener"]), 1, self.live())
        st = rec["stamp"]
        self.assert_here(st)
        self.assertRegex(st.get("started") or "", "^%s$" % self.STAMP_RE)
        started = self.epoch(st["started"])
        self.assertGreaterEqual(started, int(t0) - 1, st)
        self.assertLessEqual(started, t0 + 3, st)
        self.assertEqual(rec.get("title"), inflight.title_of(text))
        self.assertIsNone(rec.get("revise"), rec)
        self.assertEqual(rec.get("via"), via, rec)
        self.assertEqual(rec.get("model"), agents.model_of(cfg.agent("creator")), rec)
        self.assertEqual(rec.get("effort"), agents.effort_of(cfg.agent("creator")), rec)
        self.assertIsNone(rec.get("result"), rec)
        proc.wait(60)
        self.assertIsNone(self.find(self.pid_is("opener", proc.pid)), self.live())

    def test_item6_opener_record_while_a_request_runs(self):
        text = "intro\n# My title\nbody"
        self.check_opener(["--request", text], text, "main")
        self.assertEqual(self.mod().title_of(text), "My title")

    def test_item6_opener_record_from_a_request_file_via_web(self):
        text = "## Web asked title\nplease add a page\n"
        path = os.path.join(self.tmp, "request.md")
        with open(path, "w") as fh:
            fh.write(text)
        self.check_opener(["--request-file", path, "--via", "web"], text, "web")

    def test_item7_opener_record_while_a_revise_runs(self):
        self.mod()
        self.add(1)
        proc = self.popen("ticket_creator.py", "--revise", "1", "--from", "worker", "--reason", "r", hold=8)
        hit = self.wait_for(self.pid_is("opener", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no opener record for pid %d; folder: %s" % (proc.pid, self.live()))
        rec = hit[1]
        self.assertEqual(rec.get("revise"), "1", rec)
        self.assertIsNone(rec.get("title"), rec)
        self.assert_here(rec["stamp"])
        proc.wait(60)
        self.assertIsNone(self.find(self.pid_is("opener", proc.pid)), self.live())

    # ------------------------------------------------------------ items 8, 11: agent and issuer under the controller

    def test_item8_and_11_agent_and_issuer_records_under_the_controller(self):
        self.mod()
        from core import agents
        self.add(1, complexity="simple", needs_verifier=False)
        proc = self.popen("ticket_controller.py", hold=8)
        hit = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("role") == "worker_simple", proc=proc)
        self.assertIsNotNone(hit, "no worker_simple agent record; folder: %s" % self.live())
        issuer = self.find(lambda r: r.get("kind") == "issuer")
        t = self.snap().ticket("1")
        rec = hit[1]
        st = rec["stamp"]
        parent = (t.get("run") or {}).get("pid")
        self.assertTrue(parent, t)
        self.assertEqual(rec.get("state"), "running", rec)
        self.assertEqual(rec.get("parent"), parent, rec)
        self.assertNotEqual(st.get("pid"), parent, rec)
        self.assertTrue(osx.alive(st.get("pid")), rec)
        self.assert_here(st)
        self.assertLessEqual(abs(self.epoch(st["started"]) - self.proc_started(st["pid"])), 3, rec)
        self.assertEqual(rec.get("ticket"), "1", rec)
        self.assertIsNone(rec.get("group"), rec)        # the issuer's extra_env has no DEVLOOP_GROUP
        self.assertEqual(rec.get("round"), 1, rec)
        run_dir = os.path.join(self.tmp, "wt", "runs", "1", "r1-worker")
        self.assertEqual(rec.get("run_dir"), run_dir, rec)
        log = rec.get("log") or ""
        self.assertEqual(os.path.dirname(log), run_dir, rec)
        self.assertRegex(os.path.basename(log), r"^agent-\d+\.log$")
        self.assertTrue(os.path.exists(log), rec)
        self.assertEqual(rec.get("result"), os.path.join(run_dir, "result.json"), rec)
        self.assertEqual(rec.get("command"), "python3", rec)
        self.assertEqual(rec.get("model"), agents.model_of(AGENT), rec)
        self.assertIsNone(rec.get("effort"), rec)
        self.assertRegex(rec.get("since") or "", "^%s$" % self.STAMP_RE)
        self.assertIsNone(rec.get("until"), rec)
        # item 11: the issuer's record at the same moment
        self.assertIsNotNone(issuer, "no issuer record; folder: %s" % self.live())
        irec = issuer[1]
        self.assertEqual(irec.get("ticket"), "1", irec)
        self.assertEqual(irec.get("target"), t.get("target"), irec)
        self.assertTrue(t.get("target"), t)
        self.assertEqual(irec.get("round"), t.get("round"), (irec, t.get("round")))
        self.assertEqual(irec.get("round"), 1, irec)
        self.assertEqual(irec["stamp"].get("pid"), parent, irec)
        proc.wait(120)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual([r for _, r in self.live() if r.get("kind") in ("agent", "issuer")], [])

    def test_item8_in_process_record_follows_extra_env_and_goes_with_stop(self):
        self.mod()
        from core import agents
        argv = ["sh", "-c", "cat >/dev/null; sleep 30"]
        self.set_agent("worker_simple", argv)
        os.environ["DEVLOOP_TICKET"] = "999"   # inherited: never read for the record
        os.environ["DEVLOOP_GROUP"] = "g999"
        cfg = self.cfg()
        run_dir = os.path.join(self.tmp, "run-a")
        p = agents.start(cfg, "worker", "go", self.proj, run_dir, 60,
                         extra_env={"DEVLOOP_TICKET": "7", "DEVLOOP_GROUP": "g7"}, round_no=2,
                         chain_role="worker_simple")
        self.addCleanup(lambda: p.proc is not None and osx.alive(p.proc.pid) and osx.kill_tree(p.proc.pid))
        hit = self.wait_for(lambda r: r.get("kind") == "agent", timeout=5)
        self.assertIsNotNone(hit, "no agent record; folder: %s" % self.live())
        rec = hit[1]
        self.assertEqual(rec.get("state"), "running", rec)
        self.assertEqual(rec["stamp"].get("pid"), p.proc.pid, rec)
        self.assertEqual(rec.get("parent"), os.getpid(), rec)
        self.assertEqual((rec.get("role"), rec.get("ticket"), rec.get("group"), rec.get("round")),
                         ("worker_simple", "7", "g7", 2), rec)
        self.assertEqual(rec.get("run_dir"), run_dir, rec)
        self.assertEqual(rec.get("log"), p.log, rec)
        self.assertEqual(rec.get("command"), "sh", rec)
        self.assertEqual(rec.get("model"), agents.model_of(argv), rec)
        self.assertEqual(rec.get("effort"), agents.effort_of(argv), rec)
        self.assertEqual(rec.get("result"), os.path.join(run_dir, "result.json"), rec)
        p.stop()
        self.assertEqual([r for _, r in self.live() if r.get("kind") == "agent"], [])

    def test_item8_in_process_record_without_extra_env_goes_with_finish(self):
        self.mod()
        from core import agents
        self.set_agent("worker", ["sh", "-c", 'cat >/dev/null; sleep 2; echo \'{"kind": "done"}\' > "$DEVLOOP_RESULT"'])
        os.environ["DEVLOOP_TICKET"] = "999"
        os.environ["DEVLOOP_GROUP"] = "g999"
        cfg = self.cfg()
        run_dir = os.path.join(self.tmp, "run-b")
        p = agents.start(cfg, "worker", "go", self.proj, run_dir, 60, round_no=1)
        hit = self.wait_for(lambda r: r.get("kind") == "agent", timeout=5)
        self.assertIsNotNone(hit, "no agent record; folder: %s" % self.live())
        rec = hit[1]
        self.assertEqual((rec.get("role"), rec.get("ticket"), rec.get("group")), ("worker", None, None), rec)
        hand = p.finish()
        self.assertEqual(hand.kind, "done")
        self.assertEqual([r for _, r in self.live() if r.get("kind") == "agent"], [])

    # ------------------------------------------------------------ item 9: waiting for a machine slot

    def test_item9_slot_wait_then_running_in_the_same_record(self):
        self.mod()
        from core import slots
        self.config(machine_slots=1)
        slot = slots.take(self.cfg(), None)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        self.add(1, complexity="simple", needs_verifier=False)
        t0 = time.time()
        proc = self.popen("ticket_controller.py", hold=2)
        hit = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("state") == "slot", proc=proc)
        seen = time.time()
        self.assertIsNotNone(hit, "no agent record waiting for a slot; folder: %s" % self.live())
        name, rec = hit
        starter = (self.snap().ticket("1").get("run") or {}).get("pid")
        self.assertTrue(starter)
        self.assertEqual(rec["stamp"].get("pid"), starter, rec)
        self.assertRegex(rec.get("since") or "", "^%s$" % self.STAMP_RE)
        since = self.epoch(rec["since"])
        self.assertGreaterEqual(since, int(t0) - 1, rec)
        self.assertLessEqual(since, seen + 1, rec)
        slot.release()
        hit = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("state") == "running", proc=proc)
        self.assertIsNotNone(hit, "the waiting record never turned running; folder: %s" % self.live())
        self.assertEqual(hit[0], name)
        self.assertNotEqual(hit[1]["stamp"].get("pid"), starter, hit[1])
        proc.wait(120)

    # ------------------------------------------------------------ item 10: waiting for a quota reset

    def test_item10_quota_wait_record(self):
        self.mod()
        import threading
        from core import agents
        count = os.path.join(self.tmp, "count")
        self.set_agent("worker", ["sh", "-c", 'cat >/dev/null; n=$(cat %s 2>/dev/null || echo 0); '
                                  'echo $((n+1)) > %s; if [ "$n" -lt 1 ]; then echo "Error: usage limit reached"; '
                                  'exit 1; fi; sleep 4; echo \'{"kind": "done"}\' > "$DEVLOOP_RESULT"'
                                  % (count, count)])
        self.config(quota_retry_seconds=3)
        cfg = self.cfg()
        phases, untils = [], []

        def hook(phase, row=None, until=None, text=None):
            phases.append(phase)
            if phase == "wait":
                untils.append(until)
        p = agents.start(cfg, "worker", "go", self.proj, os.path.join(self.tmp, "run"), 60, on_quota=hook)
        done = {}
        th = threading.Thread(target=lambda: done.setdefault("hand", p.finish()))
        th.start()
        self.addCleanup(th.join, 60)
        deadline = time.time() + 15
        while "wait" not in phases and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("wait", phases)
        rec = self.find(lambda r: r.get("kind") == "agent")
        self.assertNotIn("over", phases, "the quota wait ended before the record was read")
        self.assertIsNotNone(rec, "no agent record during the quota wait; folder: %s" % self.live())
        name, rec = rec
        self.assertEqual(rec.get("state"), "quota", rec)
        self.assertEqual(rec["stamp"].get("pid"), os.getpid(), rec)
        self.assertEqual(rec.get("until"), time.strftime(self.FMT, time.gmtime(untils[0])), rec)
        hit = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("state") == "running", timeout=10)
        self.assertIsNotNone(hit, "no running record after the wait; folder: %s" % self.live())
        self.assertEqual(hit[0], name)
        self.assertNotEqual(hit[1]["stamp"].get("pid"), os.getpid(), hit[1])
        self.assertEqual(hit[1]["stamp"].get("pid"), p.proc.pid, hit[1])
        th.join(30)
        self.assertEqual(done["hand"].kind, "done")
        self.assertEqual([r for _, r in self.live() if r.get("kind") == "agent"], [])

    # ------------------------------------------------------------ item 11: the issuer started directly

    def test_item11_round_is_rewritten_at_each_round_start(self):
        self.mod()
        self.add(1)
        self.plan(worker={"1": ["wrong", "ok"]})
        proc = self.popen("ticket_controller.py", hold=3)
        hit = self.wait_for(lambda r: r.get("kind") == "issuer" and r.get("round") == 1, timeout=30, proc=proc)
        self.assertIsNotNone(hit, "no issuer record in round 1; folder: %s" % self.live())
        hit = self.wait_for(lambda r: r.get("kind") == "issuer" and r.get("round") == 2, timeout=60, proc=proc)
        self.assertIsNotNone(hit, "the issuer record never said round 2; folder: %s" % self.live())
        self.assertEqual(hit[1].get("ticket"), "1")
        proc.wait(120)
        self.assertEqual(self.snap().ticket("1").get("round"), 2)
        self.assertEqual([r for _, r in self.live() if r.get("kind") == "issuer"], [])

    def test_item11_issuer_record_goes_on_sigterm(self):
        self.mod()
        self.running_ticket()
        proc = self.popen("ticket_issuer.py", "--id", "1", "--target", "int/g1", hold=30)
        hit = self.wait_for(self.pid_is("issuer", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no issuer record; folder: %s" % self.live())
        self.assertEqual((hit[1].get("ticket"), hit[1].get("target")), ("1", "int/g1"), hit[1])
        agent = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("parent") == proc.pid, proc=proc)
        if agent:
            self.kill_agent_later(agent[1])
        proc.terminate()
        proc.wait(60)
        self.assertIsNone(self.find(self.pid_is("issuer", proc.pid)), self.live())

    def test_item11_issuer_record_goes_when_stuck(self):
        self.mod()
        self.running_ticket()
        self.plan(worker={"1": ["blocked"]})
        proc = self.popen("ticket_issuer.py", "--id", "1", "--target", "int/g1", hold=4)
        hit = self.wait_for(self.pid_is("issuer", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no issuer record; folder: %s" % self.live())
        proc.wait(60)
        self.assertEqual(self.state(1), "stuck")
        self.assertIsNone(self.find(self.pid_is("issuer", proc.pid)), self.live())

    # ------------------------------------------------------------ item 12: killed processes leave an 'ended' record

    def test_item12_killed_opener_leaves_an_ended_record(self):
        inflight = self.mod()
        proc = self.popen("ticket_creator.py", "--request", "add a page", hold=30)
        hit = self.wait_for(self.pid_is("opener", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no opener record; folder: %s" % self.live())
        agent = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("parent") == proc.pid, proc=proc)
        self.assertIsNotNone(agent, "no creator agent record; folder: %s" % self.live())
        self.kill_agent_later(agent[1])
        proc.kill()
        proc.wait(30)
        left = self.find(self.pid_is("opener", proc.pid))
        self.assertIsNotNone(left, self.live())
        self.assertEqual(inflight.status(left[1]), "ended")

    def test_item12_killed_issuer_leaves_an_ended_record(self):
        inflight = self.mod()
        self.running_ticket()
        proc = self.popen("ticket_issuer.py", "--id", "1", "--target", "int/g1", hold=30)
        hit = self.wait_for(self.pid_is("issuer", proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no issuer record; folder: %s" % self.live())
        agent = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("parent") == proc.pid, proc=proc)
        self.assertIsNotNone(agent, "no worker agent record; folder: %s" % self.live())
        self.kill_agent_later(agent[1])
        proc.kill()
        proc.wait(30)
        left = self.find(self.pid_is("issuer", proc.pid))
        self.assertIsNotNone(left, self.live())
        self.assertEqual(inflight.status(left[1]), "ended")


class T65Acceptance(LoopCase):
    """Acceptance of #65: live records of regression.py, auto_fixer.py and release.run under
    <worktrees>/runs/live/, with the stage a regression is in."""

    FMT = T64Acceptance.FMT
    STAMP_RE = T64Acceptance.STAMP_RE
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if key.startswith("DEVLOOP_"):
                del os.environ[key]
        for key in list(self.env):
            if key.startswith("DEVLOOP_"):
                del self.env[key]
        os.environ["AC_ROOT"] = self.proj
        self.live_dir = os.path.join(self.tmp, "wt", "runs", "live")

    mod = T64Acceptance.mod
    cfg = T64Acceptance.cfg
    epoch = T64Acceptance.epoch
    live = T64Acceptance.live
    find = T64Acceptance.find
    wait_for = T64Acceptance.wait_for
    popen = T64Acceptance.popen
    kill_agent_later = T64Acceptance.kill_agent_later
    proc_started = T64Acceptance.proc_started
    assert_here = T64Acceptance.assert_here

    # ------------------------------------------------------------ helpers

    def hold_tests(self, preflight=0, full=0, preflight_cmd=None):
        """board/config.json tests: preflight / full held S seconds before the fake tests run."""
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        if preflight_cmd is not None:
            cfg["tests"]["preflight"] = preflight_cmd
        elif preflight:
            cfg["tests"]["preflight"] = "sh -c 'sleep %d; %s preflight'" % (preflight, TESTS)
        if full:
            cfg["tests"]["full"] = "sh -c 'sleep %d; %s full'" % (full, TESTS)
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "tests held")

    def int_branch(self):
        git(self.proj, "branch", "int/g1", "main")
        return git(self.proj, "rev-parse", "int/g1")

    def regression(self, out=None, branch="int/g1"):
        out = out or os.path.join(self.tmp, "r.json")
        t0 = time.time()
        proc = self.popen("regression.py", "--branch", branch, "--out", out, hold=0)
        return proc, out, t0

    def is_reg(self, pid, stage=None):
        return lambda r: (r.get("kind") == "regression" and (r.get("stamp") or {}).get("pid") == pid
                          and (stage is None or r.get("stage") == stage))

    def hold_slot(self):
        from core import slots
        os.environ.pop("DEVLOOP_SLOT", None)
        slot = slots.take(self.cfg(), None)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        return slot

    # ------------------------------------------------------------ items 1, 2: fields and stages

    def test_item1_2_regression_record_fields_and_stages(self):
        self.mod()
        self.hold_tests(preflight=6, full=6)
        sha = self.int_branch()
        proc, out, t0 = self.regression()
        first = self.wait_for(self.is_reg(proc.pid), proc=proc)
        self.assertIsNotNone(first, "no regression record for pid %d; folder: %s" % (proc.pid, self.live()))
        self.assertEqual(first[1].get("stage"), "preflight", first[1])   # no machine_slots: first stage
        pre = self.wait_for(self.is_reg(proc.pid, "preflight"), proc=proc)
        seen_pre = time.time()
        self.assertIsNotNone(pre, self.live())
        name, rec = pre
        self.assertEqual(name, "regression-int-g1")
        self.assertEqual(len([r for _, r in self.live() if r.get("kind") == "regression"]), 1, self.live())
        self.assertEqual(rec.get("group"), "g1", rec)
        self.assertEqual(rec.get("branch"), "int/g1", rec)
        self.assertEqual(rec.get("sha"), sha, rec)
        self.assertIs(rec.get("exclusive"), False, rec)
        self.assertEqual(rec.get("result"), out, rec)
        st = rec.get("stamp") or {}
        self.assertEqual(st.get("pid"), proc.pid, rec)
        self.assert_here(st)
        self.assertRegex(st.get("started") or "", "^%s$" % self.STAMP_RE)
        self.assertLessEqual(abs(self.epoch(st["started"]) - self.proc_started(proc.pid)), 3, rec)
        self.assertRegex(rec.get("since") or "", "^%s$" % self.STAMP_RE)
        since_pre = self.epoch(rec["since"])
        self.assertGreaterEqual(since_pre, int(t0) - 1, rec)
        self.assertLessEqual(since_pre, seen_pre + 1, rec)
        full = self.wait_for(self.is_reg(proc.pid, "full"), timeout=25, proc=proc)
        seen_full = time.time()
        self.assertIsNotNone(full, "the record never showed stage 'full'; folder: %s" % self.live())
        self.assertEqual(full[0], name)
        since_full = self.epoch(full[1].get("since") or "")
        self.assertGreaterEqual(since_full, since_pre + 5, full[1])   # preflight held 6 s
        self.assertLessEqual(since_full, seen_full + 1, full[1])
        self.assertEqual(proc.wait(60), 0)
        self.assertEqual(self.find(self.is_reg(proc.pid)), None, self.live())

    def test_item1_group_is_the_branch_without_int_prefix(self):
        inflight = self.mod()
        self.hold_tests(preflight=4)
        git(self.proj, "branch", "feature/x", "main")
        sha = git(self.proj, "rev-parse", "feature/x")
        proc, out, _ = self.regression(branch="feature/x")
        hit = self.wait_for(self.is_reg(proc.pid), proc=proc)
        self.assertIsNotNone(hit, self.live())
        self.assertEqual(hit[0], "regression-feature-x")
        self.assertEqual((hit[1].get("group"), hit[1].get("branch"), hit[1].get("sha")),
                         ("feature/x", "feature/x", sha), hit[1])
        self.assertIn(hit, inflight.records(self.cfg()))
        proc.wait(60)
        self.assertIsNone(self.find(self.is_reg(proc.pid)), self.live())

    def test_item2_slot_stage_then_preflight(self):
        self.mod()
        self.config(machine_slots=1)
        self.hold_tests(preflight=6)
        self.int_branch()
        slot = self.hold_slot()
        proc, _, t0 = self.regression()
        hit = self.wait_for(self.is_reg(proc.pid), proc=proc)
        seen = time.time()
        self.assertIsNotNone(hit, self.live())
        rec = hit[1]
        self.assertEqual(rec.get("stage"), "slot", rec)
        self.assertIs(rec.get("exclusive"), False, rec)
        since = self.epoch(rec.get("since") or "")
        self.assertGreaterEqual(since, int(t0) - 1, rec)
        self.assertLessEqual(since, seen + 1, rec)
        time.sleep(1.5)
        self.assertEqual(self.find(self.is_reg(proc.pid))[1].get("stage"), "slot", self.live())
        slot.release()
        pre = self.wait_for(self.is_reg(proc.pid, "preflight"), proc=proc)
        self.assertIsNotNone(pre, "never left 'slot' for 'preflight'; folder: %s" % self.live())
        self.assertEqual(proc.wait(60), 0)
        self.assertIsNone(self.find(self.is_reg(proc.pid)), self.live())

    def test_item2_machine_stage_for_an_exclusive_regression(self):
        self.mod()
        self.config(machine_slots=1, regression_exclusive=True)
        self.hold_tests(preflight=4)
        self.int_branch()
        from core import slots
        os.environ.pop("DEVLOOP_SLOT", None)
        os.environ.pop("DEVLOOP_MACHINE", None)
        slot = slots.take_test_run(self.cfg(), None)    # #143: a test run in flight holds it off
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        proc, _, _ = self.regression()
        hit = self.wait_for(self.is_reg(proc.pid), proc=proc)
        self.assertIsNotNone(hit, self.live())
        self.assertEqual(hit[1].get("stage"), "machine", hit[1])
        self.assertIs(hit[1].get("exclusive"), True, hit[1])
        slot.release()
        pre = self.wait_for(self.is_reg(proc.pid, "preflight"), proc=proc)
        self.assertIsNotNone(pre, "never left 'machine' for 'preflight'; folder: %s" % self.live())
        self.assertEqual(proc.wait(60), 0)
        self.assertIsNone(self.find(self.is_reg(proc.pid)), self.live())

    # ------------------------------------------------------------ item 3: the record goes on exit, stays on kill

    def test_item3_record_gone_after_a_failed_preflight(self):
        self.mod()
        self.hold_tests(preflight_cmd="sh -c 'sleep 4; exit 3'")
        self.int_branch()
        proc, _, _ = self.regression()
        self.assertIsNotNone(self.wait_for(self.is_reg(proc.pid), proc=proc), self.live())
        rc = proc.wait(60)
        self.assertNotEqual(rc, 0)
        self.assertIsNone(self.find(self.is_reg(proc.pid)), self.live())

    def test_item3_record_gone_after_an_osfailure(self):
        self.mod()
        self.hold_tests(preflight=4)
        self.int_branch()
        od = os.path.join(self.tmp, "od")
        os.makedirs(od)
        proc, _, _ = self.regression(out=os.path.join(od, "r.json"))
        self.assertIsNotNone(self.wait_for(self.is_reg(proc.pid, "preflight"), proc=proc), self.live())
        shutil.rmtree(od)
        with open(od, "w") as fh:          # --out's folder is now a file: writing it fails
            fh.write("x")
        self.assertEqual(proc.wait(60), 2)
        self.assertIsNone(self.find(self.is_reg(proc.pid)), self.live())

    def test_item3_killed_regression_leaves_an_ended_record(self):
        inflight = self.mod()
        self.hold_tests(preflight=6)
        self.int_branch()
        proc, _, _ = self.regression()
        hit = self.wait_for(self.is_reg(proc.pid, "preflight"), proc=proc)
        self.assertIsNotNone(hit, self.live())
        proc.kill()
        proc.wait(30)
        left = self.find(self.is_reg(proc.pid))
        self.assertIsNotNone(left, self.live())
        self.assertEqual(inflight.status(left[1]), "ended")

    # ------------------------------------------------------------ item 4: auto_fixer

    def fixing_group(self, fix_round=2):
        self.add(1, state="merged", group="g1")
        branch = self.int_branch()
        group = {"id": "g1", "state": "fixing", "branch": "int/g1", "members": ["1"]}
        if fix_round is not None:
            group["fix_round"] = fix_round
        self.pool.update(lambda tx: tx.put_group(group), "test: fixing group")
        report = os.path.join(self.tmp, "report.json")
        with open(report, "w") as fh:
            json.dump({"status": "red", "summary": "x"}, fh)
        return report, branch

    def is_fixer(self, pid):
        return lambda r: r.get("kind") == "fixer" and (r.get("stamp") or {}).get("pid") == pid

    def check_fixer(self, fix_round, want_round, hold=8):
        self.mod()
        report, _ = self.fixing_group(fix_round)
        proc = self.popen("auto_fixer.py", "--group", "g1", "--report", report, hold=hold)
        hit = self.wait_for(self.is_fixer(proc.pid), proc=proc)
        self.assertIsNotNone(hit, "no fixer record for pid %d; folder: %s" % (proc.pid, self.live()))
        name, rec = hit
        self.assertEqual(name, "fixer-g1")
        self.assertEqual(rec.get("group"), "g1", rec)
        self.assertEqual(rec.get("round"), want_round, rec)
        self.assertEqual(rec.get("max_rounds"), self.cfg().max_rounds, rec)
        self.assertIsNone(rec.get("result"), rec)
        self.assert_here(rec.get("stamp") or {})
        agent = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("parent") == proc.pid, proc=proc)
        self.assertIsNotNone(agent, "no fixer agent record; folder: %s" % self.live())
        self.kill_agent_later(agent[1])
        self.assertEqual((agent[1].get("role"), agent[1].get("group")), ("fixer", "g1"), agent[1])
        return proc

    def test_item4_fixer_record_and_its_agent_then_gone(self):
        proc = self.check_fixer(2, 2)
        self.assertIn(proc.wait(60), (0, 1))
        self.assertIsNone(self.find(self.is_fixer(proc.pid)), self.live())
        self.assertEqual([r for _, r in self.live() if r.get("kind") == "agent"], [])

    def test_item4_round_zero_when_fix_round_missing_and_gone_after_exit_3(self):
        self.plan(fixer={"g1": ["decision"]})
        proc = self.check_fixer(None, 0, hold=4)
        self.assertEqual(proc.wait(60), 3)
        self.assertIsNone(self.find(self.is_fixer(proc.pid)), self.live())

    def test_item4_fixer_record_gone_after_sigterm(self):
        proc = self.check_fixer(2, 2, hold=30)
        proc.terminate()
        self.assertEqual(proc.wait(60), 4)
        self.assertIsNone(self.find(self.is_fixer(proc.pid)), self.live())

    # ------------------------------------------------------------ item 5: release

    def landed_group(self):
        sha = git(self.proj, "rev-parse", "main")
        self.add(1, state="landed", group="g1")
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "state": "landed", "landed_sha": sha,
                                                  "branch": "int/g1", "members": ["1"]}),
                         "test: landed group")
        return sha

    def is_release(self):
        return lambda r: r.get("kind") == "release"

    def check_release(self, caller, sha):
        from core import release
        hit = self.wait_for(self.is_release(), timeout=25, proc=caller)
        self.assertIsNotNone(hit, "no release record; folder: %s" % self.live())
        name, rec = hit
        st = rec.get("stamp") or {}
        self.assertEqual(name, "release-g1")
        self.assertEqual(rec.get("group"), "g1", rec)
        self.assertEqual(rec.get("sha"), sha, rec)
        self.assertEqual(rec.get("log"), release.log_path(self.cfg(), "g1"), rec)
        self.assertIsNone(rec.get("result"), rec)
        self.assertNotEqual(st.get("pid"), caller.pid, rec)
        self.assertTrue(osx.alive(st.get("pid")), rec)
        with open("/proc/%d/cmdline" % st["pid"]) as fh:
            argv = fh.read().split("\0")
        self.assertEqual(argv[:2], ["sh", "-c"], argv)
        self.assert_here(st)
        self.assertLessEqual(abs(self.epoch(st.get("started") or "") - self.proc_started(st["pid"])), 3, rec)
        return rec

    def test_item5_release_record_from_tickets_release(self):
        self.mod()
        sha = self.landed_group()
        self.config(release="echo devloop-stage: build; sleep 4")
        proc = self.popen("tickets.py", "release", "g1", hold=0)
        self.check_release(proc, sha)
        self.assertEqual(proc.wait(60), 0)
        self.assertIsNone(self.find(self.is_release()), self.live())

    def test_item5_release_record_gone_after_failure_and_timeout(self):
        self.mod()
        sha = self.landed_group()
        self.config(release="sleep 3; exit 7")
        proc = self.popen("tickets.py", "release", "g1", hold=0)
        self.check_release(proc, sha)
        self.assertNotEqual(proc.wait(60), 0)
        self.assertIsNone(self.find(self.is_release()), self.live())
        self.config(release="sleep 30", release_timeout_seconds=3)
        proc = self.popen("tickets.py", "release", "g1", hold=0)
        self.check_release(proc, sha)
        self.assertNotEqual(proc.wait(60), 0)
        self.assertIsNone(self.find(self.is_release()), self.live())

    def test_item5_release_record_at_the_controllers_landing(self):
        self.mod()
        self.config(release="sleep 4; echo done >> %s" % self.released)
        self.add(1, complexity="simple", needs_verifier=False)
        proc = self.popen("ticket_controller.py", hold=0)
        from core import release
        hit = self.wait_for(self.is_release(), timeout=60, proc=proc)
        self.assertIsNotNone(hit, "no release record at landing; folder: %s" % self.live())
        rec = hit[1]
        st = rec.get("stamp") or {}
        self.assertEqual(hit[0], "release-g1")
        self.assertNotEqual(st.get("pid"), proc.pid, rec)
        self.assertTrue(osx.alive(st.get("pid")), rec)
        self.assertEqual(rec.get("log"), release.log_path(self.cfg(), "g1"), rec)
        proc.wait(120)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(rec.get("sha"), self.snap().groups["g1"].get("landed_sha"), rec)
        self.assertIsNone(self.find(self.is_release()), self.live())


class T66Acceptance(LoopCase):
    """Acceptance of #66: the board's 進行中 tab, one row per running opener, agent, agent-less
    issuer, regression, fixer and release, read from the live records under <worktrees>/runs/live/.
    Expected texts are the ticket's own; times come from time.localtime of the chosen epochs."""

    FMT = "%Y-%m-%dT%H:%M:%SZ"
    CELLS = ("what", "state", "model", "start", "elapsed", "detail")
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
            "source", "track", "wbr"}
    REFRESH = '<a class="refresh" href="?tab=live">重新整理</a>'

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if key.startswith("DEVLOOP_"):
                del os.environ[key]
        for key in list(self.env):
            if key.startswith("DEVLOOP_"):
                del self.env[key]
        os.environ["AC_ROOT"] = self.proj
        self.live_dir = os.path.join(self.tmp, "wt", "runs", "live")
        self.NOW = int(time.time())

    cfg = T64Acceptance.cfg
    live = T64Acceptance.live
    find = T64Acceptance.find
    wait_for = T64Acceptance.wait_for
    popen = T64Acceptance.popen
    kill_agent_later = T64Acceptance.kill_agent_later

    # ------------------------------------------------------------ helpers

    def need(self, *names):
        import board
        self.assertTrue(hasattr(board, "TABS"), "board.TABS is missing")
        self.assertIn("live", [t[0] for t in board.TABS], "board.TABS has no 'live' entry")
        for name in ("render_live_tab",) + names:
            self.assertTrue(callable(getattr(board, name, None)), "board.%s is missing" % name)
        return board

    def ts(self, epoch):
        return time.strftime(self.FMT, time.gmtime(epoch))

    def clock(self, epoch):
        return time.strftime("%m-%d %H:%M:%S", time.localtime(epoch))

    def stamp(self, started, alive=True):
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (p.poll() is None and p.terminate(), p.wait()))
        return {"pid": p.pid, "host": osx.host(), "boot": osx.boot_id() if alive else "other-boot",
                "started": self.ts(started)}

    def put(self, name, kind, started, alive=True, **fields):
        from core import inflight
        rec = {"kind": kind, "stamp": self.stamp(started, alive), "result": None}
        rec.update(fields)
        inflight.put(self.cfg(), name, rec)
        return rec

    def agent(self, name, parent, started, alive=True, **fields):
        rec = {"parent": parent, "role": "worker", "ticket": None, "group": None, "round": None,
               "run_dir": None, "log": None, "command": "python3", "model": "m", "effort": None,
               "state": "running", "since": self.ts(started), "until": None}
        rec.update(fields)
        return self.put(name, "agent", started, alive, **rec)

    def log_file(self, name, lines=30, extra=None):
        path = os.path.join(self.tmp, "%s.log" % name)
        body = ["L%02d %s" % (i, name) for i in range(1, lines + 1)]
        if extra:
            body[-1] = body[-1] + " " + extra
        with open(path, "w") as fh:
            fh.write("\n".join(body) + "\n")
        return path, body

    def page(self, query=None, now="fixed"):
        import board
        from core.config import Config
        when = self.NOW if now == "fixed" else now
        return board.render(Config(self.proj), self.snap(), query={"tab": "live"} if query is None else query,
                            now=when)

    def parse(self, page):
        """{'tabs': count of id=tab-live, 'tables': table.live inside it, 'rows': [row], 'urls':
        href/action values inside it, 'links': [(attrs, text)] of <a> inside it}; a row is a <tr>
        with at least one <td> inside table.live: {'attrs', 'cells': {class: text}, 'pres': {class: [text]}}."""
        from html.parser import HTMLParser
        void = self.VOID

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack, self.out = [], {"tabs": 0, "tables": 0, "rows": [], "urls": [], "links": []}
                self.row = self.cell = self.pre = self.link = None

            def marks(self):
                return [m for _, m in self.stack]

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                cls = (a.get("class") or "").split()
                marks = self.marks()
                mark = None
                if "tab" not in marks:
                    if a.get("id") == "tab-live":
                        mark = "tab"
                        self.out["tabs"] += 1
                else:
                    for k, v in attrs:
                        if k in ("href", "action"):
                            self.out["urls"].append(v or "")
                    if tag == "table" and "live" in cls and "table" not in marks:
                        mark = "table"
                        self.out["tables"] += 1
                    elif tag == "tr" and "table" in marks:
                        mark = "tr"
                        self.row = {"attrs": a, "cells": {}, "pres": {}, "tds": 0}
                    elif tag == "td" and self.row is not None:
                        mark = "td"
                        self.cell = (cls, [])
                        self.row["tds"] += 1
                    elif tag == "pre" and self.cell is not None:
                        mark = "pre"
                        self.pre = []
                    elif tag == "a":
                        mark = "a"
                        self.link = (a, [])
                if tag not in void:
                    self.stack.append((tag, mark))

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                if tag not in void:
                    self.handle_endtag(tag)

            def handle_endtag(self, tag):
                if tag in void or tag not in [t for t, _ in self.stack]:
                    return
                while self.stack:
                    t, mark = self.stack.pop()
                    if mark == "pre" and self.cell is not None:
                        for c in self.cell[0] or [""]:
                            self.row["pres"].setdefault(c, []).append("".join(self.pre))
                        self.pre = None
                    elif mark == "td" and self.row is not None:
                        for c in self.cell[0] or [""]:
                            self.row["cells"].setdefault(c, "".join(self.cell[1]))
                        self.cell = None
                    elif mark == "tr":
                        if self.row["tds"]:
                            self.out["rows"].append(self.row)
                        self.row = None
                    elif mark == "a":
                        self.out["links"].append((self.link[0], "".join(self.link[1])))
                        self.link = None
                    if t == tag:
                        break

            def handle_data(self, data):
                if self.cell is not None:
                    self.cell[1].append(data)
                if self.pre is not None:
                    self.pre.append(data)
                if self.link is not None:
                    self.link[1].append(data)

        p = P()
        p.feed(page)
        p.close()
        return p.out

    def rows(self, page=None):
        out = self.parse(self.page() if page is None else page)
        self.assertEqual(out["tabs"], 1, "exactly one id=tab-live element")
        return out["rows"]

    def by_pid(self, rows, pid):
        return [r for r in rows if r["attrs"].get("data-pid") == str(pid)]

    def one(self, rows, pid):
        hit = self.by_pid(rows, pid)
        self.assertEqual(len(hit), 1, "exactly one row with data-pid %s; rows: %r" % (pid, [r["attrs"] for r in rows]))
        return hit[0]

    def cell(self, row, name):
        self.assertIn(name, row["cells"], "row %r has no td.%s" % (row["attrs"], name))
        return row["cells"][name].strip()

    def pre_lines(self, row, name="detail"):
        pres = row["pres"].get(name) or []
        self.assertEqual(len(pres), 1, "one <pre> in td.%s of %r" % (name, row["attrs"]))
        text = pres[0].strip("\n")
        return text.split("\n") if text else []

    def assert_tail(self, row, body, page, escaped=None):
        self.assertEqual(self.pre_lines(row), body[-20:], "last 20 log lines in td.detail's <pre>")
        self.assertNotIn(body[-21], self.cell(row, "detail"), "line %d is not in the tail" % (len(body) - 20))
        if escaped:
            self.assertIn(html.escape(escaped), page)
            self.assertNotIn(escaped, page)

    # ------------------------------------------------------------ item 1: TABS and nav

    def test_item1_tabs_and_nav(self):
        import board
        self.assertTrue(hasattr(board, "TABS"), "board.TABS is missing")
        self.assertEqual([t[0] for t in board.TABS], ["decide", "live", "tickets", "dag", "tokens", "host"])
        self.assertEqual(board.TABS[1][1], "進行中")
        self.assertTrue(callable(board.TABS[1][2]))
        self.add(1, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        self.assertEqual(len(board.waiting_cards(self.snap(), self.NOW)), 1, "the fixture makes one card")
        hrefs = ["?tab=decide", "?tab=live", "?tab=tickets", "?tab=dag", "?tab=tokens", "?tab=host"]
        for query in ({}, {"tab": "live"}, {"tab": "tickets"}, {"tab": "decide"}):
            page = self.page(query)
            m = re.search(r'<nav class="tabs">(.*?)</nav>', page, re.S)
            self.assertIsNotNone(m, "no <nav class=\"tabs\">")
            links = re.findall(r'<a href="([^"]*)"[^>]*>(.*?)</a>', m.group(1), re.S)
            self.assertEqual([h for h, _ in links], hrefs, "nav hrefs for %r" % (query,))
            self.assertEqual([t for _, t in links], ["要你判 (1)", "進行中", "票", "DAG", "Token", "主機"],
                             "nav texts for %r" % (query,))

    # ------------------------------------------------------------ item 2: the default tab

    def shown(self, page):
        return re.findall(r'id="tab-([a-z]+)"', page)

    def test_item2_live_is_the_default_when_nothing_waits(self):
        board = self.need()
        for query in (None, {}, {"tab": ""}, {"tab": "nope"}):
            page = board.render(self.cfg(), self.snap(), query=query, now=self.NOW)
            self.assertEqual(self.shown(page), ["live"], "query %r shows 進行中" % (query,))
            self.assertIn('<div id="tab-live" class="tab">', page)
        self.assertEqual(board.decide_default(self.snap(), {}, self.NOW), "live")
        self.assertEqual(board.decide_default(self.snap(), None, self.NOW), "live")
        for name, value in (("ticket", "1"), ("group", "g1"), ("state", "ready"), ("cat", "red"), ("q", "x"),
                            ("done", "1")):
            for query in ({name: value}, {"tab": "nope", name: value}):
                page = board.render(self.cfg(), self.snap(), query=query, now=self.NOW)
                self.assertEqual(self.shown(page), ["tickets"], "query %r shows 票" % (query,))
            self.assertEqual(board.decide_default(self.snap(), {name: value}, self.NOW), "tickets")
        self.add(1, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        self.assertTrue(board.waiting_cards(self.snap(), self.NOW))
        for query in (None, {}, {"tab": "nope"}):
            page = board.render(self.cfg(), self.snap(), query=query, now=self.NOW)
            self.assertEqual(self.shown(page), ["decide"], "query %r with a card shows 要你判" % (query,))
        self.assertEqual(board.decide_default(self.snap(), {}, self.NOW), "decide")

    def test_item2_html_without_query_writes_tab_live(self):
        self.need()
        res = self.run_py("board.py", "html")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('id="tab-live"', res.stdout)
        self.assertNotIn('id="tab-tickets"', res.stdout)

    # ------------------------------------------------------------ item 3: refresh link, relative links, served

    def every_kind(self):
        """One record of every kind (and the agents that hang under them); returns the opener's pid."""
        n = self.NOW
        self.add(1, title="Alpha title")
        log, _ = self.log_file("a")
        op = self.put("opener-x", "opener", n - 100, title="t", revise=None, via="main", model="m", effort=None)
        self.agent("agent-op", op["stamp"]["pid"], n - 90, run_dir="/r/op", log=log, role="creator")
        self.agent("agent-orphan", 1, n - 80, ticket="1", round=1, run_dir="/r/w", log=log)
        self.put("issuer-1", "issuer", n - 70, ticket="1", target="int/g1", round=1)
        self.put("regression-int-g1", "regression", n - 60, group="g1", branch="int/g1", sha="a" * 40,
                 exclusive=False, stage="full", since=self.ts(n - 50))
        fx = self.put("fixer-g1", "fixer", n - 40, group="g1", round=1, max_rounds=3)
        self.agent("agent-fx", fx["stamp"]["pid"], n - 30, role="fixer", group="g1", run_dir="/r/fx", log=log)
        self.put("release-g1", "release", n - 20, group="g1", sha="b" * 40, log=log)
        self.put("opener-dead", "opener", n - 10, alive=False, title="d", revise=None, via="main", model="m",
                 effort=None)
        return op["stamp"]["pid"]

    def test_item3_refresh_link_and_relative_urls(self):
        self.need()
        self.every_kind()
        page = self.page()
        out = self.parse(page)
        self.assertEqual(out["tabs"], 1)
        self.assertIn(self.REFRESH, page)
        refresh = [(a, t) for a, t in out["links"] if "refresh" in (a.get("class") or "").split()]
        self.assertEqual([(a.get("href"), t.strip()) for a, t in refresh], [("?tab=live", "重新整理")],
                         "the refresh link sits inside tab-live")
        self.assertTrue(out["rows"], "the fixture gives rows")
        for url in out["urls"]:
            self.assertFalse(url.startswith("/"), url)
            self.assertNotIn("://", url)

    def test_item3_served_under_a_prefix(self):
        import http.client
        import socket
        self.need()
        key = os.path.join(self.tmp, "key")
        with open(key, "w") as fh:
            fh.write("s3cret\n")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(["python3", os.path.join(LOOP, "board.py"), "serve", "--port", str(port),
                                 "--key-file", key], cwd=self.proj, env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.terminate(), proc.wait()))
        deadline = time.time() + 10
        while True:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request("GET", "/board/?tab=live", headers={"X-Board-Key": "s3cret"})
                resp = conn.getresponse()
                status, body = resp.status, resp.read().decode("utf-8")
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
            finally:
                conn.close()
        self.assertEqual(status, 200)
        self.assertIn('id="tab-live"', body)

    # ------------------------------------------------------------ item 4: row shape and order

    def test_item4_row_attributes_cells_and_order(self):
        board = self.need("live_clock", "live_elapsed")
        n = self.NOW
        self.pool.update(lambda tx: tx.put_group({"id": "g1", "branch": "int/g1", "state": "testing",
                                                  "members": [], "fix_round": 0}), "g1")
        ended = self.put("a-ended", "opener", n - 9000, alive=False, title="old", revise=None, via="main",
                         model="m", effort=None)
        r1 = self.put("m-reg", "regression", n - 5400, group="g1", branch="int/g1", sha="c" * 40,
                      exclusive=False, stage="full", since=self.ts(n - 5000))
        r2 = self.put("z-open", "opener", n - 600, title="later", revise=None, via="main", model="m", effort=None)
        r3 = self.put("b-open", "opener", n - 600, title="tie", revise=None, via="main", model="m", effort=None)
        r4 = self.put("c-orphan", "agent", n - 59, parent=1, role="worker", ticket=None, group="g1", round=None,
                      run_dir="/r", log=None, command="python3", model="m", effort=None, state="running",
                      since=self.ts(n - 59), until=None)
        rows = self.rows()
        want = [r1, r3, r2, r4, ended]
        self.assertEqual([r["attrs"].get("data-pid") for r in rows], [str(x["stamp"]["pid"]) for x in want],
                         "oldest first, ties by record name, ended last")
        starts = {str(x["stamp"]["pid"]): calendar.timegm(time.strptime(x["stamp"]["started"], self.FMT))
                  for x in want}
        for row in rows:
            a = row["attrs"]
            for attr in ("data-kind", "data-state", "data-pid", "data-started"):
                self.assertIn(attr, a, a)
            self.assertEqual(a["data-started"], str(starts[a["data-pid"]]), a)
            for name in self.CELLS:
                self.assertIn(name, row["cells"], "td.%s of %r" % (name, a))
            st = int(a["data-started"])
            self.assertEqual(self.cell(row, "start"), board.live_clock(st))
            self.assertEqual(self.cell(row, "start"), self.clock(st))
            self.assertEqual(self.cell(row, "elapsed"), board.live_elapsed(n - st))
        self.assertEqual([r["attrs"]["data-state"] for r in rows][-1], "ended")
        self.assertEqual([self.cell(r, "elapsed") for r in rows[:4]], ["1 小時 30 分", "10 分", "10 分", "0 分"])

    # ------------------------------------------------------------ item 5: clock and elapsed helpers

    def test_item5_live_clock_and_live_elapsed(self):
        board = self.need("live_clock", "live_elapsed")
        for epoch in (0, 1700000000, self.NOW, 1782000000):
            self.assertEqual(board.live_clock(epoch), time.strftime("%m-%d %H:%M:%S", time.localtime(epoch)))
        for seconds, text in ((0, "0 分"), (59, "0 分"), (61, "1 分"), (3599, "59 分"), (3600, "1 小時 0 分"),
                              (5400, "1 小時 30 分"), (-5, "0 分"), (7325, "2 小時 2 分")):
            self.assertEqual(board.live_elapsed(seconds), text, seconds)

    # ------------------------------------------------------------ item 6: handed in, ended

    def test_item6_handed_in_has_no_row_and_ended_says_so(self):
        self.need()
        n = self.NOW
        result = os.path.join(self.tmp, "result.json")
        with open(result, "w") as fh:
            fh.write("{}")
        handed = self.put("opener-h", "opener", n - 50, alive=False, title="h", revise=None, via="main",
                          model="m", effort=None, result=result)
        eo = self.put("opener-e", "opener", n - 40, alive=False, title="e", revise=None, via="main", model="m",
                      effort=None, result=os.path.join(self.tmp, "missing.json"))
        ea = self.agent("agent-e", 1, n - 30, alive=False, state="slot", run_dir="/r")
        eq = self.agent("agent-q", 1, n - 25, alive=False, state="quota", until=self.ts(n + 600), run_dir="/r")
        er = self.put("regression-e", "regression", n - 20, alive=False, group="g1", branch="int/g1",
                      sha="d" * 40, exclusive=False, stage="full", since=self.ts(n - 20))
        rows = self.rows()
        self.assertEqual(self.by_pid(rows, handed["stamp"]["pid"]), [], "a handed-in record has no row")
        self.assertEqual(len(rows), 4, [r["attrs"] for r in rows])
        for rec in (eo, ea, eq, er):
            row = self.one(rows, rec["stamp"]["pid"])
            self.assertEqual(row["attrs"].get("data-state"), "ended", row["attrs"])
            self.assertEqual(self.cell(row, "state"), "結束了,沒有交件")
        for row in rows:
            self.assertNotIn(row["attrs"].get("data-state"), ("running", "slot", "quota"))

    # ------------------------------------------------------------ item 7: opener rows

    def test_item7_opener_titles_and_own_model(self):
        self.need()
        n = self.NOW
        a = self.put("opener-a", "opener", n - 30, title="Plain title", revise=None, via="main", model="mA",
                     effort="high")
        b = self.put("opener-b", "opener", n - 20, title=None, revise="3", via="main", model="mB", effort=None)
        c = self.put("opener-c", "opener", n - 10, title=None, revise=None, via="main", model="mC", effort=None)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        for rec, what in ((a, "開題:Plain title"), (b, "改票 #3"), (c, "開題")):
            row = self.one(rows, rec["stamp"]["pid"])
            self.assertEqual(row["attrs"].get("data-kind"), "opener")
            self.assertEqual(row["attrs"].get("data-state"), "running")
            self.assertEqual(self.cell(row, "what"), what)
        model = self.cell(self.one(rows, a["stamp"]["pid"]), "model")
        self.assertIn("mA", model)
        self.assertIn("high", model)
        self.assertIn("mB", self.cell(self.one(rows, b["stamp"]["pid"]), "model"))

    def test_item7_opener_row_carries_its_newest_agent(self):
        self.need()
        n = self.NOW
        log, body = self.log_file("op", extra="<i>z</i> & co")
        op = self.put("opener-a", "opener", n - 100, title="T", revise=None, via="main", model="mO", effort=None)
        pid = op["stamp"]["pid"]
        old = self.agent("agent-old", pid, n - 90, role="creator", command="python3", model="mOld", run_dir="/r/old")
        new = self.agent("agent-new", pid, n - 50, role="creator", command="claude", model="mNew", effort="max",
                         state="slot", run_dir="/r/new-run", log=log)
        page = self.page()
        rows = self.rows(page)
        self.assertEqual(len(rows), 1, "only the opener's row: %r" % [r["attrs"] for r in rows])
        row = self.one(rows, pid)
        self.assertEqual(self.by_pid(rows, old["stamp"]["pid"]) + self.by_pid(rows, new["stamp"]["pid"]), [])
        a = row["attrs"]
        self.assertEqual((a.get("data-kind"), a.get("data-model"), a.get("data-command"), a.get("data-state")),
                         ("opener", "mNew", "claude", "slot"))
        self.assertEqual(self.cell(row, "model"), "claude · mNew · max")
        self.assertIn("/r/new-run", self.cell(row, "detail"))
        self.assert_tail(row, body, page, escaped="<i>z</i> & co")

    def test_item7_opener_agent_without_effort_running(self):
        self.need()
        n = self.NOW
        op = self.put("opener-a", "opener", n - 100, title="T", revise=None, via="main", model="mO", effort="low")
        self.agent("agent-1", op["stamp"]["pid"], n - 90, role="creator", command="python3", model="mX",
                   effort=None, state="running", run_dir="/r/x")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["attrs"].get("data-state"), "running")
        self.assertEqual(row["attrs"].get("data-model"), "mX")
        self.assertEqual(self.cell(row, "model"), "python3 · mX")

    # ------------------------------------------------------------ item 8: agent rows

    def test_item8_agent_rows(self):
        board = self.need("live_clock")
        n = self.NOW
        self.add(1, title="Alpha title")
        log, body = self.log_file("w", extra="<i>z</i> & co")
        until = n + 3000
        iss = self.put("issuer-1", "issuer", n - 200, ticket="1", target="int/g1", round=2)
        w = self.agent("agent-w", iss["stamp"]["pid"], n - 100, role="worker", ticket="1", group="g1", round=2,
                       command="claude", model="opus", effort="high", state="running", run_dir="/r/w1", log=log)
        s = self.agent("agent-s", 1, n - 90, role="fixer", group="g7", command="codex", model="gpt",
                       state="slot", run_dir="/r/s1", log=os.path.join(self.tmp, "nope.log"))
        q = self.agent("agent-q", 1, n - 80, role="verifier", ticket="1", command="claude", model="sonnet",
                       state="quota", until=self.ts(until), run_dir="/r/q1")
        page = self.page()
        # ruling g15: count only process rows (#67 item 3 adds a ticket row for the ready ticket)
        rows = [r for r in self.rows(page) if r["attrs"].get("data-kind") not in ("ticket", "decision")]
        self.assertEqual(len(rows), 3, [r["attrs"] for r in rows])
        rw = self.one(rows, w["stamp"]["pid"])
        a = rw["attrs"]
        self.assertEqual((a.get("data-kind"), a.get("data-state"), a.get("data-model"), a.get("data-command")),
                         ("agent", "running", "opus", "claude"))
        what = self.cell(rw, "what")
        for part in ("worker", "#1 Alpha title", "第 2 輪"):
            self.assertIn(part, what)
        self.assertEqual(self.cell(rw, "model"), "claude · opus · high")
        self.assertEqual(self.cell(rw, "state"), "執行中")
        self.assertIn("/r/w1", self.cell(rw, "detail"))
        self.assert_tail(rw, body, page, escaped="<i>z</i> & co")
        rs = self.one(rows, s["stamp"]["pid"])
        self.assertEqual((rs["attrs"].get("data-kind"), rs["attrs"].get("data-state")), ("agent", "slot"))
        self.assertIn("fixer", self.cell(rs, "what"))
        self.assertIn("g7", self.cell(rs, "what"))
        self.assertNotIn("輪", self.cell(rs, "what"))
        self.assertEqual(self.cell(rs, "model"), "codex · gpt")
        # ruling g15 / #67 item 1: the slot row names its queue place
        self.assertEqual(self.cell(rs, "state"), "等機器名額:第 1 個(共 1 個在等)")
        self.assertIn("/r/s1", self.cell(rs, "detail"))
        self.assertEqual(self.pre_lines(rs), [], "unreadable log: empty <pre>")
        rq = self.one(rows, q["stamp"]["pid"])
        self.assertEqual(rq["attrs"].get("data-state"), "quota")
        self.assertIn("#1 Alpha title", self.cell(rq, "what"))
        self.assertEqual(self.cell(rq, "state"), "額度用完,到 " + self.clock(until))
        self.assertEqual(self.cell(rq, "state"), "額度用完,到 " + board.live_clock(until))
        self.assertEqual(self.pre_lines(rq), [], "no log: empty <pre>")

    # ------------------------------------------------------------ item 9: issuer rows

    def test_item9_issuer_row_only_without_agent(self):
        self.need()
        n = self.NOW
        self.add(2, title="Beta title")
        lone = self.put("issuer-2", "issuer", n - 100, ticket="2", target="int/g1", round=3)
        busy = self.put("issuer-3", "issuer", n - 90, ticket="2", target="int/g1", round=1)
        ag = self.agent("agent-3", busy["stamp"]["pid"], n - 80, ticket="2", round=1, run_dir="/r")
        dead = self.agent("agent-dead", lone["stamp"]["pid"], n - 70, alive=False, ticket="2", round=3)
        rows = self.rows()
        row = self.one(rows, lone["stamp"]["pid"])
        self.assertEqual((row["attrs"].get("data-kind"), row["attrs"].get("data-state")), ("issuer", "running"))
        self.assertEqual(self.cell(row, "what"), "#2 Beta title 第 3 輪")
        self.assertEqual(self.cell(row, "state"), "沒有 agent 在跑")
        self.assertEqual(self.by_pid(rows, busy["stamp"]["pid"]), [], "an issuer with a running agent has no row")
        self.assertEqual([r["attrs"].get("data-kind") for r in rows if r["attrs"].get("data-kind") == "issuer"],
                         ["issuer"])
        self.assertEqual(self.one(rows, ag["stamp"]["pid"])["attrs"].get("data-kind"), "agent")
        self.assertEqual(self.one(rows, dead["stamp"]["pid"])["attrs"].get("data-state"), "ended")

    # ------------------------------------------------------------ item 10: regression rows

    def test_item10_regression_rows(self):
        self.need()
        n = self.NOW

        def groups(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "testing", "members": [], "fix_round": 0})
            tx.put_group({"id": "g2", "branch": "int/g2", "state": "testing", "members": []})
            tx.put_group({"id": "g3", "branch": "int/g3", "state": "testing", "members": [], "fix_round": 2})
        self.pool.update(groups, "groups")
        # ruling g15 / #67 items 1-2: g1 waits first of one in the queue and gives way to g2's exclusive run
        cases = [("g1", "slot", "第一次", "等機器名額:第 1 個(共 1 個在等)", "slot"),     # #143: no give-way suffix
                 ("g2", "machine", "第一次", "等整台機器", "slot"),
                 ("g3", "preflight", "修第 2 輪後", "preflight", "running"),
                 ("g9", "full", "未知", "full", "running")]
        recs = []
        for i, (g, stage, _, _, _) in enumerate(cases):
            recs.append(self.put("regression-int-%s" % g, "regression", n - 100 + i, group=g, branch="int/" + g,
                                 sha="e" * 40, exclusive=stage == "machine", stage=stage, since=self.ts(n - 50)))
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        for rec, (g, _, when, state, dstate) in zip(recs, cases):
            row = self.one(rows, rec["stamp"]["pid"])
            self.assertEqual((row["attrs"].get("data-kind"), row["attrs"].get("data-state")),
                             ("regression", dstate), g)
            self.assertIn(g, self.cell(row, "what"))
            self.assertIn(when, self.cell(row, "what"), g)
            self.assertEqual(self.cell(row, "state"), state, g)

    # ------------------------------------------------------------ item 11: fixer rows

    def test_item11_fixer_rows(self):
        board = self.need("live_clock")
        n = self.NOW
        until = n + 1800
        fixers, agents = [], []
        for i, (g, state) in enumerate((("g1", "running"), ("g2", "slot"), ("g3", "quota"), ("g4", None))):
            fx = self.put("fixer-%s" % g, "fixer", n - 100 + i, group=g, round=2, max_rounds=3)
            fixers.append(fx)
            if state:
                agents.append(self.agent("agent-%s" % g, fx["stamp"]["pid"], n - 50 + i, role="fixer", group=g,
                                         round=2, command="claude", model="m%s" % g, state=state,
                                         until=self.ts(until) if state == "quota" else None,
                                         run_dir="/r/fix-%s" % g))
        rows = self.rows()
        self.assertEqual(len(rows), 4, [r["attrs"] for r in rows])
        for ag in agents:
            self.assertEqual(self.by_pid(rows, ag["stamp"]["pid"]), [], "the fixer's agent has no row of its own")
        # ruling g15 / #67 item 1: the slot row names its queue place
        texts = {"running": "agent 執行中", "slot": "等機器名額:第 1 個(共 1 個在等)",
                 "quota": "額度用完,到 " + self.clock(until)}
        for fx, (g, state) in zip(fixers, (("g1", "running"), ("g2", "slot"), ("g3", "quota"), ("g4", None))):
            row = self.one(rows, fx["stamp"]["pid"])
            a = row["attrs"]
            self.assertEqual(a.get("data-kind"), "fixer")
            self.assertIn(g, self.cell(row, "what"))
            self.assertIn("第 2/3 輪", self.cell(row, "what"))
            if state:
                self.assertEqual(self.cell(row, "state"), texts[state], g)
                self.assertEqual(a.get("data-state"), state, g)
                self.assertEqual(a.get("data-model"), "m%s" % g)
                self.assertEqual(self.cell(row, "model"), "claude · m%s" % g)
                self.assertIn("/r/fix-%s" % g, self.cell(row, "detail"))
            else:
                self.assertEqual(self.cell(row, "state"), "沒有 agent 在跑")
                self.assertEqual(a.get("data-state"), "running")
        self.assertEqual(texts["quota"], "額度用完,到 " + board.live_clock(until))

    # ------------------------------------------------------------ item 12: release rows

    def test_item12_release_rows(self):
        self.need()
        n = self.NOW
        path = os.path.join(self.tmp, "rel.log")
        body = ["devloop-stage: build", "x", "devloop-stage:   upload docs  ", "y <b>&</b>"] + \
               ["R%02d" % i for i in range(5, 31)]
        with open(path, "w") as fh:
            fh.write("\n".join(body) + "\n")
        plain, plain_body = self.log_file("plain")
        sha = "0123456789abcdef0123456789abcdef01234567"
        r1 = self.put("release-g1", "release", n - 60, group="g1", sha=sha, log=path)
        r2 = self.put("release-g2", "release", n - 50, group="g2", sha="f" * 40, log=plain)
        page = self.page()
        rows = self.rows(page)
        self.assertEqual(len(rows), 2)
        row = self.one(rows, r1["stamp"]["pid"])
        self.assertEqual(row["attrs"].get("data-kind"), "release")
        self.assertIn("g1", self.cell(row, "what"))
        self.assertIn(sha[:12], self.cell(row, "what"))
        self.assertNotIn(sha[:13], self.cell(row, "what"))
        self.assertEqual(self.cell(row, "state"), "upload docs")
        self.assertEqual(self.pre_lines(row), body[-20:])
        other = self.one(rows, r2["stamp"]["pid"])
        self.assertEqual(self.cell(other, "state"), "執行中")
        self.assert_tail(other, plain_body, page)

    # ------------------------------------------------------------ item 13: machine slots

    def test_item13_slots_on_every_row(self):
        from core import slots
        self.need()
        n = self.NOW
        self.put("opener-a", "opener", n - 30, title="T", revise=None, via="main", model="m", effort=None)
        self.put("regression-int-g1", "regression", n - 20, group="g1", branch="int/g1", sha="a" * 40,
                 exclusive=False, stage="full", since=self.ts(n - 20))
        self.put("opener-d", "opener", n - 10, alive=False, title="d", revise=None, via="main", model="m",
                 effort=None)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertNotIn("data-slots", row["attrs"])
            self.assertNotIn("slots", row["cells"])
        self.config(machine_slots=2)
        slot = slots.take(self.cfg(), None)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["attrs"].get("data-slots"), "1/2", row["attrs"])
            self.assertEqual(self.cell(row, "slots"), "名額 1/2")

    # ------------------------------------------------------------ item 14: escaping

    def test_item14_record_text_is_escaped(self):
        self.need()
        self.put("opener-a", "opener", self.NOW - 30, title="<b>x</b>", revise=None, via="main", model="m",
                 effort=None)
        page = self.page()
        rows = self.rows(page)
        self.assertEqual(len(rows), 1)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<b>x</b>", page)

    # ------------------------------------------------------------ item 15: render creates nothing

    def test_item15_render_creates_nothing(self):
        from core import inflight, slots
        board = self.need()
        self.config(machine_slots=2)
        cfg = self.cfg()
        live, slot_dir = inflight.folder(cfg), slots.slot_dir(cfg)
        self.assertFalse(os.path.exists(live))
        self.assertFalse(os.path.exists(slot_dir))
        page = board.render(cfg, self.snap(), query={"tab": "live"}, now=self.NOW)
        self.assertIn('id="tab-live"', page)
        self.assertFalse(os.path.exists(live), "render created %s" % live)
        self.assertFalse(os.path.exists(slot_dir), "render created %s" % slot_dir)

    # ------------------------------------------------------------ item 16: three real processes

    def test_item16_opener_issuer_and_regression_at_once(self):
        self.need()
        self.add(1, needs_verifier=False, complexity="simple", state="running", group="g1", target="int/g1",
                 claimed_at=osx.now())
        path = os.path.join(self.proj, "board", "config.json")
        with open(path) as fh:
            conf = json.load(fh)
        conf["tests"]["full"] = "sh -c 'sleep 20; %s full'" % TESTS
        with open(path, "w") as fh:
            json.dump(conf, fh)
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "full held")
        git(self.proj, "branch", "int/g1", "main")
        git(self.proj, "branch", "int/g2", "main")
        t_op = time.time()
        op = self.popen("ticket_creator.py", "--request", "# live tab case\nbody", hold=20)
        t_is = time.time()
        iss = self.popen("ticket_issuer.py", "--id", "1", "--target", "int/g1", hold=20)
        t_rg = time.time()
        rg = self.popen("regression.py", "--branch", "int/g2", "--out", os.path.join(self.tmp, "r.json"), hold=0)
        procs = (op, iss, rg)
        deadline = time.time() + 30
        agent_seen = None
        kinds = set()
        while time.time() < deadline:
            rows = self.rows(self.page(now=None))
            kinds = {r["attrs"].get("data-kind") for r in rows}
            if "agent" in kinds and agent_seen is None:
                agent_seen = time.time()
            if {"opener", "agent", "regression"} <= kinds:
                break
            time.sleep(0.5)
        for _, rec in self.live():
            if rec.get("kind") == "agent" and rec.get("parent") in (op.pid, iss.pid):
                self.kill_agent_later(rec)
        self.assertTrue({"opener", "agent", "regression"} <= kinds, "rows seen: %r" % kinds)
        time.sleep(max(0.0, agent_seen + 6 - time.time()))
        rows = self.rows(self.page(now=None))
        self.assertEqual(sorted(r["attrs"].get("data-kind") for r in rows), ["agent", "opener", "regression"],
                         [r["attrs"] for r in rows])
        for row in rows:
            self.assertEqual(row["attrs"].get("data-state"), "running", row["attrs"])
        by_kind = {r["attrs"]["data-kind"]: r for r in rows}
        self.assertEqual(by_kind["opener"]["attrs"].get("data-pid"), str(op.pid))
        self.assertEqual(by_kind["regression"]["attrs"].get("data-pid"), str(rg.pid))
        for kind, t0 in (("opener", t_op), ("regression", t_rg)):
            started = int(by_kind[kind]["attrs"]["data-started"])
            self.assertGreaterEqual(started, t0 - 1, kind)
            self.assertLessEqual(started, t0 + 5, kind)
        started = int(by_kind["agent"]["attrs"]["data-started"])
        self.assertGreaterEqual(started, t_is - 1, "agent")
        self.assertLessEqual(started, agent_seen + 2, "agent")
        self.assertIn("#1", self.cell(by_kind["agent"], "what"))
        end = time.time() + 90
        for p in procs:
            p.wait(max(1, end - time.time()))
        rows = self.rows(self.page(now=None))
        self.assertEqual([r["attrs"] for r in rows if r["attrs"].get("data-state") == "running"], [])

    # ------------------------------------------------------------ item 17: a killed opener

    def test_item17_killed_opener_shows_as_ended(self):
        self.need()
        proc = self.popen("ticket_creator.py", "--request", "# killed case\nbody", hold=30)
        deadline = time.time() + 30
        seen = []
        while time.time() < deadline and not seen:
            seen = [r for r in self.by_pid(self.rows(self.page(now=None)), proc.pid)
                    if r["attrs"].get("data-kind") == "opener"]
            if not seen:
                time.sleep(0.5)
        self.assertTrue(seen, "no opener row for pid %d" % proc.pid)
        agent = self.wait_for(lambda r: r.get("kind") == "agent" and r.get("parent") == proc.pid, proc=proc)
        self.assertIsNotNone(agent, "no creator agent record; folder: %s" % self.live())
        self.kill_agent_later(agent[1])
        proc.kill()
        proc.wait(30)
        rows = self.rows(self.page(now=None))
        openers = [r for r in rows if r["attrs"].get("data-kind") == "opener"]
        self.assertEqual(len(openers), 1, [r["attrs"] for r in rows])
        self.assertEqual(openers[0]["attrs"].get("data-pid"), str(proc.pid))
        self.assertEqual(openers[0]["attrs"].get("data-state"), "ended")
        self.assertEqual(self.cell(openers[0], "state"), "結束了,沒有交件")
        self.assertEqual([r for r in self.by_pid(rows, proc.pid) if r["attrs"].get("data-state") == "running"], [])


class T67Acceptance(LoopCase):
    """Acceptance of #67: on the 進行中 tab every slot wait shows its queue position (and whom it gives
    way to), and every ready ticket and every decision waiting for the user has a row. Expected texts
    are the ticket's own."""

    FMT = T66Acceptance.FMT
    VOID = T66Acceptance.VOID

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if key.startswith("DEVLOOP_"):
                del os.environ[key]
        for key in list(self.env):
            if key.startswith("DEVLOOP_"):
                del self.env[key]
        os.environ["AC_ROOT"] = self.proj
        self.NOW = int(time.time())

    cfg = T64Acceptance.cfg
    ts = T66Acceptance.ts
    stamp = T66Acceptance.stamp
    put = T66Acceptance.put
    agent = T66Acceptance.agent
    page = T66Acceptance.page
    parse = T66Acceptance.parse
    rows = T66Acceptance.rows
    by_pid = T66Acceptance.by_pid
    one = T66Acceptance.one
    cell = T66Acceptance.cell

    # ------------------------------------------------------------ helpers

    def need(self):
        import board
        self.assertTrue(hasattr(board, "TABS"), "board.TABS is missing")
        self.assertIn("live", [t[0] for t in board.TABS], "board.TABS has no 'live' entry")
        return board

    def queue_text(self, k, n):
        return "等機器名額:第 %d 個(共 %d 個在等)" % (k, n)

    def queued(self, rows):
        return [r for r in rows if "data-queue" in r["attrs"]]

    def kinds(self, rows):
        return [r["attrs"].get("data-kind") for r in rows]

    def set_group(self, gid, state):
        def change(tx):
            tx.put_group({"id": gid, "branch": "int/" + gid, "state": state, "members": []})
        self.pool.update(change, "test: group %s" % gid)

    def reput(self, name, rec):
        from core import inflight
        inflight.put(self.cfg(), name, rec)

    def slot_fixture(self):
        """Item 1's fixture: three standalone slot agents and a regression in stage 'machine'. The
        stamps' start order differs from the 'since' order, so ordering by start gives other ranks."""
        n = self.NOW
        base = n - 600
        recs = {}
        # (name, since, stamp started): started order is a, b, c; since order is a/c (tie), b
        for name, since, started in (("agent-b", base + 5, n - 50), ("agent-c", base, n - 40),
                                     ("agent-a", base, n - 60)):
            recs[name] = self.agent(name, 999999, started, state="slot", since=self.ts(since))
        recs["regression-int-g2"] = self.put("regression-int-g2", "regression", n - 70, group="g2",
                                             branch="int/g2", sha="b" * 40, exclusive=False, stage="machine",
                                             since=self.ts(base - 100))
        # neither a quota agent nor an ended slot agent is in the queue
        recs["agent-q"] = self.agent("agent-q", 999999, n - 30, state="quota", since=self.ts(base - 50),
                                     until=self.ts(n + 600))
        recs["agent-z"] = self.agent("agent-z", 999999, n - 20, alive=False, state="slot",
                                     since=self.ts(base - 50))
        return recs

    # ------------------------------------------------------------ item 1: queue position

    def test_item1_slot_rows_are_numbered_by_since(self):
        self.need()
        recs = self.slot_fixture()
        rows = self.rows()
        self.assertTrue(self.queued(rows), "no row carries data-queue: %r" % [r["attrs"] for r in rows])
        want = {"agent-a": (1, 3), "agent-c": (2, 3), "agent-b": (3, 3)}
        for name, (k, total) in want.items():
            row = self.one(rows, recs[name]["stamp"]["pid"])
            self.assertEqual(row["attrs"].get("data-state"), "slot", name)
            self.assertEqual(row["attrs"].get("data-queue"), "%d/%d" % (k, total), name)
            self.assertEqual(self.cell(row, "state"), self.queue_text(k, total), name)
        machine = self.one(rows, recs["regression-int-g2"]["stamp"]["pid"])
        self.assertEqual(machine["attrs"].get("data-state"), "slot")
        self.assertNotIn("data-queue", machine["attrs"], "a regression in stage 'machine' is not queued")
        for name in ("agent-q", "agent-z"):
            self.assertNotIn("data-queue", self.one(rows, recs[name]["stamp"]["pid"])["attrs"], name)
        slot_rows = [r for r in rows if r["attrs"].get("data-state") == "slot"]
        self.assertEqual(len(self.queued(rows)), len(slot_rows) - 1, "every slot row but the machine one")

    def test_item1_waiting_record_since_for_every_kind(self):
        """Opener and fixer rows rank by their agent's since, a regression row by its own since; the
        stamps start in the opposite order."""
        self.need()
        n = self.NOW
        base = n - 900
        op = self.put("opener-a", "opener", n - 100, title="T", revise=None, via="main", model="m", effort=None)
        self.agent("agent-op", op["stamp"]["pid"], n - 95, role="creator", state="slot", since=self.ts(base + 30))
        fx = self.put("fixer-g3", "fixer", n - 90, group="g3", round=1, max_rounds=3)
        self.agent("agent-fx", fx["stamp"]["pid"], n - 85, role="fixer", group="g3", round=1, state="slot",
                   since=self.ts(base + 20))
        rg = self.put("regression-int-g4", "regression", n - 80, group="g4", branch="int/g4", sha="c" * 40,
                      exclusive=False, stage="slot", since=self.ts(base + 10))
        ag = self.agent("agent-solo", 999999, n - 75, state="slot", since=self.ts(base))
        rows = self.rows()
        self.assertTrue(self.queued(rows), "no row carries data-queue: %r" % [r["attrs"] for r in rows])
        expect = [(ag, 1), (rg, 2), (fx, 3), (op, 4)]
        for rec, k in expect:
            row = self.one(rows, rec["stamp"]["pid"])
            self.assertEqual(row["attrs"].get("data-queue"), "%d/4" % k, row["attrs"])
            self.assertEqual(self.cell(row, "state"), self.queue_text(k, 4), row["attrs"])
        self.assertEqual(len(self.queued(rows)), 4)

    # ------------------------------------------------------------ item 2: giving way

    def test_item2_queued_rows_do_not_yield_to_running_exclusive_regression(self):
        """Reversed by #143: agents no longer wait for an exclusive regression, so no row gives way."""
        self.need()
        recs = self.slot_fixture()
        n = self.NOW
        name = "regression-int-g7"
        excl = self.put(name, "regression", n - 10, group="g7", branch="int/g7", sha="d" * 40, exclusive=True,
                        stage="full", since=self.ts(n - 10))
        rows = self.rows()
        queued = self.queued(rows)
        self.assertEqual(len(queued), 3, [r["attrs"] for r in rows])
        self.assertEqual([r["attrs"] for r in rows if "data-yield" in r["attrs"]], [])
        for row in queued:
            k, total = (int(x) for x in row["attrs"]["data-queue"].split("/"))
            self.assertNotIn("讓位", self.cell(row, "state"), row["cells"])
            self.assertEqual(self.cell(row, "state"), self.queue_text(k, total))
        self.assertEqual(self.one(rows, recs["agent-a"]["stamp"]["pid"])["attrs"].get("data-queue"), "1/3")
        # the same record, no longer exclusive: nobody gives way
        excl["exclusive"] = False
        self.reput(name, excl)
        rows = self.rows()
        self.assertEqual(len(self.queued(rows)), 3)
        self.assertEqual([r["attrs"] for r in rows if "data-yield" in r["attrs"]], [])
        for row in self.queued(rows):
            k, total = (int(x) for x in row["attrs"]["data-queue"].split("/"))
            self.assertEqual(self.cell(row, "state"), self.queue_text(k, total))

    # ------------------------------------------------------------ item 3: ticket rows

    def ticket_fixture(self):
        self.set_group("g1", "open")
        self.set_group("g2", "pr")
        self.add(1, state="landed", group="g1")
        self.add(2, state="merged", group="g1")
        self.add(3, state="merged", group="g2")
        self.add(4, deps=[1, 2])
        self.add(5, deps=[3, 9])
        self.add(6, deps=[4])
        self.add(10, deps=[1])
        self.add(11, state="running", group="g1", deps=[1])

    def ticket_rows(self, rows):
        return [r for r in rows if r["attrs"].get("data-kind") == "ticket"]

    def test_item3_ready_ticket_rows(self):
        board = self.need()
        n = self.NOW
        self.ticket_fixture()
        self.put("opener-a", "opener", n - 30, title="T", revise=None, via="main", model="m", effort=None)
        self.put("opener-d", "opener", n - 20, alive=False, title="d", revise=None, via="main", model="m",
                 effort=None)
        rows = self.rows()
        tickets = self.ticket_rows(rows)
        self.assertTrue(tickets, "no row with data-kind=ticket: %r" % [r["attrs"] for r in rows])
        self.assertEqual([r["attrs"].get("data-ticket") for r in tickets], ["4", "5", "6", "10"],
                         "one row per ready ticket, numeric order")
        merged = board.STATE_TEXT["merged"]
        ready = board.STATE_TEXT["ready"]
        want = {"4": ("ready", "待派"),
                "5": ("deps", "等 #3(%s)、#9(不存在) 落地" % merged),
                "6": ("deps", "等 #4(%s) 落地" % ready),
                "10": ("ready", "待派")}
        for row in tickets:
            ident = row["attrs"]["data-ticket"]
            self.assertEqual(row["attrs"].get("data-state"), want[ident][0], ident)
            self.assertEqual(self.cell(row, "state"), want[ident][1], ident)
            self.assertEqual(self.cell(row, "what"), "#%s ticket %s" % (ident, ident))
        self.assertEqual(self.kinds(rows), ["opener", "ticket", "ticket", "ticket", "ticket", "opener"],
                         [r["attrs"] for r in rows])
        self.assertEqual(rows[0]["attrs"].get("data-state"), "running")
        self.assertEqual(rows[-1]["attrs"].get("data-state"), "ended")

    # ------------------------------------------------------------ item 4: decision rows

    def decision_fixture(self):
        self.set_group("g1", "open")
        self.add(1, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        self.add(2)
        self.pool.report("decision", "g1", "pick one")

    def tr_html(self, page, kind):
        return re.findall(r'<tr\b[^>]*data-kind="%s"[^>]*>.*?</tr>' % kind, page, re.S)

    def test_item4_decision_rows(self):
        board = self.need()
        n = self.NOW
        self.decision_fixture()
        self.put("opener-a", "opener", n - 30, title="T", revise=None, via="main", model="m", effort=None)
        self.put("opener-d", "opener", n - 20, alive=False, title="d", revise=None, via="main", model="m",
                 effort=None)
        cards = board.waiting_cards(self.snap(), n)
        self.assertEqual(len(cards), 2, "the fixture makes two cards: %r" % cards)
        page = self.page()
        rows = self.rows(page)
        decisions = [r for r in rows if r["attrs"].get("data-kind") == "decision"]
        self.assertTrue(decisions, "no row with data-kind=decision: %r" % [r["attrs"] for r in rows])
        self.assertEqual([r["attrs"].get("data-subject") for r in decisions], [str(c["subject"]) for c in cards])
        for row, card in zip(decisions, cards):
            self.assertEqual(row["attrs"].get("data-state"), "decision")
            self.assertEqual(self.cell(row, "state"), card["want"])
        html_rows = self.tr_html(page, "decision")
        self.assertEqual(len(html_rows), len(cards))
        for tr in html_rows:
            self.assertIn('<a href="?tab=decide"', tr)
        self.assertEqual(self.kinds(rows), ["opener", "ticket", "decision", "decision", "opener"],
                         [r["attrs"] for r in rows])
        self.assertEqual(rows[-1]["attrs"].get("data-state"), "ended")

    # ------------------------------------------------------------ item 5: no process cells, slots

    def test_item5_ticket_and_decision_rows_have_no_process_cells(self):
        from core import slots
        self.need()
        n = self.NOW
        self.decision_fixture()
        self.put("opener-a", "opener", n - 30, title="T", revise=None, via="main", model="m", effort=None)
        self.config(machine_slots=2)
        slot = slots.take(self.cfg(), None)
        self.assertTrue(slot and slot.held)
        self.addCleanup(slot.release)
        rows = self.rows()
        extra = [r for r in rows if r["attrs"].get("data-kind") in ("ticket", "decision")]
        self.assertEqual(sorted(self.kinds(extra)), ["decision", "decision", "ticket"],
                         [r["attrs"] for r in rows])
        opener = [r for r in rows if r["attrs"].get("data-kind") == "opener"]
        self.assertEqual(len(opener), 1)
        slots_attr = opener[0]["attrs"].get("data-slots")
        self.assertEqual(slots_attr, "1/2")
        for row in extra:
            a = row["attrs"]
            self.assertNotIn("data-pid", a, a)
            self.assertNotIn("data-started", a, a)
            self.assertEqual(self.cell(row, "start"), "", a)
            self.assertEqual(self.cell(row, "elapsed"), "", a)
            self.assertEqual(a.get("data-slots"), slots_attr, a)
            self.assertEqual(self.cell(row, "slots"), "名額 " + slots_attr, a)


class T68Acceptance(LoopCase):
    """Acceptance of #68: when no live record is running, the 進行中 tab says in one sentence why
    (p.why with data-why). Expected sentences are the ticket's own."""

    FMT = T66Acceptance.FMT
    VOID = T66Acceptance.VOID
    SENTENCES = {
        "none": "沒有在跑的,也沒有待做的事。",
        "controller": "controller 沒在跑:actl run(或 actl run --forever)才會派工與驗證。",
        "dispatch": "沒有在跑的;controller 下一輪就會接手。",
    }

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for key in list(os.environ):
            if key.startswith("DEVLOOP_"):
                del os.environ[key]
        for key in list(self.env):
            if key.startswith("DEVLOOP_"):
                del self.env[key]
        os.environ["AC_ROOT"] = self.proj
        self.NOW = int(time.time())

    cfg = T64Acceptance.cfg
    ts = T66Acceptance.ts
    stamp = T66Acceptance.stamp
    put = T66Acceptance.put
    page = T66Acceptance.page
    set_group = T67Acceptance.set_group

    # ------------------------------------------------------------ helpers

    def decision_text(self, k):
        return "沒有在跑的;剩下的都在等你判(要你判 %d 件)。" % k

    def deps_text(self, n, m):
        return "沒有可派的票;下一張 #%s 在等 #%s 落地。" % (n, m)

    def lock_path(self):
        return os.path.join(self.cfg().worktree_root, "controller.lock")

    def write_lock(self, kind):
        """'live': this test process's own stamp; 'dead': the same with another boot; 'broken': not
        JSON; 'list': JSON that is not an object; 'dir': a folder where the file should be."""
        path = self.lock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isdir(path):
            os.rmdir(path)
        elif os.path.exists(path):
            os.unlink(path)
        if kind is None:
            return
        if kind == "dir":
            os.makedirs(path)
            return
        with open(path, "w") as fh:
            if kind == "live":
                json.dump(osx.stamp(), fh)
            elif kind == "dead":
                st = osx.stamp()
                st["boot"] = "other-boot"
                json.dump(st, fh)
            elif kind == "broken":
                fh.write("not json")
            elif kind == "list":
                json.dump([osx.stamp()], fh)
        if kind == "live":
            self.assertFalse(osx.stamp_dead(osx.read_json(path)), "the live lock fixture reads as alive")
        if kind == "dead":
            self.assertTrue(osx.stamp_dead(osx.read_json(path)), "the dead lock fixture reads as dead")

    def whys(self, page=None):
        """[(attrs, text)] of every <p> with class 'why' inside id=tab-live; also asserts one tab-live."""
        from html.parser import HTMLParser
        void = self.VOID

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack, self.tabs, self.out, self.cur = [], 0, [], None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                inside = "tab" in [m for _, m in self.stack]
                mark = None
                if not inside and a.get("id") == "tab-live":
                    mark = "tab"
                    self.tabs += 1
                elif inside and tag == "p" and "why" in (a.get("class") or "").split():
                    mark = "why"
                    self.cur = (a, [])
                if tag not in void:
                    self.stack.append((tag, mark))

            def handle_endtag(self, tag):
                if tag in void or tag not in [t for t, _ in self.stack]:
                    return
                while self.stack:
                    t, mark = self.stack.pop()
                    if mark == "why" and self.cur is not None:
                        self.out.append((self.cur[0], "".join(self.cur[1]).strip()))
                        self.cur = None
                    if t == tag:
                        break

            def handle_data(self, data):
                if self.cur is not None:
                    self.cur[1].append(data)

        p = P()
        p.feed(self.page() if page is None else page)
        p.close()
        self.assertEqual(p.tabs, 1, "exactly one id=tab-live element")
        return p.out

    def why(self, page=None):
        """The one p.why's (data-why, text); fails by assertion when there is none or more than one."""
        found = self.whys(page)
        self.assertEqual(len(found), 1, "exactly one p.why inside tab-live: %r" % (found,))
        attrs, text = found[0]
        self.assertIn("data-why", attrs, "p.why has no data-why: %r" % (attrs,))
        return attrs["data-why"], text

    def assert_why(self, reason, sentence, msg=None):
        got = self.why()
        self.assertEqual(got, (reason, sentence), msg)

    def running_rows(self, page):
        return [r for r in T66Acceptance.parse(self, page)["rows"] if r["attrs"].get("data-state") == "running"]

    # ------------------------------------------------------------ item 1: one p.why unless running

    def test_item1_one_why_when_nothing_runs_none_when_running(self):
        from core import inflight
        self.add(1)
        # an ended record (dead stamp) is not running: the sentence is there
        self.put("opener-d", "opener", self.NOW - 30, alive=False, title="d", revise=None, via="main",
                 model="m", effort=None)
        self.assertNotIn("running", [inflight.status(r) for _, r in inflight.records(self.cfg())])
        reason, text = self.why()
        self.assertEqual(reason, "controller")
        self.assertEqual(text, self.SENTENCES["controller"])
        # a running record (live subprocess stamp): no p.why at all
        self.put("opener-a", "opener", self.NOW - 20, title="T", revise=None, via="main", model="m",
                 effort=None)
        self.assertIn("running", [inflight.status(r) for _, r in inflight.records(self.cfg())],
                      "the fixture has a running record")
        self.assertEqual(self.whys(), [], "a running record leaves the tab without p.why")
        # an empty pool with a running record: still no p.why
        self.write_lock("live")
        self.assertEqual(self.whys(), [])

    # ------------------------------------------------------------ item 2: none

    def test_item2_none_on_empty_pool(self):
        self.assert_why("none", self.SENTENCES["none"], "empty pool, no lock")
        self.write_lock("live")
        self.assert_why("none", self.SENTENCES["none"], "empty pool, live lock")

    def test_item2_finished_work_is_not_work_left(self):
        """Landed / cancelled / merged tickets and landed groups are not work left."""
        self.set_group("g1", "landed")
        self.add(1, state="landed", group="g1")
        self.add(2, state="cancelled")
        self.assert_why("none", self.SENTENCES["none"])

    # ------------------------------------------------------------ item 3: decision

    def test_item3_decision_stuck_ticket_only(self):
        import board
        self.add(1, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        k = len(board.waiting_cards(self.snap(), self.NOW))
        self.assertEqual(k, 1, "the fixture makes one card")
        self.assert_why("decision", self.decision_text(k))

    def test_item3_decision_paused_group_is_not_work_left(self):
        import board

        def change(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "pr", "members": [],
                          "paused": {"reason": "env", "text": "disk full", "at": osx.now()}})
        self.pool.update(change, "test: paused g1")
        k = len(board.waiting_cards(self.snap(), self.NOW))
        self.assertEqual(k, 1, "the paused group makes one card")
        self.assert_why("decision", self.decision_text(k))

    def test_item3_decision_counts_every_card(self):
        import board

        def change(tx):
            tx.put_group({"id": "g1", "branch": "int/g1", "state": "pr", "members": [],
                          "paused": {"reason": "env", "text": "disk full", "at": osx.now()}})
        self.pool.update(change, "test: paused g1")
        self.add(1, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        self.add(2, state="stuck", stuck={"reason": "env", "text": "screen locked", "at": osx.now()})
        self.write_lock("live")
        k = len(board.waiting_cards(self.snap(), self.NOW))
        self.assertEqual(k, 3, "the fixture makes three cards")
        self.assert_why("decision", self.decision_text(3))

    # ------------------------------------------------------------ item 4: controller

    def test_item4_controller_lock_missing_dead_broken(self):
        self.add(1)
        for kind in (None, "dead", "broken", "list", "dir"):
            self.write_lock(kind)
            self.assert_why("controller", self.SENTENCES["controller"], "lock fixture %r" % kind)
        # the same pool with a live lock is not 'controller'
        self.write_lock("live")
        self.assertNotEqual(self.why()[0], "controller")

    def test_item4_controller_for_each_kind_of_work(self):
        """Each kind of work left, with no lock, gives 'controller' (not 'none')."""
        cases = [("running ticket", lambda: self.add(1, state="running")),
                 ("open group", lambda: self.set_group("g1", "open")),
                 ("pr group", lambda: self.set_group("g1", "pr")),
                 ("fixing group", lambda: self.set_group("g1", "fixing")),
                 ("verified group", lambda: self.set_group("g1", "verified"))]
        for label, make in cases:
            with self.subTest(label):
                make()
                self.assert_why("controller", self.SENTENCES["controller"], label)
                # undo: cancel the ticket / land the group
                if label == "running ticket":
                    self.add(1, state="cancelled")
                else:
                    self.set_group("g1", "landed")

    # ------------------------------------------------------------ item 5: deps

    def test_item5_deps_names_first_ready_in_topo_order(self):
        self.write_lock("live")
        self.set_group("g1", "pr")
        self.add(1, state="merged", group="g1")
        self.add(2, deps=[1])
        self.assert_why("deps", self.deps_text(2, 1))
        # the same with g1 open: #2's dependency is satisfied, so 'dispatch'
        self.set_group("g1", "open")
        self.assert_why("dispatch", self.SENTENCES["dispatch"], "a merged dependency in an open group")

    def test_item5_deps_topo_and_depends_on_order(self):
        """#3 needs #5; #5 needs #7 then #6 (both merged in a 'pr' group). topo order of the ready
        tickets is 5, 3, so N is 5 (not the lower number 3), and M is #5's first unsatisfied
        dependency in depends_on order: 7."""
        self.write_lock("live")
        self.set_group("g1", "pr")
        self.add(6, state="merged", group="g1")
        self.add(7, state="merged", group="g1")
        self.add(5, deps=[7, 6])
        self.add(3, deps=[5])
        snap = self.snap()
        ready = [i for i in dagmod.topo(snap.dag) if snap.tickets[i]["state"] == "ready"]
        self.assertEqual(ready, ["5", "3"], "fixture: topo order of the ready tickets")
        self.assert_why("deps", self.deps_text(5, 7))

    def test_item5_deps_landed_and_missing(self):
        """A landed dependency is satisfied; a missing one is not. One dispatchable ready ticket is
        enough to make it 'dispatch'."""
        self.write_lock("live")
        self.add(1, state="landed")
        self.add(2, deps=[1, 9])
        self.assert_why("deps", self.deps_text(2, 9))
        self.add(4, deps=[1])
        self.assert_why("dispatch", self.SENTENCES["dispatch"], "#4 can be dispatched")

    # ------------------------------------------------------------ item 6: dispatch

    def test_item6_dispatch(self):
        self.write_lock("live")
        self.set_group("g1", "pr")
        self.assert_why("dispatch", self.SENTENCES["dispatch"], "live lock, g1 in pr, nothing ready")

    # ------------------------------------------------------------ item 7: after a real creator run

    def test_item7_after_creator_run_controller(self):
        for key in list(self.env):
            self.assertFalse(key.startswith("DEVLOOP_"), key)
        res = self.run_py("ticket_creator.py", "--request", "x", timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        snap = self.snap()
        self.assertTrue(snap.tickets, "the fake creator opened tickets")
        self.assertTrue([t for t in snap.tickets.values() if t.get("state") == "ready"], "ready tickets")
        self.assertFalse(os.path.exists(self.lock_path()), "no controller.lock")
        page = self.page()
        self.assertEqual(self.running_rows(page), [], "no running row once the creator has ended")
        self.assertEqual(self.why(page), ("controller", self.SENTENCES["controller"]))


if __name__ == "__main__":
    unittest.main()
