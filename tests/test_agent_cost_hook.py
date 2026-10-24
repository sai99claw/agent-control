"""`scripts/transcript-cost.py` + `scripts/agent-cost-hook.py`:互動 session 與子 agent 的成本(#95)。

夾具在 `tests/fixtures/agent_cost/`;每個期望 token 都是由夾具字面手算的常數(算式寫在旁邊)。
hook 的案例以子行程跑本樹腳本、`AC_ROOT` 釘在拋棄式控制根,繼承的 AC_* 一律清掉。

變異(票 #95 T3):
M1 `messages()` 不以 message.id 去重 → T1 夾具 A 的 tokens_in 紅。
M2 tokens_out 不看 stop_reason 直接加總 → T1 夾具 A 的 None 斷言紅。
M3 `ticket.py cost_total` 把 agent.cost 事件算進去 → A5 那一條紅。
"""

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
HOOK = os.path.join(SCRIPTS, "agent-cost-hook.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "agent_cost")
EVENT_KEYS = {"role", "round", "model", "tokens_in", "tokens_out", "cache_write", "cache_read",
              "wall_seconds", "outcome", "run_id", "session_id", "by", "note", "effort",
              "ticket", "agent_id"}
ENVELOPE = {"ts", "kind", "pid", "cwd"}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tc = load("transcript_cost", "transcript-cost.py")


def rows_of(name):
    return tc.read_rows(os.path.join(FIXTURES, name))


class T1Parser(unittest.TestCase):

    def test_a2_duplicate_ids_take_the_last_row_and_unfinished_output_is_null(self):
        """夾具 A:msg_a1 三列(output 3/8/8、stop_reason 全 null)+ msg_a2 一列。
        A2 去重與 output 收尾;M1 / M2 會紅;期望值手算;不放寬既有斷言。"""
        got = tc.summarize(rows_of("dup_no_stop.jsonl"))
        self.assertEqual(got["tokens_in"], 14)          # 10 + 4(不是四列的 34)
        self.assertEqual(got["cache_write"], 200)       # 200 + 0
        self.assertEqual(got["cache_read"], 6200)       # 3000 + 3200
        self.assertIsNone(got["tokens_out"])
        self.assertIn("串流開頭值", got["note"])
        self.assertIn("2/2", got["note"])
        self.assertEqual(got["wall_seconds"], 90)       # 10:00:00 → 10:01:30.5
        self.assertEqual(got["model"], "claude-opus-test")
        self.assertIsNone(got["effort"])

    def test_a2_every_last_row_finished_sums_the_last_rows_output(self):
        """夾具 B:msg_b1 兩列(1 → 120,最後一列 tool_use)+ msg_b2(45,end_turn)。
        A2 output 只信收尾;取第一列會得 None / 46;期望值手算;新案例。"""
        got = tc.summarize(rows_of("complete_stop.jsonl"))
        self.assertEqual(got["tokens_in"], 50)          # 20 + 30
        self.assertEqual(got["cache_write"], 500)       # 500 + 0
        self.assertEqual(got["cache_read"], 520)        # 0 + 520
        self.assertEqual(got["tokens_out"], 165)        # 120 + 45
        self.assertIsNone(got["note"])
        self.assertEqual(got["wall_seconds"], 42)
        self.assertEqual(got["model"], "claude-sonnet-test")

    def test_a2_missing_keys_stay_null_and_synthetic_never_wins_the_model(self):
        """夾具 C:沒有任何 cache 鍵、沒有 timestamp;model-b 2 / model-a 2 / <synthetic> 3。
        A1 null 不換 0、A2 model 規則;換成 0 或算進 synthetic 都紅;手算;新案例。"""
        got = tc.summarize(rows_of("null_and_synthetic.jsonl"))
        self.assertEqual(got["tokens_in"], 16)          # 7 + 5 + 0 + 0 + 0 + 3 + 1
        self.assertIsNone(got["cache_write"])
        self.assertIsNone(got["cache_read"])
        self.assertEqual(got["tokens_out"], 7)          # 1 + 2 + 0 + 0 + 0 + 4,msg_c7 缺鍵算 0
        self.assertIsNone(got["wall_seconds"])
        self.assertEqual(got["model"], "model-b")       # 同票數取先出現的

    def test_a2_no_assistant_usage_is_all_null(self):
        """A1 缺料明確 null;回 0 會紅;常數;新案例。"""
        got = tc.summarize([{"type": "user", "message": {"role": "user", "content": "hi"}}])
        for key in ("model", "tokens_in", "tokens_out", "cache_write", "cache_read",
                    "wall_seconds", "effort"):
            self.assertIsNone(got[key], key)
        self.assertEqual(got["note"], tc.EMPTY_NOTE)

    def test_a3_d4_ticket_and_role_examples(self):
        """原 #95 D4 的例子。A3 歸票與角色;關鍵字順序或 300 字界線錯會紅;票面字面;新案例。"""
        cases = [("#655 reviewer", "", None, ("655", "reviewer")),
                 ("調查 flaky", "你是 #12 的實作者…", None, ("12", "investigator")),
                 ("A93 opener", "沒有井號", None, (None, "opener")),
                 ("", "列出檔案", None, (None, "subagent")),
                 ("", "列出檔案", "Explore", (None, "Explore"))]
        for description, prompt, agent_type, want in cases:
            got = (tc.guess_ticket(description, prompt),
                   tc.guess_role(description, prompt, agent_type))
            self.assertEqual(got, want, (description, prompt))
        late = "x" * 300 + " #7 worker"
        self.assertIsNone(tc.guess_ticket("", late), "prompt 只看前 300 字")
        self.assertEqual(tc.guess_role("", late), "subagent")

    def test_a5_reader_takes_the_last_row_per_session_and_agent(self):
        """A5 讀端;取第一筆或與別種事件相加會紅;常數;新案例。"""
        events = [
            {"kind": "agent.cost", "session_id": "s1", "agent_id": None, "tokens_in": 10},
            {"kind": "agent.done", "session_id": "s1", "tokens_in": 999},
            {"kind": "agent.cost", "session_id": "s1", "agent_id": "a1", "tokens_in": 1},
            {"kind": "agent.cost", "session_id": "s1", "agent_id": None, "tokens_in": 20},
            {"kind": "agent.cost", "session_id": "s1", "agent_id": "a1", "tokens_in": 2},
            {"kind": "agent.cost", "session_id": "s1", "agent_id": "a2", "tokens_in": 3},
            {"kind": "agent.cost", "session_id": "s2", "agent_id": None, "tokens_in": 4},
        ]
        got = {(row["session_id"], row["agent_id"]): row["tokens_in"]
               for row in tc.latest_costs(events)}
        self.assertEqual(got, {("s1", None): 20, ("s1", "a1"): 2, ("s1", "a2"): 3,
                               ("s2", None): 4})

    def test_t3_parser_functions_have_no_side_effects(self):
        """T3 純函式;改輸入或寫檔會紅;sha / 目錄清單比對;新案例。"""
        path = os.path.join(FIXTURES, "dup_no_stop.jsonl")
        with open(path, "rb") as handle:
            before = hashlib.sha256(handle.read()).hexdigest()
        listing = sorted(os.listdir(FIXTURES))
        rows = tc.read_rows(path)
        frozen = copy.deepcopy(rows)
        tc.summarize(rows)
        tc.first_prompt(rows)
        tc.guess_role("#1 worker", tc.first_prompt(rows))
        events = [{"kind": "agent.cost", "session_id": "s", "agent_id": None}]
        tc.latest_costs(events)
        self.assertEqual(rows, frozen)
        self.assertEqual(events, [{"kind": "agent.cost", "session_id": "s", "agent_id": None}])
        with open(path, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), before)
        self.assertEqual(sorted(os.listdir(FIXTURES)), listing)


