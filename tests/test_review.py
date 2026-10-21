"""`scripts/review.sh`:閘門綠、票轉 InReview 之後自動派覆核者(#42,D-025 ②、D-022)。

D-022 裁「覆核由短命 opus 做」,三天只走過一次 —— 沒有派工範本、沒有觸發點,主線每次都得
自己組派工文,於是回到自己讀 patch。這一組問三件事:

1. reviewer 說 **pass** ⇒ 票的 `review` 由腳本寫,`by` 是 `reviewer@<reviewer.command 的 --model>`
   (不是 routing 標籤,#21)、`sha` 是分支頭;review 只有 {verdict, by, sha, note, at} 五格,
   不綁票版本(#136);
2. reviewer 說 **fail** ⇒ 逐條 blocking 反駁、票 Blocked、inbox 恰一頁 decision「覆核退回,
   裁示」,`review` 這一格不寫;
3. **沒交件不是 pass**(§5.5):沒有 `## result`、JSON 壞、逾時、verdict 不是 pass|fail ——
   不寫 review、票留 InReview、事件 review.missing(不發頁,D-032)、rc 非零。

**只派 complex 的票**(#136,VISION 流程 #9:只有複雜的票做完後檢查一次;沒標 = simple):
票 #1 的夾具標 `complexity=complex`,simple / 沒標走既有的 refuse。

reviewer 一律是假的可執行檔(`reviewer.command`)。**該替換的是代價,不是語意**:這一組
問的是腳本怎麼接它的產出,不是模型會不會覆核。期望的 sha 由夾具 `git rev-parse t1` 取、
期望的 by 由夾具寫進設定的 `--model` 拼,都不從 `review.sh` 算。
"""

import glob
import json
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (REVIEWER_PASS, REVIEWER_PASS_ENVELOPE,  # noqa: E402
                             SCRIPTS, Sandbox, fixture_candidate)

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
    """票 #1(complex)停在 InReview;分支 t1 有一個 commit、一個 worktree —— 閘門綠之後的
    真實形狀。只有 complex 的票做完後覆核(#136),所以夾具標 complex。"""

    def setUp(self):
        super().setUp()
        # #74:缺角色卡、指定的模型卡不在、或只剩標題的規則包現在非零,而且不起 agent。
        # 預設沙盒沒有那幾份(`control_harness` 只給要的測試裝),不裝的話每一條派工的案例
        # 都停在「規則包產不出來」。假 reviewer 的模型(`set_reviewer` 的 fixture-model)
        # 也要有一張卡 —— 指定了卻不存在是錯,不是「沒有記憶」。
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        self.make_ticket("1", state="InReview", complexity="complex")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.wt = self.worktree("t1")
        self.commit_in(self.wt, "thing.txt", "#1 的實作")
        self.sha = self.git("rev-parse", "t1").strip()

    def review(self, *args, ident="1"):
        return self.run_sh("scripts/review.sh", ident, *args)

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
    """A1:pass ⇒ 腳本寫 review;by / sha 各有各的來源,review 只有五格(#136)。"""

    def test_a_pass_writes_review_bound_to_the_branch_head_by_the_reviewer_model(self):
        """**變異 M1**:`review.sh` 的 by 寫死 `main` → 這一條紅。

        #72 併入同一趟 REVIEWER_PASS 的兩條:
        - 原 test_the_packet_names_the_four_things_and_goes_in_on_stdin:派工文 = 規則包 +
          範本;四件事都填上、**一個佔位都不剩**,而 reviewer 從 stdin 拿到的就是寫在
          reports 裡的那一份。佔位沒填與填了長得一樣 —— 除非有人數。
        """
        model = self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        ticket = self.load_ticket("1")
        review = ticket.get("review") or {}
        self.assertEqual(review.get("verdict"), "pass")
        self.assertEqual(review.get("by"), "reviewer@" + model,
                         "by 取自 reviewer.command 的 --model,不是 main、不是 routing 標籤")
        self.assertEqual(review.get("sha"), self.sha, "review 記的是分支 t1 的頭")
        # #136:review 只剩給人看的五格 —— 不綁票版本、內容雜湊、票面指紋,也不另記派工單。
        # **變異**:`set review` 照舊蓋 state_version → 這一條紅。
        self.assertEqual(sorted(review), ["at", "by", "note", "sha", "verdict"])
        self.assertEqual([key for key in ticket if key.startswith("review")], ["review"])
        self.assertIn("REVIEW.md", review.get("note") or "")
        self.assertEqual(ticket["state"], "InReview")
        # 通過之後是 land,不是主線的裁示:只寫事件,不發頁(D-032)。
        # **變異**:pass 照發頁 → 這一條紅。
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("REVIEW.md", self.last_event("review.pass").get("note") or "")

        packet = self.packet()
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

    def packet(self):
        with open(self.one("dispatch-reviewer.md"), encoding="utf-8") as handle:
            return handle.read()

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


