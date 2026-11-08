#!/usr/bin/env python3
"""ticket_controller : the loop's only driver. No LLM; it never decides anything a script can't.

Each tick, in this order:

  1. finish jobs  - an issuer, regression or fixer that ended; read what it left in the pool
  2. groups       - main is merged into every waiting integration branch first (#72: clean ->
                    no state change; a conflict -> the fixer once the group is due for its
                    regression); an open group whose tickets are all merged or stuck becomes a
                    PR; a PR gets a regression; red -> auto_fixer (at most devloop.max_rounds);
                    green -> main fast-forwards to the integration branch (a main that moved
                    meanwhile is merged in and verified again) and the project's release runs
  3. dispatch     - ready tickets whose dependencies are in their starting point (landed, or
                    merged into the same integration branch) get a ticket_issuer, up to
                    devloop.max_parallel at a time; "together" tickets share one open group,
                    an "alone" ticket gets its own
  4. memory       - one memory_keeper at a time for a memory file whose lessons are due

Everything it knows is in the pool and in git, so a restart picks up where it was; issuers,
regressions and fixers run detached and are adopted by the next controller.

    python3 devloop/ticket_controller.py            # run until nothing is left to do
    python3 devloop/ticket_controller.py --forever  # keep watching the pool for new tickets

Exit: 0 idle (when tickets or groups are left undone, one idle page and the same lines on stderr
say what each waits for) · 3 stopped for the user (environment / could not measure; a page was written) ·
2 could not start (another controller, config, pool).
"""

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dag as dagmod  # noqa: E402
from core import agents, codemap, host, memory, osx, release  # noqa: E402
from core.config import Config, pool_for  # noqa: E402
from core.gitx import Git  # noqa: E402
from core.pool import event  # noqa: E402

LOOP = os.path.dirname(os.path.abspath(__file__))
LOST = "lost"   # merge_no_reverify: the branch moved under the compare-and-swap


def under_paths(path, entries):
    """True when `path` equals an entry or lies under it (the hard_block.paths rule)."""
    return any(path == e or path.startswith(e.rstrip("/") + "/") for e in entries)


def only_no_reverify(files, entries):
    """D-090: True when every changed file is under devloop.no_reverify_paths. An empty file
    list counts as such; an empty `entries` ([]) turns the shortcut off."""
    if not entries:
        return False
    return all(under_paths(f, entries) for f in files)


class Stop(Exception):
    def __init__(self, code, why):
        super().__init__(why)
        self.code = code


def _gnum(gid):
    digits = "".join(c for c in str(gid) if c.isdigit())
    return (int(digits) if digits else 0, str(gid))


