"""`scripts/verify-case.py`:同一份案例,乾淨主線該紅、candidate 該綠。

2026-09-21 外部審查:「沒有『乾淨主線該紅』的機器檢查。」範本只要求驗證者貼兩份
Ran/OK,而**一份貼上來的輸出沒有辦法被機器比對** —— 票的 `verify` 裡沒有一格說得出
「乾淨主線上真的紅過」,於是一條永遠綠的斷言與一條真的在驗的斷言長得一模一樣。

這一組釘的是那一格能不能被偽造:
- 案例在兩邊都綠 → 不准算過(「一條都沒紅」)。
- 乾淨主線上是 `import` 炸掉 → 不算紅,而且要**明列**(那是還沒接上,不是驗到了)。
- 過了才寫進票的 `verify.baseline`(閘門的證據;close 不讀,D-054 Q4)。

2026-09-23(#26 / D-020):`red` 子指令(`lint` 子指令 #138 已刪;close 讀 `stage` 那幾條 #139 已刪)。
釘的是**四種紅分不分得開**:看起來紅、其實是還沒接上的紅有三種(import 失敗、紅在缺
符號、紅在別處),它們與「驗到了」一樣讓 unittest 回非零。揉成同一句「N 條紅」的那一刻,
驗證者交的紅與一份還沒接上的案例長得一樣。
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402

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

    def a_ticket(self, case=CASE, **extra):
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), case)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""},
                         **extra)

    def red_record(self, done):
        """`red` 交的證據檔 —— stdout 那一行 `證據 -> <路徑>` 指的那一份(#36)。"""
        found = re.search(r"證據 -> (\S+?baseline-red\.json)", done.stdout)
        self.assertTrue(found, "red 成立卻沒印證據檔的路徑:" + done.stdout)
        with open(found.group(1), encoding="utf-8") as handle:
            return json.load(handle)


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

        #72 併入原 RedIsTheOnlyThingTheVerifierMeasures.
        test_an_import_failure_does_not_count_and_the_ticket_is_not_touched(同一份夾具,
        先 `red` 再 `check`):🩸 乾淨基底上沒有那個新符號,案例 `import` 就會炸 —— 那是
        **還沒接上**;`red` 不成立就不動票。**變異**:把 `IMPORT_MARKS` 那一段拿掉 → 紅
        (它會被算成缺符號那一類)。
        """
        self.a_ticket(NEEDS_NEW_MODULE)
        self.write("src/newthing.py", "def size():\n    return 2\n")
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

        done = self.tool("check", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("import 失敗(不算紅)", done.stdout)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertFalse(base["ok"])
        self.assertTrue(base["baseline"]["import_failures"])
        self.assertEqual(base["baseline"]["red"], [])

    def test_a_ticket_without_verify_files_says_so(self):
        """#72:`check` 與 `red` 兩條併成 subTest(原 RedIsTheOnlyThingTheVerifierMeasures 同名)。"""
        self.make_ticket(1)
        for command in ("check", "red"):
            with self.subTest(command):
                done = self.tool(command, "1")
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

        #72 併入原 test_the_case_file_is_overlaid_onto_a_ref_tree_that_lacks_it(同一個
        分支形狀):案例是這張票才加的,ref 那棵樹上本來就沒有它 —— 不疊上去,乾淨主線
        那一趟跑到的是**零個案例**,而零個案例與「都過了」長得一樣。
        """
        _, sha = self.a_branch()
        done = self.tool("check", "1", "--candidate", "t1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.assert_measured(sha)
        self.assertEqual(base["candidate"], "t1")
        self.assertEqual(base["baseline"]["cases"], 1, "沒疊上去")

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

    def test_check_leaves_only_logs_behind_in_the_out_dir(self):
        """🩸 #33:`gate.sh` 把 `--out-dir` 指到 worktree 內的 `gate.log.verify-case.d`,
        `clean_copy` 出來的 `base`(乾淨主線的拋棄式副本,`--candidate` 是分支/sha 時
        還有 `candidate`)從來沒被清掉過 —— 同一個 worktree 第二次跑閘門時,全樹掃描
        的守衛把上一次留下的那份樹當成工作樹裡真的檔案掃到而紅(同一 worktree 重跑
        必紅)。

        **變異**:把 `discard_scratch_trees` 的呼叫拿掉 → 這一條紅。
        """
        self.a_ticket()
        self.write("src/value.txt", "2\n")
        out = os.path.join(self.home, "vc-check")
        done = self.tool("check", "1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["logs"], "base/candidate 該砍掉了")

    def test_check_with_a_branch_candidate_also_discards_the_candidate_copy(self):
        """`--candidate` 是分支名時,`resolve_tree` 另外做了一份 `candidate` 副本
        (與路徑寫法不同:路徑直接指到工作樹,不必複製)—— 那一份也要砍。"""
        self.a_branch()
        out = os.path.join(self.home, "vc-cand")
        done = self.tool("check", "1", "--candidate", "t1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["logs"], "base/candidate 該砍掉了")

    def test_the_baseline_paths_are_relative_to_the_main_repo(self):
        """#87 A2:票檔進 git,`candidate`、`baseline.log`、`candidate_run.log` 三格是
        相對主 repo 根的路徑 —— worktree 在主 repo 外時以 `../` 開頭,不含沙盒的絕對路徑。
        期望值由測試自己拼(worktree 在 `<home>/wt/t1`,主 repo 在 `<home>/repo`)。

        **變異**:`portable()` 原樣回傳 record → 這一條紅。
        """
        path, _ = self.a_branch()
        out = os.path.join(path, "gate.log.verify-case.d")
        done = self.tool("check", "1", "--candidate", path, "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        wt = os.path.join("..", "wt", "t1")
        logs = os.path.join(wt, "gate.log.verify-case.d", "logs")
        self.assertEqual(base["candidate"], wt)
        self.assertEqual(base["baseline"]["log"], os.path.join(logs, "baseline.log"))
        self.assertEqual(base["candidate_run"]["log"], os.path.join(logs, "candidate.log"))
        text = self.read(os.path.join("tickets", "1.json"))
        for where in {self.home, os.path.realpath(self.home)}:
            self.assertNotIn(where, text)

    def test_the_red_record_paths_are_relative_to_the_main_repo(self):
        """#87 A2 同一條:`red` 印出的 baseline-red.json 不含沙盒根。"""
        self.a_ticket()
        out = os.path.join(self.repo, "vc-red")
        done = self.tool("red", "1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        record = self.red_record(done)
        self.assertEqual(record["candidate"], ".")
        self.assertEqual(record["baseline"]["log"],
                         os.path.join("vc-red", "logs", "baseline.log"))
        text = json.dumps(record, ensure_ascii=False)
        for where in {self.home, os.path.realpath(self.home)}:
            self.assertNotIn(where, text)

    def test_red_leaves_only_logs_behind_in_the_out_dir(self):
        """`red`(驗證者交件那一趟)共用同一支 `clean_base`,一樣的殘留與一樣的修法。
        多留的那一份是 `red` 的證據檔(#36:它不寫票,證據落在 `--out-dir`)。"""
        self.a_ticket()
        out = os.path.join(self.home, "vc-red")
        done = self.tool("red", "1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["baseline-red.json", "logs"],
                         "base 該砍掉了")

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
        base = self.red_record(done)
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
        base = self.red_record(done)
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
        base = self.red_record(done)
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

    def test_red_leaves_the_live_ticket_alone_in_git(self):
        """A4(#36,FLOW G13):驗證者是不准 git 寫入的角色,而 `red` 以前走
        `ticketlib.save` 改活 repo 的票並 `state_version+1` —— 票檔在 git 裡多一個沒人
        commit 的改動,主線的覆核立刻過期。證據改落在 `--out-dir`,路徑印在 stdout。

        **變異**:在 `cmd_red` 裡把 `write_baseline(args.ticket, record, "red")` 放回去
        → 這一條紅(porcelain 多一行 ` M tickets/1.json`)。
        """
        self.a_ticket(state_version=4)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1(state_version=4)")
        out = os.path.join(self.home, "vc-red")
        done = self.tool("red", "1", "--ref", self.load_ticket("1")["base_sha"],
                         "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        porcelain = subprocess.run(
            ["git", "-C", self.repo, "status", "--porcelain", "tickets/"],
            capture_output=True, text=True, env=self.env()).stdout
        self.assertEqual(porcelain, "", "red 改了活票:" + porcelain)
        self.assertEqual(self.load_ticket("1")["state_version"], 4)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])
        path = os.path.join(out, "baseline-red.json")
        self.assertIn(path, done.stdout, "證據檔的路徑要印出來,驗證者才抄得到")
        self.assertEqual(self.red_record(done)["stage"], "red")

    def test_a_skip_is_counted_apart_from_both_kinds_of_red(self):
        self.a_ticket(SKIP_AND_RED)
        done = self.tool("red", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        run = self.red_record(done)["baseline"]
        self.assertEqual(run["cases"], 2)
        self.assertEqual(run["skipped"], 1)
        self.assertEqual(len(run["red"]), 1, "skip 不准算成紅")
        self.assertEqual(run["missing_symbol"], [])
        self.assertEqual(run["elsewhere"], [])

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


# #156:案例那一趟先睡再紅。睡多久由各案例自己給,時限由沙盒的 config 給。
SLEEP_THEN_RED = """import time
import unittest
TAGS = ["example"]


class Slow(unittest.TestCase):
    def test_value(self):
        time.sleep(%d)
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""


def load_verify_case():
    """`scripts/verify-case.py` 檔名有連字號,`import` 不到;照路徑載入。"""
    import importlib.util
    from control_harness import SCRIPTS
    sys.path.insert(0, SCRIPTS)
    spec = importlib.util.spec_from_file_location("verify_case_156",
                                                  os.path.join(SCRIPTS, "verify-case.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaseTimeoutFromConfig(unittest.TestCase):
    """A1(#156):`case_timeout(root)` 讀 `board/config.json` 的
    `verify_case.timeout_seconds`。期望值是票面寫的(沒設 900、壞值退回 900),
    不是跑一次記下來的。

    **變異**:`case_timeout` 不擋 `bool` → `true` 那一格拿到 1,這一條紅。
    """

    @classmethod
    def setUpClass(cls):
        cls.vc = load_verify_case()

    def setUp(self):
        import tempfile
        import shutil
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "board"))

    def resolve(self, text):
        import contextlib
        import io
        if text is not None:
            with open(os.path.join(self.root, "board", "config.json"), "w",
                      encoding="utf-8") as handle:
                handle.write(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            value = self.vc.case_timeout(self.root)
        return value, err.getvalue()

    def test_no_key_is_900(self):
        self.assertEqual(self.resolve(json.dumps({"port": 1}))[0], 900)

    def test_a_positive_int_is_taken(self):
        value, err = self.resolve(json.dumps({"verify_case": {"timeout_seconds": 1200}}))
        self.assertEqual(value, 1200)
        self.assertEqual(err, "")

    def test_a_bad_value_warns_and_falls_back_to_900(self):
        for bad in (0, -5, 1.5, "600", True, None):
            with self.subTest(bad=bad):
                value, err = self.resolve(
                    json.dumps({"verify_case": {"timeout_seconds": bad}}))
                self.assertEqual(value, 900)
                self.assertIn("verify_case.timeout_seconds", err)
                self.assertIn(json.dumps(bad), err, "警告要指名那個壞值")

    def test_an_unreadable_config_is_900(self):
        self.assertEqual(self.resolve(None)[0], 900)          # 沒有這個檔
        self.assertEqual(self.resolve("{not json")[0], 900)   # 讀得到、解不開


class ARunThatTimesOutIsUnmeasured(CaseSandbox):
    """A2–A4(#156):超過時限的一趟是「量不到」(rc 2、票不動、不交證據檔),
    不是一段沒接住的 `TimeoutExpired` traceback。

    **變異**:(m1)`run_cases` 照舊用寫死的 900、不讀 config → A2 紅(睡 10 秒跑完,
    rc 1 而非 2);(m2)拿掉 `except subprocess.TimeoutExpired` → A2 紅(stderr 有
    Traceback)。
    """

    def set_timeout(self, seconds):
        conf = json.loads(self.read("board/config.json"))
        if seconds is None:
            conf.pop("verify_case", None)
        else:
            conf["verify_case"] = {"timeout_seconds": seconds}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def committed_ticket(self, case):
        self.a_ticket(case)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        return self.load_ticket("1")["base_sha"]

    def ticket_bytes(self):
        with open(os.path.join(self.repo, "tickets", "1.json"), "rb") as handle:
            return handle.read()

    def assert_unmeasured_by_timeout(self, done):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertIn("2 秒", done.stderr)
        self.assertIn("verify.nav.test_ticket_1", done.stderr)
        self.assertIn("verify_case.timeout_seconds", done.stderr)

    def test_red_that_times_out_is_rc_2_and_writes_nothing(self):
        import time
        base = self.committed_ticket(SLEEP_THEN_RED % 10)
        self.set_timeout(2)
        before = self.ticket_bytes()
        out = os.path.join(self.home, "vc-red")
        start = time.monotonic()
        done = self.tool("red", "1", "--ref", base, "--out-dir", out)
        elapsed = time.monotonic() - start
        self.assert_unmeasured_by_timeout(done)
        self.assertLess(elapsed, 9, "時限 2 秒卻跑了 %.1f 秒" % elapsed)
        self.assertFalse(os.path.exists(os.path.join(out, "baseline-red.json")))
        self.assertEqual(self.ticket_bytes(), before)

    def test_check_that_times_out_is_rc_2_and_leaves_the_ticket(self):
        base = self.committed_ticket(SLEEP_THEN_RED % 10)
        self.set_timeout(2)
        self.write("src/value.txt", "2\n")
        before = self.ticket_bytes()
        done = self.tool("check", "1", "--ref", base, "--candidate", self.repo)
        self.assert_unmeasured_by_timeout(done)
        self.assertEqual(self.ticket_bytes(), before)
        self.assertNotIn("baseline", self.load_ticket("1")["verify"])

    def test_within_the_limit_red_is_the_same_as_without_the_key(self):
        base = self.committed_ticket(SLEEP_THEN_RED % 1)

        def red(tag):
            out = os.path.join(self.home, "vc-" + tag)
            done = self.tool("red", "1", "--ref", base, "--out-dir", out)
            with open(os.path.join(out, "baseline-red.json"), encoding="utf-8") as handle:
                record = json.load(handle)
            # 兩趟必然不同的只有時間戳與各自 out-dir 底下的 log 路徑。
            record.pop("at")
            record["baseline"].pop("log")
            return done.returncode, record

        self.set_timeout(None)
        without = red("without")
        self.set_timeout(60)
        within = red("within")
        self.assertEqual(without[0], 0)
        self.assertEqual(within, without)


# ------------------------------------------- #156:unittest 子行程的時限從設定讀

# 期望值來源是 #156 票面(A1-A4),不是跑一次記下來的:沒設 -> 900、正整數照用、
# 其餘一律警告後 900;超時 rc 2、不寫證據、不動票。
TIMEOUT_KEY = "verify_case.timeout_seconds"

SLOW_RED = """import time
import unittest


class Slow(unittest.TestCase):
    def test_slow_then_red(self):
        time.sleep(%d)
        self.assertEqual(1, 2)
"""


def load_verify_case_t156():
    spec = importlib.util.spec_from_file_location(
        "verify_case_t156", os.path.join(SCRIPTS, "verify-case.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaseTimeoutResolver(unittest.TestCase):
    """#156 A1:`case_timeout(root)` 讀 `board/config.json` 的 `verify_case.timeout_seconds`。"""

    def setUp(self):
        self.vc = load_verify_case_t156()
        self.assertTrue(hasattr(self.vc, "case_timeout"),
                        "verify-case.py 沒有 case_timeout(root)")
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "board"))
        self.path = os.path.join(self.root, "board", "config.json")

    def resolve(self, conf):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(conf, handle)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            value = self.vc.case_timeout(self.root)
        return value, err.getvalue()

    def test_absent_key_is_900(self):
        self.assertEqual(self.resolve({"port": 1})[0], 900)
        self.assertEqual(self.resolve({"verify_case": {}})[0], 900)

    def test_a_positive_int_is_used(self):
        self.assertEqual(self.resolve({"verify_case": {"timeout_seconds": 1200}})[0], 1200)

    def test_a_bad_value_warns_and_falls_back_to_900(self):
        # bool 是 int 的子類別:`true` 不准被當成 1(票面 m3)。
        for bad in (0, -5, 1.5, "600", True, None):
            with self.subTest(bad=bad):
                value, err = self.resolve({"verify_case": {"timeout_seconds": bad}})
                self.assertEqual(value, 900)
                self.assertIn(TIMEOUT_KEY, err)

    def test_an_unreadable_config_is_900(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.vc.case_timeout(self.root), 900)
        os.remove(self.path)
        os.makedirs(self.path)  # 一個目錄:root 也讀不了,chmod 000 對 root 沒用
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.vc.case_timeout(self.root), 900)


