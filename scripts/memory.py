#!/usr/bin/env python3
"""記憶的暫存區與整理 — D-053 ⑥(`docs/MEMORY.md`「整理:老師帶學生」)。

    scripts/memory.py check [--read-only]      # session 開頭跑;暫存區達批次門檻 → 退出碼 1
    scripts/memory.py check-stale --read-only [--file <主檔或 inbox>]   # 逐條分類來源
    scripts/memory.py harvest EVIDENCE.md      # 收割「記憶」段的 note 指令
    scripts/memory.py snapshot memory/model/opus.md   # 討論檔頭要抄的兩行
    scripts/memory.py lint [--file <記憶檔>]… [--json]   # 分層 lint(唯讀):錯層內容逐條點名
    scripts/memory.py lint --base main --file <記憶檔>…  # 只量本分支新增行
    scripts/memory.py consolidate memory/model/opus.md --candidate <新版> \
        --discussion discussions/2026-09-12-memory-opus-fable.md \
        --discussion discussions/2026-09-12-memory-opus-opus.md

## 為什麼不設字數上限(D-053 ⑥)
固定的字數上限會逼整理者為了湊數字壓掉有用的句子,而「超過才整理」讓暫存區的教訓
一直等到累積夠多才進卡。整理者只拿一句判準:**每個 session 開場都會讀,只留值得這個
成本的** —— 寫什麼、留多長由整理的 LLM 決定。`check` 照舊印正文字元數,只是報數,
不跟任何數字比。

## 為什麼整理是批次觸發(D-055 ①)
暫存區(`.inbox.md`)是還沒被 `consolidate` 消耗的教訓;它不進每個 session 的開場,
所以留在那裡的每一條都還沒有生效。但「有 1 條就派一個 fable session」太貴:非空行
≥ BATCH_MIN_ENTRIES,或最舊一條(note 括號裡的日期)距今 ≥ BATCH_MAX_AGE_DAYS 天
(免得條目少的暫存區永遠不整理),主檔才待整理;同一份主檔最近一張整理票(任何 state)
的 `created` 在 BATCH_COOLDOWN_HOURS 小時內,這次不開(條目留著,下次再看)。
不擋任何東西。查重照舊:一份主檔任何時刻至多一張開著的票。

## 為什麼只管 role / model 兩層
角色卡與模型卡是同一角色、同一模型每個 session 開頭都要讀的東西。專案共用層
(裁示、交接)有各自的形狀,不受這條管 —— 這是 D-006 的原話更正過的範圍。

## 為什麼沒有討論檔就不准整理(D-007)
整理是**老師帶學生**,不是老師替學生刪。而「討論過了」與「沒討論就刪了」在結果檔案上
長得一模一樣 —— 兩者都是一份變短的記憶。唯一分得開的東西是那份討論紀錄,所以它是
`consolidate` 的必要輸入,不是建議。格式見 `docs/DISCUSSION.md`。

沒有討論檔、以及有檔但**還沒有結論區**,是兩件事,兩句話 —— 下一步差很多(去開一份
vs 回去把結論寫完)。

## 為什麼整理只消耗快照(#76)
討論是對**某一刻的 inbox** 做的:`snapshot` 印出那一刻的行數 K 與前 K 行的 sha256,
兩份討論檔都抄這兩行、對 1..K 每一行寫一個處置。`consolidate` 在鎖內重算一次,對得上
才寫主檔,而且只把前 K 行移進 `.consumed` —— 整理期間新記的第 K+1 行以後留在 inbox。
「先改名整份 inbox 再判斷」會把沒有人討論過的新條目一起吃掉;任何一項驗不過,主檔、
inbox、事件一個位元組都不動。

## 為什麼 `--read-only`
短命角色開場也量一次,但**不開票**:票是主線的,一個 worker 開場留下一張整理票,
與主線開場開的那一張長得一樣,而兩張會被派兩次。唯讀的那一支不拿鎖、不發事件。
"""

import errno
import glob as globmod
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event      # noqa: E402
import ticket     # noqa: E402

# D-013:角色卡與模型卡都是「每個 session 的第一口空氣」,不只模型私有那一層。
# 紀錄類文件(DECISIONS / HANDOFF)不在這裡:它們是**用 grep 查的**,不是每次載入的。
DEFAULT_APPLIES = ("memory/model/*.md", "memory/role/*.md")
DEFAULT_INBOX_SUFFIX = ".inbox.md"
NOT_A_MEMORY = "README.md"     # 說明檔不是誰的記憶 —— glob 抓得到它,check 不該管它
CONSOLIDATOR_ROLE = "consolidator"
BATCH_MIN_ENTRIES = 5          # D-055 ①:暫存區累積幾條才開整理票
BATCH_MAX_AGE_DAYS = 3         # 最舊一條放滿幾天也開(條目少的暫存區才不會永遠不整理)
BATCH_COOLDOWN_HOURS = 24      # 同一份主檔最近一張整理票 created 在這之內 → 這次不開
LOCK_NAME = ".lock"
LOCK_TIMEOUT = 10.0
FENCE = "---"


DISPOSITIONS = ("保留", "升格", "移至 reference", "歸檔", "重複")
DISPOSITION_RE = re.compile(r"^- L(\d+):[ \t]*(%s)" % "|".join(DISPOSITIONS), re.MULTILINE)
TICKET_TAG = re.compile(r"^#(\d+)$")

CONSOLIDATE_FLAGS = (
    ("--candidate", "整理後的新版正文(必填;寫什麼、留多長由整理者決定)"),
    ("--discussion", "討論檔路徑,**給兩次、兩個不同的 model**(D-007 / D-013;"
                     "格式見 docs/DISCUSSION.md)"),
    ("--by", "誰帶的討論;不給就用 config 的 memory.consolidator"),
)

USAGE = {
    "check": "scripts/memory.py check [--read-only]         # 暫存區達批次門檻退出碼 1(不停工)",
    "check-stale": "scripts/memory.py check-stale --read-only [--file <主檔或 inbox 相對路徑>]",
    "snapshot": "scripts/memory.py snapshot <記憶檔>             # 印 source_lines / source_sha256",
    "note": "scripts/memory.py note <model|role|project> <名> \"<一行>\" [--ticket N] [--by <role>@<model>]",
    "harvest": "scripts/memory.py harvest <EVIDENCE.md>",
    "lint": ("scripts/memory.py lint [--file <記憶檔>]… [--base <ref>] [--json]   # 唯讀;空掃描不算過;"
             "--base 只量相對 merge-base(ref, HEAD) 的新增行"),
    "consolidate": ("scripts/memory.py consolidate <記憶檔> --candidate <檔> --discussion <A> "
                    "--discussion <B> [--by …]"),
}

