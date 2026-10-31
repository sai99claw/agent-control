#!/usr/bin/env python3
"""ticket_controller : the loop's only driver. No LLM; it never decides anything a script can't.

Each tick, in this order:

  1. finish jobs  - an issuer, regression or fixer that ended; read what it left in the pool
  2. groups       - an open group whose tickets are all merged or stuck becomes a PR; a PR gets
                    a regression; red -> auto_fixer (at most loop.max_rounds); green -> main
                    fast-forwards to the integration branch and the project's release runs
  3. dispatch     - ready tickets whose dependencies are in their starting point (landed, or
                    merged into the same integration branch) get a ticket_issuer, up to
                    loop.max_parallel at a time; "together" tickets share one open group,
                    an "alone" ticket gets its own

Everything it knows is in the pool and in git, so a restart picks up where it was; issuers,
regressions and fixers run detached and are adopted by the next controller.

    python3 loop/ticket_controller.py            # run until nothing is left to do
    python3 loop/ticket_controller.py --forever  # keep watching the pool for new tickets

Exit: 0 idle · 3 stopped for the user (environment / could not measure; a page was written) ·
2 could not start (another controller, config, pool).
"""

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dag as dagmod  # noqa: E402
from core import osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402
from core.pool import event  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))


class Stop(Exception):
    def __init__(self, code, why):
        super().__init__(why)
        self.code = code


