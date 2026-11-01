#!/usr/bin/env python3
"""memory_keeper --layer role|model|project --name N : consolidate one memory file.

Started only by ticket_controller, one at a time. A consolidator agent reads the file and the
lessons waiting for it and writes the whole file again (D-013: not one session deleting alone -
the consolidator is the configured higher model). The lessons it was shown are removed; ones
that arrived meanwhile stay for next time. The attempt is recorded either way, so a failing
consolidation is retried after 24 hours, not in a loop.

The file is written in the memory folder (devloop.memory, through memory.write_files); the
record of the run (memory/state.json, memory/discussions/) and the lessons stay in the pool.

Exit: 0 consolidated · 1 the consolidator's hand-in was unusable or the file could not be written (lessons kept) · 2 refused · 4 interrupted by SIGTERM/SIGINT (the running agent is stopped; the attempt is
recorded in memory/state.json 'runs' with interrupted true, the lessons stay queued and the 24-hour wait does not start).
"""

import argparse
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, osx  # noqa: E402
from core import memory  # noqa: E402
from core.config import Config, pool_for  # noqa: E402


def _run(cfg, cur, role, prompt, cwd, run_dir):
    """agents.run, but the Pending stays in `cur` while the agent runs so a signal can stop it."""
    p = agents.start(cfg, role, prompt, cwd, run_dir, cfg.agent_timeout_seconds)
    cur[:] = [p]
    return p.finish()


def keep(cfg, pool, layer, name, cur=None):
    """Returns the exit code. `cur` (a list) holds the running agent's Pending."""
    cur = [] if cur is None else cur
    costs = []
    path = memory.rel(layer, name)
    rows = memory.pending(pool.snapshot(), layer, name)
    try:
        return _keep(cfg, pool, layer, name, cur, costs)
    except agents.Interrupted:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for p in list(cur):
            row = p.stop()
            if row is None and p.hand is not None:
                row = p.hand.cost
            if row is not None and not any(row is c for c in costs):
                costs.append(row)

        def change(tx):
            state = dict(tx.data.memory_state or {})
            state.setdefault("runs", []).append({"path": path, "at": osx.now(), "ok": False, "interrupted": True,
                                                 "lessons": len(rows), "cost": costs})
            del state["runs"][:-50]
            tx.write("memory/state.json", state)
        try:
            pool.update(change, "memory: consolidating %s interrupted" % path)
        except osx.OSFailure as exc:
            print("memory_keeper: could not record the interrupted run: %s" % exc, file=sys.stderr)
        return 4


def _keep(cfg, pool, layer, name, cur, costs):
    snap = pool.snapshot()
    path = memory.rel(layer, name)
    rows = memory.pending(snap, layer, name)
    if not rows:
        return 0
    run_dir = cfg.runs_dir("memory", "%s-%s-%s" % (layer, memory._safe(name), osx.now().replace(":", "")))
    values = {
        "layer": layer, "name": name, "path": path,
        "current": memory.read(cfg, path) or memory.seed_text(cfg, layer, name),
        "lessons": json.dumps([{"text": r.get("text"), "source": r.get("source"), "at": r.get("at")}
                               for r in rows.values()], indent=2, ensure_ascii=False),
        "result_path": os.path.join(run_dir, "result.json"),
        "loop_readme": os.path.join(cfg.home, "devloop", "README.md"),
        "contract_path": os.path.join(cfg.home, "devloop", "prompts", "consolidator.md"),
        "peer_notes": "",
    }
    hand = _run(cfg, cur, "consolidator", agents.render("consolidator.md", values), pool.path, run_dir)
    costs.append(hand.cost)
    cur.clear()
    text = (hand.result or {}).get("text") if hand.kind == "memory" else None
    discussion = None
    if cfg.unlimited_tokens and isinstance(text, str) and text.strip():
        text, discussion, _ = discuss(cfg, pool, values, text, run_dir, costs, cur)
    ok = isinstance(text, str) and text.strip()
    problem = None
    if ok:
        try:
            memory.write_files(cfg, pool, {path: text.rstrip() + "\n"},
                               "memory: consolidate %s (%d lessons)" % (path, len(rows)))
        except osx.OSFailure as exc:
            ok, problem = False, "cannot write %s: %s" % (path, exc)

    def change(tx):
        state = dict(tx.data.memory_state or {})
        state.setdefault("last", {})[path] = osx.now()
        state.setdefault("runs", []).append({"path": path, "at": osx.now(), "ok": bool(ok),
                                             "lessons": len(rows), "cost": costs})
        del state["runs"][:-50]
        tx.write("memory/state.json", state)
        if discussion:
            tx.write_text("memory/discussions/%s-%s-%s.md" % (
                osx.now().replace(":", ""), layer, memory._safe(name)), discussion)
        if ok:
            for key in rows:
                tx.delete("memory/inbox/%s.json" % key)
    pool.update(change, "memory: consolidated %s (%d lessons)%s" % (path, len(rows), "" if ok else " - failed"))
    if not ok:
        print("memory_keeper: %s" % (problem or hand.problem() or "hand-in has no \"text\""), file=sys.stderr)
        return 1
    return 0


def discuss(cfg, pool, values, draft, run_dir, costs=None, cur=None):
    """Unlimited-token mode (D-084 Q1): a second model reviews the draft; if it asks for
    changes, the consolidator writes the file once more with those notes. The exchange is kept
    in the pool under memory/discussions/. Returns (final text, discussion text, costs); `costs` and `cur` are
    filled in place as agents finish, so an interrupted attempt keeps them."""
    costs = [] if costs is None else costs
    cur = [] if cur is None else cur
    peer_dir = os.path.join(run_dir, "peer")
    peer_values = dict(values, draft=draft, result_path=os.path.join(peer_dir, "result.json"))
    review = _run(cfg, cur, "consolidator_peer", agents.render("consolidator_peer.md", peer_values),
                  pool.path, peer_dir)
    costs.append(review.cost)
    cur.clear()
    verdict = (review.result or {}).get("verdict") if review.kind == "review" else None
    notes = (review.result or {}).get("notes") or ""
    final = draft
    if verdict == "change" and notes.strip():
        again_dir = os.path.join(run_dir, "revised")
        again = dict(values, result_path=os.path.join(again_dir, "result.json"),
                     peer_notes="## A second model reviewed your first draft\n\nYour draft:\n\n%s\n\n"
                                "Its notes:\n\n%s\n\nWrite the file again, taking the notes into account "
                                "where they are right." % (draft, notes))
        hand = _run(cfg, cur, "consolidator", agents.render("consolidator.md", again), pool.path, again_dir)
        costs.append(hand.cost)
        cur.clear()
        text = (hand.result or {}).get("text") if hand.kind == "memory" else None
        if isinstance(text, str) and text.strip():
            final = text
    record = "# Discussion: %s\n\n## Draft\n\n%s\n\n## Review (%s, %s)\n\n%s\n\n## Final\n\n%s\n" % (
        values["path"], draft, review.cost.get("model"), verdict or "no usable review", notes or "-", final)
    return final, record, costs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--layer", required=True, choices=memory.LAYERS)
    ap.add_argument("--name", required=True)
    args = ap.parse_args(argv)

    def on_signal(signum, frame):
        raise agents.Interrupted("signal %d" % signum)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        cfg = Config()
        return keep(cfg, pool_for(cfg), args.layer, args.name)
    except osx.OSFailure as exc:
        print("memory_keeper: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
