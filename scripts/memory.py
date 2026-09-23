#!/usr/bin/env python3
"""記憶的容量與整理 — D-006(`docs/MEMORY.md`「容量與整理:老師帶學生」)。

    scripts/memory.py check                    # session 開頭跑;超過上限 → 退出碼 1
    scripts/memory.py harvest EVIDENCE.md      # 收割「記憶」段的 note 指令
    scripts/memory.py consolidate memory/model/opus.md \
        --discussion discussions/2026-09-12-memory-opus.md \
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

## 為什麼提高上限要有理由
上限可以被討論結果打破,但**提高是掙來的**。一個沒有理由的 `cap_chars: 4000` 與
一場真的做過的討論長得一模一樣,而前者是把「還沒整理」重新命名成「上限比較高」。
所以 `--new-cap` 沒有 `--reason` 直接拒絕,理由連同前後值寫進檔案的 front matter
`cap_history` —— 寫在**那份檔自己身上**,下一個要再提高的人看得到上一次的理由。
"""

import errno
import glob as globmod
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


CONSOLIDATE_FLAGS = (
    ("--discussion", "討論檔路徑(D-007 必填;格式見 docs/DISCUSSION.md,要有 `## 結論`)"),
    ("--new-cap", "新的上限字元數;要配 --reason"),
    ("--reason", "為什麼值得提高 —— 寫得出「多讀的那幾百字替每個未來 session 省了什麼」"),
    ("--by", "誰帶的討論;不給就用 config 的 memory.consolidator"),
)

USAGE = {
    "check": "scripts/memory.py check                       # 超過上限退出碼 1(不停工)",
    "note": "scripts/memory.py note <model|role|project> <名> \"<一行>\" [--ticket N] [--by <role>@<model>]",
    "harvest": "scripts/memory.py harvest <EVIDENCE.md>",
    "consolidate": "scripts/memory.py consolidate <記憶檔> --discussion <path> [--new-cap N --reason …]",
}

