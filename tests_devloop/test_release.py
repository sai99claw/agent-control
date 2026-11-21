"""the release: failures, rerun, timeout, kept output"""

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

    def test_release_failed_page_offers_ack_then_release(self):
        self.add(1)
        self.controller()
        row = [r for r in self.snap().inbox.values() if r["kind"] == "decision"][0]
        self.assertEqual([o["action"] for o in row["options"]], ["ack", "release"])
        self.assertEqual(pages.problems(row["ref"], row), [])
        self.assertIn(self.snap().groups["g1"]["landed_sha"][:12], row["options"][1]["consequence"])


class T8Acceptance(LoopCase):
    def test_a_release_past_release_timeout_seconds_is_stopped_and_failed(self):
        out = os.path.join(self.tmp, "release-out.txt")
        self.config(release="sleep 5; echo done >> %s" % out, release_timeout_seconds=1)
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        pages = [row for row in self.snap().inbox.values()
                 if row["kind"] == "decision" and row["ref"] == "g1"]
        self.assertTrue(pages, "a decision page about g1")
        self.assertTrue(any("timeout" in row["text"] for row in pages), pages)
        self.assertFalse(os.path.exists(out), "the release's process group was not stopped")


class T9Acceptance(LoopCase):
    """tickets.py release <group>: re-run a failed release (ticket #9)."""

    def land_failed(self):
        self.flag = os.path.join(self.tmp, "release-flag")
        self.out = os.path.join(self.tmp, "release-out.txt")
        self.config(release='if test -f %s; then { git rev-parse HEAD; '
                            'echo "$DEVLOOP_RELEASE_SHA $DEVLOOP_GROUP $DEVLOOP_TICKETS"; pwd; } >> %s; '
                            'else echo NOFLAG-MARK; exit 3; fi' % (self.flag, self.out))
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        self.assertFalse(os.path.exists(self.out))

    def out_lines(self):
        if not os.path.exists(self.out):
            return []
        with open(self.out) as fh:
            return fh.read().splitlines()

    def pages(self, kind):
        return [row for row in self.snap().inbox.values() if row["kind"] == kind and row["ref"] == "g1"]

    def worktrees(self):
        return [line for line in git(self.proj, "worktree", "list", "--porcelain").splitlines()
                if line.startswith("worktree ")]

    def test_a_failed_release_is_rerun_at_landed_sha_in_a_throwaway_worktree(self):
        self.land_failed()
        trees = self.worktrees()
        history = len(self.snap().groups["g1"].get("history") or [])
        open(self.flag, "w").close()
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        lines = self.out_lines()
        self.assertEqual(len(lines), 3, "the release ran exactly once: %r" % lines)
        head, envline, cwd = lines
        grp = self.snap().groups["g1"]
        self.assertEqual(envline, "%s g1 1" % grp["landed_sha"])
        self.assertEqual(head, grp["landed_sha"])
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.proj),
                            "the release ran in the project checkout")
        self.assertFalse(os.path.exists(cwd), "the release worktree was not removed")
        self.assertEqual(self.worktrees(), trees, "the release worktree is still registered")
        self.assertEqual(grp["release"], "ok")
        self.assertEqual(len(grp["history"]), history + 1)
        self.assertIn("re-run", grp["history"][-1]["event"])
        self.assertTrue(self.pages("landed"), "a landed page about g1")

    def test_a_rerun_that_fails_stays_failed_and_leaves_a_decision_with_rc_and_output(self):
        self.land_failed()
        decisions = len(self.pages("decision"))
        history = len(self.snap().groups["g1"].get("history") or [])
        time.sleep(1.1)  # page names are unique to the second
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        grp = self.snap().groups["g1"]
        self.assertEqual(grp["release"], "failed")
        self.assertEqual(len(grp["history"]), history + 1)
        self.assertIn("re-run", grp["history"][-1]["event"])
        pages = sorted(self.pages("decision"), key=lambda r: r["at"])
        self.assertEqual(len(pages), decisions + 1)
        text = pages[-1]["text"]
        self.assertIn("NOFLAG-MARK", text)
        self.assertRegex(text, r"\b3\b")
        self.assertEqual(self.out_lines(), [])

    def test_an_ok_release_is_refused_unless_again(self):
        self.land_failed()
        open(self.flag, "w").close()
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")
        before, inbox = self.out_lines(), len(self.snap().inbox)
        self.assertEqual(len(before), 3)
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertEqual(self.out_lines(), before, "a refused release ran")
        self.assertEqual(len(self.snap().inbox), inbox, "a refused release left a page")
        time.sleep(1.1)
        res = self.run_py("tickets.py", "release", "g1", "--again")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(len(self.out_lines()), 6, "--again ran the release once more")
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")


