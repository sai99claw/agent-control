"""#162:`scripts/testiface.py` 與 `gate.sh` 跑在五個命令上(D-071 C6 §4、§8 V6–V9)。

沙盒是 commit 進來的 fixture 專案(`tests/fixtures/project/`,`fixture = True`):`tests.*` 指向
shell 寫的 command adapter(`scripts/ac-tests.sh`),`browser:*` id 交給假的引擎 runner
(`scripts/fake_browser.py`),unittest id 交給 A 的 adapter。閘門照真的跑
`$AC_HOME/scripts/gate.sh --branch --ticket 7`。

**期望值從 fixture 來,不從被測腳本算**:fixture 印的字(`select: no changed files`、
`screen locked (fixture preflight)`、`browser:safari:test_x`)在 fixture 裡寫死、這裡逐字比;
「跑了什麼」看 fixture 的 `run` 每次被叫時記的那一行(`AC_TEST_CALLS`),不看閘門自己說了什麼。
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, Sandbox, write_executable  # noqa: E402

# fixture 的 preflight 在 `AC_TEST_FAKE_PREFLIGHT=locked` 時印的那一筆(`scripts/ac-tests.sh`)。
LOCKED_ROW = {"source": "declared", "engine": "host", "why": "screen locked (fixture preflight)",
              "count": 1, "threshold": None, "log": None, "line": None}
BROWSER_ID = "browser:safari:test_x"


class FixtureGate(Sandbox):
    fixture = True

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "run-calls.log")
        self.make_ticket(7)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "票 #7")

    # ------------------------------------------------------------ 動作

    def set_conf(self, **keys):
        """改沙盒的 board/config.json 並 commit(沒 commit 的設定檔是一個改動檔,會被 select 挑到)。
        `tests_timeout_run=` 改 `tests.timeout_seconds.run`。"""
        conf = json.loads(self.read("board/config.json"))
        if "tests_timeout_run" in keys:
            conf["tests"].setdefault("timeout_seconds", {})["run"] = keys.pop("tests_timeout_run")
        conf.update(keys)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒設定")

    def gate(self, *extra, **env):
        if os.path.exists(self.calls):
            os.remove(self.calls)
        return self.run_sh("scripts/gate.sh", "--branch", "--ticket", "7", *extra,
                           env=self.env(AC_TEST_CALLS=self.calls, **env))

    def run_calls(self):
        """fixture 的 `run` 每被叫一次記一行:`run <argv>\\tmode=…\\tcode_root=…\\tout=…`。"""
        if not os.path.exists(self.calls):
            return []
        rows = []
        for line in self.read("run-calls.log", where=self.home).splitlines():
            head, *rest = line.split("\t")
            row = dict(part.split("=", 1) for part in rest)
            row["argv"] = head
            rows.append(row)
        return rows

    def browser_change(self):
        self.write(os.path.join("browser", "test_x.py"), "# 這一輪改到的引擎案例\n")


class A1SelectSemantics(FixtureGate):
    """A1 V6:`select` 的四種回答在閘門上各是什麼。"""

    def test_a1_empty_stdout_is_unmeasured_not_green(self):
        """**變異 m3**:testiface 收下 select 的空 stdout(rc 0)→ 這一條紅(閘門走「沒有東西
        可跑」或 0)。"""
        self.write(os.path.join("src", "x.py"), "x = 1\n")
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_SELECT="empty")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("select", self.status_of(7, kind="gate")["note"])
        self.assertEqual(self.run_calls(), [], "select 量不到卻跑了測試")

    def test_a1_unmapped_runs_what_mapped_then_refuses(self):
        """fixture 印 `test_ok` 與 `unmapped src/x.py`:對得到的照跑,跑完仍退 3。"""
        self.write(os.path.join("src", "x.py"), "x = 1\n")
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_SELECT="unmapped")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual([row["argv"] for row in self.run_calls()], ["run test_ok"])
        self.assertIn("gate:   src/x.py", done.stdout.splitlines())
        status = self.status_of(7, kind="gate")
        self.assertEqual(status["rc"], 3)
        self.assertEqual(self.load_ticket(7)["gate"]["rc"], 3)

    def test_a1_select_rc3_is_gate_rc3_with_the_projects_words(self):
        self.write(os.path.join("src", "x.py"), "x = 1\n")
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_SELECT="rc3")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("select: no changed files", done.stdout)
        self.assertEqual(self.run_calls(), [])

    def test_a1_normal_select_is_green(self):
        self.write(os.path.join("tests", "test_ok.py"),
                   self.read(os.path.join("tests", "test_ok.py")) + "# 這一輪的改動\n")
        done = self.gate("--no-auto-fix")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: run test_ok", done.stdout.splitlines())
        calls = self.run_calls()
        self.assertEqual([row["argv"] for row in calls], ["run test_ok"])
        self.assertEqual(calls[0]["mode"], "branch")
        self.assertEqual(os.path.realpath(calls[0]["code_root"]), os.path.realpath(self.repo))


class A2Preflight(FixtureGate):
    """A2 V7:`preflight` 回 86 → 環境可疑那一條路,測試一條都不跑。"""

    def with_fake_worker(self):
        self.marker = os.path.join(self.home, "auto-fix-ran")
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, "#!/bin/sh\necho ran >> %s\n" % self.marker)
        self.set_conf(worker={"command": "sh %s" % worker, "timeout_seconds": 60})

    def test_a2_preflight_86_is_env_suspect_with_the_declared_row(self):
        """**變異 m2**:gate 把 preflight 的 86 當紅(走 rc 1 / 跑測試)→ 這一條紅。
        **變異**(未量,開題者記):testiface 把 preflight 86 當紅(rc 1)→ 這一條紅。

        Runs with `--no-auto-fix` since #125 A4 (D-076): with auto-fix on, rc 86 now hands off to
        auto-fix (one re-run, then one diagnosis round), pinned by
        tests.test_gate.T125EnvSuspectHandsOffToAutoFix; `--no-auto-fix` keeps the page and no
        dispatch, which is what the last assertions here ask."""
        self.with_fake_worker()
        self.write(os.path.join("tests", "test_ok.py"),
                   self.read(os.path.join("tests", "test_ok.py")) + "# 改動\n")
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_PREFLIGHT="locked")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        status = self.status_of(7, kind="gate")
        self.assertEqual(status["state"], "env_suspect")
        self.assertEqual(status["environment_suspect"], [LOCKED_ROW])
        row = status["environment_suspect"][0]
        self.assertEqual(len(row), 7)
        self.assertEqual(row["source"], "declared")
        self.assertIn("locked", row["why"])
        self.assertFalse(os.path.exists(self.marker), "環境可疑的那一輪派了 worker")
        self.assertNotIn("gate: auto-fix ——", done.stdout)
        pages = [row for row in self.inbox_rows(ticket=7) if row.get("kind") == "decision"]
        self.assertEqual(len(pages), 1, self.inbox_rows())
        self.assertEqual(len(self.inbox_rows()), 1)
        self.assertEqual(self.run_calls(), [], "preflight 說環境可疑,卻跑了測試")

    def test_a2_a_broken_preflight_is_rc_2_and_runs_nothing(self):
        self.write(os.path.join("tests", "test_ok.py"),
                   self.read(os.path.join("tests", "test_ok.py")) + "# 改動\n")
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_PREFLIGHT="broken")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("preflight", done.stdout)
        self.assertEqual(self.run_calls(), [])
        self.assertNotEqual(self.status_of(7, kind="gate")["state"], "env_suspect")


class A3RunValidation(FixtureGate):
    """A3 V8:`run` 說綠不算數,result.json 驗過才算;量不到 = 2(§5.5)。"""

    def arm(self, name, **env):
        # 上一格寫了票的 gate 欄(`ticket_gate`):不還原的話票檔是這一格的改動檔,會被挑到 test_ticket。
        self.git("checkout", "-q", "--", "tickets/7.json")
        self.browser_change()
        return self.gate("--no-auto-fix", AC_TEST_FAKE_RUN=name, **env)

    def test_a3_answers_that_cannot_be_read_are_rc_2(self):
        """**變異 m1**:testiface 把「rc 86 而 rows 空」照 86 交回 → rc86-no-rows 那一格紅。"""
        for name, reason in (("zero-ids", "zero ids"), ("no-result", "result.json"),
                             ("rc86-no-rows", "environment_suspect"),
                             ("fail-rc0", "result.json")):
            with self.subTest(name):
                done = self.arm(name)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn(reason, done.stdout)
                self.assertEqual([row["argv"] for row in self.run_calls()],
                                 ["run %s" % BROWSER_ID], "run 被叫的次數不對(量不到不准重跑)")
                status = self.status_of(7, kind="gate")
                self.assertEqual(status["rc"], 2)
                self.assertNotEqual(status["state"], "env_suspect")

    def test_a3_a_real_red_is_rc_1_and_names_the_id(self):
        done = self.arm("fail")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        status = self.status_of(7, kind="gate")
        self.assertEqual(status["rc"], 1)
        self.assertEqual(status["failures"][0]["case"], BROWSER_ID)
        self.assertEqual(status["failures"][0]["excerpt"], "AssertionError: fixture browser red")

    def test_a3_a_timeout_is_unmeasured_not_red(self):
        """**變異 m5**:testiface 把逾時當紅(rc 1)→ 這一條紅。"""
        self.set_conf(tests_timeout_run=2)
        began = time.monotonic()
        done = self.arm("sleep")
        took = time.monotonic() - began
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertLess(took, 30, "逾時沒有在 testiface 那一層停下來")
        self.assertIn("timeout", done.stdout)
        self.assertEqual(self.status_of(7, kind="gate")["rc"], 2)


class A4FlakeRerunById(FixtureGate):
    """A4 V9:紅例照 id 交回 `run <id>` 單跑,不再拼 unittest 的指令。"""

    def setUp(self):
        super().setUp()
        self.set_conf(flake_auto_single_runs=3)
        self.browser_change()

    def test_a4_three_single_reruns_each_in_its_own_out_dir(self):
        """**變異 m4**:flake 重跑改回 shell 一行 unittest 指令 → 這一條紅(fixture 的 run 只被叫一次)。"""
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_RUN="flaky-then-pass",
                         AC_FLAKE_RERUN_GROUP="0")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        calls = self.run_calls()
        self.assertEqual([row["argv"] for row in calls], ["run %s" % BROWSER_ID] * 4)
        self.assertEqual(calls[0]["mode"], "branch")
        reruns = calls[1:]
        self.assertEqual([row["mode"] for row in reruns], ["rerun"] * 3)
        outs = [row["out"] for row in reruns]
        self.assertEqual(len(set(outs)), 3, outs)
        self.assertEqual([os.path.basename(out) for out in outs],
                         ["rerun-1", "rerun-2", "rerun-3"])
        status = self.status_of(7, kind="gate")
        rows = [row for row in status["failures"] if row["case"] == BROWSER_ID]
        self.assertEqual(len(rows), 1, status["failures"])
        self.assertIs(rows[0]["suspected_flaky"], True)
        self.assertEqual([row["case"] for row in status["suspected_flaky"]], [BROWSER_ID])

    def test_a4_the_group_rerun_is_run_with_all_ids_then_auto_flaky(self):
        done = self.gate("--no-auto-fix", AC_TEST_FAKE_RUN="flaky-then-pass")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.run_calls()
        self.assertEqual([row["argv"] for row in calls], ["run %s" % BROWSER_ID] * 5)
        self.assertEqual(os.path.basename(calls[-1]["out"]), "rerun-group-1")
        status = self.status_of(7, kind="gate")
        self.assertEqual(status["flaky"], "auto")
        self.assertEqual([row["case"] for row in status["auto_flaky"]], [BROWSER_ID])

    def test_a4_static_the_old_spellings_are_gone(self):
        def count(rel, needle, whole_line=False):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            return sum(1 for line in lines
                       if (line.startswith(needle) if whole_line else needle in line))
        self.assertEqual(count("scripts/gate.sh", "python3 -m unittest"), 0)
        self.assertEqual(count("scripts/status.py", "def cmd_run_tests"), 0)
        self.assertEqual(count("scripts/gate.sh", "map()", whole_line=True), 0)
        self.assertEqual(count("scripts/gate.sh", "test_sync_" "to_project"), 0)


class A5DogfoodAndTemplates(Sandbox):
    """A5:A 自己走同一個介面(內建版);兩份範本照抄得動。"""

    def a_testiface(self, *args, stdin=""):
        env = dict(self.env(), AC_ROOT=ROOT, AC_CODE_ROOT=ROOT,
                   AC_TEST_OUT=os.path.join(self.home, "out-%d" % len(os.listdir(self.home))))
        return subprocess.run(["python3", os.path.join(ROOT, "scripts", "testiface.py"), *args],
                              cwd=ROOT, env=env, input=stdin, capture_output=True, text=True,
                              timeout=120)

    def test_a5_as_base_set_is_the_four_contract_modules(self):
        done = self.a_testiface("select", "--mode", "base")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(done.stdout.split()),
                         ["test_event", "test_memory", "test_no_project_names", "test_ticket"])

    def test_a5_this_tickets_files_are_mapped_in_as_config(self):
        """map 列是設定資料(#161):這張票新增的檔都要有人守,不准走「對不到」。"""
        names = ["scripts/testiface.py", "tests/fixtures/project/scripts/ac-tests.sh",
                 "tests/fixtures/project/board/config.json",
                 "templates/project-tests.example.sh", "templates/project-config.example.json"]
        done = self.a_testiface("select", "--mode", "branch", stdin="\n".join(names) + "\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("unmapped", done.stdout)
        self.assertIn("test_testiface", done.stdout.split())

    def test_a5_actl_gate_base_prints_run_and_the_base_ids(self):
        """手搭沙盒的 `tests.unittest.base` 是 A 的那一份:`actl gate --base` 印 `gate: run` + 那四支。"""
        for name in ("test_ticket", "test_event", "test_memory", "test_no_project_names"):
            self.write(os.path.join("tests", "%s.py" % name),
                       "import unittest\n\n\nclass T(unittest.TestCase):\n"
                       "    def test_ok(self):\n        pass\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "基礎組四支")
        env = self.env()
        env.pop("AC_ROOT")
        done = subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), "gate", "--base"],
                              cwd=self.repo, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith("gate: run")]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertEqual(line[0].split()[:2], ["gate:", "run"])
        self.assertEqual(sorted(line[0].split()[2:]),
                         ["test_event", "test_memory", "test_no_project_names", "test_ticket"])

    def test_a5_the_two_templates(self):
        done = subprocess.run(["sh", "-n", os.path.join(ROOT, "templates",
                                                        "project-tests.example.sh")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(ROOT, "templates", "project-config.example.json"),
                  encoding="utf-8") as handle:
            conf = json.load(handle)
        self.assertEqual(conf["config_version"], 2)
        self.assertEqual(conf["tests"]["preflight"], "sh scripts/ac-tests.sh preflight")
        with tempfile.TemporaryDirectory() as where:
            os.makedirs(os.path.join(where, "board"))
            with open(os.path.join(where, "board", "config.json"), "w", encoding="utf-8") as handle:
                json.dump(conf, handle)
            done = subprocess.run(["python3", os.path.join(ROOT, "scripts", "project_config.py"),
                                   "check", where], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a5_the_fixture_project_passes_the_version_check(self):
        done = subprocess.run(["python3", os.path.join(ROOT, "scripts", "project_config.py"),
                               "check", os.path.join(ROOT, "tests", "fixtures", "project")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class TestifaceDirect(FixtureGate):
    """testiface 自己的幾條邊(不經閘門)。"""

    def ti(self, *args, stdin="", **env):
        extra = {"AC_CODE_ROOT": self.repo, "AC_TEST_OUT": os.path.join(self.home, "ti-out")}
        extra.update(env)
        return self.run_py("scripts/testiface.py", *args, stdin=stdin, env=self.env(**extra))

    def test_preflight_rows_land_in_the_suspect_file(self):
        suspect = os.path.join(self.home, "suspect.json")
        done = self.ti("--suspect-file", suspect, "preflight", AC_TEST_FAKE_PREFLIGHT="locked")
        self.assertEqual(done.returncode, 86, done.stdout + done.stderr)
        with open(suspect, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), [LOCKED_ROW])

    def test_a_non_empty_caller_out_dir_is_refused_not_cleaned(self):
        out = os.path.join(self.home, "ti-out")
        os.makedirs(out)
        keep = self.write("keep.txt", "the caller's\n", where=out)
        done = self.ti("run", "test_ok")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not empty", done.stderr)
        self.assertTrue(os.path.exists(keep))

    def test_run_without_ids_is_rc_2(self):
        done = self.ti("run")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_select_prints_the_ids_and_the_command_log_is_kept(self):
        done = self.ti("select", "--mode", "branch", stdin="browser/test_x.py\nsrc/x.c\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(), [BROWSER_ID, "unmapped src/x.c"])
        self.assertTrue(os.path.exists(os.path.join(self.home, "ti-out", "select.log")))

    def test_a_run_writes_its_log_and_a_validated_result(self):
        done = self.ti("run", "test_ok", BROWSER_ID)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = os.path.join(self.home, "ti-out")
        with open(os.path.join(out, "result.json"), encoding="utf-8") as handle:
            ids = [row["id"] for row in json.load(handle)["ids"]]
        self.assertEqual(ids, ["test_ok.T.test_ok", BROWSER_ID])
        with open(os.path.join(out, "run.log"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Ran 1 test", text, "adapter 自己寫的 log 被蓋掉了")
        self.assertIn("fake-browser: pass %s" % BROWSER_ID, text, "命令印的字沒接在 log 後面")


if __name__ == "__main__":
    unittest.main()
