"""#126 閘門 lint 失敗進 failures[](kind LINT),且 needs_verifier 而案例還沒交時任一輪都先派驗證者。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 夾具自己寫下的值)
A1 | integ | UNLINTED_CASE(F1)當 verify.files 跑 gate --ticket 7 | rc≠0、「不跑 check」、沒有「驗證者案例的綠」;failures[] 一列 kind LINT、case verify-case-lint:<rel>、file <rel>、line 1、excerpt 含 F1 那整行、log 指向含那行的檔 | 票面 A1 原話;行號 1 是 F1 印的
A2 | integ | verify.files 指向不存在的檔;假 lint(rc 1、一行都對不上);needs_verifier=true 而 files 空;三個例外 | 一列 LINT file=相對路徑 line 0 excerpt 含「讀不到」那行;保底恰一列 case "verify-case-lint:" file "" line 0 excerpt == 最後一個非空行;rc 3 恰一列 (no-cases);例外 0 列 | 票面 A2 原話
A3 | integ | lint 過但有不擋提示;lint 不過帶不擋行;候選上紅的案例 | 0 列 LINT;LINT 列數 == 「不過(N 條)」的 N、不擋行不成列;status.py failures 不印 LINT;FAIL 列照舊一筆 | 票面 A3 原話
A4 | integ | needs_verifier true、baseline 空、上一輪 gate 紅(RED_LOG,第 1 輪)跑 auto-fix --no-review | 第 2 輪 role=verifier 的 agent.start 恰一筆、stdout 有「案例還沒交,第 2 輪先派驗證者」;baseline 非空 / false 0 筆;缺 → 0 筆且 stderr 點名 D-028 | 票面 A4 原話
A5 | integ | 上一輪 gate 的 failures 全是 LINT(手寫同形 status.json);混合 LINT + FAIL | 第 2 輪沒有 worker 的 agent.start、恰一筆 verifier;baseline 空用一般範本、非空走 test_defect 段且列出 LINT 列;混合派 worker、LINT 列帶「驗證者的」 | 票面 A5 原話;test_plan T3
A6 | integ | 第 2 輪 worker 回 test_defect(interface_fixed 缺,等 patch) | 第 2 輪 verifier 的 agent.start 恰一筆 | 票面 A6 原話
A7 | integ | 全 LINT 的狀態檔已是第 3 輪;needs_verifier true 而上一輪沒有歸因 | rc 1「三輪耗盡」不起任何 agent;rc 4、Blocked、「沒有歸因」、不起任何 agent | 票面 A7 原話
A8 | doc   | 讀 docs/WORKFLOW.md | 有 LINT(與 failures 同段)與「任一輪」(與驗證者同段) | 票面 A8 原話

## 介面字串(全部出自票面 acceptance;票面沒定的在這裡定)
LINT_KIND   = "LINT"                                  ← A1/A2/A5 列的 kind
CASE_PREFIX = "verify-case-lint:"                     ← A1 case 前綴、A2 保底 case 全文
NO_CASES    = "verify-case-lint:(no-cases)"           ← A2 缺口列
GAP_LINE    = "這張票沒有驗證者案例"                   ← A2 缺口列 excerpt
UNREADABLE  = "讀不到"                                 ← A2 不存在的檔
NOT_YET     = "案例還沒交,第 2 輪先派驗證者"           ← A4 stdout
TEST_DEFECT = "這一輪多的一件事:test_defect"           ← A5 baseline 非空的派工文
VERIFIERS   = "驗證者的"                               ← A5 混合時 worker 派工文的 LINT 列
狀態檔裡 LINT 列以外的鍵(subtest / engine / suspected_flaky)票面沒定,A5 手寫列照 status.py 既有列的形狀補齊。

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo、外層 AC_* 一律不進沙盒)與
tests/test_auto_fix.AutoFixBase。閘門那幾條照 tests/test_gate.VerifierCasesAtTheGate 的夾具重寫
(不繼承,免得把它的案例一起跑);A2 保底用沙盒裡一支提交過的假 scripts/verify-case.py(lint rc 1,
印兩行對不上格式的字)。auto-fix 那幾條的 worker / 驗證者是 config worker.command 指向的假 sh,
以 AC_ROLE 分流;上一輪的 LINT 狀態檔是 first_round() 的真閘門跑完後把 failures[] 換成手寫同形列。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;A5 不量驗證者交件後的
收件與重跑閘門(那一段沿用既有 test_defect 那條路,AParallelVerifierThenATestDefect 守著)。
"""
import json
import os
import re
import sys
import unittest

