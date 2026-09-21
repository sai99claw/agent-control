"""測試共用的沙盒:一顆 tempdir 裡的**拋棄式真 repo**。

## 為什麼是真的 git,不是假的
受測的那幾支(`land.sh`、`heartbeat.sh`、`ticket.py verify`)**通篇都是 git 語意**
—— `merge-base --is-ancestor` / `--no-ff` / `--ff-only` / `rev-list --count` /
`ls-tree -z`。假一支 git 出來,測到的是那份模擬器對 git 的理解,不是 git
(`docs/DISPATCH-TEMPLATE.md` §5.6)。

**該替換的是「代價」而不是「語意」**:所以昂貴的那一支(全套閘門)才換成「寫一行
標記檔就退出」——那不但省掉整趟測試,而且讓「**閘門到底有沒有被叫到**」變成一個看得
見的事實,而那正是 0 commit 那張票要問的東西。

## 隔離
`HOME`、`GIT_CONFIG_GLOBAL/SYSTEM` 都指進沙盒或 `/dev/null`,`origin` 是同一顆
tempdir 裡的 bare repo —— 所以這一組碰不到這台機器上任何一個真的 repo,也不會讀到
誰的 git 設定。埠一律 0(OS 給),不碰任何固定埠。

受測腳本的來源走 `AC_TEST_SCRIPTS`,好讓變異驗紅把一份改壞的複本餵給同一組測試。
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
SCRIPTS = os.environ.get("AC_TEST_SCRIPTS", os.path.join(ROOT, "scripts"))
BOARD_DIR = os.environ.get("AC_TEST_BOARD", os.path.join(ROOT, "board"))
CODE_MAP = os.path.join(ROOT, "code-map")
TIMEOUT = 180

SCRIPT_FILES = ("event.py", "ticket.py", "memory.py", "status.py", "land.sh",
                "gate.sh", "heartbeat.sh", "new-session.sh")

# 閘門的替身:寫一行標記檔就退出。**「有沒有被呼叫」因此是一個看得見的事實。**
GATE_STUB_GREEN = """#!/bin/sh
echo "gate $* $(git rev-parse --short HEAD)" >> "$AC_TEST_LOG"
exit 0
"""
GATE_STUB_RED = """#!/bin/sh
echo "gate $* $(git rev-parse --short HEAD)" >> "$AC_TEST_LOG"
echo "FAILED (假的紅)"
exit 1
"""

DEFAULT_CONFIG = {
    "port": 0,
    "tickets_dir": "tickets",
    "events_file": "board/events.jsonl",
    "answers_file": "board/answers.jsonl",
    "main_branch": "main",
    "routing": {"implement": "opus"},
    "memory": {"unit": "chars", "cap_chars": 2000, "consolidator": "fable",
               "raise_allowed": True, "applies_to": ["memory/model/*.md"],
               "inbox_suffix": ".inbox.md"},
    "lease_seconds": {"opener": 900, "verifier": 3600, "land": 1800,
                      "worker": 7200},
}


def write_executable(path, body):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(path, 0o755)


class Sandbox(unittest.TestCase):
    """子類別可以換掉的兩格:閘門的替身、設定。"""

    gate_stub = None          # None = 用真的 scripts/gate.sh
    config_extra = None

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = os.path.join(self.home, "repo")
        self.origin = os.path.join(self.home, "origin.git")
        self.log = os.path.join(self.home, "calls.log")
        for rel in ("scripts", "tickets", "board", "docs", "tests",
                    os.path.join("memory", "model"), os.path.join("code-map", "cards")):
            os.makedirs(os.path.join(self.repo, rel))

        for name in SCRIPT_FILES:
            target = os.path.join(self.repo, "scripts", name)
            if name == "gate.sh" and self.gate_stub:
                write_executable(target, self.gate_stub)
                continue
            shutil.copy(os.path.join(SCRIPTS, name), target)
            os.chmod(target, 0o755)
        shutil.copy(os.path.join(BOARD_DIR, "board.py"),
                    os.path.join(self.repo, "board", "board.py"))
        shutil.copy(os.path.join(CODE_MAP, "check-stale.py"),
                    os.path.join(self.repo, "code-map", "check-stale.py"))

        conf = dict(DEFAULT_CONFIG)
        if self.config_extra:
            conf.update(self.config_extra)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("docs/DECISIONS.md", "# 裁示\n\n| 編號 | 裁示 | 來源 | 日期 | 狀態 |\n")
        self.write("docs/REHEARSAL.md", "# 哪些保證還只在演練裡成立\n\n"
                                        "| 工具 | 第一次真跑 | 要看到什麼 |\n|---|---|---|\n")
        self.write("README", "main\n")

        self.git("init", "-q", "--bare", self.origin, cwd=self.home)
        self.git("init", "-q")
        # `init -b main` 是新版 git 才有的;symbolic-ref 到處都會。
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的第一個 commit")
        self.git("remote", "add", "origin", self.origin)
        self.git("push", "-q", "origin", "main")

    # ------------------------------------------------------------ 基本動作

    def env(self, **extra):
        base = dict(os.environ)
        base.update({
            "HOME": self.home,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "s@example.invalid",
            "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "s@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
            "AC_TEST_LOG": self.log,
            "AC_GATE_LOG": os.path.join(self.home, "gate.log"),
        })
        base.pop("AC_ROOT", None)
        base.update(extra)
        return base

    def git(self, *args, cwd=None):
        done = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env(),
                              capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0,
                         "沙盒自己壞了:git " + " ".join(args) + "\n"
                         + done.stdout + done.stderr)
        return done.stdout

    def write(self, rel, text, where=None):
        path = os.path.join(where or self.repo, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, rel, where=None):
        with open(os.path.join(where or self.repo, rel), encoding="utf-8") as handle:
            return handle.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.repo, rel))

    # ------------------------------------------------------------ 跑受測的

    def run_py(self, script, *args, cwd=None, stdin="", env=None):
        return subprocess.run(
            ["python3", os.path.join(self.repo, script), *args],
            cwd=cwd or self.repo, env=env or self.env(), input=stdin,
            capture_output=True, text=True, timeout=TIMEOUT)

    def run_sh(self, script, *args, cwd=None, env=None):
        return subprocess.run(
            ["sh", os.path.join(self.repo, script), *args],
            cwd=cwd or self.repo, env=env or self.env(),
            capture_output=True, text=True, timeout=TIMEOUT)

    def ticket(self, *args, **kwargs):
        return self.run_py("scripts/ticket.py", *args, **kwargs)

    def event(self, *args, **kwargs):
        return self.run_py("scripts/event.py", *args, **kwargs)

    # ------------------------------------------------------------ 讀回結果

    def events(self):
        path = os.path.join(self.repo, "board", "events.jsonl")
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def kinds(self):
        return [row["kind"] for row in self.events()]

    def load_ticket(self, ident):
        return json.loads(self.read(os.path.join("tickets", "%s.json" % ident)))

    # ------------------------------------------------------------ 分支與票

    def worktree(self, branch):
        """一票一分支一 worktree —— 真實的形狀就是這樣(`docs/WORKFLOW.md`),而
        「忘了 commit」那一種正是東西躺在 worktree 裡。"""
        path = os.path.join(self.home, "wt", branch)
        self.git("worktree", "add", "-q", "-b", branch, path, "main")
        return path

    def commit_in(self, path, name, subject):
        self.write(name, subject + "\n", where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", subject, cwd=path)

    def make_ticket(self, ident, **fields):
        """直接寫一張合乎 schema 的票(不經 CLI)—— 給「受測的不是 create」的那些
        測試用,省掉一次子行程。"""
        row = {"id": str(ident), "subject": "第 %s 張" % ident,
               "created": "2026-09-12T10:00:00+08:00",
               "objective": "做完某件事", "acceptance": ["會紅的斷言一條"],
               "in_scope": [], "out_of_scope": [], "depends_on": [],
               "allowed_write_paths": ["src/*"], "role": "worker",
               "model": "opus", "tool": "claude-code", "attempt": 1,
               "base_sha": self.git("rev-parse", "main").strip(),
               "state": "Ready", "state_version": 1, "lease": None,
               "retry_limit": 2}
        row.update(fields)
        self.write(os.path.join("tickets", "%s.json" % ident),
                   json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        return row

    def gate_ran(self):
        return os.path.exists(self.log)
