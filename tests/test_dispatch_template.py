"""`docs/DISPATCH-TEMPLATE.md`:通用版還留著該留的那幾節嗎。

為什麼要測一份文件:這一份**是派工時整份給出去的東西**(CLAUDE.md 那一條硬牆),所以
它少一節,就是每一張派工單少一條規矩 —— 而少掉的那一條不會有人發現,因為派工單讀起來
仍然完整。這一組不判斷文字寫得好不好,只釘「那幾節還在,而且專案特有的東西不在」。
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT  # noqa: E402

PATH = os.path.join(ROOT, "docs", "DISPATCH-TEMPLATE.md")
SCHEMA = os.path.join(ROOT, "tickets", "SCHEMA.md")


def result_keys(text, heading):
    """`heading` 之後第一張表、第一欄反引號內的鍵。"""
    rest = text[text.index(heading):]
    table = re.search(r"^\| 鍵 \|.*?\n((?:\|.*\n)+)", rest, re.M).group(1)
    return {m.group(1) for m in re.finditer(r"^\| `([^`]+)` \|", table, re.M)}


class Template(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(PATH, encoding="utf-8") as handle:
            cls.text = handle.read()

    def test_the_sections_that_have_to_survive_are_all_here(self):
        for heading in ("## 1. 副本 + patch", "## 2. 禁區", "## 3. 判綠",
                        "## 4. 不准收窄", "## 5. 變異驗紅",
                        "## 5.5", "## 6. 查證", "## 6.5 發現問題與處置問題要分開",
                        "## 8. 回報格式"):
            self.assertIn(heading, self.text, "少了一節")

    def test_the_false_green_table_is_still_the_whole_table(self):
        """§5.5 那張表是整份文件最貴的一段(一天之內的八個實例)。逐列釘住 ——
        少一列就是少一種認得出來的形狀。"""
        for row in ("管線的退出碼是右邊那支的",
                    "computed-value time 才無效",
                    "「答不出來」被寫成「答案是沒有」",
                    "檔案沒變,測試當然綠",
                    "兩種跑法的測試數要相同",
                    "照樣跑完整套九分鐘",
                    "沒有人看得出來的壞,跟沒有壞長得一樣",
                    "不存在的產品功能",
                    "在受控環境裡成立,與在真的路徑上成立,長得一樣"):
            self.assertIn(row, self.text, "§5.5 的表少了一列")

    def test_the_question_every_check_has_to_answer_is_still_spelled_out(self):
        self.assertIn("它答不出來的時候,長什麼樣?", self.text)

    def test_the_forbidden_zone_is_placeholders_not_one_project_s_ports(self):
        """禁區那三格由專案填。**空著的派工文不要發** —— 一份沒有禁區的派工文,跟
        一份禁區剛好是空的派工文長得一樣。"""
        for placeholder in ("<受保護的埠>", "<資料與秘密目錄>", "<副本裡不准跑的腳本>"):
            self.assertIn(placeholder, self.text)
        self.assertIn("templates/project-CLAUDE.md", self.text)

    def test_the_project_specific_test_traps_are_gone(self):
        """那一節是**會過期的**,而這一份是**不會過期的**。混在一起的那一刻,兩邊會
        一起被當成「上次那個專案的東西」跳過。"""
        self.assertNotIn("這個 code base 的三個測試陷阱", self.text)
        self.assertNotIn("i18n 第一期檔", self.text)
        self.assertIn("## 9. 專案特有的那幾節", self.text)

    def test_it_points_at_this_repo_s_own_tools_not_another_project_s(self):
        self.assertIn("scripts/gate.sh", self.text)
        self.assertIn("docs/REHEARSAL.md", self.text)
        self.assertIn("tickets/SCHEMA.md", self.text)
        self.assertNotIn("fullsuite.sh", self.text)
        self.assertNotIn("test-for.sh", self.text)

    def test_the_two_result_tables_list_the_same_keys(self):
        """§8.5 與 `tickets/SCHEMA.md` 的 result 鍵表**只有這兩份**(D-018);期望集合寫死在
        這裡,不從任何一份算 —— 兩份一起漏掉同一個鍵,集合比對仍然相等。

        **變異**:只在 SCHEMA 加 `baseline` 列、§8.5 不加 → 這一條紅(#44)。
        """
        with open(SCHEMA, encoding="utf-8") as handle:
            schema = handle.read()
        expected = {"ticket", "role", "round", "rc", "patch_sha256", "gate",
                    "mutations", "objection", "excluded", "repro", "memory",
                    "baseline"}
        self.assertEqual(result_keys(self.text, "## 8.5"), expected, "§8.5")
        self.assertEqual(result_keys(schema, "## EVIDENCE 尾端的 `result` 區塊"),
                         expected, "tickets/SCHEMA.md")

    def test_the_report_format_still_asks_for_verbatim_output_and_mutations(self):
        self.assertIn("測試輸出**逐字**", self.text)
        self.assertIn("變異驗紅表", self.text)
        self.assertIn("票寫錯、發現的別的問題", self.text)

    def test_the_report_format_asks_for_what_the_next_round_needs(self):
        """**變異**:把 §8 的第 5、6 點拿掉 → 這一條紅。

        2026-09-21 外部審查:每輪換新 worker,卻只傳上一輪 EVIDENCE —— 證據帳沒有
        要求保留已排除的假設與最小重現,三輪可能重查相同 code。
        """
        for phrase in ("已排除的假設", "最小重現"):
            self.assertIn(phrase, self.text, "少了給下一輪那個新的人的那一段")

    def test_an_objection_has_to_land_on_the_ticket_not_just_in_the_report(self):
        """**沒被收進票的反駁,與沒有反駁長得一樣**(外部審查 5.1)。"""
        self.assertIn("objections[]", self.text)
        self.assertIn("test_defect", self.text)
        self.assertIn("不准放寬斷言", self.text)


if __name__ == "__main__":
    unittest.main()
