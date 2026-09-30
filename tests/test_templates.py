"""`templates/` 底下由腳本填的派工範本:**腳本要填的格子一個都不少**。

`templates/dispatch-reviewer.md` 由 `review.sh` 填 `@…@` 槽、由收件那一側抽檔尾的 `result`
塊 —— 少一格的派工文,與完整的那一份長得一樣。不比對逐字內容:範本是給人讀的,措辭會改;
**格子少一個才是回歸**。沒有腳本讀的範本與角色卡不在這裡釘(#71,稽核 §3.2)。
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")


def read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
        return handle.read()


class TheReviewerTemplate(unittest.TestCase):
    """#42 A1:`review.sh` 派覆核者用的範本 —— 四件事的佔位、固定格式、檔尾 `## result`。

    佔位是 `@…@`,由 `review.sh` 填(`tests/test_review.py` 驗「一個都不剩」);這裡只問
    範本本身少了哪一格 —— 少一格的派工文,與完整的那一份長得一樣。
    """

    def text(self):
        return read("dispatch-reviewer.md")

    def test_the_four_things_are_slots_for_the_script_to_fill(self):
        text = self.text()
        self.assertIn("四件事", text)
        block = text.split("四件事", 1)[1].split("\n## ", 1)[0]
        for slot in ("@TICKET@", "@SHA@", "@WORKTREE@", "@EVIDENCE@", "@STATUS@"):
            self.assertIn(slot, block, "四件事少了 %s 那一格" % slot)

    def test_it_asks_for_a_result_block_with_a_verdict(self):
        """**變異**:把範本檔尾的 result fence 改掉 → 這一條紅。"""
        tail = self.text().split("## result", 1)[1]
        self.assertIn("```result", tail)
        self.assertIn('"verdict"', tail)


if __name__ == "__main__":
    unittest.main()
