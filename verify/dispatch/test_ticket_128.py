"""#128 越界守衛不把主線那一邊的寫入與 push 失敗的落地判越界:主線可寫前綴只記不擋、push 失敗的 land.fail 算一段。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / 案例自己在 git 上取的 sha)
A1 | integ | 缺鍵(預設 tickets/ docs/ memory/)時 agent 期間主 repo docs/ 新增、改、刪、已髒再改,memory/ 新增;鍵不是字串 list | rc 0、沒有 guard-violation、沒有 decision 頁;guard.json 寫出、violations 空、主線那一邊逐路徑列前後;壞鍵 stderr 點名、照預設 | 票面 A1 原話
A2 | integ | docs/ + scripts/ 新檔;鍵 = [] 時 docs/;預設下 docs-x/a.md | 前者 rc 8 一整組、violations 只有 scripts 那一個、主線那一邊照列 docs;後兩者 rc 8 | 票面 A2 原話;變異 M1 / M2
A3 | integ | agent 期間真跑 `AC_ROOT=$REPO ticket.py set 2 state Blocked` 與 `set 2 objections`(處置 accepted) | rc 0、沒有 guard.json;票 2 真的變了(夾具自證) | 票面 A3 原話(基底就該綠:本票只加回歸斷言)
A4 | integ | 沙盒 land.sh:docs 通道 / 票 land 的 push 被 origin pre-receive exit 1 拒;agent 期間 docs 通道 push 被拒;假發 land.fail 各形狀 | land.fail 帶 stage push、sha = 本機 main 短 sha、前 main = 跑之前 main、stamp;守衛 rc 0、notes 有 stamp 與「push 失敗」;閘門紅那種 land.fail → rc 8(「缺前 main → rc 8」那一案 #153 刪:D-065 (1) 起缺前 main 的那一筆只算它自己的 sha)| 票面 A4 原話;變異 M3 / M4
A5 | doc   | 讀 docs/WORKFLOW.md §8「agent 越界守衛」那一條 | 寫得出鍵名、預設三前綴、`[]`、只記不擋、stage push + sha + 前 main、專案端 land 包裝要照發 | 票面 A5 原話

## 介面字串(全部出自票面 acceptance)
KEY        = "guard_" + "main_paths"      ← A1 config 鍵
SIDE       = "main" + "_side"             ← A1 guard.json 欄位(list,每列 path / before / after)
STAGE      = ("stage", "push")            ← A4 land.fail 鍵值
BEFORE_KEY = "main" + "_before"           ← A4(同 #127 A1)
PUSH_NOTE  = "push 失敗"                  ← A4 notes 字樣
GUARD_RC   = 8 / REASON = "guard-violation"(沿用 #115)

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
A1–A3 借 verify/dispatch/test_ticket_115.GuardBase(真 auto-fix + 真 git、config worker.command 指向假 sh);
主線那一邊的寫入由假 agent 在交件前直接寫 `$REPO/…` 模擬(守衛看的是主 repo 工作樹,分不出是誰寫的)。
A4 land.sh 那幾條借 tests/test_land.LandBase;push 被拒 = origin.git/hooks/pre-receive exit 1。
守衛那條的 hook 只拒 docs 那一個 commit,auto-fix 自己的 push 不受影響。期望 sha 一律由案例 `git rev-parse` 取。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;
guard.json 不存在時 notes 無處可看,所以 A4 的 notes 用「再多一個 memory/ 檔」那一案驗(只記不擋,guard.json 會寫出)。
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
from control_harness import write_executable  # noqa: E402
from test_land import LandBase  # noqa: E402
from test_ticket_115 import GUARD_RC, GuardBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
KEY = "guard_" + "main_paths"
SIDE = "main" + "_side"
STAGE_KEY, STAGE_PUSH = "stage", "push"
BEFORE_KEY = "main" + "_before"
PUSH_NOTE = "push 失敗"
DOCS_STAMP = r"^docs-\d{8}-\d{6}-\d+$"
DEFAULT_PREFIXES = ("tickets/", "docs/", "memory/")

# pre-receive:`@ONLY@` 空 = 一律拒;否則只拒新頭 commit 訊息含它的那一次(守衛那條:auto-fix 自己的 push 要過)。
REJECT_HOOK = """#!/bin/sh
ONLY="@ONLY@"
while read old new ref; do
    if [ -z "$ONLY" ] || git log -1 --format=%s "$new" 2>/dev/null | grep -q "$ONLY"; then
        echo "t128: origin 拒收" >&2
        exit 1
    fi