class CaseTimeout(CaseSandbox):
    """#156 A2-A4:超時是「量不到」(rc 2、不寫證據、不動票),不是一段 traceback。"""

    def set_timeout(self, seconds):
        conf = json.loads(self.read("board/config.json"))
        if seconds is None:
            conf.pop("verify_case", None)
        else:
            conf["verify_case"] = {"timeout_seconds": seconds}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def ticket_bytes(self):
        with open(os.path.join(self.repo, "tickets", "1.json"), "rb") as handle:
            return handle.read()

    def base_sha(self):
        return self.git("rev-parse", "HEAD").strip()

    def assert_timed_out(self, done, out):
        err = done.stderr
        self.assertEqual(done.returncode, 2, done.stdout + err)
        self.assertNotIn("Traceback", err)
        self.assertIn(TIMEOUT_KEY, err)
        self.assertTrue("verify.nav.test_ticket_1" in err
                        or "verify/nav/test_ticket_1.py" in err, "沒點名案例模組:" + err)
        # 秒數:路徑與模組名裡的數字不算,剩下的要有一個獨立的 2。
        bare = err.replace(self.home, "").replace(out, "")
        bare = bare.replace("test_ticket_1", "").replace(TIMEOUT_KEY, "")
        bare = re.sub(r"\b[0-9a-f]{7,40}\b", "", bare)  # sha 裡的 2 不算
        self.assertRegex(bare, r"(?<![\d.])2(?![\d.])", "沒說是幾秒:" + err)
        self.assertFalse(os.path.exists(os.path.join(out, "baseline-red.json")))

    def test_red_that_times_out_is_rc_2_and_writes_nothing(self):
        """A2。**變異**(票面 m1/m2):時限照舊寫死 900 → sleep 10 跑完、rc 不是 2;
        拿掉 TimeoutExpired 的處理 → stderr 有 Traceback。"""
        self.set_timeout(2)
        self.a_ticket(SLOW_RED % 10)
        before = self.ticket_bytes()
        out = os.path.join(self.home, "out-red")
        started = time.monotonic()
        done = self.tool("red", "1", "--ref", self.base_sha(), "--out-dir", out)
        wall = time.monotonic() - started
        self.assert_timed_out(done, out)
        self.assertLess(wall, 9, "沒在時限內停下")
        self.assertEqual(self.ticket_bytes(), before, "超時的那一趟動了票")

    def test_check_that_times_out_is_rc_2_and_leaves_the_ticket(self):
        """A3:同一份夾具走 check;票(含 verify.baseline)一個 byte 都不准動。"""
        self.set_timeout(2)
        self.a_ticket(SLOW_RED % 10)
        before = self.ticket_bytes()
        out = os.path.join(self.home, "out-check")
        done = self.tool("check", "1", "--ref", self.base_sha(),
                         "--candidate", self.repo, "--out-dir", out)
        self.assert_timed_out(done, out)
        self.assertEqual(self.ticket_bytes(), before, "超時的那一趟動了票")

    def test_within_the_limit_red_is_the_same_as_without_the_key(self):
        """A4:時限內跑完的那一趟,有沒有這個鍵 rc 與證據都一樣(同一條測試裡比)。

        **這一條在乾淨基底上本來就綠**:它守的是「加了時限不改既有行為」,基底上沒有
        這個鍵也一樣 —— 不適用「baseline 該紅」。"""
        self.a_ticket(SLOW_RED % 1)
        out = os.path.join(self.home, "out-same")
        runs = []
        for seconds in (None, 60):
            self.set_timeout(seconds)
            done = self.tool("red", "1", "--ref", self.base_sha(), "--out-dir", out)
            with open(os.path.join(out, "baseline-red.json"), encoding="utf-8") as handle:
                record = json.load(handle)
            record.pop("at", None)  # 時間戳記每一趟都不一樣
            runs.append((done.returncode, record))
            os.remove(os.path.join(out, "baseline-red.json"))
        self.assertEqual(runs[0][0], 0, "沒設時限時這一份夾具該驗紅成立")
        self.assertEqual(runs[1], runs[0])


