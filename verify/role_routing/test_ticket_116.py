"""#116 唯一角色模型順序表 board/role-models.json 與共用選路器 scripts/role-models.py resolve。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 interface_contracts.initial_table 逐字抄 / 票面 acceptance 原話)
A1 | unit | 讀 board/role-models.json;逐角色 resolve | roles / policies 等於 initial_table;每候選恰五鍵;各角色預設取 index 0 | 票面 interface_contracts.initial_table
A1 | cli  | 拋棄式控制根放壞表(空鏈 / 非法 effort / 非法 cap / 多一鍵 / model 與 tool 不符)、未知角色 | rc=2,好表同一顆根 rc=0 | 票面 A1 原話
A2 | cli  | worker 無標記 / --exhausted-index 0 | 無標記取 sol;標記 0 才取 opus | 票面 A2 原話(只在標記後取下一個)
A3 | cli  | verifier 預設與接手;worker effort 各案 | sol high → opus;high / complex xhigh / 第 2 輪紅 xhigh / 其他 high;opus 接手 high;sonnet medium | 票面 A3 + D-049 補
A4 | cli  | 鏈尾再標記;壞設定放在鏈的第二格 | exhausted=true、model 不是 opus(null);第二格壞也 rc=2 | 票面 A4 原話
A5 | cli  | worker 預設 | model 是 codex:gpt-5.6-sol 原字,不帶 effort 字尾 | 票面 A5 末句
A6 | cli  | --help;main | rc 0;main → opus / claude-code | 票面 A6
A7 | cli  | worker / opener 配 --complexity simple|complex|缺|其他 | 對應鏈;其他值 rc=2 | 票面 A7
A8 | cli  | --items N 對各角色 | 超過 cap rc=2 並印上限句;astra 5 過 6 擋;fable null 不擋;worker 缺 --items 視為 1 | 票面 A8

## 介面字串
CLI   = scripts/role-models.py resolve --role R [--complexity] [--repair-round N] [--last-red] [--items N] [--exhausted-index I]
KEYS  = role / candidate_index / model / tool / effort / dispatch_cap / reason / exhausted   ← 票面 resolve_result
CAP_MSG = "超過 <model> 的每次派工上限 <cap>" 與 "≤<cap> 的批"                              ← 票面 A8 原話

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
壞表放在拋棄式控制根:複製 scripts/ 與 board/,覆寫 board/role-models.json,AC_ROOT 指它、跑那一份的
role-models.py(表靠腳本位置或 AC_ROOT 找都落在沙盒)。同一顆根先放好表跑一次 rc=0 當正控。
子行程的環境清掉 AC_ROOT / AC_TICKETS_DIR / AC_CONTROL_DIR(票面 test_plan)。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例。
A2 的「錯誤分類」(provider 錯誤信封 / 缺 CLI / 普通 log / 字面 429)與 A5 的「票 effort 覆寫」票面沒定輸入介面
(new_cli 沒有對應旗標或函式名),本檔不猜,在 EVIDENCE 提反駁。
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["role-models"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
TABLE_REL = os.path.join("board", "role-models.json")
SCRIPT_REL = os.path.join("scripts", "role-models.py")
ESC = "effort_" + "escalated"
CAP = "dispatch_" + "cap"
OPENER_COMPLEX = "opener_" + "complex"
CAP_SENTENCE = "的每次" + "派工上限"
RESULT_KEYS = {"role", "candidate_index", "model", "tool", "effort", CAP, "reason", "exhausted"}
SOL, ASTRA = "codex:gpt-5.6-sol", "codex:gpt-6-astra"


def cand(model, tool, effort, esc, cap):
    return {"model": model, "tool": tool, "effort": effort, ESC: esc, CAP: cap}


# 票面 interface_contracts.initial_table 逐字抄(D-049 ② + 補)
EXPECTED_ROLES = {
    "main": [cand("opus", "claude-code", None, None, None)],
    "worker": [cand(SOL, "codex", "high", "xhigh", 1),
               cand("opus", "claude-code", "high", None, 1)],
    "worker_simple": [cand("sonnet", "claude-code", "medium", None, 1)],
    OPENER_COMPLEX: [cand(ASTRA, "codex", None, None, 5),
                     cand("fable", "claude-code", None, None, None)],
    "opener": [cand("opus", "claude-code", None, None, None)],
    "verifier": [cand(SOL, "codex", "high", None, 1),
                 cand("opus", "claude-code", None, None, 1)],
    "reviewer": [cand("opus", "claude-code", None, None, 1)],
    "consolidator": [cand("fable", "claude-code", None, None, None)],
    "design": [cand(ASTRA, "codex", None, None, 5),
               cand("fable", "claude-code", None, None, None)],
}
EXPECTED_POLICIES = {
    "fallback": "next_candidate_only_on_quota_or_unavailable",
    "exhausted": "stop_and_decision",
    "scope": "dispatched_agents",
    "escalate_effort_when": ["ticket.complexity == complex",
                             "repair_round >= 2 and previous round red"],
    "repair_round_keeps_worker_model_and_effort": True,
    "verifier_prefer_different_model": False,
}
EXPECTED_KEYS = ["model", "tool", "effort", ESC, CAP]


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("AC_ROOT", "AC_TICKETS_DIR", "AC_CONTROL_DIR")}
    env.update(extra)
    return env


def run(case, *args, root=ROOT):
    """跑 <root>/scripts/role-models.py;回 (rc, stdout+stderr, 解得開的 stdout JSON 或 None)。"""
    script = os.path.join(root, SCRIPT_REL)
    case.assertTrue(os.path.isfile(script), "還沒有 %s" % SCRIPT_REL)
    env = clean_env(AC_ROOT=root) if root != ROOT else clean_env()
    proc = subprocess.run([sys.executable, script] + list(args), cwd=root, env=env,
                          capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    return proc.returncode, proc.stdout + proc.stderr, data


def resolve(case, *args, root=ROOT):
    rc, out, data = run(case, "resolve", *args, root=root)
    case.assertEqual(rc, 0, "resolve %s 應 rc 0:\n%s" % (" ".join(args), out))
    case.assertIsInstance(data, dict, "resolve %s 的 stdout 不是 JSON 物件:\n%s" % (" ".join(args), out))
    case.assertTrue(RESULT_KEYS <= set(data), "缺鍵 %s:\n%s" % (sorted(RESULT_KEYS - set(data)), out))
    return data


def refused(case, *args, root=ROOT):
    rc, out, _ = run(case, "resolve", *args, root=root)
    case.assertEqual(rc, 2, "resolve %s 應 rc=2,實得 %s:\n%s" % (" ".join(args), rc, out))
    case.assertNotIn("Traceback", out, "rc=2 不該是炸出來的:\n%s" % out)
    return out


def a_control_root(case, roles):
    """拋棄式控制根:scripts/ + board/ 的複本,role-models.json 換成 `roles`。"""
    where = tempfile.mkdtemp(prefix="t116-"); case.addCleanup(shutil.rmtree, where, True)
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(where, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "board"), os.path.join(where, "board"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    table_path = os.path.join(ROOT, TABLE_REL)
    case.assertTrue(os.path.isfile(table_path), "還沒有 %s" % TABLE_REL)
    with open(table_path, encoding="utf-8") as handle:
        table = json.load(handle)
    table["roles"] = roles
    with open(os.path.join(where, TABLE_REL), "w", encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False, indent=2)
    return where


class TheTableIsTheInitialTable(unittest.TestCase):

    def test_a1_table_equals_the_initial_table(self):
        """A1 board/role-models.json 的 roles / policies / candidate_keys 等於票面 initial_table,每候選恰五鍵。"""
        path = os.path.join(ROOT, TABLE_REL)
        self.assertTrue(os.path.isfile(path), "還沒有 %s" % TABLE_REL)
        with open(path, encoding="utf-8") as handle:
            table = json.load(handle)
        self.assertEqual(table.get("schema_version"), 1)
        self.assertEqual(table.get("candidate_keys"), EXPECTED_KEYS)
        self.assertEqual(table.get("roles"), EXPECTED_ROLES)
        self.assertEqual(table.get("policies"), EXPECTED_POLICIES)
        for role, chain in table["roles"].items():
            for index, one in enumerate(chain):
                self.assertEqual(sorted(one), sorted(EXPECTED_KEYS), "%s[%d] 不是恰五鍵" % (role, index))

    def test_a1_every_role_resolves_to_its_first_candidate(self):
        """A1 每個角色不帶旗標 resolve 都取鏈的 index 0(worker 缺 complexity 走 worker 鏈)。"""
        for role, chain in EXPECTED_ROLES.items():
            with self.subTest(role=role):
                got = resolve(self, "--role", role)
                first = chain[0]
                self.assertEqual(got["role"], role)
                self.assertEqual(got["candidate_index"], 0)
                self.assertEqual((got["model"], got["tool"], got["effort"], got[CAP]),
                                 (first["model"], first["tool"], first["effort"], first[CAP]))
                self.assertIs(got["exhausted"], False)
                self.assertTrue(got["reason"], "reason 不該是空的")


class BadSettingsAreRefused(unittest.TestCase):

    def test_a1_unknown_role_is_refused(self):
        """A1 未知角色 rc=2 不派。"""
        refused(self, "--role", "no-such-role")

    def test_a1_bad_tables_are_refused(self):
        """A1 空鏈、非法 effort、非法 effort_escalated、非法 cap、多一鍵、model 與 tool 不符 → rc=2。"""
        good = copy.deepcopy(EXPECTED_ROLES)
        root = a_control_root(self, good)
        positive = resolve(self, "--role", "worker", root=root)       # 正控:同一顆根放好表會過
        self.assertEqual(positive["model"], SOL)

        def bad(mutate):
            roles = copy.deepcopy(EXPECTED_ROLES)
            mutate(roles["worker"])
            return roles

        variants = {
            "empty-chain": lambda chain: chain.clear(),
            "effort-turbo": lambda chain: chain[0].update(effort="turbo"),
            "ultra-on-claude": lambda chain: chain[1].update(effort="ultra"),
            "escalated-turbo": lambda chain: chain[0].update({ESC: "turbo"}),
            "cap-zero": lambda chain: chain[0].update({CAP: 0}),
            "cap-negative": lambda chain: chain[0].update({CAP: -1}),
            "cap-string": lambda chain: chain[0].update({CAP: "1"}),
            "cap-float": lambda chain: chain[0].update({CAP: 1.5}),
            "sixth-key": lambda chain: chain[0].update(extra="x"),
            "missing-key": lambda chain: chain[0].pop(CAP),
            "codex-model-on-claude-tool": lambda chain: chain[0].update(tool="claude-code"),
            "claude-model-on-codex-tool": lambda chain: chain[1].update(tool="codex"),
        }
        for name, mutate in variants.items():
            with self.subTest(variant=name):
                root = a_control_root(self, bad(mutate))
                refused(self, "--role", "worker", root=root)

    def test_a4_a_bad_second_candidate_is_not_silently_skipped(self):
        """A4 整串內無效設定不能默默跳過:鏈的第二格壞了,resolve 也 rc=2(不是照樣回第一格)。"""
        roles = copy.deepcopy(EXPECTED_ROLES)
        roles["worker"][1]["effort"] = "turbo"
        root = a_control_root(self, roles)
        refused(self, "--role", "worker", root=root)

    def test_a7_unknown_complexity_is_refused(self):
        """A7 complexity 只收 simple|complex,其他值 rc=2。"""
        refused(self, "--role", "worker", "--complexity", "medium")
        refused(self, "--role", "opener", "--complexity", "hard")


class FallbackOnlyOnMarkedCandidates(unittest.TestCase):

    def test_a2_no_mark_stays_on_the_first_candidate(self):
        """A2 沒有 quota / unavailable 標記就不換:worker 取 sol;標記 index 0 才取 opus。"""
        first = resolve(self, "--role", "worker")
        self.assertEqual((first["candidate_index"], first["model"], first["tool"]), (0, SOL, "codex"))
        second = resolve(self, "--role", "worker", "--exhausted-index", "0")
        self.assertEqual((second["candidate_index"], second["model"], second["tool"]), (1, "opus", "claude-code"))
        self.assertIs(second["exhausted"], False)
        self.assertTrue(second["reason"])

    def test_a4_exhausted_chain_does_not_fall_back_to_opus(self):
        """A4 全鏈耗盡回 exhausted,不回任意 Opus。"""
        for role, last in (("worker", 1), ("main", 0), ("reviewer", 0), ("verifier", 1)):
            with self.subTest(role=role):
                rc, out, data = run(self, "resolve", "--role", role, "--exhausted-index", str(last))
                self.assertIsInstance(data, dict, "耗盡時 stdout 也要是結果 JSON:\n%s" % out)
                self.assertTrue(RESULT_KEYS <= set(data), out)
                self.assertIs(data["exhausted"], True, out)
                self.assertIsNone(data["model"], out)
                self.assertIsNone(data["candidate_index"], out)
                self.assertEqual(data["role"], role)
                self.assertTrue(data["reason"])


class EffortAndChainSelection(unittest.TestCase):

    def assert_pick(self, got, model, effort):
        self.assertEqual((got["model"], got["effort"]), (model, effort))

    def test_a3_verifier_is_sol_then_opus(self):
        """A3 verifier 鏈固定 sol → opus(effort 照表:sol high、opus null)。"""
        self.assert_pick(resolve(self, "--role", "verifier"), SOL, "high")
        self.assert_pick(resolve(self, "--role", "verifier", "--exhausted-index", "0"), "opus", None)

    def test_a3_worker_effort_cases(self):
        """A3 worker effort:預設 high;complex → xhigh;repair_round ≥ 2 且上一輪紅 → xhigh;其他修補輪沿用 high。"""
        self.assert_pick(resolve(self, "--role", "worker"), SOL, "high")
        self.assert_pick(resolve(self, "--role", "worker", "--complexity", "complex"), SOL, "xhigh")
        self.assert_pick(resolve(self, "--role", "worker", "--repair-round", "2", "--last-red"), SOL, "xhigh")
        self.assert_pick(resolve(self, "--role", "worker", "--repair-round", "3", "--last-red"), SOL, "xhigh")
        self.assert_pick(resolve(self, "--role", "worker", "--repair-round", "2"), SOL, "high")
        self.assert_pick(resolve(self, "--role", "worker", "--repair-round", "1", "--last-red"), SOL, "high")

    def test_a3_opus_takeover_is_high_not_null(self):
        """A3 opus 接手取其候選 effort=high,不是 null;升級條件下 effort_escalated=null 仍沿用 high。"""
        self.assert_pick(resolve(self, "--role", "worker", "--exhausted-index", "0"), "opus", "high")
        self.assert_pick(resolve(self, "--role", "worker", "--exhausted-index", "0",
                                 "--complexity", "complex"), "opus", "high")
        self.assert_pick(resolve(self, "--role", "worker", "--exhausted-index", "0",
                                 "--repair-round", "2", "--last-red"), "opus", "high")

    def test_a3_simple_sonnet_stays_medium(self):
        """A3 sonnet 走 worker_simple medium,修補第 2 輪紅也不升不降(effort_escalated=null 沿用)。"""
        self.assert_pick(resolve(self, "--role", "worker", "--complexity", "simple"), "sonnet", "medium")
        self.assert_pick(resolve(self, "--role", "worker", "--complexity", "simple",
                                 "--repair-round", "2", "--last-red"), "sonnet", "medium")

    def test_a3_single_fable_consolidator(self):
        """A3 / D-049 整理者是單一 fable,沒有下一格。"""
        self.assert_pick(resolve(self, "--role", "consolidator"), "fable", None)
        rc, out, data = run(self, "resolve", "--role", "consolidator", "--exhausted-index", "0")
        self.assertIsInstance(data, dict, out)
        self.assertIs(data["exhausted"], True, out)

    def test_a5_model_is_the_bare_model_id(self):
        """A5 model 格為實際模型,不以字尾藏 effort。"""
        got = resolve(self, "--role", "worker", "--complexity", "complex")
        self.assertEqual(got["model"], SOL)
        self.assertEqual(got["effort"], "xhigh")

    def test_a6_help_and_main(self):
        """A6 交可用 CLI:--help rc 0;main 只是建議 opus / claude-code。"""
        rc, out, _ = run(self, "--help")
        self.assertEqual(rc, 0, out)
        self.assertIn("resolve", out)
        got = resolve(self, "--role", "main")
        self.assertEqual((got["model"], got["tool"]), ("opus", "claude-code"))

    def test_a7_complexity_picks_the_chain(self):
        """A7 worker:simple → worker_simple、complex → worker 升級、缺 → worker;opener:complex → opener_complex、缺 → opener。"""
        self.assert_pick(resolve(self, "--role", "worker", "--complexity", "simple"), "sonnet", "medium")
        self.assert_pick(resolve(self, "--role", "worker", "--complexity", "complex"), SOL, "xhigh")
        self.assert_pick(resolve(self, "--role", "worker"), SOL, "high")
        hard = resolve(self, "--role", "opener", "--complexity", "complex")
        self.assertEqual((hard["model"], hard["tool"], hard[CAP]), (ASTRA, "codex", 5))
        plain = resolve(self, "--role", "opener")
        self.assertEqual((plain["model"], plain["tool"]), ("opus", "claude-code"))


class DispatchCap(unittest.TestCase):

    def assert_cap_refusal(self, out, model, cap):
        self.assertIn("超過 %s %s %d" % (model, CAP_SENTENCE, cap), out)
        self.assertIn("≤%d 的批" % cap, out)

    def test_a8_astra_cap_five(self):
        """A8 astra --items 6 rc=2 並印上限句;--items 5 過;fable(null)--items 6 不擋。"""
        for role in (OPENER_COMPLEX, "design"):
            with self.subTest(role=role):
                args = ["--role", "opener", "--complexity", "complex"] if role == OPENER_COMPLEX \
                    else ["--role", role]
                out = refused(self, *(args + ["--items", "6"]))
                self.assert_cap_refusal(out, ASTRA, 5)
                ok = resolve(self, *(args + ["--items", "5"]))
                self.assertEqual((ok["model"], ok[CAP]), (ASTRA, 5))
                fable = resolve(self, *(args + ["--items", "6", "--exhausted-index", "0"]))
                self.assertEqual((fable["model"], fable[CAP]), ("fable", None))

    def test_a8_worker_verifier_reviewer_cap_one(self):
        """A8 worker/verifier/reviewer cap 固定 1;--items 缺視為 1 會過,--items 2 擋。"""
        for role, model in (("worker", SOL), ("verifier", SOL), ("reviewer", "opus")):
            with self.subTest(role=role):
                got = resolve(self, "--role", role)
                self.assertEqual(got[CAP], 1)
                resolve(self, "--role", role, "--items", "1")
                out = refused(self, "--role", role, "--items", "2")
                self.assert_cap_refusal(out, model, 1)


if __name__ == "__main__":
    unittest.main()
