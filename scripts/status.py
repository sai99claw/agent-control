#!/usr/bin/env python3
"""閘門與落地的狀態檔 — `docs/WORKFLOW.md` §狀態檔(D-010)。

    scripts/status.py start  --ticket 7 --kind gate --sha abc1234
    scripts/status.py failures --log gate.log        # 印可以單獨重跑的案例 id,一行一個
    scripts/status.py done   --ticket 7 --kind gate --sha abc1234 --rc 1 \
                             --log gate.log [--flaky test_x.Case.test_y …]
    scripts/status.py show   --ticket 7

寫的是 `<reports_dir>/t<票號>-status.json`(`board/config.json` 的 `reports_dir`,
預設 `reports/`)。

## 這一份要回答的三個問題
**「跑完了沒、錯了什麼、去哪看」** —— 這三句是使用者 2026-09-20 的原話。在它之前,
一個 agent 要知道閘門怎麼了,只能把整份 log 讀進上下文(一次幾十萬 token),或者
用 `Monitor` / `sleep` 迴圈輪詢背景工作(每看一次 = 整份上下文重送一輪)。
**一份 20 行的 JSON 取代那兩種做法。**

## 為什麼 `state` 與 `rc` 是兩格
`{"state": "running"}` 與「跑完了但 rc 還沒寫」長得一樣,而那正是要分開的兩件事:
**還在跑**要等,**跑完了而且紅了**要修。所以 `start` 先寫 `running`(那一刻還沒有
rc),`done` 才覆寫成 `done` 並帶 `rc`。一份沒有 `finished` 的 `done` 是壞掉的檔,
不是「剛好跑很快」。

## 為什麼 failures 要帶 excerpt 而不是只給 log 路徑
只給路徑的話,下一個人還是得把整份 log 讀進來 —— 那就回到原本的成本。excerpt 上限
20 行:夠認出是哪一條斷言倒了,不夠讓人偷懶把整份貼進去。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根與 board/config.json 的判讀

EXCERPT_LINES = 20
DEFAULT_REPORTS = "reports"

# `FAIL: test_x (test_mod.Case.test_x)` / `ERROR: test_x (test_mod.Case)` /
# subTest 的 `FAIL: test_x (test_mod.Case.test_x) [engine=firefox]` —— **方括號那一段
# 一定要認**,不然瀏覽器那一族的紅一條都進不了紅榜,而紅榜是空的與沒有紅長得一樣。
HEAD = re.compile(r"^(FAIL|ERROR):\s+(\S+)"
                  r"(?:\s+\((.*?)\))?"
                  r"(?:\s+\[(.*?)\])?\s*$")
DIVIDER = re.compile(r"^(=|-){20,}\s*$")
FILE_LINE = re.compile(r'^\s*File "([^"]+)", line (\d+)')
# 瀏覽器那一族會在案例名或輸出裡帶 `engine=chrome`;沒有就留空,不猜。
ENGINE = re.compile(r"engine[= ]([A-Za-z0-9_-]+)")


def reports_dir(root):
    return os.path.join(root, event.config(root).get("reports_dir") or DEFAULT_REPORTS)


def path_for(root, ticket):
    return os.path.join(reports_dir(root), "t%s-status.json" % ticket)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dotted(case, qualifier):
    """單獨重跑要用的 id。unittest 的括號裡是完整路徑(3.11 起連方法名都在裡面),
    括號外只有方法名 —— **用括號裡那一份**,它才餵得回 `python3 -m unittest`。"""
    if not qualifier:
        return case
    qualifier = qualifier.strip()
    if qualifier.endswith("." + case) or qualifier == case:
        return qualifier
    return "%s.%s" % (qualifier, case)


def parse_failures(log_path):
    """從一份 unittest 輸出裡挑出紅的案例。

    只認 `^(FAIL|ERROR):` 開頭那幾行 —— 不去猜「看起來像錯誤」的行。猜出來的那幾筆
    會讓 `failures` 變成一份沒有人敢信的清單,而**一份不能信的紅榜與沒有紅榜一樣**。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    index = 0
    while index < len(lines):
        found = HEAD.match(lines[index])
        if not found:
            index += 1
            continue
        kind, case, qualifier = found.group(1), found.group(2), found.group(3)
        label = found.group(4) or ""
        body = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if HEAD.match(line) or line.startswith("Ran ") or line.startswith("OK") \
                    or line.startswith("FAILED"):
                break
            if DIVIDER.match(line):
                # 第一條分隔線是標頭與 traceback 之間的那一條;第二條是這一筆的結尾。
                if body:
                    break
                index += 1
                continue
            body.append(line)
            index += 1
        while body and not body[-1].strip():
            body.pop()
        where, line_no = "", 0
        for line in body:
            spot = FILE_LINE.match(line)
            if spot:
                where, line_no = spot.group(1), int(spot.group(2))
        engine = ""
        for line in [label, case, qualifier or ""] + body:
            hit = ENGINE.search(line)
            if hit:
                engine = hit.group(1)
                break
        out.append({
            "case": dotted(case, qualifier),
            "kind": kind,
            # subTest 的方括號標籤。單獨重跑餵回去的是 `case`(subTest 沒辦法單獨
            # 叫),所以標籤另外留一格,讓人看得出紅的是哪一個子情境。
            "subtest": label,
            "file": where,
            "line": line_no,
            "engine": engine,
            "log": log_path,
            "excerpt": "\n".join(body[:EXCERPT_LINES]),
        })
    return out