class TheReviewerComesFromTheRoleTable(ReviewBase):
    """#121 A1:覆核者的 model / effort 由角色表選;`reviewer.command` 的 `--model` 被換掉。
    **M1**(model 照舊取 reviewer.command 的 `--model`)→ by 是 reviewer@fixture-model,紅。
    量 argv、by、agent.start、cost;值 = A1/A4;舊值紅在 by;表真的與命令不同。"""

    def test_the_table_model_and_effort_run_and_are_recorded(self):
        argv_file = os.path.join(self.home, "reviewer-argv.txt")
        self.set_reviewer("#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\n" % argv_file
                          + REVIEWER_PASS.split("\n", 1)[1])
        self.write(os.path.join("memory", "model", "fable.md"), "- 沙盒 fable 卡\n")
        self.set_role_models(reviewer=[fixture_candidate("fable", effort="high")])
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(argv_file, encoding="utf-8") as handle:
            argv = handle.read().splitlines()
        self.assertEqual(argv[argv.index("--model") + 1], "fable", argv)
        self.assertEqual(argv[argv.index("--effort") + 1], "high", argv)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["review"]["by"], "reviewer@fable")
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual([(row["model"], row.get("effort")) for row in starts], [("fable", "high")])
        cost = [row for row in ticket["cost"] if row["role"] == "reviewer"]
        self.assertEqual([(row["model"], row["effort"]) for row in cost], [("fable", "high")])


class AnUnavailableReviewerIsSkippedBeforeStarting(ReviewBase):
    """#121 A2「CLI 已知不可用可於啟動前選下一候選(reviewer 亦然)」:表首選 codex、codex 不在
    PATH → 換 fable,不起 codex。**變異**:拿掉 review.sh 的 PATH 檢查 → 命令是 codex exec,
    替身 reviewer 不被叫、by 不是 reviewer@fable,紅。"""

    def test_codex_missing_takes_the_next_reviewer(self):
        self.set_reviewer(REVIEWER_PASS)
        self.write(os.path.join("memory", "model", "fable.md"), "- 沙盒 fable 卡\n")
        self.set_role_models(reviewer=[fixture_candidate("codex:gpt-6-astra", "codex"),
                                       fixture_candidate("fable")])
        path = os.pathsep.join([self.stub_bin] + [
            where for where in os.environ.get("PATH", "").split(os.pathsep)
            if where and not os.path.exists(os.path.join(where, "codex"))])
        done = self.run_sh("scripts/review.sh", "1", env=self.env(PATH=path))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("codex 不在 PATH", done.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        self.assertEqual(self.load_ticket("1")["review"]["by"], "reviewer@fable")


class ThePacketOnlySaysTheGateRanWhenItDid(ReviewBase):
    """#56(D-H38):「已對 <sha> 跑過閘門」只在票上 `gate.sha` 等於分支頭時寫。範本以前無條件
    說「閘門已經對這個 sha 跑過」,而 auto-fix 在副本的綠沒寫回票 —— 覆核者被告知不必看紅榜,
    那個 sha 其實沒有人跑過(#661)。期望的 sha 由夾具 `git rev-parse t1` 取。"""

    def packet_with_gate(self, gate):
        if gate is not None:
            done = self.run_py("scripts/ticket.py", "set", "1", "gate",
                               json.dumps(gate, ensure_ascii=False))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.one("dispatch-reviewer.md"), encoding="utf-8") as handle:
            return handle.read()

    def assert_not_ran(self, packet):
        self.assertIn("這個 sha 沒跑過閘門,覆核者要自己看紅榜", packet)
        self.assertNotIn("已對 %s 跑過閘門" % self.sha, packet)
        self.assertIsNone(re.search(r"已對 \S+ 跑過閘門", packet), "任何一個 sha 都不准說跑過")
        self.assertNotIn("閘門已經對這個 sha 跑過", packet,
                         "範本那一句「閘門已經對這個 sha 跑過」也要拿掉,不然兩句互相矛盾")

    def test_a_gate_on_an_older_sha_says_this_sha_did_not_run(self):
        """**變異**:比對改成「有 gate 欄就算跑過」(`if gate:`)→ 這一條紅。"""
        older = self.git("rev-parse", "main").strip()
        self.assertNotEqual(older, self.sha)
        packet = self.packet_with_gate({"rc": 0, "sha": older, "run_id": "20260928-100000-1",
                                        "at": "2026-09-28T10:00:00+08:00"})
        self.assert_not_ran(packet)
        self.assertIn(older[:12], packet, "說得出票上那一個 sha 是哪一個")

    def test_a_ticket_with_no_gate_says_this_sha_did_not_run(self):
        self.assert_not_ran(self.packet_with_gate(None))

    def test_a_gate_on_the_branch_head_keeps_the_packet_as_before(self):
        """**變異**:比對寫反(`!=`)→ 這一條與上面兩條一起紅。"""
        packet = self.packet_with_gate({"rc": 0, "sha": self.sha, "run_id": "20260928-100000-1",
                                        "at": "2026-09-28T10:00:00+08:00"})
        self.assertIn("已對 %s 跑過閘門" % self.sha, packet)
        self.assertNotIn("沒跑過閘門", packet)
        self.assertIn("閘門已經對這個 sha 跑過", packet, "範本原文照舊")
        self.assertEqual(re.findall(r"@[A-Z_]+@", packet), [], "範本的佔位沒填完")


