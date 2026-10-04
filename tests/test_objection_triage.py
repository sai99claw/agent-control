"""`scripts/auto-fix.sh`:worker 的反駁改由開題者判,接回同一個 session 在原副本續做(#89,D-041)。

D-041(2026-10-04):worker 交件帶 `OBJECTION: ticket-wrong|blocking` 時不再轉 Blocked 等主線,
改起一個開題者判;accepted → 接回同一個驗證者改案例、再接回同一個 worker 在原副本續做;
rejected → 附理由接回 worker 照原票做;只有開題者判需裁示、同票第二次反駁、開題者沒判出來、
session 接不回四種才進主線收件匣。

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
ACCEPTANCE = ["A1 第一條照舊", "A2 驗收第二條照票面:輸出兩欄"]
NEW_ACCEPTANCE = ["A1 第一條照舊", "A2 驗收第二條改照設計文件:輸出三欄"]
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
    elif mode == "sleep":
        time.sleep(60)
    else:
        write(os.path.join(work, "work", "tests", "test_thing.py"),
              THING % ("2" if mode == "red" else "1"))
        handin("patch-round%s.diff" % r)
        write(evidence, "# 第 %s 輪(%s)\n已排除的假設:沒有\n" % (r, mode))
elif role == "verifier":
    if mode == "handin":
        write(os.path.join(work, "work", "verify", "example", "test_ticket_1.py"), @CASE@)
        handin("patch-verify.diff")
        write(os.path.join(work, "EVIDENCE-verifier.md"), @VEVIDENCE@)
else:
    triage = os.path.join(os.getcwd(), "reports", "t1", "triage-round%s.md" % r)
    if mode == "accept":
        subprocess.run([sys.executable, os.path.join(REPO, "scripts", "ticket.py"), "set", "1",
                        "acceptance", json.dumps(plan["new_acceptance"], ensure_ascii=False)],
                       check=True, stdout=subprocess.DEVNULL)
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
                         "objection_again": OBJECTION_AGAIN, "new_acceptance": NEW_ACCEPTANCE,
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

    def assert_not_stopped(self, done, pages_before):
        """A3/A4 的 (4)(5):不換輪、不轉 Blocked、不發頁。"""
        self.assertEqual(self.worker_rounds(), ["worker ran round 1"] * 2)
        failed = [row for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual([row for row in failed if row.get("reason") == "objection"], [])
        self.assertEqual(len(self.inbox_rows()), pages_before, self.inbox_rows())
        self.assertNotIn("decision.asked", self.kinds())
        blocked = [row for row in self.events() if row["kind"] == "ticket.state"
                   and str(row.get("to")).strip('"') == "Blocked"]
        self.assertEqual(blocked, [])

    def assert_evidence_kept(self):
        """A3/A4 的 (6):第一段的 EVIDENCE 改名留著,續做那一份另外一個檔。"""
        kept = os.path.join(self.copy(), "EVIDENCE-round1-objection.md")
        self.assertTrue(os.path.isfile(kept), os.listdir(self.copy()))
        self.assertIn(OBJECTION, self.read(kept, where="/"))
        self.assertNotIn("OBJECTION:", self.read(os.path.join(self.copy(), "EVIDENCE-round1.md"),
                                                  where="/"))

    def assert_escalated(self, done, words, started, verifier=False):
        """A5 每一種的共同斷言:rc=3、Blocked、owner=main、decision.asked、恰一頁 decision、
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
        self.assertNotIn("triage", ticket["objections"][-1])
        for sub in ("work", "base"):
            self.assertTrue(os.path.isdir(os.path.join(self.copy(), sub)), sub)
        if verifier:
            self.assertTrue(os.path.isdir(os.path.join(self.copy("verify"), "work")))
        self.assertEqual({role: row["state"] for role, row in rows.items()},
                         {role: "open" for role in rows})
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


