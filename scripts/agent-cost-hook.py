#!/usr/bin/env python3
"""Claude Code 的 SubagentStop / SessionEnd hook:互動 session 與子 agent 的成本寫成 `agent.cost` 事件(#95)。

    echo '{"hook_event_name":"SessionEnd","session_id":"s","transcript_path":"<t>.jsonl"}' \\
        | python3 scripts/agent-cost-hook.py

## 讀哪一份
- SubagentStop:stdin 有 `agent_transcript_path` 就讀它;沒有就推導
  `<transcript_path 去掉 .jsonl>/subagents/agent-<agent_id>.jsonl`。同目錄同名的
  `.meta.json` 給 description 與 agentType,拿來猜票與角色(`transcript-cost.py`)。
  第一則 user 訊息帶 `auto-fix.sh --prepare` 的派工文路徑(`.../t<n>/<run_id>/dispatch-round<r>.md`
  或 `dispatch-verifier-round<r>.md`)時不猜:ticket / role / round / run_id 取自路徑(#152)。
- SessionEnd:只讀主線自己的 `transcript_path`,role=main、ticket / agent_id = null
  (子 agent 在 subagents/ 另一份,不重算)。同一 session resume 後再結束會再記一筆累計,
  讀的人取同 session_id 最後一筆(`transcript-cost.py latest_costs`)。
- 其他 hook_event_name:不發事件。

## 永不擋
rc 恆為 0、**stdout 一個位元組都不印**:SubagentStop 的 stdout JSON 會被當成 decision,
印錯字會擋住子 agent 結束。壞 stdin / 讀不到 transcript → 不發事件、stderr 一行。
與 `session-hook.sh` 同一組不觸發:`AC_ROLE` 設了且不是 main、cwd 有一層是 `worktree_dir`
的 basename(headless worker 的子 agent 已由 #94 的信封算進票 cost[],再記就是重複)。

進不進票 cost[](#152):帶 run_id 的那幾筆(`--prepare` 派工文路徑命中)在 `ticket.py close`
(含 `--landed`、`set <n> state Done`)時,同 (session_id, agent_id) 取最後一筆摺進票 cost[]、
算進 cost_total;run_id 是 null 的(ticket 是猜的)與 role=main 照舊不進。
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import event  # noqa: E402  同一個目錄,共用 repo 根與設定的判讀

BY = "agent-cost-hook"


def say(message):
    sys.stderr.write("agent-cost-hook: %s\n" % message)


def load_parser():
    spec = importlib.util.spec_from_file_location(
        "transcript_cost", os.path.join(HERE, "transcript-cost.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_a_copy(root, cwd):
    """cwd 有一層的 basename 等於 `worktree_dir` 的 basename(與 session-hook.sh 同一條)。"""
    worktree = event.config(root).get("worktree_dir") or "../%s-wt" % os.path.basename(root)
    guard = os.path.basename(os.path.normpath(worktree))
    return bool(guard) and guard in os.path.abspath(cwd).split(os.sep)


def stem(path):
    return path[:-len(".jsonl")] if path.endswith(".jsonl") else path


def subagent_transcript(hook):
    """回 (路徑, 錯誤)。agent_transcript_path 優先,否則由 transcript_path + agent_id 推導。"""
    path = hook.get("agent_transcript_path")
    if path:
        return path, ""
    main, agent = hook.get("transcript_path"), hook.get("agent_id")
    if not main or not agent:
        return None, "stdin 沒有 agent_transcript_path,也缺 transcript_path / agent_id,推導不出子 agent 的 transcript"
    return os.path.join(stem(main), "subagents", "agent-%s.jsonl" % agent), ""


def read_meta(path):
    try:
        with open(stem(path) + ".meta.json", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def main():
    role = os.environ.get("AC_ROLE")
    if role and role != "main":
        return 0
    try:
        hook = json.loads(sys.stdin.read())
    except ValueError as exc:
        say("stdin 不是 JSON(%s),不記" % exc)
        return 0
    if not isinstance(hook, dict):
        say("stdin 不是 JSON 物件,不記")
        return 0
    root = event.repo_root()
    if not os.environ.get(event.ENV_ROOT) and event.looks_like_a_copy(root):
        return 0
    if in_a_copy(root, hook.get("cwd") or os.getcwd()):
        return 0

    name = hook.get("hook_event_name")
    if name == "SessionEnd":
        path, problem = hook.get("transcript_path"), "stdin 沒有 transcript_path"
    elif name == "SubagentStop":
        path, problem = subagent_transcript(hook)
    else:
        return 0
    if not path:
        say("%s:%s,不記" % (name, problem))
        return 0
    parser = load_parser()
    try:
        rows = parser.read_rows(path)
    except (OSError, ValueError) as exc:
        say("%s:讀不了 transcript %s(%s),不記" % (name, path, exc))
        return 0

    summary = parser.summarize(rows)
    rnd, run_id = None, None
    if name == "SessionEnd":
        role, ticket, agent_id = "main", None, None
    else:
        meta = read_meta(path)
        prompt = parser.first_prompt(rows)
        description = meta.get("description") if isinstance(meta.get("description"), str) else ""
        ticket = parser.guess_ticket(description, prompt)
        role = parser.guess_role(description, prompt, meta.get("agentType"))
        agent_id = hook.get("agent_id")
        mark = parser.dispatch_mark(prompt)
        if mark:
            ticket, role = mark["ticket"], mark["role"]
            rnd, run_id = mark["round"], mark["run_id"]
    fields = {"role": role, "round": rnd, "outcome": None, "run_id": run_id,
              "session_id": hook.get("session_id"), "by": BY}
    fields.update(summary)
    fields = {key: fields.get(key) for key in parser.COST_KEYS if key != "at"}
    fields.update(ticket=ticket, agent_id=agent_id)
    event.emit("agent.cost", root=root, keep_null=True, **fields)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001  hook 永不擋(A4):任何意外只到 stderr
        say("%s: %s,不記" % (type(exc).__name__, exc))
    sys.exit(0)