EXAMPLE = {
    "check": "python3 scripts/memory.py check",
    "note": ('python3 scripts/memory.py note role implementer '
             '"變異後先確認替換真的生效" --ticket 17 --by worker@opus'),
    "consolidate": ('python3 scripts/memory.py consolidate memory/model/opus.md \\\n'
                    '  --discussion discussions/2026-09-12-memory-opus.md \\\n'
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
    if not name or os.path.basename(name) != name:
        sys.stderr.write("memory: 名稱只能是一個檔名\n")
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
    suffix = memory_config().get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    if layer == "project":
        path = os.path.join(root, "memory", layer, name + ".md")
    else:
        path = os.path.join(root, "memory", layer, name + suffix)
    meta = []
    if ticket_no:
        meta.append("#%s" % ticket_no.lstrip("#"))
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

    判準是那張票自己的 `allowed_write_paths` 有沒有這份記憶檔 —— 不是另記一格
    「開過了」。另記的那一格會跟事實分岔(票被取消了、被關了),而分岔的那天沒有
    人會知道。
    """
    for one in ticket.load_all():
        if not ticket.is_open(one):
            continue
        if one.get("role") != CONSOLIDATOR_ROLE:
            continue
        if rel in (one.get("allowed_write_paths") or []):
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


def open_ticket_for(rel, chars, cap, out):
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    who = consolidators(conf)
    model = ", ".join(who)
    inbox = inbox_path(rel, suffix)
    argv = [
        "--subject", "整理 %s(%d 字元,上限 %d)" % (rel, chars, cap),
        "--objective",
        "由 %s 照 docs/DISCUSSION.md 的格式討論後壓縮 %s,或者寫出理由把上限提高"
        "(D-006「老師帶學生」、D-007「沒有討論檔不准整理」、D-013「兩個模型、只留原則」);"
        "併入 %s" % (model or "兩個不同的模型", rel, inbox),
        "--acceptance", "`scripts/memory.py check` 對 %s 退出碼 0" % rel,
        "--acceptance", "**兩個不同的模型**參與討論(%s);討論檔列得出雙方的分歧"
                        % (model or "名單見 board/config.json 的 memory.consolidators"),
        "--acceptance", "整理後只留**具體的原則、行為準則、思考方式**;**不直接寫案例**,"
                        "案例用票號當來源引用(例:「快照層只畫說得出處的畫面(#585)」)",
        "--acceptance", "案例原文留在紀錄類文件(DECISIONS 歸檔 / HANDOFF 歷史),記憶檔只留指路",
        "--acceptance", "保留的每一條仍帶日期 / 來源票號 / 實測或推論三個標記",
        "--acceptance", "提高上限的話,`cap_history` 有一列寫得出多讀的那幾百字省了什麼",
        "--acceptance", "討論存成 discussions/<date>-memory-<model>.md(docs/DISCUSSION.md 的格式)",
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
        "先各自寫討論檔、再互讀寫分歧,最後一位跑 "
        "`python3 scripts/memory.py consolidate %s --discussion <討論檔>`。" % rel,
        "--role", CONSOLIDATOR_ROLE,
        "--model", model,
        "--tool", "claude-code",
        "--state", "Ready",
    ]
    return ticket.cmd_create(argv, stdout=out)


def cmd_check(argv):
    del argv
    conf = memory_config()
    default_cap = int(conf.get("cap_chars") or DEFAULT_CAP)
    over = 0
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
        event.emit("memory.over_cap", file=rel, chars=chars, cap=cap)
        already = open_consolidation(rel)
        if already:
            sys.stdout.write("memory:   已經有一張開著的整理票 #%s,沒有再開\n" % already)
            continue
        open_ticket_for(rel, chars, cap, sys.stdout)
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    max_lines = int(conf.get("inbox_max_lines") or DEFAULT_INBOX_MAX_LINES)
    root = ticket.root()
    for layer in ("model", "role"):
        pattern = os.path.join(root, "memory", layer, "*" + suffix)
        for path in sorted(globmod.glob(pattern)):
            with open(path, encoding="utf-8") as handle:
                lines = sum(1 for line in handle if line.strip())
            if lines <= max_lines:
                continue
            rel = os.path.relpath(path, root)
            main_rel = rel[:-len(suffix)] + ".md"
            over += 1
            sys.stdout.write("memory: %s **%d / %d 行超過**\n"
                             % (rel, lines, max_lines))
            event.emit("memory.over_cap", file=rel, lines=lines, cap=max_lines)
            if not open_consolidation(main_rel):
                open_ticket_for(main_rel, lines, max_lines, sys.stdout)
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


def cmd_consolidate(argv):
    if not argv:
        sys.stderr.write("memory: consolidate <檔> [--new-cap N --reason …] [--by 誰]\n")
        return 2
    rel = argv[0]
    new_cap = None
    reason = ""
    by = ""
    discussion = ""
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--new-cap", "--reason", "--by", "--discussion") \
                and index + 1 < len(argv):
            if flag == "--new-cap":
                try:
                    new_cap = int(argv[index + 1])
                except ValueError:
                    sys.stderr.write("memory: --new-cap 要一個數字\n")
                    return 2
            elif flag == "--reason":
                reason = argv[index + 1]
            elif flag == "--discussion":
                discussion = argv[index + 1]
            else:
                by = argv[index + 1]
            index += 2
            continue
        return unknown_flag(flag)
    if not discussion.strip():
        sys.stderr.write("memory: consolidate 要 --discussion <path> —— 整理是老師帶"
                         "學生,而「討論過了」與「沒討論就刪了」在結果檔案上長得一樣"
                         "(D-007,格式見 docs/DISCUSSION.md)\n")
        return 2
    if new_cap is not None and not reason.strip():
        # 提高是掙來的。沒有理由的提高,`check` 視為未整理 —— 所以這裡直接擋,
        # 不要讓一個「看起來整理過了」的檔案存在。
        sys.stderr.write("memory: --new-cap 要 --reason —— 提高上限是掙來的,"
                         "理由要寫得出「多讀的那幾百字替每個未來 session 省了什麼」"
                         "(D-006)\n")
        return 2
    root = ticket.root()
    # 討論檔要**真的在那裡**,而且要**真的有結論**。兩件事兩句話:一句要人去開一份,
    # 一句要人回去把結論寫完(§5.7:守衛給錯下一步比沒有守衛更糟)。
    talk = discussion if os.path.isabs(discussion) \
        else os.path.join(ticket.root(), discussion)
    if not os.path.exists(talk):
        sys.stderr.write("memory: 找不到討論檔 %s —— 先照 templates/discussion.md "
                         "開一份(D-007)\n" % discussion)
        return 2
    with open(talk, encoding="utf-8") as handle:
        talk_text = handle.read()
    if "## 結論" not in talk_text:
        sys.stderr.write("memory: %s 還沒有結論區 —— 一份沒有結論的討論檔,與一場沒"
                         "談完的討論長得一樣。把 `## 結論` 那幾格填完再來(D-007)\n"
                         % discussion)
        return 2
    path = os.path.join(root, rel) if not os.path.isabs(rel) else rel
    rel = os.path.relpath(path, root)
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    default_cap = int(conf.get("cap_chars") or DEFAULT_CAP)
    with MemoryLock(root):
        try:
            text = read(path)
        except OSError as exc:
            sys.stderr.write("memory: 讀不到 %s —— %s\n" % (rel, exc))
            return 2
        front, body = split_front_matter(text)
        before = len(body)
        old_cap, _ = cap_of(front, default_cap)
        inbox = inbox_path(path, suffix)
        consumed = ""
        merged = 0
        if os.path.exists(inbox):
            run_id = os.environ.get("AC_RUN_ID") or "%s-%d" \
                % (datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())
            consumed = "%s.%s.consumed" % (inbox, run_id)
            os.rename(inbox, consumed)
            extra = read(consumed)
            _, extra_body = split_front_matter(extra)
            extra_body = extra_body.strip()
            if extra_body:
                body = body.rstrip("\n") + "\n" + extra_body + "\n"
                merged = len(extra_body)
        cap = old_cap
        if new_cap is not None:
            cap = new_cap
            front = set_cap(front, new_cap)
            front = add_cap_history(front, {
                "date": date.today().isoformat(), "from": old_cap, "to": new_cap,
                "by": by or conf.get("consolidator") or "?",
                "discussion": discussion, "reason": reason.strip()})
        head = FENCE + "\n" + front + FENCE + "\n" if front else ""
        write(path, head + body)
    after = len(body)
    event.emit("memory.consolidated", file=rel, before=before, after=after,
               merged=merged, cap_from=old_cap, cap_to=cap,
               by=by or conf.get("consolidator") or "",
               discussion=discussion, note=reason.strip())
    sys.stdout.write("memory: %s %d -> %d 字元(併入 %d)、上限 %d -> %d、討論 %s\n"
                     % (rel, before, after, merged, old_cap, cap, discussion))
    if after > cap:
        sys.stdout.write("memory: 還是超過上限 —— 沒有整理完\n")
        return 1
    return 0


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb in ("--help", "-h", "help"):
        sys.stdout.write(__doc__)
        return 0
    if verb in ("check", "note", "harvest", "consolidate") and ("--help" in rest or "-h" in rest):
        return help_for(verb)
    if verb in ("--help", "-h", "help") and rest and rest[0] in (
            "check", "note", "harvest", "consolidate"):
        return help_for(rest[0])
    if verb == "check":
        return cmd_check(rest)
    if verb == "note":
        return cmd_note(rest)
    if verb == "harvest":
        return cmd_harvest(rest)
    if verb == "consolidate":
        return cmd_consolidate(rest)
    sys.stderr.write("memory: 不認得 %r(check / note / harvest / consolidate)\n" % verb)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
