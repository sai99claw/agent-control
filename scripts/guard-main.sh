#!/bin/sh
# Claude Code 的 PreToolUse hook:主線越界**機械擋**,不靠自覺(#63)。
# `.claude/settings.json` 以 matcher `Bash|Read` 叫這一支。
#
#   echo '{"tool_name":"Bash","tool_input":{"command":"python3 scripts/ticket.py create"},"cwd":"<根>"}' \
#       | sh scripts/guard-main.sh
#
# 為什麼要有這一支:主線 compact 之後自寫票面(`ticket.py create` / `set <票面欄>`)、
# 讀 worker log 與 task output 不看收件匣、裸 commit 主線 —— 規矩寫在 CLAUDE.md 與角色卡,
# 而**compact 後規矩就只是一段被摘要掉的字**。hook 不靠自覺:擋下的那一手工具呼叫根本沒跑。
#
# ## 介面(#63 驗收釘死)
# stdin 是 PreToolUse 的 JSON(`tool_name`、`tool_input.command` / `tool_input.file_path`、
# `cwd`、子代理人的呼叫多帶 `agent_id`)。擋 = stdout 一行 `hookSpecificOutput`
# (`permissionDecision: deny`,理由一行寫「擋了什麼 → 該走哪」)、rc 0;放行 = rc 0、stdout 空。
# **JSON 讀不懂、指令 shlex 拆不開 → 放行**:守衛只擋認得出來的,不猜。
#
# ## 只對主線本人
# 三條各自成立就放行(與 session-hook.sh 同一組,多一條 agent_id):`AC_ROLE` 設了且不是 main /
# stdin 帶 `agent_id`(子代理人:主線用 Agent 派的開題者就在同一個 session 裡跑
# `ticket.py create`,擋它等於把開題流程整條擋死)/ cwd 有一層叫 `worktree_dir` 的 basename。
#
# ## 逃生口:`AC_MAIN_OVERRIDE=1`,而且留下紀錄
# 放在**指令前綴**(`AC_MAIN_OVERRIDE=1 python3 scripts/ticket.py create …`):hook 行程的環境
# 是 Claude Code 的,主線在 Bash 裡 export 傳不進來。Read 工具沒有前綴可放,只認行程環境。
# 放行越界的那一手發 `main.override`;每一次擋發 `main.blocked` —— 沒有分母,遵循度算不出來。
# 沒越界的指令帶著前綴也不發事件:那一行量的是「繞過守衛」,不是「打了前綴」。
set -u
[ -n "${AC_ROLE:-}" ] && [ "$AC_ROLE" != "main" ] && exit 0

# 根與同伴腳本的找法與 session-hook.sh 同一種:同步到專案後這一支住在 scripts/control/。
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
AC=$(cd "$(dirname "$0")" && pwd)

if [ -t 0 ]; then INPUT=""; else INPUT=$(cat); fi

python3 - "$ROOT" "$AC" "$INPUT" "$PWD" "${AC_MAIN_OVERRIDE:-}" <<'PY'
import json
import os
import re
import shlex
import sys

root, here, raw, pwd, env_override = sys.argv[1:6]
try:
    hook = json.loads(raw)
except ValueError:
    sys.exit(0)
if not isinstance(hook, dict) or hook.get("agent_id"):
    sys.exit(0)
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
conf = conf if isinstance(conf, dict) else {}
worktree = conf.get("worktree_dir") or "../%s-wt" % os.path.basename(root)
guard = os.path.basename(os.path.normpath(worktree))
cwd = hook.get("cwd") or pwd
if guard and guard in os.path.abspath(cwd).split(os.sep):
    sys.exit(0)

OVERRIDE = "AC_MAIN_OVERRIDE=1"
# SCHEMA「目標與範圍」那一群:開題者寫的票面。流程欄(state、objections、base_sha…)照放。
FACE = ("subject", "objective", "acceptance", "in_scope", "out_of_scope",
        "allowed_write_paths", "verify_strings", "test_plan")
READERS = ("cat", "head", "tail", "less", "more", "sed", "awk", "grep", "rg")
# 前面帶一格參數的 git 全域選項:`git -C . merge` 的子指令是 merge,不是 `.`。
GIT_ARG_OPTS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
ctl = os.path.relpath(here, root)


