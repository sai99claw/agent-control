"""#106 開題實跑證據(plan_ran)與可用的 Ready 關,涵蓋機械回歸票。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / interface_contracts / 夾具自己的 git 與命令)
A1 | unit | ticket.py plan <id> --run '<exit 3 的命令>'            | plan_ran 恰五鍵、rc==3、cmd 原字、dirty 是 bool、sha == 主線頭 | 票面 A1 + interface_contracts.plan_ran;夾具命令自己 exit 3
A1 | unit | ticket.py set <id> plan_ran '<rc=0 的五鍵>'             | rc≠0、票檔位元不變                                     | 票面 A1「不開放 set 偽造」
A2 | unit | plan --run 寫 pwd 與 HEAD 到夾具檔                       | pwd 不是 repo、HEAD == 主線頭;輸出含命令、rc=3、一個存在的 log 路徑(內含命令輸出) | 票面 A2
A2 | unit | plan --base <不在主線的 sha>                             | rc≠0、票上沒有 plan_ran                                | 票面 A2「不在所聲明基底…不能轉 Ready」
A2 | unit | plan 的命令在樹裡 commit / 改 tracked 檔                 | set state Ready 拒、state 留 Draft                     | 票面 A2「不在所聲明基底或 dirty=true」
A3 | unit | create --state Ready / set state Ready 沒有 plan_ran      | 拒;plan 之後 set Ready 收                              | 票面 A3 原話
A3 | unit | 舊 Ready 沒有 plan_ran                                   | 改別格照收;lint 不提 plan_ran                          | 票面 A3「舊 Ready 不追溯」
A3 | unit | plan 之後主線動:無關檔 / allowed_write_paths / info_homes.home / verify.files / test_plan 的測試 | 無關照收;其餘四種拒 | 票面 A3 + interface_contracts.input_scope(hint_scope ∪ test_plan)
A3 | unit | plan 之後 test_plan 只指到不存在的測試、主線再動無關檔    | 拒(解析不出 = 證明不了)                               | 票面 A3「test_plan 的測試解析不出…才補跑」
A3 | unit | plan 之後主線被改寫,plan_ran.sha 不再是祖先              | 拒                                                     | 票面 A3「不是祖先」
A3 | unit | evidence 檔                                               | reports/t<id>/planning/<sha256(at)>.json 恰六鍵、plan_ran 同值;刪掉它 → 拒 | interface_contracts.evidence
A4 | unit | regression-red 帶完整 gate 證據                          | 票 Ready、plan_ran{sha=gate base, cmd=gate cmd, rc=gate rc, dirty=false};gate 命令沒被執行 | 票面 A4 + 「不得在原 ticket lock 中啟…長測試」
A4 | unit | regression-red 只有 case                                 | 票 Draft、outline 含「待 plan 實跑」、無 plan_ran;手轉 Ready 拒;plan 後 Ready 收 | 票面 A4 原話
A4 | unit | regression-red gate 證據對不上(rc=0 而 log 紅 / base 不在主線) | 票 Draft、無 plan_ran                                | 票面 A4「可核對的真 gate」、test_plan T2「偽造 evidence 拒收」
A4 | unit | 手寫一張帶 regression_case 的 Draft worker 票             | set state Ready 拒                                     | 票面 A4「不得只因 role=worker 或 regression_case 就豁免」(M1)
A5 | unit | 前置 #1 未落地時 #2 plan;#1 之後落地(Done + review.sha 在主線,只動無關檔) | #2 Ready 拒;再 plan 一次才收 | 票面 A5「依賴落地後先補跑再 Ready」
A6 | unit | lint:Draft 無 plan_ran / plan 後 / 相關輸入又變            | 提 plan_ran / 不提 / 又提                              | 票面 A6「lint 與 Ready/create 同判準」
A6 | unit | 讀 role-procedures / dispatch-opener / opener.inbox / opener.md / test_status_67 | 前三份提 `ticket.py plan`/plan_ran;角色卡不加;回歸測試釘 plan_ran 與 Draft | 票面 A6 原話

## 介面字串(票面定的照抄;票面沒定、照 worker patch 的在這裡定)
plan_ran 五鍵 = {sha, cmd, rc, at, dirty}                       ← interface_contracts.plan_ran
evidence = reports/t<id>/planning/<sha256(at UTF-8)>.json,鍵 = {schema_version, plan_ran, face, input_digest, inputs, log_refs} ← interface_contracts.evidence
PENDING = "待 plan 實跑"                                        ← 票面 A4 原話
`ticket.py plan <id> --run <cmd>`                               ← 票面 A2 原話;`--base <sha>` 是「指定…基底」的旗標名,照 worker patch
regression-red 的 gate 證據旗標 `--gate-cmd` / `--gate-rc` / `--gate-base` ← 票面只說 command/rc/base/log,旗標名照 worker patch
landed = 前置票 state=Done 且 review.sha 在主線(既有 `ticket.py close --landed` 的形狀)

## 怎麼做假(不上真埠、不起真模型)
tests/control_harness.Sandbox:拋棄式真 git repo + 絆線 claude + AC_* 全清。plan 的命令是 sh 一行
(exit N / 寫夾具檔),regression-red 的 log 是夾具手寫的 unittest 形狀。不跑任何真測試套件。

## 不做
不改產品碼;不放寬票面驗收;不碰既有案例。「不在原 ticket lock 中啟 LLM」只量到 gate 命令沒被執行
與絆線沒被踩(沙盒 cleanup),鎖的持有期間沒有直接量。
"""
import hashlib
import json
import os
import re
import sys
import unittest

