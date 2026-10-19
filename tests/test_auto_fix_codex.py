"""`scripts/auto-fix.sh` 的 codex 路徑(#87 A19–A25;#121 起由角色表選):`board/role-models.json`
的候選決定 worker 起什麼,票的 `model` / `tool` 只是建議(兩格不一致仍拒派)。

以前 auto-fix 不讀票的 model / tool:票寫 `codex:gpt-6-astra`、`tool=codex`,起的照樣是
config 的 `claude -p --model opus`,events 記的是 opus —— 派工方以為在用 codex,實際上沒有。

假 codex 是一支放在沙盒 PATH 最前面的可執行檔:把自己的 argv 與 stdin 各存一份,再依
@MODE@ 交件 / 撞額度 / 別的失敗 / 卡住。config 的 `worker.command` 一律是 claude 那一句,
而沙盒的 `claude` 是絆線 —— 走錯路的那一刻 cleanup 就紅。期望值(argv、model 字串、頁面
那一句)寫死在這裡,不從被測腳本算回去。
"""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, fixture_candidate, write_executable  # noqa: E402
from test_auto_fix import AutoFixBase  # noqa: E402

CODEX_MODEL = "codex:gpt-6-astra"
# 真 codex(不帶 --json)撞額度時 stderr 的那一句,逐字取自
# reports/t122/20261018-231201-28931/verifier-round1.log 末兩行(#133)。
REAL_QUOTA = ("ERROR: You've hit your usage limit. Upgrade to Pro "
              "(https://chatgpt.com/explore/pro), visit "
              "https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
              "at 11:50 PM.")
CLAUDE_COMMAND = "claude -p --model opus --permission-mode acceptEdits"

