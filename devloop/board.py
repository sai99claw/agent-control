#!/usr/bin/env python3
"""board : the dashboard. Reads the pool; never writes it.

    python3 devloop/board.py html [--out board.html] [--query tab=dag]   # one page, to a file or stdout
    python3 devloop/board.py serve [--port 8765] [--host 127.0.0.1] [--key-file F]

What it shows (VISION 「看板與統計」): the four numbers, every ticket (where it is, who works on
it, what it waits for), the integration branches, what waits for the user, and memory.
A number it cannot know is shown as 「未知」, never as 0.

With --key-file a browser opens the board once with `?key=`; the board trades it for an HttpOnly,
SameSite=Strict cookie and redirects to the same page without the key, so the key never appears in
a URL or a page. Scripts send header `X-Board-Key` instead. The page then gets inputs. They are another front-end to the main session's own entry, nothing more
(D-083): a new request runs `ticket_creator.py --request-file ... --via web`; each 要你判 card's buttons
(POST act) run the command the main session would (ticket_creator.py --revise N --from user --decision ...,
tickets.py ready|cancel|rerun gN, tickets.py ack PAGE) with --by (the trusted user's name, else 'user'),
acknowledge the card's page, and the card shows how its last action ended. Only the opener's path writes
tickets; web text first passes the injection check of prompts/web-guard.md. Unsent text is kept in the
viewer's browser only.

Behind a local reverse proxy (board.trusted_user_header + board.allowed_users, D-091 item 4), a request
from a loopback peer to a board listening on loopback whose header names a listed user gets the
inputs without the key.
"""

import argparse
import calendar
import hashlib
import hmac
import html
import os
import re
import statistics
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dag as dagmod  # noqa: E402
from core import memory, osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.pool import shown_state  # noqa: E402

UNKNOWN = "未知"
STATE_TEXT = {"ready": "待派", "running": "做中", "merged": shown_state("merged"), "landed": shown_state("landed"),
              "stuck": "卡住", "cancelled": "取消"}
GROUP_TEXT = {"open": "收票中", "pr": "PR(待驗)", "fixing": "修紅中", "verified": "已驗,待落地",
              "landed": "已落地", "stuck": "卡住", "cancelled": "取消"}
REASON_TEXT = {"quota": "額度用完"}


def group_state_text(g):
    """The state cell of a group's row; a fixer waiting for a quota reset says so and until when."""
    text = GROUP_TEXT.get(g.get("state"), g.get("state"))
    waiting = g.get("waiting") or {}
    if waiting.get("reason") == "quota":
        text = "%s(%s,等到 %s)" % (text, REASON_TEXT["quota"], waiting.get("until") or "?")
    return text


# ---------------------------------------------------------------- numbers

def tokens(t):
    """(non-cache tokens, cache-read tokens), each None when nothing was recorded."""
    fresh = cached = None
    for row in t.get("cost") or []:
        for key in ("input_tokens", "output_tokens"):
            if isinstance(row.get(key), int):
                fresh = (fresh or 0) + row[key]
        if isinstance(row.get("cache_read_tokens"), int):
            cached = (cached or 0) + row["cache_read_tokens"]
    return fresh, cached


def cache_written(t):
    total = None
    for row in t.get("cost") or []:
        if isinstance(row.get("cache_write_tokens"), int):
            total = (total or 0) + row["cache_write_tokens"]
    return total


def minutes(t):
    start = end = None
    for h in t.get("history") or []:
        if h.get("event") == "dispatched" and start is None:
            start = h.get("at")
        if h.get("event") == "landed":
            end = h.get("at")
    if not start or not end:
        return None
    return round((osx.age(start) - osx.age(end)) / 60.0, 1)


def first_pass(t):
    revised = any(h.get("event") == "revised" for h in t.get("history") or [])
    return int(t.get("round") or 0) == 1 and not revised


def metrics(snap):
    landed = [t for t in snap.tickets.values() if t.get("state") == "landed"]
    finished = [t for t in snap.tickets.values() if t.get("state") in ("landed", "stuck")]
    tok = [sum(x for x in tokens(t) if x is not None) for t in landed if any(x is not None for x in tokens(t))]
    mins = [m for m in (minutes(t) for t in landed) if m is not None]
    asks = [p for p in snap.inbox.values() if p.get("kind") in ("decision", "env")]
    waiting = [p for p in snap.inbox.values() if not p.get("acked")]
    stuck = [t for t in snap.tickets.values() if t.get("state") == "stuck"]
    return {
        "tokens": "%s" % format(int(statistics.median(tok)), ",") if tok else UNKNOWN,
        "minutes": "%s 分" % statistics.median(mins) if mins else UNKNOWN,
        "first_pass": "%d / %d" % (sum(first_pass(t) for t in landed), len(landed)) if landed else UNKNOWN,
        "asks": "%.2f" % (len(asks) / float(len(finished))) if finished else UNKNOWN,
        "waiting": "%d 頁 + %d 張卡住" % (len(waiting), len(stuck)),
    }


# ---------------------------------------------------------------- stuck categories

CATEGORY_TEXT = {"quota": "額度用完", "slot": "等機器名額", "timeout": "逾時", "red": "紅燈",
                 "decision": "要你決定", "env": "環境", "conflict": "合併衝突", "other": "其他"}


def merge_failed(text):
    """True when a stuck text says a merge of a branch failed (conflict)."""
    for lead in ("merging the latest ", "merging the verifier's branch "):
        i = text.find(lead)
        if i >= 0 and " failed (" in text[i + len(lead):]:
            return True
    return False


def ticket_category(t):
    """Why a stuck or quota-waiting ticket is held, as a CATEGORY_TEXT key; None when it is neither."""
    if (t.get("waiting") or {}).get("reason") == "quota":
        return "quota"
    if t.get("state") != "stuck":
        return None
    st = t.get("stuck") or {}
    reason = st.get("reason")
    text = st.get("text") or ""
    if t.get("feedback"):
        text = "%s\n%s" % (text, t["feedback"][-1])
    if reason in ("quota", "env", "decision"):
        return reason
    if reason == "hard_block":
        return "decision"
    if reason == "timeout":
        return "slot" if "machine slot" in text else "timeout"
    if reason == "rounds":
        if merge_failed(text):
            return "conflict"
        for row in t.get("cost") or []:
            if row.get("round") == t.get("round") and row.get("outcome") in ("out of quota", "quota"):
                return "quota"
        return "slot" if "no machine slot came free" in text else "red"
    return "other"


GROUP_EVENT_CATEGORY = (("red after", "red"), ("fixer needs a decision", "decision"),
                        ("main kept moving", "conflict"), ("no ticket of the group got merged", "member"))


def group_category(snap, g):
    """Why a group is held, as a CATEGORY_TEXT key; None when nothing holds it."""
    if (g.get("waiting") or {}).get("reason") == "quota":
        return "quota"
    if g.get("state") != "stuck":
        pages = [p for p in snap.inbox.values() if not p.get("acked") and str(p.get("ref")) == str(g.get("id"))]
        for kind in ("env", "decision"):
            if any(p.get("kind") == kind for p in pages):
                return kind
        return None
    for h in reversed(g.get("history") or []):
        text = str(h.get("event") or h.get("text") or "")
        for prefix, cat in GROUP_EVENT_CATEGORY:
            if text.startswith(prefix):
                if cat != "member":
                    return cat
                for ident in sorted(g.get("members") or [], key=lambda i: int(i)):
                    t = snap.tickets.get(str(ident))
                    if t and t.get("state") == "stuck":
                        return ticket_category(t)
                return "other"
    return "other"


# ---------------------------------------------------------------- page

def e(text):
    return html.escape("" if text is None else str(text))


TABS = (
    ("decide", "要你判", lambda cfg, snap, key, query, now: render_decide_tab(cfg, snap, key, query, now)),
    ("live", "進行中", lambda cfg, snap, key, query, now: render_live_tab(cfg, snap, key, query, now)),
    ("tickets", "票", lambda cfg, snap, key, query, now: render_tickets_tab(cfg, snap, key, query, now)),
    ("dag", "DAG", lambda cfg, snap, key, query, now: render_dag_tab(cfg, snap, query, now)),
    ("tokens", "Token", lambda cfg, snap, key, query, now: render_tokens_tab(cfg, snap, query, now)),
    ("host", "主機", lambda cfg, snap, key, query, now: render_host_tab(cfg, snap, query, now)),
)


