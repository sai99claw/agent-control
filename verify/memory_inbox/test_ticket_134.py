"""#134 記憶整理由未整理暫存條目觸發，不再由固定字數上限觸發。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 30 字主檔 + 1 條 inbox note 跑 check | rc 1、逐字報數、1 票與 1 事件 | 票面 A1 / interface_contracts
A2 | unit | 5000 字主檔無 note，再放 21 條 note | 前者不觸發，後者只開 1 票 | 票面 A2，字數與條數手算
A3 | unit | glob / 任意 role 覆蓋票、結案後、兩支併發 check | 查重訊息與票數 | 票面 A3
A4 | unit | worker / main 開場讀同一份待整理 inbox | worker 不開、main 開 1 票 | 票面 A4 / new_session 契約
A5 | unit | snapshot 後以 5000 字候選 consolidate | 成功、消耗、事件無 cap 鍵、舊旗標拒絕 | 票面 A5
A6 | gate | 分支把乾淨記憶卡 1990 推到 2500，再加錯層行 | 長度不擋、錯層仍擋 | 票面 A6
A7 | unit | 讀整理票、兩份提示文字與填好的派工 stdin | 只有成本判準，沒有字數目標 | 票面 A7
A8 | static | 跑票面釘死的 git grep | 0 行 | 票面 A8 原命令
A9 | unit | 無 inbox / 有 1 條 inbox 的探針與 AGENT_PROBES 原文 | 前提會翻轉，事件名是 memory.pending | 票面 A9
A10 | static | 文件記憶段與 project 層 inbox | 三句契約一致，project 不觸發 | 票面 A10

## 介面字串
CHECK_ONE = "memory: memory/role/opener.md 30 字元 / 暫存區 1 條未整理"
CHECK_ZERO = "memory: memory/role/opener.md 5000 字元 / 暫存區 0 條未整理"
CRITERION = "只留值得這個成本的"

## 怎麼做假
tests/control_harness.Sandbox 建拋棄式真 git repo；memory.py / new-session.sh / gate.sh /
consolidate-memory.sh 都以子行程執行。整理討論與 agent 是固定文字替身，不起真模型、不上埠。

## 不做
不改產品碼；不放寬票面驗收；不刪既有案例；不跑全套或整組 tag 回歸。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import (ROOT as SOURCE_ROOT, Sandbox, fixture_candidate,  # noqa: E402
                             write_executable)

TAGS = ["memory-inbox-trigger"]

CHECK_ONE = "memory: memory/role/opener.md 30 字元 / 暫存區 1 條未整理"
CHECK_ZERO = "memory: memory/role/opener.md 5000 字元 / 暫存區 0 條未整理"
CRITERION = "只留值得這個成本的"
NOTE = "- 一條原則 (#1, 2026-10-19, worker@opus)\n"
PASSING = """import os
import unittest


class T(unittest.TestCase):
    def test_it_ran(self):
        with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
            handle.write("%s\\n")