TAGS = ["plan-ran"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import Sandbox  # noqa: E402

PLAN_KEYS = {"sha", "cmd", "rc", "at", "dirty"}
EVIDENCE_KEYS = {"schema_version", "plan_ran", "face", "input_digest", "inputs", "log_refs"}
PENDING = "待 plan 實跑"
TEST_FILE = "tests/test_x.py"
TEST_PLAN = "跑 `python3 -m unittest %s`,看 exit code" % TEST_FILE
HOME_FILE = "notes/home.md"
CASE_FILE = "verify/example/test_example.py"
RERUN = "python3 -m unittest discover -s tests"
CASE = "tests.test_x.T.test_one"


def one_red(case):
    """只有一條紅的 unittest log(夾具手寫)。"""
    method = case.rsplit(".", 1)[-1]
    return ("%s (%s) ... FAIL\n\n" % (method, case) + "=" * 70 + "\n"
            "FAIL: %s (%s)\n" % (method, case) + "-" * 70 + "\n"
            "AssertionError: 假的紅\n\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n")


class World(Sandbox):
    """主線有 src/a.txt、tests/test_x.py、notes/home.md、docs/other.md;票是 Draft 的 worker 票。"""

    def setUp(self):
        super().setUp()
        self.write("src/a.txt", "old\n")
        self.write(TEST_FILE, "import unittest\n\n\nclass T(unittest.TestCase):\n"
                              "    def test_one(self):\n        self.assertEqual(1, 1)\n")
        self.write(HOME_FILE, "資訊在這裡\n")
        self.write("docs/other.md", "無關\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上的夾具檔")

    def draft(self, ident="1", **fields):
        row = dict(state="Draft", test_plan=TEST_PLAN, allowed_write_paths=["src/*"],
                   needs_verifier=False, interface_fixed=False,
                   info_homes=[{"what": "資訊", "home": HOME_FILE}],
                   verify={"files": [CASE_FILE], "tags": [], "run": "", "notes": ""},
                   verify_strings=[{"path": "src/a.txt", "contains": "new"}])
        row.update(fields)
        return self.make_ticket(ident, **row)

    def head(self):
        return self.git("rev-parse", "main").strip()

    def main_commit(self, rel, text, subject):
        self.write(rel, text)
        self.git("add", rel)
        self.git("commit", "-q", "-m", subject, "--", rel)

    def plan(self, ident, cmd, *extra, env=None):
        return self.ticket("plan", ident, "--run", cmd, *extra, env=env)

    def plan_ok(self, ident="1", cmd="exit 0"):
        done = self.plan(ident, cmd)
        self.assertEqual(done.returncode, 0, "plan 要跑得起來:\n" + done.stdout + done.stderr)
        plan = self.load_ticket(ident).get("plan_ran")
        self.assertIsNotNone(plan, "plan 之後票上要有 plan_ran:\n" + done.stdout + done.stderr)
        return plan

    def to_ready(self, ident="1"):
        return self.ticket("set", ident, "state", "Ready")

    def assert_ready_refused(self, ident="1", why=""):
        done = self.to_ready(ident)
        self.assertNotEqual(done.returncode, 0, "%s 不能轉 Ready:\n%s%s" % (why, done.stdout, done.stderr))
        self.assertEqual(self.load_ticket(ident)["state"], "Draft", why)

    def assert_ready_accepted(self, ident="1", why=""):
        done = self.to_ready(ident)
        self.assertEqual(done.returncode, 0, "%s 要能轉 Ready:\n%s%s" % (why, done.stdout, done.stderr))
        self.assertEqual(self.load_ticket(ident)["state"], "Ready", why)

    def evidence_path(self, ident, at):
        name = hashlib.sha256(at.encode("utf-8")).hexdigest() + ".json"
        return os.path.join(self.repo, "reports", "t%s" % ident, "planning", name)

    def lint(self, ident="1"):
        done = self.ticket("lint", ident)
        return done.returncode, done.stdout + done.stderr


# ------------------------------------------------------------------ A1


class A1PlanRanIsRecordedAfterARealRun(World):

    def test_a1_nonzero_rc_is_recorded_with_exactly_five_keys(self):
        """A1 plan --run 'exit 3':plan_ran 恰五鍵、rc=3、cmd 原字、dirty 是 bool、sha 是主線頭。

        四問:量票上 plan_ran 的鍵集、rc、cmd、dirty 型別、sha;紅 = 沒有 plan 子指令、未跑先寫 rc=0(M2)、
        多鍵或 list;期望值 = 夾具命令自己 exit 3 + 夾具的主線頭;不測 = evidence 內容(A3 那一條)。
        """
        self.draft()
        cmd = "echo plan-ran-106; exit 3"
        plan = self.plan_ok(cmd=cmd)
        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual(plan["rc"], 3, "非零測試結果也要照實記")
        self.assertEqual(plan["cmd"], cmd)
        self.assertIs(type(plan["dirty"]), bool)
        self.assertFalse(plan["dirty"])
        self.assertEqual(plan["sha"], self.head())

    def test_a1_set_cannot_forge_plan_ran(self):
        """A1 set <id> plan_ran '<rc=0 的五鍵>' 拒收,票檔位元不變。

        四問:量 set 的 rc 與票檔位元;紅 = set 收下偽造的五鍵;期望值 = 票面「不開放 set 偽造」;
        不測 = 錯誤訊息用詞。
        """
        self.draft()
        before = self.read("tickets/1.json")
        forged = {"sha": self.head(), "cmd": "true", "rc": 0,
                  "at": "2026-10-19T00:00:00.000000+00:00", "dirty": False}
        done = self.ticket("set", "1", "plan_ran", json.dumps(forged))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.read("tickets/1.json"), before)


