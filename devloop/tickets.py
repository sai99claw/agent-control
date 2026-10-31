#!/usr/bin/env python3
"""tickets : what the main session does to the pool by hand.

    python3 devloop/tickets.py init                 # create the pool (a git repo) if missing
    python3 devloop/tickets.py list [--all]         # tickets and groups, open ones by default
    python3 devloop/tickets.py show 203
    python3 devloop/tickets.py ready 203            # after you decided on a stuck ticket: try again
    python3 devloop/tickets.py cancel 203 --why "no longer needed"
    python3 devloop/tickets.py inbox [--all]        # pages for the main session
    python3 devloop/tickets.py ack <page>|--all
    python3 devloop/tickets.py lessons --layer role --name worker --file OLD.md   # old notes -> lessons
    python3 devloop/tickets.py release g1 [--again]  # re-run a landed group's failed release

Opening tickets is ticket_creator.py; moving them is ticket_controller.py.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, memory  # noqa: E402
from core import dag as dagmod  # noqa: E402
from core import osx, release  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.pool import event  # noqa: E402


def cmd_list(pool, args):
    snap = pool.snapshot()
    for ident in dagmod.topo(snap.dag) + sorted(set(snap.tickets) - set(snap.dag)):
        t = snap.tickets.get(ident)
        if not t or (not args.all and t.get("state") in ("landed", "cancelled")):
            continue
        extra = t.get("stuck", {}).get("reason") if t.get("state") == "stuck" else (t.get("target") or "")
        print("#%-5s %-9s r%-2s %-12s %s" % (ident, t.get("state"), t.get("round", 0), extra or "",
                                             t.get("title", "")))
    for g in sorted(snap.groups.values(), key=lambda g: int(g["id"][1:])):
        if args.all or g.get("state") != "landed":
            print("%-6s %-9s %-10s fix %s  #%s" % (g["id"], g.get("state"), g.get("branch"),
                                                   g.get("fix_round", 0), " #".join(g.get("members") or [])))
    return 0


def cmd_show(pool, args):
    snap = pool.snapshot()
    t = snap.ticket(args.id)
    print(json.dumps({"ticket": t, "dag": snap.dag.get(args.id)}, indent=2, ensure_ascii=False))
    return 0


def _set(pool, ident, state, why):
    def change(tx):
        t = tx.ticket(ident)
        if t.get("state") in ("running", "landed"):
            raise osx.OSFailure("#%s is %s; leave it alone" % (ident, t.get("state")))
        t["state"] = state
        if state == "ready":
            t["round"], t["stuck"], t["run"] = 0, None, None
            if t.get("group"):
                g = tx.data.groups.get(t["group"])
                if not g or g.get("state") != "open":
                    t["group"], t["target"] = None, None
        event(t, "set %s by main" % state, why=why)
        tx.put_ticket(t)
    pool.update(change, "#%s: %s (main)" % (ident, state))
    print("#%s is %s" % (ident, state))
    return 0


def cmd_inbox(pool, args):
    snap = pool.snapshot()
    rows = [(k, v) for k, v in sorted(snap.inbox.items()) if args.all or not v.get("acked")]
    if not rows:
        print("inbox: nothing waiting for you")
    for name, row in rows:
        print("%s %-8s %-10s %s" % ("." if row.get("acked") else "*", row.get("kind"), row.get("ref"), name))
        for line in str(row.get("text") or "").splitlines()[:8]:
            print("    " + line)
    return 0


def cmd_ack(pool, args):
    def change(tx):
        n = 0
        for name, row in tx.data.inbox.items():
            if (args.all or name == args.page) and not row.get("acked"):
                row["acked"], row["acked_at"] = True, osx.now()
                tx.write("inbox/%s.json" % name, row)
                n += 1
        return n
    print("acked %d" % pool.update(change, "inbox: ack"))
    return 0


def cmd_lessons(pool, args):
    name = args.name
    if args.layer == "project":
        if name not in (None, "project"):
            raise osx.OSFailure("--layer project takes no --name other than 'project'")
        name = "project"
    elif args.layer == "role" and name not in memory.ROLES:
        raise osx.OSFailure("--name for role must be one of: %s" % ", ".join(memory.ROLES))
    elif not name:
        raise osx.OSFailure("--name is required for --layer %s" % args.layer)
    try:
        texts = memory.bullets(args.file)
    except (OSError, UnicodeDecodeError) as exc:
        raise osx.OSFailure("cannot read %s: %s" % (args.file, exc))
    if not texts:
        raise osx.OSFailure("%s has no bullet line" % args.file)
    n = pool.update(lambda tx: memory.import_lessons(tx, texts, args.layer, name, args.file),
                    "memory: %d lesson(s) imported from %s" % (len(texts), args.file))
    print("queued %d lessons for %s" % (n, memory.rel(args.layer, name)))
    return 0


def cmd_release(pool, cfg, args):
    """Re-run the release of a landed group the way the controller does at landing; once, no
    retry, no rollback. The outcome goes on the group and in a page."""
    gid = args.group
    g = pool.snapshot().groups.get(gid)
    if not g:
        raise osx.OSFailure("no group %s" % gid)
    if g.get("state") != "landed" or not g.get("landed_sha"):
        raise osx.OSFailure("group %s is %s, not landed; only a landed group is released" % (gid, g.get("state")))
    if g.get("release") == "ok" and not args.again:
        raise osx.OSFailure("group %s is already released; --again runs the release once more" % gid)
    cfg.require_release()
    sha = g["landed_sha"]
    ok, text = release.run(cfg, g, sha)

    def change(tx):
        grp = tx.data.groups[gid]
        grp["release"] = "ok" if ok else "failed"
        event(grp, "release re-run: %s" % ("ok" if ok else "failed"))
        tx.put_group(grp)
    pool.update(change, "group %s: release re-run (%s)" % (gid, "ok" if ok else "failed"))
    if ok:
        pool.report("landed", gid, "group %s released (re-run): #%s (%s)" % (
            gid, ", #".join(g.get("members") or []), sha[:12]))
        print("group %s released" % gid)
        return 0
    pool.report("decision", gid, "group %s (%s): the re-run release failed - not retried, not "
                "rolled back:\n%s" % (gid, sha[:12], text))
    print("group %s: release failed\n%s" % (gid, text), file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("id")
    p = sub.add_parser("ready")
    p.add_argument("id")
    p.add_argument("--why", default="")
    p = sub.add_parser("cancel")
    p.add_argument("id")
    p.add_argument("--why", default="")
    p = sub.add_parser("inbox")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("ack")
    p.add_argument("page", nargs="?")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("lessons")
    p.add_argument("--layer", required=True, choices=memory.LAYERS)
    p.add_argument("--name")
    p.add_argument("--file", required=True)
    p = sub.add_parser("release")
    p.add_argument("group")
    p.add_argument("--again", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        if args.cmd == "init":
            pool.init()
            models = sorted({agents.model_of(c) for r in cfg.agents for c in cfg.agent_chain(r)})
            pool.update(lambda tx: memory.seed(tx, models, cfg.home), "memory: seed")
            print("pool ready at %s" % pool.path)
            return 0
        if args.cmd == "release":
            return cmd_release(pool, cfg, args)
        return {"list": cmd_list, "show": cmd_show, "inbox": cmd_inbox, "ack": cmd_ack, "lessons": cmd_lessons,
                "ready": lambda p, a: _set(p, a.id, "ready", a.why),
                "cancel": lambda p, a: _set(p, a.id, "cancelled", a.why)}[args.cmd](pool, args)
    except osx.OSFailure as exc:
        print("tickets: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
