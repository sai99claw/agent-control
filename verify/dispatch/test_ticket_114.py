"""#114 票可指定推理強度 effort:agent-command 轉旗標、auto-fix 取值 / 擋不合法、事件與成本帶 effort。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 開題在 base_sha 上實跑的基準句 / 既有案例 test_a20 的 golden)
A1  | cli   | agent-command --model opus|fable --tool claude-code --base "claude -p --model opus --permission-mode acceptEdits" --swap-model --effort high | rc 0;stdout 恰為 `claude -p --model <m> --effort high --permission-mode acceptEdits` | 票面 A1 原話
A2  | cli   | base 已有 `--effort low`;base 沒有 --model 且不 swap;base 有 --model 不 swap | 原位換 high、只一次 --effort;句尾接 `--effort high`;接在 --model opus 之後 | 票面 A2 原話;objective「在 --model <m> 之後加」
A3  | cli   | agent-command --model codex:gpt-5.6-sol --tool codex --effort high | stdout 恰為 `codex exec -m gpt-5.6-sol -c 'model_reasoning_effort="high"' -s danger-full-access -c 'approval_policy="never"' -C . --skip-git-repo-check -` | 票面 A3 原話;沙盒旗標依 #115 A1 換新
A4  | cli   | 不給 --effort / 給 --effort "" | 與今天逐字同:兩條基準句 | 票面 A4(開題在 base_sha 實跑);codex 那一句依 #115 A1 換新
A5  | cli   | claude-code+ultra、codex+turbo、表外 tool(other)+high | rc 2、stdout 空、stderr 含 effort=<值> 與合法值清單 / 點名那個 tool | 票面 A5 原話(清單抄 `claude --help` 與票面);#131 A2 讓 agy 進合法值表,「不支援 effort 的 tool」改用表外的 other
A6  | cli   | ticket.py create … --effort high / 不合法 / 不給 | 票檔 effort="high";rc 2 不寫檔;沒有 effort 鍵 | 票面 A6 原話
A7  | integ | auto-fix --dry-run:票 effort / 只有 config effort.implement / 兩者都有 / 都沒有;codex 票;沒 model 的 claude-code 票 | worker 命令那一行與 `auto-fix: effort 來源=<票|config> <e>` 那一行 | 票面 A7 原話;無 effort 那一句抄 tests/test_auto_fix_codex.py test_a20
A8  | integ | 票 effort=ultra(claude-code)/ config effort.implement=ultra;另乾跑一次 | rc 2、stderr 含 effort=ultra 與來源;票 Ready、state_version 與 cost 不變、沒有 agent.start | 票面 A8 原話
A9  | integ | 假 claude worker 交件一輪 / 失敗一輪;ticket.py cost --effort high | agent.start/done/failed 帶 effort=high、無 effort 不帶;cost 列恰 15 鍵、effort 對上;ticket.cost 事件帶 effort | 票面 A9 原話;15 鍵 = #94 的 14 鍵字面 + effort
A9b | doc   | 讀 tickets/SCHEMA.md | 票欄位表有 `effort` 那一列;成本列含 effort 與「15 鍵」 | 票面 A9 末句
A10 | integ | 表 worker = [codex astra, opus];假 codex 回額度信封:票 effort=high(opus 表值 medium)/ effort=ultra(opus 表值 high) | 同一次接著起 opus:high → `--effort high`(票贏);ultra → `--effort high`(取表值,不寫 null);票的 model / tool / effort 都不改 | 票面 A10;#121 A2 / A7 取代「改派 opus、票 effort 設 null」(tickets/SCHEMA.md effort 列)

## 介面字串(全部出自票面 acceptance / objective,不從 ticket.py / auto-fix.sh 讀回來)
CLAUDE_LEVELS = "low medium high xhigh max"           ← A5 claude-code 合法值清單
CODEX_LEVELS  = "low medium high xhigh max ultra"     ← A5 codex 合法值清單
WORKER_LINE   = "auto-fix: worker 命令 —— "           ← A7 乾跑那一行(既有字串)
SOURCE_LINE   = "auto-fix: effort 來源="              ← A7 來源行
ORIGINAL      = "原 effort ultra"                     ← 舊 A10 decision 頁那一句(#121 起沒有改派頁,不再斷言)
COST_KEYS_15  = #94 的 14 鍵 + "effort"               ← A9

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo、PATH 最前面的 `claude` 是絆線)與
tests/test_auto_fix.AutoFixBase / tests/test_auto_fix_codex.CodexBase 的假 codex(MODE=deliver/quota)。
claude-code 的 worker 是 `board/config.json` 的 `worker.command` 指向的假 sh(交件 / 失敗 / 什麼都不做)。
A1–A5 直接用 subprocess 跑本 repo 的 scripts/ticket.py agent-command(不讀票、不寫東西);外層 AC_* 清掉。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不跑真的模型。
"""
import json
import os
import subprocess
import sys
import unittest

