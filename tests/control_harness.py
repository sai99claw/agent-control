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

import glob
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
TIMEOUT = 180

SCRIPT_FILES = ("event.py", "ticket.py", "memory.py", "status.py", "land.sh",
                "gate.sh", "heartbeat.sh", "new-session.sh", "verify.py",
                "verify-case.py", "apply.sh", "auto-fix.sh", "review.sh", "inbox.py",
                "rules.py", "metrics.py", "wtbase.sh", "role-models.py")
# `review.sh` 逐字讀它(#42):沙盒少了它,覆核在「派工文組不出來」那一步就停,
# 而那與「reviewer 沒交件」在收件匣上是兩頁不同的東西。`auto-fix.sh` 的驗證者派工文
# 也是範本填出來的(#51):少了它,派工文裡只剩一行「範本找不到」。
TEMPLATE_FILES = ("dispatch-reviewer.md", "dispatch-verifier.md")

# 回歸層的最小形狀:一個登記過的標籤 + 一個會綠的案例。沙盒少了它,`gate --full`
# 跑到的回歸是一個空集合 —— 而**空集合與「都過了」長得一樣**,那正是這裡在擋的事。
VERIFY_TAGS = "# 功能標籤登記\n- `example` — 沙盒示範用\n"
# 模組 docstring 的四段是 `verify/_template_ticket.py` 的形狀(D-020 §四;案例 lint #138 已刪)。
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

# 沙盒的角色模型表(#121):auto-fix / review 起什麼只由它選。預設每個角色一格 claude-code、
# 不帶 effort —— 替身照 `worker.command` 起(換上 `--model <表的值>`),與沒有表時的派工同形。
# 要測候選鏈、codex、effort 的案例用 `set_role_models()` 換掉它。
def fixture_candidate(model="opus", tool="claude-code", effort=None, escalated=None, cap=1):
    return {"model": model, "tool": tool, "effort": effort, "effort_escalated": escalated,
            "dispatch_cap": cap}


def fixture_role_models(**chains):
    roles = {"main": [fixture_candidate(cap=None)], "worker": [fixture_candidate()],
             "worker_simple": [fixture_candidate("sonnet", effort="medium")],
             "opener": [fixture_candidate(cap=None)],
             "opener_complex": [fixture_candidate("fable", cap=None)],
             "verifier": [fixture_candidate()], "reviewer": [fixture_candidate()],
             "consolidator": [fixture_candidate("fable", cap=None)],
             "design": [fixture_candidate("fable", cap=None)]}
    roles.update(chains)
    return {"schema_version": 1,
            "candidate_keys": ["model", "tool", "effort", "effort_escalated", "dispatch_cap"],
            "roles": roles,
            "policies": {"fallback": "next_candidate_only_on_quota_or_unavailable",
                         "exhausted": "stop_and_decision", "scope": "dispatched_agents",
                         "escalate_effort_when": ["ticket.complexity == complex",
                                                  "repair_round >= 2 and previous round red"],
                         "repair_round_keeps_worker_model_and_effort": True,
                         "verifier_prefer_different_model": False}}


DEFAULT_CONFIG = {
    "port": 0,
    "tickets_dir": "tickets",
    "events_file": "board/events.jsonl",
    "answers_file": "board/answers.jsonl",
    "main_branch": "main",
    "routing": {"implement": "opus"},
    "memory": {"consolidator": "fable", "applies_to": ["memory/model/*.md"],
               "inbox_suffix": ".inbox.md"},
    "lease_seconds": {"opener": 900, "verifier": 3600, "land": 1800,
                      "worker": 7200},
    # **保險絲**(#42):`review.sh` 在設定裡找不到 `reviewer.command` 就起真的
    # `claude -p`,而 auto-fix 綠與手跑 `gate.sh --branch --ticket` 綠都會叫它。
    # 沙盒預設一支什麼都不印的替身 —— 覆核照走「沒交件」那條路,**從不**碰真的模型。
    # 要測覆核的案例用 `set_reviewer()` 換掉它。
    "reviewer": {"command": "true", "timeout_seconds": 60},
    # 同一個保險絲(#70):`auto-fix.sh` 找不到 `worker.command` 就起 `claude -p --model
    # opus` —— 紅的 land 預設 auto-fix,於是紅的 land 測試會從透傳的 PATH 起真的模型
    # (沒認證的機器靠認證失敗才綠)。`verifier.command` **故意不放**:沒設時它跟 worker
    # 同一支(這裡就是這支替身),而 `test_auto_fix` 與 `verify/agent-output` 的案例只蓋
    # `worker`、靠的正是「驗證者跟 worker 同一支」。
    "worker": {"command": "true"},
    # 硬擋清單(#135,D-054 Q3)跟 board/config.json 同一份值:沙盒預設就是 prod 的形狀。
    # 測「沒設就不擋」的案例要自己 `pop("hard_block")`,不能靠預設沒有。patterns 字面拆開
    # 寫 —— 這一支在 tests/**,自己的新增行也會被 patterns 查。
    "hard_block": {"paths": ["docs/DECISIONS.md", "docs/VISION.md",
                             "scripts/sync-to-project.sh"],
                   "dirs": [],
                   "patterns": [r"os\." + "kill", "kill" + "pg", "p" + "kill", "kill" + " -"]},
}

