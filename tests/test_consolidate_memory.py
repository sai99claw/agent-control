"""`scripts/consolidate-memory.sh`:整理票自動起角色表的整理者,一個 session(#122,D-043 / D-049 ②;
原 #87 A39–A41 的兩模型)。

這一組問五件事:
1. **只起一個 fable**:角色表 consolidator 鏈選人;票上 `model` 寫 opus、config 還留著兩模型的
   `memory.consolidators`,替身照樣只被叫起恰好 1 次、`--model fable`(#131 A10 不套 consolidator)。
   連續跑兩次、同時跑兩次 → 同一張票只取得一個租約、只起一個。
2. **前者不可用才換下一個**:provider 錯誤信封說額度用完 → 換鏈上下一格;一般退出非零不換。
   fable 用不了 = consolidator 鏈耗盡 → 停、送 decision,不改派別的模型。
3. **起不來不留在 Running**:命令缺、退出非零、逾時、耗盡 → NeedsDecision、owner=main、租約清空、
   一頁 decision,標題帶票號與原因。
4. **成本證據**:每次起都寫一列 cost(role=consolidator、model=實際起的那個、outcome)。
5. **單一整理者的討論檔由啟動端記**:attempt_history 一筆 {model, discussion, at};
   `memory.py consolidate` 只收那一份(檔頭 model 一致)當唯一一份討論,別的一份照舊拒收。

session 一律是替身:把 argv 與 stdin 各存一份、照 `AC_DISCUSSION` 寫一份討論檔頭,再依
@MODE@ 退出 0 / 退出 3 / 印額度信封退 1 / 印模型不存在信封退 1 / 睡過逾時。期望值寫死在這裡。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import (ROOT, SCRIPTS, TIMEOUT, Sandbox, fixture_candidate,  # noqa: E402
                             write_executable)
from test_memory_lifecycle import Consolidation  # noqa: E402

SESSION = """#!/bin/sh
echo "session $*" >> "@CALLS@"
cat >> "@STDIN@"
echo "=== end of stdin ===" >> "@STDIN@"
mkdir -p "$(dirname "$AC_DISCUSSION")"
printf -- '---\\nmodel: %s\\n---\\n' "$AC_MODEL" > "$AC_DISCUSSION"
case "@MODE@" in
    fail) exit 3 ;;
    quota) echo '{"type":"error","message":"You have hit your usage limit"}'; exit 1 ;;
    gone) echo '{"type":"error","error":{"type":"not_found_error","message":"model: fable"}}'; exit 1 ;;
    hang) exec sleep 30 ;;