TAGS = ["verifier-gap"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import Sandbox  # noqa: E402
from test_auto_fix import AutoFixBase, RED_LOG, NO_ATTRIBUTION_LOG, \
    WORKER_REPORTS_TEST_DEFECT  # noqa: E402
from test_gate import PASSING, UNLINTED_CASE, VERIFIER_CASE  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
LINT_KIND = "LINT"
CASE_PREFIX = "verify-case-lint:"
NO_CASES = "verify-case-lint:(no-cases)"
GAP_LINE = "這張票沒有驗證者案例"
UNREADABLE = "讀不到"
NOT_YET = "案例還沒交,第 2 輪先派驗證者"
TEST_DEFECT = "這一輪多的一件事:test_defect"
VERIFIERS = "驗證者的"
VERIFIER_TITLE = "# 派工:驗證者 —— #1 第 2 輪"

CASE_REL = "verify/nav/test_ticket_7.py"
MISSING_REL = "verify/nav/test_ticket_404.py"

# lint 過、但有一條不擋的提示(F3:test_ 的 docstring 首行不是驗收編號)。
HINT_ONLY_CASE = VERIFIER_CASE.replace('"""A1 src/value.txt 的值是 2。"""',
                                       '"""src/value.txt 的值是 2。"""')

# A2 保底:lint rc 非零,而印的每一行都不是 `<檔>:<行>` 也不是「讀不到 <檔>」。
FAKE_LINT = """import sys

if sys.argv[1:2] == ["lint"]:
    print("lint stub: something went sideways")
    print("lint stub: the last words")
    print("")
    sys.exit(1)
sys.exit(2)
"""

# 假 agent:AC_ROLE 分流。worker 交一份會綠的 patch;驗證者什麼都不交(只記一行)。
AGENT = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
[ "$AC_ROLE" = worker ] || exit 0
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

EMPTY_PLAN = {"files": [], "tags": [], "run": "", "notes": ""}
HANDED_IN = {"files": ["verify/example/test_ticket_1.py"], "tags": [], "run": "", "notes": "",
             "baseline": {"stage": "red", "ok": True, "why": "",
                          "files": ["verify/example/test_ticket_1.py"]}}


def lint_rows(data):
    return [row for row in data.get("failures") or [] if row.get("kind") == LINT_KIND]


def line_with(text, needle):
    hits = [line for line in text.splitlines() if needle in line]
    return hits[0] if hits else None


def lint_row(rel, line_no, excerpt):
    """手寫的 LINT 列:票面 A1 定的六個鍵,其餘照 status.py 既有列的形狀。"""
    return {"case": CASE_PREFIX + rel, "kind": LINT_KIND, "subtest": "", "file": rel,
            "line": line_no, "engine": "", "log": "", "excerpt": excerpt,
            "suspected_flaky": False}


FAIL_ROW = {"case": "test_thing.T.test_thing", "kind": "FAIL", "subtest": "",
            "file": "tests/test_thing.py", "line": 6, "engine": "", "log": "",
            "excerpt": "AssertionError: 1 != 2 : 第一輪是紅的", "suspected_flaky": False}
F1_ROW = lint_row("verify/example/test_ticket_1.py", 1,
                  "verify-case lint: verify/example/test_ticket_1.py:1 F1 模組沒有 docstring")
MISSING_ROW = lint_row("verify/example/test_ticket_1.py", 0,
                       "verify-case lint: 讀不到 verify/example/test_ticket_1.py")
GAP_ROW = dict(lint_row("", 0, "gate: 這張票沒有驗證者案例(#1 的 verify.files 是空的)"),
               case=NO_CASES)


class GateBase(Sandbox):
    """照 tests/test_gate.VerifierCasesAtTheGate 的夾具:回歸層一支會綠的替身、src/value.txt=1。"""

    def setUp(self):
        super().setUp()
        self.write(os.path.join("tests", "test_land.py"), PASSING % "test_land")
        self.write(os.path.join("src", "value.txt"), "1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:主線上的值是 1")

    def a_ticket(self, case=None, **fields):
        if case is not None:
            self.write(os.path.join("verify", "nav", "__init__.py"), "")
            self.write(os.path.join("verify", "nav", "test_ticket_7.py"), case)
            fields.setdefault("verify", {"files": [CASE_REL], "tags": [], "run": "", "notes": ""})
        return self.make_ticket(7, allowed_write_paths=["verify/*", "src/*", "tests/*"], **fields)

    def gate(self):
        return self.run_sh("scripts/gate.sh", "tests/test_land.py", "--ticket", "7",
                           "--no-auto-fix")

    def gate_status(self):
        return self.status_of("7", kind="gate")


class LintRedEntersTheRedList(GateBase):
    """A1 / A3:lint 擋的格式錯成 LINT 列;不擋的不成列。"""

    def test_a1_an_f1_case_is_one_lint_row_naming_file_line_and_log(self):
        """A1 F1 的案例:rc≠0、不跑 check;failures[] 一列 LINT,case / file / line / excerpt / log 對得上 lint 那一行。"""
        self.a_ticket(UNLINTED_CASE)
        self.write(os.path.join("src", "value.txt"), "2\n")
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, said)
        self.assertIn("不跑 check", done.stdout)
        self.assertNotIn("驗證者案例的綠", done.stdout)
        printed = line_with(done.stdout, "%s:1 F1" % CASE_REL)
        self.assertIsNotNone(printed, "前提:lint 印了 F1 那一行\n" + said)
        rows = lint_rows(self.gate_status())
        f1 = [row for row in rows if "F1" in str(row.get("excerpt"))]
        self.assertEqual(len(f1), 1, "failures[] 沒有 F1 那一列:%r" % rows)  # pin:票面寫「恰一列 / 恰一筆」
        row = f1[0]
        self.assertEqual(row.get("case"), CASE_PREFIX + CASE_REL, row)
        self.assertEqual(row.get("file"), CASE_REL, row)
        self.assertEqual(row.get("line"), 1, row)
        self.assertIn(printed.strip(), row.get("excerpt"), row)
        log = str(row.get("log") or "")
        self.assertTrue(log, "LINT 列沒有 log:%r" % row)
        where = log if os.path.isabs(log) else os.path.join(self.repo, log)
        self.assertTrue(os.path.isfile(where), "log 指的檔不在:%s" % where)
        with open(where, encoding="utf-8") as handle:
            self.assertIn(printed.strip(), handle.read(), "log 指的不是 lint 的 log")

    def test_a3_rows_equal_the_blocking_count_and_hints_are_not_rows(self):
        """A3 lint 不過:LINT 列數 == 「不過(N 條)」的 N,不擋行不成列;status.py failures 不印 LINT。"""
        self.a_ticket(UNLINTED_CASE)
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertIn("不擋", done.stdout, "前提:這份案例印了一條不擋的提示\n" + said)
        count = re.search(r"不過\**[(（]\s*(\d+) 條", done.stdout)
        self.assertIsNotNone(count, said)
        rows = lint_rows(self.gate_status())
        self.assertEqual(len(rows), int(count.group(1)), rows)
        for row in rows:
            self.assertNotIn("不擋", str(row.get("excerpt")), row)
        ids = self.run_py("scripts/status.py", "failures", "--log",
                          os.path.join(self.home, "gate.log"))
        self.assertNotIn(CASE_PREFIX, ids.stdout + ids.stderr)

    def test_a3_a_passing_lint_with_a_hint_writes_no_lint_row(self):
        """A3 lint 過(只有不擋的 F3 提示):failures[] 沒有 LINT 列。"""
        self.a_ticket(HINT_ONLY_CASE)
        self.write(os.path.join("src", "value.txt"), "2\n")
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertIn("verify-case lint: 過", done.stdout, said)
        self.assertIn("F3", done.stdout, "前提:印了不擋的提示\n" + said)
        self.assertEqual(lint_rows(self.gate_status()), [])

    def test_a3_the_unittest_red_list_keeps_its_shape(self):
        """A3 lint 過、案例在候選上紅:紅榜恰一筆 FAIL 列(既有形狀),沒有 LINT 列。"""
        self.a_ticket(VERIFIER_CASE)
        done = self.gate()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.gate_status().get("failures") or []
        self.assertEqual(lint_rows({"failures": rows}), [], rows)
        self.assertEqual([(row.get("case"), row.get("kind")) for row in rows],
                         [("verify.nav.test_ticket_7.TheValue.test_a1_the_value_is_two", "FAIL")],
                         rows)
        for key in ("case", "kind", "subtest", "file", "line", "engine", "log", "excerpt",
                    "suspected_flaky"):
            self.assertIn(key, rows[0], rows[0])


