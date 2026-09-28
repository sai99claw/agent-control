"""`scripts/guard-main.sh` + `.claude/settings.json`:主線越界的機械守衛(#63)。

主線 compact 之後自寫票面、讀 worker log 與 task output、裸 commit 主線 —— 規則靠自覺,
在 compact 後失效。這一組釘的是:認得的越界被 deny、理由一行指路;流程欄與控制腳本照放;
子代理人 / 非主線 / 副本裡一個位元組都不印;逃生口放行但留一筆事件,每一次擋也留一筆。

期望值的來源都在被測腳本之外:deny 的 JSON 形狀抄自 hooks 文件
(code.claude.com/docs/en/hooks,PreToolUse 的 `hookSpecificOutput.permissionDecision`)、
案例指令逐字抄自票面驗收、事件數是直接數事件檔的行數。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox  # noqa: E402

SETTINGS = os.path.join(ROOT, ".claude", "settings.json")
WORKTREE = "../agent-control-wt"
CREATE = "python3 scripts/ticket.py create --subject x"


class F1Wiring(unittest.TestCase):

    def test_f1_settings_call_the_guard_on_bash_and_read(self):
        with open(SETTINGS, encoding="utf-8") as handle:
            conf = json.load(handle)
        hits = [(entry, hook) for entry in conf["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
                if "scripts/guard-main.sh" in hook.get("command", "")]
        self.assertEqual(len(hits), 1, conf["hooks"]["PreToolUse"])
        entry, hook = hits[0]
        self.assertTrue({"Bash", "Read"} <= set(entry["matcher"].split("|")), entry)
        self.assertEqual(hook["type"], "command")
        self.assertTrue(hook.get("timeout"), "timeout 要有值")


class GuardSandbox(Sandbox):
    config_extra = {"worktree_dir": WORKTREE}

    def setUp(self):
        super().setUp()
        target = os.path.join(self.repo, "scripts", "guard-main.sh")
        shutil.copy(os.path.join(SCRIPTS, "guard-main.sh"), target)
        os.chmod(target, 0o755)

    def guard(self, command=None, file_path=None, cwd=None, agent_id=None, raw=None, **env):
        cwd = cwd or self.repo
        if raw is None:
            if file_path is not None:
                payload = {"tool_name": "Read", "tool_input": {"file_path": file_path}}
            else:
                payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            payload.update({"cwd": cwd, "hook_event_name": "PreToolUse"})
            if agent_id is not None:
                payload["agent_id"] = agent_id
                payload["agent_type"] = "general-purpose"
            raw = json.dumps(payload)
        return subprocess.run(
            ["sh", os.path.join(self.repo, "scripts", "guard-main.sh")],
            cwd=cwd, env=self.env(**env), input=raw,
            capture_output=True, text=True, timeout=TIMEOUT)

    def set_config(self, **keys):
        conf = json.loads(self.read("board/config.json"))
        conf.update(keys)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False))

    def event_lines(self):
        """直接數事件檔的行數 —— 不從被測腳本算。"""
        path = os.path.join(self.repo, "board", "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def assert_denied(self, done, needle):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.count("\n"), 1, "deny 是一行 JSON:" + done.stdout)
        out = json.loads(done.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        reason = out["permissionDecisionReason"]
        self.assertTrue(reason.startswith("guard-main: "), reason)
        self.assertNotIn("\n", reason)
        self.assertIn(needle, reason)
        return reason

    def assert_allowed(self, done):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "", "放行 = stdout 空")


class B1B2TheTicketFace(GuardSandbox):

    def test_b1_create_is_denied(self):
        for command in (CREATE, "python3 scripts/control/ticket.py create"):
            with self.subTest(command=command):
                self.assert_denied(self.guard(command), "開題者")

    def test_b2_set_on_a_face_field_is_denied(self):
        for field in ("subject", "objective", "acceptance", "in_scope", "out_of_scope",
                      "allowed_write_paths", "verify_strings", "test_plan"):
            with self.subTest(field=field):
                self.assert_denied(
                    self.guard("python3 scripts/ticket.py set 5 %s '[\"x\"]'" % field),
                    "開題者")


class B3ReadingLogs(GuardSandbox):

    def test_b3_bash_readers_on_a_log_are_denied(self):
        for command in ("cat reports/t12/20260929-1/worker-round1.log",
                        "tail -50 ../x-wt/reports/t3/r/gate.log",
                        "sed -n 1,9p worker-round2.log",
                        "grep FAIL reports/t7/r/x.log"):
            with self.subTest(command=command):
                self.assert_denied(self.guard(command), "inbox.py show")

    def test_b3_read_on_a_task_output_is_denied(self):
        self.assert_denied(self.guard(file_path="/private/tmp/claude/x/tasks/abc123.output"),
                           "inbox.py show")


class B4BareGit(GuardSandbox):

    def test_b4_commit_and_merge_point_at_land_sh(self):
        """**變異**:git 子指令改成子字串比對 → C2 的 echo 案例紅(另一條)。"""
        for command in ("git commit -m x", "git -C . merge t5", "cd x && git commit -am y"):
            with self.subTest(command=command):
                reason = self.assert_denied(self.guard(command), "land.sh")
                self.assertIn("docs", reason)
                self.assertIn("sh scripts/land.sh", reason)
                self.assertIn("land.sh docs", reason)

    def test_g2_canon_with_a_land_key_follows_the_key(self):
        self.set_config(land=LAND)
        reason = self.assert_denied(self.guard("git commit -m x"), LAND["entry"])
        self.assertIn(LAND["docs"], reason)


# 下游專案的落地入口不是 scripts/land.sh(那一支可能是別的東西),兩支都放進沙盒。
LAND = {"entry": "sh scripts/land-ticket.sh <分支…>",
        "docs": 'sh scripts/land-ticket.sh docs "<訊息>" <檔…>'}


class B4ProjectLayout(GuardSandbox):
    config_extra = {"worktree_dir": WORKTREE,
                    "rules": {"roles_dir": "docs/roles", "models_dir": "docs/roles/model"}}

    def setUp(self):
        super().setUp()
        self.write("scripts/land-ticket.sh", "#!/bin/sh\n")
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "scripts", "land.sh")))

    def test_g1_the_declared_entry_is_printed_not_land_sh(self):
        """**變異**:改回 os.path.exists(scripts/land.sh) 判斷 → 這一條紅。"""
        self.set_config(land=LAND)
        reason = self.assert_denied(self.guard("git commit -m x"), "land-ticket.sh")
        self.assertIn(LAND["entry"], reason)
        self.assertIn(LAND["docs"], reason)
        self.assertNotIn("land.sh", reason.replace("land-ticket.sh", ""))

    def test_g3_without_the_key_points_at_the_table_and_names_the_key(self):
        reason = self.assert_denied(self.guard("git commit -m x"), "對照表")
        self.assertNotIn("scripts/land.sh", reason)
        self.assertIn("land", reason)


class C1C2WhatStaysOpen(GuardSandbox):

    def test_c1_the_process_fields_pass(self):
        for field, value in (("state", "Ready"), ("objections", "[]"),
                             ("verify_waiver", "{}"), ("base_sha", "abc1234"),
                             ("needs_verifier", "false"), ("worker", "{}")):
            with self.subTest(field=field):
                self.assert_allowed(
                    self.guard("python3 scripts/ticket.py set 5 %s '%s'" % (field, value)))

    def test_c2_inbox_and_control_scripts_pass(self):
        for command in ("python3 scripts/inbox.py show 5",
                        "python3 scripts/event.py tail 20",
                        "python3 scripts/ticket.py show 5",
                        "ticket.py list --open",
                        "ticket.py verify 5",
                        'sh scripts/land.sh docs "m" tickets/5.json',
                        "sh scripts/apply.sh 5 p.diff",
                        "sh scripts/review.sh 5",
                        "sh scripts/auto-fix.sh 5",
                        "cat reports/inbox/5-r1.md",
                        "git log --oneline -3",
                        "git merge --abort",
                        'echo "git commit"'):
            with self.subTest(command=command):
                self.assert_allowed(self.guard(command))
        self.assertEqual(self.event_lines(), [], "放行的指令一筆事件都不該發")

    def test_unreadable_input_passes(self):
        for raw in ("not json", "", "[]"):
            with self.subTest(raw=raw):
                self.assert_allowed(self.guard(raw=raw))
        self.assert_allowed(self.guard("python3 scripts/ticket.py create --subject 'x"))


class D1D2TheEscapeHatchAndTheDenominator(GuardSandbox):

    def test_d1_the_prefix_passes_and_leaves_one_override(self):
        """**變異**:拿掉 override 那一次 emit → 這一條紅(事件行數不變)。"""
        before = len(self.event_lines())
        self.assert_allowed(self.guard("AC_MAIN_OVERRIDE=1 " + CREATE))
        rows = self.event_lines()
        self.assertEqual(len(rows), before + 1)
        row = rows[-1]
        self.assertEqual((row["kind"], row["role"]), ("main.override", "main"))
        self.assertIn("ticket.py create", row["note"])
        self.assertLessEqual(len(row["note"]), 200)

    def test_d1_the_hook_environment_passes_too(self):
        before = len(self.event_lines())
        self.assert_allowed(self.guard(CREATE, AC_MAIN_OVERRIDE="1"))
        rows = self.event_lines()
        self.assertEqual([row["kind"] for row in rows[before:]], ["main.override"])

    def test_d1_a_long_command_is_summarised_to_200(self):
        self.assert_allowed(self.guard("AC_MAIN_OVERRIDE=1 " + CREATE + " " + "y" * 400))
        self.assertLessEqual(len(self.event_lines()[-1]["note"]), 200)

    def test_d2_a_block_leaves_one_blocked(self):
        before = len(self.event_lines())
        self.assert_denied(self.guard(CREATE), "開題者")
        rows = self.event_lines()
        self.assertEqual(len(rows), before + 1)
        self.assertEqual((rows[-1]["kind"], rows[-1]["role"]), ("main.blocked", "main"))
        self.assertIn("ticket.py create --subject x", rows[-1]["note"])


class E1NotTheMainLine(GuardSandbox):
    """三條各一案,各自只讓一條成立 —— 揉成一案的話,少實作兩條也綠。"""

    def assert_silent(self, done):
        self.assert_allowed(done)
        self.assertEqual(done.stderr, "")
        self.assertEqual(self.event_lines(), [], "非主線一筆事件都不該發")

    def test_e1a_a_role_that_is_not_main(self):
        self.assert_silent(self.guard(CREATE, AC_ROLE="worker"))

    def test_e1b_a_subagent_call(self):
        """**變異**:拿掉 agent_id 那一條 → 這一條紅。"""
        self.assert_silent(self.guard(CREATE, agent_id="a1b2c3"))

    def test_e1c_a_cwd_under_the_worktree_dir(self):
        where = os.path.join(self.repo, WORKTREE, "t9", "work")
        os.makedirs(where)
        self.assert_silent(self.guard(CREATE, cwd=where))


def sync(dest):
    return subprocess.run(["sh", os.path.join(ROOT, "scripts", "sync-to-project.sh"), dest],
                          capture_output=True, text=True, timeout=120)


class G1Sync(unittest.TestCase):
    LINE = '"command": "sh \\"$CLAUDE_PROJECT_DIR/scripts/control/guard-main.sh\\""'

    def project(self, dest, settings=None):
        os.makedirs(os.path.join(dest, "board"))
        with open(os.path.join(dest, "board", "config.json"), "w") as handle:
            handle.write('{"rules":{"roles_dir":"docs/roles","models_dir":"docs/roles/model"},'
                         '"memory":{"applies_to":["memory/model/*.md"]},'
                         '"land":' + json.dumps(LAND) + '}')
        if settings is not None:
            os.makedirs(os.path.join(dest, ".claude"))
            with open(os.path.join(dest, ".claude", "settings.json"), "w") as handle:
                handle.write(settings)

    def test_g1_it_lands_and_the_missing_hook_is_printed(self):
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(d, "scripts", "control",
                                                        "guard-main.sh")))
            lines = [x for x in r.stdout.splitlines() if self.LINE in x]
            self.assertTrue(lines, r.stdout)
            self.assertIn('"matcher": "Bash|Read"', lines[0])
            self.assertIn("PreToolUse", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "settings.json")),
                             "sync 不自己改專案的 settings.json")

    def test_g1_a_wired_project_is_not_told_again_and_its_settings_stay(self):
        wired = json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [
            {"type": "command", "timeout": 10,
             "command": 'sh "$CLAUDE_PROJECT_DIR/scripts/control/guard-main.sh"'}]}]}})
        with tempfile.TemporaryDirectory() as d:
            self.project(d, wired)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse([x for x in r.stdout.splitlines() if self.LINE in x], r.stdout)
            with open(os.path.join(d, ".claude", "settings.json")) as handle:
                self.assertEqual(handle.read(), wired)


if __name__ == "__main__":
    unittest.main()
