"""`scripts/ticket.py`:票是唯一的工作單位,所以這一組釘的是「什麼樣的票開不出來、
什麼樣的票關不掉」。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

MIN = ("--subject", "land 對 0 commit 整批拒絕",
       "--objective", "任一支 0 commit 時 land 秒退並點名",
       "--acceptance", "0 commit → rc!=0 且輸出含分支名",
       "--allowed-write-path", "scripts/land.sh",
       "--role", "worker", "--model", "opus", "--tool", "claude-code")


class Create(Sandbox):

    def test_it_writes_a_file_and_the_id_goes_up(self):
        first = self.ticket("create", *MIN)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("#1", first.stdout)
        second = self.ticket("create", *MIN)
        self.assertIn("#2", second.stdout)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Draft")
        self.assertEqual(row["state_version"], 1)
        self.assertEqual(row["allowed_write_paths"], ["scripts/land.sh"])
        self.assertIn("ticket.created", self.kinds())

    def test_the_base_sha_is_filled_in_from_main_when_it_is_not_given(self):
        """留空的那一格看起來跟「還沒決定」一樣,而落地器會拿它去問「這個基準還是
        主線的祖先嗎」。"""
        self.ticket("create", *MIN)
        self.assertEqual(self.load_ticket("1")["base_sha"],
                         self.git("rev-parse", "main").strip())

    def test_a_ticket_without_acceptance_is_refused_and_the_missing_field_is_named(self):
        """**變異**:把 `acceptance` 從 `NOT_EMPTY` 拿掉 → 這一條紅。"""
        args = [a for a in MIN]
        at = args.index("--acceptance")
        del args[at:at + 2]
        done = self.ticket("create", *args)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("acceptance", done.stderr)
        self.assertIn("空的", done.stderr)
        self.assertFalse(self.exists("tickets/1.json"), "沒開成的票不該留下檔案")

    def test_a_ticket_without_allowed_write_paths_is_refused(self):
        args = [a for a in MIN]
        at = args.index("--allowed-write-path")
        del args[at:at + 2]
        done = self.ticket("create", *args)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("allowed_write_paths", done.stderr)

    def test_it_can_be_asked_one_field_at_a_time(self):
        answers = "\n".join([
            "互動開的票", "把每一格問出來",
            "驗收一", "", "src/a.py", "", "src/b.py", "", "src/*", "",
            "3", "",
            "worker", "opus", "claude-code"]) + "\n"
        done = self.ticket("create", stdin=answers)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["subject"], "互動開的票")
        self.assertEqual(row["acceptance"], ["驗收一"])
        self.assertEqual(row["depends_on"], [{"id": "3", "condition": ""}])

    def test_depends_on_takes_a_condition_after_a_colon(self):
        done = self.ticket("create", *MIN, "--depends-on", "3:閘門綠")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["depends_on"],
                         [{"id": "3", "condition": "閘門綠"}])


class ListAndShow(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="Ready")
        self.make_ticket(2, state="Done")
        self.make_ticket(3, state="NeedsDecision")

    def test_open_hides_the_closed_ones(self):
        done = self.ticket("list", "--open")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("#1", done.stdout)
        self.assertIn("#3", done.stdout)
        self.assertNotIn("#2", done.stdout)

    def test_state_filters_to_one(self):
        done = self.ticket("list", "--state", "NeedsDecision")
        self.assertIn("#3", done.stdout)
        self.assertNotIn("#1", done.stdout)

    def test_a_state_that_is_not_in_the_machine_is_refused(self):
        done = self.ticket("list", "--state", "Doing")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_an_empty_result_says_so(self):
        done = self.ticket("list", "--state", "Cancelled")
        self.assertIn("沒有符合的票", done.stdout)

    def test_show_prints_the_whole_json(self):
        done = self.ticket("show", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["id"], "1")


class Set(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="Ready")

    def test_every_change_bumps_state_version_and_emits(self):
        """**每次變更 +1**,不管改的是哪一格 —— 只在改 state 時 +1 的版本擋不住
        「改了範圍、版本沒動」那一種。

        **變異**:把 `state_version` 那一行搬進 `if field == "state"` → 這一條紅。
        """
        self.assertEqual(self.ticket("set", "1", "state", "Running").returncode, 0)
        self.assertEqual(self.load_ticket("1")["state_version"], 2)
        self.assertEqual(self.ticket(
            "set", "1", "allowed_write_paths", '["src/*", "tests/*"]').returncode, 0)
        row = self.load_ticket("1")
        self.assertEqual(row["state_version"], 3)
        self.assertEqual(row["allowed_write_paths"], ["src/*", "tests/*"])
        self.assertEqual(self.kinds().count("ticket.state"), 2)

    def test_a_list_field_given_one_word_becomes_a_one_element_list(self):
        """字串形狀的 `allowed_write_paths` 會讓落地的 glob 比對逐字元跑過去,
        而它不會出聲。"""
        self.ticket("set", "1", "allowed_write_paths", "src/only.py")
        self.assertEqual(self.load_ticket("1")["allowed_write_paths"], ["src/only.py"])

    def test_an_unknown_state_is_refused_and_nothing_moves(self):
        done = self.ticket("set", "1", "state", "Finished")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state_version"], 1)

    def test_id_and_state_version_cannot_be_set_by_hand(self):
        for field in ("id", "state_version"):
            done = self.ticket("set", "1", field, "99")
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["id"], "1")


class Freeze(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1)

    def test_it_records_both_the_reason_and_the_way_out(self):
        done = self.ticket("freeze", "1", "--reason", "視覺方向未定",
                           "--criterion", "產出會不會因視覺方向改變而重做")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["frozen"]["reason"], "視覺方向未定")
        self.assertIn("重做", row["frozen"]["criterion"])
        self.assertEqual(row["state_version"], 2)
        self.assertIn("ticket.frozen", self.kinds())

    def test_a_freeze_without_a_way_out_is_refused(self):
        """少了解凍條件,凍結會變成一張沒有人記得要回來看的票 —— 「凍著」與
        「忘了」長得一樣。"""
        done = self.ticket("freeze", "1", "--reason", "等使用者")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn("frozen", self.load_ticket("1"))


class VerifyAndClose(Sandbox):
    """2026-09-10(前一個專案):一張票被關成完成,而它的程式碼從來沒有進主線。
    事後查證用的就是 `git show main:<檔> | grep -c <那張票獨有的字串>` = 0。"""

    def land_a_file(self, path, text):
        self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 %s 放進主線" % path)

    def test_a_string_that_is_really_on_main_verifies(self):
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("在主線上", done.stdout)
        self.assertIn("×1", done.stdout)

    def test_a_string_that_never_reached_main_does_not_verify(self):
        """**變異**:把 `verify` 的 `hits > 0` 改成 `hits >= 0` → 這一條紅。"""
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("**不在主線上**", done.stdout)

    def test_close_refuses_when_nothing_of_the_ticket_is_on_main(self):
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("exit code 0 不等於 Done", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready", "票不該被關掉")
        self.assertNotIn("ticket.closed", self.kinds())

    def test_close_goes_through_once_the_change_is_really_on_main(self):
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Done")
        self.assertEqual(row["state_version"], 2)
        self.assertIn("ticket.closed", self.kinds())

    def test_without_verify_strings_it_falls_back_and_says_the_check_is_weak(self):
        base = self.git("rev-parse", "main").strip()
        self.land_a_file("src/nav.py", "x = 1\n")
        self.make_ticket(1, base_sha=base, allowed_write_paths=["src/*"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("弱檢查", done.stdout)

    def test_a_bare_needle_is_looked_for_across_the_allowed_paths(self):
        self.land_a_file("src/a.py", "SENTINEL = 1\n")
        self.make_ticket(1, allowed_write_paths=["src/*"], verify_strings=["SENTINEL"])
        self.assertEqual(self.ticket("verify", "1").returncode, 0)


class Inbox(Sandbox):

    def answer(self, ident, text):
        self.write("board/answers.jsonl",
                   json.dumps({"ts": "2026-09-12T11:00:00+08:00",
                               "ticket": ident, "answer": text},
                              ensure_ascii=False) + "\n")

    def test_it_lists_tickets_waiting_for_a_decision(self):
        self.make_ticket(1, state="NeedsDecision", subject="兌換碼的最小單位")
        done = self.ticket("inbox")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("#1", done.stdout)
        self.assertIn("兌換碼的最小單位", done.stdout)

    def test_an_answer_that_has_not_become_a_decision_is_still_in_the_inbox(self):
        self.make_ticket(1, state="Ready")
        self.answer("1", "成員退出帳本,月繳的規定當然一起不見")
        done = self.ticket("inbox")
        self.assertIn("還沒落成", done.stdout)
        self.assertIn("月繳", done.stdout)

    def test_once_the_decision_names_the_ticket_it_leaves_the_inbox(self):
        """判準就是 `docs/DECISIONS.md` 自己 —— 不另記一格「已處理」,因為另記的
        那一格會跟事實分岔。

        **變異**:把 `decided_tickets()` 改成永遠回空集合 → 這一條紅。
        """
        self.make_ticket(1, state="Ready")
        self.answer("1", "成員退出帳本,月繳的規定當然一起不見")
        self.write("docs/DECISIONS.md",
                   "# 裁示\n\n| D-007 | 退出即解除月繳 | 使用者 #1 原話 | 2026-09-12 | ✅ |\n")
        done = self.ticket("inbox")
        self.assertIn("收件匣是空的", done.stdout)

    def test_an_empty_inbox_says_so(self):
        self.assertIn("收件匣是空的", self.ticket("inbox").stdout)


class Import(Sandbox):

    def old_ticket(self, where, ident, **fields):
        row = {"id": str(ident), "subject": "舊票 %s" % ident, "status": "completed",
               "blocks": [], "blockedBy": [], "description": "當時的描述",
               "phases": {"created": "2026-09-10T11:50:45+08:00"}}
        row.update(fields)
        self.write("%s.json" % ident, json.dumps(row, ensure_ascii=False), where=where)

    def test_old_json_becomes_this_schema_with_the_gaps_left_empty(self):
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 497)
        self.old_ticket(old, 498, status="in_progress", blockedBy=["497"])
        done = self.ticket("import", old)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("497")
        self.assertTrue(row["legacy"], "沒有標 legacy")
        self.assertEqual(row["state"], "Done")
        self.assertEqual(row["base_sha"], "", "舊票的 base_sha 要留空,不是猜一個")
        self.assertEqual(row["allowed_write_paths"], [])
        self.assertIn("當時的描述", row["outline"])
        self.assertEqual(self.load_ticket("498")["state"], "Running")
        self.assertEqual(self.load_ticket("498")["depends_on"],
                         [{"id": "497", "condition": ""}])

    def test_a_ticket_that_is_already_here_is_not_overwritten(self):
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 1)
        self.make_ticket(1, subject="新的那一張")
        done = self.ticket("import", old)
        self.assertIn("跳過 1 張", done.stdout)
        self.assertEqual(self.load_ticket("1")["subject"], "新的那一張")

    def test_a_file_it_cannot_read_is_counted_separately_and_is_not_zero_exit(self):
        """「跳過」是已經有了,「讀不懂」是這裡有東西沒進來 —— 揉成一句「匯入 N 張」
        會讓後者看起來像沒發生。"""
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 1)
        self.write("broken.json", "{這不是 JSON", where=old)
        done = self.ticket("import", old)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("讀不懂 1 個檔", done.stdout)


if __name__ == "__main__":
    unittest.main()