# 絆線(#70):每個沙盒的 PATH 最前面放這一支 `claude`。**替身漏了哪一條路**,那條路
# 起的就是它 —— 把完整參數記進 `AC_TEST_TRIPWIRE`、退非 0;cleanup 讀到非空就紅在那一條
# 測試自己身上,不是靠「這台機器沒認證」靜靜綠。環境被誰剝掉 `AC_*` 時退回沙盒裡那一份。
TRIPWIRE_CLAUDE = """#!/bin/sh
echo "claude $*" >> "${AC_TEST_TRIPWIRE:-%s}"
exit 97
"""

# 假 reviewer:記一行「被叫到了」、把派工文(stdin)留一份,印一份 pass 的覆核。
REVIEWER_PASS = """#!/bin/sh
echo "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"
cat > "$(dirname "$AC_TEST_LOG")/reviewer-stdin.md"
cat <<'EOF'
# 覆核
① verdict:pass
② 逐條驗收 → code 位置:沙盒的替身不讀 code

## result

```result
{"ticket": "1", "role": "reviewer", "round": 1, "verdict": "pass",
 "rc": null, "patch_sha256": null, "gate": null, "mutations": [],
 "objection": null, "excluded": [], "repro": null, "memory": []}
```
EOF
"""


def envelope(result, input_tokens, output_tokens, cache_write, cache_read, duration_ms):
    """`claude -p --output-format json` 的信封,**形狀照 reports/t48 那份真的 reviewer.json**
    (usage 裡還有 iterations / cache_creation,頂層有 total_cost_usd / modelUsage)——
    只讀四個 token 欄的程式,碰到真信封多出來的那幾格也不該讀歪。一行,不縮排。"""
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "duration_ms": duration_ms, "duration_api_ms": duration_ms - 274,
        "num_turns": 6, "result": result, "stop_reason": "end_turn",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "total_cost_usd": 0.39085679999999995,
        "usage": {"input_tokens": input_tokens,
                  "cache_creation_input_tokens": cache_write,
                  "cache_read_input_tokens": cache_read,
                  "output_tokens": output_tokens,
                  "output_tokens_details": {"thinking_tokens": 664},
                  "service_tier": "standard",
                  "cache_creation": {"ephemeral_1h_input_tokens": cache_write,
                                     "ephemeral_5m_input_tokens": 0},
                  "iterations": [{"input_tokens": 2, "output_tokens": 2394,
                                  "cache_read_input_tokens": 46130,
                                  "cache_creation_input_tokens": 1727,
                                  "type": "message"}]},
        "modelUsage": {"fixture-model": {"inputTokens": input_tokens,
                                         "outputTokens": output_tokens,
                                         "costUSD": 0.39085679999999995}},
    }, ensure_ascii=False)


# 假 reviewer(信封版):與 `REVIEWER_PASS` 同一份 pass,但照真的 `--output-format json`
# 把全文包在信封裡印 —— `review.sh` 拆信封、把信封原樣留在 reviewer.json(token 在裡面)。
REVIEWER_PASS_ENVELOPE = """#!/bin/sh
echo "reviewer ran $AC_TICKET" >> "$AC_TEST_LOG"
cat > /dev/null
cat <<'EOF'
%s
EOF
""" % envelope("# 覆核\n① verdict:pass\n\n## result\n\n```result\n"
               '{"ticket": "1", "role": "reviewer", "round": 1, "verdict": "pass",'
               ' "rc": null, "patch_sha256": null, "gate": null, "mutations": [],'
               ' "objection": null, "excluded": [], "repro": null, "memory": []}\n```\n',
               input_tokens=12, output_tokens=3601, cache_write=34386,
               cache_read=218504, duration_ms=35590)


