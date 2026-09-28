"""#61(D-H38,稽核 R6):開題規則落到三份文件。

needs_verifier=false,這一支就是驗收。期望字樣來自票面 acceptance 原文,不從文件本身抄。

**變異**(實作者自證紅):
- M1 刪掉 opener.md 的 (c) 那一行 → `test_the_opener_card_has_all_three_rules` 紅
  (#664 只引在那一行,`test_the_opener_card_cites_every_counterexample` 也跟著紅)。
- M2 SCHEMA acceptance 那格拿掉「照產品既有慣例」→ `test_schema_acceptance_cell_says_user_visible_and_convention` 紅。
- M3 DISPATCH-TEMPLATE 拿掉 §7.1 → `test_the_dispatch_template_tells_the_implementer_how_to_read_it` 紅。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT  # noqa: E402


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


class OpenerRules(unittest.TestCase):

    def test_the_opener_card_has_all_three_rules(self):
        """A1:(a)(b)(c) 各一行,各帶自己的關鍵字。"""
        lines = read("memory/role/opener.md").splitlines()
        wanted = {"(a)": ("grep", "allowed_write_paths", "預期會動的檔"),
                  "(b)": ("使用者看到的結果", "照產品既有慣例"),
                  "(c)": ("其餘斷言不變", "oracle", "查過的檔")}
        for marker, phrases in wanted.items():
            rule = [line for line in lines if line.startswith(marker)]
            self.assertEqual(len(rule), 1, f"{marker} 那一行要在,而且只有一行")
            for phrase in phrases:
                self.assertIn(phrase, rule[0], marker)

    def test_the_opener_card_cites_every_counterexample(self):
        """A1:#658–#664 七張反例,引票號。"""
        text = read("memory/role/opener.md")
        for number in range(658, 665):
            self.assertIn(f"#{number}", text)

    def test_schema_acceptance_cell_says_user_visible_and_convention(self):
        """A2:SCHEMA 欄位表 acceptance 那一列。"""
        row = [line for line in read("tickets/SCHEMA.md").splitlines()
               if line.startswith("| 目標與範圍 |")]
        self.assertEqual(len(row), 1)
        for phrase in ("使用者看到的結果", "照產品既有慣例", "其餘斷言不變"):
            self.assertIn(phrase, row[0])

    def test_schema_write_paths_cell_says_grep_the_references(self):
        """A2 附帶:(a) 在 SCHEMA 的 allowed_write_paths 那一格也看得到。"""
        row = [line for line in read("tickets/SCHEMA.md").splitlines()
               if line.startswith("| 依賴與衝突 |")]
        self.assertEqual(len(row), 1)
        self.assertIn("grep 引用端", row[0])

    def test_the_dispatch_template_tells_the_implementer_how_to_read_it(self):
        """A3:DISPATCH-TEMPLATE §7 底下有對應的一節,指回角色卡。"""
        text = read("docs/DISPATCH-TEMPLATE.md")
        self.assertIn("### 7.1 驗收寫的是使用者看到的結果", text)
        section = text[text.index("### 7.1"):text.index("## 8. 回報格式")]
        for phrase in ("memory/role/opener.md", "照產品既有慣例", "其餘斷言不變",
                       "extra_paths"):
            self.assertIn(phrase, section)


if __name__ == "__main__":
    unittest.main()