class AcceptedResumesTheSameSessions(TriageBase):

    def test_a2_a3_accepted_resumes_the_verifier_then_the_worker_in_place(self):
        """四問:守 A2 開題者的起法與當下事實、A3 (1)–(7)、A6(a) 收件後兩列 closed /
        可信回歸:替身開題者真的跑 ticket.py set 改驗收,接回的兩個替身各自檢查第一段的標記檔、
        回報 argv / cwd / stdin;頁數期望值是起跑前自己數的 / 既有四條只看「轉 Blocked」那一條路 /
        不加縫:三個角色都是 PATH 上同一支假 claude,劇本在沙盒的 scenario.json。"""
        row = self.arm(needs_verifier=True, interface_fixed=True)
        self.play(worker_resume="fix", opener="accept")
        pages_before = len(self.inbox_rows())
        done = self.auto_fix("--no-review")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertNotIn("這一筆不收", out)
        self.assertTrue(self.went_in_review())

        calls = self.calls()
        self.assertEqual(sorted(call["role"] for call in calls[:2]), ["verifier", "worker"])
        self.assertEqual([call["role"] for call in calls[2:]], ["opener", "verifier", "worker"])
        first = {call["role"]: call for call in calls[:2]}
        sid = {role: self.flag_value(first[role]["argv"], "--session-id") for role in first}

        # A2:開題者的 argv / cwd / AC_ROOT / 派工文 / 跑的當下
        opener = calls[2]
        self.assertEqual(opener["argv"], shlex.split(OPENER_COMMAND)[1:])
        self.same_dir(".", opener["cwd"])
        self.same_dir(".", opener["ac_root"])
        packet = opener["stdin"]
        self.assertTrue(packet.startswith("# 規則包:opener"), packet[:80])
        self.assertIn("## 判反駁", packet)
        self.assertIn(OBJECTION, packet)
        self.assertIn("../repo-wt/fix-t1/round1/EVIDENCE-round1.md", packet)
        self.assertIn("#1", packet)
        self.assertIn("把判決寫進 reports/t1/triage-round1.md,第一行 TRIAGE: "
                      "<accepted|rejected|escalate> <一句理由>", packet)
        self.assertEqual(opener["ticket"]["state"], "Running")
        self.assertEqual(opener["worker_copy"], [True, True])
        self.assertTrue(opener["verifier_copy"])
        kinds = [(e["kind"], e.get("role")) for e in self.events()
                 if e["kind"].startswith("objection.triage.")
                 or (e["kind"].startswith("agent.") and e.get("role") == "opener")]
        self.assertEqual(kinds, [("objection.triage.start", None), ("agent.start", "opener"),
                                 ("agent.done", "opener"), ("objection.triage.done", None)])
        start, end = self.triage_events()
        self.assertEqual((start.get("round"), start.get("category")), ("1", "ticket-wrong"))
        self.assertEqual(end.get("verdict"), "accepted")

        # (1) 單一寫入者:disposition 與 triage 由 auto-fix 依 TRIAGE 行寫
        objection = self.objection()
        self.assertEqual(objection["disposition"], "accepted")
        self.assertEqual(objection["triage"], {"verdict": "accepted", "reason": ACCEPT_REASON,
                                               "round": 1, "by": "auto-fix"})
        self.assertEqual(self.load_ticket("1")["acceptance"], NEW_ACCEPTANCE)
        # (2) 接回驗證者
        verifier = calls[3]
        self.assertEqual(self.flag_value(verifier["argv"], "--resume"), sid["verifier"])
        self.assertNotIn("--session-id", verifier["argv"])
        self.same_dir(first["verifier"]["sessions"]["verifier"]["cwd"], verifier["cwd"])
        for text in ("## 被接回", "acceptance", NEW_ACCEPTANCE[1]):
            self.assertIn(text, verifier["stdin"])
        self.assertTrue(verifier["mark_there"], "驗證者第一段的標記檔不在 —— 副本被重建了")
        # (3) 接回 worker
        worker = calls[4]
        self.assertEqual(self.flag_value(worker["argv"], "--resume"), sid["worker"])
        self.assertNotIn("--session-id", worker["argv"])
        self.same_dir(first["worker"]["sessions"]["worker"]["cwd"], worker["cwd"])
        for text in ("## 被接回", "accepted", ACCEPT_REASON, "acceptance", NEW_ACCEPTANCE[1]):
            self.assertIn(text, worker["stdin"])
        self.assertTrue(worker["mark_there"], "worker 第一段的標記檔不在 —— 副本被重建了")
        # (4)(5)(6)
        self.assertEqual(self.load_ticket("1")["attempt"], row["attempt"])
        self.assert_not_stopped(done, pages_before)
        self.assert_evidence_kept()
        # (7) 照一般那一輪走:兩份 patch 一起進閘門那一棵樹
        self.assertIn("test_worker_case_is_there",
                      self.git("show", "t1:verify/example/test_ticket_1.py"))
        # A6(a):收件後兩份副本刪掉、兩列 closed
        for kind in ("fix", "verify"):
            for sub in ("work", "base"):
                self.assertFalse(os.path.isdir(os.path.join(self.copy(kind), sub)), (kind, sub))
        for role, rec in self.sessions().items():
            self.assertEqual(rec["state"], "closed", role)
            self.assertTrue(rec.get("closed_at"), role)


