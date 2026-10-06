"""#94 成本帳一列一形:cost[] 每列 14 鍵、outcome 只收登記字、token 取 modelUsage 全模型加總、
ticket.cost 事件帶整列、每個派工點填 outcome / run_id、關票寫 cost_total。

## 驗收表(票面每一條一列;期望值全部取自票面字面或手算,不從被測程式算回來)
C1 | unit   | cost 帶信封 session_id=s-1 + --outcome handed-in --run-id r-1   | 列鍵集合 == 14 鍵、三新鍵值對上      | 票面 C1 字面
C1 | unit   | cost 三旗標都不給、不帶信封                                    | 三新鍵與 token 四欄皆 None            | 票面 C1 字面
C2 | unit   | 每個登記字(配它所屬的角色)各寫一列                            | 都 rc=0                               | 票面 C2 登記表
C2 | unit   | --outcome passed                                               | rc=2、stderr 印整張表、cost/事件不多  | 票面 C2 字面
C3 | unit   | usage {5,77,0,9} + modelUsage m-a {5,77,0,9} m-b {3,20,4,100}   | (8, 97, 4, 109)                       | 手算 5+3 / 77+20 / 0+4 / 9+100
C3 | unit   | 只有 usage 的信封                                               | (5, 77, 0, 9)                         | 票面 C3 字面
C4 | unit   | 寫一列後讀最後一筆 ticket.cost                                  | 列的每鍵(除 at)事件裡值相同          | 票面 C4 字面
C5 | script | auto-fix 假 worker 交 / 不交;驗證者交;開題者判 accepted / 不寫 TRIAGE;review pass;land 綠 / 全套紅 | 各列 outcome / run_id | 票面 C5 字面
C6 | unit   | 預放六列後 close / set state Done;close 被拒                  | cost_total 逐格 == 手算;拒絕時沒有    | 票面 C6 手算(本檔 Close 類 docstring)
C7 | unit   | cost 不動 state_version / review;close 只 +1                  | 版本差 0 / 1                          | 票面 C7 字面 + 既有 close 契約(+1)
C8 | text   | SCHEMA / event.py / ticket.py cost --help                      | 含 14 鍵、登記字、cost_total、新旗標  | 票面 C8 字面

C9(既有斷言照綠)沒有案例:它在乾淨基底上本來就綠,不是「基底該紅」的驗收,由閘門跑既有那十支守。

## 介面字串(每條斷言靠哪個字串)
`--outcome` / `--run-id`(C1 新旗標)、信封頂層 `session_id`、`modelUsage` 四鍵 inputTokens /
outputTokens / cacheCreationInputTokens / cacheReadInputTokens(C3)、票上 `cost_total`(C6)。

## 怎麼做假
全在 control_harness 的拋棄式沙盒;auto-fix / 判反駁 / review / land 的替身沿用既有測試的 base 類
(AutoFixBase / TriageBase / ReviewBase / LandBase)與它們的假 worker / 假 claude / 假 reviewer / 閘門替身。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例。
"""

import json
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (GATE_STUB_GREEN, GATE_STUB_RED,  # noqa: E402
                             REVIEWER_PASS_ENVELOPE, Sandbox, envelope)
from test_auto_fix import AutoFixBase, WORKER_NEVER, WORKER_ROUND_ONE_COSTS  # noqa: E402
from test_land import LandBase  # noqa: E402
from test_objection_triage import TriageBase  # noqa: E402
from test_review import REVIEW_FAIL, ReviewBase  # noqa: E402

TAGS = []

ROW_KEYS = {"role", "round", "model", "tokens_in", "tokens_out", "cache_write", "cache_read",
            "wall_seconds", "outcome", "run_id", "session_id", "by", "note", "at"}
TOKENS = ("tokens_in", "tokens_out", "cache_write", "cache_read")
# C2 登記表,照票面逐字抄;每個字配它所屬的角色(驗不驗角色是實作者的事,配對了兩種都過)。
REGISTERED = (("worker", "handed-in"), ("worker", "no-patch"), ("worker", "apply-failed"),
              ("worker", "timeout"), ("worker", "objection"), ("worker", "error"),
              ("reviewer", "pass"), ("reviewer", "fail"), ("reviewer", "missing"),
              ("opener", "accepted"), ("opener", "rejected"), ("opener", "escalate"),
              ("opener", "undecided"),
              ("land", "pass"), ("land", "fail"), ("land", "refused"))
REGISTERED_WORDS = sorted({word for _, word in REGISTERED})