# ------------------------------------------------------------------ A2


class A2PlanRunsInAThrowawayTreeOnTheDeclaredBase(World):

    def test_a2_runs_in_a_throwaway_tree_at_main_and_prints_cmd_rc_log(self):
        """A2 命令在拋棄式樹(不是 repo 本身)、HEAD = 主線頭;輸出含命令、rc=3 與存在的 log 位置。

        四問:量夾具檔裡的 pwd / HEAD、輸出的命令原字與 rc、輸出裡指到的 log 檔內容;紅 = 在 repo 本身跑、
        不跑、輸出不給 log;期望值 = 夾具命令自己寫的檔 + 主線頭;不測 = log 檔名形狀。
        """
        self.draft()
        mark = os.path.join(self.home, "where.txt")
        cmd = 'pwd > "$T106_MARK"; git rev-parse HEAD >> "$T106_MARK"; echo LOG-MARK-106; exit 3'
        done = self.plan("1", cmd, env=self.env(T106_MARK=mark))
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertTrue(os.path.exists(mark), "命令要真的跑過:\n" + out)
        with open(mark, encoding="utf-8") as handle:
            where, head = handle.read().split()[:2]
        self.assertNotEqual(os.path.realpath(where), os.path.realpath(self.repo),
                            "要在拋棄式樹裡跑,不是 repo 本身")
        self.assertEqual(head, self.head())
        self.assertIn(cmd, out)
        self.assertRegex(out, r"rc\W{0,3}3\b")
        logs = []
        for token in re.split(r"[\s,()（）:：]+", out):
            path = token if os.path.isabs(token) else os.path.join(self.repo, token)
            if token and os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as handle:
                    if "LOG-MARK-106" in handle.read():
                        logs.append(token)
        self.assertTrue(logs, "輸出要指到一份含命令輸出的 log:\n" + out)

    def test_a2_a_base_off_main_is_refused(self):
        """A2 --base <不在主線的 sha>:rc≠0、票上沒有 plan_ran。

        四問:量 plan 的 rc、票上 plan_ran;紅 = 在任意 sha 上跑也記;期望值 = 夾具 side 分支;
        不測 = 訊息用詞。乾淨基底沒有 plan 子指令也是 rc≠0,所以這一條在基底上可能是綠的。
        """
        self.draft()
        self.git("checkout", "-q", "-b", "side")
        self.write("side.txt", "x\n")
        self.git("add", "side.txt")
        self.git("commit", "-q", "-m", "不在主線")
        side = self.git("rev-parse", "side").strip()
        self.git("checkout", "-q", "main")
        done = self.plan("1", "exit 0", "--base", side)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("plan_ran", self.load_ticket("1"))

    def test_a2_a_run_that_moves_head_cannot_go_ready(self):
        """A2 命令在樹裡 commit(實跑的樹離開所聲明基底):plan_ran.dirty 是 bool,轉 Ready 拒。

        四問:量 plan_ran.dirty 型別、set state Ready 的 rc 與 state;紅 = 照樣轉 Ready;
        期望值 = 夾具命令自己 commit;不測 = 記下的 sha 是哪一顆。
        """
        self.draft()
        plan = self.plan_ok(cmd="git -c user.name=t -c user.email=t@example.invalid "
                                "commit -q --allow-empty -m moved; exit 0")
        self.assertIs(type(plan["dirty"]), bool)
        self.assert_ready_refused(why="實跑的樹 HEAD 離開了基底")

    def test_a2_a_run_that_dirties_tracked_files_cannot_go_ready(self):
        """A2 命令改了 tracked 檔(樹不再乾淨):dirty=true,轉 Ready 拒。

        四問:量 plan_ran.dirty、set state Ready;紅 = dirty 記 false 或照轉 Ready;期望值 = 夾具命令改 README;
        不測 = 未追蹤檔(測試產物)算不算 dirty。
        """
        self.draft()
        plan = self.plan_ok(cmd="echo changed >> README; exit 0")
        self.assertIs(plan["dirty"], True)
        self.assert_ready_refused(why="dirty=true")