FAKE_CODEX = """#!/bin/sh
printf '%s\\n' "$@" > "@ARGV@"
printf '%s\\n%s\\n%s\\n' "$(pwd -P)" "$AC_ROLE" "$AC_ROOT" > "@ARGV@.where"
cp "$AC_ROOT/tickets/1.json" "@ARGV@.control-ticket" 2>/dev/null || true
cp "$AC_WORK/work/tickets/1.json" "@ARGV@.work-ticket" 2>/dev/null || true
cat > "@STDIN@"
echo "codex ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
case "@MODE@" in
    quota)
        echo '{"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}'
        exit 1 ;;
    quota-stray)
        echo stray > "$(dirname "$AC_TEST_LOG")/repo/stray.txt"
        echo '{"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}'
        exit 1 ;;
    text429)
        echo "ERROR: stream error: 429 Too Many Requests"
        echo "You've hit your usage limit." >&2
        exit 1 ;;
    other)
        echo "ERROR: something unrelated broke" >&2
        exit 1 ;;
    plainquota)
        echo "@REAL_QUOTA@" >&2
        echo "@REAL_QUOTA@" >&2
        exit 1 ;;
    hang)
        exec sleep 60 ;;
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


class CodexBase(AutoFixBase):

    def setUp(self):
        super().setUp()
        self.argv_file = os.path.join(self.home, "codex-argv.txt")
        self.stdin_file = os.path.join(self.home, "codex-stdin.md")
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": CLAUDE_COMMAND, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def fake_codex(self, mode="deliver"):
        write_executable(os.path.join(self.stub_bin, "codex"),
                         FAKE_CODEX.replace("@ARGV@", self.argv_file)
                         .replace("@STDIN@", self.stdin_file).replace("@MODE@", mode)
                         .replace("@REAL_QUOTA@", REAL_QUOTA))

    def make_ticket(self, ident, **fields):
        """票寫 codex(`tool=codex` 且 `codex:` 前綴)時表頭也換成它(#121:起什麼由表選)。
        只在開票這一手換、不在 setUp:子類別(#114 / #115 的驗證案例)開的 claude 票要照
        沙盒預設表起 opus —— setUp 一換,它們起的是 PATH 上真的 codex。案例自己先排好
        codex 開頭的鏈就不動它。"""
        model = str(fields.get("model") or "")
        if fields.get("tool") == "codex" and model.startswith("codex:"):
            roles = json.loads(self.read("board/role-models.json"))["roles"]
            if (roles.get("worker") or [{}])[0].get("tool") != "codex":
                self.set_role_models(worker=[fixture_candidate(model, "codex")])
        return super().make_ticket(ident, **fields)

    def codex_ticket(self, **fields):
        fields.setdefault("model", CODEX_MODEL)
        fields.setdefault("tool", "codex")
        return self.ticket_ready(**fields)

    def codex_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("codex ran")]

    def agent_events(self, kind=None):
        return [row for row in self.events() if row["kind"].startswith("agent.")
                and (kind is None or row["kind"] == kind)]

    def argv(self):
        with open(self.argv_file, encoding="utf-8") as handle:
            return handle.read().splitlines()


class TheCodexPathStartsCodex(CodexBase):

    def test_a19_a_codex_ticket_runs_codex_exec_with_the_dispatch_on_stdin(self):
        """A19。**變異 M10**:拿掉 tool 判斷(恆走 claude)→ argv 檔不在、絆線踩到,紅。"""
        self.fake_codex()
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # #115 A1/A2:與 claude 同權限;`--add-dir <副本根>` 恰一次,接在 `-` 之前。
        delivery_root = os.path.realpath(os.path.join(
            self.home, "repo-wt", "fix-t1", "round1"))
        argv = self.argv()
        self.assertEqual(argv.count("--add-dir"), 1, argv)
        add_dir = argv.index("--add-dir")
        self.assertEqual(os.path.realpath(argv[add_dir + 1]), delivery_root,
                         "Codex 要能寫副本根的 patch 與 EVIDENCE")
        self.assertEqual(argv[:add_dir] + argv[add_dir + 2:],
                         ["exec", "-m", "gpt-6-astra", "-s", "danger-full-access",
                          "-c", 'approval_policy="never"', "-C", ".",
                          "--skip-git-repo-check", "-"])
        self.assertNotIn("workspace-write", argv)
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            cwd, role, control = handle.read().splitlines()
        self.assertEqual(cwd, os.path.join(delivery_root, "work"), "codex 與 claude 同一個 cwd")
        self.assertEqual(role, "worker")
        self.assertEqual(os.path.realpath(control), os.path.join(delivery_root, "control"),
                         "AC_ROOT 是拋棄式控制根,不是主 repo")
        self.assertIn("codex exec -m gpt-6-astra -s danger-full-access", done.stderr + done.stdout)
        sent = [path for path in self.result_files("dispatch-round1.md")
                if os.path.exists(os.path.join(os.path.dirname(path), "worker-round1.log"))]
        self.assertEqual(len(sent), 1, self.result_files("dispatch-round1.md"))
        with open(sent[0], encoding="utf-8") as handle, \
                open(self.stdin_file, encoding="utf-8") as fed:
            self.assertEqual(fed.read(), handle.read(), "派工文沒有原樣從 stdin 進 codex")
        with open(sent[0], encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("## 權威票面快照", packet)
        self.assertIn("$AC_ROOT/tickets/1.json", packet)
        self.assertIn("work/tickets/1.json", packet)
        self.assertIn("不能用它推翻本快照", packet)
        for kind in ("agent.start", "agent.done"):
            rows = self.agent_events(kind)
            self.assertEqual([row["model"] for row in rows], [CODEX_MODEL], kind)
        cost = self.load_ticket("1")["cost"]
        self.assertEqual([row["model"] for row in cost if row["role"] == "worker"],
                         [CODEX_MODEL])
        self.assertIn("test_thing.py", self.git("ls-tree", "-r", "--name-only", "t1"),
                      "codex 交的 patch 沒有照既有那一手收進分支")

    def test_115_a3_the_snapshot_comes_from_the_live_store_not_the_base_copy(self):
        """#115 A3。base_sha 上的票寫舊 objective、權威票庫寫新的:派工文的快照與控制根的票副本
        是新的,work/ 那一份是舊的,派工文明說舊檔不能推翻快照。**變異**:快照改讀
        work/tickets → 派工文出現舊值,紅。"""
        self.fake_codex()
        row = self.codex_ticket(objective="OLD-objective-from-base")
        row.update(objective="NEW-objective-from-store",
                   base_sha=self.git("rev-parse", "main").strip())
        self.write("tickets/1.json", json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.stdin_file, encoding="utf-8") as handle:
            packet = handle.read()
        self.assertIn("NEW-objective-from-store", packet)
        self.assertNotIn("OLD-objective-from-base", packet)
        self.assertIn("不能用它推翻本快照", packet)
        with open(self.argv_file + ".control-ticket", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "NEW-objective-from-store")
        with open(self.argv_file + ".work-ticket", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["objective"], "OLD-objective-from-base",
                             "夾具要讓 work/ 真的拿到舊版,不然這一條什麼都沒比")

    def test_a19_dry_run_prints_the_codex_command(self):
        self.fake_codex()
        self.codex_ticket()
        done = self.auto_fix("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [l for l in done.stdout.splitlines() if l.startswith("auto-fix: worker 命令 —— ")]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertTrue(line[0].split(" —— ", 1)[1].startswith(
            "codex exec -m gpt-6-astra -s danger-full-access"), line[0])
        self.assertFalse(os.path.exists(self.argv_file), "--dry-run 不起 codex")


class TheClaudePathIsUnchanged(CodexBase):

    def test_a20_claude_code_or_no_tool_runs_the_config_command(self):
        """A20:tool=claude-code、或 tool 缺而 model=opus → config 的 worker.command,
        外加 with_envelope 補的 --output-format stream-json --verbose。"""
        want = CLAUDE_COMMAND + " --output-format stream-json --verbose"
        self.set_role_models(worker=[fixture_candidate("opus")])
        for drop_tool in (False, True):
            with self.subTest(drop_tool=drop_tool):
                row = self.make_ticket("1", allowed_write_paths=["tests/*"], model="opus",
                                       tool="claude-code")
                if drop_tool:
                    row.pop("tool")
                    self.write(os.path.join("tickets", "1.json"),
                               json.dumps(row, ensure_ascii=False, indent=2) + "\n")
                done = self.auto_fix("--dry-run")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("auto-fix: worker 命令 —— " + want, done.stdout.splitlines())


class InconsistentFieldsAreNotDispatched(CodexBase):

    def test_a21_model_and_tool_that_disagree_stop_before_the_ticket_moves(self):
        """A21。**變異**:拿掉一致性檢查 → rc 不是 2 / 有 agent.start,紅。"""
        self.fake_codex()
        for model, tool in (("opus", "codex"), (CODEX_MODEL, "claude-code")):
            with self.subTest(model=model, tool=tool):
                self.make_ticket("1", allowed_write_paths=["tests/*"], model=model, tool=tool)
                done = self.auto_fix("--no-review")
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                named = [l for l in done.stderr.splitlines()
                         if "model=%s" % model in l and "tool=%s" % tool in l]
                self.assertTrue(named, done.stderr)
                self.assertEqual(self.load_ticket("1")["state"], "Ready")
                self.assertEqual(self.agent_events("agent.start"), [])
                self.assertFalse(os.path.exists(self.argv_file))


FAKE_OPUS = """#!/bin/sh
printf '%s\\n' "$@" > "@ARGV@"
pwd -P > "@ARGV@.where"
cat > "@ARGV@.stdin"
echo "opus ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
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


class FallbackBase(CodexBase):
    """#121 A2–A4:額度 / 不可用 → 依表換下一個候選,同一輪、同一份副本。期望的 argv、model、
    effort 寫死在這裡(表的值),不從被測腳本算回去。下一個候選是一支放在 worker.command 的
    假 opus(claude-code 候選換上 `--model opus --effort high`)。"""

    def setUp(self):
        super().setUp()
        self.opus_argv = os.path.join(self.home, "opus-argv.txt")
        path = os.path.join(self.home, "fake-opus.sh")
        write_executable(path, FAKE_OPUS.replace("@ARGV@", self.opus_argv))
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % path, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(worker=[fixture_candidate(CODEX_MODEL, "codex", "high", "xhigh"),
                                     fixture_candidate("opus", "claude-code", "high")])

    def opus_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.startswith("opus ran")]

    def opus_args(self):
        with open(self.opus_argv, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def worker_cost(self):
        return [row for row in self.load_ticket("1").get("cost") or [] if row["role"] == "worker"]


class CodexNotOnPath(FallbackBase):

    def path_without_codex(self):
        return os.pathsep.join([self.stub_bin] + [
            where for where in os.environ.get("PATH", "").split(os.pathsep)
            if where and not os.path.exists(os.path.join(where, "codex"))])

    def test_a2_codex_missing_takes_the_next_candidate_before_starting(self):
        """A2「CLI 已知不可用可於啟動前選下一候選」。**變異**:拿掉啟動前的 PATH 檢查 → 起 codex
        撞 rc=127 那一列 cost 多出來 / agent.start 有 codex,紅。四問:量 agent.start 的 model 與
        cost;值 = A2;舊值(#87 A22 rc=2 停)會紅在 rc;不靠沒設就退回(表真的兩格)。"""
        self.codex_ticket()
        done = self.run_sh("scripts/auto-fix.sh", "1", "--no-review",
                           env=self.env(PATH=self.path_without_codex()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("codex 不在 PATH", done.stderr)
        self.assertEqual([row["model"] for row in self.agent_events("agent.start")], ["opus"])
        self.assertEqual([(row["model"], row["effort"]) for row in self.worker_cost()],
                         [("opus", "high")])
        self.assertEqual(len(self.opus_calls()), 1)

    def test_a3_codex_missing_with_no_other_candidate_starts_nothing(self):
        """A3「沒有候選則不再起程序」:表只有 codex 那一格 → Blocked + 一頁,不起、不記 cost。"""
        self.set_role_models(worker=[fixture_candidate(CODEX_MODEL, "codex")])
        self.codex_ticket()
        done = self.run_sh("scripts/auto-fix.sh", "1", "--no-review",
                           env=self.env(PATH=self.path_without_codex()))
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["state"], ticket.get("owner")), ("Blocked", "main"))
        self.assertEqual(ticket.get("cost") or [], [])
        self.assertEqual(self.agent_events("agent.start"), [])
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("候選整串耗盡", pages[0]["state"])


class QuotaTakesTheNextCandidate(FallbackBase):

    def test_a2_a_quota_envelope_relaunches_opus_in_the_same_copy(self):
        """T1:fake codex 先 quota、fake opus 交件。**M1**(保留舊的改派 opus + 回 Ready + rc 7)→
        rc 不是 0、opus 沒起,紅;**M2**(額度後 rm -rf 副本)→ opus 的 cwd 不在 / patch 沒進分支,紅。
        四問:量 rc、兩次啟動、cwd、cost、事件;值 = A2/A4;舊值紅在 rc 與 opus_calls。"""
        self.assert_relaunched_opus("quota")

    def test_133_a2_the_plain_codex_usage_limit_line_relaunches_opus(self):
        """#133 T4 / A2:真 codex 不帶 --json,撞額度只在 stderr 印兩次純文字 ERROR: 行。同一組斷言。
        **M1**(刪掉結尾 ERROR: 段的判讀)→ rc 5、opus 沒起,紅。"""
        self.assert_relaunched_opus("plainquota")

    def assert_relaunched_opus(self, mode):
        self.fake_codex(mode)
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((len(self.codex_calls()), len(self.opus_calls())), (1, 1))
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            codex_cwd = handle.read().splitlines()[0]
        with open(self.opus_argv + ".where", encoding="utf-8") as handle:
            opus_cwd = handle.read().strip()
        self.assertEqual(opus_cwd, codex_cwd, "下一個候選要在同一份副本續做")
        argv = self.opus_args()
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--effort") + 1], "high", "opus 接手取表的 high,不是 null")
        starts = self.agent_events("agent.start")
        self.assertEqual([(row["model"], row.get("effort")) for row in starts],
                         [(CODEX_MODEL, "high"), ("opus", "high")])
        cost = self.worker_cost()
        self.assertEqual([(row["model"], row["outcome"], row["effort"]) for row in cost],
                         [(CODEX_MODEL, "error", "high"), ("opus", "handed-in", "high")])
        self.assertIn("quota_exhausted", cost[0]["note"])
        self.assertEqual(sorted(cost[0]), sorted(cost[1]))
        self.assertEqual(len(cost[0]), 15, "不新增 cost 鍵")
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"]), (CODEX_MODEL, "codex"),
                         "選路不改寫票的 model / tool")
        failed = [row.get("reason") for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual(failed, [])
        self.assertIn("test_thing.py", self.git("ls-tree", "-r", "--name-only", "t1"))
        # 票沒標 complexity = simple(#136):綠了不派覆核,終態不是「等覆核」。
        self.assertIn("終態=green_no_review", done.stdout)

    def test_a7_a_codex_only_effort_is_not_carried_to_opus(self):
        """A7:票 effort=ultra(codex 合法、claude-code 不合法)→ opus 取表的 high;票的 effort 不改成 null。"""
        self.fake_codex("quota")
        self.codex_ticket(effort="ultra")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        argv = self.opus_args()
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertEqual(self.load_ticket("1")["effort"], "ultra")
        self.assertEqual([row["effort"] for row in self.worker_cost()], ["ultra", "high"])

    def test_a3_the_whole_chain_used_up_is_one_page_and_no_more_launches(self):
        self.set_role_models(worker=[fixture_candidate(CODEX_MODEL, "codex")])
        self.fake_codex("quota")
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        self.assertEqual((len(self.codex_calls()), len(self.opus_calls())), (1, 0))
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["state"], ticket.get("owner")), ("Blocked", "main"))
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("候選整串耗盡", pages[0]["state"])
        self.assertEqual([row["outcome"] for row in self.worker_cost()], ["error"])

    def test_a3_with_a_controller_only_the_record_is_handed_over(self):
        """A3 / T3 M4:有 Controller 時 auto-fix 只發 `agent.recovery_needed`,自己不起下一個。
        **M4**(拿掉 controller 分支,兩邊都起)→ opus_calls 變 1,紅。fake consumer 讀事件
        即得下一個候選的 model / tool / effort 與留著的副本。"""
        self.fake_codex("quota")
        self.codex_ticket()
        done = self.run_sh("scripts/auto-fix.sh", "1", "--no-review",
                           env=self.env(AC_RECOVERY_OWNER="controller"))
        self.assertEqual(done.returncode, 7, done.stdout + done.stderr)
        self.assertEqual((len(self.codex_calls()), len(self.opus_calls())), (1, 0))
        handed = [row for row in self.events() if row["kind"] == "agent.recovery_needed"]
        self.assertEqual(len(handed), 1, handed)
        row = handed[0]
        self.assertEqual((row["model"], row.get("tool"), row.get("effort"), row.get("candidate_index")),
                         ("opus", "claude-code", "high", "1"))
        self.assertTrue(os.path.isdir(row["copy"]), "副本要留給 Controller 接續")
        self.assertEqual([r["outcome"] for r in self.worker_cost()], ["error"])

    def test_115_a7_quota_and_a_violation_together_keep_the_violation(self):
        """#115 A7。**M5**:額度那一手排在守衛前面 → 換候選、rc 0,紅。"""
        self.fake_codex("quota-stray")
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 8, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"], ticket["state"], ticket.get("owner")),
                         (CODEX_MODEL, "codex", "Blocked", "main"))
        failed = [row for row in self.events() if row["kind"] == "ticket.attempt.failed"]
        self.assertEqual([row.get("reason") for row in failed], ["guard-violation"])
        self.assertEqual(len(self.opus_calls()), 0)
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("stray.txt", pages[0]["what"])


