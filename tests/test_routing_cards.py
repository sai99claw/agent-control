"""#87 A6:`board/config.json` 路由表的每個值都要有模型卡。

路由表指到一個沒有卡的模型,`rules.py pack --model <它>` 就產不出規則包(#74:指定了卻
沒有卡是錯),而那一刻是派工的時候,不是改路由表的時候。這一條把它提前到改路由表的
那一份 patch:守衛與它守的卡同一份 patch 才綠。

期望值的來源是真的 `board/config.json` 與真的 `memory/model/`,卡名的換算走
`rules.model_card`(工具前綴只取模型那一半)—— 與派工時找卡是同一條路。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS  # noqa: E402

sys.path.insert(0, SCRIPTS)
import rules  # noqa: E402


class EveryRoutingValueHasACard(unittest.TestCase):

    def test_every_routing_value_resolves_to_an_existing_card(self):
        """**變異 M18**:刪掉 memory/model/gemini-3.8-flash-high.md → 這一條紅。"""
        with open(os.path.join(ROOT, "board", "config.json"), encoding="utf-8") as handle:
            routing = json.load(handle).get("routing") or {}
        self.assertTrue(routing, "board/config.json 沒有 routing —— 這一條什麼都沒驗")
        missing = []
        for key, value in sorted(routing.items()):
            card = rules.model_card(ROOT, value)
            if not card or not os.path.isfile(os.path.join(ROOT, card)):
                missing.append("routing.%s=%s → %s" % (key, value, card or "(空)"))
        self.assertEqual(missing, [], "路由表指到沒有卡的模型:\n" + "\n".join(missing))

    def test_the_gemini_card_is_the_pinned_one(self):
        """票面釘死的內容:front matter `cap_chars: 2000`、正文一行用途。"""
        text = open(os.path.join(ROOT, "memory", "model", "gemini-3.8-flash-high.md"),
                    encoding="utf-8").read()
        self.assertIn("cap_chars: 2000", text.split("---")[1])
        self.assertIn("用於 routing.second_opinion / discuss", text)


if __name__ == "__main__":
    unittest.main()
