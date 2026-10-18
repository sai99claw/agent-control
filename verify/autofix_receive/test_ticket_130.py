"""#130 驗證者交 0 byte 的 patch-verify = 這輪沒改案例:有 worker patch 只套它,沒有就只併驗證者計畫、直接重跑閘門。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance / test_plan 字面、夾具自己寫下的值)
A1 | integ | #107 形狀:第 2 輪 worker 交產品檔 patch + test_defect;驗證者 `: > patch-verify.diff`、result 帶 verify | stdout 有 SAME;沒有「套不上」;t1 有 src/worker_change.py(替身寫的那一行);票 verify.files == 夾具值、baseline red;末筆 test_defect disposition=fixed;第 2 輪 gate.rerun | 票面 A1 原話;T1
A2 | integ | interface_fixed 第 1 輪,平行驗證者交 0 byte + verify,worker 交 tests/test_thing.py | 同 A1:SAME、沒有「套不上(apply rc=3)」、t1 有 worker 的檔、verify.files == 夾具值、第 1 輪 gate.rerun | 票面 A2;T2
A3 | integ | (a) 上一輪紅榜只有「讀不到」那一列 LINT(#98 形狀)→ lint_round 驗證者 0 byte + verify;(b) worker 只交 test_defect、驗證者 0 byte + verify;閘門替身綠 | SAME 與 PLAN_ONLY;t1 頭 sha 前後相同、替身閘門跑在那個 sha;verify 照 #100 A9 併法(files 聯集、run/notes 空才採);gate.rerun 第 2 輪;InReview;(b) disposition=fixed | 票面 A3;T3
A4 | integ | `apply.sh verify-plan 1 --evidence-verifier EV`(red / check / 沒有 result 塊);`--help` | rc 0、票 verify 四格與 baseline 併進、分支清單與 HEAD 不變、沒有 t1;check 不收、印「不是 stage=red」;沒塊 → rc≠0、stderr 有 UNPARSED、票不動;--help 有 verify-plan | 票面 A4;T4;「不是 stage=red」是 apply 既有字樣
A5 | integ | lint_round 0 byte 而 result 沒有 verify / 沒有 result 塊;lint_round patch-verify 一行文字;worker 自己交 0 byte | 照舊 Blocked、decision 有「驗證者交了空的 patch-verify」、不跑閘門;一行文字 → 「套不上(apply rc=3)」;worker 0 byte → 「空的 patch 不是綠」;三者都沒有 SAME | 票面 A5;T5
A6 | integ + doc | A3(a) 那一輪的 attempt 事件;docs/WORKFLOW.md | ticket.attempt.start / done 都有第 2 輪;WORKFLOW 有一段同時寫 patch-verify、0 byte、計畫、閘門 | 票面 A6 原話

## 介面字串(全部出自票面;票面沒定的在這裡定)
SAME       = "驗證者這輪沒有改動"            ← A1/A2/A3 stdout(票面 verify_strings)
PLAN_ONLY  = "只併驗證者計畫"                ← A3 stdout(票面 verify_strings)
SUBCOMMAND = "verify-plan"                   ← A4 子命令與 --help(票面 verify_strings)
UNPARSED   = "驗證者的 result 解析不到"      ← A4 stderr(票面 A4「照既有字樣」)
NOT_APPLIED = "套不上(apply rc=3)"         ← A2/A5(票面原字)
EMPTY_PAGE = "驗證者交了空的 patch-verify"   ← A5 lint_round 那一頁(票面原字)
EMPTY_GUARD = "空的 patch 不是綠"            ← A5 worker 0 byte(票面原字,apply 既有字樣)
NOT_RED    = "不是 stage=red"                ← A4 check 不收(apply 既有字樣)

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo、外層 AC_* 一律不進沙盒)、
tests/test_auto_fix.AutoFixBase 與 tests/test_apply.ApplyBase。worker / 驗證者是 config
worker.command 指向的假 sh(AC_ROLE 分流,交件形狀由 @…@ 換);閘門替身是 gate.rerun_cmd:
把副本的 HEAD 記進一個檔、exit 0(T3「閘門替身綠」)。上一輪全 LINT 的狀態檔照 #126 的做法:
first_round() 的真閘門跑完,把 failures[] 換成手寫同形列。驗證者 result 指向沙盒本來就有、
lint 過的 verify/example/test_example.py。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;不量閘門紅那條路的
下一輪(閘門替身一律綠);A6「既有斷言不變」由 tests/test_auto_fix.py 全檔守(T6,閘門跑)。
"""
import json
import os
import sys
import unittest

