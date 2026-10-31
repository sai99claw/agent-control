#!/usr/bin/env python3
"""board : the dashboard. Reads the pool; never writes it.

    python3 devloop/board.py html [--out board.html]       # one page, to a file or stdout
    python3 devloop/board.py serve [--port 8765] [--host 127.0.0.1] [--key-file F]

What it shows (VISION 「看板與統計」): the four numbers, every ticket (where it is, who works on
it, what it waits for), the integration branches, what waits for the user, and memory.
A number it cannot know is shown as 「未知」, never as 0.

With --key-file every request must carry the key (`?key=` or header `X-Board-Key`), and the page
gets two inputs. They are another front-end to the main session's own entry, nothing more
(D-083): a new request runs `ticket_creator.py --request-file ... --via web`; an answer to a page
about a ticket runs `ticket_creator.py --revise N --from user --decision revise|retry|cancel
--via web`. Only the opener's path writes tickets; web text first passes the injection check of
prompts/web-guard.md. Unsent text is kept in the viewer's browser only.
"""

import argparse
import hmac
import html
import os
import re
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dag as dagmod  # noqa: E402
from core import memory, osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402

UNKNOWN = "未知"
STATE_TEXT = {"ready": "待派", "running": "做中", "merged": "已合進整合分支", "landed": "已落地",
              "stuck": "卡住", "cancelled": "取消"}
GROUP_TEXT = {"open": "收票中", "pr": "PR(待驗)", "fixing": "修紅中", "verified": "已驗,待落地",
              "landed": "已落地", "stuck": "卡住"}
REASON_TEXT = {"rounds": "3 輪用完", "timeout": "超時", "decision": "要你決定", "hard_block": "碰到 hard_block",
               "env": "環境", "error": "程式錯誤", "target": "整合分支不見"}


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


# ---------------------------------------------------------------- page

def e(text):
    return html.escape("" if text is None else str(text))


def render(cfg, snap, key=None):
    m = metrics(snap)
    out = [PAGE_HEAD % {"title": e("devloop · " + cfg.name), "now": e(osx.now())}]
    hidden = "<input type='hidden' name='key' value='%s'>" % e(key) if key else ""
    if key:
        out.append("<section><h2>交代新的事</h2><form method='post' action='/request' class='ask'>%s"
                   "<textarea name='text' data-draft='request' rows='5' maxlength='8000' required "
                   "placeholder='像跟主線說話一樣寫:要做什麼、為什麼'></textarea>"
                   "<div><button>交給開題者</button> <span class='quiet'>跟在主線下 ticket_creator.py "
                   "一樣;網頁來的文字會先做注入檢查。</span></div></form></section>" % hidden)
    out.append('<section><h2>四個指標</h2><div class="cards">')
    for label, key, note in (("每張落地票的 token(中位,含快取讀)", "tokens", "越少越好"),
                             ("每張落地票的時間(中位)", "minutes", "派工到落地"),
                             ("一次就過", "first_pass", "第 1 輪就合進、沒改過票"),
                             ("每張票要你介入幾次", "asks", "決定頁與環境頁 ÷ 落地或卡住的票"),
                             ("等你處理的", "waiting", "未讀頁 + 卡住的票")):
        out.append('<div class="card"><div class="k">%s</div><div class="v">%s</div><div class="n">%s</div></div>'
                   % (e(label), e(m[key]), e(note)))
    out.append("</div></section>")

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
            st = t.get("stuck") or {}
            note = "%s:%s" % (REASON_TEXT.get(st.get("reason"), st.get("reason")), (st.get("text") or "")[:120])
        elif state == "running":
            run = t.get("run") or {}
            note = "issuer 跑了 %d 分" % (osx.age(run.get("started")) / 60) if run.get("started") else "剛派出"
        elif t.get("feedback"):
            note = "上一輪:" + t["feedback"][-1][:120]
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

    out.append('<section><h2>整合分支(一條 = 一個 PR)</h2><div class="scroll"><table><tr><th>組</th><th>狀態</th>'
               '<th>分支</th><th>票</th><th>修補輪</th><th>最後一次 regression</th><th>發版</th></tr>')
    for g in sorted(snap.groups.values(), key=lambda g: int(g["id"][1:]), reverse=True):
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td><td>%s</td><td>%s</td></tr>" % (
            e(g["id"]), e(GROUP_TEXT.get(g.get("state"), g.get("state"))), e(g.get("branch")),
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
        if key and p.get("kind") == "decision" and t and t.get("state") not in ("running", "landed"):
            out.append("<form method='post' action='/answer' class='ask'>%s"
                       "<input type='hidden' name='page' value='%s'><input type='hidden' name='ticket' value='%s'>"
                       "<textarea name='text' data-draft='answer-%s' rows='3' maxlength='8000' "
                       "placeholder='你的裁決(改票時必填)'></textarea><div>"
                       "<button name='decision' value='revise'>照我的話改票再做</button> "
                       "<button name='decision' value='retry'>不改,再試一次</button> "
                       "<button name='decision' value='cancel'>取消這張票</button></div></form>"
                       % (hidden, e(name), e(ref), e(name)))
        out.append("</div>")
    out.append("<p class='quiet'>收下:<code>python3 devloop/tickets.py ack &lt;頁名&gt;</code></p></section>")
    if key:
        out.append(render_jobs(cfg))

    out.append('<section><h2>記憶</h2><div class="scroll"><table><tr><th>檔</th><th>字元</th><th>暫存區</th>'
               '<th>上次整理</th></tr>')
    last = (snap.memory_state or {}).get("last") or {}
    for path, text in sorted(snap.memory.items()):
        layer = path.split("/")[1] if path.count("/") == 2 else "project"
        name = os.path.basename(path)[:-3] if layer != "project" else "project"
        out.append("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td>%s</td></tr>" % (
            e(path), e(len(text)), e(len(memory.pending(snap, layer, name))), e(last.get(path) or "")))
    out.append("</table></div></section>")
    out.append(PAGE_SCRIPT + "</main></body></html>")
    return "\n".join(out)


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

def serve(cfg, pool, host, port, key):
    cache = {"at": 0.0, "page": ""}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if key:
                given = (parse_qs(url.query).get("key") or [""])[0] or self.headers.get("X-Board-Key", "")
                if not hmac.compare_digest(given.encode(), key.encode()):
                    self._send(403, "key required")
                    return
            if url.path not in ("/", "/index.html"):
                self._send(404, "not found")
                return
            with lock:
                if time.time() - cache["at"] > 5:
                    try:
                        cache["page"] = render(cfg, pool.snapshot(), key)
                    except osx.OSFailure as exc:
                        cache["page"] = "<pre>%s</pre>" % e(exc)
                    cache["at"] = time.time()
                page = cache["page"]
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            if not key:
                self._send(403, "this board has no key file: read-only")
                return
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if size < 0 or size > 64 * 1024:
                self._send(413, "too large")
                return
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode("utf-8", "replace")).items()}
            if not hmac.compare_digest(form.get("key", "").encode(), key.encode()):
                self._send(403, "key required")
                return
            text = form.get("text", "").strip()
            try:
                if url.path == "/request":
                    if not text:
                        self._send(400, "empty request")
                        return
                    submit(cfg, "新需求", ["--request-file", "{input}", "--via", "web"], text)
                    sent = "request"
                elif url.path == "/answer":
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
            cache["at"] = 0.0
            self.send_response(303)
            self.send_header("Location", "/?key=%s&sent=%s" % (quote(key), quote(sent)))
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
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--key-file")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        if args.cmd == "html":
            page = render(cfg, pool.snapshot())     # a file has no key: read-only
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