def clock(now):
    """`now` (seconds since the epoch) in the pool's timestamp format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def shown_tab(query, default="tickets"):
    """The name of the tab a query asks for; anything unknown is `default`."""
    wanted = (query or {}).get("tab")
    return wanted if wanted in [name for name, _, _ in TABS] else default


def render_nav(shown, waiting=0):
    """The tab links; 要你判 carries the number of cards when there is any."""
    links = []
    for name, label, _ in TABS:
        current = ' aria-current="page"' if name == shown else ""
        if name == "decide" and waiting:
            label = "%s (%d)" % (label, waiting)
        links.append('<a href="?tab=%s"%s>%s</a>' % (e(name), current, e(label)))
    return '<nav class="tabs">%s</nav>' % "".join(links)


def render_metrics(snap):
    m = metrics(snap)
    out = ['<section><h2>四個指標</h2><div class="cards">']
    for label, name, note in (("每張落地票的 token(中位,含快取讀)", "tokens", "越少越好"),
                              ("每張落地票的時間(中位)", "minutes", "派工到落地"),
                              ("一次就過", "first_pass", "第 1 輪就合進、沒改過票"),
                              ("每張票要你介入幾次", "asks", "決定頁與環境頁 ÷ 落地或卡住的票"),
                              ("等你處理的", "waiting", "未讀頁 + 卡住的票")):
        out.append('<div class="card"><div class="k">%s</div><div class="v">%s</div><div class="n">%s</div></div>'
                   % (e(label), e(m[name]), e(note)))
    out.append("</div></section>")
    return "\n".join(out)


def render(cfg, snap, key=None, query=None, now=None):
    """The page: header and the four numbers on every tab, then the tab `query` asks for (?tab=)."""
    now = time.time() if now is None else now
    wanted = detail_wanted(query)
    if wanted:
        shown, body = "tickets", render_detail(snap, wanted)
    else:
        shown = shown_tab(query, decide_default(snap, query, now))
        body = dict((name, fn) for name, _, fn in TABS)[shown](cfg, snap, key, query, now)
    waiting = len(waiting_cards(snap, now))
    title = "devloop · " + cfg.name
    doc_title = ("(%d) " % waiting if waiting else "") + title
    return "\n".join([PAGE_HEAD % {"title": e(title), "doc_title": e(doc_title), "now": e(clock(now))},
                      render_metrics(snap), render_nav(shown, waiting),
                      '<div id="tab-%s" class="tab">' % e(shown), body, "</div>",
                      PAGE_SCRIPT + "</main></body></html>"])


# ---------------------------------------------------------------- tab: 要你判

DECIDE_STYLE = ("<style>section.wait{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);"
                "border-radius:8px;padding:10px 12px}section.wait p.want{margin:0;font-weight:700}"
                "section.wait span.cat{color:var(--quiet);font-size:12px}section.wait p.last-act{margin:6px 0 0;font-size:12.5px}"
                "form.act{display:grid;gap:6px;margin-top:8px}</style>")
QUOTA_GRACE = 300
TICKET_PARAMS = ("ticket", "group", "state", "cat", "q", "done")


def pool_seconds(stamp):
    """A pool timestamp ('YYYY-MM-DDTHH:MM:SSZ') as seconds since the epoch; None when missing or malformed."""
    try:
        return calendar.timegm(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def page_subject(snap, p):
    """('ticket', id), ('group', id) or ('other', ref): what an inbox page is about."""
    ref = "" if p.get("ref") is None else str(p.get("ref"))
    if ref in snap.tickets:
        return "ticket", ref
    if ref in snap.groups:
        return "group", ref
    return "other", ref


def card_want(snap, kind, subject, ref=None):
    """The one line a card asks of the user."""
    if kind == "ticket":
        if (snap.tickets[subject] or {}).get("state") == "stuck":
            return "#%s 卡住了:照你的話改、再試一次或取消" % subject
        return "#%s 有一頁要你看" % subject
    if kind == "group":
        g = snap.groups[subject] or {}
        if g.get("state") == "stuck":
            return "%s 卡住了:帶著你的話重驗,或取消這組" % subject
        if g.get("paused"):
            return "%s 的 regression 沒跑成:環境修好後重跑" % subject
        return "%s 有一頁要你看" % subject
    if ref == "request":
        return "開題者開票前要你決定:到「票」分頁交代新的事時一起回答"
    return "有一頁要你看(%s)" % ref


def card_details(snap, kind, subject):
    """(category text or None, feedback text or None) of a card about a ticket or group."""
    if kind == "ticket":
        t = snap.tickets[subject] or {}
        cat = ticket_category(t)
        feedback = (t.get("feedback") or [None])[-1]
        if feedback is None and t.get("state") == "stuck":
            feedback = (t.get("stuck") or {}).get("text")
        return (CATEGORY_TEXT.get(cat) if cat else None), feedback
    if kind == "group":
        g = snap.groups[subject] or {}
        cat = group_category(snap, g)
        return (CATEGORY_TEXT.get(cat) if cat else None), (g.get("last_regression") or None)
    return None, None


def waiting_cards(snap, now):
    """One dict per thing that waits for the user, newest first: kind, subject, page ('' when none),
    at (when it began to wait), want, cat, feedback, text (the page's text or None), timeline (href or None)."""
    cards = []
    about = set()
    for name, p in snap.inbox.items():
        if p.get("acked") is True or p.get("kind") not in ("decision", "env"):
            continue
        kind, subject = page_subject(snap, p)
        about.add((kind, subject))
        cat, feedback = card_details(snap, kind, subject)
        cards.append({"kind": kind, "subject": subject, "page": name, "at": p.get("at"),
                      "want": card_want(snap, kind, subject, subject), "cat": cat, "feedback": feedback,
                      "text": "" if p.get("text") is None else str(p.get("text"))})
    for ident, t in snap.tickets.items():
        if t.get("state") == "stuck" and ("ticket", ident) not in about:
            cat, feedback = card_details(snap, "ticket", ident)
            cards.append({"kind": "ticket", "subject": ident, "page": "", "at": (t.get("stuck") or {}).get("at"),
                          "want": card_want(snap, "ticket", ident), "cat": cat, "feedback": feedback, "text": None})
    for gid, g in snap.groups.items():
        if (g.get("state") == "stuck" or g.get("paused")) and ("group", gid) not in about:
            if g.get("paused"):
                at = (g.get("paused") or {}).get("at")
            else:
                at = ((g.get("history") or [{}])[-1] or {}).get("at")
            cat, feedback = card_details(snap, "group", gid)
            cards.append({"kind": "group", "subject": gid, "page": "", "at": at,
                          "want": card_want(snap, "group", gid), "cat": cat, "feedback": feedback, "text": None})
    held = [("ticket", i, t) for i, t in snap.tickets.items() if t.get("state") == "running"]
    held += [("group", i, g) for i, g in snap.groups.items() if g.get("state") == "fixing"]
    for kind, ident, row in held:
        waiting = row.get("waiting") or {}
        until = pool_seconds(waiting.get("until"))
        if waiting.get("reason") != "quota" or until is None or now - until <= QUOTA_GRACE:
            continue
        cat = ticket_category(row) if kind == "ticket" else group_category(snap, row)
        cards.append({"kind": "quota", "subject": ident, "page": "", "at": waiting.get("until"),
                      "want": "%s 等額度重置,已過了 %s 還在等" % ("#" + ident if kind == "ticket" else ident,
                                                             waiting.get("until")),
                      "cat": CATEGORY_TEXT.get(cat) if cat else None, "feedback": waiting.get("text"), "text": None,
                      "of": kind})
    for c in cards:
        of = c.get("of", c["kind"])
        c["timeline"] = None if of == "other" else "?%s=%s" % ("ticket" if of == "ticket" else "group",
                                                             quote(str(c["subject"])))

    def order(c):
        at = pool_seconds(c["at"])
        return (at is None, -(at or 0), str(c["subject"]))
    return sorted(cards, key=order)


def decide_default(snap, query, now):
    """The tab a page shows when its query names no known tab: 票 when a 票 filter is asked, else 要你判 when
    something waits, else 進行中."""
    query = query or {}
    if any(query.get(k) is not None for k in TICKET_PARAMS):
        return "tickets"
    if not waiting_cards(snap, now):
        return "live"
    return "decide"


# ---------------------------------------------------------------- tab: 進行中

LIVE_STYLE = ("<style>#tab-live table.live td{vertical-align:top}#tab-live td.detail pre{max-height:20em;overflow:auto;"
              "font-size:12px;margin:4px 0 0}#tab-live tr[data-state=ended]{color:var(--quiet)}"
              "#tab-live a.refresh{float:right;font-size:13px}</style>")
LIVE_TAIL_LINES = 20
LIVE_TAIL_BYTES = 1 << 20       # a log is read from at most this far before its end
LIVE_AGENT_STATE = {"running": "執行中", "slot": "等機器名額"}
LIVE_STAGE_TEXT = {"slot": "等機器名額", "machine": "等整台機器", "preflight": "preflight", "full": "full"}
LIVE_ENDED = "結束了,沒有交件"
LIVE_NO_AGENT = "沒有 agent 在跑"
LIVE_STAGE_MARK = "devloop-stage:"


def live_clock(epoch):
    """A row's start time in local time: 'MM-DD HH:MM:SS'."""
    return time.strftime("%m-%d %H:%M:%S", time.localtime(epoch))


def live_elapsed(seconds):
    """'<m> 分' below an hour (negative counts as 0), '<h> 小時 <m> 分' from an hour on."""
    s = max(0, int(seconds))
    if s < 3600:
        return "%d 分" % (s // 60)
    return "%d 小時 %d 分" % (s // 3600, s % 3600 // 60)


def live_log_lines(path):
    """The lines of a log's last LIVE_TAIL_BYTES; None when it cannot be read."""
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LIVE_TAIL_BYTES))
            data = fh.read()
    except OSError:
        return None
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > LIVE_TAIL_BYTES and lines else lines


def live_tail(path):
    """The last LIVE_TAIL_LINES lines of a log as one text; '' when it cannot be read."""
    lines = live_log_lines(path)
    return "\n".join(lines[-LIVE_TAIL_LINES:]) if lines else ""


def live_release_step(path):
    """The text after 'devloop-stage:' on the log's last line that starts with it; None when there is none."""
    for line in reversed(live_log_lines(path) or []):
        if line.startswith(LIVE_STAGE_MARK):
            return line[len(LIVE_STAGE_MARK):].strip()
    return None


def live_pid(rec):
    return (rec.get("stamp") or {}).get("pid")


def live_model(rec, command=True):
    """'<command> · <model> · <effort>' of a record, leaving out what it does not have."""
    parts = [rec.get("command")] if command else []
    parts += [rec.get("model"), rec.get("effort")]
    return " · ".join(str(p) for p in parts if p not in (None, ""))


def live_agent_state_text(agent):
    if agent.get("state") == "quota":
        until = pool_seconds(agent.get("until"))
        return "額度用完,到 %s" % (live_clock(until) if until is not None else "?")
    return LIVE_AGENT_STATE.get(agent.get("state"), str(agent.get("state")))


def live_ticket_text(snap, ticket):
    title = (snap.tickets.get(str(ticket)) or {}).get("title")
    return "#%s %s" % (ticket, title or "")


def live_round(rec):
    return "第 %s 輪" % rec.get("round") if rec.get("round") is not None else ""


def live_what(snap, kind, rec):
    """The td.what text of a record's row."""
    if kind == "opener":
        if rec.get("revise") is not None:
            return "改票 #%s" % rec.get("revise")
        return "開題:%s" % rec.get("title") if rec.get("title") is not None else "開題"
    if kind == "agent":
        parts = [rec.get("role")]
        if rec.get("ticket") is not None:
            parts.append(live_ticket_text(snap, rec.get("ticket")))
        elif rec.get("group") is not None:
            parts.append(rec.get("group"))
        parts.append(live_round(rec))
        return " ".join(str(p) for p in parts if p not in (None, ""))
    if kind == "issuer":
        return " ".join(p for p in (live_ticket_text(snap, rec.get("ticket")), live_round(rec)) if p)
    if kind == "regression":
        g = snap.groups.get(str(rec.get("group")))
        if g is None:
            which = UNKNOWN
        else:
            n = g.get("fix_round") or 0
            which = "修第 %s 輪後" % n if n >= 1 else "第一次"
        return "%s %s" % (rec.get("group"), which)
    if kind == "fixer":
        return "%s 第 %s/%s 輪" % (rec.get("group"), rec.get("round"), rec.get("max_rounds"))
    if kind == "release":
        return "%s %s" % (rec.get("group"), str(rec.get("sha") or "")[:12])
    return str(kind)


def live_rows(cfg, snap):
    """One dict per row of the tab (read only: records, their status and their logs)."""
    from core import inflight
    recs = [(name, rec, inflight.status(rec)) for name, rec in inflight.records(cfg)]
    recs = [(n, r, s) for n, r, s in recs if s != "handed_in"]
    running = [(n, r) for n, r, s in recs if s == "running"]
    agents = [(n, r) for n, r in running if r.get("kind") == "agent"]

    def children(rec):
        pid = live_pid(rec)
        return [(n, a) for n, a in agents if pid is not None and a.get("parent") == pid]

    def newest(kids):
        if not kids:
            return None
        return max(kids, key=lambda na: (pool_seconds((na[1].get("stamp") or {}).get("started")) or 0, na[0]))[1]

    owned = set()
    for _, r in running:
        if r.get("kind") in ("opener", "fixer"):
            owned.update(n for n, _ in children(r))
    rows = []
    for name, rec, status in recs:
        kind = rec.get("kind")
        row = {"name": name, "kind": kind, "rec": rec, "what": live_what(snap, kind, rec),
               "model": "", "attrs": {}, "run_dir": None, "log": None, "tail": False}
        if status == "ended":
            row.update(state="ended", text=LIVE_ENDED)
            if kind == "agent":
                row.update(model=live_model(rec), run_dir=rec.get("run_dir"), log=rec.get("log"), tail=True)
            rows.append(row)
            continue
        if kind == "agent":
            if name in owned:
                continue
            row.update(state=rec.get("state"), text=live_agent_state_text(rec), agent=rec)
        elif kind == "opener":
            agent = newest(children(rec))
            if agent is None:
                row.update(state="running", text=LIVE_AGENT_STATE["running"], model=live_model(rec, command=False))
                if rec.get("model"):
                    row["attrs"]["data-model"] = rec.get("model")
            else:
                row.update(state="slot" if agent.get("state") == "slot" else "running",
                           text=live_agent_state_text(agent), agent=agent)
        elif kind == "issuer":
            if children(rec):
                continue
            row.update(state="running", text=LIVE_NO_AGENT)
        elif kind == "regression":
            stage = rec.get("stage")
            row.update(state="slot" if stage in ("slot", "machine") else "running",
                       text=LIVE_STAGE_TEXT.get(stage, str(stage)))
        elif kind == "fixer":
            agent = newest(children(rec))
            if agent is None:
                row.update(state="running", text=LIVE_NO_AGENT)
            else:
                text = live_agent_state_text(agent)
                row.update(state=agent.get("state"), agent=agent,
                           text=text if agent.get("state") == "quota" else "agent " + text)
        elif kind == "release":
            row.update(state="running", text=live_release_step(rec.get("log")) or LIVE_AGENT_STATE["running"],
                       log=rec.get("log"), tail=True)
        else:
            row.update(state="running", text=LIVE_AGENT_STATE["running"])
        agent = row.pop("agent", None)
        if agent is not None:
            row.update(model=live_model(agent), run_dir=agent.get("run_dir"), log=agent.get("log"), tail=True)
            for attr in ("model", "command"):
                if agent.get(attr):
                    row["attrs"]["data-" + attr] = agent.get(attr)
        rows.append(row)
    for row in rows:
        row["started"] = pool_seconds((row["rec"].get("stamp") or {}).get("started"))
    rows.sort(key=lambda r: (r["state"] == "ended", r["started"] is None, r["started"] or 0, r["name"]))
    return rows


def render_live_row(row, now, slots_text):
    attrs = [("data-kind", row["kind"]), ("data-state", row["state"]), ("data-pid", live_pid(row["rec"])),
             ("data-started", row["started"])] + sorted(row["attrs"].items())
    if slots_text is not None:
        attrs.append(("data-slots", slots_text))
    started = row["started"]
    detail = []
    if row["run_dir"]:
        detail.append('<code class="run-dir">%s</code>' % e(row["run_dir"]))
    if row["tail"]:
        detail.append("<details><summary>log</summary><pre>%s</pre></details>" % e(live_tail(row["log"])))
    cells = [("what", e(row["what"])), ("state", e(row["text"])), ("model", e(row["model"])),
             ("start", e(live_clock(started)) if started is not None else ""),
             ("elapsed", e(live_elapsed(now - started)) if started is not None else ""),
             ("detail", "".join(detail))]
    if slots_text is not None:
        cells.append(("slots", e("名額 " + slots_text)))
    return "<tr %s>%s</tr>" % (" ".join('%s="%s"' % (k, e("" if v is None else v)) for k, v in attrs),
                               "".join('<td class="%s">%s</td>' % c for c in cells))


def render_live_tab(cfg, snap, key, query, now):
    """One row per running opener, agent, agent-less issuer, regression, fixer and release (and per
    record whose process ended without handing in), oldest first. Writes and creates nothing."""
    from core import host as hostmod
    rows = live_rows(cfg, snap)
    used = hostmod.slots_in_use(cfg)
    slots_text = None if used is None else "%d/%d" % (used, cfg.machine_slots)
    out = [LIVE_STYLE, '<section><a class="refresh" href="?tab=live">重新整理</a><h2>進行中</h2>']
    if not rows:
        out.append('<p class="quiet">沒有在跑的。</p>')
    out.append('<div class="scroll"><table class="live">')
    out.extend(render_live_row(r, now, slots_text) for r in rows)
    out.append("</table></div></section>")
    return "\n".join(out)


ACT_LABELS = {"revise": "照我的話改", "retry": "再試一次", "cancel": "取消", "group-ready": "帶著我的話重驗",
              "group-cancel": "取消這組", "rerun": "環境修好了,重跑", "ack": "知道了"}


def card_actions(snap, c):
    """The button values a card offers, in order: by the card's kind and its subject's state, then ack
    when the card has a page. A quota card has none."""
    if c["kind"] == "quota":
        return []
    out = []
    if c["kind"] == "ticket" and (snap.tickets.get(c["subject"]) or {}).get("state") == "stuck":
        out = ["revise", "retry", "cancel"]
    elif c["kind"] == "group":
        g = snap.groups.get(c["subject"]) or {}
        if g.get("state") == "stuck":
            out = ["group-ready", "group-cancel"]
        elif g.get("paused"):
            out = ["rerun"]
    if c["page"]:
        out.append("ack")
    return out


def render_act_form(c, actions):
    """One card's form: POST act with its subject and page, a text box kept as a draft, and its buttons."""
    if not actions:
        return ""
    buttons = " ".join('<button name="action" value="%s">%s</button>' % (e(a), e(ACT_LABELS[a])) for a in actions)
    return ('<form method="post" action="act" class="act">'
            '<input type="hidden" name="subject" value="%s"><input type="hidden" name="page" value="%s">'
            '<textarea name="text" maxlength="8000" rows="2" data-draft="%s" '
            'placeholder="你的話(照我的話改時必填;重驗時當作備註)"></textarea><div>%s</div></form>'
            % (e(c["subject"]), e(c["page"]), e(act_draft(c["subject"], c["page"])), buttons))


def last_act_text(meta, rc, tail):
    """The last-act line of a card: what was pressed, by whom, and how it ended."""
    status = {None: "處理中", "0": "完成", "3": "要你決定(看收件匣)"}.get(rc)
    if status is None:
        status = "沒有執行(rc %s):%s" % (rc, tail)
    who = " · %s" % meta["by"] if meta.get("by") else ""
    return "上次的動作:%s%s · %s" % (meta.get("what") or "", who, status)


def last_acts(cfg):
    """{subject: last-act text} for the newest runs/web job of each subject a card's button started."""
    out = {}
    for name in job_names(cfg):
        meta, rc, tail = job_state(os.path.join(jobs_dir(cfg), name))
        subject = meta.get("subject")
        if subject is None or str(subject) in out:
            continue
        out[str(subject)] = last_act_text(meta, rc, tail)
    return out


def render_card(c, actions=(), last=None):
    parts = ['<section class="wait" data-kind="%s" data-subject="%s" data-page="%s">'
             % (e(c["kind"]), e(c["subject"]), e(c["page"])),
             '<p class="want">%s</p>' % e(c["want"])]
    if c["cat"]:
        parts.append('<span class="cat">%s</span>' % e(c["cat"]))
    if c["feedback"]:
        parts.append('<pre class="feedback">%s</pre>' % e(c["feedback"]))
    if c["text"] is not None:
        parts.append('<pre class="page-text">%s</pre>' % e(c["text"]))
    if c["timeline"]:
        parts.append('<a class="timeline" href="%s">時間軸</a>' % e(c["timeline"]))
    if last:
        parts.append('<p class="last-act">%s</p>' % e(last))
    parts.append(render_act_form(c, actions))
    parts.append("</section>")
    return "".join(parts)


def render_decide_tab(cfg, snap, key, query, now):
    """`key` true means the viewer may submit (as for the request form): each card then gets its buttons."""
    cards = waiting_cards(snap, now)
    out = [DECIDE_STYLE, "<section><h2>要你判</h2>"]
    if not cards:
        out.append('<p class="quiet">沒有要你判的事。</p>')
    last = last_acts(cfg) if cards else {}
    out.extend(render_card(c, card_actions(snap, c) if key else (), last.get(str(c["subject"]))) for c in cards)
    out.append("</section>")
    return "\n".join(out)


# ---------------------------------------------------------------- tab: 票

FILTER_STATES = ("ready", "running", "merged", "landed", "stuck", "cancelled")
DONE_STATES = ("landed", "cancelled")
FILTER_STYLE = ("<style>form.filters{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}"
                "form.filters label{display:flex;gap:6px;align-items:center}"
                "form.filters select,form.filters input[type=search]{font:inherit;padding:4px 8px;"
                "border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}"
                "</style>")


def ticket_filters(query):
    """The 票 tab's filters from the page query: state, group, cat, q (stripped) and done (bool)."""
    query = query or {}
    return {"state": query.get("state") or "", "group": query.get("group") or "", "cat": query.get("cat") or "",
            "q": (query.get("q") or "").strip(), "done": query.get("done") == "1"}


def ticket_shown(t, f):
    """True when ticket t passes filters f (from ticket_filters); all given filters must hold."""
    state = t.get("state")
    if f["state"]:
        if state != f["state"]:
            return False
    elif state in DONE_STATES and not f["done"]:
        return False
    if f["group"] == "none":
        if t.get("group"):
            return False
    elif f["group"] and t.get("group") != f["group"]:
        return False
    if f["cat"] and ticket_category(t) != f["cat"]:
        return False
    if f["q"]:
        q = f["q"]
        ident = q[1:] if q.startswith("#") else q
        if q.lower() not in str(t.get("title") or "").lower() and str(t.get("id")) != ident:
            return False
    return True


def group_shown(snap, g, f):
    """True when group g passes filters f; state and q do not apply to groups."""
    if g.get("state") in DONE_STATES and not f["done"]:
        return False
    if f["group"] and g.get("id") != f["group"]:
        return False
    if f["cat"] and group_category(snap, g) != f["cat"]:
        return False
    return True


def group_order(ident):
    digits = str(ident)[1:]
    return (0, int(digits), "") if digits.isdigit() else (1, 0, str(ident))


def render_filters(snap, f):
    """The filter form; its controls show the filters f."""
    def select(name, label, options):
        out = ['<label>%s <select name="%s">' % (e(label), e(name))]
        for value, text in options:
            chosen = " selected" if f[name] == value else ""
            out.append('<option value="%s"%s>%s</option>' % (e(value), chosen, e(text)))
        out.append("</select></label>")
        return "".join(out)

    groups = sorted(snap.groups, key=group_order)
    parts = ['<form method="get" class="filters"><input type="hidden" name="tab" value="tickets">',
             select("state", "狀態", [("", "全部")] + [(s, STATE_TEXT.get(s, s)) for s in FILTER_STATES]),
             select("group", "整合分支", [("", "全部")] + [(g, g) for g in groups] + [("none", "未分組")]),
             select("cat", "原因", [("", "全部")] + list(CATEGORY_TEXT.items())),
             '<label>找 <input type="search" name="q" value="%s" placeholder="標題或 #票號"></label>' % e(f["q"]),
             '<label><input type="checkbox" name="done" value="1"%s> 顯示已落地與已取消</label>'
             % (" checked" if f["done"] else ""),
             "<noscript><button>套用</button></noscript></form>"]
    return "".join(parts)


def render_tickets_tab(cfg, snap, key, query, now):
    out = [FILTER_STYLE]
    f = ticket_filters(query)
    if key:
        out.append("<section><h2>交代新的事</h2><form method='post' action='request' class='ask'>"
                   "<textarea name='text' data-draft='request' rows='5' maxlength='8000' required "
                   "placeholder='像跟主線說話一樣寫:要做什麼、為什麼'></textarea>"
                   "<div><button>交給開題者</button> <span class='quiet'>跟在主線下 ticket_creator.py "
                   "一樣;網頁來的文字會先做注入檢查。</span></div></form></section>")

    order = [i for i in dagmod.topo(snap.dag) + sorted(set(snap.tickets) - set(snap.dag)) if i in snap.tickets]
    order = [i for i in order if ticket_shown(snap.tickets[i], f)]
    out.append('<section><h2>票</h2>%s<p class="quiet">顯示 %d / 共 %d 張票</p>' % (
        render_filters(snap, f), len(order), len(snap.tickets)))
    out.append('<div class="scroll"><table><tr><th>票</th><th>狀態</th><th>輪</th>'
               '<th>整合分支</th><th>相依</th><th>一起驗</th><th>驗證者</th><th>覆核</th><th>token</th>'
               '<th>快取讀</th><th>快取寫</th><th>分鐘</th><th>估 token</th><th>說明</th><th>標題</th></tr>')
    for ident in order:
        t = snap.tickets[ident]
        fresh, cached = tokens(t)
        state = t.get("state")
        note = ""
        if state == "stuck":
            note = (t.get("stuck") or {}).get("text") or ""
            note = note[:120]
        elif state == "running" and (t.get("waiting") or {}).get("reason") == "quota":
            note = "%s,等到 %s 再跑" % (CATEGORY_TEXT["quota"], t["waiting"].get("until") or "?")
        elif state == "running":
            run = t.get("run") or {}
            note = "issuer 跑了 %d 分" % (osx.age(run.get("started")) / 60) if run.get("started") else "剛派出"
        elif t.get("feedback"):
            note = "上一輪:" + t["feedback"][-1][:120]
        category = ticket_category(t)
        if category:
            note = "%s:%s" % (CATEGORY_TEXT[category], note)
        written = cache_written(t)
        complex_ = t.get("complexity") == "complex" or (t.get("complexity") is None and t.get("needs_review"))
        out.append("<tr class='s-%s'><td>#%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                   "<td>%s</td><td>%s</td>"
                   "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                   "<td class='num'>%s</td><td>%s</td><td>%s</td></tr>" % (
                       e(state), e(ident), e(STATE_TEXT.get(state, state)), e(t.get("round", 0)),
                       e(t.get("target") or ""), e(" ".join("#" + d for d in dagmod.deps(snap.dag, ident))),
                       e({"together": "一起", "alone": "單獨"}.get(dagmod.verify(snap.dag, ident))),
                       e("有" if t.get("needs_verifier", True) else "無"), e("有" if complex_ else "無"),
                       e(UNKNOWN if fresh is None else format(fresh, ",")),
                       e(UNKNOWN if cached is None else format(cached, ",")),
                       e(UNKNOWN if written is None else format(written, ",")),
                       e(minutes(t) if minutes(t) is not None else ""),
                       e(format(t["token_estimate"], ",") if isinstance(t.get("token_estimate"), int) else UNKNOWN),
                       e(note), '<a href="?ticket=%s">%s</a>' % (quote(str(ident)), e(t.get("title")))))
    out.append("</table></div></section>")

    out.append('<section><h2>整合分支(一條 = 一個 PR)</h2><div class="scroll"><table><tr><th>組</th><th>狀態</th><th>原因</th>'
               '<th>分支</th><th>票</th><th>修補輪</th><th>最後一次 regression</th><th>發版</th></tr>')
    for g in sorted(snap.groups.values(), key=lambda g: int(g["id"][1:]), reverse=True):
        if not group_shown(snap, g, f):
            continue
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td><td>%s</td><td>%s</td></tr>" % (
            e(g["id"]), e(group_state_text(g)), e(CATEGORY_TEXT.get(group_category(snap, g), "")),
            '<a href="?group=%s">%s</a>' % (quote(str(g["id"])), e(g.get("branch") or g["id"])),
            e(" ".join("#" + i for i in g.get("members") or [])), e(g.get("fix_round", 0)),
            e((g.get("last_regression") or "")[:160]), e({"ok": "成功", "failed": "失敗"}.get(g.get("release"), ""))))
    out.append("</table></div></section>")

    pages = [(k, v) for k, v in sorted(snap.inbox.items(), reverse=True) if not v.get("acked")]
    out.append('<section><h2>等你處理(收件匣未讀)</h2>')
    if not pages:
        out.append("<p class='quiet'>沒有。</p>")
    for name, p in pages:
        out.append("<div class='page k-%s'><div class='ph'>%s · %s · %s</div><pre>%s</pre>" % (
            e(p.get("kind")), e(p.get("kind")), e(p.get("ref")), e(p.get("at")), e(p.get("text"))))
        out.append("</div>")
    out.append("<p class='quiet'>要你決定的事在「要你判」分頁。收下:<code>python3 devloop/tickets.py ack &lt;頁名&gt;</code></p>"
               "</section>")
    if key:
        out.append(render_jobs(cfg))

    out.append('<section><h2>記憶</h2><div class="scroll"><table><tr><th>檔</th><th>字元</th><th>暫存區</th>'
               '<th>上次整理</th></tr>')
    last = (snap.memory_state or {}).get("last") or {}
    for path in sorted(memory.files(cfg)):
        layer = path.split("/")[1] if path.count("/") == 2 else "project"
        name = os.path.basename(path)[:-3] if layer != "project" else "project"
        text = memory.read(cfg, path) or ""
        out.append("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td>%s</td></tr>" % (
            e(path), e(len(text)), e(len(memory.pending(snap, layer, name))), e(last.get(path) or "")))
    out.append("</table></div></section>")
    return "\n".join(out)


