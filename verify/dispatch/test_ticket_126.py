"""#126 缺案例進 failures[](kind GAP),且 needs_verifier 而案例還沒交時任一輪都先派驗證者。

#138(D-054 Q4)刪了案例 lint:原 A1、A3、A5、A8 (a) 與 A2 的「讀不到」「保底」兩條隨 lint 刪掉;
「宣告了 verify 卻沒有案例」那一列改由閘門直接寫(kind GAP、case verify-case:(no-cases)),不經 lint。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 夾具自己寫下的值)
A2 | integ | needs_verifier=true 而 files 空;三個例外 | rc 3 恰一列 (no-cases);例外 0 列 | 票面 A2 原話
A4 | integ | needs_verifier true、baseline 空、上一輪 gate 紅(RED_LOG,第 1 輪)跑 auto-fix --no-review | 第 2 輪 role=verifier 的 agent.start 恰一筆、stdout 有「案例還沒交,第 2 輪先派驗證者」;baseline 非空 / false 0 筆(缺 = true 由 tests/test_auto_fix.py 守,D-054 Q10) | 票面 A4 原話
A6 | integ | 第 2 輪 worker 回 test_defect(interface_fixed 缺,等 patch) | 第 2 輪 verifier 的 agent.start 恰一筆 | 票面 A6 原話
A7 | integ | 只有缺口列的狀態檔已是第 3 輪;needs_verifier true 而上一輪沒有歸因 | rc 1「三輪耗盡」不起任何 agent;沒有歸因的那一條 #125 A3 改成派一輪診斷(第 2 輪派工文有 `## 診斷` 與 log 最後一行、attempt.start round_kind=diagnosis diagnosis=unattributed、起了第 2 輪 worker、不是 rc 4) | 票面 A7 原話;#125 A3 原話
A8 | doc   | 讀 docs/WORKFLOW.md | 有「任一輪」(與驗證者同段) | 票面 A8 原話

## 介面字串(全部出自票面 acceptance;票面沒定的在這裡定)
GAP_KIND    = "GAP"                                   ← A2 / A7 缺口列的 kind
NO_CASES    = "verify-case:(no-cases)"                ← A2 缺口列
GAP_LINE    = "這張票沒有驗證者案例"                   ← A2 缺口列 excerpt
NOT_YET     = "案例還沒交,第 2 輪先派驗證者"           ← A4 stdout
狀態檔裡缺口列以外的鍵(subtest / engine / suspected_flaky)票面沒定,A7 手寫列照 status.py 既有列的形狀補齊。

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo、外層 AC_* 一律不進沙盒)與
tests/test_auto_fix.AutoFixBase。閘門那幾條照 tests/test_gate.VerifierCasesAtTheGate 的夾具重寫
(不繼承,免得把它的案例一起跑)。auto-fix 那幾條的 worker / 驗證者是 config worker.command 指向的假 sh,
以 AC_ROLE 分流;上一輪的缺口狀態檔是手寫同形列。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例。
"""
import glob
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
from test_gate import PASSING  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
GAP_KIND = "GAP"
NO_CASES = "verify-case:(no-cases)"
GAP_LINE = "這張票沒有驗證者案例"
NOT_YET = "案例還沒交,第 2 輪先派驗證者"

# 假 agent:AC_ROLE 分流。worker 交一份會綠的 patch;驗證者什麼都不交(只記一行)。
AGENT = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
W=$PWD
cd "$AC_WORK"
[ "$AC_ROLE" = worker ] || exit 0
mkdir -p "$W"/tests
cat > "$W"/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
(cd "$W" && git add -A && git commit -q -m "t$AC_TICKET 第 $AC_ROUND 輪") || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

EMPTY_PLAN = {"files": [], "tags": [], "run": "", "notes": ""}
HANDED_IN = {"files": ["verify/example/test_ticket_1.py"], "tags": [], "run": "", "notes": "",
             "baseline": {"stage": "red", "ok": True, "why": "",
                          "files": ["verify/example/test_ticket_1.py"]}}


def gap_rows(data):
    return [row for row in data.get("failures") or [] if row.get("kind") == GAP_KIND]


GAP_ROW = {"case": NO_CASES, "kind": GAP_KIND, "subtest": "", "file": "", "line": 0,
           "engine": "", "log": "", "excerpt": "gate: 這張票沒有驗證者案例(#1 的 verify.files 是空的)",
           "suspected_flaky": False}


class GateBase(Sandbox):
    """照 tests/test_gate.VerifierCasesAtTheGate 的夾具:回歸層一支會綠的替身、src/value.txt=1。"""

    def setUp(self):
        super().setUp()
        self.write(os.path.join("tests", "test_land.py"), PASSING % "test_land")
        self.write(os.path.join("src", "value.txt"), "1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:主線上的值是 1")

    def a_ticket(self, **fields):
        return self.make_ticket(7, **fields)

    def gate(self):
        return self.run_sh("scripts/gate.sh", "tests/test_land.py", "--ticket", "7",
                           "--no-auto-fix")

    def gate_status(self):
        return self.status_of("7", kind="gate")


