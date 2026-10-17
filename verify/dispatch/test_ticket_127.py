"""#127 越界守衛認得同時的落地與 docs 通道:land.* 事件的識別、逐 commit 歸屬、往回找 land.start。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 案例自己在 git 上取的 sha)
A1 | integ | 真跑沙盒 land.sh:一次單票綠 land;docs 通道有 commit / 已在主線上 / git add 失敗 / push 失敗 | 票 land 的 land.pass 帶 40 位前 main、沒有 channel 鍵;docs 每筆同一 stamp(docs-YYYYmmdd-HHMMSS-pid)、channel docs,有 commit 的 pass 帶 sha 與前 main,已在主線上那筆兩者都沒有 | 票面 A1 原話;前 main 由案例跑之前 `git rev-parse main`
A2 | integ | auto-fix 起跑前 emit land.start S1,agent 期間 commit + land.pass S1(sha 新、前 main 舊) | rc 0、沒有 guard.json、沒有 guard-violation;start 在 pass 之後才發 → rc 8 | 票面 A2 原話(「在那一筆 pass 之前的列」)
A3 | integ | agent 期間真跑 `AC_ROOT=$REPO sh $REPO/scripts/land.sh docs …`;docs 一次再接一次票 land | 都 rc 0、沒有 guard.json;同一段再多一個工作樹檔 → guard.json 只指那個檔、notes 列得出兩次落地的 stamp | 票面 A3 原話 + objective「notes 逐次列 stamp」
A4 | integ | agent 期間先裸 commit X,再一次合法 land(前 main = X) | rc 8 一整組;guard.json 有 path=HEAD、before 舊、after 新、unattributed = X 40 位;decision 頁有 X 前 7 位 | 票面 A4 原話;X 由假 agent 在 git 上取
A5 | integ | (a) start / pass 都沒 stamp (b) 沒有前 main (c) 整檔找不到同 stamp 的 start (d) 前 main = 舊 HEAD 的父 | 四種都 rc 8 | 票面 A5 原話
A6 | integ | 新形狀的合法 land + 工作樹多一個檔 | rc 8 指那個檔、沒有 HEAD 那一列;#115 的其餘案例在 verify/dispatch/test_ticket_115.py(本票只補它的夾具) | 票面 A6 原話
A7 | doc   | 讀 docs/WORKFLOW.md §8 守衛那一條、auto-fix.sh 守衛檔頭註解 | 兩處都寫得出新規則的鍵,舊句「pass 的 sha 就是新 HEAD」不在 | 票面 A7 原話

## 介面字串(全部出自票面 acceptance;票面 verify_strings 的原字不在本檔出現 —— gate 比的是 patch 內文)
BEFORE_KEY = "main" + "_before"        ← A1 鍵名
CHANNEL    = ("channel", "docs")       ← A1(b)
DOCS_STAMP = ^docs-\\d{8}-\\d{6}-\\d+$  ← A1(b) 形狀
UNATTRIB   = "unattributed"            ← A4 guard.json 欄位
GUARD_RC   = 8 / REASON = "guard-violation"(沿用 #115)

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
A1 借 tests/test_land.LandBase(拋棄式真 repo + origin、假閘門),真跑沙盒裡的 land.sh。
A2–A6 借 verify/dispatch/test_ticket_115.GuardBase(真 auto-fix + 真 git、config worker.command 指向假 sh);
越界 / 落地動作由假 agent 在交件前做,land.* 事件以 `AC_ROOT=$REPO event.py emit` 發(agent 的 AC_ROOT 指副本)。
期望的 sha 一律由假 agent 當場 `git rev-parse` 寫進沙盒的家,不從守衛算回去。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;
「notes 兩筆」只在有 guard.json 時看得到(不擋時票面沒說 notes 落在哪),所以 A3 的 notes 用多一個工作樹檔的那一案驗。
"""
import glob
import json
import os
import re
import subprocess
import sys
import unittest

