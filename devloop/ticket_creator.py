#!/usr/bin/env python3
"""ticket_creator : turn a request into tickets + DAG entries, or revise one ticket.

    python3 devloop/ticket_creator.py --request "what the user wants"      # from the main session
    python3 devloop/ticket_creator.py --request-file need.md
    python3 devloop/ticket_creator.py --revise 203 --from worker --reason "why the ticket is wrong"
    python3 devloop/ticket_creator.py --revise 203 --from user --decision revise|retry|cancel \
        --reason-file answer.txt --page <inbox page>                       # the user's answer
    ... --via web                           # input typed into the dashboard: injection check first

Opening: the prompt chain in devloop/prompts/creator/ (read in file-name order), the reference
ticket devloop/prompts/ticket.example.json and the current DAG go to the opener agent. Its hand-in is
validated here (fields, DAG references, no cycles) and written to the pool in one push; one retry
with the validation errors if it is wrong. Anything that touches direction, business logic or
user experience the request does not settle comes back as a question for the main session.

Revising (called by a worker or verifier mid-run, or by the user answering a page): the opener reads
the ticket and the reason and either fixes the ticket, keeps it (and says why), or asks the main
session. What it decided is printed for the caller.

Cost: every opener run leaves its cost row in the pool. An opening run's row is split over the tickets
it opened (integer token fields value // N, the lowest id also gets the remainder; usd and seconds
value / N; each share carries split_over, the opened ids); a request whose runs opened no ticket keeps
the rows unsplit on the decision page it writes; a revising run appends one row to the revised ticket,
whatever its outcome. A merged or landed ticket is frozen: every
--revise of it is refused before anything starts (D-089).

Exit: 0 done · 1 the opener's hand-in stayed unusable (reported) · 3 a question for the user
(reported; a revising worker must stop and hand in kind "blocked") · 2 refused.
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, inflight, memory, osx, pages  # noqa: E402
from core import dag as dagmod  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.pool import event, frozen_reason  # noqa: E402

PROMPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
TEXT_FIELDS = ("title", "objective", "outline", "test_plan", "notes")
EDITABLE = TEXT_FIELDS + ("acceptance", "needs_verifier", "complexity", "effort", "token_estimate",
                          "token_basis")


def editable(cfg):
    """Fields the opener may set: the fixed ones plus the project's required ones."""
    return EDITABLE + tuple(k for k in (cfg.required_fields or {}) if k not in EDITABLE)
REVISE_TIMEOUT = 540          # a worker waits for this inside one tool call (10 min cap)


def guarded(text, via):
    """Input from the main session passes as is. Web input is cleaned (control characters,
    our own markers, length) and wrapped behind the injection check in prompts/web-guard.md."""
    if via != "web":
        return text
    return agents.web_guarded(text)


def ack(pool, page, by=None):
    if not page:
        return

    def change(tx):
        row = tx.data.inbox.get(page)
        if row is not None and not row.get("acked"):
            row["acked"], row["acked_at"], row["acked_by"] = True, osx.now(), by or "answer"
            tx.write("inbox/%s.json" % page, row)
    pool.update(change, "inbox: answered %s" % page)


def rejected(pool, ref, text, via, cost=None):
    pool.report("decision", ref, "input from the %s was NOT acted on - it looked like a prompt "
                "injection: %s" % (via, text), **pages.ask(
                    "input from the %s was not acted on (it looked like a prompt injection): drop it?" % via,
                    [pages.option("ack", "nothing was done; send it again without the flagged part if it was yours")],
                    "acting on input judged an injection is never automatic"),
                **({"cost": cost} if cost else {}))
    print("REJECTED: %s" % text)
    return 3


def chain():
    parts = []
    for path in sorted(glob.glob(os.path.join(PROMPTS, "creator", "*.md"))):
        try:
            with open(path) as fh:
                parts.append(fh.read())     # plain text: the chain has no placeholders
        except OSError as exc:
            raise osx.OSFailure(osx.explain(exc, "cannot read prompt %s" % path))
    if not parts:
        raise osx.OSFailure("no prompt chain in %s" % os.path.join(PROMPTS, "creator"))
    return "\n\n".join(parts)