class MissingCasesEnterTheRedList(GateBase):
    """A2:讀不到的案例檔、對不上的 lint 輸出、宣告了卻沒有案例 —— 都不准是空紅榜。"""

    def test_a2_a_missing_case_file_is_a_relative_line_zero_row(self):
        """A2 verify.files 指向不存在的檔:一列 LINT,file 是相對 repo 根的路徑、line 0、excerpt 含「讀不到」那行。"""
        self.a_ticket(verify={"files": [MISSING_REL], "tags": [], "run": "", "notes": ""})
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, said)
        printed = line_with(done.stdout, UNREADABLE)
        self.assertIsNotNone(printed, "前提:lint 印了讀不到那一行\n" + said)
        rows = lint_rows(self.gate_status())
        self.assertEqual(len(rows), 1, rows)  # pin:票面寫「恰一列 / 恰一筆」
        row = rows[0]
        self.assertTrue(str(row.get("case")).startswith(CASE_PREFIX), row)
        self.assertFalse(str(row.get("file")).startswith("/"), row)
        self.assertNotIn(self.home, str(row.get("file")), row)
        self.assertNotIn(os.path.expanduser("~"), str(row.get("file")), row)
        self.assertEqual(row.get("file"), MISSING_REL, row)
        self.assertEqual(row.get("line"), 0, row)
        self.assertIn(printed.strip(), row.get("excerpt"), row)

    def test_a2_an_unmatched_lint_failure_still_writes_exactly_one_row(self):
        """A2 保底:lint rc≠0 而一行都對不上 → 恰一列 case "verify-case-lint:"、file ""、line 0、excerpt == 最後一個非空行。"""
        self.write(os.path.join("scripts", "verify-case.py"), FAKE_LINT)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:假 lint")
        self.a_ticket(VERIFIER_CASE)
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, said)
        self.assertIn("lint stub: the last words", done.stdout, "前提:假 lint 有被叫到\n" + said)
        rows = lint_rows(self.gate_status())
        self.assertEqual(len(rows), 1, rows)  # pin:票面寫「恰一列 / 恰一筆」
        self.assertEqual((rows[0].get("case"), rows[0].get("file"), rows[0].get("line"),
                          rows[0].get("excerpt")),
                         (CASE_PREFIX, "", 0, "lint stub: the last words"), rows[0])

    def test_a2_declared_but_empty_files_is_one_no_cases_row(self):
        """A2 needs_verifier=true、宣告 verify 而 files 空:rc 3,恰一列 (no-cases)、file ""、line 0、excerpt 含那句話。"""
        self.a_ticket(needs_verifier=True, verify=dict(EMPTY_PLAN))
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertEqual(done.returncode, 3, said)
        rows = lint_rows(self.gate_status())
        self.assertEqual(len(rows), 1, rows)  # pin:票面寫「恰一列 / 恰一筆」
        self.assertEqual((rows[0].get("case"), rows[0].get("file"), rows[0].get("line")),
                         (NO_CASES, "", 0), rows[0])
        self.assertIn(GAP_LINE, rows[0].get("excerpt"), rows[0])

    def test_a2_the_three_exceptions_write_no_lint_row(self):
        """A2 waiver / needs_verifier=false / 沒宣告 verify:各 0 列 LINT。"""
        for fields in ({"needs_verifier": True, "verify": dict(EMPTY_PLAN),
                        "verify_waiver": {"by": "main", "reason": "純文字,沒有邏輯可紅"}},
                       {"needs_verifier": False, "verify": dict(EMPTY_PLAN)},
                       {"needs_verifier": True}):
            with self.subTest(fields=sorted(fields)):
                self.a_ticket(**fields)
                done = self.gate()
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(lint_rows(self.gate_status()), [])


