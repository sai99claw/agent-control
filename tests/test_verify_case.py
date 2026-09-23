"""`scripts/verify-case.py`:同一份案例,乾淨主線該紅、candidate 該綠。

2026-09-21 外部審查:「沒有『乾淨主線該紅』的機器檢查。」範本只要求驗證者貼兩份
Ran/OK,而**一份貼上來的輸出沒有辦法被機器比對** —— 票的 `verify` 裡沒有一格說得出
「乾淨主線上真的紅過」,於是一條永遠綠的斷言與一條真的在驗的斷言長得一模一樣。

這一組釘的是那一格能不能被偽造:
- 案例在兩邊都綠 → 不准算過(「一條都沒紅」)。
- 乾淨主線上是 `import` 炸掉 → 不算紅,而且要**明列**(那是還沒接上,不是驗到了)。
- 過了才寫進票的 `verify.baseline`,讓 `ticket.py close` 有東西可以問。

2026-09-23(#26 / D-020):`red` 與 `lint` 兩個子指令,以及 `close` 只認 `stage=="check"`。
釘的是**四種紅分不分得開**:看起來紅、其實是還沒接上的紅有三種(import 失敗、紅在缺
符號、紅在別處),它們與「驗到了」一樣讓 unittest 回非零。揉成同一句「N 條紅」的那一刻,
驗證者交的紅與一份還沒接上的案例長得一樣。

`close` 那幾條本來該住 `tests/test_ticket.py`,#26 的 `allowed_write_paths` 沒有它 ——
所以放在這一份的最後一個 class 裡(EVIDENCE 有記)。
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

# 紅在案例檔自己、但例外不是 AssertionError:`dict` 沒有 `missing_key` 這個屬性。
MISSING_SYMBOL = """import unittest
TAGS = ["example"]


class NeedsSymbol(unittest.TestCase):
    def test_symbol(self):
        where = {}
        self.assertEqual(where.missing_key(), 2)
"""

# 紅在產品碼:最後一個 frame 是 src/boom.py,不是案例檔。
RED_ELSEWHERE = """import unittest
TAGS = ["example"]


class Elsewhere(unittest.TestCase):
    def test_product_blows_up(self):
        import src.boom
        src.boom.go()
"""

# 案例用 `subprocess` 跑工具,再把工具的**輸出**當 `assertEqual` 的訊息:那份輸出裡
# 的整段 traceback 排在 `AssertionError:` 那一行**後面**,而紅是紅在案例檔自己這一行。
RED_AFTER_A_SUBPROCESS = """import subprocess
import sys
import unittest

TAGS = ["example"]