def example():
    try:
        with open(os.path.join(PROMPTS, "ticket.example.json")) as fh:
            return fh.read()
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot read the reference ticket"))


# ---------------------------------------------------------------- opening

def required_section(cfg):
    fields = cfg.required_fields or {}
    if not fields:
        return "## Fields this project requires\n\nNone beyond the reference ticket."
    return "## Fields this project requires\n\nEvery ticket must also have:\n\n" + "\n".join(
        "- `%s`: %s" % (k, v) for k, v in sorted(fields.items()))


def last_full_seconds(cfg):
    """Seconds of the newest regression full run that recorded a number, or None."""
    found = []
    for f in glob.glob(os.path.join(cfg.runs_dir("regression"), "*", "full", "result.json")):
        try:
            found.append((os.path.getmtime(f), f))
        except OSError:
            pass
    for _, f in sorted(found, reverse=True):
        try:
            with open(f, encoding="utf-8") as fh:
                sec = json.load(fh).get("seconds")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(sec, (int, float)) and not isinstance(sec, bool) and sec == sec \
                and abs(sec) != float("inf"):
            return sec
    return None


def limits_section(cfg):
    sec = last_full_seconds(cfg)
    last = ("The project's last full run took %d seconds." % round(sec) if sec is not None
            else "No full run of this project is recorded yet.")
    return ("## Time limits\n\nOne agent call is limited to %d seconds (devloop.agent_timeout_seconds).\n\n%s"
            % (cfg.agent_timeout_seconds, last))


def check_new(result, snap, cfg=None):
    """Problems with an opener hand-in of kind "tickets", as sentences."""
    out = []
    tickets = result.get("tickets")
    dag = result.get("dag") or {}
    if not isinstance(tickets, list) or not tickets:
        return ["\"tickets\" must be a non-empty list"]
    keys = []
    for i, t in enumerate(tickets):
        where = "tickets[%d]" % i
        if not isinstance(t, dict):
            out.append(where + " must be an object")
            continue
        key = str(t.get("key") or "")
        if not key or key in snap.tickets:
            out.append(where + ": \"key\" must be a new placeholder like \"new-1\"")
        keys.append(key)
        for f in ("title", "objective"):
            if not isinstance(t.get(f), str) or not t.get(f).strip():
                out.append("%s: \"%s\" is required" % (where, f))
        acc = t.get("acceptance")
        if not isinstance(acc, list) or not acc or not all(isinstance(a, str) and a.strip() for a in acc):
            out.append(where + ": \"acceptance\" must be a non-empty list of checkable sentences")
        est = t.get("token_estimate")
        if not isinstance(est, int) or isinstance(est, bool) or est <= 0:
            out.append(where + ": \"token_estimate\" must be a positive whole number of tokens")
        if not isinstance(t.get("needs_verifier"), bool):
            out.append(where + ": \"needs_verifier\" must be true or false")
        if t.get("complexity") not in ("simple", "complex"):
            out.append(where + ": \"complexity\" must be \"simple\" or \"complex\"")
        if t.get("effort") is not None and t.get("effort") not in agents.EFFORTS:
            out.append(where + ": \"effort\" must be one of %s, or left out" % ", ".join(agents.EFFORTS))
        for f in sorted((cfg.required_fields if cfg else None) or {}):
            if not isinstance(t.get(f), str) or not t.get(f).strip():
                out.append("%s: \"%s\" is required by this project" % (where, f))
        if key and key not in dag:
            out.append("dag has no entry for %s" % key)
    if out:
        return out
    merged = dict(snap.dag)
    for key in keys:
        merged[key] = dag[key]
    return dagmod.problems(merged, known_tickets=list(snap.tickets) + keys)


SPLIT_INTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
SPLIT_FLOATS = ("usd", "seconds")


