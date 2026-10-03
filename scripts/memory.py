#!/usr/bin/env python3
"""記憶的容量與整理 — D-006(`docs/MEMORY.md`「容量與整理:老師帶學生」)。

    scripts/memory.py check [--read-only]      # session 開頭跑;超過上限 → 退出碼 1
    scripts/memory.py check-stale --read-only [--file <主檔或 inbox>]   # 逐條分類來源
    scripts/memory.py harvest EVIDENCE.md      # 收割「記憶」段的 note 指令
    scripts/memory.py snapshot memory/model/opus.md   # 討論檔頭要抄的兩行
    scripts/memory.py consolidate memory/model/opus.md --candidate <新版> \
        --discussion discussions/2026-09-12-memory-opus-fable.md \
        --discussion discussions/2026-09-12-memory-opus-opus.md \
        --new-cap 2600 --reason "四條 land 事故的反例各不相同,合併會失去可辨識性"

## 為什麼量字元
**字元是唯一不需要 tokenizer 的決定性量法。** 用 token 量會讓「這份檔多大」取決於
哪一個模型在問,而同一份檔在兩個 session 得到兩個答案的上限,不是上限。

## 為什麼只管模型私有記憶
上限管的是**每個新 session 的第一口空氣**:`memory/model/<model>.md` 是同模型每個
session 開頭都要讀的東西,多一個字就是每一個 session 都多讀一個字。專案共用層
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

## 為什麼提高上限要有理由
上限可以被討論結果打破,但**提高是掙來的**。一個沒有理由的 `cap_chars: 4000` 與
一場真的做過的討論長得一模一樣,而前者是把「還沒整理」重新命名成「上限比較高」。
所以 `--new-cap` 沒有 `--reason` 直接拒絕,理由連同前後值寫進檔案的 front matter
`cap_history` —— 寫在**那份檔自己身上**,下一個要再提高的人看得到上一次的理由。
"""

import errno
import glob as globmod
import hashlib
import io
import os
import re
import shlex
import sys
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event      # noqa: E402
import ticket     # noqa: E402

DEFAULT_CAP = 2000
# D-013:**任一層記憶**都受上限管,不只模型私有那一層 —— 角色卡與專案共識一樣是
# 「每個 session 的第一口空氣」。紀錄類文件(DECISIONS / HANDOFF)不在這裡:
# 它們是**用 grep 查的**,不是每次載入的。
DEFAULT_APPLIES = ("memory/model/*.md", "memory/role/*.md")
DEFAULT_INBOX_SUFFIX = ".inbox.md"
DEFAULT_INBOX_MAX_LINES = 20
NOT_A_MEMORY = "README.md"     # 說明檔不是誰的記憶 —— glob 抓得到它,上限不該管它
CONSOLIDATOR_ROLE = "consolidator"
LOCK_NAME = ".lock"
LOCK_TIMEOUT = 10.0
FENCE = "---"
CAP_RE = re.compile(r"^cap_chars:\s*(\d+)\s*$", re.MULTILINE)


DISPOSITIONS = ("保留", "升格", "移至 reference", "歸檔", "重複")
DISPOSITION_RE = re.compile(r"^- L(\d+):[ \t]*(%s)" % "|".join(DISPOSITIONS), re.MULTILINE)
TICKET_TAG = re.compile(r"^#(\d+)$")

CONSOLIDATE_FLAGS = (
    ("--candidate", "整理後的新版正文(必填;正文不得超過上限 —— 新上限有給就量新上限)"),
    ("--discussion", "討論檔路徑,**給兩次、兩個不同的 model**(D-007 / D-013;"
                     "格式見 docs/DISCUSSION.md)"),
    ("--new-cap", "新的上限字元數;要配 --reason"),
    ("--reason", "為什麼值得提高 —— 寫得出「多讀的那幾百字替每個未來 session 省了什麼」"),
    ("--by", "誰帶的討論;不給就用 config 的 memory.consolidator"),
)

USAGE = {
    "check": "scripts/memory.py check [--read-only]         # 超過上限退出碼 1(不停工)",
    "check-stale": "scripts/memory.py check-stale --read-only [--file <主檔或 inbox 相對路徑>]",
    "snapshot": "scripts/memory.py snapshot <記憶檔>             # 印 source_lines / source_sha256",
    "note": "scripts/memory.py note <model|role|project> <名> \"<一行>\" [--ticket N] [--by <role>@<model>]",
    "harvest": "scripts/memory.py harvest <EVIDENCE.md>",
    "consolidate": ("scripts/memory.py consolidate <記憶檔> --candidate <檔> --discussion <A> "
                    "--discussion <B> [--new-cap N --reason …] [--by …]"),
}

