"""#121 worker、驗證者與覆核者依同一張角色表(#116 role-models)派工:選路、額度換人、耗盡停、effort、覆核終態、拆 routing 下限。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 夾具自己寫下的角色表 / test_plan)
A1 | integ | 表的 worker 首選改 fable high、verifier 首選 fable medium、reviewer 首選 fable;批次 --dry-run | worker / 驗證者 / 覆核者 argv 的 --model、--effort 等於表;worker 派工文的規則包是 fable 卡;批次 worker 命令同表 | 票面 A1「都呼叫 #116;實際 argv/model/effort、rules pack 相符」
A2 | integ | 假 codex 回 provider 錯誤信封(429 / usage limit)、表 worker = [sol, fable];假 codex 只印含 429 的純文字 | 前者同一次 auto-fix 接著起 fable(不是 opus),起跑那一刻 codex 的副本還在;後者只起 1 次、票的 model/tool 不動、沒有「改派」 | 票面 A2 + interface_contracts.quota_detect
A3 | integ | 表 worker = [sol, astra],兩個都撞額度 | codex 恰起 2 次、不起 claude;票 Blocked;收件匣恰 1 頁;副本留著 | 票面 A3「整串用完 Blocked + 一頁 decision,沒有候選則不再起程序」
A4 | integ | 同 A2 前者;額度 + 越界 | worker cost 兩列都恰 15 鍵、額度那列 outcome=error 且 note 非空;agent.start 帶 role=worker 與實際 model/effort(= argv);越界 rc 8、Blocked、不起下一候選 | 票面 A4 + tickets/SCHEMA.md 15 鍵
A5 | integ+doc | 表 worker 首選只有 fable、票不寫 model;讀 tests/test_auto_fix_codex.py 與 tickets/SCHEMA.md | 起的是 fable;舊「已改 opus,重跑 auto-fix」/「改派 opus」預期不在 | 票面 A5
A6 | integ | 第 1 輪閘門綠(complex 票,#136 起只有它派覆核),reviewer 交 fail / 不交;--no-review | 兩案 auto-fix rc≠0、fail 案票 Blocked;--no-review rc 0 且輸出含 awaiting_review | 票面 A6(交件前票被改那一案隨 #136 刪綁定一起拿掉)
A7 | integ+doc | 預設表 sol(high/xhigh) → opus(high):第一輪乾跑、complexity=complex 乾跑、第 1 輪紅後第 2 輪乾跑、額度後 opus 實跑;讀 SCHEMA | high / xhigh / xhigh / --effort high;SCHEMA 不再寫「effort 不在 claude-code 表內就設成 null」 | 票面 A7 + 表 board/role-models.json
A8 | integ+doc | config model_tiers + routing.implement=opus:rules.py pack worker --model sonnet 等;complexity=simple 的票跑 auto-fix | pack rc 0 且 stdout 非空;worker 起 sonnet medium;rules.py 沒有 below_floor、DISPATCH-TEMPLATE 沒有「低於 routing 下限」 | 票面 A8 + test_plan T4/T5

## 介面字串(每條斷言靠哪個字串;票面沒定的在這裡定,EVIDENCE 不重抄)
WORKER_LINE  = "auto-fix: worker 命令 —— "             ← 乾跑印出的 worker 命令(#87 A19 既有那一行)
CODEX_EFFORT = 'model_reasoning_effort="<e>"'          ← codex 的 effort 旗標(#114 / #115 A1)
CLAUDE flags = "--model <m>" / "--effort <e>"          ← claude-code 那一支 worker.command 換進去的(#91 C7 / #114)
QUOTA 證據   = 一行頂層 JSON {"type":"error","message":"429 … usage limit"}(role-models.py classify 認的信封)
AWAITING     = "awaiting_review"                       ← 票面 A6 原字
OLD_PAGE     = "已改 opus,重跑 auto-fix" / "改派 opus"   ← 舊 #87 A23 頁與 SCHEMA 字(A5 要移除的預期)
OLD_NULL     = "effort 不在 claude-code 表內就設成 null"  ← 舊 SCHEMA 字(A7)
OLD_FLOOR    = "below_floor"(rules.py)/「低於 routing 下限」(DISPATCH-TEMPLATE)← A8、T5

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox 經 tests/test_auto_fix.AutoFixBase(拋棄式真 git repo;PATH 最前面的 `claude`
是絆線)。沙盒另外放進 scripts/role-models.py 與一份由本檔寫下的 board/role-models.json(由真表複製再改鏈)。
claude-code 那一支 = config worker.command 指向的假 sh(驗證者沒設 verifier.command 時同一支),記 AC_ROLE 與 argv、
存 stdin;worker 交一份會綠的 patch。codex = stub-bin 的假 `codex`,記 argv,依模式交件 / 回額度信封 / 印純文字。
reviewer = config reviewer.command 指向的假 sh(交 pass / fail / 不交 / 先改票再交 pass)。外層 AC_* 由沙盒清掉。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;不跑真的模型。
A3 的「有 Controller 時只交 action 記錄、由 Controller 接續」票面沒有定 Controller 存在的訊號(#120 才整合),
這裡只驗無 Controller 的本地有限驅動;A2「CLI 已知不可用可於啟動前選下一候選」是「可」,不驗;
A1 的 test_defect 驗證者與晚起驗證者入口、A5 的 receipt/CAS 細節不另寫案例(平行驗證者與 stale 覆核那兩條代表)。
"""
import copy
import json
import os
import shlex
import sys
import unittest

