"""#109 批次落地 ②:land.sh N≥2 張全套紅 → auto-fix --batch 修 rounds 輪 → 仍紅才按紅榜踢嫌疑票、其餘重 land 一次。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / 夾具自己寫的票檔、替身、紅榜 / git 自己算)
B1 | unit | land t1-a t2-b;閘門替身 第 1 次紅、第 2 次綠;auto-fix 替身 rc 0、在整合分支疊一個 commit | rc 0;main 含替身 commit;1.json 2.json state=Done;stdout 無「不猜是誰紅的」;auto-fix 恰 1 次、參數 = --batch land/<land.start 的 stamp> 1 2;AC_BATCH_RUN_ID = reports/t1 那一輪 kind=land 的 run_id | 票面 B1 原話;stamp 取自 land.start 事件 kv
B2 | unit | 閘門替身一律紅、紅榜 file 只命中票 2(verify.files / 分支改動檔 兩種各一) | auto-fix --batch 恰 rounds 次(預設 2;config rounds=1 時 1 次);閘門(第一層)恰 rounds+1 次;收件匣 ticket=2 一頁含「踢出」「單張 land」與紅榜 case 名;events land.fail kicked=2;stdout 含「重 land」;第二次 land.start 只剩 t1-a、它的閘門 env AC_LAND_DEPTH=1;rc 1 | 票面 B2 原話;3 張票時重 land 的紅榜改指票 2 → 不再踢(只踢過 3)
B3 | unit | 紅榜 file=src/zzz(對不到)或 src/a(票 1 與票 2 都中) | rc 1;沒有 kicked 事件、沒有「踢出」頁;land.start 只一筆;land worktree 還在;decision 只掛 #1 一頁且列得出 #2 | 票面 B3 原話
B4 | unit | --no-auto-fix 兩張;單張一批 | 前者 auto-fix 0 次、閘門 1 次、rc 1、有 decision;後者 auto-fix 恰 1 次參數 = 1(沒有 --batch)、閘門 1 次、rc 1;tests/test_land.py 既有三條還在 | 票面 B4 原話
B5 | unit | 真的 gate.sh --full 紅,AC_GATE_TICKETS="1 2" / AC_GATE_TICKET=1 | 前者 reports/t1 與 t2 各一份 kind=gate、run_id 都是給的那個、failures 相同且有那條紅;後者只有 t1 | 票面 B5 原話
B6 | unit | 同 B1 的世界 | gate.start×2 / gate.fail round=1 / gate.pass round=2,都帶 mode=full batch=<stamp>;kind=land 的 phases 裡 gate 兩筆、note 含「第 1 輪」「第 2 輪」;auto-fix 與兩次閘門跑的時候 .land.lock 都在;修復中另起 land.sh docs → rc 2「已經有一個 land 在跑」 | 票面 B6 原話;等鎖的字串取自既有案例 test_it_waits_behind_the_same_lock_as_a_ticket_landing
B7 | unit | auto-fix 替身 rc 5 | 閘門只 1 次;rc 1;「批次修復停了」恰一頁、掛 #1 | 票面 B7 原話
B8 | doc  | 讀 docs/WORKFLOW.md | §落地 提到 batch_fix.rounds 與踢;第 11 條不再寫「剛好一張票」;能力表沒有「一批多張票…未實作」那一列 | 票面 B8 原話

## 介面字串(全部出自票面 acceptance / outline,不從腳本抄)
AC_BATCH_RUN_ID / AC_GATE_TICKETS / AC_LAND_DEPTH  ← env 名(interface_fixed)
batch_fix.rounds(預設 2)                         ← board/config.json 鍵
"--batch land/<stamp> <id…>"                      ← auto-fix 參數形狀(#108)
KICKED = "踢出"、SINGLE = "單張 land"、RELAND = "重 land"、STOPPED = "批次修復停了"、OLD = "不猜是誰紅的"
events: land.fail kicked=<id>;gate.start/pass/fail mode=full round=<r> batch=<stamp>;phase note 「第 r 輪」

## 怎麼做假(不上真埠、不起真服務、不起真模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo + 絆線 claude)。閘門替身每叫一次把計數 +1、
照 gate-plan.json 回 rc,並像真閘門一樣把 kind=gate 的 status.json(含 failures[].file)寫給 env 裡的每張票
($AC_ROOT/reports/t<n>/$AC_GATE_RUN_ID/);auto-fix 替身把參數、AC_BATCH_RUN_ID、鎖在不在印進 AC_TEST_LOG,
--batch 時在 $AC_FIX_SOURCE(沒給就用第二個參數)那條分支的 worktree 疊一個 commit。B5 用真的 gate.sh。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不跑真的模型。
"""
import json
import os
import re
import sys
import unittest

