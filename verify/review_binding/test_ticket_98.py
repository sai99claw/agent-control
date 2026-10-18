"""#98 覆核綁內容(patch_id)與票面(face),拒收遲到的覆核。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / 夾具自己算的 git patch-id 與 SHA256)
A1 | unit | review.sh pass(假 reviewer)              | review.patch_id == 夾具 `git diff <merge-base> t1 \\| git patch-id --stable`;review.face 非空 | 票面 A1、test_plan T1
A1 | unit | 覆核後主線前進、t1 rebase(sha 變、patch 同)  | land 走過覆核那一關(閘門替身被叫到)                  | 票面 A1「同一 patch 重套到新主線仍有效」
A1 | unit | 覆核後只改運行欄(cost/lease/base_sha/gate/branch/workspace/patch_ref) | land 走過覆核那一關 | 票面 A1 + A2 運行欄名單
A1 | unit | 覆核後 amend 改一行                         | land 拒(rc 2、閘門沒跑),stdout 指名 patch_id      | 票面 A1「印差異種類」
A1 | unit | 覆核後逐一改 13 個 face 欄                   | land 拒,stdout 指名 face / 票面                      | 票面 A1 + A2 face 名單
A1 | unit | 舊 review(沒有 patch_id / face)           | 又 commit → 「覆核之後又 commit 過」;改票 → 「覆核之後票被改過」;沒動 → 放行 | 票面 A1「沿用原判準與訊息」;字串取自既有 tests/test_land.py
A2 | unit | review.sh pass;票缺 info_homes / test_plan  | review.face ∈ SHA256(json.dumps(13 欄、缺值填空值、sort_keys))的幾種序列化 | 票面 A2 原話,夾具手算
A3 | unit | 假 reviewer 停在 barrier 時看票與 reports     | 票有 review_request{run_id,owner_id,attempt,state_version,face,patch_id,sha};receipt.request 同值;完成後 request 一字不變、result.accepted=true | 票面 A3 + interface_contracts
A4 | unit | R1 停住 → R2 派且 pass → 放 R1(pass / fail) | R1 rc 4、輸出含 stale;review/objections/state/lease 不動;R1 receipt.accepted=false;無 R1 的 review.pass/fail 事件 | 票面 A4、test_plan T2
A4 | unit | R1 停住 → 改 acceptance / 新 attempt → 放 R1   | 同上(票上沒有 review)                                 | 票面 A4
A5 | unit | 沙盒 ticket.py 的 review_problems / objection_problems 換成哨兵 | land 與 close 說的就是哨兵那一句;回 [] 時沒有 review 也過 | 票面 A5「不各抄判準」
A5 | unit | 票沒有 review、只有 review_request + accepted receipt | land 照拒「沒有 review」                            | 票面 A5「receipt 不能當 review 真相」
A6 | unit | pass 的 result 帶 note + objection_replies=[](票上只有阻擋項、沒有待回應的 note) | rc 0、review 寫進票;票上 objections 一格不動 | 票面 A6 收件接縫;#104 A3 起 objection_replies 恰 {index,verdict,reason},舊式 disposition/reply 的斷言由 #104 接管

## 介面字串(全部出自票面 acceptance / interface_contracts,不從腳本抄)
review.patch_id / review.face(string)                                 ← interface_contracts.review
ticket.review_request = {run_id, owner_id, attempt, state_version, face, patch_id, sha}
reports/t<id>/<run_id>/review-receipt + .json:request={run_id,attempt,state_version,face,patch_id,sha}、result={accepted,reason}
完成遲到 → rc 4、輸出含 "stale"                                       ← 票面 A4
差異種類:patch_id → r"patch[_ -]?id";face → r"face|票面"           ← 票面 A1「印差異種類」(用詞容忍,種類不容忍)
舊判準訊息:「覆核之後又 commit 過」「覆核之後票被改過」「票上沒有 review」 ← 既有 tests/test_land.py

## 怎麼做假(不上真埠、不起真服務、不起真模型)
沙盒借 tests/control_harness.Sandbox(拋棄式真 git repo + 絆線 claude + 拋棄式控制根,AC_* 全清)。
閘門換成 GATE_STUB_GREEN(寫一行 `gate …` 進 AC_TEST_LOG)—— 「走過覆核那一關」= 閘門被叫到。
reviewer 是一支假 sh(`reviewer.command`):`$T98_DIR/hold` 在就把它 mv 走、寫 started、等 release
(barrier;完成順序由測試控制),verdict 讀 held-verdict / verdict 檔。A5 的哨兵是在沙盒那一份
ticket.py 的 `__main__` 之前補一段覆寫(夾具,不是產品碼)。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字(receipt 檔名用拼接)。
A6 的「同鎖擴充接縫」票面沒給函式名或可觀察面,只驗「本票不認 note/objection_replies」。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unittest

TAGS = ["review-binding"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import GATE_STUB_GREEN, Sandbox  # noqa: E402

RECEIPT = "review-receipt" + ".json"     # 拼接:落地的 verify_strings grep 不准命中案例檔
STALE = "stale"
PATCH_KIND = re.compile(r"patch[_ -]?id", re.I)
FACE_KIND = re.compile(r"face|票面", re.I)
REFUSED = "覆核 / 反駁過不了"
OLD_SHA_MSG = "覆核之後又 commit 過"
OLD_VERSION_MSG = "覆核之後票被改過"
NO_REVIEW_MSG = "票上沒有 review"
SENTINEL_REVIEW = "T98-SENTINEL-REVIEW"
SENTINEL_OBJECTION = "T98-SENTINEL-OBJECTION"
REQUEST_KEYS = ("run_id", "attempt", "state_version", "face", "patch_id", "sha")
WAIT = 90

# 票面 A2 的 13 欄與各自的空值(缺值依欄位空值正規化)。
FACE_EMPTY = {"subject": "", "objective": "", "acceptance": [], "in_scope": [],
              "out_of_scope": [], "allowed_write_paths": [], "info_homes": [],
              "needs_verifier": False, "interface_fixed": False, "verify_strings": [],
              "test_plan": "", "depends_on": [], "decision_refs": []}
# 逐一改 face 欄的新值:每一個都跟夾具票面不同。
FACE_CHANGES = {"subject": "改過的標題", "objective": "改過的目標",
                "acceptance": ["會紅的斷言一條", "多一條驗收"],
                "in_scope": ["多一條範圍"], "out_of_scope": ["docs/elsewhere.md"],
                "allowed_write_paths": ["src/*", "lib/*"], "info_homes": ["docs/X.md"],
                "needs_verifier": True, "interface_fixed": True,
                "verify_strings": [{"path": "src/thing.txt", "contains": "x"}],
                "test_plan": "改過的測試計畫", "depends_on": ["7"],
                "decision_refs": ["D-001", "D-999"]}
RUN_1 = "20261017-120000-1"
RUN_2 = "20261017-120000-2"

FAKE_REVIEWER = r'''#!/bin/sh
d=$T98_DIR
echo "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"
cat > /dev/null
verdict=pass
if mv "$d/hold" "$d/holding.$$" 2>/dev/null; then
    cp "$T98_TICKET" "$d/seen-held.json"
    : > "$d/started"
    n=0
    while [ ! -f "$d/release" ] && [ $n -lt 1200 ]; do sleep 0.1; n=$((n+1)); done
    [ -f "$d/held-verdict" ] && verdict=$(cat "$d/held-verdict")
else
    [ -f "$d/verdict" ] && verdict=$(cat "$d/verdict")
fi
echo "# 覆核"
echo "① verdict:$verdict"
[ "$verdict" = fail ] && echo "OBJECTION: blocking 遲到的退回不該進票"
extra=""
[ -f "$d/extra" ] && extra=$(cat "$d/extra")
cat <<EOF

## result

\`\`\`result
{"ticket": "1", "role": "reviewer", "round": 1, "verdict": "$verdict",
 "rc": null, "patch_sha256": null, "gate": null, "mutations": [],
 "objection": null, "excluded": [], "repro": null, "memory": []$extra}
\`\`\`
EOF
'''

SENTINEL_PATCH = '''
_t98_review_problems = review_problems
_t98_objection_problems = objection_problems


def review_problems(ticket, *args, **kwargs):
    if os.environ.get("T98_REVIEW_SENTINEL"):
        return ["%s"]
    if os.environ.get("T98_REVIEW_EMPTY"):
        return []
    return _t98_review_problems(ticket, *args, **kwargs)


def objection_problems(ticket, *args, **kwargs):
    if os.environ.get("T98_OBJECTION_SENTINEL"):
        return ["%s"]
    return _t98_objection_problems(ticket, *args, **kwargs)


''' % (SENTINEL_REVIEW, SENTINEL_OBJECTION)


def face_candidates(ticket):
    """票面 A2:13 欄、缺值填空值、JSON sort_keys 後 SHA256。序列化細節票面沒定,收幾種常見的。"""
    face = {name: ticket[name] if ticket.get(name) is not None else empty
            for name, empty in FACE_EMPTY.items()}
    out = set()
    for ascii_only in (True, False):
        for seps in (None, (",", ":")):
            text = json.dumps(face, sort_keys=True, ensure_ascii=ascii_only, separators=seps)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            out.update((digest, "sha256:" + digest))
    return out


class ReviewBindingBase(Sandbox):
    """票 #1 停在 InReview(13 欄 face 缺 info_homes / test_plan);分支 t1 一個 commit、有 worktree。"""

    gate_stub = GATE_STUB_GREEN

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        self.make_ticket("1", state="InReview", in_scope=["做完某件事"],
                         needs_verifier=False, interface_fixed=False,
                         verify_strings=[], decision_refs=["D-001"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        self.wt = self.worktree("t1")
        self.write(os.path.join("src", "thing.txt"), "第一行\n第二行\n", where=self.wt)
        self.git("add", "-A", cwd=self.wt)
        self.git("commit", "-q", "-m", "#1 的實作", cwd=self.wt)
        self.ctl = os.path.join(self.home, "t98")
        os.makedirs(self.ctl)
        self.set_reviewer(FAKE_REVIEWER)

    # ---------------------------------------------------------------- 夾具動作

    def renv(self, **extra):
        return self.env(T98_DIR=self.ctl, T98_TICKET=os.path.join(self.repo, "tickets", "1.json"),
                        **extra)

    def flag(self, name, text=""):
        with open(os.path.join(self.ctl, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def review(self, run_id):
        return self.run_sh("scripts/review.sh", "1", "--run-id", run_id, env=self.renv())

    def review_async(self, run_id):
        return subprocess.Popen(["sh", os.path.join(self.repo, "scripts", "review.sh"), "1",
                                 "--run-id", run_id], cwd=self.repo, env=self.renv(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def hold_first(self, run_id, verdict="pass"):
        """barrier:R1 起來、停在假 reviewer 裡;回傳行程。started 只有假 reviewer 會寫。"""
        self.flag("hold")
        self.flag("held-verdict", verdict)
        proc = self.review_async(run_id)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.time() + WAIT
        while not os.path.exists(os.path.join(self.ctl, "started")):
            if proc.poll() is not None:
                self.fail("R1 沒停在 reviewer 就結束了(rc=%s):\n%s"
                          % (proc.returncode, proc.stdout.read()))
            if time.time() > deadline:
                self.fail("R1 的假 reviewer %ds 內沒起來" % WAIT)
            time.sleep(0.1)
        return proc

    def release(self, proc):
        self.flag("release")
        out, _ = proc.communicate(timeout=WAIT)
        return proc.returncode, out

    def patch_id(self, branch="t1"):
        base = self.git("merge-base", "main", branch).strip()
        diff = self.git("diff", base, branch)
        done = subprocess.run(["git", "patch-id", "--stable"], input=diff, cwd=self.repo,
                              env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split()[0]

    def edit_ticket(self, **fields):
        """直接改票檔、版本 +1 —— 模擬任何一支腳本的一次存檔。"""
        row = self.load_ticket("1")
        row.update(fields)
        row["state_version"] = int(row["state_version"]) + 1
        self.write(os.path.join("tickets", "1.json"),
                   json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        return row

    def write_legacy_review(self, note):
        """舊式 review(沒有 patch_id / face):綁分支頭 sha 與存檔後的版本,直接寫票檔。"""
        version = int(self.load_ticket("1")["state_version"]) + 1
        self.edit_ticket(review={"verdict": "pass", "by": "main", "note": note,
                                 "sha": self.git("rev-parse", "t1").strip(),
                                 "state_version": version})
        self.assertEqual(self.load_ticket("1")["state_version"], version)

    def land(self, env=None):
        return self.run_sh("scripts/land.sh", "t1", "--no-auto-fix", env=env or self.env())

    def gate_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line for line in handle if line.startswith("gate ")]

    def assert_review_gate_passed(self, done):
        out = done.stdout + done.stderr
        self.assertNotIn(REFUSED, out, out)
        self.assertTrue(self.gate_calls(), "land 沒走到閘門 —— 覆核那一關沒過:\n" + out)

    def assert_refused(self, done):
        out = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, out)
        self.assertIn(REFUSED, out, out)
        self.assertEqual(self.gate_calls(), [], "拒絕要發生在全套之前")
        return out

    def pass_review(self, run_id=RUN_1):
        done = self.review(run_id)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        review = self.load_ticket("1").get("review") or {}
        self.assertEqual(review.get("verdict"), "pass", review)
        return review

    def receipt(self, run_id):
        path = os.path.join(self.repo, "reports", "t1", run_id, RECEIPT)
        self.assertTrue(os.path.exists(path), "沒有 %s" % path)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def events_for(self, run_id, *kinds):
        return [row for row in self.events()
                if row.get("kind") in kinds and str(row.get("run_id") or "") == run_id]


class ReviewBindsPatchIdAndFace(ReviewBindingBase):
    """A1 / A2:pass 記 patch_id 與 face;同 patch 重套、只改運行欄仍有效;內容或票面變了就拒並指名種類。"""

    def test_a1_pass_records_the_stable_patch_id_and_a_face(self):
        """A1 review pass 記 patch_id(git patch-id --stable)與 face。"""
        expected = self.patch_id()
        review = self.pass_review()
        self.assertEqual(review.get("patch_id"), expected,
                         "review.patch_id 要是 merge-base..t1 那份 diff 的 git patch-id --stable")
        self.assertTrue(isinstance(review.get("face"), str) and review.get("face"),
                        "review.face 要是一個非空字串:%r" % review.get("face"))

    def test_a2_face_is_sha256_of_the_13_fields_with_empties_normalised(self):
        """A2 face = 13 欄(缺值填空值)JSON sort_keys 後 SHA256;缺 info_homes / test_plan 等同空值。"""
        before = self.load_ticket("1")
        self.assertNotIn("info_homes", before)
        self.assertNotIn("test_plan", before)
        review = self.pass_review()
        self.assertIn(review.get("face"), face_candidates(self.load_ticket("1")),
                      "face 與夾具手算的 SHA256 對不上:%r" % review.get("face"))

    def test_a1_same_patch_rebased_onto_a_newer_main_still_lands(self):
        """A1 同一 patch 重套到新主線(sha 變、patch_id 不變)review 仍有效。"""
        self.pass_review()
        old_tip = self.git("rev-parse", "t1").strip()
        old_id = self.patch_id()
        self.write("other.txt", "主線往前走\n")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "主線上別人的 commit")
        self.git("push", "-q", "origin", "main")
        self.git("rebase", "-q", "main", cwd=self.wt)
        self.assertNotEqual(self.git("rev-parse", "t1").strip(), old_tip, "夾具:rebase 要換 sha")
        self.assertEqual(self.patch_id(), old_id, "夾具:rebase 不改 patch_id")
        self.assert_review_gate_passed(self.land())

    def test_a1_runtime_only_edits_keep_the_review_valid(self):
        """A1 只改運行欄(cost/lease/base_sha/gate/branch/workspace/patch_ref)review 仍有效。"""
        self.pass_review()
        tip = self.git("rev-parse", "t1").strip()
        self.write("other.txt", "主線往前走\n")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "主線上別人的 commit")
        self.git("push", "-q", "origin", "main")
        new_main = self.git("rev-parse", "main").strip()
        row = self.load_ticket("1")
        self.edit_ticket(cost=list(row.get("cost") or []) + [{"role": "land", "round": 1}])
        self.edit_ticket(lease={"holder": "someone", "role": "land",
                                "expires": "2026-10-17T13:00:00+08:00"})
        self.edit_ticket(base_sha=new_main)
        self.edit_ticket(gate={"sha": tip, "rc": 0, "run_id": "20261017-130000-9"})
        self.edit_ticket(branch="t1")
        self.edit_ticket(workspace=os.path.join(self.home, "elsewhere"))
        self.edit_ticket(patch_ref="reports/t1/20261017-130000-9/patch.diff")
        self.edit_ticket(lease=None)
        self.assert_review_gate_passed(self.land())

    def test_a1_a_changed_line_is_refused_naming_patch_id(self):
        """A1 patch_id 不同(改一行)→ land 拒,印差異種類 patch_id。"""
        self.pass_review()
        self.write(os.path.join("src", "thing.txt"), "第一行\n第二行改了\n", where=self.wt)
        self.git("add", "-A", cwd=self.wt)
        self.git("commit", "-q", "--amend", "--no-edit", cwd=self.wt)
        out = self.assert_refused(self.land())
        self.assertRegex(out, PATCH_KIND, "拒絕要指名是 patch_id 不同")

    def test_a1_each_face_field_change_is_refused_naming_face(self):
        """A1 / A2 票面指紋不同(13 欄逐一改)→ land 拒,印差異種類 face。"""
        self.pass_review()
        snapshot = self.read(os.path.join("tickets", "1.json"))
        for field, value in FACE_CHANGES.items():
            with self.subTest(field=field):
                self.write(os.path.join("tickets", "1.json"), snapshot)
                if os.path.exists(self.log):
                    os.remove(self.log)
                self.edit_ticket(**{field: value})
                out = self.assert_refused(self.land())
                self.assertRegex(out, FACE_KIND, "改 %s 的拒絕要指名是 face 不同" % field)


class LegacyReviewKeepsOldRules(ReviewBindingBase):
    """A1 舊 review 缺新鍵:沿用原 sha / state_version 判準與訊息。"""

    def legacy(self):
        self.write_legacy_review("舊式覆核")

    def test_a1_legacy_review_still_refuses_a_new_commit_with_the_old_message(self):
        """A1 舊 review:蓋章後又 commit → 原訊息「覆核之後又 commit 過」。"""
        self.legacy()
        self.commit_in(self.wt, os.path.join("src", "more.txt"), "蓋章之後又改的")
        out = self.assert_refused(self.land())
        self.assertIn(OLD_SHA_MSG, out)

    def test_a1_legacy_review_still_refuses_a_ticket_edit_with_the_old_message(self):
        """A1 舊 review:蓋章後改票 → 原訊息「覆核之後票被改過」。"""
        self.legacy()
        self.assertEqual(self.ticket("set", "1", "objective", "蓋章之後改的").returncode, 0)
        out = self.assert_refused(self.land())
        self.assertIn(OLD_VERSION_MSG, out)

    def test_a1_legacy_review_untouched_still_lands(self):
        """A1 舊 review 沒過期 → 照舊放行。"""
        self.legacy()
        self.assert_review_gate_passed(self.land())


class ReceiptIsCapturedAtDispatch(ReviewBindingBase):
    """A3 派覆核當下存 review_request 與不可變 receipt;完成時一次寫 review。"""

    def test_a3_dispatch_captures_request_and_receipt_and_completion_accepts_it(self):
        """A3 派工當下票有 review_request、reports 有 receipt.request;完成後 request 不變、accepted=true。"""
        expected_patch = self.patch_id()
        expected_sha = self.git("rev-parse", "t1").strip()
        proc = self.hold_first(RUN_1)
        with open(os.path.join(self.ctl, "seen-held.json"), encoding="utf-8") as handle:
            seen = json.load(handle)
        request = seen.get("review_request")
        self.assertIsInstance(request, dict, "派工當下票上沒有 review_request")
        self.assertEqual(request.get("run_id"), RUN_1)
        self.assertEqual(str(request.get("attempt")), str(seen.get("attempt")))
        self.assertEqual(str(request.get("state_version")), str(seen.get("state_version")),
                         "request 記的是派工存檔後的版本,完成時才比得上")
        self.assertEqual(request.get("patch_id"), expected_patch)
        self.assertEqual(request.get("sha"), expected_sha)
        self.assertIn(request.get("face"), face_candidates(seen))
        self.assertTrue(isinstance(request.get("owner_id"), str) and request.get("owner_id"),
                        "review_request.owner_id 要是派工 owner")
        during = self.receipt(RUN_1)
        self.assertEqual({key: (during.get("request") or {}).get(key) for key in REQUEST_KEYS},
                         {key: request.get(key) for key in REQUEST_KEYS},
                         "receipt.request 是 review_request 的不可變副本")

        rc, out = self.release(proc)
        self.assertEqual(rc, 0, out)
        after = self.receipt(RUN_1)
        self.assertEqual(after.get("request"), during.get("request"), "receipt.request 完成後被改了")
        self.assertIs((after.get("result") or {}).get("accepted"), True, after)
        review = self.load_ticket("1").get("review") or {}
        self.assertEqual(review.get("verdict"), "pass")
        self.assertEqual(review.get("patch_id"), request.get("patch_id"))
        self.assertEqual(review.get("face"), request.get("face"))
        self.assertEqual(review.get("sha"), request.get("sha"))


class LateReviewIsStale(ReviewBindingBase):
    """A4 舊 reviewer 在新覆核 / 新 attempt / 改票之後交件:rc 4 stale,票一格不動。"""

    def assert_stale(self, rc, out, before):
        self.assertEqual(rc, 4, "遲到的覆核要回 rc=4:\n" + out)
        self.assertIn(STALE, out.lower(), out)
        after = self.load_ticket("1")
        for field in ("review", "objections", "state", "lease"):
            self.assertEqual(after.get(field), before.get(field), "遲到的覆核改了 %s" % field)
        receipt = self.receipt(RUN_1)
        self.assertEqual((receipt.get("request") or {}).get("run_id"), RUN_1)
        self.assertIs((receipt.get("result") or {}).get("accepted"), False, receipt)
        self.assertEqual(self.events_for(RUN_1, "review.pass", "review.fail"), [],
                         "遲到的覆核只留觀測事件,不發 review.pass / review.fail")

    def second_review_passes(self):
        done = self.review(RUN_2)
        self.assertEqual(done.returncode, 0, "R2 要能在 R1 未完成時派且通過:\n"
                         + done.stdout + done.stderr)
        review = self.load_ticket("1").get("review") or {}
        self.assertIn(RUN_2, review.get("note") or "", "夾具:票上的 review 該是 R2 的")
        return self.load_ticket("1")

    def test_a4_old_pass_after_a_newer_review_is_stale(self):
        """A4 新覆核之後舊 reviewer 交 pass → rc 4 stale,不覆蓋新 review。"""
        proc = self.hold_first(RUN_1, "pass")
        before = self.second_review_passes()
        rc, out = self.release(proc)
        self.assert_stale(rc, out, before)

    def test_a4_old_fail_after_a_newer_review_is_stale(self):
        """A4 新覆核之後舊 reviewer 交 fail → rc 4 stale,不加反駁、不轉 Blocked。"""
        proc = self.hold_first(RUN_1, "fail")
        before = self.second_review_passes()
        rc, out = self.release(proc)
        self.assert_stale(rc, out, before)

    def test_a4_old_pass_after_the_ticket_face_changed_is_stale(self):
        """A4 改票(acceptance)之後舊 reviewer 交 pass → rc 4 stale,不替新票面蓋章。"""
        proc = self.hold_first(RUN_1, "pass")
        done = self.ticket("set", "1", "acceptance", json.dumps(["改過的驗收"], ensure_ascii=False))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")
        rc, out = self.release(proc)
        self.assert_stale(rc, out, before)

    def test_a4_old_pass_after_a_new_attempt_is_stale(self):
        """A4 新 attempt 之後舊 reviewer 交 pass → rc 4 stale。"""
        proc = self.hold_first(RUN_1, "pass")
        row = self.load_ticket("1")
        before = self.edit_ticket(attempt=int(row.get("attempt") or 0) + 1)
        rc, out = self.release(proc)
        self.assert_stale(rc, out, before)


class LandAndCloseAskTicketPy(ReviewBindingBase):
    """A5 land 與 close 只問 ticket.py 的 review_problems / objection_problems。"""

    def setUp(self):
        super().setUp()
        path = os.path.join(self.repo, "scripts", "ticket.py")
        text = self.read(path)
        guard = '\nif __name__ == "__main__":'
        self.assertIn(guard, text, "夾具:ticket.py 找不到 __main__ 那一行")
        self.write(path, text.replace(guard, "\n" + SENTINEL_PATCH + guard.lstrip("\n"), 1))

    def approve_head(self):
        self.write_legacy_review("沙盒的覆核")

    def test_a5_land_reports_what_review_problems_says(self):
        """A5 land 的覆核判準就是 ticket.py review_problems 的回答(哨兵)。"""
        self.approve_head()
        out = self.assert_refused(self.land(env=self.env(T98_REVIEW_SENTINEL="1")))
        self.assertIn(SENTINEL_REVIEW, out)

    def test_a5_land_has_no_review_rule_of_its_own(self):
        """A5 review_problems 說沒問題,land 不另抄判準(沒有 review 也走得到閘門)。"""
        self.assertNotIn("review", self.load_ticket("1"))
        self.assert_review_gate_passed(self.land(env=self.env(T98_REVIEW_EMPTY="1")))

    def test_a5_land_reports_what_objection_problems_says(self):
        """A5 land 的反駁判準就是 ticket.py objection_problems 的回答(哨兵)。"""
        self.approve_head()
        out = self.assert_refused(self.land(env=self.env(T98_OBJECTION_SENTINEL="1")))
        self.assertIn(SENTINEL_OBJECTION, out)

    def test_a5_close_reports_what_review_problems_says(self):
        """A5 ticket close 的覆核判準就是 review_problems 的回答(哨兵)。"""
        self.edit_ticket(verify_strings=[{"path": "src/thing.txt", "contains": "第一行"}])
        self.approve_head()
        self.git("merge", "-q", "--no-ff", "t1", "-m", "夾具:t1 進主線,close 才問得到覆核")
        main = self.git("rev-parse", "main").strip()
        done = self.ticket("close", "1", "--landed", main, env=self.env(T98_REVIEW_SENTINEL="1"))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(SENTINEL_REVIEW, done.stdout + done.stderr)

    def test_a5_an_accepted_receipt_is_not_a_review(self):
        """A5 receipt 只用於收件:票沒有 review、只有 request + accepted receipt → land 照拒。"""
        sha = self.git("rev-parse", "t1").strip()
        request = {"run_id": RUN_1, "owner_id": "review.sh", "attempt": 1, "state_version": 2,
                   "face": "0" * 64, "patch_id": self.patch_id(), "sha": sha}
        self.edit_ticket(review_request=request)
        self.write(os.path.join("reports", "t1", RUN_1, RECEIPT), json.dumps(
            {"request": {key: request[key] for key in REQUEST_KEYS},
             "result": {"accepted": True, "reason": "夾具"}}))
        out = self.assert_refused(self.land())
        self.assertIn(NO_REVIEW_MSG, out)


class NoNoteOrRepliesYet(ReviewBindingBase):
    """A6 #98 的收件接縫:note 處置由 #104 接(A3 的 objection_replies 形狀與 rc=3 歸 #104 的案例)。"""

    def test_a6_objection_replies_in_a_pass_do_not_touch_objections(self):
        """A6 pass 的 result 帶 note 與空的 objection_replies(票上沒有待回應的 note),
        review 照收、票上的阻擋項一格不動。"""
        rows = [{"category": "blocking", "owner": "main", "body": "還沒處置的阻擋",
                 "evidence": "EVIDENCE.md:1", "disposition": ""}]
        done = self.ticket("set", "1", "objections", json.dumps(rows, ensure_ascii=False))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.load_ticket("1")["objections"]
        self.flag("extra", ', "note": "收件的備註", "objection_replies": []')
        done = self.review(RUN_1)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(ticket["objections"], before)
        self.assertEqual((ticket.get("review") or {}).get("verdict"), "pass")


if __name__ == "__main__":
    unittest.main()
