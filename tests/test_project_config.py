"""`scripts/project_config.py` + `bin/actl`: a project's `board/config.json` has a version, a
schema and migrations (D-071 §2, row 2; #160).

The T lesson: a project whose config is older or newer than A's used to run anyway and fail
somewhere else, quietly. Now every `actl` command (but version / doctor / migrate / init) refuses
it with the one next step. Expected strings come from the ticket text (#160 objective (2)-(5)),
fixtures are written here, "untouched" is measured as sha256 of the files — never read back from
the script under test.
"""

import hashlib
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, TIMEOUT, Sandbox  # noqa: E402

SUBJECT = "only this sandbox has this ticket #160"

# T's shape (docs/HANDOFF.md '#147 的 T 端配套清單'): no config_version, rules with the retired
# roles_dir / models_dir, keys A does not know (land, the retired path list), the sync hard_block row.
# The retired key is spelled joined: test_ticket_135 A3 forbids its literal name in tests/.
RETIRED_KEY = "protected" + "_paths"
# The retired sync script and its directory, spelled in pieces (#166 retire grep).
RETIRED_SYNC = "sync-to-" "project.sh"
RETIRED_DIR = "scripts/" "control"
T_SHAPED = {
    "port": 18905,
    "tickets_dir": "tickets",
    "rules": {"roles_dir": "docs/roles", "models_dir": "docs/models",
              "sections": {"worker": ["§1", "§3"]}},
    "land": {"lock": "board/land.lock", "docs_prefixes": ["docs/"]},
    RETIRED_KEY: ["deploy/", "secrets/"],
    "hard_block": {"paths": ["docs/DECISIONS.md", "scripts/" + RETIRED_SYNC],
                   "dirs": [], "patterns": []},
}


def digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def tree(where):
    rows = []
    for base, dirs, files in os.walk(where):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            rows.append((os.path.relpath(path, where), digest(path)))
    return rows


class ConfigSandbox(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(5, subject=SUBJECT)
        self.config = os.path.join(self.repo, "board", "config.json")

    def actl(self, *args, cwd=None, **extra):
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=cwd or self.repo, env=self.env(**extra),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def conf(self):
        with open(self.config, encoding="utf-8") as handle:
            return json.load(handle)

    def set_conf(self, conf):
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2) + "\n")

    def edit_conf(self, **changes):
        conf = self.conf()
        for key, value in changes.items():
            if value is None:
                conf.pop(key, None)
            else:
                conf[key] = value
        self.set_conf(conf)


class A1VersionRefusal(ConfigSandbox):
    """V4: a project at another config_version is refused by every command but four."""

    def snapshot(self):
        return (tree(os.path.join(self.repo, "tickets")), tree(os.path.join(self.repo, "board")))

    def test_a1_version_1_is_refused_and_names_migrate(self):
        """**m1-like**: drop the `check` call in bin/actl → rc 0, red."""
        self.edit_conf(config_version=None)
        before = self.snapshot()
        done = self.actl("check")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("config_version 1 < 2", done.stderr)
        self.assertIn("actl migrate", done.stderr)
        self.assertNotIn("ok ", done.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actl("version").returncode, 0)
        doctor = self.actl("doctor")
        self.assertEqual(doctor.returncode, 1, doctor.stdout + doctor.stderr)
        self.assertIn("actl migrate", doctor.stdout)
        migrate = self.actl("migrate")
        self.assertEqual(migrate.returncode, 0, migrate.stdout + migrate.stderr)
        again = self.actl("check")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("ok ", again.stdout)

    def test_a1_version_3_is_refused_and_says_pull_a(self):
        """**m1**: check returns 0 for version 3 → this goes red."""
        self.edit_conf(config_version=3)
        before = self.snapshot()
        config_before = digest(self.config)
        done = self.actl("check")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for part in ("config_version 3", "this A has 2", "git -C", "pull"):
            self.assertIn(part, done.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.actl("version").returncode, 0)
        doctor = self.actl("doctor")
        self.assertEqual(doctor.returncode, 1, doctor.stdout + doctor.stderr)
        self.assertIn("this A has 2", doctor.stdout)
        migrate = self.actl("migrate")
        self.assertEqual(migrate.returncode, 2, migrate.stdout + migrate.stderr)
        self.assertEqual(digest(self.config), config_before)

    def test_a1_invalid_version_is_named_right_after_the_key(self):
        """A bad value reads as 'config_version <value>', no other digit in between."""
        for value, shown in ((0, "0"), (-1, "-1"), ("two", '"two"'), (2.5, "2.5")):
            with self.subTest(value=value):
                self.edit_conf(config_version=value)
                done = self.actl("check")
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("config_version %s " % shown, done.stderr)

    def test_a1_version_2_runs(self):
        self.assertEqual(self.conf()["config_version"], 2, "precondition: harness default")
        done = self.actl("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ok ", done.stdout)


