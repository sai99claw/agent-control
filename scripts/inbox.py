#!/usr/bin/env python3
"""終態的收件匣:**完成的事去叫醒主線,主線不去輪詢** — `docs/WORKFLOW.md`(D-015)。

    scripts/inbox.py post --ticket 7 --run-id … --state "閘門紅" \
                          --what "讀 patch 記 review" --where "reports/t7/…/status.json"
    scripts/inbox.py list [--all]          # 還沒 ack 的(--all 連 ack 過的一起)
    scripts/inbox.py show 7                # 那一頁
    scripts/inbox.py ack 7-20260921-…      # 收下了
    scripts/inbox.py ack --all

## 為什麼要有這一支
2026-09-21 的外部審查:「最容易空轉的是『gate 紅了以後由主線人工接續』」。在它之前,
主線要知道一輪跑完了沒,只有兩條路 —— **輪詢**(每看一次背景工作 = 整份上下文重送
一輪),或**等人來講**(而沒有人會來講,因為跑完的是一支腳本)。

所以每一個**終態**(gate 跑完、auto-fix 停下來、land 跑完、票轉 Blocked)寫兩樣東西:
一則事件(控制台看得到)與**一頁**`reports/inbox/<票號>-<run_id>.md`。一頁只答四句:
**哪張票、什麼狀態、要主線做什麼、去哪看**。多寫一句,讀它的人就得把它整份讀完。

## 為什麼 `list` 讀索引而不是掃 markdown
掃 markdown 等於在解析自己寫的散文,而散文改一個字就會解析錯。索引(`index.jsonl`)
是只加不改的一行一筆;markdown 那一頁是給人讀的,索引是給 `list` 讀的。

## ack 是「我收下了」,不是「我做完了」
ack 只從清單上拿掉 —— 票的狀態由票說了算(`ticket.py`),不由收件匣說。兩者混在一起
的話,一次 ack 會讓一張還沒處理的票在畫面上消失。
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根與 board/config.json 的判讀

DEFAULT_REPORTS = "reports"
INBOX_REL = "inbox"
INDEX = "index.jsonl"
ACKED = "acked.jsonl"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def reports_dir(root):
    return os.path.join(root, event.config(root).get("reports_dir") or DEFAULT_REPORTS)


def inbox_dir(root):
    return os.path.join(reports_dir(root), INBOX_REL)


def index_path(root):
    return os.path.join(inbox_dir(root), INDEX)


def acked_path(root):
    return os.path.join(inbox_dir(root), ACKED)


def read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def append_jsonl(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, line)
    finally:
        os.close(handle)


def acked_names(root):
    return set(row.get("name") for row in read_jsonl(acked_path(root)))


def entries(root, include_acked=False):
    done = acked_names(root)
    rows = []
    for row in read_jsonl(index_path(root)):
        row["acked"] = row.get("name") in done
        if row["acked"] and not include_acked:
            continue
        rows.append(row)
    return rows


def subject_of(root, ident):
    path = os.path.join(event.tickets_dir(root), "%s.json" % ident)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    return str(data.get("subject") or "")


PAGE = """# #%(ticket)s —— %(state)s

| | |
|---|---|
| 票 | #%(ticket)s %(subject)s |
| 狀態 | %(state)s |
| 這一輪 | `%(run_id)s`(%(kind)s)|
| 時間 | %(at)s |

## 要主線做什麼
%(what)s

