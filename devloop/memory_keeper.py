#!/usr/bin/env python3
"""memory_keeper --layer role|model|project --name N : consolidate one memory file.

Started only by ticket_controller, one at a time. A consolidator agent reads the file and the
lessons waiting for it and writes the whole file again (D-013: not one session deleting alone -
the consolidator is the configured higher model). The lessons it was shown are removed; ones
that arrived meanwhile stay for next time. The attempt is recorded either way, so a failing
consolidation is retried after 24 hours, not in a loop.

Exit: 0 consolidated · 1 the consolidator's hand-in was unusable (nothing changed) · 2 refused.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, osx  # noqa: E402
from core import memory  # noqa: E402
from core.config import Config, pool_for  # noqa: E402


def keep(cfg, pool, layer, name):
    snap = pool.snapshot()
    path = memory.rel(layer, name)
    rows = memory.pending(snap, layer, name)
    if not rows:
        return 0
    run_dir = cfg.runs_dir("memory", "%s-%s-%s" % (layer, memory._safe(name), osx.now().replace(":", "")))
    prompt = agents.render("consolidator.md", {
        "layer": layer, "name": name, "path": path,
        "current": snap.memory.get(path) or memory.SEED[layer].format(name=name),
        "lessons": json.dumps([{"text": r.get("text"), "source": r.get("source"), "at": r.get("at")}
                               for r in rows.values()], indent=2, ensure_ascii=False),
        "result_path": os.path.join(run_dir, "result.json"),
    })
    hand = agents.run(cfg, "consolidator", prompt, pool.path, run_dir, cfg.agent_timeout_seconds)
    text = (hand.result or {}).get("text") if hand.kind == "memory" else None
    ok = isinstance(text, str) and text.strip()

    def change(tx):
        state = dict(tx.data.memory_state or {})
        state.setdefault("last", {})[path] = osx.now()
        state.setdefault("runs", []).append({"path": path, "at": osx.now(), "ok": bool(ok),
                                             "lessons": len(rows), "cost": hand.cost})
        del state["runs"][:-50]
        tx.write("memory/state.json", state)
        if ok:
            tx.write_text(path, text.rstrip() + "\n")
            for key in rows:
                tx.delete("memory/inbox/%s.json" % key)
    pool.update(change, "memory: consolidate %s (%d lessons)%s" % (path, len(rows), "" if ok else " - failed"))
    if not ok:
        print("memory_keeper: %s" % (hand.problem() or "hand-in has no \"text\""), file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--layer", required=True, choices=memory.LAYERS)
    ap.add_argument("--name", required=True)
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        return keep(cfg, pool_for(cfg), args.layer, args.name)
    except osx.OSFailure as exc:
        print("memory_keeper: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