EXAMPLE = {
    "check": "python3 scripts/memory.py check",
    "check-stale": "python3 scripts/memory.py check-stale --read-only --file memory/role/worker.md",
    "snapshot": "python3 scripts/memory.py snapshot memory/model/opus.md",
    "lint": "python3 scripts/memory.py lint --file memory/role/implementer.md --json",
    "note": ('python3 scripts/memory.py note role implementer '
             '"變異後先確認替換真的生效" --ticket 17 --by worker@opus'),
    "consolidate": ('python3 scripts/memory.py consolidate memory/model/opus.md \\\n'
                    '  --candidate discussions/2026-09-12-memory-opus.candidate.md \\\n'
                    '  --discussion discussions/2026-09-12-memory-opus-fable.md \\\n'
                    '  --discussion discussions/2026-09-12-memory-opus-opus.md \\\n'
                    '  --by fable'),
}


def help_for(verb, out=sys.stdout):
    """一個子指令的說明。**程式要自己說得出規則**(D-001)。"""
    out.write("用法:%s\n" % USAGE.get(verb, "scripts/memory.py %s" % verb))
    if verb == "consolidate":
        out.write("\n認得的參數:\n")
        for flag, note in CONSOLIDATE_FLAGS:
            out.write("  %-14s %s\n" % (flag, note))
    out.write("\n例:\n%s\n" % EXAMPLE.get(verb, ""))
    return 0


def unknown_flag(flag):
    """不認得的參數 —— **把認得的那幾個列出來**。"""
    sys.stderr.write("memory: consolidate 不認得 %r\n" % flag)
    sys.stderr.write("memory: consolidate 認得的是:%s\n"
                     % "  ".join(name for name, _ in CONSOLIDATE_FLAGS))
    sys.stderr.write("memory: 看範例:python3 scripts/memory.py consolidate --help\n")
    return 2


def memory_config():
    conf = event.config(ticket.root()).get("memory")
    return conf if isinstance(conf, dict) else {}


def split_front_matter(text):
    """`---` 夾住的那一段,與正文。沒有 front matter 就回 `(None, 全文)`。

    **量的是正文** —— front matter 是這份檔的中繼資料,不是每個 session 要讀的內容。
    """
    if not text.startswith(FENCE + "\n"):
        return None, text
    end = text.find("\n" + FENCE + "\n", len(FENCE))
    if end < 0:
        return None, text
    return text[len(FENCE) + 1:end + 1], text[end + len(FENCE) + 2:]


def body_chars(text):
    return len(split_front_matter(text)[1])


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def inbox_path(path, suffix):
    stem = path[:-3] if path.endswith(".md") else path
    return stem + suffix


class MemoryLock(object):

    def __init__(self, root, timeout=LOCK_TIMEOUT):
        self.path = os.path.join(root, "memory", LOCK_NAME)
        self.timeout = timeout
        self.held = False

    def __enter__(self):
        deadline = time.time() + self.timeout
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        while True:
            try:
                os.mkdir(self.path)
                self.held = True
                with open(os.path.join(self.path, "holder"), "w", encoding="utf-8") as handle:
                    handle.write("%d\n" % os.getpid())
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
                if time.time() >= deadline:
                    raise RuntimeError("memory: 整理鎖拿不到(%s)" % self.path)
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(os.path.join(self.path, "holder"))
                os.rmdir(self.path)
            except OSError:
                pass
        return False


def append_line(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = line.encode("utf-8")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, data)
    finally:
        os.close(handle)


NOTE_LINE = re.compile(r"^- (.*) \(([^()]*)\)$")


def note_name(name, suffix):
    """`X` 或 `X.md` → `(X, None)`;不合法 → `(None, 錯誤訊息)`。

    串接是 `name + suffix`:多一個 `.md` 就落成 `X.md.inbox.md`,讀端只認
    `X.inbox.md`,那一條沒有讀者。推得出本意的(多寫了 `.md` 或 inbox 後綴)附上
    可以直接重試的名稱。
    """
    stem = name[:-3] if name.endswith(".md") else name
    inbox = suffix[:-3] if suffix.endswith(".md") else suffix
    if stem in ("", ".", "..") or "/" in name or "\\" in name:
        return None, "memory: 名稱不合法:'%s'(要是一個檔名)\n" % name
    if not stem.endswith(".md") and not (inbox and stem.endswith(inbox)):
        return stem, None
    guess = stem
    while True:
        if guess.endswith(".md"):
            guess = guess[:-3]
        elif inbox and guess.endswith(inbox):
            guess = guess[:-len(inbox)]
        else:
            break
    hint = ";改用 %s" % guess if guess not in ("", ".", "..") else ""
    return None, ("memory: 名稱不合法:'%s'(只寫名稱,不帶第二個 .md 或 %s 後綴)%s\n"
                  % (name, suffix, hint))


def already_noted(path, text, tag):
    """去重鍵 =(檔、正文、票號);日期與 `--by` 不入鍵。"""
    try:
        lines = read(path).splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        found = NOTE_LINE.match(line)
        if not found or found.group(1) != text:
            continue
        first = found.group(2).split(", ")[0]
        if (first if first.startswith("#") else "") == tag:
            return True
    return False


# ------------------------------------------------------------------- lint
#
# 記憶分層(#87 A33–A37,原 #79;裁示原話 Q2 / Q3):角色與模型記憶只留通用原則與自身反思,
# 專案記憶留設計理由與不變條件;具體的路徑、行號、呼叫、旗標、commit 指紋去 repo map /
# reference / 票。這是**可測的語法近似**,不宣稱判斷得了所有語意錯層 —— 抽象過的專案限定
# 說法仍要覆核者看。rule ID 是穩定的介面(note / consolidate 共用這一份判斷)。
#
# 誤判的例外寫在**同一行**、只豁免一條規則的一個逐字值,要有理由與存在的裁示引用:
#   <!-- memory-allow rule=path value="docs/ROLES.md" reason="navigation-only" ref="D-013" -->
# 沒有整檔、萬用字元的豁免;例外全部列在輸出裡,覆核者看得到。

LINT_RULES = ("path", "line", "call", "flag", "sha")
# 專案記憶可以引用模組 / 設計文件的路徑;其餘四條各層都擋。
LAYER_ALLOWS = {"project": ("path",)}
LINT_PATTERNS = {
    "path": re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|sh|js|ts|tsx|jsx|json|ya?ml|toml|sql"
                       r"|md|html|css)\b", re.ASCII),
    # 前一個字是數字就是時間(10:30),不是行號。
    "line": re.compile(r"(?<![\d\s:]):\d+\b|#L\d+\b|\bline \d+\b", re.ASCII | re.IGNORECASE),
    "call": re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\([^()\n]*\)", re.ASCII),
    "flag": re.compile(r"(?<![\w-])--[A-Za-z][\w-]*", re.ASCII),
    "sha": re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,64}(?![0-9A-Za-z])", re.ASCII),
}
CODE_SPAN = re.compile(r"`([^`\n]+)`")
SHORT_FLAG = re.compile(r"(?:^|(?<=\s))-[A-Za-z][A-Za-z0-9]*\b", re.ASCII)
LINT_DEST = {
    "path": "專案記憶(memory/project/)只寫設計理由;定位交給 repo map(repo-map.py query <主題>)",
    "line": "repo map / 票 —— 行號會過期,哪一層記憶都不存",
    "call": "reference / repo map —— 精確 API 不進記憶,記憶只留原則",
    "flag": "reference / repo map —— 指令旗標不進記憶",
    "sha": "票 / review 歷史 —— commit 指紋是事件,不是原則",
    "marker": "改正這一行的 memory-allow(rule、逐字 value、reason、存在的 ref),或拿掉它",
}
ALLOW_MARKER = re.compile(r"<!--\s*memory-allow\b(.*?)-->")
ALLOW_FIELD = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')
WILDCARD = re.compile(r"[*?\[\]]")