esac
exit 0
"""


class ConsolidateBase(Sandbox):

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(SCRIPTS, "consolidate-memory.sh"),
                    os.path.join(self.repo, "scripts", "consolidate-memory.sh"))
        shutil.copy(os.path.join(ROOT, "templates", "dispatch-consolidator.md"),
                    os.path.join(self.repo, "templates", "dispatch-consolidator.md"))
        self.install_rules_sources()
        self.calls = os.path.join(self.home, "session-calls.log")
        self.stdin = os.path.join(self.home, "session-stdin.log")
        self.write("memory/model/opus.md", "---\nowner: opus\n---\n" + "舊" * 2100)

    def body(self, mode):
        return SESSION.replace("@CALLS@", self.calls).replace("@STDIN@", self.stdin) \
            .replace("@MODE@", mode)

    def arm(self, mode="ok", codex_mode="ok", command=None, timeout=60, legacy=True):
        claude = os.path.join(self.home, "fake-session.sh")
        write_executable(claude, self.body(mode))
        write_executable(os.path.join(self.stub_bin, "codex"), self.body(codex_mode))
        conf = json.loads(self.read("board/config.json"))
        if legacy:
            conf["memory"] = dict(conf["memory"], consolidators=["fixture-a", "codex:gpt-6-astra"])
        conf["worker"] = {"command": command or "sh %s --model opus" % claude,
                          "timeout_seconds": 600}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket(7, role="consolidator", state="Ready", model="opus",
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

    def costs(self):
        return [(row["role"], row["model"], row["outcome"], row["by"])
                for row in self.load_ticket("7").get("cost") or []]


class OneLeaseOneFable(ConsolidateBase):

    def test_a1_one_fable_session_even_when_the_ticket_and_config_say_otherwise(self):
        """四問:守 A1 整理只起一個 fable、A3 舊 consolidators 不選路 / 可信回歸:M1 起兩個
        → calls=2 紅;A10 套到 consolidator → `--model opus` 紅 / 字面 fable、1 次 / 新案例
        (取代原 test_a39_two_sessions_each_with_its_own_model 的兩模型 oracle)。"""
        self.arm()
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.session_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("--model fable", calls[0])
        self.assertNotIn("exec -m", calls[0], "codex 不該被起")
        for kind in ("agent.start", "agent.done"):
            self.assertEqual([row["model"] for row in self.agent_rows(kind)], ["fable"], kind)
        with open(self.stdin, encoding="utf-8") as handle:
            fed = handle.read()
        self.assertEqual(fed.count("# 派工文範本"), 1, "一個 session 一份範本")
        self.assertIn("**整理票**:#`7`", fed)
        self.assertIn("`memory/model/opus.md`", fed)
        template = fed.split("# 派工文範本", 1)[1]
        self.assertNotIn("<票號>", template, "範本的佔位沒填")
        self.assertEqual(template.count("--discussion "), 1, "合併那一句只帶一份討論檔")
        warned = [line for line in done.stderr.splitlines() if "memory.consolidators" in line]
        self.assertEqual(len(warned), 1, "舊設定與表衝突卻不說:" + done.stderr)
        self.assertIn("衝突", warned[0])
        self.assertIsNone(self.load_ticket("7").get("lease"), "回來之後租約沒清")

    def test_the_dispatch_gives_the_criterion_and_no_length_target(self):
        """#134 A7:填好的派工文(規則包前言 + 範本)帶判準一句,沒有字數目標。
        **變異**:範本拿掉判準句,或 fill 留著 `<N>` / `<cap>` → 這一條紅。"""
        self.arm()
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.stdin, encoding="utf-8") as handle:
            fed = handle.read()
        self.assertIn("只留值得這個成本的", fed.split("# 派工文範本", 1)[1])
        for word in ("提高上限", "超過上限", "cap_history", "<cap>", "<N>"):
            self.assertNotIn(word, fed)

    def test_a1_running_twice_in_a_row_starts_one(self):
        self.arm()
        first, second = self.launch(), self.launch()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("不起", second.stdout)
        self.assertEqual(len(self.session_calls()), 1)
        self.assertEqual(len(self.leases_taken()), 1)

    def test_a1_running_twice_at_once_starts_one(self):
        """**變異 M16**:不取租約(直接轉 Running 起 session)→ 替身被叫 2 次,紅。"""
        self.arm()
        procs = [subprocess.Popen(["sh", os.path.join(self.repo, "scripts",
                                                      "consolidate-memory.sh"), "7"],
                                  cwd=self.repo, env=self.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [proc.communicate(timeout=TIMEOUT) for proc in procs]
        self.assertEqual(len(self.session_calls()), 1, outputs)
        self.assertEqual(len(self.leases_taken()), 1, outputs)


class OnlyUnavailableMovesOn(ConsolidateBase):
    """A1「前者不可用才換下一個,Fable 整理不可用即耗盡停」。"""

    def test_a1_a_quota_envelope_moves_to_the_next_candidate(self):
        """四問:守額度信封才換下一格 / 可信回歸:不判信封就停在 codex → calls=1 紅 /
        字面 codex 一次、fable 一次 / 新案例。"""
        self.set_role_models(consolidator=[fixture_candidate("codex:gpt-6-astra", tool="codex",
                                                             cap=None),
                                           fixture_candidate("fable", cap=None)])
        self.arm(codex_mode="quota")
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.session_calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("exec -m gpt-6-astra", calls[0])
        self.assertIn("--model fable", calls[1])
        self.assertEqual([row["model"] for row in self.agent_rows("agent.failed")],
                         ["codex:gpt-6-astra"])
        self.assertEqual([row["model"] for row in self.agent_rows("agent.done")], ["fable"])
        self.assertEqual(self.costs(),
                         [("consolidator", "codex:gpt-6-astra", "error", "consolidate-memory.sh"),
                          ("consolidator", "fable", "handed-in", "consolidate-memory.sh")])

    def test_a1_an_ordinary_failure_does_not_move_on(self):
        """四問:守一般失敗不換 / 可信回歸:失敗一律換 → fable 也被起、calls=2 紅 / 字面 1 次 / 新案例。"""
        self.set_role_models(consolidator=[fixture_candidate("codex:gpt-6-astra", tool="codex",
                                                             cap=None),
                                           fixture_candidate("fable", cap=None)])
        self.arm(codex_mode="fail")
        done = self.launch()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.session_calls()), 1)
        self.assertIn("退出 3", done.stderr)

    def test_a1_fable_unavailable_is_the_end_of_the_chain(self):
        """四問:守 Fable 不可用 → 耗盡停、不改派 / 可信回歸:耗盡後退回 config 的模型或任意 opus
        → calls>1 紅 / 字面 1 次、「整串耗盡」 / 新案例。"""
        self.arm(mode="gone")
        done = self.launch()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(self.session_calls()), 1)
        row = self.load_ticket("7")
        self.assertEqual((row["state"], row.get("owner"), row.get("lease")),
                         ("NeedsDecision", "main", None))
        self.assertIn("整串耗盡", done.stderr)
        self.assertEqual(self.costs(),
                         [("consolidator", "fable", "error", "consolidate-memory.sh")])


class FailuresGoToMain(ConsolidateBase):
    """起不來 → NeedsDecision、owner=main、租約清空、一頁 decision。"""

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
        done = self.launch()
        self.assert_asked(done, "命令缺")
        self.assertIn("整串耗盡", done.stderr)
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
        self.assertEqual(self.costs(), [("consolidator", "fable", "timeout",
                                         "consolidate-memory.sh")])


class TheLauncherRecordsWhoTalked(ConsolidateBase):

    def test_a41_attempt_history_records_the_one_model_and_its_discussion(self):
        self.arm(legacy=False)
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("衝突", done.stderr, "舊設定與表一致(memory.consolidator=fable)卻警告")
        rows = self.load_ticket("7")["attempt_history"]
        self.assertEqual([row["model"] for row in rows], ["fable"])
        self.assertEqual(set(rows[0]), {"model", "discussion", "at"})
        self.assertTrue(os.path.isfile(os.path.join(self.repo, rows[0]["discussion"])), rows)
        self.assertIn("fable", rows[0]["discussion"])
        self.assertEqual(self.costs(), [("consolidator", "fable", "handed-in",
                                         "consolidate-memory.sh")])


IN_SESSION = """#!/bin/sh
echo "session $*" >> "@CALLS@"
cat > /dev/null
snap=$(python3 scripts/memory.py snapshot memory/model/opus.md)
mkdir -p "$(dirname "$AC_DISCUSSION")" candidates
printf -- '---\\nmodel: %s\\n%s\\n---\\n\\n## 第 1 輪 — %s\\n- 主張:逐條處置\\n\\n## 結論\\n- L1: 保留\\n' \\
    "$AC_MODEL" "$snap" "$AC_MODEL" > "$AC_DISCUSSION"