class OtherCodexFailuresKeepTheOldPages(FallbackBase):
    """T2:逾時 / 沒交件 / 只有字面 429 不換模型 —— 表上有 opus 那一格也不起它。"""

    def assert_left_alone(self, done):
        self.assertEqual(len(self.opus_calls()), 0, "一般失敗不換候選")
        ticket = self.load_ticket("1")
        self.assertEqual((ticket["model"], ticket["tool"]), (CODEX_MODEL, "codex"))
        self.assertEqual(ticket["state"], "Blocked")
        self.assertEqual(ticket.get("owner"), "main")

    def test_a24_an_unrelated_failure_without_a_patch_is_the_no_patch_page(self):
        for mode in ("other", "text429"):
            with self.subTest(mode=mode):
                self.fake_codex(mode)
                self.codex_ticket()
                done = self.auto_fix("--no-review")
                self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
                self.assert_left_alone(done)
                pages = self.inbox_rows()
                self.assertTrue(pages)
                self.assertIn("worker 沒交出 patch", self.read(pages[-1]["page"]))

    def test_a24_a_timeout_is_the_timeout_page(self):
        self.fake_codex("hang")
        self.codex_ticket(worker={"timeout_seconds": 2})
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assert_left_alone(done)
        pages = self.inbox_rows()
        self.assertEqual(len(pages), 1, pages)
        self.assertIn("逾時", self.read(pages[0]["page"]))
        self.assertEqual(glob.glob(os.path.join(self.repo, "reports", "t1", "*",
                                                "patch-round1.diff")), [])


