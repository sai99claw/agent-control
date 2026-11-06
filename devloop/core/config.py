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
    "max_group_size": 10,            # a together ticket opens a new integration branch once the open one has this many members
    "issuer_timeout_seconds": 3 * 3600,
    "agent_timeout_seconds": 3600,
    "quota_retry_seconds": 1800,     # wait after an agent hit its usage limit when its message names no reset
    "regression_timeout_seconds": 3 * 3600,
    "poll_seconds": 10,
    "release": None,                 # required: shell command run at the landed sha
    "release_timeout_seconds": 3600, # time limit of the release command at landing
    "worktree_setup": None,          # D-091 item 10: shell command run in every new worktree devloop
                                     # makes (cwd = the worktree, AC_ROOT = the project); null = nothing
    "base_tests": None,              # D-073 ①: ids of the ~1 minute base set; default tests.unittest.base
    "index_path": "CODE-MAP.md",     # D-038 / D-084: generated code index, refreshed at landing; null = off
    "required_fields": {},           # extra ticket fields the opener must fill: {"name": "what to write"}
    "unlimited_tokens": False,       # D-084 Q1: when true, memory consolidation is a two-model discussion
    "machine_slots": None,           # at most N agent / test runs at once on the machine; null = no limit
    "regression_exclusive": False,   # true: a regression runs alone on the machine (needs machine_slots)
    "memory": None,                  # D-088: folder of the role/model/project memory files; null = <pool>/memory,
                                     # relative = against the project root
    "no_reverify_paths": ["memory/", "docs/DECISIONS.md", "docs/HANDOFF.md"],
                                     # D-090: when main moved after verification only in these paths,
                                     # the controller merges main in and lands without a second regression
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
        slots = self.values.get("machine_slots")
        if slots is not None and (isinstance(slots, bool) or not isinstance(slots, int) or slots < 1):
            raise osx.OSFailure("board/config.json devloop.machine_slots must be null or a whole number "
                                ">= 1, not %r" % (slots,))
        size = self.values.get("max_group_size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise osx.OSFailure("board/config.json devloop.max_group_size must be a whole number "
                                ">= 1, not %r" % (size,))
        mem = self.values.get("memory")
        if mem is not None and not (isinstance(mem, str) and mem):
            raise osx.OSFailure("board/config.json devloop.memory must be null or a folder path (a "
                                "non-empty string), not %r" % (mem,))
        setup = self.values.get("worktree_setup")
        if setup is not None and not (isinstance(setup, str) and setup):
            raise osx.OSFailure("board/config.json devloop.worktree_setup must be null or a shell "
                                "command (a non-empty string), not %r" % (setup,))
        nrv = self.values.get("no_reverify_paths")
        if not (isinstance(nrv, list) and all(isinstance(p, str) and p for p in nrv)):
            raise osx.OSFailure("board/config.json devloop.no_reverify_paths must be a list of "
                                "non-empty path strings ([] turns it off), not %r" % (nrv,))
        self.values["no_reverify_paths"] = list(nrv)
        exclusive = self.values.get("regression_exclusive")
        if not isinstance(exclusive, bool):
            raise osx.OSFailure("board/config.json devloop.regression_exclusive must be true or false, "
                                "not %r" % (exclusive,))
        if exclusive and slots is None:
            raise osx.OSFailure("board/config.json devloop.regression_exclusive is true but "
                                "devloop.machine_slots is not set: exclusivity is counted in slots, "
                                "so it needs a slot count")
        retry = self.values.get("quota_retry_seconds")
        if isinstance(retry, bool) or not isinstance(retry, int) or retry < 1:
            raise osx.OSFailure("board/config.json devloop.quota_retry_seconds must be a whole number "
                                ">= 1, not %r" % (retry,))
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
    def memory_dir(self):
        """The folder holding memory/role, memory/model and project.md, as an absolute path."""
        path = self.values.get("memory")
        if path is None:
            return os.path.join(self.pool_path, "memory")
        return os.path.normpath(path if os.path.isabs(path) else os.path.join(self.root, path))

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
        env = {k: v for k, v in os.environ.items() if k not in PARENT_SESSION}
        env.update(AC_HOME=self.home, AC_ROOT=self.root, PYTHONDONTWRITEBYTECODE="1",
                   AC_REPORTS_DIR=os.path.join(self.worktree_root, "reports"))
        env.update({k: str(v) for k, v in extra.items() if v is not None})
        return env


# When devloop is started from inside a Claude Code session, these name that session. An agent
# that inherits them reports under the parent's session id and may post into its transcript;
# each agent must be its own session.
PARENT_SESSION = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_REMOTE_SDK_URL",
                  "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SYNC_SESSION_REFS",
                  "CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2", "CLAUDE_CODE_MESSAGING_SOCKET",
                  "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID")


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
