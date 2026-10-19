"""#131 auto-fix 的 agy 路徑:argv/cwd/stdin、fallback、成本、守衛、修補輪與驗證者。

## 驗收表(期望值取自票面,不從被測程式回算)
A3/A7 | sandbox | 假 agy 交件 | cwd=work、stdin=0、prompt 指派工文、成本照信封 | 票面 A3/A7
A4/A6 | sandbox | 越界或 429 | 守衛擋 / 換角色鏈下一格 | 票面 A4/A6
A5/A8 | sandbox | CLI 不在 / 修補輪 / 驗證者 | 不起 agy / 沿用 agy / 同形起法 | 票面 A5/A8

## 介面字串
`-p=`、`--dangerously-skip-permissions`、`--output-format stream-json`、agy result 信封。

## 怎麼做假
只在 tempfile 沙盒 PATH 放假 agy,不呼叫真 CLI、LLM 或服務埠。

## 不做
不改角色表產品預設;不放寬既有 Claude/Codex 斷言。
"""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402

AGY_MODEL = "agy:gemini-3.8-flash-high"

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
cd "$AC_WORK"
if [ "$AC_ROLE" = verifier ]; then
    mkdir -p work/verify/dispatch
    cat > work/verify/dispatch/test_ticket_131.py <<'CASE'
"""#131 A8 agy 驗證者起法。

## 驗收表(期望值來源獨立於被測程式)
A8 | sandbox | 假 agy 驗證者交件 | 案例存在 | 票面 A8

## 介面字串
(沒有;argv 由單元層量。)

## 怎麼做假
不上服務、不連埠。

## 不做
不改產品碼。
"""
import unittest

TAGS = ["example"]


class T(unittest.TestCase):
    def test_worker_file_exists(self):
        """A8 驗證者案例由 agy 交出。"""
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
    mkdir -p work/tests
    if [ "${AGY_REPAIR:-}" = 1 ] && [ "$AC_ROUND" = 1 ]; then
        want=2
    else
        want=1
    fi
    cat > work/tests/test_thing.py <<CASE
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, $want)
CASE
    diff -ruN base work > "patch-round$AC_ROUND.diff" || true
    printf '# 第 %s 輪\n已排除的假設:沒有\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
