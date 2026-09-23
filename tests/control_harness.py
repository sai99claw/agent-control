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
                "gate.sh", "heartbeat.sh", "new-session.sh", "verify.py",
                "verify-case.py", "apply.sh", "auto-fix.sh", "inbox.py",
                "rules.py", "metrics.py")

# 回歸層的最小形狀:一個登記過的標籤 + 一個會綠的案例。沙盒少了它,`gate --full`
# 跑到的回歸是一個空集合 —— 而**空集合與「都過了」長得一樣**,那正是這裡在擋的事。
VERIFY_TAGS = "# 功能標籤登記\n- `example` — 沙盒示範用\n"
# 模組 docstring 的四段是 `verify-case.py lint` 的 F1(D-020 §四)。**沙盒的示範案例
# 也要過 lint** —— 閘門現在會對票的 `verify.files` 跑一次 lint(#27),而一份 lint 過
# 不了的示範案例會讓「閘門有沒有去 lint」與「被測的票寫壞了」長得一樣。
VERIFY_CASE = '''"""#0 沙盒的示範案例:回歸層不是空集合。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 跑這一條 | 綠 | 沙盒用:只證「回歸層選得到案例」

## 介面字串
(沒有;這一條不斷言任何字串)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬任何票面驗收。
"""
import unittest

TAGS = ["example"]


class T(unittest.TestCase):
    def test_true(self):
        """A1 沙盒的示範案例是綠的。"""
        self.assertTrue(True)
'''

# 沙盒也要有 `.gitignore`,而且是**真 repo 那幾條**:執行時寫出來的東西不進 git。
# 少了它,`gate.sh --branch` 會把 `reports/`、`__pycache__/` 當成「沒有人守著的改動檔」
# 而退 3 —— 那是沙盒與事實的差異,不是受測腳本的行為(§5.6:演練環境要真)。
SANDBOX_IGNORE = """__pycache__/
*.pyc
reports/
gate.log
gate.log.*
verify.log
board/events.jsonl
board/answers.jsonl
.land.lock/
*.tmp[0-9]*
"""

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
                    os.path.join("verify", "example"),
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
        self.write(".gitignore", SANDBOX_IGNORE)
        self.write(os.path.join("verify", "__init__.py"), "")
        self.write(os.path.join("verify", "example", "__init__.py"), "")
        self.write(os.path.join("verify", "example", "test_example.py"), VERIFY_CASE)
        self.write(os.path.join("verify", "TAGS.md"), VERIFY_TAGS)

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
        # 外面那一層的 `AC_*` 一個都不准進沙盒:land.sh 跑全套時帶著 AC_GATE_TICKET /
        # AC_NO_INBOX / AC_GATE_RUN_ID,auto-fix 派的 worker 帶著 AC_ROUND / AC_ROOT ——
        # 漏進去就是「沒給票號卻有票」「該有 inbox 卻沒有」,而紅榜指的是被測的腳本。
        base = {key: value for key, value in os.environ.items()
                if not key.startswith("AC_")}
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

    def tickets_on_disk(self):
        """票庫裡現在有哪幾張(含腳本自己開出來的)。"""
        where = os.path.join(self.repo, "tickets")
        rows = []
        for name in sorted(os.listdir(where)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(where, name), encoding="utf-8") as handle:
                rows.append(json.load(handle))
        return rows

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
        測試用,省掉一次子行程。

        **這一份票面沒有 `verify` 那一格**,而閘門把「沒有宣告」與「宣告了卻沒有案例」
        分開處理(#27):所以用它的測試不會被驗證者那一層擋住,而要測那一層的人自己
        傳一格 `verify={...}` 進來。
        """
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

    def approve(self, ident, branch=None, verdict="pass"):
        """主線的覆核:**綁票版本與分支頭的 sha**(D-014)。land 少了它會拒絕,
        而那正是這一格的重點 —— 一張沒有人讀過 patch 的票不該進得了主線。"""
        sha = self.git("rev-parse", branch).strip() if branch else ""
        self.ticket("set", str(ident), "review",
                    json.dumps({"verdict": verdict, "by": "main", "sha": sha,
                                "note": "沙盒的覆核"}, ensure_ascii=False))

    def status_of(self, ident, kind=None):
        """這張票最新一輪的狀態檔。**一輪一個目錄、不覆寫**,所以讀的人要先挑輪
        (D-014);挑最新那一輪就是接手的人會做的事。"""
        where = os.path.join(self.repo, "reports", "t%s" % ident)
        runs = sorted(name for name in os.listdir(where)
                      if os.path.exists(os.path.join(where, name, "status.json")))
        for name in reversed(runs):
            with open(os.path.join(where, name, "status.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            if kind is None or data.get("kind") == kind:
                return data
        raise AssertionError("#%s 沒有 kind=%s 的狀態檔(%s)" % (ident, kind, runs))

    def gate_ran(self):
        return os.path.exists(self.log)

    # ------------------------------------------------------- 規則包要的那幾份

    def install_rules_sources(self):
        """`rules.py` 讀的三份:共用規矩、角色卡、模型記憶。

        **只有要它們的測試才裝** —— 預設就複製進去的話,記憶上限那一組會量到這幾份,
        而那一組問的是「超標會不會被發現」,不是「這個 repo 有幾份記憶」。
        """
        shutil.copy(os.path.join(ROOT, "docs", "DISPATCH-TEMPLATE.md"),
                    os.path.join(self.repo, "docs", "DISPATCH-TEMPLATE.md"))
        for kind in ("role", "model"):
            src = os.path.join(ROOT, "memory", kind)
            dst = os.path.join(self.repo, "memory", kind)
            os.makedirs(dst, exist_ok=True)
            for name in sorted(os.listdir(src)):
                if name.endswith(".md"):
                    shutil.copy(os.path.join(src, name), os.path.join(dst, name))
