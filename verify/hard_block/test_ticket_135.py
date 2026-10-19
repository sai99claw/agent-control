"""#135 一段 hard_block 設定同時約束三個執行點與事後越界守衛。

## 驗收表(期望值只取票面 objective / acceptance / test_plan)
A1 | integration | hard_block.paths 或 tests/**、verify/** 新增行命中 patterns | apply rc=5、gate preflight rc=4、land 拒收且點名路徑 | 票面驗收 1
A1 | integration | scripts/** 新增行命中 patterns；未設定 hard_block | apply / gate / land 不因硬擋拒收 | 票面驗收 1
A2 | integration | agent 改到 hard_block.dirs；未設定 dirs | auto-fix rc=8；未設定不查 repo 外 | 票面驗收 2
A3 | contract | 對票面指定範圍跑三個舊鍵的 git grep | 0 行(*.inbox.md 不算；verify_lint 歸 #138) | 票面驗收 3
A4 | contract | 讀 board/config.json、SCHEMA、DISPATCH-TEMPLATE | 三個子清單值與說明完全符合票面 | 票面驗收 4
A5 | contract | 讀指定既有 oracle 與 Sandbox 預設設定 | 舊鍵已遷移且每份仍釘 hard_block | 票面驗收 5 的 test_plan

## 介面字串(全部出自票面 objective / acceptance)
HARD_BLOCK = board/config.json 的 "hard_block": {"paths": [...], "dirs": [...], "patterns": [...]}
PATHS      = docs/DECISIONS.md、docs/VISION.md、scripts/sync-to-project.sh(票面驗收 4)
PATTERNS   = verify-case.py DEFAULT_FORBID 那四條殺行程字樣(本檔 PATTERNS 常數拆字寫,避開 F4);只查 tests/**、verify/** 新增行
RC         = apply.sh 5、gate.sh preflight 4、land.sh 拒收(既有拒收 rc 2)、auto-fix.sh 守衛 8
A3 GREP    = 票面驗收 3 那條 git grep 的 regex 與路徑原樣;*.inbox.md 不算

## 怎麼做假
tests/control_harness.Sandbox 建拋棄式真 git repo；apply.sh / gate.sh / land.sh / auto-fix.sh
都以子行程跑真腳本。land 的昂貴閘門換 GATE_STUB_GREEN；agent 是只寫夾具檔的 shell 替身。

## 不做
不起模型、不綁埠、不跑全套、不讀實作者產物、不從被測程式計算期望值。
"""
import difflib
import fnmatch
import json
import os
import re
import subprocess
import sys
import unittest

TAGS = ["hard-block"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import DEFAULT_CONFIG, GATE_STUB_GREEN, Sandbox, write_executable  # noqa: E402

PATHS = ["docs/DECISIONS.md", "docs/VISION.md", "scripts/sync-to-project.sh"]
PATTERNS = [r"os\." + "kill", "kill" + "pg", "p" + "kill", "kill" + " -"]
OS_KILL = "os." + "kill("


def patch(*files):
    out = []
    for path, old, new in files:
        out.append("diff -ruN base/%s work/%s\n" % (path, path))
        out.extend(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True),
                                        "base/" + path, "work/" + path))
    return "".join(out)


class ScriptWorld(Sandbox):

    def configure(self, hard_block=None):
        conf = json.loads(self.read("board/config.json"))
        if hard_block is None:
            conf.pop("hard_block", None)
        else:
            conf["hard_block"] = hard_block
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def commit_config_and_ticket(self, hard_block):
        self.configure(hard_block)
        self.make_ticket("1")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "hard_block fixture")
        self.git("push", "-q", "origin", "main")


class ApplyReadsTheSharedHardBlock(ScriptWorld):

    def setUp(self):
        super().setUp()
        self.write("docs/blocked.md", "old\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "apply fixture")
        self.git("push", "-q", "origin", "main")

    def apply(self, body):
        patch_file = self.write("ticket.diff", body, where=self.home)
        return self.run_sh("scripts/apply.sh", "1", patch_file)

    def test_a1_apply_uses_paths_and_scoped_patterns_from_one_setting(self):
        """A1 apply 對 paths / 測試新增行擋 rc=5，scripts 命中與未設定不擋。"""
        rows = (
            ("path", {"paths": ["docs/blocked.md"], "dirs": [], "patterns": []},
             patch(("docs/blocked.md", "old\n", "new\n")), 5, "docs/blocked.md"),
            ("tests pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             patch(("tests/new_case.py", None, "# " + OS_KILL + " is forbidden here\n")), 5,
             "tests/new_case.py"),
            ("verify pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             patch(("verify/example/new_case.py", None,
                    "# " + "kill" + "pg is forbidden here\n")), 5,
             "verify/example/new_case.py"),
            ("scripts pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             patch(("scripts/new_tool.py", None, "# " + OS_KILL + " is legitimate here\n")),
             0, None),
            ("unset", None, patch(("docs/blocked.md", "old\n", "new\n")), 0, None),
        )
        for name, config, body, expected, named in rows:
            with self.subTest(name):
                self.setUp()
                self.commit_config_and_ticket(config)
                done = self.apply(body)
                self.assertEqual(done.returncode, expected, done.stdout + done.stderr)
                if named:
                    self.assertIn(named, done.stdout + done.stderr)