def split(row, ids):
    """One opener run's cost row as len(ids) shares, one per ticket it opened (in ascending id order):
    integer token fields give value // N each and the lowest id the remainder too, usd and seconds
    value / N, a null stays null, everything else is copied; every share carries split_over."""
    ids = sorted((str(i) for i in ids), key=dagmod._order_key)
    n = len(ids)
    shares = []
    for k in range(n):
        share = dict(row)
        for f in SPLIT_INTS:
            v = row.get(f)
            if isinstance(v, int) and not isinstance(v, bool):
                share[f] = v // n + (v % n if k == 0 else 0)
        for f in SPLIT_FLOATS:
            v = row.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                share[f] = v / n
        share["split_over"] = list(ids)
        shares.append(share)
    return shares


def write_new(pool, result, request, fields=EDITABLE, by=None, cost=None):
    def change(tx):
        ids, mapping = [], {}
        nxt = int(tx.data.next_ticket_id())
        for t in result["tickets"]:
            mapping[str(t["key"])] = str(nxt)
            nxt += 1
        dag = tx.data.dag
        for t in result["tickets"]:
            ident = mapping[str(t["key"])]
            row = {k: t[k] for k in fields if k in t}
            row.update({"id": ident, "state": "ready", "round": 0, "created": osx.now(),
                        "request": request[:4000], "feedback": [], "cases": None})
            event(row, "opened", **({"by": by} if by else {}))
            tx.put_ticket(row)
            node = dict(result["dag"][str(t["key"])])
            node["depends_on"] = [mapping.get(str(d), str(d)) for d in node.get("depends_on") or []]
            node.setdefault("verify", "together")
            dag[ident] = node
            ids.append(ident)
        if cost:
            shares = [split(r, ids) for r in cost]
            for k, ident in enumerate(sorted(ids, key=dagmod._order_key)):
                row = tx.data.tickets[ident]
                row["cost"] = list(row.get("cost") or []) + [s[k] for s in shares]
                tx.put_ticket(row)
        tx.put_dag({"tickets": dag})
        return ids
    return pool.update(change, "tickets: open from a request")


def opener_live(cfg, title=None, revise=None, via="main"):
    """Put this process's opener record (core/inflight.py); returns its name for inflight.drop."""
    name = "opener-%d" % os.getpid()
    creator = cfg.agent("creator")
    inflight.put(cfg, name, {
        "kind": "opener", "stamp": osx.stamp(), "title": title, "revise": revise, "via": via,
        "model": agents.model_of(creator), "effort": agents.effort_of(creator), "result": None})
    return name


def open_tickets(cfg, pool, request, via="main", by=None):
    name = opener_live(cfg, title=inflight.title_of(request), via=via)
    try:
        return _open_tickets(cfg, pool, request, via, by)
    finally:
        inflight.drop(cfg, name)