## 去哪看
%(where)s
%(note)s"""


def cmd_post(args):
    root = event.repo_root()
    where = inbox_dir(root)
    os.makedirs(where, exist_ok=True)
    # 同一輪可以有兩個終態(閘門綠了,接著 auto-fix 說「停在等覆核」)。同名會讓
    # 後面那一頁**蓋掉**前面那一頁,而索引上兩列還指著同一個檔 —— 讀的人看到兩列、
    # 打開是同一頁,分不出哪一列是真的。所以撞名就加序號。
    stem = "%s-%s" % (args.ticket, args.run_id or "no-run")
    name, serial = stem, 1
    while os.path.exists(os.path.join(where, "%s.md" % name)):
        serial += 1
        name = "%s-%d" % (stem, serial)
    page = PAGE % {
        "ticket": args.ticket,
        "subject": subject_of(root, args.ticket),
        "state": args.state,
        "run_id": args.run_id or "(沒有 run_id)",
        "kind": args.kind or "?",
        "at": now(),
        "what": args.what or "(沒有寫要做什麼 —— 那等於沒有收件匣)",
        "where": args.where or "(沒有寫去哪看)",
        "note": ("\n## 補充\n%s\n" % args.note) if args.note else "",
    }
    path = os.path.join(where, "%s.md" % name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)
    append_jsonl(index_path(root), {
        "name": name, "ticket": args.ticket, "run_id": args.run_id or "",
        "kind": args.kind or "", "state": args.state, "what": args.what or "",
        "where": args.where or "", "at": now(),
        "page": os.path.relpath(path, root)})
    try:
        # `kind=` 這個名字是 `event.emit` 的第一個位置參數 —— 拿它當欄位名會丟
        # TypeError,而 except 會把它吞成「事件發不出去」。所以這一格叫 `run_kind`。
        event.emit("inbox.posted", ticket=args.ticket, run_id=args.run_id or "",
                   run_kind=args.kind or "", state=args.state,
                   page=os.path.relpath(path, root))
    except Exception:                                          # noqa: BLE001
        # 事件發不出去要出聲,但不擋 —— 那一頁已經寫好了,而把紀錄問題升級成
        # 交付問題會讓終態根本沒人收到。
        sys.stderr.write("inbox: 事件發不出去(那一頁還是寫好了)\n")
    sys.stdout.write("inbox: %s\n" % os.path.relpath(path, root))
    return 0


def cmd_list(args):
    root = event.repo_root()
    rows = entries(root, include_acked=args.all)
    if not rows:
        sys.stdout.write("inbox: 沒有等你的東西%s\n"
                         % ("" if args.all else "(ack 過的用 --all 看)"))
        return 0
    sys.stdout.write("inbox: %d 則%s\n" % (len(rows), "" if args.all else "(還沒 ack)"))
    for row in rows:
        mark = "✓" if row.get("acked") else "•"
        sys.stdout.write("  %s #%-4s %-22s %s\n"
                         % (mark, row.get("ticket", "?"), row.get("state", "?"),
                            row.get("what", "")[:60]))
        sys.stdout.write("      %s\n" % row.get("page", ""))
    sys.stdout.write("inbox: 讀一頁 `python3 scripts/inbox.py show <票號>`;"
                     "收下 `python3 scripts/inbox.py ack <票號>`\n")
    return 0


def pick(root, needle, include_acked=True):
    rows = entries(root, include_acked=include_acked)
    exact = [row for row in rows if row.get("name") == needle]
    if exact:
        return exact
    return [row for row in rows if str(row.get("ticket")) == str(needle).lstrip("#")]


def cmd_show(args):
    root = event.repo_root()
    rows = pick(root, args.which)
    if not rows:
        sys.stderr.write("inbox: 沒有 %r 這一則(`inbox.py list --all` 看全部)\n"
                         % args.which)
        return 2
    row = rows[-1]
    try:
        with open(os.path.join(root, row.get("page", "")), encoding="utf-8") as handle:
            sys.stdout.write(handle.read())
    except OSError as exc:
        sys.stderr.write("inbox: 索引上有這一則,那一頁卻讀不到 —— %s\n" % exc)
        return 2
    return 0


def cmd_ack(args):
    root = event.repo_root()
    if args.all:
        rows = entries(root, include_acked=False)
    else:
        if not args.which:
            sys.stderr.write("inbox: ack <票號|名稱> 或 ack --all\n")
            return 2
        rows = [row for row in pick(root, args.which, include_acked=False)]
    if not rows:
        sys.stdout.write("inbox: 沒有還沒 ack 的%s\n"
                         % ("" if args.all else "(%s)" % args.which))
        return 0
    for row in rows:
        append_jsonl(acked_path(root), {"name": row["name"], "at": now(),
                                        "ticket": row.get("ticket", "")})
        sys.stdout.write("inbox: ack %s(#%s %s)\n"
                         % (row["name"], row.get("ticket", "?"), row.get("state", "")))
    sys.stdout.write("inbox: ack 只是「我收下了」—— 票的狀態還是由 ticket.py 說了算\n")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="inbox.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    post = subs.add_parser("post")
    post.add_argument("--ticket", required=True)
    post.add_argument("--run-id", default="")
    post.add_argument("--kind", default="")
    post.add_argument("--state", required=True)
    post.add_argument("--what", default="")
    post.add_argument("--where", default="")
    post.add_argument("--note", default="")
    post.set_defaults(run=cmd_post)

    listing = subs.add_parser("list")
    listing.add_argument("--all", action="store_true")
    listing.set_defaults(run=cmd_list)

    show = subs.add_parser("show")
    show.add_argument("which")
    show.set_defaults(run=cmd_show)

    ack = subs.add_parser("ack")
    ack.add_argument("which", nargs="?", default="")
    ack.add_argument("--all", action="store_true")
    ack.set_defaults(run=cmd_ack)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
