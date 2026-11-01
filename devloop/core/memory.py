"""Layered memory (D-013, D-053 ⑥, D-055 ①, D-088).

Layers (what belongs where is in prompts/consolidator.md, the one text of the contract):
    role     memory/role/<role>.md     principles for doing this role well, any project
    model    memory/model/<model>.md   how this model tends to go wrong, any project
    project  memory/project.md         this project's design reasons and invariants

The names above (rel()) are the keys of memory files everywhere (memory/state.json, the board,
the keeper job). The files themselves live in the folder devloop.memory names (Config.memory_dir,
default the pool's own memory/): key "memory/role/worker.md" is <memory_dir>/role/worker.md.
write_files() is the only place that writes them. Lessons (memory/inbox/), memory/state.json and
memory/discussions/ are records and always stay in the pool.

Agents never edit memory. A hand-in may carry lessons:
    "lessons": [{"layer": "role" | "model" | "project", "text": "one principle"}]
The scripts that read hand-ins put them in memory/inbox/. ticket_controller starts
memory_keeper.py for one memory file at a time once it has 5 lessons waiting, or its oldest has
waited 3 days, and that file was not consolidated in the last 24 hours. A consolidator (LLM)
rewrites the file; nobody deletes memory alone.
"""

import hashlib
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


# agent-control's role cards from before devloop, by the devloop role they now inform.
OLD_ROLE_CARDS = {"worker": "implementer", "fixer": "implementer", "creator": "opener",
                  "verifier": "verifier", "reviewer": "reviewer", "consolidator": "consolidator"}


def file_path(cfg, key):
    """Where the memory file with key `key` ("memory/role/worker.md") is on disk."""
    if not key.startswith("memory/"):
        raise ValueError("not a memory file key: %r" % (key,))
    return os.path.join(cfg.memory_dir, *key[len("memory/"):].split("/"))


def project_path(cfg):
    return file_path(cfg, rel("project", "project"))


def read(cfg, key):
    """The text of one memory file, or None when there is none."""
    try:
        with open(file_path(cfg, key), errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise osx.OSFailure(osx.explain(exc, "cannot read %s" % file_path(cfg, key)))


def files(cfg):
    """Keys of the memory files in the folder: role/*.md, model/*.md and project.md."""
    out = []
    base = cfg.memory_dir
    if os.path.isfile(os.path.join(base, "project.md")):
        out.append(rel("project", "project"))
    for layer in ("role", "model"):
        try:
            names = sorted(os.listdir(os.path.join(base, layer)))
        except (FileNotFoundError, NotADirectoryError):
            continue
        out += ["memory/%s/%s" % (layer, n) for n in names
                if n.endswith(".md") and os.path.isfile(os.path.join(base, layer, n))]
    return out


def in_pool(cfg, pool):
    """True when the memory folder is the pool's own memory/ (devloop.memory unset)."""
    return os.path.realpath(cfg.memory_dir) == os.path.realpath(os.path.join(pool.path, "memory"))


def write_files(cfg, pool, texts, message):
    """Write memory files ({key: text}). The only writer of memory files: in the pool's own
    memory/ it is one pool.update; elsewhere atomic writes under the folder. Raises OSFailure."""
    if not texts:
        return
    if in_pool(cfg, pool):
        def change(tx):
            for key, text in texts.items():
                file_path(cfg, key)   # refuse a key that is not a memory file
                tx.write_text(key, text)
        pool.update(change, message)
        return
    for key, text in texts.items():
        osx.atomic_write(file_path(cfg, key), text)


def seed(cfg, pool, models=(), home=None):
    """Create the memory files that are missing in the folder (never overwrites), each with
    only its heading.

    Older notes (agent-control's model and role cards from before devloop) are not copied in: every
    bullet goes to memory/inbox as a lesson, so the consolidator applies the layer contract to
    it like to any other lesson - nothing reaches an agent's prompt unfiltered."""
    have = set(files(cfg))
    wanted = [("role", r) for r in ROLES] + [("model", m) for m in models] + [("project", "project")]
    missing = [(layer, name) for layer, name in wanted if rel(layer, name) not in have]
    write_files(cfg, pool, {rel(layer, name): SEED[layer].format(name=name) for layer, name in missing},
                "memory: seed")
    if not home or not missing:
        return 0

    def change(tx):
        imported = 0
        for layer, name in missing:
            if layer == "model":
                imported += _import_card(tx, os.path.join(home, "memory", "model", "%s.md" % name), layer, name)
            elif layer == "role" and name in OLD_ROLE_CARDS:
                imported += _import_card(tx, os.path.join(home, "memory", "role", "%s.md" % OLD_ROLE_CARDS[name]),
                                         layer, name)
        return imported
    return pool.update(change, "memory: import old cards as lessons")


def bullets(path):
    """The lesson texts of a notes file: every line whose first non-space character is '-' or '*',
    without the marker, trimmed, empty ones skipped. Raises OSError when it cannot be read."""
    with open(path) as fh:
        lines = fh.read().splitlines()
    texts = [l.lstrip("-* ").strip() for l in lines if l.lstrip().startswith(("-", "*"))]
    return [t[:MAX_TEXT] for t in texts if t]


def queue(tx, texts, layer, name, source, prefix):
    """Put texts into memory/inbox as lessons for one memory file; keys are prefix-NN."""
    for i, text in enumerate(texts):
        tx.write("memory/inbox/%s-%02d.json" % (prefix, i), {
            "layer": layer, "name": name, "text": text, "source": source, "at": osx.now()})
    return len(texts)


def _import_card(tx, card, layer, name):
    try:
        texts = bullets(card)
    except OSError:
        return 0
    return queue(tx, texts, layer, name, "agent-control %s card before devloop" % layer,
                 "import-%s-%s" % (layer, _safe(name)))


def import_lessons(tx, texts, layer, name, path):
    """Queue a project's own old notes (texts from bullets(path)); every import gets its own keys."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    uniq = hashlib.sha1(("%s|%s" % (path, time.time_ns())).encode()).hexdigest()[:8]
    prefix = "lessons-%s-%s-%s-%s-%s" % (stamp, uniq, layer, _safe(name), _safe(os.path.basename(path)))
    return queue(tx, texts, layer, name, "lessons import from %s" % path, prefix)


def for_prompt(cfg, role, model):
    """What a starting agent reads: its role memory and its model memory (from the folder)."""
    parts = []
    for layer, name in (("role", role), ("model", model)):
        text = (read(cfg, rel(layer, name)) or "").strip()
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
