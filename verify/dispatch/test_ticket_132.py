"""#132 驗證者、覆核者與批次 worker 執行中撞額度 → 照角色表換下一候選(同 #121 worker 主幹)。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 本檔夾具寫下的角色表)
B1 | integ | verifier 鏈 [codex high, fable medium];假 codex 回 provider 額度信封、假 fable 交件 | 同一次 auto-fix 接著起 fable,argv --model fable --effort medium、cwd 同一份副本且 codex 留下的記號還在;agent.start 與 verifier cost 依序 codex → fable;argv 不含 opus | 票面 B1 + 本檔 CHAIN
B2 | integ | reviewer 鏈 [codex high, fable medium];假 codex 回額度信封 | 起 fable、pass 寫進票的 review(sha = 分支頭)(#136 刪了 #98 的綁定與「票被改過就不收」那一案) | 票面 B2
B3 | integ | 批次 worker 鏈 [codex high, fable medium];假 codex 回額度信封 | 同一份副本起 fable,argv 照表;票 #2 的 cost / state / review 一個字不動 | 票面 B3 + #121 A2/A3
B4 | integ | verifier 的 codex 逾時 / 印測試紅 rc 1 / rc 0 沒交件 / 只印字面 429;覆核與批次各一案字面 429 | 每案只起 codex 一次,fable 不起 | 票面 B4「沿用 #121 classify」
B5 | integ | 同 B1,一次無 owner、一次 AC_RECOVERY_OWNER=controller + 本檔的 fake consumer(讀 agent.recovery_needed 起下一候選) | 兩次的總啟動數相等(= 2,表兩格);controller 那次 auto-fix 自己只起 1 次 | 票面 B5

## 介面字串
QUOTA 證據 = 一行頂層 JSON {"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}(#121 夾具的 codex 額度信封)
CLAUDE flags = "--model <m>" / "--effort <e>"(claude-code 候選換進 worker.command / reviewer.command 的)
RECOVERY = 事件 kind "agent.recovery_needed",欄位 model / effort / copy(#121 A3 已有的 worker 那一種,本票擴到 verifier)

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/test_auto_fix_codex.FallbackBase / tests/test_review.ReviewBase / tests/test_auto_fix.BatchFix(拋棄式 git repo,
PATH 最前面的 claude 是絆線)。codex = stub-bin 的假 codex,每次被叫記一行(角色、argv、cwd)並在 $AC_WORK 留記號;
claude-code 候選 = worker.command / reviewer.command 指向的假 sh,記一行(角色、argv、cwd、codex 記號在不在)。
下一候選刻意排 fable medium(不是 opus、effort 也和 codex 那格不同),硬寫 opus 或沿用上一格 effort 都會紅。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不呼叫真 LLM。
"""
import json
import os
import shlex
import subprocess
import sys
import unittest

TAGS = ["midrun-fallback"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import DEFAULT_CONFIG, REVIEWER_PASS, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix import BATCH_WORKER_FIXES, FIRST_ROUND_PAIR, AutoFixBase  # noqa: E402
from test_auto_fix_codex import CODEX_MODEL, FallbackBase  # noqa: E402
from test_review import ReviewBase  # noqa: E402

FIRST_EFFORT = "high"
NEXT_MODEL = "fable"
NEXT_EFFORT = "medium"
CHAIN = [fixture_candidate(CODEX_MODEL, "codex", FIRST_EFFORT),
         fixture_candidate(NEXT_MODEL, "claude-code", NEXT_EFFORT)]
QUOTA = '{"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}'
MARK = "codex-was-here"

FAKE_CODEX = """#!/bin/sh
printf 'codex\\t%s\\t%s\\t%s\\n' "$AC_ROLE" "$*" "$(pwd -P)" >> "@CALLS@"
[ -z "$AC_WORK" ] || : > "$AC_WORK/@MARK@"
cat > /dev/null
@BEFORE@
case "@MODE@" in
    quota)
        echo '@QUOTA@'
        exit 1 ;;
    timeout)
        exec sleep 60 ;;
    test-red)
        echo 'FAIL: test_thing (tests.test_thing.T.test_thing)'
        echo 'FAILED (failures=1)'
        exit 1 ;;
    no-patch)
        exit 0 ;;
    literal-429)
        echo 'ERROR: stream error: 429 Too Many Requests'
        echo "You've hit your usage limit." >&2
        exit 1 ;;
esac
exit 3
"""

# claude-code 候選的前一行:記角色、argv、起跑 cwd、codex 留在 $AC_WORK 的記號還在不在。
LOG_LINE = ('printf \'agent\\t%s\\t%s\\t%s\\t%s\\n\' "$AC_ROLE" "$*" "$(pwd -P)" '
            '"$( [ -n "$AC_WORK" ] && [ -f "$AC_WORK/@MARK@" ] && echo kept || echo gone )" >> "@CALLS@"\n')


def install_codex(case, calls, mode, before=""):
    write_executable(os.path.join(case.stub_bin, "codex"),
                     FAKE_CODEX.replace("@CALLS@", calls).replace("@MODE@", mode)
                     .replace("@MARK@", MARK).replace("@QUOTA@", QUOTA).replace("@BEFORE@", before))


def with_log(body, calls):
    head, rest = body.split("\n", 1)
    return head + "\n" + LOG_LINE.replace("@CALLS@", calls).replace("@MARK@", MARK) + rest


def calls_of(path, role):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]
    return [row for row in rows if row[1] == role]


