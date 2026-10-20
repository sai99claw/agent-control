"""#131 auto-fix 支援 agy worker、修補輪、驗證者、錯誤換候選與成本信封。

## 驗收表(期望值來源獨立於被測程式:票面 acceptance、E1/E4/E5、既有命令 golden)
A1  | cli     | agy model/tool 與四種不一致組合 | 逐字命令;不一致 rc=2 且點名兩格 | 票面 A1、E1/E4
A2  | cli     | 五種 effort、後綴衝突、ultra | 合法值進命令;衝突與非法值 rc=2 | 票面 A2、E5
A3  | integ   | PATH 前置假 agy 交件 | cwd=票分支 worktree(#144)、stdin=0、-p= 絕對派工文、無 add-dir/sandbox | 票面 A3
A4  | integ   | 假 agy 在主 repo 寫檔 | rc=8、Blocked、guard-violation、不套 patch | 票面 A4、D-048
A5  | integ   | PATH 沒有 agy、角色鏈下一格 opus | 不起 agy;只起 opus;無 agy cost | 票面 A5、#121
A6  | integ   | direct/stream 錯誤信封及 429 實跑 | 三分類正確;429 後起 opus | 票面 A6、E5 原文
A7  | integ   | 假 agy 成功信封 | 15 鍵;input/output 不加 thinking;cache_write null;session/note | 票面 A7、E1
A8  | integ   | 第一輪紅後修補、agy 驗證者 | 修補沿用 model/effort;驗證者同形;session 不可接回 | 票面 A8
A9  | cli     | 既有 Claude/Codex agent-command | stdout 與既有 golden 逐字相同 | #114/#115 golden
A10 | cli     | 手造 worker 鏈 sol→opus 加票 model | agy→sol→opus;opus→sol;無票 model 等於表首 | 票面 A10

## 介面字串(全部由票面或既有 golden 寫死)
AGY_COMMAND = `agy --model gemini-3.8-flash-high --effort high --dangerously-skip-permissions --output-format stream-json`
PROMPT = `-p=Read <absolute dispatch-round1.md> and follow it.`
QUOTA = `RESOURCE_EXHAUSTED: 429 Too Many Requests`

## 怎麼做假(不上真埠、不呼叫真 agy / LLM)
CLI 案例直接跑 repo 腳本。整合案例借 tests.test_auto_fix.AutoFixBase 的拋棄式 git 沙盒；PATH 最前方放
假 agy，記 argv/cwd/stdin 到測試自己的 tmp，成功時交最小 patch 與手寫 stream-json result。

## 不做
不改產品碼、不讀真秘密、不呼叫真 agy、不跑全套、不把 worker 測試當 oracle。
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["agy-dispatch"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import DEFAULT_CONFIG, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402

AGY_MODEL = "agy:gemini-3.8-flash-high"
AGY_COMMAND = ("agy --model gemini-3.8-flash-high --effort high "
               "--dangerously-skip-permissions --output-format stream-json\n")
CLAUDE_COMMAND = "claude -p --model opus --permission-mode acceptEdits\n"
CODEX_COMMAND = ("codex exec -m gpt-5.6-sol -s danger-full-access "
                 "-c 'approval_policy=\"never\"' -C . --skip-git-repo-check -\n")
SOL = "codex:gpt-5.6-sol"
COST_KEYS = {"role", "round", "model", "tokens_in", "tokens_out", "cache_write",
             "cache_read", "wall_seconds", "outcome", "run_id", "session_id", "by",
             "note", "effort", "at"}


def clean_env(**extra):
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    env.update(extra)
    return env


def run_ticket(*args):
    return subprocess.run(
        [sys.executable, "-B", os.path.join(ROOT, "scripts", "ticket.py"),
         "agent-command", *args],
        capture_output=True, text=True, timeout=30, env=clean_env())


def candidate(model, tool, effort="high"):
    return {"model": model, "tool": tool, "effort": effort,
            "effort_escalated": None, "dispatch_cap": 1}


class CommandAndRoutingContracts(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t131-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def role_models(self, *args, table=None):
        command = [sys.executable, "-B", os.path.join(ROOT, "scripts", "role-models.py")]
        if table:
            command += ["--table", table]
        return subprocess.run(command + list(args), capture_output=True, text=True,
                              timeout=30, env=clean_env())

    def evidence(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def routing_table(self):
        with open(os.path.join(ROOT, "board", "role-models.json"), encoding="utf-8") as handle:
            table = copy.deepcopy(json.load(handle))
        table["roles"]["worker"] = [candidate(SOL, "codex"),
                                     candidate("opus", "claude-code")]
        path = os.path.join(self.tmp, "role-models.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(table, handle, ensure_ascii=False)
        return path

    def test_a1_agy_command_and_prefix_tool_symmetry_are_literal(self):
        """A1 agy 命令逐字符合 E1/E4，四種 model/tool 不一致都 rc=2 且點名兩格。"""
        done = run_ticket("--model", AGY_MODEL, "--tool", "agy", "--effort", "high")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, AGY_COMMAND)
        for model, tool in ((AGY_MODEL, "codex"), (AGY_MODEL, "claude-code"),
                            (SOL, "agy"), ("opus", "agy")):
            with self.subTest(model=model, tool=tool):
                done = run_ticket("--model", model, "--tool", tool)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertIn("model=%s" % model, done.stderr)
                self.assertIn("tool=%s" % tool, done.stderr)

    def test_a2_agy_effort_table_suffix_conflicts_and_omission(self):
        """A2 low/medium/high/xhigh/max 可用；模型後綴衝突與 ultra 拒絕；未給 effort 不輸出旗標。"""
        for effort in ("low", "medium", "high", "xhigh", "max"):
            model = "agy:gemini-3.8-flash-%s" % effort \
                if effort in ("low", "medium", "high") else "agy:gemini-3.8-flash"
            with self.subTest(effort=effort):
                done = run_ticket("--model", model, "--tool", "agy", "--effort", effort)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("--effort %s" % effort, done.stdout)
        for model, effort in ((AGY_MODEL, "max"),
                              ("agy:gemini-3.8-flash-low", "high")):
            done = run_ticket("--model", model, "--tool", "agy", "--effort", effort)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("衝突", done.stderr)
        done = run_ticket("--model", "agy:gemini-3.8-flash", "--tool", "agy",
                          "--effort", "ultra")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("low medium high xhigh max", done.stderr)
        done = run_ticket("--model", AGY_MODEL, "--tool", "agy")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("--effort", done.stdout)

    def test_a6_agy_direct_and_stream_error_envelopes_are_classified(self):
        """A6 未知模型/effort 衝突/429 direct/RESOURCE_EXHAUSTED stream 分到三個指定種類。"""
        rows = (
            ('{"status":"ERROR","error":"not recognized as a known model"}\n',
             "provider_unavailable"),
            ('{"status":"ERROR","error":"invalid model selection gemini-3.8-flash-high '
             'conflicts with --effort=max"}\n', "auth_config"),
            ('{"status":"ERROR","error":"429 rate limit"}\n', "quota_exhausted"),
            ('{"event":"result","result":{"status":"ERROR",'
             '"error":"RESOURCE_EXHAUSTED: 429 Too Many Requests"}}\n', "quota_exhausted"),
        )
        for index, (body, want) in enumerate(rows):
            done = self.role_models("classify", "--evidence",
                                    self.evidence("error-%d.log" % index, body), "--rc", "1")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(json.loads(done.stdout).get("kind"), want)
        done = self.role_models("classify", "--evidence",
                                self.evidence("plain.log", "429 rate limit\n"), "--rc", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout).get("kind"), "other")

    def test_a9_claude_and_codex_commands_remain_byte_for_byte_stable(self):
        """A9 Claude 與 Codex 既有 agent-command golden 不因 agy 分支改變。"""
        claude = run_ticket("--model", "opus", "--tool", "claude-code", "--base",
                             CLAUDE_COMMAND.rstrip("\n"))
        self.assertEqual((claude.returncode, claude.stdout), (0, CLAUDE_COMMAND),
                         claude.stdout + claude.stderr)
        codex = run_ticket("--model", SOL, "--tool", "codex")
        self.assertEqual((codex.returncode, codex.stdout), (0, CODEX_COMMAND),
                         codex.stdout + codex.stderr)

    def test_a10_explicit_ticket_model_precedes_and_deduplicates_the_worker_chain(self):
        """A10 agy→sol→opus；明寫 opus 時 opus→sol；沒寫票 model 時從 sol 開始。"""
        table = self.routing_table()

        def resolve(*more):
            done = self.role_models("resolve", "--role", "worker", *more, table=table)
            self.assertIn(done.returncode, (0, 3), done.stdout + done.stderr)
            return done.returncode, json.loads(done.stdout)

        agy = []
        for index in (None, 0, 1):
            args = ["--ticket-model", AGY_MODEL, "--ticket-tool", "agy",
                    "--ticket-effort", "high"]
            if index is not None:
                args += ["--exhausted-index", str(index)]
            _, row = resolve(*args)
            agy.append((row.get("candidate_index"), row.get("model"), row.get("tool"),
                        row.get("effort")))
        self.assertEqual(agy, [(0, AGY_MODEL, "agy", "high"),
                               (1, SOL, "codex", "high"),
                               (2, "opus", "claude-code", "high")])
        _, first = resolve("--ticket-model", "opus", "--ticket-tool", "claude-code")
        _, second = resolve("--ticket-model", "opus", "--ticket-tool", "claude-code",
                            "--exhausted-index", "0")
        self.assertEqual((first.get("model"), second.get("model")), ("opus", SOL))
        _, plain = resolve()
        self.assertEqual((plain.get("candidate_index"), plain.get("model"), plain.get("tool"),
                          plain.get("effort")), (0, SOL, "codex", "high"))


FAKE_AGY = r'''#!/bin/sh
set -e
mkdir -p "$AGY_OBS"
name="$AC_ROLE-$AC_ROUND"
printf '%s\n' "$@" > "$AGY_OBS/$name.argv"
pwd -P > "$AGY_OBS/$name.cwd"
wc -c | tr -d ' ' > "$AGY_OBS/$name.stdin"
printf '%s %s\n' "$AC_ROLE" "$AC_ROUND" >> "$AGY_OBS/calls"
case "${AGY_MODE:-deliver}" in
    quota)
        echo '{"event":"result","result":{"status":"ERROR","error":"RESOURCE_EXHAUSTED: 429 Too Many Requests"}}'
        exit 1 ;;
    stray)
        echo stray > "$AGY_REPO/agy-stray.txt" ;;
esac
W=$PWD
cd "$AC_WORK"
if [ "$AC_ROLE" = verifier ]; then
    mkdir -p work/verify/dispatch
    cat > work/verify/dispatch/test_ticket_131.py <<'CASE'
"""#131 verifier fixture.