class ReleaseRerun(LoopCase):
    """tickets.py release: refusals, the timeout, the shared function and the docs (#9)."""
    release = "exit 7"

    def land_failed(self):
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")

    def decisions(self):
        return [r for r in self.snap().inbox.values() if r["kind"] == "decision" and r["ref"] == "g1"]

    def test_unknown_or_unlanded_group_is_refused_and_nothing_runs(self):
        out = os.path.join(self.tmp, "out")
        self.config(release="echo ran >> %s" % out)
        res = self.run_py("tickets.py", "release", "g9")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertIn("g9", res.stderr)
        self.pool.update(lambda tx: tx.put_group({"id": "g2", "state": "pr", "members": ["1"],
                                                  "branch": "int/g2"}), "test: open group")
        res = self.run_py("tickets.py", "release", "g2")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertIn("g2", res.stderr)
        self.assertFalse(os.path.exists(out))
        self.assertEqual(self.snap().inbox, {})

    def test_no_release_configured_gives_the_config_sentence(self):
        from core.config import Config
        self.land_failed()
        self.config(release=None)
        with self.assertRaises(osx.OSFailure) as ctx:
            Config(self.proj).require_release()
        pages = len(self.snap().inbox)
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn(str(ctx.exception), res.stderr)
        self.assertEqual(len(self.snap().inbox), pages)

    def test_the_rerun_is_limited_by_release_timeout_seconds(self):
        self.land_failed()
        out = os.path.join(self.tmp, "out")
        self.config(release="sleep 5; echo done >> %s" % out, release_timeout_seconds=1)
        before = len(self.decisions())
        res = self.run_py("tickets.py", "release", "g1", timeout=60)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        new = self.decisions()
        self.assertEqual(len(new), before + 1)
        self.assertTrue(any("timeout" in r["text"] and "rc " in r["text"] for r in new), new)
        self.assertIn("release re-run: failed", json.dumps(self.snap().groups["g1"]))
        self.assertFalse(os.path.exists(out), "the release ran past its limit")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "wt", "release-g1")), "worktree left behind")

    def test_actl_tickets_release_runs_it_too(self):
        self.land_failed()
        self.config(release="true")
        res = subprocess.run([os.path.join(HOME, "bin", "actl"), "tickets", "release", "g1"], cwd=self.proj,
                             env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")

    def test_controller_release_is_the_shared_function(self):
        import ticket_controller
        from core import release
        from core.config import Config
        calls = []
        orig = release.run
        release.run = lambda cfg, g, sha: calls.append((g["id"], sha)) or (True, "x")
        try:
            ctl = ticket_controller.Controller(Config(self.proj), self.pool)
            self.assertEqual(ctl.release({"id": "g5", "members": []}, "abc"), (True, "x"))
        finally:
            release.run = orig
        self.assertEqual(calls, [("g5", "abc")])

    def test_help_docstring_and_readme_list_the_command(self):
        def read(*parts):
            with open(os.path.join(HOME, *parts)) as fh:
                return fh.read()
        actl = [ln for ln in read("bin", "actl").splitlines() if ln.startswith("  tickets ")]
        self.assertEqual(len(actl), 1)
        self.assertIn("|release", actl[0])
        doc = read("devloop", "tickets.py").split('"""')[1]
        self.assertIn("tickets.py release", doc)
        readme = read("devloop", "README.md").splitlines()
        cancel = [i for i, ln in enumerate(readme) if ln.startswith("| `python3 devloop/tickets.py cancel")][0]
        self.assertIn("tickets.py release", readme[cancel + 1])


class T21Acceptance(LoopCase):
    """Nothing a release produces is thrown away: the log and DEVLOOP_RELEASE_DIR (ticket #21)."""

    def wt(self, *parts):
        return os.path.join(self.tmp, "wt", *parts)

    def log_path(self):
        return self.wt("runs", "release", "g1.log")

    def log_text(self):
        path = self.log_path()
        self.assertTrue(os.path.isfile(path), "no release log at %s" % path)
        with open(path) as fh:
            return fh.read()

    def pages(self, kind):
        return sorted([r for r in self.snap().inbox.values() if r["kind"] == kind and r["ref"] == "g1"],
                      key=lambda r: r["at"])

    def assert_release_log_field(self):
        grp = self.snap().groups["g1"]
        self.assertIn("release_log", grp, "group g1 has no 'release_log'")
        value = grp["release_log"]
        self.assertTrue(os.path.isabs(value), value)
        self.assertEqual(os.path.realpath(value), os.path.realpath(self.log_path()))
        return value

    def land(self, release, **extra):
        self.config(release=release, **extra)
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")

    def test_ok_release_keeps_stdout_and_stderr_in_the_log_named_on_group_and_page(self):
        self.land("echo OUT-MARK; echo ERR-MARK >&2; exit 0")
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")
        text = self.log_text()
        self.assertIn("OUT-MARK", text)
        self.assertIn("ERR-MARK", text)
        value = self.assert_release_log_field()
        pages = self.pages("landed")
        self.assertTrue(pages, "a landed page about g1")
        self.assertTrue(any(value in r["text"] for r in pages), pages)

    def test_the_whole_output_is_kept_not_only_the_tail(self):
        self.land("echo FIRST-MARK; python3 -c \"print('x' * 3500)\"")
        self.assertIn("FIRST-MARK", self.log_text())

    def test_a_failed_release_keeps_its_log_and_the_decision_page_names_it(self):
        self.land("echo FAIL-MARK; exit 3")
        grp = self.snap().groups["g1"]
        self.assertEqual(grp["release"], "failed")
        value = self.assert_release_log_field()
        self.assertIn("FAIL-MARK", self.log_text())
        pages = self.pages("decision")
        self.assertTrue(pages, "a decision page about g1")
        self.assertTrue(any(value in r["text"] for r in pages), pages)

    def test_a_timed_out_release_keeps_what_it_printed(self):
        self.land("echo EARLY-MARK; sleep 5", release_timeout_seconds=1)
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        self.assertIn("EARLY-MARK", self.log_text())

    def test_release_dir_is_set_exists_and_outlives_the_throwaway_worktree(self):
        seen = os.path.join(self.tmp, "release-dir-seen.txt")
        self.land('echo "$DEVLOOP_RELEASE_DIR" > %s; '
                  'test -d "$DEVLOOP_RELEASE_DIR" && echo kept > "$DEVLOOP_RELEASE_DIR/manifest.json"' % seen)
        with open(seen) as fh:
            value = fh.read().strip()
        self.assertTrue(value and os.path.isabs(value), "DEVLOOP_RELEASE_DIR not absolute: %r" % value)
        self.assertEqual(os.path.realpath(value), os.path.realpath(self.wt("runs", "release", "g1")))
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")
        manifest = self.wt("runs", "release", "g1", "manifest.json")
        self.assertTrue(os.path.isfile(manifest), "manifest.json not kept")
        with open(manifest) as fh:
            self.assertEqual(fh.read().strip(), "kept")
        self.assertFalse(os.path.exists(self.wt("release-g1")), "the throwaway worktree is still there")

    def test_rerun_replaces_the_log_names_it_and_keeps_the_release_dir(self):
        flag = os.path.join(self.tmp, "release-flag")
        self.land('if test -f %s; then echo RERUN-MARK; else echo FIRSTRUN-MARK; '
                  'test -n "$DEVLOOP_RELEASE_DIR" && echo left > "$DEVLOOP_RELEASE_DIR/left.txt"; exit 3; fi'
                  % flag)
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")
        self.assertIn("FIRSTRUN-MARK", self.log_text())
        left = self.wt("runs", "release", "g1", "left.txt")
        self.assertTrue(os.path.isfile(left), "the first run's file was not kept")
        open(flag, "w").close()
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.snap().groups["g1"]["release"], "ok")
        text = self.log_text()
        self.assertIn("RERUN-MARK", text)
        self.assertNotIn("FIRSTRUN-MARK", text, "the re-run did not replace the log")
        value = self.assert_release_log_field()
        pages = self.pages("landed")
        self.assertTrue(pages, "a landed page about g1")
        self.assertIn(value, pages[-1]["text"])
        self.assertTrue(os.path.isfile(left), "devloop emptied the release dir")

    def test_docs_name_the_release_dir_and_the_log(self):
        with open(os.path.join(HOME, "devloop", "README.md")) as fh:
            readme = fh.read()
        self.assertTrue("DEVLOOP_RELEASE_DIR" in readme, "devloop/README.md lacks DEVLOOP_RELEASE_DIR")
        self.assertTrue("runs/release/" in readme, "devloop/README.md lacks runs/release/")
        with open(os.path.join(HOME, "templates", "project-CLAUDE.md")) as fh:
            template = fh.read()
        self.assertTrue("DEVLOOP_RELEASE_DIR" in template, "templates/project-CLAUDE.md lacks DEVLOOP_RELEASE_DIR")


