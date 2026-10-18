"""`scripts/auto-fix.sh`:worker 的反駁停下發 decision 給主線,開題者只補分析;非阻擋的
`note` 不停線(#104 A1 / A2,D-049 ③;取代 #89 / D-041 的自動接回)。

`OBJECTION: ticket-wrong|blocking`(與降為阻擋的 note)一律票轉 Blocked、一頁 decision;停之前
起一個開題者寫分析,它的 TRIAGE 判決只記進那一筆的 `triage`,**disposition 留空、不接回**。
同票第二次反駁、開題者沒判出來時不分析,直接停。`OBJECTION: note` 已交 patch、不要求改驗收 /
保護範圍 / 產品取捨 → 一筆 note、不起開題者、照常收件走閘門。

worker / 驗證者 / 開題者三個角色都是 PATH 上同一支假 `claude`(`FAKE_CLAUDE`):它把每一次
被叫的 argv、`os.getcwd()`、stdin 與跑的當下看到的事實(票、副本、sessions.json)記進一份
JSONL,再照 `scenario.json` 演那一個角色。**期望值寫死在這裡**(驗收全文、TRIAGE 行、字樣),
不從被測腳本算回去。
"""

import json
import os
import shlex
import sys
import time
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402

OPENER_COMMAND = ("claude -p --model opus --permission-mode acceptEdits --allowedTools Read "
                  "Glob Grep Bash Write --output-format stream-json --verbose")
OBJECTION = "OBJECTION: ticket-wrong 驗收第二條與設計文件對不上"
OBJECTION_AGAIN = "OBJECTION: ticket-wrong 驗收第三條也與設計文件對不上"
NOTE = "OBJECTION: note 照原驗收做完,順手把案例名改得更清楚"
NOTE_ASK = "OBJECTION: note 兩欄或三欄是產品取捨,我先做了兩欄"
ACCEPTANCE = ["A1 第一條照舊", "A2 驗收第二條照票面:輸出兩欄"]
ACCEPT_REASON = "驗收第二條改照設計文件"
REJECT_REASON = "驗收第二條與設計文件一致,反駁不成立"
ESCALATE_REASON = "兩份設計文件互相矛盾,要使用者選一份"

# 假 `claude`:先記一筆(被砍的那一種也留得下),再照劇本演。標記檔不進 patch(`-x`)。
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time

CALLS, SCENARIO, REPO, WTBASE = "@CALLS@", "@SCENARIO@", "@REPO@", "@WTBASE@"
argv = sys.argv[1:]
stdin = sys.stdin.read()
role = os.environ.get("AC_ROLE", "")
resumed = "--resume" in argv
r = os.environ.get("AC_ROUND", "")
work = os.environ.get("AC_WORK", "")
with open(SCENARIO, encoding="utf-8") as handle:
    plan = json.load(handle)


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def handin(name):
    with open(os.path.join(work, name), "w", encoding="utf-8") as out:
        subprocess.run(["diff", "-ruN", "-x", "worker-mark", "-x", "verifier-mark",
                        "base", "work"], cwd=work, stdout=out)


rec = {"role": role, "argv": argv, "cwd": os.getcwd(), "stdin": stdin, "resumed": resumed,
       "round": r, "ac_root": os.environ.get("AC_ROOT"), "pid": os.getpid(),
       "ticket": load(os.path.join(REPO, "tickets", "1.json")),
       "sessions": load(os.path.join(REPO, "reports", "t1", "sessions.json"))}
if role == "worker":
    with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
        handle.write("worker ran round %s\n" % r)
    mark = os.path.join(work, "work", "worker-mark")
    if resumed:
        rec["mark_there"] = os.path.exists(mark)
        mode = plan.get("worker_resume", "fix")
    else:
        write(mark, "first segment\n")
        mode = plan.get("worker_r" + r, plan.get("worker_first", "fix"))