## 驗收表
A8 | sandbox | fake verifier | case exists | ticket A8

## 介面字串
none

## 怎麼做假
no service

## 不做
no product edit
"""
import unittest

TAGS = ["example"]


class T(unittest.TestCase):
    def test_case_exists(self):
        """A8 verifier hands in a case."""
        self.assertTrue(True)
CASE
    diff -ruN base work > patch-verify.diff || true
    cat > EVIDENCE-verifier.md <<'EV'
# verifier

## result

```result
{"ticket":"1","role":"verifier","round":1,"rc":0,"patch_sha256":null,
 "gate":null,"mutations":[],"objection":null,"excluded":[],"repro":null,"memory":[],
 "verify":{"files":["verify/dispatch/test_ticket_131.py"],"tags":[],
           "run":"python3 -m unittest verify/dispatch/test_ticket_131.py","notes":"sandbox"},
 "baseline":{"stage":"red","ok":true,"why":"","files":["verify/dispatch/test_ticket_131.py"]}}
```
EV
else
    mkdir -p "$W"/tests
    if [ "${AGY_REPAIR:-}" = 1 ] && [ "$AC_ROUND" = 1 ]; then want=2; else want=1; fi
    cat > "$W"/tests/test_thing.py <<CASE
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, $want)
CASE
    (cd "$W" && git add -A && git commit -q -m "t$AC_TICKET 第 $AC_ROUND 輪") || true
    printf '# 第 %s 輪\n已排除的假設:沒有\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
fi
echo '{"event":"result","result":{"conversation_id":"agy-conv","status":"SUCCESS","duration_seconds":1,"usage":{"input_tokens":31,"output_tokens":7,"thinking_tokens":3,"cache_read_tokens":2,"total_tokens":38}}}'
'''

FAKE_FALLBACK = r'''#!/bin/sh
echo "fallback $AC_ROLE $AC_ROUND" >> "$AC_TEST_LOG"
cat >/dev/null
W=$PWD
cd "$AC_WORK"
mkdir -p "$W"/tests
cat > "$W"/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
(cd "$W" && git add -A && git commit -q -m "t$AC_TICKET 第 $AC_ROUND 輪") || true
printf '# fallback\n已排除的假設:沒有\n' > "EVIDENCE-round$AC_ROUND.md"
'''


class AgyFlowBase(AutoFixBase):

    def setUp(self):
        super().setUp()
        self.obs = os.path.join(self.home, "agy-observations")
        write_executable(os.path.join(self.stub_bin, "agy"), FAKE_AGY)
        self.write(os.path.join("memory", "model", "agy:gemini-3.8-flash-high.md"),
                   "- sandbox agy model card\n")

    def agy_ticket(self, **fields):
        fields.setdefault("model", AGY_MODEL)
        fields.setdefault("tool", "agy")
        fields.setdefault("effort", "high")
        row = self.make_ticket("1", **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        return row

    def run_agy(self, *args, **extra):
        return self.run_sh("scripts/auto-fix.sh", "1", *args,
                           env=self.env(AGY_OBS=self.obs, **extra))

    def argv(self, role="worker", rnd=1):
        path = os.path.join(self.obs, "%s-%s.argv" % (role, rnd))
        if not os.path.exists(path):
            return []
        return self.read("%s-%s.argv" % (role, rnd), where=self.obs).splitlines()

    def worker_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "worker"]

    def configure_fallback(self):
        fallback = os.path.join(self.home, "fallback.sh")
        write_executable(fallback, FAKE_FALLBACK)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % fallback, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(worker=[fixture_candidate("opus", effort="high")])


class AgyInvocation(AgyFlowBase):

    def test_a3_agy_reads_no_stdin_and_gets_one_prompt_for_the_absolute_dispatch_file(self):
        """A3 worker 的 cwd、stdin、prompt 與禁止旗標都由假 agy 的外部觀測檔證明。"""
        self.agy_ticket()
        done = self.run_agy("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        argv = self.argv()
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        prompts = [arg for arg in argv if arg.startswith("-p=")]
        self.assertEqual(len(prompts), 1, argv)
        dispatch = prompts[0].split("Read ", 1)[1].rsplit(" and follow it.", 1)[0]
        self.assertTrue(os.path.isabs(dispatch), dispatch)
        self.assertTrue(dispatch.endswith("dispatch-round1.md"), dispatch)
        self.assertNotIn("--add-dir", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertEqual(self.read("worker-1.stdin", where=self.obs).strip(), "0")
        self.assertTrue(self.read("worker-1.cwd", where=self.obs).strip().endswith("/repo-wt/t1"),
                        "worker 的 cwd 是票分支的 worktree(#144)")

    def test_a7_agy_result_maps_to_one_complete_cost_row_without_double_counting_thinking(self):
        """A7 成本列 15 鍵，output=7 不另加 thinking=3，cache_write null 並註明原因。"""
        self.agy_ticket()
        done = self.run_agy("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.worker_cost()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(set(row), COST_KEYS)
        self.assertEqual((row.get("tokens_in"), row.get("tokens_out"),
                          row.get("cache_write"), row.get("cache_read")), (31, 7, None, 2))
        self.assertEqual((row.get("session_id"), row.get("model"), row.get("effort")),
                         ("agy-conv", AGY_MODEL, "high"))
        self.assertIn("cache_write", row.get("note") or "")


class AgyFallback(AgyFlowBase):

    def setUp(self):
        super().setUp()
        self.configure_fallback()

    def test_a5_missing_agy_skips_that_candidate_before_agent_start_and_cost(self):
        """A5 PATH 沒有 agy 時只起下一個 opus，事件與成本都沒有假造 agy 一輪。"""
        os.remove(os.path.join(self.stub_bin, "agy"))
        self.agy_ticket()
        path = os.pathsep.join(where for where in os.environ.get("PATH", "").split(os.pathsep)
                               if where and not os.path.exists(os.path.join(where, "agy")))
        done = self.run_agy("--no-review", PATH=path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("agy 不在 PATH", done.stderr)
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual([row.get("model") for row in starts], ["opus"])
        self.assertEqual([row.get("model") for row in self.worker_cost()], ["opus"])

    def test_a6_quota_envelope_switches_to_the_first_role_table_candidate(self):
        """A6 agy 的 RESOURCE_EXHAUSTED 429 使同一次流程由 agy 換到 worker 鏈首 opus。"""
        self.agy_ticket()
        done = self.run_agy("--no-review", AGY_MODE="quota")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual([(row.get("model"), row.get("effort")) for row in starts],
                         [(AGY_MODEL, "high"), ("opus", "high")])
        self.assertEqual([(row.get("model"), row.get("outcome")) for row in self.worker_cost()],
                         [(AGY_MODEL, "error"), ("opus", "handed-in")])
        self.assertIn("quota_exhausted", self.worker_cost()[0].get("note") or "")


class AgyGuardAndRoles(AgyFlowBase):

    def test_a4_main_tree_write_is_blocked_before_the_agy_patch_is_applied(self):
        """A4 agy 在主 repo 寫檔時 rc=8、票 Blocked、不收件(#144:分支派工前就開了,看閘門沒跑)且失敗原因是 guard-violation。"""
        self.agy_ticket()
        done = self.run_agy("--no-review", AGY_MODE="stray", AGY_REPO=self.repo)
        self.assertEqual(done.returncode, 8, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket.get("state"), ticket.get("owner")), ("Blocked", "main"))
        self.assertNotIn("gate.rerun", self.kinds(), "越界時不收件、不跑閘門")
        failed = [row.get("reason") for row in self.events()
                  if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual(failed, ["guard-violation"])

    def test_a8_repair_round_keeps_agy_model_effort_and_marks_the_session_nonresumable(self):
        """A8 第一輪真紅後第二輪仍用同一 agy model/high，session 明記不可接回。"""
        self.agy_ticket(retry_limit=2)
        done = self.run_agy("--no-review", AGY_REPAIR="1")
        self.assertIn(done.returncode, (0, 7), done.stdout + done.stderr)
        calls_path = os.path.join(self.obs, "calls")
        calls = self.read("calls", where=self.obs).splitlines() if os.path.exists(calls_path) else []
        self.assertIn("worker 1", calls)
        self.assertIn("worker 2", calls)
        for rnd in (1, 2):
            argv = self.argv(rnd=rnd)
            self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")
            self.assertEqual(argv[argv.index("--effort") + 1], "high")
        sessions_path = os.path.join(self.repo, "reports", "t1", "sessions.json")
        self.assertTrue(os.path.exists(sessions_path), sessions_path)
        with open(sessions_path, encoding="utf-8") as handle:
            session = json.load(handle)["worker"]
        self.assertFalse(session.get("resumable"))
        self.assertIn("不能接回", session.get("why") or "")

    def test_a8_agy_verifier_uses_the_same_prompt_cwd_devnull_and_cost_shape(self):
        """A8 verifier 候選為 agy 時有同形 prompt/cwd/stdin，且成本記實際 model/effort/tokens。"""
        self.set_role_models(verifier=[fixture_candidate(AGY_MODEL, "agy", "high")])
        self.write("tests/test_verify_runner.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_there(self):\n        self.assertTrue(True)\n")
        self.agy_ticket(needs_verifier=True, interface_fixed=False)
        done = self.run_agy("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        argv = self.argv("verifier")
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")
        self.assertEqual(len([arg for arg in argv if arg.startswith("-p=")]), 1)
        self.assertEqual(self.read("verifier-1.stdin", where=self.obs).strip(), "0")
        self.assertTrue(self.read("verifier-1.cwd", where=self.obs).strip().endswith("/work"))
        rows = [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "verifier"]
        self.assertEqual([(row.get("model"), row.get("effort"), row.get("tokens_out"))
                          for row in rows], [(AGY_MODEL, "high", 7)])


if __name__ == "__main__":
    unittest.main()
