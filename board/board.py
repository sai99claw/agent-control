#!/usr/bin/env python3
"""控制台。只讀票與事件,不猜 — D-003。

    python3 board/board.py            # 127.0.0.1:<board/config.json 的 port>
    python3 board/board.py --port 0   # OS 給一個埠(測試用)

五個畫面,一頁裡五段:
 ① 票與依賴 —— 狀態、凍結原因、`base_sha` 是不是還是主線的祖先
 ② agent 時間線 —— 從事件重建:誰、什麼票、第幾次、開始/結束、現在還活著嗎
 ③ 落地佇列與最近的 land 結果
 ④ 決策收件匣 —— 可以填答案,寫進 `board/answers.jsonl` 並發 `decision.answered`
 ⑤ 哪些保證還只在演練裡成立 —— 讀 `docs/REHEARSAL.md`

## token 用量顯示「未知」,不估
`docs/DESIGN.md` §15:用量要區分「provider 回報」「估計」「無法取得」,而
**未知不可顯示為零**。Claude Code 不給這份資料,所以這裡寫「未知」並說出為什麼 ——
一個估出來的數字與一個量出來的數字在畫面上長得一樣,而只有後者可以拿來做決定。

## 它只寫一種東西
使用者在收件匣按下的那顆鈕,append 一行進 `answers.jsonl`,並發一筆事件。
**不改票的狀態、不派工** —— 一顆會順手改狀態的儲存鈕,是一顆不敢按的儲存鈕。
"""

import html
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import event    # noqa: E402
import metrics  # noqa: E402
import ticket   # noqa: E402

DEFAULT_PORT = 18905
ANSWER_MAX = 64 * 1024
REHEARSAL_REL = os.path.join("docs", "REHEARSAL.md")
LAND_KINDS = ("land.start", "land.refused", "land.pass", "land.fail")
QUEUE_STATES = ("InReview", "IntegrationQueued", "Integrating")
ATTEMPT_START = "ticket.attempt.start"
ATTEMPT_END = ("ticket.attempt.done", "ticket.attempt.failed")
SESSION_START = "session.start"
SESSION_END = "session.end"
UNKNOWN = "未知"


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


def rehearsal_rows():
    """`docs/REHEARSAL.md` 的表:**哪些保證還只在演練裡成立。**

    演練綠只證明「在受控環境裡這樣寫是對的」;新行為第一次真的被觸發的那一刻,
    才是它第一次在真的路徑上成立 —— 而那兩件事在畫面上長得一樣。確認過的那一列
    由人從檔案裡刪掉,所以這一段是空的才是好消息。
    """
    path = os.path.join(root(), REHEARSAL_REL)
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue
        if cells[0].startswith("工具") or cells[0].startswith("保證"):
            continue
        rows.append(cells)
    return rows


def latest_answers():
    return ticket.latest_answers()


def state():
    rows = event.read_events(root())
    tickets = ticket.load_all()
    ask = ancestor_cache()
    for one in tickets:
        one["_ancestor"] = ask(one.get("base_sha"))
    return {"generated_at": now_text(),
            "tickets": tickets,
            "events": rows,
            "runs": agent_runs(rows),
            "sessions": sessions(rows),
            "answers": latest_answers(),
            "rehearsal": rehearsal_rows(),
            # 每票一筆,鍵是票號。**這一格不是 `tokens` 的替身** —— 抬頭那一格問的
            # 是「這個看板自己知不知道整體用量」(不知道),這一格答的是「每一張票
            # 各自量到了什麼」,而其中一部分的答案照樣是「未知」。
            "metrics": metrics.collect(root()),
            "tokens": UNKNOWN}


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
       ("land", "落地"), ("inbox", "決策收件匣"), ("rehearsal", "只在演練裡成立"))


