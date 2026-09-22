#!/usr/bin/env python3
"""每票一行的數字 — `docs/DESIGN.md` §15、D-017。

    python3 scripts/metrics.py line 7          # 一張票一行
    python3 scripts/metrics.py all             # 每張票一行,票號數值由小到大
    python3 scripts/metrics.py all --json      # 同一份數字,給看板吃

一行長這樣(鍵是字面,值才會變):

    ticket=7 runs: gate_runs=3 land_runs=2 apply_runs=3 fix_rounds=2 red_runs=4
    env_runs=1 product_runs=3 wall_seconds=418 objections: objections_ticket_wrong=1
    objections_test_defect=0 tokens=已知1趟=12345 未知1趟 note=-

## 為什麼要有這一支
在它之前,「這張票返工了幾輪、紅在環境還是紅在程式、花了多久、燒了多少 token」
四個問題**都只能靠抄某一份輸出**回答 —— 而抄出來的數字沒有人可以覆核。這一支不新增
任何一種紀錄:它只讀 `status.py` 已經寫在磁碟上的 `status.json`、票自己的
`objections[]`,以及 `auto-fix.sh` 已經落下的 `worker-round<r>.log`。

## 兩種單位不准相加
`runs:` 那一段數的是**跑了幾趟**,`objections:` 那一段數的是**票上有幾筆反駁**。
兩個單位不同,加起來沒有意義,所以行裡有兩個分段標示把它們隔開 —— 一串沒有分段的
`a=1 b=2 c=3` 會被下一個人 `awk` 起來加總,而那個總和看起來完全正常。

## 「沒有在看」與「看了、沒有」是兩件事
`runs` 那一類的鍵在沒有 reports 目錄時印 `0`:**看了,而一趟都沒有**。
`tokens=` 在同樣的情況印 `未知`:**根本沒有一份 log 可以看**。
兩者揉成同一個 `0` 的那一刻,「這張票沒燒 token」與「我沒去量」長得一樣
(`docs/DISPATCH-TEMPLATE.md` §5.5)。同樣地,目錄不在與目錄在但一份 `status.json`
都沒有,`note=` 那一格印的是**不同的字**。

## token 從哪裡來
`auto-fix.sh` 把每一趟 worker 的 stdout+stderr 導進
`<reports_dir>/t<票號>/<run_id>/worker-round<r>.log`(它的格式不是這一支能決定的,
所以這裡只認兩種形狀,認不出就說不知道):

1. 檔尾是一個 JSON 物件而且有 `usage` → `usage.input_tokens + usage.output_tokens`
   (`board/config.json` 的 `worker.command` 帶 `--output-format json` 時的形狀);
2. 有一行含 `tokens used <N>` → 取 N(最後一筆);
3. 都不是 → **那一趟是 unknown**,不估、不寫 0。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event   # noqa: E402  共用 repo 根與 board/config.json 的判讀
import status  # noqa: E402  run 目錄的形狀只有一份定義
import ticket  # noqa: E402

WORKER_LOG = re.compile(r"^worker-round(\d+)\.log$")
TOKENS_USED = re.compile(r"tokens used\s+(\d+)")
# 檔尾那個 JSON 最多往回找幾行就放棄。一份幾 MB 的 log 逐行回試是白燒時間,而
# 「找不到」與「沒去找」在這裡是同一個答案(unknown),所以放棄是安全的。
TAIL_LINES = 400
UNKNOWN = "未知"
TICKET_WRONG = "ticket-wrong"
TEST_DEFECT = "test_defect"
NO_DIR = "沒有 %s —— 這張票一輪都還沒跑過"
EMPTY_DIR = "%s 在,但裡面 0 個 status.json"
HAS_RUNS = "-"
KEYS = ("gate_runs", "land_runs", "apply_runs", "fix_rounds", "red_runs",
        "env_runs", "product_runs", "wall_seconds")


def tail_json(text):
    """檔尾的那一個 JSON 物件,沒有就是沒有。

    從最後一行往回找開頭的 `{`:多行 pretty-print 的內層 `{` 會因為尾巴多一個 `}`
    而解析失敗,於是迴圈自己走到最外層那一個。單行的 JSON 第一次就中。
    """
    tail = text.rstrip()
    if not tail.endswith("}"):
        return None
    lines = tail.splitlines()
    for start in range(len(lines) - 1, max(-1, len(lines) - 1 - TAIL_LINES), -1):
        if not lines[start].lstrip().startswith("{"):
            continue
        try:
            row = json.loads("\n".join(lines[start:]))
        except ValueError:
            continue
        if isinstance(row, dict):
            return row
    return None


def usage_tokens(row):
    """`usage` 那一格的兩個數。兩個都不在就不是一份可以拿來算的 usage。"""
    usage = row.get("usage")
    if not isinstance(usage, dict):
        return None
    total, seen = 0, False
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        total += value
        seen = True
    return total if seen else None


def tokens_in_log(path):
    """一趟 worker 燒了幾個 token,答不出來就回 None。

    **回 None 不是回 0**:0 是「這一趟一個 token 都沒用」,而那從來沒發生過 ——
    它真正的意思是「這份 log 沒有記」,兩件事在同一欄數字裡長得一樣(D-017 ①)。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    row = tail_json(text)
    if row is not None:
        total = usage_tokens(row)
        if total is not None:
            return total
    found = TOKENS_USED.findall(text)
    if found:
        return int(found[-1])
    return None


def worker_logs(where):
    """一個 run 目錄裡的 `worker-round<r>.log`,輪數由小到大。"""
    try:
        names = os.listdir(where)
    except OSError:
        return []
    rows = []
    for name in names:
        found = WORKER_LOG.match(name)
        if found:
            rows.append((int(found.group(1)), os.path.join(where, name)))
    return [path for _, path in sorted(rows)]


