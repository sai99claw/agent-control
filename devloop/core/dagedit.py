"""core.dagedit : the main session changes a ready ticket's depends_on or group (D-096). The DAG is
scheduling, not ticket content, so these changes never touch a ticket's text.

    apply(tx, ident, by, why, add=M | drop=M | to=G|"new")
    after_apply(tx, a, by, why, add=B | drop=B)    D-099: group a comes after group b, or no longer

runs inside one pool write (Pool.update). Every check runs on the data `tx` read, on an in-memory
copy of the changed dag.json entry and group fields, before anything is written. Returns the
refusal as one line (nothing written), or None after writing the change and its events through
`tx`. `actl new --revise` reuses it through replace(): the opener's hand-in may set a ticket's whole
depends_on and its group, and the opener judges a mentioned dependency itself (mentions=False).
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


class Refused(Exception):
    """A change refused by its check; nothing was written. str() is the refusal line."""


class Dry:
    """A Tx on an in-memory copy of `data`: a change runs here first, so a refusal halfway writes nothing."""

    def __init__(self, data):
        self.data = Snapshot(copy.deepcopy(data.raw))

    def ticket(self, ident):
        return self.data.ticket(ident)

    def put_ticket(self, ticket):
        self.data.tickets[ticket["id"]] = ticket

    def put_group(self, group):
        self.data.groups[group["id"]] = group

    def put_dag(self, dag):
        self.data.raw["dag"] = dag


def _ticket_refusal(data, n, why):
    """The refusal for any change to #n (a blank reason, an unknown or not ready ticket), or None."""
    if not str(why or "").strip():
        return "--reason is required and must not be blank"
    t = data.tickets.get(n)
    if t is None:
        return "#%s does not exist" % n
    state = t.get("state")
    if state != "ready":
        return frozen_reason(n, state) or "#%s is %s; only a ready ticket's dependencies and group change" % (n, state)
    return None


def _refusal(data, n, why, add, drop, to, mentions=True):
    """The first refusal for the change on `data` (a Snapshot, not modified), or None."""
    bad = _ticket_refusal(data, n, why)
    if bad:
        return bad
    t = data.tickets[n]
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
            if mentions and _mentions(t, m):
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


def check(data, n, why, add=None, drop=None, to=None, mentions=True):
    """The refusal line for the change, or None when it may be written."""
    n = str(n)
    add, drop = (None if add is None else str(add)), (None if drop is None else str(drop))
    bad = _refusal(data, n, why, add, drop, to, mentions)
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


def apply(tx, n, by, why, add=None, drop=None, to=None, mentions=True):
    """Check, then write the change through `tx`. Returns the refusal line, or None when written."""
    bad = check(tx.data, n, why, add=add, drop=drop, to=to, mentions=mentions)
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


def after_check(data, a, b, why, add):
    """D-099: the refusal line for putting group a after group b (add) or dropping that edge, or None."""
    if not str(why or "").strip():
        return "--reason is required and must not be blank"
    for gid in (a, b):
        if gid not in data.groups:
            return "group %s does not exist" % gid
    if a == b:
        return "%s cannot be after itself" % a
    ga, gb = data.groups[a], data.groups[b]
    if (ga.get("job") or {}).get("kind") == "regression":
        return "group %s has a regression running: its order no longer changes" % a
    if ga.get("state") in ("verified",) + DONE:
        return "group %s is %s: its order no longer changes" % (a, ga.get("state"))
    have = chain.after_edges(data).get(a, [])
    if not add:
        return None if b in have else "%s is not set after %s" % (a, b)
    if b in have:
        return "%s is already set after %s" % (a, b)
    if gb.get("state") in DONE:
        return "group %s is %s: %s cannot wait for it" % (b, gb.get("state"), a)
    snap = Snapshot(copy.deepcopy(data.raw))
    snap.raw["dag"].setdefault("group_after", {}).setdefault(a, []).append(b)
    loop = chain.cycle(snap)
    if loop:
        return "the group order would be a cycle: " + " -> ".join(loop)
    return None


def after_apply(tx, a, by, why, add=None, drop=None):
    """Check, then write group a after group add (or drop that edge) with an event on both groups.
    Returns the refusal line, or None when written."""
    b = add if add is not None else drop
    bad = after_check(tx.data, a, b, why, add is not None)
    if bad:
        return bad
    dag = tx.data.raw["dag"]
    edges = dag.setdefault("group_after", {})
    if add is not None:
        edges.setdefault(a, []).append(b)
    else:
        edges[a] = [x for x in edges[a] if x != b]
        if not edges[a]:
            del edges[a]
    tx.put_dag(dag)
    what = "dag: %s %s %s" % (a, "after" if add is not None else "no longer after", b)
    for gid in (a, b):
        g = tx.data.groups[gid]
        event(g, what, by=by, why=why)
        tx.put_group(g)
    return None


def replace(tx, n, by, why, part):
    """Apply an opener's 'dag' part {'depends_on': [ids], 'group': gid | 'new'} (each key optional;
    depends_on replaces the whole list) to #n through `tx`, as single apply() steps without the
    'mentions #M' refusal: drops, then the move, then adds, so no step sees an edge or group the
    result lacks. Raises Refused with the first refusal line; run it on a Dry first so a refusal
    writes nothing."""
    n = str(n)
    if not isinstance(part, dict) or set(part) - {"depends_on", "group"}:
        raise Refused("'dag' must be an object with only 'depends_on' and 'group'")
    want, group = part.get("depends_on"), part.get("group")
    if want is not None and (not isinstance(want, list)
                             or not all(isinstance(d, (str, int)) and not isinstance(d, bool) for d in want)):
        raise Refused("'dag'.'depends_on' must be a list of ticket ids")
    if group is not None and not isinstance(group, str):
        raise Refused("'dag'.'group' must be an open group id or 'new'")
    bad = _ticket_refusal(tx.data, n, why)
    if bad:
        raise Refused(bad)
    steps = []
    if want is not None:
        want = list(dict.fromkeys(str(d) for d in want))
        have = dagmod.deps(tx.data.dag, n)
        steps += [{"drop": d} for d in have if d not in want]
    if group is not None and group != (tx.data.tickets.get(n) or {}).get("group"):
        steps.append({"to": group})
    if want is not None:
        steps += [{"add": d} for d in want if d not in have]
    for step in steps:
        bad = apply(tx, n, by, why, mentions=False, **step)
        if bad:
            raise Refused(bad)
