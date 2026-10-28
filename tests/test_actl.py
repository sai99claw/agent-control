"""`bin/actl` 與找根規則(D-071 C1–C2 §1,D-072;#159)。

A 是一支裝好的工具:專案 clone A、在原地跑。專案根(`AC_ROOT`,票 / 事件 / reports)**不從
`$0` 來** —— 舊的 `_ac_root()` 在 A 的 clone 裡往上找永遠找到 A 自己的 `board/config.json`,
票與事件寫進 A,而且不報錯(D-071 ① 的原點)。這一組釘新規則:`AC_ROOT` > `-C` >
`CLAUDE_PROJECT_DIR` > cwd 的主 repo 根 > rc 2;程式碼根 `AC_CODE_ROOT` > cwd 的 toplevel。

期望的路徑一律取自這裡自己開的 tempdir(`realpath`),期望的票題是這裡寫進沙盒的字串 ——
從不從 `actl` 的輸出讀回來。dogfood 的閘門規則(V3)在 `tests/test_land.py`(`land.sh` 的守衛);
`ac_sha`(A5)在 `tests/test_new_session.py` 與 `tests/test_gate.py`。
"""

import ast
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox  # noqa: E402

SUBJECT = "只有這顆沙盒才有的票 #159"
OTHER_SUBJECT = "另一顆沙盒的票 #159"


def real(path):
    return os.path.realpath(path)


def labelled(stdout):
    """`actl version` 的 `KEY=VALUE` 行 → dict;一行一格,重複的鍵是錯。"""
    out = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            assert key not in out, "重複的鍵 %s" % key
            out[key] = value
    return out


def tree_digest(where):
    """一棵目錄的 (相對路徑, sha256) 清單 —— 「一個位元組都沒動」的量法。不存在就是 None。"""
    if not os.path.exists(where):
        return None
    rows = []
    for base, dirs, files in os.walk(where):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                rows.append((os.path.relpath(path, where), hashlib.sha256(handle.read()).hexdigest()))
    return rows


