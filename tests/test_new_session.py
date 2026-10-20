"""`scripts/new-session.sh`:開 session 的固定動作。

**一張要靠人記得照做的清單,漏掉一項時長得跟做完了一樣。** 所以這一組釘的是「每一段
都真的跑過」以及「事件真的發出去了」。
"""

import json
import os
import re
import shutil
import sys
import time
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox, write_executable  # noqa: E402


# #148(D-055 ①):1 條要放滿 3 天才觸發
OLD_NOTE = "- 一條原則 (#1, %s, worker@opus)\n" % (date.today() - timedelta(days=3)).isoformat()


class NewSession(Sandbox):

    def start(self, *args):
        return self.run_sh("scripts/new-session.sh", *args)

    def test_it_runs_every_step_and_puts_the_session_on_the_board(self):
        """#74:事件流與開票清單只給主線(`docs/SESSION-START.md` 那張表),所以「每一段」
        用 main 跑;短命角色少的那兩段由 `test_rules_delivery` 釘。"""
        self.make_ticket(1, state="Ready", subject="開著的那一張")
        done = self.start("main", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for step in ("站在哪個版本", "最近發生的事", "開著的票",
                     "記憶暫存區有沒有待整理", "接下來要讀的"):
            self.assertIn(step, done.stdout, "少跑了一段")
        self.assertIn("開著的那一張", done.stdout)
        rows = [row for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "main")
        self.assertEqual(rows[0]["model"], "fable")

    def test_main_gets_sections_5_to_7_and_a_worker_does_not(self):
        """主線多印第 5–7 節(收件匣、決策收件匣、心跳);worker 指向派工規矩,不印那三節。
        節標題的期望來自 new-session.sh 對 main 的分支。

        #72 由原 test_a_worker_is_pointed_at_the_dispatch_rules_and_the_main_line_is_not
        與 test_the_main_line_also_gets_the_inbox_and_the_heartbeat 併成,斷言全留,
        補上「worker 沒有」那一半(以前沒有任何測試守)。

        **變異**:把 new-session.sh 第 5–7 節外面那個 `if [ "$ROLE" = "main" ]` 拿掉
        → 這一條紅(worker 也印了第 5–7 節)。
        """
        self.make_ticket(1, state="NeedsDecision", subject="等你裁決的")
        worker = self.start("worker", "opus")
        self.assertIn("DISPATCH-TEMPLATE", worker.stdout)
        main = self.start("main", "fable")
        self.assertEqual(main.returncode, 0, main.stdout + main.stderr)
        self.assertIn("收件匣", main.stdout)
        self.assertIn("心跳", main.stdout)
        self.assertIn("等你裁決的", main.stdout)
        self.assertIn("沒有到期的租約", main.stdout)
        for title in ("5. 收件匣:", "6. 決策收件匣:", "7. 心跳:"):
            self.assertIn(title, main.stdout)
            self.assertNotIn(title, worker.stdout)

    def test_the_opening_does_not_leak_a_shell_error(self):
        """🩸 反引號在雙引號裡是**命令替換**:那一行在乾淨 clone 裡吐出
        `command substitution: syntax error`,而畫面上其他每一段都正常 ——
        一個 session 的第一印象因此是「這份東西壞的」。

        **變異**:把那一行的單引號改回雙引號加反引號 → 這一條紅。
        """
        done = self.start("main", "fable")
        for noise in ("syntax error", "command not found", "command substitution"):
            self.assertNotIn(noise, done.stdout + done.stderr, "開場吐了 shell 的錯")

    def test_a_pending_inbox_does_not_stop_the_session(self):
        """退出碼 1 只是讓這個 session 知道有暫存區待整理 —— **不停工**
        (docs/MEMORY.md 第 1 點;#134 new_session 字樣)。

        **變異**:把那一行的 `|| echo …` 拿掉(讓 `set -e` 式的失敗傳出去)
        → 這一條紅。
        """
        self.write("memory/model/opus.md", "短的\n")
        self.write("memory/model/opus.inbox.md", OLD_NOTE)
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("4. 記憶暫存區有沒有待整理(D-053 ⑥)", done.stdout)
        self.assertIn("new-session: (有待整理的暫存區,不停工;整理票由主線開場開,worker 不開)",
                      done.stdout)
        self.assertIn("session.start", self.kinds())
        self.assertNotIn("memory.pending", self.kinds())
        self.assertEqual(self.tickets_on_disk(), [])

    def test_the_main_line_opens_the_ticket_and_still_goes_on(self):
        self.write("memory/model/opus.inbox.md", OLD_NOTE)
        done = self.start("main", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("new-session: (有待整理的暫存區,不停工;整理票已開或沿用)", done.stdout)
        self.assertEqual([one["subject"] for one in self.tickets_on_disk()
                          if one.get("role") == "consolidator"],
                         ["整理 memory/model/opus.md(暫存區 1 條未整理)"])

    def test_a_cloud_copy_of_the_main_line_does_not_open_the_ticket(self):
        """CLAUDE_CODE_REMOTE=true(雲端 session 的官方變數)時,主線開場的第 4 段走唯讀:
        不開整理票、不發 memory.pending,其餘(session.start)照常(#150,D-057 ②)。

        **變異**:拿掉 new-session.sh 第 4 段那個 CLAUDE_CODE_REMOTE 條件 → 這一條紅。
        """
        self.write("memory/model/opus.inbox.md", OLD_NOTE)
        done = self.run_sh("scripts/new-session.sh", "main", "fable",
                           env=self.env(CLAUDE_CODE_REMOTE="true"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([one for one in self.tickets_on_disk()
                          if one.get("role") == "consolidator"], [])
        self.assertNotIn("memory.pending", self.kinds())
        self.assertIn("session.start", self.kinds())

    def test_missing_arguments_is_a_usage_error_and_emits_nothing(self):
        done = self.start("main")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.events(), [], "沒開成的 session 不該留下一筆 start")

    def test_no_event_prints_the_page_and_emits_nothing(self):
        """#39:resume / compact 後 hook 重印這一頁 —— 那不是新的 session。"""
        done = self.start("main", "fable", "--no-event")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("開著的票", done.stdout)
        self.assertEqual(self.events(), [], "--no-event 還是發了事件")

    def test_the_reading_list_names_this_role_s_card(self):
        """#39:角色卡那一行。worker 的卡叫 implementer.md(rules.py WANTED)——
        指到一個不存在的檔,比沒有那一行更糟。

        #72 併入原 test_the_reading_list_points_at_this_model_s_own_memory:同模型跨
        session 共享 —— Opus 的下一個 session 應該知道 Opus 上次犯過什麼。"""
        main = self.start("main", "fable").stdout
        self.assertIn("memory/role/main.md", main)
        self.assertIn("docs/HANDOFF.md", main)
        worker = self.start("worker", "opus").stdout
        self.assertIn("memory/role/implementer.md", worker)
        self.assertIn("memory/model/opus.md", worker)
        # #74:全域交接是主線的,短命角色的讀單不列它。
        self.assertNotIn("docs/HANDOFF.md", worker)

    def test_the_closing_line_ends_the_same_session_it_started(self):
        """#45 B3:沒給 `--session` 就自己產一個 SID、印出來,start 帶它,結語那句
        session.end 也帶它 —— 照抄那句,heartbeat 才配得起來。"""
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = [row for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual(len(rows), 1)
        sid = rows[0].get("session")
        self.assertTrue(sid, "session.start 沒帶 session id")
        self.assertIn("session: %s" % sid, done.stdout.splitlines())
        self.assertIn("結束前:python3 scripts/event.py emit session.end --role worker "
                      "--session %s" % sid, done.stdout.splitlines())

    def test_a_given_session_id_is_used(self):
        done = self.start("main", "fable", "--session", "abc")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = [row for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual([row.get("session") for row in rows], ["abc"])
        self.assertIn("--session abc", done.stdout)

    def test_an_unknown_flag_is_a_usage_error_and_emits_nothing(self):
        done = self.start("main", "fable", "--no-evnet")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.events(), [])


class TheMainLineStartsTheConsolidators(Sandbox):
    """#87 A41:主線開場對每張 role=consolidator、Ready、沒有有效租約的整理票叫一次
    consolidate-memory.sh;短命角色開場不叫。替身只記「被叫到了」與票號。"""

    STUB = '#!/bin/sh\necho "consolidate $*" >> "%s"\n'

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "consolidate-calls.log")
        path = os.path.join(self.repo, "scripts", "consolidate-memory.sh")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.STUB % self.calls)
        self.make_ticket(7, role="consolidator", state="Ready",
                         memory_file="memory/model/opus.md")
        self.make_ticket(8, role="consolidator", state="Ready",
                         memory_file="memory/model/fable.md",
                         lease={"holder": "someone", "until": "2999-01-01T00:00:00+08:00"})
        self.make_ticket(9, role="worker", state="Ready")

    def called(self, wait):
        deadline = time.time() + wait
        while time.time() < deadline and not os.path.exists(self.calls):
            time.sleep(0.1)
        time.sleep(0.3)
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def test_a41_main_starts_it_and_a_worker_does_not(self):
        """**變異**:拿掉 new-session.sh 那一段的 `ROLE = main` 條件 → worker 那一半紅。"""
        worker = self.run_sh("scripts/new-session.sh", "worker", "opus")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertEqual(self.called(2), [], "短命角色開場叫了整理")
        main = self.run_sh("scripts/new-session.sh", "main", "opus")
        self.assertEqual(main.returncode, 0, main.stdout + main.stderr)
        self.assertEqual(self.called(10), ["consolidate 7"],
                         "只該叫 Ready、沒有有效租約的整理票那一張")


class A7ItMovesWithTheSync(Sandbox):
    """#39 A7:同步到專案後這幾支住在 `scripts/control/`,「上一層」不再是根。"""

    MOVED = ("new-session.sh", "heartbeat.sh", "session-hook.sh", "event.py",
             "ticket.py", "inbox.py", "memory.py", "status.py", "verify.py", "rules.py")

    def setUp(self):
        super().setUp()
        control = os.path.join(self.repo, "scripts", "control")
        os.makedirs(control)
        for name in self.MOVED:
            shutil.copy(os.path.join(SCRIPTS, name), os.path.join(control, name))
        self.make_ticket(7, subject="只有這顆沙盒才有的那一張")

    def run_moved(self, **env):
        return self.run_sh("scripts/control/new-session.sh", "main", "fable",
                           "--no-event", env=self.env(**env))

    def test_a7_with_ac_root(self):
        done = self.run_moved(AC_ROOT=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("開著的票", done.stdout)
        self.assertIn("只有這顆沙盒才有的那一張", done.stdout)

    def test_a7_without_ac_root_it_walks_up_to_the_config(self):
        done = self.run_moved()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("開著的票", done.stdout)
        self.assertIn("只有這顆沙盒才有的那一張", done.stdout,
                      "票庫讀錯地方 —— 根沒有往上找到 board/config.json")



# #62:主線開場那一頁。期望值都來自沙盒自己寫進去的標記,不從被測腳本算。
RULES = ("# 沙盒的契約\n\n## 開場\n開場那一節\n\n## 不可違反的\n- RULE-MARK 一條規則\n"
         "### 子標題不算下一節\n- 還是規則\n\n## 別的\n- OTHER-MARK 不該印\n")
CARD = "# 主線\nCARD-MARK 主線角色卡\n"
PROJECT_RULES = {"roles_dir": "docs/roles", "models_dir": "docs/roles/model"}


def section(out, title, stop="\n── "):
    """`── <title>` 那一段(到下一個 `── `)。"""
    at = out.index("── " + title)
    end = out.find(stop, at + 1)
    return out[at:end if end >= 0 else len(out)]


class OpeningPage(Sandbox):

    def main(self, *more):
        done = self.run_sh("scripts/new-session.sh", "main", "fable", "--no-event", *more)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def canon(self):
        self.write("CLAUDE.md", RULES)
        self.write("memory/role/main.md", CARD)

    def post(self, count):
        for n in range(1, count + 1):
            done = self.run_py("scripts/inbox.py", "post", "--ticket", str(n), "--run-id",
                               "r%d" % n, "--kind", "decision", "--state", "閘門紅",
                               "--what", "看紅榜 %d" % n,
                               "--where", "reports/t%d/r%d/status.json" % (n, n))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class A1RulesOnTop(OpeningPage):

    def test_a1_the_contract_and_the_card_come_before_everything(self):
        """**變異**:把規則段移到讀單後 → 這一條紅。"""
        self.canon()
        out = self.main()
        first = out.index("站在哪個版本")
        self.assertLess(out.index("RULE-MARK"), first)
        self.assertLess(out.index("CARD-MARK"), first)
        self.assertLess(out.index("RULE-MARK"), out.index("CARD-MARK"))
        self.assertIn("還是規則", out, "### 子標題不是下一節")
        self.assertNotIn("OTHER-MARK", out, "只印「不可違反的」那一節,到下一個 ## 為止")
        self.assertNotIn("開場那一節", out)


class A2ProjectLayers(OpeningPage):
    config_extra = {"rules": PROJECT_RULES}

    def setUp(self):
        super().setUp()
        self.write("docs/roles/contract.md", "## 不可違反的\n- RULE-MARK 專案那一份\n")
        self.write("docs/roles/main.md", CARD)
        self.write("memory/role/main.inbox.md",
                   "".join("- L%02d 專案疊層第 %d 行\n" % (n, n) for n in range(1, 13)))

    def test_a2_contract_then_card_then_the_inbox_tail(self):
        out = self.main()
        marks = ["RULE-MARK", "CARD-MARK"] + ["L%02d" % n for n in range(8, 13)]
        where = [out.index(mark) for mark in marks]
        self.assertEqual(where, sorted(where), "順序:契約 → 角色卡 → 暫存區尾巴")
        self.assertLess(where[-1], out.index("站在哪個版本"))
        for n in range(1, 8):
            self.assertNotIn("L%02d" % n, out, "只印最後 5 行")

    def test_a2_a_missing_contract_is_said_out_loud(self):
        os.remove(os.path.join(self.repo, "docs", "roles", "contract.md"))
        done = self.run_sh("scripts/new-session.sh", "main", "fable", "--no-event")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        said = [line for line in done.stdout.splitlines()
                if "contract.md" in line and "sync-to-project.sh" in line]
        self.assertTrue(said, "缺了要講,不靜默:" + done.stdout[:2000])


class A3OnlyTheMainLine(OpeningPage):

    def test_a3_other_roles_do_not_get_the_rules_on_top(self):
        self.canon()
        for role in ("worker", "opener", "verifier"):
            done = self.run_sh("scripts/new-session.sh", role, "opus", "--no-event")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertNotIn("RULE-MARK", done.stdout, role)
            self.assertNotIn("CARD-MARK", done.stdout, role)


class A4TicketsHaveACap(OpeningPage):

    def ticket_lines(self, out):
        return [line for line in section(out, "3. 開著的票").splitlines()
                if re.match(r"#\d+\s", line)]

    def test_a4_drafts_are_counted_not_listed(self):
        """**變異**:拿掉 Draft 過濾 → 這一條紅。"""
        for n in range(1, 46):
            self.make_ticket(n, state="Draft", subject="DRAFT-SUBJ 第 %d 張" % n)
        for n in range(46, 61):
            self.make_ticket(n, state="Ready", subject="READY 第 %d 張" % n)
        part = section(self.main(), "3. 開著的票")
        self.assertNotIn("DRAFT-SUBJ", part)
        self.assertTrue([line for line in part.splitlines() if "Draft" in line and "45" in line],
                        part)
        listed = {int(re.match(r"#(\d+)", line).group(1)) for line in self.ticket_lines(part)}
        self.assertEqual(listed, set(range(46, 61)))

    def test_a4_sixty_ready_tickets_list_twenty_and_say_the_rest(self):
        for n in range(1, 61):
            self.make_ticket(n, state="Ready")
        out = self.main()
        self.assertLessEqual(len(self.ticket_lines(out)), 20)
        self.assertTrue([line for line in section(out, "3. 開著的票").splitlines()
                         if "還有 40 張沒列" in line and "ticket.py list --open" in line])

    def test_a4_the_other_open_states_are_listed_and_count_toward_the_cap(self):
        states = ("Blocked", "Running", "InReview", "NeedsDecision")
        for n, state in enumerate(states, 1):
            self.make_ticket(n, state=state, subject="%s-SUBJ" % state)
        for n in range(5, 23):
            self.make_ticket(n, state="Ready")
        out = self.main()
        part = section(out, "3. 開著的票")
        for state in states:
            self.assertIn("%s-SUBJ" % state, part)
        self.assertLessEqual(len(self.ticket_lines(out)), 20)
        self.assertIn("還有 2 張沒列", part, "22 張不是 Draft 的,列 20、說 2")


class A5InboxHasACap(OpeningPage):

    def test_a5_fifty_entries_list_twenty_one_line_each(self):
        for n in range(1, 51):
            self.make_ticket(n)
        self.post(50)
        part = section(self.main(), "5. 收件匣")
        entries = [line for line in part.splitlines() if "看紅榜" in line]
        self.assertLessEqual(len(entries), 20)
        self.assertTrue(entries)
        for line in entries:
            number = re.search(r"看紅榜 (\d+)", line).group(1)
            self.assertIn("#%s" % number, line, "每一行都含票號")
        self.assertNotIn("reports/inbox/", part, "頁檔路徑那一行不印")
        self.assertTrue([line for line in part.splitlines()
                         if "還有 30 則沒列" in line and "inbox.py list" in line], part)

    def test_a5_an_empty_inbox_looks_empty(self):
        part = section(self.main(), "5. 收件匣")
        self.assertIn("沒有等你的東西", part)
        self.assertNotIn("沒列", part)


class A6ThePageFitsTheHook(OpeningPage):

    def test_a6_a_full_board_stays_under_ten_thousand_characters(self):
        self.canon()
        for n in range(1, 61):
            self.make_ticket(n, state="Ready", subject="%02d" % n + "滿" * 58)
        self.post(50)
        for n in range(30):
            done = self.run_py("scripts/event.py", "emit", "ticket.state", "--role", "main",
                               "--ticket", str(n + 1), "--note", "事件 %d:" % n + "長" * 40)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = self.main()
        self.assertLess(len(out), 10000)
        for piece in ("RULE-MARK", "CARD-MARK", "接下來要讀的"):
            self.assertIn(piece, out)


class TheOtherSectionsHaveACapToo(OpeningPage):
    """#62:在 T 的真實資料上,票與收件匣收完之後頁面仍超過 10,000 字元 —— 多出來的是
    24 筆到期租約、一列 300 字元的事件與髒工作樹。這三段也收,沒列的說數字。"""

    def test_git_status_lists_ten_and_says_the_rest(self):
        for n in range(15):
            self.write("dirty-%02d.txt" % n, "")
        part = section(self.main(), "1. 站在哪個版本")
        self.assertEqual(len([line for line in part.splitlines() if "dirty-" in line]), 10)
        self.assertIn("還有 5 行沒列", part)

    def test_heartbeat_lists_five_stale_rows_per_block(self):
        rows = [json.dumps({"ts": "2026-09-01T00:0%d:00+08:00" % n, "kind": "session.start",
                            "role": "main", "session": "stale-%d" % n})
                for n in range(8)]
        self.write("board/events.jsonl", "\n".join(rows) + "\n")
        part = section(self.main(), "7. 心跳")
        self.assertEqual(len([line for line in part.splitlines() if "stale-" in line
                              or "session.start" in line]), 5, part)
        self.assertIn("還有 3 列沒列", part)
        self.assertIn("處置", part, "塊尾那一行處置不能被收掉")

    def test_one_long_event_is_cut_to_one_short_line(self):
        done = self.run_py("scripts/event.py", "emit", "ticket.state", "--ticket", "1",
                           "--note", "長" * 400)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        part = section(self.main(), "2. 最近發生的事")
        line = [one for one in part.splitlines() if "長長" in one][0]
        self.assertLessEqual(len(line), 160)


class A8TheReadingList(OpeningPage):
    config_extra = {"rules": PROJECT_RULES}

    def setUp(self):
        super().setUp()
        self.write("docs/roles/contract.md", "## 不可違反的\n- RULE-MARK\n")
        self.write("docs/roles/main.md", CARD)
        self.write("docs/roles/model/fable.md", "# fable\n")
        self.write("memory/role/main.inbox.md", "- 專案疊層\n")
        self.write("memory/model/fable.inbox.md", "- 還沒併檔的\n")
        self.write("memory/project/facts.md", "# 事實\n")
        self.write("docs/HANDOFF.md", "# 交接\n")

    def reading(self):
        return section(self.main(), "接下來要讀的", stop="\n\n")

    def test_a8_card_then_project_layer_then_memory_then_handoff(self):
        part = self.reading()
        order = [part.index(one) for one in ("docs/roles/main.md", "memory/role/main.inbox.md",
                                             "memory/model/fable.inbox.md", "docs/HANDOFF.md")]
        self.assertEqual(order, sorted(order), part)
        self.assertIn("memory/role/main.md", part[:part.index("memory/role/main.inbox.md")],
                      "角色卡那一行要標明它是 memory/role/main.md 的同步本")
        self.assertNotIn("memory/role/main.md    你這個角色的角色卡", part)
        self.assertFalse([line for line in part.splitlines()
                          if line.split()[:1] == ["memory/role/main.md"]],
                         "專案沒有 memory/role/main.md,不准指過去")
        for one in ("docs/roles/model/fable.md", "memory/role/", "memory/project/"):
            self.assertIn(one, part)
        self.assertIn("現況", part)
        self.assertNotIn("最後三節", part)

    def test_a8_every_path_is_checked_against_the_root(self):
        shutil.rmtree(os.path.join(self.repo, "memory", "project"))
        part = self.reading()
        seen = 0
        for line in part.splitlines()[1:]:
            words = line.split()
            if not words or not ("/" in words[0] or words[0].endswith(".md")):
                continue
            seen += 1
            gone = not os.path.exists(os.path.join(self.repo, words[0]))
            self.assertEqual("(不在)" in line, gone, line)
        self.assertGreater(seen, 5, part)
        self.assertIn("(不在)", [line for line in part.splitlines()
                               if "memory/project/" in line][0])

    def test_a8_canon_points_at_its_own_card(self):
        conf = json.loads(self.read("board/config.json"))
        del conf["rules"]
        self.write("board/config.json", json.dumps(conf))
        self.write("memory/role/main.md", CARD)
        part = self.reading()
        self.assertEqual([line.split()[0] for line in part.splitlines()[1:3]],
                         ["memory/role/main.md", "memory/model/fable.inbox.md"])
        self.assertNotIn("memory/role/main.inbox.md", part, "正本不印專案疊層")


# #122:角色表的消費端。替身 agent 記 argv、留 stdin;@MODE@ = ok / fail(退 3)/
# quota(印 codex 額度信封退 1)/ gone(印模型不存在信封退 1)。期望的模型名寫死在這裡。
AGENT = """#!/bin/sh
echo "agent $*" >> "@CALLS@"
cat > /dev/null
case "@MODE@" in
    fail) exit 3 ;;
    quota) echo '{"type":"error","message":"You have hit your usage limit"}'; exit 1 ;;
    gone) echo '{"type":"error","error":{"type":"not_found_error","message":"model gone"}}'; exit 1 ;;
esac
exit 0
"""
ASTRA = {"model": "codex:gpt-6-astra", "tool": "codex", "effort": None,
         "effort_escalated": None, "dispatch_cap": 5}
FABLE = {"model": "fable", "tool": "claude-code", "effort": None,
         "effort_escalated": None, "dispatch_cap": None}


class TheTableIsOnlyASuggestion(OpeningPage):
    """A2:互動 session 的模型是啟動時指定的那個;角色表只印成建議,不熱切。"""

    def test_a2_main_keeps_its_launch_model_and_shows_the_table_s(self):
        """守 main 不熱切、表只顯示 / 可信回歸:把 session.start 改記表的模型 → model=opus 紅 /
        字面 fable、opus / 新案例。"""
        done = self.run_sh("scripts/new-session.sh", "main", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        part = section(done.stdout, "角色表的建議")
        self.assertIn("main 建議 opus", part)
        self.assertIn("與本 session 的 fable 不同,不熱切", part)
        self.assertEqual([row["model"] for row in self.events() if row["kind"] == "session.start"],
                         ["fable"])

    def test_a2_an_opener_sees_both_opener_chains(self):
        self.set_role_models(opener_complex=[ASTRA, FABLE])
        done = self.run_sh("scripts/new-session.sh", "opener", "opus", "--no-event")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        part = section(done.stdout, "角色表的建議")
        self.assertIn("opener 建議 opus", part)
        self.assertIn("opener_complex 建議 codex:gpt-6-astra(tool codex,每次派工上限 5)",
                      part)

    def test_a3_main_is_warned_about_a_conflicting_legacy_setting(self):
        """守 A3 舊衝突設定要警告且給遷移 / 可信回歸:拿掉主線那段 legacy → 沒有「衝突」紅 /
        字面 routing.design / 新案例。"""
        conf = json.loads(self.read("board/config.json"))
        conf["routing"] = {"implement": "opus", "design": "opus"}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False))
        part = section(self.main(), "角色表的建議")
        clash = [line for line in part.splitlines() if "衝突" in line]
        self.assertEqual(len(clash), 1, part)
        self.assertIn("routing.design", clash[0])
        self.assertIn("legacy --write", part)


class IndependentOpenerAndDesignDispatch(Sandbox):
    """A1:獨立開題 / 設計派工只照角色表選人、`--items` 超過 dispatch_cap 不起程序;
    額度 / 不可用才換下一個候選。"""

    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.home, "agent-calls.log")
        self.prompt = self.write("prompt.md", "# 派工文\n開三張票\n")

    def arm(self, claude="ok", codex="ok"):
        body = AGENT.replace("@CALLS@", self.calls)
        fake = os.path.join(self.home, "fake-agent.sh")
        write_executable(fake, body.replace("@MODE@", claude))
        write_executable(os.path.join(self.stub_bin, "codex"), body.replace("@MODE@", codex))
        conf = json.loads(self.read("board/config.json"))
        conf["opener"] = {"command": "sh %s --model opus" % fake, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def dispatch(self, role, *flags):
        return self.run_sh("scripts/new-session.sh", "dispatch", role, "--prompt", self.prompt,
                           *flags)

    def agent_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def started(self):
        return [(row["role"], row["model"]) for row in self.events()
                if row["kind"] == "agent.start"]

    def test_a1_a_plain_opener_is_opus(self):
        self.set_role_models(opener_complex=[ASTRA, FABLE])
        self.arm()
        done = self.dispatch("opener", "--items", "3")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("--model opus", calls[0])
        self.assertEqual(self.started(), [("opener", "opus")])

    def test_a1_a_complex_opener_is_astra(self):
        self.set_role_models(opener_complex=[ASTRA, FABLE])
        self.arm()
        done = self.dispatch("opener", "--items", "3", "--complexity", "complex")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("exec -m gpt-6-astra", calls[0])
        self.assertEqual(self.started(), [("opener", "codex:gpt-6-astra")])

    def test_a1_design_moves_to_fable_only_on_a_codex_quota(self):
        """守「前者不可用才換下一個」/ 可信回歸:不判信封 → 停在 astra、fable 沒起紅 /
        字面 astra 一次、fable 一次 / 新案例。"""
        self.set_role_models(design=[ASTRA, FABLE])
        self.arm(codex="quota")
        done = self.dispatch("design", "--items", "2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.agent_calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("exec -m gpt-6-astra", calls[0])
        self.assertIn("--model fable", calls[1])
        self.assertEqual(self.started(), [("design", "codex:gpt-6-astra"), ("design", "fable")])

    def test_a1_an_ordinary_codex_failure_does_not_move_on(self):
        """守一般失敗不換 / 可信回歸:失敗一律換 → fable 被起紅 / 字面 rc 1、1 次 / 新案例。"""
        self.set_role_models(design=[ASTRA, FABLE])
        self.arm(codex="fail")
        done = self.dispatch("design", "--items", "2")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.agent_calls()), 1)

    def test_a1_every_candidate_gone_stops(self):
        self.set_role_models(design=[ASTRA, FABLE])
        self.arm(claude="gone", codex="quota")
        done = self.dispatch("design", "--items", "1")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(len(self.agent_calls()), 2)
        self.assertIn("整串耗盡", done.stderr)

    def test_a1_more_items_than_the_cap_starts_nothing(self):
        """守 dispatch_cap 由 --items 擋、印切批建議、不起程序 / 可信回歸:不傳 --items 給
        resolve → rc 0、astra 被起紅 / 字面 ≤5 / 新案例。"""
        self.set_role_models(opener_complex=[ASTRA, FABLE])
        self.arm()
        done = self.dispatch("opener", "--items", "6", "--complexity", "complex")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("請切成 ≤5 的批", done.stderr)
        self.assertEqual(self.agent_calls(), [])
        self.assertEqual(self.started(), [])

    def test_a1_a_fable_candidate_has_no_cap(self):
        self.set_role_models(opener_complex=[FABLE])
        self.arm()
        done = self.dispatch("opener", "--items", "40", "--complexity", "complex")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.started(), [("opener", "fable")])

    def test_a1_items_is_required(self):
        self.arm()
        done = self.dispatch("opener")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.agent_calls(), [])


if __name__ == "__main__":
    unittest.main()
