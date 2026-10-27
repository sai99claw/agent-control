"""`scripts/gate.sh`:三層介面,以及「對不到任何模組」那一條。

#511(前一個專案,2026-09-10,第三次):對照表漏了一格時,舊版印一行「沒有對到任何
測試模組」然後**退出碼 0** —— 印一行、看起來像跑完了。三次缺口都是別張票順手撞到才
發現的。所以這一組釘的重點不是那幾格對照表,是**缺口會不會出聲**。
"""

import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import REVIEWER_PASS, ROOT, VERIFY_CASE, Sandbox, write_executable  # noqa: E402

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


# ---------------------------------------- D-020:驗證者的案例(check)
# 乾淨基底上紅、這一輪的樹上綠的一條案例。**期望值 2 是票面說的**,不是跑一次記
# 下來的;它是 `verify/_template_ticket.py` 的形狀,`test_` 的 docstring 首行是驗收編號。
#
# 那個 docstring **是這一組的一部分**,不是裝飾:#31 之前 `unittest` 印在 `FAIL:`
# 標頭下面的那一行說明會把整段 traceback 擠出 `excerpt`,於是照 F3 寫的案例一律被
# 算成「紅在別處(不算紅)」—— 範本要求的形狀與分類器對打。這一份照範本寫,所以
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

# 兩邊都綠的一條案例:它在乾淨基底上也不會紅 —— `check` 因此判
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

