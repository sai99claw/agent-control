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
printf '%s\\n%s\\n%s\\n' "$(pwd -P)" "$AC_ROLE" "$AC_ROOT" > "@ARGV@.where"
cp "$AC_ROOT/tickets/1.json" "@ARGV@.control-ticket" 2>/dev/null || true
cp "$AC_WORK/work/tickets/1.json" "@ARGV@.work-ticket" 2>/dev/null || true
cat > "@STDIN@"
echo "codex ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
case "@MODE@" in
    quota)
        echo "ERROR: stream error: 429 Too Many Requests"
        echo "You've hit your usage limit." >&2
        exit 1 ;;
    quota-stray)
        echo stray > "$(dirname "$AC_TEST_LOG")/repo/stray.txt"
        echo "ERROR: stream error: 429 Too Many Requests"
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
        # #115 A1/A2:與 claude 同權限;`--add-dir <副本根>` 恰一次,接在 `-` 之前。
        delivery_root = os.path.realpath(os.path.join(
            self.home, "repo-wt", "fix-t1", "round1"))
        argv = self.argv()
        self.assertEqual(argv.count("--add-dir"), 1, argv)
        add_dir = argv.index("--add-dir")
        self.assertEqual(os.path.realpath(argv[add_dir + 1]), delivery_root,
                         "Codex 要能寫副本根的 patch 與 EVIDENCE")
        self.assertEqual(argv[:add_dir] + argv[add_dir + 2:],
                         ["exec", "-m", "gpt-6-astra", "-s", "danger-full-access",
                          "-c", 'approval_policy="never"', "-C", ".",
                          "--skip-git-repo-check", "-"])
        self.assertNotIn("workspace-write", argv)
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            cwd, role, control = handle.read().splitlines()
        self.assertEqual(cwd, os.path.join(delivery_root, "work"), "codex 與 claude 同一個 cwd")
        self.assertEqual(role, "worker")
        self.assertEqual(os.path.realpath(control), os.path.join(delivery_root, "control"),
                         "AC_ROOT 是拋棄式控制根,不是主 repo")
        self.assertIn("codex exec -m gpt-6-astra -s danger-full-access", done.stderr + done.stdout)
        sent = [path for path in self.result_files("dispatch-round1.md")
                if os.path.exists(os.path.join(os.path.dirname(path), "worker-round1.log"))]
        self.assertEqual(len(sent), 1, self.result_files("dispatch-round1.md"))
        with open(sent[0], encoding="utf-8") as handle, \
                open(self.stdin_file, encoding="utf-8") as fed:
            self.assertEqual(fed.read(), handle.read(), "派工文沒有原樣從 stdin 進 codex")
        with open(sent[0], encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("## 權威票面快照", packet)
        self.assertIn("$AC_ROOT/tickets/1.json", packet)
        self.assertIn("work/tickets/1.json", packet)
        self.assertIn("不能用它推翻本快照", packet)
        for kind in ("agent.start", "agent.done"):
            rows = self.agent_events(kind)
            self.assertEqual([row["model"] for row in rows], [CODEX_MODEL], kind)
        cost = self.load_ticket("1")["cost"]
        self.assertEqual([row["model"] for row in cost if row["role"] == "worker"],
                         [CODEX_MODEL])
        self.assertIn("test_thing.py", self.git("ls-tree", "-r", "--name-only", "t1"),
                      "codex 交的 patch 沒有照既有那一手收進分支")

    def test_115_a3_the_snapshot_comes_from_the_live_store_not_the_base_copy(self):
        """#115 A3。base_sha 上的票寫舊 objective、權威票庫寫新的:派工文的快照與控制根的票副本
        是新的,work/ 那一份是舊的,派工文明說舊檔不能推翻快照。**變異**:快照改讀
        work/tickets → 派工文出現舊值,紅。"""
        self.fake_codex()
        row = self.codex_ticket(objective="OLD-objective-from-base")
        row.update(objective="NEW-objective-from-store",
                   base_sha=self.git("rev-parse", "main").strip())
        self.write("tickets/1.json", json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.stdin_file, encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("NEW-objective-from-store", packet)
        self.assertNotIn("OLD-objective-from-base", packet)
        self.assertIn("不能用它推翻本快照", packet)
        with open(self.argv_file + ".control-ticket", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "NEW-objective-from-store")
        with open(self.argv_file + ".work-ticket", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "OLD-objective-from-base",
                             "夾具要讓 work/ 真的拿到舊版,不然這一條什麼都沒比")

    def test_a19_dry_run_prints_the_codex_command(self):
        self.fake_codex()
        self.codex_ticket()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith("auto-fix: worker 命令 —— ")]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertTrue(line[0].split(" —— ", 1)[1].startswith(
            "codex exec -m gpt-6-astra -s danger-full-access"), line[0])
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

    def test_115_a7_quota_and_a_violation_together_keep_the_violation(self):
        """#115 A7。**M5**:額度那一手排在守衛前面(提早返回)→ rc 7、票回 Ready 改 opus,紅。
        四問:量 rc / 票 / attempt reason / 頁;值 = A7(越界 decision 不被吞)。"""
        self.fake_codex("quota-stray")
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 8, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"], ticket["state"], ticket.get("owner")),
                         (CODEX_MODEL, "codex", "Blocked", "main"))
        failed = [row for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual([row.get("reason") for row in failed], ["guard-violation"])
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("stray.txt", pages[0]["what"])


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


class EffortBase(CodexBase):
    """#114:票的 `effort` > config 的 `effort.implement` > 無。期望句抄票面字面。"""

    def config_effort(self, value):
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": CLAUDE_COMMAND, "timeout_seconds": 120}
        if value:
            conf["effort"] = {"implement": value}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def dry_lines(self, done):
        lines = done.stdout.splitlines()
        return ([l for l in lines if l.startswith("auto-fix: worker 命令 —— ")],
                [l for l in lines if l.startswith("auto-fix: effort 來源=")])


class AutoFixPicksTheEffort(EffortBase):

    def test_a7_dry_run_shows_the_effort_and_where_it_came_from(self):
        """T3 四種情境 + 票沒寫 model 的 claude-code 票。**變異 M3**:取值順序顛倒(config
        蓋票)→「兩者都有」那一列印 low,紅。"""
        envelope_tail = " --output-format stream-json --verbose"
        rows = (
            ("票", dict(model="fable", tool="claude-code", effort="high"), None,
             "claude -p --model fable --effort high --permission-mode acceptEdits" + envelope_tail,
             "auto-fix: effort 來源=票 high"),
            ("config", dict(model=CODEX_MODEL, tool="codex"), "medium",
             "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"medium\"' "
             "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -",
             "auto-fix: effort 來源=config medium"),
            ("兩者", dict(model=CODEX_MODEL, tool="codex", effort="xhigh"), "low",
             "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"xhigh\"' "
             "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -",
             "auto-fix: effort 來源=票 xhigh"),
            ("都沒有", dict(model="fable", tool="claude-code"), None,
             "claude -p --model fable --permission-mode acceptEdits" + envelope_tail, None),
            ("票沒 model", dict(model="", tool="claude-code"), "high",
             "claude -p --model opus --effort high --permission-mode acceptEdits" + envelope_tail,
             "auto-fix: effort 來源=config high"),
        )
        self.fake_codex()
        for name, fields, conf, want, source in rows:
            with self.subTest(case=name):
                self.config_effort(conf)
                self.make_ticket("1", allowed_write_paths=["tests/*"], **fields)
                done = self.auto_fix("--dry-run")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                worker, sources = self.dry_lines(done)
                self.assertEqual(worker, ["auto-fix: worker 命令 —— " + want], done.stdout)
                self.assertEqual(sources, [source] if source else [], done.stdout)


class AnIllegalEffortIsNotDispatched(EffortBase):

    def test_a8_an_illegal_effort_stops_before_the_ticket_moves(self):
        """T4:票 ultra(claude-code)、config turbo(codex)→ exit 2,票不動、不起、不記。
        **變異**:auto-fix 拿掉早停 → 票轉 Running / 有 agent.start,紅。"""
        self.fake_codex()
        rows = (("票", "ultra", dict(tool="claude-code", effort="ultra"), None),
                ("config", "turbo", dict(model=CODEX_MODEL, tool="codex"), "turbo"))
        for source, value, fields, conf in rows:
            for args in (("--no-review",), ("--dry-run",)):
                with self.subTest(source=source, args=args):
                    self.config_effort(conf)
                    self.make_ticket("1", allowed_write_paths=["tests/*"], **fields)
                    before = self.load_ticket("1")
                    done = self.auto_fix(*args)
                    self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                    named = [l for l in done.stderr.splitlines()
                             if "effort=%s" % value in l and source in l]
                    self.assertTrue(named, done.stderr)
                    after = self.load_ticket("1")
                    self.assertEqual(after["state"], "Ready")
                    self.assertEqual(after["state_version"], before["state_version"])
                    self.assertEqual(after.get("cost") or [], [])
                    self.assertEqual(self.agent_events("agent.start"), [])
                    self.assertFalse(os.path.exists(self.argv_file))


class CodexQuotaKeepsOrDropsTheEffort(EffortBase):

    def test_a10_a_claude_legal_effort_survives_the_reassignment(self):
        self.fake_codex("quota")
        self.codex_ticket(effort="high")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["tool"], ticket["effort"]), ("claude-code", "high"))
        self.assertIn("-c\nmodel_reasoning_effort=\"high\"\n", "\n".join(self.argv()) + "\n")
        self.assertNotIn("原 effort", self.read(self.inbox_rows()[0]["page"]))

    def test_a10_an_effort_claude_does_not_take_becomes_null_and_is_named(self):
        """**變異**:改派時不清 effort → 票留 ultra(下一次 auto-fix 會被 A8 擋),紅。"""
        self.fake_codex("quota")
        self.codex_ticket(effort="ultra")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertIn("effort", ticket)
        self.assertIsNone(ticket["effort"])
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("原 effort ultra", self.read(pages[0]["page"]))


if __name__ == "__main__":
    unittest.main()
