"""#27 gate.sh --ticket 接線:regression() 之後跑 verify-case.py lint → check,
status.json 收 baseline 紅榜(D-020,只證紅,不搭參考實作、不做變異)

## 驗收表(票面 acceptance 逐條,期望值來源 = docs/DESIGN-VERIFY-CASES.md §三 + 票 #27 acceptance 原句)
A1   | integration | 票 verify.files=[]、無 verify_waiver,`gate.sh --ticket <n>` | stdout 印『這張票沒有驗證者案例』、視為缺口(rc 非 0) | 票 #27 acceptance 第 1 條原話
A1-2 | integration | 同上但票有 verify_waiver                                  | 不印那句話、不當缺口(這一段本身仍是 0)            | 票 #27 acceptance 第 1 條「有 verify_waiver 才不算缺口」
A2   | integration | verify.files 非空但案例檔 lint 不過(缺 docstring/TAGS)   | stdout 指名檔案與行號(`lint`/`F1` 那一類訊息);不繼續跑 check | 票 #27 acceptance 第 2 條原話
A3   | integration | verify.files 非空且案例檔 lint 過                        | gate 會呼叫 `verify-case.py check`(stdout 出現 check 的輸出) | 票 #27 acceptance 第 3 條原話
A4   | integration | check 執行完                                             | 票的 `verify.baseline.stage` 從 `red` 升成 `check`  | 票 #27 acceptance 第 4 條原話
A5   | integration | check 判 ok:false(候選仍紅)                             | status.json 的 `failures[]` 非空,且走既有 auto-fix 派工路 | 票 #27 acceptance 第 5 條原話 + D-014 既有 auto-fix 機制
A6   | integration | 只跑一次 `gate.sh --ticket <n>`(從不呼叫 land.sh)       | regression() 的輸出先出現,check 的輸出在它之後才出現 | 票 #27 acceptance 第 6 條原話「在 regression() 之後執行,不是在 land 才量」

## 介面字串(斷言只認這裡的常數;票面沒定的形狀在這裡定)
GAP_LINE = "這張票沒有驗證者案例"          ← A1 斷言 stdout 含它(逐字,票面 acceptance 原句)
AUTO_FIX_LINE = "gate: auto-fix ——"        ← A5 沿用 tests/test_gate.py 既有 auto-fix 派工的斷言字樣
CHECK_MARKER = "verify-case: #91 案例"      ← A3/A6 斷言 `verify-case.py check` 真的被叫到(cmd_check 開頭那一句)
REGRESSION_MARKER = "沒有宣告 verify.tags"  ← A6 用來定位 regression() 印出的那一行(這幾張票都沒宣告 verify.tags)
MARKER_FILE = "IMPLEMENTED_A27.marker"      ← A3/A4/A6 的假案例讀這個檔存不存在

## 怎麼做假(不上真埠、不起真服務、不殺行程;沿用 tests/control_harness.py 的 Sandbox)
- 全部在 `Sandbox`(拋棄式 tempdir 真 git repo,port 全 0)裡跑,不碰這台機器上任何真
  repo。
- 「基底沒有實作、候選有」用**一個相對於工作目錄的空白 marker 檔**模擬:`verify-case.py
  check` 的基底副本是 `git archive <base_sha>` 出來的,只要 marker 檔在 base_sha 那個
  commit 之前就不存在;候選（`--candidate` 預設就是這一輪工作目錄本身)一定讀得到它。
  不搭任何「真的把票實作出來」的產品程式碼。
- auto-fix 那一步用假 worker(寫一行標記檔就結束,同 `tests/test_gate.py` 既有寫法),
  不叫真的 claude-code。
- 每一段测试自己的 fixture 都 `git commit` 進沙盒的 `main`,讓 gate.sh 的機械格前置
  檢查(`--ticket` 的 `allowed_write_paths` 那一段)看到的 diff 是空的,不受它掣肘
  (票給 `allowed_write_paths=["*"]`,不是為了繞過驗收,是為了不讓這一段跟本票驗收
  無關的機制擋住案例)。

## 不做
不改 `scripts/gate.sh`(那是實作者的事,這一份只證乾淨基底上這些驗收還沒被滿足);
不搭參考實作讓案例轉綠;不做變異驗紅;不等實作者的 patch;不碰 127.0.0.1 任何埠;
不在沙盒外的任何 repo 寫入。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox, write_executable  # noqa: E402

# 沿用已登記的 `example` 標籤(沙盒示範用)——這一份沒有另外開
# `verify/TAGS.d/27.md`:交付範圍只有這一個檔(見上面「不做」)。
TAGS = ["example"]

GAP_LINE = "這張票沒有驗證者案例"
AUTO_FIX_LINE = "gate: auto-fix ——"
CHECK_MARKER = "verify-case: #91 案例"
REGRESSION_MARKER = "沒有宣告 verify.tags"
MARKER_FILE = "IMPLEMENTED_A27.marker"

DUMMY_MAPPED_TEST = """import unittest