class MissingCasesEnterTheRedList(GateBase):
    """A2:宣告了卻沒有案例 —— 不准是空紅榜。"""

    def test_a2_declared_but_empty_files_is_one_no_cases_row(self):
        """A2 needs_verifier=true、宣告 verify 而 files 空:rc 3,恰一列 (no-cases)、file ""、line 0、excerpt 含那句話。"""
        self.a_ticket(needs_verifier=True, verify=dict(EMPTY_PLAN))
        done = self.gate()
        said = done.stdout + done.stderr
        self.assertEqual(done.returncode, 3, said)
        rows = gap_rows(self.gate_status())
        self.assertEqual(len(rows), 1, rows)  # pin:票面寫「恰一列 / 恰一筆」
        self.assertEqual((rows[0].get("case"), rows[0].get("file"), rows[0].get("line")),
                         (NO_CASES, "", 0), rows[0])
        self.assertIn(GAP_LINE, rows[0].get("excerpt"), rows[0])

    def test_a2_the_exceptions_write_no_gap_row(self):
        """A2 needs_verifier=false / 沒宣告 verify:各 0 列 GAP。"""
        for fields in ({"needs_verifier": False, "verify": dict(EMPTY_PLAN)},
                       {"needs_verifier": True}):
            with self.subTest(fields=sorted(fields)):
                self.a_ticket(**fields)
                done = self.gate()
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(gap_rows(self.gate_status()), [])


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


class RoundsAndStopsStayTheSame(AutoFixBase126):
    """A6 / A7:一輪最多一個新派的驗證者;輪數上限與「沒有歸因」照舊。"""

    def test_a6_a_test_defect_round_starts_one_verifier_not_two(self):
        """A6 第 2 輪 worker 回 test_defect(等 patch 的票):第 2 輪 verifier 的 agent.start 恰一筆。"""
        self.arm(WORKER_REPORTS_TEST_DEFECT, needs_verifier=True, verify=dict(EMPTY_PLAN))
        self.first_round()
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertIn("test_defect", done.stdout, said)
        self.assertEqual(len(self.starts("verifier")), 1, said)  # pin:票面寫「恰一列 / 恰一筆」

    def test_a7_a_gap_only_round_still_counts_toward_the_limit(self):
        """A7 只有缺口列的狀態檔已是第 3 輪(retry_limit 2):rc 1、三輪耗盡、不起任何 agent。"""
        self.arm(needs_verifier=True, verify=dict(EMPTY_PLAN), retry_limit=2)
        self.status(1, RED_LOG, round_no=3)
        self.last_gate_says([GAP_ROW])
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual([row for row in self.events() if row["kind"] == "agent.start"], [])
        self.assertIn("三輪耗盡", self.inbox_list())

    def test_a7_no_attribution_still_stops_before_any_agent(self):
        """A7 needs_verifier=true、baseline 空,上一輪沒有歸因。#125 A3(D-076)取代了「rc 4、不起任何 agent」:
        第一次沒有歸因是**一輪診斷** —— 第 2 輪派工文有 `## 診斷`(含 gate log 最後一行)、
        ticket.attempt.start 帶 round_kind=diagnosis diagnosis=unattributed、起了第 2 輪 worker,不在派工前停在 rc 4。
        (案例名照舊,讓紅榜對得上;期望值出自 #125 票面 A3 原話。)"""
        self.arm(needs_verifier=True, interface_fixed=True, verify=dict(EMPTY_PLAN))
        self.status(1, NO_ATTRIBUTION_LOG)
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 4, said)
        found = sorted(glob.glob(os.path.join(self.repo, "reports", "t1", "*", "dispatch-round2.md")))
        self.assertEqual(len(found), 1, "第 2 輪(診斷)要有一份派工文:%s\n%s" % (found, said))
        packet = self.read(found[0])
        self.assertIn("## 診斷", packet)
        self.assertIn("ModuleNotFoundError: No module named 'test_thing'", packet)  # log 最後一行
        starts = [row for row in self.events() if row["kind"] == "ticket.attempt.start"
                  and str(row.get("attempt")).strip('"') == "2"]
        self.assertEqual(len(starts), 1, said)
        self.assertEqual((starts[0].get("round_kind"), starts[0].get("diagnosis")),
                         ("diagnosis", "unattributed"), starts[0])
        self.assertTrue(self.starts("worker"), "診斷輪要起 worker:\n" + said)


class TheWorkflowDocSaysSo(unittest.TestCase):
    """A8:docs/WORKFLOW.md 寫明兩件事。"""

    def paragraphs(self):
        with open(os.path.join(ROOT, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            return re.split(r"\n\s*\n", handle.read())

    def test_a8_any_round_dispatches_the_missing_verifier(self):
        """A8 (b) 有一段同時寫「任一輪」與驗證者、needs_verifier。"""
        hits = [text for text in self.paragraphs()
                if "任一輪" in text and "驗證者" in text and "needs_verifier" in text]
        self.assertTrue(hits, "docs/WORKFLOW.md 沒有寫 needs_verifier 時任一輪都先派驗證者")


if __name__ == "__main__":
    unittest.main()