TAGS = ["effort"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import DEFAULT_CONFIG, Sandbox, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402
from test_auto_fix_codex import CodexBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
CLAUDE_LEVELS = "low medium high xhigh max"
CODEX_LEVELS = "low medium high xhigh max ultra"
WORKER_LINE = "auto-fix: worker 命令 —— "
SOURCE_LINE = "auto-fix: effort 來源="
ORIGINAL = "原 effort ultra"
BASE_CLAUDE = "claude -p --model opus --permission-mode acceptEdits"
ENVELOPE = " --output-format stream-json --verbose"
# #94 的 14 鍵(tests/test_cost_ledger.py ROW_KEYS 字面)+ 本票的 effort。
COST_KEYS_15 = {"role", "round", "model", "tokens_in", "tokens_out", "cache_write",
                "cache_read", "wall_seconds", "outcome", "run_id", "session_id", "by",
                "note", "at", "effort"}
CODEX_TICKET_MODEL = "codex:gpt-6-astra"

# 假 claude worker:交一份 patch(同 test_auto_fix_codex 的 deliver 那一段),不看參數。
WORKER_DELIVERS = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
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

WORKER_FAILS = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
echo "假 worker 故意失敗" >&2
exit 1
"""

WORKER_IDLE = """#!/bin/sh
echo "worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
exit 0
"""


def agent_command(*args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    return subprocess.run(
        ["python3", os.path.join(ROOT, "scripts", "ticket.py"), "agent-command", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def unquoted(value):
    return None if value is None else str(value).strip('"')


class AgentCommandTurnsEffortIntoAFlag(unittest.TestCase):
    """A1–A5:`ticket.py agent-command --effort` 依 tool 轉旗標,缺省逐字不變,不合法不印命令。"""

    def assert_prints(self, done, want):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, want + "\n", done.stderr)

    def test_a1_claude_code_swap_model_adds_effort_after_model(self):
        """A1 claude-code + swap-model + --effort high → `--effort high` 緊接在 `--model <m>` 之後。"""
        for model in ("opus", "fable"):
            with self.subTest(model=model):
                done = agent_command("--model", model, "--tool", "claude-code",
                                     "--base", BASE_CLAUDE, "--swap-model", "--effort", "high")
                self.assert_prints(
                    done, "claude -p --model %s --effort high --permission-mode acceptEdits" % model)

    def test_a2_an_existing_effort_is_replaced_in_place(self):
        """A2 base 已有 `--effort low` → 原位換成 high、全句只出現一次 `--effort`。"""
        done = agent_command("--model", "opus", "--tool", "claude-code", "--base",
                             "claude -p --model opus --effort low --permission-mode acceptEdits",
                             "--swap-model", "--effort", "high")
        self.assert_prints(done, "claude -p --model opus --effort high --permission-mode acceptEdits")
        self.assertEqual(done.stdout.split().count("--effort"), 1, done.stdout)

    def test_a2_without_model_and_without_swap_effort_goes_to_the_end(self):
        """A2 base 沒有 `--model` 且不 swap → `--effort high` 接在句尾。"""
        done = agent_command("--model", "opus", "--tool", "claude-code",
                             "--base", "claude -p --permission-mode acceptEdits", "--effort", "high")
        self.assert_prints(done, "claude -p --permission-mode acceptEdits --effort high")

    def test_a2_with_model_and_without_swap_effort_follows_the_model(self):
        """A2 base 有 `--model` 不 swap → 照 objective「在 `--model <m>` 之後加」。"""
        done = agent_command("--model", "opus", "--tool", "claude-code",
                             "--base", BASE_CLAUDE, "--effort", "high")
        self.assert_prints(done, "claude -p --model opus --effort high --permission-mode acceptEdits")

    def test_a3_codex_effort_is_a_reasoning_config_after_the_model(self):
        """A3 codex + --effort high → `-c 'model_reasoning_effort="high"'` 接在 `-m <id>` 之後。"""
        done = agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex", "--effort", "high")
        self.assert_prints(
            done, "codex exec -m gpt-5.6-sol -c 'model_reasoning_effort=\"high\"' "
                  "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -")

    def test_a4_no_effort_is_byte_for_byte_today(self):
        """A4 不給 `--effort` → 兩條基準句逐字不變(開題在 base_sha 實跑)。"""
        self.assert_prints(agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex"),
                           "codex exec -m gpt-5.6-sol -s danger-full-access "
                           "-c 'approval_policy=\"never\"' -C . --skip-git-repo-check -")
        self.assert_prints(agent_command("--model", "fable", "--tool", "claude-code",
                                         "--base", BASE_CLAUDE, "--swap-model"),
                           "claude -p --model fable --permission-mode acceptEdits")

    def test_a4_an_empty_effort_is_the_same_as_none(self):
        """A4 `--effort ""` → 與不給逐字相同。"""
        self.assert_prints(agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex",
                                         "--effort", ""),
                           "codex exec -m gpt-5.6-sol -s danger-full-access "
                           "-c 'approval_policy=\"never\"' -C . --skip-git-repo-check -")
        self.assert_prints(agent_command("--model", "fable", "--tool", "claude-code",
                                         "--base", BASE_CLAUDE, "--swap-model", "--effort", ""),
                           "claude -p --model fable --permission-mode acceptEdits")

    def test_a5_a_value_outside_the_tool_table_is_refused_with_the_table(self):
        """A5 claude-code+ultra、codex+turbo → rc 2、stdout 空、stderr 含 effort=<值> 與該 tool 的合法值清單。"""
        cases = ((("--model", "opus", "--tool", "claude-code", "--base", BASE_CLAUDE,
                   "--swap-model", "--effort", "ultra"), "ultra", CLAUDE_LEVELS),
                 (("--model", "codex:gpt-5.6-sol", "--tool", "codex", "--effort", "turbo"),
                  "turbo", CODEX_LEVELS))
        for args, value, levels in cases:
            with self.subTest(value=value):
                done = agent_command(*args)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertIn("effort=%s" % value, done.stderr)
                self.assertIn(levels, done.stderr)

    def test_a5_a_tool_without_effort_support_is_refused(self):
        """A5 表外 tool(other)帶 effort → rc 2、stdout 空、stderr 說 tool=other 不支援 effort。

        #131 A2 起 agy 有合法值表,不再是「不支援 effort 的 tool」;改用表外的 tool。
        """
        done = agent_command("--model", "opus", "--tool", "other", "--base", BASE_CLAUDE,
                             "--effort", "high")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn("tool=other", done.stderr)
        self.assertIn("不支援 effort", done.stderr)


MIN = ("--subject", "推理強度開票",
       "--objective", "票帶 effort",
       "--acceptance", "effort 進票檔",
       "--role", "worker")


class CreateTakesEffort(Sandbox):
    """A6:`ticket.py create --effort`。"""

    def test_a6_a_legal_effort_is_written_to_the_ticket(self):
        """A6 `create … --effort high`(claude-code)→ 票檔 effort == "high";codex 票收 ultra。"""
        done = self.ticket("create", *MIN, "--model", "opus", "--tool", "claude-code",
                           "--effort", "high")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1").get("effort"), "high")
        done = self.ticket("create", *MIN, "--model", "codex:gpt-5.6-sol", "--tool", "codex",
                           "--effort", "ultra")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("2").get("effort"), "ultra")

    def test_a6_an_illegal_effort_writes_no_ticket(self):
        """A6 值不在該票 tool 的合法表 → rc 2、tickets/ 下沒有多一張。"""
        for tool, model, value in (("claude-code", "opus", "ultra"),
                                   ("codex", "codex:gpt-5.6-sol", "turbo")):
            with self.subTest(tool=tool, value=value):
                done = self.ticket("create", *MIN, "--model", model, "--tool", tool,
                                   "--effort", value)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(self.tickets_on_disk(), [])

    def test_a6_no_effort_means_no_key(self):
        """A6 不給 `--effort` → 票檔沒有 effort 這個鍵(與今天同形)。"""
        done = self.ticket("create", *MIN, "--model", "opus", "--tool", "claude-code")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("effort", self.load_ticket("1"))


class EffortBase(CodexBase):
    """CodexBase 的 config:worker.command = `claude -p --model opus …`(乾跑不起它)。"""

    def set_config_effort(self, value):
        conf = json.loads(self.read("board/config.json"))
        conf["effort"] = {"implement": value}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def claude_ticket(self, **fields):
        fields.setdefault("model", "opus")
        fields.setdefault("tool", "claude-code")
        return self.ticket_ready(**fields)

    def worker_line(self, done):
        lines = [l for l in done.stdout.splitlines() if l.startswith(WORKER_LINE)]
        self.assertEqual(len(lines), 1, done.stdout + done.stderr)
        return lines[0][len(WORKER_LINE):]

    def source_lines(self, done):
        return [l for l in (done.stdout + done.stderr).splitlines() if l.startswith(SOURCE_LINE)]


class AutoFixPicksTheEffort(EffortBase):
    """A7:取值順序 票 > config effort.implement > 無;乾跑印命令與來源行。"""

    def dry(self):
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_a7_the_ticket_effort_reaches_the_claude_command(self):
        """A7 票 effort=high、config 沒有 → `--model opus --effort high`,來源行 `票 high`。"""
        self.claude_ticket(effort="high")
        done = self.dry()
        self.assertEqual(self.worker_line(done),
                         "claude -p --model opus --effort high --permission-mode acceptEdits"
                         + ENVELOPE)
        self.assertEqual(self.source_lines(done), [SOURCE_LINE + "票 high"])

    def test_a7_only_the_config_effort_is_used_when_the_ticket_has_none(self):
        """A7 票沒有、config effort.implement=medium → `--effort medium`,來源行 `config medium`。"""
        self.set_config_effort("medium")
        self.claude_ticket()
        done = self.dry()
        self.assertEqual(self.worker_line(done),
                         "claude -p --model opus --effort medium --permission-mode acceptEdits"
                         + ENVELOPE)
        self.assertEqual(self.source_lines(done), [SOURCE_LINE + "config medium"])

    def test_a7_the_ticket_wins_over_the_config(self):
        """A7 兩者都有(票 high、config low)→ 票贏。"""
        self.set_config_effort("low")
        self.claude_ticket(effort="high")
        done = self.dry()
        line = self.worker_line(done)
        self.assertIn("--model opus --effort high", line)
        self.assertEqual(line.split().count("--effort"), 1, line)
        self.assertEqual(self.source_lines(done), [SOURCE_LINE + "票 high"])

    def test_a7_neither_leaves_the_line_as_today(self):
        """A7 兩處都沒有 → 那一行與今天逐字相同、不印來源行(golden:test_a20)。"""
        self.claude_ticket()
        done = self.dry()
        self.assertEqual(self.worker_line(done), BASE_CLAUDE + ENVELOPE)
        self.assertEqual(self.source_lines(done), [])

    def test_a7_a_codex_ticket_gets_the_reasoning_config(self):
        """A7 codex 票 effort=high → `-c 'model_reasoning_effort="high"'` 接在 `-m <id>` 之後。"""
        self.fake_codex()
        self.codex_ticket(effort="high")
        done = self.dry()
        self.assertTrue(self.worker_line(done).startswith(
            "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"high\"' -s danger-full-access"),
            self.worker_line(done))
        self.assertEqual(self.source_lines(done), [SOURCE_LINE + "票 high"])
        self.assertFalse(os.path.exists(self.argv_file), "--dry-run 不起 codex")

    def test_a7_a_claude_ticket_without_model_still_gets_effort(self):
        """A7 票沒寫 model 的 claude-code 票也帶 `--effort`。"""
        self.claude_ticket(model="", effort="high")
        done = self.dry()
        line = self.worker_line(done)
        self.assertIn("--model opus --effort high", line)
        self.assertEqual(line.split().count("--effort"), 1, line)
        self.assertEqual(self.source_lines(done), [SOURCE_LINE + "票 high"])


class AutoFixStopsOnAnIllegalEffort(EffortBase):
    """A8:不合法就在動票之前停。"""

    def setUp(self):
        super().setUp()
        conf = json.loads(self.read("board/config.json"))
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, WORKER_IDLE)
        conf["worker"] = {"command": "sh %s --model opus --permission-mode acceptEdits" % path,
                          "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def assert_untouched(self, done, before, source):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("effort=ultra", done.stderr)
        self.assertIn(source, done.stderr)
        after = self.load_ticket("1")
        self.assertEqual(after["state"], "Ready")
        self.assertEqual(after["state_version"], before["state_version"])
        self.assertEqual(len(after.get("cost") or []), len(before.get("cost") or []))
        self.assertEqual([row for row in self.agent_events("agent.start")
                          if str(row.get("ticket")).lstrip("#") == "1"], [])
        self.assertEqual(self.worker_rounds(), [])

    def test_a8_an_illegal_ticket_effort_stops_before_the_ticket_moves(self):
        """A8 票 effort=ultra(claude-code)→ exit 2、stderr 含 effort=ultra 與「票」;票不動、不發 agent.start。"""
        before = self.claude_ticket(effort="ultra")
        self.assert_untouched(self.auto_fix("--no-review"), before, "票")

    def test_a8_an_illegal_config_effort_stops_before_the_ticket_moves(self):
        """A8 config effort.implement=ultra、票沒有 → exit 2、stderr 含 effort=ultra 與「config」。"""
        self.set_config_effort("ultra")
        before = self.claude_ticket()
        self.assert_untouched(self.auto_fix("--no-review"), before, "config")

    def test_a8_the_dry_run_is_refused_too(self):
        """A8 乾跑同樣 rc 2。"""
        before = self.claude_ticket(effort="ultra")
        self.assert_untouched(self.auto_fix("--dry-run"), before, "票")


class EventsAndCostCarryTheEffort(EffortBase):
    """A9:worker 的 agent.* 事件帶 effort;cost 列恰 15 鍵。"""

    def use_worker(self, body):
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, body)
        conf = json.loads(self.read("board/config.json"))
        conf["worker"] = {"command": "sh %s --model opus --permission-mode acceptEdits" % path,
                          "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def worker_events(self, kind):
        return [row for row in self.agent_events(kind) if row.get("role") in (None, "worker")]

    def last_worker_cost(self):
        rows = [row for row in (self.load_ticket("1").get("cost") or [])
                if row.get("role") == "worker"]
        self.assertTrue(rows, "票上沒有 worker 的 cost 列")
        return rows[-1]

    def test_a9_a_delivered_round_records_effort_on_events_and_cost(self):
        """A9 effort=high 交件一輪 → agent.start / agent.done 帶 effort=high;cost 最後一列 15 鍵、effort=high。"""
        self.use_worker(WORKER_DELIVERS)
        self.claude_ticket(effort="high")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for kind in ("agent.start", "agent.done"):
            rows = self.worker_events(kind)
            self.assertEqual(len(rows), 1, (kind, rows))
            self.assertEqual(unquoted(rows[0].get("effort")), "high", (kind, rows[0]))
        row = self.last_worker_cost()
        self.assertEqual(set(row), COST_KEYS_15)
        self.assertEqual(row.get("effort"), "high")

    def test_a9_a_failed_round_records_effort_on_agent_failed(self):
        """A9 effort=high 的 worker 失敗 → agent.failed 帶 effort=high。"""
        self.use_worker(WORKER_FAILS)
        self.claude_ticket(effort="high")
        self.auto_fix("--no-review")
        rows = self.worker_events("agent.failed")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(unquoted(rows[0].get("effort")), "high", rows[0])

    def test_a9_without_effort_events_have_no_effort_and_cost_has_null(self):
        """A9 對照組:沒有 effort → agent.start / agent.done 沒有 effort 這格;cost 列 15 鍵、effort=null。"""
        self.use_worker(WORKER_DELIVERS)
        self.claude_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for kind in ("agent.start", "agent.done"):
            rows = self.worker_events(kind)
            self.assertEqual(len(rows), 1, (kind, rows))
            self.assertNotIn("effort", rows[0], (kind, rows[0]))
        row = self.last_worker_cost()
        self.assertEqual(set(row), COST_KEYS_15)
        self.assertIsNone(row.get("effort"))

    def test_a9_ticket_cost_takes_effort(self):
        """A9 `ticket.py cost … --effort high` → 列恰 15 鍵、effort=high;ticket.cost 事件同列;不給 → null。"""
        self.claude_ticket()
        done = self.ticket("cost", "1", "--role", "worker", "--round", "1", "--model", "opus",
                           "--effort", "high")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")["cost"][-1]
        self.assertEqual(set(row), COST_KEYS_15)
        self.assertEqual(row.get("effort"), "high")
        events = [r for r in self.events() if r["kind"] == "ticket.cost"]
        self.assertEqual(len(events), 1, events)
        self.assertEqual(unquoted(events[0].get("effort")), "high", events[0])
        done = self.ticket("cost", "1", "--role", "worker", "--round", "2", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")["cost"][-1]
        self.assertEqual(set(row), COST_KEYS_15)
        self.assertIsNone(row.get("effort"))


class SchemaDocumentsEffort(unittest.TestCase):
    """A9 末句:tickets/SCHEMA.md。"""

    def test_a9_schema_has_the_effort_field_and_fifteen_cost_keys(self):
        """A9 SCHEMA 的票欄位表有 `effort` 一列;成本列含 effort 與「15 鍵」。"""
        with open(os.path.join(ROOT, "tickets", "SCHEMA.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        rows = [l for l in lines if l.startswith("|")]
        effort_rows = [l for l in rows if len(l.split("|")) > 2
                       and l.split("|")[2].strip().startswith("`effort`")]
        self.assertTrue(effort_rows, "票欄位表沒有 `effort` 那一列")
        cost_rows = [l for l in rows if len(l.split("|")) > 2
                     and l.split("|")[2].strip().startswith("`cost`")]
        self.assertEqual(len(cost_rows), 1, cost_rows)
        self.assertIn("effort", cost_rows[0])
        self.assertIn("15 鍵", cost_rows[0])


class CodexQuotaKeepsOrDropsTheEffort(EffortBase):
    """A10:codex 撞額度後依角色表接下一個候選(opus / claude-code)時 effort 的去留(#121 A2 / A7)。"""

    def quota_then_opus(self, opus_effort, ticket_effort):
        """表 worker = [codex astra, opus(opus_effort)];假 codex 回額度信封,假 claude worker 記 argv 後交件。"""
        self.set_role_models(worker=[fixture_candidate(CODEX_TICKET_MODEL, "codex"),
                                     fixture_candidate("opus", "claude-code", effort=opus_effort)])
        argv = os.path.join(self.home, "claude-argv.txt")
        path = os.path.join(self.home, "fake-worker.sh")
        write_executable(path, WORKER_DELIVERS.replace(
            "#!/bin/sh\n", '#!/bin/sh\nprintf \'%%s\\n\' "$*" >> "%s"\n' % argv, 1))
        conf = json.loads(self.read("board/config.json"))
        conf["worker"] = {"command": "sh %s --model opus --permission-mode acceptEdits" % path,
                          "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.fake_codex("quota")
        self.codex_ticket(effort=ticket_effort)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(self.argv_file), "codex 要先起過一次")
        with open(argv, encoding="utf-8") as handle:
            runs = handle.read().splitlines()
        self.assertEqual(len(runs), 1, runs)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"], ticket.get("effort")),
                         (CODEX_TICKET_MODEL, "codex", ticket_effort), "票上的 model / tool / effort 不被改寫")
        return runs[0]

    def test_a10_a_claude_legal_effort_is_kept(self):
        """A10 票 effort=high、opus 表值 medium → 接著起的 opus 帶 `--effort high`(票贏),票 effort 仍 high。"""
        argv = self.quota_then_opus("medium", "high")
        self.assertIn("--model opus --effort high", argv)
        self.assertEqual(argv.split().count("--effort"), 1, argv)

    def test_a10_an_effort_claude_does_not_take_falls_back_to_the_table(self):
        """A10 票 effort=ultra(claude-code 不收)、opus 表值 high → opus 帶 `--effort high`(不寫 null),票 effort 仍 ultra。"""
        argv = self.quota_then_opus("high", "ultra")
        self.assertIn("--model opus --effort high", argv)
        self.assertNotIn("ultra", argv)


if __name__ == "__main__":
    unittest.main()
