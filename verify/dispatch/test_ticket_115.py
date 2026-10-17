"""#115 兩種 worker 同權限與共同事後越界檢查:codex 命令字面、--add-dir 交付根、權威票面快照、越界守衛 rc=8。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 字面 / interface_contracts / 夾具自己寫下的值)
A1 | cli   | agent-command --model codex:gpt-5.6-sol --tool codex(不給 / high / turbo);claude-code 兩句 | 恰為票面那一句;high 只在 -m 後加 -c 'model_reasoning_effort="high"';turbo rc 2;claude 不變 | 票面 A1 原話;claude 句抄 #114 契約
A2 | integ | auto-fix 真起假 codex 與假 claude worker,各記 argv / pwd / AC_ROLE / AC_ROOT | --add-dir 恰一次、值 = 副本根(不是主 repo);cwd 兩者都是副本根/work;AC_ROLE=worker;AC_ROOT 同一個隔離位置 | 票面 A2 原話
A3 | integ | base_sha 上的票寫 OLD objective,權威票庫寫 NEW | 派工文有「權威票面快照」、含 NEW 不含 OLD、明說 work/tickets/1.json 不能推翻;控制根票副本 NEW;work/ 那份 OLD(夾具自證) | 票面 A3 原話
A4 | integ | worker 在主 repo 新增 / 刪除 / 修改、已髒檔再改、HEAD 前進、切分支、guarded_dirs 新增 / 刪除 / 改動;驗證者越界;批次 worker 越界 | rc 8、票 Blocked / owner=main、attempt.failed reason=guard-violation、唯一 decision 指出路徑、guard 報告有前後;patch 不套、副本留著 | 票面 A4 原話 + interface_contracts.guard_result
A5 | integ | 寫 tickets/(控制面)/ board/ 下非控制面檔;guarded_dirs ["~/g"] / ["../o"];缺 / [] 時寫主 repo 外 | 控制面不擋、board/ 其他檔擋;~ 展開與相對主 repo 都抓;缺 / [] 不抓 repo 外 | 票面 A5 原話
A6 | integ | 同 stamp land.start + land.pass(sha = 新 HEAD);只有 land.start;pass 的 sha 對不上;合法 land + 多一個檔 | 第一種不擋,其餘三種 rc 8 | 票面 A6 原話
A7 | integ | 假 codex 撞 429 且越界;只撞 429 | 前者 rc 8、票 Blocked 不改派、reason=guard-violation、頁指出路徑;後者照舊 rc 7、Ready、opus / claude-code | 票面 A7 原話
A8 | doc   | 讀 docs/ROLES.md、docs/WORKFLOW.md、scripts/ticket.py、scripts/auto-fix.sh;乾跑 codex 票 | 兩份文件有 D-048、rc=8、「待專案同步後冒煙實證」;兩支腳本與乾跑 worker 行沒有 workspace-write | 票面 A8 原話

## 介面字串(全部出自票面 acceptance / interface_contracts;票面沒定的照 t115 的 patch,EVIDENCE 記一行)
CODEX_BASE   = "codex exec -m gpt-5.6-sol -s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -"  ← A1 原話
EFFORT_HIGH  = "-c 'model_reasoning_effort=\"high\"'"     ← A1「只在 -m 後加入」
SNAPSHOT     = "權威票面快照"                                ← A3、verify_strings
REASON       = "guard-violation"                             ← A4、verify_strings
GUARD_RC     = 8                                             ← A4、interface_contracts.guard_result
GUARD_REPORT = reports/t1/*/*.guard.json(patch 的形狀;docs/WORKFLOW.md 寫明)  ← A4「前後證據」
OLD_SANDBOX  = "workspace-write"                             ← A8 不准再出現

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
沙盒借 tests/control_harness.Sandbox 經 tests/test_auto_fix.AutoFixBase / tests/test_auto_fix_codex.CodexBase
(拋棄式真 git repo、PATH 最前面的 `claude` 是絆線)。worker / 驗證者 / 批次 worker 是 config worker.command
指向的假 sh;codex 是 stub-bin 裡的假 `codex`,把 argv、pwd、AC_ROLE、AC_ROOT、stdin 與兩份票副本記下來。
越界動作(寫主 repo、commit、切分支、emit land.*)由假 agent 在交件前做;期望值寫死在本檔。
A1 直接 subprocess 跑本 repo 的 scripts/ticket.py agent-command;外層 AC_* 清掉。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字;不跑真的模型;
不驗真 codex 吃 danger-full-access 的實跑效果(T 同步票的冒煙)。
"""
import glob
import json
import os
import subprocess
import sys
import unittest