class TheReviewerCostGoesOnTheTicket(ReviewBase):
    """#49 A5 / A3:覆核者回來之後 `ticket.py cost --role reviewer` 記一筆;期望的數字是
    夾具信封裡寫死的那幾個(`REVIEWER_PASS_ENVELOPE`),不從 ticket.py 算回去。"""

    def test_a_pass_records_one_reviewer_row_and_the_review_still_holds(self):
        """寫了 cost 之後 review 照樣過(#136 起 review 不綁票版本)。"""
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
        self.assertEqual(review_problems(ticket), [],
                         "寫了 cost 之後 land 對 review 的檢查要照樣過")


class TheReviewerRowSaysHowItEnded(ReviewBase):
    """#94 C5(d):reviewer 列的 outcome 與它發的 review.* 同字,run_id 是 review 的 RUN_ID。"""

    def test_c5d_a_pass_is_outcome_pass_with_the_run_id(self):
        """**變異**:review.sh 的 pass 路 `write_cost pass` 改成 `write_cost fail` → 這一條紅。"""
        self.set_reviewer(REVIEWER_PASS_ENVELOPE)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row, = self.load_ticket("1").get("cost") or []
        self.assertEqual(row["outcome"], "pass")
        passed = [e for e in self.events() if e["kind"] == "review.pass"][-1]
        self.assertEqual(row["run_id"], passed["run_id"])

class ItLeavesEarlierPagesAlone(ReviewBase):
    """D-032 之後沒有「等覆核」頁可收:review.sh 不再 ack 任何一頁,舊頁留給主線收。"""

    def test_a_review_neither_acks_nor_adds_a_page(self):
        done = self.run_py("scripts/inbox.py", "post", "--ticket", "1", "--run-id", "r1",
                           "--kind", "decision", "--state", "第 1 輪綠了,等覆核", "--what", "x")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([(row["state"], row["acked"]) for row in self.inbox_rows()],
                         [("第 1 輪綠了,等覆核", False)])


