"""#26 `verify-case.py red` 的四類紅分類 + `lint`(F1-F6)+ `ticket.py close` 認
`verify.baseline.stage` —— `docs/DESIGN-VERIFY-CASES.md` §三/§四(D-020)。

驗證者案例(角色:verifier,D-020 第一批只交紅的驗證者):**不搭參考實作、不做
變異、不等 patch**。十四條逐條對應票 #26 的 `acceptance`(與設計文件 §三「紅的
形狀分類表」、§四「F1-F6」逐列相同)。乾淨基底上 `red`/`lint` 兩個子指令都還不
存在(`scripts/verify-case.py` 只有 `check`/`extract`/`tags-merge`),所以每一條
在這裡都紅在**這個檔自己的斷言**——不是 import、不是缺符號、不是別處炸。

## 三組 fixture
- **A1-A7**(`RedClassification`):在沙盒裡疊一份案例檔到乾淨主線上跑
  `verify-case.py red`,用五種 traceback 形狀(AssertionError / import 失敗 /
  缺符號 / 別處炸 / skip)驗證分類與聚合判準(A6/A7)。案例檔本身不依賴任何「這張
  票才加的新符號」——A1-A7 問的是分類邏輯,不是接線。
- **A8-A13**(`Lint`):把一份違反單一 F 規則、其餘盡量乾淨的案例檔餵給
  `verify-case.py lint`,驗證六條規則各自命中。F6 需要一張真票的
  `verify_strings`,案例檔名走 `test_ticket_<n>.py` 命名慣例對應那張票。
- **A14**(`CloseGate`):`ticket.py close` 讀 `verify.baseline.stage`。**waiver
  放行那一半不是本票的新行為**(`waiver_covers_regression` 早就在關票前整段跳過
  baseline 檢查,不受 `stage` 影響)——這裡只證新加的判準:`stage=="red"` 該擋、
  `stage=="check"` 該放,同一個測試方法先斷言前者(今天就紅),後者留給實作落地
  後在同一輪裡一起跑。

## 怎麼做假
全部走 `tests/control_harness.py` 的 `Sandbox`(拋棄式真 git repo,`HOME`/
`GIT_CONFIG_*` 都指進沙盒);不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不猜 `red`/`lint` 沒寫清楚的輸出細節
以外的格式(訊息斷言只認票面逐字引用的那幾句)。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


# --------------------------------------------------------------- A1-A7 fixtures
# 期望值來源獨立於被測程式:dotted id 是從 rel path 手算的(`verify.redcase.
# test_ticket_1.<Class>.<method>`),不是跑一次記下來的。

CASE_A1 = """import unittest

TAGS = ["example"]


class ARedAssertion(unittest.TestCase):
    def test_one_is_not_two(self):
        self.assertEqual(1, 2, "1 != 2")
"""

CASE_A2 = """import unittest

from scripts.nonexistent_module_abc import ghost

TAGS = ["example"]


class NeedsAMissingModule(unittest.TestCase):
    def test_uses_it(self):
        self.assertTrue(ghost())
"""

# A3:AttributeError 直接炸在案例檔自己那一行(不經 getattr 預設值),最後一個
# frame 就是這個檔 —— 範本(`docs/DESIGN-VERIFY-CASES.md` §四)裡 A1 那個例子的
# 反面示範。
CASE_A3 = """import json
import unittest

TAGS = ["example"]


class AMissingSymbol(unittest.TestCase):
    def test_the_helper_exists_on_json(self):
        fn = json.not_a_real_json_function
        self.assertTrue(callable(fn))
"""

# A4:錯炸在既有產品碼裡面(`status.parse_failures` 的 `open()` 那一行),不是
# 案例檔自己那一行 —— 最後一個 frame 在 scripts/status.py。
CASE_A4 = """import unittest

import scripts.status as status

TAGS = ["example"]


class AnErrorFromProductCode(unittest.TestCase):
    def test_parse_failures_rejects_a_bad_path(self):
        status.parse_failures(None)
"""

CASE_A5 = """import unittest

TAGS = ["example"]


class MixedOutcomes(unittest.TestCase):
    def test_a_real_assertion(self):
        self.assertEqual(1, 2)

    @unittest.skip("尚未實作,先跳過")
    def test_not_ready_yet(self):
        self.fail("不該被跑到")