class GateSh(Sandbox):

    def setUp(self):
        super().setUp()
        # 沙盒裡放一組與真對照表同名的替身模組:跑起來只寫一行標記。
        # 換的是**代價**不是語意 —— 這一組問的是「哪幾支被挑到」,不是那幾支測什麼。
        for name in ("test_ticket", "test_event", "test_memory",
                     "test_no_project_names", "test_heartbeat", "test_land"):
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)
        # #74:缺角色卡、指定的模型卡不在、或只剩標題的規則包現在非零,而且不起 agent。
        # 預設沙盒沒有那幾份(`control_harness` 只給要的測試裝),不裝的話每一條派工的案例
        # 都停在「規則包產不出來」。假 reviewer 的模型(`set_reviewer` 的 fixture-model)
        # 也要有一張卡 —— 指定了卻不存在是錯,不是「沒有記憶」。
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
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
        done = self.gate("scripts/heartbeat.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_heartbeat"])

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
        self.write("scripts/land.sh", "# 動一下\n")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_land"])

    def test_a_deleted_test_module_is_not_run(self):
        """#71 A3:這條分支刪掉的 `tests/test_*.py` 不排進模組清單 —— unittest 對
        不存在的模組會報錯,閘門就紅在自己排錯的清單上。
        **變異**:把 `map()` 的 `[ -f "$ROOT/$f" ]` 守衛拿掉 → 這一條紅。"""
        self.write("tests/test_x.py", PASSING % "test_x")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test_x 先在主線")
        self.git("checkout", "-q", "-b", "t71")
        self.git("rm", "-q", "tests/test_x.py")
        self.write("scripts/land.sh", "# 動一下\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "刪掉 test_x")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith("gate: run")]
        self.assertEqual(line, ["gate: run test_land"])
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
        done = self.gate("scripts/heartbeat.sh", "assets/logo.png")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(self.ran(), ["test_heartbeat"], "對得到的那一支要照跑")
        self.assertIn("assets/logo.png", done.stdout)

    # ------------------ 對照表沒列、但同票改了 tests:推給那幾支守(#45 B2)

    def test_an_unmapped_file_with_a_green_test_in_the_diff_is_guarded_by_it(self):
        """**變異 M2**:把推導那一手拿掉 → 這一條紅(rc 3)。
        **變異 M3**:推得到也退 3 → 這一條紅。"""
        self.write("scripts/zzz.sh", "echo zzz\n")
        self.write("tests/test_zzz.py", PASSING % "test_zzz")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("依同票 tests 改動推到", done.stdout)
        self.assertIn("scripts/zzz.sh", done.stdout)
        self.assertNotIn("對不到任何測試模組", done.stdout)
        self.assertEqual(self.ran(), ["test_zzz"])

    def test_an_unmapped_file_with_a_red_test_in_the_diff_is_red_by_the_test(self):
        """推得的守衛紅了,rc 是測試的紅,不是 3;狀態檔 NOTE 記下推得的對照。"""
        self.make_ticket(7)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "票 #7")
        self.write("scripts/zzz.sh", "echo zzz\n")
        self.write("tests/test_zzz.py", FAILING)
        done = self.gate("--branch", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("FAILED", done.stdout)
        self.assertIn("依同票 tests 改動推到", done.stdout)
        self.assertIn("推得的對照:scripts/zzz.sh→test_zzz",
                      self.status_of(7, kind="gate").get("note", ""))

    def test_an_unmapped_file_with_no_test_in_the_diff_still_refuses(self):
        """推不到就照舊:退 3、逐檔列出。**變異 M3** 下這一條仍綠。"""
        self.write("scripts/zzz.sh", "echo zzz\n")
        done = self.gate("--branch")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("gate:   scripts/zzz.sh", done.stdout.splitlines())
        self.assertIn("對不到任何測試模組", done.stdout)
        self.assertNotIn("依同票 tests 改動推到", done.stdout)

    # ------------------------------------------------------------- 介面本身

    def test_full_runs_the_whole_discover_and_is_green(self):
        done = self.gate("--full")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ran ", done.stdout)
        self.assertIn("OK", done.stdout)

    def test_full_is_red_when_a_test_is_red(self):
        """判綠先寫檔再讀退出碼,不用 `cmd | tail` —— 管線的退出碼是右邊那一支的。

        **變異**:把 `--full` 那一段的 `run_tests full full` 改成 `run_tests full full | tail -5`
        並回 `$?` → 這一條紅(它會判成綠)。
        """
        self.write("tests/test_zz_red.py", FAILING)
        done = self.gate("--full")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("FAILED", done.stdout)
        self.assertIn("紅了", done.stdout)

    def test_full_writes_one_status_per_ticket_of_a_batch(self):
        """#109 B5:`AC_GATE_TICKETS="1 2"` 跑 --full 紅 → 每張票各一份 kind=gate 的狀態檔,
        同 run_id、failures 相同(`auto-fix.sh --batch` 讀第一張,看板按票看)。

        **變異 ③**:只寫第一張票的狀態檔 → 這一條紅。"""
        self.write("tests/test_zz_red.py", FAILING)
        done = self.run_sh("scripts/gate.sh", "--full", "--no-auto-fix",
                           env=self.env(AC_GATE_TICKETS="1 2"))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        one, two = self.status_of(1, kind="gate"), self.status_of(2, kind="gate")
        self.assertEqual(one["run_id"], two["run_id"])
        self.assertTrue(one["failures"], "紅榜不該是空的")
        self.assertEqual(one["failures"], two["failures"])
        self.assertIn("test_zz_red", json.dumps(one["failures"]))

    # ------------------------------------------------- 環境壞了要當場停(#7)

    def test_same_safari_failure_shape_stops_the_segment_and_alerts(self):
        """**變異**:把 `EnvironmentResult._observe` 裡的 `self.failfast = True`
        拿掉 → 這一條紅(12 != 8)。

        理由:`shouldStop` 停的是下一條測試方法,而這 12 條 subTest 跑在同一個方法
        裡面 —— 只設它,那一段還是會整組跑完,而「跑完再說」就是這張票要擋的事。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7)
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
        # 不可歸因、不自動派 → 要人動手:恰 1 頁 kind=decision(D-032)。
        rows = self.inbox_rows()
        self.assertEqual([row["kind"] for row in rows], ["decision"])
        page = self.read(rows[0]["page"])
        self.assertIn("Safari --automation", page)
        self.assertIn("引擎:safari", page)
        self.assertIn("同形訊息:", page)

    def test_the_aborted_segment_does_not_pay_for_flake_reruns(self):
        """環境 fail-fast 在 flake 判定**之前**:被中止的紅榜不完整,而在壞掉的環境
        裡每條紅例單跑 N 次只會把浪費加倍。所以這一段不准印出 flake 那幾句,狀態檔
        也不准留下 flaky 的判定。"""
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.make_ticket(7)
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
        self.make_ticket(7)
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
        self.make_ticket(7)
        done = self.run_sh("scripts/gate.sh", "tests/test_env_wave.py", "--ticket", "7",
                           "--no-auto-fix", env=self.env(AC_NO_FLAKE_RERUN="1"))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len([row for row in self.ran() if row.startswith("different-")]), 3,
                         "三條不同訊息要照常跑完")
        self.assertEqual(self.status_of("7")["state"], "done")
        self.assertNotIn("env.suspect", self.kinds())

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
        self.make_ticket(7)
        done = self.gate("tests/test_zz_red.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("環境可疑,不自動派", done.stdout)
        self.assertIn("gate: auto-fix ——", done.stdout)
        self.assertEqual(self.status_of("7")["environment_suspect"], [])

    def test_a_hand_run_green_branch_turns_the_ticket_in_review_and_calls_review_sh(self):
        """#42 A3(b):手跑 `--branch --ticket <n>` 綠 → 票轉 InReview、`review.sh` 被叫。
        以前手跑綠沒有人轉 InReview:票停在 Running,與「還在修」長得一樣。

        **變異 M3**:拿掉 gate.sh 結尾那一手 `review_after_green` → 這一條紅。
        只有 complex 的票派覆核(#136),所以夾具標 complex。
        """
        model = self.set_reviewer(REVIEWER_PASS)
        self.make_ticket(7, state="Running",
                         complexity="complex")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的假 reviewer 與票 #7")
        self.git("branch", "t7")
        self.write("tests/test_zz_green.py", PASSING % "test_zz_green")
        done = self.gate("--branch", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("reviewer ran 7", self.ran(), "review.sh 沒被叫(或沒起 reviewer)")
        ticket = self.load_ticket("7")
        self.assertEqual(ticket["state"], "InReview")
        self.assertEqual((ticket.get("review") or {}).get("by"), "reviewer@" + model)

    def test_a_hand_run_green_simple_or_unmarked_ticket_is_not_reviewed(self):
        """#136(VISION 流程 #9:只有複雜的票做完後檢查一次;沒標 = simple):手跑綠 → 票轉
        InReview 等 land,不叫 review.sh、不起 reviewer。

        量 rc、票 state 與 review、reviewer 有沒有被叫、review.* 事件;紅 = gate.sh 對每張
        票仍派覆核(reviewer ran 7);期望值 = VISION 流程 #9 與 D-054 Q1 原話。
        """
        for name, fields in (("simple", {"complexity": "simple"}), ("沒標", {})):
            with self.subTest(name):
                self.setUp()
                self.set_reviewer(REVIEWER_PASS)
                self.make_ticket(7, state="Running", **fields)
                self.git("add", "-A")
                self.git("commit", "-q", "-m", "沙盒的假 reviewer 與票 #7")
                self.git("branch", "t7")
                self.write("tests/test_zz_green.py", PASSING % "test_zz_green")
                done = self.gate("--branch", "--ticket", "7")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("不派覆核", done.stdout)
                self.assertNotIn("reviewer ran 7", self.ran())
                ticket = self.load_ticket("7")
                self.assertEqual(ticket["state"], "InReview")
                self.assertIsNone(ticket.get("review"))
                self.assertEqual([kind for kind in self.kinds() if kind.startswith("review.")], [])

    def test_a_green_file_run_does_not_call_review_sh(self):
        """指定檔(與 land 跑的 `--full`)不是「手跑票閘門」:不轉 InReview、不派覆核。"""
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket(7, state="Running")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的假 reviewer 與票 #7")
        self.git("branch", "t7")
        self.write("tests/test_zz_green.py", PASSING % "test_zz_green")
        done = self.gate("tests/test_zz_green.py", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("reviewer ran 7", self.ran())
        self.assertEqual(self.load_ticket("7")["state"], "Running")

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

        `verify.tags` 留空:#170 起回歸層不看標籤、整層跑(這份案例也在裡面),
        標籤只拿去查缺口 —— 留空就不查,這一組不碰缺口那一條。
        """
        self.write(os.path.join("verify", "nav", "__init__.py"), "")
        self.write(os.path.join("verify", "nav", "test_ticket_7.py"), case)
        fields.setdefault("verify", {"files": ["verify/nav/test_ticket_7.py"],
                                     "tags": [], "run": "", "notes": ""})
        return self.make_ticket(7, **fields)

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
        self.make_ticket(7, verify={"files": [], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("這張票沒有驗證者案例", done.stdout)
        self.assertIn("needs_verifier=false", done.stdout, "下一步要寫在裡面")

    def test_a_ticket_that_never_declared_verify_is_not_a_gap(self):
        """**沒有宣告**與**宣告了卻沒有案例**是兩件事,下一步也不同。

        同一支腳本對回歸層的缺口檢查就是這樣分的:`regression()` 對「票沒有宣告 verify.tags」
        不查,對「宣告了 tags 卻一個案例都選不到」才非零(#619)。`needs_verifier`
        那一格則是與 `land.sh` 對齊(#8)—— 兩支對同一張票不該給出不同的答案。

        **變異**:把 `undeclared)` 那一格拿掉 → 後半紅(rc 變 3);把
        `needs-verifier-false)` 那一格拿掉 → 前半紅。
        """
        self.make_ticket(7, needs_verifier=False,
                         verify={"files": [], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("needs_verifier=false", done.stdout)

        # 票上連 `verify` 這一格都沒有(沙盒手寫票的形狀)。
        self.make_ticket(7)
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("連 verify 這一格都沒有宣告", done.stdout)
        self.assertNotIn("這張票沒有驗證者案例", done.stdout)

    def test_a_gate_in_the_ticket_worktree_reads_the_main_repo_ticket(self):
        """#40 A4:`needs_verifier=false` 只在**主 repo** 那一份票上,票 worktree 裡那一份
        是沒有那一格的舊版。閘門在 worktree 裡只帶 `AC_ROOT`、**不帶** `AC_TICKETS_DIR`
        —— auto-fix 起閘門就是這個形狀 —— 要讀到主 repo 那一份,驗證者層不算缺口。

        **變異 M3**:`verify_plan` 改回拿 `$ROOT` 拼票檔路徑 → 紅(讀到 worktree 那份
        舊票,rc=3「這張票沒有驗證者案例」)。
        """
        self.make_ticket(7, verify={"files": [], "tags": [], "run": "", "notes": ""})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #7(還沒有 needs_verifier)")
        marked = self.ticket("set", "7", "needs_verifier", "false")
        self.assertEqual(marked.returncode, 0, marked.stdout + marked.stderr)
        self.assertIs(self.load_ticket("7").get("needs_verifier"), False)
        wt = self.worktree("t7")
        with open(os.path.join(wt, "tickets", "7.json"), encoding="utf-8") as handle:
            self.assertNotIn("needs_verifier", json.load(handle),
                             "前提:worktree 那一份票沒有那一格")
        self.write(os.path.join("tests", "test_land.py"),
                   PASSING % "test_land" + "\n# 這條分支的改動\n", where=wt)
        env = self.env(AC_ROOT=self.repo)
        self.assertNotIn("AC_TICKETS_DIR", env)

        done = self.run_sh("scripts/gate.sh",
                           "--branch", "--ticket", "7", "--no-auto-fix", cwd=wt, env=env)

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("needs_verifier=false —— 這一層不要求案例", done.stdout)
        self.assertNotIn("這張票沒有驗證者案例", done.stdout)

    def worktree_with_an_old_ticket(self, old, new):
        """#53 D1 的形狀(同上一條):worktree 那一份票是 `old`、主 repo 那一份是 `new`,
        分支改了 tests/test_land.py;回傳 worktree 路徑。兩份票的欄位都由這裡寫。"""
        self.make_ticket(7, **old)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #7(舊版)")
        wt = self.worktree("t7")
        self.make_ticket(7, **new)
        self.write(os.path.join("tests", "test_land.py"),
                   PASSING % "test_land" + "\n# 這條分支的改動\n", where=wt)
        return wt

    def gate_in(self, wt, env):
        return self.run_sh("scripts/gate.sh",
                           "--branch", "--ticket", "7", "--no-auto-fix", cwd=wt, env=env)

    def test_ticket_tags_in_the_ticket_worktree_come_from_the_main_repo(self):
        """#53 D1(b):主 repo 那一份 `verify.tags` 是 [],worktree 那份是未登記的 ["zzz"]。
        preflight 不退 4,回歸層也不拿 zzz 去選案例。

        **變異 M2**:ticket_tags 改回 `event.tickets_dir(root)` 拼路徑 → 紅(回歸層選 zzz)。
        """
        fields = {"needs_verifier": False}
        wt = self.worktree_with_an_old_ticket(
            dict(fields, verify={"files": [], "tags": ["zzz"], "run": "", "notes": ""}),
            dict(fields, verify={"files": [], "tags": [], "run": "", "notes": ""}))
        done = self.gate_in(wt, self.env(AC_ROOT=self.repo))
        self.assertNotEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertNotIn("zzz", done.stdout + done.stderr)
        # #170:回歸層整層跑;worktree 那份 zzz 要是被讀到,缺口檢查(`--list --tag zzz`)
        # 會印出它而退 3。
        self.assertIn("gate: 回歸全部", done.stdout)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_hand_run_in_the_ticket_worktree_writes_the_main_repo_ticket(self):
        """#53 D2:手跑在 worktree、**沒帶** `AC_ROOT`。綠了轉 InReview 要寫主 repo 那一份,
        worktree 那一份不動。

        **變異 M3**:拿掉 gate.sh 那段 `export AC_ROOT=<main_root>` → 紅(worktree 那份
        被寫成 InReview)。
        """
        fields = {"state": "Running"}
        wt = self.worktree_with_an_old_ticket(fields, fields)
        env = self.env()
        env.pop("AC_ROOT")   # 手跑:票根由 cwd 規則找(#159)
        done = self.gate_in(wt, env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("票改讀主 repo", done.stdout)
        self.assertEqual(self.load_ticket("7")["state"], "InReview")
        with open(os.path.join(wt, "tickets", "7.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["state"], "Running", "worktree 那一份被寫了")

    def test_the_regression_layer_in_the_ticket_worktree_runs_the_worktree_cases(self):
        """#55 A3:閘門在 worktree 裡帶 `AC_ROOT=<主 repo>`(land.sh 的形狀),回歸層
        看到的要是 **worktree** 那份 `verify/`。主 repo 的 example 案例是綠的,worktree
        那份改成紅(字串由這裡寫)—— 紅只可能來自 worktree。

        **變異 M1**:verify.py 的 `_find_root()` 放回 `AC_ROOT` override 分支 → 紅
        (回歸層跑主 repo 那份綠案例,rc 0)。
        """
        self.make_ticket(7, needs_verifier=False,
                         verify={"files": [], "tags": ["example"], "run": "", "notes": ""})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #7(回歸層 example)")
        wt = self.worktree("t7")
        red = VERIFY_CASE.replace("self.assertTrue(True)", "self.fail('wt-only red')")
        self.assertNotEqual(red, VERIFY_CASE, "前提:夾具真的把案例改成紅")
        self.write(os.path.join("verify", "example", "test_example.py"), red, where=wt)
        # 對照表把 verify/* 對到 test_verify_runner;沙盒沒有那支,給一份綠的替身,
        # 讓這一條的紅只可能來自回歸層。
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   PASSING % "test_verify_runner", where=wt)
        log = os.path.join(self.home, "verify-t55.log")
        env = self.env(AC_ROOT=self.repo, AC_VERIFY_LOG=log)
        self.assertNotIn("AC_TICKETS_DIR", env)

        done = self.gate_in(wt, env)

        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(log, encoding="utf-8") as handle:
            self.assertIn("wt-only red", handle.read())
        self.assertEqual(self.read(os.path.join("verify", "example", "test_example.py")),
                         VERIFY_CASE, "主 repo 的 verify/example 被動了")

    # ------------------------------- 缺口進 failures[](#126 A2,kind=GAP)

    def gap_rows(self):
        return [row for row in self.status_of("7", kind="gate")["failures"]
                if row.get("kind") == "GAP"]

    def test_t126_a2_the_no_cases_gap_is_one_gap_row(self):
        """A2 缺口列:needs_verifier=true、verify 宣告了而 files 空 → rc 仍 3,恰一列 (no-cases)。

        **變異 M6**:CRC=3 那條路不設 `CASES_ARGS` → failures[] 空 → 紅。
        """
        self.make_ticket(7, needs_verifier=True,
                         verify={"files": [], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        rows = self.gap_rows()
        self.assertEqual(len(rows), 1, self.status_of("7", kind="gate")["failures"])
        self.assertEqual((rows[0]["case"], rows[0]["file"], rows[0]["line"]),
                         ("verify-case:(no-cases)", "", 0))
        self.assertIn("這張票沒有驗證者案例", rows[0]["excerpt"])

    def test_t126_a2_the_exceptions_write_no_gap_row(self):
        """A2 的兩個例外:needs_verifier=false / 沒宣告 verify → 0 列 GAP。

        **變異 M7**:例外那幾格也設 `CASES_ARGS` → 紅。
        """
        empty = {"files": [], "tags": [], "run": "", "notes": ""}
        for label, fields in (
                ("needs_verifier=false", {"verify": empty, "needs_verifier": False}),
                ("沒有 verify 格", {})):
            with self.subTest(label):
                self.make_ticket(7, **fields)
                done = self.gate()
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.gap_rows(), [])

    # --------------------------------------------- 量「候選該綠」

    def test_check_runs_and_lifts_the_stage(self):
        """驗收 3 與 4:`stage` 由 `red` 升成 `check`(閘門的證據;close 不讀)。

        **變異**:把 `verify_case` 裡 check 那一段拿掉 → 這一條紅(票上沒有
        `verify.baseline`)。
        """
        self.a_ticket()
        self.implement()
        done = self.gate()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
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
        self.assertLess(done.stdout.index("gate: 回歸全部"),
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


class TheHandRunGateWritesTheTicketGate(Sandbox):
    """#56 第 2 輪(主線裁示):手跑 `gate.sh --branch --ticket <n>` 也寫票上**同一個**
    `gate` 欄 —— 與 auto-fix 綠了寫的同形狀 {rc, sha, run_id, at}。以前這條路只寫狀態檔,
    review.sh 看票上沒有這個 sha,派工文就照實說「沒跑過閘門」,即使閘門剛跑完。

    形狀照真的:票在主 repo、分支 t7 一個 worktree、閘門在 worktree 手跑(不帶 `AC_ROOT`)。
    期望的 sha 由夾具 `git rev-parse t7` 取,不由受測腳本印。
    """

    KEYS = {"rc", "sha", "run_id", "at"}

    def setUp(self):
        super().setUp()
        # #74:缺角色卡、指定的模型卡不在、或只剩標題的規則包現在非零,而且不起 agent。
        # 預設沙盒沒有那幾份(`control_harness` 只給要的測試裝),不裝的話每一條派工的案例
        # 都停在「規則包產不出來」。假 reviewer 的模型(`set_reviewer` 的 fixture-model)
        # 也要有一張卡 —— 指定了卻不存在是錯,不是「沒有記憶」。
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")

    def branch_with(self, name, body):
        # complex:手跑綠要派覆核,派工文才看得到「已對 <sha> 跑過閘門」(#136 起只派 complex)。
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket(7, state="Running",
                         complexity="complex")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的假 reviewer 與票 #7")
        wt = self.worktree("t7")
        self.write(os.path.join("tests", name), body, where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 的改動", cwd=wt)
        return wt

    def gate_in(self, wt, *extra):
        env = self.env()
        env.pop("AC_ROOT")   # 手跑:票根由 cwd 規則找(#159)
        return self.run_sh("scripts/gate.sh",
                           "--branch", "--ticket", "7", *extra, cwd=wt, env=env)

    def test_a_green_hand_run_writes_rc_0_at_the_branch_head(self):
        """(a) **變異**:拿掉綠那一路的 `ticket_gate` → 票上沒有 gate 欄,紅。

        (c)(#72 併入原 test_the_review_packet_after_a_green_hand_run_says_the_gate_ran,
        同一趟綠):覆核者收到的派工文說「已對 <sha> 跑過閘門」,不說「沒跑過閘門」。
        """
        wt = self.branch_with("test_zz_green.py", PASSING % "test_zz_green")
        done = self.gate_in(wt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        gate = self.load_ticket("7").get("gate")
        self.assertIsInstance(gate, dict, "手跑綠了,票上沒有 gate 欄")
        self.assertEqual(set(gate), self.KEYS, "形狀要與 auto-fix 寫的同一個")
        self.assertIs(type(gate["rc"]), int)
        self.assertEqual(gate["rc"], 0)
        self.assertEqual(gate["sha"], self.git("rev-parse", "t7").strip())
        self.assertEqual(gate["run_id"], self.status_of("7", kind="gate")["run_id"])

        stdin = os.path.join(self.home, "reviewer-stdin.md")
        self.assertTrue(os.path.exists(stdin), "reviewer 沒被叫到:" + done.stdout + done.stderr)
        packet = self.read("reviewer-stdin.md", where=self.home)
        self.assertIn("已對 %s 跑過閘門" % self.git("rev-parse", "t7").strip(), packet)
        self.assertNotIn("沒跑過閘門", packet)

    def test_a_red_hand_run_writes_its_rc_at_the_same_sha(self):
        """(b) 紅也寫:票上記的是這個 sha 真正的結果,不是上一個綠。"""
        wt = self.branch_with("test_zz_red.py", FAILING)
        done = self.gate_in(wt, "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        gate = self.load_ticket("7").get("gate")
        self.assertIsInstance(gate, dict, "手跑紅了,票上沒有 gate 欄")
        self.assertEqual(set(gate), self.KEYS)
        self.assertEqual(gate["rc"], done.returncode)
        self.assertEqual(gate["sha"], self.git("rev-parse", "t7").strip())


class BranchGateRefusesAnEmptyCompareOnMain(Sandbox):
    """#87 A3:主 repo 的 HEAD 在主線、除了票檔什麼改動都沒有時跑
    `gate.sh --branch --ticket <n>` → rc=2,指出該去的 worktree;不寫狀態檔、不動票、
    不發事件。提示路徑的期望值由測試照 `wtbase.sh` 那一條自己拼。"""

    REFUSAL = "gate: --branch 不在主線上跑"

    def setUp(self):
        super().setUp()
        self.make_ticket(7)

    def refused(self, expected, **env):
        ticket_before = self.read(os.path.join("tickets", "7.json"))
        events_before = self.events()
        done = self.run_sh("scripts/gate.sh", "--branch", "--ticket", "7",
                           env=self.env(**env))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        line = [l for l in done.stderr.splitlines() if self.REFUSAL in l]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn(expected, line[0])
        self.assertFalse(self.exists(os.path.join("reports", "t7")), "拒跑卻寫了狀態檔")
        self.assertEqual(self.read(os.path.join("tickets", "7.json")), ticket_before)
        self.assertEqual(self.events(), events_before, "拒跑卻發了事件")
        self.assertFalse(os.path.exists(self.log), "拒跑卻跑了測試")

    def test_a3_the_default_worktree_dir_is_named(self):
        """**變異 M3**:拿掉 HEAD==main 那一段 → 這一條紅(走 preflight、rc 不是 2)。"""
        self.refused(os.path.join(os.path.dirname(self.repo), "repo-wt", "t7"))

    def test_a3_the_env_worktree_dir_wins(self):
        where = os.path.join(self.home, "elsewhere-wt")
        self.refused(os.path.join(where, "t7"), AC_WORKTREE_DIR=where)

    def test_a3_the_config_worktree_dir_is_used(self):
        conf = json.loads(self.read("board/config.json"))
        conf["worktree_dir"] = "../custom-wt"
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "worktree_dir")
        self.refused(os.path.join(self.home, "custom-wt", "t7"))

    def test_a3_other_dirty_ticket_files_still_refuse(self):
        """**變異 M3b**:判準退回只扣本票票檔 → 這一條紅。真主線上 `ticket.py set` 寫票
        不 commit,別張票的未提交票檔是常態(已追蹤改了、或根本沒追蹤),它們不是改動。"""
        self.make_ticket(8)
        self.git("add", "tickets")
        self.git("commit", "-q", "-m", "tickets")
        self.make_ticket(7, attempt=2)
        self.make_ticket(8, attempt=2)
        self.make_ticket(9)
        self.assertEqual(sorted(self.git("status", "--porcelain").splitlines()),
                         [" M tickets/7.json", " M tickets/8.json", "?? tickets/9.json"])
        self.refused(os.path.join(os.path.dirname(self.repo), "repo-wt", "t7"))


def a_home_sha(case):
    """A 的根(這個 repo)的 HEAD —— 測試自己用 git 量,不讀被測腳本。封存副本(不是 git 樹)
    量不了真 sha:skip 並說原因,`unknown` 那一半另有一條。"""
    top = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(ROOT):
        case.skipTest("A 的根在這裡不是一棵 git 樹(封存副本)—— 量不了真 sha")
    return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


# `status.py` 的 gate 狀態檔在 #159 之前的鍵集合:base 6d788d2 上同一趟 `gate.sh --base --ticket 7`
# (舊 harness)量出來、抄在這裡。這張票不加 status.json 欄位(D-071 §4;§1 那一句留給主線裁)。
STATUS_KEYS_BEFORE_159 = [
    "auto_flaky", "duration_seconds", "environment_suspect", "extra_logs", "failures", "finished",
    "flaky", "kept_logs", "kind", "logs", "note", "order_dependent", "order_dependent_cases",
    "phases", "rc", "repair_context", "report", "round", "run_id", "sha", "started", "state",
    "suspected_flaky", "ticket"]


class A5TheGateSaysWhichA(Sandbox):
    """#159 A5(D-071 §1):`gate.start` / `gate.pass` 帶 `ac_sha` = AC_HOME 的 HEAD;AC_HOME 不是
    git 樹就是字面 `unknown`(永不空、永不缺)。status.json 不加鍵。"""

    def setUp(self):
        super().setUp()
        # 每支一條綠的(#162):testiface 起,一個載不出任何測試的 id 是 error 列、不是綠
        # (adapter 的 §5.5,「Ran 0」與「都過了」不再長得一樣)。這一組問的是事件與鍵,不是 0 條。
        for name in ("test_ticket", "test_event", "test_memory", "test_no_project_names"):
            self.write(os.path.join("tests", "%s.py" % name),
                       "import unittest\n\n\nclass T(unittest.TestCase):\n"
                       "    def test_ok(self):\n        pass\n")

    def row(self, kind):
        rows = [row for row in self.events() if row["kind"] == kind]
        self.assertEqual(len(rows), 1, self.events())
        return rows[0]

    def test_a5_gate_base_carries_the_head_of_ac_home(self):
        """**變異 m5**:拿掉 gate.sh 那一格 `--kv ac_sha=…` → 紅。"""
        sha = a_home_sha(self)
        done = self.run_sh("scripts/gate.sh", "--base", env=self.env(AC_HOME=ROOT))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.row("gate.start")
        self.assertEqual(row.get("ac_sha"), sha)
        self.assertEqual(row.get("mode"), "base")

    def test_a5_a_non_git_ac_home_says_unknown_and_status_keeps_its_keys(self):
        self.own_ac_home()
        self.make_ticket(7)
        done = self.run_sh("scripts/gate.sh", "--base", "--ticket", "7", "--no-auto-fix")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.row("gate.start").get("ac_sha"), "unknown")
        self.assertEqual(self.row("gate.pass").get("ac_sha"), "unknown")
        status = self.status_of(7, kind="gate")
        self.assertEqual(sorted(status), STATUS_KEYS_BEFORE_159, status)



# #170:回歸層的案例自己記「我真的跑了」(`REG_RUNS`,一次一行),假的 regression 命令記
# 「我被叫了、帶什麼參數」(`REG_CALLS`)—— 兩份 log 分開,快取命中(被叫了、案例沒跑)
# 與真的跑過才分得開。
REG_CASE = '''"""#170 沙盒的第二個標籤:兩條案例。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 跑這兩條 | %(want)s | 沙盒 fixture

## 介面字串
(沒有)

## 怎麼做假
不上真埠、不起真服務、不殺行程:只寫環境給的一份 log。

## 不做
不改產品碼。
"""
import os
import unittest

TAGS = ["second"]


class Second(unittest.TestCase):
    def mark(self, name):
        with open(os.environ["REG_RUNS"], "a", encoding="utf-8") as handle:
            handle.write(name + "\\n")

    def test_one(self):
        """A1 第一條。"""
        self.mark("one")
        %(one)s

    def test_two(self):
        """A1 第二條。"""
        self.mark("two")
'''
# 第一次跑紅、之後都綠(計數器住在 `REG_RUNS` 旁邊)—— flake 判定那條路的夾具。
FLAKY_BODY = ("count = os.environ['REG_RUNS'] + '.flaky'\n"
              "        seen = os.path.exists(count)\n"
              "        open(count, 'a').close()\n"
              "        self.assertTrue(seen, 'first run is red')")
REGRESS_SH = """#!/bin/sh
echo "$*" >> "$REG_CALLS"
exec python3 "$AC_HOME/scripts/verify.py" --result "$AC_TEST_OUT/result.json" "$@"
"""


class BranchGateRunsTheWholeRegressionLayer(Sandbox):
    """#170(D-073):`gate.sh --branch --ticket <n>` 在單元層之後跑**整個回歸層**,不只票的
    標籤那一組 —— #159 的 84 條紅全在票沒宣告的標籤底下,分支閘門綠、到 land 才紅。
    票的標籤只剩缺口檢查(`regression --list --tag …`,不跑)。

    沙盒:verify/example(1 條,沙盒預設)+ verify/second(2 條,標籤 second)+ 登記了卻沒有
    案例的 ghost。regression 命令是一支記帳的包裝(`REGRESS_SH`),底下是真的 verify.py。
    期望的案例數(3)由這裡寫的夾具數出來,不從閘門輸出讀回來。
    """

    WHOLE = 3   # verify/example 1 條 + verify/second 2 條

    def setUp(self):
        super().setUp()
        self.write(os.path.join("tests", "test_land.py"), PASSING % "test_land")
        # 對照表把 verify/* 對到 test_verify_runner;沙盒沒有那支,給一份綠的替身,
        # 讓這一組的紅只可能來自回歸層。
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   PASSING % "test_verify_runner")
        self.write(os.path.join("verify", "TAGS.md"),
                   self.read(os.path.join("verify", "TAGS.md"))
                   + "- `second` — #170 沙盒的第二個標籤\n- `ghost` — 登記了,沒有案例\n")
        self.write(os.path.join("verify", "second", "__init__.py"), "")
        self.second(green=True)
        regress = os.path.join(self.home, "regress.sh")
        write_executable(regress, REGRESS_SH)
        config = json.loads(self.read("board/config.json"))
        config["tests"]["regression"] = "sh %s" % regress
        config["flake_auto_single_runs"] = 3
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.calls = os.path.join(self.home, "reg-calls.log")
        self.runs = os.path.join(self.home, "reg-runs.log")
        self.verify_log = os.path.join(self.home, "verify-t170.log")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:兩個標籤的回歸層")

    def second(self, green=True, where=None, body=None):
        one = body or ("pass" if green else "self.fail('second-tag red')")
        self.write(os.path.join("verify", "second", "test_second.py"),
                   REG_CASE % {"want": "綠" if green else "紅", "one": one}, where=where)

    def branch(self, **fields):
        """票 #7 + 分支 t7(改一個對得到的 scripts/ 檔);回傳 worktree。"""
        fields.setdefault("needs_verifier", False)
        self.make_ticket(7, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #7")
        wt = self.worktree("t7")
        self.write(os.path.join("scripts", "land.sh"), "# 動一下\n", where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 的改動", cwd=wt)
        return wt

    def gate_in(self, wt, *extra, **env):
        env.setdefault("AC_GATE_RUN_ID", "20261026-000000-%d" % os.getpid())
        return self.run_sh("scripts/gate.sh", "--branch", "--ticket", "7", *extra, cwd=wt,
                           env=self.env(AC_ROOT=self.repo, AC_VERIFY_LOG=self.verify_log,
                                        REG_CALLS=self.calls, REG_RUNS=self.runs, **env))

    def lines(self, path):
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def list_calls(self):
        return [c for c in self.lines(self.calls) if "--list" in c.split()]

    def run_calls(self):
        return [c for c in self.lines(self.calls) if "--list" not in c.split()]

    def ran_count(self):
        with open(self.verify_log, encoding="utf-8") as handle:
            text = handle.read()
        counts = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
        self.assertTrue(counts, "verify log 裡沒有 Ran:" + text)
        return sum(int(n) for n in counts)

    def reset_logs(self):
        for path in (self.calls, self.runs):
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------ A1

    def test_a1_the_branch_gate_runs_every_regression_case_whatever_the_ticket_declares(self):
        """A1:verify.tags [] / ['example'] / 沒有 verify 格 → 整層 3 條都跑。

        **變異 m2**:分支模式照舊帶 `--tag example` → 'example' 那一臂紅(Ran 1 ≠ 3)。
        """
        wt = self.branch()
        shapes = (("verify.tags []", {"files": [], "tags": [], "run": "", "notes": ""}),
                  ("verify.tags ['example']",
                   {"files": [], "tags": ["example"], "run": "", "notes": ""}),
                  ("沒有 verify 格", None))
        for label, verify in shapes:
            with self.subTest(label):
                self.reset_logs()
                fields = {"needs_verifier": False}
                if verify is not None:
                    fields["verify"] = verify
                self.make_ticket(7, **fields)
                done = self.gate_in(wt, "--no-auto-fix", "--no-cache")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("gate: 回歸全部", done.stdout)
                self.assertEqual(self.ran_count(), self.WHOLE, done.stdout)
                self.assertEqual(sorted(self.lines(self.runs)), ["one", "two"],
                                 "第二個標籤的案例沒跑")
                self.assertEqual(len(self.run_calls()), 1, self.lines(self.calls))
                for call in self.run_calls():
                    self.assertNotIn("--tag", call.split(), "整層跑不帶 --tag")

    def test_a1_a_declared_tag_with_no_case_is_still_the_gap(self):
        """A1 ghost 臂:登記了、一條案例都沒有 → rc 3、今天那句話;`--list` 恰一次、run 至多一次。

        **變異 m3**:拿掉缺口檢查 → rc 0,紅。
        """
        wt = self.branch(verify={"files": [], "tags": ["ghost"], "run": "", "notes": ""})
        done = self.gate_in(wt, "--no-auto-fix")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("gate: 票宣告了 tags,但一個案例都選不到 —— 那是缺口,不是綠。", done.stdout)
        self.assertEqual(len(self.list_calls()), 1, self.lines(self.calls))
        self.assertIn("--tag ghost", self.list_calls()[0])
        self.assertLessEqual(len(self.run_calls()), 1, self.lines(self.calls))

    # ------------------------------------------------------------ A2

    def test_a2_a_red_case_under_an_undeclared_tag_is_the_gates_red(self):
        """A2 紅臂(#159 的形狀):票只宣告 example,second 的一條在分支上紅 → rc 1、
        failures[0].case 是那條回歸 id、state done、假 auto-fix 被叫到。

        **變異 m1**:回歸的 result 不餵狀態檔(拿掉 REG_RESULT_ARGS)→ failures[] 空,紅。
        """
        marker = os.path.join(self.home, "auto-fix-called")
        self.stub_script("auto-fix.sh", "#!/bin/sh\necho \"$1\" >> %s\n" % marker)
        wt = self.branch(verify={"files": [], "tags": ["example"], "run": "", "notes": ""})
        self.second(green=False, where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 把 second 弄紅", cwd=wt)
        done = self.gate_in(wt, AC_NO_FLAKE_RERUN="1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        status = self.status_of("7", kind="gate")
        self.assertEqual(status["state"], "done")
        self.assertTrue(status["failures"], status)
        self.assertEqual(status["failures"][0]["case"],
                         "verify.second.test_second.Second.test_one", status["failures"])
        self.assertTrue(os.path.exists(marker), "回歸紅了卻沒派 auto-fix:" + done.stdout)

    def test_a2_a_flaky_regression_case_goes_through_the_flake_rerun(self):
        """A2 flake 臂:第一次紅、之後綠 → 那條回歸 id 單跑 flake_auto_single_runs(3)次,
        status.json 的 suspected_flaky 有它。執行次數 = 1(原跑)+ 3(單跑)+ 1(整組)。
        """
        wt = self.branch(verify={"files": [], "tags": ["example"], "run": "", "notes": ""})
        self.second(where=wt, body=FLAKY_BODY)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 的 second 第一次紅", cwd=wt)
        done = self.gate_in(wt, "--no-auto-fix")
        self.assertIn("單獨重跑 3 次全綠", done.stdout, done.stdout + done.stderr)
        singles = [c for c in self.run_calls()
                   if "--case verify/second/test_second.py" in c]
        self.assertEqual(len(singles), 3, self.lines(self.calls))
        self.assertEqual(self.lines(self.runs).count("one"), 1 + 3 + 1, self.lines(self.runs))
        status = self.status_of("7", kind="gate")
        self.assertEqual([row["case"] for row in status["suspected_flaky"]],
                         ["verify.second.test_second.Second.test_one"], status)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # ------------------------------------------------------------ A3

    def test_a3_the_second_gate_of_a_round_is_served_from_the_cache(self):
        """A3:同一個 AC_RUN_ID 跑兩次 → 第二次回歸吃快取(案例只真跑一次);--no-cache → 兩次。"""
        wt = self.branch(verify={"files": [], "tags": [], "run": "", "notes": ""})
        for _ in range(2):
            done = self.gate_in(wt, "--no-auto-fix")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.lines(self.runs).count("one"), 1, self.lines(self.runs))
        self.assertIn("用快取", self.read("verify-t170.log", where=self.home))
        self.reset_logs()
        for _ in range(2):
            done = self.gate_in(wt, "--no-auto-fix", "--no-cache",
                                AC_GATE_RUN_ID="20261026-000001-%d" % os.getpid())
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.lines(self.runs).count("one"), 2, self.lines(self.runs))

    # ------------------------------------------------------------ A4

    # base 4ca7e6a 的 `gate.sh --full` 在這個沙盒的回歸段,用 base 的 scripts/ 跑一次抄下來的
    # (`AC_TEST_SCRIPTS=<git archive 4ca7e6a scripts>/scripts`)。段 = `gate: 回歸全部` 那一行
    # 到下一個 `gate: ` 或 `status: ` 行(`status: ` 是 status.py 印的,不屬回歸段)。只換掉
    # 跟機器有關的三樣:秒數、`parallel:` 括號裡的核數與記憶體、行程數。
    FULL_SECTION_AT_BASE = ("gate: 回歸全部 —— regression\n"
                            "parallel: <n> processes (<machine>)\n"
                            "Ran 1 test in <t>s\n"
                            "OK\n"
                            "Ran 2 tests in <t>s\n"
                            "OK\n")

    def full_section(self, stdout):
        lines = stdout.splitlines(keepends=True)
        start = [i for i, line in enumerate(lines) if line.startswith("gate: 回歸全部")]
        self.assertEqual(len(start), 1, stdout)
        out = [lines[start[0]]]
        for line in lines[start[0] + 1:]:
            if line.startswith(("gate: ", "status: ")):
                break
            out.append(line)
        text = re.sub(r"(^Ran \d+ tests? in )\d+\.\d+s", r"\1<t>s", "".join(out), flags=re.M)
        return re.sub(r"^parallel: \d+ processes \(.*\)$", "parallel: <n> processes (<machine>)",
                      text, flags=re.M)

    def test_a4_the_full_regression_section_is_unchanged(self):
        """A4:`--full` 的回歸段與 base 逐字相同。**變異 m4**:改那一段的字 → 紅。"""
        self.make_ticket(7)
        done = self.run_sh("scripts/gate.sh", "--full", "--ticket", "7", "--no-auto-fix",
                           env=self.env(AC_VERIFY_LOG=self.verify_log, REG_CALLS=self.calls,
                                        REG_RUNS=self.runs))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.full_section(done.stdout), self.FULL_SECTION_AT_BASE)



class T125EnvSuspectHandsOffToAutoFix(Sandbox):
    """#125 A4 (D-076 replaces D-019 reason 5's 'never dispatch over env suspect'): a gate that ends
    in rc 86 with auto-fix on calls auto-fix (which re-runs once, then diagnoses) and writes no
    page itself; `--no-auto-fix` keeps today's page and calls nothing. auto-fix is a stub that
    records its argument, so 'was it called' is a visible fact."""

    def setUp(self):
        super().setUp()
        for name in ("test_ticket", "test_event", "test_memory",
                     "test_no_project_names", "test_heartbeat", "test_land"):
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)
        self.write("tests/test_env_wave.py", ENVIRONMENT_WAVE)
        self.marker = os.path.join(self.home, "auto-fix-called")
        self.stub_script("auto-fix.sh", "#!/bin/sh\necho \"$1\" >> %s\n" % self.marker)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "sandbox: env wave + stub auto-fix")
        self.make_ticket(7)

    def called(self):
        if not os.path.exists(self.marker):
            return []
        with open(self.marker, encoding="utf-8") as handle:
            return handle.read().split()

    def decision_pages(self):
        return [row for row in self.inbox_rows(ticket="7") if row.get("kind") == "decision"]

    def test_a4_rc_86_with_auto_fix_on_calls_auto_fix_and_writes_no_page(self):
        done = self.run_sh("scripts/gate.sh", "tests/test_env_wave.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.status_of("7")["state"], "env_suspect", "precondition: rc 86 path")
        self.assertEqual(self.called(), ["7"], done.stdout + done.stderr)
        self.assertNotIn("環境可疑,不自動派", done.stdout)
        self.assertEqual(self.decision_pages(), [],
                         "the page is auto-fix's terminal state, not the gate's")

    def test_a4_rc_86_with_no_auto_fix_keeps_the_page_and_calls_nothing(self):
        done = self.run_sh("scripts/gate.sh", "tests/test_env_wave.py", "--ticket", "7",
                           "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.called(), [])
        self.assertEqual(len(self.decision_pages()), 1, self.decision_pages())


if __name__ == "__main__":
    unittest.main()