def multi_model_envelope(session_id="s-3"):
    """C3 票面的信封:usage 與 m-a 同數,m-b 是子 agent 換的模型。一行,形狀照真的 result 信封。"""
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "duration_ms": 4000,
        "result": "x", "session_id": session_id,
        "usage": {"input_tokens": 5, "output_tokens": 77,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 9},
        "modelUsage": {
            "m-a": {"inputTokens": 5, "outputTokens": 77, "cacheCreationInputTokens": 0,
                    "cacheReadInputTokens": 9, "costUSD": 0.1},
            "m-b": {"inputTokens": 3, "outputTokens": 20, "cacheCreationInputTokens": 4,
                    "cacheReadInputTokens": 100, "costUSD": 0.2}},
    }, ensure_ascii=False)


def usage_only_envelope():
    return json.dumps({
        "type": "result", "subtype": "success", "duration_ms": 1000, "result": "x",
        "session_id": "s-u",
        "usage": {"input_tokens": 5, "output_tokens": 77,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 9},
    }, ensure_ascii=False)


def parse_ts(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


class CostBase(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="InReview", state_version=4,
                         review={"verdict": "pass", "by": "reviewer@opus",
                                 "sha": "abc1234", "state_version": 4})

    def envelope_file(self, text, name="envelope.json"):
        return self.write(name, text + "\n", where=self.home)

    def cost(self, *args, role="worker"):
        return self.ticket("cost", "1", "--role", role, "--round", "1",
                           "--model", "opus", "--by", "auto-fix.sh", *args)

    def rows(self):
        return self.load_ticket("1").get("cost") or []

    def cost_events(self):
        return [row for row in self.events() if row["kind"] == "ticket.cost"]


class TheRowHasOneShape(CostBase):

    def test_c1_new_keys_come_from_flags_and_the_envelope(self):
        """C1 信封 session_id=s-1 + --outcome handed-in --run-id r-1 → 14 鍵、三新鍵對上。"""
        path = self.envelope_file(envelope("x", 1, 2, 3, 4, duration_ms=1000)
                                  .replace("00000000-0000-0000-0000-000000000000", "s-1"))
        done = self.cost("--from-envelope", path, "--outcome", "handed-in", "--run-id", "r-1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual((row.get("outcome"), row.get("run_id"), row.get("session_id")),
                         ("handed-in", "r-1", "s-1"))

    def test_c1_missing_values_are_json_null(self):
        """C1 三旗標都不給、不帶信封 → outcome / run_id / session_id 與 token 四欄皆 None。"""
        done = self.cost("--wall-seconds", "3")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows()[-1]
        self.assertEqual(set(row), ROW_KEYS)
        for key in ("outcome", "run_id", "session_id") + TOKENS:
            self.assertIsNone(row.get(key), key)
        raw = self.read(os.path.join("tickets", "1.json"))
        self.assertIn('"outcome": null', raw)
        self.assertIn('"session_id": null', raw)


class OutcomeIsRegistered(CostBase):

    def test_c2_every_registered_word_is_accepted(self):
        """C2 正例:登記表每個字(配它的角色)各寫一列都 rc=0,列上的 outcome 就是那個字。"""
        for index, (role, word) in enumerate(REGISTERED):
            with self.subTest(word=word, role=role):
                done = self.cost("--outcome", word, "--run-id", "r-%d" % index, role=role)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.rows()[-1].get("outcome"), word)
        self.assertEqual(len(self.rows()), len(REGISTERED))

    def test_c2_an_unregistered_word_is_refused_and_writes_nothing(self):
        """C2 反例:--outcome passed → rc=2、stderr 印整張表、cost[] 不變、不多一筆 ticket.cost。"""
        self.cost("--outcome", "handed-in")
        rows_before = len(self.rows())
        events_before = len(self.cost_events())
        done = self.cost("--outcome", "passed", "--run-id", "r-x")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for word in REGISTERED_WORDS:
            self.assertIn(word, done.stderr, "stderr 沒印出登記字 %s" % word)
        self.assertEqual(len(self.rows()), rows_before)
        self.assertEqual(len(self.cost_events()), events_before)