"""


class RedClassification(Sandbox):
    """A1-A7:`verify-case.py red` 對五種 traceback 形狀的分類,以及聚合判準。"""

    def tool(self, *args):
        return self.run_py("scripts/verify-case.py", *args)

    def a_ticket(self, ident, rels):
        self.make_ticket(ident, verify={"files": rels, "tags": ["example"],
                                        "run": "", "notes": ""})

    def red_record(self, done, out):
        """`red` 的紀錄不寫票(#36 A4):讀 stdout『證據 -> <路徑>』印出的那份
        `<out-dir>/baseline-red.json`。"""
        lines = [line for line in done.stdout.splitlines() if "證據 -> " in line]
        self.assertTrue(lines, "red 要在 stdout 印出證據檔路徑:" + done.stdout)
        path = os.path.join(out, "baseline-red.json")
        self.assertTrue(lines[0].split("證據 -> ", 1)[1].startswith(path), done.stdout)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_a1_an_assertion_error_in_the_case_file_counts_as_red(self):
        """A1 最後一個 frame 在 verify.files 之一且例外是 AssertionError(含
        self.fail)時算數的紅,印『紅 <案例>: <第一行>』並寫進 baseline.red_lines。
        """
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A1)
        self.a_ticket(1, [rel])
        expected_id = "verify.redcase.test_ticket_1.ARedAssertion.test_one_is_not_two"
        out = os.path.join(self.home, "red-out")
        done = self.tool("red", "1", "--candidate", self.repo, "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("紅 %s:" % expected_id, done.stdout)
        base = self.red_record(done, out)
        self.assertTrue(base["baseline"]["red_lines"], "算數的紅要寫進 red_lines")
        self.assertTrue(any(expected_id in str(row) for row in base["baseline"]["red_lines"]),
                        base["baseline"]["red_lines"])

    def test_a2_an_import_failure_does_not_count_as_red(self):
        """A2 ImportError/ModuleNotFoundError/_FailedTest 不算紅,印『import 失敗
        (不算紅)』。"""
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A2)
        self.a_ticket(1, [rel])
        done = self.tool("red", "1", "--candidate", self.repo)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"],
                         "不算的紅不准寫票")

    def test_a3_a_missing_symbol_error_in_the_case_file_does_not_count(self):
        """A3 AttributeError/NameError/FileNotFoundError/TypeError 且最後 frame
        在案例檔:不算紅,印『紅在缺符號(不算紅):改成先 assert 它存在』。"""
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A3)
        self.a_ticket(1, [rel])
        done = self.tool("red", "1", "--candidate", self.repo)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在缺符號(不算紅):改成先 assert 它存在", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_a4_an_error_that_blows_up_elsewhere_does_not_count(self):
        """A4 最後一個 frame 不在案例檔(產品碼或既有測試炸):不算紅,印『紅在
        別處(不算紅)』。"""
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A4)
        self.a_ticket(1, [rel])
        done = self.tool("red", "1", "--candidate", self.repo)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在別處(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_a5_a_skip_is_counted_separately_from_red(self):
        """A5 skip 另外分開計數,不歸進算數或不算的紅。"""
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A5)
        self.a_ticket(1, [rel])
        out = os.path.join(self.home, "red-out")
        done = self.tool("red", "1", "--candidate", self.repo, "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.red_record(done, out)
        self.assertEqual(base["baseline"].get("skipped"), 1,
                         "skip 要另外算,不是 0 也不是併進紅")
        red_names = " ".join(str(row) for row in base["baseline"]["red_lines"])
        self.assertNotIn("test_not_ready_yet", red_names, "skip 不准被算進紅")

    def test_a6_a_clean_red_run_writes_the_full_baseline_shape(self):
        """A6 cases>0 且算數的紅>=1 且三類不算的紅=0 時 rc=0,寫 verify.baseline =
        {stage:red, ok:true, files, base_ref, base_sha, baseline:{...,red_lines}},
        candidate_run:null。"""
        rel = os.path.join("verify", "redcase", "test_ticket_1.py")
        self.write(rel, CASE_A1)
        self.a_ticket(1, [rel])
        out = os.path.join(self.home, "red-out")
        done = self.tool("red", "1", "--candidate", self.repo, "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.red_record(done, out)
        self.assertEqual(base["stage"], "red")
        self.assertTrue(base["ok"])
        self.assertEqual(base["files"], [rel])
        self.assertTrue(base.get("base_ref"))
        self.assertTrue(base.get("base_sha"))
        self.assertIsNone(base["candidate_run"], "red 只跑一次,candidate_run 該是 null")
        self.assertTrue(base["baseline"]["red_lines"])

    def test_a7_any_uncounted_red_blocks_and_leaves_the_ticket_untouched(self):
        """A7 三類不算的紅任一出現時列出來、rc=1、不寫票(同 #22『量不到不動票』
        的原則)。"""
        good = os.path.join("verify", "redcase", "test_ticket_1.py")
        bad = os.path.join("verify", "redcase", "test_ticket_1b.py")
        self.write(good, CASE_A1)
        self.write(bad, CASE_A2)
        self.a_ticket(1, [good, bad])
        done = self.tool("red", "1", "--candidate", self.repo)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"],
                         "混了一個不算的紅,整張票都不准寫")


# ------------------------------------------------------------------ A8-A13
# 四段固定 docstring 見範本(`docs/DESIGN-VERIFY-CASES.md` §四);每份 fixture
# 只故意違反一條 F 規則,其餘盡量乾淨,讓斷言問得到「是哪一條規則命中」。

CASE_A8 = '''"""#9 demo:docstring 漏了一段。

## 驗收表
A1 | unit | 呼叫 do_thing() | 回傳 True | 手算

## 介面字串
(無)

## 怎麼做假
不需要假環境
"""
import unittest

TAGS = ["example"]


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 佔位。"""
        self.assertTrue(True)
'''

CASE_A9 = '''"""#9 demo:標籤沒登記。

## 驗收表
A1 | unit | 呼叫 do_thing() | 回傳 True | 手算

## 介面字串
(無)

## 怎麼做假
不需要假環境

## 不做
不改產品碼
"""
import unittest

TAGS = ["never-registered-tag-xyz"]


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 佔位。"""
        self.assertTrue(True)
'''

CASE_A10 = '''"""#9 demo:一個方法忘了標號。

## 驗收表
A1 | unit | 呼叫 do_thing() | 回傳 True | 手算

## 介面字串
(無)

## 怎麼做假
不需要假環境

## 不做
不改產品碼
"""
import unittest

TAGS = ["example"]


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 有標號的測試。"""
        self.assertTrue(True)

    def test_forgot_the_number(self):
        """忘記在第一行放驗收編號。"""
        self.assertTrue(True)
'''

CASE_A11 = '''"""#9 demo:字串裡藏了禁字。

## 驗收表
A1 | unit | 呼叫 do_thing() | 回傳 True | 手算

## 介面字串
NOTE = "reminder: 別對 supervisor pkill"

## 怎麼做假
不需要假環境

## 不做
不改產品碼
"""
import unittest

TAGS = ["example"]

NOTE = "reminder: 別對 supervisor pkill"


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 佔位。"""
        self.assertTrue(True)
'''

CASE_A12 = '''"""#9 demo:少了 addCleanup。

## 驗收表
A1 | unit | 呼叫 do_thing() | 回傳 True | 手算

## 介面字串
(無)

## 怎麼做假
tempfile.mkdtemp() 建一個真的暫存目錄

## 不做
不改產品碼
"""
import tempfile
import unittest

TAGS = ["example"]


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 佔位。"""
        where = tempfile.mkdtemp(prefix="demo-")
        self.assertTrue(where)
'''

CASE_A13 = '''"""#9 demo:模組頂層 import 了新符號。

## 驗收表
A1 | unit | 呼叫 brand_new_symbol() | 可呼叫 | 手算

## 介面字串
(無)

## 怎麼做假
不需要假環境

## 不做
不改產品碼
"""
import unittest

from scripts.newmod import brand_new_symbol

TAGS = ["example"]


class Demo(unittest.TestCase):
    def test_a1_documented(self):
        """A1 佔位。"""
        self.assertTrue(callable(brand_new_symbol))
'''


class Lint(Sandbox):
    """A8-A13:`verify-case.py lint` 的六條規則(F1-F6)。"""

    def tool(self, *args):
        return self.run_py("scripts/verify-case.py", *args)

    def test_a8_lint_flags_a_missing_docstring_section(self):
        """A8 F1:模組 docstring 缺 ## 驗收表/## 介面字串/## 怎麼做假/## 不做
        任一段落即報那一行。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A8)
        done = self.tool("lint", rel)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("## 不做", done.stdout + done.stderr)

    def test_a9_lint_flags_an_unregistered_tag(self):
        """A9 F2:TAGS 是字面 list 且已在 TAGS.md 或 TAGS.d 登記(既有邏輯延用)。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A9)
        done = self.tool("lint", rel)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("never-registered-tag-xyz", done.stdout + done.stderr)

    def test_a10_lint_names_missing_acceptance_numbers_without_blocking(self):
        """A10 F3:每個 test_ 方法 docstring 首行以驗收編號開頭(正則
        ^[A-Z]{0,2}[0-9]+(-[0-9]+)?),缺的印出來但不擋(rc 不因此非0)。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A10)
        done = self.tool("lint", rel)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("test_forgot_the_number", done.stdout)

    def test_a11_lint_flags_a_forbidden_word_even_inside_a_string_literal(self):
        """A11 F4:原始碼字串裡也不含 board/config.json 的 verify_lint.forbid
        禁字(預設 kill 家族)。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A11)
        done = self.tool("lint", rel)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("pkill", done.stdout + done.stderr)

    def test_a12_lint_flags_a_tempdir_without_addcleanup(self):
        """A12 F5:tempfile.mkdtemp/TemporaryDirectory 同一語句或下一行有
        addCleanup,缺的報那一行。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A12)
        done = self.tool("lint", rel)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("addCleanup", done.stdout + done.stderr)

    def test_a13_lint_flags_a_top_level_import_of_a_new_ticket_symbol(self):
        """A13 F6:模組頂層 import 沒有票面 verify_strings 裡『def <name>(』的
        新符號名字。"""
        rel = os.path.join("verify", "redcase", "test_ticket_9.py")
        self.write(rel, CASE_A13)
        self.make_ticket(9, verify_strings=["scripts/newmod.py:def brand_new_symbol("])
        done = self.tool("lint", rel)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("brand_new_symbol", done.stdout + done.stderr)


class CloseGate(Sandbox):
    """A14:`ticket.py close` 讀 `verify.baseline.stage`。waiver 放行那一半不是
    本票的新行為(`waiver_covers_regression` 早就在關票前整段跳過 baseline 檢查,
    不受 `stage` 影響)—— 這裡只證新加的判準。"""

    def setUp(self):
        super().setUp()
        self.write("src/nav.py", "def size_nav():\n    return 42\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線先有 size_nav")

    def a_closeable_ticket(self, ident, stage):
        rel = "tests/test_ticket_%s.py" % ident
        self.make_ticket(
            ident, allowed_write_paths=["src/*"],
            verify_strings=["src/nav.py:def size_nav"],
            test_evidence=[],
            verify={"files": [rel], "tags": [], "run": "", "notes": "",
                    "baseline": {
                        "ok": True, "stage": stage, "why": "",
                        "at": "2026-09-23T00:00:00+08:00",
                        "files": [rel], "base_ref": "main", "base_sha": "deadbeef",
                        "baseline": {"cases": 1, "red": ["demo"],
                                     "red_lines": ["demo: boom"], "skipped": 0},
                        "candidate_run": None}})
        self.ticket("set", str(ident), "review",
                   json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                              ensure_ascii=False))

    def test_a14_close_gates_on_verify_baseline_stage(self):
        """A14 ticket.py close 讀 verify.baseline.stage,只有 check 或票面有
        verify_waiver 時放行;stage=red 時拒絕並印出還缺 check。"""
        self.a_closeable_ticket(1, stage="red")
        still_red = self.ticket("close", "1")
        self.assertNotEqual(still_red.returncode, 0, still_red.stdout + still_red.stderr)
        self.assertIn("check", still_red.stdout + still_red.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Ready",
                         "stage=red 不該放行關票")

        self.a_closeable_ticket(2, stage="check")
        checked = self.ticket("close", "2")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.load_ticket("2")["state"], "Done")


if __name__ == "__main__":
    unittest.main()