class RejectedResumesTheWorkerOnly(TriageBase):

    def test_a4_rejected_resumes_the_worker_with_the_reason_and_leaves_the_ticket(self):
        """四問:守 rejected → 票面不動、驗證者不接回、worker 帶理由接回 / 可信回歸:票面比對
        的兩端是替身開題者與接回的 worker 各自讀的票檔 / 既有測試沒有 rejected 這條路 /
        不加縫:同一支假 claude。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(worker_resume="fix", opener="reject")
        pages_before = len(self.inbox_rows())
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        objection = self.objection()
        self.assertEqual(objection["disposition"], "rejected")
        self.assertEqual(objection["triage"]["verdict"], "rejected")
        self.assertEqual(objection["triage"]["reason"], REJECT_REASON)
        opener, = self.calls("opener")
        workers = self.calls("worker")
        self.assertEqual(len(workers), 2, workers)
        resumed = workers[1]
        skip = ("objections", "state_version")
        self.assertEqual({k: v for k, v in resumed["ticket"].items() if k not in skip},
                         {k: v for k, v in opener["ticket"].items() if k not in skip})
        verifier_sid = self.flag_value(self.calls("verifier")[0]["argv"], "--session-id")
        resumed_ids = [call["argv"][call["argv"].index("--resume") + 1]
                       for call in self.calls() if "--resume" in call["argv"]]
        self.assertNotIn(verifier_sid, resumed_ids)
        self.assertEqual(len(self.calls("verifier")), 1)
        self.assertEqual(self.flag_value(resumed["argv"], "--resume"),
                         self.flag_value(workers[0]["argv"], "--session-id"))
        self.same_dir(workers[0]["sessions"]["worker"]["cwd"], resumed["cwd"])
        for text in ("反駁不成立", REJECT_REASON, "照原票做"):
            self.assertIn(text, resumed["stdin"])
        self.assertTrue(resumed["mark_there"])
        self.assert_not_stopped(done, pages_before)
        self.assert_evidence_kept()


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
        """四問:守 (b) 被接回的 worker 再交 OBJECTION → 不再起開題者 / 可信回歸:開題者呼叫只有
        一次、第二筆 disposition 留空 / 既有測試沒有接回 / 不加縫:同一支假 claude。"""
        self.arm()
        self.play(worker_resume="object-again", opener="reject")
        done = self.auto_fix("--no-review")
        page = self.assert_escalated(done, ["同票第二次反駁"], started=False)
        self.assertEqual(len(self.calls("opener")), 1)
        self.assertEqual(len(self.calls("worker")), 2)
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

    def test_a5d_a_worker_session_that_cannot_resume(self):
        """四問:守 (d) worker 列 resumable=false → 不起開題者、頁上帶 why / 可信回歸:沒有 opener
        呼叫、沒有 triage.start / 既有四條走的是 (c) / 不加縫:命令帶 --no-session-persistence。"""
        self.arm(worker_cmd="claude -p --model opus --no-session-persistence")
        done = self.auto_fix("--no-review")
        self.assert_escalated(done, ["不能接回", "--no-session-persistence"], started=False)
        self.assertEqual(self.calls("opener"), [])


class ClosingAndTimeouts(TriageBase):
    """A6:收掉與逾時。"""

    def test_a6a_a_closed_session_is_never_resumed_and_the_next_round_gets_a_new_one(self):
        """四問:守收件後 sessions 轉 closed、下一輪新 --session-id、closed 的 id 不再出現 /
        可信回歸:接回的 worker 交一份仍紅的 patch,auto-fix 自己進第 2 輪,第 2 輪的替身記下
        argv 與它看到的 sessions.json / 既有測試沒有 session / 不加縫:同一支假 claude。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(worker_resume="red", opener="accept", worker_r2="fix")
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

    def test_a6c_a_resumed_worker_that_times_out_leaves_its_copy_and_session_open(self):
        """四問:守接回的 worker 用票的 worker.timeout_seconds、從接回那一刻重新計,逾時走既有頁 /
        可信回歸:第一段立刻交、接回那一段睡 60 秒,時限 3 秒 / 既有逾時案例沒有接回 /
        不加縫:票的 worker.timeout_seconds。"""
        self.arm(worker={"timeout_seconds": 3})
        self.play(worker_resume="sleep", opener="reject")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertEqual(len(self.calls("worker")), 2)
        self.assertEqual(self.load_ticket("1")["state"], "Blocked")
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("worker 逾時", json.dumps(pages[0], ensure_ascii=False))
        self.assertTrue(os.path.isdir(os.path.join(self.copy(), "work")))
        self.assertEqual(len(self.result_files("patch-partial-round1.diff")), 1)
        self.assertEqual(self.sessions()["worker"]["state"], "open")

    def test_a6c_a_resumed_verifier_that_hands_in_nothing_stops_before_the_worker(self):
        """四問:守接回的驗證者沒交件 → 既有「驗證者沒交出 patch-verify」頁、不接回 worker /
        可信回歸:worker 呼叫只有第一段那一次 / 既有 C6 案例沒有接回 / 不加縫:同一支假 claude。"""
        self.arm(needs_verifier=True, interface_fixed=True)
        self.play(opener="accept", verifier_resume="nothing")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertEqual(len(self.calls("verifier")), 2)
        self.assertEqual(len(self.calls("worker")), 1, "驗證者沒交件就不該接回 worker")
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("驗證者沒交出 patch-verify", json.dumps(pages[0], ensure_ascii=False))
        failed = [row.get("reason") for row in self.events()
                  if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual(failed, ["verifier-no-patch"])


if __name__ == "__main__":
    unittest.main()
