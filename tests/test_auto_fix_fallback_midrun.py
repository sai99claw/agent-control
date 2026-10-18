"""#132:驗證者 / 覆核者 / 批次 worker **執行中**撞額度,與 #121 worker 路同一套換下一個候選。

假 codex(#121 的 `FallbackBase` 夾具,不複製)先回 provider 的額度信封,下一個候選是一支記
argv / cwd / stdin 的假程式。期望的 model / effort 取自這裡排的角色表(夾具的值),不從被測
腳本算回去;下一個候選刻意排 fable(不是 opus),硬寫 opus 的實作會紅。測試清掉外層的
AC_ROOT / AC_TICKETS_DIR / AC_CONTROL_DIR(`control_harness.env` 不帶任何 `AC_*`),不叫真 LLM。
"""

import glob
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import REVIEWER_PASS, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix_codex import CODEX_MODEL, FallbackBase  # noqa: E402
from test_review import ReviewBase  # noqa: E402

NEXT_MODEL = "fable"
NEXT_EFFORT = "high"
FABLE_CARD = "FABLE-CARD-132:沙盒 fable 的模型卡"

# 下一個候選的驗證者:案例在乾淨基底上紅(worker 交的 tests/test_thing.py 還不在),閘門才綠得了。
VERIFIER_DELIVERS = """#!/bin/sh
printf '%s\\n' "$@" > "@ARGV@"
pwd -P > "@ARGV@.where"
cat > "@ARGV@.stdin"
echo "verifier successor ran" >> "$AC_TEST_LOG"
cd "$AC_WORK"
mkdir -p work/verify/example
cat > work/verify/example/test_ticket_1.py <<'CASE'
\"\"\"#1 驗證者的案例:worker 交的 tests/test_thing.py 要在候選樹上。

## 驗收表(期望值來源獨立於被測程式)
C1 | sandbox | 看 tests/test_thing.py 在不在 | 在 | 乾淨基底上沒有這個檔

## 介面字串
(沒有)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼。
\"\"\"
import os
import unittest

TAGS = ["example"]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class T(unittest.TestCase):
    def test_worker_case_is_there(self):
        \"\"\"C1 worker 交的案例檔在候選樹上。\"\"\"
        self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", "test_thing.py")))
CASE
diff -ruN base work > patch-verify.diff || true
cat > EVIDENCE-verifier.md <<'EOF'
# verifier

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0, "patch_sha256": null,
 "gate": null, "mutations": [], "objection": null, "excluded": [], "repro": null,
 "memory": [],
 "verify": {"files": ["verify/example/test_ticket_1.py"], "tags": ["example"],
            "run": "python3 scripts/verify.py --tag example", "notes": "沙盒"},
 "baseline": {"stage": "red", "ok": true, "why": "",
              "files": ["verify/example/test_ticket_1.py"]}}
```
EOF
"""


