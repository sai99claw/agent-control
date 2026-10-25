"""#161 `scripts/adapter-unittest.py`: A's built-in unittest adapter (D-071 C6, design §4).

What is pinned here is the seam A's gate will read in row 3b — `select`'s stdout, the exit code and
`result.json` — never the log text:
- A1 `select`: every row of `gate.sh map()` (copied below as literal data, not read back from the
  config the adapter reads) selects the same ids through `tests.unittest.map`; first match wins;
  `ids: []` is "no test, not unmapped"; empty stdin is rc 3, not an empty green.
- A2 `run` / `full` write schema 1: one row per id with status / message / exception / file / line;
  a skip without a reason is an error; zero ids and no `AC_TEST_OUT` are rc 2.
- A3 environment suspect: a declared row makes rc 86, and rc 86 never comes with `[]`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT  # noqa: E402

ADAPTER = os.path.join(SCRIPTS, "adapter-unittest.py")

# One representative path per row of `scripts/gate.sh map()` (base 3bdba76 plus this ticket's
# `scripts/adapter-unittest.py` row) and the ids that row adds — copied by hand from the shell,
# so a config row that drifts from the gate is red here. Multi-pattern arms (`a|b`) are one entry
# per pattern. Not listed: the `tests/test_*.py` arm (the adapter's built-in, asserted below) and
# `*` (→ `unmapped`).
HARNESS_IDS = ["test_ticket", "test_event", "test_memory", "test_land", "test_gate",
               "test_heartbeat", "test_new_session", "test_board", "test_no_project_names",
               "test_status", "test_verify_case", "test_apply", "test_auto_fix", "test_inbox",
               "test_rules", "test_actl", "test_session_hook", "test_review",
               "test_verify_runner", "test_consolidate_memory"]
VERIFY_IDS = ["test_verify_runner", "test_verify_case", "test_actl"]
REPO_MAP_IDS = ["test_repo_map_contract"]
GATE_MAP = [
    ("tests/control_harness.py", HARNESS_IDS),
    ("bin/actl", ["test_actl", "test_project_config", "test_hooks"]),
    ("scripts/hooks.py", ["test_hooks", "test_session_hook"]),
    ("scripts/project_config.py", ["test_project_config", "test_actl", "test_session_hook"]),
    (".gitignore", ["test_no_project_names"]),
    ("scripts/event.py", ["test_event", "test_ticket", "test_land", "test_actl"]),
    ("scripts/ticket.py", ["test_ticket", "test_land", "test_auto_fix_codex"]),
    ("scripts/land.sh", ["test_land"]),
    ("scripts/gate.sh", ["test_gate", "test_status"]),
    ("scripts/gate.example.sh", ["test_gate", "test_status"]),
    ("scripts/apply.sh", ["test_apply"]),
    ("scripts/auto-fix.sh", ["test_auto_fix", "test_auto_fix_codex", "test_rules_delivery"]),
    ("scripts/review.sh", ["test_review", "test_rules_delivery"]),
    ("scripts/wtbase.sh", ["test_apply", "test_auto_fix", "test_land", "test_review", "test_actl",
                           "test_gate", "test_heartbeat", "test_new_session", "test_session_hook",
                           "test_consolidate_memory"]),
    ("scripts/inbox.py", ["test_inbox", "test_gate", "test_land", "test_new_session"]),
    ("scripts/rules.py", ["test_rules", "test_rules_delivery"]),
    ("scripts/heartbeat.sh", ["test_heartbeat"]),
    ("scripts/new-session.sh", ["test_new_session", "test_rules_delivery"]),
    ("scripts/session-hook.sh", ["test_session_hook"]),
    (".claude/settings.json", ["test_session_hook"]),
    ("scripts/consolidate-memory.sh", ["test_consolidate_memory", "test_new_session"]),
    ("scripts/memory.py", ["test_memory", "test_memory_note_names", "test_memory_lifecycle",
                           "test_memory_layer_policy"]),
    ("scripts/verify.py", VERIFY_IDS),
    ("verify/TAGS.md", VERIFY_IDS),
    ("verify/TAGS.d/zzz.md", VERIFY_IDS),
    ("scripts/verify-case.py", ["test_verify_case", "test_actl"]),
    ("verify/example/test_example.py", ["test_verify_runner"]),
    ("scripts/sync-to-project.sh", ["test_sync_to_project"]),
    ("scripts/status.py", ["test_status", "test_gate", "test_land"]),
    ("scripts/adapter-unittest.py", ["test_adapter_unittest", "test_status", "test_gate"]),
    ("memory/model/opus.md", ["test_memory", "test_no_project_names", "test_routing_cards"]),
    ("memory/role/implementer.md", ["test_memory", "test_sync_to_project",
                                    "test_no_project_names"]),
    ("board/board.py", ["test_board"]),
    ("board/config.json", ["test_board", "test_event", "test_heartbeat", "test_memory",
                           "test_routing_cards"]),
    ("scripts/repo-map.py", REPO_MAP_IDS),
    ("code-map/catalog.json", REPO_MAP_IDS),
    ("code-map/index.json", REPO_MAP_IDS),
    ("code-map/INDEX.md", REPO_MAP_IDS),
    ("tickets/161.json", ["test_ticket"]),
    ("tickets/SCHEMA.md", ["test_ticket"]),
    ("docs/DESIGN-ENV-SUSPECT.md", ["test_status", "test_no_project_names"]),
    ("docs/DISPATCH-TEMPLATE.md", ["test_rules_delivery", "test_no_project_names"]),
    ("templates/dispatch-verifier.md", ["test_templates", "test_rules_delivery",
                                        "test_no_project_names"]),
    ("templates/dispatch-reviewer.md", ["test_review", "test_templates", "test_rules_delivery",
                                        "test_no_project_names"]),
    ("templates/dispatch-opener.md", ["test_rules_delivery", "test_no_project_names"]),
    ("docs/FLOW.html", ["test_no_project_names"]),
    ("docs/HANDOFF.md", ["test_no_project_names"]),
]
BASE_IDS = ["test_ticket", "test_event", "test_memory", "test_no_project_names"]

# A2's fixture: one of each status. `FAIL_LINE` is found in this text, not hard-coded.
FIXTURE = '''import unittest


class FX(unittest.TestCase):
    def test_pass(self):
        pass

    def test_fail(self):
        self.assertEqual(1, 2)

    @unittest.skip("fx reason")
    def test_skip(self):
        pass

    @unittest.skip("")
    def test_skip_empty(self):
        pass


class Broken(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setUp broke")

    def test_x(self):
        pass
'''
FIVE = ["test_fx.FX.test_pass", "test_fx.FX.test_fail", "test_fx.Broken.test_x",
        "test_fx.FX.test_skip", "test_fx.FX.test_skip_empty"]
FAIL_LINE = FIXTURE.splitlines().index("        self.assertEqual(1, 2)") + 1

# Same shape as tests/test_status.py's DECLARES_THEN_SKIPS: the case prints the line, then skips.
DECLARES_THEN_SKIPS = '''import unittest


class T(unittest.TestCase):
    def test_it_declares_and_skips(self):
        print("ENVIRONMENT-SUSPECT: firefox fake")
        self.skipTest("environment is not right, recorded as skip")
'''
# Same shape as tests/test_gate.py's ENVIRONMENT_WAVE: one engine, one message shape, 12 times.
WAVE = '''import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index in range(12):
            with self.subTest(engine="safari", id=index):
                self.fail("localStorage id=%d empty after %d seconds" % (index, index + 100))
'''
SUSPECT_KEYS = ["source", "engine", "why", "count", "threshold", "log", "line"]


def clean_env(**extra):
    """No outer `AC_*` (land's full run sets them) — each call states the ones it means."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    env.update(extra)
    return env


def adapter(*args, env, stdin="", cwd=ROOT):
    return subprocess.run([sys.executable, ADAPTER, *args], input=stdin, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=TIMEOUT)


class Select(unittest.TestCase):
    """A1 (V12): `select --mode branch|base` with A's own config, cwd = A."""

    def env(self, **extra):
        return clean_env(AC_ROOT=ROOT, AC_CODE_ROOT=ROOT, **extra)

    def select(self, *files, env=None):
        stdin = "".join(path + "\n" for path in files)
        return adapter("select", "--mode", "branch", env=env or self.env(), stdin=stdin)

    def test_every_gate_map_row_selects_the_ids_that_row_adds(self):
        """**Mutation m2-adjacent**: drop a row from `tests.unittest.map` → its path prints
        `unmapped` here. Each path is fed alone, so a row is not covered by its neighbour's ids."""
        for path, ids in GATE_MAP:
            with self.subTest(path=path):
                done = self.select(path)
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = done.stdout.splitlines()
                self.assertNotIn("unmapped", done.stdout)
                self.assertEqual(set(lines), set(ids), done.stdout)
                self.assertEqual(len(lines), len(set(lines)), "deduped: %r" % lines)

    def test_unknown_file_is_unmapped_and_test_files_are_built_in(self):
        self.assertEqual(self.select("docs/zzz.unknownext").stdout.splitlines(),
                         ["unmapped docs/zzz.unknownext"])
        self.assertEqual(self.select("tests/test_event.py").stdout.splitlines(), ["test_event"])
        gone = self.select("tests/test_gone_zzz.py")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertEqual(gone.stdout, "", "a deleted test file selects nothing, not unmapped")

    def test_several_files_are_deduped_in_order(self):
        done = self.select("scripts/land.sh", "scripts/status.py", "docs/zzz.unknownext")
        self.assertEqual(done.stdout.splitlines(),
                         ["test_land", "test_status", "test_gate", "unmapped docs/zzz.unknownext"])

    def test_first_match_wins_and_empty_ids_is_not_unmapped(self):
        """**Mutation m2**: let a later matching row add its ids too → the first assert is red."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "board"))
            with open(os.path.join(tmp, "board", "config.json"), "w", encoding="utf-8") as h:
                json.dump({"tests": {"unittest": {"map": [
                    {"match": "scripts/*", "ids": ["test_first"]},
                    {"match": "scripts/x.py", "ids": ["test_second"]},
                    {"match": "docs/*.txt", "ids": []}]}}}, h)
            env = clean_env(AC_ROOT=tmp, AC_CODE_ROOT=ROOT)
            first = self.select("scripts/x.py", env=env)
            self.assertEqual(first.stdout.splitlines(), ["test_first"], first.stderr)
            # `*` crosses `/` (sh `case`), so `scripts/a/b.sh` is still the first row.
            self.assertEqual(self.select("scripts/a/b.sh", env=env).stdout.splitlines(),
                             ["test_first"])
            empty = self.select("docs/a/note.txt", env=env)
            self.assertEqual((empty.returncode, empty.stdout), (0, ""), empty.stderr)

    def test_empty_stdin_is_rc_3_with_a_reason(self):
        done = self.select()
        self.assertEqual(done.returncode, 3, done.stdout)
        self.assertEqual(done.stdout, "")
        self.assertTrue(done.stderr.strip(), "rc 3 must say why")

    def test_base_prints_the_four_base_ids(self):
        done = adapter("select", "--mode", "base", env=self.env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), BASE_IDS)


class Fixture(unittest.TestCase):
    """A tmp code root with `tests/<name>.py`; `AC_ROOT` = the same tmp (no config → defaults)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.code = os.path.join(self.tmp, "code")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(os.path.join(self.code, "tests"))

    def put(self, name, text):
        with open(os.path.join(self.code, "tests", name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def env(self, **extra):
        return clean_env(AC_ROOT=self.code, AC_CODE_ROOT=self.code, AC_TEST_OUT=self.out,
                         AC_TEST_MODE="branch", **extra)

    def run_adapter(self, *args, env=None):
        return adapter(*args, env=env or self.env(), cwd=self.code)

    def result(self):
        with open(os.path.join(self.out, "result.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def has_result(self):
        return os.path.exists(os.path.join(self.out, "result.json"))


class RunWritesSchemaOne(Fixture):
    """A2: `run` / `full` write `result.json` schema 1."""

    def setUp(self):
        super().setUp()
        self.put("test_fx.py", FIXTURE)

    def test_run_of_the_five_ids(self):
        """**Mutations**: m1 (file/line from the outermost frame) → the `file` / `line` asserts;
        m5 (empty-reason skip reported as skip) → the `skip_empty` status assert."""
        done = self.run_adapter("run", *FIVE)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["command"], "run")
        self.assertEqual(data["ac_test_mode"], "branch")
        rows = {row["id"]: row for row in data["ids"]}
        self.assertEqual(set(rows), set(FIVE))
        self.assertEqual(len(data["ids"]), 5, "one row per id")
        self.assertEqual([rows[i]["status"] for i in FIVE],
                         ["pass", "fail", "error", "skip", "error"])
        fail = rows["test_fx.FX.test_fail"]
        self.assertEqual(fail["exception"], "AssertionError")
        self.assertEqual(fail["file"], "tests/test_fx.py")
        self.assertEqual(fail["line"], FAIL_LINE)
        self.assertTrue(fail["message"].startswith("AssertionError"), fail["message"])
        self.assertIsNotNone(fail["traceback"])
        broken = rows["test_fx.Broken.test_x"]
        self.assertEqual(broken["exception"], "RuntimeError")
        self.assertEqual(broken["file"], "tests/test_fx.py")
        self.assertEqual(rows["test_fx.FX.test_skip"]["message"], "fx reason")
        self.assertIn("without reason", rows["test_fx.FX.test_skip_empty"]["message"])
        self.assertIsNone(rows["test_fx.FX.test_pass"]["message"])
        self.assertEqual(data["environment_suspect"], [])
        self.assertEqual(data["log"], "run.log")
        with open(os.path.join(self.out, "run.log"), encoding="utf-8") as handle:
            self.assertIn("Ran 5 tests", handle.read())
        for key in ("started", "seconds"):
            self.assertIsNotNone(data[key], key)

    def test_full_finds_the_same_ids(self):
        done = self.run_adapter("full")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(data["command"], "full")
        self.assertEqual(data["log"], "full.log")
        self.assertEqual({row["id"] for row in data["ids"]}, set(FIVE))
        self.assertTrue(os.path.exists(os.path.join(self.out, "full.log")))

    def test_all_green_is_rc_0(self):
        done = self.run_adapter("run", "test_fx.FX.test_pass", "test_fx.FX.test_skip")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row["status"] for row in self.result()["ids"]], ["pass", "skip"])

    def test_zero_ids_is_rc_2_without_a_result(self):
        """**Mutation m3**: `run` with zero ids returns 0 → red."""
        done = self.run_adapter("run")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(self.has_result())

    def test_no_test_out_is_rc_2(self):
        env = self.env()
        env.pop("AC_TEST_OUT")
        for args in (("run", FIVE[0]), ("full",)):
            with self.subTest(args=args):
                done = self.run_adapter(*args, env=env)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertTrue(done.stderr.strip())
        self.assertFalse(self.has_result())


class EnvironmentSuspect(Fixture):
    """A3: rc 86 ⇔ `environment_suspect` non-empty (D-019 seven keys)."""

    def assert_rows(self, rows):
        self.assertIsInstance(rows, list)
        self.assertTrue(rows, "rc 86 never comes with environment_suspect []")
        for row in rows:
            self.assertEqual(sorted(row), sorted(SUSPECT_KEYS))

    def test_a_declared_line_is_rc_86_with_one_declared_row(self):
        self.put("test_declared.py", DECLARES_THEN_SKIPS)
        done = self.run_adapter("run", "test_declared")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        data = self.result()
        rows = data["environment_suspect"]
        self.assert_rows(rows)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "declared")
        self.assertEqual(rows[0]["engine"], "firefox")
        self.assertEqual(rows[0]["why"], "fake")
        self.assertEqual(rows[0]["line"], "ENVIRONMENT-SUSPECT: firefox fake")
        self.assertEqual([row["status"] for row in data["ids"]], ["skip"])

    def test_a_statistical_wave_is_rc_86_with_one_statistical_row(self):
        self.put("test_wave.py", WAVE)
        done = self.run_adapter("run", "test_wave")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        data = self.result()
        rows = data["environment_suspect"]
        self.assert_rows(rows)
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["source"], row["engine"], row["count"], row["threshold"]),
                         ("statistical", "safari", 8, 8))
        self.assertEqual(row["log"], os.path.join(self.out, "run.log"))
        self.assertEqual([r["id"] for r in data["ids"]], ["test_wave.T.test_wave"])
        self.assertEqual(data["ids"][0]["status"], "fail")
        with open(os.path.join(self.out, "run.log"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("AssertionError: localStorage id="), 8,
                             "fail-fast stops at the threshold (8), not after all 12")


if __name__ == "__main__":
    unittest.main()
