"""`scripts/inbox.py`:終態去叫醒主線,主線不輪詢(D-015)。

2026-09-21 外部審查:「最容易空轉的是『gate 紅了以後由主線人工接續』。」在它之前,
主線要知道一輪跑完了沒,只有**輪詢**(每看一次背景工作 = 整份上下文重送一輪)或
**等人來講**(而跑完的是一支腳本,它不會來講)。

所以這一組問的是:**跑完的事會不會自己來排隊,而且那一頁答不答得出四句話** ——
哪張票、什麼狀態、要主線做什麼、去哪看。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

RED_CASE = """import unittest


class T(unittest.TestCase):
    def test_always_red(self):
        self.assertEqual(1, 2, "沙盒裡真的紅一條")
"""


class InboxBase(Sandbox):

    def inbox(self, *args):
        return self.run_py("scripts/inbox.py", *args)

    def post(self, ident="7", run="r1", state="閘門紅", what="看紅榜", where="reports/x"):
        return self.inbox("post", "--ticket", ident, "--run-id", run,
                          "--kind", "gate", "--state", state, "--what", what,
                          "--where", where)


class OnePage(InboxBase):

    def test_a_terminal_state_writes_one_page_that_answers_the_four_questions(self):
        self.make_ticket("7", subject="把閘門接上票的回歸")
        done = self.post()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        page = self.read(os.path.join("reports", "inbox", "7-r1.md"))
        self.assertIn("#7", page)
        self.assertIn("把閘門接上票的回歸", page, "哪張票")
        self.assertIn("閘門紅", page, "什麼狀態")
        self.assertIn("看紅榜", page, "要主線做什麼")
        self.assertIn("reports/x", page, "去哪看")

    def test_each_round_gets_its_own_page(self):
        """**一輪一頁、不覆寫** —— 覆寫的話,接手的人分不出手上這頁是哪一輪的。"""
        self.make_ticket("7")
        self.post(run="r1")
        self.post(run="r2", state="閘門綠")
        self.assertTrue(self.exists(os.path.join("reports", "inbox", "7-r1.md")))
        self.assertTrue(self.exists(os.path.join("reports", "inbox", "7-r2.md")))

    def test_two_terminal_states_in_one_run_do_not_share_a_page(self):
        """同一輪可以有兩個終態(閘門綠了,接著 auto-fix 說「停在等覆核」)。

        **變異**:把撞名加序號那一段拿掉 → 這一條紅(索引上兩列指著同一個檔,
        而後寫的那一頁蓋掉了前一頁)。
        """
        self.make_ticket("7")
        self.post(run="r1", state="閘門綠")
        self.post(run="r1", state="第 2 輪綠了,等覆核")
        rows = [json.loads(line) for line in self.read(
            os.path.join("reports", "inbox", "index.jsonl")).splitlines() if line.strip()]
        self.assertEqual(len({row["page"] for row in rows}), 2, "兩列指著同一頁")
        for row in rows:
            page = self.read(row["page"])
            self.assertIn(row["state"], page)

    def test_posting_emits_an_event_so_the_board_sees_it(self):
        """控制台只認事件(D-003):沒發事件的事,對系統而言沒發生。"""
        self.make_ticket("7")
        self.post()
        self.assertIn("inbox.posted", self.kinds())

    def test_an_unknown_event_kind_would_be_refused(self):
        """`inbox.posted` 要真的在 `event.py` 的固定表裡,不是靠 inbox.py 自己說了算。"""
        done = self.event("emit", "inbox.posted", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class ListAndAck(InboxBase):

    def test_list_shows_what_is_waiting_and_ack_takes_it_off(self):
        self.make_ticket("7")
        self.post()
        listed = self.inbox("list")
        self.assertIn("#7", listed.stdout)
        self.assertIn("閘門紅", listed.stdout)
        acked = self.inbox("ack", "7")
        self.assertEqual(acked.returncode, 0, acked.stdout + acked.stderr)
        after = self.inbox("list")
        self.assertNotIn("#7", after.stdout)
        self.assertIn("沒有等你的東西", after.stdout)

    def test_ack_does_not_touch_the_ticket_state(self):
        """ack 是「我收下了」,不是「我做完了」。混在一起的話,一次 ack 會讓一張
        還沒處理的票在畫面上消失。"""
        self.make_ticket("7", state="Blocked")
        self.post()
        self.inbox("ack", "7")
        self.assertEqual(self.load_ticket("7")["state"], "Blocked")

    def test_an_acked_page_is_still_readable_with_all(self):
        self.make_ticket("7")
        self.post()
        self.inbox("ack", "7")
        self.assertIn("#7", self.inbox("list", "--all").stdout)

    def test_show_prints_that_one_page(self):
        self.make_ticket("7", subject="這張票的標題")
        self.post()
        done = self.inbox("show", "7")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("這張票的標題", done.stdout)

    def test_show_of_something_that_is_not_there_says_so(self):
        done = self.inbox("show", "9")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("沒有", done.stderr)


class WhoAcked(InboxBase):
    """#43 A3:`acked.jsonl` 每一筆記是誰收的。腳本做掉的頁由腳本收(`--by <腳本>`),
    主線只剩 Blocked / 裁示 / 落地順序三類頁 —— 而「誰收的」要在帳上分得開。"""

    def acked_rows(self):
        return [json.loads(line) for line in self.read(
            os.path.join("reports", "inbox", "acked.jsonl")).splitlines() if line.strip()]

    def test_ack_by_a_script_records_that_script(self):
        """**變異 M3**:`--by` 收了不寫進 jsonl → 這一條紅。"""
        self.make_ticket("7")
        self.post(state="第 1 輪綠了,等覆核")
        done = self.inbox("ack", "7", "--by", "review.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([row.get("by") for row in self.acked_rows()], ["review.sh"])

    def test_ack_without_by_is_recorded_as_main_not_blank(self):
        """「沒寫是誰」與「主線收的」要分得開(§5.5):預設記 main,不是空字串。"""
        self.make_ticket("7")
        self.post()
        self.inbox("ack", "7")
        self.assertEqual([row.get("by") for row in self.acked_rows()], ["main"])

    def test_a_blank_by_is_refused(self):
        self.make_ticket("7")
        self.post()
        done = self.inbox("ack", "7", "--by", "")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("#7", self.inbox("list").stdout, "拒收的 ack 不該把頁收掉")

    def test_ack_all_by_migration_marks_every_row(self):
        for ident in ("7", "8"):
            self.make_ticket(ident)
            self.post(ident=ident)
        self.post(ident="8", run="r2")
        done = self.inbox("ack", "--all", "--by", "migration")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.acked_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual({row.get("by") for row in rows}, {"migration"})
        self.assertIn("沒有等你的東西", self.inbox("list").stdout)

    def test_state_picks_only_the_pages_the_script_is_about_to_do(self):
        """`--state` 只收 state 含那幾個字的頁:同一張票的其它頁(主線的待辦)不動。"""
        self.make_ticket("7")
        self.post(run="r1", state="第 1 輪綠了,等覆核")
        self.post(run="r2", state="Blocked(三輪耗盡)")
        done = self.inbox("ack", "7", "--state", "等覆核", "--by", "review.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        listed = self.inbox("list").stdout
        self.assertNotIn("等覆核", listed)
        self.assertIn("Blocked", listed)
        self.assertEqual([row["name"] for row in self.acked_rows()], ["7-r1"])


class WhoWritesIntoIt(InboxBase):
    """三個終態各寫一則:閘門跑完、票轉 Blocked、落地跑完(land 那一則在 test_land)。

    這裡用**真的** `gate.sh`(不換替身):要問的正是「那一支自己會不會寫」。
    """

    def setUp(self):
        super(WhoWritesIntoIt, self).setUp()
        self.write("tests/test_land.py", RED_CASE)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的替身測試模組")

    def test_a_red_gate_round_leaves_a_page_that_names_the_next_step(self):
        self.make_ticket("7")
        done = self.run_sh("scripts/gate.sh", "scripts/land.sh", "--ticket", "7",
                           "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout)
        listed = self.inbox("list")
        self.assertIn("#7", listed.stdout, listed.stdout + done.stdout)
        self.assertIn("閘門紅", listed.stdout)
        self.assertIn("auto-fix.sh 7", self.read(
            os.path.join("reports", "inbox", "%s.md" % self._only_page())))

    def _only_page(self):
        rows = [json.loads(line) for line in self.read(
            os.path.join("reports", "inbox", "index.jsonl")).splitlines() if line.strip()]
        return rows[-1]["name"]

    def test_a_green_gate_round_asks_for_a_review_not_for_a_fix(self):
        """綠了也是終態,而它要主線做的事不一樣 —— **覆核不自動**(D-010)。"""
        self.write("tests/test_land.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n        pass\n")
        self.make_ticket("7", allowed_write_paths=["tests/*"])
        done = self.run_sh("scripts/gate.sh", "scripts/land.sh", "--ticket", "7")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        page = self.read(os.path.join("reports", "inbox", "%s.md" % self._only_page()))
        self.assertIn("review", page)
        self.assertIn("閘門綠", page)

    def test_running_out_of_rounds_leaves_a_page_that_names_the_next_step(self):
        """三輪耗盡是一個**狀態轉換**,而狀態轉換是一個終態 —— 它要來叫醒主線。"""
        self.make_ticket("7", retry_limit=2, state="Running")
        done = self.ticket("round", "7", "3", "--red")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("7")["state"], "Blocked")
        listed = self.inbox("list")
        self.assertIn("Blocked", listed.stdout)
        self.assertIn("#7", listed.stdout)

    def test_the_index_is_append_only_json_not_parsed_prose(self):
        """`list` 讀索引而不是掃 markdown:散文改一個字就會解析錯。"""
        self.make_ticket("7")
        self.post()
        self.post(run="r2")
        rows = [json.loads(line) for line
                in self.read(os.path.join("reports", "inbox", "index.jsonl")).splitlines()
                if line.strip()]
        self.assertEqual([row["run_id"] for row in rows], ["r1", "r2"])


if __name__ == "__main__":
    unittest.main()