class TheFailPath(ReviewBase):
    """A2:fail ⇒ 每條理由一筆 blocking 反駁、票 Blocked、一頁「覆核退回,裁示」。"""

    def test_a_fail_turns_each_reason_into_a_blocking_objection_and_blocks_the_ticket(self):
        """**變異 M2**:fail 那條路也寫 `review.verdict=pass` → 這一條紅。

        #72 併入原 TheReviewerCostGoesOnTheTicket.test_a_fail_records_one_reviewer_row:
        fail 也記一筆 reviewer 的 cost。
        """
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
        self.assertEqual([row["role"] for row in ticket.get("cost") or []], ["reviewer"])

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
        """#72 併入原 TheReviewerCostGoesOnTheTicket.
        test_a_reviewer_that_delivers_nothing_still_costs_a_row_of_nulls:沒交件也算派過
        一次 —— 每一種照樣一筆,token 欄是 null(拿不到,不是 0)。同一個沙盒連派四次,
        所以第 n 種之後是 n 筆。"""
        for count, (name, body, timeout) in enumerate(NOT_DELIVERED, 1):
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
                rows = ticket.get("cost") or []
                self.assertEqual([row["role"] for row in rows], ["reviewer"] * count)
                self.assertIsNone(rows[-1]["tokens_out"])
                self.assertIsInstance(rows[-1]["wall_seconds"], int)


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

    def test_a_simple_or_unmarked_ticket_is_refused_before_any_reviewer_runs(self):
        """#136(VISION 流程 #9:只有複雜的票做完後檢查一次;沒標 = simple):simple 與沒有
        complexity 這一格的票都走既有的 refuse —— rc=2、review.refused、reviewer 沒被叫、
        review 不寫、不發頁,不是 crash。

        量 rc / reviewer 叫了幾次 / review 格 / 事件與頁;紅 = review.sh 不看 complexity
        (M1:reviewer 被叫、rc=0);期望值 = VISION 流程 #9 與 D-054 Q1 原話,不從 review.sh 算。
        """
        self.set_reviewer(REVIEWER_PASS)
        for name, fields in (("simple", {"complexity": "simple"}), ("沒標", {})):
            with self.subTest(name):
                self.make_ticket("1", state="InReview", **fields)
                done = self.review()
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                self.assertEqual(self.reviewer_calls(), [])
                self.assertIsNone(self.load_ticket("1").get("review"))
                self.assertEqual(self.inbox_rows(), [])
                note = self.last_event("review.refused").get("note") or ""
                self.assertIn("不是 complex", note)
                self.assertIn("不是 complex", done.stderr)
                self.assertNotIn("agent.start", self.kinds())

    def test_a_ticket_awaiting_review_is_reviewed_too(self):
        """#49 A8:下游閘門綠寫的是 `AwaitingReview`(A 自己仍寫 InReview)—— 兩個都派。

        **變異**:守衛只認 InReview → 這一條紅(rc=2、reviewer 沒被叫)。
        """
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("1", state="AwaitingReview", complexity="complex")
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        self.assertEqual((self.load_ticket("1").get("review") or {}).get("verdict"), "pass")

    def test_a_ticket_without_its_branch_is_refused_by_name(self):
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("2", state="InReview", complexity="complex")
        done = self.review(ident="2")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.reviewer_calls(), [])
        self.assertEqual(self.inbox_rows(), [])
        self.assertIn("分支 t2 不存在", self.last_event("review.refused").get("note") or "")

    def test_the_branch_named_on_the_ticket_is_the_one_reviewed(self):
        """#53 D3(D-018):分支名**只有一個來源 —— 票的 `branch` 欄**。票面 branch=t2-gaps
        而且只有這一條(沒有 t2):不說分支不存在,review 綁的是 t2-gaps 的頭。"""
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("2", state="InReview", branch="t2-gaps", complexity="complex")
        wt = self.worktree("t2-gaps")
        self.commit_in(wt, "gaps.txt", "#2 的實作")
        done = self.review(ident="2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("不存在", done.stderr)
        self.assertEqual((self.load_ticket("2").get("review") or {}).get("sha"),
                         self.git("rev-parse", "t2-gaps").strip())


# #87 A14:reviewer 起一個 `setsid()` 自開 session 的孫行程(每 0.2 秒往 heartbeat 追加一行),
# 自己睡過 `reviewer.timeout_seconds`。
REVIEWER_GRANDCHILD = """#!/bin/sh
python3 - "@HB@" "@PIDF@" > /dev/null 2>&1 <<'GRAND' &
import os, sys, time
os.setsid()
heartbeat, pid_file = sys.argv[1:3]
with open(pid_file + ".tmp", "w") as handle:
    handle.write(str(os.getpid()))
os.rename(pid_file + ".tmp", pid_file)
while True:
    with open(heartbeat, "a") as handle:
        handle.write("beat\\n")
    time.sleep(0.2)
GRAND
while [ ! -s "@PIDF@" ]; do sleep 0.1; done
exec sleep 60
"""


class ATimedOutReviewerIsReapedWithItsTree(ReviewBase):
    """#87 A14 reviewer 路徑:逾時收整棵子孫樹。返回後等 2 秒,heartbeat 不再長、
    孫行程 `kill -0` 失敗。

    **變異**:reviewer 逾時只 killpg 那一組 → 這一條紅。
    """

    def test_a14_the_reviewer_grandchild_in_its_own_session_is_reaped(self):
        hb = os.path.join(self.home, "heartbeat.log")
        pidf = os.path.join(self.home, "grandchild.pid")
        self.set_reviewer(REVIEWER_GRANDCHILD.replace("@HB@", hb).replace("@PIDF@", pidf),
                          timeout=3)
        done = self.review()
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("reviewer 超過 3 秒", done.stderr)
        self.assertTrue(os.path.exists(pidf), "孫行程沒起來 —— 這一條什麼都沒驗")
        with open(pidf, encoding="utf-8") as handle:
            pid = int(handle.read())
        self.addCleanup(self.reap, pid)
        time.sleep(2)
        before = self.beats(hb)
        time.sleep(1)
        self.assertEqual(self.beats(hb), before, "逾時之後 heartbeat 還在長")
        self.assertFalse(self.alive(pid), "自開 session 的孫行程 %d 還活著" % pid)

    @staticmethod
    def beats(path):
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            return len(handle.read().splitlines())

    @staticmethod
    def alive(pid):
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            time.sleep(0.1)
        return True

    @staticmethod
    def reap(pid):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass



class ReviewBatch(Sandbox):
    """#108 A5 / A6,#145:`review.sh --batch land/X 1 2 --run-id <stamp> [--base <sha>]` 覆核批次 worker
    在整合分支上的 commit(`<base>..<頭>`;沒給 base = first-parent 上最近一個 merge)。pass 寫 `reports/batch/<stamp>/review.json`(sha = land/X 頭,不是哪一張票的
    分支頭);fail 不寫票、收件匣一頁 decision 掛票 1、內文列 #2。成本一列只記在票 1。
    期望的 sha 由夾具 `git rev-parse land/X` 取,不從 review.sh 算。"""

    STAMP = "20261010-130000-9"
    RUN = "20261010-120000-1"

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒:規則包的來源")
        self.batch = self.make_batch(run_id=self.RUN)
        land = self.worktree_of("land/X")
        self.write("src/one.txt", "one fixed by the batch\n", where=land)
        self.git("add", "-A", cwd=land)
        self.git("commit", "-q", "-m", "t1 fix: one fixed by the batch", cwd=land)
        self.fix = self.git("rev-parse", "land/X").strip()
        self.before = {ident: self.load_ticket(ident) for ident in ("1", "2")}

    def review(self, *extra):
        return self.run_sh("scripts/review.sh", "--batch", "land/X", "1", "2",
                           "--run-id", self.STAMP, *extra, env=self.env(AC_BATCH_RUN_ID=self.RUN))

    def record(self, name):
        return os.path.join(self.repo, "reports", "batch", self.STAMP, name)

    def assert_tickets_kept(self):
        for ident, row in self.before.items():
            now = self.load_ticket(ident)
            for key in ("state_version", "review", "state", "objections"):
                self.assertEqual(now.get(key), row.get(key), "#%s 的 %s 被寫了" % (ident, key))

    def test_pass_writes_review_json_bound_to_the_land_head(self):
        """A5 pass。量 review.json 的 verdict / sha / by / at / note、票不動;
        紅 = sha 寫成票分支頭或寫進票;期望值 = 夾具 rev-parse land/X 與設定的 --model。

        **變異 ④**:review --batch 把 sha 寫成票 1 分支頭 → sha 那一條紅。
        """
        model = self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.record("review.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["verdict"], "pass")
        self.assertEqual(data["sha"], self.fix)
        self.assertNotEqual(data["sha"], self.batch["shas"]["t1-a"])
        self.assertEqual(data["by"], "reviewer@%s" % model)
        self.assertTrue(data["at"])
        self.assertIn("REVIEW.md", data["note"])
        self.assert_tickets_kept()

    def test_the_packet_is_the_fix_diff_plus_every_ticket(self):
        """A5 派工文。量派工文帶 fix commit 的 diff 行與兩張票的 subject / acceptance;
        紅 = 只帶第一張票或帶整批 diff;期望值 = 夾具寫的那一行與票檔。"""
        self.set_reviewer(REVIEWER_PASS)
        self.assertEqual(self.review().returncode, 0)
        text = self.read(self.record("dispatch-reviewer.md"))
        self.assertIn("+one fixed by the batch", text)
        self.assertNotIn("+two by #2", text, "派工文帶的該是 fix commit 前後,不是整批 diff")
        for ident in ("1", "2"):
            row = self.before[ident]
            self.assertIn(row["subject"], text)
            for item in row["acceptance"]:
                self.assertIn(item, text)

    def test_fail_pages_ticket_one_listing_the_batch_and_writes_nothing(self):
        """A5 fail。量 rc≠0、review.json 不在、恰一頁 decision ticket=1 且內文有 #2、票不動;
        紅 = 寫票轉 Blocked 或沒發頁;期望值 = 票號來自命令列。"""
        self.set_reviewer(REVIEW_FAIL)
        done = self.review()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.record("review.json")))
        rows = [row for row in self.inbox_rows() if row.get("kind") == "decision"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(str(rows[0]["ticket"]), "1")
        self.assertIn("#2", rows[0]["what"])
        self.assert_tickets_kept()

    def test_the_cost_row_lands_on_ticket_one_only(self):
        """A6。量票 1 多一列 role=reviewer、note 帶 batch <stamp> 與 #1 #2、run_id = AC_BATCH_RUN_ID;
        票 2 的 cost 不變;紅 = 每張各記一列或沒標批次;期望值 = 測試給的 stamp / run。"""
        self.set_reviewer(REVIEWER_PASS)
        self.assertEqual(self.review().returncode, 0)
        rows = self.load_ticket("1").get("cost") or []
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["role"], "reviewer")
        self.assertIn("batch %s" % self.STAMP, rows[0]["note"])
        self.assertIn("#1 #2", rows[0]["note"])
        self.assertEqual(rows[0]["run_id"], self.RUN)
        self.assertEqual(self.load_ticket("2").get("cost"), self.before["2"].get("cost"))

    def test_the_packet_spans_every_commit_since_the_base(self):
        """#145:worker 可以 commit 不只一次;派工文的 diff 是 `<base>..<頭>`,不是頭那一個。
        量兩個 commit 的改動都在、`--base` 給到第一個之後就只剩第二個;紅 = 照舊 `<頭>^..<頭>`;
        期望值 = 夾具寫的兩行。"""
        self.set_reviewer(REVIEWER_PASS)
        land = os.path.join(self.home, "wt", "land-X")
        self.write("src/two.txt", "two fixed by the batch\n", where=land)
        self.git("add", "-A", cwd=land)
        self.git("commit", "-q", "-m", "t2 fix: two fixed by the batch", cwd=land)
        self.assertEqual(self.review().returncode, 0)
        text = self.read(self.record("dispatch-reviewer.md"))
        self.assertIn("+one fixed by the batch", text)
        self.assertIn("+two fixed by the batch", text)
        self.assertEqual(self.review("--base", self.fix).returncode, 0)
        text = self.read(self.record("dispatch-reviewer.md"))
        self.assertNotIn("+one fixed by the batch", text)
        self.assertIn("+two fixed by the batch", text)

    def test_a_base_off_the_branch_is_refused(self):
        """#145 前提:`--base` 不在整合分支的歷史上 ⇒ rc 2、reviewer 不起;期望值 = 夾具做的一個孤兒 commit。"""
        self.set_reviewer(REVIEWER_PASS)
        orphan = self.git("commit-tree", "main^{tree}", "-m", "孤兒").strip()
        done = self.review("--base", orphan)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.log), "reviewer 不該被起")

    def test_a_head_that_is_not_a_batch_fix_is_refused(self):
        """前提:頭是 merge(最近一個 merge 之後沒有批次修復的 commit)⇒ rc 2、reviewer 不起。"""
        self.set_reviewer(REVIEWER_PASS)
        land = os.path.join(self.home, "wt", "land-X")
        side = self.git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "side", cwd=land).strip()
        self.git("merge", "-q", "--no-ff", "-m", "merge side", side, cwd=land)
        done = self.review()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.log), "reviewer 不該被起")



if __name__ == "__main__":
    unittest.main()