class GateReadsTheSharedHardBlock(ScriptWorld):

    def setUp(self):
        super().setUp()
        marker = ("import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                  "    def test_ok(self):\n"
                  "        with open(os.environ['AC_TEST_LOG'], 'a', encoding='utf-8') as handle:\n"
                  "            handle.write('test-started\\n')\n")
        self.write("tests/test_land.py", marker)
        self.write("tests/test_verify_runner.py", marker)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "gate marker")

    def gate(self, changed):
        return self.run_sh("scripts/gate.sh", changed, "--ticket", "1",
                           env=self.env(AC_TEST_HOME=self.home))

    def test_a1_gate_preflight_uses_paths_and_scoped_patterns_from_one_setting(self):
        """A1 gate 對 paths / 測試新增行擋 rc=4，scripts 命中與未設定不擋。"""
        rows = (
            ("path", {"paths": ["scripts/land.sh"], "dirs": [], "patterns": []},
             "scripts/land.sh", "\n# hard path\n", 4, False),
            ("tests pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "tests/test_land.py", "\n# " + OS_KILL + " forbidden\n", 4, False),
            ("verify pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "verify/example/test_example.py", "\n# " + "p" + "kill forbidden\n", 4, False),
            ("scripts pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "scripts/land.sh", "\n# " + OS_KILL + " legitimate\n", 0, True),
            ("unset", None, "scripts/land.sh", "\n# no hard block\n", 0, True),
        )
        for name, config, changed, addition, expected, tests_run in rows:
            with self.subTest(name):
                self.setUp()
                self.configure(config)
                self.git("add", "-A")
                self.git("commit", "-q", "--allow-empty", "-m", "hard_block config")
                self.make_ticket("1")
                self.write(changed, self.read(changed) + addition)
                done = self.gate(changed)
                self.assertEqual(done.returncode, expected, done.stdout + done.stderr)
                self.assertEqual(os.path.exists(self.log), tests_run, done.stdout + done.stderr)
                if expected:
                    self.assertIn(changed, done.stdout + done.stderr)


class LandReadsTheSharedHardBlock(ScriptWorld):
    gate_stub = GATE_STUB_GREEN

    def land_case(self, hard_block, path, content, expected):
        self.commit_config_and_ticket(hard_block)
        branch = self.worktree("t1-hard-block")
        self.commit_in(branch, path, content)
        self.approve("1", "t1-hard-block")
        before = self.git("rev-parse", "main").strip()
        done = self.run_sh("scripts/land.sh", "t1-hard-block")
        self.assertEqual(done.returncode, expected, done.stdout + done.stderr)
        if expected:
            self.assertIn(path, done.stdout + done.stderr)
            self.assertEqual(self.git("rev-parse", "main").strip(), before)
            self.assertFalse(self.gate_ran())
        else:
            self.assertTrue(self.gate_ran())

    def test_a1_land_uses_paths_and_scoped_patterns_from_one_setting(self):
        """A1 land 對 paths / 測試新增行拒收，scripts 命中與未設定不擋。"""
        rows = (
            ("path", {"paths": ["docs/blocked.md"], "dirs": [], "patterns": []},
             "docs/blocked.md", "changed", 2),
            ("tests pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "tests/new_case.py", "# " + OS_KILL + " forbidden", 2),
            ("verify pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "verify/new_case.py", "# " + "kill" + " - forbidden", 2),
            ("scripts pattern", {"paths": [], "dirs": [], "patterns": PATTERNS},
             "scripts/new_tool.py", "# " + OS_KILL + " legitimate", 0),
            ("unset", None, "docs/blocked.md", "changed", 0),
        )
        for name, config, path, content, expected in rows:
            with self.subTest(name):
                self.setUp()
                self.land_case(config, path, content, expected)


AGENT_WRITES_OUTSIDE = """#!/bin/sh
set -e
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
echo changed >> "$HOME/guarded/a.txt"
cd "$AC_WORK"
[ "$AC_ROLE" = worker ] || exit 0
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# evidence\n' > "EVIDENCE-round$AC_ROUND.md"
"""


class GuardReadsHardBlockDirs(ScriptWorld):

    def arm(self, hard_block):
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- fixture model\n")
        os.makedirs(os.path.join(self.home, "guarded"), exist_ok=True)
        self.write("guarded/a.txt", "before\n", where=self.home)
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, AGENT_WRITES_OUTSIDE)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % worker, "timeout_seconds": 120}
        if hard_block is not None:
            conf["hard_block"] = hard_block
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket("1", needs_verifier=False)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "guard fixture")
        self.git("push", "-q", "origin", "main")

    def test_a2_guard_reads_dirs_and_missing_dirs_means_not_watched(self):
        """A2 hard_block.dirs 內被 agent 改動為 rc=8；沒設定就不查 repo 外。"""
        rows = (("configured", {"paths": [], "dirs": ["~/guarded"], "patterns": []}, 8),
                ("unset", None, 0))
        for name, config, expected in rows:
            with self.subTest(name):
                self.setUp()
                self.arm(config)
                done = self.run_sh("scripts/auto-fix.sh", "1", "--no-review")
                self.assertEqual(done.returncode, expected, done.stdout + done.stderr)
                violations = [row for row in self.events()
                              if row["kind"] == "ticket.attempt.failed"
                              and row.get("reason") == "guard-violation"]
                self.assertEqual(bool(violations), expected == 8, violations)


class RepositoryContract(unittest.TestCase):

    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_a3_old_configuration_names_are_absent_from_the_declared_scope(self):
        """A3 票面那條 git grep 在指定路徑為 0 行；*.inbox.md 不算，verify_lint 歸 #138。"""
        pattern = re.compile("protected" + "_paths|guarded" + "_dirs|PROTECTED" + "_FIELD")
        pathspec = ["scripts", "board", "tests", "verify", "templates", "docs/WORKFLOW.md",
                    "docs/DISPATCH-TEMPLATE.md", "docs/ROLES.md", "docs/reference",
                    "docs/DESIGN.md", "docs/FLOW.html", "tickets/SCHEMA.md", "README.md",
                    "CLAUDE.md", "AGENTS.md", "memory/role/*.md", "code-map/catalog.json"]
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
        listed = subprocess.run(["git", "ls-files", "-z", "--", *pathspec], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and os.path.realpath(top.stdout.strip()) == os.path.realpath(ROOT):
            files = [rel for rel in listed.stdout.split("\0") if rel]
        else:
            files = []
            for parent, dirs, names in os.walk(ROOT):
                dirs[:] = [name for name in dirs if name not in (".git", "__pycache__")]
                for name in names:
                    rel = os.path.relpath(os.path.join(parent, name), ROOT)
                    if any(rel == spec or rel.startswith(spec + "/") or fnmatch.fnmatch(rel, spec)
                           for spec in pathspec):
                        files.append(rel)
        self.assertIn("scripts/ticket.py", files)
        unexpected = []
        for rel in sorted(set(files)):
            if rel.endswith(".inbox.md"):
                continue
            path = os.path.join(ROOT, rel)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if pattern.search(line):
                        unexpected.append("%s:%d:%s" % (rel, number, line.rstrip()))
        if unexpected:
            self.fail("舊設定鍵仍存在:\n" + "\n".join(unexpected[:12]))

    def test_a4_config_and_docs_define_the_three_lists_once(self):
        """A4 設定值固定，SCHEMA 與派工規矩各說明 paths / dirs / patterns。"""
        conf = json.loads(self.read("board/config.json"))
        self.assertEqual(conf.get("hard_block"),
                         {"paths": PATHS, "dirs": [], "patterns": PATTERNS})
        for rel in ("tickets/SCHEMA.md", "docs/DISPATCH-TEMPLATE.md"):
            text = self.read(rel)
            self.assertIn("hard_block", text, rel)
            for key in ("paths", "dirs", "patterns", "tests/**", "verify/**", "新增行"):
                self.assertIn(key, text, "%s 缺 %s" % (rel, key))

    def test_a5_existing_oracles_are_migrated_instead_of_silently_removed(self):
        """A5 指定既有 oracle 仍在且已改守 hard_block，Sandbox 預設也只有新形狀。"""
        files = ("tests/test_apply.py", "tests/test_status.py", "tests/test_land.py",
                 "tests/test_auto_fix.py", "tests/control_harness.py",
                 "verify/batch_fix/test_ticket_108.py")
        for rel in files:
            text = self.read(rel)
            self.assertTrue("hard_block" in text, "%s 缺 hard_block" % rel)
            old_names = ("protected" + "_paths", "guarded" + "_dirs")
            self.assertFalse(any(name in text for name in old_names), rel)
        self.assertEqual(DEFAULT_CONFIG.get("hard_block"),
                         {"paths": PATHS, "dirs": [], "patterns": PATTERNS})
