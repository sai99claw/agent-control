"""`templates/` 底下由腳本填的派工範本:**腳本要填的格子一個都不少**。

`templates/dispatch-reviewer.md` 由 `review.sh` 填 `@…@` 槽、由收件那一側抽檔尾的 `result`
塊 —— 少一格的派工文,與完整的那一份長得一樣。不比對逐字內容:範本是給人讀的,措辭會改;
**格子少一個才是回歸**。沒有腳本讀的範本與角色卡不在這裡釘(#71,稽核 §3.2)。
"""

import json
import os
import re
import sys
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


class TheVerifierTemplate(unittest.TestCase):
    """#113:`templates/dispatch-verifier.md` 的 `result` 範例收件端讀得到。

    #731/#772/#775 三次驗證者照範本的 ```json 片段寫成 `## result` + ```json,
    `ticket.result_block` 只認 ```result → `no-block`,verify 計畫沒併進票。
    判準用 `scripts/ticket.py` 那一份,不另抄 regex。
    """

    def text(self):
        return read("dispatch-verifier.md")

    def test_no_json_fence_left(self):
        """T1:範本沒有任何一行是 ```json 柵欄 —— 範本示範什麼,驗證者就抄什麼。"""
        hits = [line for line in self.text().splitlines()
                if re.match(r"^\s*```json", line)]
        self.assertEqual(hits, [])

    def test_the_result_example_parses(self):
        """T2:佔位換成 1 之後,`ticket.result_block` 取得出、解得開、鍵齊。

        **變異**:把範例柵欄改回 ```json → result_block 回 None,這一條紅。
        """
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        try:
            import ticket
        finally:
            sys.path.pop(0)
        filled = re.sub(r"@[A-Z_]+@", "1", self.text())
        raw = ticket.result_block(filled.splitlines())
        self.assertIsNotNone(raw, "範本裡沒有收件端認得的 ```result 區塊")
        data = json.loads(raw)
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("role"), "verifier")
        for key in ("ticket", "round", "rc", "verify", "baseline"):
            self.assertIn(key, data)

    def test_section_five_says_how_to_self_check(self):
        """T3:「第五樣」那一節帶自檢指令,並說 present 要是 true、柵欄要寫 result。

        **變異**:刪掉自檢那一句 → 這一條紅。
        """
        text = self.text()
        self.assertIn("\n## 第五樣", text)
        block = text.split("\n## 第五樣", 1)[1].split("\n## ", 1)[0]
        for word in ("ticket.py result", "present", "```result"):
            self.assertIn(word, block, "第五樣那一節少了 %s" % word)


if __name__ == "__main__":
    unittest.main()
