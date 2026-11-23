"""core.chain : groups form one chain (issue #6). A group starts a regression cycle only when no
earlier group still holds the chain, so a group that waited takes main once and runs its
regression once.

Pure functions of a core.pool Snapshot:

  order(snap)          ids of the groups not landed or cancelled, in group-number order
  holds(snap, gid)     gid is in the chain and is not a stuck group without a merged member
                       (a stuck group with no merged ticket has nothing to verify or land)
  waits_for(snap, gid) the nearest earlier group that holds the chain, or None
  behind(snap, gid)    when gid holds the chain, the ids after it in order(snap), else []
"""

DONE = ("landed", "cancelled")


def _gnum(gid):
    digits = "".join(c for c in str(gid) if c.isdigit())
    return (int(digits) if digits else 0, str(gid))


def order(snap):
    return sorted((gid for gid, g in snap.groups.items() if g.get("state") not in DONE), key=_gnum)


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