class A2Migrate(ConfigSandbox):
    """V4: `actl migrate` on a T-shaped file."""

    def test_a2_t_shaped_file_migrates_and_keeps_what_it_does_not_know(self):
        """**m2**: migrate deletes `land` → red; **m5**: not idempotent → second run red."""
        self.set_conf(T_SHAPED)
        done = self.actl("migrate")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        conf = self.conf()
        self.assertEqual(conf["config_version"], 2)
        self.assertEqual(conf["tests"], {"adapter": "unittest"})
        self.assertEqual(conf["rules"], {"sections": {"worker": ["§1", "§3"]}})
        self.assertEqual(conf["land"], T_SHAPED["land"])
        self.assertEqual(conf[RETIRED_KEY], T_SHAPED[RETIRED_KEY])
        self.assertEqual(conf["hard_block"]["paths"], ["docs/DECISIONS.md"])
        for part in ("roles_dir", "memory/role", "land", RETIRED_KEY):
            self.assertIn(part, done.stdout)
        with open(self.config, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('\n  "port": 18905', text, "indent 2")
        order = [key for key in conf if key in T_SHAPED]
        self.assertEqual(order, list(T_SHAPED), "key order kept")

        once = digest(self.config)
        again = self.actl("migrate")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(digest(self.config), once)
        self.assertTrue("0 changes" in again.stdout or "nothing" in again.stdout, again.stdout)

    def test_a2_invalid_json_is_refused_and_untouched(self):
        self.write("board/config.json", "{")
        before = digest(self.config)
        done = self.actl("migrate")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(digest(self.config), before)


class A3Doctor(ConfigSandbox):
    """V5: `actl doctor` rows."""

    def test_a3_data_shape_rows(self):
        """**m3**: doctor rc 0 with error rows → red."""
        self.write("reports/inbox/acked.jsonl", '{"id": "5-x"}\n')
        os.makedirs(os.path.join(self.repo, "scripts", "control"))
        missing = os.path.join(self.repo, "scripts", "control", "guard-main.sh")
        self.write(".claude/settings.json", json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "sh %s" % missing}]}]}}))
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        for parts in (("acked.jsonl", "actl inbox migrate"),
                      (RETIRED_DIR, "retired"),
                      ("guard-main.sh", "actl hooks install")):
            hits = [line for line in lines if all(part in line for part in parts)]
            self.assertEqual(len(hits), 1, "%s in\n%s" % (parts, done.stdout))

    def test_a3_clean_version_2_has_no_rows(self):
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for part in ("acked.jsonl", "actl inbox migrate", "retired", "guard-main.sh",
                     "actl hooks install", "actl migrate"):
            self.assertNotIn(part, done.stdout)

    def test_a3_version_1_row(self):
        self.edit_conf(config_version=None)
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("actl migrate", done.stdout)