TAGS = ["worker-guard"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, HERE)
from test_land import LandBase  # noqa: E402
from test_ticket_115 import GUARD_RC, REASON, GuardBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
BEFORE_KEY = "main" + "_before"
CHANNEL_KEY, CHANNEL_DOCS = "channel", "docs"
DOCS_STAMP = r"^docs-\d{8}-\d{6}-\d+$"
UNATTRIB = "unattributed"
LAND_KINDS = ("land.start", "land.refused", "land.fail", "land.pass")


def shell(text):
    """假 agent 的片段:`@KB@` 換成鍵名(verify_strings 的原字不進本檔)。"""
    return text.replace("@KB@", BEFORE_KEY)


def sha_file(home, name):
    with open(os.path.join(home, name), encoding="utf-8") as handle:
        return handle.read().strip()


# 一次合法的票 land:同 stamp 的 start + pass,pass 帶 sha(短)= 新 HEAD、前 main = 這一段的起點。
LEGAL_LAND = shell("""emit land.start --note "同時的落地" --kv stamp=@STAMP@
LB="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" commit -q --allow-empty -m "同時的落地 @STAMP@"
emit land.pass --note "同時的落地" --kv stamp=@STAMP@ --kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$LB"
""")

# docs 通道真跑沙盒裡的 land.sh;AC_ROOT 要指主 repo,不然事件寫進副本、守衛看不到。
DOCS_LAND = """mkdir -p "$REPO/docs"
echo "t127 docs note" > "$REPO/docs/t127-note.md"
AC_ROOT="$REPO" sh "$REPO/scripts/land.sh" docs "docs: t127 note" docs/t127-note.md > "$SANDBOX/docs-land.out" 2>&1
"""


def legal_land(stamp):
    return LEGAL_LAND.replace("@STAMP@", stamp)


