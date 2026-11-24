"""core.chain : groups form one chain (issue #6). A group starts a regression cycle only when no
earlier group still holds the chain, so a group that waited takes main once and runs its
regression once.

The chain is ordered by the DAG (issue #13), so a group id is only a name. Group B waits for
group A when a ticket of B depends directly on a ticket of A (A != B), neither ticket landed or
cancelled, neither group landed or cancelled. Taking the open groups by creation key (the
group's 'created', missing = '', then its id in natural order: g2 < g10, g62 < g62a < g63), each
group is placed after every group it waits for that is not placed yet (pulled ahead
recursively, by the same key). A cycle (only hand edits make one) never raises: a group already
being placed is skipped.

Pure functions of a core.pool Snapshot:

  order(snap)          ids of the groups not landed or cancelled, in that order
  sequence(snap)       the landed and cancelled groups by creation key, then order(snap)
  cycle(snap)          None, or one cycle of the waits-for relation as group ids, each waiting
                       for the next, last == first, starting at the smallest creation key on one
  holds(snap, gid)     gid is in the chain and is not a stuck group without a merged member
                       (a stuck group with no merged ticket has nothing to verify or land)
  waits_for(snap, gid) the nearest earlier group that holds the chain, or None
  behind(snap, gid)    when gid holds the chain, the ids after it in order(snap), else []
  behind_line(snap, gid)  the stuck-group page line naming behind(snap, gid), or "" when empty
  with_behind(text, snap, gid)  text with behind_line appended as its last line when not empty
  stuck_holds(snap, ident, gid)  D-097: stuck ticket `ident` holds its open group gid (the group
                       waits for the decision on it) while a ticket not cancelled depends on it
                       directly, or another member of gid is neither merged nor cancelled, or no
                       other member is merged
"""

import re

from . import dag as dagmod

DONE = ("landed", "cancelled")


def _natural(gid):
    return tuple((0, int(run), "") if run.isdigit() else (1, 0, run)
                 for run in re.findall(r"\d+|\D+", str(gid)))


def created_key(snap, gid):
    """The creation key: the group's 'created' (missing = ''), then its id in natural order."""
    return (str((snap.groups.get(gid) or {}).get("created") or ""), _natural(gid))


def waits(snap):
    """{gid: set of group ids it waits for}, over the groups not landed or cancelled."""
    live = {gid for gid, g in snap.groups.items() if (g or {}).get("state") not in DONE}
    out = {gid: set() for gid in live}
    for ident, t in snap.tickets.items():
        b = (t or {}).get("group")
        if b not in live or t.get("state") in DONE:
            continue
        for d in (snap.dag.get(str(ident)) or {}).get("depends_on") or []:
            dt = snap.tickets.get(str(d)) or {}
            a = dt.get("group")
            if a in live and a != b and dt.get("state") not in DONE:
                out[b].add(a)
    return out


def order(snap):
    rel = waits(snap)
    placed, busy, out = set(), set(), []

    def place(gid):
        if gid in placed or gid in busy:
            return
        busy.add(gid)
        for a in sorted(rel[gid], key=lambda x: created_key(snap, x)):
            place(a)
        busy.discard(gid)
        placed.add(gid)
        out.append(gid)

    for gid in sorted(rel, key=lambda x: created_key(snap, x)):
        place(gid)
    return out


def sequence(snap):
    done = sorted((gid for gid, g in snap.groups.items() if (g or {}).get("state") in DONE),
                  key=lambda x: created_key(snap, x))
    return done + order(snap)


def cycle(snap):
    rel = waits(snap)
    key = lambda x: created_key(snap, x)

    def path_back(start):
        seen, stack = set(), [(start, [start])]
        while stack:
            gid, path = stack.pop()
            for a in sorted(rel[gid], key=key, reverse=True):
                if a == start:
                    return path + [a]
                if a not in seen:
                    seen.add(a)
                    stack.append((a, path + [a]))
        return None

    for gid in sorted(rel, key=key):
        found = path_back(gid)
        if found:
            return found
    return None


def _has_merged_member(snap, gid):
    for ident in snap.groups[gid].get("members") or []:
        t = snap.tickets.get(ident) or {}
        if t.get("state") == "merged" and t.get("group") == gid:
            return True
    return False


def holds(snap, gid):
    if gid not in order(snap):
        return False
    return snap.groups[gid].get("state") != "stuck" or _has_merged_member(snap, gid)


def waits_for(snap, gid):
    chain = order(snap)
    if gid not in chain:
        return None
    for earlier in reversed(chain[:chain.index(gid)]):
        if holds(snap, earlier):
            return earlier
    return None


def behind(snap, gid):
    if not holds(snap, gid):
        return []
    chain = order(snap)
    return chain[chain.index(gid) + 1:]


def behind_line(snap, gid):
    later = behind(snap, gid)
    if not later:
        return ""
    return "later groups waiting for %s to land: %d (%s)" % (gid, len(later), ", ".join(later))


def with_behind(text, snap, gid):
    line = behind_line(snap, gid)
    return text + "\n" + line if line else text


def stuck_holds(snap, ident, gid):
    for n, t in snap.tickets.items():
        if t.get("state") != "cancelled" and str(ident) in dagmod.deps(snap.dag, n):
            return True
    others = [(snap.tickets.get(m) or {}).get("state")
              for m in snap.groups[gid].get("members") or [] if m != str(ident)]
    if any(s not in ("merged", "cancelled") for s in others):
        return True
    return "merged" not in others
