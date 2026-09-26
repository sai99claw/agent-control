"""`scripts/review.sh`:閘門綠、票轉 InReview 之後自動派覆核者(#42,D-025 ②、D-022)。

D-022 裁「覆核由短命 opus 做」,三天只走過一次 —— 沒有派工範本、沒有觸發點,主線每次都得
自己組派工文,於是回到自己讀 patch。這一組問三件事:

1. reviewer 說 **pass** ⇒ 票的 `review` 由腳本寫,`by` 是 `reviewer@<reviewer.command 的 --model>`
   (不是 routing 標籤,#21)、`sha` 是分支頭、`state_version` 由 `ticket.py set` 蓋;
2. reviewer 說 **fail** ⇒ 逐條 blocking 反駁、票 Blocked、inbox 恰一頁 decision「覆核退回,
   裁示」,`review` 這一格不寫;
3. **沒交件不是 pass**(§5.5):沒有 `## result`、JSON 壞、逾時、verdict 不是 pass|fail ——
   不寫 review、票留 InReview、事件 review.missing(不發頁,D-032)、rc 非零。

reviewer 一律是假的可執行檔(`reviewer.command`)。**該替換的是代價,不是語意**:這一組
問的是腳本怎麼接它的產出,不是模型會不會覆核。期望的 sha 由夾具 `git rev-parse t1` 取、
期望的 by 由夾具寫進設定的 `--model` 拼,都不從 `review.sh` 算。
"""

import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (REVIEWER_PASS, REVIEWER_PASS_ENVELOPE,  # noqa: E402
                             SCRIPTS, Sandbox)

sys.path.insert(0, SCRIPTS)
from ticket import review_problems  # noqa: E402


def reviewer_printing(text, then=""):
    """一支假 reviewer:記一行被叫到、印 `text`,再做 `then`(例如睡過逾時)。"""
    return ('#!/bin/sh\necho "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"\n'
            "cat <<'EOF'\n%s\nEOF\n%s" % (text, then))


def result_block(verdict, objection="null"):
    return ("## result\n\n```result\n"
            '{"ticket": "1", "role": "reviewer", "round": 1, "verdict": %s,\n'
            ' "rc": null, "patch_sha256": null, "gate": null, "mutations": [],\n'
            ' "objection": %s, "excluded": [], "repro": null, "memory": []}\n'
            "```" % (json.dumps(verdict, ensure_ascii=False), objection))


FAIL_REASONS = ("A2 的 fail 路沒有轉 Blocked(review.sh 沒有 set state)",
                "範圍外動了 scripts/land.sh")

REVIEW_FAIL = reviewer_printing(
    "# 覆核\n① verdict:fail\n④ 疑慮\n"
    + "".join("OBJECTION: blocking %s\n" % reason for reason in FAIL_REASONS)
    + "- 不阻擋:變數名可以再短一點\n\n"
    + result_block("fail", json.dumps({"category": "blocking", "body": FAIL_REASONS[0]},
                                      ensure_ascii=False)))

# verdict 說 pass,阻擋那一行卻寫了 —— 以 `OBJECTION:` 那一行為準(§8.5)。
REVIEW_PASS_BUT_OBJECTS = reviewer_printing(
    "# 覆核\n① verdict:pass\nOBJECTION: blocking 驗收 A4 沒有案例守著\n\n" + result_block("pass"))

# 四種「沒交」。每一種的內文都**說了 pass**:判準若是「文字裡有沒有 pass」,四種全會放行。
NOT_DELIVERED = (
    ("no-block", reviewer_printing("# 覆核\nverdict: pass\n(忘了檔尾那一塊)"), 60),
    ("bad-json", reviewer_printing(
        "# 覆核\n\n## result\n\n```result\n{\"verdict\": \"pass\", 這裡少了引號}\n```"), 60),
    ("逾時", reviewer_printing("# 覆核\n\n" + result_block("pass"), then="sleep 4\n"), 1),
    ("verdict=", reviewer_printing("# 覆核\n\n" + result_block("看起來不錯")), 60),
)