class T106Acceptance(LoopCase):
    """Ticket #106: a release-failed page names the last stage and the last 15 lines of the release log."""
    MARK = "devloop-" + "stage:"
    STAGED = ("echo '" + "devloop-" + "stage: build'; i=1; while [ $i -le 30 ]; do printf 'L%02d\\n' $i; i=$((i+1)); done; "
              "echo '" + "devloop-" + "stage: upload'; echo U1; echo U2 >&2; exit 3")
    STAGED_BLOCK = (["rc 3", "last step: upload", "last 15 lines of the release log:"]
                    + ["L%02d" % i for i in range(19, 31)] + ["devloop-" + "stage: upload", "U1", "U2"])

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith("DEVLOOP_")]:
            del self.env[key]

    def land(self, release):
        self.config(release=release)
        self.add(1)
        res = self.controller()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(1), "landed")
        self.assertEqual(self.snap().groups["g1"]["release"], "failed")

    def newest_decision(self):
        pages = sorted([r for r in self.snap().inbox.values() if r["kind"] == "decision" and r["ref"] == "g1"],
                       key=lambda r: r["at"])
        self.assertTrue(pages, "no decision page about g1")
        return pages[-1]["text"]

    def sha12(self):
        return git(self.proj, "rev-parse", "main").strip()[:12]

    def split(self, text, header):
        lines = text.rstrip("\n").split("\n")
        self.assertEqual(lines[0], header, text)
        return lines[1:]

    def landing_header(self):
        rel_log = self.snap().groups["g1"]["release_log"]
        return ("group g1 is in main (%s) but the release failed - not retried, not rolled back "
                "(release log: %s):" % (self.sha12(), rel_log))

    def test_landing_page_is_header_rc_step_and_last_fifteen_lines(self):
        self.land(self.STAGED)
        text = self.newest_decision()
        self.assertEqual(self.split(text, self.landing_header()), self.STAGED_BLOCK, text)
        self.assertNotIn("L18", text)

    def test_no_stage_line_gives_none_printed(self):
        self.land("echo A; echo B; exit 2")
        text = self.newest_decision()
        self.assertEqual(self.split(text, self.landing_header()),
                         ["rc 2", "last step: (none printed)", "last 15 lines of the release log:", "A", "B"], text)

    def test_rerun_page_has_its_header_and_the_same_block(self):
        self.land(self.STAGED)
        before = set(self.snap().inbox)
        res = self.run_py("tickets.py", "release", "g1")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        new = [r["text"] for k, r in self.snap().inbox.items()
               if k not in before and r["kind"] == "decision" and r["ref"] == "g1"]
        self.assertEqual(len(new), 1, new)
        text = new[0]
        rel_log = self.snap().groups["g1"]["release_log"]
        header = ("group g1 (%s): the re-run release failed - not retried, not rolled back "
                  "(release log: %s):" % (self.sha12(), rel_log))
        self.assertEqual(self.split(text, header), self.STAGED_BLOCK, text)
        self.assertNotIn("L18", text)

    def test_the_stage_literal_lives_only_in_core_logtail(self):
        dev = os.path.join(HOME, "devloop")
        self.assertTrue(os.path.isfile(os.path.join(dev, "core", "logtail.py")), "devloop/core/logtail.py missing")
        found = []
        for sub in ("", "core"):
            for name in sorted(os.listdir(os.path.join(dev, sub))):
                if not name.endswith(".py"):
                    continue
                with open(os.path.join(dev, sub, name), encoding="utf-8") as fh:
                    if self.MARK in fh.read():
                        found.append(os.path.join(sub, name) if sub else name)
        self.assertEqual(found, [os.path.join("core", "logtail.py")])
        import board
        for name in ("live_log_lines", "live_tail", "live_release_step"):
            self.assertTrue(callable(getattr(board, name, None)), "board.%s is gone" % name)


class ReleaseTimeoutSetting(LoopCase):
    def test_release_timeout_defaults_to_3600_and_can_be_set(self):
        from core.config import Config
        self.config(release="true")
        self.assertEqual(Config(self.proj).release_timeout_seconds, 3600)
        self.config(release_timeout_seconds=7)
        self.assertEqual(Config(self.proj).release_timeout_seconds, 7)


if __name__ == "__main__":
    unittest.main()