elif role == "verifier":
    mark = os.path.join(work, "work", "verifier-mark")
    if resumed:
        rec["mark_there"] = os.path.exists(mark)
        mode = plan.get("verifier_resume", "handin")
    else:
        write(mark, "first segment\n")
        mode = "handin"
else:
    rec["worker_copy"] = [os.path.isdir(os.path.join(WTBASE, "fix-t1", "round" + r, sub))
                          for sub in ("work", "base")]
    rec["verifier_copy"] = os.path.isdir(os.path.join(WTBASE, "verify-t1", "round" + r, "work"))
    mode = plan.get("opener", "accept")
with open(CALLS, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(rec, ensure_ascii=False) + "\n")

THING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_thing(self):\n" \
        "        self.assertEqual(1, %s)\n"
if role == "worker":
    evidence = os.path.join(work, "EVIDENCE-round%s.md" % r)
    if mode in ("object", "object-again"):
        write(evidence, (plan["objection_again"] if mode == "object-again"
                         else plan["objection"]) + "\n")
    elif mode == "note-nopatch":
        write(evidence, plan["note"] + "\n")
    elif mode == "sleep":
        time.sleep(60)
    else:
        write(os.path.join(work, "work", "tests", "test_thing.py"),
              THING % ("2" if mode == "red" else "1"))
        handin("patch-round%s.diff" % r)
        write(evidence, "# 第 %s 輪(%s)\n已排除的假設:沒有\n" % (r, mode))
        if mode in ("fix-note", "fix-note-ask"):
            with open(evidence, "a", encoding="utf-8") as handle:
                handle.write(plan["note" if mode == "fix-note" else "note_ask"] + "\n")
elif role == "verifier":
    if mode == "handin":
        write(os.path.join(work, "work", "verify", "example", "test_ticket_1.py"), @CASE@)
        handin("patch-verify.diff")
        write(os.path.join(work, "EVIDENCE-verifier.md"), @VEVIDENCE@)
else:
    triage = os.path.join(os.getcwd(), "reports", "t1", "triage-round%s.md" % r)
    if mode == "accept":
        write(triage, "TRIAGE: accepted %s\n" % plan["accept_reason"])
    elif mode == "reject":
        write(triage, "TRIAGE: rejected %s\n" % plan["reject_reason"])
    elif mode == "escalate":
        write(triage, "TRIAGE: escalate %s\n" % plan["escalate_reason"])
    elif mode == "exit3":
        sys.exit(3)
    elif mode == "sleep":
        time.sleep(60)
    elif mode == "garbage":
        write(triage, "我覺得反駁成立\nTRIAGE: accepted 第二行才寫\n")
