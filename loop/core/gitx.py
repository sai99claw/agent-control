"""Git, through one wrapper: every failure becomes GitError with git's own last line."""

import os

from .osx import OSFailure, run


class GitError(OSFailure):
    pass


class Git:
    def __init__(self, cwd, timeout=600):
        self.cwd = cwd
        self.timeout = timeout

    def run(self, *args, check=True, timeout=None):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", LC_ALL="C")
        res = run(["git"] + list(args), cwd=self.cwd, env=env,
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

    def move_ref(self, branch, new, old):
        """Compare-and-swap a branch: succeeds only if it still points at `old`."""
        return self.run("update-ref", "refs/heads/" + branch, new, old, check=False).ok

    def worktree(self, path, branch, start=None):
        """Make sure `path` is a worktree on `branch` (created from `start` if new)."""
        if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git")):
            here = Git(path).out("rev-parse", "--abbrev-ref", "HEAD")
            if here != branch:
                raise GitError("%s is on %s, expected %s" % (path, here, branch))
            return path
        self.run("worktree", "prune", check=False)
        if self.sha("refs/heads/" + branch):
            self.run("worktree", "add", path, branch)
        else:
            self.run("worktree", "add", "-b", branch, path, start or "HEAD")
        return path

    def detached(self, path, ref):
        """A throwaway worktree at `ref` (replaced if it exists)."""
        self.remove_worktree(path)
        self.run("worktree", "add", "--detach", path, ref)
        return path

    def remove_worktree(self, path):
        if os.path.exists(path):
            self.run("worktree", "remove", "--force", path, check=False)
        self.run("worktree", "prune", check=False)
