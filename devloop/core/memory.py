"""Layered memory, kept in the pool next to the tickets (D-013, D-053 ⑥, D-055 ①).

Layers (what belongs where is in prompts/consolidator.md, the one text of the contract):
    role     memory/role/<role>.md     principles for doing this role well, any project
    model    memory/model/<model>.md   how this model tends to go wrong, any project
    project  memory/project.md         this project's design reasons and invariants

Agents never edit memory. A hand-in may carry lessons:
    "lessons": [{"layer": "role" | "model" | "project", "text": "one principle"}]
The scripts that read hand-ins put them in memory/inbox/. ticket_controller starts
memory_keeper.py for one memory file at a time once it has 5 lessons waiting, or its oldest has
waited 3 days, and that file was not consolidated in the last 24 hours. A consolidator (LLM)
rewrites the file; nobody deletes memory alone.
"""

import os
import time

from . import osx

LAYERS = ("role", "model", "project")
DUE_COUNT = 5
DUE_AGE = 3 * 24 * 3600
MIN_GAP = 24 * 3600
MAX_TEXT = 1200
ROLES = ("creator", "worker", "verifier", "reviewer", "fixer", "consolidator")

SEED = {
    "role": "# Role memory: {name}\n\nPrinciples learned while doing this role. Rules for the job are "
            "in the prompt; this holds what experience added.\n",
    "model": "# Model memory: {name}\n\nHow this model tends to go wrong, and what helps.\n",
    "project": "# Project memory\n\nThis project's design reasons and invariants: why things are the "
               "way they are, and what must stay true.\n",
}


def rel(layer, name):
    if layer == "project":
        return "memory/project.md"
    return "memory/%s/%s.md" % (layer, _safe(name))


def target_of(layer, role, model):
    return {"role": role, "model": model, "project": "project"}[layer]


def _safe(name):
    return "".join(c if c.isalnum() or c in "-._" else "-" for c in str(name)) or "unknown"


def seed(tx, models=(), home=None):
    """Create the memory files that are missing (never overwrites). A model's memory starts from
    agent-control's own model card when there is one (how a model goes wrong holds anywhere)."""
    have = tx.data.memory
    wanted = [("role", r) for r in ROLES] + [("model", m) for m in models] + [("project", "project")]
    for layer, name in wanted:
        path = rel(layer, name)
        if path in have:
            continue
        text = SEED[layer].format(name=name)
        card = os.path.join(home or "", "memory", "model", "%s.md" % name)
        if layer == "model" and home and os.path.isfile(card):
            try:
                with open(card) as fh:
                    text = fh.read()
            except OSError:
                pass
        tx.write_text(path, text)


def for_prompt(snap, role, model):
    """What a starting agent reads: its role memory and its model memory."""
    parts = []
    for layer, name in (("role", role), ("model", model)):
        text = (snap.memory.get(rel(layer, name)) or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts) or "(nothing recorded yet)"


def collect(pool, hand, source):
    """Put the lessons of one hand-in into memory/inbox. Bad rows are dropped, not fatal."""
    rows = (hand.result or {}).get("lessons") if hand and hand.result else None
    if not isinstance(rows, list) or not rows:
        return 0
    model = (hand.cost or {}).get("model") or "unknown"
    keep = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        layer, text = row.get("layer"), row.get("text")
        if layer not in LAYERS or not isinstance(text, str) or not text.strip():
            continue
        keep.append({"layer": layer, "name": target_of(layer, hand.role, model),
                     "text": text.strip()[:MAX_TEXT], "source": source, "at": osx.now()})
    if not keep:
        return 0

    def change(tx):
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        for i, row in enumerate(keep):
            tx.write("memory/inbox/%s-%d-%s.json" % (stamp, os.getpid(), i), row)
    try:
        pool.update(change, "memory: %d lesson(s) from %s" % (len(keep), source))
    except osx.OSFailure:
        return 0
    return len(keep)


def pending(snap, layer, name):
    return {k: v for k, v in snap.lessons.items()
            if v.get("layer") == layer and v.get("name") == name}


def due(snap, now=None):
    """Memory files whose lessons should be consolidated now, oldest wait first."""
    now = now or time.time()
    groups = {}
    for key, row in snap.lessons.items():
        groups.setdefault((row.get("layer"), row.get("name")), []).append(row)
    out = []
    last = (snap.memory_state or {}).get("last") or {}
    for (layer, name), rows in groups.items():
        if layer not in LAYERS:
            continue
        oldest = max(osx.age(r.get("at")) for r in rows)
        if len(rows) < DUE_COUNT and oldest < DUE_AGE:
            continue
        if osx.age(last.get(rel(layer, name))) < MIN_GAP:
            continue
        out.append((oldest, layer, name))
    return [(layer, name) for _, layer, name in sorted(out, reverse=True)]