print(json.dumps({"type": "result", "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''

VERIFIER_CASE = '''"""#1 第 1 輪驗證者的案例:worker 交的 tests/test_thing.py 要在候選樹上。

## 驗收表(期望值來源獨立於被測程式)
A2 | sandbox | 看 tests/test_thing.py 在不在 | 在 | 票面 A2:乾淨基底上沒有這個檔

## 介面字串
(沒有;這一條不斷言任何字串)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬任何票面驗收。
"""
import os
import unittest

TAGS = ["example"]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class T(unittest.TestCase):
    def test_worker_case_is_there(self):
        """A2 worker 交的案例檔在候選樹上。"""
        self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", "test_thing.py")))
'''

VERIFIER_EVIDENCE = """# verifier
案例一個,基底紅。

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0, "patch_sha256": null,
 "gate": null, "mutations": [], "objection": null, "excluded": [], "repro": null,
 "memory": [],
 "verify": {"files": ["verify/example/test_ticket_1.py"], "tags": ["example"],
            "run": "python3 scripts/verify.py --tag example", "notes": "沙盒"},
 "baseline": {"stage": "red", "ok": true, "why": "",
              "files": ["verify/example/test_ticket_1.py"]}}
```
"""


class TriageBase(AutoFixBase):

    def arm(self, worker_first="object", worker_cmd="claude -p --model opus",
            verifier_cmd="claude -p --model opus", opener=None, **fields):
        """`opener`:None = 沙盒 config 有本票寫死的那一塊;False = 沒有 opener 區塊;
        dict = 換掉那一塊。其餘劇本鍵(worker_resume / opener 模式…)用 `play()` 補。"""
        self.calls_path = os.path.join(self.home, "claude-calls.jsonl")
        self.scenario = {"worker_first": worker_first, "objection": OBJECTION,
                         "objection_again": OBJECTION_AGAIN, "note": NOTE, "note_ask": NOTE_ASK,
                         "accept_reason": ACCEPT_REASON, "reject_reason": REJECT_REASON,
                         "escalate_reason": ESCALATE_REASON}
        self.save_scenario()
        self.bindir = os.path.join(self.home, "bin")
        os.makedirs(self.bindir, exist_ok=True)
        write_executable(os.path.join(self.bindir, "claude"), FAKE_CLAUDE
                         .replace("@CALLS@", self.calls_path)
                         .replace("@SCENARIO@", self.scenario_path())
                         .replace("@REPO@", self.repo)
                         .replace("@WTBASE@", os.path.join(self.home, "repo-wt"))
                         .replace("@CASE@", repr(VERIFIER_CASE))
                         .replace("@VEVIDENCE@", repr(VERIFIER_EVIDENCE)))
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": worker_cmd, "timeout_seconds": 120}
        conf["verifier"] = {"command": verifier_cmd}
        if opener is None:
            conf["opener"] = {"command": OPENER_COMMAND, "timeout_seconds": 60}
        elif opener:
            conf["opener"] = opener
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        # `gate.sh --branch` 把 `verify/*` 的改動對到 `test_verify_runner`(同 test_auto_fix)。
        self.write(os.path.join("tests", "test_verify_runner.py"),
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_there(self):\n        self.assertTrue(True)\n")
        fields.setdefault("allowed_write_paths", ["tests/*", "verify/*"])
        fields.setdefault("acceptance", list(ACCEPTANCE))
        row = self.make_ticket("1", **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        return row

    def scenario_path(self):
        return os.path.join(self.home, "scenario.json")

    def save_scenario(self):
        with open(self.scenario_path(), "w", encoding="utf-8") as handle:
            json.dump(self.scenario, handle, ensure_ascii=False)

    def play(self, **plan):
        self.scenario.update(plan)
        self.save_scenario()

    def auto_fix(self, *args):
        return self.run_sh("scripts/auto-fix.sh", "1", *args, env=self.env(
            PATH=os.pathsep.join([self.bindir, self.stub_bin, os.environ["PATH"]])))

    def calls(self, role=None):
        if not os.path.exists(self.calls_path):
            return []
        with open(self.calls_path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return [row for row in rows if role is None or row["role"] == role]

    def sessions(self):
        return json.loads(self.read(os.path.join("reports", "t1", "sessions.json")))

    def sessions_text(self):
        return self.read(os.path.join("reports", "t1", "sessions.json"))

    def copy(self, kind="fix", round_no=1):
        return os.path.join(self.home, "repo-wt", "%s-t1" % kind, "round%d" % round_no)

    def same_dir(self, rel, actual):
        self.assertEqual(os.path.realpath(os.path.join(self.repo, rel)),
                         os.path.realpath(actual))

    def flag_value(self, argv, flag):
        self.assertIn(flag, argv)
        return argv[argv.index(flag) + 1]

    def triage_events(self):
        return [row for row in self.events() if row["kind"].startswith("objection.triage.")]

    def objection(self, index=0):
        return self.load_ticket("1")["objections"][index]

    def assert_stopped(self, done, words, verifier=False):
        """停下的共同斷言:rc=3、Blocked、owner=main、decision.asked、恰一頁 decision、
        頁上有字樣 + worker session + 副本相對路徑;disposition 留空;副本與 session 都留著。"""
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["state"], ticket["owner"]), ("Blocked", "main"))
        self.assertIn("decision.asked", self.kinds())
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertEqual(pages[0].get("kind"), "decision")
        page = json.dumps(pages[0], ensure_ascii=False)
        for word in words:
            self.assertIn(word, page)
        rows = self.sessions()
        if rows["worker"]["session_id"]:
            self.assertIn(rows["worker"]["session_id"], page)
        self.assertIn("../repo-wt/fix-t1/round1/work", page)
        self.assertEqual(ticket["objections"][-1]["disposition"], "")
        for sub in ("work", "base"):
            self.assertTrue(os.path.isdir(os.path.join(self.copy(), sub)), sub)
        if verifier:
            self.assertTrue(os.path.isdir(os.path.join(self.copy("verify"), "work")))
        self.assertEqual({role: row["state"] for role, row in rows.items()},
                         {role: "open" for role in rows})
        return page

    def assert_escalated(self, done, words, started, verifier=False):
        """A5 每一種:`assert_stopped` + 開題者沒有判決(那一筆沒有 triage)+ triage 事件。"""
        page = self.assert_stopped(done, words, verifier=verifier)
        self.assertNotIn("triage", self.load_ticket("1")["objections"][-1])
        kinds = [row["kind"] for row in self.triage_events()]
        self.assertEqual(kinds[-1], "objection.triage.done")
        last = self.triage_events()[-1]
        self.assertEqual((last.get("verdict"), last.get("reason")), ("escalate", words[0]))
        if started:
            self.assertEqual(kinds[-2], "objection.triage.start")
        else:
            self.assertNotIn("objection.triage.start", kinds[-2:])
        return page


class SessionsAreNamedUpFront(TriageBase):
    """A1 + A6(b):session 先指定、記到交件;一般綠路徑兩份副本收掉、兩列轉 closed。"""

    def test_a1_claude_agents_get_a_session_id_and_a_row(self):
        """四問:守 worker / 驗證者起跑時 argv 補 --session-id、sessions.json 逐格、cwd 是 work/
        相對主 repo 根 / 可信回歸:假 claude 記下自己的 argv 與 os.getcwd(),期望值是 uuid 解析與
        realpath 相等,不從腳本算 / 既有測試沒有 session 這個概念 / 不加縫:只換 PATH 上的 claude。"""
        self.arm(worker_first="fix", needs_verifier=True, interface_fixed=True)
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for role in ("worker", "verifier"):
            with self.subTest(role):
                calls = self.calls(role)
                self.assertEqual(len(calls), 1, calls)
                sid = self.flag_value(calls[0]["argv"], "--session-id")
                uuid.UUID(sid)
                row = calls[0]["sessions"][role]
                self.assertEqual((row["session_id"], row["round"], row["model"], row["state"],
                                  row["resumable"]), (sid, 1, "opus", "open", True))
                self.same_dir(row["cwd"], calls[0]["cwd"])
                self.assertTrue(row["cwd"].startswith("../"), row["cwd"])
                final = self.sessions()[role]
                self.assertEqual((final["session_id"], final["state"]), (sid, "closed"))
                self.assertTrue(final.get("closed_at"))
        text = self.sessions_text()
        self.assertNotIn(self.home, text)
        self.assertNotIn(os.path.realpath(self.home), text)
        for kind in ("fix", "verify"):
            for sub in ("work", "base"):
                self.assertFalse(os.path.isdir(os.path.join(self.copy(kind), sub)), (kind, sub))

    def test_a1_a_command_that_cannot_resume_gets_no_flag_and_says_why(self):
        """四問:守非 claude 起頭 / --no-session-persistence → 不補旗標、resumable=false、why /
        可信回歸:假 claude 的 argv 裡沒有 --session-id / 既有測試沒有 sessions.json /
        不加縫:`env X=1 claude` 讓命令頭不是 claude 而跑的仍是同一支假 claude。"""
        self.arm(worker_first="fix", needs_verifier=True, interface_fixed=True,
                 worker_cmd="claude -p --model opus --no-session-persistence",
                 verifier_cmd="env AC_FAKE=1 claude -p --model opus")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for role in ("worker", "verifier"):
            with self.subTest(role):
                calls = self.calls(role)
                self.assertEqual(len(calls), 1, calls)
                self.assertNotIn("--session-id", calls[0]["argv"])
                row = calls[0]["sessions"][role]
                self.assertIs(row["resumable"], False)
                self.assertIsNone(row["session_id"])
                self.assertIn("不能接回", row["why"])
        self.assertIn("--no-session-persistence", self.sessions()["worker"]["why"])


class TheOpenerOnlyAnalyses(TriageBase):
    """#104 A2:開題者判 accepted / rejected 都只是分析 —— 票停下、一頁 decision、那一筆記
    `triage`、disposition 留空、worker 與驗證者都不接回、副本與 session 留著。"""

    def assert_analysed(self, done, verdict, reason):
        page = self.assert_stopped(done, ["開題者已分析,待主線裁示", reason])
        self.assertIn("開題者判 %s" % verdict, page)
        row = self.objection()
        self.assertEqual(row["disposition"], "", "沒人裁示,auto-fix 不自寫 %s" % verdict)
        self.assertEqual(row["triage"], {"verdict": verdict, "reason": reason, "round": 1,
                                         "by": "auto-fix"})
        self.assertEqual(self.load_ticket("1")["acceptance"], ACCEPTANCE, "票面一格不動")
        self.assertEqual([call["role"] for call in self.calls()].count("worker"), 1,
                         "不接回 worker")
        self.assertEqual([call for call in self.calls() if "--resume" in call["argv"]], [])
        return page

    def test_a2_an_accepted_verdict_still_stops_for_the_main_line(self):
        """四問:守開題者 accepted → 不接回、disposition 空、triage 記判決、Blocked + decision;
        開題者的起法與派工文(不改票)/ 可信回歸:TRIAGE 行是替身寫死的,接回與否看替身自己記的
        argv / 既有 accepted 案例斷言的是接回(D-041,已被 A2 取代)/ 不加縫:同一支假 claude。
        **變異 M3**:auto-fix 照 TRIAGE 行自寫 `row["disposition"] = verdict` → 這一條紅。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(opener="accept")
        done = self.auto_fix("--no-review")
        self.assert_analysed(done, "accepted", ACCEPT_REASON)
        opener, = self.calls("opener")
        self.assertEqual(opener["argv"], shlex.split(OPENER_COMMAND)[1:])
        self.same_dir(".", opener["cwd"])
        self.same_dir(".", opener["ac_root"])
        packet = opener["stdin"]
        self.assertTrue(packet.startswith("# 規則包:opener"), packet[:80])
        for text in ("## 判反駁", OBJECTION, "../repo-wt/fix-t1/round1/EVIDENCE-round1.md",
                     "**不改票**", "disposition 由主線寫",
                     "把判決寫進 reports/t1/triage-round1.md,第一行 TRIAGE: "
                     "<accepted|rejected|escalate> <一句理由>"):
            self.assertIn(text, packet)
        self.assertEqual(opener["ticket"]["state"], "Running")
        self.assertEqual(opener["worker_copy"], [True, True])
        self.assertTrue(opener["verifier_copy"])
        self.assertEqual(len(self.calls("verifier")), 1, "驗證者不接回")
        kinds = [(e["kind"], e.get("role")) for e in self.events()
                 if e["kind"].startswith("objection.triage.")
                 or (e["kind"].startswith("agent.") and e.get("role") == "opener")]
        self.assertEqual(kinds, [("objection.triage.start", None), ("agent.start", "opener"),
                                 ("agent.done", "opener"), ("objection.triage.done", None)])
        self.assertTrue(os.path.isdir(os.path.join(self.copy("verify"), "work")))

    def test_a2_a_rejected_verdict_still_stops_for_the_main_line(self):
        """四問:守開題者 rejected → 同樣停下、不接回 / 可信回歸:同上 / 既有 rejected 案例斷言
        的是接回 / 不加縫:同一支假 claude。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(opener="reject")
        done = self.auto_fix("--no-review")
        self.assert_analysed(done, "rejected", REJECT_REASON)


class NotesDoNotStopTheLine(TriageBase):
    """#104 A1 / A2 / T1:合格的 note → 單筆 note、不起開題者、照常收件走閘門;note 沒交 patch /
    要產品取捨 → 照阻擋收、停下發 decision。"""

    def test_t1_a_note_with_a_green_patch_goes_on_to_the_gate(self):
        """四問:守 note+patch → 一筆 note(blocking=false)、沒有開題者、閘門綠進 InReview、
        副本收掉 / 可信回歸:開題者呼叫數看替身自己記的 JSONL / 既有測試沒有 note / 不加縫。"""
        self.arm(worker_first="fix-note")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.went_in_review())
        self.assertEqual(self.calls("opener"), [])
        rows = self.load_ticket("1")["objections"]
        self.assertEqual([(row["category"], row["blocking"], row["disposition"]) for row in rows],
                         [("note", False, "")])
        self.assertEqual(self.triage_events(), [])
        self.assertNotIn("decision.asked", self.kinds())
        for sub in ("work", "base"):
            self.assertFalse(os.path.isdir(os.path.join(self.copy(), sub)), sub)

    def assert_demoted(self, mode, why):
        self.arm(worker_first=mode)
        self.play(opener="reject")
        done = self.auto_fix("--no-review")
        self.assert_stopped(done, ["開題者已分析,待主線裁示"])
        row, = self.load_ticket("1")["objections"]
        self.assertEqual((row["category"], row["blocking"], row["disposition"]),
                         ("blocking", True, ""))
        self.assertIn(why, row["demoted"])

    def test_t1_a_note_without_a_patch_stops(self):
        """四問:守 note 無 patch → blocking + demoted、rc=3、Blocked、decision / 可信回歸:替身
        只交 EVIDENCE / 既有沒有 note / 不加縫。"""
        self.assert_demoted("note-nopatch", "沒交 patch")

    def test_t1_a_note_asking_for_a_product_call_stops_even_with_a_green_patch(self):
        """四問:守有綠 patch 的 note 要產品取捨 → 照阻擋收、停下 / 可信回歸:替身交 fix 的
        patch / 既有沒有 note / 不加縫。**變異 M1**:`note_blocker` 不看 NOTE_ASKS(有 patch 一律
        降級)→ 這一條紅(rc=0)。"""
        self.assert_demoted("fix-note-ask", "取捨")


class OnlyTheseGoToTheMainLine(TriageBase):
    """A5:四種升級。每一種 rc=3 / Blocked / 一頁 / disposition 留空 / 副本與 session 留著。"""

    def test_a5a_the_opener_says_escalate(self):
        """四問:守 (a) 開題者判 escalate / 可信回歸:TRIAGE 行是替身寫死的 / 既有四條沒有開題者 /
        不加縫:同一支假 claude。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(opener="escalate")
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["開題者判需要裁示", ESCALATE_REASON], started=True,
                              verifier=True)
        self.assertEqual(len(self.calls("worker")), 1, "升級了不該接回 worker")

    def test_a5b_a_second_objection_on_the_same_ticket(self):
        """四問:守 (b) 票上已有一筆開題者分析過(帶 triage)的反駁、worker 再交 OBJECTION →
        不再起開題者 / 可信回歸:開題者呼叫 0 次、第二筆 disposition 留空、第一筆(主線裁過的)
        不動 / 既有版本靠接回造出第二次,#104 A2 不再接回,改由票上預先有那一筆 / 不加縫。"""
        first = {"category": "ticket-wrong", "body": "驗收第二條與設計文件對不上",
                 "evidence": "", "owner": "main", "disposition": "rejected", "follow_up": "",
                 "triage": {"verdict": "rejected", "reason": REJECT_REASON, "round": 1,
                            "by": "auto-fix"}}
        self.arm(worker_first="object-again", objections=[first])
        done = self.auto_fix("--no-review")
        page = self.assert_escalated(done, ["同票第二次反駁"], started=False)
        self.assertEqual(self.calls("opener"), [])
        rows = self.load_ticket("1")["objections"]
        self.assertEqual([row["disposition"] for row in rows], ["rejected", ""])
        self.assertIn("第三條", page + json.dumps(rows, ensure_ascii=False))

    def test_a5c_no_opener_block_in_config(self):
        """四問:守 (c) config 沒有 opener 區塊 → 開題者命令缺 / 可信回歸:沒有任何 opener 呼叫 /
        既有四條只斷言 rc=3 不看字樣 / 不加縫:config 少一塊。"""
        self.arm(opener=False)
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["開題者命令缺"], started=True)
        self.assertEqual(self.calls("opener"), [])

    def test_a5c_an_opener_command_that_is_not_there(self):
        """四問:守 (c) opener.command 找不到 / 可信回歸:沒有任何 opener 呼叫 /
        既有測試沒有這一格 / 不加縫:config 指一個不存在的命令。"""
        self.arm(opener={"command": "no-such-opener-xyz -p --model opus", "timeout_seconds": 60})
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["開題者命令缺", "no-such-opener-xyz"], started=True)

    def test_a5c_the_opener_exits_non_zero(self):
        """四問:守 (c) 退出非零 / 可信回歸:替身 exit 3,字樣含 3 / 既有測試沒有開題者 /
        不加縫:同一支假 claude。"""
        self.arm()
        self.play(opener="exit3")
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["開題者退出 3"], started=True)

    def test_a5c_the_opener_runs_past_its_timeout(self):
        """四問:守 (c) 逾時 + 收掉替身 / 可信回歸:替身睡 60 秒、時限 2 秒,量牆鐘與 pid /
        既有逾時測試只量 worker / 不加縫:config 的 opener.timeout_seconds。"""
        self.arm(opener={"command": OPENER_COMMAND, "timeout_seconds": 2})
        self.play(opener="sleep")
        began = time.time()
        done = self.auto_fix("--no-review")
        self.assertLess(time.time() - began, 50, "開題者沒有在時限被砍")
        self.assert_escalated(done, ["開題者逾時"], started=True)
        pid = self.calls("opener")[0]["pid"]
        with self.assertRaises(OSError):
            os.kill(pid, 0)

    def test_a5c_the_opener_hands_in_no_verdict(self):
        """四問:守 (c) 第一行不是 TRIAGE: 起頭(第二行才寫的不算)/ 可信回歸:替身寫死那兩行 /
        既有測試沒有 triage 檔 / 不加縫:同一支假 claude。"""
        self.arm()
        self.play(opener="garbage")
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["開題者沒交判決"], started=True)

    def test_a5d_a_worker_session_that_cannot_resume_still_gets_the_analysis(self):
        """四問:守 worker 列 resumable=false 不再擋開題者分析(#104 A2 不接回,接不接得回不是
        停下的理由)、頁上照樣寫 worker session(沒有)/ 可信回歸:開題者呼叫一次、頁的字樣寫死 /
        既有版本斷言「不能接回」升級 / 不加縫:命令帶 --no-session-persistence。"""
        self.arm(worker_cmd="claude -p --model opus --no-session-persistence")
        self.play(opener="reject")
        done = self.auto_fix("--no-review")
        page = self.assert_stopped(done, ["開題者已分析,待主線裁示", "worker session (沒有)"])
        self.assertEqual(len(self.calls("opener")), 1)
        self.assertNotIn("不能接回", page)