class VerifierFallbackBase(FallbackBase):
    """worker 是表上的 opus(假 opus 照交);驗證者鏈 = codex → fable,codex 那一格由 @MODE@ 決定。"""

    def setUp(self):
        super().setUp()
        self.verifier_argv = os.path.join(self.home, "verifier-argv.txt")
        self.successor = os.path.join(self.home, "fake-verifier.sh")
        write_executable(self.successor, VERIFIER_DELIVERS.replace("@ARGV@", self.verifier_argv))
        conf = json.loads(self.read("board/config.json"))
        conf["verifier"] = {"command": "sh %s --model opus" % self.successor}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write(os.path.join("memory", "model", "fable.md"), "- %s\n" % FABLE_CARD)
        # `gate.sh --branch` 把 verify/* 的改動對到 test_verify_runner;沙盒補一支(同 test_auto_fix)。
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_there(self):\n        self.assertTrue(True)\n")
        self.set_role_models(
            worker=[fixture_candidate("opus", "claude-code", "high")],
            verifier=[fixture_candidate(CODEX_MODEL, "codex", "high"),
                      fixture_candidate(NEXT_MODEL, "claude-code", NEXT_EFFORT)])

    def run_verifier_case(self, mode, timeout=120, owner=None):
        self.fake_codex(mode)
        self.make_ticket("1", allowed_write_paths=["tests/*", "verify/*"], model="opus",
                         tool="claude-code", needs_verifier=True, interface_fixed=True,
                         worker={"timeout_seconds": timeout})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        extra = {"AC_RECOVERY_OWNER": owner} if owner else {}
        return self.run_sh("scripts/auto-fix.sh", "1", "--no-review", env=self.env(**extra))

    def verifier_starts(self):
        return [row for row in self.events()
                if row["kind"] == "agent.start" and row.get("role") == "verifier"]

    def verifier_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row["role"] == "verifier"]

    def launches(self):
        """auto-fix 與 consumer 起過的驗證者總數:假 codex 的驗證者那幾行 + 下一個候選那幾行。"""
        if not os.path.exists(self.log):
            return 0
        with open(self.log, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
        return (len([line for line in lines if line.startswith("codex ran verifier")])
                + len([line for line in lines if line == "verifier successor ran"]))


class VerifierTakesTheNextCandidate(VerifierFallbackBase):

    def test_b1_quota_restarts_the_verifier_in_the_same_copy(self):
        """B1。**M1**(verifier_done 不判額度 / 不換候選)→ 驗證者只起 1 次、rc 5,紅。
        四問:量 rc、agent.start、cost、argv、cwd、派工文;值 = B1 與夾具表(fable / high);
        舊值紅在 rc 與第二次啟動;表真的兩格、第二格不是 opus。"""
        done = self.run_verifier_case("quota")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([(row["model"], row.get("effort")) for row in self.verifier_starts()],
                         [(CODEX_MODEL, "high"), (NEXT_MODEL, NEXT_EFFORT)])
        cost = self.verifier_cost()
        self.assertEqual([(row["model"], row["outcome"], row.get("effort")) for row in cost],
                         [(CODEX_MODEL, "error", "high"), (NEXT_MODEL, "handed-in", NEXT_EFFORT)])
        self.assertIn("quota_exhausted", cost[0]["note"])
        with open(self.verifier_argv, encoding="utf-8") as handle:
            argv = handle.read().splitlines()
        self.assertEqual(argv[argv.index("--model") + 1], NEXT_MODEL)
        self.assertEqual(argv[argv.index("--effort") + 1], NEXT_EFFORT)
        self.assertNotIn("opus", argv, "不硬寫 opus")
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            codex_cwd = handle.read().splitlines()[0]
        with open(self.verifier_argv + ".where", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), codex_cwd, "下一個候選要在同一份副本續做")
        with open(self.verifier_argv + ".stdin", encoding="utf-8") as handle:
            self.assertIn(FABLE_CARD, handle.read(), "派工文的規則包要照實際起的模型重組")
        self.assertIn("test_ticket_1.py", self.git("ls-tree", "-r", "--name-only", "t1"),
                      "下一個候選交的案例要照既有那一手收進分支")

    def assert_no_verifier_fallback(self, mode, timeout=120):
        done = self.run_verifier_case(mode, timeout=timeout)
        self.assertEqual(len(self.verifier_starts()), 1, done.stdout + done.stderr)
        self.assertEqual(len(self.verifier_cost()), 1, self.verifier_cost())
        self.assertFalse(os.path.exists(self.verifier_argv), "%s 不該起下一個候選" % mode)
        self.assertEqual(self.launches(), 1)

    def test_b4_an_unrelated_failure_does_not_change_model(self):
        """B4。**M5**(換候選的條件放寬成 rc≠0)→ 起 2 次,紅。
        四問:量驗證者 agent.start / cost / 下一個候選的 argv;值 = B4(只起 1 次);舊值本來就 1 次,
        紅在 M5;表真的兩格(第二格在,只是不該起)。"""
        self.assert_no_verifier_fallback("other")

    def test_b4_literal_429_does_not_change_model(self):
        """B4:沒有 provider 信封、只有字面 429。文字 grep 429 的實作 → 起 2 次,紅。
        四問:同上;值 = B4 字面 429;M5 紅;表真的兩格。"""
        self.assert_no_verifier_fallback("text429")

    def test_b4_no_patch_does_not_change_model(self):
        """B4:rc 0 沒交 patch-verify 不是不可用。
        四問:同上;值 = B4 no-patch;rc 0 不進判讀,把沒交件當不可用的實作會紅;表真的兩格。"""
        self.assert_no_verifier_fallback("deliver")

    def test_b4_timeout_does_not_change_model(self):
        """B4:逾時(rc 124)不換。
        四問:同上;值 = B4 timeout;M5(連 124 也判)紅;表真的兩格。"""
        self.assert_no_verifier_fallback("hang", timeout=1)

    def run_with_owner(self, owner):
        """跑一次:有 Controller 時照 `agent.recovery_needed` 那一列當 consumer 起下一個候選。"""
        done = self.run_verifier_case("quota", owner=owner)
        if owner is None:
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return self.launches()
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        self.assertEqual(len(self.verifier_starts()), 1, "有 Controller 時 auto-fix 自己不起下一個")
        handed = [row for row in self.events() if row["kind"] == "agent.recovery_needed"
                  and row.get("role") == "verifier"]
        self.assertEqual(len(handed), 1, handed)
        row = handed[0]
        self.assertEqual((row["model"], row.get("tool"), row.get("effort"), row.get("candidate_index")),
                         (NEXT_MODEL, "claude-code", NEXT_EFFORT, "1"))
        self.assertTrue(os.path.isdir(row["copy"]), "副本要留給 Controller 接續")
        self.assertTrue(os.path.isfile(row["dispatch"]), "派工文要留給 Controller 餵")
        with open(row["dispatch"], encoding="utf-8") as feed:
            consumer = subprocess.run(
                ["sh", self.successor, "--model", row["model"], "--effort", row["effort"]],
                cwd=row["copy"], stdin=feed, capture_output=True, text=True, timeout=60,
                env=self.env(AC_WORK=row["copy"], AC_ROLE="verifier", AC_TICKET="1",
                             AC_ROUND=row["round"], AC_DISPATCH=row["dispatch"]))
        self.assertEqual(consumer.returncode, 0, consumer.stdout + consumer.stderr)
        self.assertTrue(os.path.isfile(os.path.join(row["copy"], "patch-verify.diff")))
        return self.launches()

    def test_b5_controller_consumer_keeps_the_total_launch_count(self):
        """B5。**M3**(Controller 分支發了事件還照樣自己起)→ 有 Controller 那一次 rc 0 不是 7(總啟動 3),紅。
        四問:量 auto-fix 與 fake consumer 的總啟動;值 = B5(兩種 owner 相等,= 表的兩格);
        舊值(沒有 handoff)紅在 rc;owner 兩種都真的跑。"""
        local = self.run_with_owner(None)
        self.doCleanups()
        self.setUp()
        controller = self.run_with_owner("controller")
        self.assertEqual(controller, local)
        self.assertEqual(local, 2, "夾具表的驗證者鏈是兩格:codex 一次、fable 一次")


