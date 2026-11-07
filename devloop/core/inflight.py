"""Live records: one JSON file per running opener, issuer or agent, under <worktrees>/runs/live/.

Each record carries the devloop liveness stamp of the process it describes ({pid, host, boot,
started}). The process that writes a record removes it when it ends normally; a process that was
stopped hard leaves it behind, and status() then tells a reader it ended. The pool's own stamps
(a ticket's "run", a group's "job") stay as they are: these records are only for readers.
"""

import glob
import json
import os

from . import osx

STALE_SECONDS = 86400   # an ended record older than this is deleted by the next put()


def folder(cfg):
    """Where the records live. Creates nothing."""
    return os.path.join(cfg.worktree_root, "runs", "live")


def _path(cfg, name):
    return os.path.join(folder(cfg), "%s.json" % name)


def put(cfg, name, record):
    """Write <folder>/<name>.json holding `record` (atomically), and delete other records that
    ended more than STALE_SECONDS after they started."""
    path = _path(cfg, name)
    osx.write_json(path, record)
    for other, rec in records(cfg):
        if other == name or status(rec) == "running":
            continue
        if osx.age((rec.get("stamp") or {}).get("started")) > STALE_SECONDS:
            drop(cfg, other)


def drop(cfg, name):
    """Remove a record; a missing one is fine."""
    try:
        os.unlink(_path(cfg, name))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def records(cfg):
    """[(name, record)] sorted by name, for every *.json in the folder that holds a JSON object."""
    out = []
    for path in sorted(glob.glob(os.path.join(folder(cfg), "*.json"))):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append((os.path.basename(path)[:-len(".json")], rec))
    out.sort(key=lambda nr: nr[0])
    return out


def status(record):
    """'running' while its stamp's process lives; else 'handed_in' when its result file exists;
    else 'ended'."""
    if not osx.stamp_dead(record.get("stamp")):
        return "running"
    result = record.get("result")
    if isinstance(result, str) and result and os.path.isfile(result):
        return "handed_in"
    return "ended"


def title_of(text, limit=120):
    """A short title for a request: its first '#' heading with text, else its first non-empty
    line; at most `limit` characters; None when the text has no non-empty line."""
    lines = (text or "").splitlines()
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            heading = s.lstrip("#").strip()
            if heading:
                return heading[:limit]
    for line in lines:          # a line of bare '#' characters is not text
        if line.strip().lstrip("#").strip():
            return line.strip()[:limit]
    for line in lines:
        if line.strip():
            return line.strip()[:limit]
    return None
