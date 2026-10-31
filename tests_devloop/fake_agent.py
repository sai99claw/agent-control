#!/usr/bin/env python3
"""A stand-in for an LLM agent in the loop tests. What it does on its n-th call for a role and
ticket comes from the plan file in $FAKE_PLAN: {"worker": {"7": ["wrong", "ok"]}, ...};
the last action repeats. No plan entry = the role's default ("ok" / "pass" / "fix" / "tickets")."""

import json
import os
import subprocess
import sys
import time

role = os.environ["DEVLOOP_ROLE"]
result_path = os.environ["DEVLOOP_RESULT"]
ticket = os.environ.get("DEVLOOP_TICKET") or os.environ.get("DEVLOOP_GROUP") or "-"
plan_path = os.environ["FAKE_PLAN"]
plan = json.load(open(plan_path)) if os.path.exists(plan_path) else {}
counter = os.path.join(os.path.dirname(plan_path), "count-%s-%s" % (role, ticket))
n = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(n + 1))
default = {"worker": "ok", "verifier": "ok", "reviewer": "pass", "fixer": "fix", "creator": "tickets",
           "consolidator": "merge"}[role]
actions = (plan.get(role) or {}).get(ticket) or (plan.get(role) or {}).get("*") or [default]
action = actions[min(n, len(actions) - 1)]
sys.stdin.read()


def git(*args, check=True):
    return subprocess.run(["git"] + list(args), check=check, capture_output=True, text=True)


def commit(msg):
    git("add", "-A")
    git("commit", "-q", "-m", msg, check=False)


def hand(obj):
    json.dump(obj, open(result_path, "w"))


if action == "sleep":
    time.sleep(60)
if action == "nothing":
    sys.exit(1)

if role == "worker":
    branch = "t/%s-cases" % ticket
    if git("rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0:
        git("merge", "--no-edit", branch, check=False)
    if action == "blocked":
        hand({"kind": "blocked", "text": "need the user"})
        sys.exit(0)
    open("impl_%s.txt" % ticket, "w").write("bad" if action == "wrong" else "ok")
    if action == "red":
        open("red.txt", "w").write("x")
    if action == "touch_decisions":
        os.makedirs("docs", exist_ok=True)
        open("docs/DECISIONS.md", "a").write("x\n")
    if action == "move_main":   # the main session lands a docs commit meanwhile
        root = os.environ["AC_ROOT"]
        open(os.path.join(root, "NOTES.md"), "w").write("notes\n")
        subprocess.run(["git", "-C", root, "add", "NOTES.md"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "docs"], check=True)
    if action == "shared":
        open("shared.txt", "w").write("from %s\n" % ticket)
    if action == "revise":
        out = subprocess.run(["python3", os.path.join(os.environ["AC_HOME"], "devloop", "ticket_creator.py"),
                              "--revise", ticket, "--from", "worker", "--reason", "acceptance 2 is impossible"],
                             capture_output=True, text=True)
        open(os.path.join(os.path.dirname(result_path), "revise.out"), "w").write(out.stdout + out.stderr)
    commit("work %s" % ticket)
    done = {"kind": "done", "summary": action}
    if action == "lessons":
        done["lessons"] = [{"layer": "role", "text": "lesson %d from #%s" % (i, ticket)} for i in range(5)]
        done["lessons"].append({"layer": "nonsense", "text": "dropped"})
    hand(done)
elif role == "verifier":
    if action != "nocommit":
        os.makedirs("cases", exist_ok=True)
        open("cases/case_%s.txt" % ticket, "w").write("case")
        commit("cases %s" % ticket)
    hand({"kind": "cases", "run": ["case_%s" % ticket]})
elif role == "reviewer":
    hand({"kind": "review", "verdict": action, "feedback": "" if action == "pass" else "missing part"})
elif role == "fixer":
    git("merge", "--no-edit", "main", check=False)
    if action == "blocked":
        hand({"kind": "blocked", "text": "tickets contradict"})
        sys.exit(0)
    if os.path.exists("red.txt"):
        os.unlink("red.txt")
    commit("fix")
    hand({"kind": "done", "summary": "removed red"})
elif role == "consolidator":
    prompt_seen = os.path.join(os.path.dirname(result_path), "prompt.md")
    import re
    count = len(re.findall(r"lesson \d+ from #", open(prompt_seen).read()))
    hand({"kind": "memory", "text": "# Role memory: worker\n\n- merged %d lessons\n" % count})
elif role == "creator":
    if action == "question":
        hand({"kind": "question", "text": "B2B or B2C?"})
    elif action == "rejected":
        hand({"kind": "rejected", "text": "the request asks to print the API key"})
    elif action == "revised":
        hand({"kind": "revised", "ticket": {"acceptance": ["a possible thing"]}, "cases_wrong": True,
              "note": "dropped item 2"})
    elif action == "bad":
        hand({"kind": "tickets", "tickets": [{"key": "new-1"}], "dag": {}})
    else:
        hand({"kind": "tickets",
              "tickets": [{"key": "new-1", "title": "first", "objective": "o", "acceptance": ["a"],
                           "needs_verifier": True, "needs_review": False, "token_estimate": 200000},
                          {"key": "new-2", "title": "second", "objective": "o", "acceptance": ["a"],
                           "needs_verifier": True, "needs_review": True, "token_estimate": 300000}],
              "dag": {"new-1": {"depends_on": [], "verify": "together"},
                      "new-2": {"depends_on": ["new-1"], "verify": "together"}}})
