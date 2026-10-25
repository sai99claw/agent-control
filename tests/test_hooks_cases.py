"""#164 acceptance cases written by the verifier (D-071 C8, design section 5 / V13):
`scripts/hooks.py install|check` merge edge cases next to the worker's tests/test_hooks.py.

Every expected command string is built here from the ticket's pinned target set and the
realpath of the AC_HOME this module passes, never read from what hooks.py prints. The
script is run directly (`python3 <AC_HOME>/scripts/hooks.py`, objective (1)) with an
explicit AC_HOME and --project, so these cases do not lean on actl or the harness sandbox.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS  # noqa: E402


class HooksMergeCases(unittest.TestCase):
    """Edge cases of the managed-entry merge (ticket test_plan, verifier list)."""

    MATCHER = "startup|resume|clear|compact|fork"

    def setUp(self):
        self.ac_home = os.path.dirname(os.path.abspath(SCRIPTS))
        self.home_real = os.path.realpath(self.ac_home)
        self.tmp = tempfile.mkdtemp(prefix="t164-hooks-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(self.project)
        self.project_real = os.path.realpath(self.project)
        self.settings = os.path.join(self.project, ".claude", "settings.json")

    # --- helpers (kept inside the class: no module-level names) ---

    def target(self, hook):
        interp = "sh" if hook.endswith(".sh") else "python3"
        return '%s "%s/scripts/%s"' % (interp, self.home_real, hook)

    def run_hooks(self, *args, ac_home=True):
        env = dict(os.environ)
        for name in ("AC_ROOT", "AC_HOME", "CLAUDE_PROJECT_DIR"):
            env.pop(name, None)
        if ac_home:
            env["AC_HOME"] = self.ac_home
        return subprocess.run(
            [sys.executable, os.path.join(self.ac_home, "scripts", "hooks.py")] + list(args),
            cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)

    def install(self, project=None):
        return self.run_hooks("install", "--project", project or self.project)

    def check(self, project=None):
        return self.run_hooks("check", "--project", project or self.project)

    def put(self, data):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")

    def raw(self, path=None):
        path = path or self.settings
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def load(self):
        with open(self.settings) as handle:
            return json.load(handle)

    def items(self, data, event):
        """(group, item) pairs of one event; [] only when the event list is really empty."""
        out = []
        for group in data["hooks"][event]:
            for item in group["hooks"]:
                out.append((group, item))
        return out

    def all_commands(self, data):
        cmds = []
        for groups in data.get("hooks", {}).values():
            for group in groups:
                for item in group.get("hooks", []):
                    cmds.append(item.get("command", ""))
        return cmds

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, "rc=%s\nstdout:\n%s\nstderr:\n%s"
                         % (proc.returncode, proc.stdout, proc.stderr))

    def assert_targets(self, data):
        """The three target entries, exactly once each, in the pinned shape."""
        for event, hook, timeout in (("SessionStart", "session-hook.sh", 60),
                                     ("SubagentStop", "agent-cost-hook.py", 10),
                                     ("SessionEnd", "agent-cost-hook.py", 10)):
            hits = [(g, i) for g, i in self.items(data, event) if hook in i.get("command", "")]
            self.assertEqual(len(hits), 1, "%s: %r" % (event, data["hooks"][event]))
            group, item = hits[0]
            self.assertEqual(item.get("type"), "command")
            self.assertEqual(item["command"], self.target(hook))
            self.assertEqual(item.get("timeout"), timeout)
            if event == "SessionStart":
                self.assertEqual(group.get("matcher"), self.MATCHER)

    def assert_settled(self):
        """A second install changes nothing; check agrees."""
        before = self.raw()
        again = self.install()
        self.assert_ok(again)
        self.assertIn("no change", again.stdout)
        self.assertEqual(self.raw(), before)
        self.assert_ok(self.check())

    # --- cases ---

    def test_managed_and_foreign_in_same_hooks_array(self):
        foreign = {"type": "command", "command": "echo foreign-in-same-array", "timeout": 7}
        managed = {"type": "command",
                   "command": 'bash "%s/scripts/session-hook.sh"' % self.home_real,
                   "timeout": 5}
        self.put({"hooks": {"SessionStart": [
            {"matcher": self.MATCHER, "hooks": [foreign, managed]}]}})
        proc = self.install()
        self.assert_ok(proc)
        data = self.load()
        self.assertIn(foreign, [i for _, i in self.items(data, "SessionStart")],
                      "the foreign item sharing the array was dropped")
        self.assertEqual(sum("foreign-in-same-array" in c for c in self.all_commands(data)), 1)
        self.assert_targets(data)
        self.assertFalse(any(c.startswith("bash ") for c in self.all_commands(data)))
        self.assert_settled()

    def test_managed_command_with_extra_arguments_is_rewritten(self):
        self.put({"hooks": {
            "SessionStart": [{"matcher": self.MATCHER, "hooks": [
                {"type": "command", "command": self.target("session-hook.sh"), "timeout": 60}]}],
            "SubagentStop": [{"hooks": [
                {"type": "command",
                 "command": self.target("agent-cost-hook.py") + " --verbose", "timeout": 10}]}],
            "SessionEnd": [{"hooks": [
                {"type": "command", "command": self.target("agent-cost-hook.py"),
                 "timeout": 10}]}]}})
        before = self.raw()
        chk = self.check()
        self.assertEqual(chk.returncode, 1, chk.stdout + chk.stderr)
        self.assertEqual(self.raw(), before, "check wrote the file")
        self.assertTrue([l for l in chk.stdout.splitlines()
                         if l.startswith("-") and "--verbose" in l], chk.stdout)
        proc = self.install()
        self.assert_ok(proc)
        data = self.load()
        self.assertFalse([c for c in self.all_commands(data) if "--verbose" in c])
        self.assert_targets(data)
        self.assert_settled()

    def test_settings_without_hooks_key(self):
        perms = {"allow": ["Bash(ls)"], "deny": []}
        env = {"FOO": "1"}
        self.put({"permissions": perms, "env": env})
        proc = self.install()
        self.assert_ok(proc)
        self.assertTrue([l for l in proc.stdout.splitlines()
                         if l.startswith("+") and "session-hook.sh" in l], proc.stdout)
        with open(self.settings) as handle:
            text = handle.read()
        data = json.loads(text)
        self.assertEqual(text, json.dumps(data, indent=2) + "\n", "not indent 2 + newline")
        self.assertEqual(data["permissions"], perms)
        self.assertEqual(data["env"], env)
        keys = list(data)
        self.assertLess(keys.index("permissions"), keys.index("env"))
        self.assert_targets(data)
        self.assert_settled()

    def test_project_with_trailing_slash(self):
        proc = self.install(self.project + "/")
        self.assert_ok(proc)
        self.assertTrue(os.path.isfile(self.settings), "file not written under the project")
        data = self.load()
        self.assert_targets(data)
        for cmd in self.all_commands(data):
            self.assertNotIn("//", cmd)
            self.assertNotIn("$CLAUDE_PROJECT_DIR", cmd)
        again = self.install(self.project + "/")
        self.assert_ok(again)
        self.assertIn("no change", again.stdout)

    def test_dogfood_with_trailing_slash_is_still_dogfood(self):
        a_file = os.path.join(self.ac_home, ".claude", "settings.json")
        before = self.raw(a_file)
        self.assertIsNotNone(before, "A has no committed .claude/settings.json")
        chk = self.check(self.ac_home + "/")
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
        self.assertFalse([l for l in chk.stdout.splitlines()
                          if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))],
                         chk.stdout)
        self.assertEqual(self.raw(a_file), before)

    def test_session_start_entry_without_matcher(self):
        self.put({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": self.target("session-hook.sh"), "timeout": 60}]}]}})
        proc = self.install()
        self.assert_ok(proc)
        data = self.load()
        self.assert_targets(data)
        self.assert_settled()

    def test_managed_entry_at_existing_foreign_file_is_rewritten_not_deleted(self):
        vendor = os.path.join(self.project_real, "vendor", "scripts", "session-hook.sh")
        os.makedirs(os.path.dirname(vendor))
        with open(vendor, "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        self.put({"hooks": {"SessionStart": [{"matcher": self.MATCHER, "hooks": [
            {"type": "command", "command": 'sh "%s"' % vendor, "timeout": 30}]}]}})
        proc = self.install()
        self.assert_ok(proc)
        data = self.load()
        self.assertFalse([c for c in self.all_commands(data) if "vendor" in c])
        self.assert_targets(data)
        self.assertTrue(os.path.isfile(vendor), "the user's own file was touched")
        self.assert_settled()

    def test_project_dir_spelling_missing_file_is_removed(self):
        self.put({"hooks": {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [
            {"type": "command",
             "command": 'python3 "$CLAUDE_PROJECT_DIR/scripts/main_guard.py"'},
            {"type": "command", "command": "echo keep-me"}]}]}})
        proc = self.install()
        self.assert_ok(proc)
        self.assertTrue([l for l in proc.stdout.splitlines()
                         if l.startswith("-") and "main_guard.py" in l], proc.stdout)
        data = self.load()
        cmds = self.all_commands(data)
        self.assertFalse([c for c in cmds if "main_guard.py" in c])
        self.assertIn("echo keep-me", cmds)
        self.assert_targets(data)

    def test_check_on_absent_file_writes_nothing(self):
        chk = self.check()
        self.assertEqual(chk.returncode, 1, chk.stdout + chk.stderr)
        self.assertTrue([l for l in chk.stdout.splitlines()
                         if l.startswith("+") and "agent-cost-hook.py" in l], chk.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".claude")),
                         "check created .claude/")

    def test_ac_home_unset_is_rc2(self):
        proc = self.run_hooks("install", "--project", self.project, ac_home=False)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("AC_HOME not set", proc.stderr + proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".claude")))


if __name__ == "__main__":
    unittest.main()
