"""Git, through one wrapper: every failure becomes GitError with git's own last line."""

import contextlib
import fcntl
import os
import shlex
import tempfile

from .osx import OSFailure, run


class GitError(OSFailure):
    pass


class Git:
    def __init__(self, cwd, timeout=600):
        self.cwd = cwd
        self.timeout = timeout

    def run(self, *args, check=True, timeout=None, stdin_text=None, env=None):
        full = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", LC_ALL="C")
        full.update(env or {})
        res = run(["git"] + list(args), cwd=self.cwd, env=full, stdin_text=stdin_text,
                  timeout=timeout or self.timeout, what="git " + args[0])
        if check and not res.ok:
            lines = (res.err or res.out).strip().splitlines()
            raise GitError("git %s failed in %s: %s" % (
                " ".join(args[:3]), self.cwd, lines[-1] if lines else "rc %s" % res.rc))
        return res

    def out(self, *args):
        return self.run(*args).out.strip()

    # ------------------------------------------------------------ questions

    def sha(self, ref):
        res = self.run("rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
        return res.out.strip() if res.ok else None

    def is_ancestor(self, older, newer):
        return self.run("merge-base", "--is-ancestor", older, newer, check=False).rc == 0

    def has_remote(self, name):
        return name in self.out("remote").split()

    def changed_files(self, base, head="HEAD"):
        text = self.out("diff", "--name-only", "%s...%s" % (base, head))
        return [line for line in text.splitlines() if line]

    def added_lines(self, base, head="HEAD"):
        text = self.out("diff", "--unified=0", "%s...%s" % (base, head))
        return [line[1:] for line in text.splitlines()
                if line.startswith("+") and not line.startswith("+++")]

    def toplevel(self):
        return self.out("rev-parse", "--show-toplevel")

    def toplevel_or_none(self):
        """The worktree root that holds `cwd`, or None when `cwd` is not in a git checkout."""
        res = self.run("rev-parse", "--show-toplevel", check=False)
        return res.out.strip() if res.ok and res.out.strip() else None

    def prefix(self):
        """`cwd`'s path inside its worktree, '' or ending in '/'."""
        return self.out("rev-parse", "--show-prefix")

    def current_branch(self):
        """The branch checked out at `cwd`, or None for a detached HEAD."""
        res = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return res.out.strip() if res.ok and res.out.strip() else None

    def common_root(self):
        """The main repository's root, also when `cwd` is a linked worktree."""
        common = self.out("rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.dirname(common.rstrip(os.sep))

    def checked_out_at(self, branch):
        """Path of the worktree that has `branch` checked out, or None."""
        path = None
        for line in self.out("worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line == "branch refs/heads/%s" % branch:
                return path
        return None

    def dirty(self):
        return bool(self.out("status", "--porcelain", "--untracked-files=no"))

    # ------------------------------------------------------------ changes

    def merge(self, ref):
        """Merge `ref` into the checked-out branch. Returns (True, "") or (False, why).

        Any failure - a conflict, untracked files in the way, anything git refuses - is the
        caller's to hand to someone who can resolve it; the merge is aborted so the tree is
        as it was."""
        res = self.run("merge", "--no-edit", "--no-ff", ref, check=False)
        if res.ok:
            return True, ""
        self.run("merge", "--abort", check=False)
        lines = [l for l in (res.out + "\n" + res.err).splitlines() if l.strip()]
        keep = [l.strip() for l in lines if "CONFLICT" in l or "error:" in l or "overwritten" in l
                or l.startswith("\t")]
        return False, "; ".join(keep[:8]) or (lines[-1] if lines else "rc %s" % res.rc)

    def own_commits(self, tip, exclude):
        """The commits `tip` made itself, oldest first: its first-parent line minus merge commits
        (and so minus all a merge brought in), not reachable from any of `exclude` (D-098)."""
        return self.out("rev-list", "--reverse", "--first-parent", "--no-merges", tip,
                        "--not", *exclude).split()

    def replay(self, start, commits):
        """Put the checked-out branch on `start` followed by `commits` re-applied in order (a
        commit that became empty is dropped; authors kept, committer devloop). Returns (True, "")
        or (False, why) with the replay aborted, the branch back on its old head; `self.conflict`
        then tells whether it stopped on a conflict (unmerged paths, or untracked files that
        would be overwritten) rather than on any other failure."""
        who = {"GIT_COMMITTER_NAME": "devloop", "GIT_COMMITTER_EMAIL": "devloop@localhost"}
        self.conflict = False
        if not commits:
            res = self.run("reset", "--hard", start, check=False, env=who)
            lines = (res.err or res.out).strip().splitlines()
            return res.ok, "" if res.ok else (lines[-1] if lines else "rc %s" % res.rc)
        fd, todo = tempfile.mkstemp(prefix="devloop-todo-")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write("".join("pick %s\n" % c for c in commits))
            env = dict(who, GIT_SEQUENCE_EDITOR="cp %s" % shlex.quote(todo))
            res = self.run("rebase", "-i", "--empty=drop", "--onto", start, start, check=False, env=env)
        finally:
            os.unlink(todo)
        if res.ok:
            return True, ""
        lines = [l for l in (res.out + "\n" + res.err).splitlines() if l.strip()]
        keep = [l.strip() for l in lines if "CONFLICT" in l or "would be overwritten" in l]
        unmerged = self.run("diff", "--name-only", "--diff-filter=U", check=False).out.split()
        self.conflict = bool(keep or unmerged)
        keep = keep or unmerged
        self.run("rebase", "--abort", check=False)
        return False, "; ".join(keep[:8]) or (lines[-1] if lines else "rc %s" % res.rc)

    def move_ref(self, branch, new, old):
        """Compare-and-swap a branch: succeeds only if it still points at `old`."""
        return self.run("update-ref", "refs/heads/" + branch, new, old, check=False).ok

    def all_ref_shas(self):
        """The sha of every ref (branches, tags, remotes, devloop refs), deduplicated."""
        return sorted(set(self.out("for-each-ref", "--format=%(objectname)").split()))

    def commits_not_in(self, head, shas):
        """Commits reachable from `head` but from none of `shas`, oldest first."""
        return self.out("rev-list", "--reverse", head, "--not", *shas).split()

    def is_merge_commit(self, sha):
        """True when commit `sha` has more than one parent."""
        return len(self.out("rev-list", "--parents", "-n", "1", sha).split()) > 2

    def commit_files(self, parent, files, message, index_file, identity=("devloop", "devloop@localhost")):
        """A commit on top of `parent` whose tree is parent's tree with `files` ({repo path: text})
        replaced, built in the temporary index `index_file` without touching any checkout.
        Returns its sha, or None when the tree would not change."""
        env = {"GIT_INDEX_FILE": index_file}
        try:
            self.run("read-tree", parent, env=env)
            for path, text in sorted(files.items()):
                blob = self.run("hash-object", "-w", "--stdin", stdin_text=text).out.strip()
                self.run("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, path), env=env)
            tree = self.run("write-tree", env=env).out.strip()
        finally:
            try:
                os.unlink(index_file)
            except FileNotFoundError:
                pass
        if tree == self.out("rev-parse", parent + "^{tree}"):
            return None
        name, email = identity
        who = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
               "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
        return self.run("commit-tree", tree, "-p", parent, "-m", message, env=who).out.strip()

    def worktree(self, path, branch, start=None):
        """Make sure `path` is a worktree on `branch` (created from `start` if new)."""
        if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git")):
            here = Git(path).out("rev-parse", "--abbrev-ref", "HEAD")
            if here != branch:
                raise GitError("%s is on %s, expected %s" % (path, here, branch))
            return path
        with self._worktree_lock():
            self.run("worktree", "prune", check=False)
            if self.sha("refs/heads/" + branch):
                self.run("worktree", "add", path, branch)
            else:
                self.run("worktree", "add", "-b", branch, path, start or "HEAD")
        return path

    def detached(self, path, ref):
        """A throwaway worktree at `ref` (replaced if it exists)."""
        with self._worktree_lock():
            self._remove_worktree(path)
            self.run("worktree", "add", "--detach", path, ref)
        return path

    def remove_worktree(self, path):
        with self._worktree_lock():
            self._remove_worktree(path)

    def _remove_worktree(self, path):
        if os.path.exists(path):
            self.run("worktree", "remove", "--force", path, check=False)
        self.run("worktree", "prune", check=False)

    @contextlib.contextmanager
    def _worktree_lock(self):
        """Serialize worktree add / remove / prune on one repository across processes: git does
        not, so a prune can delete another process's half-made .git/worktrees/<name> entry and an
        add can read one ("failed to read .../commondir"), failing that add at random."""
        common = self.out("rev-parse", "--path-format=absolute", "--git-common-dir")
        try:
            fh = open(os.path.join(common, "devloop-worktree.lock"), "a")
            fcntl.flock(fh, fcntl.LOCK_EX)
        except OSError as exc:
            raise GitError("cannot lock worktrees of %s: %s" % (common, exc))
        try:
            yield
        finally:
            fh.close()