def page(body, generated):
    links = "".join("<a href=\"#%s\">%s</a>" % (esc(href), esc(label))
                    for href, label in NAV)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-Hant\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>控制台</title>\n"
        "<link rel=\"stylesheet\" href=\"board.css\">\n"
        "</head>\n<body>\n"
        "<header><h1>控制台</h1>"
        "<div class=\"stampline\">讀於 %s ・ 每 30 秒自動刷新 ・ token 用量:%s</div>"
        "</header>\n<nav>%s</nav>\n%s"
        "<script src=\"board.js\"></script>\n</body>\n</html>\n"
        % (esc(generated), esc(UNKNOWN), links, body))


def ancestor_cell(value):
    if value is True:
        return "<span class=\"ok\">是</span>"
    if value is False:
        return "<span class=\"bad\">**不是了**,閘門的綠已經過期</span>"
    return "<span class=\"warn\">問不出來</span>"


def red_cell(row):
    """`紅(env/product)`。**三個數字一起印** —— 只印總數的話,「環境爛了四次」與
    「程式錯了四次」長得一樣,而那兩件事的下一步完全不同(一個修機器,一個修 code)。
    """
    if not row:
        return esc(UNKNOWN)
    return "%s (%s/%s)" % (esc(row.get("red_runs", 0)), esc(row.get("env_runs", 0)),
                           esc(row.get("product_runs", 0)))


def render_tickets(data):
    out = ["<section id=\"tickets\"><h2>① 票與依賴</h2>"]
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
               "<th>base_sha 還是主線祖先?</th><th>返工輪</th>"
               "<th>紅(env/product)</th><th>token</th>"
               "<th>凍結</th><th>標題</th></tr>")
    numbers = data.get("metrics") or {}
    for one in tickets:
        deps = ", ".join("#%s" % esc(d.get("id", "")) for d in (one.get("depends_on") or []))
        frozen = one.get("frozen") or {}
        row = numbers.get(str(one.get("id", ""))) or {}
        out.append(
            "<tr><td class=\"id\">#%s</td>"
            "<td><span class=\"pill s-%s\">%s</span></td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td></tr>"
            % (esc(one.get("id", "")), esc(one.get("state", "")),
               esc(one.get("state", "")), esc(one.get("state_version", "")),
               deps or "—", ancestor_cell(one.get("_ancestor")),
               esc(row.get("fix_rounds", UNKNOWN)), red_cell(row),
               esc(row.get("tokens", UNKNOWN)),
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
        out.append("<p class=\"empty\">沒有等你裁決的事</p></section>")
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
    out.append("</section>")
    return "".join(out)


def render_rehearsal(data):
    out = ["<section id=\"rehearsal\"><h2>⑤ 哪些保證還只在演練裡成立</h2>"]
    rows = data["rehearsal"]
    if rows is None:
        out.append("<p class=\"empty\">沒有 <code>%s</code> 這份檔</p>" % esc(REHEARSAL_REL))
    elif not rows:
        out.append("<p class=\"empty\">表是空的 —— 沒有欠確認的真跑</p>")
    else:
        out.append("<table>")
        for cells in rows:
            out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in cells))
        out.append("</table>")
    out.append("<p class=\"empty\">演練綠只證明「在受控環境裡這樣寫是對的」。"
               "新行為第一次真的被觸發時,誰在場誰確認一眼,然後把那一列從 "
               "<code>%s</code> 刪掉。</p></section>" % esc(REHEARSAL_REL))
    return "".join(out)


def render(data):
    return ("<main>" + render_tickets(data) + render_timeline(data)
            + render_land(data) + render_inbox(data) + render_rehearsal(data)
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


class Handler(BaseHTTPRequestHandler):
    server_version = "control-board"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s board %s %s\n" % (
            datetime.now().isoformat(timespec="seconds"),
            self.command, urlparse(self.path).path))

    def do_GET(self):
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
        return self.send_body("找不到\n".encode("utf-8"),
                              "text/plain; charset=utf-8", 404)

    def do_POST(self):
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
