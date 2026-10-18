"""`scripts/role-models.py` 與 `board/role-models.json`(#116):角色 → 有序模型候選的唯一一張表,
與所有派工共用的純選路器。期望值全寫在這裡(D-049 ② + 10-17 補的原值),不從被測程式算。
不呼叫真 LLM:provider 的錯誤信封是手寫的假 log。"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import BOARD_DIR, SCRIPTS  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "role-models.py")
TABLE = os.path.join(BOARD_DIR, "role-models.json")
RUN_TIMEOUT = 30
SOL, ASTRA = "codex:gpt-5.6-sol", "codex:gpt-6-astra"
AGY = "agy:gemini-3.8-flash-high"


def cand(model, tool, effort=None, escalated=None, cap=None):
    return {"model": model, "tool": tool, "effort": effort, "effort_escalated": escalated,
            "dispatch_cap": cap}


INITIAL_ROLES = {
    "main": [cand("opus", "claude-code")],
    "worker": [cand(SOL, "codex", "high", "xhigh", 1), cand("opus", "claude-code", "high", None, 1)],
    "worker_simple": [cand("sonnet", "claude-code", "medium", None, 1)],
    "opener_complex": [cand(ASTRA, "codex", cap=5), cand("fable", "claude-code")],
    "opener": [cand("opus", "claude-code")],
    "verifier": [cand(SOL, "codex", "high", None, 1), cand("opus", "claude-code", cap=1)],
    "reviewer": [cand("opus", "claude-code", cap=1)],
    "consolidator": [cand("fable", "claude-code")],
    "design": [cand(ASTRA, "codex", cap=5), cand("fable", "claude-code")],
}
INITIAL_POLICIES = {
    "fallback": "next_candidate_only_on_quota_or_unavailable",
    "exhausted": "stop_and_decision",
    "scope": "dispatched_agents",
    "escalate_effort_when": ["ticket.complexity == complex",
                             "repair_round >= 2 and previous round red"],
    "repair_round_keeps_worker_model_and_effort": True,
    "verifier_prefer_different_model": False,
}
RESULT_KEYS = ["candidate_index", "dispatch_cap", "effort", "exhausted", "model", "reason",
               "role", "tool"]

# 假 provider 的 log。每一份都是那個 CLI 失敗時 log 的最後幾行。
QUOTA_CLAUDE = ('{"type":"system","subtype":"init"}\n'
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"Claude AI usage limit reached|1700000000"}\n')
QUOTA_CLAUDE_API = ('{"type":"error","error":{"type":"rate_limit_error",'
                    '"message":"Number of requests has exceeded your rate limit"}}\n')
QUOTA_CODEX = ('{"type":"thread.started"}\n'
               '{"type":"error","message":"You\'ve hit your usage limit. Try again later."}\n')
GONE_CODEX = ('{"type":"turn.failed","error":{"message":'
              '"The model `gpt-5.6-sol` does not exist or you do not have access to it."}}\n')
GONE_CLAUDE = ('{"type":"error","error":{"type":"not_found_error",'
               '"message":"model: claude-x"}}\n')
NO_CLI = "sh: codex: command not found\n"
AUTH_CODEX = ('{"type":"error","error":{"code":"invalid_api_key",'
              '"message":"Incorrect API key provided"}}\n')
TEST_LOG = ("FAIL: test_retry (tests.test_http.Retry)\n"
            "AssertionError: 429 != 200 : too many requests from the fake server\n"
            "Ran 12 tests in 0.4s\nFAILED (failures=1)\n")
PATCH_TEXT = ("+++ work/src/http.py\n"
              "+    if status == 429:  # rate limit / quota\n"
              '+        raise QuotaError("usage limit")\n')
NESTED = ('{"type":"user","message":{"content":[{"type":"tool_result",'
          '"content":"HTTP 429 Too Many Requests: rate limit"}]}}\n'
          '{"type":"result","subtype":"success","is_error":false,'
          '"result":"done; saw a 429 in the test log"}\n')
TIMEOUT_LOG = "auto-fix: worker 逾時 3600 秒\n"
AGY_UNKNOWN = '{"status":"ERROR","error":"not recognized as a known model"}\n'
AGY_EFFORT = ('{"status":"ERROR","error":"invalid model selection gemini-3.8-flash-high '
              'conflicts with --effort=max"}\n')
AGY_429 = '{"status":"ERROR","error":"429"}\n'
AGY_QUOTA = ('{"event":"result","result":{"status":"ERROR",'
             '"error":"RESOURCE_EXHAUSTED: 429 Too Many Requests"}}\n')


def env(**extra):
    """外面那一層的 `AC_*` 一個都不准進來(派工帶著 AC_ROOT 指到拋棄式控制根)。"""
    base = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    base.update(extra)
    return base


class RoleModels(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        with open(TABLE, encoding="utf-8") as handle:
            self.table = json.load(handle)

    def run_cli(self, *args, table=None, env_extra=None):
        argv = [sys.executable, "-B", SCRIPT]
        if table is not False:
            argv += ["--table", table or TABLE]
        done = subprocess.run(argv + list(args), capture_output=True, text=True,
                              timeout=RUN_TIMEOUT, env=env(**(env_extra or {})))
        return done.returncode, done.stdout, done.stderr

    def resolve(self, *args, table=None):
        rc, out, err = self.run_cli("resolve", *args, table=table)
        self.assertIn(rc, (0, 3), out + err)
        got = json.loads(out)
        self.assertEqual(sorted(got), RESULT_KEYS)
        return rc, got

    def refused(self, *args, table=None):
        rc, out, err = self.run_cli("resolve", *args, table=table)
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(out, "", "不派就不該印出一個候選")
        return err

    def evidence(self, text, name="worker.log"):
        path = os.path.join(self.home, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def after(self, role, index, log, rc=1, *more):
        return ("--role", role, "--exhausted-index", str(index), "--evidence", self.evidence(log),
                "--evidence-rc", str(rc), *more)

    def write_table(self, table):
        path = os.path.join(self.home, "role-models.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(table, handle, ensure_ascii=False)
        return path

    def pick(self, got):
        return (got["role"], got["candidate_index"], got["model"], got["tool"], got["effort"],
                got["dispatch_cap"])


class InitialTable(RoleModels):
    """A1(T1):表的初值逐角色逐格對 D-049 ② 原值。"""

    def test_a1_every_role_and_candidate_is_the_confirmed_initial_value(self):
        """四問:釘什麼 = 表初值;為何會紅 = 任一格偏離裁示;oracle = 本檔寫死的 D-049 ② 原值;
        **變異 M1**(全角色改 sol)→ 本案紅。"""
        self.assertEqual(self.table["schema_version"], 1)
        self.assertEqual(self.table["candidate_keys"],
                         ["model", "tool", "effort", "effort_escalated", "dispatch_cap"])
        self.assertEqual(list(self.table["roles"]), list(INITIAL_ROLES))
        for role, chain in INITIAL_ROLES.items():
            with self.subTest(role=role):
                self.assertEqual(self.table["roles"][role], chain)
        self.assertEqual(self.table["policies"], INITIAL_POLICIES)
        rc, out, err = self.run_cli("check")
        self.assertEqual(rc, 0, out + err)

    def test_a1_the_default_table_is_the_one_under_the_repo_root(self):
        """四問:釘什麼 = 不給 --table 讀 <根>/board/role-models.json;為何會紅 = 讀錯樹;
        oracle = 拋棄式根裡改過一格的表;變異:路徑寫死成腳本旁 → 讀到真表、model 不是 fable。"""
        root = os.path.join(self.home, "root")
        os.makedirs(os.path.join(root, "board"))
        with open(os.path.join(root, "board", "config.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        table = copy.deepcopy(self.table)
        table["roles"]["reviewer"] = [cand("fable", "claude-code", cap=1)]
        with open(os.path.join(root, "board", "role-models.json"), "w", encoding="utf-8") as handle:
            json.dump(table, handle)
        rc, out, err = self.run_cli("resolve", "--role", "reviewer", table=False,
                                    env_extra={"AC_ROOT": root})
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(json.loads(out)["model"], "fable")
        os.remove(os.path.join(root, "board", "role-models.json"))
        rc, out, err = self.run_cli("resolve", "--role", "reviewer", table=False,
                                    env_extra={"AC_ROOT": root})
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("讀不到角色模型表", err)

    def test_a1_a4_a_bad_cell_anywhere_refuses_every_role(self):
        """四問:釘什麼 = 未知角色 / 空鏈 / 非法 effort / 非法 cap / model 與 tool 不符 / 多鍵
        rc=2 不派,且壞在別的角色也整張不用(A4 不默默跳過);為何會紅 = 驗證漏一類;
        oracle = 逐格手改的表與錯誤訊息裡的欄位名;變異:load_table 不擋問題 → resolve 回 rc=0。"""
        def broken(change):
            table = copy.deepcopy(self.table)
            change(table)
            return self.write_table(table)

        rows = (
            ("roles.opener 要是非空 list", lambda t: t["roles"].__setitem__("opener", [])),
            ("roles.design[0].effort", lambda t: t["roles"]["design"][0].__setitem__("effort", "turbo")),
            ("roles.reviewer[0].effort", lambda t: t["roles"]["reviewer"][0].__setitem__("effort", "ultra")),
            ("roles.worker[0].effort_escalated",
             lambda t: t["roles"]["worker"][0].__setitem__("effort_escalated", "warp")),
            ("roles.design[0].dispatch_cap", lambda t: t["roles"]["design"][0].__setitem__("dispatch_cap", 0)),
            ("roles.design[0].dispatch_cap", lambda t: t["roles"]["design"][0].__setitem__("dispatch_cap", "5")),
            ("roles.design[1].dispatch_cap", lambda t: t["roles"]["design"][1].__setitem__("dispatch_cap", True)),
            ("roles.verifier[1].dispatch_cap", lambda t: t["roles"]["verifier"][1].__setitem__("dispatch_cap", 2)),
            ("不一致", lambda t: t["roles"]["consolidator"][0].__setitem__("tool", "codex")),
            ("的鍵要恰是", lambda t: t["roles"]["main"][0].__setitem__("tier", "x")),
            ("escalate_effort_when",
             lambda t: t["policies"]["escalate_effort_when"].append("always")),
        )
        for word, change in rows:
            with self.subTest(word=word):
                path = broken(change)
                err = self.refused("--role", "worker", table=path)
                self.assertIn(word, err)
                rc, out, err = self.run_cli("check", table=path)
                self.assertEqual(rc, 2, out + err)
        self.assertIn("未知角色 owner", self.refused("--role", "owner"))

    def test_131_a1_an_agy_prefix_mismatch_invalidates_the_table(self):
        """四問:量 agy 前綴/tool 對稱;值=A1;舊值放行;表真的含完整候選。"""
        for model, tool in ((AGY, "codex"), ("opus", "agy")):
            with self.subTest(model=model, tool=tool):
                table = copy.deepcopy(self.table)
                table["roles"]["worker"][0].update(model=model, tool=tool, effort="high",
                                                    effort_escalated="high")
                err = self.refused("--role", "worker", table=self.write_table(table))
                self.assertIn("不一致", err)
                self.assertIn("agy", err)


class Effort(RoleModels):
    """A3 / A7(T1):effort 取法與 complexity 選鏈。"""

    def test_a3_worker_effort_default_complex_and_red_repair(self):
        """四問:釘什麼 = sol 預設 high、complex → xhigh、修補第 2 輪仍紅 → xhigh、其他修補沿用;
        為何會紅 = 升級條件寫錯或修補輪換模型;oracle = D-049 補的 worker_effort 原話;
        變異:升級條件改成 repair_round >= 1 → 「第 1 輪紅」那列紅。"""
        rows = (
            ((), (SOL, "high")),
            (("--complexity", "complex"), (SOL, "xhigh")),
            (("--repair-round", "2", "--last-red", "--worker-model", SOL), (SOL, "xhigh")),
            (("--repair-round", "3", "--last-red", "--worker-model", SOL), (SOL, "xhigh")),
            (("--repair-round", "2", "--worker-model", SOL), (SOL, "high")),
            (("--repair-round", "1", "--last-red", "--worker-model", SOL), (SOL, "high")),
            (("--repair-round", "1", "--worker-model", "opus"), ("opus", "high")),
            (("--repair-round", "2", "--last-red", "--worker-model", "opus"), ("opus", "high")),
        )
        for args, want in rows:
            with self.subTest(args=args):
                _, got = self.resolve("--role", "worker", *args)
                self.assertEqual((got["role"], got["model"], got["effort"]), ("worker", *want))
        err = self.refused("--role", "worker", "--repair-round", "1", "--worker-model", "haiku")
        self.assertIn("表外模型先改表或由主線裁示", err)

    def test_a3_opus_takeover_is_high_and_simple_sonnet_stays_medium(self):
        """四問:釘什麼 = sol 額度耗盡後 opus 接手 effort=high(不是 null)、sonnet medium 不降級、
        單一 fable 整理無 effort 無 cap;為何會紅 = 換候選時 effort 沒跟著換;oracle = 表初值;
        變異:換候選時沿用第 0 格的 effort → complex 那列給 opus xhigh、紅。"""
        _, got = self.resolve(*self.after("worker", 0, QUOTA_CODEX))
        self.assertEqual(self.pick(got), ("worker", 1, "opus", "claude-code", "high", 1))
        _, got = self.resolve(*self.after("worker", 0, QUOTA_CODEX, 1, "--complexity", "complex"))
        self.assertEqual(self.pick(got), ("worker", 1, "opus", "claude-code", "high", 1))
        _, got = self.resolve("--role", "worker", "--complexity", "simple")
        self.assertEqual(self.pick(got), ("worker_simple", 0, "sonnet", "claude-code", "medium", 1))
        _, got = self.resolve("--role", "consolidator")
        self.assertEqual(self.pick(got), ("consolidator", 0, "fable", "claude-code", None, None))

    def test_a3_the_verifier_chain_never_looks_at_the_worker(self):
        """四問:釘什麼 = verifier 不論 worker 是 sol/opus/fable 都 sol → opus;為何會紅 = 依
        worker 換序;oracle = D-049 補「不強制異模型」;**變異 M5** → 本案紅。"""
        for worker in (None, SOL, "opus", "fable"):
            more = ("--worker-model", worker, "--repair-round", "2", "--last-red") if worker else ()
            with self.subTest(worker=worker):
                _, got = self.resolve("--role", "verifier", *more)
                self.assertEqual(self.pick(got), ("verifier", 0, SOL, "codex", "high", 1))
                _, got = self.resolve(*self.after("verifier", 0, QUOTA_CODEX, 1, *more))
                self.assertEqual(self.pick(got), ("verifier", 1, "opus", "claude-code", None, 1))

    def test_a7_complexity_picks_the_chain_and_anything_else_is_refused(self):
        """四問:釘什麼 = opener 缺 → opener、complex → opener_complex、worker 缺 → worker、
        其他值 rc=2;為何會紅 = 多收一種值或選錯鏈;oracle = A7 原文;變異:complexity 不驗
        → 「medium」那列 rc=0。"""
        _, got = self.resolve("--role", "opener")
        self.assertEqual(self.pick(got), ("opener", 0, "opus", "claude-code", None, None))
        _, got = self.resolve("--role", "opener", "--complexity", "complex")
        self.assertEqual(self.pick(got), ("opener_complex", 0, ASTRA, "codex", None, 5))
        _, got = self.resolve("--role", "worker")
        self.assertEqual(got["role"], "worker")
        for bad in ("medium", "Complex", ""):
            with self.subTest(bad=bad):
                self.assertIn("complexity=", self.refused("--role", "worker", "--complexity", bad))


class TicketEffort(RoleModels):
    """A5(T1):票明寫的 effort 優先;換 provider 時不合法的不跨工具硬套。"""

    def test_a5_a_ticket_effort_wins_unless_the_tool_cannot_take_it(self):
        """四問:釘什麼 = 票 effort 蓋過表(含升級)、ultra 不帶給 claude-code 而記原因;
        為何會紅 = 跨工具硬套;oracle = EFFORT_LEVELS 的 claude-code 沒有 ultra(抄自 claude --help);
        **變異 M4**(直接沿用 ultra)→ 本案紅。"""
        _, got = self.resolve("--role", "worker", "--ticket-effort", "ultra")
        self.assertEqual((got["model"], got["effort"]), (SOL, "ultra"))
        _, got = self.resolve("--role", "worker", "--complexity", "complex", "--ticket-effort", "medium")
        self.assertEqual((got["model"], got["effort"]), (SOL, "medium"))
        _, got = self.resolve(*self.after("worker", 0, QUOTA_CODEX, 1, "--ticket-effort", "max"))
        self.assertEqual((got["model"], got["effort"]), ("opus", "max"))
        _, got = self.resolve(*self.after("worker", 0, QUOTA_CODEX, 1, "--ticket-effort", "ultra"))
        self.assertEqual((got["model"], got["tool"], got["effort"]), ("opus", "claude-code", "high"))
        self.assertIn("effort=ultra 不在 tool=claude-code", got["reason"])
        self.assertNotIn("ultra", got["model"], "model 格不以字尾藏 effort")


class Fallback(RoleModels):
    """A2 / A4(T2):只有可信的額度 / 不可用證據才換下一個;耗盡回 exhausted。"""

    def test_a2_provider_envelopes_and_a_missing_cli_move_to_the_next(self):
        """四問:釘什麼 = claude/codex 的額度信封、模型不存在信封、rc=127 都換下一格;
        為何會紅 = 認不得真的 provider 錯誤;oracle = 手寫假信封;變異:只認 claude 形狀 →
        codex 那幾列紅。"""
        rows = ((QUOTA_CLAUDE, 1, "quota_exhausted"), (QUOTA_CLAUDE_API, 1, "quota_exhausted"),
                (QUOTA_CODEX, 1, "quota_exhausted"), (GONE_CODEX, 1, "provider_unavailable"),
                (GONE_CLAUDE, 1, "provider_unavailable"), (NO_CLI, 127, "provider_unavailable"))
        for log, rc, kind in rows:
            with self.subTest(log=log[:40]):
                path = self.evidence(log)
                code, out, err = self.run_cli("classify", "--evidence", path, "--rc", str(rc))
                self.assertEqual(code, 0, out + err)
                self.assertEqual(json.loads(out)["kind"], kind)
                _, got = self.resolve(*self.after("worker", 0, log, rc))
                self.assertEqual((got["candidate_index"], got["model"]), (1, "opus"))

    def test_a2_ordinary_failures_and_a_literal_429_never_move(self):
        """四問:釘什麼 = 一般 rc≠0、測試紅、逾時、沒交 patch、patch 文字與工具輸出裡的 429
        都不換候選(rc=2、不印候選);為何會紅 = 用字面比對判額度(今天 auto-fix 的 grep);
        oracle = A2 原文;**變異 M2**(任意非零即 fallback)→ 本案紅。"""
        rows = ((TEST_LOG, 1), (PATCH_TEXT, 1), (NESTED, 1), (TIMEOUT_LOG, 124),
                ("", 1), ("no patch produced\n", 3), (QUOTA_CODEX, 0))
        for log, rc in rows:
            with self.subTest(log=log[:40], rc=rc):
                path = self.evidence(log)
                code, out, err = self.run_cli("classify", "--evidence", path, "--rc", str(rc))
                self.assertEqual(code, 0, out + err)
                self.assertEqual(json.loads(out)["kind"], "other")
                err = self.refused(*self.after("worker", 0, log, rc))
                self.assertIn("不換候選", err)
        self.assertIn("要與 --exhausted-index", self.refused("--role", "worker", "--evidence",
                                                          self.evidence(QUOTA_CODEX)))
        _, got = self.resolve("--role", "worker", "--exhausted-index", "0")
        self.assertEqual(got["model"], "opus")
        self.assertIn("由呼叫端標記", got["reason"])

    def test_a2_an_auth_error_stops_with_the_setting_problem(self):
        """四問:釘什麼 = 認證錯停下、印出那句設定問題、不換下一個;為何會紅 = 當成額度盲試;
        oracle = 假信封的 message;變異:auth 歸成 quota → 回 opus、rc=0、紅。"""
        err = self.refused(*self.after("worker", 0, AUTH_CODEX))
        self.assertIn("認證設定錯誤", err)
        self.assertIn("Incorrect API key provided", err)

    def test_a4_a_spent_chain_is_exhausted_not_opus(self):
        """四問:釘什麼 = 整串耗盡回 exhausted、model 空、rc=3,不回任意 Opus;為何會紅 = 退回
        預設模型;oracle = A4 原文;**變異 M3**(全耗回 opus)→ 本案紅。"""
        for role, last in (("worker", 1), ("reviewer", 0), ("design", 1)):
            with self.subTest(role=role):
                rc, got = self.resolve(*self.after(role, last, QUOTA_CLAUDE))
                self.assertEqual(rc, 3)
                self.assertEqual(self.pick(got), (role, None, None, None, None, None))
                self.assertTrue(got["exhausted"])
                self.assertIn("耗盡", got["reason"])
        self.assertIn("不在 worker 鏈的範圍",
                      self.refused(*self.after("worker", 2, QUOTA_CLAUDE)))

    def test_131_a6_agy_error_envelopes_are_classified_without_plain_text_guessing(self):
        """四問:量 direct/stream 信封與純文字;值=A6;舊值全 other;只讀頂層 provider 信封。"""
        rows = ((AGY_UNKNOWN, "provider_unavailable"), (AGY_EFFORT, "auth_config"),
                (AGY_429, "quota_exhausted"), (AGY_QUOTA, "quota_exhausted"))
        for log, want in rows:
            with self.subTest(want=want):
                code, out, err = self.run_cli("classify", "--evidence", self.evidence(log),
                                              "--rc", "1")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(json.loads(out)["kind"], want)
        code, out, err = self.run_cli("classify", "--evidence",
                                      self.evidence("429 rate limit\n"), "--rc", "1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["kind"], "other")


class ExplicitTicketCandidate(RoleModels):
    """#131 A10:worker 票的明確 model 是第一候選,後接去重的角色鏈。"""

    def explicit(self, model, tool="claude-code", effort="high", *more):
        return self.resolve("--role", "worker", "--ticket-model", model,
                            "--ticket-tool", tool, "--ticket-effort", effort, *more)

    def test_a10_agy_is_first_then_the_unchanged_worker_chain(self):
        """四問:量三格順序;值=A10 寫死;舊值忽略票 model;表有 sol/opus 兩格。"""
        _, first = self.explicit(AGY, "agy")
        _, second = self.explicit(AGY, "agy", "high", "--exhausted-index", "0")
        _, third = self.explicit(AGY, "agy", "high", "--exhausted-index", "1")
        self.assertEqual([(row["candidate_index"], row["model"], row["tool"], row["effort"])
                          for row in (first, second, third)],
                         [(0, AGY, "agy", "high"), (1, SOL, "codex", "high"),
                          (2, "opus", "claude-code", "high")])

    def test_a10_a_duplicate_explicit_model_is_removed_from_the_table_tail(self):
        """四問:量 opus 只出現一次;值=A10;舊值先 sol;用耗盡 index 證鏈尾。"""
        _, first = self.explicit("opus")
        _, second = self.explicit("opus", "claude-code", "high", "--exhausted-index", "0")
        rc, spent = self.explicit("opus", "claude-code", "high", "--exhausted-index", "1")
        self.assertEqual((first["model"], second["model"]), ("opus", SOL))
        self.assertEqual(rc, 3)
        self.assertTrue(spent["exhausted"])

    def test_a10_without_a_ticket_model_is_byte_for_byte_the_table_choice(self):
        """四問:量無 model 的完整結果;值=既有表;變異 M5 只影響有 model;非 fallback。"""
        _, plain = self.resolve("--role", "worker")
        _, explicit = self.resolve("--role", "worker", "--ticket-model", SOL,
                                   "--ticket-tool", "codex", "--ticket-effort", "high")
        self.assertEqual((plain["candidate_index"], plain["model"], plain["tool"], plain["effort"],
                          plain["dispatch_cap"]),
                         (explicit["candidate_index"], explicit["model"], explicit["tool"],
                          explicit["effort"], explicit["dispatch_cap"]))


