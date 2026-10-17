"""#102 案例 lint 的 F7 起六條:預設只印、board/config.json verify_lint 明確啟用才擋。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 + 夾具自己寫死的行號)
B1 | unit | 同一份 F8 違規檔 × 三種 config(沒設 / block=[F8] / block=[F8] 且 allow 該檔:F8);block=[F10] | rc 0 / 1 / 0,三次都印那行 F8;第三次那行帶「(白名單,不擋)」;block 寫 F10 印「只印不擋,block 忽略」 | 票面 B1 原話
B2 | unit | demo/test_browsers.py 宣告 VERIFY_API;案例 tb.http / tb.step / self.step / self.ready / self.assertEqual / driver._call / self._mine | F7 恰在 tb.step、self.ready、driver._call 三行;from-import 兩種;沒宣告或檔不在 → 印「沒宣告 VERIFY_API」且只剩底線那行 | 票面 B2 原話;行號夾具寫死
B3 | unit | len 等於整數(兩種參數序)、count == 整數、sha 字面配 hexdigest、# pin: 有理由 / 空理由、assertGreaterEqual | F8 恰在五行;空理由那行帶「pin 沒寫理由」 | 票面 B3 原話
B4 | unit | 模組層 TODAY、class 本體 DAY、setUp 取一次再比、同函式取兩次且一次在 assert 裡 | F9 在模組層行、class 本體行、第二個函式內;setUp 那段不中 | 票面 B4 原話
B5 | unit | sleep 後正向 assert / wait_until 字面與下一個 assert 無交集 / src.index(;各一份負向形狀;block 全開 | 正向各印一行、rc 0;負向不印 | 票面 B5 原話
B6 | unit | 兩檔各中 F7、F8(其中一檔中兩次 F8),給 baseline 旗標;讀不到的檔 | stdout 是排序去重的 JSON 陣列(寫死);rc 0;讀不到 rc 2 | 票面 B6 原話
B7 | doc  | 讀 docs/DESIGN-VERIFY-CASES.md、scripts/verify-case.py 檔頭、lint --help | 設計文件含 F7 到第十二條六個編號與 allow 鍵、VERIFY_API;檔頭不再寫「lint 的六條」;help 提到 baseline 旗標 | 票面 B7 原話

## 介面字串(全部出自票面 acceptance,不從腳本抄;verify_strings 的原字用拼的,不讓落地 grep 命中這一檔)
LINE = "verify-case lint: <檔>:<行> F<n> …"     ← 每條命中一行
ALLOWED = "(白名單,不擋)"                     ← allow 命中那行的尾巴
IGNORED = "F10–F<十二> 只印不擋,block 忽略"     ← block 寫了只印的規則
NO_API = "沒宣告 VERIFY_API"、"名單檢查略過"    ← api 的檔沒宣告或不在
NO_REASON = "pin 沒寫理由"
board/config.json:verify_lint.api / block / allow;allow 每筆 "<案例檔相對路徑>:<規則>"

## 怎麼做假(不上真埠、不起真服務、不起真模型)
沙盒借 tests/control_harness.Sandbox(拋棄式 repo、絆線 claude、env 剝掉外層 AC_*)。夾具 verify/x/test_ticket_9.py
本身過 F1–F6(四段 docstring、TAGS=["example"] 沙盒已登記、票 #9 不存在所以 F6 不查),所以 rc 只反映新規則。
夾具只被 ast 讀,不執行;demo/test_browsers.py 是假檔。B7 讀被測樹自己的文件與腳本(red 時是乾淨基底的副本)。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;F10 起「怎麼判」以票面為準,命中行只在票面沒定時給範圍。
"""
import json
import os
import re
import subprocess
import sys
import unittest

