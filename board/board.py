#!/usr/bin/env python3
"""控制台。只讀票與事件,不猜 — D-003。

    python3 board/board.py            # 127.0.0.1:<board/config.json 的 port>
    python3 board/board.py --port 0   # OS 給一個埠(測試用)

七個畫面,一頁裡七段:
 ① VISION 四個指標(讀票 JSON)+ 票與依賴 —— 狀態、凍結原因、`base_sha` 是不是還是主線的祖先
 ② agent 時間線 —— 從事件重建:誰、什麼票、第幾次、開始/結束、現在還活著嗎
 ③ 落地佇列與最近的 land 結果
 ④ 決策收件匣(可以填答案)+ 終態收件匣 —— 讀 `<reports_dir>/inbox/` 目錄(`scripts/inbox.py`)
 ⑥ 每票最後一輪 —— 讀 `<reports_dir>/t<票號>/<run_id>/status.json`
 ⑦ 反駁 —— 讀票自己的 `objections[]`

外加一頁 `/t/<票號>`:那張票的全部 run、反駁、覆核、驗證、收件匣。

## 這一段不轉寫任何東西
閘門、落地、auto-fix、收件匣已經把結構化的檔寫在磁碟上了(`status.py`、`inbox.py`),
票自己也早就有 `objections` / `review` / `verify` 三格。所以這裡只加**讀**:
一行都不必有人抄進看板 —— 而抄進來的那一份沒有人可以覆核(D-017 ②)。

## 四個指標只讀票 JSON(D-054 Q5)
VISION「目的與成功指標」的四個數字:每張落地票的 token 與時間(`cost_total` / `cost[]`)、
一次就過測試的比例(Done 票 worker 最大 round = 1)、每張票需要主線介入幾次
(`interventions`)、等主線處理的事(Blocked / NeedsDecision 且 owner=main 的票 + 收件匣
未 ack 的頁)。**拿不到就顯示「未知」,不顯示 0**:舊票沒有那一格、cost 列全是 null,
都是「未知」;一個補出來的 0 與一個量出來的 0 在畫面上長得一樣。

舊的 metrics 欄(從 worker log 解析返工輪與 token)已刪:同一件事不再存第二份。
抬頭那一格「token 用量:未知」問的是「看板自己知不知道整體用量」(不估),不是四個指標。

## 它只寫一種東西
使用者在收件匣按下的那顆鈕,append 一行進 `answers.jsonl`,並發一筆事件。
**不改票的狀態、不派工** —— 一顆會順手改狀態的儲存鈕,是一顆不敢按的儲存鈕。
"""

import hmac
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import event    # noqa: E402
import inbox    # noqa: E402  收件匣的目錄怎麼讀只有一份定義
import status   # noqa: E402  `environment_suspect` 的正規化只有一份定義
import ticket   # noqa: E402

DEFAULT_PORT = 18905
ANSWER_MAX = 64 * 1024
LAND_KINDS = ("land.start", "land.refused", "land.pass", "land.fail")
QUEUE_STATES = ("InReview", "IntegrationQueued", "Integrating")
ATTEMPT_START = "ticket.attempt.start"
ATTEMPT_END = ("ticket.attempt.done", "ticket.attempt.failed")
SESSION_START = "session.start"
SESSION_END = "session.end"
UNKNOWN = "未知"

DEFAULT_REPORTS = "reports"
STATUS_NAME = "status.json"
ROUND_FILE = re.compile(r"^result-round(\d+)\.json$")
# 票號拿來接目錄名,所以它走白名單而不是黑名單:`/` 與 `..` 當然不在裡面,而**沒有
# 被想到的第三種寫法**也不在 —— 一張黑名單只擋得住已經被想到的那幾種。
TICKET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FAILURES_SHOWN = 5
BODY_SHOWN = 200
# 三種「沒有」,三句不同的話。揉成同一句的那一刻,「還沒跑」「跑了沒結果」「跑完
# 是綠的」在畫面上長得一樣,而那三件事的下一步完全不同(§5.5)。
NO_RUN_DIR = "沒有跑過"
EMPTY_RUN_DIR = "有目錄、沒有結果"
GREEN_RUN = "綠"
NO_ROUND_JSON = "worker 沒交結構化輸出"
UNREADABLE = "%s 讀不動"
NOT_FOUND = "找不到\n"


def esc(text):
    return html.escape("" if text is None else str(text), quote=True)


def root():
    return event.repo_root()


def now_text():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------- git 那幾問