class DispatchCap(RoleModels):
    """A8(T1):每次派工的份量上限只在派工前擋。"""

    def test_a8_items_over_the_cap_are_refused_with_the_batch_size(self):
        """四問:釘什麼 = astra --items 6 rc=2 並印上限訊息、5 過、fable null 不擋、worker 固定 1;
        為何會紅 = cap 不擋或擋錯候選;oracle = A8 原文的訊息句;**變異 M6**(cap 不擋)→ 本案紅。"""
        err = self.refused("--role", "opener", "--complexity", "complex", "--items", "6")
        self.assertIn("超過 codex:gpt-6-astra 的每次派工上限 5,請切成 ≤5 的批", err)
        rc, got = self.resolve("--role", "design", "--items", "5")
        self.assertEqual((rc, got["model"], got["dispatch_cap"]), (0, ASTRA, 5))
        _, got = self.resolve(*self.after("design", 0, QUOTA_CODEX, 1, "--items", "40"))
        self.assertEqual((got["model"], got["dispatch_cap"]), ("fable", None))
        _, got = self.resolve("--role", "consolidator", "--items", "40")
        self.assertEqual(got["model"], "fable")
        self.assertIn("每次派工上限 1", self.refused("--role", "worker", "--items", "2"))
        self.assertIn("每次派工上限 1", self.refused("--role", "verifier", "--items", "2"))
        self.assertIn("--items", self.refused("--role", "worker", "--items", "0"))


if __name__ == "__main__":
    unittest.main()