class EffortBase(CodexBase):
    """#114 / #121 A7:票的 `effort` > 角色表被選候選的 effort(complex 取 effort_escalated)。
    期望句抄表與票面字面。"""

    def dry_lines(self, done):
        lines = done.stdout.splitlines()
        return ([l for l in lines if l.startswith("auto-fix: worker 命令 —— ")],
                [l for l in lines if l.startswith("auto-fix: effort 來源=")])


class AutoFixPicksTheEffort(EffortBase):

    def test_a7_dry_run_shows_the_effort_and_where_it_came_from(self):
        """**變異 M3**:取值順序顛倒(表蓋票)→「兩者都有」那一列印 medium,紅。
        「票 / 都沒有」兩列表首選是 fable、票寫 opus:A5 新表首選改 fable 就起 fable。"""
        envelope_tail = " --output-format stream-json --verbose"
        fable = [fixture_candidate("fable")]
        codex = [fixture_candidate(CODEX_MODEL, "codex", "medium", "xhigh")]
        rows = (
            ("票", fable, dict(model="", tool="", effort="high"),
             "claude -p --model fable --effort high --permission-mode acceptEdits" + envelope_tail,
             "auto-fix: effort 來源=票 high"),
            ("表", codex, dict(model=CODEX_MODEL, tool="codex"),
             "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"medium\"' "
             "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -",
             "auto-fix: effort 來源=表 medium"),
            ("兩者", codex, dict(model=CODEX_MODEL, tool="codex", effort="xhigh"),
             "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"xhigh\"' "
             "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -",
             "auto-fix: effort 來源=票 xhigh"),
            ("complex", codex, dict(model=CODEX_MODEL, tool="codex", complexity="complex"),
             "codex exec -m gpt-6-astra -c 'model_reasoning_effort=\"xhigh\"' "
             "-s danger-full-access -c 'approval_policy=\"never\"' -C . --skip-git-repo-check -",
             "auto-fix: effort 來源=表 xhigh"),
            ("都沒有", fable, dict(model="", tool=""),
             "claude -p --model fable --permission-mode acceptEdits" + envelope_tail, None),
        )
        self.fake_codex()
        for name, chain, fields, want, source in rows:
            with self.subTest(case=name):
                self.set_role_models(worker=chain)
                self.make_ticket("1", allowed_write_paths=["tests/*"], **fields)
                done = self.auto_fix("--dry-run")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                worker, sources = self.dry_lines(done)
                self.assertEqual(worker, ["auto-fix: worker 命令 —— " + want], done.stdout)
                self.assertEqual(sources, [source] if source else [], done.stdout)

    def test_a5_a_fable_first_table_really_starts_fable(self):
        """A5「新表首選改為 fable 的夾具能實際起 fable」:不是 dry-run,真的起、事件與 cost 記 fable。
        **M1**(model 照舊硬寫 opus)→ argv 的 --model 是 opus,紅。"""
        opus_argv = os.path.join(self.home, "argv.txt")
        path = os.path.join(self.home, "fake.sh")
        write_executable(path, FAKE_OPUS.replace("@ARGV@", opus_argv))
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s --model opus" % path, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(worker=[fixture_candidate("fable", effort="high")])
        self.ticket_ready(model="", tool="")
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(opus_argv, encoding="utf-8") as handle:
            argv = handle.read().splitlines()
        self.assertEqual(argv[argv.index("--model") + 1], "fable")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertEqual([row["model"] for row in self.agent_events("agent.start")], ["fable"])
        cost = [row for row in self.load_ticket("1")["cost"] if row["role"] == "worker"]
        self.assertEqual([(row["model"], row["effort"]) for row in cost], [("fable", "high")])


class AnIllegalEffortIsNotDispatched(EffortBase):

    def test_a8_an_illegal_effort_stops_before_the_ticket_moves(self):
        """T4:票 ultra、表首選 claude-code → exit 2,票不動、不起、不記。
        **變異**:auto-fix 拿掉早停 → 票轉 Running / 有 agent.start,紅。"""
        self.fake_codex()
        self.set_role_models(worker=[fixture_candidate("opus")])
        for args in (("--no-review",), ("--dry-run",)):
            with self.subTest(args=args):
                self.make_ticket("1", allowed_write_paths=["tests/*"], tool="claude-code",
                                 effort="ultra")
                before = self.load_ticket("1")
                done = self.auto_fix(*args)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                named = [l for l in done.stderr.splitlines() if "effort=ultra" in l and "票" in l]
                self.assertTrue(named, done.stderr)
                after = self.load_ticket("1")
                self.assertEqual(after["state"], "Ready")
                self.assertEqual(after["state_version"], before["state_version"])
                self.assertEqual(after.get("cost") or [], [])
                self.assertEqual(self.agent_events("agent.start"), [])
                self.assertFalse(os.path.exists(self.argv_file))


if __name__ == "__main__":
    unittest.main()
