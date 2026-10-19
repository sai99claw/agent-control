"""#136 只有 complex 票在做完後覆核，simple 與未標票一路不覆核。

## 驗收表(期望值只取 VISION 流程 #9、D-054 Q1 與本票 objective)
A1 | integration | simple 或未標票跑 review / gate / land / close | reviewer 不起，land / close 不因沒有 review 拒收 | VISION #9、D-054 Q1
A1 | integration | complex 票跑 review / land / close                 | review 照派，沒有 pass 時 land / close 仍拒 | D-054 Q1
A1 | integration | complex 票 pass 後改票面且再 commit          | land / close 仍收，review 只留五格             | 本票 objective
A2 | contract    | 對票面指定範圍跑禁用名搜尋                  | 0 行，舊 verifier 與舊事件種類消失             | 票面驗收 2
A3 | contract    | 讀開題、覆核、land 與 schema 規則來源        | 每個指定位置都有 simple / complex 三句原則  | 票面驗收 3
A4 | integration | 帶舊 review 與舊派工格的未標票跑 show / set / close | 指令照常跑，歷史格原樣保留                 | 票面驗收 4
A5 | contract    | 讀 test_plan 點名的舊案例                     | 舊綁定案例已刪，留下的測試可 compile           | 票面驗收 5

## 介面字串
RULE = "開題者每張票標 simple / complex;只有 complex 做完後覆核;沒標 = simple"
REVIEW_KEYS = {verdict, by, sha, note, at}

## 怎麼做假
夾具用 tests/control_harness.Sandbox 的拋棄式真 git repo；reviewer 是既有假程式，
land 的昂貴閣門換成 GATE_STUB_GREEN。不起模型、不綁埠。

## 不做
不改產品碼；不從被測腳本計算期望值；不跑全套或 tag 回歸。
"""
import json
import os
import re
import sys
import unittest

TAGS = ["review-complex-only"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import GATE_STUB_GREEN, REVIEWER_PASS, Sandbox  # noqa: E402

RULE = "開題者每張票標 simple / complex;只有 complex 做完後覆核;沒標 = simple"
REVIEW_KEYS = {"verdict", "by", "sha", "note", "at"}
CLOSABLE = {
    "verify_strings": [{"path": "src/g1", "contains": "做完的那一張"}],
    "verify": {"files": [], "tags": [], "run": "", "notes": "",
               "baseline": {"ok": True, "stage": "check"}},
}
PASSING = """import unittest


class T(unittest.TestCase):
    def test_green(self):
        self.assertEqual(1, 1)
"""


class ReviewWorld(Sandbox):
    gate_stub = GATE_STUB_GREEN

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- fixture reviewer\n")
        self.set_reviewer(REVIEWER_PASS)

    def branch_for(self, ident, name, **fields):
        fields.setdefault("branch", name)
        self.make_ticket(ident, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "ticket #%s" % ident)
        self.git("push", "-q", "origin", "main")
        return self.worktree(name)

    def reviewer_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("reviewer ran")]