def write(root, ticket, data):
    path = path_for(root, ticket)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def read(root, ticket):
    try:
        with open(path_for(root, ticket), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def cmd_start(args):
    root = event.repo_root()
    data = {"state": "running", "kind": args.kind, "ticket": args.ticket,
            "sha": args.sha or "", "started": now(), "finished": None,
            "rc": None, "report": args.report or "", "logs": [],
            "failures": [], "flaky": []}
    print("status: %s" % os.path.relpath(write(root, args.ticket, data), root))
    return 0


def cmd_done(args):
    root = event.repo_root()
    before = read(root, args.ticket)
    failures = []
    for log in args.log or []:
        failures.extend(parse_failures(log))
    flaky_names = set(args.flaky or [])
    flaky = [row for row in failures if row["case"] in flaky_names]
    failures = [row for row in failures if row["case"] not in flaky_names]
    data = {
        "state": "done",
        "kind": args.kind or before.get("kind") or "",
        "ticket": args.ticket,
        "sha": args.sha or before.get("sha") or "",
        "started": before.get("started") or now(),
        "finished": now(),
        "rc": args.rc,
        "report": args.report or before.get("report") or "",
        "logs": list(args.log or []),
        "failures": failures,
        "flaky": flaky,
    }
    path = write(root, args.ticket, data)
    print("status: %s rc=%d 紅 %d 條,flaky %d 條"
          % (os.path.relpath(path, root), args.rc, len(failures), len(flaky)))
    return 0


def cmd_failures(args):
    """印出可以單獨重跑的案例 id,一行一個。**呼叫它的是 shell 迴圈** —— 所以這裡
    只印 id,不印別的;多印一個字,那個迴圈就會拿它當案例名去跑。"""
    seen = []
    for log in args.log or []:
        for row in parse_failures(log):
            if row["case"] not in seen:
                seen.append(row["case"])
    for case in seen:
        print(case)
    return 0


def cmd_show(args):
    root = event.repo_root()
    data = read(root, args.ticket)
    if not data:
        sys.stderr.write("status: #%s 還沒有狀態檔(%s)—— 這一輪閘門根本沒開跑?\n"
                         % (args.ticket, os.path.relpath(path_for(root, args.ticket), root)))
        return 2
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="status.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    start = subs.add_parser("start")
    start.add_argument("--ticket", required=True)
    start.add_argument("--kind", default="gate")
    start.add_argument("--sha", default="")
    start.add_argument("--report", default="")
    start.set_defaults(run=cmd_start)

    done = subs.add_parser("done")
    done.add_argument("--ticket", required=True)
    done.add_argument("--kind", default="")
    done.add_argument("--sha", default="")
    done.add_argument("--rc", type=int, required=True)
    done.add_argument("--report", default="")
    done.add_argument("--log", action="append", default=[])
    done.add_argument("--flaky", action="append", default=[])
    done.set_defaults(run=cmd_done)

    fails = subs.add_parser("failures")
    fails.add_argument("--log", action="append", default=[])
    fails.set_defaults(run=cmd_failures)

    show = subs.add_parser("show")
    show.add_argument("--ticket", required=True)
    show.set_defaults(run=cmd_show)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