class AutoFixBase126(AutoFixBase):

    def arm(self, agent=AGENT, **fields):
        self.set_worker(agent)
        return self.ticket_ready(**fields)

    def starts(self, role, round_no="2"):
        """該輪某角色的 agent.start。worker 那一筆**不帶 role**(run_worker 只記 agent=auto-fix),
        所以 worker 認「沒有 role 或 role=worker」—— 只比 role=="worker" 的話,「沒起 worker」永遠是綠的。"""
        wanted = (None, "", "worker") if role == "worker" else (role,)
        return [row for row in self.events() if row["kind"] == "agent.start"
                and row.get("role") in wanted and str(row.get("round")).strip('"') == round_no]

    def packet(self, name):
        found = self.result_files(name)
        self.assertEqual(len(found), 1, "找不到(或不只一份)%s:%s" % (name, found))  # pin:票面寫「恰一列 / 恰一筆」
        with open(found[0], encoding="utf-8") as handle:
            return handle.read()

    def last_gate_says(self, rows):
        """first_round() 的真閘門跑完之後,把那一份 gate 狀態檔的 failures[] 換成 rows。"""
        where = os.path.join(self.repo, "reports", "t1")
        for name in sorted(os.listdir(where), reverse=True):
            path = os.path.join(where, name, "status.json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("kind") != "gate":
                continue
            data["failures"] = rows
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            return
        self.fail("first_round() 沒留下 gate 狀態檔")


class EveryRoundDispatchesTheMissingVerifier(AutoFixBase126):
    """A4:needs_verifier=true 而 baseline 空,從第 2 輪接著跑也先派驗證者。"""

    def test_a4_round_two_starts_exactly_one_verifier_and_says_so(self):
        """A4 上一輪 gate 紅(RED_LOG、第 1 輪):第 2 輪恰一筆 verifier 的 agent.start,stdout 有那句話。"""
        self.arm(needs_verifier=True, interface_fixed=True, verify=dict(EMPTY_PLAN))
        self.status(1, RED_LOG)
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」
        self.assertIn(NOT_YET, done.stdout, said)

    def test_a4_a_handed_in_baseline_starts_no_verifier(self):
        """A4 對照:baseline 非空 → 第 2 輪不起驗證者。"""
        self.arm(needs_verifier=True, interface_fixed=True, verify=dict(HANDED_IN))
        self.status(1, RED_LOG)
        done = self.auto_fix("--no-review")
        self.assertEqual(self.starts("verifier"), [], done.stdout + done.stderr)
        self.assertNotIn(NOT_YET, done.stdout)

    def test_a4_needs_verifier_false_starts_no_verifier(self):
        """A4 對照:needs_verifier=false → 第 2 輪不起驗證者。"""
        self.arm(needs_verifier=False, interface_fixed=True, verify=dict(EMPTY_PLAN))
        self.status(1, RED_LOG)
        done = self.auto_fix("--no-review")
        self.assertEqual(self.starts("verifier"), [], done.stdout + done.stderr)

    def test_a4_needs_verifier_missing_starts_none_and_names_d028(self):
        """A4 對照:缺 needs_verifier → 第 2 輪不起,stderr 照舊點名 D-028。"""
        row = self.arm(interface_fixed=True, verify=dict(EMPTY_PLAN))
        self.assertNotIn("needs_verifier", row, "前提:票面沒有這一格")
        self.status(1, RED_LOG)
        done = self.auto_fix("--no-review")
        self.assertEqual(self.starts("verifier"), [], done.stdout + done.stderr)
        self.assertIn("D-028", done.stderr, done.stdout + done.stderr)


class AnAllLintRoundOnlyDispatchesTheVerifier(AutoFixBase126):
    """A5:上一輪紅榜全是 LINT → 這一輪只派驗證者;混合 → worker 照派、LINT 列標成驗證者的。"""

    def run_after(self, rows, **fields):
        fields.setdefault("needs_verifier", True)
        fields.setdefault("interface_fixed", True)
        self.arm(**fields)
        self.first_round()
        self.last_gate_says(rows)
        return self.auto_fix("--no-review")

    def test_a5_all_lint_with_no_baseline_sends_only_the_plain_verifier(self):
        """A5 全 LINT、baseline 空:第 2 輪沒有 worker、恰一個驗證者,派工文是一般範本。"""
        done = self.run_after([F1_ROW], verify=dict(EMPTY_PLAN))
        said = done.stdout + done.stderr
        self.assertEqual(self.starts("worker"), [], said)
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」
        packet = self.packet("dispatch-verifier-round2.md")
        self.assertIn(VERIFIER_TITLE, packet)
        self.assertNotIn(TEST_DEFECT, packet)

    def test_a5_a_no_cases_row_alone_sends_only_the_verifier(self):
        """A5 / T3 上一輪只有 (no-cases) 缺口列:第 2 輪沒有 worker、恰一個驗證者。"""
        done = self.run_after([GAP_ROW], verify=dict(EMPTY_PLAN))
        said = done.stdout + done.stderr
        self.assertEqual(self.starts("worker"), [], said)
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」

    def test_a5_all_lint_with_a_baseline_takes_the_test_defect_packet(self):
        """A5 全 LINT、baseline 非空:第 2 輪沒有 worker、恰一個驗證者,派工文走 test_defect 那一段且列出 LINT 列。"""
        done = self.run_after([MISSING_ROW], verify=dict(HANDED_IN))
        said = done.stdout + done.stderr
        self.assertEqual(self.starts("worker"), [], said)
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」
        packet = self.packet("dispatch-verifier-round2.md")
        self.assertIn(TEST_DEFECT, packet)
        self.assertIn(MISSING_ROW["case"], packet)

    def test_a5_a_mixed_red_list_dispatches_the_worker_and_marks_the_lint_row(self):
        """A5 混合(LINT + FAIL):worker 照派,worker 派工文裡 LINT 那一列帶「驗證者的」。"""
        done = self.run_after([F1_ROW, FAIL_ROW], verify=dict(HANDED_IN))
        said = done.stdout + done.stderr
        self.assertEqual(len(self.starts("worker")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」
        lines = self.packet("dispatch-round2.md").splitlines()
        spots = [index for index, line in enumerate(lines) if F1_ROW["case"] in line]
        self.assertTrue(spots, "worker 派工文沒列 LINT 那一列")
        marked = [index for index in spots
                  if any(VERIFIERS in line for line in lines[max(0, index - 2):index + 4])]
        self.assertTrue(marked, "LINT 列旁邊沒有「%s」:\n%s"
                        % (VERIFIERS, "\n".join(lines[spots[0] - 2:spots[0] + 4])))


class RoundsAndStopsStayTheSame(AutoFixBase126):
    """A6 / A7:一輪最多一個新派的驗證者;輪數上限與「沒有歸因」照舊。"""

    def test_a6_a_test_defect_round_starts_one_verifier_not_two(self):
        """A6 第 2 輪 worker 回 test_defect(等 patch 的票):第 2 輪 verifier 的 agent.start 恰一筆。"""
        self.arm(WORKER_REPORTS_TEST_DEFECT, needs_verifier=True, verify=dict(EMPTY_PLAN),
                 in_scope=["src/app.py"])
        self.first_round()
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertIn("test_defect", done.stdout, said)
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」

    def test_a7_an_all_lint_round_still_counts_toward_the_limit(self):
        """A7 全 LINT 的狀態檔已是第 3 輪(retry_limit 2):rc 1、三輪耗盡、不起任何 agent。"""
        self.arm(needs_verifier=True, verify=dict(EMPTY_PLAN), retry_limit=2)
        self.status(1, RED_LOG, round_no=3)
        self.last_gate_says([F1_ROW])
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual([row for row in self.events() if row["kind"] == "agent.start"], [])
        self.assertIn("三輪耗盡", self.inbox_list())

    def test_a7_no_attribution_still_stops_before_any_agent(self):
        """A7 needs_verifier=true、baseline 空,上一輪沒有歸因:照舊 rc 4、Blocked、不起任何 agent。"""
        self.arm(needs_verifier=True, interface_fixed=True, verify=dict(EMPTY_PLAN))
        self.status(1, NO_ATTRIBUTION_LOG)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("沒有歸因", done.stderr)
        self.assertEqual([row for row in self.events() if row["kind"] == "agent.start"], [])
        self.assertEqual(self.load_ticket("1")["state"], "Blocked")


class TheWorkflowDocSaysSo(unittest.TestCase):
    """A8:docs/WORKFLOW.md 寫明兩件事。"""

    def paragraphs(self):
        with open(os.path.join(ROOT, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            return re.split(r"\n\s*\n", handle.read())

    def test_a8_lint_red_is_a_lint_row_in_failures(self):
        """A8 (a) 有一段同時寫 LINT 與 failures(驗證者案例 lint 不過以 kind=LINT 進 failures[])。"""
        hits = [text for text in self.paragraphs() if "LINT" in text and "failures" in text]
        self.assertTrue(hits, "docs/WORKFLOW.md 沒有寫 LINT 列進 failures[]")

    def test_a8_any_round_dispatches_the_missing_verifier(self):
        """A8 (b) 有一段同時寫「任一輪」與驗證者、needs_verifier。"""
        hits = [text for text in self.paragraphs()
                if "任一輪" in text and "驗證者" in text and "needs_verifier" in text]
        self.assertTrue(hits, "docs/WORKFLOW.md 沒有寫 needs_verifier 時任一輪都先派驗證者")


if __name__ == "__main__":
    unittest.main()
