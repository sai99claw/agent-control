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
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (FACE_ORACLE, REVIEWER_PASS,  # noqa: E402
                             REVIEWER_PASS_ENVELOPE, SCRIPTS, TIMEOUT, Sandbox,
                             fixture_candidate, fixture_patch_id, oracle_face)

sys.path.insert(0, SCRIPTS)
import ticket as ticket_mod  # noqa: E402
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
        # #74:缺角色卡、指定的模型卡不在、或只剩標題的規則包現在非零,而且不起 agent。
        # 預設沙盒沒有那幾份(`control_harness` 只給要的測試裝),不裝的話每一條派工的案例
        # 都停在「規則包產不出來」。假 reviewer 的模型(`set_reviewer` 的 fixture-model)
        # 也要有一張卡 —— 指定了卻不存在是錯,不是「沒有記憶」。
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
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
        """**變異 M1**:`review.sh` 的 by 寫死 `main` → 這一條紅。

        #72 併入同一趟 REVIEWER_PASS 的兩條:
        - 原 test_the_packet_names_the_four_things_and_goes_in_on_stdin:派工文 = 規則包 +
          範本;四件事都填上、**一個佔位都不剩**,而 reviewer 從 stdin 拿到的就是寫在
          reports 裡的那一份。佔位沒填與填了長得一樣 —— 除非有人數。
        - 原 test_files_outside_the_allow_list_get_their_own_section_in_the_packet:D-H38 ①:
          白名單外不擋,擋的是覆核者這一段。票允許 `src/*`,分支改的是 `thing.txt`。
          **變異**:不插這一段 → 這一條紅(負對照留在
          test_no_files_outside_the_allow_list_means_no_such_section)。
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
        self.assertEqual(review.get("sha"), self.sha, "review 綁的是分支 t1 的頭")
        self.assertEqual(review.get("state_version"), ticket["state_version"],
                         "state_version 由 ticket.py set 蓋,land 比得上")
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

        self.assertIn("## 白名單外改動", packet)
        section = packet.split("## 白名單外改動")[1].split("\n## ")[0]
        self.assertIn("- `thing.txt`", section)
        self.assertIn("斷言數不減", section)
        self.assertIn("無關或改弱 = fail", section)
        self.assertLess(packet.index("## 白名單外改動"), packet.index("## 交付:固定格式"))

    def packet(self):
        with open(self.one("dispatch-reviewer.md"), encoding="utf-8") as handle:
            return handle.read()

    def test_each_extra_file_shows_its_glob_and_reason(self):
        """#101 T2 / A5:覆核派工文逐檔顯示檔名、命中的 out_of_scope glob 與理由(各輪收件併起來的
        extra_reasons);沒寫理由的照列、標「沒寫」,由覆核判。
        四問:量那兩列;紅 = 只列檔名、或理由讀錯一份;期望值 = 夾具票面的 out_of_scope 與 handin;
        不測 = land 的拒絕。
        **變異**:`extra_line` 不印 glob(`hit` 當成 None)→ 這一條紅。"""
        self.make_ticket("1", state="InReview", out_of_scope=["docs/*"])
        self.commit_in(self.wt, "docs/x.md", "#1 動到 out_of_scope")
        self.write(os.path.join("reports", "t1", "20261018-090000-1", "result-round1.json"),
                   json.dumps({"extra_paths": ["docs/x.md", "thing.txt"],
                               "extra_reasons": {"docs/x.md": "驗收 A1 的說明寫在這",
                                                 "thing.txt": None}}, ensure_ascii=False))
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        section = self.packet().split("## 白名單外改動")[1].split("\n## ")[0]
        self.assertIn("- `docs/x.md` —— 命中票的 out_of_scope `docs/*` —— 理由:驗收 A1 的說明寫在這",
                      section)
        self.assertIn("- `thing.txt` —— 不在提示範圍 —— 理由:(沒寫)", section)

    def test_a_reason_given_in_round_one_is_still_shown_after_a_repair_round(self):
        """#101 A5 覆核反駁:第 1 輪收件寫了理由,第 2 輪修紅只量增量(extra_paths / extra_reasons 空),
        派工文仍要印第 1 輪的理由,不是「(沒寫)」。
        **變異**:`handin_reasons` 只回最近一份 → 這一條紅。"""
        self.make_ticket("1", state="InReview", out_of_scope=["docs/*"])
        self.commit_in(self.wt, "docs/x.md", "#1 動到 out_of_scope")
        run = os.path.join("reports", "t1", "20261018-090000-1")
        self.write(os.path.join(run, "result-round1.json"),
                   json.dumps({"extra_paths": ["docs/x.md"],
                               "extra_reasons": {"docs/x.md": "驗收 A1 的說明寫在這"}},
                              ensure_ascii=False))
        self.write(os.path.join(run, "result-round2.json"),
                   json.dumps({"extra_paths": [], "extra_reasons": {}}))
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        section = self.packet().split("## 白名單外改動")[1].split("\n## ")[0]
        self.assertIn("- `docs/x.md` —— 命中票的 out_of_scope `docs/*` —— 理由:驗收 A1 的說明寫在這",
                      section)

    def test_no_files_outside_the_allow_list_means_no_such_section(self):
        """**變異**:不管有沒有都印 → 這一條紅。"""
        self.assertEqual(self.ticket("set", "1", "allowed_write_paths",
                                     '["thing.txt"]').returncode, 0)
        self.set_reviewer(REVIEWER_PASS)
        done = self.review()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("白名單外改動", self.packet())

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
    四問:量 argv、by、agent.start、cost;值 = A1/A4;舊值紅在 by;表真的與命令不同。"""

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
        self.assertFalse(os.path.exists(
            os.path.join(self.repo, "reports", "inbox", "acked.jsonl")))
        self.assertEqual([row["state"] for row in self.inbox_rows()], ["第 1 輪綠了,等覆核"])


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
    """#108 A5 / A6:`review.sh --batch land/X 1 2 --run-id <stamp>` 覆核整合分支頭上那一個
    fix commit。pass 寫 `reports/batch/<stamp>/review.json`(sha = land/X 頭,不是哪一張票的
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
        self.git("commit", "-q", "-m", "batch fix round 1 #1 #2 (land/X)", cwd=land)
        self.fix = self.git("rev-parse", "land/X").strip()
        self.before = {ident: self.load_ticket(ident) for ident in ("1", "2")}

    def review(self, *ids):
        return self.run_sh("scripts/review.sh", "--batch", "land/X", *(ids or ("1", "2")),
                           "--run-id", self.STAMP, env=self.env(AC_BATCH_RUN_ID=self.RUN))

    def record(self, name):
        return os.path.join(self.repo, "reports", "batch", self.STAMP, name)

    def inbox_rows(self):
        path = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def assert_tickets_kept(self):
        for ident, row in self.before.items():
            now = self.load_ticket(ident)
            for key in ("state_version", "review", "state", "objections"):
                self.assertEqual(now.get(key), row.get(key), "#%s 的 %s 被寫了" % (ident, key))

    def test_pass_writes_review_json_bound_to_the_land_head(self):
        """A5 pass。四問:量 review.json 的 verdict / sha / by / at / note、票不動;
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
        """A5 派工文。四問:量派工文帶 fix commit 的 diff 行與兩張票的 subject / acceptance;
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

    def test_the_packet_lists_the_extra_files_with_glob_and_reason(self):
        """#101 A5 批次路:apply --batch 寫的 result(extra_paths / extra_reasons)逐檔進覆核派工文,
        命中的是哪一張票的 out_of_scope glob 也印出來。四問:量那兩列;紅 = 批次派工文不列;期望值 =
        夾具的 result 與票 1 的 out_of_scope;不測 = apply 怎麼抽。"""
        self.write(self.record("result-round1.json"),
                   json.dumps({"extra_paths": ["docs/guarded.md", "misc/n.txt"],
                               "extra_reasons": {"docs/guarded.md": "修紅要動它",
                                                 "misc/n.txt": None}}, ensure_ascii=False))
        self.set_reviewer(REVIEWER_PASS)
        self.assertEqual(self.review().returncode, 0)
        text = self.read(self.record("dispatch-reviewer.md"))
        self.assertIn("- `docs/guarded.md` —— 命中#1 的 out_of_scope `docs/guarded.md` —— "
                      "理由:修紅要動它", text)
        self.assertIn("- `misc/n.txt` —— 不在提示範圍 —— 理由:(沒寫)", text)

    def test_the_batch_packet_unions_the_extra_files_across_rounds(self):
        """#101 A5 覆核反駁(批次路):第 2 輪收件只量增量,第 1 輪列的檔與理由仍要進派工文。
        **變異**:只讀最後一份 result-round*.json → 這一條紅。"""
        self.write(self.record("result-round1.json"),
                   json.dumps({"extra_paths": ["docs/guarded.md"],
                               "extra_reasons": {"docs/guarded.md": "修紅要動它"}},
                              ensure_ascii=False))
        self.write(self.record("result-round2.json"),
                   json.dumps({"extra_paths": ["misc/n.txt"], "extra_reasons": {"misc/n.txt": None}}))
        self.set_reviewer(REVIEWER_PASS)
        self.assertEqual(self.review().returncode, 0)
        text = self.read(self.record("dispatch-reviewer.md"))
        self.assertIn("- `docs/guarded.md` —— 命中#1 的 out_of_scope `docs/guarded.md` —— "
                      "理由:修紅要動它", text)
        self.assertIn("- `misc/n.txt` —— 不在提示範圍 —— 理由:(沒寫)", text)

    def test_fail_pages_ticket_one_listing_the_batch_and_writes_nothing(self):
        """A5 fail。四問:量 rc≠0、review.json 不在、恰一頁 decision ticket=1 且內文有 #2、票不動;
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
        """A6。四問:量票 1 多一列 role=reviewer、note 帶 batch <stamp> 與 #1 #2、run_id = AC_BATCH_RUN_ID;
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

    def test_a_head_that_is_not_a_batch_fix_is_refused(self):
        """前提:頭不是 apply --batch 疊的 commit ⇒ rc 2、reviewer 不起。"""
        self.set_reviewer(REVIEWER_PASS)
        land = os.path.join(self.home, "wt", "land-X")
        side = self.git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "side", cwd=land).strip()
        self.git("merge", "-q", "--no-ff", "-m", "merge side", side, cwd=land)
        done = self.review()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.log), "reviewer 不該被起")


# ------------------------------------------------------- #98:覆核綁內容與票面

RECEIPT_KEYS = ["attempt", "face", "patch_id", "run_id", "sha", "state_version"]


class TheReviewBindsThePatchAndTheFace(ReviewBase):
    """#98 A1 / A3:pass 的 review 記 patch_id 與 face;收件單是 review_request 的不可變副本。
    期望的 patch_id 由夾具自己 `git diff main...t1 | git patch-id --stable`,face 由票面 A2 的
    oracle 算(`control_harness`),都不從 ticket.py 拿。"""

    RUN = "20261017-120000-1"

    def receipt_path(self, run=RUN):
        return os.path.join(self.repo, "reports", "t1", run, "review-receipt.json")

    def test_a1_a3_a_pass_binds_patch_id_and_face_and_leaves_an_accepted_receipt(self):
        """四問:量 review 的 patch_id / face / sha 與收件單的鍵、值、result;紅 = 沒記新鍵、
        收件單多帶 owner_id 或與票上 review_request 不同、版本沒綁收件那一版;期望值 = 夾具
        patch-id 與 A2 oracle。"""
        self.set_reviewer(REVIEWER_PASS)
        done = self.review("--run-id", self.RUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        review = ticket["review"]
        self.assertEqual(review["patch_id"], fixture_patch_id(self.env(), self.repo, "t1"))
        self.assertEqual(review["face"], oracle_face(ticket))
        self.assertEqual(review["sha"], self.sha)
        with open(self.receipt_path(), encoding="utf-8") as handle:
            receipt = json.load(handle)
        self.assertEqual(sorted(receipt["request"]), RECEIPT_KEYS)
        asked = ticket["review_request"]
        self.assertEqual(asked["owner_id"], "review.sh", "沒有租約 = 派工的那一支")
        self.assertEqual(receipt["request"], {key: asked[key] for key in RECEIPT_KEYS})
        self.assertEqual(receipt["request"]["run_id"], self.RUN)
        self.assertEqual(receipt["request"]["attempt"], 1)
        self.assertEqual(receipt["request"]["patch_id"], review["patch_id"])
        self.assertEqual(receipt["request"]["face"], review["face"])
        self.assertEqual(receipt["request"]["state_version"] + 1, review["state_version"],
                         "派工那一手 +1、收件那一手再 +1")
        self.assertEqual(review["state_version"], ticket["state_version"])
        self.assertEqual(receipt["result"], {"accepted": True, "reason": ""})
        self.assertEqual(review_problems(ticket, self.sha), [])

    def test_a3_the_owner_is_the_existing_lease_holder_and_the_lease_is_left_alone(self):
        """四問:量 review_request.owner_id 與收件後的 lease;紅 = owner 取別的來源或覆核改寫租約;
        期望值 = 夾具寫進去的那一份租約。"""
        lease = {"holder": "auto-fix@fixture", "until": "2099-01-01T00:00:00+08:00"}
        done = self.ticket("set", "1", "lease", json.dumps(lease))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.set_reviewer(REVIEWER_PASS)
        done = self.review("--run-id", self.RUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["lease"], lease)
        self.assertEqual(ticket["review_request"]["owner_id"], "auto-fix@fixture")
        self.assertEqual(ticket["review"]["verdict"], "pass")

    def test_a3_reviewing_a_run_again_keeps_its_finished_receipt_verbatim(self):
        """同一個 run_id 收過件之後再派一次:舊單改名留著、一個位元組都不改,新單另寫。
        四問:量舊單的位元組與新單的版本;紅 = 舊單被蓋掉;期望值 = 第一次讀到的位元組。"""
        self.set_reviewer(REVIEWER_PASS)
        self.assertEqual(self.review("--run-id", self.RUN).returncode, 0)
        with open(self.receipt_path(), "rb") as handle:
            first = handle.read()
        version = json.loads(first.decode("utf-8"))["request"]["state_version"]
        done = self.review("--run-id", self.RUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.receipt_path()[:-len(".json")] + "-v%d.json" % version, "rb") as handle:
            self.assertEqual(handle.read(), first)
        with open(self.receipt_path(), encoding="utf-8") as handle:
            second = json.load(handle)
        self.assertGreater(second["request"]["state_version"], version)
        self.assertTrue(second["result"]["accepted"])


# 假 reviewer(#98 T2):給了 `FAKE_HOLD` 就先在那個目錄寫 started、等測試放 go 才交件 ——
# 雙向會合:測試等它真的起來了(收件單已記)才動票,它等測試動完票才交。
REVIEWER_HELD = """#!/bin/sh
echo "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"
cat > /dev/null
if [ -n "${FAKE_HOLD:-}" ]; then
    : > "$FAKE_HOLD/started"
    i=0
    while [ ! -f "$FAKE_HOLD/go" ] && [ $i -lt 2400 ]; do sleep 0.05; i=$((i+1)); done