class T(unittest.TestCase):
    def test_it_passes(self):
        pass
"""

# lint 一定不過:沒有模組 docstring、沒有 TAGS(F1 + F2)。
BAD_LINT_CASE = """import unittest


class T(unittest.TestCase):
    def test_something(self):
        self.assertTrue(True)
"""

# lint 一定過(四個標題都有、TAGS 是已登記的字面 list、沒有 tempfile/沒有禁字),
# 而且行為只依賴 MARKER_FILE 在不在工作目錄底下 —— 基底副本(git archive 出來的舊
# commit)天生沒有它,候選(工作目錄本身)天生有它。
GOOD_CASE = '''"""#27 沙盒案例:候選樹上有 marker 檔才綠,基底上沒有就紅

## 驗收表
X1 | integration | 讀 marker 檔是否存在(相對於跑測試那棵樹的 cwd) | marker 在就過、不在就紅 | 這份案例檔本身的設計(#27 沙盒 fixture,不是票面驗收原文)

## 介面字串
MARKER = "IMPLEMENTED_A27.marker" ← 相對於 cwd 檢查存不存在

## 怎麼做假
不起真服務、不開真埠、不殺行程;用一個空白檔案模擬「這一輪的分支樹已經有實作」。

## 不做
不改產品碼;只讀一個檔案存不存在。
"""
import os
import unittest

TAGS = ["example"]


class MarkerPresence(unittest.TestCase):

    def test_marker_only_exists_on_the_candidate_tree(self):
        """X1 候選樹上已經有 marker,基底副本上還沒有。"""
        self.assertTrue(os.path.exists("IMPLEMENTED_A27.marker"),
                        "還沒有 marker —— 這一棵樹還沒有『這一輪的實作』")
'''

# 兩邊永遠紅(候選也紅),用來讓 check 判 ok:false。
ALWAYS_RED_CASE = '''"""#27 沙盒案例:永遠紅,讓候選樹也紅、check 判 ok:false

## 驗收表
X1 | integration | 執行這條案例 | 不論基底或候選都是 AssertionError | 這份案例檔本身的設計(#27 沙盒 fixture)

## 介面字串
(無)

## 怎麼做假
不起真服務、不開真埠、不殺行程;斷言恆假。

## 不做
不改產品碼。
"""
import unittest

TAGS = ["example"]


class AlwaysRed(unittest.TestCase):

    def test_it_never_turns_green(self):
        """X1 這一條案例兩邊都紅,證明 candidate 仍紅時 check 判 ok:false。"""
        self.fail("verify-case-a5-still-red")
'''


class GateTicketWiring(Sandbox):
    """`gate.sh --ticket` 接上 `verify-case.py lint` / `check`(#27,依賴 A#26 已有的
    子指令)。乾淨基底(`scripts/gate.sh` 還沒接線)上,下面每一條都該紅在自己的
    `assert*`——`verify-case: #91 案例` 這種 check 才會印的字樣今天根本不會出現,
    `verify.baseline.stage` 也不會被寫成 `check`。
    """

    def commit_all(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def mapped_test(self, name="tests/test_dummy91.py"):
        self.write(name, DUMMY_MAPPED_TEST)
        return name

    def write_case(self, rel, body):
        self.write(rel, body)
        walk = os.path.dirname(rel)
        while walk:
            stub = os.path.join(walk, "__init__.py")
            if not self.exists(stub):
                self.write(stub, "")
            walk = os.path.dirname(walk)

    # ---------------------------------------------------------- A1

    def test_a1_empty_verify_files_without_waiver_prints_the_gap_line(self):
        """A1 verify.files 空、沒有 verify_waiver —— gate 印『這張票沒有驗證者
        案例』,視為缺口(同 #619「宣告了 tags 卻選不到案例」的精神)。"""
        dummy = self.mapped_test()
        self.commit_all("沙盒:dummy 模組")
        self.make_ticket(91, allowed_write_paths=["*"],
                         verify={"files": [], "tags": []})
        done = self.gate(dummy, "--ticket", "91", "--no-auto-fix")
        self.assertIn(GAP_LINE, done.stdout,
                     "verify.files 空且沒有 waiver 時,gate 沒有印票面 acceptance "
                     "第 1 條指定的那句話:\n" + done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, 0,
                            "驗證者案例的缺口要當非零處理,不能與『都過了』長得一樣:\n"
                            + done.stdout + done.stderr)

    def test_a1_2_a_verify_waiver_is_not_treated_as_a_gap(self):
        """A1-2 同一格 verify.files 空,但票上有 verify_waiver —— 不當缺口,也
        不印那句話。"""
        dummy = self.mapped_test()
        self.commit_all("沙盒:dummy 模組")
        self.make_ticket(91, allowed_write_paths=["*"],
                         verify={"files": [], "tags": []},
                         verify_waiver={"by": "main", "reason": "沙盒案例:A1-2 免驗"})
        done = self.gate(dummy, "--ticket", "91", "--no-auto-fix")
        self.assertNotIn(GAP_LINE, done.stdout,
                         "票上已經有 verify_waiver,不該再被印成缺口:\n"
                         + done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0,
                         "這一段本身(dummy 模組綠、沒有 tags 可跑回歸)不該因為"
                         "verify_waiver 被錯判成缺口而變紅:\n"
                         + done.stdout + done.stderr)

    # ---------------------------------------------------------- A2

    def test_a2_lint_failure_stops_before_check_runs(self):
        """A2 verify.files 非空但案例檔 lint 不過(缺模組 docstring 與 TAGS)——
        gate 要指名哪個檔哪一行,而且不准往下跑 check。"""
        dummy = self.mapped_test()
        bad_rel = "verify/t91/test_badlint.py"
        self.write_case(bad_rel, BAD_LINT_CASE)
        self.commit_all("沙盒:lint 不過的案例檔")
        self.make_ticket(91, allowed_write_paths=["*"],
                         verify={"files": [bad_rel], "tags": []})
        done = self.gate(dummy, "--ticket", "91", "--no-auto-fix")
        self.assertIn(bad_rel, done.stdout,
                     "lint 不過時 gate 要指名是哪個檔,而不是只說一句『lint 不過』:\n"
                     + done.stdout + done.stderr)
        self.assertIn("F1", done.stdout,
                     "lint 不過的理由(F1 缺 docstring)要印出來,不能只是一個非零"
                     "退出碼:\n" + done.stdout + done.stderr)
        self.assertNotIn(CHECK_MARKER, done.stdout,
                         "lint 沒過,check 就不准被叫到:\n"
                         + done.stdout + done.stderr)
        data = self.load_ticket("91")
        baseline = (data.get("verify") or {}).get("baseline")
        self.assertIsNone(baseline,
                          "lint 沒過還是把 check 的結果寫進了票的 verify.baseline:\n"
                          + json.dumps(data.get("verify"), ensure_ascii=False))

    # ---------------------------------------------------------- A3 / A4 / A6

    def _setup_good_case(self, with_marker=True):
        dummy = self.mapped_test()
        good_rel = "verify/t91/test_good.py"
        self.write_case(good_rel, GOOD_CASE)
        self.commit_all("沙盒:會綠的案例檔")
        base_sha = self.git("rev-parse", "main").strip()
        if with_marker:
            self.write(MARKER_FILE, "")
            self.commit_all("沙盒:這一輪的分支樹已經有實作(marker)")
        self.make_ticket(91, allowed_write_paths=["*"], base_sha=base_sha,
                         verify={"files": [good_rel], "tags": [],
                                 "baseline": {"stage": "red", "ok": True}})
        return dummy

    def test_a3_check_runs_once_lint_passes(self):
        """A3 lint 過了才跑 `verify-case.py check <n> --ref <base_sha>
        --candidate <這一輪的分支樹>`。"""
        dummy = self._setup_good_case()
        done = self.gate(dummy, "--ticket", "91")
        self.assertIn(CHECK_MARKER, done.stdout,
                     "lint 過了,check 應該要被叫到(cmd_check 開頭那一句沒有出現):\n"
                     + done.stdout + done.stderr)

    def test_a4_check_upgrades_the_stage_from_red_to_check(self):
        """A4 check 執行後,票的 verify.baseline.stage 從 red 升成 check。"""
        dummy = self._setup_good_case()
        before = self.load_ticket("91")
        self.assertEqual((before.get("verify") or {}).get("baseline", {}).get("stage"),
                         "red", "沙盒前置條件本身沒設對:出發前 stage 要是 red")
        self.gate(dummy, "--ticket", "91")
        after = self.load_ticket("91")
        stage = (after.get("verify") or {}).get("baseline", {}).get("stage")
        self.assertEqual(stage, "check",
                         "check 跑完之後,verify.baseline.stage 沒有從 red 升成 "
                         "check(現在是 %r):\n%s" % (stage, json.dumps(
                             after.get("verify"), ensure_ascii=False)))

    def test_a6_check_happens_inside_gate_after_regression_not_at_land(self):
        """A6 check 在 regression() 之後執行,而且只跑一次 gate.sh(從不呼叫
        land.sh)就看得到 —— 不是留到 land 才量。"""
        dummy = self._setup_good_case()
        done = self.gate(dummy, "--ticket", "91")
        self.assertIn(REGRESSION_MARKER, done.stdout,
                     "這張沙盒票沒有宣告 verify.tags,regression() 該印出那一句"
                     "(用來定位『regression 已經跑過』這件事):\n"
                     + done.stdout + done.stderr)
        self.assertIn(CHECK_MARKER, done.stdout,
                     "只跑了一次 gate.sh、完全沒有呼叫 land.sh,check 卻沒有被叫到"
                     "——量綠這件事不該等到 land 才發生:\n"
                     + done.stdout + done.stderr)
        regression_at = done.stdout.index(REGRESSION_MARKER)
        check_at = done.stdout.index(CHECK_MARKER)
        self.assertLess(regression_at, check_at,
                        "check 的輸出要在 regression() 的輸出之後才出現(D-020 §三:"
                        "「check 在 regression() 之後執行」):\n" + done.stdout)

    # ---------------------------------------------------------- A5

    def test_a5_a_failing_check_lands_in_status_failures_and_dispatches_autofix(self):
        """A5 check 判 ok:false(候選仍紅)—— 紅榜寫進 status.json 的
        failures[],走既有 auto-fix 派工路(D-014 既有機制,不重造)。"""
        dummy = self.mapped_test()
        red_rel = "verify/t91/test_alwaysred.py"
        self.write_case(red_rel, ALWAYS_RED_CASE)
        self.commit_all("沙盒:永遠紅的案例檔")
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, "#!/bin/sh\n"
                                 "echo fake-worker-ran >> \"$AC_TEST_LOG\"\n")
        config = json.loads(self.read("board/config.json"))
        config["worker"] = {"command": "sh %s" % worker, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        self.commit_all("沙盒:假 worker")
        self.make_ticket(91, allowed_write_paths=["*"],
                         verify={"files": [red_rel], "tags": []})
        done = self.gate(dummy, "--ticket", "91")
        self.assertNotEqual(done.returncode, 0,
                            "候選上這條案例永遠紅,check 判 ok:false 要讓整趟閘門非零:\n"
                            + done.stdout + done.stderr)
        data = self.status_of("91")
        self.assertTrue(data.get("failures"),
                        "check ok:false 時,status.json 的 failures[] 是空的(現在是 "
                        "%r)—— 紅榜沒有寫進去" % data.get("failures"))
        self.assertIn(AUTO_FIX_LINE, done.stdout,
                     "check 紅了應該走既有的 auto-fix 派工路(同 tests/test_gate.py "
                     "既有紅榜的派工斷言),但沒看到那一行:\n"
                     + done.stdout + done.stderr)

    def gate(self, *args):
        return self.run_sh("scripts/gate.sh", *args)


if __name__ == "__main__":
    unittest.main()