printf -- '- 整理後的原則 (#10, 2026-10-01, fable@fable)\\n' > candidates/opus.md
python3 scripts/memory.py consolidate memory/model/opus.md --candidate candidates/opus.md \\
    --discussion "$AC_DISCUSSION" --by "$AC_MODEL" > "@OUT@" 2>&1
echo "rc=$?" >> "@OUT@"
exit 0
"""


class TheSessionConsolidatesWithItsOneDiscussion(ConsolidateBase):
    """範本的時序:整理 session **在 session 裡**跑 `memory.py consolidate --discussion <一份>`;
    那時啟動端的紀錄必須已經在票的 attempt_history,單一 fable 才合併得了(#122 round 2)。"""

    def body(self, mode):
        self.out = os.path.join(self.home, "in-session-consolidate.log")
        return IN_SESSION.replace("@CALLS@", self.calls).replace("@OUT@", self.out)

    def test_a1_the_in_session_consolidate_is_accepted_with_one_discussion(self):
        """四問:守啟動端起 session 之前就記下討論檔 / 可信回歸:session 退出後才記 →
        in-session consolidate rc=2「要兩份」紅 / 字面 rc=0、consolidated 1 筆、主檔換成候選 /
        新案例(既有 OneRecordedDiscussionIsEnough 是 launcher 收完之後才跑,守不到時序)。"""
        self.write("memory/model/opus.inbox.md",
                   "- 待整理的一條 (#11, 2026-09-01, worker@opus)\n")
        self.arm(legacy=False)
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.session_calls()), 1)
        with open(self.out, encoding="utf-8") as handle:
            said = handle.read()
        self.assertIn("rc=0", said, said)
        consolidated = [row for row in self.events() if row["kind"] == "memory.consolidated"]
        self.assertEqual(len(consolidated), 1, said)
        self.assertIn("整理後的原則", self.read("memory/model/opus.md"))
        rows = self.load_ticket("7")["attempt_history"]
        self.assertEqual([row["model"] for row in rows], ["fable"], rows)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, rows[0]["discussion"])), rows)


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


class OneRecordedDiscussionIsEnough(Consolidation):
    """#122 A1「沒有雙模型互讀門檻」:啟動端記下的那一份 = 唯一一份討論;別的一份照舊拒收。"""

    def setUp(self):
        super().setUp()
        self.seed(2)
        self.sha = self.sha_of_first(2)
        self.make_ticket(7, role="consolidator", state="Running",
                         allowed_write_paths=[self.MAIN, self.INBOX],
                         attempt_history=[{"model": "fable", "discussion": "d/fable.md",
                                           "at": "x"}])

    def test_a1_the_recorded_discussion_alone_consolidates(self):
        """四問:守單一整理者收得進去 / 可信回歸:仍要兩份 → rc 2 紅 / 字面 consolidated 1 筆 /
        新案例。"""
        done = self.memory("consolidate", self.MAIN, "--candidate", self.CANDIDATE,
                           "--discussion", self.talk("d/fable.md", "fable", 2, self.sha))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(self.consolidated()), 1)

    def test_a1_one_unrecorded_or_mismatched_discussion_is_still_refused(self):
        """四問:守單一的那份只認啟動端紀錄 / 可信回歸:任何一份都收 → rc 0 紅 / 字面 rc≠0、
        主檔不變 / 新案例。"""
        main = self.raw(self.MAIN)
        for name, model in (("d/other.md", "fable"), ("d/fable.md", "opus")):
            with self.subTest(name=name, model=model):
                done = self.memory("consolidate", self.MAIN, "--candidate", self.CANDIDATE,
                                   "--discussion", self.talk(name, model, 2, self.sha))
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("兩份", done.stderr)
                self.assertEqual(self.raw(self.MAIN), main)
                self.assertEqual(self.consolidated(), [])


if __name__ == "__main__":
    unittest.main()