fi
echo '{"event":"result","result":{"conversation_id":"agy-conv","status":"SUCCESS","duration_seconds":1,"usage":{"input_tokens":31,"output_tokens":7,"thinking_tokens":3,"cache_read_tokens":2,"total_tokens":38}}}'
'''

FAKE_FALLBACK = r'''#!/bin/sh
echo "fallback $AC_ROLE $AC_ROUND" >> "$AC_TEST_LOG"
cat >/dev/null
cd "$AC_WORK"
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# fallback\n已排除的假設:沒有\n' > "EVIDENCE-round$AC_ROUND.md"
'''


class AgyBase(AutoFixBase):

    def setUp(self):
        super().setUp()
        self.obs = os.path.join(self.home, "agy-observations")
        write_executable(os.path.join(self.stub_bin, "agy"), FAKE_AGY)
        self.write(os.path.join("memory", "model", "agy:gemini-3.8-flash-high.md"),
                   "- sandbox agy model card\n")

    def env_for_agy(self, **extra):
        return self.env(AGY_OBS=self.obs, **extra)

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
        return self.run_sh("scripts/auto-fix.sh", "1", *args, env=self.env_for_agy(**extra))

    def argv(self, role="worker", rnd=1):
        with open(os.path.join(self.obs, "%s-%s.argv" % (role, rnd)), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def worker_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or [] if row["role"] == "worker"]


class AgyInvocationAndCost(AgyBase):

    def test_a3_a7_worker_uses_prompt_file_devnull_and_agy_usage(self):
        """四問:量 argv/cwd/stdin/cost/session;值=A3/A7;舊值 stdin 非零;不靠空集合。"""
        self.agy_ticket()
        done = self.run_agy("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        argv = self.argv()
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")
        self.assertIn("--effort", argv)
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        prompts = [arg for arg in argv if arg.startswith("-p=")]
        self.assertEqual(len(prompts), 1, argv)
        dispatch = prompts[0].split("Read ", 1)[1].rsplit(" and follow it.", 1)[0]
        self.assertTrue(os.path.isabs(dispatch), dispatch)
        self.assertIn("dispatch-round1.md", dispatch)
        self.assertNotIn("--add-dir", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertEqual(self.read("worker-1.stdin", where=self.obs).strip(), "0")
        self.assertTrue(self.read("worker-1.cwd", where=self.obs).strip().endswith("/work"))
        row = self.worker_cost()[0]
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["cache_write"],
                          row["cache_read"]), (31, 7, None, 2))
        self.assertEqual(row["session_id"], "agy-conv")
        self.assertEqual(row["model"], AGY_MODEL)
        self.assertEqual(row["effort"], "high")
        self.assertIn("cache_write", row["note"])
        sessions = glob.glob(os.path.join(self.repo, "reports", "t1", "sessions.json"))
        self.assertEqual(len(sessions), 1)
        with open(sessions[0], encoding="utf-8") as handle:
            session = json.load(handle)["worker"]
        self.assertFalse(session["resumable"])
        self.assertIn("不能接回", session["why"])


class AgyFallbacks(AgyBase):

    def setUp(self):
        super().setUp()
        fallback = os.path.join(self.home, "fallback.sh")
        write_executable(fallback, FAKE_FALLBACK)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % fallback, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(worker=[fixture_candidate("opus", effort="high")])

    def test_a6_a_quota_envelope_takes_the_first_table_candidate(self):
        """四問:量 agy→opus 起動/成本;值=A6/A10;舊值不認 agy 信封;表真的有下一格。"""
        self.agy_ticket()
        done = self.run_agy("--no-review", AGY_MODE="quota")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual([(row["model"], row.get("effort")) for row in starts],
                         [(AGY_MODEL, "high"), ("opus", "high")])
        self.assertEqual([(row["model"], row["outcome"]) for row in self.worker_cost()],
                         [(AGY_MODEL, "error"), ("opus", "handed-in")])
        self.assertIn("quota_exhausted", self.worker_cost()[0]["note"])

    def test_a5_missing_agy_is_skipped_before_agent_start_or_cost(self):
        """四問:量 PATH 缺 CLI 時只起 fallback;值=A5/#121;舊值會記 agy rc127;有真下一格。"""
        os.remove(os.path.join(self.stub_bin, "agy"))
        self.agy_ticket()
        path = os.pathsep.join(where for where in os.environ.get("PATH", "").split(os.pathsep)
                               if where and not os.path.exists(os.path.join(where, "agy")))
        done = self.run_agy("--no-review", PATH=path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("agy 不在 PATH", done.stderr)
        starts = [row for row in self.events() if row["kind"] == "agent.start"]
        self.assertEqual([row["model"] for row in starts], ["opus"])
        self.assertEqual([row["model"] for row in self.worker_cost()], ["opus"])


class AgyGuardAndRoles(AgyBase):

    def test_a4_an_agy_write_in_the_main_tree_is_blocked_before_patch_application(self):
        """四問:量 rc/Blocked/無分支;值=A4;舊值套 patch;守衛觀察主 repo 真工作樹。"""
        self.agy_ticket()
        done = self.run_agy("--no-review", AGY_MODE="stray", AGY_REPO=self.repo)
        self.assertEqual(done.returncode, 8, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["state"], ticket.get("owner")), ("Blocked", "main"))
        self.assertEqual(self.git("branch", "--list", "t1").strip(), "")
        failed = [row.get("reason") for row in self.events()
                  if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual(failed, ["guard-violation"])

    def test_a8_red_round_two_keeps_the_explicit_agy_model_and_effort(self):
        """四問:量 round1/2 argv與事件;值=A8;舊值把表外 agy 拒掉;第一輪真紅。"""
        self.agy_ticket(retry_limit=2)
        done = self.run_agy("--no-review", AGY_REPAIR="1")
        self.assertIn(done.returncode, (0, 7), done.stdout + done.stderr)
        calls = self.read("calls", where=self.obs).splitlines()
        self.assertIn("worker 1", calls)
        self.assertIn("worker 2", calls)
        for rnd in (1, 2):
            argv = self.argv(rnd=rnd)
            self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")
            self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_a8_an_agy_verifier_uses_the_same_prompt_cwd_and_devnull_shape(self):
        """四問:量 verifier argv/cwd/stdin/cost;值=A8;舊值起 claude;真的交 patch-verify。"""
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
                if row["role"] == "verifier"]
        self.assertEqual([(row["model"], row["effort"], row["tokens_out"]) for row in rows],
                         [(AGY_MODEL, "high", 7)])


if __name__ == "__main__":
    unittest.main()
