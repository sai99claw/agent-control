"""`scripts/consolidate-memory.sh`:整理票自動起兩個模型的 session(#87 A39–A41,原 #84)。

這一組問四件事:
1. **只起一組**:連續跑兩次、同時跑兩次 → 同一張票只取得一個租約、替身被叫起恰好 2 次。
2. **兩個 session 各是自己的模型**:`codex:*` 走 `codex exec`、其餘把 config 的
   `worker.command` 換 `--model`(與 auto-fix 同一支 `ticket.py agent-command`);派工文是
   範本逐格填好、從 stdin 進;agent.start / agent.done 的 model 欄記實際起的那個。
3. **起不來不留在 Running**:命令缺、退出非零、逾時 → NeedsDecision、owner=main、租約清空、
   一頁 decision,標題帶票號與原因。
4. **討論的模型身份由啟動端記**:attempt_history 每筆 {model, discussion, at};
   `memory.py consolidate` 讀到的檔頭 `model:` 對不上 → rc=1、主檔不動。

session 一律是替身:把 argv 與 stdin 各存一份、照 `AC_DISCUSSION` 寫一份討論檔頭,再依
@MODE@ 退出 0 / 退出 3 / 睡過逾時。期望值(模型名、次數、字樣)寫死在這裡。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox, write_executable  # noqa: E402
from test_memory_lifecycle import Consolidation  # noqa: E402

SESSION = """#!/bin/sh
echo "session $*" >> "@CALLS@"
cat >> "@STDIN@"
echo "=== end of stdin ===" >> "@STDIN@"
mkdir -p "$(dirname "$AC_DISCUSSION")"
printf -- '---\\nmodel: %s\\n---\\n' "$AC_MODEL" > "$AC_DISCUSSION"
case "@MODE@" in
    fail) exit 3 ;;
    hang) exec sleep 30 ;;
