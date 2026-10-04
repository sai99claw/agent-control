"""`docs/DISPATCH-TEMPLATE.md`:通用版還留著該留的那幾節嗎。

為什麼要測一份文件:這一份**是派工時整份給出去的東西**(CLAUDE.md 那一條硬牆),所以
它少一節,就是每一張派工單少一條規矩 —— 而少掉的那一條不會有人發現,因為派工單讀起來
仍然完整。這一組不判斷文字寫得好不好,只釘「每個角色要的那幾節都找得到,而且兩張 result
鍵表列的是同一組鍵」。
"""

import json
import os
import re
import subprocess
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


class EveryRolePacks(unittest.TestCase):

    def test_every_role_packs_against_the_real_docs_without_a_missing_section(self):
        """每個角色的 `rules.py pack` 都找得到它要的每一節與它的角色卡(#71 A4)。

        節名的名單在 `rules.py`,文件改了章節編號而名單沒跟上,包頭就印「找不到這幾節」;
        角色卡不在就印 `(找不到 <路徑>)`。問的是名單與真文件對不對得上,不是文字寫得好不好。
        **變異**:把 `docs/DISPATCH-TEMPLATE.md` 的 `## 5.5` 改成 `## 5.5x` → 這一條紅。
        """
        rules = os.path.join(ROOT, "scripts", "rules.py")
        # `rules.py` 的根吃 `AC_ROOT`;派工帶著它時不釘住,讀到的是別棵樹的文件。
        env = dict(os.environ, AC_ROOT=ROOT)
        done = subprocess.run(["python3", rules, "roles"], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        roles = re.findall(r"^  (\w+) ", done.stdout, re.M)
        self.assertLessEqual({"consolidator", "design", "main", "opener", "reviewer",
                              "verifier", "worker"}, set(roles), done.stdout)
        # 每個角色用它 routing 那一格的模型(#87 A8:低於下限 `pack` 就 rc=2,例 design=fable);
        # 沒有路由格的角色用 opus。
        with open(os.path.join(ROOT, "board", "config.json"), encoding="utf-8") as handle:
            routing = json.load(handle).get("routing") or {}
        keys = {"opener": "open", "worker": "implement", "verifier": "verify",
                "main": "main", "design": "design"}
        for role in roles:
            model = routing.get(keys.get(role), "opus")
            pack = subprocess.run(["python3", rules, "pack", role, "--model", model],
                                  capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(pack.returncode, 0, role + ": " + pack.stderr)
            self.assertNotIn("找不到這幾節", pack.stdout, role)
            self.assertNotIn("(找不到 ", pack.stdout, role + ":角色卡不在")


class Template(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(PATH, encoding="utf-8") as handle:
            cls.text = handle.read()

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


if __name__ == "__main__":
    unittest.main()