# ------------------------------------------------------------------ A3


class A3ReadyNeedsAValidPlanRan(World):

    def test_a3_create_ready_without_plan_ran_is_refused(self):
        """A3 create --state Ready 的 worker 票(還沒有 plan_ran)拒,票沒開出來。

        四問:量 create 的 rc 與票檔在不在;紅 = 照開 Ready;期望值 = 票面 A3「worker 新轉 Ready/create Ready
        必有有效 plan_ran」;不測 = 改開 Draft 的路(那是 create 的既有行為)。
        """
        before = sorted(os.listdir(os.path.join(self.repo, "tickets")))
        done = self.ticket("create", "--subject", "要實跑的票", "--objective", "做某件事",
                           "--acceptance", "會紅的斷言一條", "--allowed-write-path", "src/*",
                           "--test-plan", TEST_PLAN, "--role", "worker", "--model", "opus",
                           "--tool", "claude-code", "--needs-verifier", "false",
                           "--interface-fixed", "false",
                           "--verify-string", json.dumps({"path": "src/a.txt", "contains": "new"}),
                           "--state", "Ready")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, "tickets"))), before)

    def test_a3_set_ready_needs_plan_ran_and_accepts_after_plan(self):
        """A3 Draft → Ready:沒有 plan_ran 拒;plan 之後(主線沒動)收。

        四問:量兩次 set state Ready 的 rc 與 state;紅 = 沒跑過也收;期望值 = 票面 A3;不測 = 訊息用詞。
        """
        self.draft()
        self.assert_ready_refused(why="沒有 plan_ran")
        self.plan_ok()
        self.assert_ready_accepted(why="剛 plan 過、主線沒動")

    def test_a3_an_old_ready_ticket_is_not_retroactive(self):
        """A3 舊 Ready(沒有 plan_ran)不追溯:改別格照收、lint 不提 plan_ran。

        四問:量 set priority 的 rc、lint 輸出;紅 = 舊 Ready 被新判準卡住;期望值 = 票面「舊 Ready 不追溯」;
        不測 = lint 其他檢查。
        """
        self.draft(state="Ready")
        done = self.ticket("set", "1", "priority", "P2")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        _, out = self.lint()
        self.assertNotIn("plan_ran", out)

    def test_a3_unrelated_main_change_keeps_plan_ran_valid(self):
        """A3 plan 之後主線只動無關檔(docs/other.md):轉 Ready 收,不必重跑。

        四問:量 set state Ready;紅 = main 每動一顆 commit 就要全重跑(舊 #106);期望值 = D-050 ③;
        不測 = 輸入比對的實作方式。
        """
        self.draft()
        self.plan_ok()
        self.main_commit("docs/other.md", "還是無關\n", "主線動了無關檔")
        self.assert_ready_accepted(why="主線只動了無關檔")

    def test_a3_related_input_changes_require_a_rerun(self):
        """A3 plan 之後主線動到 allowed_write_paths / info_homes.home / verify.files / test_plan 的測試:各自拒,重跑後收。

        四問:量每種變動後 set state Ready、重 plan 後再轉;紅 = 任意祖先 sha 就准而不比輸入(M3);
        期望值 = interface_contracts.input_scope(hint_scope 三格 ∪ test_plan 的測試);不測 = digest 算法。
        """
        rows = (("allowed_write_paths", "src/a.txt"), ("info_homes.home", HOME_FILE),
                ("verify.files", CASE_FILE), ("test_plan 的測試", TEST_FILE))
        for ident, (name, rel) in enumerate(rows, 1):
            with self.subTest(name):
                ident = str(ident)
                self.draft(ident)
                self.plan_ok(ident)
                self.main_commit(rel, self.read(rel) + "# 改了 %s\n" % name, "主線動了 %s" % name)
                self.assert_ready_refused(ident, why="主線動了 %s" % name)
                self.plan_ok(ident)
                self.assert_ready_accepted(ident, why="重 plan 之後")

    def test_a3_unresolvable_test_plan_reference_cannot_be_proven(self):
        """A3 test_plan 只指到不存在的測試、主線再動無關檔:證明不了 → 拒。

        四問:量 set state Ready;紅 = 解析不出就當無關;期望值 = 票面「test_plan 的測試解析不出…才補跑」;
        不測 = 主線完全沒動時的判法(票面沒說)。
        """
        self.draft(test_plan="跑 `python3 -m unittest tests/test_missing_106.py`")
        self.plan_ok()
        self.main_commit("docs/other.md", "還是無關\n", "主線動了無關檔")
        self.assert_ready_refused(why="test_plan 的測試解析不出")

    def test_a3_plan_sha_no_longer_an_ancestor_of_main(self):
        """A3 plan 之後主線被改寫,plan_ran.sha 不再是祖先:拒。

        四問:量 set state Ready;紅 = 不問祖先;期望值 = 票面「不是祖先」;不測 = 改寫怎麼發生。
        """
        self.main_commit("docs/other.md", "會被改寫掉\n", "之後被改寫的 commit")
        self.draft()
        plan = self.plan_ok()
        self.git("checkout", "-q", "-B", "main", plan["sha"] + "~1")
        self.main_commit("docs/third.md", "另一條歷史\n", "改寫後的主線")
        self.assert_ready_refused(why="plan_ran.sha 不在主線")

    def test_a3_evidence_file_has_the_contract_shape_and_is_required(self):
        """A3 evidence 在 reports/t<id>/planning/<sha256(at)>.json、恰六鍵、plan_ran 同值;刪了它 → 拒。

        四問:量檔在不在、鍵集、plan_ran、刪檔後 set state Ready;紅 = 沒有 evidence 或不比對它;
        期望值 = interface_contracts.evidence;不測 = face / input_digest 的算法。
        """
        self.draft()
        plan = self.plan_ok()
        path = self.evidence_path("1", plan["at"])
        self.assertTrue(os.path.isfile(path), "evidence 要在 %s" % os.path.relpath(path, self.repo))
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
        self.assertEqual(set(body), EVIDENCE_KEYS)
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["plan_ran"], plan)
        self.assertIsInstance(body["log_refs"], list)
        self.assertIsInstance(body["inputs"], list)
        os.remove(path)
        self.assert_ready_refused(why="evidence 不見了")