def flag(argv_text, name):
    argv = shlex.split(argv_text)
    return argv[argv.index(name) + 1] if name in argv else None


class MidrunCase:
    """三路共用:建 calls 檔、裝 fable 模型卡、讀事件與 cost。"""

    def midrun_setup(self):
        self.calls = os.path.join(self.home, "midrun-calls.tsv")
        self.write("midrun-calls.tsv", "", where=self.home)
        self.write(os.path.join("memory", "model", NEXT_MODEL + ".md"), "- 沙盒 fable 卡\n")

    def starts(self, role):
        return [(row["model"], row.get("effort")) for row in self.events()
                if row["kind"] == "agent.start" and row.get("role") == role]

    def costs(self, role, ident="1"):
        return [row for row in self.load_ticket(ident).get("cost") or [] if row["role"] == role]

    def assert_next_candidate(self, row):
        self.assertEqual(row[0], "agent", row)
        self.assertEqual(flag(row[2], "--model"), NEXT_MODEL, row)
        self.assertEqual(flag(row[2], "--effort"), NEXT_EFFORT, row)
        self.assertNotIn("opus", shlex.split(row[2]), "不硬寫 opus")


class VerifierFixture(MidrunCase, FallbackBase):

    def arm(self, mode="quota", **fields):
        self.midrun_setup()
        install_codex(self, self.calls, mode)
        body = with_log(FIRST_ROUND_PAIR, self.calls)
        for key, value in (("@VCALLS@", os.path.join(self.home, "verifier-calls.log")),
                           ("@VSAW@", os.path.join(self.home, "verifier-saw-patch")),
                           ("@WPATCH@", os.path.join(self.home, "nowhere.diff")),
                           ("@WORKER_MODE@", "patch"), ("@VMARK@", os.path.join(self.home, "v")),
                           ("@WMARK@", os.path.join(self.home, "w")), ("@WAIT@", "0"),
                           ("@VERIFIER_HANDS_IN@", "1")):
            body = body.replace(key, value)
        self.agent = os.path.join(self.home, "fake-agent.sh")
        write_executable(self.agent, body)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % self.agent, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(worker=[fixture_candidate("opus", effort="high")], verifier=CHAIN)
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_there(self):\n        self.assertTrue(True)\n")
        fields.setdefault("allowed_write_paths", ["tests/*", "verify/*"])
        self.make_ticket("1", needs_verifier=True, interface_fixed=True, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")

    def run_fix(self, **extra):
        return self.run_sh("scripts/auto-fix.sh", "1", "--no-review", env=self.env(**extra))


class B1TheVerifierTakesTheNextCandidate(VerifierFixture):

    def test_b1_a_quota_verifier_restarts_fable_in_the_same_copy(self):
        """B1 codex 驗證者撞額度 → 同副本起表上下一格 fable medium;事件與 cost 依序兩格。

        四問:量 verifier 的兩次啟動(argv/cwd/記號)、agent.start、cost;值 = 本檔 CHAIN;
        舊行為(t131:驗證者撞額度只記 error)紅在「只起一次」;不靠預設(表真的兩格、下一格不是 opus)。"""
        self.arm()
        done = self.run_fix()
        rows = calls_of(self.calls, "verifier")
        self.assertEqual([row[0] for row in rows], ["codex", "agent"], done.stdout + done.stderr)
        self.assert_next_candidate(rows[1])
        self.assertEqual(rows[1][3], rows[0][3], "下一候選沿用驗證者那一份副本")
        self.assertEqual(rows[1][4], "kept", "副本不刪:codex 留的記號要還在")
        self.assertEqual(self.starts("verifier"),
                         [(CODEX_MODEL, FIRST_EFFORT), (NEXT_MODEL, NEXT_EFFORT)])
        self.assertEqual([(row["model"], row["effort"]) for row in self.costs("verifier")],
                         [(CODEX_MODEL, FIRST_EFFORT), (NEXT_MODEL, NEXT_EFFORT)])
        self.assertEqual(self.costs("verifier")[-1]["outcome"], "handed-in")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class B4VerifierOrdinaryFailuresKeepTheModel(VerifierFixture):

    def assert_one_launch(self, mode, **fields):
        self.arm(mode, **fields)
        done = self.run_fix()
        rows = calls_of(self.calls, "verifier")
        self.assertEqual([row[0] for row in rows], ["codex"], done.stdout + done.stderr)
        self.assertEqual([m for m, _ in self.starts("verifier")], [CODEX_MODEL])

    def test_b4_a_verifier_timeout_does_not_change_model(self):
        """B4 逾時不換模型:codex 驗證者睡過頭,只起一次。"""
        self.assert_one_launch("timeout", worker={"timeout_seconds": 2})

    def test_b4_a_verifier_test_red_does_not_change_model(self):
        """B4 測試紅(rc 1、印 FAILED)不換模型。"""
        self.assert_one_launch("test-red")

    def test_b4_a_verifier_no_patch_does_not_change_model(self):
        """B4 rc 0 沒交 patch-verify 不換模型。"""
        self.assert_one_launch("no-patch")

    def test_b4_a_literal_429_verifier_does_not_change_model(self):
        """B4 只有字面 429(不是 provider 信封)不換模型。"""
        self.assert_one_launch("literal-429")


class B5ControllerOwnerKeepsTheLaunchCount(VerifierFixture):

    def consume(self):
        """fake consumer:讀 agent.recovery_needed 的 model / effort / copy,照派工文起下一候選。"""
        handed = [row for row in self.events() if row["kind"] == "agent.recovery_needed"]
        self.assertEqual(len(handed), 1, handed)  # pin:恰一筆交接,兩筆就是兩邊都起
        row = handed[0]
        self.assertEqual((row.get("model"), row.get("effort")), (NEXT_MODEL, NEXT_EFFORT))
        self.assertTrue(os.path.isdir(row["copy"]), "副本留給 Controller 接續")
        packets = self.result_files("dispatch-verifier-round1.md")
        self.assertTrue(packets, "派工文要留著給 consumer")
        with open(packets[-1], encoding="utf-8") as packet:
            done = subprocess.run(
                ["sh", self.agent, "--model", row["model"], "--effort", row["effort"]],
                cwd=row["copy"], stdin=packet, capture_output=True, text=True, timeout=120,
                env=self.env(AC_WORK=row["copy"], AC_ROLE="verifier", AC_ROUND="1",
                             AC_TICKET="1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_b5_controller_and_local_owner_start_the_same_number_of_agents(self):
        """B5 無 owner 與 controller+consumer 總啟動數相同(= 表兩格 = 2);controller 那次
        auto-fix 自己只起 codex。四問:量兩次的 verifier 啟動數;值 = 票面 B5;
        「兩邊都起」紅在 3,「沒交 Controller」紅在 recovery 事件不在。"""
        self.arm()
        self.run_fix()
        local = len(calls_of(self.calls, "verifier"))
        self.tearDown_and_reset()
        self.arm()
        self.run_fix(AC_RECOVERY_OWNER="controller")
        own = len(calls_of(self.calls, "verifier"))
        self.assertEqual(own, 1, "有 Controller 時 auto-fix 只交記錄、不自己起下一候選")
        self.consume()
        controller = len(calls_of(self.calls, "verifier"))
        self.assertEqual((local, controller), (2, 2))

    def tearDown_and_reset(self):
        self.doCleanups()
        self.setUp()


class ReviewFixture(MidrunCase, ReviewBase):

    def arm(self, mode="quota", before=""):
        self.midrun_setup()
        install_codex(self, self.calls, mode, before)
        self.set_reviewer(with_log(REVIEWER_PASS, self.calls), model=NEXT_MODEL)
        self.set_role_models(reviewer=CHAIN)


class B2TheReviewerTakesTheNextCandidate(ReviewFixture):

    def test_b2_a_quota_reviewer_restarts_fable(self):
        """B2 覆核者 codex 撞額度 → 起 fable medium;pass 寫進票的 review。

        四問:量 reviewer 啟動、argv、agent.start、review;值 = CHAIN;
        舊行為紅在 fable 沒起 / review 不是 pass。"""
        self.arm()
        done = self.review()
        rows = calls_of(self.calls, "reviewer")
        self.assertEqual([row[0] for row in rows], ["codex", "agent"], done.stdout + done.stderr)
        self.assert_next_candidate(rows[1])
        self.assertEqual(self.starts("reviewer"),
                         [(CODEX_MODEL, FIRST_EFFORT), (NEXT_MODEL, NEXT_EFFORT)])
        self.assertEqual([row["model"] for row in self.costs("reviewer")], [CODEX_MODEL, NEXT_MODEL])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["review"]["verdict"], "pass")
        self.assertEqual(ticket["review"]["sha"], self.sha)


class B4TheReviewerLiteral429KeepsTheModel(ReviewFixture):

    def test_b4_a_literal_429_reviewer_does_not_change_model(self):
        """B4 覆核者只印字面 429 → 不起 fable。"""
        self.arm("literal-429")
        done = self.review()
        self.assertEqual([row[0] for row in calls_of(self.calls, "reviewer")], ["codex"],
                         done.stdout + done.stderr)


class BatchFixture(MidrunCase, AutoFixBase):
    """tests/test_auto_fix.BatchFix 的 setUp / run_batch 同形(不繼承它:它帶著自己的 test_)。"""

    def setUp(self):
        super().setUp()
        self.batch = self.make_batch()
        self.before = {ident: self.load_ticket(ident) for ident in ("1", "2")}

    def run_batch(self, *extra):
        return self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2", *extra,
                           env=self.env(AC_BATCH_RUN_ID=self.batch["run_id"]))

    def arm(self, mode="quota"):
        self.midrun_setup()
        install_codex(self, self.calls, mode)
        self.set_worker(with_log(BATCH_WORKER_FIXES, self.calls))
        self.set_role_models(worker=CHAIN)


class B3TheBatchWorkerTakesTheNextCandidate(BatchFixture):

    def test_b3_a_quota_batch_worker_restarts_fable_and_leaves_ticket_2(self):
        """B3 批次 worker codex 撞額度 → 同一份副本起 fable medium;票 #2 一個字不動。

        四問:量 worker 兩次啟動、cwd、記號、cost、票 #2;值 = CHAIN + #121 A2/A3;
        舊行為紅在「只起一次」。"""
        self.arm()
        done = self.run_batch("--no-review")
        rows = calls_of(self.calls, "worker")
        self.assertEqual([row[0] for row in rows], ["codex", "agent"], done.stdout + done.stderr)
        self.assert_next_candidate(rows[1])
        self.assertEqual(rows[1][3], rows[0][3], "下一候選沿用批次那一份副本")
        self.assertEqual(rows[1][4], "kept")
        self.assertEqual(self.starts("worker"),
                         [(CODEX_MODEL, FIRST_EFFORT), (NEXT_MODEL, NEXT_EFFORT)])
        self.assertEqual(self.load_ticket("2"), self.before["2"], "其他批次票不受影響")


class B4TheBatchLiteral429KeepsTheModel(BatchFixture):

    def test_b4_a_literal_429_batch_worker_does_not_change_model(self):
        """B4 批次 worker 只印字面 429 → 不起 fable。"""
        self.arm("literal-429")
        done = self.run_batch("--no-review")
        self.assertEqual([row[0] for row in calls_of(self.calls, "worker")], ["codex"],
                         done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