def is_log(path):
    return path.endswith(".log") or re.search(r"/tasks/[^/]+\.output$", path) is not None


def segments(command):
    """`&&` `||` `;` `|` 切開的每一段。拆不開(引號沒收)就回 None —— 放行。"""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    parts, part = [], []
    for token in tokens:
        if token and all(c in "();<>|&" for c in token):
            if part:
                parts.append(part)
            part = []
        else:
            part.append(token)
    if part:
        parts.append(part)
    return parts


def ticket_face(words):
    verb = os.path.basename(words[0])
    args = words[1:]
    if verb.startswith("python"):
        while args and args[0].startswith("-"):
            args = args[1:]
        if not args:
            return False
        verb, args = os.path.basename(args[0]), args[1:]
    if verb != "ticket.py" or not args:
        return False
    if args[0] == "create":
        return True
    return args[0] == "set" and len(args) >= 3 and args[2] in FACE


def git_write(words):
    if os.path.basename(words[0]) != "git":
        return False
    args = words[1:]
    while args and args[0].startswith("-"):
        args = args[2:] if args[0] in GIT_ARG_OPTS else args[1:]
    if not args:
        return False
    if args[0] == "commit":
        return True
    return args[0] == "merge" and not ({"--abort", "--quit"} & set(args[1:]))


def log_read(words):
    if os.path.basename(words[0]) not in READERS:
        return None
    for word in words[1:]:
        if not word.startswith("-") and is_log(word):
            return word
    return None


def verdict_bash(command):
    """(規則, 放行了嗎) —— 沒越界回 (None, False)。"""
    parts = segments(command)
    if parts is None:
        return None, False
    whole = command.lstrip().startswith(OVERRIDE + " ")
    for words in parts:
        lead = []
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            lead.append(words[0])
            words = words[1:]
        if not words:
            continue
        rule = ("ticket" if ticket_face(words) else "git" if git_write(words)
                else "log" if log_read(words) else None)
        if rule:
            return rule, whole or OVERRIDE in lead
    return None, False


tool = hook.get("tool_name")
tool_input = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
if tool == "Bash" and isinstance(tool_input.get("command"), str):
    command = tool_input["command"]
    rule, prefixed = verdict_bash(command)
    summary = command.strip()
    if summary.startswith(OVERRIDE + " "):
        summary = summary[len(OVERRIDE) + 1:].lstrip()
elif tool == "Read" and isinstance(tool_input.get("file_path"), str):
    path = tool_input["file_path"]
    rule, prefixed = ("log" if is_log(path) else None), False
    summary = "Read " + path
else:
    sys.exit(0)
if not rule:
    sys.exit(0)
summary = " ".join(summary.split())[:200]

if os.path.exists(os.path.join(root, "scripts", "land.sh")):
    land = ("sh scripts/land.sh <分支…>;票檔 / 文件 / 記憶走 docs 通道 "
            "sh scripts/land.sh docs \"<訊息>\" <檔…>")
else:
    land = "專案 CLAUDE.md 對照表的落地入口"
REASONS = {
    "ticket": "擋了主線自寫票面(ticket.py create / set <票面欄>) → 派開題者(Agent)開票或改票面",
    "log": "擋了主線讀 log / task output(%s) → 讀收件匣 python3 %s/inbox.py show <票號>"
           % (summary, ctl),
    "git": "擋了裸 git commit / merge 主線 → 走 %s" % land,
}

sys.path.insert(0, here)
import event  # noqa: E402

overridden = prefixed or env_override == "1"
try:
    event.emit("main.override" if overridden else "main.blocked", root=root,
               role="main", tool=tool, rule=rule, note=summary)
    lost = ""
except OSError as exc:
    lost = "(事件沒寫進去:%s)" % exc
if overridden:
    sys.exit(0)
reason = "guard-main: %s;真的要越界:指令前面加 %s(記一筆 main.override)%s" % (
    REASONS[rule], OVERRIDE, lost)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": reason}}, ensure_ascii=False))
PY
exit 0