def stub_tab(heading):
    return "<section><h2>%s</h2><p class='quiet'>還沒有內容。</p></section>" % e(heading)


# ---------------------------------------------------------------- detail views

DETAIL_STYLE = ("<style>section.detail dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px}"
                "section.detail dt{color:var(--quiet)}section.detail dd{margin:0}"
                "ol.timeline{padding-left:20px}ol.timeline li{margin:2px 0}"
                "ol.timeline li[data-kind=run]{color:var(--quiet)}"
                "table.cost td.n{text-align:right;font-variant-numeric:tabular-nums}</style>")
EVENT_FIELDS = ("round", "reason", "target", "group", "note", "source")
COST_COLUMNS = (("role", "角色"), ("model", "模型"), ("effort", "強度"), ("round", "輪"), ("seconds", "秒"),
                ("outcome", "結果"), ("input_tokens", "input"), ("output_tokens", "output"),
                ("cache_read_tokens", "快取讀"), ("cache_write_tokens", "快取寫"), ("usd", "美元"), ("at", "時間"))
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def detail_wanted(query):
    """('ticket', N), ('group', gN) or None. A group alone (no tab=) is a detail page; with tab= it is
    the 票 tab's filter, which the filter form always sends with tab=tickets."""
    query = query or {}
    if query.get("ticket"):
        return "ticket", str(query["ticket"])
    if query.get("group") and not query.get("tab"):
        return "group", str(query["group"])
    return None