def lint_layer(rel):
    """這份檔在哪一層:`memory/project/` → project;其餘(角色卡、模型卡、暫存區、
    同步到專案的角色目錄)一律按 role / model 的嚴格那一套。"""
    parts = rel.replace(os.sep, "/").split("/")
    if parts[:2] == ["memory", "project"]:
        return "project"
    rules = event.config(ticket.root()).get("rules") or {}
    for layer, key, default in (("model", "models_dir", "memory/model"),
                                ("role", "roles_dir", "memory/role")):
        where = (rules.get(key) or default).rstrip("/") + "/"
        if rel.replace(os.sep, "/").startswith(where):
            return layer
    return "role"


def lint_hits(text):
    """一行文字的命中:`[(rule, 逐字值)]`。"""
    hits = []
    for rule in LINT_RULES:
        for found in LINT_PATTERNS[rule].finditer(text):
            value = found.group(0)
            if rule == "sha" and not re.search(r"[a-f]", value):
                continue
            hits.append((rule, value))
    for span in CODE_SPAN.finditer(text):
        hits.extend(("flag", found.group(0)) for found in SHORT_FLAG.finditer(span.group(1)))
    return hits


def parse_marker(raw, decisions):
    """一個 memory-allow → `(dict, 問題)`;問題非空就是無效的例外。"""
    fields = {key: (quoted if quoted is not None and quoted != "" else bare or "")
              for key, quoted, bare in ALLOW_FIELD.findall(raw)}
    rule, value = fields.get("rule", ""), fields.get("value", "")
    if rule not in LINT_RULES:
        return fields, "rule=%r 不是 %s 之一" % (rule, " / ".join(LINT_RULES))
    if not value or WILDCARD.search(value):
        return fields, "value 要是一個逐字值(不准空的、不准萬用字元):%r" % value
    if not fields.get("reason"):
        return fields, "沒有 reason"
    ref = fields.get("ref", "")
    if not ref or not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(ref), decisions):
        return fields, "ref=%r 在 docs/DECISIONS.md 找不到" % ref
    return fields, ""


def lint_text(text, layer, decisions, first_line=1, note_meta=False):
    """一段正文 → `(violations, allowed)`;每筆帶 `line`(從 `first_line` 起算)。

    `note_meta`:暫存區那一種 `- <正文> (#票, 日期, 誰)` 只量正文,括號裡的來源是
    中繼資料,不是記憶的內容。
    """
    allows = LAYER_ALLOWS.get(layer, ())
    bad, allowed = [], []
    for offset, line in enumerate(text.splitlines()):
        number = first_line + offset
        markers = []
        for found in ALLOW_MARKER.finditer(line):
            fields, problem = parse_marker(found.group(1), decisions)
            if problem:
                bad.append({"line": number, "rule": "marker", "value": found.group(0),
                            "why": problem})
            else:
                markers.append(fields)
        body = ALLOW_MARKER.sub("", line)
        if note_meta:
            matched = NOTE_LINE.match(body.rstrip())
            if matched:
                body = matched.group(1)
        used = set()
        for rule, value in lint_hits(body):
            if rule in allows:
                continue
            exact = [n for n, row in enumerate(markers)
                     if row["rule"] == rule and row["value"] == value]
            if exact:
                used.update(exact)
                row = markers[exact[0]]
                allowed.append({"line": number, "rule": rule, "value": value,
                                "reason": row.get("reason", ""), "ref": row.get("ref", "")})
                continue
            bad.append({"line": number, "rule": rule, "value": value, "why": ""})
        for n, row in enumerate(markers):
            if n not in used:
                bad.append({"line": number, "rule": "marker", "value": row["value"],
                            "why": "這一行沒有 rule=%s value=%r 的命中 —— 例外沒有豁免到任何東西"
                                   % (row["rule"], row["value"])})
    for row in bad:
        row["destination"] = LINT_DEST[row["rule"]]
    return bad, allowed


def decisions_text(root):
    try:
        return read(os.path.join(root, "docs", "DECISIONS.md"))
    except OSError:
        return ""


def lint_file(root, rel, decisions):
    text = read(os.path.join(root, rel))
    front, body = split_front_matter(text)
    first = text[:len(text) - len(body)].count("\n") + 1
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    bad, allowed = lint_text(body, lint_layer(rel), decisions, first_line=first,
                             note_meta=rel.endswith(suffix))
    for row in bad + allowed:
        row["file"] = rel
    return bad, allowed


def lint_targets(root):
    """全 repo 的記憶檔:三層的主卡與暫存區、同步過去的角色目錄;README 是契約文件,不掃。"""
    rules = event.config(root).get("rules") or {}
    dirs = ["memory/role", "memory/model", "memory/project"]
    dirs += [rules[key] for key in ("roles_dir", "models_dir") if rules.get(key)]
    out = []
    for where in dirs:
        for path in sorted(globmod.glob(os.path.join(root, where, "*.md"))):
            rel = os.path.relpath(path, root)
            if os.path.basename(rel) != NOT_A_MEMORY and rel not in out:
                out.append(rel)
    return out


def print_lint(bad, out=sys.stderr):
    for row in bad:
        out.write("memory: lint %s:%d rule=%s %r%s —— 建議去處:%s\n"
                  % (row.get("file", "<正文>"), row["line"], row["rule"], row["value"],
                     "(%s)" % row["why"] if row.get("why") else "", row["destination"]))


# ------------------------------------------------------- lint --base(D-042)
#
# `--base` 問的是**這條分支加了什麼**,不是這張卡整份乾不乾淨:既有正文的命中歸整理票
# (`check` 開的那一張),拿來擋一個只在卡尾加一行的分支,等於要每張票順手整理
# 整張卡。改前 = 該卡在 merge-base(ref, HEAD) 的內容;改後 = 工作樹;改前不存在的卡
# (含未追蹤)整份都算新增。