class ToolExitCode(unittest.TestCase):
    def test_the_tool_exits_zero(self):
        done = subprocess.run([sys.executable, "src/tool.py"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
"""

# 與上面同一條紅,差別只在**測試方法有 docstring**:unittest 會把它的第一行印在
# `FAIL:` 標頭下面,而那一行不是 traceback。
RED_WITH_A_DOCSTRING = """import unittest

TAGS = ["example"]


class NavSize(unittest.TestCase):
    def test_value(self):
        \"\"\"A1 主線上的值是 2。\"\"\"
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""

# 一條算數的紅 + 一條不算的紅同一檔:算數的那一條**蓋不過**不算的那一條。
MIXED_RED = """import unittest
TAGS = ["example"]


class Mixed(unittest.TestCase):
    def test_value(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")

    def test_symbol(self):
        import src.thing
        self.assertEqual(src.thing.size(), 2)
"""

# skip 與算數的紅同一檔:skip 要另外數,不歸進任何一邊。
SKIP_AND_RED = """import unittest
TAGS = ["example"]


class Mixed(unittest.TestCase):
    @unittest.skip("沙盒:這一條跳過")
    def test_skipped(self):
        self.fail("不該跑到這裡")

    def test_value(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""

# lint 的基準:F1–F6 六條都過。每一條變異都從這一份改一個地方。
GOOD_CASE = '''"""#1 一句話:這一組案例守住哪個行為。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 起一顆拋棄式目錄 | 目錄在 | 票面驗收第 1 條

## 介面字串
REFUSAL = "擋下:"

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬票面驗收。
"""
import shutil
import tempfile
import unittest

TAGS = ["example"]

REFUSAL = "擋下:"


class TheValue(unittest.TestCase):

    def test_a1_a_throwaway_dir_is_bound_to_cleanup(self):
        """A1 拋棄式目錄同一行綁 addCleanup。"""
        where = tempfile.mkdtemp(prefix="t1-"); self.addCleanup(shutil.rmtree, where, True)
        self.assertTrue(where)
'''

NEEDS_NEW_MODULE = """import unittest
from src.newthing import size

TAGS = ["example"]


class NeedsIt(unittest.TestCase):
    def test_size(self):
        self.assertEqual(size(), 2)
"""


class CaseSandbox(Sandbox):
    """主線上的值是 1、`verify/nav/` 在、一張票指著一份案例。

    **這個 class 自己沒有 test_**:子類別繼承過去的 test_ 會被 unittest 再跑一次,而
    同一組斷言跑三遍的那幾秒沒有換到任何新的資訊。
    """

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


class VerifyCase(CaseSandbox):

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

    # ------------------------------------------ candidate 的三種寫法(#22)

    def a_branch(self, branch="t1", case=CASE):
        """票的實作與案例都 commit 在分支上,主線還停在舊值 —— 落地**前**的真實形狀。

        關鍵是 repo 的工作樹裡**沒有**那個案例檔:只有真的去 git 裡把 candidate 那棵樹
        拿出來,才跑得到它。
        """
        path = self.worktree(branch)
        self.write("src/value.txt", "2\n", where=path)
        self.write(os.path.join("verify", "nav", "__init__.py"), "", where=path)
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case, where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "值改成 2,附案例", cwd=path)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        return path, self.git("rev-parse", branch).strip()

    def assert_measured(self, sha):
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["baseline"]["red"],
                         ["verify.nav.test_ticket_1.NavSize.test_value"])
        self.assertEqual(base["candidate_run"]["red"], [])
        self.assertEqual(base["candidate_sha"], sha)
        self.assertEqual(base["base_sha"], self.load_ticket("1")["base_sha"])
        return base

    def test_candidate_may_be_a_branch_name(self):
        """🩸 #20:`--candidate t20` 舊版去找 `$PWD/t20`,印「candidate 裡找不到這幾個
        案例檔」—— 而檔就在 t20 上,只是沒有人去 git 裡拿。

        **變異**:把 `resolve_tree` 裡 `rev-parse --verify` 那一段拔掉 → 這一條紅。
        """
        _, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", "t1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.assert_measured(sha)["candidate"], "t1")

    def test_candidate_may_be_a_sha(self):
        """落地後補量走的是 `--ref <base_sha> --candidate <merge sha>`,兩格都是 sha。"""
        _, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", sha)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_measured(sha)

    def test_candidate_may_be_a_worktree_path(self):
        """路徑這一種是舊的叫法,**不准被新的解法吃掉**(同名目錄優先於同名分支)。"""
        path, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_measured(sha)

    def test_the_case_file_is_overlaid_onto_a_ref_tree_that_lacks_it(self):
        """案例是這張票才加的,ref 那棵樹上本來就沒有它 —— 不疊上去,乾淨主線那一趟
        跑到的是**零個案例**,而零個案例與「都過了」長得一樣。"""
        _, _ = self.a_branch()
        out = os.path.join(self.home, "vc")
        done = self.tool("check", "1", "--candidate", "t1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(os.path.join(
            out, "base", "verify", "nav", "test_ticket_1.py")), "沒疊上去")
        self.assertEqual(self.load_ticket("1")["verify"]["baseline"]["baseline"]["cases"], 1)

    def test_after_landing_the_ref_defaults_to_the_ticket_base_sha(self):
        """🩸 #19:票落地之後,對**主線**量基準永遠是「一條都沒紅」—— 實作已經在主線
        上了。那一句說的是「這一趟量錯了地方」,不是「這條案例是假的」。

        **變異**:把預設的 ref 改回 `ticketlib.main_branch()` → 這一條紅。
        """
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        opened_at = self.load_ticket("1")["base_sha"]
        self.write("src/value.txt", "2\n")                 # 落地:主線的頭往前走
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), CASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "#1 落地")
        self.assertNotEqual(self.git("rev-parse", "main").strip(), opened_at)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["base_sha"], opened_at, "量的不是開票那一版")

    # --------------------------------------------------- 量不到 ≠ 驗紅沒過

    def test_a_baseline_that_cannot_be_measured_does_not_overwrite_the_ticket(self):
        """🩸「這一趟沒量到」與「這條案例驗不到東西」在票上長得一樣,而 `ticket.py
        close` 只看得到 `ok: false` —— #19 就是這樣卡死的:一句量錯地方的結論蓋掉了
        票上已經有的判決,而且再也沒有人分得出來。

        **變異**:把失敗路徑改回蓋寫票(`unmeasured` 換成走 record 那一段)→ 這一條紅。
        """
        self.a_ticket()
        row = self.load_ticket("1")
        row["verify"]["baseline"] = {"ok": True, "why": "",
                                     "at": "2026-09-22T00:00:00+08:00"}
        row["state_version"] = 7
        self.write(os.path.join("tickets", "1.json"),
                   json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        done = self.tool("check", "1", "--candidate", "沒有這個分支")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("下一步", done.stderr, "守衛的產出要是一個可以敲的動作")
        after = self.load_ticket("1")
        self.assertTrue(after["verify"]["baseline"]["ok"], "量不到卻把票上的判決蓋掉了")
        self.assertEqual(after["state_version"], 7, "量不到的那一趟不該動票")

    def test_a_ref_that_already_contains_the_candidate_is_not_a_verdict(self):
        """🩸 #19:票落地之後拿 `--ref main` 去量,主線上**已經有**那份實作 ——
        「一條都沒紅」說的是這一趟量錯了地方,不是這條案例是假的。舊版把它當判決
        蓋進票,`close` 從此擋著那張票,而票面上再也看不出差別。

        **變異**:把 `merge-base --is-ancestor` 那一段拔掉 → 這一條紅。
        """
        self.a_branch()
        landed = self.worktree("landed")
        self.git("merge", "-q", "--no-ff", "-m", "#1 落地", "t1", cwd=landed)
        done = self.tool("check", "1", "--ref", "landed", "--candidate", "t1")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("本來就有這份實作", done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_case_files_missing_from_the_candidate_is_not_a_verdict_either(self):
        """#20 印的就是這一句。它說的是「這一趟拿錯樹」,不是「驗紅沒過」。"""
        self.a_branch()
        done = self.tool("check", "1", "--candidate", "main")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("找不到這幾個案例檔", done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

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


class RedIsTheOnlyThingTheVerifierMeasures(CaseSandbox):
    """`red`:乾淨基底上跑一次,四種形狀分開數(#26 / D-020 §三)。

    **綠不在這裡量**:驗證者在時間上拿不到實作者的 patch,要它證綠等於要它自己搭一份
    參考實作 —— 那是 #23 那 340K 的來源。
    """

    def test_an_assertion_in_the_case_file_counts_and_lands_in_red_lines(self):
        """**變異**:把 `shapes` 裡 `row["kind"] == "FAIL" or …` 換成 `False`
        → 這一條紅(算數的紅變成 0,rc 從 0 變 1)。"""
        self.a_ticket()
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("紅 verify.nav.test_ticket_1.NavSize.test_value", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertEqual(base["stage"], "red")
        self.assertTrue(base["ok"], base["why"])
        self.assertIsNone(base["candidate_run"], "red 不跑 candidate —— 綠不是驗證者的事")
        self.assertEqual(base["files"], ["verify/nav/test_ticket_1.py"])
        self.assertEqual(base["base_sha"], self.load_ticket("1")["base_sha"])
        self.assertEqual(len(base["baseline"]["red_lines"]), 1)
        self.assertIn("AssertionError", base["baseline"]["red_lines"][0],
                      "red_lines 要留紅訊息的第一行,主線覆核時就是掃這一句")

    def test_a_case_that_is_green_on_the_clean_base_is_not_a_baseline(self):
        """🩸 一個永遠綠的案例與一個真的在驗的案例長得一樣。"""
        self.a_ticket(ALWAYS_GREEN)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("一條算數的紅都沒有", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"], "不成立就不動票")

    def test_an_import_failure_does_not_count_and_the_ticket_is_not_touched(self):
        """🩸 乾淨基底上沒有那個新符號,案例 `import` 就會炸 —— 那是**還沒接上**。

        **變異**:把 `IMPORT_MARKS` 那一段拿掉 → 這一條紅(它會被算成缺符號那一類)。
        """
        self.a_ticket(NEEDS_NEW_MODULE)
        self.write("src/newthing.py", "def size():\n    return 2\n")
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_a_red_that_is_only_a_missing_symbol_does_not_count(self):
        """🩸 `AttributeError` 也讓 unittest 回非零,而它說的是「這個名字還不存在」。

        **變異**:把 `shapes` 的 `missing_symbol` 那一支改成走 `red` → 這一條紅。
        """
        self.a_ticket(MISSING_SYMBOL)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在缺符號(不算紅):改成先 assert 它存在", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_a_red_whose_last_frame_is_product_code_does_not_count(self):
        """🩸 產品碼炸掉不是這幾條案例在說話 —— 而它與驗到了一樣是一條紅。

        **變異**:把 `frame_in` 的 `endswith` 那一段改成永遠 True → 這一條紅。
        """
        self.write("src/boom.py", 'def go():\n    raise AssertionError("產品碼自己炸了")\n')
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "產品碼會炸的那一版")
        self.a_ticket(RED_ELSEWHERE)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在別處(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def a_tool_that_blows_up(self):
        """乾淨基底上有一支自己會炸的工具 —— 案例跑它、拿它的輸出當紅訊息。"""
        self.write("src/tool.py",
                   'def go():\n    raise ValueError("工具自己炸了")\n\n\ngo()\n')
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "會炸的工具")

    def test_a_red_after_a_subprocess_still_counts_as_the_case_files_own(self):
        """🩸 工具的 traceback 在**訊息裡**,排在例外那一行後面;照字面取最後一個
        frame 會指到工具裡的檔,於是一條紅在案例檔自己斷言的紅被算成「紅在別處」
        —— #26 那 14 條驗證者案例就是這樣全被判成不算紅的(#31)。

        **變異**:`traceback_end` 裡那一行 `if not any(mark in text …CHAIN_MARKS):`
        換成 `if False:`(= 回到「整段文字裡最後一個 frame」)→ 這一條紅。
        """
        self.a_tool_that_blows_up()
        self.a_ticket(RED_AFTER_A_SUBPROCESS)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("紅在別處", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertEqual(base["baseline"]["red"],
                         ["verify.nav.test_ticket_1.ToolExitCode.test_the_tool_exits_zero"])
        self.assertEqual(base["baseline"]["elsewhere"], [])
        self.assertIn("AssertionError", base["baseline"]["red_lines"][0],
                      "型別要讀案例自己丟的那一個,不是工具訊息裡那一個")

    def test_a_case_method_with_a_docstring_still_has_its_traceback_read(self):
        """🩸 `unittest` 把方法 docstring 的第一行印在 `FAIL:` 標頭下面 —— 那一行不是
        traceback。把它當 body 的第一行,標頭與 traceback 之間那條分隔線就把整筆收掉,
        `excerpt` 裡只剩一行中文:frame 讀不到、型別讀不到,分類只能說「紅在別處」。

        **變異**:`parse_reds` 裡那一行 `if not status.parse_head(lines[index]):`
        換成 `if True:`(= 說明行不再拿掉)→ 這一條紅。
        """
        self.a_ticket(RED_WITH_A_DOCSTRING)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("紅在別處", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertEqual(base["baseline"]["red"],
                         ["verify.nav.test_ticket_1.NavSize.test_value"])
        self.assertIn("AssertionError", base["baseline"]["red_lines"][0])

    def a_half_finished_product(self):
        """`src/thing.py` 在,但裡面還沒有 `size()` —— 乾淨基底上那是 `AttributeError`
        (不算紅),而候選補上 `size()` 之後同一條會綠。"""
        self.write("src/thing.py", "# size() 還沒寫\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "src/thing.py 還沒有 size()")

    def test_one_counted_red_does_not_cover_for_a_red_that_does_not_count(self):
        """🩸 一條算數的紅**蓋不過**一條不算的紅 —— 三類不算的紅任一出現就不成立。

        **變異**:把 `cmd_red` 的 `why = not_counted(run, "乾淨基底")` 換成 `why = []`
        → 這一條紅。只問「有沒有算數的紅」的版本會放它過,而那份 baseline 裡有一條
        說的是「還沒接上」。
        """
        self.a_half_finished_product()
        self.a_ticket(MIXED_RED)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅 verify.nav.test_ticket_1.Mixed.test_value", done.stdout)
        self.assertIn("紅在缺符號(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_check_also_refuses_a_baseline_whose_red_is_half_not_connected(self):
        """同一件事在 `check` 那一邊:基底上一條算數的紅 + 一條缺符號的紅,候選兩條都
        綠 —— 舊的判準(只問 `baseline["red"]` 空不空)會把它算成過。

        **變異**:把 `cmd_check` 的 `why = not_counted(baseline, "乾淨主線")` 換成
        `why = []` → 這一條紅。
        """
        self.a_half_finished_product()
        self.a_ticket(MIXED_RED)
        self.write("src/value.txt", "2\n")
        self.write("src/thing.py", "def size():\n    return 2\n")
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("紅在缺符號", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertFalse(base["ok"], base["why"])
        self.assertEqual(base["candidate_run"]["red"], [], "候選那一邊是綠的")

    def test_a_skip_is_counted_apart_from_both_kinds_of_red(self):
        self.a_ticket(SKIP_AND_RED)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        run = self.load_ticket("1")["verify"]["baseline"]["baseline"]
        self.assertEqual(run["cases"], 2)
        self.assertEqual(run["skipped"], 1)
        self.assertEqual(len(run["red"]), 1, "skip 不准算成紅")
        self.assertEqual(run["missing_symbol"], [])
        self.assertEqual(run["elsewhere"], [])

    def test_a_ticket_without_verify_files_says_so(self):
        self.make_ticket(1)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("verify.files 是空的", done.stderr)

    def test_a_base_that_cannot_be_measured_does_not_write_the_ticket(self):
        """量不到的下一步是一句可以敲的指令,而且**要指名 red**(不是 check)。"""
        self.a_ticket()
        done = self.tool("red", "1", "--candidate", "沒有這個分支")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("verify-case.py red 1", done.stderr)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_check_upgrades_the_same_slot_from_red_to_check(self):
        """🩸 `stage` 是同一格的**升級**,不是第二格 —— 兩格會長成兩種形狀(D-018)。"""
        self.a_ticket()
        self.assertEqual(self.tool("red", "1").returncode, 0)
        self.write("src/value.txt", "2\n")
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertEqual(base["stage"], "check")
        self.assertTrue(base["candidate_run"], "check 才量 candidate")

    def test_a_candidate_red_that_is_only_a_missing_symbol_still_fails_check(self):
        """🩸「不算紅」那張表問的是**基底紅得對不對**,不是候選綠不綠:候選上一條
        `AttributeError` 說的是實作還沒接上。

        **變異**:把 `cand_bad` 裡的 `missing_symbol` 那一項拿掉 → 這一條紅。
        """
        self.a_ticket(MISSING_SYMBOL)
        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("candidate 上還有", done.stdout)
        self.assertFalse(self.load_ticket("1")["verify"]["baseline"]["ok"])


class LintIsTheFormatRuleInMachineForm(CaseSandbox):
    """`lint`:F1–F6(#26 / D-020 §四)。**指名行號** —— 一句「格式不合」要人自己去找
    是哪一行,那一份退件與沒有退件一樣貴。"""

    REL = "verify/nav/test_ticket_1.py"

    def lint(self, case, *args):
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case)
        return self.tool("lint", self.REL, *args)

    def test_a_case_that_follows_the_template_passes_all_six(self):
        done = self.lint(GOOD_CASE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("lint: 過", done.stdout)
        self.assertIn("A1", done.stdout, "宣告到的驗收編號要印出來給主線對照")

    def test_f1_names_the_line_of_a_missing_docstring_section(self):
        done = self.lint(GOOD_CASE.replace(
            "## 不做\n不改產品碼;不放寬票面驗收。\n", ""))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F1 模組 docstring 缺 ## 不做", done.stdout)
        self.assertIn("%s:1" % self.REL, done.stdout, "要指名行")

    def test_f2_an_unregistered_tag_is_named(self):
        done = self.lint(GOOD_CASE.replace('TAGS = ["example"]', 'TAGS = ["nope"]'))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F2 標籤未登記", done.stdout)

    def test_f2_a_tags_that_is_not_a_literal_list_is_named(self):
        """`verify.py` 用 `ast.literal_eval` 讀 TAGS —— 算出來的那一份它讀不到,而
        「讀不到」與「沒宣告」在回歸選案例時長得一樣。"""
        done = self.lint(GOOD_CASE.replace('TAGS = ["example"]',
                                           'TAGS = list(("example",))'))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F2 TAGS 不是字面 list", done.stdout)

    def test_f3_a_method_without_an_acceptance_number_is_printed_but_does_not_block(self):
        """🩸 F3 **不擋**:擋下去只會讓人為了過 lint 編一個號碼進去。"""
        done = self.lint(GOOD_CASE.replace('"""A1 拋棄式目錄同一行綁 addCleanup。"""',
                                           '"""拋棄式目錄同一行綁 addCleanup。"""'))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("F3", done.stdout)
        self.assertIn("(一條都沒宣告)", done.stdout)

    def test_f4_a_forbidden_word_is_named_even_inside_a_string(self):
        """禁字在**原始碼**上掃,不是 ast:寫在字串裡的那一招也要抓得到。"""
        done = self.lint(GOOD_CASE + '\nSTOP = "os.kill(pid, 9)"\n')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F4 禁字", done.stdout)

    def test_f4_reads_the_forbidden_list_from_the_board_config(self):
        """埠那一族是**專案特有**的,走 `board/config.json` 的 `verify_lint.forbid`
        —— 寫進這一支就是把專案的東西塞進共用工具(§9)。"""
        conf = json.loads(self.read("board/config.json"))
        conf["verify_lint"] = {"forbid": ["1890[3-5]"]}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        done = self.lint(GOOD_CASE + '\nPORT = 18904\n')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F4 禁字 1890[3-5]", done.stdout)

    def test_f5_a_throwaway_dir_without_cleanup_is_named(self):
        done = self.lint(GOOD_CASE.replace(
            'where = tempfile.mkdtemp(prefix="t1-"); '
            'self.addCleanup(shutil.rmtree, where, True)',
            'where = tempfile.mkdtemp(prefix="t1-")'))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F5 mkdtemp() 同一語句或下一行沒有 addCleanup", done.stdout)

    def test_f6_importing_the_new_symbol_at_module_level_is_named(self):
        """🩸「import 失敗不算紅」的預防版:與其事後不算,不如寫的時候就擋。"""
        self.make_ticket(1, verify_strings=["src/nav.py:def size_nav("])
        done = self.lint(GOOD_CASE.replace(
            "import shutil", "import shutil\nfrom src.nav import size_nav"),
            "--ticket", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F6 模組頂層 import 了票面的新符號 size_nav", done.stdout)
        self.assertIn("票 #1 有 1 條驗收", done.stdout)

    def test_f6_takes_the_ticket_from_the_test_ticket_n_filename(self):
        """🩸 `--ticket` 是選項,而閘門與驗證者都是整批餵檔名進來:少了檔名這條路,
        F6 在真的會跑的那一趟一次都不會查(#26 第 2 輪紅在這裡)。

        **變異**:把 `cmd_lint` 裡 `ticket_of_case(rel)` 那一段拿掉 → 這一條紅。
        """
        self.make_ticket(1, verify_strings=["src/nav.py:def size_nav("])
        done = self.lint(GOOD_CASE.replace(
            "import shutil", "import shutil\nfrom src.nav import size_nav"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("F6 模組頂層 import 了票面的新符號 size_nav", done.stdout)

    def test_without_a_ticket_f6_is_not_checked(self):
        """拿不到票就查不了「哪些符號是新的」—— 猜出來的那一份會擋掉既有模組。
        檔名是 `test_ticket_1.py` 而票 #1 不存在,所以檔名那條路也拿不到票。"""
        done = self.lint(GOOD_CASE.replace(
            "import shutil", "import shutil\nfrom src.nav import size_nav"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_case_file_that_does_not_parse_is_a_finding_not_a_crash(self):
        done = self.lint(GOOD_CASE + "\ndef (:\n")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("解不開", done.stdout)


class CloseOnlyCountsTheGateRun(Sandbox):
    """`ticket.py close` 只認 `verify.baseline.stage == "check"`(#26 / D-020 C5)。

    驗證者交的那一趟說的是「這條案例真的在驗東西」,不是「這張票的東西真的做出來了」
    —— 少了後半的票與做完的票在票面上長得一樣。

    (這幾條本來該住 `tests/test_ticket.py`;#26 的 `allowed_write_paths` 沒有它。)
    """

    def a_closable(self, baseline=None, **extra):
        self.write("src/nav.py", "def size_nav():\n    return 42\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 src/nav.py 放進主線")
        plan = {"files": ["verify/nav/test_ticket_1.py"], "tags": ["example"],
                "run": "", "notes": ""}
        if baseline is not None:
            plan["baseline"] = baseline
        fields = {"allowed_write_paths": ["src/*"],
                  "verify_strings": ["src/nav.py:def size_nav"], "verify": plan}
        fields.update(extra)
        self.make_ticket(1, **fields)
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                               ensure_ascii=False))

    def test_a_baseline_that_the_gate_measured_closes_the_ticket(self):
        self.a_closable({"stage": "check", "ok": True, "why": ""})
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Done")

    def test_a_baseline_still_at_stage_red_does_not_close(self):
        """**變異**:把 `done_blockers` 裡 `stage != "check"` 那一段拿掉 → 這一條紅。"""
        self.a_closable({"stage": "red", "ok": True, "why": ""})
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("還缺閘門那一趟 check", done.stdout)
        self.assertIn("stage 是 red", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready", "票不該被關掉")

    def test_a_baseline_without_any_stage_does_not_close_either(self):
        """舊版的 `check` 沒有寫 `stage`。**沒有那一格**與「閘門量過了」長得一樣,
        而 close 只認量過的那一趟。"""
        self.a_closable({"ok": True, "why": ""})
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("還缺閘門那一趟 check", done.stdout)

    def test_a_baseline_that_says_red_failed_still_names_that_instead(self):
        """`ok:false` 與「還缺 check」的下一步不一樣,兩句話不准揉在一起。"""
        self.a_closable({"stage": "check", "ok": False, "why": "乾淨主線上一條都沒紅"})
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("verify.baseline 說驗紅沒過", done.stdout)
        self.assertNotIn("還缺閘門那一趟 check", done.stdout)

    def test_an_honest_waiver_still_gets_past_the_stage_check(self):
        """#15 那條豁免不變:誠實的 `verify_waiver` + review.sha 真的在主線歷史裡。"""
        self.a_closable({"stage": "red", "ok": True, "why": ""},
                        verify_waiver={"by": "main", "reason": "控制腳本票:無獨立驗證者"})
        sha = self.git("rev-parse", "main").strip()
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": sha},
                               ensure_ascii=False))
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Done")


if __name__ == "__main__":
    unittest.main()
