"""Where things are and how the project wants the loop run.

Project settings live in `<project>/board/config.json` under the key "loop"; everything has a
default except `release` (D-080: a project must ship a release script; the controller only calls
it). `hard_block` is the project's existing top-level list.
"""

import os

from . import osx
from .gitx import Git

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../loop
AC_HOME = os.path.dirname(HERE)                                      # the agent-control clone

CLAUDE_EDIT = ["claude", "-p", "--model", "opus", "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", "Bash",
               "--output-format", "json"]
CLAUDE_READ = ["claude", "-p", "--model", "opus", "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Write", "Glob", "Grep", "Bash",
               "--output-format", "json"]

DEFAULTS = {
    "pool": None,                    # default <AC_HOME>/pools/<project name>
    "pool_branch": "main",
    "pool_remote": "origin",
    "main_branch": "main",
    "remote": "origin",              # code remote to push main to; null = local only
    "worktrees": None,               # default <project>/../<project name>-loop
    "max_parallel": 2,
    "max_rounds": 3,
    "issuer_timeout_seconds": 3 * 3600,
    "agent_timeout_seconds": 3600,
    "regression_timeout_seconds": 3 * 3600,
    "poll_seconds": 10,
    "release": None,                 # required: shell command run at the landed sha
    "agents": {
        "creator": CLAUDE_READ,
        "worker": CLAUDE_EDIT,
        "verifier": CLAUDE_EDIT,
        "reviewer": CLAUDE_READ,
        "fixer": CLAUDE_EDIT,
    },
}


class Config:
    def __init__(self, root=None):
        self.home = AC_HOME
        self.root = root or project_root()
        self.name = os.path.basename(self.root.rstrip(os.sep))
        raw = osx.read_json(os.path.join(self.root, "board", "config.json"), {}) or {}
        loop = raw.get("loop") or {}
        if not isinstance(loop, dict):
            raise osx.OSFailure("board/config.json: \"loop\" must be an object")
        self.raw = raw
        self.values = dict(DEFAULTS)
        self.values.update({k: v for k, v in loop.items() if k != "agents"})
        agents = dict(DEFAULTS["agents"])
        agents.update(loop.get("agents") or {})
        self.values["agents"] = agents
        hb = raw.get("hard_block") or {}
        self.hard_paths = list(hb.get("paths") or [])
        self.hard_patterns = list(hb.get("patterns") or [])

    def __getattr__(self, key):
        values = self.__dict__.get("values", {})
        if key in values:
            return values[key]
        raise AttributeError(key)

    @property
    def pool_path(self):
        path = self.values.get("pool") or os.path.join(self.home, "pools", self.name)
        return path if os.path.isabs(path) else os.path.join(self.home, path)

    @property
    def worktree_root(self):
        path = self.values.get("worktrees") or os.path.join(
            os.path.dirname(self.root.rstrip(os.sep)), self.name + "-loop")
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    def runs_dir(self, *parts):
        return osx.makedirs(os.path.join(self.worktree_root, "runs", *parts))

    def agent(self, role):
        argv = self.values["agents"].get(role)
        if not argv or not isinstance(argv, list):
            raise osx.OSFailure("board/config.json loop.agents.%s must be a command list" % role)
        return list(argv)

    def require_release(self):
        cmd = self.values.get("release")
        if not cmd or not isinstance(cmd, str):
            raise osx.OSFailure(
                "board/config.json loop.release is missing: every project must have a release "
                "script; the controller only calls it (e.g. \"sh scripts/release.sh\")")
        return cmd

    def env(self, **extra):
        env = dict(os.environ, AC_HOME=self.home, AC_ROOT=self.root)
        env.update({k: str(v) for k, v in extra.items() if v is not None})
        return env


def project_root():
    """AC_ROOT, else the main repository of the current directory."""
    env = os.environ.get("AC_ROOT")
    if env:
        return os.path.abspath(env)
    try:
        return Git(os.getcwd()).common_root()
    except osx.OSFailure:
        raise osx.OSFailure("not inside a git repository and AC_ROOT is not set")


def pool_for(cfg):
    from .pool import Pool
    return Pool(cfg.pool_path, branch=cfg.pool_branch, remote=cfg.pool_remote)
