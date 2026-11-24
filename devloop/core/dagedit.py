"""core.dagedit : the main session changes a ready ticket's depends_on or group (D-096). The DAG is
scheduling, not ticket content, so these changes never touch a ticket's text.

    apply(tx, ident, by, why, add=M | drop=M | to=G|"new")

runs inside one pool write (Pool.update). Every check runs on the data `tx` read, on an in-memory
copy of the changed dag.json entry and group fields, before anything is written. Returns the
refusal as one line (nothing written), or None after writing the change and its events through
`tx`. A later ticket reuses this for `actl new --revise`.
"""

import copy
import re

from . import chain
from . import dag as dagmod
from . import osx
from .pool import Snapshot, event, frozen_reason, new_group

DONE = ("landed", "cancelled")
TEXT_FIELDS = ("objective", "acceptance", "test_plan", "outline", "notes")


def _mentions(t, ident):
    pat = re.compile(r"#%s(?!\d)" % re.escape(str(ident)))
    for field in TEXT_FIELDS:
        value = t.get(field)
        for text in value if isinstance(value, list) else [value]:
            if isinstance(text, str) and pat.search(text):
                return True
    return False


def _refusal(data, n, why, add, drop, to):
    """The first refusal for the change on `data` (a Snapshot, not modified), or None."""
    if not str(why or "").strip():
        return "--reason is required and must not be blank"
    t = data.tickets.get(n)
    if t is None:
        return "#%s does not exist" % n
    state = t.get("state")
    if state != "ready":
        return frozen_reason(n, state) or "#%s is %s; only a ready ticket's dependencies and group change" % (n, state)
    if to is None:
        m = add if add is not None else drop
        if m not in data.tickets:
            return "#%s does not exist" % m
        if m == n:
            return "#%s cannot depend on itself" % n
        have = dagmod.deps(data.dag, n)
        if add is not None:
            if m in have:
                return "#%s already depends on #%s" % (n, m)
            if data.tickets[m].get("state") == "cancelled":
                return "#%s is cancelled: #%s would wait for it forever" % (m, n)
        else:
            if m not in have:
                return "#%s does not depend on #%s" % (n, m)
            if _mentions(t, m):
                return ("#%s's ticket text mentions #%s: drop the dependency with `actl new --revise %s`"
                        % (n, m, n))
    elif to != "new":
        g = data.groups.get(to)
        if g is None:
            return "group %s does not exist" % to
        if g.get("state") != "open":
            return "group %s is %s" % (to, g.get("state"))
        if t.get("group") == to:
            return "#%s is already in %s" % (n, to)
    return None


def _changed(data, n, add, drop, to):
    """A Snapshot of `data` with the change made in memory (dag, the ticket's group, members)."""
    raw = {"dag": copy.deepcopy(data.raw["dag"]), "tickets": copy.deepcopy(data.tickets),
           "groups": copy.deepcopy(data.groups)}
    snap = Snapshot(raw)
    if to is None:
        row = snap.dag.setdefault(n, {})
        have = dagmod.deps(snap.dag, n)
        row["depends_on"] = have + [add] if add is not None else [d for d in have if d != drop]
        return snap
    old = snap.tickets[n].get("group")
    gid = data.next_group_id() if to == "new" else to
    if to == "new":
        snap.groups[gid] = {"id": gid, "state": "open", "members": [], "created": osx.now()}
    if old in snap.groups:
        g = snap.groups[old]
        g["members"] = [x for x in g.get("members") or [] if x != n]
        if not g["members"]:
            g["state"] = "cancelled"
    snap.groups[gid]["members"] = list(snap.groups[gid].get("members") or []) + [n]
    snap.tickets[n]["group"] = gid
    snap.tickets[n]["target"] = None
    return snap


def check(data, n, why, add=None, drop=None, to=None):
    """The refusal line for the change, or None when it may be written."""
    n = str(n)
    add, drop = (None if add is None else str(add)), (None if drop is None else str(drop))
    bad = _refusal(data, n, why, add, drop, to)
    if bad:
        return bad
    after = _changed(data, n, add, drop, to)
    for p in dagmod.problems(after.dag, after.tickets):
        if p.startswith("cycle: "):
            return p
    loop = chain.cycle(after)
    if loop:
        return "the group order would be a cycle: " + " -> ".join(loop)
    return None


def apply(tx, n, by, why, add=None, drop=None, to=None):
    """Check, then write the change through `tx`. Returns the refusal line, or None when written."""
    bad = check(tx.data, n, why, add=add, drop=drop, to=to)
    if bad:
        return bad
    n = str(n)
    t = tx.ticket(n)
    if to is None:
        m = str(add if add is not None else drop)
        dag = tx.data.dag
        row = dag.setdefault(n, {})
        have = dagmod.deps(dag, n)
        row["depends_on"] = have + [m] if add is not None else [d for d in have if d != m]
        tx.put_dag(tx.data.raw["dag"])
        word = "depends on" if add is not None else "no longer depends on"
        event(t, "dag: %s #%s" % (word, m), by=by, why=why)
        tx.put_ticket(t)
        for gid in dict.fromkeys([t.get("group"), (tx.data.tickets.get(m) or {}).get("group")]):
            g = tx.data.groups.get(gid) if gid else None
            if g and g.get("state") not in DONE:
                event(g, "dag: #%s %s #%s" % (n, word, m), by=by, why=why)
                tx.put_group(g)
        return None
    old = t.get("group")
    old_name = old or "no group"
    if to == "new":
        gid = new_group(tx, [n], "dag: member #%s moved in from %s" % (n, old_name))
        g = tx.data.groups[gid]
        g["history"][-1].update(by=by, why=why)
        tx.put_group(g)
    else:
        gid = to
        g = tx.data.groups[gid]
        g["members"] = list(g.get("members") or []) + [n]
        event(g, "dag: member #%s moved in from %s" % (n, old_name), by=by, why=why)
        tx.put_group(g)
    og = tx.data.groups.get(old) if old else None
    if og:
        og["members"] = [x for x in og.get("members") or [] if x != n]
        event(og, "dag: member #%s moved to %s" % (n, gid), by=by, why=why)
        if not og["members"]:
            og["state"] = "cancelled"
            event(og, "cancelled: its last member #%s moved to %s" % (n, gid), by=by, why=why)
        tx.put_group(og)
    t["group"], t["target"] = gid, None
    event(t, "dag: moved from %s to %s" % (old_name, gid), by=by, why=why)
    tx.put_ticket(t)
    return None