TAGS = ["case-lint"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import Sandbox  # noqa: E402

LAST = "F%d" % 12                      # 第十二條;拼出來,落地 grep 不命中這一檔
BASELINE_FLAG = "--" + "base" + "line"
ALLOW_KEY = "verify_lint." + "allow"
ALLOWED = "(白名單,不擋)"
IGNORED = "F10–%s 只印不擋,block 忽略" % LAST
NO_API = "沒宣告 VERIFY_API"
NO_API_TAIL = "名單檢查略過"
NO_REASON = "pin 沒寫理由"
CASE = "verify/x/test_ticket_9.py"
OTHER = "verify/x/test_ticket_8.py"

# 夾具共用的檔頭:恰 18 行,本體從第 19 行起 —— 下面寫死的行號都從這裡數。
HEAD = '''"""#9 lint 夾具。

## 驗收表
A1 | unit | 夾具 | lint 命中 | 票面

## 介面字串
(無)

## 怎麼做假
不上真埠。

## 不做
不改產品碼。
"""
import unittest

TAGS = ["example"]

'''

F8_ONLY = HEAD + '''class T(unittest.TestCase):
    def test_a1(self):
        """A1 夾具。"""
        x = [1, 2, 3]
        self.assertEqual(len(x), 3)
'''
F8_ONLY_LINE = 23

CLEAN = HEAD + '''class T(unittest.TestCase):
    def test_a1(self):
        """A1 夾具。"""
        self.assertTrue(True)
'''

BROWSERS_WITH_API = '''VERIFY_API = ("http", "BrowserCase", "BrowserCase.step")


def http():
    return 1


def step():
    return 2


class BrowserCase:
    def step(self):
        return 3

    def ready(self):
        return 4
'''
BROWSERS_WITHOUT_API = BROWSERS_WITH_API.split("\n", 1)[1]

F7_ATTR = HEAD + '''import test_browsers as tb


class T(tb.BrowserCase, unittest.TestCase):
    def _mine(self):
        return 1

    def test_a1(self):
        """A2 夾具。"""
        driver = object()
        tb.http()
        tb.step()
        self.step()
        self.ready()
        self.assertEqual(1, 1)
        driver._call
        self._mine()
'''
# 29 tb.http 過、30 tb.step 中(i)、31 self.step 過、32 self.ready 中(ii)、
# 33 assertEqual 過、34 driver._call 中(iii)、35 self._mine 過(案例自己 def)
F7_ATTR_HITS = {30, 32, 34}
F7_UNDERSCORE_ONLY = {34}

F7_FROM = HEAD + '''from test_browsers import http
from test_browsers import step
from helpers import _secret


class T(unittest.TestCase):
    def test_a1(self):
        """A2 夾具。"""
        self.assertTrue(http and step and _secret)
'''
F7_FROM_HITS = {20, 21}

F8_ALL = HEAD + '''import hashlib


class T(unittest.TestCase):
    def test_a1(self):
        """A3 夾具。"""
        x = [1, 2, 3]
        s = "aab"
        self.assertEqual(len(x), 3)
        self.assertEqual(3, len(x))
        self.assertTrue(s.count("a") == 2)
        self.assertGreaterEqual(len(x), 3)
        self.assertEqual(len(x), 3)  # pin: 釘 base 原文
        self.assertEqual(len(x), 3)  # pin:

    def test_a2(self):
        """A3 夾具。"""
        digest = hashlib.sha256(b"x").hexdigest()
        self.assertEqual(digest[:7], "deadbee")
'''
# 27 len 中、28 參數對調中、29 count 中、30 下限過、31 有理由過、32 空理由中、37 sha 字面中
F8_ALL_HITS = {27, 28, 29, 32, 37}
F8_EMPTY_PIN = 32

F9_ALL = HEAD + '''import time
from datetime import date

TODAY = date.today()


class T(unittest.TestCase):
    DAY = time.strftime("%Y-%m-%d")

    def setUp(self):
        self.today = date.today()

    def test_a1(self):
        """A4 夾具。"""
        self.assertEqual(self.today.year, self.today.year)

    def test_a2(self):
        """A4 夾具。"""
        seeded = date.today()
        self.assertEqual(seeded, date.today())
'''
F9_MODULE, F9_CLASS = 22, 26
F9_QUIET = range(28, 34)      # setUp 取一次、test_a1 只比
F9_TWICE = range(35, 39)      # 票面沒定 (i) 報在哪一行:函式內任一行都算

F10_HIT = HEAD + '''import time


class T(unittest.TestCase):
    def test_a1(self):
        """A5 夾具。"""
        time.sleep(0.01)
        self.assertTrue(True)
'''
F10_MISS = F10_HIT.replace("self.assertTrue(True)", "self.assertFalse(False)")
F10_LINES = range(25, 27)

F11_HIT = HEAD + '''class T(unittest.TestCase):
    def test_a1(self):
        """A5 夾具。"""
        self.wait_until("saved-ok")
        self.assertIn("other-text", "x")
'''
F11_MISS = F11_HIT.replace('"other-text"', '"saved"')
F11_LINES = range(22, 24)

IDX_HIT = HEAD + '''class T(unittest.TestCase):
    def test_a1(self):
        """A5 夾具。"""
        src = "abc"
        self.assertLess(src.index("a"), src.index("b"))
'''
IDX_MISS = IDX_HIT.replace("src", "text")
IDX_LINE = 23

BASE_ONE = HEAD + '''class T(unittest.TestCase):
    def test_a1(self):
        """A6 夾具。"""
        x = [1]
        x._probe
        self.assertEqual(len(x), 1)
'''
BASE_TWO = BASE_ONE + '''        self.assertEqual(len(x), 1)
'''
BASELINE_WANT = [OTHER + ":F7", OTHER + ":F8", CASE + ":F7", CASE + ":F8"]


def hit_lines(out, rule, rel=CASE):
    """`verify-case lint: <檔>:<行> F<n>` 那幾行的行號(F1 不會吃到 F10 起那幾條)。"""
    pattern = re.compile(r"^verify-case lint: %s:(\d+) %s(?!\d)" % (re.escape(rel), rule), re.M)
    return {int(n) for n in pattern.findall(out)}


def line_of(out, rule, line_no, rel=CASE):
    for row in out.splitlines():
        if re.match(r"verify-case lint: %s:%d %s(?!\d)" % (re.escape(rel), line_no, rule), row):
            return row
    return ""


class LintSandbox(Sandbox):
    """沙盒 repo + 一支寫 verify_lint 的手。**這個 class 自己沒有 test_。**"""

    def configure(self, **lint):
        conf = json.loads(self.read("board/config.json"))
        if lint:
            conf["verify_lint"] = lint
        else:
            conf.pop("verify_lint", None)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def lint(self, case, *args, rel=CASE):
        self.write(rel, case)
        done = self.run_py("scripts/verify-case.py", "lint", rel, *args)
        return done, done.stdout + done.stderr


class B1ConfigDecidesWhatBlocks(LintSandbox):

    def test_b1_same_f8_file_under_three_configs_is_0_1_0(self):
        """B1 同一份 F8 違規檔 × 沒設 / block=[F8] / block=[F8]+allow → rc 0 / 1 / 0,三次都印那行 F8。"""
        rcs = []
        for lint in ({}, {"block": ["F8"]},
                     {"block": ["F8"], "allow": [CASE + ":F8"]}):
            self.configure(**lint)
            done, out = self.lint(F8_ONLY)
            rcs.append(done.returncode)
            self.assertIn(F8_ONLY_LINE, hit_lines(done.stdout, "F8"),
                          "config=%s 沒印那行 F8:\n%s" % (lint, out))
        self.assertEqual(rcs, [0, 1, 0], "三種 config 的 rc")

    def test_b1_the_allowed_line_says_it_is_whitelisted(self):
        """B1 在 allow 的那行尾巴印「(白名單,不擋)」;沒在 allow 時不印。"""
        self.configure(block=["F8"], allow=[CASE + ":F8"])
        done, out = self.lint(F8_ONLY)
        row = line_of(done.stdout, "F8", F8_ONLY_LINE)
        self.assertTrue(row.rstrip().endswith(ALLOWED), "那行 F8 的尾巴沒有白名單註記:\n" + out)
        self.configure(block=["F8"])
        done, out = self.lint(F8_ONLY)
        self.assertNotIn(ALLOWED, line_of(done.stdout, "F8", F8_ONLY_LINE), out)

    def test_b1_allow_for_another_rule_or_file_does_not_unblock(self):
        """B1 只有「檔:規則」整筆對上才不擋:同檔別條規則、別檔同條規則都照擋。"""
        for allow in ([CASE + ":F7"], [OTHER + ":F8"]):
            self.configure(block=["F8"], allow=allow)
            done, out = self.lint(F8_ONLY)
            self.assertEqual(done.returncode, 1, "allow=%s 不該放行:\n%s" % (allow, out))

    def test_b1_a_print_only_rule_in_block_is_ignored_out_loud(self):
        """B1 block 寫了 F10 起的只印規則 → 印一行「只印不擋,block 忽略」,而且照樣不擋。"""
        self.configure(block=["F10"])
        done, out = self.lint(F10_HIT)
        self.assertIn(IGNORED, done.stdout, out)
        self.assertEqual(done.returncode, 0, out)

    def test_b1_a_clean_case_still_passes_with_every_blocking_rule_on(self):
        """B1 負控:F1–F6 乾淨、F7 起也沒命中的檔,三條可擋規則全開仍 rc 0、「lint: 過」。"""
        self.configure(block=["F7", "F8", "F9"])
        done, out = self.lint(CLEAN)
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("lint: 過", done.stdout)


class B2InterfaceListAndUnderscoreNames(LintSandbox):

    def setUp(self):
        super().setUp()
        self.write("demo/test_browsers.py", BROWSERS_WITH_API)
        self.configure(api={"test_browsers": "demo/test_browsers.py"}, block=["F7"])

    def test_b2_attribute_and_inherited_and_underscore_hit_exactly_three_lines(self):
        """B2 tb.step(i)、self.ready(ii)、driver._call(iii)中;tb.http / self.step / assertEqual / self._mine 過。"""
        done, out = self.lint(F7_ATTR)
        self.assertEqual(hit_lines(done.stdout, "F7"), F7_ATTR_HITS, out)
        self.assertEqual(done.returncode, 1, "block=[F7] 而有命中,要擋:\n" + out)
        self.assertNotIn(NO_API, done.stdout, out)

    def test_b2_from_import_of_an_unlisted_name_and_of_an_underscore_name(self):
        """B2 from <M> import step(不在名單)中;from 任何模組 import _secret 中;import http 過。"""
        done, out = self.lint(F7_FROM)
        self.assertEqual(hit_lines(done.stdout, "F7"), F7_FROM_HITS, out)

    def test_b2_a_module_without_verify_api_skips_the_list_but_not_underscores(self):
        """B2 api 的檔沒宣告 VERIFY_API 或檔不在 → 印「F7 <M> 沒宣告 VERIFY_API,名單檢查略過」,(iii) 照查。"""
        for where, body in (("demo/test_browsers.py", BROWSERS_WITHOUT_API),
                            ("demo/nope.py", None)):
            with self.subTest(api_file=where):
                self.write("demo/test_browsers.py", BROWSERS_WITHOUT_API)
                self.configure(api={"test_browsers": where}, block=["F7"])
                done, out = self.lint(F7_ATTR)
                notice = [row for row in done.stdout.splitlines()
                          if "F7 test_browsers " + NO_API in row]
                self.assertTrue(notice, "沒印略過那一行:\n" + out)
                self.assertIn(NO_API_TAIL, notice[0], out)
                self.assertEqual(hit_lines(done.stdout, "F7"), F7_UNDERSCORE_ONLY, out)


class B3PinnedCountsAndShas(LintSandbox):

    def test_b3_exact_counts_and_sha_literals_hit_and_a_reasoned_pin_escapes(self):
        """B3 len==3 兩種序、count==2、sha 字面配 hexdigest、空 # pin: 中;有理由的 pin 與下限過。"""
        self.configure(block=["F8"])
        done, out = self.lint(F8_ALL)
        self.assertEqual(hit_lines(done.stdout, "F8"), F8_ALL_HITS, out)
        self.assertEqual(done.returncode, 1, out)

    def test_b3_an_empty_pin_is_named(self):
        """B3 `# pin:` 後空著 → 照命中並註明「pin 沒寫理由」。"""
        done, out = self.lint(F8_ALL)
        self.assertIn(NO_REASON, line_of(done.stdout, "F8", F8_EMPTY_PIN), out)


class B4TimeReadTwiceAndAtImport(LintSandbox):

    def test_b4_module_and_class_body_and_twice_in_one_function(self):
        """B4 模組層 TODAY、class 本體 DAY 中(ii);同函式種資料再在 assertEqual 裡取中(i);setUp 取一次再比過。"""
        self.configure(block=["F9"])
        done, out = self.lint(F9_ALL)
        lines = hit_lines(done.stdout, "F9")
        self.assertIn(F9_MODULE, lines, "模組層 date.today():\n" + out)
        self.assertIn(F9_CLASS, lines, "class 本體 time.strftime(:\n" + out)
        self.assertTrue(lines & set(F9_TWICE), "同函式取兩次、一次在 assert 裡:\n" + out)
        self.assertFalse(lines & set(F9_QUIET), "setUp 取一次再比不該中:\n" + out)
        self.assertEqual(done.returncode, 1, out)


class B5PrintOnlyRules(LintSandbox):

    ALL_ON = ["F7", "F8", "F9", "F10", "F11", LAST]

    def test_b5_each_positive_shape_is_printed_and_never_blocks(self):
        """B5 F10 sleep 後正向 assert、F11 wait_until 字面與 assert 無交集、第十二條 src.index( —— 印出、rc 0。"""
        self.configure(block=self.ALL_ON)
        for rule, case, where in (("F10", F10_HIT, F10_LINES),
                                  ("F11", F11_HIT, F11_LINES),
                                  (LAST, IDX_HIT, range(IDX_LINE, IDX_LINE + 1))):
            with self.subTest(rule=rule):
                done, out = self.lint(case)
                self.assertTrue(hit_lines(done.stdout, rule) & set(where),
                                "%s 沒印在 %s:\n%s" % (rule, list(where), out))
                self.assertEqual(done.returncode, 0, "%s 只印不擋:\n%s" % (rule, out))

    def test_b5_each_negative_shape_is_not_printed(self):
        """B5 assertFalse / 字面有交集 / 名字不是 src —— 不印。"""
        self.configure(block=self.ALL_ON)
        for rule, case in (("F10", F10_MISS), ("F11", F11_MISS), (LAST, IDX_MISS)):
            with self.subTest(rule=rule):
                done, out = self.lint(case)
                self.assertEqual(hit_lines(done.stdout, rule), set(), out)
                self.assertEqual(done.returncode, 0, out)


class B6WhitelistGenerator(LintSandbox):

    def test_b6_baseline_prints_a_sorted_deduplicated_json_array(self):
        """B6 兩檔各中 F7、F8(一檔兩次 F8)→ stdout 等於寫死的陣列、rc 0。"""
        self.write(OTHER, BASE_ONE)
        self.write(CASE, BASE_TWO)
        done = self.run_py("scripts/verify-case.py", "lint", BASELINE_FLAG, CASE, OTHER)
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        try:
            got = json.loads(done.stdout)
        except ValueError:
            self.fail("stdout 不是一份 JSON 陣列:\n" + out)
        self.assertEqual(got, BASELINE_WANT, out)

    def test_b6_an_unreadable_file_is_rc_2(self):
        """B6 檔讀不到 rc=2。"""
        done = self.run_py("scripts/verify-case.py", "lint", BASELINE_FLAG, "verify/x/nope.py")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)


