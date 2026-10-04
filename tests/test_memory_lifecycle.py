"""#76 超標與過期記憶:唯讀檢查不留痕、恰好一張整理票、整理成功前不消耗原文。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / 手算)
A1 | unit | 超標沙盒跑 check --read-only、check-stale --read-only | rc 1;tickets/ events memory/ reports/ 逐位元組不變 | 票面 A1 原話
A2 | unit | 票 #1 Done #2 Cancelled #3 Ready,inbox 七行             | 七行分類依序、rc 1/0;別的票庫的 #3 不算   | 票面 A2 原話
A3 | unit | 開著的 consolidator 票 glob `memory/role/*.md`          | 不開新票、印票號;Cancelled 後開 1 張;併發 1 張 | 票面 A3 原話
A4 | unit | 只有 worker.inbox.md 21 行;只因 needs-review           | subject 行數形、needs_verifier false;subject 含 needs-review | 票面 A4 原話
A5 | unit | consolidate 七種不合格輸入                              | rc 非 0、stderr 非空、什麼都沒動            | 票面 A5 原話
A6 | unit | snapshot → 兩份討論 → note 第 4 條 → consolidate        | 主檔=候選、inbox 只剩第 4 條、consumed=前 3 行 | 票面 A6 原話;sha256 用 hashlib 手算
A7 | unit | new-session worker / main 在超標沙盒                    | worker 不開票且不停工;main 開 1 張          | 票面 A7 原話
A8 | unit | 讀 scripts/gate.sh 與 tests/test_memory.py              | 對照格有 test_memory_lifecycle;舊案例改用 --candidate | 票面 A8 原話

## 介面字串(每條斷言靠哪個字串;全部抄自票面)
OVER_LINE  = "memory/model/opus.md **2500 / 2000 超過**"   ← A1
STALE_LINE = "memory: <inbox>:<行號> <分類> <來源>"          ← A2(逐行 assertEqual)
ALREADY    = "已經有一張開著的整理票 #<票號>"               ← A3
INBOX_SUBJ = "inbox 21 行,上限 20 行"                       ← A4
SNAPSHOT   = "source_lines: K" / "source_sha256: <hex>"      ← A5 A6
GOES_ON    = "不停工"                                         ← A7

## 怎麼做假
control_harness.Sandbox 的拋棄式真 repo;memory.py / new-session.sh 一律子行程跑。不上埠、
不起服務、不碰真模型(沙盒的 claude 絆線照常)。討論檔是手寫的固定文字,不起兩個模型。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;既有 test_memory.py / test_new_session.py
的綠由閘門的 memory.py 對照格跑,這一份不重跑它們。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, Sandbox  # noqa: E402

TAGS = ["memory-lifecycle"]

OVER_LINE = "memory/model/opus.md **2500 / 2000 超過**"
ALREADY = "已經有一張開著的整理票 #%s"
INBOX_SUBJ = "inbox 21 行,上限 20 行"
GOES_ON = "不停工"
FRONT = "---\ncap_chars: 2000\ncap_history: []\n---\n"
BOTH_LAYERS = {"memory": {"unit": "chars", "cap_chars": 2000, "consolidator": "fable",
                          "raise_allowed": True,
                          "applies_to": ["memory/model/*.md", "memory/role/*.md"],
                          "inbox_suffix": ".inbox.md"}}


def body_of(raw):
    """front matter 之後的正文(`docs/MEMORY.md`:量的是正文)。"""
    text = raw.decode("utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 3)
        if end >= 0:
            return text[end + 5:]
    return text


class Lifecycle(Sandbox):

    config_extra = BOTH_LAYERS

    def memory(self, *args, cwd=None):
        return self.run_py("scripts/memory.py", *args, cwd=cwd)

    def raw(self, rel):
        path = os.path.join(self.repo, rel)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def tree(self, rel):
        """目錄的逐位元組快照:相對路徑 → 內容(目錄記成 `<dir>`)。不存在 → {}。"""
        out = {}
        top = os.path.join(self.repo, rel)
        for here, dirs, files in os.walk(top):
            for name in dirs:
                out[os.path.relpath(os.path.join(here, name), self.repo)] = "<dir>"
            for name in files:
                path = os.path.join(here, name)
                with open(path, "rb") as handle:
                    out[os.path.relpath(path, self.repo)] = handle.read()
        return out

    def world(self):
        return {"tickets": self.tree("tickets"), "memory": self.tree("memory"),
                "reports": self.tree("reports"),
                "events": self.raw(os.path.join("board", "events.jsonl"))}

    def ticket_ids(self):
        return sorted(one["id"] for one in self.tickets_on_disk())

    def new_tickets(self, before):
        return [one for one in self.tickets_on_disk() if one["id"] not in before]

    def consumed(self):
        out = []
        for here, _, files in os.walk(os.path.join(self.repo, "memory")):
            out.extend(os.path.relpath(os.path.join(here, name), self.repo)
                       for name in files if name.endswith(".consumed"))
        return sorted(out)

    def consolidated(self):
        return [row for row in self.events() if row["kind"] == "memory.consolidated"]

    def entry(self, text, mark):
        return "- %s (%s2026-09-01, worker@opus)\n" % (text, mark + ", " if mark else "")


class A1ReadOnlyLeavesNoTrace(Lifecycle):

    def test_a1_read_only_check_and_check_stale_change_nothing(self):
        """A1 check --read-only 與 check-stale --read-only:rc 1,四處逐位元組不變。"""
        self.make_ticket(5, state="Done")
        self.write("memory/model/opus.md", "坑" * 2500)
        self.write("memory/role/worker.inbox.md",
                   self.entry("來源已結案的一條", "#5")
                   + "".join(self.entry("第 %d 條" % i, "") for i in range(20)))
        before = self.world()
        check = self.memory("check", "--read-only")
        stale = self.memory("check-stale", "--read-only")
        after = self.world()
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertIn(OVER_LINE, check.stdout)
        self.assertNotIn(os.path.join("memory", ".lock"), after["memory"],
                         "唯讀指令留下了 memory/.lock")
        for key in ("tickets", "events", "memory", "reports"):
            self.assertEqual(before[key], after[key], "唯讀指令動了 %s" % key)
        full = self.memory("check")
        mine = [line for line in check.stdout.splitlines() if OVER_LINE in line]
        theirs = [line for line in full.stdout.splitlines() if OVER_LINE in line]
        self.assertEqual(mine, theirs, "check --read-only 與 check 印的不是同一行")


class A2StaleSources(Lifecycle):

    INBOX = "memory/role/worker.inbox.md"
    EXPECTED = ["1 needs-review #1 Done", "2 needs-review #2 Cancelled",
                "3 fresh #3 Ready", "4 unknown-ticket #99", "5 no-source -",
                "6 foreign #T-5", "7 unparsed -"]

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="Done")
        self.make_ticket(2, state="Cancelled")
        self.make_ticket(3, state="Ready")
        self.write("memory/role/worker.md", self.entry("主卡上的一條", "#1"))
        self.write(self.INBOX, self.entry("一", "#1") + self.entry("二", "#2")
                   + self.entry("三", "#3") + self.entry("四", "#99")
                   + self.entry("五", "") + self.entry("六", "#T-5")
                   + "這一行不是條目\n")
        self.write("memory/project/decisions.md", self.entry("專案層", "#1"))
        self.write("memory/model/opus.inbox.md", self.entry("只有開著的", "#3"))

    def lines_of(self, out, rel):
        prefix = "memory: %s:" % rel
        return [line[len(prefix):] for line in out.splitlines() if line.startswith(prefix)]

    def test_a2_seven_entries_classify_in_order(self):
        """A2 七行分類依序;只讀 inbox;有 needs-review → rc 1。"""
        for extra in ([], ["--file", self.INBOX], ["--file", "memory/role/worker.md"]):
            with self.subTest(args=extra):
                done = self.memory("check-stale", "--read-only", *extra)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(self.lines_of(done.stdout, self.INBOX), self.EXPECTED,
                                 done.stdout + done.stderr)
                self.assertEqual(self.lines_of(done.stdout, "memory/role/worker.md"), [],
                                 "主卡不是 inbox,不該被分類")
                self.assertNotIn("memory/project", done.stdout, "project 層不在範圍")

    def test_a2_only_fresh_entries_exit_zero(self):
        """A2 沒有 needs-review → rc 0。"""
        done = self.memory("check-stale", "--read-only", "--file",
                           "memory/model/opus.inbox.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.lines_of(done.stdout, "memory/model/opus.inbox.md"),
                         ["1 fresh #3 Ready"], done.stdout + done.stderr)

    def test_a2_another_repo_with_the_same_number_does_not_count(self):
        """A2 跨 repo 同號:另一個目錄的 #3 是 Done,本 repo #3 仍判 fresh。"""
        other = os.path.join(self.home, "other")
        self.write("board/config.json", self.read("board/config.json"), where=other)
        self.write("tickets/3.json", json.dumps(dict(self.load_ticket(3), state="Done"),
                                                ensure_ascii=False), where=other)
        done = self.memory("check-stale", "--read-only", cwd=other)
        lines = self.lines_of(done.stdout, self.INBOX)
        self.assertIn("3 fresh #3 Ready", lines, done.stdout + done.stderr)