EXAMPLE = {
    "check": "python3 scripts/memory.py check",
    "check-stale": "python3 scripts/memory.py check-stale --read-only --file memory/role/worker.md",
    "snapshot": "python3 scripts/memory.py snapshot memory/model/opus.md",
    "note": ('python3 scripts/memory.py note role implementer '
             '"變異後先確認替換真的生效" --ticket 17 --by worker@opus'),
    "consolidate": ('python3 scripts/memory.py consolidate memory/model/opus.md \\\n'
                    '  --candidate discussions/2026-09-12-memory-opus.candidate.md \\\n'
                    '  --discussion discussions/2026-09-12-memory-opus-fable.md \\\n'
                    '  --discussion discussions/2026-09-12-memory-opus-opus.md \\\n'
                    '  --new-cap 2600 --by fable \\\n'
                    '  --reason "四條 land 事故的反例各不相同,合併會失去可辨識性"'),
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

    **量的是正文** —— front matter 是這份檔的中繼資料(上限、上限的歷史),不是
    每個 session 要讀的內容。把它算進去會有一個很難看見的後果:**寫一次提高上限
    的理由,就把自己往上限推近一步**,於是誠實記錄會被制度懲罰。
    """
    if not text.startswith(FENCE + "\n"):
        return None, text
    end = text.find("\n" + FENCE + "\n", len(FENCE))
    if end < 0:
        return None, text
    return text[len(FENCE) + 1:end + 1], text[end + len(FENCE) + 2:]


def cap_of(front, default):
    if not front:
        return default, False
    found = CAP_RE.search(front)
    return (int(found.group(1)), True) if found else (default, False)


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
    """`memory.applies_to` 展開。兩種檔不受上限管:

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
            sys.stdout.write("memory: %s 是 project 紀錄層,不做容量檢查 —— 已跳過\n"
                             % pattern)
            continue
        for path in sorted(globmod.glob(os.path.join(root, pattern))):
            rel = os.path.relpath(path, root)
            if rel.endswith(suffix) or os.path.basename(rel) == NOT_A_MEMORY:
                continue
            out.append(rel)
    return out


def open_consolidation(rel):
    """已經有一張開著的整理票?**不重複開。**

    判準是那張票自己的 `allowed_write_paths` 蓋不蓋得到這份記憶檔 —— 不是另記一格
    「開過了」。另記的那一格會跟事實分岔(票被取消了、被關了),而分岔的那天沒有
    人會知道。蓋不蓋得到用 `ticket.first_match`,與 land 判寫入範圍同一支:逐字比對
    認不得 `memory/role/*.md`,主線開場就會為一份已經有人在整理的檔再開一張(#81)。
    """
    for one in ticket.load_all():
        if not ticket.is_open(one):
            continue
        if one.get("role") != CONSOLIDATOR_ROLE:
            continue
        if ticket.first_match(rel, one.get("allowed_write_paths") or []):
            return one["id"]
    return None


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
    return [str(one)] if one else []


def open_ticket_for(rel, reasons, out):
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    who = consolidators(conf)
    model = ", ".join(who)
    inbox = inbox_path(rel, suffix)
    argv = [
        "--subject", "整理 %s(%s)" % (rel, ";".join(reasons)),
        "--objective",
        "由 %s 照 docs/DISCUSSION.md 的格式討論後壓縮 %s,或者寫出理由把上限提高"
        "(D-006「老師帶學生」、D-007「沒有討論檔不准整理」、D-013「兩個模型、只留原則」);"
        "處置 %s 在 snapshot 那一刻的每一行" % (model or "兩個不同的模型", rel, inbox),
        "--acceptance", "`scripts/memory.py check` 對 %s 退出碼 0" % rel,
        "--acceptance", "`scripts/memory.py check-stale --read-only --file %s` 沒有 needs-review"
                        % rel,
        "--acceptance", "**兩個不同的模型**參與討論(%s);討論檔列得出雙方的分歧"
                        % (model or "名單見 board/config.json 的 memory.consolidators"),
        "--acceptance", "整理後只留**具體的原則、行為準則、思考方式**;**不直接寫案例**,"
                        "案例用票號當來源引用(例:「快照層只畫說得出處的畫面(#585)」)",
        "--acceptance", "案例原文留在紀錄類文件(DECISIONS 歸檔 / HANDOFF 歷史),記憶檔只留指路",
        "--acceptance", "保留的每一條仍帶日期 / 來源票號 / 實測或推論三個標記",
        "--acceptance", "提高上限的話,`cap_history` 有一列寫得出多讀的那幾百字省了什麼",
        "--acceptance", "討論存成 discussions/<date>-memory-<model>.md(docs/DISCUSSION.md 的格式:"
                        "檔頭 model / source_lines / source_sha256,結論區逐條 `- L<n>: <處置>`)",
        "--in-scope", rel,
        "--in-scope", inbox,
        "--out-of-scope", "docs/DECISIONS.md",
        "--allowed-write-path", rel,
        "--allowed-write-path", inbox,
        # 怎麼派這兩個 session:**一句可以貼的指令住在範本裡**(#29 A8,G8)。以前
        # 票面只寫「由兩個模型討論」,而「兩個 session 怎麼被派、討論檔誰先寫」沒有
        # 任何一份檔說得出來 —— 一張沒有人知道怎麼執行的票,與沒有開票長得一樣。
        "--outline",
        "派工照 `templates/dispatch-consolidator.md`(兩個模型各開一個 session、各貼一次;"
        "規則包 `python3 scripts/rules.py pack consolidator --model <模型>`,"
        "角色卡 `memory/role/consolidator.md`)。"
        "先 `python3 scripts/memory.py snapshot %s` 把兩行抄進各自的討論檔頭,"
        "再互讀寫分歧,最後一位跑 "
        "`python3 scripts/memory.py consolidate %s --candidate <新版> "
        "--discussion <討論檔 A> --discussion <討論檔 B>`。" % (rel, rel),
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
        # `ticket.py create` 沒有這一格的旗標(#76 不改 ticket.py);開好再補。
        with ticket.Lock():
            row = ticket.load(found.group(1))
            row["needs_verifier"] = False
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
    conf = memory_config()
    default_cap = int(conf.get("cap_chars") or DEFAULT_CAP)
    over = 0
    # 主檔 → 為什麼要整理。一份主檔一張票:字元超標、inbox 超行、needs-review 併成一句。
    pending = {}
    for rel in watched_files():
        path = os.path.join(ticket.root(), rel)
        try:
            text = read(path)
        except OSError as exc:
            sys.stdout.write("memory: 讀不到 %s —— %s\n" % (rel, exc))
            over += 1
            continue
        front, body = split_front_matter(text)
        cap, from_file = cap_of(front, default_cap)
        chars = len(body)
        where = "檔上的" if from_file else "設定的"
        if chars <= cap:
            sys.stdout.write("memory: %s %d / %d(%s上限)\n" % (rel, chars, cap, where))
            continue
        over += 1
        sys.stdout.write("memory: %s **%d / %d 超過**(%s上限)\n"
                         % (rel, chars, cap, where))
        if not read_only:
            event.emit("memory.over_cap", file=rel, chars=chars, cap=cap)
        pending.setdefault(rel, []).append("%d 字元,上限 %d" % (chars, cap))
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    max_lines = int(conf.get("inbox_max_lines") or DEFAULT_INBOX_MAX_LINES)
    root = ticket.root()
    for rel in inbox_files(suffix):
        path = os.path.join(root, rel)
        main_rel = rel[:-len(suffix)] + ".md"
        with open(path, encoding="utf-8") as handle:
            lines = sum(1 for line in handle if line.strip())
        if lines > max_lines:
            over += 1
            sys.stdout.write("memory: %s **%d / %d 行超過**\n"
                             % (rel, lines, max_lines))
            if not read_only:
                event.emit("memory.over_cap", file=rel, lines=lines, cap=max_lines)
            pending.setdefault(main_rel, []).append("inbox %d 行,上限 %d 行"
                                                    % (lines, max_lines))
        review = sum(1 for _, kind, _ in inbox_rows(path) if kind == "needs-review")
        if review:
            over += 1
            sys.stdout.write("memory: %s %d 條 needs-review(來源票已結案;逐條:"
                             "memory.py check-stale --read-only --file %s)\n"
                             % (rel, review, rel))
            pending.setdefault(main_rel, []).append("inbox %d 條 needs-review" % review)
    if pending and not read_only:
        # 查核與開票在同一把鎖裡:兩個主線同時開場,「沒有整理票」在兩邊都成立,
        # 鎖外各開一張就是兩張。
        with MemoryLock(root):
            for rel, reasons in pending.items():
                already = open_consolidation(rel)
                if already:
                    sys.stdout.write("memory:   %s 已經有一張開著的整理票 #%s,沒有再開\n"
                                     % (rel, already))
                    continue
                open_ticket_for(rel, reasons, sys.stdout)
    # 退出碼 1 是給 session 開頭看的:`new-session.sh` 跑這一支,紅了那個 session
    # 就知道自己的記憶該整理了 —— **但不停工**(docs/MEMORY.md 第 1 點)。
    return 1 if over else 0


def add_cap_history(front, entry):
    """`cap_history` 那一列 —— 三種既有形狀都要接得住:沒有這一格、`[]`、已經是
    一串 `- {…}`。寫成一行 flow map(與 `docs/MEMORY.md` 的範例同形),不引入
    YAML 函式庫(零依賴)。

    `discussion:` 那一欄是 D-007 要的:上限提高的理由寫在這裡,而**支撐那個理由的
    討論**在那個路徑上 —— 下一個想再提高的人翻得到上一次是怎麼談出來的。
    """
    line = ("  - {date: %s, from: %s, to: %s, by: %s, discussion: %s, reason: \"%s\"}"
            % (entry["date"], entry["from"], entry["to"], entry["by"],
               entry["discussion"], entry["reason"].replace('"', "'")))
    front = front or ""
    if re.search(r"^cap_history:\s*\[\s*\]\s*$", front, re.MULTILINE):
        return re.sub(r"^cap_history:\s*\[\s*\]\s*$", "cap_history:\n" + line,
                      front, count=1, flags=re.MULTILINE)
    if re.search(r"^cap_history:\s*$", front, re.MULTILINE):
        return re.sub(r"^cap_history:\s*$", "cap_history:\n" + line,
                      front, count=1, flags=re.MULTILINE)
    return front.rstrip("\n") + "\ncap_history:\n" + line + "\n"


def set_cap(front, value):
    if front and CAP_RE.search(front):
        return CAP_RE.sub("cap_chars: %d" % value, front, count=1)
    return "cap_chars: %d\n" % value + (front or "")


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
    new_cap = None
    reason = ""
    by = ""
    candidate = ""
    discussions = []
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--new-cap", "--reason", "--by", "--discussion", "--candidate") \
                and index + 1 < len(argv):
            if flag == "--new-cap":
                try:
                    new_cap = int(argv[index + 1])
                except ValueError:
                    return refuse("--new-cap 要一個數字")
            elif flag == "--reason":
                reason = argv[index + 1]
            elif flag == "--discussion":
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
    if new_cap is not None and not reason.strip():
        # 提高是掙來的。沒有理由的提高,`check` 視為未整理 —— 所以這裡直接擋,
        # 不要讓一個「看起來整理過了」的檔案存在。
        return refuse("--new-cap 要 --reason —— 提高上限是掙來的,"
                      "理由要寫得出「多讀的那幾百字替每個未來 session 省了什麼」(D-006)")
    root = ticket.root()
    talks = []
    for name in discussions:
        talk, problem = read_discussion(root, name)
        if problem:
            return refuse(problem)
        talks.append(talk)
    if len(talks) != 2:
        return refuse("consolidate 要**兩份** --discussion(兩個不同的 model 各一份,D-013);"
                      "這次給了 %d 份" % len(talks))
    if not candidate.strip():
        return refuse("consolidate 要 --candidate <檔> —— 整理後的新版正文;"
                      "主檔只會被換成驗過的候選,不會被就地改")
    models = [talk["model"] for talk in talks]
    if models[0] == models[1]:
        return refuse("兩份討論檔的 model 都是 %s —— 一個模型自己整理不算數(D-013)"
                      % models[0])
    if (talks[0]["source_lines"], talks[0]["source_sha256"]) \
            != (talks[1]["source_lines"], talks[1]["source_sha256"]):
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
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    default_cap = int(conf.get("cap_chars") or DEFAULT_CAP)
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
        old_cap, _ = cap_of(front, default_cap)
        cap = old_cap if new_cap is None else new_cap
        if len(candidate_body) > cap:
            return refuse("候選 %s 正文 %d 字元,超過上限 %d —— 還沒整理完;壓下來,或者"
                          "--new-cap 配 --reason" % (candidate, len(candidate_body), cap))
        # 在鎖裡重算:討論是對那一刻的 inbox 做的,對不上就是有人動過前 K 行,
        # 或者這一份快照已經被上一輪消耗了。
        total, head, digest = snapshot_of(read_bytes(inbox), count)
        if total < count or digest != expected:
            return refuse("inbox 的前 %d 行對不上討論檔的 source_sha256(現在 %d 行,sha256 %s)"
                          " —— 討論的不是這一份快照,重跑 `memory.py snapshot %s`"
                          % (count, total, digest, rel))
        if new_cap is not None:
            front = set_cap(front, new_cap)
            front = add_cap_history(front, {
                "date": date.today().isoformat(), "from": old_cap, "to": new_cap,
                "by": who, "discussion": " + ".join(discussions), "reason": reason.strip()})
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
               cap_from=old_cap, cap_to=cap, by=who, models=models,
               discussions=discussions, candidate=candidate, note=reason.strip())
    sys.stdout.write("memory: %s %d -> %d 字元、消耗 inbox 前 %d 行、上限 %d -> %d、討論 %s\n"
                     % (rel, before, after, count, old_cap, cap, " + ".join(discussions)))
    return 0


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    verbs = {"check": cmd_check, "check-stale": cmd_check_stale, "note": cmd_note,
             "harvest": cmd_harvest, "snapshot": cmd_snapshot,
             "consolidate": cmd_consolidate}
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