TAGS = ["role-dispatch"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import DEFAULT_CONFIG, SCRIPTS, write_executable  # noqa: E402
from test_auto_fix import RED_LOG, AutoFixBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
WORKER_LINE = "auto-fix: worker 命令 —— "
AWAITING = "awaiting_review"
OLD_PAGE = "已改 opus,重跑 auto-fix"
OLD_SCHEMA_REASSIGN = "改派 opus"
OLD_NULL = "effort 不在 claude-code 表內就設成 null"
OLD_FLOOR_SYMBOL = "below_floor"
OLD_FLOOR_TEXT = "低於 routing 下限"
SOL = "codex:gpt-5.6-sol"
ASTRA = "codex:gpt-6-astra"
COST_KEYS = {"role", "round", "model", "tokens_in", "tokens_out", "cache_write", "cache_read",
             "wall_seconds", "outcome", "run_id", "session_id", "by", "note", "effort", "at"}

GREEN_PATCH = """mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# claude-code 那一支:一行一次「agent<TAB>角色<TAB>argv<TAB>起跑那一刻 codex 的半成品還在不在」。
FAKE_AGENT = """#!/bin/sh
if [ -f "@PARTIAL@" ]; then seen=partial; else seen=nopartial; fi
printf 'agent\\t%s\\t%s\\t%s\\n' "$AC_ROLE" "$*" "$seen" >> "@CALLS@"
cat > "@HOME@/stdin-$AC_ROLE-$AC_ROUND.md"
[ "$AC_ROLE" = worker ] || exit 0
cd "$AC_WORK"
""" + GREEN_PATCH

# 假 codex:@MODE@ = deliver / quota / quota-stray / plain429。額度那一行是 provider 的錯誤信封(頂層 JSON)。
FAKE_CODEX = """#!/bin/sh
printf 'codex\\t%s\\t%s\\t-\\n' "$AC_ROLE" "$*" >> "@CALLS@"
cat > /dev/null
case "@MODE@" in
    quota|quota-stray)
        mkdir -p "$AC_WORK/work/tests"
        echo "codex 做到一半" > "$AC_WORK/work/tests/codex_partial.txt"
        [ "@MODE@" != quota-stray ] || echo stray > "@REPO@/codex-stray.txt"
        echo '{"type":"error","message":"429 Too Many Requests: You have hit your usage limit."}'
        exit 1 ;;
    plain429)
        echo "codex: fixture text mentions 429 and rate limit, but it is not a provider envelope"
        exit 1 ;;
esac
cd "$AC_WORK"
""" + GREEN_PATCH

# 假 codex(A7 同程序修補輪):第 1 輪交一份會紅的案例,之後的輪次交 GREEN_PATCH。
FAKE_CODEX_RED_THEN_GREEN = """#!/bin/sh
printf 'codex\\t%s\\t%s\\t-\\n' "$AC_ROLE" "$*" >> "@CALLS@"
cat > /dev/null
cd "$AC_WORK"
if [ "$AC_ROUND" = 1 ]; then
    mkdir -p work/tests
    cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 2, "第一輪是紅的")
CASE
    diff -ruN base work > "patch-round$AC_ROUND.diff" || true
    printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
    exit 0
