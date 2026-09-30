"""`scripts/new-session.sh`:開 session 的固定動作。

**一張要靠人記得照做的清單,漏掉一項時長得跟做完了一樣。** 所以這一組釘的是「每一段
都真的跑過」以及「事件真的發出去了」。
"""

import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402


class NewSession(Sandbox):

    def start(self, *args):
        return self.run_sh("scripts/new-session.sh", *args)

    def test_it_runs_every_step_and_puts_the_session_on_the_board(self):
        self.make_ticket(1, state="Ready", subject="開著的那一張")
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for step in ("站在哪個版本", "最近發生的事", "開著的票",
                     "記憶有沒有超過上限", "接下來要讀的"):
            self.assertIn(step, done.stdout, "少跑了一段")
        self.assertIn("開著的那一張", done.stdout)
        rows = [row for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role"], "worker")
        self.assertEqual(rows[0]["model"], "opus")

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

    def test_memory_over_cap_does_not_stop_the_session(self):
        """退出碼 1 只是讓這個 session 知道自己的記憶該整理了 —— **不停工**
        (docs/MEMORY.md 第 1 點)。

        **變異**:把那一行的 `|| echo …` 拿掉(讓 `set -e` 式的失敗傳出去)
        → 這一條紅。
        """
        self.write("memory/model/opus.md", "坑" * 2500)
        done = self.start("worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("不停工", done.stdout)
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
        self.assertIn("memory/role/main.md", self.start("main", "fable").stdout)
        worker = self.start("worker", "opus").stdout
        self.assertIn("memory/role/implementer.md", worker)
        self.assertIn("memory/model/opus.md", worker)
        self.assertIn("docs/HANDOFF.md", worker)

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


if __name__ == "__main__":
    unittest.main()