class B7Docs(unittest.TestCase):

    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_b7_design_doc_lists_the_six_new_rules_and_the_config_keys(self):
        """B7 docs/DESIGN-VERIFY-CASES.md 含 F7 到第十二條六個編號、allow 鍵與 VERIFY_API。"""
        # 訊息不貼文件全文:文件裡有 ImportError 這種字,貼進去 red 會把這條歸成 import 失敗。
        text = self.read("docs/DESIGN-VERIFY-CASES.md")
        for rule in ("F7", "F8", "F9", "F10", "F11", LAST):
            self.assertTrue(re.search(r"\b%s\b" % rule, text), "設計文件沒有 %s" % rule)
        self.assertTrue(ALLOW_KEY in text, "設計文件沒有 allow 鍵")
        self.assertTrue("VERIFY_API" in text, "設計文件沒有 VERIFY_API")

    def test_b7_script_header_counts_twelve_rules(self):
        """B7 verify-case.py 檔頭「lint 的六條」改成十二條。"""
        text = self.read("scripts/verify-case.py")
        self.assertFalse("lint 的六條" in text, "檔頭還寫「lint 的六條」")
        self.assertTrue("lint 的十二條" in text, "檔頭沒寫「lint 的十二條」")

    def test_b7_lint_help_mentions_the_baseline_flag(self):
        """B7 verify-case.py lint --help 提到 baseline 旗標。"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        done = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify-case.py"),
                               "lint", "--help"], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertIn(BASELINE_FLAG, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