class ClosingAndTimeouts(TriageBase):
    """A6:收掉。(接回之後的逾時兩條隨 #104 A2 拿掉:不再有接回。)"""

    def test_a6a_a_closed_session_is_never_resumed_and_the_next_round_gets_a_new_one(self):
        """四問:守收件後 sessions 轉 closed、下一輪新 --session-id、closed 的 id 不再出現 /
        可信回歸:第 1 輪 worker 交一份紅的 patch,auto-fix 自己進第 2 輪,第 2 輪的替身記下
        argv 與它看到的 sessions.json / 既有版本由接回的 worker 交紅(#104 A2 不再接回)/
        不加縫:同一支假 claude。"""
        self.arm(worker_first="red", needs_verifier=True, interface_fixed=True)
        self.play(worker_r2="fix")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        round_one = {call["role"]: self.flag_value(call["argv"], "--session-id")
                     for call in self.calls()[:2]}
        second = [call for call in self.calls("worker") if call["round"] == "2"]
        self.assertEqual(len(second), 1, self.calls("worker"))
        seen = second[0]["sessions"]
        self.assertEqual(seen["verifier"]["state"], "closed")
        self.assertTrue(seen["verifier"].get("closed_at"))
        fresh = self.flag_value(second[0]["argv"], "--session-id")
        self.assertNotIn(fresh, round_one.values())
        self.assertNotIn("--resume", second[0]["argv"])
        for sub in ("work", "base"):
            self.assertFalse(os.path.isdir(os.path.join(self.copy(), sub)), sub)
            self.assertFalse(os.path.isdir(os.path.join(self.copy("verify"), sub)), sub)
        self.assertTrue(all(row["state"] == "closed" for row in self.sessions().values()))
        closed = {row["session_id"] for row in self.sessions().values()} | set(round_one.values())
        before = len(self.calls())
        again = self.auto_fix("--no-review")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        for call in self.calls()[before:]:
            self.assertEqual(set(call["argv"]) & closed, set(), call["argv"])


