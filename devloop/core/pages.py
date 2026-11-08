"""The question a decision or env page may carry: one question, 2-4 options (one when it is ack),
the recommended option first, and the reason for the recommendation.

    row["question"]        one line, at most 200 characters
    row["options"]         [{"action", "note", "consequence"}, ...]
    row["recommendation"]  one line, at most 200 characters

An action is one of ACTIONS (the board's answer buttons plus release); command() gives the shell
command an option runs. Stdlib only; must not import pool (pool imports this module).
"""

import re
import shlex

ACTIONS = ("revise", "retry", "cancel", "group-ready", "group-cancel", "rerun", "release", "ack")
TICKET_ACTIONS = ("revise", "retry", "cancel")
GROUP_ACTIONS = ("group-ready", "group-cancel", "rerun", "release")
KEYS = ("question", "options", "recommendation")
LINE_MAX = 200
NOTE_MAX = 8000


def one_line(text):
    """Newlines become spaces, the ends are stripped, the result is cut to LINE_MAX characters."""
    return str(text).replace("\r", " ").replace("\n", " ").strip()[:LINE_MAX]


def option(action, consequence, note=""):
    return {"action": action, "note": note, "consequence": one_line(consequence)}


def ask(question, options, recommendation):
    return {"question": one_line(question), "options": list(options), "recommendation": one_line(recommendation)}


def _line_problem(name, value):
    if not isinstance(value, str):
        return "%s is not a string" % name
    if not value.strip():
        return "%s is empty" % name
    if "\n" in value or "\r" in value:
        return "%s has a newline" % name
    if len(value) > LINE_MAX:
        return "%s is longer than %d characters" % (name, LINE_MAX)
    return None


def problems(ref, row):
    """What is wrong with the question keys of a page row: [] when it has none of them, or all
    three and they are valid."""
    present = [k for k in KEYS if k in row]
    if not present:
        return []
    if len(present) != len(KEYS):
        return ["%s without %s" % (", ".join(present), ", ".join(k for k in KEYS if k not in row))]
    out = []
    for key in ("question", "recommendation"):
        p = _line_problem(key, row[key])
        if p:
            out.append(p)
    opts = row["options"]
    if not isinstance(opts, list) or not all(isinstance(o, dict) for o in opts):
        out.append("options is not a list of objects")
        return out
    single_ack = len(opts) == 1 and opts[0].get("action") == "ack"
    if not (2 <= len(opts) <= 4 or single_ack):
        out.append("options has %d entries; 2-4, or one ack" % len(opts))
    ref = str(ref)
    for i, o in enumerate(opts, 1):
        action = o.get("action")
        if action not in ACTIONS:
            out.append("option %d: unknown action %r" % (i, action))
        elif action in TICKET_ACTIONS and not re.fullmatch(r"[0-9]+", ref):
            out.append("option %d: %s is for a ticket, not %r" % (i, action, ref))
        elif action in GROUP_ACTIONS and not re.fullmatch(r"g[0-9]+", ref):
            out.append("option %d: %s is for a group, not %r" % (i, action, ref))
        p = _line_problem("option %d consequence" % i, o.get("consequence"))
        if p:
            out.append(p)
        if "note" in o:
            note = o["note"]
            if not isinstance(note, str):
                out.append("option %d note is not a string" % i)
            elif len(note) > NOTE_MAX:
                out.append("option %d note is longer than %d characters" % (i, NOTE_MAX))
    return out


def command(action, ref, page, note):
    """The shell command that answers page `page` (about `ref`) with this option. Quoting follows
    the ticket (#78) literally: ref and page are pool names (digits, g<digits>, stamped page names)."""
    q = shlex.quote
    if action == "revise":
        return "actl new --revise %s --from user --decision revise --reason %s --page %s" % (
            q(ref), q(note) if note else "'<your words>'", q(page))
    if action in ("retry", "cancel"):
        return "actl new --revise %s --from user --decision %s%s --page %s" % (
            ref, action, " --reason %s" % q(note) if note else "", page)
    if action == "group-ready":
        return "actl tickets ready %s%s --page %s" % (ref, " --note %s" % q(note) if note else "", page)
    if action == "group-cancel":
        return "actl tickets cancel %s --why %s --page %s" % (ref, q(note), page)
    if action == "rerun":
        return "actl tickets rerun %s --why %s --page %s" % (ref, q(note), page)
    if action == "release":
        return "actl tickets release %s --page %s" % (ref, page)
    if action == "ack":
        return "actl tickets ack %s" % page
    raise ValueError("unknown action %r" % (action,))


def lines(name, row):
    """The lines inbox prints after a page's text for its question; [] when the row has no valid
    question keys."""
    if not row.get("options") or problems(row.get("ref"), row):
        return []
    ref = str(row.get("ref"))
    out = ["    question: %s" % row["question"]]
    for i, o in enumerate(row["options"], 1):
        out.append("    %d. %s%s: %s" % (i, o["action"], " (recommended)" if i == 1 else "", o["consequence"]))
        out.append("       $ %s" % command(o["action"], ref, name, o.get("note") or ""))
    out.append("    recommendation: %s" % row["recommendation"])
    return out