TAGS = ["batch-land"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import Sandbox, write_executable  # noqa: E402

KICKED = "踢出"
SINGLE = "單張 land"
RELAND = "重 land"
STOPPED = "批次修復停了"
OLD = "不猜是誰紅的"
RED_CASE = "tests.test_zz_red.T.test_it_is_red"
FIX_SUBJECT = "替身的批次修復"

# 計數檔 + 計畫檔:第 n 次照 rcs[n-1](超出就用最後一格),紅榜照 files[n-1]。
GATE_STUB_COUNTING = r'''#!/bin/sh
here=$(dirname "$AC_TEST_LOG")
n=$(cat "$here/gate-count" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$here/gate-count"
root=${AC_ROOT:-$(pwd)}
lock=no
[ -d "$root/.land.lock" ] && lock=yes
echo "gate $* call=$n depth=${AC_LAND_DEPTH:-} tickets=${AC_GATE_TICKETS:-} ticket=${AC_GATE_TICKET:-} lock=$lock" >> "$AC_TEST_LOG"
python3 - "$here/gate-plan.json" "$n" "$root" <<'PY'
import json, os, sys
plan_path, n, root = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(plan_path, encoding="utf-8") as handle:
    plan = json.load(handle)
pick = lambda rows: rows[min(n, len(rows)) - 1]
rc = pick(plan["rcs"])
files = pick(plan["files"]) if rc else []
tickets = (os.environ.get("AC_GATE_TICKETS") or os.environ.get("AC_GATE_TICKET") or "").split()
run_id = os.environ.get("AC_GATE_RUN_ID") or "stub-gate-%d" % n
failures = [{"case": plan["case"], "kind": "fail", "subtest": "", "file": name, "line": 1,
             "engine": "", "log": "gate.log", "excerpt": "AssertionError: 假的紅",
             "suspected_flaky": False} for name in files]
for ident in tickets:
    where = os.path.join(root, "reports", "t%s" % ident, run_id)
    os.makedirs(where, exist_ok=True)
    with open(os.path.join(where, "status.json"), "w", encoding="utf-8") as handle:
        json.dump({"state": "done", "run_id": run_id, "kind": "gate", "ticket": ident,
                   "sha": "", "started": "2026-10-11T10:00:00+08:00",
                   "finished": "2026-10-11T10:00:01+08:00", "rc": rc, "note": "替身",
                   "report": "", "logs": [], "kept_logs": [], "phases": [],
                   "failures": failures, "suspected_flaky": [],
                   "environment_suspect": []}, handle, ensure_ascii=False)
if rc:
    print("FAILED (假的紅)")
sys.exit(rc)
PY
'''

AUTO_FIX_STUB = r'''#!/bin/sh
here=$(dirname "$AC_TEST_LOG")
root=$(cd "$(dirname "$0")/.." && pwd)
lock=no
[ -d "$root/.land.lock" ] && lock=yes
echo "autofix $* |batch_run=${AC_BATCH_RUN_ID:-} lock=$lock" >> "$AC_TEST_LOG"
if [ -f "$here/docs-probe" ]; then
    sh "$root/scripts/land.sh" docs "擠進去" tickets/7.json > "$here/docs-probe.out" 2>&1
    echo "docs-probe rc=$?" >> "$AC_TEST_LOG"
fi
if [ "${1:-}" = "--batch" ]; then
    br=${AC_FIX_SOURCE:-$2}
    wt=$(git -C "$root" worktree list --porcelain | awk -v want="refs/heads/$br" \
        '/^worktree /{p=substr($0,10)} $0=="branch "want{print p}')
    k=$(grep -c "^autofix --batch" "$AC_TEST_LOG")
    echo "fix $k" > "$wt/src/fix-$k"
    git -C "$wt" add -A
    git -C "$wt" commit -q -m "替身的批次修復 $k"
fi
exit $(cat "$here/autofix-rc" 2>/dev/null || echo 0)
'''


class BatchLandWorld(Sandbox):
    gate_stub = GATE_STUB_COUNTING

    def setUp(self):
        super().setUp()
        write_executable(os.path.join(self.repo, "scripts", "auto-fix.sh"), AUTO_FIX_STUB)

    # ------------------------------------------------------------ 夾具

    def plan(self, rcs, files=(("src/b",),)):
        with open(os.path.join(self.home, "gate-plan.json"), "w", encoding="utf-8") as handle:
            json.dump({"rcs": list(rcs), "files": [list(row) for row in files],
                       "case": RED_CASE}, handle)

    def autofix_rc(self, rc):
        with open(os.path.join(self.home, "autofix-rc"), "w") as handle:
            handle.write("%d\n" % rc)

    def branch_for(self, ident, name, **fields):
        self.make_ticket(ident, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #%s" % ident)
        self.git("push", "-q", "origin", "main")
        return self.worktree(name)

    def two_tickets(self, first=None, second=None, second_extra=None):
        """票 1 寫 src/a、票 2 寫 src/b;第二支可以多改一個檔。"""
        one = self.branch_for(1, "t1-a", **(first or {}))
        self.commit_in(one, "src/a", "第一張")
        two = self.branch_for(2, "t2-b", **(second or {}))
        self.commit_in(two, "src/b", "第二張")
        if second_extra:
            self.commit_in(two, second_extra, "第二張也動了 %s" % second_extra)
        self.approve(1, "t1-a")
        self.approve(2, "t2-b")

    def land(self, *args):
        return self.run_sh("scripts/land.sh", *args)

    # ------------------------------------------------------------ 讀回

    def lines(self, prefix):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.startswith(prefix)]

    def autofix_args(self):
        return [line[len("autofix "):].split(" |batch_run=")[0].strip()
                for line in self.lines("autofix ")]

    def gate_calls(self, depth=None):
        rows = self.lines("gate ")
        if depth is None:
            return rows
        return [row for row in rows if (" depth=%s " % depth) in row]

    def ev(self, kind):
        return [row for row in self.events() if row.get("kind") == kind]

    def pages(self, ident=None):
        out = []
        for row in self.inbox_rows(ident):
            if row.get("kind") != "decision":
                continue
            with open(os.path.join(self.repo, row["page"]), encoding="utf-8") as fh:
                row["text"] = fh.read()
            out.append(row)
        return out

    def kicked_values(self):
        return [str(row.get("kicked")) for row in self.ev("land.fail") if row.get("kicked")]

    def land_branches(self):
        """每一筆 land.start 要落地的分支(note 是 land 的參數,去掉旗標)。"""
        return [[word for word in (row.get("note") or "").split() if not word.startswith("-")]
                for row in self.ev("land.start")]

    def land_worktrees(self):
        out = self.git("worktree", "list", "--porcelain")
        return [line for line in out.splitlines()
                if line.startswith("branch refs/heads/land/")]


# ============================================================== B1 / B6


class B1RedThenBatchFixThenGreen(BatchLandWorld):

    def setUp(self):
        super().setUp()
        closable = lambda: {
            "verify": {"files": [], "tags": [], "run": "", "notes": "",
                       "baseline": {"ok": True, "stage": "check"}}}
        self.two_tickets(first=closable(), second=closable())
        self.plan([1, 0])

    def test_b1_one_batch_fix_round_turns_it_green_and_both_tickets_close(self):
        """B1 紅 → auto-fix --batch 一次 → 重跑全套綠 → main 含修復 commit、兩張 Done、參數與 run_id 逐字。"""
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertNotIn(OLD, done.stdout)
        self.assertIn(FIX_SUBJECT, self.git("log", "--format=%s", "main"),
                      "main 要前進到含 auto-fix 疊的那個 commit 的頭")
        self.assertEqual(self.load_ticket(1)["state"], "Done", out)
        self.assertEqual(self.load_ticket(2)["state"], "Done", out)
        stamp = self.ev("land.start")[0]["stamp"]
        self.assertEqual(self.autofix_args(), ["--batch land/%s 1 2" % stamp], out)
        batch_run = self.lines("autofix ")[0].split("|batch_run=")[1].split()[0]
        self.assertEqual(batch_run, self.status_of(1, kind="land")["run_id"],
                         "AC_BATCH_RUN_ID 要等於這次 land 的 run_id")
        self.assertEqual(len(self.gate_calls()), 2, out)

    def test_b6_each_round_has_its_own_events_and_phase(self):
        """B6 gate.start/pass/fail 帶 mode=full round=<r> batch=<stamp>;phase gate 每輪一筆、note 含第 r 輪。"""
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        stamp = self.ev("land.start")[0]["stamp"]
        rows = [(row["kind"], row.get("mode"), row.get("round"), row.get("batch"))
                for row in self.events()
                if row.get("kind") in ("gate.start", "gate.pass", "gate.fail")]
        self.assertEqual(rows, [("gate.start", "full", "1", stamp),
                                ("gate.fail", "full", "1", stamp),
                                ("gate.start", "full", "2", stamp),
                                ("gate.pass", "full", "2", stamp)], out)
        for ident in (1, 2):
            gates = [row for row in self.status_of(ident, kind="land")["phases"]
                     if row.get("phase") == "gate"]
            self.assertEqual(len(gates), 2, gates)
            self.assertIn("第 1 輪", gates[0]["note"])
            self.assertIn("第 2 輪", gates[1]["note"])

    def test_b6_the_lock_is_held_through_the_fix_and_docs_waits(self):
        """B6 .land.lock 整段不放:auto-fix 與兩次閘門跑時鎖都在;修復中另起 land.sh docs → 等鎖。"""
        self.make_ticket(7)
        with open(os.path.join(self.home, "docs-probe"), "w") as handle:
            handle.write("on\n")
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertEqual(len(self.lines("autofix ")), 1, out)
        self.assertIn("lock=yes", self.lines("autofix ")[0])
        self.assertEqual(len(self.gate_calls()), 2, out)
        for row in self.gate_calls():
            self.assertIn("lock=yes", row)
        self.assertEqual(self.lines("docs-probe "), ["docs-probe rc=2"])
        with open(os.path.join(self.home, "docs-probe.out"), encoding="utf-8") as handle:
            self.assertIn("已經有一個 land 在跑", handle.read())


# ============================================================== B2


class B2KickTheSuspectAndRelandTheRest(BatchLandWorld):

    def assert_ticket_two_kicked(self, done):
        out = done.stdout + done.stderr
        self.assertEqual(self.kicked_values(), ["2"], out)
        kicks = [row for row in self.pages(2) if KICKED in row["text"]]
        self.assertEqual(len(kicks), 1, out)
        self.assertIn(SINGLE, kicks[0]["text"])
        self.assertIn(RED_CASE, kicks[0]["text"])
        self.assertIn(RELAND, done.stdout)
        self.assertEqual(self.land_branches()[1:2], [["t1-a"]], out)

    def test_b2_red_rounds_plus_one_times_kicks_ticket_two_by_allowed_paths(self):
        """B2 連紅 3 次(rounds 預設 2)→ 不再派;紅榜 file 命中票 2 的分支改動檔 → 踢 2、重 land t1-a(AC_LAND_DEPTH=1)、rc 1。"""
        self.two_tickets()
        self.plan([1], files=[["src/b"]])
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        batch = [args for args in self.autofix_args() if args.startswith("--batch")]
        self.assertEqual(len(batch), 2, out)
        self.assertEqual(len(self.gate_calls(depth="")), 3, out)
        self.assert_ticket_two_kicked(done)
        relanded = self.gate_calls(depth="1")
        self.assertTrue(relanded, "重 land 的閘門要看得到 AC_LAND_DEPTH=1:\n" + out)
        for row in relanded:
            hit = re.search(r"tickets=(\S*) ticket=(\S*)", row)
            self.assertNotIn("2", hit.group(1).split() + hit.group(2).split(), row)
        self.assertEqual(len(self.ev("land.start")), 2, "重 land 只一次")

    def test_b2_verify_files_alone_is_enough_to_kick(self):
        """B2 紅榜 file 只命中票 2 的 verify.files(不是分支改動檔)→ 踢 2。"""
        case = "verify/x/test_ticket_2.py"
        self.write(case, "TAGS = [\"example\"]\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "案例先在主線上")
        self.two_tickets(second={"verify": {"files": [case], "tags": ["example"],
                                            "run": "", "notes": ""}})
        self.plan([1], files=[[case]])
        done = self.land("t1-a", "t2-b")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_ticket_two_kicked(done)

    def test_b2_a_branch_changed_file_alone_is_enough_to_kick(self):
        """B2 紅榜 file 只命中票 2 分支改動檔 → 踢 2。"""
        self.two_tickets(second_extra="lib/c")
        self.plan([1], files=[["lib/c"]])
        done = self.land("t1-a", "t2-b")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_ticket_two_kicked(done)

    def test_b2_the_second_land_does_not_kick_again(self):
        """B2 重 land 不再踢:三張、先踢 3;重 land 的紅榜改指票 2 → 不踢、不再重 land、rc 1。"""
        one = self.branch_for(1, "t1-a")
        self.commit_in(one, "src/a", "第一張")
        two = self.branch_for(2, "t2-b")
        self.commit_in(two, "src/b", "第二張")
        three = self.branch_for(3, "t3-c")
        self.commit_in(three, "src/c", "第三張")
        for ident, name in ((1, "t1-a"), (2, "t2-b"), (3, "t3-c")):
            self.approve(ident, name)
        self.plan([1], files=[["src/c"], ["src/c"], ["src/c"], ["src/b"]])
        done = self.land("t1-a", "t2-b", "t3-c")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(self.kicked_values(), ["3"], out)
        self.assertEqual([row for row in self.pages(2) if KICKED in row["text"]], [], out)
        self.assertEqual(self.land_branches(), [["t1-a", "t2-b", "t3-c"], ["t1-a", "t2-b"]], out)


class B2RoundsComeFromConfig(BatchLandWorld):
    config_extra = {"batch_fix": {"rounds": 1}}

    def test_b2_batch_fix_rounds_one_means_one_fix_and_two_gates(self):
        """B2 batch_fix.rounds=1 → auto-fix --batch 1 次、第一層閘門 2 次,接著踢 2。"""
        self.two_tickets()
        self.plan([1], files=[["src/b"]])
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(len([a for a in self.autofix_args() if a.startswith("--batch")]), 1, out)
        self.assertEqual(len(self.gate_calls(depth="")), 2, out)
        self.assertEqual(self.kicked_values(), ["2"], out)


# ============================================================== B3


class B3NoClearSuspectStops(BatchLandWorld):

    def assert_stopped_without_kicking(self, done):
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(self.kicked_values(), [], out)
        self.assertEqual([row for row in self.pages() if KICKED in row["text"]], [], out)
        self.assertEqual(len(self.ev("land.start")), 1, "不重 land")
        self.assertEqual(len(self.land_worktrees()), 1, "land worktree 要留著")
        self.assertEqual(len([a for a in self.autofix_args() if a.startswith("--batch")]), 2, out)
        self.assertEqual(self.pages(2), [], "decision 只掛第一張票")
        first = self.pages(1)
        self.assertEqual(len(first), 1, out)
        self.assertTrue("#2" in first[0]["text"] or "t2-b" in first[0]["text"],
                        "那一頁要列整批:\n" + first[0]["text"])

    def test_b3_a_red_file_no_ticket_owns_stops(self):
        """B3 紅榜 file=src/zzz 對不到任何票 → 不踢、不重 land、worktree 留、一頁 decision 掛 #1、rc 1。"""
        self.two_tickets()
        self.plan([1], files=[["src/zzz"]])
        self.assert_stopped_without_kicking(self.land("t1-a", "t2-b"))

    def test_b3_a_red_file_every_ticket_owns_stops(self):
        """B3 紅榜 file=src/a 同時命中票 1(分支改動檔)與票 2(verify.files)→ 同上。"""
        self.write("src/a", "主線上先有\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "src/a 先在主線上")
        self.two_tickets(second={"verify": {"files": ["src/a"], "tags": ["example"],
                                            "run": "", "notes": ""}})
        self.plan([1], files=[["src/a"]])
        self.assert_stopped_without_kicking(self.land("t1-a", "t2-b"))


# ============================================================== B4


class B4TheOldPathsStay(BatchLandWorld):

    def test_b4_no_auto_fix_stops_at_the_first_red(self):
        """B4 --no-auto-fix → 紅就停、有 decision、auto-fix 0 次、閘門 1 次、rc 1。"""
        self.two_tickets()
        self.plan([1])
        done = self.land("t1-a", "t2-b", "--no-auto-fix")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(self.autofix_args(), [], out)
        self.assertEqual(len(self.gate_calls()), 1, out)
        self.assertTrue(self.pages(1), out)

    def test_b4_a_single_ticket_still_takes_the_single_ticket_auto_fix(self):
        """B4 一張票一批 → 走既有單票 auto-fix(參數就是票號,沒有 --batch)、閘門 1 次、rc 1。"""
        one = self.branch_for(1, "t1-a")
        self.commit_in(one, "src/a", "第一張")
        self.approve(1, "t1-a")
        self.plan([1])
        done = self.land("t1-a")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(self.autofix_args(), ["1"], out)
        self.assertEqual(len(self.gate_calls()), 1, out)

    def test_b4_the_three_existing_auto_fix_cases_are_still_there(self):
        """B4 AutoFixHook 既有三條還在(照綠是閘門 L2 的事,這裡只守沒被刪)。"""
        with open(os.path.join(ROOT, "tests", "test_land.py"), encoding="utf-8") as handle:
            text = handle.read()
        for name in ("test_a_single_ticket_dispatches_by_default_from_the_land_tree",
                     "test_no_auto_fix_leaves_a_single_red_land_for_a_human",
                     "test_an_unknown_flag_is_named"):
            self.assertIn("def %s(" % name, text)


# ============================================================== B7


class B7BatchFixStops(BatchLandWorld):

    def test_b7_a_failed_batch_fix_does_not_rerun_the_gate(self):
        """B7 auto-fix --batch rc 5 → 不重跑全套、rc 1、「批次修復停了」恰一頁掛 #1。"""
        self.two_tickets()
        self.plan([1, 0])
        self.autofix_rc(5)
        done = self.land("t1-a", "t2-b")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 1, out)
        self.assertEqual(len(self.lines("autofix --batch")), 1, out)
        self.assertEqual(len(self.gate_calls()), 1, out)
        stopped = [row for row in self.pages() if STOPPED in row["text"]]
        self.assertEqual([str(row["ticket"]) for row in stopped], ["1"], out)


# ============================================================== B5


FAILING = """import unittest


class T(unittest.TestCase):
    def test_it_is_red(self):
        self.assertEqual(1, 2, "假的紅")
"""


class B5FullGateWritesEveryTicket(Sandbox):
    gate_stub = None

    def setUp(self):
        super().setUp()
        self.write(os.path.join("tests", "test_zz_red.py"), FAILING)
        self.make_ticket(1)
        self.make_ticket(2)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "一條紅 + 兩張票")

    def full(self, **env):
        return self.run_sh("scripts/gate.sh", "--full", "--no-auto-fix",
                           env=self.env(AC_NO_INBOX="1", AC_GATE_RUN_ID="20261011-100000-7", **env))

    def has_status(self, ident):
        return os.path.isdir(os.path.join(self.repo, "reports", "t%s" % ident))

    def test_b5_gate_tickets_one_two_writes_a_gate_status_for_each(self):
        """B5 AC_GATE_TICKETS="1 2" 跑 --full 紅 → t1 與 t2 各一份 kind=gate、run_id 相同、failures 相同。"""
        done = self.full(AC_GATE_TICKETS="1 2")
        out = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, out)
        self.assertTrue(self.has_status(2), "票 2 沒有狀態檔:\n" + out)
        one, two = self.status_of(1, kind="gate"), self.status_of(2, kind="gate")
        self.assertEqual(one["run_id"], "20261011-100000-7")
        self.assertEqual(two["run_id"], "20261011-100000-7")
        key = lambda data: [(row.get("case"), row.get("file")) for row in data["failures"]]
        self.assertEqual(key(one), key(two))
        self.assertTrue(any("test_it_is_red" in (case or "") for case, _ in key(one)), key(one))

    def test_b5_a_single_gate_ticket_writes_only_that_one(self):
        """B5 AC_GATE_TICKET 單張照舊:只寫 t1。"""
        done = self.full(AC_GATE_TICKET="1")
        out = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, out)
        self.assertEqual(self.status_of(1, kind="gate")["run_id"], "20261011-100000-7")
        self.assertFalse(self.has_status(2), out)


# ============================================================== B8


class B8TheWorkflowSaysTheNewRule(unittest.TestCase):

    def test_b8_workflow_landing_section_names_rounds_and_kicking(self):
        """B8 WORKFLOW §落地 第 11 條改成新行為、寫明 batch_fix.rounds 與踢人規則;能力表不再寫未實作。"""
        with open(os.path.join(ROOT, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## 落地")
        section = text[start:text.index("\n## ", start + 1)]
        self.assertIn("batch_fix.rounds", section)
        self.assertIn("踢", section)
        item = re.search(r"^11\. .*?(?=^\S|\Z)", section, re.M | re.S)
        self.assertIsNotNone(item, "§落地 沒有第 11 條")
        self.assertNotIn("剛好一張票", item.group(0))
        rows = [line for line in text.splitlines()
                if line.startswith("|") and "一批多張票" in line and "未實作" in line]
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