class A5CostTotalIgnoresAgentCost(unittest.TestCase):
    """A5:agent.cost 是猜的票,不進 cost_total(M3 把它算進去 → 這一條紅)。"""

    def test_a5_cost_total_counts_only_the_ticket_cost_rows(self):
        """A5 不相加;M3 會紅;期望值由兩列 cost[] 手算;新案例。"""
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "board"))
        with open(os.path.join(home, "board", "config.json"), "w") as handle:
            handle.write("{}")
        agent = {"kind": "agent.cost", "ticket": "1", "role": "main", "session_id": "s",
                 "agent_id": None, "tokens_in": 999, "wall_seconds": 999}
        with open(os.path.join(home, "board", "events.jsonl"), "w") as handle:
            handle.write(json.dumps(agent) + "\n")
        ticket = {"id": "1", "created": "2026-10-01T00:00:00+08:00", "cost": [
            {"role": "worker", "round": 1, "tokens_in": 100, "tokens_out": 10, "cache_write": 5,
             "cache_read": 50, "wall_seconds": 60, "outcome": "handed-in"},
            {"role": "reviewer", "round": None, "tokens_in": 30, "tokens_out": 3,
             "cache_write": 0, "cache_read": 7, "wall_seconds": 20, "outcome": "pass"}]}
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        env["AC_ROOT"] = home
        with mock.patch.dict(os.environ, env, clear=True):
            total = load("ticket_for_cost", "ticket.py").cost_total(ticket)
        self.assertEqual(total["runs"], 2)
        self.assertEqual(total["wall_seconds"], 80)         # 60 + 20
        self.assertEqual(set(total["by_role"]), {"worker", "reviewer"})
        self.assertEqual(total["by_role"]["worker"]["tokens_in"], 100)


