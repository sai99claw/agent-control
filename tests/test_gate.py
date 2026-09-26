"""`scripts/gate.sh`:三層介面,以及「對不到任何模組」那一條。

#511(前一個專案,2026-09-10,第三次):對照表漏了一格時,舊版印一行「沒有對到任何
測試模組」然後**退出碼 0** —— 印一行、看起來像跑完了。三次缺口都是別張票順手撞到才
發現的。所以這一組釘的重點不是那幾格對照表,是**缺口會不會出聲**。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox, write_executable  # noqa: E402

PASSING = """import os
import unittest


class T(unittest.TestCase):
    def test_it_ran(self):
        with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
            handle.write("%s\\n")
"""
FAILING = """import unittest


class T(unittest.TestCase):
    def test_it_is_red(self):
        self.assertEqual(1, 2, "假的紅")
"""
# #620 的形狀:同一個引擎、12 條 subTest 倒在同一句,只差流水號與 id。
#
# 訊息用 `self.fail(…)` 的**單行**斷言訊息,不用 `assertEqual("home", "", msg)`:兩個
# 字串進 `assertEqual` 會走 `assertMultiLineEqual`,而它的 standardMsg 是**多行的
# ndiff**,那句話被擠到最後一行(實測 `str(err)` 是 `'home' != ''` 換行 `- home`
# 換行 ` : localStorage …`)。於是「原始訊息第一行」會變成 `AssertionError: 'home' != ''`
# —— 一句沒有帶到證據的話,而 #620 真實的紅是單行的斷言訊息
# (`docs/DESIGN-ENV-SUSPECT.md` 的 `line` 範例就是它)。同一句話、同一個引擎這兩個
# 條件一個字都沒放寬。
ENVIRONMENT_WAVE = """import os
import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index in range(12):
            with self.subTest(engine="safari", id=index):
                with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
                    handle.write("safari-%d\\n" % index)
                self.fail("localStorage id=%d empty after %d seconds"
                          % (index, index + 100))
"""
# 同一引擎但三句不同的紅:那是三個 bug,不是一次環境故障 —— 要照常跑完。
DIFFERENT_FAILURES = """import os
import unittest


class T(unittest.TestCase):
    def test_wave(self):
        for index, message in enumerate(("storage empty", "port occupied", "disk full")):
            with self.subTest(engine="safari", id=index):
                with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
                    handle.write("different-%d\\n" % index)
                self.fail(message)
"""


# ---------------------------------------- D-020:驗證者的案例(lint -> check)
# 乾淨基底上紅、這一輪的樹上綠的一條案例。**期望值 2 是票面說的**,不是跑一次記
# 下來的;lint 的六條(F1-F6)在這一份上全過 —— 它是 `verify/_template_ticket.py`
# 的形狀,`test_` 的 docstring 首行是驗收編號(F3)。
#
# 那個 docstring **是這一組的一部分**,不是裝飾:#31 之前 `unittest` 印在 `FAIL:`
# 標頭下面的那一行說明會把整段 traceback 擠出 `excerpt`,於是照 F3 寫的案例一律被
# 算成「紅在別處(不算紅)」—— lint 要求的形狀與分類器對打。這一份照 F3 寫,所以
# 那條路要是再壞掉,這裡會紅。
VERIFIER_CASE = '''"""#7 一句話:src/value.txt 的值由 1 變成 2。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 讀 src/value.txt | 檔裡的那個字 | 票面驗收第 1 條:2

## 介面字串
VALUE = "2"          ← A1 斷言檔裡的字等於它

## 怎麼做假
不上真埠、不起真服務、不殺行程:只讀工作目錄裡的一個檔。

## 不做
不改產品碼;不放寬票面驗收。
"""
import unittest

TAGS = ["example"]

VALUE = "2"


class TheValue(unittest.TestCase):

    def test_a1_the_value_is_two(self):
        """A1 src/value.txt 的值是 2。"""
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), VALUE)
'''

# 兩邊都綠的一條案例:lint 過得了,但它在乾淨基底上也不會紅 —— `check` 因此判
# `ok:false`(「一個永遠綠的案例與一個真的在驗的案例長得一樣」)。**那是驗證者那一趟
# 要證的事**,不是 worker 這一輪弄壞的。
ALWAYS_GREEN_CASE = '''"""#7 一句話:一條永遠綠的案例(沙盒用)。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 跑這一條 | 綠 | 沙盒 fixture:兩邊都綠