done
exit 0
"""


def shell(text):
    """假 agent 的片段:`@KB@` 換成前 main 的鍵名、`@SK@` 換成 stage 鍵。"""
    return text.replace("@KB@", BEFORE_KEY).replace("@SK@", STAGE_KEY)


def reject_pushes(origin, only=""):
    write_executable(os.path.join(origin, "hooks", "pre-receive"), REJECT_HOOK.replace("@ONLY@", only))


DOCS_LAND = """mkdir -p "$REPO/docs"
echo "t128 docs note" > "$REPO/docs/t128-landed.md"
AC_ROOT="$REPO" sh "$SCRIPTS/land.sh" docs "docs: t128 note" docs/t128-landed.md > "$SANDBOX/docs-land.out" 2>&1 || true
"""


class MainSideCase(GuardBase):
    """A1–A4 共用:config 的鍵、guard.json 整份、主線那一邊的列。"""

    def set_main_paths(self, value):
        conf = json.loads(self.read("board/config.json"))
        conf[KEY] = value
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def guard_files(self):
        return glob.glob(os.path.join(self.repo, "reports", "t1", "*", "*.guard.json"))

    def guard_doc(self):
        files = self.guard_files()
        self.assertEqual(len(files), 1, "guard.json 要恰一份:%s" % files)
        with open(files[0], encoding="utf-8") as handle:
            return json.load(handle)

    def side_rows(self):
        rows = self.guard_doc().get(SIDE)
        self.assertIsInstance(rows, list, "guard.json 沒有 %s list" % SIDE)
        return rows

    def assert_side_lists(self, *paths):
        rows = self.side_rows()
        for path in paths:
            hits = [row for row in rows if isinstance(row, dict) and str(row.get("path", "")).endswith(path)]
            self.assertEqual(len(hits), 1, "%s 沒有恰一列 %s:%s" % (SIDE, path, rows))
            self.assertIn("before", hits[0], hits[0])
            self.assertIn("after", hits[0], hits[0])
            self.assertNotEqual(hits[0].get("before"), hits[0].get("after"), hits[0])

    def assert_passed(self, done):
        """rc 0 先在本檔斷言(驗紅工具只認案例檔自己的斷言),其餘照 #115 的 assert_not_stopped。"""
        self.assertNotEqual(done.returncode, GUARD_RC, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_not_stopped(done)

    def assert_recorded_not_stopped(self, done, *paths):
        """只記不擋:rc 0、沒有 guard-violation、沒有 decision 頁;guard.json 寫出、violations 空、主線那一邊列得出。"""
        self.assert_passed(done)
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.guard_doc().get("violations"), [])
        self.assert_side_lists(*paths)


