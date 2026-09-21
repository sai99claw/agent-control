"""`scripts/verify-case.py`:同一份案例,乾淨主線該紅、candidate 該綠。

2026-09-21 外部審查:「沒有『乾淨主線該紅』的機器檢查。」範本只要求驗證者貼兩份
Ran/OK,而**一份貼上來的輸出沒有辦法被機器比對** —— 票的 `verify` 裡沒有一格說得出
「乾淨主線上真的紅過」,於是一條永遠綠的斷言與一條真的在驗的斷言長得一模一樣。

這一組釘的是那一格能不能被偽造:
- 案例在兩邊都綠 → 不准算過(「一條都沒紅」)。
- 乾淨主線上是 `import` 炸掉 → 不算紅,而且要**明列**(那是還沒接上,不是驗到了)。
- 過了才寫進票的 `verify.baseline`,讓 `ticket.py close` 有東西可以問。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# 期望值來源獨立於被測程式:這個 2 是票面說的,不是跑一次記下來的。
CASE = """import unittest
TAGS = ["example"]


class NavSize(unittest.TestCase):
    def test_value(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""

ALWAYS_GREEN = """import unittest
TAGS = ["example"]


class Nothing(unittest.TestCase):
    def test_true(self):
        self.assertTrue(True)
"""

NEEDS_NEW_MODULE = """import unittest
from src.newthing import size

TAGS = ["example"]


class NeedsIt(unittest.TestCase):
    def test_size(self):
        self.assertEqual(size(), 2)
"""


class VerifyCase(Sandbox):

    def tool(self, *args):
        return self.run_py("scripts/verify-case.py", *args)

    def setUp(self):
        super().setUp()
        self.write("src/value.txt", "1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上的值是 1")
        self.write(os.path.join("verify", "nav", "__init__.py"), "")

    def a_ticket(self, case=CASE):
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})

    # ------------------------------------------------------------ 驗紅驗綠

    def test_red_on_clean_main_and_green_on_candidate_is_recorded(self):
        """**變異**:把 `cmd_check` 裡「乾淨主線上一條都沒紅」那一句拿掉 → 下一條紅。"""
        self.a_ticket()
        self.write("src/value.txt", "2\n")          # candidate 上的改動
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["baseline"]["red"], ["verify.nav.test_ticket_1.NavSize.test_value"])
        self.assertEqual(base["candidate_run"]["red"], [])
        self.assertEqual(base["baseline"]["cases"], 1)
        self.assertTrue(base["base_sha"], "沒記兩邊的 sha 就答不出這是對哪一版說的")
        self.assertTrue(base["candidate_sha"])

    def test_a_case_that_is_green_everywhere_does_not_count(self):
        """🩸 少了「乾淨主線上會紅」這一半,一個永遠綠的案例與一個真的在驗的案例
        長得一樣。"""
        self.a_ticket(ALWAYS_GREEN)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("一條都沒紅", done.stdout)
        self.assertFalse(self.load_ticket("1")["verify"]["baseline"]["ok"])

    def test_an_import_failure_is_listed_and_does_not_count_as_red(self):
        """**變異**:把 `IMPORT_MARKS` 那一段拿掉 → 這一條紅。

        乾淨主線上沒有那個新符號,案例 `import` 就會炸;它與真的驗到了一樣讓
        unittest 回非零,而下一步差很多。
        """
        self.a_ticket(NEEDS_NEW_MODULE)
        self.write("src/newthing.py", "def size():\n    return 2\n")
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertFalse(base["ok"])
        self.assertTrue(base["baseline"]["import_failures"])
        self.assertEqual(base["baseline"]["red"], [])

    def test_a_ticket_without_verify_files_says_so(self):
        self.make_ticket(1)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("verify.files 是空的", done.stderr)

    # -------------------------------------------------------------- extract

    def test_extract_writes_a_diff_of_only_the_verify_files(self):
        """驗證者的 `work/` 裡已經有實作者的 patch —— 手工挑檔遲早挑錯一個,
        而差分基準本來沒有寫死在任何地方(外部審查)。"""
        self.a_ticket()
        self.write("src/value.txt", "2\n")
        done = self.tool("extract", "1", "--out", os.path.join(self.repo, "patch-verify.diff"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read("patch-verify.diff")
        self.assertIn("--- base/verify/nav/test_ticket_1.py", text)
        self.assertIn("+++ work/verify/nav/test_ticket_1.py", text)
        self.assertNotIn("+++ work/src/", text, "只准含驗證檔,實作者那一半不進來")
        self.assertNotIn(self.repo, text, "檔頭不准是絕對路徑(D-012)")

    # ------------------------------------------------------------ 標籤登記

    def test_a_fragment_counts_as_registered_before_it_is_merged(self):
        """一票一個片段檔就不會撞;**合併那一步不准變成新的排隊點**。"""
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `nav-size` — 導覽列尺寸(#7)\n")
        self.write(os.path.join("verify", "nav", "test_ticket_7.py"),
                   "import unittest\nTAGS = ['nav-size']\n\n\n"
                   "class T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        done = self.run_py("scripts/verify.py", "--list")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("nav-size", done.stdout)

    def test_tags_merge_folds_the_fragments_in_and_sorts_them(self):
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `nav-size` — 導覽列尺寸(#7)\n")
        self.write(os.path.join("verify", "TAGS.d", "9.md"), "- `ledger-colour` — 帳本配色(#9)\n")
        done = self.tool("tags-merge")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read(os.path.join("verify", "TAGS.md"))
        self.assertLess(text.index("`example`"), text.index("`ledger-colour`"))
        self.assertIn("`nav-size`", text)

    def test_the_same_tag_with_two_different_meanings_is_loud(self):
        """同名不同說明 = 兩張票對同一個標籤的理解不一樣。靜靜取其中一份,那個分歧
        會在半年後以「這個 tag 到底在守什麼」的形式回來。"""
        self.write(os.path.join("verify", "TAGS.d", "7.md"), "- `example` — 別的意思\n")
        done = self.tool("tags-merge")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("撞名", done.stderr)


if __name__ == "__main__":
    unittest.main()