# ------------------------------------------------------------------ A4


class A4RegressionRedRecordsThroughTheSameRecorder(World):

    def red(self, *args):
        return self.run_py("scripts/status.py", "regression-red", *args)

    def regression_row(self, case=CASE):
        rows = [row for row in self.tickets_on_disk() if row.get("regression_case") == case]
        self.assertEqual(len(rows), 1, "這條案例要恰一張修復票:%s" % rows)  # pin:一條案例恰一張開著的修復票(#67 查重)
        return rows[0]

    def test_a4_full_gate_evidence_records_plan_ran_and_goes_ready(self):
        """A4 完整 gate 證據:票 Ready、plan_ran 五鍵取自 gate、evidence 在;gate 命令本身沒被執行。

        四問:量票 state、plan_ran 各鍵、evidence 檔、夾具 gate 命令的標記檔;紅 = 仍只 Draft、plan_ran
        不是 gate 的值、或在票鎖裡重跑 gate;期望值 = 夾具旗標;不測 = --dispatch。
        """
        log = self.write("gate.log", one_red(CASE))
        mark = os.path.join(self.home, "gate-ran")
        gate_cmd = "touch %s; %s" % (mark, RERUN)
        base = self.head()
        done = self.red("--source", "gate", "--log", log, "--rerun", RERUN,
                        "--gate-cmd", gate_cmd, "--gate-rc", "1", "--gate-base", base)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.regression_row()
        self.assertEqual(row["state"], "Ready", done.stdout + done.stderr)
        plan = row.get("plan_ran")
        self.assertIsNotNone(plan, "完整 gate 證據要記 plan_ran")
        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual((plan["sha"], plan["cmd"], plan["rc"], plan["dirty"]),
                         (base, gate_cmd, 1, False))
        self.assertTrue(os.path.isfile(self.evidence_path(row["id"], plan["at"])))
        self.assertFalse(os.path.exists(mark), "regression-red 不得自己重跑 gate 命令")

    def test_a4_case_only_is_draft_until_ticket_py_plan(self):
        """A4 只有 case:Draft、outline 含「待 plan 實跑」、無 plan_ran;手轉 Ready 拒;plan 後收。

        四問:量 state / outline / plan_ran、兩次 set state Ready;紅 = 只有 case 也 Ready(舊行為);
        期望值 = 票面 A4 原話;不測 = #125 的觸發流程。
        """
        done = self.red("--source", "release", "--case", CASE, "--rerun", RERUN)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.regression_row()
        self.assertEqual(row["state"], "Draft", done.stdout + done.stderr)
        self.assertNotIn("plan_ran", row)
        self.assertIn(PENDING, row.get("outline") or "")
        self.assert_ready_refused(row["id"], why="只有 case")
        self.plan_ok(row["id"], cmd="exit 1")
        self.assert_ready_accepted(row["id"], why="ticket.py plan 補跑後")

    def test_a4_gate_evidence_that_does_not_add_up_stays_draft(self):
        """A4 gate 證據對不上(rc=0 而 log 紅 / base 不在主線):Draft、無 plan_ran。

        四問:量 state 與 plan_ran;紅 = 對不上的證據也記;期望值 = 夾具自己造的矛盾;不測 = 訊息用詞。
        """
        self.git("checkout", "-q", "-b", "side")
        self.write("side.txt", "x\n")
        self.git("add", "side.txt")
        self.git("commit", "-q", "-m", "不在主線")
        side = self.git("rev-parse", "side").strip()
        self.git("checkout", "-q", "main")
        rows = (("rc0", "tests.test_x.T.test_rc0", "0", self.head()),
                ("off-main", "tests.test_x.T.test_side", "1", side))
        for name, case, rc, base in rows:
            with self.subTest(name):
                log = self.write("%s.log" % name, one_red(case))
                done = self.red("--source", "gate", "--log", log, "--rerun", RERUN,
                                "--gate-cmd", RERUN, "--gate-rc", rc, "--gate-base", base)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                row = self.regression_row(case)
                self.assertEqual(row["state"], "Draft", done.stdout + done.stderr)
                self.assertNotIn("plan_ran", row)

    def test_a4_regression_case_is_not_an_exemption(self):
        """A4 帶 regression_case 的 Draft worker 票沒有 plan_ran:轉 Ready 拒(M1)。

        四問:量 set state Ready;紅 = regression_case 豁免;期望值 = 票面「不得只因 role=worker 或
        regression_case 就豁免」;不測 = regression-red 的開票。
        """
        self.draft(regression_case=CASE, regression_source="gate")
        self.assert_ready_refused(why="regression_case 不是豁免")


