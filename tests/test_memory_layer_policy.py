"""`scripts/memory.py lint`:記憶分層的契約做成 note / consolidate / 閘門共用的一份判斷
(#87 A33–A36,原 #79;裁示原話 Q2 / Q3)。

角色與模型記憶只留通用原則;專案記憶留設計理由。路徑、行號、呼叫、旗標、commit 指紋
是會過期的定位,去 repo map / reference / 票。五條 rule(path / line / call / flag / sha)
是穩定的 ID。這一組問三件事:

1. **矩陣**:每條規則在嚴格層(role / model)與專案層各有正例;模組名稱、時間、日期、
   全形括號、破折號這類長得像卻不是的,一條都不准誤判;精確的 allow marker 只豁免一條
   規則的一個逐字值,無效的 marker 自己就是一條命中。
2. **拒絕發生在寫入之前**:`note`、`consolidate` 的真入口碰到錯層內容 → 檔、事件一個
   位元組都不動。
3. **閘門只量分支改到的記憶檔**:候選記憶插一條函式呼叫 → `gate.sh --branch --ticket` 紅,
   診斷有檔、行、rule 與建議去處。

期望值(rule 名、逐字值、行號)寫死在這裡,不從 memory.py 讀回來。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import SCRIPTS, Sandbox  # noqa: E402
from test_memory_lifecycle import Consolidation  # noqa: E402

sys.path.insert(0, SCRIPTS)
import memory  # noqa: E402

DECISIONS = "| 編號 | 裁示 |\n|---|---|\n| D-013 | 記憶只留原則 |\n"
MARK = '<!-- memory-allow rule=%s value="%s" reason="%s" ref="%s" -->'

# (正文, 嚴格層該紅的 rule, 專案層該紅的 rule)
MATRIX = (
    ("照 docs/ROLES.md 的分工走", ["path"], []),
    ("停在 gate:42 那一段", ["line"], ["line"]),
    ("看 #L88 那一行", ["line"], ["line"]),
    ("改 line 12 的判斷", ["line"], ["line"]),
    ("改用 os.kill(pid) 收尾", ["call"], ["call"]),
    ("記得帶 --no-auto-fix 再跑", ["flag"], ["flag"]),
    ("刪的時候是 `rm -rf work`", ["flag"], ["flag"]),
    ("基準是 a1b2c3d 那一版", ["sha"], ["sha"]),
    ("指紋 `deadbeefcafe0123` 對得上才收", ["sha"], ["sha"]),
)
# 長得像、卻不是:模組名稱、時間、日期、純數字、全形括號、破折號、code span 外的短橫。
NOT_HITS = (
    "memory 模組負責整理與上限",
    "10:30 開會,12:30:45 收工",
    "2026-10-03 的裁示 D-013",
    "連續 1234567 次都綠",
    "原則(說明)寫在括號裡",
    "這一句 —— 是破折號",
    "-x 寫在 code span 外面不算旗標",
    "line 這個字後面沒有數字",
    "abcdefgh 不是十六進位",
)


def rules_of(text, layer):
    bad, _ = memory.lint_text(text, layer, DECISIONS)
    return [row["rule"] for row in bad]


class TheMatrix(unittest.TestCase):

    def test_a33_each_rule_hits_its_positive_case_per_layer(self):
        """**變異**:把 `LAYER_ALLOWS` 的 project 放寬清單拿掉 → 專案層 path 那一格紅。"""
        for text, strict, project in MATRIX:
            with self.subTest(text=text):
                self.assertEqual(rules_of(text, "role"), strict)
                self.assertEqual(rules_of(text, "model"), strict)
                self.assertEqual(rules_of(text, "project"), project)

    def test_a33_look_alikes_are_not_hits(self):
        for text in NOT_HITS:
            with self.subTest(text=text):
                self.assertEqual(rules_of(text, "role"), [])

    def test_a33_the_offending_value_and_a_destination_are_named(self):
        bad, _ = memory.lint_text("第一行沒事\n改用 os.kill(pid) 收尾", "role", DECISIONS,
                                  first_line=5)
        self.assertEqual([(row["line"], row["rule"], row["value"]) for row in bad],
                         [(6, "call", "os.kill(pid)")])
        self.assertTrue(bad[0]["destination"])


class TheAllowMarker(unittest.TestCase):
    """精確 marker:只豁免同一行、指定 rule、逐字 value;要有 reason 與存在的 ref。"""

    LINE = "導覽:docs/ROLES.md "

    def test_a33_an_exact_marker_exempts_exactly_that_value(self):
        """**變異 M14**:marker 改成整行豁免(不比 value)→ 第二條(別的值)紅。"""
        text = self.LINE + MARK % ("path", "docs/ROLES.md", "navigation-only", "D-013")
        bad, allowed = memory.lint_text(text, "role", DECISIONS)
        self.assertEqual(bad, [])
        self.assertEqual([(row["rule"], row["value"], row["ref"]) for row in allowed],
                         [("path", "docs/ROLES.md", "D-013")])
        two = "docs/ROLES.md 與 docs/MEMORY.md " + MARK % ("path", "docs/ROLES.md",
                                                         "navigation-only", "D-013")
        bad, allowed = memory.lint_text(two, "role", DECISIONS)
        self.assertEqual([(row["rule"], row["value"]) for row in bad],
                         [("path", "docs/MEMORY.md")])
        self.assertEqual(len(allowed), 1)

    def test_a33_invalid_markers_are_hits_and_exempt_nothing(self):
        cases = {
            "別的值": MARK % ("path", "docs/OTHER.md", "navigation-only", "D-013"),
            "萬用字元": MARK % ("path", "docs/*.md", "navigation-only", "D-013"),
            "沒有理由": MARK % ("path", "docs/ROLES.md", "", "D-013"),
            "裁示不存在": MARK % ("path", "docs/ROLES.md", "navigation-only", "D-999"),
            "規則不對": MARK % ("flag", "docs/ROLES.md", "navigation-only", "D-013"),
            "不認得的規則": MARK % ("everything", "docs/ROLES.md", "navigation-only", "D-013"),
        }
        for name, marker in cases.items():
            with self.subTest(name):
                bad, allowed = memory.lint_text(self.LINE + marker, "role", DECISIONS)
                self.assertEqual(sorted(row["rule"] for row in bad), ["marker", "path"])
                self.assertEqual(allowed, [])

    def test_a33_a_marker_on_another_line_does_not_reach(self):
        text = self.LINE + "\n" + MARK % ("path", "docs/ROLES.md", "navigation-only", "D-013")
        self.assertEqual(sorted(rules_of(text, "role")), ["marker", "path"])


class LintBase(Sandbox):

    config_extra = {"memory": {"unit": "chars", "cap_chars": 2000, "consolidator": "fable",
                               "raise_allowed": True,
                               "applies_to": ["memory/model/*.md", "memory/role/*.md"],
                               "inbox_suffix": ".inbox.md"}}

    def setUp(self):
        super().setUp()
        self.write("docs/DECISIONS.md", DECISIONS)

    def memory(self, *args, **kwargs):
        return self.run_py("scripts/memory.py", *args, **kwargs)


class TheLintCommand(LintBase):

    def test_a35_json_lists_what_it_scanned_and_the_exceptions(self):
        self.write("memory/role/clean.md", "- 原則一條\n- 導覽 docs/ROLES.md "
                   + MARK % ("path", "docs/ROLES.md", "navigation-only", "D-013") + "\n")
        done = self.memory("lint", "--file", "memory/role/clean.md", "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = json.loads(done.stdout)
        self.assertEqual(data["scanned"], 1)
        self.assertEqual(data["files"], ["memory/role/clean.md"])
        self.assertEqual([(row["file"], row["line"], row["rule"]) for row in data["allowed"]],
                         [("memory/role/clean.md", 2, "path")])
        self.assertTrue(data["ok"])

    def test_a35_an_empty_scan_is_not_a_pass(self):
        done = self.memory("lint", "--json")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["scanned"], 0)
        self.assertIn("空掃描", done.stderr)

    def test_a35_front_matter_is_not_body_and_lines_count_from_the_file(self):
        self.write("memory/model/opus.md",
                   "---\ncap_chars: 2000\nsource: a1b2c3d\n---\n- 原則\n- 用 --force 硬推\n")
        done = self.memory("lint", "--file", "memory/model/opus.md", "--json")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        rows = json.loads(done.stdout)["violations"]
        self.assertEqual([(row["line"], row["rule"], row["value"]) for row in rows],
                         [(6, "flag", "--force")])


class RefusedBeforeAnythingIsWritten(LintBase):
    """A34:真入口(note / consolidate)拒絕在寫檔、發事件之前。"""

    def test_a34_note_refuses_a_call_in_the_role_layer_before_writing(self):
        """**變異**:cmd_note 的 lint 搬到 append_line 之後 → inbox 有那一行,紅。"""
        inbox = os.path.join(self.repo, "memory", "role", "implementer.inbox.md")
        done = self.memory("note", "role", "implementer", "改用 os.kill(pid) 收尾",
                           "--ticket", "7", "--by", "worker@opus")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("rule=call", done.stderr)
        self.assertFalse(os.path.exists(inbox), "被拒的那一行落地了")
        self.assertNotIn("memory.noted", self.kinds())

    def test_a34_the_project_layer_may_cite_a_design_path(self):
        done = self.memory("note", "project", "pipeline", "分層理由見 docs/MEMORY.md",
                           "--ticket", "7", "--by", "worker@opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory.noted", self.kinds())

    def test_a34_harvest_goes_through_the_same_refusal(self):
        evidence = self.write("EVIDENCE.md", "## 記憶\n"
                              "- python3 scripts/memory.py note model opus "
                              "\"跑 --no-cache 才準\" --ticket 7 --by worker@opus\n",
                              where=self.home)
        done = self.memory("harvest", evidence)
        self.assertIn("拒絕 evidence 行", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "memory", "model",
                                                     "opus.inbox.md")))


class ConsolidateRefusesTheCandidate(Consolidation):

    def setUp(self):
        super().setUp()
        self.write("docs/DECISIONS.md", DECISIONS)

    def test_a34_a_candidate_with_a_wrong_layer_line_moves_nothing(self):
        """**變異**:拿掉 cmd_consolidate 的 lint → 主檔被換、有 consolidated 事件,紅。"""
        self.seed(2)
        self.write(self.CANDIDATE, "- 整理後的原則 (#10, 2026-10-01, fable@fable)\n"
                                   "- 收尾用 os.kill(pid)\n")
        sha = self.sha_of_first(2)
        main, inbox = self.raw(self.MAIN), self.raw(self.INBOX)
        done = self.memory("consolidate", self.MAIN, "--candidate", self.CANDIDATE,
                           "--discussion", self.talk("d/a.md", "fable", 2, sha),
                           "--discussion", self.talk("d/b.md", "opus", 2, sha), "--by", "fable")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("rule=call", done.stderr)
        self.assertEqual(self.raw(self.MAIN), main, "主檔被動了")
        self.assertEqual(self.raw(self.INBOX), inbox, "inbox 被動了")
        self.assertEqual(self.consumed(), [])
        self.assertEqual(self.consolidated(), [])


class TheGateLintsTheChangedMemory(LintBase):
    """A36:候選記憶插一條函式呼叫 → `gate.sh --branch --ticket` 紅,診斷有檔、行、rule、去處。"""

    def test_a36_a_call_in_a_changed_card_reds_the_gate(self):
        """**變異**:拿掉 gate.sh 的記憶 lint 那一段 → rc 不是 4,紅。"""
        self.write("memory/model/opus.md", "- 原則一條\n")
        self.make_ticket(7, allowed_write_paths=["memory/model/*.md"], state="Running")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的模型卡與票 #7")
        wt = self.worktree("t7")
        self.write("memory/model/opus.md", "- 原則一條\n- 收尾用 os.kill(pid)\n", where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 候選記憶插了一條呼叫", cwd=wt)
        done = self.run_sh(os.path.join(wt, "scripts", "gate.sh"), "--branch", "--ticket", "7",
                           "--no-auto-fix", cwd=wt)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        line = [l for l in done.stderr.splitlines() if "memory/model/opus.md:2" in l]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn("rule=call", line[0])
        self.assertIn("建議去處", line[0])
        self.assertFalse(os.path.exists(self.log), "錯層記憶卻跑了測試")

    # D-042(#90 A7/A8):閘門只量**這條分支加了什麼** —— 新增行才 lint;本分支讓卡從上限內
    # 推到上限外才擋。主線上 commit 卡 → worktree t7 改卡並 commit → 真 gate.sh。

    OLD_HIT = "- 舊句用 os.kill(pid)\n"
    CARD = "memory/model/opus.md"

    def branch_gate(self, on_main, on_branch, config=None):
        """`on_main` / `on_branch`:{相對路徑: 全文}。回 gate 的 CompletedProcess。"""
        if config is not None:
            self.write("board/config.json", json.dumps(config, ensure_ascii=False, indent=2))
        for rel, text in on_main.items():
            self.write(rel, text)
        self.make_ticket(7, allowed_write_paths=["memory/*/*.md"], state="Running")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的記憶卡與票 #7")
        wt = self.worktree("t7")
        for rel, text in on_branch.items():
            self.write(rel, text, where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 改了記憶卡", cwd=wt)
        return self.run_sh(os.path.join(wt, "scripts", "gate.sh"), "--branch", "--ticket", "7",
                           "--no-auto-fix", cwd=wt)

    @staticmethod
    def body(chars):
        """正文恰 `chars` 個字元的一條(`- ` + 字… + 換行)。"""
        return "- " + "字" * (chars - 3) + "\n"

    @staticmethod
    def capped(body, cap=100):
        return "---\ncap_chars: %d\n---\n%s" % (cap, body)

    def lint_lines(self, done):
        return [l for l in done.stderr.splitlines() if l.startswith("memory: lint ")]

    def test_only_lines_added_on_the_branch_are_linted(self):
        """A7(a):主線上的卡已有一行命中,分支只在卡尾加一行乾淨原則 → 不擋。
        **變異 M5**:gate 對改到的卡整檔 lint(不看改前)→ 這一條紅。"""
        main = "- 原則一條\n" + self.OLD_HIT
        done = self.branch_gate({self.CARD: main}, {self.CARD: main + "- 新的乾淨原則\n"})
        self.assertNotIn("記憶檔有錯層內容", done.stderr, done.stdout + done.stderr)
        self.assertNotIn("memory: lint", done.stderr)

    def test_a_new_hit_is_named_and_the_old_one_is_not(self):
        """A7(b):同一張主線卡,分支另加一行呼叫(第 3 行)→ rc 4,恰一條 lint 行指名第 3 行。"""
        main = "- 原則一條\n" + self.OLD_HIT
        done = self.branch_gate({self.CARD: main},
                                {self.CARD: main + "- 新句用 subprocess.run(x)\n"})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        lines = self.lint_lines(done)
        self.assertEqual(len(lines), 1, done.stderr)
        self.assertIn("memory/model/opus.md:3 ", lines[0])
        self.assertFalse([l for l in lines if "memory/model/opus.md:2 " in l], done.stderr)

    def test_a_rewritten_old_hit_is_named(self):
        """A7(c):分支改寫舊命中那一行(仍含呼叫)→ rc 4,第 2 行被點名(改到的行就要乾淨)。"""
        done = self.branch_gate({self.CARD: "- 原則一條\n" + self.OLD_HIT},
                                {self.CARD: "- 原則一條\n- 舊句改寫後仍用 os.kill(pid)\n"})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        lines = self.lint_lines(done)
        self.assertEqual(len(lines), 1, done.stderr)
        self.assertIn("memory/model/opus.md:2 ", lines[0])

    def test_a_card_pushed_over_its_cap_by_the_branch_reds_the_gate(self):
        """A8(a):改前 90、改後 110(上限 100)→ 擋,那一行三個數字逐字對。
        **變異 M6**:只看改後 > 上限 → (b) 紅;**M7**:拿掉超上限那一手 → 這一條紅。"""
        done = self.branch_gate({self.CARD: self.capped(self.body(90))},
                                {self.CARD: self.capped(self.body(90) + self.body(20))})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("機械格不合 —— 本分支讓記憶卡超上限", done.stderr)
        self.assertIn("memory: cap memory/model/opus.md 90 -> 110 / 100\n", done.stderr)
        self.assertFalse(os.path.exists(self.log), "超上限卻跑了測試")

    def test_a_card_already_over_its_cap_is_not_the_gates_to_stop(self):
        """A8(b):改前 120、改後 125 → 不擋(改前已超標的卡歸整理票)。"""
        done = self.branch_gate({self.CARD: self.capped(self.body(120))},
                                {self.CARD: self.capped(self.body(120) + self.body(5))})
        self.assertNotIn("本分支讓記憶卡超上限", done.stderr, done.stdout + done.stderr)
        self.assertNotIn("memory: cap ", done.stderr)

    def test_a_card_landing_exactly_on_its_cap_passes(self):
        """A8(c):改前 90、改後恰 100 → 不擋(上限是「不得超過」)。"""
        done = self.branch_gate({self.CARD: self.capped(self.body(90))},
                                {self.CARD: self.capped(self.body(90) + self.body(10))})
        self.assertNotIn("本分支讓記憶卡超上限", done.stderr, done.stdout + done.stderr)

    def test_a_new_card_over_the_configured_cap_counts_from_zero(self):
        """A8(d):分支新增一張沒有 cap_chars 的卡、正文超過沙盒設定的上限 → 擋,改前 0。"""
        cap = self.config_extra["memory"]["cap_chars"]
        done = self.branch_gate({}, {"memory/role/fresh.md": self.body(cap + 1)})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("memory: cap memory/role/fresh.md 0 -> %d / %d\n" % (cap + 1, cap),
                      done.stderr)

    def test_inbox_and_project_cards_are_not_measured(self):
        """A8(e):*.inbox.md 與 memory/project/ 卡寫到超過 → 不擋(check 不量它們)。
        project 這一半要有意義:沙盒的 applies_to 也列 memory/project/(同真設定)。"""
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["applies_to"] = ["memory/model/*.md", "memory/role/*.md",
                                        "memory/project/*.md"]
        cap = conf["memory"]["cap_chars"]
        done = self.branch_gate({}, {"memory/model/opus.inbox.md": self.body(cap + 50),
                                     "memory/project/pipeline.md": self.body(cap + 50)},
                                config=conf)
        self.assertNotIn("本分支讓記憶卡超上限", done.stderr, done.stdout + done.stderr)
        self.assertNotIn("memory: cap ", done.stderr)

    def test_a_new_hit_and_a_cap_crossing_are_both_listed(self):
        """A8(f):同一分支新增行命中 + 超上限 → 兩行 `機械格不合 —— ` 都在 stderr,
        gate 狀態檔的 note 兩行全列(A1)。"""
        done = self.branch_gate({self.CARD: self.capped(self.body(90))},
                                {self.CARD: self.capped(self.body(90)
                                                        + "- 新句用 subprocess.run(x)\n")})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        mech = [l for l in done.stderr.splitlines() if "機械格不合 —— " in l]
        self.assertEqual(len(mech), 2, done.stderr)
        self.assertTrue(any("錯層內容" in l for l in mech), mech)
        self.assertTrue(any("本分支讓記憶卡超上限" in l for l in mech), mech)
        note = self.status_of(7, kind="gate")["note"]
        for line in mech:
            self.assertIn(line, note)


if __name__ == "__main__":
    unittest.main()
