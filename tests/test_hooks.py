"""`actl hooks install|check` (#164, D-071 C8): the three Claude Code hook entries of
`<project>/.claude/settings.json` point into AC_HOME; everything that is not ours survives.

Expected strings are built here from the AC_HOME the test set, never read back from the output.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import TIMEOUT, Sandbox  # noqa: E402

MATCHER = "startup|resume|clear|compact|fork"


class HooksSandbox(Sandbox):

    @property
    def settings_path(self):
        return os.path.join(self.repo, ".claude", "settings.json")

    def actl(self, *args):
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=self.repo, env=self.env(), capture_output=True, text=True,
                              timeout=TIMEOUT)

    def raw(self):
        with open(self.settings_path, "rb") as handle:
            return handle.read()

    def loaded(self):
        return json.loads(self.raw().decode("utf-8"))

    def home_dir(self):
        return os.path.realpath(self.ac_home)


class A1InstallTwice(HooksSandbox):

    def test_a1_install_creates_then_is_idempotent(self):
        """**m3**: install appends instead of rewriting → the second run is not 'no change'."""
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude")))
        done = self.actl("hooks", "install")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        added = [line for line in done.stdout.splitlines() if line.startswith("+")]
        self.assertTrue([line for line in added if "session-hook.sh" in line], done.stdout)
        self.assertTrue([line for line in added if "agent-cost-hook.py" in line], done.stdout)
        conf = self.loaded()
        for event in ("SessionStart", "SubagentStop", "SessionEnd"):
            self.assertIn(event, conf["hooks"])
        start = conf["hooks"]["SessionStart"]
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0]["matcher"], MATCHER)
        self.assertEqual(start[0]["hooks"], [{
            "type": "command",
            "command": 'sh "%s/scripts/session-hook.sh"' % self.home_dir(),
            "timeout": 60}])
        for event in ("SubagentStop", "SessionEnd"):
            self.assertEqual(conf["hooks"][event], [{"hooks": [{
                "type": "command",
                "command": 'python3 "%s/scripts/agent-cost-hook.py"' % self.home_dir(),
                "timeout": 10}]}])
        before = self.raw()
        self.assertTrue(before.endswith(b"\n"))
        again = self.actl("hooks", "install")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("no change", again.stdout)
        self.assertEqual(self.raw(), before)


class A2ForeignSurvives(HooksSandbox):

    def test_a2_foreign_kept_missing_managed_removed(self):
        """**m1**: install drops the foreign entry; **m5**: the missing managed entry is kept."""
        foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo foreign-hook"}]}
        gone = os.path.join(self.repo, "scripts", "control", "guard-main.sh")
        perms = {"allow": ["Bash(ls:*)"], "deny": []}
        written = {
            "hooks": {
                "PreToolUse": [foreign],
                "SessionStart": [{"hooks": [{"type": "command",
                                             "command": 'sh "%s"' % gone}]}],
            },
            "permissions": perms,
        }
        self.write(".claude/settings.json", json.dumps(written, indent=2) + "\n")
        done = self.actl("hooks", "install")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        conf = self.loaded()
        self.assertEqual(conf["hooks"]["PreToolUse"], [foreign])
        self.assertEqual(conf["permissions"], perms)
        self.assertEqual(list(conf), ["hooks", "permissions"])
        self.assertNotIn("guard-main.sh", self.raw().decode("utf-8"))
        lines = done.stdout.splitlines()
        self.assertTrue([l for l in lines if l.startswith("-") and "guard-main.sh" in l], done.stdout)
        for name in ("session-hook.sh", "agent-cost-hook.py"):
            self.assertTrue([l for l in lines if l.startswith("+") and name in l], done.stdout)
        self.assertEqual(sorted(conf["hooks"]), ["PreToolUse", "SessionEnd", "SessionStart",
                                                 "SubagentStop"])


    def test_a2_missing_managed_in_a_non_target_event_is_deleted(self):
        """**m5**: a missing managed entry outside the three target events (T's PreToolUse
        guard) is kept → red; a foreign entry beside it survives."""
        gone = os.path.join(self.repo, "scripts", "control", "guard-main.sh")
        foreign = {"type": "command", "command": "echo foreign-hook"}
        self.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash|Read", "hooks": [{"type": "command", "command": 'sh "%s"' % gone}]},
            {"matcher": "Bash", "hooks": [foreign]}]}}))
        self.assertEqual(self.actl("hooks", "install").returncode, 0)
        conf = self.loaded()
        self.assertEqual(conf["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [foreign]}])
        self.assertNotIn("guard-main.sh", self.raw().decode("utf-8"))

    def test_a2_foreign_sibling_in_the_same_group_survives(self):
        foreign = {"type": "command", "command": "echo foreign-hook"}
        self.write(".claude/settings.json", json.dumps({"hooks": {"SessionStart": [
            {"hooks": [foreign, {"type": "command", "command": "sh /x/session-hook.sh --a"}]}]}}))
        self.assertEqual(self.actl("hooks", "install").returncode, 0)
        groups = self.loaded()["hooks"]["SessionStart"]
        self.assertEqual(groups[0], {"hooks": [foreign]})
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[1]["matcher"], MATCHER)


class A3Check(HooksSandbox):

    def test_a3_check_reports_drift_and_never_writes(self):
        """**m2**: check writes the file → the byte comparison goes red."""
        self.assertEqual(self.actl("hooks", "install").returncode, 0)
        clean = self.actl("hooks", "check")
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertFalse([l for l in clean.stdout.splitlines() if l[:1] in "+-"], clean.stdout)
        conf = self.loaded()
        conf["hooks"]["SessionStart"][0]["hooks"][0]["command"] += " --x"
        self.write(".claude/settings.json", json.dumps(conf, indent=2) + "\n")
        before = self.raw()
        drift = self.actl("hooks", "check")
        self.assertEqual(drift.returncode, 1, drift.stdout + drift.stderr)
        lines = drift.stdout.splitlines()
        self.assertTrue([l for l in lines if l.startswith("-") and "--x" in l], drift.stdout)
        plus = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
        self.assertTrue(plus and all("--x" not in l for l in plus), drift.stdout)
        self.assertEqual(self.raw(), before)

    def test_a3_invalid_json_is_rc_2_for_both_and_untouched(self):
        self.write(".claude/settings.json", "{")
        for sub in ("check", "install"):
            done = self.actl("hooks", sub)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("settings.json", done.stderr)
            self.assertEqual(self.raw(), b"{")


class A5Wiring(HooksSandbox):

    def test_a5_check_without_file_and_bad_subcommand(self):
        done = self.actl("hooks", "check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue([l for l in done.stdout.splitlines() if l.startswith("+")], done.stdout)
        self.assertFalse(os.path.exists(self.settings_path))
        self.assertEqual(self.actl("hooks", "nonsense").returncode, 2)

    def test_a5_ac_home_is_required(self):
        env = self.env()
        env.pop("AC_HOME")
        done = subprocess.run([sys.executable, self.script("scripts/hooks.py"), "check"],
                              cwd=self.repo, env=env, capture_output=True, text=True,
                              timeout=TIMEOUT)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("AC_HOME not set", done.stderr)

    def test_a5_doctor_handshake(self):
        gone = os.path.join(self.repo, "scripts", "control", "guard-main.sh")
        self.write(".claude/settings.json", json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": 'sh "%s"' % gone}]}]}}))
        self.assertIn("actl hooks install", self.actl("doctor").stdout)
        self.assertEqual(self.actl("hooks", "install").returncode, 0)
        after = self.actl("doctor")
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertNotIn("hooks install", after.stdout)


if __name__ == "__main__":
    unittest.main()