class BatchWorkerTakesTheNextCandidate(FallbackBase):

    def test_b3_quota_restarts_only_the_batch_worker(self):
        """B3。**M4**(批次 worker 撞額度不換)→ rc 5、opus 沒起,紅。
        四問:量 rc、agent.start、cost、argv、第二張票;值 = B3 與夾具表;舊值紅在 rc。"""
        batch = self.make_batch()
        before_two = self.load_ticket("2")
        self.fake_codex("quota")
        done = self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2",
                           "--no-review",
                           env=self.env(AC_BATCH_RUN_ID=batch["run_id"],
                                        AC_BATCH_STAMP="ticket-132"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = [row for row in self.events()
                  if row["kind"] == "agent.start" and row.get("role") == "worker"]
        self.assertEqual([(row["model"], row.get("effort")) for row in starts],
                         [(CODEX_MODEL, "high"), ("opus", "high")])
        rows = self.worker_cost()
        self.assertEqual([(row["model"], row["outcome"]) for row in rows],
                         [(CODEX_MODEL, "error"), ("opus", "handed-in")])
        self.assertIn("quota_exhausted", rows[0]["note"])
        self.assertEqual(self.load_ticket("2"), before_two, "其他批次票不受影響")
        argv = self.opus_args()
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            codex_cwd = handle.read().splitlines()[0]
        with open(self.opus_argv + ".where", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), codex_cwd, "同一份批次副本續做")

    def test_b4_batch_unrelated_failure_does_not_change_model(self):
        """B4(批次):一般失敗不換 —— opus 不起。
        四問:量假 codex / 假 opus 的呼叫數;值 = B4;換候選條件放寬成 rc≠0 會紅;表真的兩格。"""
        batch = self.make_batch()
        self.fake_codex("other")
        self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2", "--no-review",
                    env=self.env(AC_BATCH_RUN_ID=batch["run_id"], AC_BATCH_STAMP="ticket-132"))
        self.assertEqual((len(self.codex_calls()), len(self.opus_calls())), (1, 0))