def is_red(data):
    """紅 = 跑完了而且 rc 非 0。

    `rc` 是 `None` 的那一份是**還在跑**(`status.py` 把 `state` 與 `rc` 分成兩格就
    是為了這件事)—— 把它算成紅,等於把「要等」講成「要修」。
    """
    rc = data.get("rc")
    return isinstance(rc, int) and rc != 0


def round_of(data):
    """第幾輪。頂層那一格是 #18 才加的,**舊的檔沒有它** —— 只問頂層的話,這個
    repo 磁碟上每一輪都會答 0,而「返工 0 輪」是一句假話,不是一格空白。
    """
    for value in (data.get("round"), (data.get("repair_context") or {}).get("round")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def duration_of(data):
    """這一輪幾秒。同上:舊的檔沒有 `duration_seconds`,但**兩個時間戳一直都在**,
    所以用 `status.py` 自己那支減法補算 —— 算不出來還是 None,不是 0。
    """
    seconds = data.get("duration_seconds")
    if isinstance(seconds, int) and not isinstance(seconds, bool):
        return seconds
    return status.seconds_between(data.get("started"), data.get("finished"))


def read_runs(root, ident):
    """這張票每一輪的 `status.json`,舊的在前。"""
    rows = []
    for run_id in status.runs_of(root, ident):
        data = status.read(root, ident, run_id)
        if isinstance(data, dict) and data:
            rows.append((run_id, data))
    return rows


def measure(root, ident, objections):
    """一張票的所有數字。**這一支不寫任何檔** —— 它只是把磁碟上已經有的東西數一遍。"""
    where = status.ticket_dir(root, ident)
    rel = os.path.relpath(where, root)
    runs = read_runs(root, ident)
    row = {"ticket": str(ident)}
    for key in KEYS:
        row[key] = 0
    known, unknown = [], 0
    for run_id, data in runs:
        kind = data.get("kind") or ""
        if kind in ("gate", "land", "apply"):
            row["%s_runs" % kind] += 1
        rounds = round_of(data)
        if rounds > row["fix_rounds"]:
            row["fix_rounds"] = rounds
        if is_red(data):
            row["red_runs"] += 1
            # 分類只問一句:那一輪有沒有留下環境可疑的證據。**非空 = 環境**,
            # 空的 = 程式 —— 所以 env 與 product 兩格相加必然等於 red。
            if data.get("environment_suspect"):
                row["env_runs"] += 1
            else:
                row["product_runs"] += 1
        seconds = duration_of(data)
        if seconds is not None:
            row["wall_seconds"] += seconds
        for log in worker_logs(status.run_dir(root, ident, run_id)):
            total = tokens_in_log(log)
            if total is None:
                unknown += 1
            else:
                known.append(total)
    row["objections_ticket_wrong"] = objections.get(TICKET_WRONG, 0)
    row["objections_test_defect"] = objections.get(TEST_DEFECT, 0)
    row["tokens_known_runs"] = len(known)
    row["tokens_known_total"] = sum(known)
    row["tokens_unknown_runs"] = unknown
    row["tokens"] = tokens_text(len(known), sum(known), unknown)
    if not os.path.isdir(where):
        row["note"] = NO_DIR % rel
    elif not runs:
        row["note"] = EMPTY_DIR % rel
    else:
        row["note"] = HAS_RUNS
    return row


def tokens_text(known_runs, known_total, unknown_runs):
    """`tokens=` 那一格的字。一趟都問不出來時整格是 `未知`,不是 `0`。"""
    if known_runs == 0:
        return UNKNOWN
    return "已知%d趟=%d %s%d趟" % (known_runs, known_total, UNKNOWN, unknown_runs)


def objection_counts(one):
    """票的 `objections[]` 依 `category` 計數。單位是**筆**,不是趟。"""
    counts = {}
    for item in one.get("objections") or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        counts[category] = counts.get(category, 0) + 1
    return counts


def line_of(row):
    runs = " ".join("%s=%s" % (key, row[key]) for key in KEYS)
    return ("ticket=%s runs: %s objections: objections_ticket_wrong=%s "
            "objections_test_defect=%s tokens=%s note=%s"
            % (row["ticket"], runs, row["objections_ticket_wrong"],
               row["objections_test_defect"], row["tokens"], row["note"]))


def of_ticket(root, ident):
    try:
        one = ticket.load(str(ident))
    except (OSError, ValueError):
        # 票檔不在也要答得出來:reports 與票是兩份東西,少了一份不該讓另一份閉嘴。
        one = {}
    return measure(root, str(ident), objection_counts(one))


def collect(root=None):
    """每張票一筆,鍵是票號。看板的 `/api/state` 吃的就是這一份。"""
    root = root or event.repo_root()
    return {str(one.get("id")): measure(root, str(one.get("id")),
                                        objection_counts(one))
            for one in ticket.load_all() if one.get("id")}


def cmd_line(args):
    print(line_of(of_ticket(event.repo_root(), args.ticket)))
    return 0


def cmd_all(args):
    rows = collect(event.repo_root())
    order = sorted(rows, key=lambda i: (0, int(i)) if i.isdigit() else (1, 0))
    if args.json:
        print(json.dumps({i: rows[i] for i in order}, ensure_ascii=False, indent=2))
        return 0
    for ident in order:
        print(line_of(rows[ident]))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="metrics.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    one = subs.add_parser("line")
    one.add_argument("ticket")
    one.set_defaults(run=cmd_line)

    every = subs.add_parser("all")
    every.add_argument("--json", action="store_true")
    every.set_defaults(run=cmd_all)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