def git_out(root, *args):
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def text_at(root, ref, rel):
    """`ref` 上那一份的全文;那一版沒有這個檔 → None。"""
    return git_out(root, "show", "%s:%s" % (ref, rel.replace(os.sep, "/")))


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(root, before, after):
    """改後相對改前的 `+` 行行號(改後的行號;新增與改寫都算)。比法是 git diff。"""
    if before is None:
        return set(range(1, len(after.splitlines()) + 1))
    with tempfile.TemporaryDirectory() as tmp:
        old, new = os.path.join(tmp, "before"), os.path.join(tmp, "after")
        for path, text in ((old, before), (new, after)):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        out = subprocess.run(["git", "-C", root, "diff", "--no-index", "--no-color",
                              "--unified=0", old, new], capture_output=True, text=True).stdout
    lines = set()
    for row in out.splitlines():
        found = HUNK.match(row)
        if found:
            start, count = int(found.group(1)), int(found.group(2) or 1)
            lines.update(range(start, start + count))
    return lines


def cmd_lint(argv):
    files, as_json, base, index = [], False, None, 0
    while index < len(argv):
        flag = argv[index]
        if flag == "--json":
            as_json = True
            index += 1
        elif flag == "--file" and index + 1 < len(argv):
            files.append(argv[index + 1])
            index += 2
        elif flag == "--base" and index + 1 < len(argv):
            base = argv[index + 1]
            index += 2
        else:
            return unknown_flag(flag)
    root = ticket.root()
    decisions = decisions_text(root)
    targets = [os.path.relpath(os.path.join(root, rel), root) for rel in files] \
        if files else lint_targets(root)
    fork = None
    if base is not None:
        fork = (git_out(root, "merge-base", base, "HEAD") or "").strip()
        if not fork:
            sys.stderr.write("memory: lint --base 問不到 merge-base(%s, HEAD)—— 量不了新增行\n"
                             % base)
            return 2
    bad, allowed, scanned = [], [], []
    for rel in targets:
        try:
            more, ok = lint_file(root, rel, decisions)
            after = read(os.path.join(root, rel)) if fork else None
        except OSError as exc:
            sys.stderr.write("memory: lint 讀不到 %s —— %s\n" % (rel, exc))
            return 2
        if fork:
            before = text_at(root, fork, rel)
            fresh = added_lines(root, before, after)
            more = [row for row in more if row["line"] in fresh]
            ok = [row for row in ok if row["line"] in fresh]
        scanned.append(rel)
        bad.extend(more)
        allowed.extend(ok)
    data = {"scanned": len(scanned), "files": scanned, "violations": bad, "allowed": allowed,
            "ok": bool(scanned) and not bad}
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    else:
        for row in allowed:
            sys.stdout.write("memory: lint 例外 %s:%d rule=%s %r(reason=%s ref=%s)\n"
                             % (row["file"], row["line"], row["rule"], row["value"],
                                row["reason"], row["ref"]))
        sys.stdout.write("memory: lint 掃了 %d 份、命中 %d 條、例外 %d 條\n"
                         % (len(scanned), len(bad), len(allowed)))
    print_lint(bad)
    if not scanned:
        sys.stderr.write("memory: lint 一份都沒掃到 —— 空掃描不算過\n")
        return 2
    return 1 if bad else 0


def cmd_note(argv):
    if len(argv) < 3:
        sys.stderr.write("memory: %s\n" % USAGE["note"])
        return 2
    layer, name, text = argv[:3]
    ticket_no = ""
    by = ""
    index = 3
    while index < len(argv):
        flag = argv[index]
        if flag in ("--ticket", "--by") and index + 1 < len(argv):
            if flag == "--ticket":
                ticket_no = argv[index + 1]
            else:
                by = argv[index + 1]
            index += 2
            continue
        return unknown_flag(flag)
    if layer not in ("model", "role", "project"):
        sys.stderr.write("memory: 層只認 model / role / project\n")
        return 2
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    name, problem = note_name(name, suffix)
    if problem:
        sys.stderr.write(problem)
        return 2
    if "\n" in text or "\r" in text:
        sys.stderr.write("memory: note 一次只能寫一行\n")
        return 2
    if len(text) > 300:
        sys.stderr.write("memory: note 最多 300 字元\n")
        return 2
    if by and ("@" not in by or by.rsplit("@", 1)[1] == ""):
        sys.stderr.write("memory: --by 要是 <role>@<model>\n")
        return 2
    if not by:
        by = "?@%s" % name if layer == "model" else "?@?"
    if layer == "model" and by.rsplit("@", 1)[1] != name:
        sys.stderr.write("memory: model 層只能由同模型寫入(%s != %s)\n"
                         % (by.rsplit("@", 1)[1], name))
        return 2
    if any(word in text for word in ("當時", "那次")):
        sys.stderr.write("memory: 警告:這句像案例;記憶只留原則,案例請用票號指路\n")
    root = ticket.root()
    # 分層 lint 在寫檔、發 noted **之前**(#87 A34):錯層的那一行一個位元組都不落地。
    bad, _ = lint_text(text, layer, decisions_text(root))
    if bad:
        print_lint(bad)
        sys.stderr.write("memory: note 拒絕寫入 %s/%s —— 錯層內容(見上面 lint 那幾行)\n"
                         % (layer, name))
        return 2
    if layer == "project":
        path = os.path.join(root, "memory", layer, name + ".md")
    else:
        path = os.path.join(root, "memory", layer, name + suffix)
    meta = []
    if ticket_no:
        meta.append("#%s" % ticket_no.lstrip("#"))
    # auto-fix 每輪 harvest 同一份 EVIDENCE;重送要 rc 0,harvest 把非零當「拒絕」。
    if already_noted(path, text, meta[0] if meta else ""):
        sys.stdout.write("memory: 已有同一條 %s/%s,不重寫\n" % (layer, name))
        return 0
    meta.extend((date.today().isoformat(), by))
    append_line(path, "- %s (%s)\n" % (text, ", ".join(meta)))
    event.emit("memory.noted", layer=layer, name=name, ticket=ticket_no,
               by=by, note=text)
    sys.stdout.write("memory: noted %s/%s\n" % (layer, name))
    return 0


MEMORY_HEADING = re.compile(r"^(?:#+\s*)?記憶[:：]?\s*$")


def cmd_harvest(argv):
    if len(argv) != 1:
        sys.stderr.write("memory: %s\n" % USAGE["harvest"])
        return 2
    path = argv[0]
    try:
        lines = read(path).splitlines()
    except OSError as exc:
        sys.stderr.write("memory: evidence 讀不到:%s\n" % exc)
        return 2
    inside = False
    found = False
    for raw in lines:
        stripped = raw.strip()
        if MEMORY_HEADING.match(stripped):
            inside = True
            found = True
            continue
        if inside and stripped.startswith("#"):
            break
        if not inside or not stripped or stripped in ("無", "- 無"):
            continue
        line = stripped[2:].strip() if stripped.startswith("- ") else stripped
        line = line.strip("`")
        try:
            words = shlex.split(line)
        except ValueError as exc:
            sys.stderr.write("memory: 拒絕 evidence 行:%s (%s)\n" % (raw, exc))
            continue
        prefix = 1 if words[:1] == ["python3"] else 0
        if (len(words) < prefix + 2
                or os.path.basename(words[prefix]) != "memory.py"
                or words[prefix + 1] != "note"):
            sys.stderr.write("memory: 拒絕 evidence 行:%s\n" % raw)
            continue
        args = words[prefix + 2:]
        if cmd_note(args) != 0:
            sys.stderr.write("memory: 拒絕 evidence 行:%s\n" % raw)
    if not found:
        sys.stdout.write("memory: evidence 沒有記憶段 —— 0 筆\n")
    return 0