"""


class MemoryInboxWorld(Sandbox):

    config_extra = {
        "memory": {
            "applies_to": ["memory/model/*.md", "memory/role/*.md"],
            "inbox_suffix": ".inbox.md",
        }
    }

    def memory(self, *args, cwd=None):
        script = os.path.join(cwd or self.repo, "scripts", "memory.py")
        return subprocess.run(["python3", script, *args], cwd=cwd or self.repo,
                              env=self.env(), capture_output=True, text=True, timeout=120)

    def ticket_ids(self):
        return sorted(row["id"] for row in self.tickets_on_disk())

    def pending_events(self):
        return [row for row in self.events() if row["kind"] == "memory.pending"]

    def seed_opener(self, chars=30, inbox=NOTE):
        self.write("memory/role/opener.md", "坑" * chars)
        if inbox is not None:
            self.write("memory/role/opener.inbox.md", inbox)

    def cancel_all_tickets(self):
        for row in self.tickets_on_disk():
            row["state"] = "Cancelled"
            self.write("tickets/%s.json" % row["id"],
                       json.dumps(row, ensure_ascii=False, indent=2) + "\n")


class TriggerAndCounting(MemoryInboxWorld):

    def test_a1_one_unknown_ticket_note_triggers_exactly_once(self):
        """A1 一條 unknown-ticket note 也觸發逐字報數、整理票與 pending 事件。"""
        self.seed_opener()
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(CHECK_ONE, done.stdout)
        rows = self.tickets_on_disk()
        self.assertEqual(len(rows), 1, rows)  # pin:A1 恰好開一張票
        row = rows[0]
        self.assertEqual(row["subject"],
                         "整理 memory/role/opener.md(暫存區 1 條未整理)")
        self.assertEqual((row["role"], row["state"], row.get("needs_verifier")),
                         ("consolidator", "Ready", False))
        events = self.pending_events()
        self.assertEqual(len(events), 1, self.events())  # pin:A1 恰好一筆 pending
        self.assertEqual((events[0]["file"], events[0]["entries"]),
                         ("memory/role/opener.inbox.md", 1))

    def test_a2_length_is_only_reported_and_twenty_one_notes_are_one_trigger(self):
        """A2 5000 字且 0 條不觸發；21 條仍只是同一種待整理觸發。"""
        self.seed_opener(chars=5000, inbox=None)
        no_inbox = self.memory("check")
        self.assertEqual(no_inbox.returncode, 0, no_inbox.stdout + no_inbox.stderr)
        self.assertIn(CHECK_ZERO, no_inbox.stdout)
        self.assertNotIn("超過", no_inbox.stdout + no_inbox.stderr)
        self.assertEqual(self.tickets_on_disk(), [])
        self.assertEqual(self.events(), [])

        self.write("memory/role/opener.inbox.md", "\n   \n")
        blank = self.memory("check")
        self.assertEqual(blank.returncode, 0, blank.stdout + blank.stderr)
        self.assertIn(CHECK_ZERO, blank.stdout)

        notes = "".join("- 第 %d 條 (#1, 2026-10-19, worker@opus)\n" % n
                        for n in range(1, 22))
        self.write("memory/role/opener.inbox.md", notes)
        pending = self.memory("check")
        self.assertEqual(pending.returncode, 1, pending.stdout + pending.stderr)
        self.assertIn("5000 字元 / 暫存區 21 條未整理", pending.stdout)
        self.assertNotIn("超過", pending.stdout + pending.stderr)
        rows = self.tickets_on_disk()
        self.assertEqual(len(rows), 1, rows)  # pin:A2 二十一條只開一張票
        self.assertEqual(rows[0]["subject"],
                         "整理 memory/role/opener.md(暫存區 21 條未整理)")
        self.assertEqual(len(self.pending_events()), 1, self.events())  # pin:A2 一份主檔一筆事件


class Deduplication(MemoryInboxWorld):

    def test_a3_open_coverage_cancelled_tickets_and_concurrency_keep_one_ticket(self):
        """A3 glob、任何 role、Cancelled 後重開與兩支併發都沿用既有查重。"""
        self.seed_opener()
        self.make_ticket(7, state="Draft", role="consolidator",
                         allowed_write_paths=["memory/role/*.md"])
        before = self.ticket_ids()
        covered = self.memory("check")
        self.assertEqual(covered.returncode, 1, covered.stdout + covered.stderr)
        self.assertIn("已經有一張開著的整理票 #7,沒有再開", covered.stdout)
        self.assertEqual(self.ticket_ids(), before)

        self.make_ticket(7, state="Cancelled", role="consolidator",
                         allowed_write_paths=["memory/role/*.md"])
        reopened = self.memory("check")
        self.assertEqual(reopened.returncode, 1, reopened.stdout + reopened.stderr)
        self.assertEqual(len(self.ticket_ids()), len(before) + 1)

        self.cancel_all_tickets()
        self.make_ticket(20, state="Running", role="worker",
                         allowed_write_paths=["memory/role/*.md", "scripts/x.py"])
        before = self.ticket_ids()
        other_role = self.memory("check")
        self.assertIn("已經有一張開著的票 #20(role=worker)覆蓋 "
                      "memory/role/opener.md,沒有再開", other_role.stdout)
        self.assertEqual(self.ticket_ids(), before)

        self.cancel_all_tickets()
        reopened = self.memory("check")
        self.assertEqual(reopened.returncode, 1, reopened.stdout + reopened.stderr)
        self.assertEqual(len(self.ticket_ids()), len(before) + 1)
        self.cancel_all_tickets()

        before = self.ticket_ids()
        command = ["python3", os.path.join(self.repo, "scripts", "memory.py"), "check"]
        procs = [subprocess.Popen(command, cwd=self.repo, env=self.env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
        outputs = [proc.communicate(timeout=120) for proc in procs]
        self.assertEqual(len(self.ticket_ids()), len(before) + 1, outputs)


class SessionOpening(MemoryInboxWorld):

    def test_a4_only_main_opens_the_pending_memory_ticket(self):
        """A4 worker 只報待整理且不停工，main 才開一張整理票。"""
        self.seed_opener()
        worker = self.run_sh("scripts/new-session.sh", "worker", "opus")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertTrue("4. 記憶暫存區有沒有待整理(D-053 ⑥)" in worker.stdout,
                        "worker 開場缺新的第 4 段標題")
        self.assertIn("new-session: (有待整理的暫存區,不停工;"
                      "整理票由主線開場開,worker 不開)", worker.stdout)
        self.assertEqual(self.tickets_on_disk(), [])
        self.assertEqual(self.pending_events(), [])

        main = self.run_sh("scripts/new-session.sh", "main", "fable")
        self.assertEqual(main.returncode, 0, main.stdout + main.stderr)
        self.assertIn("new-session: (有待整理的暫存區,不停工;整理票已開或沿用)",
                      main.stdout)
        opened = [row for row in self.tickets_on_disk()
                  if row.get("role") == "consolidator"]
        self.assertEqual(len(opened), 1, main.stdout + main.stderr)  # pin:A4 main 開一張


class ConsolidationWithoutCaps(MemoryInboxWorld):

    MAIN = "memory/model/opus.md"
    INBOX = "memory/model/opus.inbox.md"

    def discussion(self, name, model, lines, digest):
        text = ("---\nmodel: %s\nsource_lines: %d\nsource_sha256: %s\n---\n\n"
                "## 第 1 輪 — %s\n- 主張:逐條處置\n\n## 結論\n"
                % (model, lines, digest, model))
        text += "".join("- L%d: 保留\n" % n for n in range(1, lines + 1))
        self.write(name, text)
        return name

    def raw(self, rel):
        path = os.path.join(self.repo, rel)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def test_a5_a_long_candidate_consolidates_and_removed_flags_are_unknown(self):
        """A5 5000 字候選可整理；事件無 cap 鍵，舊旗標 rc 2 且不動位元組。"""
        self.write(self.MAIN, "舊的一條\n")
        self.write(self.INBOX, NOTE)
        snap = self.memory("snapshot", self.MAIN)
        self.assertEqual(snap.returncode, 0, snap.stdout + snap.stderr)
        values = dict(line.split(": ", 1) for line in snap.stdout.splitlines())
        self.assertEqual(values["source_lines"], "1")
        self.assertEqual(values["source_sha256"], hashlib.sha256(NOTE.encode()).hexdigest())

        candidate = self.write("candidates/opus.md", "坑" * 5000)
        candidate_rel = os.path.relpath(candidate, self.repo)
        first = self.discussion("discussions/a.md", "fable", 1,
                                values["source_sha256"])
        second = self.discussion("discussions/b.md", "opus", 1,
                                 values["source_sha256"])
        done = self.memory("consolidate", self.MAIN, "--candidate", candidate_rel,
                           "--discussion", first, "--discussion", second, "--by", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.raw(self.MAIN), ("坑" * 5000).encode())
        self.assertIsNone(self.raw(self.INBOX))
        consumed = []
        for here, _, files in os.walk(os.path.join(self.repo, "memory")):
            consumed.extend(os.path.join(here, name) for name in files
                            if name.endswith(".consumed"))
        self.assertEqual(len(consumed), 1, consumed)  # pin:A5 一份 snapshot 一份 consumed
        with open(consumed[0], "rb") as handle:
            self.assertEqual(handle.read(), NOTE.encode())
        events = [row for row in self.events() if row["kind"] == "memory.consolidated"]
        self.assertEqual(len(events), 1, self.events())  # pin:A5 一次整理一筆事件
        self.assertFalse({"cap_from", "cap_to"} & set(events[0]), events[0])

        checked = self.memory("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("memory: memory/model/opus.md 5000 字元 / 暫存區 0 條未整理",
                      checked.stdout)
        self.assertEqual(self.tickets_on_disk(), [])

        before = (self.raw(self.MAIN), self.raw(self.INBOX))
        unknown = self.memory("consolidate", self.MAIN,
                              "--new-cap", "4000", "--reason", "x")
        self.assertEqual(unknown.returncode, 2, unknown.stdout + unknown.stderr)
        self.assertIn("不認得 '--new-cap'", unknown.stderr)
        self.assertEqual((self.raw(self.MAIN), self.raw(self.INBOX)), before)


class GateKeepsOnlyLayerPolicy(MemoryInboxWorld):

    def setUp(self):
        super().setUp()
        for name in ("test_memory", "test_no_project_names", "test_routing_cards"):
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)

    @staticmethod
    def body(chars):
        return "- " + "字" * (chars - 3) + "\n"

    def committed_branch(self, branch, text):
        where = self.worktree(branch)
        self.write("memory/model/opus.md", text, where=where)
        self.git("add", "-A", cwd=where)
        self.git("commit", "-q", "-m", "#134 改記憶卡", cwd=where)
        return where

    def run_gate(self, where):
        return subprocess.run(["sh", os.path.join(where, "scripts", "gate.sh"),
                               "--branch", "--ticket", "134", "--no-auto-fix"],
                              cwd=where, env=self.env(), capture_output=True,
                              text=True, timeout=120)

    def test_a6_length_growth_passes_but_a_new_wrong_layer_line_still_blocks(self):
        """A6 1990→2500 不再是 gate 關卡；新增錯層行仍由原 lint 關卡擋下。"""
        self.write("memory/model/opus.md", self.body(1990))
        self.make_ticket(134, state="Running", role="worker",
                         allowed_write_paths=["memory/model/opus.md"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "#134 的主線夾具")

        clean = self.committed_branch("t134-clean", self.body(2500))
        lint = self.memory("lint", "--base", "main", "--json", cwd=clean)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertNotIn("over_cap", json.loads(lint.stdout))
        gate = self.run_gate(clean)
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.assertNotIn("本分支讓記憶卡超上限", gate.stderr)
        self.assertNotIn("memory: cap ", gate.stderr)

        bad = self.committed_branch(
            "t134-bad", self.body(2500) + "- 改用 subprocess.run(x) 收尾\n")
        blocked = self.run_gate(bad)
        self.assertEqual(blocked.returncode, 4, blocked.stdout + blocked.stderr)
        self.assertIn("分支改到的記憶檔有錯層內容", blocked.stderr)


class ConsolidatorInstructions(MemoryInboxWorld):

    def test_a7_ticket_cards_template_and_filled_dispatch_only_give_the_criterion(self):
        """A7 整理票、兩份文字來源與填好派工文只有成本判準，沒有字數目標。"""
        self.seed_opener()
        checked = self.memory("check")
        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        rows = self.tickets_on_disk()
        self.assertEqual(len(rows), 1, rows)  # pin:A7 由 A1 觸發一張整理票
        row = rows[0]
        contract = row["objective"] + "\n" + "\n".join(row["acceptance"])
        self.assertIn(CRITERION, contract)
        for old in ("上限", "cap_history", "字元"):
            self.assertNotIn(old, contract)

        shutil.copy(os.path.join(SOURCE_ROOT, "scripts", "consolidate-memory.sh"),
                    os.path.join(self.repo, "scripts", "consolidate-memory.sh"))
        shutil.copy(os.path.join(SOURCE_ROOT, "templates", "dispatch-consolidator.md"),
                    os.path.join(self.repo, "templates", "dispatch-consolidator.md"))
        self.install_rules_sources()
        for rel in ("templates/dispatch-consolidator.md", "memory/role/consolidator.md"):
            text = self.read(rel)
            self.assertIn(CRITERION, text, rel)
            for old in ("<cap>", "<N>", "提高上限", "超過上限", "cap_history"):
                self.assertNotIn(old, text, rel)

        stdin = os.path.join(self.home, "consolidator-stdin.md")
        agent = os.path.join(self.home, "fake-consolidator.sh")
        write_executable(agent, "#!/bin/sh\ncat > \"$AC_TEST_STDIN\"\n"
                               "mkdir -p \"$(dirname \"$AC_DISCUSSION\")\"\n"
                               "printf '%s\\n' '---' 'model: fable' '---' > \"$AC_DISCUSSION\"\n")
        conf = json.loads(self.read("board/config.json"))
        conf["worker"] = {"command": "sh %s" % agent, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(consolidator=[fixture_candidate("fable")])
        launched = self.run_sh("scripts/consolidate-memory.sh", row["id"],
                               env=self.env(AC_TEST_STDIN=stdin))
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        with open(stdin, encoding="utf-8") as handle:
            dispatch = handle.read()
        self.assertIn(CRITERION, dispatch)
        for old in ("提高上限", "超過上限", "cap_history"):
            self.assertNotIn(old, dispatch)


class StaticContracts(MemoryInboxWorld):

    def test_a8_the_removed_cap_execution_tokens_have_no_remaining_lines(self):
        """A8 票面釘死的執行點 grep 在指定路徑輸出 0 行。"""
        paths = ["scripts", "templates", "board/config.json", "memory",
                 "docs/MEMORY.md", "docs/ROLES.md", "docs/DISCUSSION.md",
                 "docs/REHEARSAL.md", "docs/FLOW.html",
                 "docs/reference/role-procedures.md", "README.md", "docs/DESIGN.md",
                 "code-map/catalog.json"]
        pattern = ("cap_chars|cap_history|over_cap|inbox_max_lines|new-cap|new_cap|"
                   "DEFAULT_CAP|memory: cap|<cap>|raise_allowed")
        done = subprocess.run(["grep", "-R", "-nE", pattern, *paths],
                              cwd=SOURCE_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")

    def test_a9_the_probe_has_a_real_pending_precondition_and_names_the_new_event(self):
        """A9 無 inbox 的負控不觸發；加一條 note 才發 pending，探針與守衛同名。"""
        source = open(os.path.join(SOURCE_ROOT, "tests", "test_auto_fix.py"),
                      encoding="utf-8").read()
        probe = re.search(r'^AGENT_PROBES = """(.*?)"""', source, re.MULTILINE | re.DOTALL)
        self.assertTrue(probe, "找不到 AGENT_PROBES")
        self.assertTrue("memory/model/huge-probe.inbox.md" in probe.group(1),
                        "AGENT_PROBES 沒有建立 inbox 探針")
        guard = re.search(r'    def assert_the_board_is_untouched\(.*?(?=\n    def |\nclass )',
                          source, re.DOTALL)
        self.assertTrue(guard, "找不到 assert_the_board_is_untouched")
        self.assertTrue('"memory.pending"' in guard.group(0),
                        "看板不變守衛沒有斷言 memory.pending")

        self.write("memory/model/huge-probe.md", "探" * 30)
        without = self.memory("check")
        self.assertEqual(without.returncode, 0, without.stdout + without.stderr)
        self.assertNotIn("暫存區 1 條未整理", without.stdout)
        self.assertEqual(self.pending_events(), [])

        self.write("memory/model/huge-probe.inbox.md", NOTE)
        with_note = self.memory("check")
        self.assertEqual(with_note.returncode, 1, with_note.stdout + with_note.stderr)
        self.assertIn("暫存區 1 條未整理", with_note.stdout)
        self.assertEqual(len(self.pending_events()), 1, self.events())  # pin:A9 一條 note 一筆事件

    def test_a10_docs_share_the_contract_and_project_memory_stays_out(self):
        """A10 十份文件同列三句契約；project 層有 inbox 仍不整理。"""
        docs = ["docs/MEMORY.md", "memory/role/README.md", "docs/ROLES.md",
                "docs/DISCUSSION.md", "docs/REHEARSAL.md", "docs/FLOW.html",
                "docs/reference/role-procedures.md", "README.md", "docs/DESIGN.md",
                "code-map/catalog.json"]
        for rel in docs:
            with self.subTest(file=rel):
                text = open(os.path.join(SOURCE_ROOT, rel), encoding="utf-8").read()
                self.assertTrue("暫存區" in text, "%s 缺暫存區契約" % rel)
                self.assertTrue("不設固定字數上限" in text,
                                "%s 缺不設固定字數上限" % rel)
                self.assertTrue(CRITERION in text, "%s 缺成本判準" % rel)

        self.write("memory/project/pipeline.md", "專案決策\n")
        self.write("memory/project/pipeline.inbox.md", NOTE)
        checked = self.memory("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.pending_events(), [])
        self.assertEqual(self.tickets_on_disk(), [])


if __name__ == "__main__":
    unittest.main()
