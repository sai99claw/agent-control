"""`scripts/rules.py pack <角色>`:按角色裁切、帶版本的規則包(D-015)。

2026-09-21 外部審查的 token 帳:「共用規矩重複載入 —— CLAUDE 要整份派工規範一起給,
角色卡卻說 prompt 只指路。」整份共用規矩是一萬多個字,而一個實作者真的會被擋到的是
其中幾節;驗證者要的是另外幾節。**每派一次工,每個 agent 各付一次整份的錢。**

所以這一組問三件事:
1. 抽出來的是**這個角色要的那幾節**,不是全部;
2. 壓得進位元組上限;
3. 砍掉的時候**說得出砍了哪一份** —— 砍掉而不說,與那一節不存在長得一樣。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


class RulesBase(Sandbox):

    def setUp(self):
        super(RulesBase, self).setUp()
        self.install_rules_sources()

    def rules(self, *args):
        return self.run_py("scripts/rules.py", *args)

    def source_size(self):
        return len(self.read(os.path.join("docs", "DISPATCH-TEMPLATE.md")).encode("utf-8"))


class WhatItPacks(RulesBase):

    def test_it_points_at_the_three_files_instead_of_pasting_them_whole(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for pointer in ("memory/role/implementer.md", "memory/model/opus.md",
                        "docs/DISPATCH-TEMPLATE.md"):
            self.assertIn(pointer, done.stdout, "沒有指路就等於要對方自己猜")

    def test_it_is_much_smaller_than_the_whole_thing(self):
        done = self.rules("pack", "worker", "--model", "opus")
        size = len(done.stdout.encode("utf-8"))
        self.assertLessEqual(size, 4096, "上限就是這一支的全部意義")
        self.assertLess(size, self.source_size() // 2,
                        "比整份的一半還大的話,省下來的不值得多一支腳本")

    def test_a_worker_gets_the_sections_that_bite_a_worker(self):
        """禁區與假綠家族是實作者真的會被擋到的兩節。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertIn("禁區", done.stdout)
        self.assertIn("5.5", done.stdout)

    def test_a_verifier_gets_a_different_cut(self):
        """驗證者不需要「副本 + patch」那一整節(它交的是案例,不是產品 patch)。"""
        worker = self.rules("pack", "worker", "--model", "opus").stdout
        verifier = self.rules("pack", "verifier", "--model", "sonnet").stdout
        self.assertNotEqual(worker, verifier)
        self.assertIn("副本 + patch", worker)
        self.assertNotIn("### 1. 副本 + patch", verifier)

    def test_it_names_the_version_it_was_cut_from(self):
        """規則包會被貼進派工文,而**一份不知道自己是哪一版的規則包**沒辦法被追。"""
        done = self.rules("pack", "worker", "--model", "opus")
        sha = self.git("rev-parse", "--short", "HEAD").strip()
        self.assertIn(sha, done.stdout)


class WhenItHasToCut(RulesBase):

    def test_cutting_says_which_file_was_cut(self):
        done = self.rules("pack", "worker", "--model", "opus", "--max-bytes", "1200")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 1200)
        self.assertIn("截斷", done.stdout)
        self.assertIn("砍過", done.stdout)

    def test_a_bigger_budget_carries_more(self):
        small = self.rules("pack", "worker", "--model", "opus",
                           "--max-bytes", "1500").stdout
        big = self.rules("pack", "worker", "--model", "opus",
                         "--max-bytes", "6000").stdout
        self.assertGreater(len(big.encode("utf-8")), len(small.encode("utf-8")))

    def test_a_section_that_is_no_longer_in_the_source_is_called_out(self):
        """名單與文件分岔時要**出聲**:一份靜靜少了兩節的規則包,與完整的那一份
        在畫面上長得一樣。

        **變異**:把 `missing` 那一段拿掉 → 這一條紅。
        """
        text = self.read(os.path.join("docs", "DISPATCH-TEMPLATE.md"))
        self.write(os.path.join("docs", "DISPATCH-TEMPLATE.md"),
                   text.replace("## 2. 禁區", "## 2222. 換了號碼的禁區"))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("找不到這幾節", done.stdout)
        self.assertIn("§2", done.stdout)


class HowItAnswers(RulesBase):

    def test_an_unknown_role_lists_the_ones_it_knows(self):
        """「不認得 X」只說了它不是什麼(`docs/DISPATCH-TEMPLATE.md` §5.7)。"""
        done = self.rules("pack", "dispatcher")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("worker", done.stderr)
        self.assertIn("verifier", done.stderr)

    def test_roles_prints_the_table_of_who_gets_what(self):
        done = self.rules("roles")
        self.assertEqual(done.returncode, 0, done.stderr)
        for role in ("worker", "verifier", "opener", "main"):
            self.assertIn(role, done.stdout)

    def test_a_missing_model_memory_does_not_break_the_pack(self):
        """模型記憶還沒有的那個模型也要派得出工。"""
        done = self.rules("pack", "worker", "--model", "nobody")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("memory/role/implementer.md", done.stdout)


if __name__ == "__main__":
    unittest.main()
