"""The ticket pool: a git repository that holds the only state of the loop.

    tickets/<id>.json   one ticket
    dag.json            {"tickets": {"<id>": {"depends_on": [...], "verify": "together"|"alone"}}}
    groups/<gid>.json   one integration branch (= one PR)
    inbox/<name>.json   reports for the main session
    memory/role/<role>.md, memory/model/<model>.md, memory/project.md   what agents learned
    memory/inbox/<name>.json   lessons waiting to be consolidated; memory/state.json

Rules (D-079): read = sync first; write = change, commit, push; a write counts only once the push
succeeded. A rejected push throws the local commit away, re-reads, and applies the change again,
so a change is a function ("how to change"), never a saved diff. Processes on one machine share
the working copy under an flock; processes on different machines meet at the remote.
"""

import fcntl
import json
import os
import re

from . import osx
from .gitx import Git, GitError

TICKET_STATES = ("ready", "running", "merged", "landed", "stuck", "cancelled")
GROUP_STATES = ("open", "pr", "fixing", "landed", "stuck")


class PoolError(osx.OSFailure):
    pass


class Pool:
    def __init__(self, path, branch="main", remote="origin", tries=8):
        self.path = os.path.abspath(path)
        self.branch = branch
        self.remote_name = remote
        self.tries = tries
        self.git = Git(self.path, timeout=120)
        self._remote = None

    # ------------------------------------------------------------ setup

    def init(self):
        """Create an empty pool repository if there is none."""
        osx.makedirs(self.path)
        if not os.path.exists(os.path.join(self.path, ".git")):
            self.git.run("init", "-q", "-b", self.branch)
            self.git.run("config", "user.name", "agent-control")
            self.git.run("config", "user.email", "agent-control@localhost")
        for sub in ("tickets", "groups", "inbox", "memory/inbox"):
            osx.makedirs(os.path.join(self.path, sub))
            keep = os.path.join(self.path, sub, ".keep")
            if not os.path.exists(keep):
                osx.atomic_write(keep, "")
        if not os.path.exists(os.path.join(self.path, "dag.json")):
            osx.write_json(os.path.join(self.path, "dag.json"), {"tickets": {}})
        self.git.run("add", "-A")
        if self.git.run("diff", "--cached", "--quiet", check=False).rc != 0:
            self.git.run("commit", "-q", "-m", "pool: init")
        return self

    def exists(self):
        return os.path.isfile(os.path.join(self.path, "dag.json"))

    @property
    def remote(self):
        if self._remote is None:
            self._remote = bool(self.remote_name) and self.git.has_remote(self.remote_name)
        return self._remote

    # ------------------------------------------------------------ locking

    def _lock(self):
        lock_path = os.path.join(self.path, ".git", "devloop-pool.lock")
        try:
            fh = open(lock_path, "a")
            fcntl.flock(fh, fcntl.LOCK_EX)
        except OSError as exc:
            raise PoolError(osx.explain(exc, "cannot lock pool %s" % self.path))
        return fh

    def _sync_locked(self):
        if not self.remote:
            self.git.run("reset", "-q", "--hard", "HEAD")
            return
        self.git.run("fetch", "-q", self.remote_name, self.branch)
        upstream = "%s/%s" % (self.remote_name, self.branch)
        if self.git.sha(upstream):
            self.git.run("reset", "-q", "--hard", upstream)

    # ------------------------------------------------------------ reading

    def snapshot(self):
        """Sync, then read everything once. Returns a Snapshot (plain data)."""
        if not self.exists():
            raise PoolError("no ticket pool at %s - run `python3 devloop/tickets.py init`" % self.path)
        fh = self._lock()
        try:
            self._sync_locked()
            return Snapshot(self._read_all())
        finally:
            fh.close()

    def _read_all(self):
        data = {"dag": self._read("dag.json", {"tickets": {}}), "tickets": {}, "groups": {}, "inbox": {},
                "lessons": {}, "memory": {}, "memory_state": self._read("memory/state.json", {})}
        folders = {"tickets": "tickets", "groups": "groups", "inbox": "inbox", "lessons": "memory/inbox"}
        for kind, folder_rel in folders.items():
            folder = os.path.join(self.path, folder_rel)
            try:
                names = sorted(os.listdir(folder))
            except FileNotFoundError:
                names = []
            for name in names:
                if name.endswith(".json"):
                    data[kind][name[:-5]] = self._read(os.path.join(folder_rel, name), {})
        for rel in _memory_files(self.path):
            data["memory"][rel] = _read_text(os.path.join(self.path, rel))
        return data

    def _read(self, rel, default):
        return osx.read_json(os.path.join(self.path, rel), default)

    # ------------------------------------------------------------ writing

    def update(self, change, message):
        """Apply `change(tx)` and make it durable. `tx` reads and writes pool files.

        Returns whatever `change` returned. `change` may run more than once (after a rejected
        push it runs again on fresh data), so it must only touch the pool through `tx`.
        """
        fh = self._lock()
        try:
            last = None
            for _ in range(self.tries):
                self._sync_locked()
                tx = Tx(self)
                result = change(tx)
                self.git.run("add", "-A")
                if self.git.run("diff", "--cached", "--quiet", check=False).rc == 0:
                    return result
                self.git.run("commit", "-q", "-m", message)
                if not self.remote:
                    return result
                res = self.git.run("push", "-q", self.remote_name,
                                   "HEAD:refs/heads/" + self.branch, check=False)
                if res.ok:
                    return result
                last = (res.err or res.out).strip()
                if "rejected" not in last and "fetch first" not in last and "non-fast-forward" not in last:
                    self.git.run("reset", "-q", "--hard", "HEAD~1", check=False)
                    raise PoolError("pool push failed (not a race): %s" % last.splitlines()[-1:])
            raise PoolError("pool push kept being rejected after %d tries: %s" % (self.tries, last))
        except GitError as exc:
            raise PoolError(str(exc))
        finally:
            fh.close()

    # ------------------------------------------------------------ helpers

    def report(self, kind, ref, text, **extra):
        """Leave a page for the main session (kinds: created, landed, decision, env)."""
        name = "%s-%s-%s" % (osx.now().replace(":", "").replace("-", ""), kind, ref)

        def change(tx):
            row = {"kind": kind, "ref": ref, "text": text, "at": osx.now(), "acked": False}
            row.update(extra)
            tx.write("inbox/%s.json" % name, row)
        self.update(change, "inbox: %s %s" % (kind, ref))
        return name