def _open_tickets(cfg, pool, request, via, by):
    snap = pool.snapshot()
    summary = [{"id": k, "title": v.get("title"), "state": v.get("state")}
               for k, v in sorted(snap.tickets.items(), key=lambda kv: dagmod._order_key(kv[0]))
               if v.get("state") not in ("landed", "cancelled")]
    base = "\n\n".join([
        chain(),
        "## What earlier openers learned\n\n%s\n\nProject memory (design reasons and invariants): %s" % (
            memory.for_prompt(cfg, "creator", agents._model(cfg.agent("creator"))),
            memory.project_path(cfg)),
        "## Reference ticket\n\n```json\n%s\n```" % example(),
        required_section(cfg),
        limits_section(cfg),
        "## Tickets still open\n\n```json\n%s\n```" % json.dumps(summary, indent=2, ensure_ascii=False),
        "## Current DAG\n\n```json\n%s\n```" % json.dumps(snap.dag, indent=2, ensure_ascii=False),
        "## The request\n\n%s" % guarded(request, via),
    ])
    errors, rows = [], []
    for attempt in (1, 2):
        run_dir = cfg.runs_dir("creator", "%s-a%d" % (osx.now().replace(":", ""), attempt))
        prompt = base + ("\n\n## Your previous hand-in was rejected\n\n- " + "\n- ".join(errors)
                         if errors else "")
        prompt += "\n\nWrite your hand-in as JSON to: %s\n" % os.path.join(run_dir, "result.json")
        hand = agents.run(cfg, "creator", prompt, cfg.root, run_dir, cfg.agent_timeout_seconds)
        if hand.cost:
            rows.append(hand.cost)
        memory.collect(pool, hand, "request (opener)")
        if hand.problem():
            errors = [hand.problem()]
            continue
        if hand.kind == "rejected":
            return rejected(pool, "request", hand.result.get("text"), via, cost=rows)
        if hand.kind == "question":
            pool.report("decision", "request", "the opener needs your decision before opening "
                        "tickets:\n%s" % hand.result.get("text"), request=request[:2000], cost=rows)
            print(hand.result.get("text"))
            return 3
        if hand.kind != "tickets":
            errors = ["\"kind\" must be \"tickets\", \"question\" or \"rejected\""]
            continue
        errors = check_new(hand.result, pool.snapshot(), cfg)
        if errors:
            continue
        ids = write_new(pool, hand.result, request, editable(cfg), by=by, cost=rows)
        titles = {i: pool.snapshot().tickets[i].get("title") for i in ids}
        pool.report("created", ",".join(ids), "opened " + "; ".join(
            "#%s %s" % (i, titles[i]) for i in ids))
        print("opened: " + " ".join("#" + i for i in ids))
        return 0
    pool.report("decision", "request", "the opener could not produce valid tickets:\n- %s" % (
        "\n- ".join(errors)), request=request[:2000], cost=rows, **pages.ask(
            "the opener could not produce valid tickets: send the request again?",
            [pages.option("ack", "nothing was opened; send the request again with `actl new --request` after reading the errors")],
            "nothing was written to the pool, and the errors above say what was missing"))
    return 1


# ---------------------------------------------------------------- revising

def user_reset(pool, ident, state, note, changes=None, cases_wrong=False, by=None):
    """The user decided: the ticket goes back to ready (round 0) or is cancelled."""
    def change(tx):
        row = tx.ticket(ident)
        frozen = frozen_reason(ident, row.get("state"))
        if frozen:
            raise osx.OSFailure(frozen)
        if row.get("state") == "running":
            raise osx.OSFailure("#%s is %s; it cannot be changed now" % (ident, row.get("state")))
        row.update(changes or {})
        if cases_wrong:
            row["cases"] = None
        row["state"] = state
        if state == "ready":
            row["round"], row["stuck"], row["run"] = 0, None, None
            group = tx.data.groups.get(row.get("group") or "")
            if not group or group.get("state") != "open":
                row["group"], row["target"] = None, None
        event(row, "user decision: %s" % state, note=note[:500], fields=sorted(changes or {}),
              **({"by": by} if by else {}))
        tx.put_ticket(row)
    pool.update(change, "#%s: %s (user decision)" % (ident, state))


def decide(cfg, pool, ident, decision, text, via, page, by=None):
    """A user's answer to a page about one ticket: revise | retry | cancel."""
    def record(tx):
        row = tx.ticket(ident)
        if row.get("state") == "running":
            raise osx.OSFailure("#%s is running; it cannot be changed now" % ident)
        detail = {"decision": decision, "text": (text or "")[:2000], "by": by or "user"}
        if page:
            detail["page"] = page
        event(row, "user answer", **detail)
        tx.put_ticket(row)
    pool.update(record, "#%s: user answer (%s)" % (ident, decision))
    if decision == "cancel":
        user_reset(pool, ident, "cancelled", text or "cancelled by the user", by=by)
        ack(pool, page, by)
        print("CANCELLED #%s" % ident)
        return 0
    if decision == "retry":
        user_reset(pool, ident, "ready", text or "try again unchanged", by=by)
        ack(pool, page, by)
        print("READY AGAIN #%s" % ident)
        return 0
    rc = revise(cfg, pool, ident, "user", text, via=via, by=by)
    if rc in (0, 3):
        ack(pool, page, by)
    return rc