# ------------------------------------------- #163:案例走 testiface regression,分類讀 result.json

FIXTURE_CASE_REL = "verify/fixture/test_fixture_case.py"
FIXTURE_CASE_ID = "verify.fixture.test_fixture_case.T.test_fixture"

# fixture 專案那一份案例檔改成「值該是 2」:基底(值 1)上紅在案例檔自己的 assertEqual。
FIXTURE_RED = """import unittest

TAGS = ["example"]


class T(unittest.TestCase):
    def test_fixture(self):
        with open("src/value.txt", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2")
"""


class FixtureRegression(CaseSandbox):
    """#163 A1、A3、A4:沙盒是 fixture 專案(row 3b),`tests.regression` 指向它的
    `scripts/ac-tests.sh regression` —— 每被叫一次在 `$AC_TEST_CALLS` 記一行,
    `AC_TEST_FAKE_REGRESSION` 換成三種量不到的形狀。期望值從 fixture 與票面來。

    **變異**(票面 m3):`run_cases` 不把 `AC_CODE_ROOT` 設成那一份副本(兩趟都看 candidate)
    → A1 的「基底紅」那一段與 A4 紅;(m4)`AC_TEST_MODE` 寫死一個值 → A4 紅;(m2)
    `exception` 是 null 那一列算成紅 → A3 第一條紅。
    """

    fixture = True

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "regression-calls.log")
        conf = json.loads(self.read("board/config.json"))
        conf["tests"]["regression"] = "sh scripts/ac-tests.sh regression"
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write(FIXTURE_CASE_REL, FIXTURE_RED)
        self.make_ticket(1, verify={"files": [FIXTURE_CASE_REL], "tags": ["example"],
                                    "run": "", "notes": ""})

    def tool(self, *args, **env):
        return self.run_py("scripts/verify-case.py", *args,
                           env=self.env(AC_TEST_CALLS=self.calls, **env))

    def regression_calls(self):
        """`[{argv, mode, code_root, out}]`,一次呼叫一筆;沒被叫過就是 []。"""
        if not os.path.exists(self.calls):
            return []
        rows = []
        with open(self.calls, encoding="utf-8") as handle:
            for line in handle:
                head, *rest = line.rstrip("\n").split("\t")
                row = {"argv": head}
                row.update(part.split("=", 1) for part in rest)
                rows.append(row)
        return rows

    def ticket_bytes(self):
        with open(os.path.join(self.repo, "tickets", "1.json"), "rb") as handle:
            return handle.read()

    def under(self, path, parent):
        path, parent = os.path.realpath(path), os.path.realpath(parent)
        return path.startswith(parent + os.sep)

    # ------------------------------------------------------------ A1

    def test_an_assert_equal_red_on_the_base_counts_and_check_records_it(self):
        """A1:案例檔裡的 assertEqual 在基底上紅、在候選上綠 → red rc 0、列在紅、
        red_lines 帶那一列的 message;check rc 0、票的 verify.baseline 照舊寫。"""
        out = os.path.join(self.home, "vc-red")
        done = self.tool("red", "1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("紅 %s" % FIXTURE_CASE_ID, done.stdout)
        run = self.red_record(done)["baseline"]
        self.assertEqual(run["red"], [FIXTURE_CASE_ID])
        self.assertEqual(run["red_lines"], ["%s: AssertionError: '1' != '2'" % FIXTURE_CASE_ID])
        self.assertEqual((run["cases"], run["skipped"]), (1, 0))

        self.write("src/value.txt", "2\n")
        done = self.tool("check", "1", "--out-dir", os.path.join(self.home, "vc-check"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        base = self.load_ticket("1")["verify"]["baseline"]
        self.assertEqual(base["stage"], "check")
        self.assertTrue(base["ok"], base["why"])
        self.assertEqual(base["baseline"]["red"], [FIXTURE_CASE_ID])
        self.assertEqual(base["candidate_run"]["red"], [])
        for key in ("rc", "cases", "skipped", "log", "red", "red_lines", "import_failures",
                    "missing_symbol", "elsewhere"):
            self.assertIn(key, base["baseline"], "verify.baseline 的形狀變了")

    # ------------------------------------------------------------ A4

    def test_red_runs_the_base_copy_once_in_verify_red_mode(self):
        """A4(red):一次呼叫,`AC_CODE_ROOT` 是 out dir 底下的基底副本,模式 verify-red,
        `AC_TEST_OUT` 在 out dir 底下;參數是 `--case <案例檔>`。綠不在 red 量(D-020)。"""
        out = os.path.join(self.home, "vc-red")
        done = self.tool("red", "1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.regression_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0]["argv"], "regression --case %s" % FIXTURE_CASE_REL)
        self.assertEqual(calls[0]["mode"], "verify-red")
        self.assertEqual(os.path.realpath(calls[0]["code_root"]),
                         os.path.realpath(os.path.join(out, "base")))
        self.assertTrue(self.under(calls[0]["out"], out), calls[0])
        self.assertIsNone(self.red_record(done)["candidate_run"], "red 不跑 candidate(D-020)")

    def test_check_runs_each_copy_with_its_own_code_root_and_out(self):
        """A4(check):兩次呼叫,`AC_CODE_ROOT` 分別是基底副本與候選副本(`--candidate` 是
        分支,兩份都在 out dir 底下、不同),模式都是 verify-check,`AC_TEST_OUT` 各一個。"""
        path = self.worktree("t1")
        self.write("src/value.txt", "2\n", where=path)
        self.write(FIXTURE_CASE_REL, FIXTURE_RED, where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "值改成 2,附案例", cwd=path)
        out = os.path.join(self.home, "vc-check")
        done = self.tool("check", "1", "--candidate", "t1", "--out-dir", out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.regression_calls()
        self.assertEqual(len(calls), 2, calls)
        roots = [os.path.realpath(call["code_root"]) for call in calls]
        self.assertEqual(roots, [os.path.realpath(os.path.join(out, "base")),
                                 os.path.realpath(os.path.join(out, "candidate"))])
        self.assertEqual([call["mode"] for call in calls], ["verify-check", "verify-check"])
        outs = [call["out"] for call in calls]
        self.assertNotEqual(outs[0], outs[1])
        for one in outs:
            self.assertTrue(self.under(one, out), one)
        self.assertEqual(sorted(os.listdir(out)), ["logs"], "base/candidate 該砍掉了")

    # ------------------------------------------------------------ A3

    def assert_unmeasured(self, done, before):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("量不到基準,票沒有動", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(self.ticket_bytes(), before, "量不到的那一趟動了票")

    def test_a_fail_row_with_a_null_exception_is_unmeasured(self):
        before = self.ticket_bytes()
        done = self.tool("check", "1", AC_TEST_FAKE_REGRESSION="null-exception")
        self.assert_unmeasured(done, before)
        self.assertIn("unmeasured", done.stderr)
        self.assertIn(FIXTURE_CASE_ID, done.stderr)

    def test_a_regression_without_result_json_is_unmeasured(self):
        before = self.ticket_bytes()
        done = self.tool("check", "1", AC_TEST_FAKE_REGRESSION="no-result")
        self.assert_unmeasured(done, before)
        self.assertIn("result.json", done.stderr)

    def test_a_regression_past_the_timeout_is_unmeasured(self):
        conf = json.loads(self.read("board/config.json"))
        conf["verify_case"] = {"timeout_seconds": 1}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        before = self.ticket_bytes()
        out = os.path.join(self.home, "vc-sleep")
        started = time.monotonic()
        done = self.tool("check", "1", "--out-dir", out, AC_TEST_FAKE_REGRESSION="sleep")
        wall = time.monotonic() - started
        self.assert_unmeasured(done, before)
        self.assertLess(wall, 30, "時限 1 秒卻跑了 %.1f 秒" % wall)
        self.assertIn("verify_case.timeout_seconds", done.stderr)
        self.assertIn(os.path.join(out, "logs", "baseline.log"), done.stderr)


def result_row(ident, status, exception=None, file=None, line=None, message=None,
               traceback=None):
    """一列 result.json(schema 1)—— 欄位照 adapter 的那一張表。"""
    return {"id": ident, "status": status, "message": message, "exception": exception,
            "file": file, "line": line, "seconds": 0.0, "traceback": traceback}


CASE_REL = "verify/nav/test_ticket_1.py"


class ShapesFromRows(unittest.TestCase):
    """#163:`shapes()` 是一支只吃 result.json 列的純函式;D-020 那張表一格一條,期望值是
    表上寫的那一類,不是跑一次記下來的。

    **變異**(票面 m1):分類只看 exception、不看 file(拿掉 `not frame_in(...)`)→
    `test_an_assertion_in_another_file_is_elsewhere` 紅;`raised_below` 永遠回 False →
    `test_a_raise_below_the_case_frame_is_elsewhere` 紅。
    """

    @classmethod
    def setUpClass(cls):
        cls.vc = load_verify_case()

    def shape(self, *rows, rels=(CASE_REL,)):
        return self.vc.shapes(list(rows), list(rels))

    def test_an_assertion_in_the_case_file_is_red(self):
        got = self.shape(result_row("c.T.t", "fail", "AssertionError", CASE_REL, 9,
                                    "AssertionError: 1 != 2"))
        self.assertEqual(got["red"], ["c.T.t"])
        self.assertEqual(got["red_lines"], ["c.T.t: AssertionError: 1 != 2"])

    def test_an_assertion_subclass_name_is_red(self):
        got = self.shape(result_row("c.T.t", "fail", "CustomAssertionError", CASE_REL, 9, "x"))
        self.assertEqual(got["red"], ["c.T.t"])

    def test_import_failures_by_exception_or_message(self):
        got = self.shape(
            result_row("c", "error", "ImportError", None, None,
                       "ImportError: Failed to import test module: c"),
            result_row("d.T.t", "error", "ModuleNotFoundError", CASE_REL, 2, "No module"),
            result_row("e", "error", "SomethingElse", None, None, "Failed to import e"))
        self.assertEqual(len(got["import_failures"]), 3)
        self.assertEqual(got["red"], [])

    def test_another_exception_in_the_case_file_is_a_missing_symbol(self):
        for exc in ("AttributeError", "NameError", "FileNotFoundError", "TypeError"):
            with self.subTest(exc):
                got = self.shape(result_row("c.T.t", "error", exc, CASE_REL, 3, exc))
                self.assertEqual(len(got["missing_symbol"]), 1)
                self.assertEqual(got["red"], [])

    def test_an_assertion_in_another_file_is_elsewhere(self):
        got = self.shape(result_row("c.T.t", "fail", "AssertionError",
                                    "verify/nav/test_helper.py", 4, "AssertionError: x"))
        self.assertEqual(got["elsewhere"], ["c.T.t: AssertionError: x"])
        self.assertEqual(got["red"], [])

    def test_a_row_without_a_file_is_elsewhere(self):
        got = self.shape(result_row("c.T.t", "error", "RuntimeError", None, None, "boom"))
        self.assertEqual(len(got["elsewhere"]), 1)

    def test_a_case_file_under_a_longer_prefix_still_matches(self):
        """frame_in 的 endswith 規則:result 的 `file` 帶著前綴時照樣認得出是案例檔。"""
        got = self.shape(result_row("c.T.t", "fail", "AssertionError",
                                    "sub/" + CASE_REL, 9, "x"))
        self.assertEqual(got["red"], ["c.T.t"])

    def test_a_raise_below_the_case_frame_is_elsewhere(self):
        """`file` 是案例檔(adapter 記的是最後一個測試檔 frame),但例外是產品碼丟的。"""
        tb = ("Traceback (most recent call last):\n"
              '  File "/tmp/x/%s", line 6, in test_t\n'
              "    src.boom.go()\n"
              '  File "/tmp/x/src/boom.py", line 2, in go\n'
              '    raise AssertionError("prod")\n'
              "AssertionError: prod\n") % CASE_REL
        got = self.shape(result_row("c.T.t", "fail", "AssertionError", CASE_REL, 6,
                                    "AssertionError: prod", tb))
        self.assertEqual(got["elsewhere"], ["c.T.t: AssertionError: prod"])
        self.assertEqual(got["red"], [])

    def test_a_traceback_inside_the_message_does_not_move_the_frame(self):
        """#31:工具的 traceback 在例外那一行**之後**(訊息裡),不是更深的 frame。"""
        tb = ("Traceback (most recent call last):\n"
              '  File "/tmp/x/%s", line 9, in test_t\n'
              "    self.assertEqual(done.returncode, 0, done.stderr)\n"
              "AssertionError: 1 != 0 : Traceback (most recent call last):\n"
              '  File "/tmp/x/src/tool.py", line 1, in <module>\n'
              "ValueError: tool\n") % CASE_REL
        got = self.shape(result_row("c.T.t", "fail", "AssertionError", CASE_REL, 9,
                                    "AssertionError: 1 != 0 : Traceback", tb))
        self.assertEqual(got["red"], ["c.T.t"])

    def test_skip_rows_are_counted_apart(self):
        got = self.shape(result_row("c.T.s", "skip", message="why"),
                         result_row("c.T.p", "pass"))
        self.assertEqual(got["skipped"], 1)
        self.assertEqual((got["red"], got["elsewhere"], got["missing_symbol"]), ([], [], []))

    def test_a_fail_row_with_a_null_exception_cannot_be_classified(self):
        with self.assertRaises(self.vc.CaseUnmeasured) as caught:
            self.shape(result_row("c.T.t", "fail", None, CASE_REL, 9, "unexpected success"))
        self.assertIn("unmeasured", str(caught.exception))
        self.assertIn("c.T.t", str(caught.exception))


class TheRefsResolveInTheCodeRepo(CaseSandbox):
    """#181 A1/A2:`AC_ROOT` 是一個**不是 git** 的拋棄式控制根(只有 `tickets/<n>.json`
    與 `board/config.json`),程式碼 repo 是 cwd / `AC_CODE_ROOT` —— 驗證者派工的真實形狀。
    `--ref`、`--candidate`、票的 base_sha 都要在程式碼 repo 解,不是在 `AC_ROOT`。

    **變異**:`code_repo()` 直接回 `event.repo_root()` → 這一組紅(rc 2「既不是一個目錄」)。
    """

    NOT_A_REF = "既不是一個目錄"

    def control_root(self):
        where = os.path.join(self.home, "control")
        for rel in (os.path.join("tickets", "1.json"), os.path.join("board", "config.json")):
            source = os.path.join(self.repo, rel)
            if os.path.exists(source):
                os.makedirs(os.path.dirname(os.path.join(where, rel)), exist_ok=True)
                shutil.copy(source, os.path.join(where, rel))
        probe = subprocess.run(["git", "-C", where, "rev-parse", "--git-dir"],
                               capture_output=True, text=True)
        self.assertNotEqual(probe.returncode, 0, "前提:控制根不在任何 git 樹裡")
        return where

    def tool_in(self, control, *args, **extra):
        return self.run_py("scripts/verify-case.py", *args,
                           env=self.env(AC_ROOT=control, **extra))

    def assert_reached_the_measuring_step(self, done):
        self.assertNotIn(self.NOT_A_REF, done.stderr)
        self.assertIn("案例 1 個", done.stdout, done.stdout + done.stderr)

    def test_a1_red_resolves_the_base_sha_ref_sha_and_ref_branch_from_the_code_repo(self):
        self.a_ticket()
        control = self.control_root()
        base = self.load_ticket("1")["base_sha"]
        for label, args, extra in (
                ("票的 base_sha(cwd)", (), {}),
                ("票的 base_sha(AC_CODE_ROOT)", (), {"AC_CODE_ROOT": self.repo}),
                ("--ref <sha>", ("--ref", base), {}),
                ("--ref <branch>", ("--ref", "main"), {})):
            with self.subTest(label):
                done = self.tool_in(control, "red", "1", *args, **extra)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assert_reached_the_measuring_step(done)
                self.assertEqual(self.red_record(done)["base_sha"], base)

    def test_a2_check_resolves_a_candidate_branch_from_the_code_repo(self):
        path = self.worktree("t1")
        self.write("src/value.txt", "2\n", where=path)
        self.write(os.path.join("verify", "nav", "__init__.py"), "", where=path)
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), CASE, where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "值改成 2,附案例", cwd=path)
        self.make_ticket(1, verify={"files": ["verify/nav/test_ticket_1.py"],
                                    "tags": ["example"], "run": "", "notes": ""})
        control = self.control_root()
        done = self.tool_in(control, "check", "1", "--candidate", "t1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_reached_the_measuring_step(done)
        with open(os.path.join(control, "tickets", "1.json"), encoding="utf-8") as handle:
            baseline = json.load(handle)["verify"]["baseline"]
        self.assertEqual(baseline["candidate_sha"], self.git("rev-parse", "t1").strip())
        self.assertEqual(baseline["base_sha"], self.load_ticket("1")["base_sha"])


class ADirectoryRefSaysItsShaWithAMarker(CaseSandbox):
    """#182 A2/A3:驗證者派工的真實形狀,而且**哪裡都摸不到 git**:AC_ROOT 是拋棄式控制根、
    cwd 與 AC_CODE_ROOT 是封存副本 `work/`(或 AC_CODE_ROOT 沒設)。`--ref` 是 auto-fix 展好的
    `ref/`(票 base_sha 的樹);那棵樹是哪一個 sha 只有 `.ac-base-sha` 說得出來。

    **變異**:`resolve_tree` 的目錄那一支不讀標記 → A2 紅(rc 2);拿掉「rev-parse 是空的就拒絕」
    → A3 紅(rc 0,證據檔的 base_sha 是空字串)。
    """

    MARKER = ".ac-base-sha"

    def layout(self, marker=True):
        self.a_ticket()
        copy = os.path.join(self.home, "copy")
        control = os.path.join(copy, "control")
        for rel in (os.path.join("tickets", "1.json"), os.path.join("board", "config.json")):
            source = os.path.join(self.repo, rel)
            if os.path.exists(source):
                os.makedirs(os.path.dirname(os.path.join(control, rel)), exist_ok=True)
                shutil.copy(source, os.path.join(control, rel))
        base = self.load_ticket("1")["base_sha"]
        for name in ("ref", "work"):
            os.makedirs(os.path.join(copy, name))
            tar = subprocess.run(["git", "-C", self.repo, "archive", base],
                                 capture_output=True, check=True)
            subprocess.run(["tar", "-x", "-C", os.path.join(copy, name)], input=tar.stdout,
                           check=True)
        work = os.path.join(copy, "work")
        self.write(os.path.join("verify", "nav", "__init__.py"), "", where=work)
        self.write(os.path.join("verify", "nav", "test_ticket_1.py"), CASE, where=work)
        if marker:
            self.write(self.MARKER, base + "\n", where=os.path.join(copy, "ref"))
        for where in (control, os.path.join(copy, "ref"), work):
            probe = subprocess.run(["git", "-C", where, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True)
            self.assertNotEqual(probe.returncode, 0, "前提:%s 不在任何 git 樹裡" % where)
        return copy, control, work, base

    def red(self, control, work, *args, **extra):
        env = self.env(AC_ROOT=control, **extra)
        return self.run_py("scripts/verify-case.py", "red", "1", *args, cwd=work, env=env)

    def test_a2_a_ref_dir_with_a_marker_reaches_the_measuring_step_with_the_marker_sha(self):
        copy, control, work, base = self.layout()
        ref = os.path.join(copy, "ref")
        for label, args, extra in (
                ("AC_CODE_ROOT = 副本", ("--ref", ref), {"AC_CODE_ROOT": work}),
                ("AC_CODE_ROOT 沒設", ("--ref", ref, "--candidate", work), {})):
            with self.subTest(label):
                done = self.red(control, work, *args, **extra)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("案例 1 個", done.stdout, done.stdout + done.stderr)
                self.assertEqual(self.red_record(done)["base_sha"], base)

    def test_a3_a_ref_dir_without_a_marker_that_is_not_git_is_refused(self):
        copy, control, work, _ = self.layout(marker=False)
        out = os.path.join(self.home, "red-out")
        done = self.red(control, work, "--ref", os.path.join(copy, "ref"), "--out-dir", out,
                        AC_CODE_ROOT=work)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, done.stderr)
        self.assertIn("sha", lines[0])
        self.assertIn(self.MARKER, lines[0])
        self.assertFalse(os.path.exists(os.path.join(out, "baseline-red.json")),
                         "沒有 sha 不准留一份證據檔")
        self.assertNotIn("案例 1 個", done.stdout)


class TheLogTextPathIsGone(unittest.TestCase):
    """#163 A5:`-m unittest` 的子行程與讀 log 字的解析器不在了 —— 分類只有 result.json 一個來源。"""

    def test_no_unittest_spawn_and_no_log_parser(self):
        with open(os.path.join(SCRIPTS, "verify-case.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count('"-m", "unittest"'), 0)
        self.assertEqual(len(re.findall(r"def parse_reds|def traceback_end", text)), 0)
        self.assertIn("testiface.py", text)


if __name__ == "__main__":
    unittest.main()