def files_of(diff_text):
    """新增檔的 `diff -ruN` 夾具 → {相對路徑: 新內容}(#144:worker 交件改成票分支上的 commit,
    舊夾具的 patch 文字照用,只把它換成要 commit 的檔)。只收整檔新增的那一種形狀。"""
    files, name = {}, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].split("\t")[0].strip().split("/", 1)[1]
            files[name] = []
        elif line.startswith("@@") or line.startswith("--- ") or line.startswith("diff "):
            continue
        elif name is not None and line.startswith("+"):
            files[name].append(line[1:])
    return {name: "\n".join(lines) + "\n" for name, lines in files.items()}


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
        self.tripwire = os.path.join(self.home, "tripwire.log")
        self.stub_bin = os.path.join(self.home, "stub-bin")
        os.makedirs(self.stub_bin)
        write_executable(os.path.join(self.stub_bin, "claude"),
                         TRIPWIRE_CLAUDE % self.tripwire)
        # 後登記先跑:在 rmtree 之前讀絆線。
        self.addCleanup(self.assert_no_tripwire)
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
        os.makedirs(os.path.join(self.repo, "templates"))
        for name in TEMPLATE_FILES:
            shutil.copy(os.path.join(ROOT, "templates", name),
                        os.path.join(self.repo, "templates", name))
        shutil.copy(os.path.join(BOARD_DIR, "board.py"),
                    os.path.join(self.repo, "board", "board.py"))

        conf = dict(DEFAULT_CONFIG)
        if self.config_extra:
            conf.update(self.config_extra)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("board/role-models.json",
                   json.dumps(fixture_role_models(), ensure_ascii=False, indent=2))
        self.write("docs/DECISIONS.md", "# 裁示\n\n| 編號 | 裁示 | 來源 | 日期 | 狀態 |\n")
        self.write("docs/NOTES.md", "# notes\n")
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
        # `CLAUDE_CODE_REMOTE` 同理:worker 在雲端跑全套時,外層的 true 會讓「主線開票」那條變紅(#150)。
        # 漏進去就是「沒給票號卻有票」「該有 inbox 卻沒有」,而紅榜指的是被測的腳本。
        base = {key: value for key, value in os.environ.items()
                if not key.startswith("AC_") and key != "CLAUDE_CODE_REMOTE"}
        base.update({
            "HOME": self.home,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "s@example.invalid",
            "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "s@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
            # 呼叫端自己給 `PATH=` 的(自帶假 claude 的案例)下面的 update 照舊蓋掉這一格。
            "PATH": self.stub_bin + os.pathsep + os.environ.get("PATH", os.defpath),
            "AC_TEST_TRIPWIRE": self.tripwire,
            "AC_TEST_LOG": self.log,
            "AC_GATE_LOG": os.path.join(self.home, "gate.log"),
        })
        base.update(extra)
        return base

    def assert_no_tripwire(self):
        """cleanup:絆線被踩過(沙盒裡有誰起了 `claude`)就紅,並印出被叫的參數。"""
        if not os.path.exists(self.tripwire):
            return
        with open(self.tripwire, encoding="utf-8") as handle:
            calls = handle.read()
        if calls.strip():
            raise AssertionError("tripwire: real claude would have run in %s:\n%s"
                                 % (self.id(), calls))

    def set_reviewer(self, body, model="fixture-model", timeout=60):
        """`reviewer.command` 換成一支假的可執行檔;`review.by` 的模型名取自角色表(#121),
        所以表的 reviewer 鏈也換成這個模型。回傳那個模型名,讓斷言自己拼 `reviewer@<模型>`。"""
        path = os.path.join(self.home, "fake-reviewer.sh")
        write_executable(path, body)
        conf = json.loads(self.read("board/config.json"))
        conf["reviewer"] = {"command": "sh %s --model %s" % (path, model),
                            "timeout_seconds": timeout}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.set_role_models(reviewer=[fixture_candidate(model)])
        return model

    def set_role_models(self, **chains):
        """換沙盒角色模型表裡的幾條鏈(其餘照現在的表);`chains` 是角色 → 候選 list
        (`fixture_candidate`)。"""
        with open(os.path.join(self.repo, "board", "role-models.json"), encoding="utf-8") as handle:
            roles = json.load(handle)["roles"]
        roles.update(chains)
        self.write("board/role-models.json",
                   json.dumps(fixture_role_models(**roles), ensure_ascii=False, indent=2))

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

    def ticket_branch_commit(self, ident, files, subject=None, branch=None):
        """worker 交件的形狀(#144,D-054 Q9):票分支的 worktree(`<repo>-wt/<分支>`,auto-fix 派工前
        建的那一個)上一個 commit。分支 / worktree 還沒有就從 main 開。`files` 是 {相對路徑: 內容},
        內容 None = 刪檔。回 worktree 路徑。"""
        branch = branch or "t%s" % ident
        path = os.path.join(self.home, "repo-wt", branch)
        if not os.path.exists(os.path.join(path, ".git")):
            known = subprocess.run(["git", "-C", self.repo, "rev-parse", "-q", "--verify",
                                    "refs/heads/%s" % branch],
                                   capture_output=True, env=self.env()).returncode == 0
            if known:
                self.git("worktree", "add", "-q", path, branch)
            else:
                self.git("worktree", "add", "-q", "-b", branch, path, "main")
        for rel, text in files.items():
            if text is None:
                os.remove(os.path.join(path, rel))
            else:
                self.write(rel, text, where=path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", subject or "t%s 交件" % ident, cwd=path)
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
               "depends_on": [], "role": "worker",
               "model": "opus", "tool": "claude-code", "attempt": 1,
               "base_sha": self.git("rev-parse", "main").strip(),
               "state": "Ready", "state_version": 1, "lease": None,
               "retry_limit": 2}
        row.update(fields)
        self.write(os.path.join("tickets", "%s.json" % ident),
                   json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        return row

    def approve(self, ident, branch=None, verdict="pass"):
        """主線的覆核:{verdict, by, sha, note},`at` 由 set 補(#136 起不綁票版本)。
        complex 的票 land 少了它會拒絕;simple / 沒標的票不要它。"""
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

        **只有要它們的測試才裝** —— 預設就複製進去的話,記憶整理那一組會量到這幾份(連暫存區),
        而那一組問的是「待整理會不會被發現」,不是「這個 repo 有幾份記憶」。
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

    # ------------------------------------------------------- 批次修復的那一批(#108)

    def make_batch(self, run_id="20261010-120000-1"):
        """批次修復的真實形狀:主線 + `t1-a`、`t2-b` 兩條票分支 + 兩條都 `--no-ff` merge 進去的
        整合分支 `land/X`;票 1 / 票 2 已覆核(review 綁各自分支頭);`reports/t1/<run_id>/status.json`
        是 land 那一輪全套紅的兩條紅榜。期望值一律從這裡寫下的檔讀回。"""
        self.write("src/one.txt", "one\n")
        self.write("src/two.txt", "two\n")
        self.write("docs/guarded.md", "guarded\n")
        self.write("docs/shared.md", "shared\n")
        self.write("tests/test_x.py", "import unittest\n\n\nclass T(unittest.TestCase):\n"
                                      "    def test_one(self):\n        self.assertEqual(1, 1)\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有這一批要動的檔")
        shas = {}
        for branch, name, text in (("t1-a", "src/one.txt", "one by #1\n"),
                                   ("t2-b", "src/two.txt", "two by #2\n")):
            self.git("branch", branch, "main")
            path = self.worktree_of(branch)
            self.commit_in(path, name, text.strip())
            shas[branch] = self.git("rev-parse", branch).strip()
        self.make_ticket("1", subject="批次 A:票 1 的題目", objective="票 1 的目標:one 要對",
                         acceptance=["A1 one.txt 寫著 one by #1", "A2 票 1 的第二條"],
                         branch="t1-a", state="InReview", state_version=3,
                         review={"verdict": "pass", "by": "main", "sha": shas["t1-a"],
                                 "note": "沙盒"})
        self.make_ticket("2", subject="批次 B:票 2 的題目", objective="票 2 的目標:two 要對",
                         acceptance=["B1 two.txt 寫著 two by #2"],
                         branch="t2-b", state="InReview", state_version=5,
                         review={"verdict": "pass", "by": "main", "sha": shas["t2-b"],
                                 "note": "沙盒"})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1 #2")
        self.git("branch", "land/X", "main")
        land = self.worktree_of("land/X")
        for branch in ("t1-a", "t2-b"):
            self.git("merge", "-q", "--no-ff", "-m", "merge %s" % branch, branch, cwd=land)
        self.git("worktree", "remove", "--force", land)
        failures = [{"kind": "FAIL", "case": "tests.test_x.T.test_one", "file": "tests/test_x.py",
                     "line": 6, "excerpt": "AssertionError: 批次紅榜第一條"},
                    {"kind": "ERROR", "case": "tests.test_y.T.test_two", "file": "tests/test_y.py",
                     "line": 3, "excerpt": "NameError: 批次紅榜第二條"}]
        self.write(os.path.join("reports", "t1", run_id, "status.json"),
                   json.dumps({"ticket": "1", "kind": "land", "run_id": run_id, "rc": 1,
                               "failures": failures}, ensure_ascii=False, indent=2))
        return {"run_id": run_id, "failures": failures, "shas": shas,
                "land": self.git("rev-parse", "land/X").strip()}

    def worktree_of(self, branch):
        """已經存在的分支開一個 worktree(`worktree()` 是連分支一起開)。"""
        path = os.path.join(self.home, "wt", branch.replace("/", "-"))
        self.git("worktree", "add", "-q", path, branch)
        return path

    def batch_dirs(self):
        return sorted(glob.glob(os.path.join(self.repo, "reports", "batch", "*")))