def apply_revised(cfg, pool, ident, source, res, run_name, applied_by=None):
    """Write the opener's kind 'revised' hand-in `res` into the pool; returns the fields it changed.
    `run_name` is the revise-* directory the hand-in came from (it is the event's run field)."""
    changes = {k: v for k, v in (res.get("ticket") or {}).items() if k in editable(cfg)}

    def change(tx):
        row = tx.ticket(ident)
        frozen = frozen_reason(ident, row.get("state"))
        if frozen:
            raise osx.OSFailure(frozen)
        row.update(changes)
        if res.get("cases_wrong"):
            row["cases"] = None
        extra = {"applied_by": applied_by} if applied_by else {}
        event(row, "revised", source=source, fields=sorted(changes),
              cases_wrong=bool(res.get("cases_wrong")), run=run_name, **extra)
        tx.put_ticket(row)
    pool.update(change, "#%s: revised (%s)" % (ident, source))
    return changes


def apply_unapplied(cfg, pool, ident, since):
    """Write into the pool every kind 'revised' hand-in under runs/<ident>/revise-* that the opener
    wrote at or after `since` and that no 'revised' event names yet. This recovers a revision whose
    ticket_creator.py was stopped (with the worker that ran it) after the opener handed in.
    A user revision, an older, unreadable or other-kind hand-in is skipped. Returns the names applied."""
    base = os.path.join(cfg.worktree_root, "runs", str(ident))
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("revise-"))
    except OSError:
        return []
    applied = []
    for name in names:
        d = os.path.join(base, name)
        try:
            meta = osx.read_json(os.path.join(d, "revise.json"))
            res = osx.read_json(os.path.join(d, "result.json"))
            mtime = os.path.getmtime(os.path.join(d, "result.json"))
        except (OSError, ValueError, osx.OSFailure):
            continue
        if not isinstance(meta, dict) or not isinstance(res, dict):
            continue
        source = meta.get("source")
        if not source or source == "user" or mtime < since or res.get("kind") != "revised":
            continue
        history = pool.snapshot().ticket(ident).get("history") or []
        if any(e.get("event") == "revised" and e.get("run") == name for e in history):
            continue
        apply_revised(cfg, pool, ident, source, res, name, applied_by="issuer")
        applied.append(name)
    return applied


def revise(cfg, pool, ident, source, reason, via="main", by=None):
    name = opener_live(cfg, revise=str(ident), via=via)
    try:
        return _revise(cfg, pool, ident, source, reason, via, by)
    finally:
        inflight.drop(cfg, name)


