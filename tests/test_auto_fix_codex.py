"""`scripts/auto-fix.sh` 的 codex 路徑(#87 A19–A25):票的 `model` / `tool` 決定 worker 起什麼。

以前 auto-fix 不讀票的 model / tool:票寫 `codex:gpt-6-astra`、`tool=codex`,起的照樣是
config 的 `claude -p --model opus`,events 記的是 opus —— 派工方以為在用 codex,實際上沒有。

假 codex 是一支放在沙盒 PATH 最前面的可執行檔:把自己的 argv 與 stdin 各存一份,再依
@MODE@ 交件 / 撞額度 / 別的失敗 / 卡住。config 的 `worker.command` 一律是 claude 那一句,
而沙盒的 `claude` 是絆線 —— 走錯路的那一刻 cleanup 就紅。期望值(argv、model 字串、頁面
那一句)寫死在這裡,不從被測腳本算回去。
"""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402

CODEX_MODEL = "codex:gpt-6-astra"
CLAUDE_COMMAND = "claude -p --model opus --permission-mode acceptEdits"

FAKE_CODEX = """#!/bin/sh
printf '%s\\n' "$@" > "@ARGV@"
cat > "@STDIN@"
echo "codex ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
case "@MODE@" in
    quota)
        echo "ERROR: stream error: 429 Too Many Requests"
        echo "You've hit your usage limit." >&2
        exit 1 ;;
    other)
        echo "ERROR: something unrelated broke" >&2
        exit 1 ;;
    hang)
        exec sleep 60 ;;
esac
cd "$AC_WORK"
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""


class CodexBase(AutoFixBase):

    def setUp(self):
        super().setUp()
        self.argv_file = os.path.join(self.home, "codex-argv.txt")
        self.stdin_file = os.path.join(self.home, "codex-stdin.md")
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": CLAUDE_COMMAND, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def fake_codex(self, mode="deliver"):
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX.replace("@ARGV@", self.argv_file)
                         .replace("@STDIN@", self.stdin_file).replace("@MODE@", mode))

    def codex_ticket(self, **fields):
        fields.setdefault("model", CODEX_MODEL)
        fields.setdefault("tool", "codex")
        return self.ticket_ready(**fields)

    def codex_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("codex ran")]

    def agent_events(self, kind=None):
        return [row for row in self.events() if row["kind"].startswith("agent.")
                and (kind is None or row["kind"] == kind)]

    def argv(self):
        with open(self.argv_file, encoding="utf-8") as handle:
            return handle.read().splitlines()


class TheCodexPathStartsCodex(CodexBase):

    def test_a19_a_codex_ticket_runs_codex_exec_with_the_dispatch_on_stdin(self):
        """A19。**變異 M10**:拿掉 tool 判斷(恆走 claude)→ argv 檔不在、絆線踩到,紅。"""
        self.fake_codex()
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.argv()[:5], ["exec", "-m", "gpt-6-astra", "-s", "workspace-write"])
        self.assertIn("codex exec -m gpt-6-astra -s workspace-write", done.stderr + done.stdout)
        sent = [path for path in self.result_files("dispatch-round1.md")
                if os.path.exists(os.path.join(os.path.dirname(path), "worker-round1.log"))]
        self.assertEqual(len(sent), 1, self.result_files("dispatch-round1.md"))
        with open(sent[0], encoding="utf-8") as handle, \
                open(self.stdin_file, encoding="utf-8") as fed:
            self.assertEqual(fed.read(), handle.read(), "派工文沒有原樣從 stdin 進 codex")
        for kind in ("agent.start", "agent.done"):
            rows = self.agent_events(kind)
            self.assertEqual([row["model"] for row in rows], [CODEX_MODEL], kind)
        cost = self.load_ticket("1")["cost"]
        self.assertEqual([row["model"] for row in cost if row["role"] == "worker"],
                         [CODEX_MODEL])
        self.assertIn("test_thing.py", self.git("ls-tree", "-r", "--name-only", "t1"),
                      "codex 交的 patch 沒有照既有那一手收進分支")

    def test_a19_dry_run_prints_the_codex_command(self):
        self.fake_codex()
        self.codex_ticket()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith("auto-fix: worker 命令 —— ")]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertTrue(line[0].split(" —— ", 1)[1].startswith(
            "codex exec -m gpt-6-astra -s workspace-write"), line[0])
        self.assertFalse(os.path.exists(self.argv_file), "--dry-run 不起 codex")


class TheClaudePathIsUnchanged(CodexBase):

    def test_a20_claude_code_or_no_tool_runs_the_config_command(self):
        """A20:tool=claude-code、或 tool 缺而 model=opus → config 的 worker.command,
        外加 with_envelope 補的 --output-format stream-json --verbose。"""
        want = CLAUDE_COMMAND + " --output-format stream-json --verbose"
        for drop_tool in (False, True):
            with self.subTest(drop_tool=drop_tool):
                row = self.make_ticket("1", allowed_write_paths=["tests/*"], model="opus",
                                       tool="claude-code")
                if drop_tool:
                    row.pop("tool")
                    self.write(os.path.join("tickets", "1.json"),
                               json.dumps(row, ensure_ascii=False, indent=2) + "\n")
                done = self.auto_fix("--dry-run")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("auto-fix: worker 命令 —— " + want, done.stdout.splitlines())


class InconsistentFieldsAreNotDispatched(CodexBase):

    def test_a21_model_and_tool_that_disagree_stop_before_the_ticket_moves(self):
        """A21。**變異**:拿掉一致性檢查 → rc 不是 2 / 有 agent.start,紅。"""
        self.fake_codex()
        for model, tool in (("opus", "codex"), (CODEX_MODEL, "claude-code")):
            with self.subTest(model=model, tool=tool):
                self.make_ticket("1", allowed_write_paths=["tests/*"], model=model, tool=tool)
                done = self.auto_fix("--no-review")
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                named = [l for l in done.stderr.splitlines()
                         if "model=%s" % model in l and "tool=%s" % tool in l]
                self.assertTrue(named, done.stderr)
                self.assertEqual(self.load_ticket("1")["state"], "Ready")
                self.assertEqual(self.agent_events("agent.start"), [])
                self.assertFalse(os.path.exists(self.argv_file))


class CodexNotOnPath(CodexBase):

    def test_a22_codex_missing_stops_before_the_worker(self):
        dirs = [self.stub_bin] + [
            where for where in os.environ.get("PATH", "").split(os.pathsep)
            if where and not os.path.exists(os.path.join(where, "codex"))]
        self.codex_ticket()
        done = self.run_sh("scripts/auto-fix.sh", "1", "--no-review",
                           env=self.env(PATH=os.pathsep.join(dirs)))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("codex 不在 PATH", done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["state"], "Ready")
        self.assertEqual(ticket.get("cost") or [], [])
        self.assertEqual(self.agent_events("agent.start"), [])


class CodexQuotaGoesBackToOpus(CodexBase):

    def test_a23_a_429_reassigns_the_ticket_to_opus_and_stops(self):
        """A23。**變異 M11**:429 判斷拿掉 → 票轉 Blocked、沒有「改派 opus」,紅。
        **變異**:改派後不改 state → state 是 Running,紅。"""
        self.fake_codex("quota")
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if "改派 opus" in l]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertIn(CODEX_MODEL, line[0])
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"], ticket["state"]),
                         ("opus", "claude-code", "Ready"))
        failed = [row for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual([row.get("reason") for row in failed], ["codex-quota"])
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("#1 codex 額度用完,已改 opus,重跑 auto-fix", self.read(pages[0]["page"]))
        self.assertEqual(len(self.codex_calls()), 1, "這一次不准自動重派第二個 worker")


class OtherCodexFailuresKeepTheOldPages(CodexBase):

    def assert_left_alone(self, done):
        self.assertNotIn("改派 opus", done.stdout)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"]), (CODEX_MODEL, "codex"))
        self.assertEqual(ticket["state"], "Blocked")
        self.assertEqual(ticket.get("owner"), "main")

    def test_a24_an_unrelated_failure_without_a_patch_is_the_no_patch_page(self):
        self.fake_codex("other")
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assert_left_alone(done)
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("worker 沒交出 patch", self.read(pages[0]["page"]))

    def test_a24_a_timeout_is_the_timeout_page(self):
        self.fake_codex("hang")
        self.codex_ticket(worker={"timeout_seconds": 2})
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assert_left_alone(done)
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("逾時", self.read(pages[0]["page"]))
        self.assertEqual(glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                                "patch-round1.diff")), [])


if __name__ == "__main__":
    unittest.main()
