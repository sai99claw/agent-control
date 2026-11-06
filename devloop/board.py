#!/usr/bin/env python3
"""board : the dashboard. Reads the pool; never writes it.

    python3 devloop/board.py html [--out board.html] [--query tab=dag]   # one page, to a file or stdout
    python3 devloop/board.py serve [--port 8765] [--host 127.0.0.1] [--key-file F]

What it shows (VISION 「看板與統計」): the four numbers, every ticket (where it is, who works on
it, what it waits for), the integration branches, what waits for the user, and memory.
A number it cannot know is shown as 「未知」, never as 0.

With --key-file a browser opens the board once with `?key=`; the board trades it for an HttpOnly,
SameSite=Strict cookie and redirects to the same page without the key, so the key never appears in
a URL or a page. Scripts send header `X-Board-Key` instead. The page then gets two inputs. They are another front-end to the main session's own entry, nothing more
(D-083): a new request runs `ticket_creator.py --request-file ... --via web`; an answer to a page
about a ticket runs `ticket_creator.py --revise N --from user --decision revise|retry|cancel
--via web`. Only the opener's path writes tickets; web text first passes the injection check of
prompts/web-guard.md. Unsent text is kept in the viewer's browser only.
"""

import argparse
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
    ("tickets", "票", lambda cfg, snap, key, query, now: render_tickets_tab(cfg, snap, key, query, now)),
    ("dag", "DAG", lambda cfg, snap, key, query, now: render_dag_tab(cfg, snap, query, now)),
    ("tokens", "Token", lambda cfg, snap, key, query, now: render_tokens_tab(cfg, snap, query, now)),
    ("host", "主機", lambda cfg, snap, key, query, now: render_host_tab(cfg, snap, query, now)),
)