def git(args):
    try:
        return subprocess.run(["git", "-C", root(), *args], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def ancestor_cache():
    """`base_sha` 還是主線的祖先嗎。

    三種答案要分開:**是 / 不是 / 問不出來**(票上沒有 base_sha、或那個 sha 這顆
    repo 根本沒有)。揉成一個布林值的話,「問不出來」會變成「不是」或「是」,
    而兩種都是假話。
    """
    main = ticket.main_branch()
    cache = {}

    def ask(sha):
        if not sha:
            return None
        if sha not in cache:
            done = git(["merge-base", "--is-ancestor", sha, main])
            cache[sha] = None if done is None else (done.returncode == 0)
            if done is not None and done.returncode not in (0, 1):
                cache[sha] = None
        return cache[sha]
    return ask


# ------------------------------------------------------------------ 讀資料


def agent_runs(rows):
    """從事件重建 agent 的時間線。

    `ticket.attempt.start` 配 `done` / `failed`,鍵是(票號, attempt)—— 用「最近
    一筆 done」去配所有 start,兩個同時在跑的 agent 會互相把對方銷掉。
    配不到結尾的就是**現在還活著**(或者死了沒人知道,那是 `heartbeat.sh` 的事)。
    """
    runs = {}
    order = []
    for row in rows:
        kind = row.get("kind")
        if kind not in (ATTEMPT_START,) + ATTEMPT_END:
            continue
        key = (str(row.get("ticket") or ""), str(row.get("attempt") or ""))
        if kind == ATTEMPT_START:
            runs[key] = {"ticket": key[0], "attempt": key[1],
                         "role": row.get("role", ""), "model": row.get("model", ""),
                         "start": row.get("ts", ""), "end": "", "outcome": "在跑",
                         "note": row.get("note", "")}
            order.append(key)
        elif key in runs:
            runs[key]["end"] = row.get("ts", "")
            runs[key]["outcome"] = "完成" if kind == "ticket.attempt.done" else "失敗"
            if row.get("note"):
                runs[key]["note"] = row["note"]
    return [runs[key] for key in order]


def sessions(rows):
    """誰在線上:`session.start` 配同一個 pid 的 `session.end`。"""
    live = {}
    order = []
    for row in rows:
        pid = str(row.get("pid") or "")
        if row.get("kind") == SESSION_START:
            live[pid] = {"pid": pid, "role": row.get("role", ""),
                         "model": row.get("model", ""), "start": row.get("ts", ""),
                         "end": ""}
            order.append(pid)
        elif row.get("kind") == SESSION_END and pid in live:
            live[pid]["end"] = row.get("ts", "")
    return [live[pid] for pid in order]


def latest_answers():
    return ticket.latest_answers()


# ------------------------------------------------- 磁碟上已經有的那幾份(只讀)


def reports_dir():
    """`board/config.json` 的 `reports_dir`(預設 `reports/`)。

    路徑從設定解析,不寫死 —— 專案的 reports 可以落在別的地方(`reports_dir`),
    而一個寫死的相對路徑在那裡會**安靜地**指到
    一個沒有人寫過的空目錄,然後這一段就會永遠說「沒有跑過」。
    """
    where = root()
    rel = event.config(where).get("reports_dir") or DEFAULT_REPORTS
    return rel if os.path.isabs(rel) else os.path.join(where, rel)


def rel_path(path):
    try:
        return os.path.relpath(path, root())
    except ValueError:
        return path


def read_json(path, broken):
    """一份 JSON,讀不動就記一句「<路徑> 讀不動」並回 None。

    **壞掉的一份檔不准讓整頁 500** —— 看板是拿來查「哪裡壞了」的,它自己先倒下去的
    那一刻,壞掉的那份檔就從看得見變成看不見。而「壞了」與「沒有」是兩句話,所以
    壞掉的那一份要指名它自己。
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        broken.append(UNREADABLE % rel_path(path))
        return None
    return data if isinstance(data, dict) else None


def round_file(where, broken):
    """這一趟的 `result-round<r>.json`(#20 的產出),輪數最大的那一份。

    沒有那個檔是**常態不是錯**(#20 落地之前每一趟都沒有),所以它回 None,由畫面
    去說那一句話 —— 這裡回一份空的 dict 的話,「沒交」會變成「交了但都是空的」。
    """
    try:
        names = os.listdir(where)
    except OSError:
        return None
    found = []
    for name in names:
        hit = ROUND_FILE.match(name)
        if hit:
            found.append((int(hit.group(1)), name))
    if not found:
        return None
    name = sorted(found)[-1][1]
    data = read_json(os.path.join(where, name), broken)
    if data is None:
        return None
    gate = data.get("gate")
    mutations = data.get("mutations")
    return {"file": name,
            "rc": data.get("rc"),
            # `gate.ran`:巢狀那一格優先,平的那一格是退路。沒有就是**不知道**,
            # 不是「沒跑」—— 那兩句在同一格布林值裡長得一樣。
            "gate_ran": gate.get("ran") if isinstance(gate, dict) else data.get("gate_ran"),
            "mutations": len(mutations) if isinstance(mutations, list) else None}


def first_line(text):
    return str(text or "").splitlines()[0] if str(text or "").strip() else ""


def is_green(data):
    """綠 = 跑完了、`rc` 是 0、紅榜是空的。

    `rc` 是 `None` 的那一份是**還在跑**(`status.py` 把 `state` 與 `rc` 分成兩格就是
    為了這件事)—— 把它算成綠,等於把「還沒有答案」講成「過了」。
    """
    rc = data.get("rc")
    if isinstance(rc, bool) or not isinstance(rc, int) or rc != 0:
        return False
    return not (data.get("failures") or [])


def one_run(run_id, where, broken):
    """一趟 run:`status.json` 那幾格 + 同目錄的 `result-round<r>.json`。"""
    mine = []
    data = read_json(os.path.join(where, STATUS_NAME), mine)
    broken.extend(mine)
    data = data or {}
    failures = []
    for row in (data.get("failures") or [])[:FAILURES_SHOWN]:
        if isinstance(row, dict):
            failures.append({"case": str(row.get("case") or ""),
                             "excerpt": first_line(row.get("excerpt"))})
    # 這一格經 `status.environment_suspects()` 讀(形狀見
    # `docs/DESIGN-ENV-SUSPECT.md`,D-019),不直接下標:舊檔的非空 dict 在 `[0]` 上會炸,
    # 而一頁炸掉的看板與一頁「什麼都沒發生」的看板長得一樣。
    suspects = [{"source": str(row.get("source") or ""),
                 "engine": str(row.get("engine") or ""),
                 "why": str(row.get("why") or "")}
                for row in status.environment_suspects(data)]
    return {"run_id": run_id,
            "kind": data.get("kind") or "",
            "env_suspects": suspects,
            "state": data.get("state") or "",
            "rc": data.get("rc"),
            "started": data.get("started") or "",
            "finished": data.get("finished") or "",
            "duration_seconds": data.get("duration_seconds"),
            "failures": failures,
            "failure_total": len(data.get("failures") or []),
            "green": is_green(data),
            "broken": mine[0] if mine else "",
            "round_file": round_file(where, broken)}


def run_rows(ident):
    """(a) 一張票的**全部** run。`run_id` 開頭是時間戳,所以字典序就是時間序。"""
    ident = str(ident)
    where = os.path.join(reports_dir(), "t%s" % ident)
    box = {"ticket": ident, "dir": rel_path(where), "exists": False,
           "runs": [], "broken": []}
    if not TICKET_ID.match(ident):
        return box
    box["exists"] = os.path.isdir(where)
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return box
    for name in names:
        run = os.path.join(where, name)
        if not os.path.exists(os.path.join(run, STATUS_NAME)):
            continue
        box["runs"].append(one_run(name, run, box["broken"]))
    return box


def inbox_rows():
    """(b) `<reports_dir>/inbox/` 裡還沒 ack 的頁(ack 過的搬進 `acked/`,不在這裡)。

    ack 是「我收下了」不是「我做完了」 —— 票的狀態由票說了算(`scripts/inbox.py`)。
    目錄不在是**一則都還沒寫過**,那是新裝起來的樣子,不是壞掉。
    """
    where = root()
    return {"dir": rel_path(inbox.inbox_dir(where)), "entries": inbox.entries(where),
            "broken": []}


# ---------------------------------------------- VISION 四個指標(只讀票 JSON)


def whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def add_known(values):
    """非 null 的加總;一個都沒有就是 None(未知),不是 0。"""
    known = [v for v in (whole(x) for x in values) if v is not None]
    return sum(known) if known else None


def cost_rows(one):
    return [row for row in (one.get("cost") if isinstance(one.get("cost"), list) else [])
            if isinstance(row, dict)]


def ticket_numbers(one):
    """一張票的 token(非 cache = in + out + cache_write,cache 讀另列;D-056 ②)、
    wall 秒數、worker 最大 round、介入次數。`cost_total` 在就讀它(關票那一刻的定本),
    不在才從 `cost[]` 加。拿不到的格子是 None —— 畫面與 /api/state 印成「未知」。"""
    total = one.get("cost_total") if isinstance(one.get("cost_total"), dict) else None
    rows = cost_rows(one)
    if total is not None:
        parts = [part for part in (total.get("by_role") or {}).values()
                 if isinstance(part, dict)]
        tokens = add_known(add_known(part.get(k) for k in ("tokens_in", "tokens_out",
                                                           "cache_write"))
                           for part in parts)
        cache_read = add_known(part.get("cache_read") for part in parts)
        wall = whole(total.get("wall_seconds"))
        rounds = whole(total.get("worker_rounds"))
    else:
        tokens = add_known(add_known(row.get(k) for k in ("tokens_in", "tokens_out",
                                                          "cache_write"))
                           for row in rows)
        cache_read = add_known(row.get("cache_read") for row in rows)
        wall = add_known(row.get("wall_seconds") for row in rows)
        rounds = max([n for n in (whole(row.get("round")) for row in rows
                                  if row.get("role") == "worker") if n is not None],
                     default=None)
    return {"has_cost": total is not None or bool(rows),
            "tokens": tokens, "cache_read": cache_read, "wall_seconds": wall,
            "worker_rounds": rounds, "interventions": whole(one.get("interventions"))}


def waits_on_main(one):
    return one.get("state") in ticket.INTERVENTION_STATES and one.get("owner") == "main"


def shown(value):
    return UNKNOWN if value is None else value


def vision(tickets, box):
    """四個指標。每一格拿不到就是「未知」(VISION「看板與統計」)。"""
    per = {str(one.get("id", "")): ticket_numbers(one) for one in tickets}
    done = [per[str(one.get("id", ""))] for one in tickets if one.get("state") == "Done"]
    # 一次就過:有 cost 列、而且有 worker round 的 Done 票才計;一張都沒有就是未知。
    counted = [row for row in done if row["has_cost"] and row["worker_rounds"] is not None]
    first = sum(1 for row in counted if row["worker_rounds"] == 1)
    recorded = [row["interventions"] for row in per.values() if row["interventions"] is not None]
    waiting_tickets = sum(1 for one in tickets if waits_on_main(one))
    pages = len(box.get("entries") or [])
    landed = [row for row in done if row["tokens"] is not None or row["wall_seconds"] is not None]
    return {
        "tickets": {ident: {key: shown(row[key]) for key in
                            ("tokens", "cache_read", "wall_seconds", "worker_rounds",
                             "interventions")}
                    for ident, row in per.items()},
        "landed": {"count": len(done),
                   "tokens": shown(add_known(row["tokens"] for row in done)),
                   "wall_seconds": shown(add_known(row["wall_seconds"] for row in done)),
                   "measured": len(landed)},
        "first_pass": ({"passed": first, "counted": len(counted),
                        "text": "%d/%d" % (first, len(counted))}
                       if counted else {"passed": UNKNOWN, "counted": 0, "text": UNKNOWN}),
        "interventions": ({"total": sum(recorded), "tickets": len(recorded)}
                          if recorded else {"total": UNKNOWN, "tickets": 0}),
        "waiting": {"tickets": waiting_tickets, "inbox_pages": pages,
                    "total": waiting_tickets + pages},
    }


def open_blocking(item):
    """處置空著、而且 blocking 為真 —— land 與 close 都會被它擋住的那一種。"""
    return bool(item.get("blocking")) and not str(item.get("disposition") or "").strip()


def ticket_boxes(one):
    """(c) 票自己的三格:`objections[]` / `review` / `verify`。

    反駁的排序:**處置空著而且阻擋的排最前** —— 那一種是 land 與 close 都會被擋下的
    事實,而排在第三頁的阻擋項與沒有反駁長得一樣。
    """
    items = [row for row in (one.get("objections") or []) if isinstance(row, dict)]
    items.sort(key=lambda row: 0 if open_blocking(row) else 1)
    review = one.get("review") if isinstance(one.get("review"), dict) else {}
    verify = one.get("verify") if isinstance(one.get("verify"), dict) else {}
    return {"objections": items, "review": review, "verify": verify}


def state():
    rows = event.read_events(root())
    tickets = ticket.load_all()
    ask = ancestor_cache()
    for one in tickets:
        one["_ancestor"] = ask(one.get("base_sha"))
    box = inbox_rows()
    return {"generated_at": now_text(),
            "tickets": tickets,
            "events": rows,
            "runs": agent_runs(rows),
            "sessions": sessions(rows),
            "answers": latest_answers(),
            # **`runs` 與 `ticket_runs` 不是同一件事**:上面那一格是從事件重建的
            # agent 時間線(誰在跑),這一格是磁碟上的閘門/落地結果(跑出了什麼)。
            # 合成一格的話,一個沒有發事件的閘門會看起來像沒有跑過。
            "ticket_runs": {str(one.get("id", "")): run_rows(one.get("id", ""))
                            for one in tickets},
            "boxes": {str(one.get("id", "")): ticket_boxes(one) for one in tickets},
            "inbox": box,
            # VISION 四個指標,只讀票 JSON 與收件匣目錄。**這一格不是 `tokens` 的替身**
            # —— 抬頭那一格問的是「這個看板自己知不知道整體用量」(不知道)。
            "vision": vision(tickets, box),
            "tokens": UNKNOWN}


def ticket_state(one):
    """`/t/<票號>` 那一頁要的那幾份。整張看板的 `state()` 這裡不跑 —— 一個人打開
    一張票的頁,不該把全部票的 reports 掃一遍。"""
    ident = str(one.get("id", ""))
    return {"generated_at": now_text(), "ticket": one, "ticket_id": ident,
            "runs": run_rows(ident), "box": ticket_boxes(one),
            "inbox": inbox_rows()}


# --------------------------------------------------------------------- 畫面


CSS = """
:root { color-scheme: light dark; --line: #d8d5cf; --ink: #1b1a17; --dim: #6b675f;
        --bg: #faf9f6; --card: #fff; --ok: #1f7a44; --bad: #b03030; --warn: #9a6b12; }
@media (prefers-color-scheme: dark) {
  :root { --line: #3a3833; --ink: #ece9e2; --dim: #9b968c; --bg: #171613;
          --card: #201f1b; --ok: #5fbf87; --bad: #e57373; --warn: #d9a441; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55
       -apple-system, "Helvetica Neue", "PingFang TC", sans-serif; }
header { padding: 12px 16px 0; }
h1 { font-size: 17px; margin: 0 0 2px; }
.stampline { color: var(--dim); font-size: 12px; }
nav { display: flex; gap: 8px; padding: 10px 16px; border-bottom: 1px solid var(--line); }
nav a { text-decoration: none; color: var(--ink); border: 1px solid var(--line);
        border-radius: 999px; padding: 4px 12px; font-size: 13px; }
main { padding: 12px 16px 40px; max-width: 1100px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          padding: 12px 14px; margin: 0 0 12px; scroll-margin-top: 12px; }
h2 { font-size: 14px; margin: 0 0 8px; color: var(--dim); font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px 5px 0; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { color: var(--dim); font-weight: 600; white-space: nowrap; }
td.id, td.when, td.model { white-space: nowrap; }
.pill { display: inline-block; border-radius: 999px; padding: 1px 8px; font-size: 12px;
        border: 1px solid var(--line); }
.s-Running, .s-Integrating { color: var(--warn); border-color: var(--warn); }
.s-Done { color: var(--ok); border-color: var(--ok); }
.s-NeedsDecision, .s-Blocked, .s-Failed { color: var(--bad); border-color: var(--bad); }
.ok { color: var(--ok); } .bad { color: var(--bad); } .warn { color: var(--warn); }
.empty { color: var(--dim); font-size: 13px; }
code { font: 12px ui-monospace, Menlo, monospace; }
pre { overflow-x: auto; font: 12px/1.45 ui-monospace, Menlo, monospace; margin: 0; }
ul { margin: 0; padding-left: 18px; }
li { margin: 2px 0; }
.counts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; }
.counts b { font-size: 20px; display: block; font-weight: 600; }
ul.inbox { list-style: none; padding-left: 0; }
.ib-item { border: 1px solid var(--line); border-left: 3px solid var(--bad);
           border-radius: 8px; padding: 8px 10px; margin: 0 0 10px; }
.ib-answer { display: block; width: 100%; margin-top: 8px; font: inherit;
             font-size: 13px; padding: 6px 8px; border-radius: 8px;
             border: 1px solid var(--line); background: var(--card);
             color: var(--ink); resize: vertical; }
.ib-row { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.ib-save { font: inherit; font-size: 13px; padding: 4px 16px; border-radius: 999px;
           border: 1px solid var(--ink); background: var(--ink); color: var(--bg);
           cursor: pointer; }
.ib-save[disabled] { opacity: .55; cursor: progress; }
.ib-note { font-size: 12px; }
.ib-note.saved { color: var(--ok); }
.ib-note.bad { color: var(--bad); }
"""

JS = """
(function () {
  var main = document.querySelector('main');

  function wireInbox() {
    Array.prototype.forEach.call(document.querySelectorAll('.ib-save'), function (btn) {
      btn.addEventListener('click', function () {
        var item = btn.closest('.ib-item');
        var box = item.querySelector('.ib-answer');
        var note = item.querySelector('.ib-note');
        btn.disabled = true;
        note.className = 'ib-note';
        note.textContent = '存…';
        fetch('api/answer', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticket: item.dataset.ticket, answer: box.value })
        }).then(function (r) { return r.json(); }).then(function (data) {
          btn.disabled = false;
          if (data && data.ok) {
            note.className = 'ib-note saved';
            note.textContent = '存好了 ' + (data.saved_time || '');
          } else {
            note.className = 'ib-note bad';
            note.textContent = (data && data.error) || '沒存成';
          }
        }).catch(function () {
          btn.disabled = false;
          note.className = 'ib-note bad';
          note.textContent = '沒存成(連不上)';
        });
      });
    });
  }

  wireInbox();

  // 只換 <main>:打到一半的答案在 textarea 裡,所以有人在打字就這一輪不換。
  setInterval(function () {
    if (document.activeElement && document.activeElement.classList
        && document.activeElement.classList.contains('ib-answer')) { return; }
    fetch(location.href, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) { throw new Error('refresh'); }
      return r.text();
    }).then(function (text) {
      var fresh = new DOMParser().parseFromString(text, 'text/html')
        .querySelector('main');
      if (fresh) { main.innerHTML = fresh.innerHTML; wireInbox(); }
    }).catch(function () { /* 這一輪抓不到,下一輪再說 */ });
  }, 30000);
})();
"""

NAV = (("tickets", "票與依賴"), ("timeline", "agent 時間線"),
       ("land", "落地"), ("inbox", "決策收件匣"),
       ("runs", "每票最後一輪"), ("objections", "反駁"))


def page(body, generated, title="控制台", prefix=""):
    """一頁的殼。

    css / js 走**絕對路徑**:`/t/<票號>` 那一頁的相對 `board.css` 會被瀏覽器解成
    `/t/board.css`,而那是一個 404 —— 一頁沒有樣式的表格與一頁壞掉的表格長得一樣。
    錨點在子頁要帶 `/` 前綴,不然它們指到一頁自己沒有的那幾段。
    """
    links = "".join("<a href=\"%s#%s\">%s</a>" % (esc(prefix), esc(href), esc(label))
                    for href, label in NAV)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-Hant\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>%s</title>\n"
        "<link rel=\"stylesheet\" href=\"/board.css\">\n"
        "</head>\n<body>\n"
        "<header><h1>%s</h1>"
        "<div class=\"stampline\">讀於 %s ・ 每 30 秒自動刷新 ・ token 用量:%s</div>"
        "</header>\n<nav>%s</nav>\n%s"
        "<script src=\"/board.js\"></script>\n</body>\n</html>\n"
        % (esc(title), esc(title), esc(generated), esc(UNKNOWN), links, body))


def ancestor_cell(value):
    if value is True:
        return "<span class=\"ok\">是</span>"
    if value is False:
        return "<span class=\"bad\">**不是了**,閘門的綠已經過期</span>"
    return "<span class=\"warn\">問不出來</span>"


def render_vision(numbers):
    """VISION 四個指標,一格一句;拿不到的格子寫「未知」。"""
    landed, first = numbers["landed"], numbers["first_pass"]
    told, waiting = numbers["interventions"], numbers["waiting"]
    cells = (
        ("每張落地票的 token 與時間",
         "%s token・%s 秒(Done %d 張,有數字 %d 張)"
         % (landed["tokens"], landed["wall_seconds"], landed["count"], landed["measured"])),
        ("一次就過測試的比例", "%s" % first["text"]),
        ("需要主線介入的次數",
         "%s 次(有記數的 %d 張;轉 Blocked / NeedsDecision 且 owner=main 記一次)"
         % (told["total"], told["tickets"])),
        ("等主線處理的事",
         "%d(票 %d 張 + 收件匣未 ack %d 頁)"
         % (waiting["total"], waiting["tickets"], waiting["inbox_pages"])),
    )
    return ("<div class=\"counts vision\">%s</div>"
            % "".join("<div data-metric=\"%d\"><b>%s</b>%s</div>"
                      % (n, esc(value), esc(label))
                      for n, (label, value) in enumerate(cells, 1)))


def render_tickets(data):
    out = ["<section id=\"tickets\"><h2>① VISION 四個指標與票</h2>"]
    out.append(render_vision(data["vision"]))
    tickets = data["tickets"]
    if not tickets:
        out.append("<p class=\"empty\">一張票都沒有</p></section>")
        return "".join(out)
    counts = {}
    for one in tickets:
        counts[one.get("state", "?")] = counts.get(one.get("state", "?"), 0) + 1
    out.append("<div class=\"counts\">")
    for state_name, count in sorted(counts.items()):
        out.append("<div><b>%d</b>%s</div>" % (count, esc(state_name)))
    out.append("</div>")
    out.append("<table><tr><th>票</th><th>狀態</th><th>v</th><th>前置</th>"
               "<th>base_sha 還是主線祖先?</th><th>token</th><th>cache 讀</th>"
               "<th>wall 秒</th><th>worker 輪</th><th>介入</th>"
               "<th>驗證者</th><th>覆核</th><th>凍結</th><th>標題</th></tr>")
    numbers = data["vision"]["tickets"]
    for one in tickets:
        deps = ", ".join("#%s" % esc(d.get("id", "")) for d in (one.get("depends_on") or []))
        frozen = one.get("frozen") or {}
        row = numbers.get(str(one.get("id", ""))) or {}
        out.append(
            "<tr><td class=\"id\">#%s</td>"
            "<td><span class=\"pill s-%s\">%s</span></td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(one.get("id", "")), esc(one.get("state", "")),
               esc(one.get("state", "")), esc(one.get("state_version", "")),
               deps or "—", ancestor_cell(one.get("_ancestor")),
               *(esc(row.get(key, UNKNOWN)) for key in
                 ("tokens", "cache_read", "wall_seconds", "worker_rounds", "interventions")),
               # D-075:讀法與動手的那支一樣 —— 只有 JSON false 不起驗證者(auto-fix.sh
               # 的 `is False`),只有 complex 做覆核(ticket.py needs_review);缺 / null 不另說。
               "無" if one.get("needs_verifier") is False else "有",
               "有" if one.get("complexity") == "complex" else "無",
               esc(frozen.get("reason", "")) or "—", esc(one.get("subject", ""))))
    out.append("</table></section>")
    return "".join(out)


def render_timeline(data):
    out = ["<section id=\"timeline\"><h2>② agent 時間線(從事件重建)</h2>"]
    runs = data["runs"]
    if not runs:
        out.append("<p class=\"empty\">還沒有人開過工(沒有 ticket.attempt.start 事件)</p>")
    else:
        out.append("<table><tr><th>票</th><th>第幾次</th><th>角色</th><th>模型</th>"
                   "<th>開始</th><th>結束</th><th>現在</th><th>備註</th></tr>")
        for run in runs:
            css = {"在跑": "warn", "完成": "ok", "失敗": "bad"}[run["outcome"]]
            out.append("<tr><td class=\"id\">#%s</td><td>%s</td><td>%s</td>"
                       "<td class=\"model\">%s</td><td class=\"when\">%s</td>"
                       "<td class=\"when\">%s</td><td class=\"%s\">%s</td><td>%s</td></tr>"
                       % (esc(run["ticket"]), esc(run["attempt"]), esc(run["role"]),
                          esc(run["model"]), esc(run["start"]), esc(run["end"]) or "—",
                          css, esc(run["outcome"]), esc(run["note"])))
        out.append("</table>")
    live = [s for s in data["sessions"] if not s["end"]]
    out.append("<h2>在線上的 session</h2>")
    if not live:
        out.append("<p class=\"empty\">沒有 session 在線上</p>")
    else:
        out.append("<ul>")
        for one in live:
            out.append("<li>%s ・ %s ・ pid %s ・ 從 %s</li>"
                       % (esc(one["role"]), esc(one["model"]), esc(one["pid"]),
                          esc(one["start"])))
        out.append("</ul>")
        out.append("<p class=\"empty\">「有 start、沒有 end」與「正在思考」長得一樣 ——"
                   "誰死在半路要問 <code>scripts/heartbeat.sh</code>(它比對租約)。</p>")
    out.append("</section>")
    return "".join(out)


def render_land(data):
    out = ["<section id=\"land\"><h2>③ 落地佇列與最近的 land</h2>"]
    queued = [t for t in data["tickets"] if t.get("state") in QUEUE_STATES]
    if not queued:
        out.append("<p class=\"empty\">佇列是空的</p>")
    else:
        out.append("<ul>")
        for one in queued:
            out.append("<li>#%s <span class=\"pill s-%s\">%s</span> %s</li>"
                       % (esc(one["id"]), esc(one["state"]), esc(one["state"]),
                          esc(one.get("subject", ""))))
        out.append("</ul>")
    lands = [row for row in data["events"] if row.get("kind") in LAND_KINDS][-12:]
    out.append("<h2>最近的 land 事件</h2>")
    if not lands:
        out.append("<p class=\"empty\">還沒有落地過</p>")
    else:
        out.append("<table><tr><th>時間</th><th>事件</th><th>批次</th><th>說明</th></tr>")
        for row in reversed(lands):
            css = {"land.pass": "ok", "land.fail": "bad",
                   "land.refused": "bad", "land.start": "warn"}[row["kind"]]
            out.append("<tr><td class=\"when\">%s</td><td class=\"%s\">%s</td>"
                       "<td><code>%s</code></td><td>%s</td></tr>"
                       % (esc(row.get("ts", "")), css, esc(row["kind"]),
                          esc(row.get("stamp", "")), esc(row.get("note", ""))))
        out.append("</table>")
    out.append("</section>")
    return "".join(out)


def render_inbox(data):
    out = ["<section id=\"inbox\"><h2>④ 決策收件匣</h2>"]
    rows = [t for t in data["tickets"] if t.get("state") == "NeedsDecision"]
    if not rows:
        out.append("<p class=\"empty\">沒有等你裁決的事</p>")
        out.append(inbox_pages(data["inbox"]))
        out.append("</section>")
        return "".join(out)
    out.append("<ul class=\"inbox\">")
    for one in rows:
        saved = data["answers"].get(one["id"]) or {}
        out.append("<li class=\"ib-item\" data-ticket=\"%s\">" % esc(one["id"]))
        out.append("<div><b>#%s</b> %s</div>" % (esc(one["id"]), esc(one.get("subject", ""))))
        if one.get("objective"):
            out.append("<div class=\"empty\">%s</div>" % esc(one["objective"]))
        out.append("<textarea class=\"ib-answer\" rows=\"3\">%s</textarea>"
                   % esc(saved.get("answer", "")))
        out.append("<div class=\"ib-row\"><button class=\"ib-save\">儲存</button>"
                   "<span class=\"ib-note\">%s</span></div>"
                   % (esc("上次存於 " + saved["ts"]) if saved.get("ts") else ""))
        out.append("</li>")
    out.append("</ul>")
    out.append("<p class=\"empty\">這顆鈕只把答案寫成一行,不改票的狀態、不派工。"
               "落成裁示是主線的事(<code>docs/DECISIONS.md</code> 一列一條)。</p>")
    out.append(inbox_pages(data["inbox"]))
    out.append("</section>")
    return "".join(out)


# ----------------------------------------------- ⑥⑦ 與 `/t/<票號>` 那幾段


RUN_HEAD = ("<table><tr><th>票</th><th>run_id</th><th>kind</th><th>state</th>"
            "<th>rc</th><th>開始</th><th>結束</th><th>秒</th><th>結果</th></tr>")
RUN_COLS = 9


def number_text(value):
    """數字那一格。缺料是**未知**,不是 0 —— 那兩件事在同一欄數字裡長得一樣。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return UNKNOWN
    return str(value)


def ran_text(value):
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def broken_list(messages):
    if not messages:
        return ""
    seen = []
    for one in messages:
        if one not in seen:
            seen.append(one)
    return ("<ul>%s</ul>"
            % "".join("<li class=\"bad\">%s</li>" % esc(one) for one in seen))


def suspect_bit(run):
    """環境可疑那一句,**接在紅的那一句前面**(D-019)。

    這一格存在就是為了把讀的人導去修環境。一張只寫著「紅 26 條」的看板把人導去
    auto-fix,而那一輪的機器根本跑不動 —— 所以兩句都要印:**幾筆**(導向環境)
    與**紅幾條**(那是真的證據,不該被蓋掉)。
    """
    rows = run.get("env_suspects") or []
    if not rows:
        return ""
    first = rows[0]
    return ("<span class=\"warn\">環境可疑 %d 筆(%s:%s)</span> "
            % (len(rows), esc(first.get("engine") or UNKNOWN),
               esc(first.get("why") or "")))


def verdict_cell(run):
    """這一趟的結果。**綠是一句話,不是一格空白。**"""
    if run["broken"]:
        return "<span class=\"bad\">%s</span>" % esc(run["broken"])
    head = suspect_bit(run)
    if run["green"]:
        return head + "<span class=\"ok\">%s</span>" % esc(GREEN_RUN)
    if run["failure_total"]:
        return head + "<span class=\"bad\">紅 %d 條</span>" % run["failure_total"]
    rc = run["rc"]
    if isinstance(rc, int) and not isinstance(rc, bool) and rc != 0:
        # 紅、而且**紅榜是空的**:land 的拒收就長這樣(rc≠0,一條測試都沒倒)。
        # 這一格退回去印 `state` 的話,一次拒收會在畫面上寫著 `done`。
        return head + "<span class=\"bad\">紅(rc=%d),沒有逐條紅榜</span>" % rc
    return head + esc(run["state"] or UNKNOWN)


def run_tr(ident, run):
    return ("<tr data-ticket=\"%s\"><td class=\"id\"><a href=\"/t/%s\">#%s</a></td>"
            "<td class=\"id\">%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td class=\"when\">%s</td><td class=\"when\">%s</td><td>%s</td>"
            "<td class=\"verdict\">%s</td></tr>"
            % (esc(ident), esc(ident), esc(ident), esc(run["run_id"]),
               esc(run["kind"]) or "—", esc(run["state"]) or "—",
               esc(number_text(run["rc"])), esc(run["started"]) or "—",
               esc(run["finished"]) or "—", esc(number_text(run["duration_seconds"])),
               verdict_cell(run)))


def empty_tr(ident, sentence):
    return ("<tr data-ticket=\"%s\"><td class=\"id\"><a href=\"/t/%s\">#%s</a></td>"
            "<td class=\"id\">—</td><td>—</td><td>—</td><td>—</td>"
            "<td class=\"when\">—</td><td class=\"when\">—</td><td>—</td>"
            "<td class=\"verdict\">%s</td></tr>"
            % (esc(ident), esc(ident), esc(ident), esc(sentence)))


def run_detail_tr(run):
    """一趟底下那一列:`result-round<r>.json` 的三格,與紅榜最前面那幾條。

    沒有那個檔就印一句話。**空白不是答案** —— `#20` 落地之前每一趟都會是這一句,
    而那時「worker 沒交」與「看板沒讀到」在一格空白裡長得一樣。
    """
    bits = []
    box = run["round_file"]
    if box is None:
        bits.append("<span class=\"warn\">%s</span>" % esc(NO_ROUND_JSON))
    else:
        bits.append("<code>%s</code> ・ rc %s ・ gate.ran %s ・ mutations %s 筆"
                    % (esc(box["file"]), esc(number_text(box["rc"])),
                       esc(ran_text(box["gate_ran"])),
                       esc(number_text(box["mutations"]))))
    for row in run["failures"]:
        bits.append("<div><code>%s</code> ・ %s</div>"
                    % (esc(row["case"]), esc(row["excerpt"])))
    left = run["failure_total"] - len(run["failures"])
    if left > 0:
        bits.append("<div class=\"empty\">還有 %d 條 —— 整份紅榜在 status.json</div>"
                    % left)
    return ("<tr><td>—</td><td colspan=\"%d\" class=\"empty\">%s</td></tr>"
            % (RUN_COLS - 1, "".join(bits)))


def render_runs(data):
    """⑥ 每票最後一輪。

    這一段不問任何人:它讀的是 `status.py` 已經寫在磁碟上的那份 JSON。**三種「沒有」
    要用三句不同的話** —— 目錄不在(一輪都還沒跑)、目錄在但一份結果都沒有(跑了、
    沒留下東西)、跑完而且是綠的,三件事的下一步完全不同。
    """
    out = ["<section id=\"runs\"><h2>⑥ 每票最後一輪(讀磁碟上的 status.json)</h2>"]
    boxes = data.get("ticket_runs") or {}
    tickets = data["tickets"]
    if not tickets:
        out.append("<p class=\"empty\">一張票都沒有</p></section>")
        return "".join(out)
    out.append(RUN_HEAD)
    broken = []
    for one in tickets:
        ident = str(one.get("id", ""))
        box = boxes.get(ident) or {}
        broken.extend(box.get("broken") or [])
        runs = box.get("runs") or []
        if not box.get("exists"):
            out.append(empty_tr(ident, NO_RUN_DIR))
        elif not runs:
            out.append(empty_tr(ident, EMPTY_RUN_DIR))
        else:
            out.append(run_tr(ident, runs[-1]))
            out.append(run_detail_tr(runs[-1]))
    out.append("</table>")
    out.append(broken_list(broken))
    out.append("<p class=\"empty\">一票一列,印的是**最後**那一輪;整串 run 在 "
               "<code>/t/&lt;票號&gt;</code>。</p></section>")
    return "".join(out)


def objection_item(ident, item):
    disposition = str(item.get("disposition") or "").strip()
    body = str(item.get("body") or "")
    return ("<li class=\"ib-item\"><div><b>#%s</b> <span class=\"pill\">%s</span> "
            "%s ・ 處置:%s ・ 阻擋:%s</div><div class=\"empty\">%s</div></li>"
            % (esc(ident), esc(item.get("category", "")) or "—",
               esc(item.get("owner", "")) or "—",
               esc(disposition) or "還沒處置(land 與 close 都會拒絕)",
               "是" if item.get("blocking") else "否",
               esc(body[:BODY_SHOWN])))


def render_objections(data):
    """⑦ 反駁。**處置空著而且阻擋的排最前** —— 沒被收進票的反駁與沒有反駁長得一樣,
    而排在最後一頁的阻擋項是同一件事的另一種寫法。"""
    out = ["<section id=\"objections\"><h2>⑦ 反駁(票自己的 objections[])</h2>"]
    rows = []
    boxes = data.get("boxes") or {}
    for one in data["tickets"]:
        ident = str(one.get("id", ""))
        for item in (boxes.get(ident) or {}).get("objections") or []:
            rows.append((ident, item))
    rows.sort(key=lambda pair: 0 if open_blocking(pair[1]) else 1)
    if not rows:
        out.append("<p class=\"empty\">沒有人反駁過任何一張票</p></section>")
        return "".join(out)
    out.append("<ul class=\"inbox\">")
    for ident, item in rows:
        out.append(objection_item(ident, item))
    out.append("</ul></section>")
    return "".join(out)


def inbox_pages(box, ident=None):
    """終態收件匣:`<reports_dir>/inbox/` 裡的頁。ack 過的搬進 `acked/`,不在這裡。"""
    out = ["<h2>終態收件匣(還沒 ack)</h2>"]
    rows = box.get("entries") or []
    if ident is not None:
        rows = [row for row in rows if str(row.get("ticket", "")) == str(ident)]
    if not rows:
        out.append("<p class=\"empty\">收件匣是空的(ack 過的不算)</p>")
    else:
        out.append("<table><tr><th>票</th><th>狀態</th><th>要主線做什麼</th>"
                   "<th>去哪看</th><th>時間</th></tr>")
        for row in rows:
            out.append("<tr><td class=\"id\"><a href=\"/t/%s\">#%s</a></td><td>%s</td>"
                       "<td>%s</td><td><code>%s</code></td><td class=\"when\">%s</td></tr>"
                       % (esc(row.get("ticket", "")), esc(row.get("ticket", "")),
                          esc(row.get("state", "")), esc(row.get("what", "")),
                          esc(row.get("where", "")) or esc(row.get("page", "")),
                          esc(row.get("at", ""))))
        out.append("</table>")
    out.append(broken_list(box.get("broken") or []))
    return "".join(out)


def render_ticket_runs(box):
    out = ["<section id=\"runs\"><h2>全部 run(舊的在前)</h2>"]
    runs = box.get("runs") or []
    if not box.get("exists"):
        out.append("<p class=\"empty\">%s —— 沒有 <code>%s</code> 這個目錄</p>"
                   % (esc(NO_RUN_DIR), esc(box.get("dir", ""))))
    elif not runs:
        out.append("<p class=\"empty\">%s —— <code>%s</code> 在,裡面 0 份 %s</p>"
                   % (esc(EMPTY_RUN_DIR), esc(box.get("dir", "")), esc(STATUS_NAME)))
    else:
        out.append(RUN_HEAD)
        for run in runs:
            out.append(run_tr(box["ticket"], run))
            out.append(run_detail_tr(run))
        out.append("</table>")
    out.append(broken_list(box.get("broken") or []))
    out.append("</section>")
    return "".join(out)


def render_review(one, box):
    out = ["<section id=\"review\"><h2>覆核</h2>"]
    review = box["review"]
    if not review:
        # 只有 complex 的票做完後覆核(#136;沒標 = simple,land 不要它的 review)。
        said = ("還沒有人覆核(complex 票,land 會拒絕)" if one.get("complexity") == "complex"
                else "不做做完後的覆核(simple 票)")
        out.append("<p class=\"empty\">%s</p></section>" % esc(said))
        return "".join(out)
    out.append("<table>")
    for key in ("verdict", "by", "sha", "note", "at"):
        out.append("<tr><th>%s</th><td>%s</td></tr>"
                   % (esc(key), esc(review.get(key, "")) or "—"))
    out.append("</table>")
    out.append("</section>")
    return "".join(out)


def render_verify(box):
    out = ["<section id=\"verify\"><h2>驗證</h2>"]
    verify = box["verify"]
    rows = (("files", ", ".join(str(f) for f in (verify.get("files") or []))),
            ("tags", ", ".join(str(t) for t in (verify.get("tags") or []))),
            ("run", verify.get("run", "")),
            ("baseline", verify.get("baseline", "")))
    if not verify or not any(value for _, value in rows):
        out.append("<p class=\"empty\">還沒有人寫案例在哪、怎麼跑</p></section>")
        return "".join(out)
    out.append("<table>")
    for key, value in rows:
        # 空著的那一格印一句話。**一格空白不是答案** —— 2026-09-20 驗證者交了案例卻
        # 沒寫怎麼跑,下一個人重跑整組閘門去找它們(D-010)。
        out.append("<tr><th>%s</th><td>%s</td></tr>"
                   % (esc(key), esc(value) or "<span class=\"warn\">還沒有人寫</span>"))
    out.append("</table></section>")
    return "".join(out)


def render_ticket_objections(ident, box):
    out = ["<section id=\"objections\"><h2>反駁</h2>"]
    items = box.get("objections") or []
    if not items:
        out.append("<p class=\"empty\">這張票沒有人反駁過</p></section>")
        return "".join(out)
    out.append("<ul class=\"inbox\">")
    for item in items:
        out.append(objection_item(ident, item))
    out.append("</ul></section>")
    return "".join(out)


def render_ticket_head(one):
    out = ["<section id=\"ticket\"><h2>票面</h2><table>"]
    deps = ", ".join("#%s" % esc(d.get("id", "")) for d in (one.get("depends_on") or []))
    frozen = one.get("frozen") or {}
    for label, value in (("標題", one.get("subject", "")),
                         ("狀態", one.get("state", "")),
                         ("state_version", one.get("state_version", "")),
                         ("角色 / 模型", "%s / %s" % (one.get("role", ""),
                                                  one.get("model", ""))),
                         ("第幾次", one.get("attempt", "")),
                         ("base_sha", one.get("base_sha", "")),
                         ("凍結", frozen.get("reason", ""))):
        out.append("<tr><th>%s</th><td>%s</td></tr>" % (esc(label), esc(value) or "—"))
    out.append("<tr><th>前置</th><td>%s</td></tr>" % (deps or "—"))
    out.append("<tr><th>目標</th><td>%s</td></tr>" % esc(one.get("objective", "")))
    out.append("</table></section>")
    return "".join(out)


def render_ticket(data):
    one = data["ticket"]
    ident = data["ticket_id"]
    return ("<main>" + render_ticket_head(one) + render_ticket_runs(data["runs"])
            + render_ticket_objections(ident, data["box"])
            + render_review(one, data["box"]) + render_verify(data["box"])
            + "<section id=\"inbox\">" + inbox_pages(data["inbox"], ident)
            + "</section></main>\n")


def render(data):
    return ("<main>" + render_tickets(data) + render_timeline(data)
            + render_land(data) + render_inbox(data)
            + render_runs(data) + render_objections(data)
            + "</main>\n")


# --------------------------------------------------------------------- 伺服


def append_answer(ident, answer):
    """`O_APPEND` 一次 write —— 同一份只加不改的檔拿暫存檔重寫,會把別的行程在這
    中間 append 的那一行整行吃掉(同 `event.py`)。"""
    path = event.answers_path(root())
    row = {"ts": now_text(), "ticket": str(ident), "answer": str(answer)}
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, line)
    finally:
        os.close(handle)
    return row