class ReviewBase(Sandbox):
    """票 #1 停在 InReview;分支 t1 有一個 commit、一個 worktree —— 閘門綠之後的真實形狀。"""

    def setUp(self):
        super().setUp()
        self.make_ticket("1", state="InReview")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.wt = self.worktree("t1")
        self.commit_in(self.wt, "thing.txt", "#1 的實作")
        self.sha = self.git("rev-parse", "t1").strip()

    def review(self, *args, ident="1"):
        return self.run_sh("scripts/review.sh", ident, *args)

    def inbox_rows(self):
        path = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def reviewer_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("reviewer ran")]

    def last_event(self, kind):
        rows = [row for row in self.events() if row["kind"] == kind]
        self.assertTrue(rows, "沒有 %s 事件" % kind)
        return rows[-1]

    def one(self, name):
        found = glob.glob(os.path.join(self.repo, "reports", "t1", "*", name))
        self.assertEqual(len(found), 1, "找不到(或不只一份)%s:%s" % (name, found))
        return found[0]


class ThePassPath(ReviewBase):
    """A1:pass ⇒ 腳本寫 review;by / sha / state_version 各有各的來源。"""

    def test_a_pass_writes_review_bound_to_the_branch_head_by_the_reviewer_model(self):
        """**變異 M1**:`review.sh` 的 by 寫死 `main` → 這一條紅。"""
        model = self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        ticket = self.load_ticket("1")
        review = ticket.get("review") or {}
        self.assertEqual(review.get("verdict"), "pass")
        self.assertEqual(review.get("by"), "reviewer@" + model,
                         "by 取自 reviewer.command 的 --model,不是 main、不是 routing 標籤")
        self.assertEqual(review.get("sha"), self.sha, "review 綁的是分支 t1 的頭")
        self.assertEqual(review.get("state_version"), ticket["state_version"],
                         "state_version 由 ticket.py set 蓋,land 比得上")
        self.assertIn("REVIEW.md", review.get("note") or "")
        self.assertEqual(ticket["state"], "InReview")
        # 通過之後是 land,不是主線的裁示:只寫事件,不發頁(D-032)。
        # **變異**:pass 照發頁 → 這一條紅。
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("REVIEW.md", self.last_event("review.pass").get("note") or "")

    def test_the_packet_names_the_four_things_and_goes_in_on_stdin(self):
        """派工文 = 規則包 + 範本;四件事都填上、**一個佔位都不剩**,而 reviewer 從 stdin
        拿到的就是寫在 reports 裡的那一份。佔位沒填與填了長得一樣 —— 除非有人數。"""
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.one("dispatch-reviewer.md"), encoding="utf-8") as handle:
            packet = handle.read()
        with open(os.path.join(self.home, "reviewer-stdin.md"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), packet, "reviewer 的 stdin 就是那一份派工文")
        self.assertEqual(re.findall(r"@[A-Z_]+@", packet), [], "範本的佔位沒填完")
        self.assertIn(self.sha, packet)
        self.assertIn("## result", packet)
        self.assertIn('"role": "reviewer"', packet)
        line = [row for row in packet.splitlines() if "worktree 路徑" in row][0]
        self.assertTrue(os.path.samefile(line.split("`")[1], self.wt), line)
        self.assertTrue(os.path.exists(self.one("REVIEW.md")))
        self.assertTrue(os.path.exists(self.one("result-reviewer-round1.json")))

    def test_the_default_json_envelope_is_unwrapped_before_the_result_is_read(self):
        """預設命令帶 `--output-format json`:stdout 是一個信封,全文在 `result` 裡、換行是
        `\\n`。不拆開的話 `## result` 不在行首,**每一次真的覆核都會被判成沒交件**。"""
        text = ("# 覆核\n① verdict:pass\n\n" + result_block("pass"))
        envelope = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                               "result": text, "usage": {"output_tokens": 42}},
                              ensure_ascii=False)
        self.set_reviewer(reviewer_printing(envelope))
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.load_ticket("1").get("review") or {}).get("verdict"), "pass")
        with open(self.one("REVIEW.md"), encoding="utf-8") as handle:
            self.assertIn("## result", handle.read().splitlines())
        with open(self.one("reviewer.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["usage"]["output_tokens"], 42)


class TheReviewerCostGoesOnTheTicket(ReviewBase):
    """#49 A5 / A3:覆核者回來之後 `ticket.py cost --role reviewer` 記一筆;期望的數字是
    夾具信封裡寫死的那幾個(`REVIEWER_PASS_ENVELOPE`),不從 ticket.py 算回去。"""

    def test_a_pass_records_one_reviewer_row_and_the_review_still_binds(self):
        """**變異**:把 `write_cost` 搬到 `set review` 之前、且 cost 走 `state_version +1`
        → review 綁的版本對不上票,`review_problems` 那一條紅。"""
        model = self.set_reviewer(REVIEWER_PASS_ENVELOPE)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        rows = ticket.get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["role"], "reviewer")
        self.assertEqual(row["model"], model)
        self.assertEqual(row["by"], "review.sh")
        self.assertEqual(row["round"], 1, "round 是票的 attempt")
        self.assertEqual(row["tokens_out"], 3601)
        self.assertEqual(row["tokens_in"], 12)
        self.assertEqual(row["cache_write"], 34386)
        self.assertEqual(row["cache_read"], 218504)
        self.assertIsInstance(row["wall_seconds"], int)
        self.assertEqual(review_problems(ticket, self.sha), [],
                         "寫了 cost 之後 land 對 review 的綁定檢查要照樣過")
        self.assertIn("ticket.cost", self.kinds())

    def test_a_reviewer_that_delivers_nothing_still_costs_a_row_of_nulls(self):
        """沒交件也算派過一次:照樣一筆,token 欄是 null(拿不到,不是 0)。"""
        self.set_reviewer(reviewer_printing("# 覆核\n(忘了檔尾那一塊)"))
        done = self.review()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual([row["role"] for row in rows], ["reviewer"])
        self.assertIsNone(rows[0]["tokens_out"])
        self.assertIsInstance(rows[0]["wall_seconds"], int)

    def test_a_fail_records_one_reviewer_row(self):
        self.set_reviewer(REVIEW_FAIL)
        done = self.review()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual([row["role"] for row in rows], ["reviewer"])


class ItLeavesEarlierPagesAlone(ReviewBase):
    """D-032 之後沒有「等覆核」頁可收:review.sh 不再 ack 任何一頁,舊頁留給主線收。"""

    def test_a_review_neither_acks_nor_adds_a_page(self):
        done = self.run_py("scripts/inbox.py", "post", "--ticket", "1", "--run-id", "r1",
                           "--kind", "decision", "--state", "第 1 輪綠了,等覆核", "--what", "x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(self.repo, "reports", "inbox", "acked.jsonl")))
        self.assertEqual([row["state"] for row in self.inbox_rows()], ["第 1 輪綠了,等覆核"])


class TheFailPath(ReviewBase):
    """A2:fail ⇒ 每條理由一筆 blocking 反駁、票 Blocked、一頁「覆核退回,裁示」。"""

    def test_a_fail_turns_each_reason_into_a_blocking_objection_and_blocks_the_ticket(self):
        """**變異 M2**:fail 那條路也寫 `review.verdict=pass` → 這一條紅。"""
        model = self.set_reviewer(REVIEW_FAIL)
        done = self.review()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertIsNone(ticket.get("review"), "fail 不寫 review —— land 收到的會是一張假章")
        self.assertEqual(ticket["state"], "Blocked")
        rows = ticket.get("objections") or []
        self.assertEqual([row.get("body") for row in rows], list(FAIL_REASONS))
        for row in rows:
            self.assertEqual(row.get("category"), "blocking")
            self.assertEqual(row.get("owner"), "reviewer@" + model)
            self.assertEqual(row.get("disposition"), "", "沒處置 —— land 與 close 因此拒絕")
            self.assertTrue(row.get("evidence", "").endswith("REVIEW.md"), row)
        rows = self.inbox_rows()
        self.assertEqual([row["kind"] for row in rows], ["decision"], "恰一頁 decision")
        self.assertIn("覆核退回", rows[0]["state"])
        self.assertIn("裁示", rows[0]["what"])
        self.assertIn("review.fail", self.kinds())
        self.assertIn("decision.asked", self.kinds())

    def test_a_pass_with_an_objection_line_is_read_as_a_fail(self):
        """verdict 說 pass、阻擋那一行卻寫了:放行的話那一行等於沒人收(§8.5 以行為準)。"""
        self.set_reviewer(REVIEW_PASS_BUT_OBJECTS)
        done = self.review()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertIsNone(ticket.get("review"))
        self.assertEqual(ticket["state"], "Blocked")
        self.assertEqual([row.get("body") for row in ticket.get("objections") or []],
                         ["驗收 A4 沒有案例守著"])


class NotDeliveredIsNotAPass(ReviewBase):
    """A4:四種沒交,每一種都**說了 pass**,每一種都不准變成 pass。"""

    def test_no_block_bad_json_timeout_and_an_unknown_verdict_all_leave_it_in_review(self):
        for name, body, timeout in NOT_DELIVERED:
            with self.subTest(name):
                self.set_reviewer(body, timeout=timeout)
                done = self.review()
                self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
                ticket = self.load_ticket("1")
                self.assertIsNone(ticket.get("review"), "沒交件寫出了 review")
                self.assertEqual(ticket["state"], "InReview")
                self.assertEqual(ticket.get("objections") or [], [])
                self.assertEqual(self.inbox_rows(), [], "沒交件只寫事件,不發頁")
                note = self.last_event("review.missing").get("note") or ""
                self.assertIn(name, note, "四種沒交要說得出是哪一種")
                self.assertIn("review.sh 1", note)


class ItOnlyReviewsWhatIsReadyForIt(ReviewBase):
    """前提不成立就不起 reviewer,並且留一頁說下一步 —— 靜靜地退出與沒被叫長得一樣。"""

    def test_a_ticket_that_is_not_in_review_is_refused_before_any_reviewer_runs(self):
        self.set_reviewer(REVIEWER_PASS)
        self.ticket("set", "1", "state", "Running")
        done = self.review()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), [])
        self.assertIsNone(self.load_ticket("1").get("review"))
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("不是 InReview / AwaitingReview",
                      self.last_event("review.refused").get("note") or "")
        self.assertIn("不是 InReview / AwaitingReview", done.stderr)

    def test_a_ticket_awaiting_review_is_reviewed_too(self):
        """#49 A8:下游閘門綠寫的是 `AwaitingReview`(A 自己仍寫 InReview)—— 兩個都派。

        **變異**:守衛只認 InReview → 這一條紅(rc=2、reviewer 沒被叫)。
        """
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("1", state="AwaitingReview")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        self.assertEqual((self.load_ticket("1").get("review") or {}).get("verdict"), "pass")

    def test_a_ticket_without_its_branch_is_refused_by_name(self):
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("2", state="InReview")
        done = self.review(ident="2")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), [])
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("分支 t2 不存在", self.last_event("review.refused").get("note") or "")

    def test_the_branch_named_on_the_ticket_is_the_one_reviewed(self):
        """#53 D3(D-018):分支名**只有一個來源 —— 票的 `branch` 欄**。票面 branch=t2-gaps
        而且只有這一條(沒有 t2):不說分支不存在,review 綁的是 t2-gaps 的頭。"""
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("2", state="InReview", branch="t2-gaps")
        wt = self.worktree("t2-gaps")
        self.commit_in(wt, "gaps.txt", "#2 的實作")
        done = self.review(ident="2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("不存在", done.stderr)
        self.assertEqual((self.load_ticket("2").get("review") or {}).get("sha"),
                         self.git("rev-parse", "t2-gaps").strip())


if __name__ == "__main__":
    unittest.main()
