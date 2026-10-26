#!/usr/bin/env python3
"""`actl hooks <install|check> [--project <dir>]` (D-071 C8, section 5; #164).

Writes the three Claude Code hook entries of <project>/.claude/settings.json so they point into
AC_HOME. A managed entry is an item of hooks.<event>[].hooks[] whose command names one of
MANAGED basenames at any path; everything else in the file is left as it was.

  install  rewrite managed entries to the target set, add the missing ones, delete a managed
           entry whose script file is gone; print a unified diff, or 'hooks: no change'
  check    the same computation, never writes; rc 1 when install would change something

rc 2: AC_HOME / project missing, file not valid JSON or not the expected shape.
"""

import copy
import difflib
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_config import first_path  # noqa: E402

MANAGED = ("session-hook.sh", "agent-cost-hook.py", "guard-main.sh", "main_guard.py")
SOURCES = "startup|resume|clear|compact|fork"
# event -> (interpreter, script, timeout, matcher or None)
TARGETS = (
    ("SessionStart", "sh", "session-hook.sh", 60, SOURCES),
    ("SubagentStop", "python3", "agent-cost-hook.py", 10, None),
    ("SessionEnd", "python3", "agent-cost-hook.py", 10, None),
)


class Refused(Exception):
    pass


def is_managed(item):
    command = item.get("command") if isinstance(item, dict) else None
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return any(os.path.basename(token) in MANAGED for token in tokens)


def dangling(item, project):
    """A managed entry whose first path token is a file that does not exist."""
    if not is_managed(item):
        return False
    path = first_path(item["command"], project)
    return path is not None and not os.path.exists(path)


def home_spelling(project, ac_home):
    if os.path.realpath(project) == os.path.realpath(ac_home):
        return "$CLAUDE_PROJECT_DIR"
    return os.path.realpath(ac_home)


def target_item(base, interpreter, script, timeout):
    return {"type": "command",
            "command": '%s "%s/scripts/%s"' % (interpreter, base, script),
            "timeout": timeout}


def compute(settings, project, ac_home):
    """New settings object (deep copy; the input is not touched)."""
    new = copy.deepcopy(settings)
    hooks = new.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise Refused("'hooks' is not an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise Refused("hooks.%s is not a list" % event)
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                raise Refused("hooks.%s has an entry without a hooks list" % event)
    base = home_spelling(project, ac_home)

    # 1. a managed entry whose script file is gone is deleted (every event); a group this
    #    empties goes with it, a group that was already empty is not ours to touch
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            before = group.get("hooks", [])
            after = [item for item in before if not dangling(item, project)]
            group["hooks"] = after if "hooks" in group else before
            if before and not after:
                continue
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]

    # 2. target events: first managed item rewritten, further ones dropped, missing one added
    for event, interpreter, script, timeout, matcher in TARGETS:
        wanted = target_item(base, interpreter, script, timeout)
        groups = hooks.setdefault(event, [])
        found = [(gi, ii) for gi, g in enumerate(groups)
                 for ii, item in enumerate(g.get("hooks", [])) if is_managed(item)]
        fresh = {"hooks": [wanted]}
        if matcher:
            fresh = {"matcher": matcher, "hooks": [wanted]}
        if not found:
            groups.append(fresh)
            continue
        for gi, ii in reversed(found[1:]):
            del groups[gi]["hooks"][ii]
        gi, ii = found[0]
        group = groups[gi]
        if all(is_managed(item) for item in group["hooks"]):
            item = group["hooks"][ii]
            item.update(wanted)
            if matcher and group.get("matcher") != matcher:
                rebuilt = {"matcher": matcher}
                rebuilt.update({k: v for k, v in group.items() if k != "matcher"})
                groups[gi] = group = rebuilt
            group["hooks"] = [item]
        else:
            del group["hooks"][ii]
            groups.insert(gi + 1, fresh)
        groups[:] = [g for g in groups if g.get("hooks") or "hooks" not in g]
    return new


def dump(settings):
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def main(argv):
    if not argv or argv[0] not in ("install", "check") or len(argv) not in (1, 3) \
            or (len(argv) == 3 and argv[1] != "--project"):
        print("usage: hooks.py <install|check> [--project <dir>]", file=sys.stderr)
        return 2
    mode = argv[0]
    ac_home = os.environ.get("AC_HOME")
    if not ac_home:
        print("hooks: AC_HOME not set", file=sys.stderr)
        return 2
    project = argv[2] if len(argv) == 3 else os.environ.get("AC_ROOT")
    if not project:
        print("hooks: no project: give --project <dir> or set AC_ROOT", file=sys.stderr)
        return 2
    project = os.path.realpath(project)
    path = os.path.join(project, ".claude", "settings.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            old_text = handle.read()
        try:
            settings = json.loads(old_text)
        except ValueError as err:
            print("hooks: %s is not valid JSON (%s)" % (path, err), file=sys.stderr)
            return 2
        if not isinstance(settings, dict):
            print("hooks: %s: top level is not an object" % path, file=sys.stderr)
            return 2
    else:
        old_text, settings = "", {}
    try:
        new = compute(settings, project, ac_home)
    except Refused as err:
        print("hooks: %s: %s" % (path, err), file=sys.stderr)
        return 2
    if new == settings and old_text:
        print("hooks: no change")
        return 0
    new_text = dump(new)
    sys.stdout.writelines(difflib.unified_diff(
        old_text.splitlines(True), new_text.splitlines(True),
        ".claude/settings.json (old)", ".claude/settings.json (new)"))
    if mode == "check":
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