class MainWritablePrefixesAreRecordedNotStopped(MainSideCase):
    """A1:預設前綴底下主線的寫入不擋,記進 guard.json。"""

    def test_a1_a_new_docs_file_is_recorded_not_stopped(self):
        """A1 缺鍵時主 repo docs/ 新增檔 → rc 0、沒有 decision、guard.json violations 空、主線那一邊列出它。"""
        self.arm('echo note > "$REPO/docs/t128-new.md"')
        self.assert_recorded_not_stopped(self.auto_fix("--no-review"), "docs/t128-new.md")

    def test_a1_modify_delete_and_dirty_again_under_docs(self):
        """A1 docs/ 已追蹤檔改、刪、開工前已髒再改 → 不擋;三個路徑各一列、前後不同。"""
        self.write("docs/t128-gone.md", "要被刪的\n")
        self.write("docs/t128-dirty.md", "開工前就會髒的\n")
        self.git("add", "docs/t128-gone.md", "docs/t128-dirty.md")
        self.git("commit", "-q", "-m", "t128: 要被刪的檔")
        # #153 / D-065 (2):docs/DECISIONS.md 在 hard_block.paths 上、不豁免,已髒再改改用一般 docs 檔。
        self.arm('echo changed >> "$REPO/docs/NOTES.md"\n'
                 'echo again >> "$REPO/docs/t128-dirty.md"\n'
                 'rm "$REPO/docs/t128-gone.md"')
        self.write("docs/t128-dirty.md", self.read("docs/t128-dirty.md") + "開工前就髒了\n")
        self.assert_recorded_not_stopped(self.auto_fix("--no-review"), "docs/NOTES.md",
                                         "docs/t128-dirty.md", "docs/t128-gone.md")

    def test_a1_a_new_memory_file_is_recorded_not_stopped(self):
        """A1 缺鍵時主 repo memory/ 新增檔 → 不擋、主線那一邊列出它。"""
        self.arm('mkdir -p "$REPO/memory/role"\necho x > "$REPO/memory/role/t128-note.md"')
        self.assert_recorded_not_stopped(self.auto_fix("--no-review"), "memory/role/t128-note.md")

    def test_a1_an_explicit_prefix_list_is_honoured(self):
        """A1 鍵 = ["memory/"] 時 memory/ 改動不擋(前綴來自設定)。"""
        self.arm('echo x > "$REPO/memory/t128-only.md"')
        self.set_main_paths(["memory/"])
        self.assert_recorded_not_stopped(self.auto_fix("--no-review"), "memory/t128-only.md")

    def test_a1_a_non_list_value_is_named_and_falls_back_to_the_default(self):
        """A1 鍵不是字串 list("docs/" 一個字串)→ stderr 點名鍵名、照缺鍵處理:docs/ 改動不擋。"""
        self.arm('echo note > "$REPO/docs/t128-bad-conf.md"')
        self.set_main_paths("docs/")
        done = self.auto_fix("--no-review")
        self.assertIn(KEY, done.stderr, done.stdout + done.stderr)
        self.assert_recorded_not_stopped(done, "docs/t128-bad-conf.md")


class TheExemptionDoesNotSpill(MainSideCase):
    """A2:豁免只到前綴為止、來自設定、以 `/` 為界。"""

    def test_a2_a_change_outside_the_prefixes_is_still_rc8_alone(self):
        """A2 同一輪 docs/ 新檔 + scripts/ 新檔 → rc 8 一整組;violations 只有 scripts 那一個;主線那一邊照列 docs。"""
        self.arm('echo note > "$REPO/docs/t128-side.md"\n'
                 'mkdir -p "$REPO/scripts"\n'
                 'echo "#!/bin/sh" > "$REPO/scripts/t128-x.sh"')
        self.assert_stopped(self.auto_fix("--no-review"), "scripts/t128-x.sh")
        violations = self.guard_doc().get("violations") or []
        self.assertEqual([str(row.get("path")).endswith("scripts/t128-x.sh") for row in violations],
                         [True], violations)
        self.assert_side_lists("docs/t128-side.md")

    def test_a2_a_root_file_is_still_rc8(self):
        """A2 同一輪 memory/ 新檔 + 根目錄新檔 → rc 8、violations 只有根目錄那一個。"""
        self.arm('echo x > "$REPO/memory/t128-side.md"\necho stray > "$REPO/t128-root.txt"')
        self.assert_stopped(self.auto_fix("--no-review"), "t128-root.txt")
        violations = self.guard_doc().get("violations") or []
        self.assertEqual([str(row.get("path")).endswith("t128-root.txt") for row in violations],
                         [True], violations)

    def test_a2_an_empty_list_exempts_nothing(self):
        """A2 鍵 = [] → docs/ 改動 rc 8(豁免來自設定,不是寫死;M1)。"""
        self.arm('echo note > "$REPO/docs/t128-empty.md"')
        self.set_main_paths([])
        self.assert_stopped(self.auto_fix("--no-review"), "docs/t128-empty.md")

    def test_a2_the_prefix_is_bounded_by_its_slash(self):
        """A2 預設下 docs-x/a.md 不算 docs/ 底下 → rc 8(M2)。"""
        self.arm('mkdir -p "$REPO/docs-x"\necho x > "$REPO/docs-x/a.md"')
        self.assert_stopped(self.auto_fix("--no-review"), "docs-x/a.md")