# ------------------------------------------------------------------ A5


class A5LandedDependencyRequiresARerun(World):

    def test_a5_dependency_landing_after_plan_requires_a_rerun(self):
        """A5 #2 依賴 #1 landed;#2 在 #1 落地前 plan,#1 之後落地(只動無關檔):#2 轉 Ready 拒,再 plan 才收。

        四問:量 #2 兩次 set state Ready;紅 = 只比輸入檔、不管前置在實跑之後才落地;期望值 = 票面 A5
        「依賴落地後先補跑再 Ready」+ close --landed 的 Done/review.sha 形狀;不測 = PlanningBatch 排程。
        """
        self.draft("1", allowed_write_paths=["lib/*"])
        self.draft("2", depends_on=[{"id": "1", "condition": "landed"}])
        self.plan_ok("2")
        self.main_commit("docs/landed.md", "#1 落地\n", "#1 落地(無關 #2 的輸入)")
        self.make_ticket("1", state="Done", allowed_write_paths=["lib/*"],
                         review={"verdict": "pass", "by": "main", "sha": self.head(),
                                 "note": "落地"})
        self.assert_ready_refused("2", why="前置在實跑之後才落地")
        self.plan_ok("2")
        self.assert_ready_accepted("2", why="前置落地後補跑")


# ------------------------------------------------------------------ A6