KEY = None          # board.key_file 的第一行;None = 沒設閘門(main 填)
COOKIE = "ac_board_key"


def load_key(base):
    """`board.key_file`(相對專案根,或絕對)的第一行去掉空白。沒設 → None;設了卻讀不到
    或第一行是空的 → 拒答(ValueError):設了的閘門不能靜靜地開著(D-071 C9)。"""
    board = event.config(base).get("board")
    rel = board.get("key_file") if isinstance(board, dict) else None
    if rel is None:
        return None
    path = os.path.join(base, str(rel))
    try:
        with open(path, encoding="utf-8") as handle:
            first = handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        first = ""
    if not first:
        raise ValueError("board: key_file %s missing or empty" % path)
    return first


def same(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "control-board"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s board %s %s\n" % (
            datetime.now().isoformat(timespec="seconds"),
            self.command, urlparse(self.path).path))

    def gate(self):
        """True = 放行。有 key 時,除了 `/healthz` 每個方法每條路徑都要過:`?key=` 對 →
        302 到去掉 key 的同一條路徑並種 HttpOnly cookie;cookie 對 → 放行;其餘 401 空 body。"""
        if KEY is None:
            return True
        parsed = urlparse(self.path)
        if (parsed.path.rstrip("/") or "/") == "/healthz":
            return True
        good = [v for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k == "key" and same(v, KEY)]
        if good:
            rest = "&".join(
                part for part in parsed.query.split("&")
                if part and unquote(part.partition("=")[0].replace("+", " ")) != "key")
            self.close_connection = True
            self.send_response(302)
            self.send_header("Location", parsed.path + ("?" + rest if rest else ""))
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (
                COOKIE, good[0]))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        for header in self.headers.get_all("Cookie") or []:
            for item in header.split(";"):
                name, _, value = item.strip().partition("=")
                if name == COOKIE and same(value, KEY):
                    return True
        self.close_connection = True
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if self.gate():
            self.serve_get()

    def serve_get(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path == "/board.css":
            return self.send_body(CSS.encode("utf-8"), "text/css; charset=utf-8")
        if path == "/board.js":
            return self.send_body(JS.encode("utf-8"),
                                  "application/javascript; charset=utf-8")
        if path == "/api/state":
            data = state()
            data.pop("events", None)
            return self.send_json(data)
        if path == "/":
            data = state()
            return self.send_body(page(render(data), data["generated_at"]).encode("utf-8"),
                                  "text/html; charset=utf-8")
        if path.startswith("/t/"):
            return self.send_ticket(path[len("/t/"):])
        return self.not_found()

    def not_found(self):
        """404 的 body 是既有的那一句。**一張不存在的票不是一個 500** —— 500 的意思
        是「這個看板壞了」,而那會讓人去查一個沒有壞掉的東西。"""
        return self.send_body(NOT_FOUND.encode("utf-8"),
                              "text/plain; charset=utf-8", 404)

    def send_ticket(self, raw):
        """`/t/<票號>` 一頁。

        票號先解碼再過白名單:`%2f` 在 `urlparse` 之後還是 `%2f`,不解碼就會有一個
        `..%2f..%2fetc` 從檢查底下走過去。白名單擋掉的不只是 `/` 與 `..`,還有**沒有
        被想到的第三種寫法**。
        """
        ident = unquote(raw)
        if not TICKET_ID.match(ident):
            return self.not_found()
        try:
            one = ticket.load(ident)
        except (OSError, ValueError):
            one = None
        if not isinstance(one, dict) or not one.get("id"):
            return self.not_found()
        data = ticket_state(one)
        body = page(render_ticket(data), data["generated_at"],
                    title="票 #%s" % data["ticket_id"], prefix="/")
        return self.send_body(body.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if self.gate():
            self.serve_post()

    def serve_post(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        raw = self.read_body()
        if path != "/api/answer":
            return self.send_body("找不到\n".encode("utf-8"),
                                  "text/plain; charset=utf-8", 404)
        if raw is None:
            return self.send_json({"ok": False, "error": "答案太長,沒有存"}, 400)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return self.send_json({"ok": False, "error": "body 不是一個 JSON 物件"}, 400)
        ident = str(payload.get("ticket") or "").strip()
        answer = payload.get("answer")
        if not ident or not isinstance(answer, str):
            return self.send_json({"ok": False, "error": "要有 ticket 與 answer 兩格"}, 400)
        row = append_answer(ident, answer)
        event.emit("decision.answered", root=root(), ticket=ident, chars=len(answer))
        return self.send_json({"ok": True, "ticket": ident, "saved_at": row["ts"],
                               "saved_time": row["ts"][11:16]})

    def read_body(self):
        """讀完整個 body,或者拒收。HTTP/1.1 是 keep-alive:沒讀完的那幾個 byte
        會被當成下一個請求的開頭,而那比一個 400 難查得多。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > ANSWER_MAX:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        self.send_body(body.encode("utf-8"), "application/json; charset=utf-8", code)

    def send_body(self, body, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


def make_server(port):
    """只綁 127.0.0.1。埠 0 = 讓 OS 給一個 —— 測試用的就是這一條,而**埠寫死的
    測試會在別人剛好佔著那個埠的那天紅**,那種紅跟真的壞掉長得一樣。"""
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv):
    global KEY
    try:
        KEY = load_key(root())
    except ValueError as err:
        sys.stderr.write(str(err) + "\n")
        return 2
    port = int(event.config(root()).get("port") or DEFAULT_PORT)
    if "--port" in argv:
        at = argv.index("--port")
        if at + 1 >= len(argv):
            sys.stderr.write("board: --port 少了值\n")
            return 2
        port = int(argv[at + 1])
    httpd = make_server(port)
    sys.stderr.write("board: http://127.0.0.1:%d/\n" % httpd.server_address[1])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