if __name__ == "__main__":
    unittest.main()


class TheOpenerCostsARow(TriageBase):
    """#94 C5(c):判反駁起的開題者每次起跑後寫一列 role=opener,outcome 是 TRIAGE 判決;
    沒寫出 TRIAGE 行 → undecided。"""

    def opener_rows(self):
        return [row for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "opener"]

    def test_c5c_an_accepted_verdict_is_a_row(self):
        """四問:守 accepted 那一列 / 可信回歸:TRIAGE 行是替身寫死的 / 既有測試這條路一列都
        沒有 / 不加縫:同一支假 claude。**變異 m7**:判反駁那條路不寫 cost → 這一條紅。
        #104 A2:accepted 之後停下(rc=3),worker 只有反駁那一列。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(opener="accept")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        rows = self.opener_rows()
        self.assertEqual([(row["outcome"], row["round"], row["by"]) for row in rows],
                         [("accepted", 1, "auto-fix.sh")], rows)
        self.assertTrue(rows[0]["model"])
        self.assertTrue(rows[0]["run_id"])
        workers = [row["outcome"] for row in self.load_ticket("1")["cost"]
                   if row["role"] == "worker"]
        self.assertEqual(workers, ["objection"])

    def test_c5c_an_opener_without_a_triage_line_is_undecided(self):
        """四問:守 undecided / 可信回歸:替身第一行不是 TRIAGE: / 既有 a5c 只看升級字樣 /
        不加縫:同一支假 claude。"""
        self.arm()
        self.play(opener="garbage")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual([row["outcome"] for row in self.opener_rows()], ["undecided"])