fi
""" + GREEN_PATCH

# 假 reviewer:@VERDICT@ = pass / fail / none(不交)/ stale(先改票面再交 pass)。
FAKE_REVIEWER = """#!/bin/sh
printf 'reviewer\\t%s\\t%s\\t-\\n' "$AC_ROLE" "$*" >> "@CALLS@"
cat > /dev/null
verdict="@VERDICT@"
[ "$verdict" != none ] || exit 0
if [ "$verdict" = stale ]; then
    env -u AC_TICKETS_DIR -u AC_CONTROL_DIR AC_ROOT="@REPO@" \\
        python3 "@REPO@/scripts/ticket.py" set 1 acceptance '["覆核途中改過的驗收"]' >/dev/null
    verdict=pass
fi
cat <<EOF
# 覆核
① verdict:$verdict
② 逐條驗收 → code 位置:沙盒的替身不讀 code

## result

\\`\\`\\`result
{"ticket": "1", "role": "reviewer", "round": 1, "verdict": "$verdict",
 "rc": null, "patch_sha256": null, "gate": null, "mutations": [],
 "objection": null, "excluded": [], "repro": null, "memory": []}
\\`\\`\\`
EOF
"""


def cand(model, tool, effort=None, escalated=None):
    return {"model": model, "tool": tool, "effort": effort, "effort_escalated": escalated,
            "dispatch_cap": 1}


def flag_after(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def codex_effort(argv):
    for tok in argv:
        if tok.startswith("model_reasoning_effort="):
            return tok.split("=", 1)[1].strip("'\"")
    return None


def launched(call):
    """一次起跑 → (model, effort):codex 讀 -m / model_reasoning_effort,claude-code 讀 --model / --effort。"""
    argv = call["argv"]
    if call["who"] == "codex":
        model = flag_after(argv, "-m")
        return ("codex:" + model if model else None), codex_effort(argv)
    return flag_after(argv, "--model"), flag_after(argv, "--effort")


class RoleTableBase(AutoFixBase):

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "calls.tsv")
        self.partial = os.path.join(self.home, "repo-wt", "fix-t1", "round1", "work", "tests",
                                    "codex_partial.txt")
        target = os.path.join(self.repo, "scripts", "role-models.py")
        if not os.path.exists(target):
            with open(os.path.join(SCRIPTS, "role-models.py"), encoding="utf-8") as src, \
                    open(target, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            os.chmod(target, 0o755)
        with open(os.path.join(ROOT, "board", "role-models.json"), encoding="utf-8") as handle:
            self.base_table = json.load(handle)
        self.table = copy.deepcopy(self.base_table)
        # 預設:worker / verifier / reviewer 都是 claude-code 的 opus,沒覆寫的案例不會走到 codex。
        self.set_table(worker=[cand("opus", "claude-code", "high")],
                       verifier=[cand("opus", "claude-code")],
                       reviewer=[cand("opus", "claude-code")])
        agent = os.path.join(self.home, "fake-agent.sh")
        write_executable(agent, FAKE_AGENT.replace("@CALLS@", self.calls)
                         .replace("@HOME@", self.home).replace("@PARTIAL@", self.partial))
        self.conf = dict(DEFAULT_CONFIG)
        self.conf["worker"] = {"command": "sh %s --model opus --permission-mode acceptEdits" % agent,
                               "timeout_seconds": 120}
        self.save_conf()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:角色表與假 agent")

    def save_conf(self):
        self.write("board/config.json", json.dumps(self.conf, ensure_ascii=False, indent=2))

    def set_table(self, **chains):
        """疊在沙盒現有那張表上:案例只換自己點名的鏈,setUp 的 claude-code 預設留著(不落到真 codex)。"""
        self.table["roles"].update(chains)
        self.write(os.path.join("board", "role-models.json"),
                   json.dumps(self.table, ensure_ascii=False, indent=2) + "\n")

    def fake_codex(self, mode="deliver"):
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX.replace("@CALLS@", self.calls).replace("@MODE@", mode)
                         .replace("@REPO@", self.repo))

    def fake_reviewer(self, verdict):
        path = os.path.join(self.home, "fake-reviewer.sh")
        write_executable(path, FAKE_REVIEWER.replace("@CALLS@", self.calls)
                         .replace("@VERDICT@", verdict).replace("@REPO@", self.repo))
        self.conf["reviewer"] = {"command": "sh %s --model fixture-model" % path,
                                 "timeout_seconds": 60}
        self.save_conf()

    def runs(self, role=None):
        if not os.path.exists(self.calls):
            return []
        out = []
        with open(self.calls, encoding="utf-8") as handle:
            for line in handle:
                who, ac_role, argv, seen = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
                if role is None or ac_role == role or (role == "reviewer" and who == "reviewer"):
                    out.append({"who": who, "role": ac_role, "argv": argv.split(), "seen": seen})
        return out

    def worker_events(self, kind="agent.start"):
        return [row for row in self.events() if row["kind"] == kind
                and row.get("role") not in ("verifier", "reviewer", "opener")
                and row.get("agent") != "auto-fix-verifier"]

    def worker_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or [] if row.get("role") == "worker"]

    def dry_worker(self, done):
        lines = [l for l in done.stdout.splitlines() if l.startswith(WORKER_LINE)]
        self.assertEqual(len(lines), 1, done.stdout + done.stderr)  # pin:乾跑恰印一行 worker 命令
        return shlex.split(lines[0][len(WORKER_LINE):])

    def says(self, done):
        return done.stdout + done.stderr

    def source(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()


class EveryEntryAsksTheTable(RoleTableBase):

    def test_a1_first_round_worker_runs_the_table_head_with_its_pack(self):
        """A1 第一輪 worker:表首選 fable high → argv --model fable --effort high、規則包是 fable 卡、事件與 cost 同值。"""
        self.set_table(worker=[cand("fable", "claude-code", "high")])
        self.ticket_ready(model="", tool="claude-code")
        done = self.auto_fix("--no-review")
        workers = self.runs("worker")
        self.assertEqual(len(workers), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertEqual(launched(workers[0]), ("fable", "high"), workers[0]["argv"])
        stdin = os.path.join(self.home, "stdin-worker-1.md")
        self.assertTrue(os.path.exists(stdin), "worker 沒拿到派工文")
        with open(stdin, encoding="utf-8") as handle:
            self.assertIn("memory/model/fable.md", handle.read(), "規則包要照實際模型切")
        self.assertEqual([(row.get("model"), row.get("effort")) for row in self.worker_events()],
                         [("fable", "high")])
        self.assertEqual([(row.get("model"), row.get("effort")) for row in self.worker_cost()],
                         [("fable", "high")])

    def test_a1_the_parallel_verifier_runs_the_verifier_chain(self):
        """A1 平行驗證者:表 verifier 首選 fable medium → 驗證者 argv、規則包、agent.start 都是 fable。"""
        self.set_table(verifier=[cand("fable", "claude-code", "medium")])
        self.ticket_ready(needs_verifier=True, interface_fixed=True)
        done = self.auto_fix("--no-review")
        verifiers = self.runs("verifier")
        self.assertEqual(len(verifiers), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertEqual(launched(verifiers[0]), ("fable", "medium"), verifiers[0]["argv"])
        stdin = os.path.join(self.home, "stdin-verifier-1.md")
        self.assertTrue(os.path.exists(stdin), "驗證者沒拿到派工文")
        with open(stdin, encoding="utf-8") as handle:
            self.assertIn("memory/model/fable.md", handle.read())
        starts = [row for row in self.events()
                  if row["kind"] == "agent.start" and row.get("role") == "verifier"]
        self.assertEqual([row.get("model") for row in starts], ["fable"])

    def test_a1_the_reviewer_runs_the_reviewer_chain(self):
        """A1 review 入口:表 reviewer 首選 fable → reviewer argv 的 --model 是 fable(不是 config 的 fixture-model)。"""
        self.set_table(reviewer=[cand("fable", "claude-code")])
        self.fake_reviewer("pass")
        self.ticket_ready(complexity="complex")   # 只有 complex 的票派覆核(#136)
        done = self.auto_fix()
        reviewers = self.runs("reviewer")
        self.assertEqual(len(reviewers), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertEqual(flag_after(reviewers[0]["argv"], "--model"), "fable", reviewers[0]["argv"])
        starts = [row for row in self.events()
                  if row["kind"] == "agent.start" and row.get("role") == "reviewer"]
        self.assertEqual([row.get("model") for row in starts], ["fable"])

    def test_a1_the_batch_worker_command_comes_from_the_table(self):
        """A1 批次 worker:表 worker 首選 fable high → `--batch --dry-run` 的 worker 命令 --model fable --effort high。"""
        self.set_table(worker=[cand("fable", "claude-code", "high")])
        batch = self.make_batch()
        done = self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2", "--dry-run",
                           env=self.env(AC_BATCH_RUN_ID=batch["run_id"]))
        argv = self.dry_worker(done)
        self.assertEqual((flag_after(argv, "--model"), flag_after(argv, "--effort")),
                         ("fable", "high"), argv)


class QuotaMovesToTheNextCandidate(RoleTableBase):

    def quota_then(self, second):
        self.set_table(worker=[cand(SOL, "codex", "high", "xhigh"), second])
        self.fake_codex("quota")
        self.ticket_ready(model=SOL, tool="codex")
        return self.auto_fix("--no-review")

    def test_a2_quota_keeps_the_copy_and_starts_the_next_candidate_from_the_table(self):
        """A2 codex 額度信封 → 同一次接著起表上的下一個(fable,不是 opus),起跑那一刻 codex 的副本還在。"""
        done = self.quota_then(cand("fable", "claude-code", "high"))
        runs = self.runs("worker")
        self.assertEqual([run["who"] for run in runs], ["codex", "agent"], self.says(done))
        self.assertEqual(launched(runs[1])[0], "fable", runs[1]["argv"])
        self.assertEqual(runs[1]["seen"], "partial", "換人前把 codex 的副本刪了")

    def test_a2_plain_text_429_without_an_envelope_does_not_switch(self):
        """A2 普通失敗(log 只有純文字 429,沒有 provider 信封)→ 不換模型:只起 1 次、票的 model/tool 不動。"""
        self.set_table(worker=[cand(SOL, "codex", "high"), cand("opus", "claude-code", "high")])
        self.fake_codex("plain429")
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        self.assertEqual([run["who"] for run in self.runs("worker")], ["codex"], self.says(done))
        ticket = self.load_ticket("1")
        self.assertEqual((ticket.get("model"), ticket.get("tool")), (SOL, "codex"), self.says(done))
        self.assertNotIn("改派", done.stdout)


class TheChainRunsOut(RoleTableBase):

    def test_a3_every_candidate_on_quota_blocks_with_one_page_and_no_more_starts(self):
        """A3 整串用完 → codex 恰起 2 次、不起 claude-code;票 Blocked;收件匣恰 1 頁;副本留著。"""
        self.set_table(worker=[cand(SOL, "codex", "high"), cand(ASTRA, "codex", "high")])
        self.fake_codex("quota")
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        runs = self.runs()
        self.assertEqual([(run["who"], launched(run)[0]) for run in runs],
                         [("codex", SOL), ("codex", ASTRA)], self.says(done))
        self.assertNotEqual(done.returncode, 0, self.says(done))
        self.assertEqual(self.load_ticket("1").get("state"), "Blocked", self.says(done))
        self.assertEqual(len(self.inbox_rows()), 1, self.inbox_rows())  # pin:A3 恰一頁 decision
        self.assertTrue(os.path.exists(self.partial), "整串用完也要留副本與交接資料")


class EveryRunCostsARow(RoleTableBase):

    def test_a4_both_runs_cost_fifteen_keys_and_events_match_argv(self):
        """A4 額度那次與接手那次各一列 15 鍵;額度列 outcome=error、note 記原因;agent.start 帶 role/model/effort = argv。"""
        self.set_table(worker=[cand(SOL, "codex", "high"), cand("fable", "claude-code", "high")])
        self.fake_codex("quota")
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        runs = self.runs("worker")
        self.assertEqual(len(runs), 2, self.says(done))  # pin:A4 恰兩次執行
        rows = self.worker_cost()
        self.assertEqual(len(rows), 2, rows)  # pin:A4 一次執行一列
        for row in rows:
            self.assertEqual(set(row), COST_KEYS, row)
        self.assertEqual((rows[0]["model"], rows[0]["outcome"], rows[0]["effort"]),
                         (SOL, "error", "high"), rows[0])
        self.assertTrue(rows[0]["note"], "額度那一列的 note 要記原因")
        self.assertEqual((rows[1]["model"], rows[1]["effort"]), ("fable", "high"), rows[1])
        starts = self.worker_events()
        self.assertEqual([(row.get("role"), row.get("model"), row.get("effort")) for row in starts],
                         [("worker",) + launched(run) for run in runs])

    def test_a4_a_violation_with_quota_still_blocks_before_any_next_candidate(self):
        """A4 越界仍依 #115 優先:額度信封 + 寫主 repo → rc 8、票 Blocked、不起下一候選。"""
        self.set_table(worker=[cand(SOL, "codex", "high"), cand("fable", "claude-code", "high")])
        self.fake_codex("quota-stray")
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 8, self.says(done))
        self.assertEqual(self.load_ticket("1").get("state"), "Blocked")
        self.assertEqual([run["who"] for run in self.runs("worker")], ["codex"])