TAGS = ["worker-guard"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402
from test_auto_fix_codex import CodexBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
CODEX_BASE = ("codex exec -m gpt-5.6-sol -s danger-full-access -c 'approval_policy=\"never\"' "
              "-C . --skip-git-repo-check -")
CODEX_HIGH = ("codex exec -m gpt-5.6-sol -c 'model_reasoning_effort=\"high\"' -s danger-full-access "
              "-c 'approval_policy=\"never\"' -C . --skip-git-repo-check -")
CLAUDE_BASE = "claude -p --model opus --permission-mode acceptEdits"
SNAPSHOT = "權威票面快照"
REASON = "guard-violation"
GUARD_RC = 8
QUOTA_RC = 7
OLD_SANDBOX = "workspace-write"
ADD_DIR = "--add-dir"
WORKER_LINE = "auto-fix: worker 命令 —— "
CODEX_MODEL = "codex:gpt-6-astra"

# 假 agent:`@WHO@` 那個角色在交件前做 `@STRAY@`(`$REPO` = 主 repo、`$SANDBOX` = 沙盒的家)。
# worker 交一份會綠的 patch;別的角色什麼都不交。記下 pwd / AC_ROLE / AC_ROOT / stdin / 兩份票。
FAKE_AGENT = """#!/bin/sh
set -e
REPO="@REPO@"
SANDBOX="@SANDBOX@"
echo "$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
emit() { AC_ROOT="$REPO" python3 "$REPO/scripts/event.py" emit "$@" >/dev/null; }
if [ "$AC_ROLE" = worker ]; then
    printf '%s\\n%s\\n%s\\n' "$(pwd -P)" "$AC_ROLE" "$AC_ROOT" > "$SANDBOX/agent.where"
    cat > "$SANDBOX/agent.stdin"
    cp "$AC_ROOT/tickets/1.json" "$SANDBOX/agent.control-ticket" 2>/dev/null || true
    cp "$AC_WORK/work/tickets/1.json" "$SANDBOX/agent.work-ticket" 2>/dev/null || true
fi
if [ "$AC_ROLE" = "@WHO@" ]; then
@STRAY@
fi
cd "$AC_WORK"
[ "$AC_ROLE" = worker ] || exit 0
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 codex:argv 一行一格;@MODE@ = deliver / quota / quota-stray。429 那一行不以 `ERROR:` 起頭:
# 斷言訊息帶著 stdout 時,行首的 `ERROR:` 會被驗紅工具當成 unittest 的 ERROR 標頭(紅在別處)。
FAKE_CODEX = """#!/bin/sh
SANDBOX="@SANDBOX@"
printf '%s\\n' "$@" > "$SANDBOX/codex.argv"
printf '%s\\n%s\\n%s\\n' "$(pwd -P)" "$AC_ROLE" "$AC_ROOT" > "$SANDBOX/codex.where"
cat > "$SANDBOX/codex.stdin"
echo "codex ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
case "@MODE@" in
    quota)
        echo "codex: stream error: 429 Too Many Requests"
        echo "You've hit your usage limit." >&2
        exit 1 ;;
    quota-stray)
        echo stray > "@REPO@/codex-stray.txt"
        echo "codex: stream error: 429 Too Many Requests"
        echo "You've hit your usage limit." >&2
        exit 1 ;;
esac
cd "$AC_WORK"
mkdir -p work/tests
cat > work/tests/test_thing.py <<'CASE'
import unittest


