"""Where things are and how the project wants the loop run.

Project settings live in `<project>/board/config.json` under the key "devloop"; everything has a
default except `release` (D-080: a project must ship a release script; the controller only calls
it). `hard_block` is the project's existing top-level list.
"""

import os

from . import osx
from .gitx import Git

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../loop
AC_HOME = os.path.dirname(HERE)                                      # the agent-control clone



def claude(model, effort, edit=True):
    """A headless Claude Code command. Read-only roles may still Write (their hand-in file)."""
    tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"] if edit else ["Read", "Write", "Glob", "Grep", "Bash"]
    return ["claude", "-p", "--model", model, "--effort", effort, "--permission-mode", "acceptEdits",
            "--allowedTools"] + tools + ["--output-format", "json"]


# Each role: a chain of commands, tried in order (D-049 ②; the user's defaults in D-084:
# worker opus medium, simple tickets sonnet medium, opener opus extra-high). The consolidator
# is the higher model (D-013) with opus as its fallback.
DEFAULT_AGENTS = {
    "creator": [claude("opus", "xhigh", edit=False)],
    "worker": [claude("opus", "medium")],
    "worker_simple": [claude("sonnet", "medium")],
    "verifier": [claude("opus", "medium")],
    "reviewer": [claude("opus", "medium", edit=False)],
    "fixer": [claude("opus", "medium")],
    "consolidator": [claude("fable", "medium", edit=False), claude("opus", "medium", edit=False)],
    "consolidator_peer": [claude("opus", "medium", edit=False)],
}

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
    "base_tests": None,              # D-073 ①: ids of the ~1 minute base set; default tests.unittest.base
    "index_path": "CODE-MAP.md",     # D-038 / D-084: generated code index, refreshed at landing; null = off
    "required_fields": {},           # extra ticket fields the opener must fill: {"name": "what to write"}
    "unlimited_tokens": False,       # D-084 Q1: when true, memory consolidation is a two-model discussion
    "agents": DEFAULT_AGENTS,
}


class Config:
    def __init__(self, root=None):
        self.home = AC_HOME
        self.root = root or project_root()
        self.name = os.path.basename(self.root.rstrip(os.sep))
        raw = osx.read_json(os.path.join(self.root, "board", "config.json"), {}) or {}
        loop = raw.get("devloop") or {}
        if not isinstance(loop, dict):
            raise osx.OSFailure("board/config.json: \"devloop\" must be an object")
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
            os.path.dirname(self.root.rstrip(os.sep)), self.name + "-devloop")
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    def runs_dir(self, *parts):
        return osx.makedirs(os.path.join(self.worktree_root, "runs", *parts))

    def agent_chain(self, role):
        """The commands for a role, in the order to try them. A role may be configured as one
        command (a list of strings) or a chain (a list of such lists)."""
        value = self.values["agents"].get(role)
        if role == "worker_simple" and not value:
            value = self.values["agents"].get("worker")
        if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            value = [value]
        if not (isinstance(value, list) and value and
                all(isinstance(c, list) and c and all(isinstance(x, str) for x in c) for c in value)):
            raise osx.OSFailure("board/config.json devloop.agents.%s must be a command (a list of "
                                "strings) or a chain of commands" % role)
        return [list(c) for c in value]

    def agent(self, role):
        """The first command of a role (its model names the role's model memory)."""
        return self.agent_chain(role)[0]

    @property
    def base_test_ids(self):
        ids = self.values.get("base_tests")
        if ids is None:
            ids = ((self.raw.get("tests") or {}).get("unittest") or {}).get("base") or []
        return [str(i) for i in ids]

    def require_release(self):
        cmd = self.values.get("release")
        if not cmd or not isinstance(cmd, str):
            raise osx.OSFailure(
                "board/config.json devloop.release is missing: every project must have a release "
                "script; the controller only calls it (e.g. \"sh scripts/release.sh\")")
        return cmd

    def env(self, **extra):
        env = dict(os.environ, AC_HOME=self.home, AC_ROOT=self.root, PYTHONDONTWRITEBYTECODE="1",
                   AC_REPORTS_DIR=os.path.join(self.worktree_root, "reports"))
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
