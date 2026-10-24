"""#160 acceptance cases written by the verifier (D-071 row 2): config_version check,
migrate on a second T-shaped fixture, doctor's hook row negative control.

Expected values come from the ticket text (design section 2: migrate_1_to_2, the
check rc / message fragments), never from what project_config.py prints. Every config
here is written by the test itself, config_version included, so the cases do not lean
on what the harness DEFAULT_CONFIG happens to hold.
"""

import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, TIMEOUT, Sandbox  # noqa: E402


class ProjectConfigCases(Sandbox):
    """Arms beyond A1-A5: key order, nested unknown keys, odd versions, foreign hooks."""

    # A second T shape: hard_block first, rules last, nested unknown keys under rules and
    # memory, a top-level unknown key, and a sync row with a different directory.
    T_FIXTURE = {
        "hard_block": {"patterns": [], "dirs": [],
                       "paths": ["tools/sync-to-project.sh", "docs/DECISIONS.md",
                                 "scripts/sync-to-project.sh"]},
        "land": {"require_review": True, "gate": "full"},
        "port": 8123,
        "protected_paths": ["docs/VISION.md"],
        "verify_lint": {"enabled": True},
        "memory": {"cap_chars": 4000, "inbox_max_lines": 50},
        "tickets_dir": "tickets",
        "rules": {"models_dir": "docs/models", "sections": ["a", "b"],
                  "zzz_nested": {"keep": 1}, "roles_dir": "docs/roles"},
    }

    def actl(self, *args):
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=self.repo, env=self.env(),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def check(self):
        return subprocess.run(["python3", os.path.join(self.scripts, "project_config.py"),
                               "check", self.repo],
                              cwd=self.repo, env=self.env(),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def put_config(self, conf):
        return self.write("board/config.json", json.dumps(conf, indent=2) + "\n")

    def config_bytes(self):
        with open(os.path.join(self.repo, "board", "config.json"), "rb") as handle:
            return handle.read()

    def v2(self, **extra):
        conf = dict(DEFAULT_CONFIG)
        conf["config_version"] = 2
        conf.update(extra)
        return conf

    def say(self, done):
        return "rc=%s\nstdout:\n%s\nstderr:\n%s" % (done.returncode, done.stdout, done.stderr)

    # ------------------------------------------------------------ migrate

    def test_migrate_second_t_fixture_keeps_order_and_nested_unknown_keys(self):
        self.put_config(self.T_FIXTURE)
        done = self.actl("migrate")
        self.assertEqual(done.returncode, 0, self.say(done))
        raw = self.config_bytes().decode("utf-8")
        conf = json.loads(raw)
        self.assertEqual(conf.get("config_version"), 2, raw)
        self.assertEqual(conf.get("tests"), {"adapter": "unittest"}, raw)
        self.assertEqual(conf["rules"], {"sections": ["a", "b"], "zzz_nested": {"keep": 1}},
                         "only roles_dir / models_dir leave rules; nested unknown keys stay")
        self.assertEqual(conf["hard_block"]["paths"], ["docs/DECISIONS.md"],
                         "every entry ending in sync-to-project.sh goes, nothing else")
        self.assertEqual(conf["hard_block"]["patterns"], [])
        self.assertEqual(conf["hard_block"]["dirs"], [])
        for key in ("land", "protected_paths", "verify_lint", "memory", "port", "tickets_dir"):
            self.assertEqual(conf.get(key), self.T_FIXTURE[key], "%s must be kept" % key)
        kept = [k for k in conf if k in self.T_FIXTURE]
        self.assertEqual(kept, list(self.T_FIXTURE), "key order of the file must be kept")
        self.assertTrue(raw.endswith("}\n") and not raw.endswith("\n\n"),
                        "one trailing newline:\n" + raw[-20:])
        self.assertTrue(raw.splitlines()[1].startswith('  "')
                        and not raw.splitlines()[1].startswith('   '), "indent 2:\n" + raw)
        for word in ("roles_dir", "memory/role", "land", "protected_paths"):
            self.assertIn(word, done.stdout, self.say(done))
        self.assertIn("verify_lint", done.stdout + done.stderr,
                      "unknown top-level keys are printed as warnings")

        before = self.config_bytes()
        again = self.actl("migrate")
        self.assertEqual(again.returncode, 0, self.say(again))
        self.assertEqual(self.config_bytes(), before, "second migrate must not touch the file")
        self.assertTrue(re.search(r"\b0\b", again.stdout) or "nothing" in again.stdout,
                        self.say(again))

    # ------------------------------------------------------------ check: odd versions

    def test_check_refuses_version_zero_and_names_it(self):
        self.put_config(self.v2())
        ok = self.check()
        self.assertEqual(ok.returncode, 0, "positive control: version 2 passes\n" + self.say(ok))

        self.put_config(self.v2(config_version=0))
        done = self.check()
        self.assertEqual(done.returncode, 2, self.say(done))
        self.assertIn("config_version", done.stderr, self.say(done))
        self.assertRegex(done.stderr, r"config_version\D{0,20}\b0\b",
                         "the message names the value 0")
        gated = self.actl("ticket", "list", "--open")
        self.assertEqual(gated.returncode, 2, "actl runs check first\n" + self.say(gated))
        self.assertIn("config_version", gated.stderr, self.say(gated))

    def test_check_refuses_non_int_versions_and_names_the_value(self):
        self.put_config(self.v2())
        ok = self.check()
        self.assertEqual(ok.returncode, 0, "positive control: version 2 passes\n" + self.say(ok))

        for value, shown in (("two", "two"), (True, "true"), (2.5, "2.5")):
            with self.subTest(value=value):
                self.put_config(self.v2(config_version=value))
                done = self.check()
                self.assertEqual(done.returncode, 2, self.say(done))
                self.assertIn("config_version", done.stderr, self.say(done))
                self.assertIn(shown, done.stderr.lower(), self.say(done))

    # ------------------------------------------------------------ doctor: hooks

    def test_doctor_has_no_row_for_a_foreign_hook_whose_file_exists(self):
        hook = self.write("tools/foreign-hook.sh", "#!/bin/sh\nexit 0\n")
        settings = {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "sh %s --quiet" % hook, "timeout": 10}]}]}}
        self.write(".claude/settings.json", json.dumps(settings, indent=2))
        self.put_config(self.v2())

        done = self.actl("doctor")
        self.assertEqual(done.returncode, 0, self.say(done))
        self.assertNotIn("foreign-hook.sh", done.stdout, self.say(done))
        self.assertNotIn("actl hooks install", done.stdout, self.say(done))

        # Must-be-false control: the same hook with its file gone is an error row, so the
        # silence above came from reading settings.json, not from never looking.
        os.remove(hook)
        gone = self.actl("doctor")
        self.assertEqual(gone.returncode, 1, self.say(gone))
        rows = [line for line in gone.stdout.splitlines() if "foreign-hook.sh" in line]
        self.assertTrue(rows and "actl hooks install" in rows[0], self.say(gone))


if __name__ == "__main__":
    unittest.main()