def file_digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class ActlBase(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(5, subject=SUBJECT)
        self.bare = os.path.join(self.home, "bare")
        os.makedirs(self.bare)

    def actl(self, *args, cwd, keep_root=False, **extra):
        """`sh $AC_HOME/bin/actl …`;預設拿掉 `AC_ROOT`(要測的是 actl 自己找根)。"""
        env = self.env(**extra)
        if not keep_root and "AC_ROOT" not in extra:
            env.pop("AC_ROOT")
        return subprocess.run(["sh", os.path.join(self.ac_home, "bin", "actl"), *args],
                              cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=TIMEOUT)

    def other_project(self):
        """第二顆專案:有 board/config.json 與一張票,不必是 git(`-C` / CLAUDE_PROJECT_DIR 直給)。"""
        other = os.path.join(self.home, "other")
        os.makedirs(os.path.join(other, "board"))
        os.makedirs(os.path.join(other, "tickets"))
        with open(os.path.join(other, "board", "config.json"), "w", encoding="utf-8") as handle:
            handle.write(self.read("board/config.json"))
        row = dict(json.loads(self.read("tickets/5.json")), id="6", subject=OTHER_SUBJECT)
        with open(os.path.join(other, "tickets", "6.json"), "w", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False, indent=2)
        return other


class A1TheEntryFindsTheProject(ActlBase):
    """V1:`actl` 的找根順序。"""

    def test_a1a_in_a_ticket_worktree_the_root_is_the_main_repo(self):
        """(a) cwd = 票 worktree(`worktree_dir` 底下 `git worktree add` 的那一個):AC_ROOT 是主
        repo、AC_CODE_ROOT 是 worktree;`ticket list --open` 列得出沙盒的票(票只在主 repo 工作樹)。"""
        wt = os.path.join(self.home, "repo-wt", "t5")
        self.git("worktree", "add", "-q", "-b", "t5", wt, "main")
        self.assertFalse(os.path.exists(os.path.join(wt, "tickets", "5.json")), "前提")
        version = self.actl("version", cwd=wt)
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        rows = labelled(version.stdout)
        self.assertEqual(rows["AC_ROOT"], real(self.repo))
        self.assertEqual(rows["AC_CODE_ROOT"], real(wt))
        listed = self.actl("ticket", "list", "--open", cwd=wt)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn(SUBJECT, listed.stdout)

    def test_a1b_outside_any_project_is_rc_2_and_touches_nothing(self):
        """(b) cwd = 任何 git 樹之外的空目錄、沒有 -C、沒有 AC_ROOT:rc 2,stderr 說
        `not inside a project` 與 `-C`;那個目錄與 A 自己的 tickets/ 一個位元組都沒動。

        **變異**:`ac_root` 退回 `$0` 的上一層 → rc 0、列出的是 A 的票 → 紅。"""
        a_tickets = os.path.join(ROOT, "tickets")
        before = (sorted(os.listdir(a_tickets)) if os.path.isdir(a_tickets) else None,
                  tree_digest(self.bare))
        done = self.actl("ticket", "list", "--open", cwd=self.bare)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not inside a project", done.stderr)
        self.assertIn("-C", done.stderr)
        self.assertEqual(done.stdout, "")
        after = (sorted(os.listdir(a_tickets)) if os.path.isdir(a_tickets) else None,
                 tree_digest(self.bare))
        self.assertEqual(after, before)

    def test_a1c_dash_c_wins_over_cwd(self):
        """(c) cwd 在沙盒、`-C <另一顆>` → AC_ROOT 是 -C 那一顆;它的票、不是 cwd 那一顆的票。

        **變異 m2**:actl 不理 `-C` → 紅。"""
        other = self.other_project()
        version = self.actl("-C", other, "version", cwd=self.repo)
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        self.assertEqual(labelled(version.stdout)["AC_ROOT"], real(other))
        listed = self.actl("-C", other, "ticket", "list", "--open", cwd=self.repo)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn(OTHER_SUBJECT, listed.stdout)
        self.assertNotIn(SUBJECT, listed.stdout)

    def test_a1d_claude_project_dir_wins_over_cwd(self):
        """(d) cwd 在沙盒、`CLAUDE_PROJECT_DIR=<另一顆>` → AC_ROOT 是那一顆。"""
        other = self.other_project()
        version = self.actl("version", cwd=self.repo, CLAUDE_PROJECT_DIR=other)
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        self.assertEqual(labelled(version.stdout)["AC_ROOT"], real(other))
        listed = self.actl("ticket", "list", "--open", cwd=self.repo, CLAUDE_PROJECT_DIR=other)
        self.assertIn(OTHER_SUBJECT, listed.stdout, listed.stdout + listed.stderr)

    def test_a1_the_order_is_ac_root_then_dash_c_then_claude_project_dir(self):
        """第 1 條 `AC_ROOT` 蓋過 `-C`;`-C` 蓋過 `CLAUDE_PROJECT_DIR`(§1 表的順序)。"""
        other = self.other_project()
        both = self.actl("-C", other, "version", cwd=self.bare, AC_ROOT=self.repo)
        self.assertEqual(labelled(both.stdout)["AC_ROOT"], self.repo)
        both = self.actl("-C", self.repo, "version", cwd=self.bare, CLAUDE_PROJECT_DIR=other)
        self.assertEqual(labelled(both.stdout)["AC_ROOT"], real(self.repo))

    def test_a1e_an_unknown_command_prints_the_table(self):
        """(e) `actl nonsense` → rc 2,stderr 是指令表。"""
        done = self.actl("nonsense", cwd=self.repo)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for word in ("land", "gate", "ticket", "version"):
            self.assertIn(word, done.stderr)

    def test_a1_version_works_outside_any_project(self):
        """`actl version` 在任何專案之外 rc 0,四行各一格;AC_ROOT / AC_CODE_ROOT 是 `(none)`。"""
        done = self.actl("version", cwd=self.bare)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = labelled(done.stdout)
        self.assertEqual(sorted(rows), ["AC_CODE_ROOT", "AC_HOME", "AC_ROOT", "ac_sha"])
        self.assertEqual(rows["AC_HOME"], real(self.ac_home))
        self.assertEqual(rows["AC_ROOT"], "(none)")
        self.assertEqual(rows["AC_CODE_ROOT"], "(none)")
        self.assertTrue(rows["ac_sha"])

    def test_a1_a_root_without_config_names_actl_init(self):
        """根找到了、卻沒有 board/config.json → rc 2,說得出 `actl init`。"""
        empty = os.path.join(self.home, "empty")
        os.makedirs(empty)
        done = self.actl("-C", empty, "ticket", "list", cwd=self.bare)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("actl init", done.stderr)

    def test_a1_no_row_says_not_landed_yet(self):
        # doctor / migrate / init landed in #160 (tests/test_project_config.py), test in #162
        # (tests/test_testiface.py), hooks in #164 (tests/test_hooks.py): no stub row is left.
        done = self.actl("--help", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("not landed yet", done.stdout)
        for command in ("test", "hooks"):
            with self.subTest(command):
                self.assertIn("\n  %s " % command, done.stdout)

    def test_a1_schedule_has_a_row_and_needs_a_subcommand(self):
        """#119: `actl schedule` is in the table and execs scripts/schedule.py; no subcommand -> rc 2."""
        done = self.actl("--help", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("\n  schedule ", done.stdout)
        done = self.actl("schedule", cwd=self.repo)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("plan | run", done.stderr)

    def test_a1_a_child_writes_into_the_resolved_root(self):
        """`actl` export 的 AC_ROOT 就是子行程寫的地方:`-C <另一顆> event emit` 寫進那一顆的
        events.jsonl,cwd 那一顆一筆都沒有。"""
        other = self.other_project()
        done = self.actl("-C", other, "event", "emit", "gate.start", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(other, "board", "events.jsonl"), encoding="utf-8") as handle:
            self.assertEqual([json.loads(line)["kind"] for line in handle if line.strip()],
                             ["gate.start"])
        self.assertEqual(self.events(), [])


class A2TheDollarZeroWalkIsGone(ActlBase):
    """V2 + `event.repo_root()`:根不從腳本的位置推。"""

    def sources(self):
        return {os.path.basename(path): open(path, encoding="utf-8").read()
                for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.sh")))}

    def test_a2_no_root_walk_left_in_the_shell_scripts(self):
        """`_ac_root` 0 處;`dirname "$0")/..` 0 處(D-071 row 6 刪掉的兩支以前不算,#166 起沒有例外)。

        **變異 m1**:放回任何一支的 `_ac_root()` → 紅。"""
        sources = self.sources()
        self.assertIn("land.sh", sources, "前提:量到的是 scripts/*.sh")
        walk = {name: text.count("_ac_root") for name, text in sources.items()
                if "_ac_root" in text}
        self.assertEqual(walk, {})
        dollar0 = {name: text.count('dirname "$0")/..') for name, text in sources.items()
                   if 'dirname "$0")/..' in text}
        self.assertEqual(dollar0, {})

    def test_a2_verify_find_root_has_no_file_based_root(self):
        path = os.path.join(SCRIPTS, "verify.py")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        found = [node for node in ast.walk(ast.parse(text))
                 if isinstance(node, ast.FunctionDef) and node.name == "_find_root"]
        self.assertEqual(len(found), 1, "前提:verify.py 有 _find_root")
        body = ast.get_source_segment(text, found[0])
        self.assertNotIn("__file__", body)
        self.assertIn("AC_CODE_ROOT", body)

    def test_a2_event_outside_any_project_is_rc_2_and_writes_nowhere(self):
        """`event.py emit` 在任何 git 樹之外、沒有 AC_ROOT:rc 2、`not inside a project`;A 的
        board/events.jsonl 一個位元組都沒動、那個目錄底下沒有 events.jsonl。

        **變異 m3**:`repo_root()` 退回 `dirname(__file__)` 那一棵 → 寫進 A,紅。"""
        homes = sorted({ROOT, self.ac_home, os.path.dirname(SCRIPTS)})
        before = {home: file_digest(os.path.join(home, "board", "events.jsonl")) for home in homes}
        env = self.env()
        env.pop("AC_ROOT")
        done = subprocess.run(["python3", os.path.join(SCRIPTS, "event.py"), "emit",
                               "ticket.attempt.start", "--ticket", "1"],
                              cwd=self.bare, env=env, capture_output=True, text=True,
                              timeout=TIMEOUT)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("not inside a project", done.stderr)
        after = {home: file_digest(os.path.join(home, "board", "events.jsonl")) for home in homes}
        self.assertEqual(after, before)
        self.assertEqual([path for path in glob.glob(os.path.join(self.bare, "**"), recursive=True)
                          if path.endswith("events.jsonl")], [])
        self.assertEqual(self.events(), [])

    def test_a2_every_script_without_a_root_says_the_same_line(self):
        """shell 那幾支(一份實作,`wtbase.sh` 的 `ac_root`)在專案之外同一句、rc 2。"""
        env = self.env()
        env.pop("AC_ROOT")
        for name, args in (("heartbeat.sh", ()), ("land.sh", ("t1",)),
                           ("apply.sh", ("1",)), ("review.sh", ("1",)),
                           ("auto-fix.sh", ("1",)), ("consolidate-memory.sh", ("1",))):
            with self.subTest(name):
                done = subprocess.run(["sh", os.path.join(SCRIPTS, name), *args],
                                      cwd=self.bare, env=env, capture_output=True, text=True,
                                      timeout=TIMEOUT)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("not inside a project", done.stderr)
                self.assertEqual(os.listdir(self.bare), [])


PRINTS = re.compile(r"\becho\b|\bsay\(|\bprint\(|\.write\(")


def dispatched_scripts(actl_text):
    """bin/actl's own tables → the scripts/ files it execs, read from the file so a new
    subcommand is covered without touching this case: the allowed-command `case` arm (the one
    ending in `) ;;`) and the final `exec` arms (`$CMD` expanded per name, `*` = the rest)."""
    allowed = re.search(r"\n\s*((?:[\w-]+\|\\?\s*)+[\w-]+)\) ;;", actl_text)
    assert allowed, "bin/actl: the allowed-command case arm moved"
    names = re.findall(r"[\w-]+", allowed.group(1))
    out, taken = {}, set()
    arms = re.findall(r'^\s*([\w|*-]+)\) exec (?:sh|python3) "\$(AC|AC_HOME)/([^"]+)"',
                      actl_text, re.M)
    for pattern, where, target in arms:
        cmds = [n for n in names if n not in taken] if pattern == "*" else pattern.split("|")
        taken.update(cmds)
        if where != "AC":
            continue  # not under scripts/ (board/board.py)
        for cmd in cmds:
            script = target.replace("$CMD", cmd)
            out[script] = " / ".join(filter(None, (out.get(script), cmd)))
    return out


class A185HintsNameActl(unittest.TestCase):
    """#185 A2:在專案裡 `scripts/x` 不存在、直接叫也沒有 AC_HOME(D-071:bin/actl 是唯一入口),
    所以印出來的提示對有 actl 子命令的腳本一律寫 `actl <子命令>`。"""

    def test_185_a2_no_printed_line_names_a_dispatched_script(self):
        """**變異**:`inbox.py` 的提示改回 `python3 scripts/inbox.py show` → 紅,訊息列出那一行。"""
        with open(os.path.join(ROOT, "bin", "actl"), encoding="utf-8") as handle:
            scripts = dispatched_scripts(handle.read())
        checked = sorted(scripts)
        # the parse itself must answer: an empty or short table is not a pass
        for must in ("inbox.py", "ticket.py", "gate.sh", "auto-fix.sh", "new-session.sh",
                     "testiface.py", "project_config.py", "verify-case.py"):
            self.assertIn(must, checked, "bin/actl parse lost %s; checked: %s" % (must, checked))
        bad = []
        files = sorted(glob.glob(os.path.join(SCRIPTS, "*.sh")) + glob.glob(os.path.join(SCRIPTS, "*.py")))
        self.assertTrue(files)
        for path in files:
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if line.lstrip().startswith("#") or not PRINTS.search(line):
                        continue
                    for script in checked:
                        old = ("python3 scripts/%s" if script.endswith(".py") else "sh scripts/%s") % script
                        if old in line:
                            bad.append("%s:%d %s -> actl %s" % (os.path.basename(path), number,
                                                                old, scripts[script]))
        self.assertEqual(bad, [], "checked scripts: %s" % ", ".join(checked))


if __name__ == "__main__":
    unittest.main()