class TokensSumEveryModel(CostBase):

    def test_c3_model_usage_is_summed_over_every_model(self):
        """C3 usage {5,77,0,9} + modelUsage m-a {5,77,0,9} m-b {3,20,4,100} → (8, 97, 4, 109)。"""
        done = self.cost("--from-envelope", self.envelope_file(multi_model_envelope()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows()[-1]
        self.assertEqual(tuple(row.get(key) for key in TOKENS), (8, 97, 4, 109))
        self.assertEqual(row.get("model"), "opus", "model 欄仍是 --model")
        path = self.envelope_file(usage_only_envelope(), name="usage-only.json")
        done = self.cost("--from-envelope", path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(tuple(self.rows()[-1].get(key) for key in TOKENS), (5, 77, 0, 9))


class TheEventCarriesTheWholeRow(CostBase):

    def test_c4_ticket_cost_event_equals_the_row_minus_at(self):
        """C4 寫一列後最後一筆 ticket.cost 對列的每鍵(除 at)值相同。

        事件層 `emit` 本來就不寫值為 None 的鍵(event.py),所以比 `event.get(key)`;
        票面點名的七鍵這一列都給了非 null 的值,它們必須真的在事件裡。"""
        done = self.cost("--from-envelope", self.envelope_file(multi_model_envelope("s-4")),
                         "--outcome", "handed-in", "--run-id", "r-4")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.rows()[-1]
        event = self.cost_events()[-1]
        self.assertEqual(event.get("ticket"), "1")
        for key in ("model", "tokens_in", "cache_write", "cache_read",
                    "outcome", "run_id", "session_id"):
            self.assertIn(key, event, "ticket.cost 事件沒帶 %s" % key)
        for key in sorted(set(row) - {"at"}):
            self.assertEqual(event.get(key), row[key], key)


class AutoFixRowsHaveOutcome(AutoFixBase):

    def test_c5a_a_worker_that_hands_in_is_handed_in_with_a_run_id(self):
        """C5(a) 假 worker 交 patch → 一輪一列,outcome==handed-in、run_id 非空。"""
        self.set_worker(WORKER_ROUND_ONE_COSTS.replace(
            "@ENVELOPE@", envelope("done", 21, 4242, 1000, 50000, duration_ms=9000)))
        self.ticket_ready()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "worker"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "handed-in")
        self.assertTrue(rows[0].get("run_id"), rows[0])

    def test_c5a_a_worker_that_hands_in_nothing_is_no_patch(self):
        """C5(a) 假 worker 不交 → outcome==no-patch。"""
        self.set_worker(WORKER_NEVER)
        self.ticket_ready()
        done = self.auto_fix()
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "no-patch")
        self.assertTrue(rows[0].get("run_id"), rows[0])


class TriageAndVerifierRows(TriageBase):

    def rows_of(self, role):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == role]

    def test_c5b_a_verifier_that_hands_in_is_handed_in(self):
        """C5(b) 驗證者交件 → verifier 列 outcome==handed-in。"""
        self.arm(worker_first="fix", needs_verifier=True, interface_fixed=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows_of("verifier")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "handed-in")

    def test_c5c_an_accepted_triage_writes_an_opener_row(self):
        """C5(c) 開題者判 accepted → 票上多一列 role==opener、outcome==accepted、round 1。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(worker_resume="fix", opener="accept")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.rows_of("opener")
        self.assertEqual(len(rows), 1, self.load_ticket("1").get("cost"))
        self.assertEqual((rows[0].get("outcome"), rows[0].get("round"), rows[0].get("model")),
                         ("accepted", 1, "opus"))

    def test_c5c_an_opener_without_a_triage_line_is_undecided(self):
        """C5(c) 開題者第一行不是 TRIAGE → outcome==undecided。"""
        self.arm()
        self.play(opener="garbage")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        rows = self.rows_of("opener")
        self.assertEqual(len(rows), 1, self.load_ticket("1").get("cost"))
        self.assertEqual(rows[0].get("outcome"), "undecided")


class ReviewRowsHaveOutcome(ReviewBase):

    def reviewer_rows(self):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "reviewer"]

    def test_c5d_a_pass_is_pass_with_the_review_run_id(self):
        """C5(d) review.sh pass 路 → reviewer 列 outcome==pass、run_id 非空。"""
        self.set_reviewer(REVIEWER_PASS_ENVELOPE)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.reviewer_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "pass")
        self.assertTrue(rows[0].get("run_id"), rows[0])

    def test_c5d_a_fail_is_fail(self):
        """C5(d) review.sh fail 路 → reviewer 列 outcome==fail(與 review.fail 同字)。"""
        self.set_reviewer(REVIEW_FAIL)
        done = self.review()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = self.reviewer_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "fail")


class LandGreenRow(LandBase):
    gate_stub = GATE_STUB_GREEN

    def test_c5e_a_green_land_writes_pass(self):
        """C5(e) land 綠路 → land 列 outcome==pass、run_id 非空。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "land"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "pass")
        self.assertTrue(rows[0].get("run_id"), rows[0])