class EventShapesFromLandSh(LandBase):
    """A1:land.sh 兩條路發的 land.* 事件形狀(真跑沙盒 land.sh,讀 board/events.jsonl)。"""

    def land_rows_since(self, count):
        return [row for row in self.events()[count:] if str(row.get("kind")).startswith("land.")]

    def docs(self, *args):
        return self.run_sh("scripts/land.sh", "docs", *args)

    def assert_docs_rows(self, rows, kinds):
        self.assertEqual([row["kind"] for row in rows], list(kinds), rows)
        stamps = {row.get("stamp") for row in rows}
        self.assertEqual(len(stamps), 1, "一次呼叫一個 stamp:%s" % rows)
        stamp = stamps.pop()
        self.assertRegex(str(stamp), DOCS_STAMP)
        for row in rows:
            self.assertEqual(row.get(CHANNEL_KEY), CHANNEL_DOCS, row)
        return stamp

    def test_a1a_ticket_land_pass_carries_the_prior_main_and_no_channel(self):
        """A1 (a) 單票綠 land 的 land.pass 帶前 main 40 位(= 跑之前 main)、stamp、短 sha,沒有 channel 鍵。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        before = self.git("rev-parse", "main").strip()
        count = len(self.events())
        done = self.run_sh("scripts/land.sh", "t1-good")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = self.git("rev-parse", "main").strip()
        passes = [row for row in self.land_rows_since(count) if row["kind"] == "land.pass"]
        self.assertEqual(len(passes), 1, passes)
        row = passes[0]
        self.assertEqual(row.get(BEFORE_KEY), before, row)
        self.assertEqual(len(str(row.get(BEFORE_KEY))), 40, row)
        self.assertNotIn(CHANNEL_KEY, row)
        self.assertTrue(row.get("stamp"), row)
        sha = str(row.get("sha") or "")
        self.assertTrue(len(sha) >= 7 and after.startswith(sha), (sha, after))

    def test_a1b_docs_commit_rows_share_one_stamp_and_carry_the_prior_main(self):
        """A1 (b) docs 有 commit:start / pass 同一個 docs-… stamp、channel docs;pass 帶短 sha 與前 main。"""
        self.write("docs/t127.md", "一份新文件\n")
        before = self.git("rev-parse", "main").strip()
        count = len(self.events())
        done = self.docs("docs: t127", "docs/t127.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = self.git("rev-parse", "main").strip()
        self.assertNotEqual(after, before)
        rows = self.land_rows_since(count)
        self.assert_docs_rows(rows, ("land.start", "land.pass"))
        sha = str(rows[-1].get("sha") or "")
        self.assertTrue(len(sha) >= 7 and after.startswith(sha), (sha, after))
        self.assertEqual(rows[-1].get(BEFORE_KEY), before, rows[-1])

    def test_a1b_already_on_main_pass_has_no_sha_and_no_prior_main(self):
        """A1 (b) 「已在主線上」那筆 pass 有 stamp / channel docs,沒有 sha 也沒有前 main;兩次呼叫兩個 stamp。"""
        self.write("docs/t127.md", "一份新文件\n")
        count = len(self.events())
        done = self.docs("docs: t127", "docs/t127.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        first = self.assert_docs_rows(self.land_rows_since(count), ("land.start", "land.pass"))
        count = len(self.events())
        done = self.docs("docs: t127 again", "docs/t127.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.land_rows_since(count)
        second = self.assert_docs_rows(rows, ("land.start", "land.pass"))
        self.assertNotEqual(first, second, "一次呼叫產生一個 stamp")
        self.assertNotIn("sha", rows[-1])
        self.assertNotIn(BEFORE_KEY, rows[-1])

    def test_a1b_refused_rows_carry_the_stamp_and_channel(self):
        """A1 (b) git add 收不下(檔不存在)→ land.start + land.refused 同 stamp、channel docs。"""
        count = len(self.events())
        done = self.docs("docs: 沒這個檔", "docs/t127-missing.md")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_docs_rows(self.land_rows_since(count), ("land.start", "land.refused"))

    def test_a1b_push_failure_rows_carry_the_stamp_and_channel(self):
        """A1 (b) push 失敗 → land.start + land.fail 同 stamp、channel docs。"""
        self.git("remote", "set-url", "origin", os.path.join(self.home, "gone.git"))
        self.write("docs/t127.md", "一份新文件\n")
        count = len(self.events())
        done = self.docs("docs: t127", "docs/t127.md")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_docs_rows(self.land_rows_since(count), ("land.start", "land.fail"))


class GuardCase(GuardBase):
    """A2–A6 共用:沙盒主 repo 上發事件、讀 guard.json。"""

    def emit(self, *args):
        done = subprocess.run(["python3", os.path.join(self.repo, "scripts", "event.py"), "emit", *args],
                              env=self.env(AC_ROOT=self.repo), capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def guard_notes(self):
        notes = []
        for path in self.guard_files():
            with open(path, encoding="utf-8") as handle:
                notes += json.load(handle).get("notes") or []
        return notes

    def guard_files(self):
        return glob.glob(os.path.join(self.repo, "reports", "t1", "*", "*.guard.json"))

    def assert_clean_pass(self, done):
        self.assert_not_stopped(done)
        self.assertEqual(self.guard_files(), [])


class TheStartIsLookedForBackwards(GuardCase):
    """A2:land.start 可以早於 agent 起跑;只認 pass 之前的列。"""

    def test_a2_a_start_before_the_agent_started_is_found(self):
        """A2 起跑前已有 land.start S1,期間 commit + land.pass S1(sha 新、前 main 舊)→ rc 0、沒有 guard.json。"""
        self.arm(shell('OLD="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "跑了幾分鐘的 land"\n'
                       'emit land.pass --note "早開始的 land" --kv stamp=S1 '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$OLD"'))
        self.emit("land.start", "--note", "agent 起跑前就開始的 land", "--kv", "stamp=S1")
        self.assert_clean_pass(self.auto_fix("--no-review"))

    def test_a2_a_start_after_the_pass_does_not_count(self):
        """A2 同 stamp 的 land.start 在 pass 之後才發 → rc 8(搜尋範圍是 pass 之前的列)。"""
        self.arm(shell('OLD="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "倒過來的 land"\n'
                       'emit land.pass --note "倒過來" --kv stamp=S5 '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$OLD"\n'
                       'emit land.start --note "倒過來" --kv stamp=S5'))
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")


class TheDocsChannelIsALegalLand(GuardCase):
    """A3:agent 期間真跑 docs 通道;docs + 票 land 首尾相接。"""

    def docs_stamp(self):
        rows = [row for row in self.events()
                if row["kind"] == "land.pass" and row.get(CHANNEL_KEY) == CHANNEL_DOCS]
        self.assertEqual(len(rows), 1, rows)
        return str(rows[0].get("stamp"))

    def assert_docs_landed(self):
        out = self.read("docs-land.out", where=self.home)
        self.assertIn("docs: t127 note", self.git("log", "--format=%s", "main"), out)

    def test_a3_a_real_docs_land_during_the_run_is_not_a_violation(self):
        """A3 agent 期間 `AC_ROOT=$REPO sh land.sh docs …` 真 commit → rc 0、沒有 guard.json。"""
        self.arm(DOCS_LAND)
        done = self.auto_fix("--no-review")
        self.assert_docs_landed()
        self.assert_clean_pass(done)

    def test_a3_docs_then_a_ticket_land_end_to_end_is_not_a_violation(self):
        """A3 同一段先 docs 通道、再一次票 land(前 main = docs 的 commit)→ rc 0、沒有 guard.json。"""
        self.arm(DOCS_LAND + legal_land("S9"))
        done = self.auto_fix("--no-review")
        self.assert_docs_landed()
        self.assert_clean_pass(done)

    def test_a3_notes_list_both_lands(self):
        """A3 docs + 票 land 再多一個工作樹檔 → rc 8 只指那個檔;guard.json notes 列得出 docs 的 stamp 與 S9。"""
        self.arm(DOCS_LAND + legal_land("S9") + 'echo stray > "$REPO/stray-127.txt"')
        done = self.auto_fix("--no-review")
        self.assert_docs_landed()
        self.assert_stopped(done, "stray-127.txt")
        self.assertEqual([row for row in self.guard_rows() if row.get("path") == "HEAD"], [],
                         "兩次落地都歸屬得上,HEAD 不該是違規")
        notes = "\n".join(str(note) for note in self.guard_notes())
        self.assertIn(self.docs_stamp(), notes)
        self.assertIn("S9", notes)


class EveryCommitIsAttributed(GuardCase):
    """A4:沿主線逐 commit 歸屬;夾在中間沒有落地的 commit 抓得到。"""

    def test_a4_a_bare_commit_before_a_legal_land_is_named(self):
        """A4 期間先裸 commit X、再一次合法 land(前 main = X)→ rc 8;guard.json 點名 X;頁上有 X 前 7 位。"""
        self.arm('git -C "$REPO" rev-parse HEAD > "$SANDBOX/old.sha"\n'
                 'git -C "$REPO" commit -q --allow-empty -m "沒有落地的 commit X"\n'
                 'git -C "$REPO" rev-parse HEAD > "$SANDBOX/x.sha"\n'
                 + legal_land("S9")
                 + 'git -C "$REPO" rev-parse HEAD > "$SANDBOX/new.sha"\n')
        done = self.auto_fix("--no-review")
        self.assert_stopped(done, "HEAD")
        old, x, new = (sha_file(self.home, name) for name in ("old.sha", "x.sha", "new.sha"))
        heads = [row for row in self.guard_rows() if row.get("path") == "HEAD"]
        self.assertEqual(len(heads), 1, self.guard_rows())
        self.assertEqual((heads[0].get("before"), heads[0].get("after")), (old, new), heads[0])
        self.assertEqual(heads[0].get(UNATTRIB), x, heads[0])
        self.assertIn(x[:7], self.page_text(self.decisions()[0]))


class IdentityDoesNotComeFromMissingFields(GuardCase):
    """A5:缺欄不算配上。"""

    def test_a5a_start_and_pass_without_stamp(self):
        """A5 (a) land.start 與 land.pass 都沒有 stamp(其餘都對)→ rc 8。"""
        self.arm(shell('OLD="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'emit land.start --note "沒有 stamp"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "沒有 stamp 的落地"\n'
                       'emit land.pass --note "沒有 stamp" '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$OLD"'))
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a5b_pass_without_the_prior_main(self):
        """A5 (b) 同 stamp 的 start + pass、pass 有 sha 沒有前 main → rc 8。"""
        self.arm('emit land.start --note "沒有前 main" --kv stamp=S9\n'
                 'git -C "$REPO" commit -q --allow-empty -m "沒有前 main 的落地"\n'
                 'emit land.pass --note "沒有前 main" --kv stamp=S9 '
                 '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)"')
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a5c_no_start_with_that_stamp_anywhere(self):
        """A5 (c) pass 的 stamp 在整個事件檔找不到同 stamp 的 land.start(只有別的 stamp)→ rc 8。"""
        self.arm(shell('OLD="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "找不到開始的落地"\n'
                       'emit land.pass --note "找不到開始" --kv stamp=S7 '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$OLD"'))
        self.emit("land.start", "--note", "別的一次 land", "--kv", "stamp=S8")
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a5d_the_prior_main_is_not_the_previous_end(self):
        """A5 (d) pass 的前 main = 舊 HEAD 的父(不是上一段的終點)→ rc 8。"""
        self.arm(shell('PARENT="$(git -C "$REPO" rev-parse HEAD~1)"\n'
                       'emit land.start --note "接不上" --kv stamp=S9\n'
                       'git -C "$REPO" commit -q --allow-empty -m "接不上的落地"\n'
                       'emit land.pass --note "接不上" --kv stamp=S9 '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$PARENT"'))
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")


class TheRestOfTheGuardIsUnchanged(GuardCase):
    """A6:新形狀的合法 land 照樣不藏工作樹改動(#115 其餘案例在 test_ticket_115.py)。"""

    def test_a6_a_legal_land_does_not_hide_a_worktree_change(self):
        """A6 新形狀的合法 land + 工作樹多一個檔 → rc 8 指那個檔,沒有 HEAD 那一列。"""
        self.arm(legal_land("S9") + 'echo stray > "$REPO/stray-during-land.txt"')
        self.assert_stopped(self.auto_fix("--no-review"), "stray-during-land.txt")
        self.assertEqual([row for row in self.guard_rows() if row.get("path") == "HEAD"], [])

    def test_a6_a_legal_land_alone_is_not_a_violation(self):
        """A6 新形狀的合法 land 單獨一次 → rc 0、沒有 guard.json。"""
        self.arm(legal_land("S9"))
        self.assert_clean_pass(self.auto_fix("--no-review"))


class TheDocsSayTheNewRule(unittest.TestCase):
    """A7:WORKFLOW §8 守衛那一條與 auto-fix.sh 守衛檔頭改成本票規則。"""

    def text(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def block(self, rel, start, stop):
        lines = self.text(rel).splitlines()
        at = [no for no, line in enumerate(lines) if start in line]
        self.assertTrue(at, "%s 找不到 %r" % (rel, start))
        out = []
        for line in lines[at[0]:]:
            if out and re.match(stop, line):
                break
            out.append(re.sub(r"^\s*#?\s*", "", line))
        return "".join(out)

    def test_a7_workflow_section_8_states_the_new_rule(self):
        """A7 WORKFLOW §8「agent 越界守衛」寫得出前 main 鍵與 docs 通道,舊句「pass 的 sha 就是新 HEAD」不在。"""
        text = self.block("docs/WORKFLOW.md", "agent 越界守衛", r"^(\d+\. |#)")
        self.assertIn(BEFORE_KEY, text)
        self.assertIn("%s=%s" % (CHANNEL_KEY, CHANNEL_DOCS), text)
        self.assertIn("stamp", text)
        self.assertNotIn("pass 的 sha 就是新 HEAD", text)

    def test_a7_auto_fix_guard_header_is_in_step(self):
        """A7 auto-fix.sh「## 越界守衛」檔頭註解寫得出前 main 鍵,舊句「pass 的 sha 就是新的 HEAD」不在。"""
        text = self.block("scripts/auto-fix.sh", "## 越界守衛", r"^[^#]")
        self.assertIn(BEFORE_KEY, text)
        self.assertNotIn("pass 的 sha 就是新的 HEAD", text)


if __name__ == "__main__":
    unittest.main()