class SimpleTicketsNeverAcquireAReview(ReviewWorld):

    def test_a1_simple_and_unmarked_tickets_are_refused_before_the_reviewer_runs(self):
        """A1 simple 與未標票都走既有 refuse 路徑，reviewer 一次都不起。"""
        rows = (("1", {"complexity": "simple"}), ("2", {}))
        done = []
        for ident, extra in rows:
            self.make_ticket(ident, state="InReview", **extra)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "ticket #%s" % ident)
            branch = self.worktree("t%s" % ident)
            self.commit_in(branch, "src/%s" % ident, "ticket %s" % ident)
            done.append(self.run_sh("scripts/review.sh", ident))

        self.assertEqual([row.returncode for row in done], [2, 2],
                         "simple / 未標票不應派 reviewer:\n" +
                         "\n".join(row.stdout + row.stderr for row in done))
        self.assertEqual(self.reviewer_calls(), [])
        self.assertEqual([row["kind"] for row in self.events()].count("review.refused"), 2)
        for ident, _ in rows:
            self.assertIsNone(self.load_ticket(ident).get("review"))

    def test_a1_simple_land_and_unmarked_close_do_not_require_review(self):
        """A1 simple 票經 land 到 Done，未標票 close 也不要 review。"""
        branch = self.branch_for("1", "t1-simple", state="InReview", complexity="simple",
                                 **CLOSABLE)
        self.commit_in(branch, "src/g1", "做完的那一張")
        landed = self.run_sh("scripts/land.sh", "t1-simple")
        self.assertEqual(landed.returncode, 0, landed.stdout + landed.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Done")
        self.assertIsNone(self.load_ticket("1").get("review"))

        self.write("src/unmarked", "未標票已在主線\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "unmarked ticket payload")
        self.make_ticket("2", verify_strings=[{"path": "src/unmarked",
                                                "contains": "未標票已在主線"}],
                         test_evidence=[{"cmd": "gate --branch", "rc": 0}])
        closed = self.ticket("close", "2")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(self.load_ticket("2")["state"], "Done")


class AManualGreenGateDoesNotDispatchSimpleTickets(Sandbox):

    def test_a1_a_green_simple_gate_stops_without_calling_a_reviewer(self):
        """A1 simple 票的手動閣門綠可轉 InReview，但 reviewer 不起。"""
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- fixture reviewer\n")
        self.set_reviewer(REVIEWER_PASS)
        self.make_ticket("7", state="Running", complexity="simple")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "simple ticket #7")
        self.git("branch", "t7")
        self.write("tests/test_zz_green.py", PASSING)

        done = self.run_sh("scripts/gate.sh", "--branch", "--ticket", "7",
                           env=self.env(AC_TEST_HOME=self.home))
        calls = []
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8") as handle:
                calls = [line.strip() for line in handle if line.startswith("reviewer ran")]
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(calls, [], "simple 票閣門綠後不應起 reviewer")
        self.assertEqual(self.load_ticket("7")["state"], "InReview")
        self.assertIsNone(self.load_ticket("7").get("review"))


class ComplexTicketsStillRequireOneReview(ReviewWorld):

    def test_a1_complex_dispatches_and_land_close_still_require_pass(self):
        """A1 complex 仍派 reviewer，review 只有五格，沒有 pass 的 land / close 仍拒。"""
        reviewed = self.branch_for("1", "t1-complex", state="InReview", complexity="complex")
        self.commit_in(reviewed, "src/reviewed", "complex payload")
        review_done = self.run_sh("scripts/review.sh", "1")
        review = self.load_ticket("1").get("review") or {}

        unreviewed = self.branch_for("2", "t2-complex", state="InReview",
                                     complexity="complex", **CLOSABLE)
        self.commit_in(unreviewed, "src/g1", "做完的那一張")
        land_done = self.run_sh("scripts/land.sh", "t2-complex")

        self.write("src/complex-close", "complex close payload\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "complex close payload")
        self.make_ticket("3", complexity="complex",
                         verify_strings=[{"path": "src/complex-close",
                                          "contains": "complex close payload"}],
                         test_evidence=[{"cmd": "gate --branch", "rc": 0}])
        close_done = self.ticket("close", "3")

        self.assertEqual(review_done.returncode, 0, review_done.stdout + review_done.stderr)
        self.assertEqual(set(review), REVIEW_KEYS, review)
        self.assertEqual(review.get("verdict"), "pass")
        self.assertEqual(self.reviewer_calls(), ["reviewer ran 1"])
        self.assertEqual(land_done.returncode, 2, land_done.stdout + land_done.stderr)
        self.assertEqual(close_done.returncode, 1, close_done.stdout + close_done.stderr)

    def test_a1_a_pass_survives_a_ticket_edit_and_a_later_branch_commit(self):
        """A1 complex pass 後改票面又 commit，land / close 仍收。"""
        branch = self.branch_for("1", "t1-complex", state="InReview",
                                 complexity="complex", **CLOSABLE)
        self.commit_in(branch, "src/g1", "做完的那一張")
        self.approve("1", "t1-complex")
        changed = self.ticket("set", "1", "objective", "pass 後改過的票面")
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.commit_in(branch, "src/g2", "pass 後又一個 commit")

        done = self.run_sh("scripts/land.sh", "t1-complex")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Done")


class RemovedReviewBindingContract(unittest.TestCase):

    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def matching_lines(self, pattern, paths):
        found = []
        for rel in paths:
            start = os.path.join(ROOT, rel)
            names = []
            if os.path.isdir(start):
                for parent, dirs, files in os.walk(start):
                    dirs[:] = [name for name in dirs if name != "__pycache__"]
                    names.extend(os.path.join(parent, name) for name in files)
            elif os.path.isfile(start):
                names.append(start)
            for path in names:
                try:
                    with open(path, encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            if pattern.search(line):
                                found.append("%s:%d:%s" %
                                             (os.path.relpath(path, ROOT), number,
                                              line.rstrip()))
                except (UnicodeDecodeError, OSError):
                    continue
        return found

    def test_a2_removed_names_and_artifacts_are_absent_from_the_declared_scope(self):
        """A2 票面禁用名在指定範圍 0 行，舊 verifier 與舊事件種類都消失。"""
        names = [
            "review_" + "request", "review-" + "request", "review-" + "receive",
            "receive_" + "review", "request_" + "review", "request_" + "problem",
            "patch_" + "id", "FACE_" + "FIELDS", "REQUEST_" + "KEYS",
            "REVIEW_" + "DISPATCHER", "bound_" + "to_content", "review." + "stale",
            "review-" + "receipt", "CARRY_" + "REVIEW_FIELDS",
        ]
        pattern = re.compile("|".join(re.escape(name) for name in names))
        paths = ["scripts", "board", "templates", "tests", "verify", "docs/WORKFLOW.md",
                 "docs/ROLES.md", "docs/DISPATCH-TEMPLATE.md", "docs/reference",
                 "docs/FLOW.html", "tickets/SCHEMA.md", "README.md", "CLAUDE.md",
                 "memory/role", "code-map/catalog.json"]
        found = self.matching_lines(pattern, paths)
        self.assertEqual(found, [], "\n".join(found))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "verify", "review_binding")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "verify", "TAGS.d", "98.md")))
        self.assertNotIn("review." + "stale", self.read("scripts/event.py"))

    def test_a3_every_declared_rule_home_states_the_simple_complex_contract(self):
        """A3 開題、覆核、land 與 schema 指定位置都寫明三句原則。"""
        counts = {
            "memory/role/opener.md": 1,
            "templates/dispatch-opener.md": 1,
            "docs/ROLES.md": 2,
            "tickets/SCHEMA.md": 1,
            "docs/reference/role-procedures.md": 2,
        }
        found = {path: self.read(path).count(RULE) for path in counts}
        self.assertEqual(found, counts)

    def test_a5_old_binding_cases_are_removed_and_the_remaining_tests_compile(self):
        """A5 test_plan 點名的舊綁定案例已刪，留下的測試仍可 compile。"""
        removed = {
            "tests/test_review.py": [
                "TheReviewBindsThePatchAndTheFace", "ALateReviewIsStale",
                "TheFaceIsThirteenFields", "TheReceiptIsComparedNotReread",
                "TheReceiveHasASameLockSeam",
            ],
            "tests/test_land.py": ["TheReviewIsBoundToThePatchAndTheFace"],
        }
        found = []
        for path, names in removed.items():
            text = self.read(path)
            found.extend("%s:%s" % (path, name) for name in names if name in text)
        self.assertEqual(found, [], "仍留下的舊案例:" + ", ".join(found))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "verify", "review_binding",
                                                     "test_ticket_98.py")))
        for path in ("tests/test_review.py", "tests/test_land.py", "tests/test_ticket.py",
                     "tests/test_auto_fix.py", "tests/test_gate.py", "tests/test_event.py"):
            compile(self.read(path), path, "exec")