class LandRedRow(LandBase):
    gate_stub = GATE_STUB_RED

    def test_c5e_a_red_land_still_writes_a_fail_row(self):
        """C5(e) 全套紅那條路 → 票上有一列 role==land、outcome==fail、wall_seconds 非 null。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        done = self.land("t1-good")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("land.fail", self.kinds())
        rows = [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "land"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("outcome"), "fail")
        self.assertIsNotNone(rows[0].get("wall_seconds"))


def new_row(role, rnd, tokens, wall, outcome):
    row = {"role": role, "round": rnd, "model": "opus" if role != "land" else None,
           "wall_seconds": wall, "outcome": outcome, "run_id": "r-%s-%s" % (role, rnd),
           "session_id": None, "by": "fixture", "note": None,
           "at": "2026-09-12T11:00:00+08:00"}
    row.update(zip(TOKENS, tokens))
    return row


# C6 票面預放的六列。最後一列是舊格式:沒有 outcome / run_id / session_id 鍵。
PRESET = [
    new_row("worker", 1, (10, 100, 1000, 5000), 60, "handed-in"),
    new_row("worker", 2, (5, 50, 200, 3000), 40, "handed-in"),
    new_row("reviewer", 2, (1, 10, 20, 300), 9, "fail"),
    new_row("reviewer", 2, (1, 12, 20, 310), 8, "pass"),
    new_row("land", None, (None, None, None, None), 30, "pass"),
    {"role": "verifier", "round": 1, "model": "opus", "tokens_in": None, "tokens_out": None,
     "cache_write": None, "cache_read": None, "wall_seconds": None, "by": "fixture",
     "note": "沒帶 --from-envelope", "at": "2026-09-12T11:00:00+08:00"},
]
CREATED = "2026-09-12T10:00:00+08:00"


class ClosableTicket:
    """C6 / C7 共用:一張真的關得掉的票(東西在主線、有回歸證據、有覆核),預放六列。

    C6 手算:
    worker   runs 2, wall 60+40=100, in 10+5=15, out 100+50=150, cw 1000+200=1200,
             cr 5000+3000=8000, unknown 0, outcomes {handed-in: 2}
    reviewer runs 2, wall 9+8=17, in 1+1=2, out 10+12=22, cw 20+20=40, cr 300+310=610,
             unknown 0, outcomes {fail: 1, pass: 1}
    land     runs 1, wall 30, 四欄全 null → None, unknown 1, outcomes {pass: 1}
    verifier runs 1, wall 全 null → None, 四欄 None, unknown 1, outcomes {"null": 1}
    總 wall 60+40+9+8+30 = 147;runs 6;worker_rounds max(1,2) = 2;objections 1(一筆不擋的 nit)。
    """

    def land_a_file(self):
        self.write("src/nav.py", "def size_nav():\n    return 42\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 src/nav.py 放進主線")

    def ready(self):
        self.make_ticket(1, created=CREATED, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         test_evidence=[{"cmd": "gate --branch", "rc": 0}],
                         objections=[{"category": "nit", "body": "變數名可以短一點",
                                      "evidence": "", "owner": "main", "disposition": ""}],
                         cost=[dict(row) for row in PRESET])
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                               ensure_ascii=False))

    def assert_cost_total(self, total):
        self.assertEqual(set(total), {"calendar_seconds", "wall_seconds", "runs",
                                      "worker_rounds", "objections", "by_role", "at"})
        self.assertEqual(total["wall_seconds"], 147)
        self.assertEqual(total["runs"], 6)
        self.assertEqual(total["worker_rounds"], 2)
        self.assertEqual(total["objections"], 1)
        by_role = total["by_role"]
        self.assertEqual(set(by_role), {"worker", "reviewer", "land", "verifier"})
        self.assertEqual(by_role["worker"], {
            "runs": 2, "wall_seconds": 100, "tokens_in": 15, "tokens_out": 150,
            "cache_write": 1200, "cache_read": 8000, "unknown_token_runs": 0,
            "outcomes": {"handed-in": 2}})
        self.assertEqual(by_role["reviewer"], {
            "runs": 2, "wall_seconds": 17, "tokens_in": 2, "tokens_out": 22,
            "cache_write": 40, "cache_read": 610, "unknown_token_runs": 0,
            "outcomes": {"fail": 1, "pass": 1}})
        land = by_role["land"]
        for key in TOKENS:
            self.assertIsNone(land[key], "land.%s 全 null 要是 None,不是 0" % key)
        self.assertEqual((land["runs"], land["wall_seconds"], land["unknown_token_runs"],
                          land["outcomes"]), (1, 30, 1, {"pass": 1}))
        verifier = by_role["verifier"]
        self.assertEqual(verifier["outcomes"], {"null": 1})
        self.assertIsNone(verifier["wall_seconds"])
        self.assertEqual(verifier["unknown_token_runs"], 1)
        expected = (parse_ts(total["at"]) - parse_ts(CREATED)).total_seconds()
        self.assertIsInstance(total["calendar_seconds"], int)
        self.assertLessEqual(abs(total["calendar_seconds"] - expected), 1)


class CloseWritesCostTotal(ClosableTicket, Sandbox):

    def test_c6_close_writes_cost_total_and_the_closed_event_carries_it(self):
        """C6 close → 票上 cost_total 逐格 == 手算;ticket.closed 帶同一物件,hits / weak / state_version 照舊。"""
        self.land_a_file()
        self.ready()
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "Done")
        self.assertIn("cost_total", ticket)
        self.assert_cost_total(ticket["cost_total"])
        closed = [row for row in self.events() if row["kind"] == "ticket.closed"]
        self.assertEqual(len(closed), 1, closed)
        self.assertEqual(closed[0].get("cost_total"), ticket["cost_total"])
        for key in ("hits", "weak", "state_version"):
            self.assertIn(key, closed[0])

    def test_c6_set_state_done_goes_through_the_same_write(self):
        """C6 `set <n> state Done` 走同一關 → 票上同樣有 cost_total。"""
        self.land_a_file()
        self.ready()
        done = self.ticket("set", "1", "state", "Done")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "Done")
        self.assertIn("cost_total", ticket)
        self.assert_cost_total(ticket["cost_total"])

    def test_c6_a_refused_close_writes_no_cost_total(self):
        """C6 反例:東西不在主線 → close 拒絕,票上沒有 cost_total、沒有 ticket.closed。

        先證同一張票在主線上有東西時關得掉且寫 cost_total(否則「沒有」是空真)。"""
        self.ready()
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertNotEqual(ticket["state"], "Done")
        self.assertNotIn("cost_total", ticket)
        self.assertNotIn("ticket.closed", self.kinds())
        self.land_a_file()
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("cost_total", self.load_ticket("1"))


class VersionsDoNotMove(ClosableTicket, CostBase):

    def test_c7_cost_leaves_state_version_and_review_alone_and_close_bumps_once(self):
        """C7 cost 帶新旗標也不動 state_version / review;close 只 +1 且同一次寫 cost_total。"""
        before = self.load_ticket("1")
        done = self.cost("--outcome", "handed-in", "--run-id", "r-7",
                         "--from-envelope", self.envelope_file(multi_model_envelope()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = self.load_ticket("1")
        self.assertEqual(after["state_version"], before["state_version"])
        self.assertEqual(after["review"], before["review"])

        self.land_a_file()
        self.ready()
        version = self.load_ticket("1")["state_version"]
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertIn("cost_total", ticket)
        self.assertEqual(ticket["state_version"], version + 1)


class TheContractIsWrittenDown(CostBase):

    def test_c8_schema_event_kinds_and_usage_name_the_new_shape(self):
        """C8 SCHEMA 成本列有 14 鍵與登記表、有 cost_total 列;event.py 註解;cost --help 列新旗標。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "tickets", "SCHEMA.md"), encoding="utf-8") as handle:
            schema = handle.read()
        cost_line = next((line for line in schema.splitlines()
                          if line.startswith("| 成本 |")), "")
        self.assertTrue(cost_line, "SCHEMA 沒有「成本」那一列")
        for key in sorted(ROW_KEYS) + REGISTERED_WORDS + ["modelUsage"]:
            self.assertIn(key, cost_line, "SCHEMA 成本列缺 %s" % key)
        self.assertTrue(any("`cost_total`" in line and line.startswith("|")
                            for line in schema.splitlines()), "SCHEMA 沒有 cost_total 那一列")
        with open(os.path.join(root, "scripts", "event.py"), encoding="utf-8") as handle:
            kinds = handle.read()
        self.assertIn("整列", kinds)
        self.assertIn("cost_total", kinds)
        done = self.ticket("cost", "--help")
        text = done.stdout + done.stderr
        self.assertIn("--outcome", text)
        self.assertIn("--run-id", text)


if __name__ == "__main__":
    unittest.main()