class HookCase(unittest.TestCase):
    """拋棄式控制根 + 一份夾具拷貝(推導路徑要在拷貝裡成立)。"""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.root = os.path.join(self.home, "ctl")
        os.makedirs(os.path.join(self.root, "board"))
        with open(os.path.join(self.root, "board", "config.json"), "w") as handle:
            handle.write("{}")
        self.t = os.path.join(self.home, "transcripts")
        shutil.copytree(FIXTURES, self.t)
        self.events = os.path.join(self.root, "board", "events.jsonl")

    def run_hook(self, stdin, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        env["AC_ROOT"] = self.root
        env.update(extra)
        if not isinstance(stdin, str):
            stdin = json.dumps(stdin)
        return subprocess.run(["python3", HOOK], input=stdin, cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=60)

    def rows(self):
        if not os.path.exists(self.events):
            return []
        with open(self.events, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def only_event(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        rows = self.rows()
        self.assertEqual(len(rows), 1, result.stderr)
        row = rows[0]
        self.assertEqual(row["kind"], "agent.cost")
        self.assertEqual(set(row), EVENT_KEYS | ENVELOPE)
        self.assertEqual(row["by"], "agent-cost-hook")
        for key in ("round", "outcome", "run_id", "effort"):
            self.assertIsNone(row[key], key)
        return row

    def session_end(self):
        return {"hook_event_name": "SessionEnd", "session_id": "s-1", "cwd": self.root,
                "transcript_path": os.path.join(self.t, "session.jsonl")}


class T2Hook(HookCase):

    def test_a3_session_end_reads_only_the_main_transcript(self):
        """A1 鍵集合 / A3 SessionEnd;重算子 agent 或漏鍵會紅;手算;新案例。"""
        row = self.only_event(self.run_hook(self.session_end()))
        self.assertEqual(row["role"], "main")
        self.assertIsNone(row["ticket"])
        self.assertIsNone(row["agent_id"])
        self.assertEqual(row["session_id"], "s-1")
        self.assertEqual(row["tokens_in"], 210)          # 100 + 110(不含 subagents/ 的 40、9)
        self.assertEqual(row["cache_write"], 1000)       # 1000 + 0
        self.assertEqual(row["cache_read"], 1000)        # 0 + 1000
        self.assertIsNone(row["tokens_out"])             # msg_s2 沒有 stop_reason
        self.assertEqual(row["wall_seconds"], 600)
        self.assertEqual(row["model"], "claude-opus-test")

    def test_a3_subagent_stop_derives_the_path_from_transcript_path_and_agent_id(self):
        """A3 推導路徑 + meta 歸票;拿掉推導會紅;手算;新案例。"""
        row = self.only_event(self.run_hook({
            "hook_event_name": "SubagentStop", "session_id": "s-1", "agent_id": "a1",
            "cwd": self.root, "transcript_path": os.path.join(self.t, "session.jsonl")}))
        self.assertEqual((row["ticket"], row["role"], row["agent_id"]), ("655", "reviewer", "a1"))
        self.assertEqual((row["tokens_in"], row["cache_write"], row["cache_read"],
                          row["tokens_out"]), (40, 300, 0, 60))

    def test_a3_agent_transcript_path_wins_over_the_derived_one(self):
        """agent_transcript_path 指夾具 B,推導路徑會指 agent-a1 —— 讀到的必須是 B。
        A3 優先序;先推導會紅;手算;新案例。"""
        row = self.only_event(self.run_hook({
            "hook_event_name": "SubagentStop", "session_id": "s-1", "agent_id": "a1",
            "cwd": self.root, "transcript_path": os.path.join(self.t, "session.jsonl"),
            "agent_transcript_path": os.path.join(self.t, "complete_stop.jsonl")}))
        self.assertEqual((row["tokens_in"], row["tokens_out"]), (50, 165))
        self.assertEqual((row["ticket"], row["role"]), (None, "investigator"))

    def test_a3_missing_meta_and_no_keyword_is_subagent(self):
        """A3 meta 缺失;回 agentType 以外的猜測會紅;常數;新案例。"""
        row = self.only_event(self.run_hook({
            "hook_event_name": "SubagentStop", "session_id": "s-1", "agent_id": "a2",
            "cwd": self.root, "transcript_path": os.path.join(self.t, "session.jsonl")}))
        self.assertEqual((row["ticket"], row["role"]), (None, "subagent"))
        self.assertEqual((row["tokens_in"], row["cache_write"], row["cache_read"],
                          row["tokens_out"]), (9, None, None, 1))

    def test_a4_skips_emit_nothing_print_nothing_and_exit_zero(self):
        """壞 stdin / 缺 transcript / AC_ROLE=worker / cwd 在 `<根>-wt/` 底下 / 別的 hook。
        最後同一個根跑一次正常的,證明前面的「沒有」不是空真。
        A4;拿掉任一條不觸發會紅;stdout / rc / 事件數;新案例。"""
        good = self.session_end()
        missing = dict(good, transcript_path=os.path.join(self.t, "nope.jsonl"))
        in_copy = dict(good, cwd=os.path.join(self.home, "ctl-wt", "fix-t1", "work"))
        cases = [("bad stdin", "{not json", {}, True),
                 ("missing transcript", missing, {}, True),
                 ("AC_ROLE=worker", good, {"AC_ROLE": "worker"}, False),
                 ("copy cwd", in_copy, {}, False),
                 ("other hook", dict(good, hook_event_name="Stop"), {}, False)]
        for label, stdin, env, says in cases:
            result = self.run_hook(stdin, **env)
            self.assertEqual(result.returncode, 0, label)
            self.assertEqual(result.stdout, "", label)
            self.assertEqual(self.rows(), [], label)
            if says:
                self.assertIn("agent-cost-hook:", result.stderr, label)
        self.only_event(self.run_hook(good, AC_ROLE="main"))


class T155NoDispatchPathMark(HookCase):
    """#155 T7: #152 undone - a dispatch path in the first prompt no longer pins the event;
    ticket / role are the #95 guess (description first), round / run_id null. Fixture
    literals only (usage 11/22/33/44 written below)."""

    def write_subagent(self, agent, prompt, description):
        sub = os.path.join(self.t, "session", "subagents")
        rows = [{"type": "user", "message": {"role": "user", "content": prompt},
                 "timestamp": "2026-10-22T09:00:00.000Z"},
                {"type": "assistant", "message": {
                    "id": "msg_t155", "type": "message", "role": "assistant",
                    "model": "claude-opus-test", "stop_reason": "end_turn",
                    "usage": {"input_tokens": 11, "cache_creation_input_tokens": 22,
                              "cache_read_input_tokens": 33, "output_tokens": 44}},
                 "timestamp": "2026-10-22T09:00:01.000Z"}]
        with open(os.path.join(sub, "agent-%s.jsonl" % agent), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(os.path.join(sub, "agent-%s.meta.json" % agent), "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "description": description}, fh)

    def test_t7_a_dispatch_path_in_the_prompt_is_ignored(self):
        self.write_subagent("x155", "先讀這一份:/x/reports/t4/20261022-1/dispatch-round1.md",
                            "#9 verifier")
        row = self.only_event(self.run_hook({
            "hook_event_name": "SubagentStop", "session_id": "s-155", "agent_id": "x155",
            "cwd": self.root, "transcript_path": os.path.join(self.t, "session.jsonl")}))
        self.assertEqual((row["ticket"], row["role"], row["agent_id"]),
                         ("9", "verifier", "x155"))
        self.assertIsNone(row["round"])
        self.assertIsNone(row["run_id"])
        self.assertEqual((row["tokens_in"], row["cache_write"], row["cache_read"],
                          row["tokens_out"]), (11, 22, 33, 44))

    def test_t7_session_end_is_unchanged(self):
        row = self.only_event(self.run_hook(self.session_end()))
        self.assertEqual(row["role"], "main")
        for key in ("ticket", "agent_id", "round", "run_id"):
            self.assertIsNone(row[key], key)

    def test_t7_the_parser_has_no_dispatch_regex(self):
        module = load("transcript_cost_t155", "transcript-cost.py")
        self.assertFalse(hasattr(module, "dispatch" + "_mark"))
        self.assertFalse(hasattr(module, "DISPATCH"))


if __name__ == "__main__":
    unittest.main()
