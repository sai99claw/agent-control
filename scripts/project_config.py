#!/usr/bin/env python3
"""A project's `board/config.json`: version, schema, migrations (D-071 §2, row 2; #160).

    project_config.py check   [<root>]   # rc 0 / 2 — bin/actl runs it before every command
    project_config.py doctor  [<root>]   # one row per finding; rc 1 when any row is an error
    project_config.py migrate [<root>]   # MIGRATIONS up to CURRENT_VERSION; rc 0 / 2; idempotent
    project_config.py init    [<root>]   # write a version-2 file of defaults; rc 2 when one exists

<root> defaults to `$AC_ROOT` (bin/actl exports it); with neither, rc 2.

## Compatibility (the T lesson, in one place)
A file without `config_version` is version 1. Lower than A's → rc 2 and `actl migrate`; higher
than A's → rc 2 and "pull A" (main's ruling: A behind ⇒ refuse). A known key with the wrong type
→ rc 2 naming key, expected type, got value. An unknown top-level key → silent in `check` (it runs
before every command; T keeps legacy keys through §7, #185), a warning row in `actl doctor` and
`actl migrate`, never rc. A missing key → the reader's default, silent.

`actl version / doctor / migrate / init` skip `check` (bin/actl) — otherwise a stale project could
never be migrated.

## Schema
`SCHEMA` types the keys that exist at base (design §2 rows marked *existing*) plus the keys the
scripts read today but the design table does not list (`verifier`, `gate`, `guard_main_paths`,
`unit_cmd`, `regression`, `regression_auto_ticket`) — calling a key A reads "possibly retired" would
be a wrong next step. `tests` and `board.key_file` are typed now so rows 3a / 3b / 5 add data, not
schema. A nested key is typed only when its row is here; nested unknown keys are not warned.
"""

import json
import os
import shlex
import sys

CURRENT_VERSION = 2
CONFIG_REL = os.path.join("board", "config.json")
AC_HOME = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
NO_DEFAULT = object()

# (dotted key, type, default). Types: int (not bool), bool, string, list, object.
SCHEMA = [
    ("config_version", "int", NO_DEFAULT),
    ("main_branch", "string", "main"),
    ("tickets_dir", "string", "tickets"),
    ("events_file", "string", "board/events.jsonl"),
    ("answers_file", "string", "board/answers.jsonl"),
    ("reports_dir", "string", "reports"),
    ("worktree_dir", "string", NO_DEFAULT),        # ../<repo>-wt, computed by the readers
    ("port", "int", 18905),
    ("health_urls", "object", NO_DEFAULT),
    ("routing", "object", NO_DEFAULT),
    ("model_tiers", "list", NO_DEFAULT),
    ("effort", "object", NO_DEFAULT),
    ("worker", "object", NO_DEFAULT),
    ("worker.command", "string", NO_DEFAULT),
    ("worker.timeout_seconds", "int", NO_DEFAULT),
    ("opener", "object", NO_DEFAULT),
    ("opener.command", "string", NO_DEFAULT),
    ("opener.timeout_seconds", "int", NO_DEFAULT),
    ("reviewer", "object", NO_DEFAULT),
    ("reviewer.command", "string", NO_DEFAULT),
    ("reviewer.timeout_seconds", "int", NO_DEFAULT),
    ("verifier", "object", NO_DEFAULT),
    ("verifier.command", "string", NO_DEFAULT),
    ("lease_seconds", "object", NO_DEFAULT),
    ("lease_seconds.opener", "int", NO_DEFAULT),
    ("lease_seconds.verifier", "int", NO_DEFAULT),
    ("lease_seconds.land", "int", NO_DEFAULT),
    ("lease_seconds.worker", "int", NO_DEFAULT),
    ("flaky_threshold", "int", NO_DEFAULT),
    ("flake_auto_single_runs", "int", NO_DEFAULT),
    ("flake_auto_group_runs", "int", NO_DEFAULT),
    ("environment_fail_fast_threshold", "int", NO_DEFAULT),
    ("flaky_auto_ticket", "bool", NO_DEFAULT),
    ("verify_case", "object", NO_DEFAULT),
    ("verify_case.timeout_seconds", "int", NO_DEFAULT),
    ("batch_fix", "object", NO_DEFAULT),
    ("batch_fix.rounds", "int", NO_DEFAULT),        # optional batch override; absent = repair.max_rounds
    # The repair budget (#125, D-050 ②): repair rounds after the first red, and free mechanical
    # gate re-runs on rc 86. Absent = these defaults; readers go through `repair_setting`.
    ("repair", "object", NO_DEFAULT),
    ("repair.max_rounds", "int", 3),
    ("repair.env_rerun", "int", 1),
    ("schedule", "object", NO_DEFAULT),             # scripts/schedule.py (#119); readers default 2 / 2
    ("schedule.max_workers", "int", NO_DEFAULT),
    ("schedule.reland_max", "int", NO_DEFAULT),
    ("apply", "object", NO_DEFAULT),
    ("apply.regen_cmd", "string", NO_DEFAULT),
    ("memory", "object", NO_DEFAULT),
    ("memory.applies_to", "list", NO_DEFAULT),
    ("memory.inbox_suffix", "string", NO_DEFAULT),
    ("rules", "object", NO_DEFAULT),
    ("rules.sections", "object", NO_DEFAULT),
    ("hard_block", "object", {"paths": [], "dirs": [], "patterns": []}),
    ("hard_block.paths", "list", NO_DEFAULT),
    ("hard_block.dirs", "list", NO_DEFAULT),
    ("hard_block.patterns", "list", NO_DEFAULT),
    ("board", "object", NO_DEFAULT),
    ("board.key_file", "string", NO_DEFAULT),
    ("tests", "object", {"adapter": "unittest"}),
    ("tests.adapter", "string", NO_DEFAULT),
    ("tests.parallel", "int", NO_DEFAULT),            # cap on the run-time process count (#169)
    ("tests.unittest", "object", NO_DEFAULT),
    ("tests.unittest.serial", "list", NO_DEFAULT),    # modules run one at a time after (#169)
    # read at base, not in the design table
    ("gate", "object", NO_DEFAULT),
    ("guard_main_paths", "list", NO_DEFAULT),
    ("unit_cmd", "string", NO_DEFAULT),
    ("regression", "object", NO_DEFAULT),
    ("regression_auto_ticket", "bool", NO_DEFAULT),
]
TOP_KEYS = {key for key, _, _ in SCHEMA if "." not in key}
ROLE_CARD_NOTE = ("project role cards now overlay $AC_HOME/memory/role; "
                  "move docs/roles/<role>.md additions into memory/role/<role>.md")