## 介面字串
(沒有;這一條不斷言任何字串)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬票面驗收。
"""
import unittest

TAGS = ["example"]


class NeverRed(unittest.TestCase):

    def test_a1_it_is_green_on_both_trees(self):
        """A1 這一條兩邊都綠。"""
        self.assertTrue(True)
'''

# 同一條斷言,但模組 docstring 的四段一段都沒有 —— lint 的 F1 擋它。
UNLINTED_CASE = """import unittest

TAGS = ["example"]


class TheValue(unittest.TestCase):
    def test_a1_the_value_is_two(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""


class GateSh(Sandbox):

    def setUp(self):
        super().setUp()
        # 沙盒裡放一組與真對照表同名的替身模組:跑起來只寫一行標記。
        # 換的是**代價**不是語意 —— 這一組問的是「哪幾支被挑到」,不是那幾支測什麼。
        for name in ("test_ticket", "test_event", "test_memory",
                     "test_no_project_names", "test_check_stale", "test_land"):
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的替身測試模組")

    def gate(self, *args):
        return self.run_sh("scripts/gate.sh", *args)

    def gate_log_path(self):
        """`gate.sh` 寫 log 的那一份檔 —— 沙盒用 `AC_GATE_LOG`(`control_harness`)。"""
        return os.path.join(self.home, "gate.log")

    def ran(self):
        if not os.path.exists(self.log):
            return []
        return [line.strip() for line in self.read("calls.log", where=self.home).splitlines()
                if line.strip()]

    # ------------------------------------------------------ 對照表挑得對不對

    def test_a_mapped_file_runs_the_modules_that_guard_it(self):
        done = self.gate("code-map/check-stale.py")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_check_stale"])

    def test_a_markdown_file_maps_to_the_guard_that_really_reads_it(self):
        """文件那一格不是硬塞的:那條「不准出現專案名 / 絕對路徑」的守衛掃的就是
        整個 repo,所以它真的讀 `*.md`。"""
        done = self.gate("docs/WORKFLOW.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_no_project_names"])

    def test_a_docs_html_file_maps_to_the_guard_that_really_reads_it(self):
        """`docs/FLOW.html` 與 `.md` 同一格(#29):`test_no_project_names` 逐字讀它
        (`test_real_flow_html_passes_the_project_name_check` 直接
        `read("docs/FLOW.html")`),而 `is_document()` 也把「`docs/` 前綴 + `.html`」
        列為文件級 —— 兩邊同一條判準。

        🩸 沒有這一格的時候,改 `docs/FLOW.html` 的那一輪走「對不到任何測試模組」退 3,
        而那條守衛**真的在守它**,只是對照表沒說:一個沒有人守的檔與一個沒被登記的檔
        長得一樣,而這是後者(#29 第 2 輪實測)。

        **變異**:把 `gate.sh` 對照表裡 `docs/*.html` 那一列拿掉 → 這一條紅(rc=3)。
        """
        done = self.gate("docs/FLOW.html")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_no_project_names"])
        self.assertNotIn("對不到任何測試模組", done.stdout)

    def test_a_nested_docs_html_is_covered_too(self):
        """守衛的判準是「`docs/` 前綴 + `.html`」,不分幾層;`case` 的 `*` 跨 `/`,
        所以對照表那一列一樣蓋得到 —— 兩邊不能只有一邊分層。"""
        done = self.gate("docs/review/20260913/report.html")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_no_project_names"])

    def test_the_session_hook_and_its_settings_map_to_test_session_hook(self):
        """#39 的兩個新檔各自對到 `test_session_hook`:它 A1 解析 `.claude/settings.json`、
        其餘案例跑 `scripts/session-hook.sh`。兩個檔分開叫,一列少了另一列補不了。

        **變異**:把對照表裡其中一個檔名拿掉 → 那一個 subTest 紅(rc=3)。
        """
        self.write(os.path.join("tests", "test_session_hook.py"),
                   PASSING % "test_session_hook")
        for path in ("scripts/session-hook.sh", ".claude/settings.json"):
            with self.subTest(path=path):
                if os.path.exists(self.log):
                    os.remove(self.log)
                done = self.gate(path)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.ran(), ["test_session_hook"])
                self.assertNotIn("對不到任何測試模組", done.stdout)

    def test_an_html_outside_docs_is_still_nobody_guarded(self):
        """**只有 `docs/` 底下的 `.html` 算**:守衛那一側把 `templates/x.html` 當
        程式級(`test_docs_html_and_ticket_json_are_document_level` 釘住這一條),
        所以對照表放寬到所有 `.html` 的那一刻,兩邊的判準就分岔了 —— 而分岔的那一份
        看起來仍然像規格。

        **變異**:把對照表那一列改成 `*.html` → 這一條紅。
        """
        done = self.gate("templates/x.html")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("templates/x.html", done.stdout)
        self.assertIn("對不到任何測試模組", done.stdout)

    def test_base_adds_the_contract_layer(self):
        done = self.gate("--base")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(self.ran()),
                         ["test_event", "test_memory", "test_no_project_names",
                          "test_ticket"])

    def test_branch_picks_up_what_this_branch_changed(self):
        self.write("scripts/land.sh", self.read("scripts/land.sh") + "\n# 動一下\n")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_land"])

    # ----------------------------------------------- 對不到任何模組:要出聲

    def test_a_file_nobody_guards_is_named_and_the_exit_code_is_not_zero(self):
        """**變異**:把 `if [ -n "$unmapped" ]` 那一段拿掉(退回印一行、退出 0)
        → 這一條紅。
        """
        done = self.gate("assets/logo.png")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("assets/logo.png", done.stdout, "沒有指名是哪個檔")
        self.assertIn("對不到任何測試模組", done.stdout)
        self.assertEqual(self.ran(), [], "什麼都沒跑")

    def test_a_mixed_change_runs_what_it_can_and_still_refuses(self):
        """混合的情況是判斷題:**跑掉的測試是真的證據,不該丟**;而那幾個沒人守的
        檔要當場處理,所以退出碼仍然非零。"""
        done = self.gate("code-map/check-stale.py", "assets/logo.png")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_check_stale"], "對得到的那一支要照跑")
        self.assertIn("assets/logo.png", done.stdout)

    # ------------------------------------------------------------- 介面本身

    def test_full_runs_the_whole_discover_and_is_green(self):
        done = self.gate("--full")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ran ", done.stdout)
        self.assertIn("OK", done.stdout)

    def test_full_is_red_when_a_test_is_red(self):
        """判綠先寫檔再讀退出碼,不用 `cmd | tail` —— 管線的退出碼是右邊那一支的。

        **變異**:把 `--full` 那一段改成 `python3 -m unittest … | tail -5` 並回
        `$?` → 這一條紅(它會判成綠)。
        """
        self.write("tests/test_zz_red.py", FAILING)
        done = self.gate("--full")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("FAILED", done.stdout)
        self.assertIn("紅了", done.stdout)

    # ------------------------------------------------- 環境壞了要當場停(#7)

    def test_same_safari_failure_shape_stops_the_segment_and_alerts(self):
        """**變異**:把 `EnvironmentResult._observe` 裡的 `self.failfast = True`
        拿掉 → 這一條紅(12 != 8)。

        理由:`shouldStop` 停的是下一條測試方法,而這 12 條 subTest 跑在同一個方法
        裡面 —— 只設它,那一段還是會整組跑完,而「跑完再說」就是這張票要擋的事。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("safari-")]), 8,
                         "預設門檻 8 到了就要停,不准把 12 條跑完")
        data = self.status_of("7")
        self.assertEqual(data["state"], "env_suspect")
        # D-019:這一格**永遠是 list**,每一筆七個鍵一個都不缺(形狀的真實來源是
        # `docs/DESIGN-ENV-SUSPECT.md`)。**變異**:`_observe` 改回寫單筆 dict
        # → 下面第一條 `assertIsInstance(list)` 紅。
        rows = data["environment_suspect"]
        self.assertIsInstance(rows, list, "這一格是 list,不是 dict")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(sorted(row),
                         sorted(["source", "engine", "why", "count",
                                 "threshold", "log", "line"]),
                         "七個鍵要一個都不缺:%r" % sorted(row))
        self.assertEqual(row["source"], "statistical")
        self.assertEqual(row["engine"], "safari")
        self.assertEqual(row["count"], 8)
        self.assertEqual(row["threshold"], 8)
        # `why` 是正規化過的形狀(數字都被換掉了),`line` 是原始那一句 ——
        # 兩格揉成一格的那一刻,「同一句話」與「那台機器當時說了什麼」少掉一邊。
        self.assertNotRegex(row["why"], r"\d", "why 沒有正規化過:%r" % row["why"])
        self.assertRegex(row["line"], r"\d+ seconds",
                         "line 要是未正規化的原字:%r" % row["line"])
        self.assertTrue(row["line"].startswith("AssertionError: "),
                        "line 是 log 上那一行的原樣:%r" % row["line"])
        self.assertEqual(row["log"], self.gate_log_path(),
                         "log 那一格要指得出證據住在哪一份檔")
        self.assertIn("env.suspect", self.kinds())
        pages = [name for name in os.listdir(os.path.join(self.repo, "reports", "inbox"))
                 if name.endswith(".md")]
        page = self.read(os.path.join("reports", "inbox", pages[0]))
        self.assertIn("Safari --automation", page)
        self.assertIn("引擎:safari", page)
        self.assertIn("同形訊息:", page)

    def test_the_aborted_segment_does_not_pay_for_flake_reruns(self):
        """環境 fail-fast 在 flake 判定**之前**:被中止的紅榜不完整,而在壞掉的環境
        裡每條紅例單跑 N 次只會把浪費加倍。所以這一段不准印出 flake 那幾句,狀態檔
        也不准留下 flaky 的判定。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("單獨重跑", done.stdout)
        self.assertNotIn("整組重跑", done.stdout)
        data = self.status_of("7")
        self.assertEqual(data["suspected_flaky"], [])
        self.assertEqual(data["auto_flaky"], [])
        self.assertEqual(data["flaky"], "")
        self.assertNotIn("flake.auto_pass", self.kinds())

    def test_the_threshold_comes_from_board_config(self):
        """**變異**:把門檻改成讀死的常數、不讀 `board/config.json` → 這一條紅
        (停在 8 而不是 3)。N 住在設定檔裡才調得動:不同專案的環境壞法不一樣。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        config = json.loads(self.read("board/config.json"))
        config["environment_fail_fast_threshold"] = 3
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒的環境門檻")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("safari-")]), 3,
                         "設定檔說 3 就停在 3")
        data = self.status_of("7")
        self.assertEqual(data["state"], "env_suspect")
        self.assertEqual(data["environment_suspect"][0]["threshold"], 3)
        self.assertEqual(data["environment_suspect"][0]["count"], 3)

    def test_different_messages_do_not_trigger_environment_fail_fast(self):
        """**變異**:把形狀的鍵改成只看引擎、不看訊息 → 這一條紅(state 變
        `env_suspect`)。三條不同訊息是三個 bug,把它們算成一次環境故障等於**把真的
        紅榜丟掉**。"""
        self.write("tests/test_env_wave.py", DIFFERENT_FAILURES)
        config = json.loads(self.read("board/config.json"))
        config["environment_fail_fast_threshold"] = 3
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒的環境門檻")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.run_sh("scripts/gate.sh", "tests/test_env_wave.py", "--ticket", "7",
                           "--no-auto-fix", env=self.env(AC_NO_FLAKE_RERUN="1"))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("different-")]), 3,
                         "三條不同訊息要照常跑完")
        self.assertEqual(self.status_of("7")["state"], "done")
        self.assertNotIn("env.suspect", self.kinds())

    def test_an_environment_suspect_round_does_not_dispatch_a_worker(self):
        """驗收 8(D-019):**這一格非空 → 不自動派**。

        `gate.sh` 舊版對任何 rc≠0 都叫 `auto_fix`,而 `auto_fix()` 沒看這一格 ——
        rc=86 那一輪照樣送一個 worker 進一台「現在跑不動」的機器(#7 的案例帶著
        `--no-auto-fix` 跑,所以沒抓到)。

        **變異**:把 `auto_fix` 開頭那一行守衛拿掉 → 這一條紅(`gate: auto-fix ——`
        那一行會出現,假 worker 會被叫)。守衛**寫反邊**(非空才派)時,其他驗收
        全綠,只有這一條紅。
        """
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        # 假 worker:被叫到就寫一行。**換的是代價不是語意** —— 這一條問的是
        # 「它到底有沒有被叫到」,而那正好因此變成一個看得見的事實。
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, "#!/bin/sh\n"
                                 "echo fake-worker-ran >> \"$AC_TEST_LOG\"\n")
        config = json.loads(self.read("board/config.json"))
        config["worker"] = {"command": "sh %s" % worker, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的假 worker")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("環境可疑,不自動派", done.stdout)
        # **釘住這一條的是下面兩句,不是最後那一句。** 實測:把守衛拿掉,`auto-fix.sh`
        # 真的被叫起來(stdout 出現這兩行),但那支腳本在這個沙盒裡會因為自己的前置
        # (票沒 commit、沒有第一輪的 patch)先停下來,所以**假 worker 兩邊都沒被叫** ——
        # 只斷言「worker 沒被叫」的那一版,守衛在不在都是綠的(§5.5 的母題)。
        # 最後那一句留著是因為它是驗收的字面,而它現在是**第二道**而不是唯一那道。
        self.assertNotIn("gate: auto-fix ——", done.stdout,
                         "連 auto-fix.sh 都不該被叫起來")
        self.assertNotIn("auto-fix: #7", done.stdout,
                         "auto-fix.sh 已經開始讀上一輪了")
        self.assertEqual([row for row in self.ran() if row == "fake-worker-ran"], [],
                         "環境可疑的那一輪派了 worker")
        self.assertEqual(self.status_of("7")["state"], "env_suspect")

    def test_a_plain_red_round_still_dispatches(self):
        """守衛不准把**所有**紅都攔下來:那一版與「auto-fix 壞了」長得一樣。

        **變異**:把守衛的條件寫成「永遠不派」→ 這一條紅。
        """
        self.write("tests/test_zz_red.py", FAILING)
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, "#!/bin/sh\n"
                                 "echo fake-worker-ran >> \"$AC_TEST_LOG\"\n")
        config = json.loads(self.read("board/config.json"))
        config["worker"] = {"command": "sh %s" % worker, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的假 worker")
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_zz_red.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("環境可疑,不自動派", done.stdout)
        self.assertIn("gate: auto-fix ——", done.stdout)
        self.assertEqual(self.status_of("7")["environment_suspect"], [])

    def test_an_unknown_flag_is_refused(self):
        done = self.gate("--quick")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("不認得", done.stderr)

    def test_no_arguments_at_all_is_a_usage_error(self):
        done = self.gate()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)


class VerifierCasesAtTheGate(Sandbox):
    """`gate.sh --ticket <票號>` 在回歸層之後多跑的兩步(D-020 C5)。

    在這一層接上之前,「候選該綠」這一半**沒有任何腳本自動量**:`verify-case.py check`
    存在(#22),而唯一被要求跑它的人(驗證者)在時間上拿不到實作者的 patch。於是
    驗證者只剩一條路 —— 自己搭一份拋棄式參考實作,那是 #23 那 340K 的來源。

    所以這一組問的不是 `verify-case.py` 怎麼分類紅(那是 #26 / `test_verify_case`),
    是**閘門到底有沒有去叫它、紅了有沒有進紅榜**。
    """

    def setUp(self):
        super().setUp()
        # 對得到對照表、而且會綠的一支替身模組:這一組問的不是單元層。
        self.write(os.path.join("tests", "test_land.py"), PASSING % "test_land")
        self.write(os.path.join("src", "value.txt"), "1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:主線上的值是 1")

    def a_ticket(self, case=VERIFIER_CASE, **fields):
        """一張指著一份驗證者案例的票。

        `verify.tags` 留空是**故意的**:回歸層因此只印一行就回來,這一組量到的紅與
        綠就只會來自驗證者那一層 —— 兩層混在同一個 rc 裡的話,紅榜指誰說不清楚。
        """
        self.write(os.path.join("verify", "nav", "__init__.py"), "")
        self.write(os.path.join("verify", "nav", "test_ticket_7.py"), case)
        fields.setdefault("verify", {"files": ["verify/nav/test_ticket_7.py"],
                                     "tags": [], "run": "", "notes": ""})
        return self.make_ticket(7, allowed_write_paths=["verify/*", "src/*", "tests/*"],
                                **fields)

    def implement(self):
        """實作者這一輪的改動:值變成 2(還沒 commit —— 閘門問的是現在手上這一份)。"""
        self.write(os.path.join("src", "value.txt"), "2\n")

    def gate(self, *extra):
        return self.run_sh("scripts/gate.sh", "tests/test_land.py", "--ticket", "7",
                           "--no-auto-fix", *extra)

    # --------------------------------------------- 沒有案例 = 缺口,不是綠

    def test_a_declared_but_empty_verify_files_is_a_gap_not_a_green(self):
        """驗收 1(同 #619 的精神)。

        **變異**:把 `verify_case` 裡 `CRC=3` 那一行拿掉(退回只印一行)→ 這一條紅,
        因為退出碼變成 0 —— 而「沒有人驗過」與「驗過了都過」正是在那裡長得一樣。
        """
        self.make_ticket(7, allowed_write_paths=["tests/*", "src/*"],
                         verify={"files": [], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("這張票沒有驗證者案例", done.stdout)
        self.assertIn("verify_waiver", done.stdout, "下一步要寫在裡面")

    def test_a_waiver_excuses_the_ticket_from_this_layer(self):
        """驗收 1 的另一半:**有 waiver 就不是缺口**。守衛擋下所有沒有案例的票的
        那一版,與 `verify_waiver` 這一格不存在長得一樣。

        waiver 免的是**「有沒有案例」那一格**,不是整層:`verify.files` 非空時 lint 與
        check 照跑(票面驗收 2、3 對這兩步沒有寫例外)。第 2 輪把它寫成短路整層,於是
        帶 waiver 的票再也不會被 lint —— 驗證者的 A2–A6 就是量到那件事(#27 第 3 輪)。

        **變異**:把 `waiver)` 那一格拿掉 → 前半紅(rc 變 3);把它從 `[ -z "$files" ]`
        裡面搬到外面(退回第 2 輪)→ 後半紅(lint 不會被叫到)。
        """
        self.make_ticket(7, allowed_write_paths=["tests/*", "src/*"],
                         verify={"files": [], "tags": [], "run": "", "notes": ""},
                         verify_waiver={"by": "main", "reason": "純文字,沒有邏輯可紅"})
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verify_waiver", done.stdout)
        self.assertNotIn("這張票沒有驗證者案例", done.stdout)

        # 同一張票,這次**有**案例檔(而且是一份 lint 過不了的):waiver 不免 lint。
        self.a_ticket(UNLINTED_CASE,
                      verify_waiver={"by": "main", "reason": "純文字,沒有邏輯可紅"})
        done = self.gate()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verify-case lint:", done.stdout,
                      "有 waiver 的票,案例檔還是要過 lint")

    def test_a_ticket_that_never_declared_verify_is_not_a_gap(self):
        """**沒有宣告**與**宣告了卻沒有案例**是兩件事,下一步也不同。

        同一支腳本對回歸層就是這樣分的:`regression()` 對「票沒有宣告 verify.tags」
        出聲不擋,對「宣告了 tags 卻一個案例都選不到」才非零(#619)。`needs_verifier`
        那一格則是與 `land.sh` 對齊(#8)—— 兩支對同一張票不該給出不同的答案。

        **變異**:把 `undeclared)` 那一格拿掉 → 後半紅(rc 變 3);把
        `needs-verifier-false)` 那一格拿掉 → 前半紅。
        """
        self.make_ticket(7, allowed_write_paths=["tests/*", "src/*"],
                         needs_verifier=False,
                         verify={"files": [], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("needs_verifier=false", done.stdout)

        # 票上連 `verify` 這一格都沒有(沙盒手寫票的形狀)。
        self.make_ticket(7, allowed_write_paths=["tests/*", "src/*"])
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("連 verify 這一格都沒有宣告", done.stdout)
        self.assertNotIn("這張票沒有驗證者案例", done.stdout)

    # ------------------------------------------------- lint 紅了就停在這裡

    def test_a_case_that_fails_lint_stops_the_gate_and_names_the_line(self):
        """驗收 2:lint 的 rc 非零時**不繼續跑 check**。

        格式不合的案例跑出來的紅指向的是格式,不是實作 —— 讓它往下跑,下一輪的
        worker 會去修一個沒有壞掉的東西。

        **變異**:把 `if [ "$lrc" -ne 0 ]` 那一段拿掉 → 這一條紅(check 的那一行
        會出現在 stdout 裡)。
        """
        self.a_ticket(UNLINTED_CASE)
        self.implement()
        done = self.gate()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verify/nav/test_ticket_7.py:1", done.stdout, "沒有指名是哪一行")
        self.assertIn("F1", done.stdout)
        self.assertIn("不跑 check", done.stdout)
        self.assertNotIn("驗證者案例的綠", done.stdout, "lint 紅了還往下跑 check")
        self.assertNotIn("baseline", self.load_ticket("7")["verify"],
                         "lint 紅了卻仍然在票上寫了一格判決")

    # --------------------------------------------- lint 過了才量「候選該綠」

    def test_lint_passing_lets_check_run_and_lifts_the_stage(self):
        """驗收 3 與 4:`stage` 由 `red` 升成 `check`(`ticket.py close` 只認它)。

        **變異**:把 `verify_case` 裡 check 那一段拿掉 → 這一條紅(票上沒有
        `verify.baseline`)。
        """
        self.a_ticket()
        self.implement()
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verify-case lint: 過", done.stdout)
        self.assertIn("驗證者案例的綠", done.stdout)
        baseline = self.load_ticket("7")["verify"]["baseline"]
        self.assertEqual(baseline["stage"], "check",
                         "閘門量完了,stage 還停在驗證者那一段")
        self.assertTrue(baseline["ok"], baseline["why"])
        self.assertEqual(baseline["candidate_run"]["red"], [])
        self.assertEqual(baseline["baseline"]["red"],
                         ["verify.nav.test_ticket_7.TheValue.test_a1_the_value_is_two"],
                         "乾淨基底上要紅在案例檔自己的斷言")

    def test_check_runs_after_the_regression_layer_not_at_land_time(self):
        """驗收 6:**回歸層之後**。

        量在 land 等於紅了才發現,而 worker 的三輪修復迴圈住在閘門 —— 那是多一整輪
        的事。**變異**:把 `verify_case` 的呼叫搬到 `regression ticket` 前面 →
        這一條紅。
        """
        self.a_ticket()
        self.implement()
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertLess(done.stdout.index("沒有宣告 verify.tags"),
                        done.stdout.index("驗證者案例的綠"),
                        "驗證者那一層跑在回歸層前面")

    # ------------------------------------- 候選上紅 = 閘門紅,紅榜要指得到人

    def test_a_verifier_case_that_stays_red_puts_the_red_list_in_the_status_file(self):
        """驗收 5:`ok:false` 的紅榜進 `status.json` 的 `failures[]`。

        沒有 `implement()` —— 值還是 1,驗證者的那一條在這一輪的樹上仍然紅。

        **變異**:把 `CHECK_LOG_ARGS="--log …candidate.log"` 那一行拿掉 → 這一條紅
        (rc 仍然非零,但 `failures[]` 是空的,而下一輪的 worker 讀的正是那一格 ——
        一個沒有案例名字的紅榜與沒有紅榜一樣)。
        """
        self.a_ticket()
        done = self.gate()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("紅榜進狀態檔", done.stdout)
        self.assertFalse(self.load_ticket("7")["verify"]["baseline"]["ok"])
        data = self.status_of("7")
        self.assertIn("verify.nav.test_ticket_7.TheValue.test_a1_the_value_is_two",
                      [row["case"] for row in data["failures"]],
                      "紅榜裡沒有那一條驗證者案例:%r" % data["failures"])

    def test_a_case_that_never_turns_red_is_not_this_rounds_red(self):
        """`ok:false` 但**候選樹上一條都沒紅**:baseline 不成立的理由在另一邊(乾淨
        基底上沒紅)。那是驗證者那一趟(`stage=red`)要證的事,worker 這一輪修不動 ——
        把它算成這一輪的紅,下一輪的人會去修一個沒有壞掉的東西(D-014 §紅了誰修)。

        **不是把它吞掉**:票上那一格仍然是 `ok:false`(下面第三句),`ticket.py close`
        讀的就是它;閘門也印了一行、寫進狀態檔的 note。

        **變異**:把 `[ "$bad" = "0" ]` 那一段拿掉(退回「rc 非零就是這一輪的紅」)
        → 這一條紅(rc 變 1)。
        """
        self.a_ticket(ALWAYS_GREEN_CASE)
        self.implement()
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("不算這一輪的紅", done.stdout)
        baseline = self.load_ticket("7")["verify"]["baseline"]
        self.assertFalse(baseline["ok"], "票上那一格被寫成綠了 —— close 就擋不住了")
        self.assertEqual(baseline["stage"], "check")
        self.assertEqual(self.status_of("7")["failures"], [],
                         "候選全綠卻留了一份紅榜給下一輪的 worker")

    def test_a_red_verifier_case_goes_down_the_existing_auto_fix_path(self):
        """驗收 5 的另一半:走的是**既有**那條路(D-014),不是新造一條。

        **變異**:把 `rc=$(merge_rc "$rc" "$CRC")` 那一行拿掉 → 這一條紅
        (rc 是 0,`auto_fix` 第一行就回去了)。
        """
        self.a_ticket()
        done = self.run_sh("scripts/gate.sh", "tests/test_land.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("gate: auto-fix ——", done.stdout)


class GateExample(unittest.TestCase):
    """範例那一份要**自己的語法是對的** —— 一份 `sh -n` 過不了的範本,照抄的人第一
    件事是修語法,不是讀理由。"""

    def test_the_example_parses(self):
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        done = subprocess.run(["sh", "-n", os.path.join(here, "scripts", "gate.example.sh")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_example_keeps_the_three_layer_interface(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "scripts", "gate.example.sh"), encoding="utf-8") as handle:
            text = handle.read()
        for flag in ("--branch", "--base", "--full"):
            self.assertIn(flag, text)
        self.assertIn("對不到任何測試模組", text)

    def test_the_example_accepts_the_ticket_flag(self):
        """**變異**:把範本的 `--ticket` 那一格拿掉 → 這一條紅。

        2026-09-21 外部審查:範本不接受 `--ticket`,照抄的人拿不到狀態檔與票的回歸,
        而他的 gate 收到 `--ticket 7` 只會回「不認得」+ 退出碼 2 —— 而 2 與「紅了」
        在呼叫者眼裡長得很像。
        """
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "scripts", "gate.example.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("--ticket", text)
        self.assertIn("status.py", text, "範本要示範狀態檔怎麼寫")
        self.assertIn("verify.py", text, "範本要示範票的回歸怎麼跑")


if __name__ == "__main__":
    unittest.main()