class LegacyTicketFieldsRemainReadable(Sandbox):

    def test_a4_show_set_and_close_preserve_old_review_fields(self):
        """A4 帶歷史 review 與舊派工格的未標票照常 show / set / close。"""
        self.write("src/nav.py", "def legacy_nav():\n    return 42\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "legacy payload on main")
        old_review = {
            "verdict": "pass", "by": "reviewer@history", "sha": "deadbeef",
            "note": "historical record", "at": "2026-01-01T00:00:00+08:00",
            "state_version": 1, "patch_" + "id": "old-patch", "face": "old-face",
        }
        old_dispatch = {
            "run_id": "old-run", "owner_id": "old-owner", "attempt": 1,
            "state_version": 1, "face": "old-face", "patch_" + "id": "old-patch",
            "sha": "deadbeef",
        }
        self.make_ticket("1", verify_strings=[{"path": "src/nav.py",
                                                "contains": "def legacy_nav"}],
                         test_evidence=[{"cmd": "gate --branch", "rc": 0}],
                         review=old_review, **{"review_" + "request": old_dispatch})

        shown = self.ticket("show", "1")
        changed = self.ticket("set", "1", "objective", "legacy ticket edited")
        before_close = self.load_ticket("1")
        closed = self.ticket("close", "1")
        after_close = self.load_ticket("1")

        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertEqual(before_close["review"], old_review)
        self.assertEqual(before_close["review_" + "request"], old_dispatch)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(after_close["state"], "Done")
        self.assertEqual(after_close["review"], old_review)
        self.assertEqual(after_close["review_" + "request"], old_dispatch)