fi
if [ "${FAKE_VERDICT:-pass}" = fail ]; then
cat <<'EOF'
%s
EOF
else
cat <<'EOF'
%s
EOF
fi
""" % ("# 覆核\n① verdict:fail\nOBJECTION: blocking 遲到的那一條\n\n"
       + result_block("fail", json.dumps({"category": "blocking", "body": "遲到的那一條"},
                                         ensure_ascii=False)),
       "# 覆核\n① verdict:pass\n\n" + result_block("pass"))


class ALateReviewIsStale(ReviewBase):
    """#98 A3 / A4(T2):reviewer A 先派、停在交件前;測試動票(又派一次覆核 / 新 attempt /
    改票面)之後才放行 A。A 交 pass 或 fail 都是 rc=4 stale:review、objections、state、
    state_version、租約、review_request 一格不動(零覆蓋),只多一則 review.stale、A 自己的
    收件單記 accepted=false(request 一字不改);成本照記 A 真的跑過的那一列。"""

    RUN_A = "20261017-120000-1"
    RUN_B = "20261017-120000-2"
    KEPT = ("review", "objections", "state", "state_version", "lease", "review_request", "owner")

    def setUp(self):
        super().setUp()
        self.set_reviewer(REVIEWER_HELD)

    def receipt(self, run):
        path = os.path.join(self.repo, "reports", "t1", run, "review-receipt.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def start_held(self, verdict):
        hold = tempfile.mkdtemp(dir=self.home)
        proc = subprocess.Popen(
            ["sh", os.path.join(self.repo, "scripts", "review.sh"), "1", "--run-id", self.RUN_A],
            cwd=self.repo, env=self.env(FAKE_HOLD=hold, FAKE_VERDICT=verdict),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.release, proc, hold)
        deadline = time.time() + TIMEOUT
        while not os.path.exists(os.path.join(hold, "started")):
            if proc.poll() is not None:
                self.fail("reviewer A 沒等到就結束了:\n" + "".join(proc.communicate()))
            self.assertLess(time.time(), deadline, "reviewer A 一直沒起來")
            time.sleep(0.05)
        self.asked = self.receipt(self.RUN_A)["request"]
        return proc, hold

    @staticmethod
    def release(proc, hold):
        if proc.returncode is not None:
            return proc.returncode, ""
        with open(os.path.join(hold, "go"), "w", encoding="utf-8"):
            pass
        out, err = proc.communicate(timeout=TIMEOUT)
        return proc.returncode, out + err

    def assert_nothing_written(self, before, rc, said, why):
        self.assertEqual(rc, 4, said)
        self.assertIn("stale", said)
        now = self.load_ticket("1")
        for key in self.KEPT:
            self.assertEqual(now.get(key), before.get(key), "遲到的 A 動了 %s" % key)
        late = self.receipt(self.RUN_A)
        self.assertEqual(late["request"], self.asked, "收件單的 request 一字不改")
        self.assertFalse(late["result"]["accepted"])
        self.assertIn(why, late["result"]["reason"])
        self.assertEqual(self.last_event("review.stale")["run_id"], self.RUN_A)
        rows = [row for row in now.get("cost") or [] if row.get("run_id") == self.RUN_A]
        self.assertEqual(len(rows), 1, "成本照記 A 真的跑過的那一列")
        self.assertIn("stale", rows[0]["note"])
        return now, rows[0]

    def test_t2_a_pass_finishing_after_a_second_dispatch_is_stale(self):
        """四問:量 A 的 rc、票七格、A 的收件單、事件、A 的成本列;紅 = A 蓋掉 B 的 review;
        期望值 = B 收件後的票(夾具讀檔)。

        **變異 M3**:收件改拿票上現在的 review_request 當收件單(完成時重讀新版)→ 這一條紅。"""
        proc, hold = self.start_held("pass")
        done = self.review("--run-id", self.RUN_B)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")
        self.assertEqual(before["review_request"]["run_id"], self.RUN_B, "前提:票上是 B 那一份")
        rc, said = self.release(proc, hold)
        _now, row = self.assert_nothing_written(before, rc, said, "又派了一次覆核")
        self.assertEqual(row["outcome"], "pass")
        self.assertEqual([e["run_id"] for e in self.events() if e["kind"] == "review.pass"],
                         [self.RUN_B])
        self.assertTrue(self.receipt(self.RUN_B)["result"]["accepted"])

    def test_t2_a_fail_finishing_after_a_second_dispatch_is_stale(self):
        """四問:同上,另量 objections / state / inbox / review.fail;紅 = 遲到的 fail 記了反駁、
        轉 Blocked 或發頁;期望值 = B 收件後的票。"""
        proc, hold = self.start_held("fail")
        done = self.review("--run-id", self.RUN_B)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")
        rc, said = self.release(proc, hold)
        now, row = self.assert_nothing_written(before, rc, said, "又派了一次覆核")
        self.assertEqual(now.get("objections") or [], [])
        self.assertEqual(now["state"], "InReview")
        self.assertEqual(now["review"]["verdict"], "pass")
        self.assertEqual(self.inbox_rows(), [], "遲到的 fail 不發頁")
        self.assertNotIn("review.fail", self.kinds())
        self.assertEqual(row["outcome"], "fail")

    def test_a4_a_new_attempt_makes_the_late_pass_stale(self):
        """四問:量 rc、review、A 成本列的 round;紅 = 收下舊 attempt 的 pass,或成本記成票現在的
        attempt;期望值 = 夾具的 attempt 1 → 2。"""
        proc, hold = self.start_held("pass")
        done = self.ticket("set", "1", "attempt", "2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")
        rc, said = self.release(proc, hold)
        now, row = self.assert_nothing_written(before, rc, said, "第 1 次派工")
        self.assertIsNone(now.get("review"))
        self.assertEqual(row["round"], 1, "成本記的是 A 真的跑的那一次,不是票現在的 attempt")

    def test_a4_a_ticket_edit_makes_the_late_fail_stale(self):
        """四問:量 rc、objections、state、inbox;紅 = 改票之後遲到的 fail 照記;期望值 = 改票後的票。"""
        proc, hold = self.start_held("fail")
        done = self.ticket("set", "1", "objective", "派覆核之後改的目標")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")
        rc, said = self.release(proc, hold)
        now, _row = self.assert_nothing_written(before, rc, said, "派工之後票被改過")
        self.assertEqual(now.get("objections") or [], [])
        self.assertEqual(now["state"], "InReview")
        self.assertEqual(self.inbox_rows(), [])

    def test_a3_the_same_run_id_is_not_dispatched_twice_while_one_is_out(self):
        """四問:量第二次的 rc、reviewer 被叫幾次、票版本、A 交件後的 review;紅 = 第二份蓋掉
        A 的收件單(A 回來就拿著別人的單);期望值 = A 派工後的票版本。"""
        proc, hold = self.start_held("pass")
        version = self.load_ticket("1")["state_version"]
        again = self.review("--run-id", self.RUN_A)
        self.assertEqual(again.returncode, 2, again.stdout + again.stderr)
        self.assertIn("還有一份沒收件的覆核", again.stderr)
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"], "第二份不起 reviewer")
        self.assertEqual(self.load_ticket("1")["state_version"], version, "不派的那一手不動票")
        rc, said = self.release(proc, hold)
        self.assertEqual(rc, 0, said)
        self.assertEqual(self.load_ticket("1")["review"]["verdict"], "pass")


class TheFaceIsThirteenFields(unittest.TestCase):
    """#98 A1 / A2(T1、T3)的純函式那一半:`ticket.face` 對票面 A2 的 oracle、逐一改 face 欄 →
    「face 不同」、改運行欄 → 照樣有效;舊 review(缺新鍵)三種錯誤照舊。頭給的是 review 綁的
    那個 sha,這幾條不問 git(patch_id 那一半在 test_land 走真的分支)。"""

    SHA = "a" * 40
    FACE_CHANGES = {"subject": "換了標題", "objective": "換了目標", "acceptance": ["只剩一條"],
                    "in_scope": ["scripts/x.py"], "out_of_scope": ["docs/y.md"],
                    "allowed_write_paths": ["scripts/*"],
                    "info_homes": [{"what": "規則", "home": "docs/h.md"}],
                    "needs_verifier": True, "interface_fixed": True,
                    "verify_strings": ["scripts/x.py:新字"], "test_plan": "T9",
                    "depends_on": ["7"], "decision_refs": ["D-001"]}

    def make(self, **extra):
        row = {"id": "1", "subject": "第 1 張", "objective": "做完某件事",
               "acceptance": ["會紅的斷言一條", "第二條:中文也進指紋"], "in_scope": [],
               "out_of_scope": [], "depends_on": [], "allowed_write_paths": ["src/*"],
               "role": "worker", "state": "InReview", "state_version": 7, "attempt": 1,
               "lease": None, "base_sha": "b" * 40}
        row.update(extra)
        row["review"] = {"verdict": "pass", "by": "reviewer@fixture-model", "sha": self.SHA,
                         "state_version": 7, "patch_id": "c" * 40, "face": oracle_face(row)}
        return row

    def test_a2_face_is_the_oracle_of_the_thirteen_fields(self):
        row = self.make(needs_verifier=True, decision_refs=["D-049"],
                        verify_strings=[{"path": "scripts/x.py", "contains": "審"}])
        self.assertEqual(ticket_mod.face(row), oracle_face(row))
        self.assertEqual(review_problems(row, self.SHA), [])

    def test_a2_missing_and_null_normalise_to_the_empty_value_of_the_field(self):
        """#101 的 info_homes 還沒落地:缺這一格與寫成空 list 要是同一個指紋。"""
        bare = self.make()
        filled = dict(bare, info_homes=[], needs_verifier=None, interface_fixed=False,
                      verify_strings=[], test_plan="", decision_refs=None)
        self.assertEqual(ticket_mod.face(filled), ticket_mod.face(bare))
        self.assertEqual(review_problems(filled, self.SHA), [])

    def test_a1_a2_each_face_field_changed_is_refused_as_a_face_difference(self):
        """review 的 face 用 `ticket.face` 自己蓋(oracle 那一條另外比):漏掉的那一格改了,
        自己比自己就看不出來。**變異 M2**:FACE_FIELDS 漏 acceptance → acceptance 那一格紅。"""
        self.assertEqual(sorted(self.FACE_CHANGES), sorted(name for name, _ in FACE_ORACLE))
        for name, value in self.FACE_CHANGES.items():
            with self.subTest(name):
                row = self.make()
                row["review"]["face"] = ticket_mod.face(row)
                self.assertEqual(review_problems(row, self.SHA), [], "前提:沒改之前有效")
                row[name] = value
                out = review_problems(row, self.SHA)
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("face 不同"), out)

    def test_a1_runtime_fields_changed_keep_the_review(self):
        """**變異**:新式 review 照舊比 state_version → 這一條紅。"""
        row = self.make()
        row.update({"state": "IntegrationQueued", "state_version": 42, "attempt": 3,
                    "lease": {"holder": "someone", "until": "2099-01-01T00:00:00+08:00"},
                    "cost": [{"role": "reviewer", "round": 1}], "base_sha": "d" * 40,
                    "gate": {"rc": 0, "sha": self.SHA}, "branch": "t1-x",
                    "workspace": "../wt/t1-x", "patch_ref": "reports/t1/r/patch.diff",
                    "review_request": {"run_id": "r"}})
        self.assertEqual(review_problems(row, self.SHA), [])
        self.assertEqual(review_problems(row), [])

    def test_t3_an_old_review_keeps_its_three_oracles(self):
        """缺 patch_id 或 face 的 review 照舊比 state_version / sha,訊息照舊(A1 後半)。

        **變異**:`bound_to_content` 改成只看 face → 「只缺 patch_id」那一格紅。"""
        for label, drop in (("兩格都缺", ("patch_id", "face")), ("只缺 patch_id", ("patch_id",)),
                            ("只缺 face", ("face",))):
            with self.subTest(label):
                def old(**extra):
                    row = self.make(**extra)
                    for key in drop:
                        row["review"].pop(key)
                    return row
                unbound = old()
                unbound["review"].pop("state_version")
                self.assertIn("沒有綁票版本", "".join(review_problems(unbound, self.SHA)))
                self.assertIn("覆核之後票被改過",
                              "".join(review_problems(old(state_version=8), self.SHA)))
                self.assertIn("覆核之後又 commit 過", "".join(review_problems(old(), "e" * 40)))
                self.assertEqual(review_problems(old(), self.SHA), [])


