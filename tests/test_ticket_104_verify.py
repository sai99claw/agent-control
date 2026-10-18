"""#104 反駁分流、逐條覆核與主線 overrule 的獨立驗收。

## 驗收表(期望值來源獨立於被測程式)
A1 | auto-fix | 合格 note 自動續走；缺 patch／產品取捨停下 | 分流依票面 A1 | #104 A1
A2 | apply | note 只記一次且 rc!=6；阻擋分類保留 | 收件依票面 A2 | #104 A2
A3 | review | 逐條 reply 必須完整唯一合法 | rc/disposition 依票面 A3 | #104 A3
A4 | review CAS | 遲到回報不處置新反駁；未處置 note 擋落地 | 依票面 A4 | #104 A4
A5 | ticket CLI | overrule 有版本守衛並恢復阻擋；通過只發事件 | 依票面 A5 | #104 A5

## 介面字串
`OBJECTION: note`、`result.objection_replies`、`ticket.py objection-overrule`。

## 怎麼做假
沿用 control_harness 的拋棄式 repo、假 provider 與自動清理；不呼叫真 LLM、不碰正式票庫。

## 不做
不改產品碼、不跑全套、不接受由受測程式反算的期望值、不使用固定服務埠。
"""

import glob
import json
import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import control_harness  # noqa: E402
import test_apply  # noqa: E402
import test_objection_triage  # noqa: E402
import test_review  # noqa: E402

sys.path.insert(0, control_harness.SCRIPTS)
import ticket as ticket_mod  # noqa: E402

TAGS = []

NOTE = "OBJECTION: note 實作符合原驗收，補充說明不影響產品行為"
PRODUCT_CHOICE = "OBJECTION: note 請把原驗收改成允許略過失敗項目"


