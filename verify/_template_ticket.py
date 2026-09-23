"""#<票號> <一句話:這一組案例守住哪個行為>

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:設計文件 / 手算 / 既有 golden)
A1 | unit    | <輸入或步驟>                  | <可觀察輸出>            | <期望值從哪來,例:docs/DESIGN-X.md §3>
A2 | api     | POST /api/x {…}                | 200 且 body.y == 3      | 手算:2+1
A3 | browser | 開 #/home,等 ready()          | 主控台沒有 TypeError    | 票面驗收第 3 條原話

## 介面字串(每條斷言靠哪個字串;票面沒定的在這裡定,EVIDENCE 不重抄)
REFUSAL = "擋下:…"          ← A2 斷言 stdout 含它
NEW_FN  = "environment_suspects" ← A1 用 getattr 取,不在模組頂層 import

## 怎麼做假(不上真埠、不起真服務、不殺行程、不鎖螢幕)
<例:PATH 前綴的假 df / 假 ioreg;沙盒 server 綁埠 0;斷線用假輸入模擬>

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字(落地 grep 會命中自己)
"""
import os
import shutil
import tempfile
import unittest

TAGS = ["<tag>"]   # 字面 list;verify.py 用 ast.literal_eval 讀。登記:verify/TAGS.d/<票號>.md 一行 `- `<tag>` — 說明(#<票號>)`

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
REFUSAL = "擋下:"


def a_workdir(case):
    """拋棄式目錄:同一行綁 addCleanup,lint(F5)才問得到它。"""
    where = tempfile.mkdtemp(prefix="t<票號>-"); case.addCleanup(shutil.rmtree, where, True)
    return where


class TheBehaviourUnderTest(unittest.TestCase):
    """一個 class 守一條驗收(或一組同 fixture 的驗收);名字寫成一句話。"""

    def test_a1_the_new_symbol_is_reached_through_getattr(self):
        """A1 讀端正規化函式存在且對空輸入回 []。

        乾淨基底上這一條要紅在下面第一個 assert(缺符號),不是紅在 import。
        """
        import scripts.status as status                     # 既有模組可以在這裡 import
        fn = getattr(status, "environment_suspects", None)
        self.assertIsNotNone(fn, "status.py 還沒有 environment_suspects()")
        self.assertEqual(fn({}), [])

    def test_a2_the_guard_refuses_and_names_the_next_step(self):
        """A2 低空間時秒退,stdout 含拒絕句與下一步。"""
        where = a_workdir(self)
        out = "<在 where 裡起假世界,跑被測腳本,收 stdout>"
        self.assertIn(REFUSAL, out)