def _first_line(text):
    lines = str(text or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def idle_lines(snap):
    """What each ticket or group left undone waits for, one line each (empty = nothing left).
    Tickets in numeric id order, then groups in number order; landed, cancelled and merged
    tickets and landed or cancelled groups get no line."""
    out = []
    for ident in sorted(snap.tickets, key=_gnum):
        t = snap.tickets[ident]
        state = t.get("state")
        if state == "stuck":
            stuck = t.get("stuck") or {}
            out.append("#%s stuck (%s): %s" % (ident, stuck.get("reason") or "?", _first_line(stuck.get("text"))))
        elif state == "ready":
            waits = []
            for d in dagmod.deps(snap.dag, ident):
                dt = snap.tickets.get(d)
                if dt is None:
                    waits.append("#%s missing" % d)
                elif dt.get("state") == "landed":
                    continue
                elif dt.get("state") == "merged":
                    g = snap.groups.get(dt.get("group") or "") or {}
                    waits.append("#%s merged in %s (%s)" % (d, dt.get("group") or "?", g.get("state") or "missing"))
                else:
                    waits.append("#%s %s" % (d, dt.get("state")))
            out.append("#%s ready, waits for: %s" % (ident, ", ".join(waits) or "nothing found"))
    for gid in sorted(snap.groups, key=_gnum):
        g = snap.groups[gid]
        state = g.get("state")
        if state == "stuck":
            history = g.get("history") or []
            out.append("%s stuck: %s" % (gid, history[-1].get("event", "") if history else ""))
        elif g.get("paused") and state not in ("landed", "cancelled"):
            mark = g["paused"]
            out.append("%s paused (%s): %s" % (gid, mark.get("reason") or "?", _first_line(mark.get("text"))))
    return out


class Controller:
    def __init__(self, cfg, pool):
        self.cfg, self.pool = cfg, pool
        self.repo = Git(cfg.root)
        self.children = {}          # key -> Popen, for jobs this process started
        self.release_cmd = cfg.require_release()
        self.stopping = False
        self.forever = False        # run(forever) sets it: an env result pauses the group instead of Stop(3)

    # ------------------------------------------------------------ loop

    def run(self, forever=False):
        self.forever = bool(forever)
        while not self.stopping:
            busy = self.tick()
            if not busy and not forever:
                self.report_idle()
                return 0
            time.sleep(self.cfg.poll_seconds)
        return 0

    def report_idle(self):
        """A run without --forever that ends with work left: one idle page (and the same text on
        stderr) saying what each ticket or group waits for. Nothing when nothing is left or when
        the controller is stopping."""
        if self.stopping:
            return None
        lines = idle_lines(self.pool.snapshot())
        if not lines:
            return None
        text = "\n".join(["nothing more can move; left undone:"] + lines)
        print(text, file=sys.stderr)
        return self.pool.report("idle", "controller", text)

    def tick(self):
        snap = self.pool.snapshot()
        self.finish_jobs(snap)
        snap = self.pool.snapshot()
        self.advance_groups(snap)
        snap = self.pool.snapshot()
        started = self.dispatch(snap)
        snap = self.pool.snapshot()
        keeping = self.consolidate(snap)
        running = [t for t in snap.tickets.values() if t.get("state") == "running"]
        active = [g for g in snap.groups.values() if g.get("state") in ("open", "pr", "fixing", "verified")]
        return bool(started or keeping or running or active or self.children)

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
                t.pop("waiting", None)
                event(t, "issuer ended without an outcome; dispatch again")
                tx.put_ticket(t)
        self.pool.update(change, "#%s: issuer gone, back to ready" % ident)

    # ------------------------------------------------------------ 2. groups

    def advance_groups(self, snap):
        for g in sorted(snap.groups.values(), key=lambda x: int(x["id"][1:])):
            state = g.get("state")
            if state not in ("landed", "cancelled") and not g.get("job") and self.all_cancelled(snap, g):
                self.set_group(g["id"], "cancelled", "all tickets cancelled: group closed")
                continue
            behind = self.follow_main(g)
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
                if g.get("paused") and self.forever:
                    continue                   # waits for `tickets.py rerun gN`
                if behind:
                    self.fix_main_moved(g)     # main conflicts with the branch: the fixer merges it
                    continue
                if g.get("fix_retry") and g.get("fix_report") and os.path.isfile(g["fix_report"]):
                    self.start_fixer(g["id"], g["fix_report"], int(g.get("fix_round") or 0) + 1)
                    continue                   # the last round never reached the fixer agent
                self.start_regression(g)
            elif state == "verified":
                self.land(g)

    def follow_main(self, g):
        """Merge main into a waiting integration branch (any state but landed, cancelled or
        verified, no job, branch exists): a fast-forward when the branch has nothing of its own,
        else a merge commit; one event naming main's sha. A clean merge changes no state and no
        fix round. A main sha already tried on this group (`main_tried`) is not tried again.
        Returns True when the branch still lacks main because of a conflict."""
        if g.get("state") in ("landed", "cancelled", "verified") or g.get("job") or not g.get("branch"):
            return False
        head, main_sha = self.repo.sha("refs/heads/" + g["branch"]), self.repo.sha(self.cfg.main_branch)
        if not head or not main_sha or self.repo.is_ancestor(main_sha, head):
            return False
        if g.get("main_tried") == main_sha:
            return True                         # this main conflicted before
        kind, _ = self.merge_main(g, head, main_sha)
        if kind == LOST:
            return False                        # the branch moved: not tried; the next tick looks again
        self.set_group(g["id"], None, self.main_event(kind, main_sha, g["branch"]), main_tried=main_sha)
        return kind == "conflict"

    @staticmethod
    def main_event(kind, main_sha, branch):
        what = {"ff": "fast-forwarded %s to it" % branch, "merged": "merged it into %s" % branch,
                "conflict": "it conflicts with %s; not merged" % branch}[kind]
        return "main moved to %s: %s" % (main_sha[:12], what)

    def merge_main(self, g, head, main_sha):
        """Bring main_sha into g's branch at `head`. Returns ("ff", main_sha) when head is an
        ancestor of main, ("merged", sha) for a clean merge commit (made as devloop in the
        throwaway detached worktree <worktrees>/main-<gid>, never set up, always removed),
        ("conflict", None), or (LOST, None) when the branch moved under the compare-and-swap.
        Only the branch ref changes; the caller records the event."""
        if self.repo.is_ancestor(head, main_sha):
            return ("ff", main_sha) if self.repo.move_ref(g["branch"], main_sha, head) else (LOST, None)
        tree = os.path.join(self.cfg.worktree_root, "main-" + g["id"])
        try:
            self.repo.detached(tree, head)
            t = Git(tree)
            res = t.run("-c", "user.name=devloop", "-c", "user.email=devloop@localhost", "merge",
                        "--no-ff", "--no-edit", main_sha, check=False)
            if not res.ok:
                t.run("merge", "--abort", check=False)
                return "conflict", None
            new = t.sha("HEAD")
            if not new or new == head:
                return "conflict", None
            if not self.repo.move_ref(g["branch"], new, head):
                return LOST, None
            return "merged", new
        except osx.OSFailure as exc:
            print("ticket_controller: merging main into %s failed: %s" % (g.get("branch"), exc),
                  file=sys.stderr)
            return "conflict", None
        finally:
            try:
                self.repo.remove_worktree(tree)
            except osx.OSFailure:
                pass

    def fix_main_moved(self, g):
        """main conflicts with the branch: a fix round with a main-moved report, or at
        devloop.max_rounds a stuck group and a decision page."""
        gid, main = g["id"], self.cfg.main_branch
        report = os.path.join(self.cfg.runs_dir("regression", gid), "main-moved.json")
        osx.write_json(report, {"status": "main_moved", "summary": "%s moved; merge it in" % main})
        rnd = int(g.get("fix_round") or 0)
        if rnd >= self.cfg.max_rounds:
            self.set_group(gid, "stuck", "main kept moving")
            self.pool.report("decision", gid, "group %s cannot catch up with %s" % (gid, main))
            return
        self.start_fixer(gid, report, rnd + 1)

    @staticmethod
    def all_cancelled(snap, g):
        members = g.get("members") or []
        return bool(members) and all((snap.tickets.get(i) or {}).get("state") == "cancelled" for i in members)

    def set_group(self, gid, state, why, drop=(), **fields):
        def change(tx):
            g = tx.data.groups[gid]
            if state is not None:
                g["state"] = state
            g.update(fields)
            for key in drop:
                g.pop(key, None)
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
        self.set_group(gid, "fixing", "fix round %d" % rnd, drop=("fix_retry", "paused"),
                       fix_round=rnd, fix_report=report_path)
        self._start_job(gid, "fixer", argv, report_path)

    def _start_job(self, gid, kind, argv, out):
        log = os.path.join(self.cfg.runs_dir(kind, gid), "%s.log" % kind)
        proc = osx.spawn(argv, cwd=self.cfg.root, env=self.cfg.env(), log_path=log)
        self.children["%s:%s" % (kind, gid)] = proc
        stamp = {"pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(), "started": osx.now()}
        self.set_group(gid, None, "%s started" % kind, job={"kind": kind, "stamp": stamp, "out": out},
                       drop=("paused",) if kind == "regression" else ())

    def pause(self, gid, reason, text):
        """Mark a group whose environment broke; a --forever controller leaves it alone until
        `tickets.py rerun gN` removes the mark."""
        mark = {"reason": reason, "at": osx.now(), "text": str(text or "")[:2000]}
        return self.set_group(gid, None, "paused: %s" % reason, paused=mark)

    def _env_page_text(self, gid):
        rows = [(name, row) for name, row in self.pool.snapshot().inbox.items()
                if row.get("kind") == "env" and row.get("ref") == gid]
        return sorted(rows)[-1][1].get("text") if rows else ""

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
            self.pool.report(kind, gid, "group %s: regression %s - %s. Fix the environment, then run "
                             "`actl tickets rerun %s` (on the board: 環境修好了,重跑); a controller "
                             "without --forever: run it again." % (gid, status, report.get("summary"), gid))
            self.pause(gid, status, report.get("summary"))
            if not self.forever:
                raise Stop(3, "regression %s" % status)
            return
        rnd = int(g.get("fix_round") or 0)
        if self.drop_redder_round(g, report):
            return
        if rnd >= self.cfg.max_rounds:
            self.set_group(gid, "stuck", "red after %d fix rounds" % rnd)
            self.pool.report("decision", gid, "group %s is still red after %d fix rounds:\n%s" % (
                gid, rnd, report.get("summary")))
            return
        self.start_fixer(gid, job["out"], rnd + 1)

    def drop_redder_round(self, g, report):
        """The red regression `report` measured the branch right after a fix round (its sha is the
        round's fix_end_sha) and fails more cases than the red report the round started from: keep
        the round's head under refs/devloop/dropped/<gid>-r<N>, compare-and-swap the branch back to
        fix_start_sha, and start the next round from the old report (no regression: the branch is
        back at the head that report measured), or at devloop.max_rounds a stuck group and a
        decision page. Returns True when the round was dropped; a lost swap resets nothing."""
        gid, rnd = g["id"], int(g.get("fix_round") or 0)
        start, end, base_path = g.get("fix_start_sha"), g.get("fix_end_sha"), g.get("fix_report")
        if not (start and end and base_path) or report.get("status") != "red" or report.get("sha") != end:
            return False
        try:
            base = osx.read_json(base_path, {}) or {}
        except osx.OSFailure:
            return False
        if base.get("status") != "red":
            return False
        before, after = len(base.get("failures") or []), len(report.get("failures") or [])
        if after <= before:
            return False
        head = report["sha"]
        ref = "refs/devloop/dropped/%s-r%d" % (gid, rnd)
        if not self.repo.run("update-ref", ref, head, check=False).ok:
            return False
        dropped = self.repo.out("rev-list", "--reverse", "%s..%s" % (start, head)).split()
        if not self.repo.move_ref(g["branch"], start, head):
            self.repo.run("update-ref", "-d", ref, head, check=False)
            return False                        # the branch moved: the next round starts from this report

        def change(tx):
            group = tx.data.groups[gid]
            event(group, "fix round %d dropped: %d failing after it, %d before" % (rnd, after, before),
                  dropped=dropped, ref=ref, failing_before=before, failing_after=after)
            tx.put_group(group)
        self.pool.update(change, "group %s: fix round %d dropped" % (gid, rnd))
        if rnd < self.cfg.max_rounds:
            self.start_fixer(gid, base_path, rnd + 1)
            return True
        self.set_group(gid, "stuck", "red after %d fix rounds" % rnd)
        self.pool.report("decision", gid, "group %s is still red after %d fix rounds; the last round made it "
                         "redder (%d -> %d failing) and was dropped (%s):\n%s" % (
                             gid, rnd, before, after, ref, base.get("summary")))
        return True

    def after_fixer(self, gid, rc):
        g = self.pool.snapshot().groups.get(gid) or {}
        rnd = int(g.get("fix_round") or 0)
        if g.get("fix_agent_round") != g.get("fix_round"):
            self.fixer_did_not_run(gid, rc, rnd)
            return
        end = {"fix_end_sha": self.repo.sha(g["branch"])} if g.get("branch") else {}
        if rc == 3:
            self.set_group(gid, "stuck", "fixer needs a decision", job=None, **end)
            return
        self.set_group(gid, "pr", "fixer done (rc %s): verify again" % rc, job=None, **end)

    def fixer_did_not_run(self, gid, rc, rnd):
        """auto_fixer ended (any rc, None for an adopted job) without starting the fixer agent:
        the round is not counted, no regression starts, the group is paused with one page and
        `fix_retry` makes the next start a fixer again. For rc 86 auto_fixer's env page is the page."""
        self.set_group(gid, "pr", "fix round %d did not run (rc %s)" % (rnd, rc), job=None,
                       fix_round=max(0, rnd - 1), fix_retry=True)
        if rc == 86:
            why = "devloop.worktree_setup failed for the fixer of %s" % gid
            self.pause(gid, "env", self._env_page_text(gid) or why)
        else:
            why = ("group %s: fix round %d did not run: auto_fixer ended (rc %s) before starting the fixer "
                   "agent: %s. The round was not counted and no regression was started. Fix the cause, "
                   "then `actl tickets rerun %s` (on the board: 環境修好了,重跑); a controller without "
                   "--forever: run it again." % (gid, rnd, "unknown" if rc is None else rc,
                                                 self._fixer_log_tail(gid), gid))
            self.pool.report("env", gid, why)
            self.pause(gid, "fixer", why)
        if not self.forever:
            raise Stop(3, "fix round %d of %s did not run" % (rnd, gid))

    def _fixer_log_tail(self, gid):
        """The last non-empty line of runs/fixer/<gid>/fixer.log, or 'no fixer log'."""
        path = os.path.join(self.cfg.worktree_root, "runs", "fixer", gid, "fixer.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            lines = []
        return lines[-1] if lines else "no fixer log"

    # ------------------------------------------------------------ landing

    def land(self, g):
        gid, sha, main = g["id"], g.get("verified_sha"), self.cfg.main_branch
        if self.all_cancelled(self.pool.snapshot(), g):
            self.set_group(gid, "cancelled", "all tickets cancelled: group closed")
            return
        old = self.repo.sha(main)
        if self.repo.sha(g["branch"]) != sha:
            self.set_group(gid, "pr", "branch moved after verification: verify again")
            return
        if not self.repo.is_ancestor(old, sha):
            merged = self.merge_no_reverify(g, sha, old)
            if merged == LOST:
                self.set_group(gid, "pr", "branch moved after verification: verify again")
                return
            if merged:
                sha = merged
                old = self.repo.sha(main)
                if not self.repo.is_ancestor(old, sha):
                    return                      # main moved again; the next tick looks again
        if not self.repo.is_ancestor(old, sha):
            kind, _ = self.merge_main(g, sha, old)
            if kind == LOST:
                self.set_group(gid, "pr", "branch moved after verification: verify again")
                return
            why = self.main_event(kind, old, g["branch"])
            if kind != "conflict":              # clean: verify the merged branch, no fix round
                self.set_group(gid, "pr", why + "; verify again", drop=("verified_sha",), main_tried=old)
                return
            self.set_group(gid, None, why, main_tried=old)
            self.fix_main_moved(self.pool.snapshot().groups[gid])
            return
        sha = self.refresh_index(g, sha)
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
        rel_log = release.log_path(self.cfg, gid)
        snap = self.pool.snapshot()
        ids = [i for i in g.get("members") or [] if (snap.tickets.get(i) or {}).get("state") == "merged"]

        def change(tx):
            for i in ids:
                t = tx.ticket(i)
                t["state"], t["landed_sha"] = "landed", sha
                t["cost_total"] = agents.total(t.get("cost"))
                event(t, "landed", group=gid)
                tx.put_ticket(t)
            grp = tx.data.groups[gid]
            grp["state"], grp["landed_sha"], grp["release"] = "landed", sha, "ok" if released else "failed"
            grp["release_log"] = rel_log
            event(grp, "landed" + ("" if released else " (release failed)"))
            tx.put_group(grp)
        self.pool.update(change, "group %s landed" % gid)
        self.repo.remove_worktree(os.path.join(self.cfg.worktree_root, "fix-" + gid))
        if released:
            self.pool.report("landed", gid, "group %s landed and released: #%s (%s); release log: %s" % (
                gid, ", #".join(ids), sha[:12], rel_log))
        else:
            self.pool.report("decision", gid, "group %s is in %s (%s) but the release failed - "
                             "not retried, not rolled back (release log: %s):\n%s" % (
                                 gid, main, sha[:12], rel_log, rel_text))

    def main_moved_files(self, sha, main_sha):
        """Files main changed since it parted from the verified sha, or None if git cannot say."""
        base = self.repo.run("merge-base", sha, main_sha, check=False)
        if not base.ok or not base.out.strip():
            return None
        res = self.repo.run("diff", "--name-only", "--no-renames", base.out.strip(), main_sha,
                            check=False)
        if not res.ok:
            return None
        return [line for line in res.out.splitlines() if line]

    def merge_no_reverify(self, g, sha, main_sha):
        """D-090: when main moved only in devloop.no_reverify_paths, merge it into the verified
        sha in a throwaway worktree and move the integration branch there; the regression result
        still holds. Returns the merge sha, None (take the fixer path) or LOST (branch moved)."""
        files = self.main_moved_files(sha, main_sha)
        if files is None or not only_no_reverify(files, self.cfg.no_reverify_paths):
            return None
        tree = os.path.join(self.cfg.worktree_root, "nrv-" + g["id"])
        try:
            self.repo.detached(tree, sha)
            t = Git(tree)
            res = t.run("-c", "user.name=devloop", "-c", "user.email=devloop@localhost", "merge",
                        "--no-ff", "--no-edit", main_sha, check=False)
            if not res.ok:
                t.run("merge", "--abort", check=False)
                return None
            new = t.sha("HEAD")
            if not new or new == sha:
                return None
            if not self.repo.move_ref(g["branch"], new, sha):
                return LOST
            self.set_group(g["id"], None, "main took only no-reverify paths: merged without "
                           "re-verifying", verified_sha=new)
            return new
        except osx.OSFailure as exc:
            print("ticket_controller: no-reverify merge failed: %s" % exc, file=sys.stderr)
            return None
        finally:
            try:
                self.repo.remove_worktree(tree)
            except osx.OSFailure:
                pass

    def refresh_index(self, g, sha):
        """Commit the regenerated code index on top of the verified sha (D-084 Q11); a change to
        one generated file only, so the regression result still holds. Returns the sha to land."""
        rel = self.cfg.index_path
        if not rel:
            return sha
        tree = os.path.join(self.cfg.worktree_root, "index-" + g["id"])
        try:
            self.repo.detached(tree, sha)
            text = codemap.build(tree, skip={rel})
            path = os.path.join(tree, rel)
            try:
                with open(path, errors="replace") as fh:
                    if fh.read() == text:
                        return sha
            except FileNotFoundError:
                pass
            osx.atomic_write(path, text)
            t = Git(tree)
            t.run("add", "--", rel)
            t.run("-c", "user.name=devloop", "-c", "user.email=devloop@localhost", "commit", "-q",
                  "-m", "devloop: code map for group %s" % g["id"])
            new = t.sha("HEAD")
            if not self.repo.move_ref(g["branch"], new, sha):
                return sha
            self.set_group(g["id"], None, "code map refreshed", verified_sha=new)
            return new
        except osx.OSFailure as exc:
            print("ticket_controller: code map not refreshed: %s" % exc, file=sys.stderr)
            return sha
        finally:
            try:
                self.repo.remove_worktree(tree)
            except osx.OSFailure:
                pass

    def release(self, g, sha):
        return release.run(self.cfg, g, sha)

    # ------------------------------------------------------------ 4. memory

    def consolidate(self, snap):
        """At most one memory_keeper at a time, for the memory file that waited longest."""
        job = (snap.memory_state or {}).get("job")
        proc = self.children.get("keeper")
        if proc is not None and proc.poll() is None:
            return True
        if proc is None and job and not osx.stamp_dead(job.get("stamp"), grace_seconds=6 * 3600):
            return True                         # adopted keeper, still alive
        if proc is not None or job:
            self.children.pop("keeper", None)
            self._keeper_job(None)
        due = memory.due(snap)
        if not due:
            return False
        layer, name = due[0]
        argv = ["python3", os.path.join(LOOP, "memory_keeper.py"), "--layer", layer, "--name", name]
        log = os.path.join(self.cfg.runs_dir("memory"), "keeper.log")
        proc = osx.spawn(argv, cwd=self.cfg.root, env=self.cfg.env(), log_path=log)
        self.children["keeper"] = proc
        self._keeper_job({"path": memory.rel(layer, name), "stamp": {
            "pid": proc.pid, "host": osx.host(), "boot": osx.boot_id(), "started": osx.now()}})
        return True

    def _keeper_job(self, job):
        def change(tx):
            state = dict(tx.data.memory_state or {})
            state["job"] = job
            tx.write("memory/state.json", state)
        self.pool.update(change, "memory: keeper %s" % ("started" if job else "done"))

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
        members = group.get("members") or []
        if ident not in members and len(members) >= self.cfg.max_group_size:
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
        cur_members = (current or {}).get("members") or []
        if (current and current.get("state") == "open"
                and (ident in cur_members or len(cur_members) < self.cfg.max_group_size)):
            open_group = current
        elif alone:
            open_group = None
        else:
            limit = self.cfg.max_group_size
            open_group = next((g for _, g in sorted(snap.groups.items(), key=lambda kv: _gnum(kv[0]))
                               if g.get("state") == "open" and not g.get("alone")
                               and len(g.get("members") or []) < limit), None)
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
            members = tx.data.groups[gid]["members"]
            if ident not in members and len(members) >= self.cfg.max_group_size:
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
    sampler = host.Sampler(cfg).start()      # #44: one line per devloop.host_sample_seconds

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
        sampler.stop()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