def watched_files():
    """`memory.applies_to` 展開。兩種檔不是主檔:

    - `.inbox.md`:整理期間的暫存區(docs/MEMORY.md 第 4 點)。
    - **`README.md`**:說明檔不是誰的記憶。它躺在同一個目錄裡、副檔名也對,所以
      glob 抓得到它 —— 而量一份說明檔的字元數,量出來的數字沒有人要用:它不是
      任何一個 session 的「第一口空氣」,壓縮它也不會替誰省下什麼。

    跳過的判準寫在這裡而不是改 glob、也不是加一格白名單:glob 是設定
    (`board/config.json`),專案換一個寫法這條就漏了;白名單要靠人記得更新,而它
    靜默失效的那天沒有人會知道(`docs/DISPATCH-TEMPLATE.md` §5.7)。「檔名是
    README.md」是一條**規則**,兩者都不是。
    """
    conf = memory_config()
    root = ticket.root()
    patterns = conf.get("applies_to") or list(DEFAULT_APPLIES)
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    out = []
    for pattern in patterns:
        normalized = os.path.normpath(pattern)
        project = os.path.join("memory", "project")
        if normalized == project or normalized.startswith(project + os.sep):
            sys.stdout.write("memory: %s 是 project 紀錄層,不做整理檢查 —— 已跳過\n"
                             % pattern)
            continue
        for path in sorted(globmod.glob(os.path.join(root, pattern))):
            rel = os.path.relpath(path, root)
            if rel.endswith(suffix) or os.path.basename(rel) == NOT_A_MEMORY:
                continue
            out.append(rel)
    return out


def covers(rel, one):
    """這張票的 `memory_file` 是不是這份記憶檔(#137;查重與冷卻共用同一個匹配)。"""
    return one.get("memory_file") == rel


def recent_consolidation(rel):
    """這份主檔最近一張整理票(role=consolidator、`memory_file` 是它,**任何 state**)的 `created`,
    在 BATCH_COOLDOWN_HOURS 小時內 → 回票號;否則 None。取消過的也算觸發過。"""
    for one in ticket.load_all():
        if one.get("role") != CONSOLIDATOR_ROLE or not covers(rel, one):
            continue
        try:
            made = datetime.fromisoformat(one.get("created") or "")
        except ValueError:
            continue
        if made.tzinfo is None:
            made = made.astimezone()
        age = datetime.now().astimezone() - made
        if age.total_seconds() < BATCH_COOLDOWN_HOURS * 3600:
            return one["id"]
    return None


def oldest_age_days(lines):
    """最舊一條 note 的日期距今幾天;沒有可解析日期的條目不算進年齡。全沒有 → None。"""
    ages = []
    for line in lines:
        found = NOTE_LINE.match(line)
        if not found:
            continue
        for tag in found.group(2).split(", "):
            try:
                ages.append((date.today() - date.fromisoformat(tag.strip())).days)
            except ValueError:
                continue
            break
    return max(ages) if ages else None


def open_consolidation(rel):
    """已經有一張開著的票蓋住這份記憶檔?**不重複開。** 回 `(票號, role)` 或 `(None, None)`。

    判準是那張票自己的 `memory_file`(整理對象的 repo 相對路徑,逐字相等;#137)是不是
    這份記憶檔 —— 不是另記一格「開過了」。另記的那一格會跟事實分岔(票被取消了、
    被關了),而分岔的那天沒有人會知道。沒有 `memory_file` 的票(#137 之前開的)不算。

    **任何 role 都算**(#87 A38):一張開著、`memory_file` 是這份檔的別的 role 的票
    也擋。整理票優先回報(字樣照舊);沒有才回別的 role 那一張。
    """
    other = (None, None)
    for one in ticket.load_all():
        if not ticket.is_open(one):
            continue
        if not covers(rel, one):
            continue
        if one.get("role") == CONSOLIDATOR_ROLE:
            return one["id"], CONSOLIDATOR_ROLE
        if other[0] is None:
            other = (one["id"], one.get("role") or "?")
    return other


def consolidators(conf):
    """整理由**兩個不同的模型**討論(D-013),不是單一 session 自己刪。

    理由:一個 session 刪自己的記憶時,最先刪掉的是它自己看不懂的那幾條 —— 而那正是
    別的模型看得出價值的那幾條。`memory.consolidators` 是一張名單;只設了舊的單數
    `memory.consolidator` 就退回一個人,並在票面上說出來。
    """
    many = conf.get("consolidators")
    if isinstance(many, list) and many:
        return [str(x) for x in many if x]
    one = conf.get("consolidator")
    if one:
        return [str(one)]
    # 舊設定遷移掉之後(#122 A3)票面的名字照角色表 consolidator 鏈的首選;起誰仍由
    # consolidate-memory.sh 當場 resolve,這一格只是票面寫給人看的。
    import importlib.util                                  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "role_models", os.path.join(os.path.dirname(os.path.abspath(__file__)), "role-models.py"))
    role_models = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(role_models)
        chain = role_models.load_table(role_models.table_path(ticket.root()))["roles"]
    except (OSError, ValueError):
        return []
    return [row["model"] for row in chain.get(CONSOLIDATOR_ROLE, [])[:1]]