class A3OneConsolidationTicket(Lifecycle):

    def setUp(self):
        super().setUp()
        self.write("memory/role/opener.md", "規矩" * 1250)

    def test_a3_a_glob_scoped_open_ticket_covers_the_card(self):
        """A3 開著的 Draft 整理票 glob 蓋住 → 不開;改 Cancelled → 開 1 張。"""
        self.make_ticket(7, state="Draft", role="consolidator",
                         allowed_write_paths=["memory/role/*.md"])
        before = self.ticket_ids()
        done = self.memory("check")
        self.assertIn(ALREADY % 7, done.stdout, done.stdout + done.stderr)
        self.assertEqual(self.ticket_ids(), before, "已有整理票還是開了新票")

        self.make_ticket(7, state="Cancelled", role="consolidator",
                         allowed_write_paths=["memory/role/*.md"])
        done = self.memory("check")
        self.assertEqual(len(self.new_tickets(before)), 1, done.stdout + done.stderr)

    def test_a3_two_checks_at_once_open_exactly_one(self):
        """A3 無覆蓋票時兩個 check 同時跑 → tickets/ 恰好多 1 張。"""
        before = self.ticket_ids()
        procs = [subprocess.Popen(["python3", os.path.join(self.repo, "scripts", "memory.py"),
                                   "check"], cwd=self.repo, env=self.env(),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
        outputs = [proc.communicate(timeout=120) for proc in procs]
        self.assertEqual(len(self.new_tickets(before)), 1, outputs)


class A4TicketContents(Lifecycle):

    def test_a4_an_inbox_over_its_lines_opens_a_lines_shaped_ticket(self):
        """A4 主卡不存在、inbox 21 行 → 1 張;subject 行數形;needs_verifier 是 false。"""
        self.write("memory/role/worker.inbox.md",
                   "".join(self.entry("第 %d 條" % i, "") for i in range(21)))
        before = self.ticket_ids()
        done = self.memory("check")
        opened = self.new_tickets(before)
        self.assertEqual(len(opened), 1, done.stdout + done.stderr)
        row = opened[0]
        self.assertIn(INBOX_SUBJ, row["subject"])
        self.assertNotIn("字元", row["subject"])
        self.assertIn("memory/role/worker.md", row["allowed_write_paths"])
        self.assertIn("memory/role/worker.inbox.md", row["allowed_write_paths"])
        self.assertIs(row.get("needs_verifier"), False)

    def test_a4_needs_review_alone_opens_one_ticket(self):
        """A4 只因 needs-review(未超標)也開 1 張,subject 含 needs-review。"""
        self.make_ticket(1, state="Done")
        self.write("memory/role/reviewer.md", "短短一句。\n")
        self.write("memory/role/reviewer.inbox.md", self.entry("來源已結案", "#1"))
        before = self.ticket_ids()
        done = self.memory("check")
        opened = self.new_tickets(before)
        self.assertEqual(len(opened), 1, done.stdout + done.stderr)
        self.assertIn("needs-review", opened[0]["subject"])

    def test_a4_a_chars_over_subject_keeps_its_shape(self):
        """A4 字元超標的 subject 維持 `整理 <檔>(N 字元,上限 M)`。"""
        self.write("memory/model/opus.md", "坑" * 2500)
        before = self.ticket_ids()
        done = self.memory("check")
        opened = self.new_tickets(before)
        self.assertEqual(len(opened), 1, done.stdout + done.stderr)
        self.assertIn("整理 memory/model/opus.md(2500 字元,上限 2000)", opened[0]["subject"])


class Consolidation(Lifecycle):

    MAIN = "memory/model/opus.md"
    INBOX = "memory/model/opus.inbox.md"
    CANDIDATE = "candidates/opus.md"

    def seed(self, count):
        self.write(self.MAIN, FRONT + "舊的一條\n")
        self.write(self.INBOX, "".join(self.entry("待整理 %d" % i, "#%d" % (i + 10))
                                       for i in range(1, count + 1)))
        self.write(self.CANDIDATE, "- 整理後的原則 (#10, 2026-10-01, fable@fable)\n")

    def talk(self, name, model, lines, sha, verdicts=None, conclusion=True):
        verdicts = verdicts or {n: "保留" for n in range(1, lines + 1)}
        text = ("---\nmodel: %s\nsource_lines: %d\nsource_sha256: %s\n---\n\n"
                "## 第 1 輪 — %s\n- 主張:逐條處置\n" % (model, lines, sha, model))
        if conclusion:
            text += "\n## 結論\n" + "".join("- L%d: %s\n" % (n, verdicts[n])
                                           for n in sorted(verdicts))
        self.write(name, text)
        return name

    def sha_of_first(self, count):
        lines = self.raw(self.INBOX).splitlines(keepends=True)
        return hashlib.sha256(b"".join(lines[:count])).hexdigest()


class A5RefusalsTouchNothing(Consolidation):

    def test_a5_each_bad_input_is_refused_and_nothing_moves(self):
        """A5 七種不合格輸入:rc 非 0、stderr 非空,主檔 inbox 事件 consumed 都不變。"""
        cases = {
            "only-one-discussion": lambda sha: ["--candidate", self.CANDIDATE,
                                                "--discussion", self.talk("d/a.md", "fable", 2, sha)],
            "same-model": lambda sha: ["--candidate", self.CANDIDATE,
                                       "--discussion", self.talk("d/a.md", "fable", 2, sha),
                                       "--discussion", self.talk("d/b.md", "fable", 2, sha)],
            "sha-mismatch": lambda sha: ["--candidate", self.CANDIDATE,
                                         "--discussion", self.talk("d/a.md", "fable", 2, sha),
                                         "--discussion", self.talk("d/b.md", "opus", 2, "0" * 64)],
            "missing-L2": lambda sha: ["--candidate", self.CANDIDATE,
                                       "--discussion", self.talk("d/a.md", "fable", 2, sha),
                                       "--discussion", self.talk("d/b.md", "opus", 2, sha,
                                                                 verdicts={1: "保留"})],
            "candidate-over-cap": lambda sha: [
                "--candidate", self.write("candidates/big.md", "坑" * 2500) and "candidates/big.md",
                "--discussion", self.talk("d/a.md", "fable", 2, sha),
                "--discussion", self.talk("d/b.md", "opus", 2, sha)],
            "no-conclusion": lambda sha: ["--candidate", self.CANDIDATE,
                                          "--discussion", self.talk("d/a.md", "fable", 2, sha),
                                          "--discussion", self.talk("d/b.md", "opus", 2, sha,
                                                                    conclusion=False)],
            "no-candidate": lambda sha: ["--discussion", self.talk("d/a.md", "fable", 2, sha),
                                         "--discussion", self.talk("d/b.md", "opus", 2, sha)],
        }
        for name, build in cases.items():
            with self.subTest(case=name):
                self.seed(2)
                args = build(self.sha_of_first(2))
                main, inbox = self.raw(self.MAIN), self.raw(self.INBOX)
                events, consumed = self.raw("board/events.jsonl"), self.consumed()
                done = self.memory("consolidate", self.MAIN, *args, "--by", "fable")
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertTrue(done.stderr.strip(), "拒絕要說出理由")
                self.assertEqual(self.raw(self.MAIN), main, "主檔被動了")
                self.assertEqual(self.raw(self.INBOX), inbox, "inbox 被動了")
                self.assertEqual(self.raw("board/events.jsonl"), events, "事件被動了")
                self.assertEqual(self.consumed(), consumed, "多了 *.consumed")
                self.assertEqual(self.consolidated(), [], "有 memory.consolidated 事件")


class A6OnlyTheSnapshotIsConsumed(Consolidation):

    def test_a6_a_note_after_the_snapshot_survives(self):
        """A6 snapshot 後追加第 4 條 → consolidate 只吃前 3 行;重跑被拒。"""
        self.seed(3)
        original = self.raw(self.INBOX)
        snap = self.memory("snapshot", self.MAIN)
        self.assertEqual(snap.returncode, 0, snap.stdout + snap.stderr)
        lines = re.search(r"^source_lines: (\d+)$", snap.stdout, re.MULTILINE)
        sha = re.search(r"^source_sha256: ([0-9a-f]{64})$", snap.stdout, re.MULTILINE)
        self.assertTrue(lines and sha, snap.stdout)
        self.assertEqual(int(lines.group(1)), 3)
        self.assertEqual(sha.group(1), hashlib.sha256(original).hexdigest())
        verdicts = {1: "升格 memory/model/opus.md", 2: "歸檔", 3: "重複"}
        first = self.talk("d/a.md", "fable", 3, sha.group(1), verdicts)
        second = self.talk("d/b.md", "opus", 3, sha.group(1), verdicts)

        noted = self.memory("note", "model", "opus", "整理期間的第四條",
                            "--ticket", "4", "--by", "worker@opus")
        self.assertEqual(noted.returncode, 0, noted.stdout + noted.stderr)
        fourth = self.raw(self.INBOX)[len(original):]
        self.assertTrue(fourth, "note 沒有追加到 inbox")

        argv = ("consolidate", self.MAIN, "--candidate", self.CANDIDATE,
                "--discussion", first, "--discussion", second, "--by", "fable")
        done = self.memory(*argv)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(body_of(self.raw(self.MAIN)), body_of(self.raw(self.CANDIDATE)))
        self.assertEqual(self.raw(self.INBOX), fourth, "inbox 應只剩第 4 條")
        consumed = self.consumed()
        self.assertEqual(len(consumed), 1, consumed)
        self.assertTrue(os.path.basename(consumed[0]).startswith("opus.inbox.md."))
        self.assertEqual(self.raw(consumed[0]), original, "consumed 應是原前 3 行")
        rows = self.consolidated()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("source_lines"), 3)

        main, inbox = self.raw(self.MAIN), self.raw(self.INBOX)
        again = self.memory(*argv)
        self.assertNotEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(self.consolidated()), 1, "重跑多了一筆事件")
        self.assertEqual(self.raw(self.MAIN), main)
        self.assertEqual(self.raw(self.INBOX), inbox)