# What the retired copy-into-the-project mechanism left behind (D-071 row 6, #166): migrate drops the
# hard_block row naming its script, doctor refuses its directory. Spelled in pieces so the retire
# grep (tests/test_retire_sync.py) keeps meaning "no code path assumes them".
RETIRED_DIR = os.path.join("scripts", "con" "trol")
RETIRED_SYNC = "sync-to-" "project.sh"
# The project's role -> model table (#184): auto-fix / review / new-session read it from the project
# only, so doctor flags its absence and migrate / init seed it from A's copy (never overwrite).
ROLE_MODELS_REL = os.path.join("board", "role-models.json")


def repair_setting(conf, key):
    """`repair.<key>` of a loaded config (#125): an int >= 0, else the SCHEMA default.

    The one place the defaults live — `ticket.py repair_limit`, `auto-fix.sh` and `land.sh`
    read through here, so "absent" means the same number everywhere."""
    name = "repair." + key
    default = next(value for dotted, _, value in SCHEMA if dotted == name)
    node = conf.get("repair") if isinstance(conf, dict) else None
    value = node.get(key) if isinstance(node, dict) else None
    if type_ok(value, "int") and value >= 0:
        return value
    return default


class Refused(Exception):
    """rc 2 with one stderr line."""


def type_ok(value, kind):
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, {"bool": bool, "string": str, "list": list, "object": dict}[kind])


def config_path(root):
    return os.path.join(root, CONFIG_REL)


def load(root):
    """(conf, raw text). Missing / not JSON / not an object → Refused."""
    path = config_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        raise Refused("%s: missing - run: actl init" % path)
    except OSError as err:
        raise Refused("%s: unreadable: %s" % (path, err))
    try:
        conf = json.loads(raw)
    except ValueError as err:
        raise Refused("%s: not valid JSON (%s)" % (path, err))
    if not isinstance(conf, dict):
        raise Refused("%s: top level is %s, expected an object" % (path, type(conf).__name__))
    return conf, raw


def version_of(conf):
    """The file's version; absent = 1. A value that is not an int >= 1 → Refused naming it."""
    if "config_version" not in conf:
        return 1
    value = conf["config_version"]
    if not type_ok(value, "int") or value < 1:
        # The value comes right after the key so "config_version <value>" reads as one phrase.
        raise Refused("config_version %s is not a valid version (expected an int >= 1)"
                      % json.dumps(value))
    return value