class TicketWritesStayExempt(MainSideCase):
    """A3:ticket.py set 寫票狀態與 objections 處置走控制面,本來就不擋(回歸斷言)。"""

    def test_a3_real_ticket_set_during_the_run_is_not_a_violation(self):
        """A3 agent 期間真跑 ticket.py set 2 state Blocked 與 objections 處置 → rc 0、沒有 guard.json。"""
        objection = {"category": "scope", "body": "t128 objection", "evidence": "",
                     "owner": "main", "disposition": "", "follow_up": ""}
        self.make_ticket(2, objections=[objection])
        disposed = json.dumps([dict(objection, disposition="accepted")])
        self.arm('T="env -u AC_TICKETS_DIR -u AC_CONTROL_DIR AC_ROOT=$REPO python3 $SCRIPTS/ticket.py"\n'
                 '$T set 2 state Blocked > "$SANDBOX/t128-set.out" 2>&1\n'
                 "$T set 2 objections '%s' >> \"$SANDBOX/t128-set.out\" 2>&1" % disposed)
        done = self.auto_fix("--no-review")
        out = self.read("t128-set.out", where=self.home)
        row = self.load_ticket("2")
        self.assertEqual(row["state"], "Blocked", "夾具:ticket.py set 沒寫到主 repo 的票:" + out)
        self.assertEqual(row["objections"][0].get("disposition"), "accepted", out)
        self.assert_passed(done)
        self.assertEqual(self.guard_files(), [])


class PushFailureShapesFromLandSh(LandBase):
    """A4:land.sh 兩個 push 失敗出口的 land.fail 形狀(真跑沙盒 land.sh,origin 拒收)。"""

    def fail_rows_since(self, count):
        return [row for row in self.events()[count:] if row.get("kind") == "land.fail"]

    def assert_push_fail(self, row, before):
        after = self.git("rev-parse", "main").strip()
        self.assertNotEqual(after, before, "夾具:本機 main 要已經前進")
        self.assertEqual(row.get(STAGE_KEY), STAGE_PUSH, row)
        sha = str(row.get("sha") or "")
        self.assertTrue(len(sha) >= 7 and after.startswith(sha), (sha, after, row))
        self.assertEqual(row.get(BEFORE_KEY), before, row)
        self.assertTrue(row.get("stamp"), row)
        self.assertNotEqual(self.git("rev-parse", "main", cwd=self.origin).strip(), after,
                            "夾具:origin 應該沒收到")

    def test_a4_docs_channel_push_rejected(self):
        """A4 docs 通道 commit 成功、push 被 pre-receive 拒 → land.fail 帶 stage push、本機 main 短 sha、前 main、docs stamp。"""
        reject_pushes(self.origin)
        self.write("docs/t128.md", "一份新文件\n")
        before = self.git("rev-parse", "main").strip()
        count = len(self.events())
        done = self.run_sh("scripts/land.sh", "docs", "docs: t128", "docs/t128.md")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        fails = self.fail_rows_since(count)
        self.assertEqual(len(fails), 1, fails)
        self.assert_push_fail(fails[0], before)
        self.assertRegex(str(fails[0].get("stamp")), DOCS_STAMP)

    def test_a4_ticket_land_push_rejected(self):
        """A4 票 land ff-only 進本機 main、push 被拒 → land.fail 帶 stage push、本機 main 短 sha、前 main、同 land.start 的 stamp。"""
        good = self.branch_for(1, "t1-good")
        self.commit_in(good, "src/g1", "有 commit 的那一張")
        self.approve(1, "t1-good")
        reject_pushes(self.origin)
        before = self.git("rev-parse", "main").strip()
        count = len(self.events())
        done = self.run_sh("scripts/land.sh", "t1-good")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        fails = self.fail_rows_since(count)
        self.assertEqual(len(fails), 1, (fails, done.stdout + done.stderr))
        self.assert_push_fail(fails[0], before)
        starts = [row.get("stamp") for row in self.events()[count:] if row.get("kind") == "land.start"]
        self.assertIn(fails[0].get("stamp"), starts)