TAGS = ["verifier-empty-patch"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_auto_fix import AutoFixBase  # noqa: E402
from test_apply import ApplyBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
SAME = "驗證者這輪沒有改動"
PLAN_ONLY = "只併驗證者計畫"
SUBCOMMAND = "verify-plan"
UNPARSED = "驗證者的 result 解析不到"
NOT_APPLIED = "套不上(apply rc=3)"
EMPTY_PAGE = "驗證者交了空的 patch-verify"
EMPTY_GUARD = "空的 patch 不是綠"
NOT_RED = "不是 stage=red"

# 驗證者 result 的 verify 計畫:夾具值,斷言從這裡讀。
CASE_FILE = "verify/example/test_example.py"
PLAN = {"files": [CASE_FILE], "tags": ["example"],
        "run": "python3 scripts/verify.py --tag example",
        "notes": "#130 夾具:案例不用改,只改 verify.files"}
RED_RECORD = {"stage": "red", "ok": True, "why": "", "files": [CASE_FILE]}
MISSING_REL = "verify/example/test_ticket_404.py"

# 假 agent。worker:@WORKER@ = change(新增產品檔)/ case(新增 tests/test_thing.py)/
# empty(0 byte 的 patch)/ none(不交 patch);@OBJECT@ = 1 時只交一行 test_defect 反駁。
# 驗證者:@VPATCH@ = empty(0 byte)/ text(一行文字、沒有檔頭);@VEVIDENCE@ = plan / noplan / prose。
AGENT = r"""#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
if [ "$AC_ROLE" = worker ]; then
    case "@WORKER@" in
        change)
            mkdir -p work/src
            printf 'CHANGED = True\n' > work/src/worker_change.py
            diff -ruN base work > "patch-round$AC_ROUND.diff" || true ;;
        case)
            mkdir -p work/tests
            cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
            diff -ruN base work > "patch-round$AC_ROUND.diff" || true ;;
        empty)
            : > "patch-round$AC_ROUND.diff" ;;
    esac
    if [ "@OBJECT@" = 1 ]; then
        printf 'OBJECTION: test_defect fixture 把正確結果寫成 2\n' > "EVIDENCE-round$AC_ROUND.md"
    else
        printf '# 第 %s 輪\n已排除的假設:沒有\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
    fi
    exit 0
fi
case "@VPATCH@" in
    empty) : > patch-verify.diff ;;
    text) echo 'the cases did not need a change' > patch-verify.diff ;;
esac
case "@VEVIDENCE@" in
    plan)
        sed "s/@R@/$AC_ROUND/g" > EVIDENCE-verifier.md <<'EV'
# verifier
案例不用改,只改 verify.files。

## result

```result
@PLAN_RESULT@
```
EV
        ;;
    noplan)
        sed "s/@R@/$AC_ROUND/g" > EVIDENCE-verifier.md <<'EV'
# verifier
案例沒改。

## result

```result
{"ticket": "1", "role": "verifier", "round": @R@, "rc": 0, "objection": null}
```
EV
        ;;
    prose)
        printf '# verifier\n案例沒改。\n' > EVIDENCE-verifier.md ;;
esac
"""

PLAN_RESULT = json.dumps({"ticket": "1", "role": "verifier", "round": "@R@", "rc": 0,
                          "verify": PLAN, "baseline": RED_RECORD, "objection": None},
                         ensure_ascii=False).replace('"@R@"', "@R@")


def agent(worker="none", objects=False, vpatch="empty", vevidence="plan"):
    return (AGENT.replace("@WORKER@", worker).replace("@OBJECT@", "1" if objects else "0")
            .replace("@VPATCH@", vpatch).replace("@VEVIDENCE@", vevidence)
            .replace("@PLAN_RESULT@", PLAN_RESULT))


def lint_row(rel, excerpt):
    """手寫的 LINT 列(#126 A1 定的形狀)。"""
    return {"case": "verify-case-lint:" + rel, "kind": "LINT", "subtest": "", "file": rel,
            "line": 0, "engine": "", "log": "", "excerpt": excerpt, "suspected_flaky": False}


MISSING_ROW = lint_row(MISSING_REL, "verify-case lint: 讀不到 " + MISSING_REL)


class ReceiveBase(AutoFixBase):

    def heads_log(self):
        return os.path.join(self.home, "gate-heads.log")

    def arm(self, body, **fields):
        """閘門替身(T3):在副本裡記下 HEAD、exit 0。"""
        self.set_worker(body, rerun_cmd="git rev-parse HEAD >> '%s'; exit 0" % self.heads_log())
        fields.setdefault("retry_limit", 1)
        return self.ticket_ready(**fields)

    def gate_heads(self):
        if not os.path.exists(self.heads_log()):
            return []
        with open(self.heads_log(), encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def reruns(self, round_no):
        return [row for row in self.events() if row["kind"] == "gate.rerun"
                and str(row.get("attempt")).strip('"') == round_no]

    def attempts(self, kind, round_no):
        return [row for row in self.events() if row["kind"] == kind
                and str(row.get("attempt")).strip('"') == round_no]

    def decisions(self):
        return [str(row.get("note")) for row in self.events() if row["kind"] == "decision.asked"]

    def plan(self):
        return self.load_ticket("1").get("verify") or {}

    def last_test_defect(self):
        rows = [row for row in self.load_ticket("1").get("objections") or []
                if row.get("category") == "test_defect"]
        self.assertTrue(rows, "票上沒有 test_defect 反駁")
        return rows[-1]

    def branch_head(self):
        return self.git("rev-parse", "t1").strip()

    def last_gate_says(self, rows):
        """first_round() 的真閘門跑完之後,把那一份 gate 狀態檔的 failures[] 換成 rows(#126 的做法)。"""
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

    def lint_round(self, body):
        """#98 形狀:verify.files 指向不存在的檔,上一輪紅榜只有「讀不到」那一列 LINT。"""
        self.arm(body, needs_verifier=True, interface_fixed=True,
                 verify={"files": [MISSING_REL], "tags": [], "run": "", "notes": ""})
        self.first_round()
        self.last_gate_says([MISSING_ROW])
        before = self.branch_head()
        return before, self.auto_fix("--no-review")


class AWorkerPatchIsAppliedAlone(ReceiveBase):
    """A1 / A2:有 worker 的 patch 時,0 byte 的 patch-verify 不交給 apply。"""

    def test_a1_a_test_defect_with_a_worker_patch_applies_only_the_worker_patch(self):
        """A1 worker 交產品檔 patch + test_defect、驗證者 0 byte + verify:只套 worker 那份,計畫併進票、反駁 fixed、閘門照跑。"""
        self.arm(agent(worker="change", objects=True), in_scope=["src/app.py"])
        self.first_round()
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertIn(SAME, done.stdout, said)
        self.assertNotIn("套不上", said)
        tree = self.git("ls-tree", "-r", "--name-only", "t1").splitlines()
        self.assertIn("src/worker_change.py", tree, said)
        self.assertEqual(self.git("show", "t1:src/worker_change.py"), "CHANGED = True\n")
        plan = self.plan()
        self.assertEqual(plan.get("files"), PLAN["files"], plan)
        self.assertEqual((plan.get("baseline") or {}).get("stage"), "red", plan)
        self.assertEqual(self.last_test_defect().get("disposition"), "fixed")
        self.assertTrue(self.reruns("2"), "第 2 輪沒有 gate.rerun\n" + said)

    def test_a2_a_parallel_verifier_with_an_empty_patch_lets_the_worker_patch_through(self):
        """A2 interface_fixed 第 1 輪平行驗證者交 0 byte + verify:只套 worker 那份、計畫併進票、閘門照跑。"""
        self.arm(agent(worker="case", objects=False), needs_verifier=True, interface_fixed=True)
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertIn(SAME, done.stdout, said)
        self.assertNotIn(NOT_APPLIED, said)
        self.assertTrue(self.git("branch", "--list", "t1").strip(), said)
        tree = self.git("ls-tree", "-r", "--name-only", "t1").splitlines()
        self.assertIn("tests/test_thing.py", tree, said)
        self.assertIn("assertEqual(1, 1)", self.git("show", "t1:tests/test_thing.py"))
        plan = self.plan()
        self.assertEqual(plan.get("files"), PLAN["files"], plan)
        self.assertTrue(self.reruns("1"), "第 1 輪沒有 gate.rerun\n" + said)


class TheVerifierAloneMergesOnlyItsPlan(ReceiveBase):
    """A3 / A6:驗證者是唯一交件者而 patch-verify 0 byte → 不套 patch、只併計畫、在分支上重跑閘門。"""

    def assert_plan_only(self, before, done):
        said = done.stdout + done.stderr
        self.assertIn(SAME, done.stdout, said)
        self.assertIn(PLAN_ONLY, done.stdout, said)
        self.assertNotIn("套不上", said)
        self.assertEqual(self.branch_head(), before, "分支頭動了 —— 只併計畫那一輪不該有 commit")
        self.assertTrue(self.reruns("2"), "第 2 輪沒有 gate.rerun\n" + said)
        self.assertEqual(self.gate_heads(), [before], "閘門替身沒在分支頭上跑(或跑了不只一次)\n" + said)
        self.assertTrue(self.went_in_review(), "閘門替身綠了,票沒進 InReview\n" + said)

    def test_a3a_an_all_lint_round_with_an_empty_patch_merges_the_plan_and_reruns(self):
        """A3 (a) lint_round 驗證者 0 byte + verify:分支不動、verify 照既有併法併進、重跑閘門、綠 → InReview。"""
        before, done = self.lint_round(agent(vpatch="empty", vevidence="plan"))
        self.assert_plan_only(before, done)
        plan = self.plan()
        # 既有併法(#100 A9):files 聯集、既有在前;run / notes 票上空才採驗證者的。
        self.assertEqual(plan.get("files"), [MISSING_REL, CASE_FILE], plan)
        self.assertEqual(plan.get("tags"), PLAN["tags"], plan)
        self.assertEqual(plan.get("run"), PLAN["run"], plan)
        self.assertEqual(plan.get("notes"), PLAN["notes"], plan)
        self.assertEqual((plan.get("baseline") or {}).get("stage"), "red", plan)

    def test_a3b_a_test_defect_without_a_worker_patch_merges_the_plan_and_reruns(self):
        """A3 (b) worker 只交 test_defect、驗證者 0 byte + verify:分支不動、計畫併進、反駁 fixed、重跑閘門。"""
        self.arm(agent(worker="none", objects=True), in_scope=["src/app.py"])
        self.first_round()
        before = self.branch_head()
        done = self.auto_fix("--no-review")
        self.assert_plan_only(before, done)
        plan = self.plan()
        self.assertEqual(plan.get("files"), PLAN["files"], plan)
        self.assertEqual((plan.get("baseline") or {}).get("stage"), "red", plan)
        self.assertEqual(self.last_test_defect().get("disposition"), "fixed")

    def test_a6_a_plan_only_round_still_counts_as_a_round(self):
        """A6 只併計畫那一輪照樣算一輪:第 2 輪的 ticket.attempt.start 與 ticket.attempt.done 都在。"""
        _, done = self.lint_round(agent(vpatch="empty", vevidence="plan"))
        said = done.stdout + done.stderr
        self.assertIn(PLAN_ONLY, done.stdout, said)
        self.assertTrue(self.attempts("ticket.attempt.start", "2"), said)
        self.assertTrue(self.attempts("ticket.attempt.done", "2"), said)


class NothingHandedInStillStops(ReceiveBase):
    """A5:只認 0 byte 且 result 帶 verify;其餘照舊停。"""

    def assert_empty_page(self, before, done):
        said = done.stdout + done.stderr
        self.assertNotIn(PLAN_ONLY, done.stdout, said)
        self.assertTrue([note for note in self.decisions() if EMPTY_PAGE in note],
                        "沒有「%s」那一頁:%r\n%s" % (EMPTY_PAGE, self.decisions(), said))
        self.assertEqual(self.load_ticket("1")["state"], "Blocked", said)
        self.assertEqual(self.reruns("2"), [], "什麼都沒交卻跑了閘門\n" + said)
        self.assertEqual(self.branch_head(), before)

    def test_a5_an_empty_patch_without_a_plan_still_blocks(self):
        """A5 lint_round 0 byte、result 沒有 verify → 照舊「驗證者交了空的 patch-verify」、Blocked、不跑閘門。"""
        before, done = self.lint_round(agent(vpatch="empty", vevidence="noplan"))
        self.assert_empty_page(before, done)

    def test_a5_an_empty_patch_with_an_unparsable_result_still_blocks(self):
        """A5 lint_round 0 byte、EVIDENCE 沒有 result 塊 → 照舊「驗證者交了空的 patch-verify」、Blocked、不跑閘門。"""
        before, done = self.lint_round(agent(vpatch="empty", vevidence="prose"))
        self.assert_empty_page(before, done)

    def test_a5_a_one_line_patch_verify_is_still_handed_to_apply(self):
        """A5 只認 0 byte:patch-verify 一行文字(沒有檔頭)→ 照舊「套不上(apply rc=3)」,沒有 SAME。"""
        _, done = self.lint_round(agent(vpatch="text", vevidence="plan"))
        said = done.stdout + done.stderr
        self.assertIn(NOT_APPLIED, said)
        self.assertNotIn(SAME, done.stdout, said)
        self.assertNotIn(PLAN_ONLY, done.stdout, said)

    def test_a5_an_empty_worker_patch_is_still_not_green(self):
        """A5 worker 自己的 patch 0 byte → 照舊「空的 patch 不是綠」,不走只併計畫。"""
        self.arm(agent(worker="empty", objects=False), needs_verifier=False)
        self.first_round()
        done = self.auto_fix("--no-review")
        said = done.stdout + done.stderr
        self.assertIn(EMPTY_GUARD, said)
        self.assertNotIn(SAME, done.stdout, said)
        self.assertEqual(self.reruns("2"), [], said)


EVIDENCE_PLAN = "# verifier\n\n## result\n\n```result\n%s\n```\n"


class TheVerifyPlanSubcommand(ApplyBase):
    """A4:`apply.sh verify-plan <票號> --evidence-verifier <檔>` 只併計畫,不建分支、不 commit。"""

    def evidence(self, stage="red", block=True):
        if not block:
            return self.patch_file("EV.md", "# verifier\n\n驗紅做了,沒有那一塊。\n")
        result = {"ticket": "1", "role": "verifier", "round": 1, "rc": 0, "verify": PLAN,
                  "baseline": dict(RED_RECORD, stage=stage)}
        return self.patch_file("EV.md", EVIDENCE_PLAN % json.dumps(result, ensure_ascii=False))

    def world(self):
        return (self.git("branch", "--list").splitlines(), self.git("rev-parse", "HEAD").strip())

    def verify_plan(self, path):
        return self.apply(SUBCOMMAND, "1", "--evidence-verifier", path)

    def test_a4_verify_plan_merges_the_plan_and_touches_no_branch(self):
        """A4 stage=red 的 result:rc 0、票 verify 四格與 baseline 併進;分支清單與 HEAD 不變、沒有 t1。"""
        self.make("1")
        before = self.world()
        done = self.verify_plan(self.evidence())
        said = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, said)
        plan = self.load_ticket("1").get("verify") or {}
        for key in ("files", "tags", "run", "notes"):
            self.assertEqual(plan.get(key), PLAN[key], plan)
        self.assertEqual((plan.get("baseline") or {}).get("stage"), "red", plan)
        self.assertEqual(self.world(), before, said)
        self.assertFalse(self.branch_exists("t1"), said)
        self.assertFalse(os.path.exists(self.wt()), said)

    def test_a4_a_check_baseline_is_not_merged(self):
        """A4 stage=check 的 baseline 照既有規則不收:印「不是 stage=red」,票上沒有 baseline。"""
        self.make("1")
        done = self.verify_plan(self.evidence(stage="check"))
        said = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, said)
        self.assertIn(NOT_RED, said)
        plan = self.load_ticket("1").get("verify") or {}
        self.assertEqual(plan.get("files"), PLAN["files"], plan)
        self.assertNotIn("baseline", plan)

    def test_a4_no_result_block_says_so_and_fails(self):
        """A4 沒有 result 塊:rc 非零、stderr 有「驗證者的 result 解析不到」、票的 verify 沒動。"""
        self.make("1")
        before = self.load_ticket("1").get("verify")
        done = self.verify_plan(self.evidence(block=False))
        said = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, said)
        self.assertIn(UNPARSED, done.stderr, said)
        self.assertEqual(self.load_ticket("1").get("verify"), before)

    def test_a4_help_lists_the_subcommand(self):
        """A4 `--help` 列出 verify-plan。"""
        done = self.apply("--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(SUBCOMMAND, done.stdout)


class TheWorkflowDocSaysSo(unittest.TestCase):
    """A6:docs/WORKFLOW.md 補一句。"""

    def test_a6_workflow_says_an_empty_patch_verify_merges_only_the_plan(self):
        """A6 有一段同時寫 patch-verify、0 byte、計畫與閘門(0 byte = 這輪沒改案例,有計畫就只併計畫重跑閘門)。"""
        with open(os.path.join(ROOT, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            paragraphs = handle.read().split("\n\n")
        hits = [text for text in paragraphs if "patch-verify" in text and "0 byte" in text
                and "計畫" in text and "閘門" in text]
        self.assertTrue(hits, "docs/WORKFLOW.md 沒有寫 patch-verify 0 byte 只併計畫重跑閘門")


if __name__ == "__main__":
    unittest.main()