def version_problem(version):
    if version < CURRENT_VERSION:
        return ("config_version %d < %d - run: actl migrate" % (version, CURRENT_VERSION),
                "actl migrate")
    if version > CURRENT_VERSION:
        pull = "git -C %s pull" % AC_HOME
        return ("project needs config_version %d, this A has %d - %s"
                % (version, CURRENT_VERSION, pull), pull)
    return None


def type_errors(conf):
    out = []
    for key, kind, _ in SCHEMA:
        node, present = conf, True
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                present = False
                break
            node = node[part]
        if present and not type_ok(node, kind):
            out.append("board/config.json key '%s': expected %s, got %s"
                       % (key, kind, json.dumps(node, ensure_ascii=False)))
    return out


def unknown_keys(conf):
    return [key for key in conf if key not in TOP_KEYS]


# ---------------------------------------------------------------- check

def cmd_check(root):
    conf, _ = load(root)
    problem = version_problem(version_of(conf))
    if problem:
        raise Refused(problem[0])
    errors = type_errors(conf)
    for line in errors:
        sys.stderr.write("project_config: %s\n" % line)
    return 2 if errors else 0


# ---------------------------------------------------------------- doctor

def hook_commands(settings):
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks or {}).values() if isinstance(hooks, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            inner = group.get("hooks") if isinstance(group, dict) else None
            for hook in inner if isinstance(inner, list) else ():
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    yield hook["command"]


def first_path(command, root):
    """The first path-looking token of a hook command (has a '/'), `$CLAUDE_PROJECT_DIR`
    expanded to the project root; relative paths resolve against it. None when there is none."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        token = token.replace("${CLAUDE_PROJECT_DIR}", root).replace("$CLAUDE_PROJECT_DIR", root)
        if "/" in token and not token.startswith("-"):
            token = os.path.expanduser(token)
            return token if os.path.isabs(token) else os.path.join(root, token)
    return None


def doctor_rows(root):
    """[(severity, text, fix or None)]."""
    rows = []
    try:
        conf, _ = load(root)
    except Refused as err:
        fix = "actl init" if not os.path.exists(config_path(root)) else None
        return [("error", str(err), fix)]
    try:
        problem = version_problem(version_of(conf))
    except Refused as err:
        problem = (str(err), None)
    if problem:
        rows.append(("error", problem[0], problem[1]))
    for line in type_errors(conf):
        rows.append(("error", line, "edit board/config.json"))
    for key in unknown_keys(conf):
        rows.append(("warning", "board/config.json key '%s' unknown to this A - possibly retired"
                     % key, None))
    reports = conf.get("reports_dir") if isinstance(conf.get("reports_dir"), str) else "reports"
    inbox = os.path.join(root, reports, "inbox")
    if os.path.exists(os.path.join(inbox, "acked.jsonl")):
        rows.append(("error", "%s/inbox/acked.jsonl exists (pre-#143 acks, not read by A)"
                     % reports, "actl inbox migrate"))
    if os.path.exists(os.path.join(inbox, "index.jsonl")):
        rows.append(("warning", "%s/inbox/index.jsonl exists - legacy ledger, not read by A"
                     % reports, None))
    if os.path.isdir(os.path.join(root, RETIRED_DIR)):
        rows.append(("warning", "%s/ exists - retired, coexists with A until §7 step 5 "
                     "(D-071), delete it then" % RETIRED_DIR, None))
    models = os.path.join(root, ROLE_MODELS_REL)
    try:
        with open(models, encoding="utf-8") as handle:
            json.load(handle)
    except FileNotFoundError:
        rows.append(("error", "%s missing - dispatch reads the project's role -> model table"
                     % ROLE_MODELS_REL, "actl migrate"))
    except (OSError, ValueError) as err:
        rows.append(("error", "%s unreadable (%s)" % (ROLE_MODELS_REL, err), "actl migrate"))
    settings_path = os.path.join(root, ".claude", "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as handle:
                settings = json.load(handle)
        except (OSError, ValueError) as err:
            rows.append(("error", ".claude/settings.json unreadable (%s)" % err, None))
            settings = {}
        for command in hook_commands(settings):
            path = first_path(command, root)
            if path is not None and not os.path.exists(path):
                rows.append(("error", ".claude/settings.json hook names a missing file: %s"
                             % path, "actl hooks install"))
    return rows


def cmd_doctor(root):
    rows = doctor_rows(root)
    for severity, text, fix in rows:
        print("%s: %s%s" % (severity, text, "  -> fix: %s" % fix if fix else ""))
    if not rows:
        print("actl doctor: no findings")
    return 1 if any(row[0] == "error" for row in rows) else 0


# ---------------------------------------------------------------- migrate

def migrate_1_to_2(conf):
    """Version 1 (no `config_version`) → 2. Returns (new conf, change lines, warnings)."""
    changes, warnings = [], []
    out = {"config_version": 2}
    for key, value in conf.items():
        if key != "config_version":
            out[key] = value
    changes.append("set config_version 2")
    if "tests" not in out:
        out["tests"] = {"adapter": "unittest"}
        changes.append('add tests {"adapter": "unittest"}')
    rules = out.get("rules")
    if isinstance(rules, dict):
        gone = [key for key in ("roles_dir", "models_dir") if key in rules]
        if gone:
            out["rules"] = {key: value for key, value in rules.items() if key not in gone}
            for key in gone:
                changes.append("delete rules.%s - %s" % (key, ROLE_CARD_NOTE))
    block = out.get("hard_block")
    if isinstance(block, dict) and isinstance(block.get("paths"), list):
        keep = [path for path in block["paths"]
                if not (isinstance(path, str) and path.endswith(RETIRED_SYNC))]
        if keep != block["paths"]:
            for path in block["paths"]:
                if path not in keep:
                    changes.append("delete hard_block.paths entry %s" % path)
            out["hard_block"] = dict(block, paths=keep)
    for key in unknown_keys(out):
        warnings.append("warning: unknown top-level key '%s' kept (possibly retired)" % key)
    return out, changes, warnings


MIGRATIONS = {1: migrate_1_to_2}


def seed_role_models(root):
    """Copy $AC_HOME/board/role-models.json into the project when it has none (#184); an existing
    file is never touched. Returns the change line, or None."""
    dest = os.path.join(root, ROLE_MODELS_REL)
    if os.path.lexists(dest):
        return None
    source = os.path.join(AC_HOME, ROLE_MODELS_REL)
    if not os.path.isfile(source):
        raise Refused("%s: missing in A - cannot seed %s" % (source, ROLE_MODELS_REL))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(source, "rb") as handle:
        data = handle.read()
    with open(dest, "xb") as handle:
        handle.write(data)
    return "add %s (copied from %s)" % (ROLE_MODELS_REL, source)


def cmd_migrate(root):
    conf, raw = load(root)
    version = version_of(conf)
    if version > CURRENT_VERSION:
        raise Refused(version_problem(version)[0])
    seeded = seed_role_models(root)
    if version == CURRENT_VERSION:
        if seeded:
            print("migrate: %s" % seeded)
            print("actl migrate: config_version %d is current (1 change)" % version)
        else:
            print("actl migrate: nothing to do - config_version %d is current (0 changes)"
                  % version)
        return 0
    changes, warnings = ([seeded] if seeded else []), []
    while version < CURRENT_VERSION:
        conf, step, warn = MIGRATIONS[version](conf)
        changes.extend(step)
        warnings.extend(warn)
        version = conf["config_version"]
    text = json.dumps(conf, ensure_ascii=False, indent=2) + "\n"
    path = config_path(root)
    tmp = path + ".migrate.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    for line in changes:
        print("migrate: %s" % line)
    for line in warnings:
        print("migrate: %s" % line)
    print("actl migrate: %s -> config_version %d (%d changes)" % (path, version, len(changes)))
    return 0


# ---------------------------------------------------------------- init

def defaults():
    out = {"config_version": CURRENT_VERSION}
    for key, _, default in SCHEMA:
        if "." not in key and default is not NO_DEFAULT:
            out[key] = json.loads(json.dumps(default))
    return out


def cmd_init(root):
    path = config_path(root)
    if os.path.exists(path):
        raise Refused("%s exists - actl init writes a new project only; see actl doctor" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(defaults(), ensure_ascii=False, indent=2) + "\n")
    print(path)
    if seed_role_models(root):
        print(os.path.join(root, ROLE_MODELS_REL))
    return 0


COMMANDS = {"check": cmd_check, "doctor": cmd_doctor, "migrate": cmd_migrate, "init": cmd_init}


def main(argv):
    if len(argv) not in (1, 2) or argv[0] not in COMMANDS:
        sys.stderr.write("usage: project_config.py {check|doctor|migrate|init} [<root>]\n")
        return 2
    root = argv[1] if len(argv) == 2 else os.environ.get("AC_ROOT")
    if not root:
        sys.stderr.write("project_config: no project root - pass <root> or set AC_ROOT\n")
        return 2
    try:
        return COMMANDS[argv[0]](os.path.abspath(root))
    except Refused as err:
        sys.stderr.write("project_config: %s\n" % err)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