class A7OnlyTheMainLineOpens(Lifecycle):

    def setUp(self):
        super().setUp()
        self.write("memory/model/opus.md", "坑" * 2500)

    def test_a7_a_worker_goes_on_and_opens_nothing(self):
        """A7 worker 開場:rc 0、印不停工、tickets/ 不多票。"""
        before = self.ticket_ids()
        done = self.run_sh("scripts/new-session.sh", "worker", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(GOES_ON, done.stdout)
        self.assertEqual(self.ticket_ids(), before, "短命角色開了票")

    def test_a7_the_main_line_opens_one(self):
        """A7 main 開場 → 開 1 張整理票。"""
        before = self.ticket_ids()
        done = self.run_sh("scripts/new-session.sh", "main", "fable")
        opened = [one for one in self.new_tickets(before) if one.get("role") == "consolidator"]
        self.assertEqual(len(opened), 1, done.stdout + done.stderr)


class A8GateAndOldCases(unittest.TestCase):

    def source(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_a8_the_gate_runs_the_lifecycle_cases_for_memory_py(self):
        """A8 gate.sh 的 `scripts/memory.py)` 那一格加 test_memory_lifecycle。"""
        arm = re.search(r"^\s*scripts/memory\.py\)(.*)$", self.source("scripts/gate.sh"),
                        re.MULTILINE)
        self.assertTrue(arm, "gate.sh 找不到 scripts/memory.py) 那一格")
        self.assertIn("test_memory_lifecycle", arm.group(1))

    def test_a8_the_old_consolidate_cases_use_the_new_contract(self):
        """A8 test_memory.py 釘舊行為的案例改用 --candidate + 兩份討論。"""
        text = self.source("tests/test_memory.py")
        for name in ("test_the_inbox_is_merged_in_and_then_gone",
                     "test_a_note_during_consolidation_lands_in_a_new_inbox",
                     "test_still_over_the_cap_after_consolidating_is_not_reported_as_done",
                     "test_a_reasoned_raise_is_written_into_the_file_with_its_history"):
            with self.subTest(case=name):
                found = re.search(r"    def %s\(.*?(?=\n    def |\nclass |\Z)" % name,
                                  text, re.DOTALL)
                self.assertTrue(found, "%s 不見了(只准改,不准刪)" % name)
                self.assertIn("--candidate", found.group(0))


if __name__ == "__main__":
    unittest.main()