class TheReceiptIsComparedNotReread(unittest.TestCase):
    """#98 A3:收件比的是收件單對票上的 review_request 與 attempt / 租約 / 版本。"""

    def test_a3_a_changed_lease_holder_alone_is_stale(self):
        """版本、attempt 都對得上,只有租約換了人 —— 只有「派工 owner」那一條擋得住。"""
        claimed = {"run_id": "r1", "attempt": 1, "state_version": 5, "face": "f",
                   "patch_id": "p", "sha": "s"}
        row = {"attempt": 1, "state_version": 5, "lease": {"holder": "someone-else"},
               "review_request": dict(claimed, owner_id="auto-fix@a")}
        self.assertIn("換手", ticket_mod.request_problem(row, claimed))
        row["lease"] = {"holder": "auto-fix@a"}
        self.assertEqual(ticket_mod.request_problem(row, claimed), "")
        row["review_request"]["patch_id"] = "q"
        self.assertIn("patch_id", ticket_mod.request_problem(row, claimed))

    def test_a3_a_lease_taken_after_a_leaseless_dispatch_is_stale(self):
        """沒有租約時派工 owner 是 review.sh;收件前有人取了租約 —— 換手,版本沒動也擋。"""
        claimed = {"run_id": "r1", "attempt": 1, "state_version": 5, "face": "f",
                   "patch_id": "p", "sha": "s"}
        row = {"attempt": 1, "state_version": 5, "lease": None,
               "review_request": dict(claimed, owner_id="review.sh")}
        self.assertEqual(ticket_mod.request_problem(row, claimed), "")
        row["lease"] = {"holder": "auto-fix@a"}
        self.assertIn("換手", ticket_mod.request_problem(row, claimed))