def run_start(row):
    """A cost row's start in the pool's time format: 'at' minus 'seconds', rounded down to the
    whole second; 'at' itself when it has no seconds or cannot be read."""
    import calendar
    import math
    at = row.get("at")
    seconds = row.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return at
    try:
        end = calendar.timegm(time.strptime(at, TIME_FORMAT))
    except (TypeError, ValueError, OverflowError):
        return at
    return clock(math.floor(end - seconds))


def timeline(history, rows):
    """[(kind, at, item)] of history events and cost rows in time order; at equal times events
    before runs; otherwise the stored order (the sort is stable)."""
    entries = [("event", h.get("at"), h) for h in history or [] if isinstance(h, dict)]
    entries += [("run", run_start(r), r) for r in rows or [] if isinstance(r, dict)]
    return sorted(entries, key=lambda x: (str(x[1] or ""), 0 if x[0] == "event" else 1))


def feedback_text(t, round_no):
    """The kept feedback entry of round `round_no` without its 'round R:' lead, or None."""
    lead = "round %s:" % round_no
    for entry in (t or {}).get("feedback") or []:
        if isinstance(entry, str) and entry.startswith(lead):
            return entry[len(lead):].strip()
    return None


def seconds_text(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return UNKNOWN
    return "%d 秒" % round(value)


def render_timeline(history, rows, t=None):
    out = ['<ol class="timeline">']
    for kind, at, item in timeline(history, rows):
        if kind == "event":
            parts = ["<b>%s</b>" % e(item.get("event"))]
            parts += ["%s=%s" % (e(k), e(item[k])) for k in EVENT_FIELDS if item.get(k) not in (None, "")]
            if item.get("event") == "feedback" and item.get("round") is not None:
                text = feedback_text(t, item["round"])
                if text:
                    parts.append("<q>%s</q>" % e(text))
        else:
            parts = ["<b>%s</b>" % e(item.get("role") or UNKNOWN), e(item.get("model") or UNKNOWN),
                     e(seconds_text(item.get("seconds"))), e(item.get("outcome") or UNKNOWN)]
        out.append('<li data-kind="%s" data-at="%s"><time>%s</time> %s</li>' % (
            e(kind), e(at or ""), e(at or UNKNOWN), " · ".join(parts)))
    out.append("</ol>")
    return "".join(out)


def cost_value(row, key):
    """One cell of the cost table; 未知 for a value nobody recorded, never 0."""
    v = row.get(key)
    if v is None or v == "":
        return UNKNOWN
    if key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        return format(v, ",") if isinstance(v, int) and not isinstance(v, bool) else UNKNOWN
    if key == "usd":
        return money(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else UNKNOWN
    if key == "seconds":
        return "%d" % round(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else UNKNOWN
    return str(v)


def render_cost_table(rows):
    from core import agents
    rows = [r for r in rows or [] if isinstance(r, dict)]
    out = ['<div class="scroll"><table class="cost"><tr>%s</tr>' % "".join(
        "<th>%s</th>" % e(label) for _, label in COST_COLUMNS)]
    for row in rows:
        out.append("<tr>%s</tr>" % "".join('<td class="n">%s</td>' % e(cost_value(row, k)) for k, _ in COST_COLUMNS))
    sums = agents.total(rows)
    cells = []
    for k, _ in COST_COLUMNS:
        if k == "role":
            cells.append("<td>合計</td>")
        elif k in sums:
            cells.append('<td class="n">%s</td>' % e(cost_value(sums, k)))
        elif k == "outcome":
            cells.append("<td>%s</td>" % e("%d 次" % sums["runs"]))
        else:
            cells.append("<td></td>")
    out.append('<tr class="total">%s</tr></table></div>' % "".join(cells))
    return "".join(out)


def _dl(pairs):
    return "<dl>%s</dl>" % "".join("<dt>%s</dt><dd>%s</dd>" % (e(k), v) for k, v in pairs)


def render_ticket_detail(snap, ident):
    t = snap.tickets.get(ident)
    if not isinstance(t, dict):
        return "<section><p>%s</p></section>" % e("沒有 #%s 這張票" % ident)
    state = t.get("state")
    category = ticket_category(t)
    acceptance = "".join("<li>%s</li>" % e(a) for a in t.get("acceptance") or [])
    pairs = [("狀態", e(STATE_TEXT.get(state, state)))]
    if category:
        pairs.append(("原因", e(CATEGORY_TEXT.get(category, category))))
    group = t.get("group")
    pairs += [("整合分支", '<a href="?group=%s">%s</a>' % (quote(str(group)), e(group)) if group else ""),
              ("輪", e(t.get("round", 0))), ("目標", e(t.get("objective"))),
              ("驗收", "<ol>%s</ol>" % acceptance)]
    return "".join(['<section class="detail" data-ticket="%s"><h2>#%s %s</h2>' % (e(ident), e(ident), e(t.get("title"))),
                    _dl(pairs), "<h3>時間線</h3>", render_timeline(t.get("history"), t.get("cost"), t),
                    "<h3>花費</h3>", render_cost_table(t.get("cost")), "</section>"])


def render_group_detail(snap, gid):
    g = snap.groups.get(gid)
    if not isinstance(g, dict):
        return "<section><p>%s</p></section>" % e("沒有 %s 這個整合分支" % gid)
    members = []
    for i in g.get("members") or []:
        t = snap.tickets.get(str(i)) or {}
        state = t.get("state")
        members.append('<li><a href="?ticket=%s">#%s</a> %s</li>' % (
            quote(str(i)), e(i), e(STATE_TEXT.get(state, state) if state else UNKNOWN)))
    notes = "".join("<li>%s</li>" % e(n.get("text") if isinstance(n, dict) else n) for n in g.get("notes") or [])
    pairs = [("狀態", e(group_state_text(g))),
             ("原因", e(CATEGORY_TEXT.get(group_category(snap, g), ""))),
             ("分支", e(g.get("branch"))), ("修補輪", e(g.get("fix_round", 0))),
             ("最後一次 regression", e(g.get("last_regression") or "")),
             ("發版", e({"ok": "成功", "failed": "失敗"}.get(g.get("release"), g.get("release") or ""))),
             ("票", "<ul>%s</ul>" % "".join(members))]
    if notes:
        pairs.append(("備註", "<ul>%s</ul>" % notes))
    return "".join(['<section class="detail" data-group="%s"><h2>%s</h2>' % (e(gid), e(gid)), _dl(pairs),
                    "<h3>時間線</h3>", render_timeline(g.get("history"), g.get("cost")),
                    "<h3>花費</h3>", render_cost_table(g.get("cost")), "</section>"])


def render_detail(snap, wanted):
    kind, ident = wanted
    body = render_ticket_detail(snap, ident) if kind == "ticket" else render_group_detail(snap, ident)
    return "\n".join([DETAIL_STYLE, '<p><a href="?tab=tickets">← 所有票</a></p>', body])


# ---------------------------------------------------------------- tab: DAG

DAG_NODE_W, DAG_NODE_H, DAG_COL_W, DAG_ROW_H = 150, 38, 200, 50
DAG_LANE_HEAD, DAG_LANE_GAP, DAG_PAD = 26, 12, 12
NO_GROUP = "未分組"
BLOCKED_TEXT, BLOCKER_TEXT = "被卡住", "卡住的源頭"

DAG_STYLE = """<style>
#tab-dag{--s-ready:#5c6670;--s-running:#1f6f8b;--s-merged:#3d7a46;--s-landed:#2f5d3a;--s-stuck:#a23b2a;--s-cancelled:#8b8f94;--chain:#c0392b}
@media (prefers-color-scheme:dark){#tab-dag{--s-ready:#98a4ad;--s-running:#6fb7d0;--s-merged:#7fc28a;--s-landed:#5f9e6c;--s-stuck:#e08a7a;--s-cancelled:#6c7378;--chain:#ff7a6b}}
#tab-dag .dagbox{padding:8px}#tab-dag svg{display:block}
#tab-dag .lane rect.band{fill:var(--bg);stroke:var(--line)}#tab-dag .lane text.lh{fill:var(--quiet);font-size:12px}
#tab-dag .node rect{fill:var(--card);stroke:var(--sc,var(--line));stroke-width:1.5}#tab-dag .node text{fill:var(--ink);font-size:12px}
#tab-dag .node text.st{fill:var(--sc,var(--quiet));font-size:11px}
#tab-dag .s-ready{--sc:var(--s-ready)}#tab-dag .s-running{--sc:var(--s-running)}#tab-dag .s-merged{--sc:var(--s-merged)}
#tab-dag .s-landed{--sc:var(--s-landed)}#tab-dag .s-stuck{--sc:var(--s-stuck)}#tab-dag .s-cancelled{--sc:var(--s-cancelled)}
#tab-dag .node.blocked rect{stroke:var(--chain);stroke-dasharray:5 3;stroke-width:2}
#tab-dag .node.blocker rect{stroke:var(--chain);stroke-width:3.5}
#tab-dag path.edge{fill:none;stroke:var(--quiet);stroke-width:1.3}#tab-dag path.edge.chain{stroke:var(--chain);stroke-width:3}
#tab-dag .legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;align-items:center}
#tab-dag .legend span{display:inline-flex;gap:6px;align-items:center}
#tab-dag .legend i{display:inline-block;width:14px;height:10px;border-radius:3px;border:2px solid var(--sc,var(--line));background:var(--card)}
#tab-dag .legend i.blocked{border:2px dashed var(--chain)}#tab-dag .legend i.blocker{border:3px solid var(--chain)}
</style>"""


def _num_key(ident):
    ident = str(ident)
    return (0, int(ident), "") if ident.isdigit() else (1, 0, ident)


def _group_key(gid):
    rest = gid[1:]
    return (0, int(rest), gid) if rest.isdigit() else (1, 0, gid)


def dag_blockers(snap):
    """Ids of tickets that keep their dependents waiting for good: stuck, cancelled, or merged into a
    stuck group (ticket_controller.joinable/group_for only start a ticket on landed or same-open-group
    merged dependencies)."""
    out = set()
    for ident, t in snap.tickets.items():
        state = t.get("state")
        if state in ("stuck", "cancelled"):
            out.add(ident)
        elif state == "merged" and (snap.groups.get(t.get("group") or "") or {}).get("state") == "stuck":
            out.add(ident)
    return out


def dag_blocked(snap, blockers):
    """{ready ticket id: sorted ids of the blockers its depends_on chain reaches}, stopping at landed
    tickets and at blockers; ready tickets that reach none are left out."""
    out = {}
    for ident, t in snap.tickets.items():
        if t.get("state") != "ready":
            continue
        found, seen, todo = set(), {ident}, list(dagmod.deps(snap.dag, ident))
        while todo:
            d = todo.pop()
            if d in seen:
                continue
            seen.add(d)
            if d in blockers:
                found.add(d)
                continue
            if (snap.tickets.get(d) or {}).get("state") == "landed":
                continue
            todo.extend(dagmod.deps(snap.dag, d))
        if found:
            out[ident] = sorted(found, key=_num_key)
    return out


def dag_shown(snap, blocked, done):
    """Ids the DAG tab draws: everything with done, else all but landed and cancelled tickets, plus
    the cancelled blockers a shown ticket waits on."""
    if done:
        return set(snap.tickets)
    shown = set(i for i, t in snap.tickets.items() if t.get("state") not in ("landed", "cancelled"))
    for ident in list(shown):
        shown.update(b for b in blocked.get(ident, ()) if b in snap.tickets)
    return shown


def dag_layers(snap, shown):
    """{id: layer}: 0 without shown dependencies, else 1 + the largest layer among them."""
    layers = {}

    def layer(ident, path):
        if ident in layers:
            return layers[ident]
        path = path | {ident}
        below = [layer(d, path) for d in dagmod.deps(snap.dag, ident) if d in shown and d not in path]
        layers[ident] = 1 + max(below) if below else 0
        return layers[ident]

    for ident in sorted(shown, key=_num_key):
        layer(ident, frozenset())
    return layers


def dag_lanes(snap, shown):
    """[(group id, label)] for the lanes that hold a shown ticket: groups by number, 未分組 ('') last."""
    gids = set((snap.tickets[i].get("group") or "") for i in shown)
    out = []
    for gid in sorted((g for g in gids if g), key=_group_key):
        g = snap.groups.get(gid)
        out.append((gid, group_state_text(g) if g else UNKNOWN))
    if "" in gids:
        out.append(("", NO_GROUP))
    return out


def _clip(text, n):
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


def dag_svg(snap, shown, layers, blocked, blockers):
    """The <svg id="dag">: lanes as bands top to bottom, layers as columns left to right."""
    lanes = dag_lanes(snap, shown)
    cells = {}
    for ident in shown:
        cells.setdefault(((snap.tickets[ident].get("group") or ""), layers[ident]), []).append(ident)
    for v in cells.values():
        v.sort(key=_num_key)
    ncols = 1 + max(layers.values())
    width = DAG_PAD * 2 + 90 + ncols * DAG_COL_W
    pos, lane_out, y = {}, [], DAG_PAD
    for gid, label in lanes:
        rows = max(len(v) for (g, _), v in cells.items() if g == gid)
        height = DAG_LANE_HEAD + rows * DAG_ROW_H
        part = ['<g class="lane" data-group="%s">' % e(gid),
                '<rect class="band" x="%d" y="%d" width="%d" height="%d" rx="8"/>'
                % (DAG_PAD, y, width - 2 * DAG_PAD, height),
                '<text class="lh" x="%d" y="%d">%s</text>' % (DAG_PAD + 8, y + 17, e(gid + " · " + label if gid else label))]
        for (g, col), idents in sorted(cells.items(), key=lambda kv: kv[0][1]):
            if g != gid:
                continue
            for row, ident in enumerate(idents):
                t = snap.tickets[ident]
                x = DAG_PAD + 90 + col * DAG_COL_W
                ny = y + DAG_LANE_HEAD + row * DAG_ROW_H
                pos[ident] = (x, ny)
                state = str(t.get("state") or "")
                cls = ["node", "s-" + state]
                extra = ""
                if ident in blocked:
                    cls.append("blocked")
                    extra = ' data-blocked-by="%s"' % e(" ".join(blocked[ident]))
                if ident in blockers:
                    cls.append("blocker")
                part.append('<a href="?ticket=%s"><g class="%s" data-id="%s" data-state="%s" data-group="%s" '
                            'data-layer="%d"%s><title>#%s %s</title>'
                            '<rect x="%d" y="%d" width="%d" height="%d" rx="6"/>'
                            '<text x="%d" y="%d">#%s %s</text><text class="st" x="%d" y="%d">%s</text></g></a>' % (
                                e(ident), e(" ".join(cls)), e(ident), e(state), e(t.get("group") or ""),
                                layers[ident], extra, e(ident), e(t.get("title")),
                                x, ny, DAG_NODE_W, DAG_NODE_H,
                                x + 8, ny + 16, e(ident), e(_clip(t.get("title"), 12)),
                                x + 8, ny + 31, e(STATE_TEXT.get(state, state))))
        part.append("</g>")
        lane_out.append("\n".join(part))
        y += height + DAG_LANE_GAP
    edges = []
    for a in sorted(shown, key=_num_key):
        for b in dagmod.deps(snap.dag, a):
            if b not in shown:
                continue
            chain = a in blocked and (b in blocked or b in blockers)
            (bx, by), (ax, ay) = pos[b], pos[a]
            x1, y1, x2, y2 = bx + DAG_NODE_W, by + DAG_NODE_H / 2, ax, ay + DAG_NODE_H / 2
            mid = (x1 + x2) / 2
            edges.append('<path class="edge%s" data-from="%s" data-to="%s" d="M%g %g C%g %g %g %g %g %g"/>' % (
                " chain" if chain else "", e(b), e(a), x1, y1, mid, y1, mid, y2, x2, y2))
    height = y - DAG_LANE_GAP + DAG_PAD
    return ('<svg id="dag" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'role="img" aria-label="票的相依圖">\n%s\n<g class="edges">%s</g>\n</svg>'
            % (width, height, width, height, "\n".join(lane_out), "".join(edges)))


def render_dag_legend():
    items = ['<span class="s-%s"><i></i>%s</span>' % (e(s), e(text)) for s, text in STATE_TEXT.items()]
    items.append('<span><i class="blocked"></i>%s</span>' % e(BLOCKED_TEXT))
    items.append('<span><i class="blocker"></i>%s</span>' % e(BLOCKER_TEXT))
    return '<div class="legend">%s</div>' % "".join(items)


def render_dag_tab(cfg, snap, query, now):
    done = (query or {}).get("done") == "1"
    blockers = dag_blockers(snap)
    blocked = dag_blocked(snap, blockers)
    shown = dag_shown(snap, blocked, done)
    toggle = ('<a href="?tab=dag">收起已落地與已取消</a>' if done
              else '<a href="%s">顯示已落地與已取消</a>' % e("?tab=dag&done=1"))
    out = [DAG_STYLE, '<section><h2>DAG</h2><p class="quiet">一欄 = 一層相依,一條帶 = 一個整合分支;紅線是卡住的鏈。 %s</p>'
           % toggle, render_dag_legend()]
    if not shown:
        out.append("<p class='quiet'>沒有要顯示的票</p></section>")
        return "\n".join(out)
    layers = dag_layers(snap, shown)
    out.append('<div class="scroll dagbox">%s</div></section>' % dag_svg(snap, shown, layers, blocked, blockers))
    return "\n".join(out)


# ---------------------------------------------------------------- tab: Token

TOKEN_DAYS = 30
TOKEN_MEASURES = (("input_tokens", "輸入"), ("output_tokens", "輸出"), ("cache_read_tokens", "快取讀"),
                  ("cache_write_tokens", "快取寫"), ("usd", "美元"))
NO_ROWS = "—"
TOKEN_STYLE = """<style>
#tab-tokens td.n{text-align:right;font-variant-numeric:tabular-nums}
#tab-tokens .charts{display:flex;flex-wrap:wrap;gap:12px}
#tab-tokens figure{margin:0}#tab-tokens figcaption{font-size:12px;color:var(--quiet)}
#tab-tokens svg.chart{display:block;background:var(--card);border:1px solid var(--line);border-radius:4px}
#tab-tokens rect.bar{fill:var(--ink);opacity:.7}
</style>"""


def _number(value, measure):
    """The row's value for `measure` when it is a number, else None (bools are not numbers)."""
    if isinstance(value, bool):
        return None
    if measure == "usd":
        return value if isinstance(value, (int, float)) else None
    return value if isinstance(value, int) else None


def cost_rows(snap):
    """Every cost row the pool holds: tickets', integration branches', memory runs' and inbox pages'
    (an opener run that opened no ticket keeps its row on the decision page it wrote)."""
    rows = []
    for t in snap.tickets.values():
        rows += [r for r in t.get("cost") or [] if isinstance(r, dict)]
    for g in snap.groups.values():
        rows += [r for r in g.get("cost") or [] if isinstance(r, dict)]
    state = snap.memory_state if isinstance(snap.memory_state, dict) else {}
    for run in state.get("runs") or []:
        if isinstance(run, dict):
            rows += [r for r in run.get("cost") or [] if isinstance(r, dict)]
    for page in (snap.inbox or {}).values():
        if isinstance(page, dict):
            rows += [r for r in page.get("cost") or [] if isinstance(r, dict)]
    return rows


def run_count(rows):
    """How many agent runs the rows stand for: the shares of one split opener run (rows that carry
    split_over) count once per distinct run_dir; every other row counts once."""
    split = set(r.get("run_dir") for r in rows if r.get("split_over"))
    return len(split) + sum(1 for r in rows if not r.get("split_over"))


def row_day(row):
    """The local date (YYYY-MM-DD) of a row's UTC 'at', or None when it cannot be read."""
    import calendar
    try:
        return time.strftime("%Y-%m-%d", time.localtime(
            calendar.timegm(time.strptime(row.get("at"), "%Y-%m-%dT%H:%M:%SZ"))))
    except (TypeError, ValueError, OverflowError):
        return None


def token_days(now):
    """The TOKEN_DAYS local dates ending on the local date of `now`, oldest first."""
    import datetime
    last = datetime.date(*time.localtime(now)[:3])
    return [(last - datetime.timedelta(days=i)).isoformat() for i in range(TOKEN_DAYS - 1, -1, -1)]


def measure_sum(rows, measure):
    """(sum, rows without a number); the sum is None when no row has one."""
    total, missing = None, 0
    for row in rows:
        v = _number(row.get(measure), measure)
        if v is None:
            missing += 1
        else:
            total = (total or 0) + v
    return total, missing


def money(value):
    return "$%.2f" % value


def measure_text(value, measure):
    return money(value) if measure == "usd" else format(value, ",")


def cell(rows, measure):
    """The cell rule: '—' for no rows, 未知 when no row has a number, else the sum and '(k 筆未知)'."""
    if not rows:
        return NO_ROWS
    total, missing = measure_sum(rows, measure)
    if total is None:
        return UNKNOWN
    return measure_text(total, measure) + ("(%d 筆未知)" % missing if missing else "")


def ticket_tokens(t):
    """input + output + cache read over the ticket's rows; None when none of them has any."""
    known = [x for x in tokens(t) if x is not None]
    return sum(known) if known else None


def ticket_dollars(t):
    return measure_sum(t.get("cost") or [], "usd")[0]


def ticket_complexity(t):
    if t.get("complexity"):
        return t["complexity"]
    return "complex" if t.get("needs_review") is True else "simple"


def median_text(values, fmt):
    if not values:
        return UNKNOWN
    return "%s(%d 張)" % (fmt(statistics.median(values)), len(values))


def _measure_cells(rows):
    return "".join('<td class="n">%s</td>' % e(cell(rows, m)) for m, _ in TOKEN_MEASURES)


def _measure_heads():
    return "".join("<th>%s</th>" % e(label) for _, label in TOKEN_MEASURES)


def token_chart(measure, label, days, by_day):
    """One bar per day; data-value is the day's sum, '' when the day's cell is 未知 or '—'."""
    sums = [measure_sum(by_day[d], measure)[0] for d in days]
    top = max([v for v in sums if v] or [1])
    w, h, bw = 4 + 10 * len(days), 84, 8
    bars = []
    for i, (day, v) in enumerate(zip(days, sums)):
        value = "" if v is None else ("%.2f" % v if measure == "usd" else str(v))
        height = 0 if not v else max(1, round(80.0 * v / top))
        bars.append('<rect class="bar" data-day="%s" data-value="%s" x="%d" y="%d" width="%d" height="%d">'
                    '<title>%s %s</title></rect>' % (e(day), e(value), 2 + 10 * i, 82 - height, bw, height,
                                                     e(day), e(value or UNKNOWN)))
    return ('<figure><figcaption>%s</figcaption><svg class="chart" data-measure="%s" width="%d" height="%d" '
            'viewBox="0 0 %d %d">%s</svg></figure>' % (e(label), e(measure), w, h, w, h, "".join(bars)))


def render_split(heading, rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(row.get(key) or UNKNOWN, []).append(row)
    out = ['<section><h2>%s</h2><div class="scroll"><table><tr><th>%s</th><th>次數</th>%s</tr>'
           % (e(heading), e("角色" if key == "role" else "模型"), _measure_heads())]
    for name in sorted(groups, key=str):
        out.append('<tr><td>%s</td><td class="n">%d</td>%s</tr>' % (e(name), run_count(groups[name]),
                                                                   _measure_cells(groups[name])))
    out.append("</table></div></section>")
    return "".join(out)


def render_tokens_tab(cfg, snap, query, now):
    days = token_days(now)
    by_day = dict((d, []) for d in days)
    for row in cost_rows(snap):
        day = row_day(row)
        if day in by_day:
            by_day[day].append(row)
    recent = [row for d in days for row in by_day[d]]
    out = [TOKEN_STYLE, "<p class='quiet'>開題者開票與改票的執行都算在這些數字裡:開票那次分攤在它開的票上,"
           "改票記在被改的票上,沒開出票的記在它寫的決定頁上。</p>"]

    out.append('<section><h2>近 30 天</h2><div class="scroll"><table><tr><th>次數</th>%s</tr>'
               '<tr><td class="n">%d</td>%s</tr></table></div></section>'
               % (_measure_heads(), run_count(recent), _measure_cells(recent)))

    out.append('<section><h2>每天</h2><div class="charts">%s</div>'
               % "".join(token_chart(m, label, days, by_day) for m, label in TOKEN_MEASURES))
    out.append('<div class="scroll"><table><caption>每天</caption><tr><th>日期</th><th>次數</th>%s</tr>' % _measure_heads())
    for d in days:
        out.append('<tr><td>%s</td><td class="n">%d</td>%s</tr>' % (e(d), run_count(by_day[d]), _measure_cells(by_day[d])))
    out.append("</table></div></section>")

    out.append(render_split("依角色", recent, "role"))
    out.append(render_split("依模型", recent, "model"))

    landed = [t for t in snap.tickets.values() if t.get("state") == "landed"]
    tok = [v for v in (ticket_tokens(t) for t in landed) if v is not None]
    usd = [v for v in (ticket_dollars(t) for t in landed) if v is not None]
    out.append('<section><h2>每張落地票</h2><table><tr><th>token(中位)</th><th>美元(中位)</th></tr>'
               '<tr><td class="n">%s</td><td class="n">%s</td></tr></table></section>'
               % (e(median_text(tok, lambda m: format(int(m), ","))), e(median_text(usd, money))))

    priced = [t for t in snap.tickets.values() if ticket_dollars(t) is not None]
    priced.sort(key=lambda t: (-ticket_dollars(t), _num_key(t.get("id"))))
    out.append('<section><h2>最貴的十張</h2><div class="scroll"><table><tr><th>票</th><th>標題</th><th>狀態</th>'
               '<th>token</th><th>快取寫</th><th>美元</th><th>次數</th></tr>')
    for t in priced[:10]:
        tk, cw = ticket_tokens(t), cache_written(t)
        out.append('<tr><td><a href="?ticket=%s">#%s</a></td><td>%s</td><td>%s</td><td class="n">%s</td>'
                   '<td class="n">%s</td><td class="n">%s</td><td class="n">%d</td></tr>'
                   % (quote(str(t.get("id")), safe=""), e(t.get("id")), e(t.get("title")),
                      e(STATE_TEXT.get(t.get("state"), t.get("state"))),
                      e(UNKNOWN if tk is None else format(tk, ",")), e(UNKNOWN if cw is None else format(cw, ",")),
                      e(money(ticket_dollars(t))), len(t.get("cost") or [])))
    out.append("</table></div></section>")

    estimated = [t for t in landed if _number(t.get("token_estimate"), "tokens") is not None]
    estimated.sort(key=lambda t: _num_key(t.get("id")))
    out.append('<section><h2>估計與實際</h2><div class="scroll"><table><tr><th>票</th><th>估計</th><th>實際</th>'
               '<th>比</th></tr>')
    ratios = {"all": [], "simple": [], "complex": []}
    for t in estimated:
        tk, est = ticket_tokens(t), t["token_estimate"]
        ratio = tk / float(est) if tk is not None and est else None
        if ratio is not None:
            ratios["all"].append(ratio)
            ratios.setdefault(ticket_complexity(t), []).append(ratio)
        out.append('<tr><td>#%s</td><td class="n">%s</td><td class="n">%s</td><td class="n">%s</td></tr>'
                   % (e(t.get("id")), e(format(est, ",")), e(UNKNOWN if tk is None else format(tk, ",")),
                      e(UNKNOWN if ratio is None else "%.2f" % ratio)))
    out.append("</table></div>")
    ratio_fmt = lambda m: "%.2f" % m  # noqa: E731
    out.append('<table><tr><th>比的中位</th><th></th></tr>%s</table></section>' % "".join(
        '<tr><td>%s</td><td class="n">%s</td></tr>' % (e(label), e(median_text(ratios[k], ratio_fmt)))
        for k, label in (("all", "全部"), ("simple", "simple"), ("complex", "complex"))))
    return "\n".join(out)


# ---------------------------------------------------------------- tab: 主機

HOST_WINDOWS = (("24h", "近 24 小時", 288, 300), ("7d", "近 7 天", 168, 3600))   # name, label, buckets, seconds
HOST_CHARTS = (("load", "負載", ("load1", "load5"), "cpus"),
               ("memory", "可用記憶體(GiB)", ("mem_available",), None),
               ("disk", "磁碟剩餘(GiB)", ("disk_free",), None),
               ("agents", "agent 與 slot", ("agents", "slots_used"), "slots"))
HOST_GIB_FIELDS = ("mem_available", "disk_free")
HOST_STYLE = """<style>
#tab-host section.host .charts{display:flex;flex-wrap:wrap;gap:12px}
#tab-host figure{margin:0}#tab-host figcaption{font-size:12px;color:var(--quiet)}
#tab-host svg.chart{display:block;background:var(--card);border:1px solid var(--line);border-radius:4px}
#tab-host polyline{fill:none;stroke-width:1.5}#tab-host circle{stroke:none}
#tab-host g.series[data-series=load1],#tab-host g.series[data-series=mem_available],#tab-host g.series[data-series=disk_free],#tab-host g.series[data-series=agents]{stroke:var(--ink);fill:var(--ink)}
#tab-host g.series[data-series=load5],#tab-host g.series[data-series=slots_used]{stroke:var(--quiet);fill:var(--quiet)}
#tab-host line.limit{stroke:var(--quiet);stroke-dasharray:4 3}
#tab-host dl.newest{display:flex;flex-wrap:wrap;gap:4px 18px;margin:6px 0}
#tab-host dl.newest dt{font-size:12px;color:var(--quiet)}#tab-host dl.newest dd{margin:0;font-variant-numeric:tabular-nums}
</style>"""
HOST_W, HOST_H, HOST_PAD = 300, 90, 4


def host_samples(path):
    """[(epoch, sample)] of one host file, read once; lines that are not JSON objects with a
    parseable 'at' are skipped."""
    import calendar
    import json
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    sample = json.loads(line)
                    at = calendar.timegm(time.strptime(sample["at"], "%Y-%m-%dT%H:%M:%SZ"))
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
                if isinstance(sample, dict):
                    out.append((at, sample))
    except OSError:
        return []
    return out


def host_files(cfg):
    """{NAME: path} for every NAME.jsonl in <worktrees>/runs/host/."""
    from core import host as hostmod
    folder = hostmod.host_dir(cfg)
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    return dict((n[:-len(".jsonl")], os.path.join(folder, n)) for n in names
                if n.endswith(".jsonl") and len(n) > len(".jsonl") and os.path.isfile(os.path.join(folder, n)))


def host_value(sample, field):
    """The field as a float (GiB for memory and disk), None when it is not a number."""
    v = sample.get(field)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v / float(2 ** 30) if field in HOST_GIB_FIELDS else float(v)


def host_buckets(samples, field, now, count, width):
    """The mean of each bucket's non-null values (None = none); bucket k covers
    [now - W + k*w, now - W + (k+1)*w) with W = count * width."""
    start = now - count * width
    sums, ns = [0.0] * count, [0] * count
    for at, sample in samples:
        k = int((at - start) // width)
        if 0 <= k < count:
            v = host_value(sample, field)
            if v is not None:
                sums[k] += v
                ns[k] += 1
    return [sums[k] / ns[k] if ns[k] else None for k in range(count)]


def host_runs(values):
    """[(first index, [values])] of the runs of consecutive non-None values."""
    runs, current = [], None
    for i, v in enumerate(values):
        if v is None:
            current = None
        else:
            if current is None:
                current = (i, [])
                runs.append(current)
            current[1].append(v)
    return runs


def host_chart(chart, label, fields, limit_field, window, samples, newest, now, count, width):
    series = [(f, [None if v is None else round(v, 2) for v in host_buckets(samples, f, now, count, width)])
              for f in fields]
    limit = host_value(newest, limit_field) if limit_field else None
    top = max([v for _, vals in series for v in vals if v is not None] + ([limit] if limit is not None else []) + [0])
    top = top or 1.0
    x = lambda i: HOST_PAD + (HOST_W - 2 * HOST_PAD) * (i + 0.5) / count  # noqa: E731
    y = lambda v: HOST_H - HOST_PAD - (HOST_H - 2 * HOST_PAD) * v / top  # noqa: E731
    parts = []
    if limit is not None:
        parts.append('<line class="limit" data-value="%s" x1="0" x2="%d" y1="%.1f" y2="%.1f"><title>%s %s</title></line>'
                     % (e("%.2f" % limit), HOST_W, y(limit), y(limit), e(limit_field), e("%g" % limit)))
    for field, vals in series:
        shapes = []
        for first, run in host_runs(vals):
            points = " ".join("%.1f,%.1f" % (x(first + j), y(v)) for j, v in enumerate(run))
            shapes.append('<polyline points="%s"/>' % points)
            if len(run) == 1:
                shapes.append('<circle cx="%.1f" cy="%.1f" r="1.5"/>' % (x(first), y(run[0])))
        data = ",".join("" if v is None else "%.2f" % v for v in vals)
        parts.append('<g class="series" data-series="%s" data-values="%s"><title>%s</title>%s</g>'
                     % (e(field), e(data), e(field), "".join(shapes)))
    return ('<figure><figcaption>%s(%s)</figcaption><svg class="chart" data-window="%s" data-chart="%s" '
            'width="%d" height="%d" viewBox="0 0 %d %d">%s</svg></figure>'
            % (e(label), e(" / ".join(fields)), e(window), e(chart), HOST_W, HOST_H, HOST_W, HOST_H, "".join(parts)))


def host_text(sample, field, gib=False):
    v = sample.get(field)
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return UNKNOWN if v is None or isinstance(v, bool) else str(v)
    return "%.1f GiB" % (v / float(2 ** 30)) if gib else str(v)


def host_newest(sample):
    """The newest sample's row: at, load1 / load5 / cpus, memory and disk in GiB, agents, slots."""
    # Label/value pairs, not one table row: each value sits after its own label, so adjacent
    # numbers (agents, then slots_used / slots) never run together in the page text.
    pairs = (("最新一筆", sample.get("at") or UNKNOWN),
             ("load1 / load5 / cpus", " / ".join(host_text(sample, f) for f in ("load1", "load5", "cpus"))),
             ("可用記憶體", host_text(sample, "mem_available", gib=True)),
             ("磁碟剩餘", host_text(sample, "disk_free", gib=True)),
             ("agents", host_text(sample, "agents")),
             ("slots_used / slots", "%s / %s" % (host_text(sample, "slots_used"), host_text(sample, "slots"))))
    return ('<dl class="newest">%s</dl>'
            % "".join('<div><dt>%s</dt><dd class="n">%s</dd></div>' % (e(k), e(v)) for k, v in pairs))


def render_host(name, samples, now):
    out = ['<section class="host" data-host="%s"><h2>%s</h2>' % (e(name), e(name))]
    if samples:
        out.append(host_newest(max(samples, key=lambda s: s[0])[1]))
    recent = [s for s in samples if now - 7 * 86400 <= s[0] < now]
    if not recent:
        out.append("<p class='quiet'>近 7 天沒有紀錄</p></section>")
        return "".join(out)
    newest = max(samples, key=lambda s: s[0])[1]
    for window, label, count, width in HOST_WINDOWS:
        out.append('<h3>%s</h3><div class="charts">' % e(label))
        for chart, clabel, fields, limit_field in HOST_CHARTS:
            out.append(host_chart(chart, clabel, fields, limit_field, window, recent, newest, now, count, width))
        out.append("</div>")
    out.append("</section>")
    return "".join(out)


def render_host_tab(cfg, snap, query, now):
    files = host_files(cfg)
    out = [HOST_STYLE]
    if not files:
        out.append("<section><h2>主機</h2><p class='quiet'>還沒有主機紀錄。controller 執行時每 "
                   "<code>devloop.host_sample_seconds</code> 秒寫一行到 <code>&lt;worktrees&gt;/runs/host/</code>。"
                   "</p></section>")
        return "\n".join(out)
    for name in sorted(files):
        out.append(render_host(name, host_samples(files[name]), now))
    return "\n".join(out)


# ---------------------------------------------------------------- web input

def jobs_dir(cfg):
    return os.path.join(cfg.worktree_root, "runs", "web")


JOB_LOGS = ("job.log", "creator.log")     # tickets.py jobs write job.log; ticket_creator jobs (and older jobs) creator.log


def job_state(folder):
    """(job.json dict, rc text or None while it runs, last non-empty line of its log) of one web job."""
    meta = osx.read_json(os.path.join(folder, "job.json"), {}) or {}
    try:
        with open(os.path.join(folder, "rc")) as fh:
            rc = fh.read().strip()
    except OSError:
        rc = None
    tail = ""
    for name in JOB_LOGS:
        try:
            with open(os.path.join(folder, name), errors="replace") as fh:
                lines = [l for l in fh.read().splitlines() if l.strip()]
        except OSError:
            continue
        tail = lines[-1] if lines else ""
        break
    return (meta if isinstance(meta, dict) else {}), rc, tail


def job_names(cfg):
    """The web job folders, newest first (their names start with the time they were made)."""
    try:
        return sorted(os.listdir(jobs_dir(cfg)), reverse=True)
    except FileNotFoundError:
        return []


def render_jobs(cfg):
    folder = jobs_dir(cfg)
    names = job_names(cfg)[:10]
    out = ["<section><h2>網頁送出的(最近 10 件)</h2>"]
    if not names:
        out.append("<p class='quiet'>還沒有。</p>")
    for name in names:
        meta, rc, tail = job_state(os.path.join(folder, name))
        status = {None: "處理中", "0": "完成", "3": "要你決定(看收件匣)"}.get(rc, "失敗(rc %s)" % rc)
        who = " · %s" % e(meta["by"]) if meta.get("by") else ""
        out.append("<div class='page'><div class='ph'>%s · %s%s · %s</div><pre>%s</pre></div>" % (
            e(meta.get("at")), e(meta.get("what")), who, e(status), e(tail[:300])))
    out.append("</section>")
    return "\n".join(out)


ACTIONS = ("revise", "retry", "cancel", "group-ready", "group-cancel", "rerun", "ack")
TICKET_ACTIONS = ("revise", "retry", "cancel")
GROUP_ACTIONS = ("group-ready", "group-cancel", "rerun")


def act_problem(action, subject, page, text):
    """Why a POST act cannot be run, one line; None when its shape is fine (the scripts judge the state)."""
    if action not in ACTIONS:
        return "unknown action"
    if not re.fullmatch(r"[0-9A-Za-z_,.-]{1,120}", subject) or not re.fullmatch(r"[0-9A-Za-z_,.-]{0,120}", page):
        return "bad subject or page"
    if action in TICKET_ACTIONS and not re.fullmatch(r"[0-9]+", subject):
        return "%s is for a ticket id" % action
    if action in GROUP_ACTIONS and not re.fullmatch(r"g[0-9]+", subject):
        return "%s is for a group g<N>" % action
    if action == "ack" and not page:
        return "ack needs a page"
    if action == "revise" and not text:
        return "write how the ticket should change"
    return None


def act_draft(subject, page):
    """The draft key of a card's textarea, also what ?sent= names after its form was sent."""
    return "act-%s" % subject + ("-%s" % page if page else "")


def act_command(action, subject, page, text, by):
    """(script, argv after the script) one card button runs; '{input}' stands for the job's input.txt.
    Every command carries --by; text values ride in one element ('--why=<text>') so a leading '-' stays text."""
    who = ["--by", by]
    on_page = ["--page", page] if page else []
    if action in TICKET_ACTIONS:
        return "ticket_creator.py", ["--revise", subject, "--from", "user", "--decision", action, "--via", "web",
                                     "--reason-file", "{input}"] + who + on_page
    if action == "group-ready":
        note = ["--note=" + text, "--via", "web"] if text else []
        return "tickets.py", ["ready", subject] + note + who + on_page
    if action == "group-cancel":
        return "tickets.py", ["cancel", subject, "--why=" + text] + who + on_page
    if action == "rerun":
        return "tickets.py", ["rerun", subject, "--why=" + text] + who + on_page
    if action == "ack":
        return "tickets.py", ["ack"] + who + ["--", page]
    raise ValueError("unknown action %r" % action)


def submit(cfg, what, argv_tail, text, by=None, script="ticket_creator.py", act=None, add_by=True):
    """Start one devloop script (ticket_creator.py or tickets.py) for one web input, detached; its
    output and rc stay in runs/web/. `act` (subject, page, action) goes into job.json for a card's
    button; `add_by` False when argv_tail already carries --by."""
    t = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(t)) + ".%06d" % int(t % 1 * 1e6)
    folder = osx.makedirs(os.path.join(jobs_dir(cfg), "%s-%s" % (stamp, os.urandom(3).hex())))
    text_path = os.path.join(folder, "input.txt")
    osx.atomic_write(text_path, text)
    meta = {"at": osx.now(), "what": what}
    if act:
        meta.update(zip(("subject", "page", "action"), act))
    if by:
        meta["by"] = by
    osx.write_json(os.path.join(folder, "job.json"), meta)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    argv = ["sh", "-c", 'python3 "$@"; echo $? > "$DEVLOOP_JOB_RC"', "devloop-web", path] + \
        [text_path if a == "{input}" else a for a in argv_tail] + (["--by", by] if by and add_by else [])
    osx.spawn(argv, cwd=cfg.root, env=cfg.env(DEVLOOP_JOB_RC=os.path.join(folder, "rc")),
              log_path=os.path.join(folder, "creator.log" if script == "ticket_creator.py" else "job.log"))
    return folder


PAGE_HEAD = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(doc_title)s</title><style>
:root{--bg:#f5f6f7;--card:#fff;--ink:#1d2329;--quiet:#5c6670;--line:#d3d9de;--accent:#1f6f8b;--bad:#a23b2a;--warn:#8a5a12}
@media (prefers-color-scheme:dark){:root{--bg:#121619;--card:#1a2025;--ink:#e3e8eb;--quiet:#98a4ad;--line:#36424b;--accent:#6fb7d0;--bad:#e08a7a;--warn:#e0b06c}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Noto Sans TC","PingFang TC",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px;display:grid;gap:28px}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 10px}.quiet{color:var(--quiet)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.k{color:var(--quiet);font-size:12.5px}.v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.n{color:var(--quiet);font-size:12px}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%%;font-size:13px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--quiet);font-weight:500;font-size:12px}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.s-stuck td:nth-child(2){color:var(--bad);font-weight:700}tr.s-running td:nth-child(2){color:var(--accent);font-weight:700}
.page{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:8px;padding:10px 12px;margin-bottom:10px}
.page.k-landed,.page.k-created{border-left-color:var(--accent)}.ph{color:var(--quiet);font-size:12px}
pre{white-space:pre-wrap;margin:6px 0 0;font:13px/1.5 ui-monospace,Menlo,monospace}code{font-family:ui-monospace,Menlo,monospace}
form.ask{display:grid;gap:8px;margin-top:8px}textarea{width:100%%;box-sizing:border-box;font:14px/1.5 inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
nav.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line)}nav.tabs a{padding:6px 14px;color:var(--quiet);text-decoration:none;border-radius:8px 8px 0 0}
nav.tabs a[aria-current=page]{color:var(--ink);font-weight:700;background:var(--card);border:1px solid var(--line);border-bottom-color:var(--card);margin-bottom:-1px}
.tab{display:grid;gap:28px}
button{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--accent);background:var(--card);color:var(--ink);cursor:pointer}button:focus-visible,textarea:focus-visible{outline:2px solid var(--accent)}
</style></head><body><main><header><h1>%(title)s</h1><div class="quiet">%(now)s · 沒在打字時每 30 秒更新 · 只讀票庫,輸入交給開題者</div></header>"""

PAGE_SCRIPT = """<script>
(function () {
  var store = null;
  try { store = window.localStorage; } catch (e) { store = null; }
  function get(k) { try { return store ? store.getItem(k) : null; } catch (e) { return null; } }
  function set(k, v) { try { if (store) { v ? store.setItem(k, v) : store.removeItem(k); } } catch (e) {} }
  var sent = new URLSearchParams(location.search).get('sent');
  if (sent) { set('devloop-draft-' + sent, ''); }
  var boxes = document.querySelectorAll('textarea[data-draft]');
  boxes.forEach(function (box) {
    var k = 'devloop-draft-' + box.getAttribute('data-draft');
    var v = get(k); if (v && !box.value) { box.value = v; }
    box.addEventListener('input', function () { set(k, box.value); });
  });
  var filters = document.querySelector('form.filters');
  var search = filters ? filters.querySelector('input[name=q]') : null;
  if (filters) {
    filters.querySelectorAll('select, input[type=checkbox]').forEach(function (c) {
      c.addEventListener('change', function () { filters.submit(); });
    });
  }
  if (search) {
    var pending = null;
    search.addEventListener('input', function () {
      if (pending) { clearTimeout(pending); }
      pending = setTimeout(function () { filters.submit(); }, 500);
    });
  }
  setInterval(function () {
    var busy = search !== null && search === document.activeElement;
    if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') { busy = true; }
    boxes.forEach(function (b) { if (b === document.activeElement || b.value) { busy = true; } });
    if (!busy) { location.reload(); }
  }, 30000);
})();
</script>"""


LOOPBACK_BINDS = ("127.0.0.1", "::1", "localhost")
LOOPBACK_PEERS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def trusted_user(settings, bind_host, peer_host, header_value):
    """The board.allowed_users entry a local reverse proxy vouches for, or None (D-091 item 4).

    Trust needs all of: board.trusted_user_header set, board.allowed_users non-empty, the board
    listening on a loopback address and the request coming from a loopback peer. The header value
    is compared with each entry ignoring case and surrounding spaces; the entry itself is returned."""
    settings = settings or {}
    name = settings.get("trusted_user_header")
    users = settings.get("allowed_users")
    if not isinstance(name, str) or not name.strip() or not isinstance(users, list) or not users:
        return None
    if bind_host not in LOOPBACK_BINDS or peer_host not in LOOPBACK_PEERS:
        return None
    if not isinstance(header_value, str) or not header_value.strip():
        return None
    wanted = header_value.strip().lower()
    for user in users:
        if isinstance(user, str) and user.strip() and user.strip().lower() == wanted:
            return user
    return None


def board_settings_errors(settings):
    """Why board.trusted_user_header / board.allowed_users cannot be used, one line each; [] when fine."""
    out = []
    if not isinstance(settings, dict):
        return out
    if "trusted_user_header" in settings:
        v = settings["trusted_user_header"]
        if not isinstance(v, str) or not v.strip():
            out.append("board.trusted_user_header must be a non-empty string, got %r" % (v,))
    if "allowed_users" in settings:
        v = settings["allowed_users"]
        if not isinstance(v, list) or not all(isinstance(u, str) and u.strip() for u in v):
            out.append("board.allowed_users must be a list of non-empty strings, got %r" % (v,))
    return out


# ---------------------------------------------------------------- serving

COOKIE = "board_key"
COOKIE_AGE = 30 * 24 * 3600


def cookie_value(key):
    """What the browser keeps instead of the key itself."""
    return hmac.new(key.encode(), b"devloop-board", hashlib.sha256).hexdigest()


def authorized(key, headers):
    """True when the request carries header X-Board-Key or cookie board_key for this key."""
    given = headers.get("X-Board-Key")
    if given is not None:
        return hmac.compare_digest(given.encode(), key.encode())
    jar = SimpleCookie()
    try:
        jar.load(headers.get("Cookie") or "")
    except CookieError:
        return False
    morsel = jar.get(COOKIE)
    return morsel is not None and hmac.compare_digest(morsel.value.encode(), cookie_value(key).encode())


def key_redirect(url):
    """Where ?key= goes: the same last path segment and the other query pairs, relative, keyless."""
    rest = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k != "key"]
    return "./" + last_segment(url) + ("?" + urlencode(rest) if rest else "")


def last_segment(url):
    """Routing looks only at the last path segment, so the board works under any path prefix."""
    return url.path.rsplit("/", 1)[-1]


def serve(cfg, pool, host, port, key):
    cache = {"at": 0.0, "snap": None}
    lock = threading.Lock()
    settings = cfg.raw.get("board") or {}
    if not isinstance(settings, dict):
        settings = {}
    header = settings.get("trusted_user_header")

    class Handler(BaseHTTPRequestHandler):
        user = None     # the allowed_users entry a local proxy vouched for (who submits: next ticket)

        def _trust(self):
            value = self.headers.get(header) if isinstance(header, str) and header.strip() else None
            self.user = trusted_user(settings, host, self.client_address[0], value)
            return self.user is not None

        def do_GET(self):
            url = urlparse(self.path)
            trusted = self._trust()
            if key and not trusted:
                given = parse_qs(url.query, keep_blank_values=True).get("key")
                if given is not None:
                    if not hmac.compare_digest(given[0].encode(), key.encode()):
                        self._send(403, "wrong key")
                        return
                    self.send_response(303)
                    self.send_header("Set-Cookie", "%s=%s; HttpOnly; SameSite=Strict; Max-Age=%d"
                                     % (COOKIE, cookie_value(key), COOKIE_AGE))
                    self.send_header("Location", key_redirect(url))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if not authorized(key, self.headers):
                    self._send(403, "key required")
                    return
            if last_segment(url) not in ("", "index.html"):
                self._send(404, "not found")
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                with lock:
                    if cache["snap"] is None or time.time() - cache["at"] > 5:
                        cache["snap"] = pool.snapshot()
                        cache["at"] = time.time()
                    snap = cache["snap"]
                page = render(cfg, snap, key or (trusted or None), query)
            except osx.OSFailure as exc:
                page = "<pre>%s</pre>" % e(exc)
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            trusted = self._trust()
            if not key and not trusted:
                self._send(403, "this board has no key file: read-only")
                return
            if not trusted and not authorized(key, self.headers):
                self._send(403, "key required")
                return
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if size < 0 or size > 64 * 1024:
                self._send(413, "too large")
                return
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode("utf-8", "replace")).items()}
            text = form.get("text", "").strip()
            try:
                if last_segment(url) == "request":
                    if not text:
                        self._send(400, "empty request")
                        return
                    submit(cfg, "新需求", ["--request-file", "{input}", "--via", "web"], text, by=self.user)
                    sent, where = "request", "./?sent="
                elif last_segment(url) == "act":
                    subject, page = form.get("subject", ""), form.get("page", "")
                    action = form.get("action", "")
                    problem = act_problem(action, subject, page, text)
                    if problem:
                        self._send(400, problem)
                        return
                    by = self.user or "user"     # D-093 item 3: the trusted user's entry, else 'user'
                    script, argv = act_command(action, subject, page, text, by)
                    label = "#" + subject if re.fullmatch(r"[0-9]+", subject) else subject
                    submit(cfg, "%s %s" % (label, ACT_LABELS[action]), argv, text, by=by, script=script,
                           act=(subject, page, action), add_by=False)
                    sent, where = act_draft(subject, page), "./?tab=decide&sent="
                else:
                    self._send(404, "not found")
                    return
            except osx.OSFailure as exc:
                self._send(500, str(exc))
                return
            cache["snap"] = None
            self.send_response(303)
            self.send_header("Location", where + quote(sent))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, code, body, ctype="text/plain; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot listen on %s:%s" % (host, port)))
    print("board: http://%s:%d/" % (host, server.server_address[1]), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("html")
    p.add_argument("--out")
    p.add_argument("--query", default="", help="the page's query, e.g. tab=dag")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--key-file")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        if args.cmd == "html":
            query = {k: v[0] for k, v in parse_qs(args.query).items()}
            page = render(cfg, pool.snapshot(), query=query)     # a file has no key: read-only
            if args.out:
                osx.atomic_write(args.out, page)
            else:
                sys.stdout.write(page)
            return 0
        problems = board_settings_errors(cfg.raw.get("board"))
        if problems:
            for line in problems:
                print("board: %s" % line, file=sys.stderr)
            return 2
        key = None
        if args.key_file:
            with open(args.key_file) as fh:
                key = fh.read().strip()
            if not key:
                raise osx.OSFailure("key file %s is empty" % args.key_file)
        return serve(cfg, pool, args.host, args.port, key)
    except OSError as exc:
        print("board: %s" % osx.explain(exc, "cannot read the key file"), file=sys.stderr)
        return 2
    except osx.OSFailure as exc:
        print("board: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