class T(unittest.TestCase):
    def test_thing(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
printf '# 第 %s 輪\\n已排除的假設:沒有\\n' "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

LAND_PASS_MATCHES = """emit land.start --note "同時的落地" --kv stamp=S9
git -C "$REPO" commit -q --allow-empty -m "同時的落地"
emit land.pass --note "同時的落地" --kv stamp=S9 --kv sha="$(git -C "$REPO" rev-parse --short HEAD)"
"""


def agent_command(*args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    return subprocess.run(
        ["python3", os.path.join(ROOT, "scripts", "ticket.py"), "agent-command", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def unquoted(value):
    return None if value is None else str(value).strip('"')


def lines_of(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


class CodexCommandHasClaudesPermissions(unittest.TestCase):
    """A1:`ticket.py agent-command` 的 codex 句與 claude 同權限,其餘 #114 契約不變。"""

    def assert_prints(self, done, want):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, want + "\n", done.stderr)

    def test_a1_the_codex_base_command_is_exactly_the_ticket_line(self):
        """A1 不給 effort / 給空 effort → 恰為票面那一句(danger-full-access、approval never、-C .)。"""
        self.assert_prints(agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex"),
                           CODEX_BASE)
        self.assert_prints(agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex",
                                         "--effort", ""), CODEX_BASE)

    def test_a1_high_only_adds_the_reasoning_config_after_the_model(self):
        """A1 --effort high → 只在 `-m <id>` 之後多 `-c 'model_reasoning_effort="high"'`,其餘逐字同。"""
        self.assert_prints(agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex",
                                         "--effort", "high"), CODEX_HIGH)

    def test_a1_an_illegal_effort_is_rc2_and_prints_no_command(self):
        """A1 codex + --effort turbo → rc 2、stdout 空。"""
        done = agent_command("--model", "codex:gpt-5.6-sol", "--tool", "codex", "--effort", "turbo")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")

    def test_a1_the_claude_command_keeps_the_114_contract(self):
        """A1 claude-code 那一支照 #114:swap-model 與 effort 的兩句逐字不變,沒有 codex 的旗標。"""
        self.assert_prints(agent_command("--model", "fable", "--tool", "claude-code",
                                         "--base", CLAUDE_BASE, "--swap-model"),
                           "claude -p --model fable --permission-mode acceptEdits")
        self.assert_prints(agent_command("--model", "fable", "--tool", "claude-code",
                                         "--base", CLAUDE_BASE, "--swap-model", "--effort", "high"),
                           "claude -p --model fable --effort high --permission-mode acceptEdits")


class GuardBase(CodexBase):
    """共用的夾具:假 agent / 假 codex、guarded_dirs、越界之後該看到的那一整組。"""

    def copy_root(self, kind="fix"):
        return os.path.realpath(os.path.join(self.home, "repo-wt", "%s-t1" % kind, "round1"))

    def arm(self, stray="true", who="worker", guarded=None, **fields):
        self.set_worker(FAKE_AGENT.replace("@REPO@", self.repo).replace("@SANDBOX@", self.home)
                        .replace("@WHO@", who).replace("@STRAY@", stray))
        if guarded is not None:
            conf = json.loads(self.read("board/config.json"))
            conf["guarded_dirs"] = guarded
            self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        fields.setdefault("subject", "守衛")
        return self.ticket_ready(**fields)

    def arm_codex(self, mode="deliver", **fields):
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX.replace("@SANDBOX@", self.home).replace("@REPO@", self.repo)
                         .replace("@MODE@", mode))
        return self.codex_ticket(**fields)

    def failed_reasons(self):
        return [unquoted(row.get("reason")) for row in self.events()
                if row["kind"] == "ticket.attempt.failed"]

    def decisions(self):
        return [row for row in self.inbox_rows() if row.get("kind") == "decision"]

    def page_text(self, row):
        text = str(row.get("what") or "")
        if row.get("page") and os.path.exists(os.path.join(self.repo, row["page"])):
            text += "\n" + self.read(row["page"])
        return text

    def guard_rows(self, ticket="t1"):
        rows = []
        for path in glob.glob(os.path.join(self.repo, "reports", ticket, "*", "*.guard.json")):
            with open(path, encoding="utf-8") as handle:
                rows += json.load(handle).get("violations") or []
        return rows

    def assert_evidence(self, path, ticket="t1"):
        """guard 報告裡有一列指著 `path`,前後兩格都在而且不同。"""
        hits = [row for row in self.guard_rows(ticket) if path in str(row.get("path"))]
        self.assertTrue(hits, "guard 報告沒有 %s:%s" % (path, self.guard_rows(ticket)))
        self.assertNotEqual(hits[0].get("before"), hits[0].get("after"), hits[0])

    def assert_stopped(self, done, path, who="worker"):
        self.assertEqual(done.returncode, GUARD_RC, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual((row["state"], row.get("owner")), ("Blocked", "main"))
        self.assertEqual(self.failed_reasons(), [REASON])
        pages = self.decisions()
        self.assertEqual(len(pages), 1, pages)  # pin:票面 A4「唯一 decision」
        self.assertIn(path, self.page_text(pages[0]))
        self.assert_evidence(path)
        self.assertEqual(self.git("branch", "--list", "t1").strip(), "", "越界的 patch 不准套")
        kind = "verify" if who == "verifier" else "fix"
        self.assertTrue(os.path.isdir(os.path.join(self.copy_root(kind), "work")), "副本要留著")

    def assert_not_stopped(self, done):
        self.assertNotEqual(done.returncode, GUARD_RC, done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn(REASON, self.failed_reasons())
        self.assertEqual(self.guard_rows(), [])


class CodexStartsWhereClaudeStarts(GuardBase):
    """A2:真起的 codex 與 claude 同 cwd / AC_ROLE / 隔離 AC_ROOT;codex 多一個交付根。"""

    def where(self, name):
        cwd, role, control = lines_of(os.path.join(self.home, name))
        return os.path.realpath(cwd), role, os.path.realpath(control)

    def test_a2_codex_gets_the_copy_root_as_add_dir_exactly_once(self):
        """A2 codex argv 裡 `--add-dir` 恰一次,值 = 當輪副本根,不是主 repo;其餘 = 票面那一句。"""
        self.arm_codex()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        argv = lines_of(os.path.join(self.home, "codex.argv"))
        self.assertEqual(argv.count(ADD_DIR), 1, argv)  # pin:票面 A2「只加入一次」
        at = [no for no, item in enumerate(argv) if item == ADD_DIR][0]
        value = os.path.realpath(argv[at + 1])
        self.assertEqual(value, self.copy_root(), argv)
        self.assertNotEqual(value, os.path.realpath(self.repo), "主 repo 不能當交付根")
        rest = argv[:at] + argv[at + 2:]
        self.assertEqual(["codex"] + rest,
                         CODEX_BASE.replace("gpt-5.6-sol", "gpt-6-astra")
                         .replace("'", "").split(), argv)

    def test_a2_codex_and_claude_share_cwd_role_and_isolated_root(self):
        """A2 codex 與 claude worker:cwd 都是副本根/work、AC_ROLE=worker、AC_ROOT 同一個隔離位置。"""
        self.arm_codex()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        codex = self.where("codex.where")
        copy = self.copy_root()
        home = os.path.realpath(self.home)
        repo = os.path.realpath(self.repo)

        self.setUp()
        self.arm()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        claude = self.where("agent.where")
        claude_home = os.path.realpath(self.home)
        self.assertEqual(codex[0], os.path.join(copy, "work"))
        self.assertEqual(os.path.relpath(codex[0], home), os.path.relpath(claude[0], claude_home))
        self.assertEqual((codex[1], claude[1]), ("worker", "worker"))
        self.assertEqual(os.path.relpath(codex[2], home), os.path.relpath(claude[2], claude_home))
        self.assertFalse(codex[2] == repo or codex[2].startswith(repo + os.sep),
                         "AC_ROOT 是隔離的控制根,不是主 repo:%s" % codex[2])


class TheSnapshotComesFromTheLiveStore(GuardBase):
    """A3:派工文與控制根的票副本來自權威票庫,不被 base_sha 展出來的舊票覆蓋。"""

    def stale_base(self, objective_old, objective_new, **fields):
        row = self.ticket_ready(objective=objective_old, **fields)
        row.update(objective=objective_new, base_sha=self.git("rev-parse", "main").strip())
        self.write("tickets/1.json", json.dumps(row, ensure_ascii=False, indent=2) + "\n")

    def assert_snapshot(self, packet, control, work):
        self.assertIn(SNAPSHOT, packet)
        self.assertIn("OBJ-NEW-from-store", packet)
        self.assertNotIn("OBJ-OLD-from-base", packet)
        said = [line for line in packet.splitlines()
                if "work/tickets/1.json" in line and "推翻" in line]
        self.assertTrue(said, "派工文要明說 work/tickets/1.json 不能推翻快照")
        with open(control, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "OBJ-NEW-from-store")
        with open(work, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "OBJ-OLD-from-base",
                             "夾具要讓 work/ 真的是舊版,不然什麼都沒比")

    def test_a3_the_claude_worker_sees_the_new_objective(self):
        """A3 claude worker:派工文「權威票面快照」是 NEW、控制根票是 NEW、work/ 那份是 OLD。"""
        self.set_worker(FAKE_AGENT.replace("@REPO@", self.repo).replace("@SANDBOX@", self.home)
                        .replace("@WHO@", "nobody").replace("@STRAY@", "true"))
        self.stale_base("OBJ-OLD-from-base", "OBJ-NEW-from-store")
        self.auto_fix("--no-review")
        self.assert_snapshot(self.read("agent.stdin", where=self.home),
                             os.path.join(self.home, "agent.control-ticket"),
                             os.path.join(self.home, "agent.work-ticket"))

    def test_a3_the_codex_worker_sees_the_new_objective(self):
        """A3 codex worker:stdin 的派工文同樣是 NEW、不含 OLD。"""
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX.replace("@SANDBOX@", self.home).replace("@REPO@", self.repo)
                         .replace("@MODE@", "deliver"))
        self.stale_base("OBJ-OLD-from-base", "OBJ-NEW-from-store", model=CODEX_MODEL, tool="codex")
        self.auto_fix("--no-review")
        packet = self.read("codex.stdin", where=self.home)
        self.assertIn(SNAPSHOT, packet)
        self.assertIn("OBJ-NEW-from-store", packet)
        self.assertNotIn("OBJ-OLD-from-base", packet)


class TheGuardSeesTheMainRepo(GuardBase):
    """A4:worker 對主 repo 的每一種改動都抓得到。"""

    def test_a4_a_new_file_in_the_main_repo(self):
        """A4 新增主 repo 未追蹤檔 → rc 8 一整組。"""
        self.arm('echo stray > "$REPO/stray-new.txt"')
        self.assert_stopped(self.auto_fix("--no-review"), "stray-new.txt")

    def test_a4_a_deleted_tracked_file(self):
        """A4 刪掉主 repo 已追蹤檔 → rc 8 一整組。"""
        self.arm('rm "$REPO/docs/REHEARSAL.md"')
        self.assert_stopped(self.auto_fix("--no-review"), "REHEARSAL.md")

    def test_a4_a_modified_tracked_file(self):
        """A4 改主 repo 已追蹤檔 → rc 8 一整組。"""
        self.arm('echo changed >> "$REPO/README"')
        self.assert_stopped(self.auto_fix("--no-review"), "README")

    def test_a4_an_already_dirty_file_changed_again(self):
        """A4 開工前就髒的已追蹤檔被再改一次(porcelain 前後都是 ` M`)→ 仍 rc 8。"""
        self.arm('echo again >> "$REPO/docs/DECISIONS.md"')
        self.write("docs/DECISIONS.md", self.read("docs/DECISIONS.md") + "開工前就髒了\n")
        self.assert_stopped(self.auto_fix("--no-review"), "DECISIONS.md")

    def test_a4_head_moved_without_a_land(self):
        """A4 worker 在主 repo commit(HEAD 前進、沒有 land 事件)→ rc 8,證據指 HEAD。"""
        self.arm('git -C "$REPO" commit -q --allow-empty -m "worker 偷 commit"')
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a4_the_branch_was_switched(self):
        """A4 worker 把主 repo 切到別的分支(sha 不變)→ rc 8,證據指 HEAD / 分支。"""
        self.arm('git -C "$REPO" checkout -q -b worker-branch')
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")


class TheGuardSeesGuardedDirs(GuardBase):
    """A4 + A5:guarded_dirs 的新增 / 刪除 / 改動;~ 展開、相對主 repo 解析。"""

    def setUp(self):
        super().setUp()
        for name in ("g", "o"):
            os.makedirs(os.path.join(self.home, name))
        self.write("g/kept.txt", "原本\n", where=self.home)
        self.write("o/kept.txt", "原本\n", where=self.home)

    def test_a4_a_new_file_under_a_guarded_dir(self):
        """A4 guarded_dirs ["~/g"] 底下新增檔 → rc 8(~ 展開成 HOME)。"""
        self.arm('echo new > "$SANDBOX/g/added.txt"', guarded=["~/g"])
        self.assert_stopped(self.auto_fix("--no-review"), "added.txt")

    def test_a4_a_deleted_file_under_a_guarded_dir(self):
        """A4 guarded_dirs ["../o"](相對主 repo)底下刪檔 → rc 8。"""
        self.arm('rm "$SANDBOX/o/kept.txt"', guarded=["../o"])
        self.assert_stopped(self.auto_fix("--no-review"), "o/kept.txt")

    def test_a4_a_modified_file_under_a_guarded_dir(self):
        """A4 guarded_dirs ["~/g"] 底下改檔 → rc 8。"""
        self.arm('echo changed >> "$SANDBOX/g/kept.txt"', guarded=["~/g"])
        self.assert_stopped(self.auto_fix("--no-review"), "g/kept.txt")

    def test_a5_missing_or_empty_guarded_dirs_watch_only_the_main_repo(self):
        """A5 guarded_dirs 缺 / [] → 寫主 repo 外的檔不擋(也不宣稱看過)。"""
        for guarded in (None, []):
            with self.subTest(guarded=guarded):
                self.setUp()
                self.arm('echo x >> "$SANDBOX/g/kept.txt"', guarded=guarded)
                self.assert_not_stopped(self.auto_fix("--no-review"))

    def test_a5_an_unlisted_dir_is_not_watched_when_another_is(self):
        """A5 只列 ~/g → 改 ~/o 不擋(只查列出來的地方)。"""
        self.arm('echo x >> "$SANDBOX/o/kept.txt"', guarded=["~/g"])
        self.assert_not_stopped(self.auto_fix("--no-review"))


class OnlyTheControlPlaneIsExempt(GuardBase):
    """A5:豁免只有 config 解析出的 tickets / events / answers / reports。"""

    def test_a5_a_write_under_tickets_is_not_a_violation(self):
        """A5 寫 tickets/ 底下(控制面)→ 不擋。"""
        self.arm('echo note > "$REPO/tickets/zz-note.txt"')
        self.assert_not_stopped(self.auto_fix("--no-review"))

    def test_a5_a_write_elsewhere_under_board_is_a_violation(self):
        """A5 board/ 底下不是 events / answers 的檔 → 擋(豁免不是整個 board/)。"""
        self.arm('echo x > "$REPO/board/stray-board.txt"')
        self.assert_stopped(self.auto_fix("--no-review"), "stray-board.txt")


class TheGuardCoversEveryAgent(GuardBase):
    """A4:驗證者、批次 worker 走同一份守衛。"""

    def test_a4_a_verifier_outside_its_copy_is_stopped(self):
        """A4 needs_verifier 票的驗證者在主 repo 寫檔 → rc 8 一整組、驗證者副本留著。"""
        self.arm('echo stray > "$REPO/verifier-stray.txt"', who="verifier", needs_verifier=True)
        self.assert_stopped(self.auto_fix("--no-review"), "verifier-stray.txt", who="verifier")


class TheBatchWorkerIsGuarded(AutoFixBase):
    """A4:批次 worker(auto-fix --batch)越界也 rc 8、整批 Blocked / owner=main、一頁。"""

    def test_a4_a_batch_worker_outside_its_copy_is_stopped(self):
        """A4 批次 worker 寫主 repo → rc 8、票 1 / 2 Blocked owner=main、唯一 decision 指路徑、land/X 不動。"""
        batch = self.make_batch()
        self.set_worker("""#!/bin/sh
set -e
echo "worker ran batch round $AC_ROUND" >> "$AC_TEST_LOG"
echo stray > "%s/batch-stray.txt"
cd "$AC_WORK"
printf 'one fixed by the batch\\n' > work/src/one.txt
diff -ruN base work > patch-round1.diff || true
printf '# EVIDENCE\\n修了 src/one.txt\\n' > EVIDENCE-round1.md
""" % self.repo)
        done = self.run_sh("scripts/auto-fix.sh", "--batch", "land/X", "1", "2",
                           env=self.env(AC_BATCH_RUN_ID=batch["run_id"]))
        self.assertEqual(done.returncode, GUARD_RC, done.stdout + done.stderr)
        for ident in ("1", "2"):
            row = self.load_ticket(ident)
            self.assertEqual((row["state"], row.get("owner")), ("Blocked", "main"), ident)
        pages = [row for row in self.inbox_rows() if row.get("kind") == "decision"]
        self.assertEqual(len(pages), 1, pages)  # pin:票面 A4「唯一 decision」
        self.assertIn("batch-stray.txt", pages[0]["what"])
        self.assertEqual(self.git("rev-parse", "land/X").strip(), batch["land"])


class OnlyAnAttributedLandExplainsHead(GuardBase):
    """A6:land.start 不是萬用豁免;要同一次 land 的識別 + 對得上的 HEAD。"""

    def test_a6_a_matching_land_start_and_pass_is_not_a_violation(self):
        """A6 同 stamp 的 land.start + land.pass、pass 的 sha = 新 HEAD → 不擋。"""
        self.arm(LAND_PASS_MATCHES)
        self.assert_not_stopped(self.auto_fix("--no-review"))

    def test_a6_a_land_start_alone_does_not_excuse_head(self):
        """A6 只有 land.start、HEAD 前進、沒有 pass → rc 8。"""
        self.arm('emit land.start --note "只有開始" --kv stamp=S9\n'
                 'git -C "$REPO" commit -q --allow-empty -m "不是落地"')
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a6_a_pass_whose_sha_is_not_the_new_head_does_not_excuse_it(self):
        """A6 land.start + land.pass 同 stamp,但 pass 的 sha 是舊 HEAD → rc 8。"""
        self.arm('OLD="$(git -C "$REPO" rev-parse --short HEAD)"\n'
                 'emit land.start --note "對不上" --kv stamp=S9\n'
                 'git -C "$REPO" commit -q --allow-empty -m "對不上的落地"\n'
                 'emit land.pass --note "對不上" --kv stamp=S9 --kv sha="$OLD"')
        self.assert_stopped(self.auto_fix("--no-review"), "HEAD")

    def test_a6_a_legal_land_does_not_hide_a_worktree_change(self):
        """A6 合法 land 同時,工作樹多一個未歸屬的檔 → 仍 rc 8,指那個檔。"""
        self.arm(LAND_PASS_MATCHES + 'echo stray > "$REPO/stray-during-land.txt"')
        self.assert_stopped(self.auto_fix("--no-review"), "stray-during-land.txt")


class QuotaDoesNotSwallowAViolation(GuardBase):
    """A7:額度與越界同時發生,守衛照做;只撞額度照舊 rc 7。"""

    def test_a7_quota_with_a_violation_is_still_a_violation(self):
        """A7 假 codex 越界又撞 429 → rc 8、票 Blocked / owner=main 不改派、reason=guard-violation、頁指路徑。"""
        self.arm_codex("quota-stray")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, GUARD_RC, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual((row["state"], row.get("owner"), row["tool"]), ("Blocked", "main", "codex"))
        self.assertEqual(self.failed_reasons(), [REASON])
        pages = self.decisions()
        self.assertEqual(len(pages), 1, pages)  # pin:票面 A4「唯一 decision」
        self.assertIn("codex-stray.txt", self.page_text(pages[0]))

    def test_a7_quota_alone_keeps_rc7_ready_and_opus(self):
        """A7 只撞 429、沒越界 → 照舊 rc 7、回 Ready、改派 opus / claude-code、沒有 guard-violation。"""
        self.arm_codex("quota")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, QUOTA_RC, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual((row["state"], row["model"], row["tool"]), ("Ready", "opus", "claude-code"))
        self.assertNotIn(REASON, self.failed_reasons())


class DocsAndScriptsSayD048(GuardBase):
    """A8:文件寫 D-048 現況與 rc=8、能力標待實證;腳本不再用 workspace-write 起 worker。"""

    def doc(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_a8_roles_and_workflow_state_d048_rc8_and_pending_smoke(self):
        """A8 ROLES.md / WORKFLOW.md 各有 D-048、rc=8、「待專案同步後冒煙實證」。"""
        for rel in ("docs/ROLES.md", "docs/WORKFLOW.md"):
            with self.subTest(rel):
                text = self.doc(rel)
                for word in ("D-048", "rc=8", "待專案同步後冒煙實證"):
                    self.assertIn(word, text, "%s 少了 %s" % (rel, word))

    def test_a8_the_scripts_no_longer_launch_workspace_write(self):
        """A8 scripts/ticket.py、scripts/auto-fix.sh 沒有 workspace-write;乾跑 codex 票的 worker 行是 danger-full-access。"""
        for rel in ("scripts/ticket.py", "scripts/auto-fix.sh"):
            with self.subTest(rel):
                self.assertNotIn(OLD_SANDBOX, self.doc(rel))
        self.arm_codex()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith(WORKER_LINE)]
        self.assertTrue(line, done.stdout)
        self.assertIn("-s danger-full-access", line[0])
        self.assertNotIn(OLD_SANDBOX, line[0])


if __name__ == "__main__":
    unittest.main()