class TheOldOpusExpectationIsGone(RoleTableBase):

    def test_a5_a_fable_first_table_actually_starts_fable(self):
        """A5 新表首選改 fable、票不寫 model → 實際起 fable,cost 記 fable。"""
        self.set_table(worker=[cand("fable", "claude-code", "high")])
        self.ticket_ready(model="", tool="claude-code")
        done = self.auto_fix("--no-review")
        workers = self.runs("worker")
        self.assertEqual([launched(run)[0] for run in workers], ["fable"], self.says(done))
        self.assertEqual([row.get("model") for row in self.worker_cost()], ["fable"])

    def test_a5_docs_and_tests_drop_the_fixed_opus_page(self):
        """A5 測試與 SCHEMA 不再預期「quota 固定改 opus 然後只發頁」。"""
        self.assertNotIn(OLD_PAGE, self.source(os.path.join("tests", "test_auto_fix_codex.py")))
        self.assertNotIn(OLD_SCHEMA_REASSIGN, self.source(os.path.join("tickets", "SCHEMA.md")))


class ReviewDecidesTheFinalState(RoleTableBase):

    def green_then_review(self, verdict):
        self.fake_reviewer(verdict)
        self.ticket_ready(complexity="complex")   # 只有 complex 的票派覆核(#136)
        return self.auto_fix()

    def test_a6_gate_green_review_fail_is_not_success(self):
        """A6 gate 綠、review=fail → auto-fix rc≠0,票 Blocked。"""
        done = self.green_then_review("fail")
        self.assertEqual(len(self.runs("reviewer")), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertNotEqual(done.returncode, 0, self.says(done))
        self.assertEqual(self.load_ticket("1").get("state"), "Blocked")

    def test_a6_gate_green_review_missing_is_not_success(self):
        """A6 gate 綠、review 沒交(missing)→ auto-fix rc≠0。"""
        done = self.green_then_review("none")
        self.assertEqual(len(self.runs("reviewer")), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertNotEqual(done.returncode, 0, self.says(done))

    def test_a6_no_review_returns_zero_but_says_awaiting_review(self):
        """A6 --no-review → rc 0,但明示 awaiting_review(complex 票;simple 票綠了不等覆核,#136)。"""
        self.ticket_ready(complexity="complex")
        done = self.auto_fix("--no-review")
        self.assertEqual(len(self.runs("worker")), 1, self.says(done))  # pin:一次派工恰起一次,多起即重派
        self.assertEqual(done.returncode, 0, self.says(done))
        self.assertIn(AWAITING, self.says(done))


class EffortFollowsTheTable(RoleTableBase):

    def default_worker_chain(self):
        self.set_table(worker=copy.deepcopy(self.base_table["roles"]["worker"]))
        self.fake_codex()

    def test_a7_first_round_sol_is_high_not_xhigh(self):
        """A7 第一輪 sol → codex 命令 effort high(不是 xhigh、不是沒有)。"""
        self.default_worker_chain()
        self.make_ticket("1", model=SOL, tool="codex")
        done = self.auto_fix("--dry-run")
        self.assertEqual(codex_effort(self.dry_worker(done)), "high", self.says(done))

    def test_a7_a_complex_ticket_escalates_to_xhigh(self):
        """A7 票 complexity=complex → 第一輪就 xhigh。"""
        self.default_worker_chain()
        self.make_ticket("1", model=SOL, tool="codex",
                         complexity="complex")
        done = self.auto_fix("--dry-run")
        self.assertEqual(codex_effort(self.dry_worker(done)), "xhigh", self.says(done))

    def test_a7_round_two_after_a_red_round_escalates_to_xhigh(self):
        """A7 修補第 2 輪、上一輪紅 → 沿用 sol、effort 升 xhigh。"""
        self.default_worker_chain()
        self.ticket_ready(model=SOL, tool="codex")
        self.status(1, RED_LOG)
        done = self.auto_fix("--dry-run")
        argv = self.dry_worker(done)
        self.assertEqual((flag_after(argv, "-m"), codex_effort(argv)), ("gpt-5.6-sol", "xhigh"),
                         self.says(done))

    def test_a7_the_in_process_repair_round_after_a_red_round_escalates_to_xhigh(self):
        """A7 同一次 auto-fix:第 1 輪 sol 交紅、迴圈自己接第 2 輪 → 第 2 輪沿用 sol、effort 升 xhigh(argv 與 cost[])。"""
        self.set_table(worker=copy.deepcopy(self.base_table["roles"]["worker"]))
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX_RED_THEN_GREEN.replace("@CALLS@", self.calls))
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        runs = self.runs("worker")
        self.assertEqual([run["who"] for run in runs], ["codex", "codex"], self.says(done))
        self.assertEqual([launched(run) for run in runs], [(SOL, "high"), (SOL, "xhigh")],
                         self.says(done))
        self.assertEqual([(row.get("round"), row.get("effort")) for row in self.worker_cost()],
                         [(1, "high"), (2, "xhigh")], self.says(done))
        self.assertEqual(done.returncode, 0, self.says(done))

    def test_a7_quota_to_opus_carries_effort_high(self):
        """A7 quota 改派 opus → opus 那一次命令含 --effort high(表的值,不是 null)。"""
        self.set_table(worker=copy.deepcopy(self.base_table["roles"]["worker"]))
        self.fake_codex("quota")
        self.ticket_ready(model=SOL, tool="codex")
        done = self.auto_fix("--no-review")
        runs = self.runs("worker")
        self.assertEqual([run["who"] for run in runs], ["codex", "agent"], self.says(done))
        self.assertEqual(launched(runs[1]), ("opus", "high"), runs[1]["argv"])

    def test_a7_schema_drops_the_null_effort_on_reassignment(self):
        """A7 SCHEMA 舊「codex 改派 claude-code 時 effort 設 null」文字已改。"""
        self.assertNotIn(OLD_NULL, self.source(os.path.join("tickets", "SCHEMA.md")))


class TheRoutingFloorIsGone(RoleTableBase):

    def floor_config(self):
        self.conf["model_tiers"] = ["haiku", "sonnet", "opus", "fable"]
        self.conf["routing"] = {"implement": "opus", "verify": "opus", "open": "fable",
                                "main": "opus", "design": "fable"}
        self.save_conf()

    def test_a8_rules_pack_no_longer_refuses_a_model_below_routing(self):
        """A8 config 有 model_tiers、routing.implement=opus:pack worker --model sonnet(及其他角色)rc 0、stdout 非空。"""
        self.floor_config()
        for role, model in (("worker", "sonnet"), ("verifier", "haiku"), ("opener", "opus")):
            with self.subTest(role=role, model=model):
                done = self.run_py("scripts/rules.py", "pack", role, "--model", model)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertTrue(done.stdout.strip(), "規則包是空的")

    def test_a8_a_simple_ticket_starts_sonnet_medium(self):
        """A8 complexity=simple 的票 → worker_simple:auto-fix 照起 sonnet medium,不被 routing 下限擋。"""
        self.floor_config()
        self.set_table(worker_simple=copy.deepcopy(self.base_table["roles"]["worker_simple"]))
        self.ticket_ready(model="", tool="claude-code", complexity="simple")
        done = self.auto_fix("--no-review")
        self.assertNotIn(OLD_FLOOR_TEXT, done.stderr)
        workers = self.runs("worker")
        self.assertEqual([launched(run) for run in workers], [("sonnet", "medium")], self.says(done))

    def test_a8_the_floor_code_and_its_doc_are_removed(self):
        """A8 rules.py 的 below_floor 整段拆除;DISPATCH-TEMPLATE 不再寫「低於 routing 下限 pack rc=2」。"""
        self.assertNotIn(OLD_FLOOR_SYMBOL, self.source(os.path.join("scripts", "rules.py")))
        self.assertNotIn(OLD_FLOOR_TEXT, self.source(os.path.join("docs", "DISPATCH-TEMPLATE.md")))


if __name__ == "__main__":
    unittest.main()