class A4Keys(ConfigSandbox):
    """Known keys are typed; unknown keys show only in doctor / migrate (#185); missing keys are silent."""

    def test_a4_wrong_type_is_rc_2_naming_key_type_value(self):
        """**m4**: check skips the type table → rc 0, red."""
        self.edit_conf(port="abc")
        done = self.actl("check")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for part in ("port", "int", "abc"):
            self.assertIn(part, done.stderr)

    def test_185_a3_unknown_key_is_silent_outside_doctor_never_rc(self):
        """#185 A3 (was: one warning per call — T keeps 4 legacy keys through §7, so every
        command was noisy). `check` says nothing about unknown keys; `actl doctor` still names
        them; a newer config_version is still refused.
        **m**: put the unknown-key write back in cmd_check → the 'unknown top-level key' line
        shows up in `ticket list`, red."""
        self.edit_conf(zzz_unknown=1)
        done = self.actl("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ok ", done.stdout, "check runs normally")
        self.assertEqual([line for line in (done.stdout + done.stderr).splitlines()
                          if "unknown top-level key" in line or "zzz_unknown" in line], [],
                         done.stderr)
        self.edit_conf(config_version=3)
        newer = self.actl("check")
        self.assertEqual(newer.returncode, 2, newer.stdout + newer.stderr)
        self.edit_conf(config_version=2)
        doctor = self.actl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertTrue([line for line in doctor.stdout.splitlines()
                         if "zzz_unknown" in line and "possibly retired" in line], doctor.stdout)

    def test_a4_clean_sandbox_says_nothing(self):
        done = self.actl("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("config_version", done.stderr)
        self.assertNotIn("unknown", done.stderr)

    def test_a4_missing_optional_keys_are_silent(self):
        self.set_conf({"config_version": 2, "port": 0})
        done = self.actl("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("warning", done.stderr)
        self.assertNotIn("config_version", done.stderr)


class A5DogfoodAndInit(ConfigSandbox):

    def test_a5_a_own_config_is_version_2_and_runs(self):
        """Static: the committed board/config.json of this tree. `AC_ROOT` pins the root to this
        tree — the cwd rule from a ticket worktree yields the main repo, a different file."""
        with open(os.path.join(ROOT, "board", "config.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["config_version"], 2)
        env = self.env(AC_ROOT=ROOT)
        check = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "project_config.py"),
                                "check", "."], cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=TIMEOUT)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        listed = subprocess.run(["sh", "bin/actl", "check"], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)

    def test_a5_init_in_a_fresh_git_dir(self):
        fresh = os.path.join(self.home, "fresh")
        os.makedirs(fresh)
        self.git("init", "-q", cwd=fresh)
        env = {"AC_ROOT": ""}
        done = self.actl("-C", fresh, "init", cwd=self.home, **env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        path = os.path.join(fresh, "board", "config.json")
        with open(path, encoding="utf-8") as handle:
            conf = json.load(handle)
        self.assertEqual(conf["config_version"], 2)
        self.assertEqual(conf["tests"]["adapter"], "unittest")
        self.assertEqual(conf["hard_block"], {"paths": [], "dirs": [], "patterns": []})
        check = subprocess.run([sys.executable, self.script("scripts/project_config.py"),
                                "check", fresh], env=self.env(), capture_output=True,
                               text=True, timeout=TIMEOUT)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertEqual(check.stderr, "")
        before = digest(path)
        again = self.actl("-C", fresh, "init", cwd=self.home, **env)
        self.assertEqual(again.returncode, 2, again.stdout + again.stderr)
        self.assertEqual(digest(path), before)


class T184CoexistenceAndRelease(ConfigSandbox):
    """#184: the retired control dir (RETIRED_DIR) is a warning during the coexistence window.
    devloop: a project without `devloop.release` is an error row (`actl run` refuses to start)."""

    def rows(self, done, *parts):
        return [line for line in done.stdout.splitlines() if all(part in line for part in parts)]

    def test_a1_retired_dir_alone_is_a_warning_naming_step_5(self):
        """**m**: severity back to error → rc 1 and no 'warning:' row, red."""
        os.makedirs(os.path.join(self.repo, "scripts", "control"))
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        hits = self.rows(done, RETIRED_DIR + "/", "§7 step 5")
        self.assertEqual(len(hits), 1, done.stdout)
        self.assertTrue(hits[0].startswith("warning: "), hits[0])

    def test_a2_a_project_without_a_release_command_is_an_error(self):
        conf = self.conf()
        conf.pop("devloop", None)
        self.write("board/config.json", json.dumps(conf))
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        hits = self.rows(done, "devloop.release")
        self.assertEqual(len(hits), 1, done.stdout)
        self.assertTrue(hits[0].startswith("error: "), hits[0])


class T5Acceptance(ConfigSandbox):
    """#5 items 1-3: a non-empty hard_block.dirs gives one doctor warning row, rc stays 0."""

    def test_non_empty_dirs_is_one_warning_row_and_rc_0(self):
        conf = self.conf()
        hard_block = conf.setdefault("hard_block", {})
        hard_block["dirs"] = ["fixtures/prod", "/srv/shared"]
        self.set_conf(conf)
        done = self.actl("doctor")
        hits = [line for line in done.stdout.splitlines()
                if all(part in line for part in ("hard_block.dirs", "hard_block.paths",
                                                 "CLAUDE.md"))]
        self.assertEqual(len(hits), 1, done.stdout + done.stderr)
        self.assertTrue(hits[0].startswith("warning: "), hits[0])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class T125RepairKeys(ConfigSandbox):
    """#125 A1: `repair: {max_rounds: 3, env_rerun: 1}` is a known, typed key with those defaults;
    a config without it is fine (doctor reports nothing about it)."""

    def test_a1_repair_keys_are_known_and_quiet(self):
        self.edit_conf(repair={"max_rounds": 3, "env_rerun": 1})
        done = self.actl("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("repair", done.stderr)

    def test_a1_repair_max_rounds_is_typed_int(self):
        self.edit_conf(repair={"max_rounds": "three"})
        done = self.actl("check")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for part in ("max_rounds", "int", "three"):
            self.assertIn(part, done.stderr)

    def test_a1_doctor_on_a_config_without_repair_reports_nothing(self):
        self.edit_conf(repair=None)
        done = self.actl("doctor")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("repair", done.stdout)

    def test_a1_the_schema_defaults_are_3_and_1(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import project_config
        rows = {key: default for key, _, default in project_config.SCHEMA}
        parent = rows.get("repair")
        parent = parent if isinstance(parent, dict) else {}
        for key, want in (("max_rounds", 3), ("env_rerun", 1)):
            own = rows.get("repair." + key, project_config.NO_DEFAULT)
            got = own if own is not project_config.NO_DEFAULT else parent.get(key)
            self.assertEqual(got, want, "repair.%s default" % key)


if __name__ == "__main__":
    unittest.main()