def open_ticket_for(rel, entries, out):
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    who = consolidators(conf)
    model = ", ".join(who)
    inbox = inbox_path(rel, suffix)
    # 一個模型 = 單一高階整理者(D-043 / D-049 ②),consolidate-memory.sh 起一個 session。
    if len(who) == 1:
        who_says = "**%s 單獨整理**(D-043 / D-049 ②,角色表 consolidator 鏈);討論檔寫得出每一行處置的理由" % model
        how = ("派工照 `templates/dispatch-consolidator.md`(主線開場由 `consolidate-memory.sh` 照角色表起"
               "**一個** session;規則包 `python3 scripts/rules.py pack consolidator --model <模型>`,"
               "角色卡 `memory/role/consolidator.md`)。"
               "先 `python3 scripts/memory.py snapshot %s` 把兩行抄進討論檔頭,逐行寫處置,最後跑 "
               "`python3 scripts/memory.py consolidate %s --candidate <新版> "
               "--discussion <啟動端記下的那一份討論檔>`。" % (rel, rel))
    else:
        who_says = "**兩個不同的模型**參與討論(%s);討論檔列得出雙方的分歧" \
                   % (model or "名單見 board/role-models.json 的 consolidator 鏈")
        how = ("派工照 `templates/dispatch-consolidator.md`(兩個模型各開一個 session、各貼一次;"
               "規則包 `python3 scripts/rules.py pack consolidator --model <模型>`,"
               "角色卡 `memory/role/consolidator.md`)。"
               "先 `python3 scripts/memory.py snapshot %s` 把兩行抄進各自的討論檔頭,"
               "再互讀寫分歧,最後一位跑 "
               "`python3 scripts/memory.py consolidate %s --candidate <新版> "
               "--discussion <討論檔 A> --discussion <討論檔 B>`。" % (rel, rel))
    argv = [
        "--subject", "整理 %s(暫存區 %d 條未整理)" % (rel, entries),
        "--objective",
        "由 %s 照 docs/DISCUSSION.md 的格式討論後整理 %s:處置 %s 在 snapshot 那一刻的每一行"
        "(D-053 ⑥ / D-055 ① 暫存區達批次門檻就整理)。判準只有一句:每個 session 開場都會讀，"
        "只留值得這個成本的;寫什麼、留多長由你決定,沒有字數目標(D-053 ⑥)。"
        "D-007「沒有討論檔不准整理」、D-013「兩個模型或一個高階模型、只留原則」、"
        "D-043「單一高階整理者」" % (model or "兩個不同的模型", rel, inbox),
        "--acceptance", "`memory.py consolidate %s` 退出碼 0(snapshot 那 K 行已移進 `.consumed`)"
                        % rel,
        "--acceptance", who_says,
        "--acceptance", "整理後只留**具體的原則、行為準則、思考方式**;**不直接寫案例**,"
                        "案例用票號當來源引用(例:「快照層只畫說得出處的畫面(#585)」)",
        "--acceptance", "案例原文留在紀錄類文件(DECISIONS 歸檔 / HANDOFF 歷史),記憶檔只留指路",
        "--acceptance", "保留的每一條仍帶日期 / 來源票號 / 實測或推論三個標記",
        "--acceptance", "討論存成 discussions/<date>-memory-<model>.md(docs/DISCUSSION.md 的格式:"
                        "檔頭 model / source_lines / source_sha256,結論區逐條 `- L<n>: <處置>`)",
        # 怎麼派 session:**一句可以貼的指令住在範本裡**(#29 A8,G8)。以前
        # 票面只寫「由兩個模型討論」,而「兩個 session 怎麼被派、討論檔誰先寫」沒有
        # 任何一份檔說得出來 —— 一張沒有人知道怎麼執行的票,與沒有開票長得一樣。
        "--outline", how,
        "--role", CONSOLIDATOR_ROLE,
        "--model", model,
        "--tool", "claude-code",
        "--state", "Ready",
    ]
    said = io.StringIO()
    done = ticket.cmd_create(argv, stdout=said)
    out.write(said.getvalue())
    found = re.search(r"#(\S+) 開好了", said.getvalue())
    if done == 0 and found:
        # `ticket.py create` 沒有這兩格的旗標(#76 不改 ticket.py);開好再補。
        # `memory_file` 是整理對象(#137),`open_consolidation` 與 consolidate-memory.sh 只認它。
        with ticket.Lock():
            row = ticket.load(found.group(1))
            row["needs_verifier"] = False
            row["memory_file"] = rel
            ticket.save(row)
    return done