@contextmanager
def sandbox(case_type):
    case = case_type(methodName="runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def notes(*bodies):
    return [{"category": "note", "body": body, "evidence": "EVIDENCE.md",
             "owner": "worker@fixture-model", "blocking": False,
             "disposition": "", "follow_up": ""}
            for body in bodies]


def reviewer_result(replies, ticket="1", before=""):
    result = {
        "ticket": ticket,
        "role": "reviewer",
        "round": 1,
        "verdict": "pass",
        "rc": 0,
        "patch_sha256": None,
        "gate": None,
        "mutations": [],
        "objection": None,
        "objection_replies": replies,
        "excluded": [],
        "repro": None,
        "memory": [],
    }
    return ("#!/bin/sh\ncat > /dev/null\n%s\ncat <<'EOF'\n"
            "# 覆核\n\n## result\n\n```result\n%s\n```\nEOF\n"
            % (before, json.dumps(result, ensure_ascii=False)))


def arm_note_worker(case, body, with_patch):
    mode = "note" if with_patch else "object"
    case.arm(worker_first=mode)
    case.scenario["objection"] = body
    case.save_scenario()
    if not with_patch:
        return
    path = os.path.join(case.bindir, "claude")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    old = '        write(evidence, "# 第 %s 輪(%s)\\n已排除的假設:沒有\\n" % (r, mode))'
    new = ('        write(evidence, ((plan["objection"] + "\\n") if mode == "note" '
           'else "# 第 %s 輪(%s)\\n已排除的假設:沒有\\n" % (r, mode)))')
    case.assertIn(old, text)
    control_harness.write_executable(path, text.replace(old, new, 1))


def note_evidence(body=NOTE):
    return """# EVIDENCE

%s

## result

```result
{"ticket": "1", "role": "worker", "round": 1, "rc": 0,
 "patch_sha256": null,
 "gate": {"cmd": "python3 -m unittest tests.test_thing", "ran": 1, "rc": 0},
 "mutations": [], "objection": {"category": "note", "body": %s},
 "excluded": [], "repro": null, "memory": []}
```
""" % (body, json.dumps(body.split(" ", 2)[-1], ensure_ascii=False))


class Ticket104Verifier(unittest.TestCase):

    def test_a1_only_a_note_with_a_patch_that_keeps_acceptance_is_non_blocking(self):
        """A1 合格 note 自動續走；缺 patch 或要求產品取捨一律阻擋。"""
        # 四問:守 note 分流；回歸是只看 category 或只看 patch；既有案例沒有三種輸入同測；不需測試縫。
        with sandbox(test_objection_triage.TriageBase) as case:
            arm_note_worker(case, NOTE, with_patch=True)
            case.set_reviewer(reviewer_result([
                {"index": 0, "verdict": "agree", "reason": "說明不改變原驗收"}
            ]))
            done = case.auto_fix()
            case.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            row = case.load_ticket("1")
            case.assertEqual(len(row.get("objections") or []), 1)
            case.assertEqual((row["objections"][0].get("category"),
                              row["objections"][0].get("blocking")), ("note", False))
            case.assertEqual(case.calls("opener"), [], "合格 note 不起 opener")

        for body, with_patch in ((NOTE, False), (PRODUCT_CHOICE, True)):
            with self.subTest(body=body, with_patch=with_patch):
                with sandbox(test_objection_triage.TriageBase) as case:
                    arm_note_worker(case, body, with_patch=with_patch)
                    case.play(opener="accept", worker_resume="fix")
                    done = case.auto_fix()
                    row = case.load_ticket("1")
                    case.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                    case.assertEqual(row.get("state"), "Blocked")
                    case.assertTrue(row["objections"][0].get("blocking"))
                    case.assertIn("decision.asked", case.kinds())
                    case.assertFalse(any(call.get("resumed") for call in case.calls("worker")),
                                     "opener 可分析，但不能自行 accepted 後放行")

    def test_a2_apply_records_a_qualified_note_once_without_rc6_and_preserves_real_objections(self):
        """A2 note 收件不回 rc=6；真正反駁保留分類、owner 與阻擋語意。"""
        # 四問:守 apply 的 note/阻擋分流；回歸是所有 OBJECTION 都 rc=6；既有 apply 案例沒有 note；不需測試縫。
        with sandbox(test_apply.ApplyBase) as case:
            case.make("1")
            evidence = case.patch_file("EVIDENCE-note.md", note_evidence())
            first = case.apply("1", case.patch_file("note.diff", test_apply.CHANGE),
                               "--evidence", evidence)
            case.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            rows = case.load_ticket("1").get("objections") or []
            case.assertEqual(len(rows), 1)
            case.assertEqual((rows[0].get("category"), rows[0].get("blocking")),
                             ("note", False))

            second = case.apply("1", case.patch_file("note-2.diff", test_apply.CREATE),
                                "--evidence", evidence)
            case.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            case.assertEqual(len(case.load_ticket("1").get("objections") or []), 1)

        for category, owner in (("ticket-wrong", "main"), ("blocking", "main"),
                                ("test_defect", "verifier")):
            with self.subTest(category=category):
                with sandbox(test_apply.ApplyBase) as case:
                    case.make("1")
                    body = "OBJECTION: %s 這一筆必須停下處置" % category
                    evidence = case.patch_file("EVIDENCE.md", note_evidence(body))
                    done = case.apply("1", case.patch_file("p.diff", test_apply.CHANGE),
                                      "--evidence", evidence)
                    case.assertEqual(done.returncode, 6, done.stdout + done.stderr)
                    row = case.load_ticket("1")["objections"][0]
                    case.assertEqual((row.get("category"), row.get("owner")), (category, owner))
                    case.assertNotEqual(row.get("blocking"), False)

    def test_a3_review_replies_are_complete_unique_valid_and_drive_each_disposition(self):
        """A3 reply 漏項、重複、非法值一律拒收；agree/disagree 逐筆生效。"""
        # 四問:守 reviewer 逐條回應；回歸是漏回 note 仍 pass；既有 review 只守整體 verdict；不需測試縫。
        invalid = (
            [{"index": 0, "verdict": "agree", "reason": "只回第一筆"}],
            [{"index": 0, "verdict": "agree", "reason": "第一次"},
             {"index": 0, "verdict": "agree", "reason": "重複"},
             {"index": 1, "verdict": "agree", "reason": "第二筆"}],
            [{"index": 0, "verdict": "maybe", "reason": "非法 verdict"},
             {"index": 1, "verdict": "agree", "reason": "第二筆"}],
            [{"index": 0, "verdict": "agree", "reason": ""},
             {"index": 1, "verdict": "agree", "reason": "第二筆"}],
        )
        for number, replies in enumerate(invalid):
            with self.subTest(invalid=number):
                with sandbox(test_review.ReviewBase) as case:
                    original = notes("第一筆 note", "第二筆 note")
                    case.ticket("set", "1", "objections",
                                json.dumps(original, ensure_ascii=False))
                    case.set_reviewer(reviewer_result(replies))
                    done = case.review()
                    case.assertEqual(done.returncode, 3, done.stdout + done.stderr)
                    row = case.load_ticket("1")
                    case.assertNotIn("review", row)
                    case.assertEqual(row["objections"], original)
                    case.assertIn("review.refused", case.kinds())

        with sandbox(test_review.ReviewBase) as case:
            case.ticket("set", "1", "objections",
                        json.dumps(notes("第一筆 note", "第二筆 note"), ensure_ascii=False))
            case.set_reviewer(reviewer_result([
                {"index": 0, "verdict": "agree", "reason": "符合原驗收"},
                {"index": 1, "verdict": "agree", "reason": "只是補充證據"},
            ]))
            done = case.review()
            case.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            row = case.load_ticket("1")
            case.assertEqual([item.get("disposition") for item in row["objections"]],
                             ["accepted", "accepted"])
            case.assertEqual(row.get("review", {}).get("verdict"), "pass")

        with sandbox(test_review.ReviewBase) as case:
            case.ticket("set", "1", "objections",
                        json.dumps(notes("第一筆 note", "第二筆 note"), ensure_ascii=False))
            case.set_reviewer(reviewer_result([
                {"index": 0, "verdict": "agree", "reason": "符合原驗收"},
                {"index": 1, "verdict": "disagree", "reason": "其實會改產品取捨"},
            ]))
            done = case.review()
            case.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
            row = case.load_ticket("1")
            case.assertEqual(row.get("state"), "Blocked")
            case.assertTrue(row["objections"][1].get("blocking"))
            case.assertIn("decision.asked", case.kinds())

    def test_a4_review_and_note_dispositions_share_one_cas_and_unresolved_notes_block_land(self):
        """A4 遲到覆核不處置新反駁；review 與 dispositions 同一個 CAS。"""
        # 四問:守覆核收件單 CAS 與共用 land/close 守衛；回歸是舊回報吃掉新 note；既有 CAS 不含 replies；不需測試縫。
        pending = notes("派覆核前的 note")
        later = notes("派覆核前的 note", "派工後新增的 note")
        command = ("python3 \"$AC_CONTROL_DIR/ticket.py\" set \"$AC_TICKET\" objections "
                   "'%s' >/dev/null" % json.dumps(later, ensure_ascii=False))
        with sandbox(test_review.ReviewBase) as case:
            case.ticket("set", "1", "objections", json.dumps(pending, ensure_ascii=False))
            case.set_reviewer(reviewer_result([
                {"index": 0, "verdict": "agree", "reason": "舊回報只看過第一筆"}
            ], before=command))
            done = case.review()
            case.assertEqual(done.returncode, 4, done.stdout + done.stderr)
            row = case.load_ticket("1")
            case.assertNotIn("review", row)
            case.assertEqual(row["objections"], later)
            receipts = glob.glob(os.path.join(case.repo, "reports", "t1", "*",
                                               "review-receipt.json"))
            case.assertEqual(len(receipts), 1)
            with open(receipts[0], encoding="utf-8") as handle:
                receipt = json.load(handle)
            case.assertFalse(receipt["result"]["accepted"])

        unresolved = {"objections": notes("尚未處置的 note")}
        accepted = {"objections": notes("已同意的 note")}
        accepted["objections"][0]["disposition"] = "accepted"
        self.assertTrue(ticket_mod.objection_problems(unresolved),
                        "land/close 的共用守衛必須擋未處置 note")
        self.assertEqual(ticket_mod.objection_problems(accepted), [])

    def test_a5_main_can_overrule_an_accepted_note_with_version_guards_and_no_done_page(self):
        """A5 overrule 原子退回 accepted note；錯版本不動票；覆核通過只發事件。"""
        # 四問:守主線 overrule 與中途通知；回歸是無版本守衛或濫發 done 頁；既有 CLI/通知案例未含 note；不需測試縫。
        with sandbox(control_harness.Sandbox) as case:
            item = notes("主線重新判定的 note")[0]
            item["disposition"] = "accepted"
            case.make_ticket("1", attempt=3, state="InReview", objections=[item])
            before = case.load_ticket("1")
            done = case.ticket("objection-overrule", "1", "--index", "0",
                               "--expect-attempt", str(before["attempt"]),
                               "--expect-state-version", str(before["state_version"]))
            case.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            row = case.load_ticket("1")
            changed = row["objections"][0]
            case.assertEqual((changed.get("blocking"), changed.get("disposition"),
                              changed.get("overruled_by"), row.get("state")),
                             (True, "", "main", "Blocked"))
            case.assertTrue(ticket_mod.objection_problems(row))

        with sandbox(control_harness.Sandbox) as case:
            item = notes("錯版本不可改的 note")[0]
            item["disposition"] = "accepted"
            case.make_ticket("1", attempt=3, state="InReview", objections=[item])
            before_text = case.read("tickets/1.json")
            row = case.load_ticket("1")
            done = case.ticket("objection-overrule", "1", "--index", "0",
                               "--expect-attempt", str(row["attempt"]),
                               "--expect-state-version", str(row["state_version"] + 1))
            case.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
            case.assertEqual(case.read("tickets/1.json"), before_text)

        with sandbox(test_review.ReviewBase) as case:
            case.ticket("set", "1", "objections",
                        json.dumps(notes("覆核同意的 note"), ensure_ascii=False))
            case.set_reviewer(reviewer_result([
                {"index": 0, "verdict": "agree", "reason": "符合原驗收"}
            ]))
            done = case.review()
            case.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            case.assertIn("review.pass", case.kinds())
            case.assertEqual(case.inbox_rows(), [], "中途 note 通過不發整票 done 頁")


if __name__ == "__main__":
    unittest.main()
