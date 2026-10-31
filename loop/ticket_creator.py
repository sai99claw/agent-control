#!/usr/bin/env python3
"""ticket_creator : turn a request into tickets + DAG entries, or revise one ticket.

    python3 loop/ticket_creator.py --request "what the user wants"      # from the main session
    python3 loop/ticket_creator.py --request-file need.md
    python3 loop/ticket_creator.py --revise 203 --from worker --reason "why the ticket is wrong"

Opening: the prompt chain in loop/prompts/creator/ (read in file-name order), the reference
ticket loop/prompts/ticket.example.json and the current DAG go to the opener agent. Its hand-in is
validated here (fields, DAG references, no cycles) and written to the pool in one push; one retry
with the validation errors if it is wrong. Anything that touches direction, business logic or
user experience the request does not settle comes back as a question for the main session.

Revising (called by a worker or fixer mid-run): the opener reads the ticket and the reason and
either fixes the ticket, keeps it (and says why), or asks the main session. What it decided is
printed for the caller.

Exit: 0 done · 1 the opener's hand-in stayed unusable (reported) · 3 a question for the user
(reported; a revising worker must stop and hand in kind "blocked") · 2 refused.
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, osx  # noqa: E402
from core import dag as dagmod  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.pool import event  # noqa: E402

PROMPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
TEXT_FIELDS = ("title", "objective", "outline", "test_plan", "notes")
EDITABLE = TEXT_FIELDS + ("acceptance", "needs_verifier", "needs_review")
REVISE_TIMEOUT = 540          # a worker waits for this inside one tool call (10 min cap)


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

def check_new(result, snap):
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
        for f in ("needs_verifier", "needs_review"):
            if not isinstance(t.get(f), bool):
                out.append("%s: \"%s\" must be true or false" % (where, f))
        if key and key not in dag:
            out.append("dag has no entry for %s" % key)
    if out:
        return out
    merged = dict(snap.dag)
    for key in keys:
        merged[key] = dag[key]
    return dagmod.problems(merged, known_tickets=list(snap.tickets) + keys)


def write_new(pool, result, request):
    def change(tx):
        ids, mapping = [], {}
        nxt = int(tx.data.next_ticket_id())
        for t in result["tickets"]:
            mapping[str(t["key"])] = str(nxt)
            nxt += 1
        dag = tx.data.dag
        for t in result["tickets"]:
            ident = mapping[str(t["key"])]
            row = {k: t[k] for k in EDITABLE if k in t}
            row.update({"id": ident, "state": "ready", "round": 0, "created": osx.now(),
                        "request": request[:4000], "feedback": [], "cases": None})
            event(row, "opened")
            tx.put_ticket(row)
            node = dict(result["dag"][str(t["key"])])
            node["depends_on"] = [mapping.get(str(d), str(d)) for d in node.get("depends_on") or []]
            node.setdefault("verify", "together")
            dag[ident] = node
            ids.append(ident)
        tx.put_dag({"tickets": dag})
        return ids
    return pool.update(change, "tickets: open from a request")


def open_tickets(cfg, pool, request):
    snap = pool.snapshot()
    summary = [{"id": k, "title": v.get("title"), "state": v.get("state")}
               for k, v in sorted(snap.tickets.items(), key=lambda kv: dagmod._order_key(kv[0]))
               if v.get("state") not in ("landed", "cancelled")]
    base = "\n\n".join([
        chain(),
        "## Reference ticket\n\n```json\n%s\n```" % example(),
        "## Tickets still open\n\n```json\n%s\n```" % json.dumps(summary, indent=2, ensure_ascii=False),
        "## Current DAG\n\n```json\n%s\n```" % json.dumps(snap.dag, indent=2, ensure_ascii=False),
        "## The request\n\n%s" % request,
    ])
    errors = []
    for attempt in (1, 2):
        run_dir = cfg.runs_dir("creator", "%s-a%d" % (osx.now().replace(":", ""), attempt))
        prompt = base + ("\n\n## Your previous hand-in was rejected\n\n- " + "\n- ".join(errors)
                         if errors else "")
        prompt += "\n\nWrite your hand-in as JSON to: %s\n" % os.path.join(run_dir, "result.json")
        hand = agents.run(cfg, "creator", prompt, cfg.root, run_dir, cfg.agent_timeout_seconds)
        if hand.problem():
            errors = [hand.problem()]
            continue
        if hand.kind == "question":
            pool.report("decision", "request", "the opener needs your decision before opening "
                        "tickets:\n%s" % hand.result.get("text"), request=request[:2000])
            print(hand.result.get("text"))
            return 3
        if hand.kind != "tickets":
            errors = ["\"kind\" must be \"tickets\" or \"question\""]
            continue
        errors = check_new(hand.result, pool.snapshot())
        if errors:
            continue
        ids = write_new(pool, hand.result, request)
        titles = {i: pool.snapshot().tickets[i].get("title") for i in ids}
        pool.report("created", ",".join(ids), "opened " + "; ".join(
            "#%s %s" % (i, titles[i]) for i in ids))
        print("opened: " + " ".join("#" + i for i in ids))
        return 0
    pool.report("decision", "request", "the opener could not produce valid tickets:\n- %s" % (
        "\n- ".join(errors)), request=request[:2000])
    return 1


# ---------------------------------------------------------------- revising

def revise(cfg, pool, ident, source, reason):
    snap = pool.snapshot()
    t = snap.ticket(ident)
    run_dir = cfg.runs_dir(ident, "revise-%s" % osx.now().replace(":", ""))
    prompt = agents.render("reviser.md", {
        "ticket_id": ident, "source": source, "reason": reason,
        "ticket_json": json.dumps({k: t.get(k) for k in EDITABLE + ("cases",)}, indent=2, ensure_ascii=False),
        "dag_json": json.dumps(snap.dag.get(ident) or {}, indent=2, ensure_ascii=False),
        "result_path": os.path.join(run_dir, "result.json"),
    })
    hand = agents.run(cfg, "creator", prompt, cfg.root, run_dir,
                      min(REVISE_TIMEOUT, cfg.agent_timeout_seconds))
    if hand.problem():
        print("REVISE FAILED: %s - continue with the ticket as it is, or hand in kind "
              "\"blocked\" if you cannot." % hand.problem())
        return 1
    res = hand.result
    if res.get("kind") == "question":
        pool.report("decision", ident, "#%s needs your decision (raised by the %s): %s" % (
            ident, source, res.get("text")))
        print("ESCALATED to the main session: %s\nStop now and hand in "
              "{\"kind\": \"blocked\", \"text\": \"waiting for the user's decision\"}." % res.get("text"))
        return 3
    if res.get("kind") == "revised":
        changes = {k: v for k, v in (res.get("ticket") or {}).items() if k in EDITABLE}

        def change(tx):
            row = tx.ticket(ident)
            row.update(changes)
            if res.get("cases_wrong"):
                row["cases"] = None
            event(row, "revised", source=source, fields=sorted(changes),
                  cases_wrong=bool(res.get("cases_wrong")))
            tx.put_ticket(row)
        pool.update(change, "#%s: revised (%s)" % (ident, source))
        print("REVISED: %s\nChanged: %s%s" % (res.get("note") or "", ", ".join(sorted(changes)) or "nothing",
              "\nThe verifier's cases were judged wrong; they are written again next round."
              if res.get("cases_wrong") else ""))
        return 0
    print("UNCHANGED: %s" % (res.get("note") or "the ticket stands as written"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--request")
    ap.add_argument("--request-file")
    ap.add_argument("--revise", metavar="ID")
    ap.add_argument("--from", dest="source", default="worker")
    ap.add_argument("--reason", default="")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        if args.revise:
            if not args.reason.strip():
                print("ticket_creator: --revise needs --reason", file=sys.stderr)
                return 2
            return revise(cfg, pool, str(args.revise), args.source, args.reason)
        request = args.request
        if args.request_file:
            with open(args.request_file) as fh:
                request = fh.read()
        if not request or not request.strip():
            print("ticket_creator: give --request or --request-file", file=sys.stderr)
            return 2
        return open_tickets(cfg, pool, request)
    except OSError as exc:
        print("ticket_creator: %s" % osx.explain(exc, "cannot read the request"), file=sys.stderr)
        return 2
    except osx.OSFailure as exc:
        print("ticket_creator: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