esac
exit 0
"""


class ConsolidateBase(Sandbox):

    MODELS = ["fixture-a", "codex:gpt-6-astra"]

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(SCRIPTS, "consolidate-memory.sh"),
                    os.path.join(self.repo, "scripts", "consolidate-memory.sh"))
        shutil.copy(os.path.join(ROOT, "templates", "dispatch-consolidator.md"),
                    os.path.join(self.repo, "templates", "dispatch-consolidator.md"))
        self.install_rules_sources()
        for name in ("fixture-a", "fixture-b"):
            self.write(os.path.join("memory", "model", "%s.md" % name),
                       "- 沙盒替身模型:照派工文做。\n")
        self.calls = os.path.join(self.home, "session-calls.log")
        self.stdin = os.path.join(self.home, "session-stdin.log")
        self.write("memory/model/opus.md", "---\ncap_chars: 2000\n---\n" + "舊" * 2100)

    def arm(self, mode="ok", models=None, command=None, timeout=60):
        body = SESSION.replace("@CALLS@", self.calls).replace("@STDIN@", self.stdin) \
            .replace("@MODE@", mode)
        claude = os.path.join(self.home, "fake-session.sh")
        write_executable(claude, body)
        write_executable(os.path.join(self.stub_bin, "codex"), body)
        conf = json.loads(self.read("board/config.json"))
        conf["memory"] = dict(conf["memory"], consolidators=models or self.MODELS)
        conf["worker"] = {"command": command or "sh %s --model opus" % claude,
                          "timeout_seconds": 600}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket(7, role="consolidator", state="Ready", model=", ".join(models or
                                                                               self.MODELS),
                         in_scope=["memory/model/opus.md", "memory/model/opus.inbox.md"],
                         allowed_write_paths=["memory/model/opus.md",
                                              "memory/model/opus.inbox.md"],
                         worker={"timeout_seconds": timeout})

    def launch(self):
        return self.run_sh("scripts/consolidate-memory.sh", "7")

    def session_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]

    def agent_rows(self, kind):
        return [row for row in self.events() if row["kind"] == kind]

    def leases_taken(self):
        return [row for row in self.events() if row["kind"] == "ticket.state"
                and row.get("field") == "lease" and row.get("to") not in (None, "null")]


class OneLeaseOnePair(ConsolidateBase):

    def test_a39_two_sessions_each_with_its_own_model(self):
        self.arm()
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.session_calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("session exec -m gpt-6-astra -s workspace-write --skip-git-repo-check -",
                      calls)
        self.assertTrue([c for c in calls if "--model fixture-a" in c], calls)
        for kind in ("agent.start", "agent.done"):
            self.assertEqual(sorted(row["model"] for row in self.agent_rows(kind)),
                             sorted(self.MODELS), kind)
        with open(self.stdin, encoding="utf-8") as handle:
            fed = handle.read()
        self.assertIn("**整理票**:#`7`", fed)
        self.assertIn("`memory/model/opus.md`", fed)
        for chunk in fed.split("=== end of stdin ===")[:-1]:
            self.assertNotIn("<票號>", chunk.split("# 派工文範本", 1)[1], "範本的佔位沒填")
        self.assertEqual(fed.count("# 派工文範本"), 2, "兩個 session 各一份範本")
        self.assertIsNone(self.load_ticket("7").get("lease"), "回來之後租約沒清")

    def test_a39_running_twice_in_a_row_starts_one_pair(self):
        self.arm()
        first, second = self.launch(), self.launch()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("不起", second.stdout)
        self.assertEqual(len(self.session_calls()), 2)
        self.assertEqual(len(self.leases_taken()), 1)

    def test_a39_running_twice_at_once_starts_one_pair(self):
        """**變異 M16**:不取租約(直接轉 Running 起 session)→ 替身被叫 4 次,紅。"""
        self.arm()
        procs = [subprocess.Popen(["sh", os.path.join(self.repo, "scripts",
                                                      "consolidate-memory.sh"), "7"],
                                  cwd=self.repo, env=self.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [proc.communicate(timeout=TIMEOUT) for proc in procs]
        self.assertEqual(len(self.session_calls()), 2, outputs)
        self.assertEqual(len(self.leases_taken()), 1, outputs)


class FailuresGoToMain(ConsolidateBase):
    """A40:三種起不來 → NeedsDecision、owner=main、租約清空、一頁 decision。"""

    MODELS = ["fixture-a", "fixture-b"]

    def assert_asked(self, done, words):
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        row = self.load_ticket("7")
        self.assertEqual((row["state"], row.get("owner"), row.get("lease")),
                         ("NeedsDecision", "main", None))
        index = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        with open(index, encoding="utf-8") as handle:
            pages = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([page["kind"] for page in pages], ["decision"])
        title = self.read(pages[0]["page"]).splitlines()[0]
        self.assertIn("#7", title)
        self.assertIn(words, title)

    def test_a40_a_missing_command(self):
        self.arm(command="no-such-agent-cli -p --model opus")
        self.assert_asked(self.launch(), "命令缺")
        self.assertEqual(self.session_calls(), [])

    def test_a40_a_session_that_exits_non_zero(self):
        """**變異 M17**:失敗態不改 state(留 Running)→ 這一條紅。"""
        self.arm(mode="fail")
        self.assert_asked(self.launch(), "退出 3")

    def test_a40_a_session_that_times_out(self):
        self.arm(mode="hang", timeout=2)
        started = time.time()
        done = self.launch()
        self.assertLess(time.time() - started, 25, "沒在逾時附近收掉")
        self.assert_asked(done, "逾時")


class TheLauncherRecordsWhoTalked(ConsolidateBase):

    def test_a41_attempt_history_records_each_model_and_its_discussion(self):
        self.arm()
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.load_ticket("7")["attempt_history"]
        self.assertEqual(sorted(row["model"] for row in rows), sorted(self.MODELS))
        for row in rows:
            self.assertEqual(set(row), {"model", "discussion", "at"})
            self.assertTrue(os.path.isfile(os.path.join(self.repo, row["discussion"])), row)
            self.assertIn(row["model"].split(":")[-1], row["discussion"])


class ConsolidateChecksTheLauncherRecord(Consolidation):
    """A41 後半:`memory.py consolidate --discussion` 的檔頭 model 與啟動端記的不一致 → rc=1。"""

    def setUp(self):
        super().setUp()
        self.seed(2)
        sha = self.sha_of_first(2)
        self.args = ["--candidate", self.CANDIDATE,
                     "--discussion", self.talk("d/a.md", "fixture-a", 2, sha)]
        self.sha = sha
        self.make_ticket(7, role="consolidator", state="Running",
                         allowed_write_paths=[self.MAIN, self.INBOX],
                         attempt_history=[
                             {"model": "fixture-a", "discussion": "d/a.md", "at": "x"},
                             {"model": "codex:gpt-6-astra", "discussion": "d/b.md", "at": "x"}])

    def test_a41_a_header_model_that_disagrees_is_refused(self):
        """**變異**:拿掉 `launcher_mismatch` 的呼叫 → rc 0、主檔被換,紅。"""
        main = self.raw(self.MAIN)
        done = self.memory("consolidate", self.MAIN, *self.args,
                           "--discussion", self.talk("d/b.md", "fable", 2, self.sha))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = [l for l in done.stderr.splitlines() if "d/b.md" in l]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn("fable", line[0])
        self.assertIn("codex:gpt-6-astra", line[0])
        self.assertEqual(self.raw(self.MAIN), main)
        self.assertEqual(self.consolidated(), [])

    def test_a41_the_short_model_name_matches(self):
        done = self.memory("consolidate", self.MAIN, *self.args,
                           "--discussion", self.talk("d/b.md", "gpt-6-astra", 2, self.sha))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.consolidated()), 1)


if __name__ == "__main__":
    unittest.main()