REVIEWER_CODEX = """#!/bin/sh
printf '%s\\n' "$@" > "@ARGV@"
cat >/dev/null
echo "codex ran reviewer" >> "$AC_TEST_LOG"
@BEFORE@
echo '{"type":"turn.failed","error":{"message":"@SAID@"}}'
exit 1
"""


class ReviewerTakesTheNextCandidate(ReviewBase):
    """覆核者鏈 = codex → fable;fable 是 `reviewer.command` 那一支假 reviewer(交 pass)。"""

    def arm(self, said="You have hit your usage limit (429).", before=""):
        self.next_argv = os.path.join(self.home, "reviewer-next-argv.txt")
        successor = "#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\n%s" % (
            self.next_argv, REVIEWER_PASS.split("\n", 1)[1])
        self.set_reviewer(successor, model=NEXT_MODEL)
        self.write(os.path.join("memory", "model", "fable.md"), "- %s\n" % FABLE_CARD)
        self.set_role_models(reviewer=[fixture_candidate(CODEX_MODEL, "codex", "high"),
                                       fixture_candidate(NEXT_MODEL, "claude-code", NEXT_EFFORT)])
        self.codex_argv = os.path.join(self.home, "reviewer-codex-argv.txt")
        write_executable(os.path.join(self.stub_bin, "codex"),
                         REVIEWER_CODEX.replace("@ARGV@", self.codex_argv)
                         .replace("@SAID@", said).replace("@BEFORE@", before))

    def starts(self):
        return [row for row in self.events()
                if row["kind"] == "agent.start" and row.get("role") == "reviewer"]

    def reviewer_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row["role"] == "reviewer"]

    def receipts(self):
        return glob.glob(os.path.join(self.repo, "reports", "t1", "*", "review-receipt.json"))

    def test_b2_quota_restarts_under_the_same_receipt(self):
        """B2。四問:量 rc、agent.start、cost、review.by、收件單、argv、派工文;值 = B2 / #98 與
        夾具表;舊值紅在 rc(沒交件 3);表真的兩格。"""
        self.arm()
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([(row["model"], row.get("effort")) for row in self.starts()],
                         [(CODEX_MODEL, "high"), (NEXT_MODEL, NEXT_EFFORT)])
        rows = self.reviewer_cost()
        self.assertEqual([(row["model"], row["outcome"]) for row in rows],
                         [(CODEX_MODEL, "missing"), (NEXT_MODEL, "pass")])
        self.assertIn("quota_exhausted", rows[0]["note"])
        self.assertEqual(self.load_ticket("1")["review"]["by"], "reviewer@" + NEXT_MODEL)
        receipt, = self.receipts()
        with open(receipt, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["request"]["sha"], self.sha)
        self.assertTrue(data["result"]["accepted"], data)
        with open(self.next_argv, encoding="utf-8") as handle:
            argv = handle.read().splitlines()
        self.assertEqual(argv[argv.index("--model") + 1], NEXT_MODEL)
        # REVIEWER_PASS 把 stdin(派工文)存在 reviewer-stdin.md。
        with open(os.path.join(self.home, "reviewer-stdin.md"), encoding="utf-8") as handle:
            self.assertIn(FABLE_CARD, handle.read(), "派工文的規則包要照實際起的模型重組")

    def test_b2_the_next_candidate_still_goes_through_the_cas(self):
        """B2「不跳過 attempt 檢查」。第一位 reviewer 跑的時候票換了 attempt:換來的 fable 交 pass
        也要 stale(rc 4)、票的 review 一格不動。**M2**(換候選時重開一張收件單)→ rc 0、
        review 寫進票,紅。四問:量 rc、agent.start、票的 review、收件單數;值 = B2 / #98 CAS;
        舊值(不換候選)紅在 starts;表真的兩格。"""
        self.arm(before='python3 "$AC_CONTROL_DIR/ticket.py" set 1 attempt 5 >/dev/null')
        done = self.review()
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual([row["model"] for row in self.starts()], [CODEX_MODEL, NEXT_MODEL])
        self.assertIsNone(self.load_ticket("1").get("review"))
        self.assertEqual(len(self.receipts()), 1)

    def test_b4_an_unrelated_provider_error_does_not_change_reviewer(self):
        """B4(覆核):provider 信封沒說額度 / 不可用 → 不換,fable 不起,沒交件 rc 3。
        四問:量 rc、agent.start、fable 的 argv;值 = B4;換候選條件放寬會紅;表真的兩格。"""
        self.arm(said="something unrelated broke")
        done = self.review()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual([row["model"] for row in self.starts()], [CODEX_MODEL])
        self.assertFalse(os.path.exists(self.next_argv))

    def test_b5_with_a_controller_the_reviewer_is_handed_over(self):
        """B5(覆核):有 Controller 只發 `agent.recovery_needed`、自己不起 fable(rc 7)。
        四問:量 rc、agent.start、recovery_needed 那一列、收件單;值 = B5;Controller 分支也自己起
        的實作紅在 rc 與 starts;表真的兩格。"""
        self.arm()
        done = self.run_sh("scripts/review.sh", "1", env=self.env(AC_RECOVERY_OWNER="controller"))
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        self.assertEqual([row["model"] for row in self.starts()], [CODEX_MODEL])
        self.assertFalse(os.path.exists(self.next_argv))
        handed = [row for row in self.events() if row["kind"] == "agent.recovery_needed"]
        self.assertEqual([(row.get("role"), row["model"], row.get("effort")) for row in handed],
                         [("reviewer", NEXT_MODEL, NEXT_EFFORT)])
        self.assertTrue(os.path.isfile(handed[0]["receipt"]), "收件單留給 Controller 接續")

    def test_a3_the_whole_reviewer_chain_used_up_is_blocked_and_one_page(self):
        """整串耗盡(A3 同形):表只有 codex 一格 → rc 7、票 Blocked、一頁、不再起。
        四問:量 rc、agent.start、票 state / owner / review、頁數;值 = #121 A3 同形;舊值紅在 rc(3);
        表真的只有一格(不靠沒設就退回)。"""
        self.arm()
        self.set_role_models(reviewer=[fixture_candidate(CODEX_MODEL, "codex", "high")])
        done = self.review()
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        self.assertEqual([row["model"] for row in self.starts()], [CODEX_MODEL])
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["state"], ticket.get("owner")), ("Blocked", "main"))
        self.assertIsNone(ticket.get("review"))
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("候選整串耗盡", pages[0]["state"])


if __name__ == "__main__":
    unittest.main()