class Tx:
    """What a change function may do: read and write pool files, nothing else."""

    def __init__(self, pool):
        self.pool = pool
        self.data = Snapshot(pool._read_all())

    def write(self, rel, obj):
        osx.write_json(os.path.join(self.pool.path, rel), obj)
        self._refresh(rel, obj)

    def write_text(self, rel, text):
        osx.atomic_write(os.path.join(self.pool.path, rel), text)
        self.data.raw["memory"][rel] = text

    def delete(self, rel):
        try:
            os.unlink(os.path.join(self.pool.path, rel))
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PoolError(osx.explain(exc, "cannot delete %s" % rel))

    def ticket(self, ident):
        return self.data.ticket(ident)

    def put_ticket(self, ticket):
        self.write("tickets/%s.json" % ticket["id"], ticket)

    def put_group(self, group):
        self.write("groups/%s.json" % group["id"], group)

    def put_dag(self, dag):
        self.write("dag.json", dag)

    def _refresh(self, rel, obj):
        if rel == "dag.json":
            self.data.raw["dag"] = obj
            return
        if rel == "memory/state.json":
            self.data.raw["memory_state"] = obj
            return
        folder, name = rel.rsplit("/", 1)
        kind = {"memory/inbox": "lessons"}.get(folder, folder)
        self.data.raw.setdefault(kind, {})[name[:-5]] = obj


class Snapshot:
    def __init__(self, raw):
        self.raw = raw

    @property
    def dag(self):
        return self.raw["dag"].setdefault("tickets", {})

    @property
    def tickets(self):
        return self.raw["tickets"]

    @property
    def groups(self):
        return self.raw["groups"]

    @property
    def inbox(self):
        return self.raw["inbox"]

    @property
    def lessons(self):
        return self.raw["lessons"]

    @property
    def memory(self):
        return self.raw["memory"]

    @property
    def memory_state(self):
        return self.raw["memory_state"]

    def ticket(self, ident):
        found = self.tickets.get(str(ident))
        if found is None:
            raise PoolError("no ticket %s in the pool" % ident)
        return found

    def next_ticket_id(self):
        nums = [int(k) for k in self.tickets if re.fullmatch(r"\d+", k)]
        nums += [int(k) for k in self.dag if re.fullmatch(r"\d+", k)]
        return str(max(nums) + 1 if nums else 1)

    def next_group_id(self):
        nums = [int(k[1:]) for k in self.groups if re.fullmatch(r"g\d+", k)]
        return "g%d" % (max(nums) + 1 if nums else 1)


def _memory_files(root):
    out = []
    base = os.path.join(root, "memory")
    if os.path.isfile(os.path.join(base, "project.md")):
        out.append("memory/project.md")
    for layer in ("role", "model", "discussions"):
        folder = os.path.join(base, layer)
        try:
            names = sorted(os.listdir(folder))
        except FileNotFoundError:
            continue
        out += ["memory/%s/%s" % (layer, n) for n in names if n.endswith(".md")]
    return out


def _read_text(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise PoolError(osx.explain(exc, "cannot read %s" % path))


def event(obj, what, **detail):
    """Append one line to an object's short history (kept on the object itself)."""
    row = {"at": osx.now(), "event": what}
    row.update(detail)
    obj.setdefault("history", []).append(row)
    del obj["history"][:-50]
    return obj


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