class A6LintAndProcedures(World):

    def test_a6_lint_uses_the_same_criterion_as_ready(self):
        """A6 lint:Draft 無 plan_ran → 提 plan_ran;plan 後不提;相關輸入又變 → 又提。

        四問:量三次 lint 輸出;紅 = lint 不看 plan_ran 或判準與 Ready 不同;期望值 = 票面 A6;
        不測 = lint 其他檢查。
        """
        self.draft()
        rc, out = self.lint()
        self.assertIn("plan_ran", out)
        self.assertNotEqual(rc, 0, out)
        self.plan_ok()
        _, out = self.lint()
        self.assertNotIn("plan_ran", out)
        self.main_commit("src/a.txt", "changed\n", "主線動了寫入範圍")
        _, out = self.lint()
        self.assertIn("plan_ran", out)


class A6ProceduresAndRegressionTests(unittest.TestCase):

    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_a6_role_reference_template_and_inbox_name_the_plan_step(self):
        """A6 role-procedures / dispatch-opener 提 `ticket.py plan`;opener.inbox 提 plan_ran;opener.md 角色卡不加。

        四問:量四份文件的字;紅 = 流程沒寫進 reference/template/inbox、或塞進角色卡;期望值 = 票面 A6 原話;
        不測 = 文字怎麼寫。
        """
        for rel in ("docs/reference/role-procedures.md", "templates/dispatch-opener.md"):
            with self.subTest(rel):
                self.assertIn("ticket.py plan", self.read(rel))
        self.assertIn("plan_ran", self.read("memory/role/opener.inbox.md"))
        self.assertNotIn("plan_ran", self.read("memory/role/opener.md"))

    def test_a6_regression_tests_pin_state_and_evidence(self):
        """A6 tests/test_status_67.py 釘 plan_ran、Draft 與「待 plan 實跑」。

        四問:量測試檔的字;紅 = 回歸測試沒跟著釘新狀態與證據;期望值 = 票面 A6;不測 = 那幾條測試綠不綠(閘門的事)。
        """
        body = self.read("tests/test_status_67.py")
        for word in ("plan_ran", "Draft", PENDING):
            with self.subTest(word):
                self.assertIn(word, body)


if __name__ == "__main__":
    unittest.main()