class TheReceiveHasASameLockSeam(ReviewBase):
    """#98 A6:`receive_review(extend=…)` 在同一把鎖裡、驗過收件單之後、存檔之前叫 extend;
    回一句話就整筆不收、票不動;回空字串則它對票的改動與 review 同一次存檔。"""

    def test_a6_extend_runs_inside_the_lock_and_can_veto_or_join_the_save(self):
        """**變異**:`receive_review` 不叫 extend → 這一條紅。"""
        receipt = os.path.join(self.repo, "reports", "t1", "r1", "review-receipt.json")
        lock = os.path.join(self.repo, "tickets", ticket_mod.LOCK_NAME)
        seen = []

        def veto(row, claimed):
            seen.append((os.path.isdir(lock), claimed["run_id"]))
            return "#104 的格子對不上"

        def join(row, claimed):
            row["seam"] = claimed["run_id"]
            return ""

        with mock.patch.dict(os.environ, {"AC_ROOT": self.repo}):
            _request, busy = ticket_mod.request_review("1", "r1", self.sha, receipt)
            self.assertEqual(busy, "")
            self.assertEqual(ticket_mod.receive_review("1", receipt, "pass", by="x", extend=veto),
                             (False, "#104 的格子對不上"))
            self.assertEqual(seen, [(True, "r1")], "extend 在鎖裡、拿的是收件單")
            self.assertIsNone(self.load_ticket("1").get("review"), "被否決的那一筆不存檔")
            self.assertEqual(ticket_mod.receive_review("1", receipt, "pass", by="x", extend=join),
                             (True, ""))
        row = self.load_ticket("1")
        self.assertEqual(row["seam"], "r1")
        self.assertEqual(row["review"]["verdict"], "pass")
        self.assertEqual(row["review"]["state_version"], row["state_version"])


if __name__ == "__main__":
    unittest.main()