def _revise(cfg, pool, ident, source, reason, via, by):
    snap = pool.snapshot()
    t = snap.ticket(ident)
    run_dir = cfg.runs_dir(ident, "revise-%s" % osx.now().replace(":", ""))
    if source == "user":
        why = ("The user decided how this ticket should change (it was stuck or needed a decision). "
               "Apply that decision to the ticket; ask only if the decision itself is unclear.\n\n%s"
               % guarded(reason, via))
    else:
        why = "The %s working on it says the ticket is wrong:\n\n> %s" % (source, reason)
    prompt = agents.render("reviser.md", {
        "ticket_id": ident, "source": source, "reason": reason, "why": why,
        "ticket_json": json.dumps({k: t.get(k) for k in editable(cfg) + ("cases",)}, indent=2, ensure_ascii=False),
        "dag_json": json.dumps(snap.dag.get(ident) or {}, indent=2, ensure_ascii=False),
        "result_path": os.path.join(run_dir, "result.json"),
    })
    osx.write_json(os.path.join(run_dir, "revise.json"), {"ticket": ident, "source": source})
    hand = agents.run(cfg, "creator", prompt, cfg.root, run_dir,
                      min(REVISE_TIMEOUT, cfg.agent_timeout_seconds),
                      extra_env={"DEVLOOP_TICKET": ident})
    if hand.cost:
        def record(tx):
            row = tx.data.tickets.get(ident)
            if row is not None:
                row["cost"] = list(row.get("cost") or []) + [hand.cost]
                tx.put_ticket(row)
        pool.update(record, "tickets: #%s revise cost" % ident)
    memory.collect(pool, hand, "#%s revise (opener)" % ident)
    if hand.problem():
        print("REVISE FAILED: %s - continue with the ticket as it is, or hand in kind "
              "\"blocked\" if you cannot." % hand.problem())
        return 1
    res = hand.result
    if res.get("kind") == "rejected":
        return rejected(pool, ident, res.get("text"), via)
    if source == "user" and res.get("kind") in ("revised", "unchanged"):
        changes = {k: v for k, v in (res.get("ticket") or {}).items() if k in editable(cfg)} \
            if res.get("kind") == "revised" else {}
        user_reset(pool, ident, "ready", res.get("note") or "", changes, bool(res.get("cases_wrong")), by=by)
        print("READY AGAIN after your decision: %s" % (res.get("note") or ""))
        return 0
    if res.get("kind") == "question":
        pool.report("decision", ident, "#%s needs your decision (raised by the %s): %s" % (
            ident, source, res.get("text")))
        print("ESCALATED to the main session: %s\nStop now and hand in "
              "{\"kind\": \"blocked\", \"text\": \"waiting for the user's decision\"}." % res.get("text"))
        return 3
    if res.get("kind") == "revised":
        changes = apply_revised(cfg, pool, ident, source, res, os.path.basename(run_dir))
        print("REVISED: %s\nChanged: %s%s" % (res.get("note") or "", ", ".join(sorted(changes)) or "nothing",
              "\nThe verifier's cases were judged wrong; they are written again next round."
              if res.get("cases_wrong") else ""))
        return 0
    if res.get("kind") != "unchanged":
        print("REVISE FAILED: the opener handed in kind %r - nothing changed" % res.get("kind"))
        return 1
    print("UNCHANGED: %s" % (res.get("note") or "the ticket stands as written"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--request")
    ap.add_argument("--request-file")
    ap.add_argument("--revise", metavar="ID")
    ap.add_argument("--from", dest="source", default="worker")
    ap.add_argument("--reason", default="")
    ap.add_argument("--reason-file")
    ap.add_argument("--via", choices=("main", "web"), default="main")
    ap.add_argument("--decision", choices=("revise", "retry", "cancel"), default="revise",
                    help="with --from user: what to do with the ticket")
    ap.add_argument("--by", default=None, help="the name of the trusted user who submitted this")
    ap.add_argument("--page", help="the inbox page this answers; marked read when handled")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        reason = args.reason
        if args.reason_file:
            with open(args.reason_file) as fh:
                reason = fh.read()
        if args.revise:
            current = pool.snapshot().tickets.get(str(args.revise)) or {}
            frozen = frozen_reason(str(args.revise), current.get("state"))
            if frozen:
                print("ticket_creator: %s" % frozen, file=sys.stderr)
                return 2
            if args.source == "user":
                if args.decision == "revise" and not reason.strip():
                    print("ticket_creator: a revise decision needs the user's words", file=sys.stderr)
                    return 2
                return decide(cfg, pool, str(args.revise), args.decision, reason, args.via, args.page, by=args.by)
            if not reason.strip():
                print("ticket_creator: --revise needs --reason", file=sys.stderr)
                return 2
            return revise(cfg, pool, str(args.revise), args.source, reason)
        request = args.request
        if args.request_file:
            with open(args.request_file) as fh:
                request = fh.read()
        if not request or not request.strip():
            print("ticket_creator: give --request or --request-file", file=sys.stderr)
            return 2
        return open_tickets(cfg, pool, request, via=args.via, by=args.by)
    except OSError as exc:
        print("ticket_creator: %s" % osx.explain(exc, "cannot read the request"), file=sys.stderr)
        return 2
    except osx.OSFailure as exc:
        print("ticket_creator: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