def clock(now):
    """`now` (seconds since the epoch) in the pool's timestamp format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def shown_tab(query):
    """The name of the tab a query asks for; anything unknown is the first tab."""
    wanted = (query or {}).get("tab")
    return wanted if wanted in [name for name, _, _ in TABS] else TABS[0][0]


def render_nav(shown):
    links = []
    for name, label, _ in TABS:
        current = ' aria-current="page"' if name == shown else ""
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
    shown = shown_tab(query)
    body = dict((name, fn) for name, _, fn in TABS)[shown](cfg, snap, key, query, now)
    return "\n".join([PAGE_HEAD % {"title": e("devloop · " + cfg.name), "now": e(clock(now))},
                      render_metrics(snap), render_nav(shown),
                      '<div id="tab-%s" class="tab">' % e(shown), body, "</div>",
                      PAGE_SCRIPT + "</main></body></html>"])


# ---------------------------------------------------------------- tab: 票

def render_tickets_tab(cfg, snap, key, query, now):
    out = []
    if key:
        out.append("<section><h2>交代新的事</h2><form method='post' action='request' class='ask'>"
                   "<textarea name='text' data-draft='request' rows='5' maxlength='8000' required "
                   "placeholder='像跟主線說話一樣寫:要做什麼、為什麼'></textarea>"
                   "<div><button>交給開題者</button> <span class='quiet'>跟在主線下 ticket_creator.py "
                   "一樣;網頁來的文字會先做注入檢查。</span></div></form></section>")

    out.append('<section><h2>票</h2><div class="scroll"><table><tr><th>票</th><th>狀態</th><th>輪</th>'
               '<th>整合分支</th><th>相依</th><th>一起驗</th><th>驗證者</th><th>覆核</th><th>token</th>'
               '<th>快取讀</th><th>快取寫</th><th>分鐘</th><th>估 token</th><th>說明</th><th>標題</th></tr>')
    order = dagmod.topo(snap.dag) + sorted(set(snap.tickets) - set(snap.dag))
    for ident in order:
        t = snap.tickets.get(ident)
        if not t:
            continue
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
                       e(note), e(t.get("title"))))
    out.append("</table></div></section>")

    out.append('<section><h2>整合分支(一條 = 一個 PR)</h2><div class="scroll"><table><tr><th>組</th><th>狀態</th><th>原因</th>'
               '<th>分支</th><th>票</th><th>修補輪</th><th>最後一次 regression</th><th>發版</th></tr>')
    for g in sorted(snap.groups.values(), key=lambda g: int(g["id"][1:]), reverse=True):
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td><td>%s</td><td>%s</td></tr>" % (
            e(g["id"]), e(group_state_text(g)), e(CATEGORY_TEXT.get(group_category(snap, g), "")), e(g.get("branch")),
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
        ref = str(p.get("ref") or "")
        t = snap.tickets.get(ref)
        if key and p.get("kind") == "decision" and t and t.get("state") not in ("running", "merged", "landed"):
            out.append("<form method='post' action='answer' class='ask'>"
                       "<input type='hidden' name='page' value='%s'><input type='hidden' name='ticket' value='%s'>"
                       "<textarea name='text' data-draft='answer-%s' rows='3' maxlength='8000' "
                       "placeholder='你的裁決(改票時必填)'></textarea><div>"
                       "<button name='decision' value='revise'>照我的話改票再做</button> "
                       "<button name='decision' value='retry'>不改,再試一次</button> "
                       "<button name='decision' value='cancel'>取消這張票</button></div></form>"
                       % (e(name), e(ref), e(name)))
        out.append("</div>")
    out.append("<p class='quiet'>收下:<code>python3 devloop/tickets.py ack &lt;頁名&gt;</code></p></section>")
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

def render_tokens_tab(cfg, snap, query, now):
    return stub_tab("Token")


# ---------------------------------------------------------------- tab: 主機

def render_host_tab(cfg, snap, query, now):
    return stub_tab("主機")


# ---------------------------------------------------------------- web input

def jobs_dir(cfg):
    return os.path.join(cfg.worktree_root, "runs", "web")


def render_jobs(cfg):
    folder = jobs_dir(cfg)
    try:
        names = sorted(os.listdir(folder), reverse=True)[:10]
    except FileNotFoundError:
        names = []
    out = ["<section><h2>網頁送出的(最近 10 件)</h2>"]
    if not names:
        out.append("<p class='quiet'>還沒有。</p>")
    for name in names:
        meta = osx.read_json(os.path.join(folder, name, "job.json"), {}) or {}
        try:
            with open(os.path.join(folder, name, "rc")) as fh:
                rc = fh.read().strip()
        except OSError:
            rc = None
        status = {None: "處理中", "0": "完成", "3": "要你決定(看收件匣)"}.get(rc, "失敗(rc %s)" % rc)
        tail = ""
        try:
            with open(os.path.join(folder, name, "creator.log"), errors="replace") as fh:
                lines = [l for l in fh.read().splitlines() if l.strip()]
            tail = lines[-1] if lines else ""
        except OSError:
            pass
        out.append("<div class='page'><div class='ph'>%s · %s · %s</div><pre>%s</pre></div>" % (
            e(meta.get("at")), e(meta.get("what")), e(status), e(tail[:300])))
    out.append("</section>")
    return "\n".join(out)


def submit(cfg, what, argv_tail, text):
    """Start ticket_creator for one web input, detached; its output and rc stay in runs/web/."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    folder = osx.makedirs(os.path.join(jobs_dir(cfg), "%s-%s" % (stamp, os.urandom(3).hex())))
    text_path = os.path.join(folder, "input.txt")
    osx.atomic_write(text_path, text)
    osx.write_json(os.path.join(folder, "job.json"), {"at": osx.now(), "what": what})
    creator = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_creator.py")
    argv = ["sh", "-c", 'python3 "$@"; echo $? > "$DEVLOOP_JOB_RC"', "devloop-web", creator] + \
        [a.replace("{input}", text_path) for a in argv_tail]
    osx.spawn(argv, cwd=cfg.root, env=cfg.env(DEVLOOP_JOB_RC=os.path.join(folder, "rc")),
              log_path=os.path.join(folder, "creator.log"))
    return folder


PAGE_HEAD = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="20">
<title>%(title)s</title><style>
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
  setInterval(function () {
    var busy = false;
    boxes.forEach(function (b) { if (b === document.activeElement || b.value) { busy = true; } });
    if (!busy) { location.reload(); }
  }, 30000);
})();
</script>"""


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

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if key:
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
                page = render(cfg, snap, key, query)
            except osx.OSFailure as exc:
                page = "<pre>%s</pre>" % e(exc)
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            if not key:
                self._send(403, "this board has no key file: read-only")
                return
            if not authorized(key, self.headers):
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
                    submit(cfg, "新需求", ["--request-file", "{input}", "--via", "web"], text)
                    sent = "request"
                elif last_segment(url) == "answer":
                    ticket, page, decision = form.get("ticket", ""), form.get("page", ""), form.get("decision", "")
                    if not re.fullmatch(r"[0-9A-Za-z_-]{1,40}", ticket) or \
                            not re.fullmatch(r"[0-9A-Za-z_,.-]{1,120}", page) or \
                            decision not in ("revise", "retry", "cancel"):
                        self._send(400, "bad answer")
                        return
                    if decision == "revise" and not text:
                        self._send(400, "write how the ticket should change")
                        return
                    submit(cfg, "#%s %s" % (ticket, decision),
                           ["--revise", ticket, "--from", "user", "--decision", decision, "--via", "web",
                            "--reason-file", "{input}", "--page", page], text)
                    sent = "answer-" + page
                else:
                    self._send(404, "not found")
                    return
            except osx.OSFailure as exc:
                self._send(500, str(exc))
                return
            cache["snap"] = None
            self.send_response(303)
            self.send_header("Location", "./?sent=" + quote(sent))
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
