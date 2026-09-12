#!/usr/bin/env python3
"""記憶的容量與整理 — D-006(`docs/MEMORY.md`「容量與整理:老師帶學生」)。

    scripts/memory.py check                    # session 開頭跑;超過上限 → 退出碼 1
    scripts/memory.py consolidate memory/model/opus.md \
        --new-cap 2600 --reason "四條 land 事故的反例各不相同,合併會失去可辨識性"

## 為什麼量字元
**字元是唯一不需要 tokenizer 的決定性量法。** 用 token 量會讓「這份檔多大」取決於
哪一個模型在問,而同一份檔在兩個 session 得到兩個答案的上限,不是上限。

## 為什麼只管模型私有記憶
上限管的是**每個新 session 的第一口空氣**:`memory/model/<model>.md` 是同模型每個
session 開頭都要讀的東西,多一個字就是每一個 session 都多讀一個字。專案共用層
(裁示、交接)有各自的形狀,不受這條管 —— 這是 D-006 的原話更正過的範圍。

## 為什麼提高上限要有理由
上限可以被討論結果打破,但**提高是掙來的**。一個沒有理由的 `cap_chars: 4000` 與
一場真的做過的討論長得一模一樣,而前者是把「還沒整理」重新命名成「上限比較高」。
所以 `--new-cap` 沒有 `--reason` 直接拒絕,理由連同前後值寫進檔案的 front matter
`cap_history` —— 寫在**那份檔自己身上**,下一個要再提高的人看得到上一次的理由。
"""

import glob as globmod
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event      # noqa: E402
import ticket     # noqa: E402

DEFAULT_CAP = 2000
DEFAULT_APPLIES = ("memory/model/*.md",)
DEFAULT_INBOX_SUFFIX = ".inbox.md"
CONSOLIDATOR_ROLE = "consolidator"
FENCE = "---"
CAP_RE = re.compile(r"^cap_chars:\s*(\d+)\s*$", re.MULTILINE)


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


def watched_files():
    """`memory.applies_to` 展開。`.inbox.md` 本身不受上限管(整理期間的暫存區)。"""
    conf = memory_config()
    root = ticket.root()
    patterns = conf.get("applies_to") or list(DEFAULT_APPLIES)
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    out = []
    for pattern in patterns:
        for path in sorted(globmod.glob(os.path.join(root, pattern))):
            rel = os.path.relpath(path, root)
            if rel.endswith(suffix):
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


def open_ticket_for(rel, chars, cap, out):
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    model = conf.get("consolidator") or ""
    inbox = inbox_path(rel, suffix)
    argv = [
        "--subject", "整理 %s(%d 字元,上限 %d)" % (rel, chars, cap),
        "--objective",
        "跟該模型討論後壓縮 %s,或者寫出理由把上限提高(D-006「老師帶學生」);"
        "併入 %s" % (rel, inbox),
        "--acceptance", "`scripts/memory.py check` 對 %s 退出碼 0" % rel,
        "--acceptance", "保留的每一條仍帶日期 / 來源票號 / 實測或推論三個標記",
        "--acceptance", "提高上限的話,`cap_history` 有一列寫得出多讀的那幾百字省了什麼",
        "--in-scope", rel,
        "--in-scope", inbox,
        "--out-of-scope", "docs/DECISIONS.md",
        "--allowed-write-path", rel,
        "--allowed-write-path", inbox,
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
    # 退出碼 1 是給 session 開頭看的:`new-session.sh` 跑這一支,紅了那個 session
    # 就知道自己的記憶該整理了 —— **但不停工**(docs/MEMORY.md 第 1 點)。
    return 1 if over else 0


def add_cap_history(front, entry):
    """`cap_history` 那一列 —— 三種既有形狀都要接得住:沒有這一格、`[]`、已經是
    一串 `- {…}`。寫成一行 flow map(與 `docs/MEMORY.md` 的範例同形),不引入
    YAML 函式庫(零依賴)。"""
    line = ("  - {date: %s, from: %s, to: %s, by: %s, reason: \"%s\"}"
            % (entry["date"], entry["from"], entry["to"], entry["by"],
               entry["reason"].replace('"', "'")))
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
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--new-cap", "--reason", "--by") and index + 1 < len(argv):
            if flag == "--new-cap":
                try:
                    new_cap = int(argv[index + 1])
                except ValueError:
                    sys.stderr.write("memory: --new-cap 要一個數字\n")
                    return 2
            elif flag == "--reason":
                reason = argv[index + 1]
            else:
                by = argv[index + 1]
            index += 2
            continue
        sys.stderr.write("memory: consolidate 不認得 %r\n" % flag)
        return 2
    if new_cap is not None and not reason.strip():
        # 提高是掙來的。沒有理由的提高,`check` 視為未整理 —— 所以這裡直接擋,
        # 不要讓一個「看起來整理過了」的檔案存在。
        sys.stderr.write("memory: --new-cap 要 --reason —— 提高上限是掙來的,"
                         "理由要寫得出「多讀的那幾百字替每個未來 session 省了什麼」"
                         "(D-006)\n")
        return 2
    root = ticket.root()
    path = os.path.join(root, rel) if not os.path.isabs(rel) else rel
    rel = os.path.relpath(path, root)
    try:
        text = read(path)
    except OSError as exc:
        sys.stderr.write("memory: 讀不到 %s —— %s\n" % (rel, exc))
        return 2
    conf = memory_config()
    suffix = conf.get("inbox_suffix") or DEFAULT_INBOX_SUFFIX
    default_cap = int(conf.get("cap_chars") or DEFAULT_CAP)
    front, body = split_front_matter(text)
    before = len(body)
    old_cap, _ = cap_of(front, default_cap)

    inbox = inbox_path(path, suffix)
    merged = 0
    if os.path.exists(inbox):
        extra = read(inbox)
        _, extra_body = split_front_matter(extra)
        extra_body = extra_body.strip()
        if extra_body:
            body = body.rstrip("\n") + "\n" + extra_body + "\n"
            merged = len(extra_body)
        os.remove(inbox)

    cap = old_cap
    if new_cap is not None:
        cap = new_cap
        front = set_cap(front, new_cap)
        front = add_cap_history(front, {
            "date": date.today().isoformat(), "from": old_cap, "to": new_cap,
            "by": by or conf.get("consolidator") or "?", "reason": reason.strip()})
    head = FENCE + "\n" + front + FENCE + "\n" if front else ""
    write(path, head + body)
    after = len(body)
    event.emit("memory.consolidated", file=rel, before=before, after=after,
               merged=merged, cap_from=old_cap, cap_to=cap,
               by=by or conf.get("consolidator") or "", note=reason.strip())
    sys.stdout.write("memory: %s %d -> %d 字元(併入 %d)、上限 %d -> %d\n"
                     % (rel, before, after, merged, old_cap, cap))
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
    if verb == "check":
        return cmd_check(rest)
    if verb == "consolidate":
        return cmd_consolidate(rest)
    sys.stderr.write("memory: 不認得 %r(check / consolidate)\n" % verb)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