def inbox_rows(path):
    """一份 inbox 的每一條:`(行號, 分類, 來源)`。只讀,不碰票庫以外的東西。

    來源只認本 repo 票庫(`ticket` 模組設定的那一個):別的 repo 有同號的票不算 ——
    `#3` 在這裡指的是這裡的 #3。
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    rows = []
    states = {}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        found = NOTE_LINE.match(line)
        if not found:
            rows.append((number, "unparsed", "-"))
            continue
        tags = [one for one in found.group(2).split(", ") if one.startswith("#")]
        if not tags:
            rows.append((number, "no-source", "-"))
            continue
        tag = tags[0]
        digits = TICKET_TAG.match(tag)
        if not digits:
            rows.append((number, "foreign", tag))
            continue
        ident = digits.group(1)
        if ident not in states:
            try:
                states[ident] = ticket.load(ident).get("state")
            except (OSError, ValueError):
                states[ident] = None
        state = states[ident]
        if state is None:
            rows.append((number, "unknown-ticket", tag))
        elif state in ticket.CLOSED_STATES:
            rows.append((number, "needs-review", "%s %s" % (tag, state)))
        else:
            rows.append((number, "fresh", "%s %s" % (tag, state)))
    return rows


def inbox_files(suffix):
    root = ticket.root()
    out = []
    for layer in ("model", "role"):
        pattern = os.path.join(root, "memory", layer, "*" + suffix)
        out.extend(os.path.relpath(path, root) for path in sorted(globmod.glob(pattern)))
    return out


def cmd_check_stale(argv):
    only = None
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag == "--read-only":
            index += 1
            continue
        if flag == "--file" and index + 1 < len(argv):
            only = argv[index + 1]
            index += 2
            continue
        sys.stderr.write("memory: check-stale 不認得 %r\n" % flag)
        sys.stderr.write("memory: %s\n" % USAGE["check-stale"])
        return 2
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    root = ticket.root()
    names = inbox_files(suffix)
    if only is not None:
        rel = os.path.relpath(os.path.join(root, only), root)
        if not rel.endswith(suffix):
            rel = inbox_path(rel, suffix)
        if os.path.dirname(rel) not in (os.path.join("memory", "model"),
                                        os.path.join("memory", "role")):
            sys.stderr.write("memory: check-stale 只讀 memory/model、memory/role 的 inbox"
                             "(%s 不是)\n" % only)
            return 2
        names = [rel] if rel in names else []
    review = 0
    for rel in names:
        for number, kind, source in inbox_rows(os.path.join(root, rel)):
            sys.stdout.write("memory: %s:%d %s %s\n" % (rel, number, kind, source))
            review += kind == "needs-review"
    return 1 if review else 0


def cmd_check(argv):
    read_only = False
    for flag in argv:
        if flag != "--read-only":
            sys.stderr.write("memory: check 不認得 %r\n" % flag)
            sys.stderr.write("memory: %s\n" % USAGE["check"])
            return 2
        read_only = True
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    root = ticket.root()
    # 受管主檔 = applies_to 展開的那幾份,加上 memory/model、memory/role 底下有 inbox 的
    # 每一份(主檔還不存在也算 —— 第一條 note 落地時只有 inbox)。
    mains = watched_files()
    inboxes = {}
    for rel in inbox_files(suffix):
        main_rel = rel[:-len(suffix)] + ".md"
        inboxes[main_rel] = rel
        if main_rel not in mains:
            mains.append(main_rel)
    flagged = 0
    # 主檔 → 暫存區幾條未整理。報數照印;達批次門檻(D-055 ①)才發事件、開票。
    pending = {}
    for rel in mains:
        path = os.path.join(root, rel)
        try:
            chars = body_chars(read(path)) if os.path.exists(path) else 0
            box = inboxes.get(rel)
            entries = 0
            lines = []
            if box:
                with open(os.path.join(root, box), encoding="utf-8") as handle:
                    lines = [line.rstrip("\n") for line in handle if line.strip()]
                entries = len(lines)
        except OSError as exc:
            sys.stdout.write("memory: 讀不到 %s —— %s\n" % (rel, exc))
            flagged += 1
            continue
        sys.stdout.write("memory: %s %d 字元 / 暫存區 %d 條未整理\n" % (rel, chars, entries))
        age = oldest_age_days(lines)
        if entries < BATCH_MIN_ENTRIES and not (age is not None and age >= BATCH_MAX_AGE_DAYS):
            continue
        # 開著的票走下面的查重(字樣照 #134);冷卻只管已經關掉的那幾張。
        recent = None if open_consolidation(rel)[0] else recent_consolidation(rel)
        if recent:
            sys.stdout.write("memory:   %s 的整理票 #%s 在 %d 小時內開過,這次不開(條目留著)\n"
                             % (rel, recent, BATCH_COOLDOWN_HOURS))
            continue
        flagged += 1
        if not read_only:
            event.emit("memory.pending", file=box, entries=entries)
        pending[rel] = entries
    if pending and not read_only:
        # 查核與開票在同一把鎖裡:兩個主線同時開場,「沒有整理票」在兩邊都成立,
        # 鎖外各開一張就是兩張。
        with MemoryLock(root):
            for rel, entries in pending.items():
                already, role = open_consolidation(rel)
                if already and role == CONSOLIDATOR_ROLE:
                    sys.stdout.write("memory:   %s 已經有一張開著的整理票 #%s,沒有再開\n"
                                     % (rel, already))
                    continue
                if already:
                    sys.stdout.write("memory:   已經有一張開著的票 #%s(role=%s)覆蓋 %s,"
                                     "沒有再開\n" % (already, role, rel))
                    continue
                open_ticket_for(rel, entries, sys.stdout)
    # 退出碼 1 是給 session 開頭看的:`new-session.sh` 跑這一支,紅了那個 session
    # 就知道有暫存區待整理 —— **但不停工**(docs/MEMORY.md 第 1 點)。
    return 1 if flagged else 0


def raw_lines(data):
    """位元組照原樣切行(行尾留著):前 K 行的 sha256 要與磁碟上那幾個位元組一致。"""
    parts = data.split(b"\n")
    tail = parts.pop()
    lines = [part + b"\n" for part in parts]
    if tail:
        lines.append(tail)
    return lines


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return b""


def snapshot_of(data, count=None):
    """`(行數, 前 count 行的位元組, sha256)`;count 不給就是整份。"""
    lines = raw_lines(data)
    head = b"".join(lines[:len(lines) if count is None else count])
    return len(lines), head, hashlib.sha256(head).hexdigest()


def cmd_snapshot(argv):
    if len(argv) != 1:
        sys.stderr.write("memory: %s\n" % USAGE["snapshot"])
        return 2
    root = ticket.root()
    path = argv[0] if os.path.isabs(argv[0]) else os.path.join(root, argv[0])
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    count, _, digest = snapshot_of(read_bytes(inbox_path(path, suffix)))
    sys.stdout.write("source_lines: %d\nsource_sha256: %s\n" % (count, digest))
    return 0


def header(text, key):
    found = re.search(r"^%s:[ \t]*(\S.*?)[ \t]*$" % key, text, re.MULTILINE)
    return found.group(1) if found else None


def read_discussion(root, name):
    """一份討論檔 → `(dict, None)` 或 `(None, 給人看的下一步)`。

    沒有檔、沒有結論區、缺檔頭、缺某一行的處置,是四件事四句話 —— 下一步各不相同
    (§5.7:守衛給錯下一步比沒有守衛更糟)。
    """
    path = name if os.path.isabs(name) else os.path.join(root, name)
    if not os.path.exists(path):
        return None, ("找不到討論檔 %s —— 先照 templates/discussion.md 開一份(D-007)"
                      % name)
    text = read(path)
    heading = re.search(r"^## 結論.*$", text, re.MULTILINE)
    if not heading:
        return None, ("%s 還沒有結論區 —— 一份沒有結論的討論檔,與一場沒談完的討論長得"
                      "一樣。把 `## 結論` 那幾格填完再來(D-007)" % name)
    rest = text[heading.end():]
    after = re.search(r"^## ", rest, re.MULTILINE)
    conclusion = rest[:after.start()] if after else rest
    talk = {"name": name, "conclusion": conclusion}
    for key in ("model", "source_lines", "source_sha256"):
        talk[key] = header(text, key)
        if not talk[key]:
            return None, ("%s 缺檔頭 `%s:` —— 三行(model / source_lines / source_sha256)"
                          "照 `memory.py snapshot <記憶檔>` 的輸出抄" % (name, key))
    try:
        talk["source_lines"] = int(talk["source_lines"])
    except ValueError:
        return None, "%s 的 source_lines 不是數字:%r" % (name, talk["source_lines"])
    handled = set(int(found.group(1)) for found in DISPOSITION_RE.finditer(conclusion))
    missing = [n for n in range(1, talk["source_lines"] + 1) if n not in handled]
    if missing:
        return None, ("%s 的結論區缺 %s 的處置 —— 每一行要有一句 `- L<n>: <%s>`"
                      % (name, ", ".join("L%d" % n for n in missing[:10]),
                         "|".join(DISPOSITIONS)))
    return talk, None


def launcher_mismatch(root, rel, talks):
    """討論檔頭的 `model:` 與整理票 attempt_history 記的模型不一致 → 一句點名;一致或沒有
    啟動端紀錄(人手派的)→ ""。模型名比全名或去掉工具前綴的那一半。"""
    ident, role = open_consolidation(rel)
    if not ident or role != CONSOLIDATOR_ROLE:
        return ""
    try:
        rows = ticket.load(ident).get("attempt_history") or []
    except (OSError, ValueError):
        return ""
    for talk in talks:
        name = talk["name"]
        where = os.path.normpath(os.path.relpath(
            name if os.path.isabs(name) else os.path.join(root, name), root))
        for row in rows:
            if not isinstance(row, dict) or not row.get("discussion"):
                continue
            if os.path.normpath(row["discussion"]) != where:
                continue
            recorded = str(row.get("model") or "")
            if talk["model"] not in (recorded, recorded.split(":")[-1]):
                return ("討論檔 %s 的 model: %s 與啟動端記的 %s 不一致(整理票 #%s 的 "
                        "attempt_history)" % (name, talk["model"], recorded, ident))
    return ""


def launcher_recorded(root, rel, talk):
    """單一整理者的那一份討論檔(#122,D-043):整理票的 attempt_history 裡有一筆是
    consolidate-memory.sh 記下的這個檔、模型與檔頭一致 → True。人手派的沒有這一筆。"""
    ident, role = open_consolidation(rel)
    if not ident or role != CONSOLIDATOR_ROLE:
        return False
    try:
        rows = ticket.load(ident).get("attempt_history") or []
    except (OSError, ValueError):
        return False
    name = talk["name"]
    where = os.path.normpath(os.path.relpath(
        name if os.path.isabs(name) else os.path.join(root, name), root))
    return any(isinstance(row, dict) and row.get("discussion")
               and os.path.normpath(row["discussion"]) == where
               and talk["model"] in (str(row.get("model") or ""),
                                     str(row.get("model") or "").split(":")[-1])
               for row in rows)


def refuse(message):
    sys.stderr.write("memory: %s\n" % message)
    return 2


def write_new(path, data):
    """只建新檔(`O_EXCL`):同名的 `.consumed` 已經在那裡就是另一輪的,不蓋。"""
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(handle, data)
    finally:
        os.close(handle)


def cmd_consolidate(argv):
    if not argv or argv[0].startswith("--"):
        sys.stderr.write("memory: %s\n" % USAGE["consolidate"])
        return 2
    rel = argv[0]
    by = ""
    candidate = ""
    discussions = []
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--by", "--discussion", "--candidate") and index + 1 < len(argv):
            if flag == "--discussion":
                discussions.append(argv[index + 1])
            elif flag == "--candidate":
                candidate = argv[index + 1]
            else:
                by = argv[index + 1]
            index += 2
            continue
        return unknown_flag(flag)
    if not [one for one in discussions if one.strip()]:
        return refuse("consolidate 要 --discussion <path> —— 整理是老師帶學生,而「討論過了」"
                      "與「沒討論就刪了」在結果檔案上長得一樣(D-007,格式見 docs/DISCUSSION.md)")
    root = ticket.root()
    talks = []
    for name in discussions:
        talk, problem = read_discussion(root, name)
        if problem:
            return refuse(problem)
        talks.append(talk)
    # 單一整理者(#122,D-043 / D-049 ②)只認啟動端記下的那一份;其餘照舊兩份兩模型。
    single = len(talks) == 1 and launcher_recorded(
        root, os.path.relpath(rel if os.path.isabs(rel) else os.path.join(root, rel), root),
        talks[0])
    if len(talks) != 2 and not single:
        return refuse("consolidate 要**兩份** --discussion(兩個不同的 model 各一份,D-013);"
                      "這次給了 %d 份(一份只收 consolidate-memory.sh 記在整理票 attempt_history 的那一份)"
                      % len(talks))
    if not candidate.strip():
        return refuse("consolidate 要 --candidate <檔> —— 整理後的新版正文;"
                      "主檔只會被換成驗過的候選,不會被就地改")
    models = [talk["model"] for talk in talks]
    if not single and models[0] == models[1]:
        return refuse("兩份討論檔的 model 都是 %s —— 一個模型自己整理不算數(D-013)"
                      % models[0])
    if (talks[0]["source_lines"], talks[0]["source_sha256"]) \
            != (talks[-1]["source_lines"], talks[-1]["source_sha256"]):
        return refuse("兩份討論檔的 source_lines / source_sha256 不同 —— 兩位討論的不是同一份"
                      "inbox 快照,重跑 `memory.py snapshot` 後對齊")
    count = talks[0]["source_lines"]
    expected = talks[0]["source_sha256"]
    spot = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
    try:
        candidate_body = split_front_matter(read(spot))[1]
    except OSError as exc:
        return refuse("讀不到候選 %s —— %s" % (candidate, exc))
    path = os.path.join(root, rel) if not os.path.isabs(rel) else rel
    rel = os.path.relpath(path, root)
    # 候選要先過分層 lint(#87 A34),在拿鎖、寫主檔、消耗 inbox、發 consolidated 之前。
    bad, _ = lint_text(candidate_body, lint_layer(rel), decisions_text(root))
    if bad:
        for row in bad:
            row["file"] = candidate
        print_lint(bad)
        return refuse("候選 %s 有錯層內容(見上面 lint 那幾行)—— 主檔沒有動" % candidate)
    # 討論的模型身份由**啟動端**記(#87 A41):consolidate-memory.sh 把每個 session 交出的
    # 討論檔與它實際起的模型寫進整理票的 attempt_history。檔頭的 `model:` 是 session 自己
    # 寫的 —— 對不上就是有一份不是它說的那個模型寫的,D-013 的「兩個不同模型」就不成立。
    mismatch = launcher_mismatch(root, rel, talks)
    if mismatch:
        sys.stderr.write("memory: %s —— 主檔沒有動\n" % mismatch)
        return 1
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    who = by or "+".join(models)
    inbox = inbox_path(path, suffix)
    consumed = ""
    with MemoryLock(root):
        try:
            text = read(path)
        except FileNotFoundError:
            text = ""
        except OSError as exc:
            return refuse("讀不到 %s —— %s" % (rel, exc))
        front, body = split_front_matter(text)
        before = len(body)
        # 在鎖裡重算:討論是對那一刻的 inbox 做的,對不上就是有人動過前 K 行,
        # 或者這一份快照已經被上一輪消耗了。
        total, head, digest = snapshot_of(read_bytes(inbox), count)
        if total < count or digest != expected:
            return refuse("inbox 的前 %d 行對不上討論檔的 source_sha256(現在 %d 行,sha256 %s)"
                          " —— 討論的不是這一份快照,重跑 `memory.py snapshot %s`"
                          % (count, total, digest, rel))
        out = FENCE + "\n" + front + FENCE + "\n" if front else ""
        write(path, out + candidate_body)
        if count:
            run_id = os.environ.get("AC_RUN_ID") or "%s-%d" \
                % (datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())
            consumed = "%s.%s.consumed" % (inbox, run_id)
            write_new(consumed, head)
            # note 不拿這把鎖:改名之後的新條目落進一份新的 inbox,改名之前的留在
            # 改過名的那一份 —— 兩邊都不會掉,第 K+1 行以後接回 inbox。
            hold = "%s.%s.rest" % (inbox, run_id)
            os.rename(inbox, hold)
            rest = read_bytes(hold)[len(head):]
            if rest:
                append_line(inbox, rest.decode("utf-8"))
            os.remove(hold)
    after = len(candidate_body)
    event.emit("memory.consolidated", file=rel, before=before, after=after,
               source_lines=count, source_sha256=expected,
               consumed=os.path.relpath(consumed, root) if consumed else "",
               by=who, models=models, discussions=discussions, candidate=candidate)
    sys.stdout.write("memory: %s %d -> %d 字元、消耗 inbox 前 %d 行、討論 %s\n"
                     % (rel, before, after, count, " + ".join(discussions)))
    return 0


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    verbs = {"check": cmd_check, "check-stale": cmd_check_stale, "note": cmd_note,
             "harvest": cmd_harvest, "snapshot": cmd_snapshot,
             "consolidate": cmd_consolidate, "lint": cmd_lint}
    if verb in ("--help", "-h", "help"):
        if rest and rest[0] in verbs:
            return help_for(rest[0])
        sys.stdout.write(__doc__)
        return 0
    if verb in verbs and ("--help" in rest or "-h" in rest):
        return help_for(verb)
    if verb in verbs:
        return verbs[verb](rest)
    sys.stderr.write("memory: 不認得 %r(%s)\n" % (verb, " / ".join(verbs)))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
