"""The DAG file: which ticket waits for which, and whether it may be verified with others.

    {"tickets": {"203": {"depends_on": ["201", "202"], "verify": "together"},
                 "204": {"depends_on": ["203"], "verify": "alone", "why_alone": "..."}}}

`verify` is "together" unless the ticket is a systemic change (D-080); a missing value means
"together".
"""

VERIFY = ("together", "alone")


def node(dag, ident):
    return dag.get(str(ident)) or {}


def deps(dag, ident):
    return [str(d) for d in node(dag, ident).get("depends_on") or []]


def verify(dag, ident):
    return node(dag, ident).get("verify") or "together"


def problems(dag, known_tickets=()):
    """Every reason this DAG cannot be used, as sentences (empty = fine)."""
    out = []
    if not isinstance(dag, dict):
        return ["dag.json \"tickets\" must be an object"]
    known = set(str(k) for k in known_tickets) | set(dag)
    for ident, row in dag.items():
        if not isinstance(row, dict):
            out.append("#%s: entry must be an object" % ident)
            continue
        dl = row.get("depends_on", [])
        if not isinstance(dl, list):
            out.append("#%s: depends_on must be a list" % ident)
            continue
        for d in dl:
            if str(d) not in known:
                out.append("#%s depends on #%s, which does not exist" % (ident, d))
            if str(d) == str(ident):
                out.append("#%s depends on itself" % ident)
        if row.get("verify", "together") not in VERIFY:
            out.append("#%s: verify must be together or alone" % ident)
        if row.get("verify") == "alone" and not row.get("why_alone"):
            out.append("#%s: verify alone needs why_alone (only for a systemic change)" % ident)
    cycle = find_cycle(dag)
    if cycle:
        out.append("cycle: " + " -> ".join("#" + c for c in cycle))
    return out


def find_cycle(dag):
    state, stack = {}, []

    def visit(n):
        state[n] = 1
        stack.append(n)
        for d in deps(dag, n):
            if d not in dag:
                continue
            if state.get(d) == 1:
                return stack[stack.index(d):] + [d]
            if state.get(d) is None:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(dag, key=_order_key):
        if state.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


def topo(dag):
    """Ticket ids, dependencies first, ties by number."""
    seen, out = set(), []

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for d in sorted(deps(dag, n), key=_order_key):
            if d in dag:
                visit(d)
        out.append(n)

    for n in sorted(dag, key=_order_key):
        visit(n)
    return out


def dependents(dag, ident):
    """Every ticket that (transitively) waits for `ident`."""
    ident, out, changed = str(ident), set(), True
    while changed:
        changed = False
        for n in dag:
            if n not in out and (ident in deps(dag, n) or out & set(deps(dag, n))):
                out.add(n)
                changed = True
    return out


def _order_key(ident):
    return (0, int(ident)) if str(ident).isdigit() else (1, str(ident))