class APushFailureIsALegalSegment(MainSideCase):
    """A4:守衛把帶 stage push 且欄位齊全的 land.fail 當一段;其他 land.fail 不算。"""

    def assert_docs_push_failed(self):
        out = self.read("docs-land.out", where=self.home)
        self.assertIn("docs: t128 note", self.git("log", "--format=%s", "main"), out)
        self.assertTrue(self.push_fail_stamp(), "夾具:docs 通道的 push 應該被拒:" + out)

    def push_fail_stamp(self):
        rows = [row for row in self.events()
                if row.get("kind") == "land.fail" and row.get("channel") == "docs"]
        self.assertEqual(len(rows), 1, rows)
        return str(rows[0].get("stamp"))

    def test_a4_a_real_docs_push_failure_is_not_a_violation(self):
        """A4 agent 期間真跑 docs 通道、commit 成功 push 被拒 → rc 0、沒有 guard-violation。"""
        reject_pushes(self.origin, "t128 note")
        self.arm(DOCS_LAND)
        done = self.auto_fix("--no-review")
        self.assert_docs_push_failed()
        self.assert_passed(done)
        self.assertEqual(self.decisions(), [])

    def test_a4_notes_name_the_push_failed_segment(self):
        """A4 同上再多一個 memory/ 檔(只記不擋、guard.json 寫出)→ rc 0;notes 有那一段的 stamp 與「push 失敗」。"""
        reject_pushes(self.origin, "t128 note")
        self.arm(DOCS_LAND + 'echo x > "$REPO/memory/t128-side.md"')
        done = self.auto_fix("--no-review")
        self.assert_docs_push_failed()
        self.assert_recorded_not_stopped(done, "memory/t128-side.md")
        notes = "\n".join(str(note) for note in self.guard_doc().get("notes") or [])
        self.assertIn(self.push_fail_stamp(), notes)
        self.assertIn(PUSH_NOTE, notes)

    def test_a4_an_emitted_push_failure_with_all_fields_is_a_segment(self):
        """A4 同 stamp land.start + land.fail(stage push、sha = 新 HEAD、前 main = 舊 HEAD)→ rc 0。"""
        self.arm(shell('emit land.start --note "push 被拒的落地" --kv stamp=S9\n'
                       'LB="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "push 被拒的落地"\n'
                       'emit land.fail --note "push 沒成功" --kv stamp=S9 --kv @SK@=push '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$LB"'))
        done = self.auto_fix("--no-review")
        self.assert_passed(done)

    def test_a4_a_gate_red_fail_does_not_excuse_a_bare_commit(self):
        """A4 land.fail 沒有 stage push(閘門紅)但 sha / 前 main / stamp 齊 + 裸 commit → rc 8(M3)。"""
        self.arm(shell('emit land.start --note "閘門紅" --kv stamp=S9\n'
                       'LB="$(git -C "$REPO" rev-parse HEAD)"\n'
                       'git -C "$REPO" commit -q --allow-empty -m "裸 commit"\n'
                       'emit land.fail --note "閘門紅" --kv stamp=S9 --kv @SK@=gate '
                       '--kv sha="$(git -C "$REPO" rev-parse --short HEAD)" --kv @KB@="$LB"'))
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")


class TheDocsSayTheNewRule(unittest.TestCase):
    """A5:WORKFLOW §8「agent 越界守衛」那一條寫明主線可寫前綴與 push 失敗的 land.fail。"""

    def block(self):
        with open(os.path.join(ROOT, "docs", "WORKFLOW.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        at = [no for no, line in enumerate(lines) if "agent 越界守衛" in line]
        self.assertTrue(at, "docs/WORKFLOW.md 找不到「agent 越界守衛」")
        out = []
        for line in lines[at[0]:]:
            if out and re.match(r"^(\d+\. |#)", line):
                break
            out.append(line.strip())
        return " ".join(out)

    def test_a5_the_main_writable_prefixes_are_documented(self):
        """A5 寫得出鍵名、預設三前綴、`[]`、只記不擋。"""
        text = self.block()
        self.assertIn(KEY, text)
        for prefix in DEFAULT_PREFIXES:
            self.assertIn(prefix, text)
        self.assertIn("[]", text)
        self.assertIn("只記不擋", text)

    def test_a5_the_push_failure_shape_is_documented(self):
        """A5 寫得出 stage push + sha + 前 main,並註明專案端 land 包裝的 push 失敗出口要照發。"""
        text = self.block()
        self.assertIn("%s=%s" % (STAGE_KEY, STAGE_PUSH), text)
        self.assertIn(BEFORE_KEY, text)
        self.assertIn("sha", text)
        said = [part for part in re.split(r"[。;]", text)
                if ("包裝" in part or "land-ticket" in part) and "push" in part]
        self.assertTrue(said, "沒寫專案端 land 包裝的 push 失敗出口要照發")


if __name__ == "__main__":
    unittest.main()
