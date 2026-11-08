#!/usr/bin/env python3
"""tickets : what the main session does to the pool by hand.

    python3 devloop/tickets.py init                 # create the pool (a git repo) if missing
    python3 devloop/tickets.py list [--all]         # tickets and groups, open ones by default
    python3 devloop/tickets.py show 203
    python3 devloop/tickets.py ready 203            # after you decided on a stuck ticket: try again
    python3 devloop/tickets.py ready g7             # a stuck group: verify it again (merged tickets kept)
    python3 devloop/tickets.py cancel 203 --why "no longer needed"
    python3 devloop/tickets.py cancel g7 --why "..."   # a stuck or open group: cancel its ready and merged tickets
    python3 devloop/tickets.py rerun g7 --why "disk freed"   # environment fixed: verify a paused group again
    python3 devloop/tickets.py inbox [--all]        # pages for the main session
    python3 devloop/tickets.py wait [--timeout S] [--kind K,...]   # block until a new page arrives
    python3 devloop/tickets.py ack <page>|--all
    python3 devloop/tickets.py lessons --layer role --name worker --file OLD.md   # old notes -> lessons
    python3 devloop/tickets.py release g1 [--again]  # re-run a landed group's failed release

Opening tickets is ticket_creator.py; moving them is ticket_controller.py.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, memory  # noqa: E402
from core import dag as dagmod  # noqa: E402
from core import osx, release  # noqa: E402
from core.gitx import Git  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.pool import event, frozen_reason, shown_state  # noqa: E402


def cmd_list(pool, args):
    snap = pool.snapshot()
    for ident in dagmod.topo(snap.dag) + sorted(set(snap.tickets) - set(snap.dag)):
        t = snap.tickets.get(ident)
        if not t or (not args.all and t.get("state") in ("landed", "cancelled")):
            continue
        extra = t.get("stuck", {}).get("reason") if t.get("state") == "stuck" else (t.get("target") or "")
        print("#%-5s %-9s r%-2s %-12s %s" % (ident, shown_state(t.get("state")), t.get("round", 0), extra or "",
                                             t.get("title", "")))
    for g in sorted(snap.groups.values(), key=lambda g: int(g["id"][1:])):
        if args.all or g.get("state") not in ("landed", "cancelled"):
            print("%-6s %-9s %-10s fix %s  #%s" % (g["id"], g.get("state"), g.get("branch"),
                                                   g.get("fix_round", 0), " #".join(g.get("members") or [])))
    return 0


def cmd_show(pool, args):
    snap = pool.snapshot()
    t = snap.ticket(args.id)
    print(json.dumps({"state_text": shown_state(t.get("state")), "ticket": t, "dag": snap.dag.get(args.id)}, indent=2, ensure_ascii=False))
    return 0


def ack_in(tx, page, by=None):
    """Mark one inbox page acked inside the caller's change(tx); returns the row, or None when the
    page is missing."""
    row = tx.data.inbox.get(page) if page else None
    if row is None:
        return None
    if not row.get("acked"):
        row["acked"], row["acked_at"] = True, osx.now()
        if by:
            row["acked_by"] = by
        elif not row.get("acked_by"):
            row["acked_by"] = "answer"
        tx.write("inbox/%s.json" % page, row)
    return row


def _set(pool, ident, state, why, by=None, page=None):
    def change(tx):
        t = tx.ticket(ident)
        frozen = frozen_reason(ident, t.get("state"))
        if state == "ready" and frozen:
            raise osx.OSFailure(frozen)
        if t.get("state") in ("running", "landed"):
            raise osx.OSFailure("#%s is %s; leave it alone" % (ident, t.get("state")))
        t["state"] = state
        if state == "ready":
            t["round"], t["stuck"], t["run"] = 0, None, None
            if t.get("group"):
                g = tx.data.groups.get(t["group"])
                if not g or g.get("state") != "open":
                    t["group"], t["target"] = None, None
        event(t, "set %s by main" % state, why=why, **({"by": by} if by else {}))
        tx.put_ticket(t)
        ack_in(tx, page, by)
    pool.update(change, "#%s: %s (main)" % (ident, state))
    print("#%s is %s" % (ident, state))
    return 0


def cmd_ready_group(pool, cfg, gid, why, note=None, by=None, page=None, via=None):
    """Send a stuck group (branch kept, at least one merged member) back to the regression.
    Only the group is written; its tickets stay as they are."""
    def change(tx):
        g = tx.data.groups.get(gid)
        if not g:
            raise osx.OSFailure("no group %s" % gid)
        if g.get("state") != "stuck":
            raise osx.OSFailure("group %s is %s, not stuck; only a stuck group goes back to the regression"
                                % (gid, g.get("state")))
        if not any(tx.data.tickets.get(m, {}).get("state") == "merged" for m in g.get("members") or []):
            raise osx.OSFailure("group %s has no merged ticket; ready its tickets one by one instead" % gid)
        if not Git(cfg.root).sha("refs/heads/" + g["branch"]):
            raise osx.OSFailure("group %s: its integration branch %s no longer exists" % (gid, g["branch"]))
        g["state"], g["fix_round"], g["job"] = "pr", 0, None
        detail = {"why": why}
        if note is not None:
            entry = {"text": note, "by": by or "user", "at": osx.now()}
            if via == "web":
                entry["via"] = "web"
            g.setdefault("notes", []).append(entry)
            detail["note"] = note
        event(g, "ready again by the user", by=by or "user", **detail)
        tx.put_group(g)
        ack_in(tx, page, by)
    pool.update(change, "group %s: ready again (main)" % gid)
    print("group %s goes back to the regression (fix rounds reset to 0)" % gid)
    return 0


def cmd_cancel_group(pool, gid, why, by=None, page=None):
    """Close a stuck or open group that has no running member. Its ready and merged members become
    cancelled; other members and the integration branch are left as they are. One page lists them."""
    def change(tx):
        g = tx.data.groups.get(gid)
        if not g:
            raise osx.OSFailure("no group %s" % gid)
        if g.get("state") not in ("stuck", "open"):
            raise osx.OSFailure("group %s is %s; only a stuck or open group is cancelled" % (gid, g.get("state")))
        members = [tx.data.tickets.get(m) for m in g.get("members") or []]
        for m in members:
            if m and m.get("state") == "running":
                raise osx.OSFailure("#%s is running; leave it alone" % m["id"])
        done = []
        for t in members:
            if t and t.get("state") in ("ready", "merged"):
                t["state"] = "cancelled"
                event(t, "cancelled with group %s" % gid, by=by or "user", why=why)
                tx.put_ticket(t)
                done.append("#%s" % t["id"])
        g["state"] = "cancelled"
        event(g, "cancelled by the user", by=by or "user", why=why)
        tx.put_group(g)
        ack_in(tx, page, by)
        return done
    done = pool.update(change, "group %s: cancelled (main)" % gid)
    text = "group %s cancelled by %s (%s); cancelled tickets: %s" % (
        gid, by or "the user", why, ", ".join(done) or "none")
    pool.report("cancelled", gid, text)
    print(text)
    return 0


def cmd_rerun_group(pool, gid, why, by=None, page=None):
    """The environment is fixed: drop a group's paused mark so the controller verifies it again.
    Accepts a group in state pr or fixing without a job; one pool write (mark, event, page ack)."""
    def change(tx):
        g = tx.data.groups.get(gid)
        if not g:
            raise osx.OSFailure("no group %s" % gid)
        if g.get("state") not in ("pr", "fixing"):
            raise osx.OSFailure("group %s is %s; rerun is for a group waiting on its environment (pr or fixing)"
                                % (gid, g.get("state")))
        if g.get("job"):
            raise osx.OSFailure("group %s has a %s job running; leave it alone" % (gid, g["job"].get("kind")))
        who = by or "user"
        g.pop("paused", None)
        event(g, "environment fixed: verify again", by=who, why=why)
        tx.put_group(g)
        ack_in(tx, page, who)
    pool.update(change, "group %s: environment fixed (main)" % gid)
    print("group %s: environment fixed; the controller verifies it again (a --forever controller at its next "
          "tick, otherwise run the controller)" % gid)
    return 0


PAGE_KINDS = ("created", "landed", "decision", "env", "cancelled", "idle")


def page_lines(name, row):
    """The lines inbox prints for one page: a header and up to 8 text lines."""
    out = ["%s %-8s %-10s %s" % ("." if row.get("acked") else "*", row.get("kind"), row.get("ref"), name)]
    for line in str(row.get("text") or "").splitlines()[:8]:
        out.append("    " + line)
    return out


def cmd_inbox(pool, args):
    snap = pool.snapshot()
    rows = [(k, v) for k, v in sorted(snap.inbox.items()) if args.all or not v.get("acked")]
    if not rows:
        print("inbox: nothing waiting for you")
    for name, row in rows:
        print("\n".join(page_lines(name, row)))
    return 0


def cmd_wait(pool, args, poll):
    kinds = None
    if args.kind is not None:
        kinds = {k.strip() for k in args.kind.split(",") if k.strip()}
        if not kinds:
            raise osx.OSFailure("--kind lists no page kind")
        bad = sorted(kinds - set(PAGE_KINDS))
        if bad:
            raise osx.OSFailure("unknown page kind %s; kinds are %s" % (", ".join(bad), ", ".join(PAGE_KINDS)))
    if args.timeout is not None and not args.timeout > 0:
        raise osx.OSFailure("--timeout must be greater than 0")
    seen = set(pool.snapshot().inbox)
    end = None if args.timeout is None else time.monotonic() + args.timeout
    while True:
        left = None if end is None else end - time.monotonic()
        if left is not None and left <= 0:
            print("wait: no new page after %gs" % args.timeout)
            return 1
        time.sleep(poll if left is None else max(0.0, min(poll, left)))
        inbox = pool.snapshot().inbox
        rows = [(k, v) for k, v in sorted(inbox.items())
                if k not in seen and not v.get("acked") and (kinds is None or v.get("kind") in kinds)]
        if rows:
            for name, row in rows:
                print("\n".join(page_lines(name, row)))
            return 0


def cmd_ack(pool, args):
    by = getattr(args, "by", None)

    def change(tx):
        n = 0
        for name, row in list(tx.data.inbox.items()):
            if (args.all or name == args.page) and not row.get("acked"):
                if by:
                    row["acked_by"] = by
                row["acked"], row["acked_at"] = True, osx.now()
                tx.write("inbox/%s.json" % name, row)
                n += 1
                ref = str(row.get("ref") or "")
                if by and not args.all:
                    subject = tx.data.tickets.get(ref) or tx.data.groups.get(ref)
                    if subject is not None:
                        event(subject, "page acknowledged", page=name, by=by)
                        (tx.put_ticket if ref in tx.data.tickets else tx.put_group)(subject)
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
    rel_log = release.log_path(cfg, gid)

    def change(tx):
        grp = tx.data.groups[gid]
        grp["release"] = "ok" if ok else "failed"
        grp["release_log"] = rel_log
        event(grp, "release re-run: %s" % ("ok" if ok else "failed"))
        tx.put_group(grp)
    pool.update(change, "group %s: release re-run (%s)" % (gid, "ok" if ok else "failed"))
    if ok:
        pool.report("landed", gid, "group %s released (re-run): #%s (%s); release log: %s" % (
            gid, ", #".join(g.get("members") or []), sha[:12], rel_log))
        print("group %s released" % gid)
        return 0
    pool.report("decision", gid, "group %s (%s): the re-run release failed - not retried, not "
                "rolled back (release log: %s):\n%s" % (gid, sha[:12], rel_log, text))
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
    p.add_argument("--note", default=None)
    p.add_argument("--by", default=None)
    p.add_argument("--page", default=None)
    p.add_argument("--via", choices=("main", "web"), default="main")
    p = sub.add_parser("cancel")
    p.add_argument("id")
    p.add_argument("--why", default="")
    p.add_argument("--by", default=None)
    p.add_argument("--page", default=None)
    p = sub.add_parser("rerun")
    p.add_argument("id")
    p.add_argument("--why", default="")
    p.add_argument("--by", default=None)
    p.add_argument("--page", default=None)
    p = sub.add_parser("inbox")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--kind", default=None)
    p = sub.add_parser("ack")
    p.add_argument("page", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--by", default=None)
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
            memory.seed(cfg, pool, models)
            print("pool ready at %s" % pool.path)
            return 0
        if args.cmd == "ready" and args.note is not None and not re.fullmatch(r"g\d+", args.id):
            print("tickets: --note is for a group (ready g<N> --note ...), not for ticket %s" % args.id,
                  file=sys.stderr)
            return 2
        if args.cmd == "ready" and re.fullmatch(r"g\d+", args.id):
            return cmd_ready_group(pool, cfg, args.id, args.why, args.note, args.by, args.page, args.via)
        if args.cmd == "cancel" and re.fullmatch(r"g\d+", args.id):
            return cmd_cancel_group(pool, args.id, args.why, args.by, args.page)
        if args.cmd == "rerun":
            if not re.fullmatch(r"g\d+", args.id):
                print("tickets: rerun is for a group g<N>, not for ticket %s" % args.id, file=sys.stderr)
                return 2
            return cmd_rerun_group(pool, args.id, args.why, args.by, args.page)
        if args.cmd == "release":
            return cmd_release(pool, cfg, args)
        if args.cmd == "wait":
            return cmd_wait(pool, args, cfg.poll_seconds)
        return {"list": cmd_list, "show": cmd_show, "inbox": cmd_inbox, "ack": cmd_ack, "lessons": cmd_lessons,
                "ready": lambda p, a: _set(p, a.id, "ready", a.why, a.by, a.page),
                "cancel": lambda p, a: _set(p, a.id, "cancelled", a.why, a.by, a.page)}[args.cmd](pool, args)
    except osx.OSFailure as exc:
        print("tickets: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