class Controller:
    def __init__(self, cfg, pool):
        self.cfg, self.pool = cfg, pool
        self.repo = Git(cfg.root)
        self.children = {}          # key -> Popen, for jobs this process started
        self.release_cmd = cfg.require_release()
        self.stopping = False

    # ------------------------------------------------------------ loop

    def run(self, forever=False):
        while not self.stopping:
            busy = self.tick()
            if not busy and not forever:
                return 0
            time.sleep(self.cfg.poll_seconds)
        return 0

    def tick(self):
        snap = self.pool.snapshot()
        self.finish_jobs(snap)
        snap = self.pool.snapshot()
        self.advance_groups(snap)
        snap = self.pool.snapshot()
        started = self.dispatch(snap)
        snap = self.pool.snapshot()
        running = [t for t in snap.tickets.values() if t.get("state") == "running"]
        active = [g for g in snap.groups.values() if g.get("state") in ("open", "pr", "fixing", "verified")]
        return bool(started or running or active or self.children)

    # ------------------------------------------------------------ 1. jobs

    def finish_jobs(self, snap):
        for t in snap.tickets.values():
            if t.get("state") != "running":
                continue
            key = "issuer:" + t["id"]
            proc = self.children.get(key)
            if proc is not None and proc.poll() is None:
                continue
            if proc is None and t.get("run") and not osx.stamp_dead(t["run"], grace_seconds=4 * 3600):
                continue                       # an adopted issuer, still alive
            if proc is None and not t.get("run") and osx.age(t.get("claimed_at")) < 120:
                continue                       # just claimed; the issuer has not stamped yet
            self.children.pop(key, None)
            self._release_orphan(t["id"])
        for key, proc in list(self.children.items()):
            if key.startswith("issuer:") and proc.poll() is not None:
                self.children.pop(key)          # it recorded its outcome in the pool itself
        for g in snap.groups.values():
            job = g.get("job")
            if not job:
                continue
            key = "%s:%s" % (job["kind"], g["id"])
            proc = self.children.get(key)
            if proc is not None:
                if proc.poll() is None:
                    continue
                rc = proc.returncode
            elif not osx.stamp_dead(job.get("stamp"), grace_seconds=6 * 3600):
                continue
            else:
                rc = None                      # adopted job that ended; read what it left
            self.children.pop(key, None)
            if job["kind"] == "regression":
                self.after_regression(g["id"], job, rc)
            else:
                self.after_fixer(g["id"], rc)

    def _release_orphan(self, ident):
        def change(tx):
            t = tx.ticket(ident)
            if t.get("state") == "running":
                t["state"], t["run"] = "ready", None
                event(t, "issuer ended without an outcome; dispatch again")
                tx.put_ticket(t)
        self.pool.update(change, "#%s: issuer gone, back to ready" % ident)

    # ------------------------------------------------------------ 2. groups

    def advance_groups(self, snap):
        for g in sorted(snap.groups.values(), key=lambda x: int(x["id"][1:])):
            state = g.get("state")
            if state == "open":
                members = [snap.tickets.get(i) or {} for i in g.get("members") or []]
                states = [m.get("state") for m in members]
                if any(s in ("ready", "running") for s in states):
                    continue
                if any(self.joinable(snap, i, g) for i, t in snap.tickets.items()
                       if t.get("state") == "ready" and i not in (g.get("members") or [])):
                    continue                   # a waiting ticket can still join this branch
                if "merged" in states:
                    self.set_group(g["id"], "pr", "all tickets merged or stuck: PR ready")
                elif members:
                    self.set_group(g["id"], "stuck", "no ticket of the group got merged")
            elif state in ("pr", "fixing") and not g.get("job"):
                self.start_regression(g)
            elif state == "verified":
                self.land(g)

    def set_group(self, gid, state, why, **fields):
        def change(tx):
            g = tx.data.groups[gid]
            if state is not None:
                g["state"] = state
            g.update(fields)
            event(g, why)
            tx.put_group(g)
            return g
        return self.pool.update(change, "group %s: %s" % (gid, state or why))

    def start_regression(self, g):
        out = os.path.join(self.cfg.runs_dir("regression", g["id"]), "result-%d.json" % int(time.time()))
        argv = ["python3", os.path.join(LOOP, "regression.py"), "--branch", g["branch"], "--out", out]
        self._start_job(g["id"], "regression", argv, out)

    def start_fixer(self, gid, report_path, rnd):
        argv = ["python3", os.path.join(LOOP, "auto_fixer.py"), "--group", gid, "--report", report_path]
        self.set_group(gid, "fixing", "fix round %d" % rnd, fix_round=rnd)
        self._start_job(gid, "fixer", argv, report_path)

    def _start_job(self, gid, kind, argv, out):
        log = os.path.join(self.cfg.runs_dir(kind, gid), "%s.log" % kind)
        proc = osx.spawn(argv, cwd=self.cfg.root, env=self.cfg.env(), log_path=log)
        self.children["%s:%s" % (kind, gid)] = proc
        stamp = {"pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(), "started": osx.now()}
        self.set_group(gid, None, "%s started" % kind, job={"kind": kind, "stamp": stamp, "out": out})

    def after_regression(self, gid, job, rc):
        report = {}
        try:
            report = osx.read_json(job.get("out"), {}) or {}
        except osx.OSFailure:
            report = {}
        status = report.get("status") or "unmeasured"
        g = self.set_group(gid, None, "regression %s" % status, job=None,
                           last_regression=report.get("summary"))
        if status == "green":
            if self.repo.sha(g["branch"]) != report.get("sha"):
                return                          # branch moved while testing: test again
            self.set_group(gid, "verified", "regression green", verified_sha=report["sha"])
            self.land(self.pool.snapshot().groups[gid])
            return
        if status in ("env", "unmeasured"):
            kind = "env" if status == "env" else "decision"
            self.pool.report(kind, gid, "group %s: regression %s - %s. Fix it, then run the "
                             "controller again." % (gid, status, report.get("summary")))
            raise Stop(3, "regression %s" % status)
        rnd = int(g.get("fix_round") or 0)
        if rnd >= self.cfg.max_rounds:
            self.set_group(gid, "stuck", "red after %d fix rounds" % rnd)
            self.pool.report("decision", gid, "group %s is still red after %d fix rounds:\n%s" % (
                gid, rnd, report.get("summary")))
            return
        self.start_fixer(gid, job["out"], rnd + 1)

    def after_fixer(self, gid, rc):
        if rc == 3:
            self.set_group(gid, "stuck", "fixer needs a decision", job=None)
            return
        self.set_group(gid, "pr", "fixer done (rc %s): verify again" % rc, job=None)

    # ------------------------------------------------------------ landing

    def land(self, g):
        gid, sha, main = g["id"], g.get("verified_sha"), self.cfg.main_branch
        old = self.repo.sha(main)
        if self.repo.sha(g["branch"]) != sha:
            self.set_group(gid, "pr", "branch moved after verification: verify again")
            return
        if not self.repo.is_ancestor(old, sha):
            report = os.path.join(self.cfg.runs_dir("regression", gid), "main-moved.json")
            osx.write_json(report, {"status": "main_moved", "summary": "%s moved; merge it in" % main})
            rnd = int(g.get("fix_round") or 0)
            if rnd >= self.cfg.max_rounds:
                self.set_group(gid, "stuck", "main kept moving")
                self.pool.report("decision", gid, "group %s cannot catch up with %s" % (gid, main))
                return
            self.start_fixer(gid, report, rnd + 1)
            return
        where = self.repo.checked_out_at(main)
        if where:
            res = Git(where).run("merge", "--ff-only", sha, check=False)
            if not res.ok:
                self.pool.report("decision", gid, "group %s is verified but %s at %s will not "
                                 "fast-forward (local changes?): %s" % (gid, main, where, res.err.strip()))
                raise Stop(3, "main checkout blocks fast-forward")
        elif not self.repo.move_ref(main, sha, old):
            return                              # main moved under us; next tick handles it
        if self.cfg.remote and self.repo.has_remote(self.cfg.remote):
            res = self.repo.run("push", "-q", self.cfg.remote, "%s:refs/heads/%s" % (main, main), check=False)
            if not res.ok:
                self.pool.report("decision", gid, "group %s merged into local %s but the push "
                                 "to %s failed: %s" % (gid, main, self.cfg.remote, res.err.strip()))
        released, rel_text = self.release(g, sha)
        snap = self.pool.snapshot()
        ids = [i for i in g.get("members") or [] if (snap.tickets.get(i) or {}).get("state") == "merged"]

        def change(tx):
            for i in ids:
                t = tx.ticket(i)
                t["state"], t["landed_sha"] = "landed", sha
                event(t, "landed", group=gid)
                tx.put_ticket(t)
            grp = tx.data.groups[gid]
            grp["state"], grp["landed_sha"], grp["release"] = "landed", sha, "ok" if released else "failed"
            event(grp, "landed" + ("" if released else " (release failed)"))
            tx.put_group(grp)
        self.pool.update(change, "group %s landed" % gid)
        self.repo.remove_worktree(os.path.join(self.cfg.worktree_root, "fix-" + gid))
        if released:
            self.pool.report("landed", gid, "group %s landed and released: #%s (%s)" % (
                gid, ", #".join(ids), sha[:12]))
        else:
            self.pool.report("decision", gid, "group %s is in %s (%s) but the release failed - "
                             "not retried, not rolled back:\n%s" % (gid, main, sha[:12], rel_text))

    def release(self, g, sha):
        tree = os.path.join(self.cfg.worktree_root, "release-" + g["id"])
        try:
            self.repo.detached(tree, sha)
            res = osx.run(["sh", "-c", self.release_cmd], cwd=tree, timeout=3600,
                          env=self.cfg.env(LOOP_RELEASE_SHA=sha, LOOP_GROUP=g["id"],
                                           LOOP_TICKETS=",".join(g.get("members") or [])),
                          what="release")
            text = (res.out + res.err).strip()[-2000:]
            return res.ok, text if res.ok else "rc %s%s\n%s" % (
                res.rc, " (timeout)" if res.timed_out else "", text)
        except osx.OSFailure as exc:
            return False, str(exc)
        finally:
            try:
                self.repo.remove_worktree(tree)
            except osx.OSFailure:
                pass

    # ------------------------------------------------------------ 3. dispatch

    def dispatch(self, snap):
        running = sum(1 for t in snap.tickets.values() if t.get("state") == "running")
        room = self.cfg.max_parallel - running
        started = 0
        for ident in dagmod.topo(snap.dag):
            if room <= 0:
                break
            t = snap.tickets.get(ident)
            if not t or t.get("state") != "ready":
                continue
            gid = self.group_for(snap, ident)
            if gid is None:
                continue
            self.start_issuer(ident, gid)
            snap = self.pool.snapshot()
            room -= 1
            started += 1
        return started

    def joinable(self, snap, ident, group):
        """Could ready ticket `ident` start on `group`'s branch right now?"""
        if group.get("alone") or dagmod.verify(snap.dag, ident) == "alone":
            return False
        for d in dagmod.deps(snap.dag, ident):
            dt = snap.tickets.get(d) or {}
            if dt.get("state") == "landed":
                continue
            if dt.get("group") == group["id"] and dt.get("state") == "merged":
                continue
            return False
        return True

    def group_for(self, snap, ident):
        """The group this ticket would join now, creating it if needed; None = must wait."""
        t, dag = snap.tickets[ident], snap.dag
        alone = dagmod.verify(dag, ident) == "alone"
        current = snap.groups.get(t.get("group") or "")
        if current and current.get("state") == "open":
            open_group = current
        elif alone:
            open_group = None
        else:
            open_group = next((g for g in snap.groups.values()
                               if g.get("state") == "open" and not g.get("alone")), None)
        for d in dagmod.deps(dag, ident):
            dt = snap.tickets.get(d) or {}
            if dt.get("state") == "landed":
                continue
            if open_group and dt.get("group") == open_group["id"] and dt.get("state") == "merged":
                continue
            return None
        if open_group:
            return open_group["id"]
        return self.new_group(alone)

    def new_group(self, alone):
        main = self.cfg.main_branch
        base = self.repo.sha(main)
        if not base:
            raise Stop(2, "no %s branch in %s" % (main, self.cfg.root))

        def change(tx):
            gid = tx.data.next_group_id()
            g = {"id": gid, "branch": "int/" + gid, "base": base, "state": "open", "alone": alone,
                 "members": [], "fix_round": 0, "job": None, "created": osx.now()}
            event(g, "opened from %s %s" % (main, base[:12]))
            tx.put_group(g)
            return gid
        gid = self.pool.update(change, "group: open")
        if not self.repo.sha("refs/heads/int/" + gid):
            self.repo.run("branch", "int/" + gid, base)
        return gid

    def start_issuer(self, ident, gid):
        branch = "int/" + gid

        def change(tx):
            t = tx.ticket(ident)
            if t.get("state") != "ready":
                return False
            t["state"], t["group"], t["target"], t["run"] = "running", gid, branch, None
            t["claimed_at"] = osx.now()
            event(t, "dispatched", target=branch)
            tx.put_ticket(t)
            g = tx.data.groups[gid]
            if ident not in g["members"]:
                g["members"].append(ident)
                tx.put_group(g)
            return True
        if not self.pool.update(change, "#%s: dispatched to %s" % (ident, branch)):
            return
        log = os.path.join(self.cfg.runs_dir(ident), "issuer.log")
        argv = ["python3", os.path.join(LOOP, "ticket_issuer.py"), "--id", ident, "--target", branch]
        try:
            self.children["issuer:" + ident] = osx.spawn(argv, cwd=self.cfg.root, env=self.cfg.env(),
                                                         log_path=log)
        except osx.OSFailure as exc:
            self._release_orphan(ident)
            raise Stop(2, "cannot start ticket_issuer: %s" % exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forever", action="store_true", help="keep watching the pool")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        pool = pool_for(cfg)
        lock = osx.PidLock(os.path.join(cfg.worktree_root, "controller.lock")).acquire()
    except osx.OSFailure as exc:
        print("ticket_controller: %s" % exc, file=sys.stderr)
        return 2
    ctl = Controller(cfg, pool)

    def on_signal(signum, frame):
        ctl.stopping = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        return ctl.run(forever=args.forever)
    except Stop as exc:
        print("ticket_controller: stopped: %s" % exc, file=sys.stderr)
        return exc.code
    except osx.OSFailure as exc:
        print("ticket_controller: %s" % exc, file=sys.stderr)
        try:
            pool.report("decision", "controller", "the controller stopped: %s" % exc)
        except osx.OSFailure:
            pass
        return 2
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
