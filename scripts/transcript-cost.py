#!/usr/bin/env python3
"""Claude Code transcript(jsonl)的成本取數:純讀取,不寫票、不發事件(#95)。

    python3 scripts/transcript-cost.py <transcript.jsonl>     # 印取數結果(JSON)

`agent-cost-hook.py`(SubagentStop / SessionEnd)與 #124 共用這一份;寫票 cost[] 的
只有 `ticket.py cost`,寫 `agent.cost` 事件的只有 hook。

## 取數規則(票 #95 A2)
- 只看 `type == "assistant"` 且 `message.usage` 是物件的列;同一個 `message.id` 取**最後一列**。
- tokens_in / cache_write / cache_read = 去重後 `input_tokens` / `cache_creation_input_tokens`
  / `cache_read_input_tokens` 逐欄加總。某一欄沒有任何訊息帶 → null;有一則帶,缺的算 0
  (與 tickets/SCHEMA.md 成本列的逐欄規則同一種)。
- tokens_out:**每則**去重後訊息的最後一列都有非 null 的 `stop_reason` 才加總,否則 null
  —— 互動 transcript 的 `output_tokens` 多半是串流開頭值(2–16 這種),加起來是假數字。
- model = 去重後非 `<synthetic>` 訊息裡出現最多的;同票數取先出現的。
- wall_seconds = 第一列與最後一列 `timestamp` 的差(整數秒)。
- effort:transcript 裡沒有可靠來源,恆為 null。
"""

import json
import re
import sys
from datetime import datetime

# 成本列的 15 鍵(D-047;正文在 tickets/SCHEMA.md「成本」那一列)。
COST_KEYS = ("role", "round", "model", "tokens_in", "tokens_out", "cache_write",
             "cache_read", "wall_seconds", "outcome", "run_id", "session_id", "by",
             "note", "effort", "at")
USAGE = (("tokens_in", "input_tokens"),
         ("cache_write", "cache_creation_input_tokens"),
         ("cache_read", "cache_read_input_tokens"))
SYNTHETIC = "<synthetic>"
STREAM_NOTE = ("互動 transcript 的 output_tokens 是串流開頭值"
               "(%d/%d 則訊息最後一列沒有 stop_reason),tokens_out 記 null")
EMPTY_NOTE = "transcript 沒有帶 usage 的 assistant 訊息"
PROMPT_CHARS = 300
# 原 #95 D4:描述(沒命中再找 prompt 前 300 字)裡**最早出現**的那一個。英文不分大小寫。
ROLE_KEYWORDS = (("覆核", "reviewer"), ("reviewer", "reviewer"),
                 ("驗證者", "verifier"), ("verifier", "verifier"),
                 ("實作者", "worker"), ("worker", "worker"),
                 ("開題者", "opener"), ("opener", "opener"),
                 ("調查", "investigator"), ("investigat", "investigator"))
TICKET = re.compile(r"#(\d+)")


def read_rows(path):
    """transcript 的每一列。壞掉的一行跳過(多半是正在被寫的最後一行);
    一列 JSON 物件都沒有 → ValueError。讀不到檔 → OSError 原樣往上。"""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    if not rows:
        raise ValueError("%s 沒有一列是 JSON 物件" % path)
    return rows


def messages(rows):
    """去重後的 assistant message(同一個 id 取最後一列);順序是 id 第一次出現的順序。"""
    last = {}
    for index, row in enumerate(rows):
        message = row.get("message")
        if row.get("type") != "assistant" or not isinstance(message, dict) \
                or not isinstance(message.get("usage"), dict):
            continue
        key = message.get("id") or ("row", index)
        last[key] = message
    return list(last.values())


def whole(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def add_up(values):
    values = [whole(value) for value in values]
    return sum(value for value in values if value is not None) \
        if any(value is not None for value in values) else None


def top_model(found):
    counts = {}
    for message in found:
        model = message.get("model")
        if model and model != SYNTHETIC:
            counts[model] = counts.get(model, 0) + 1
    best = None
    for model, count in counts.items():          # dict 保留第一次出現的順序
        if best is None or count > counts[best]:
            best = model
    return best


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def wall_seconds(rows):
    stamps = [s for s in (stamp(row.get("timestamp")) for row in rows) if s is not None]
    if not stamps:
        return None
    try:
        return int((stamps[-1] - stamps[0]).total_seconds())
    except TypeError:                             # 有時區與沒時區混在一起
        return None


def summarize(rows):
    """一份 transcript 的取數結果:model、四種 token、wall_seconds、effort、note。"""
    found = messages(rows)
    result = {"model": top_model(found)}
    for field, key in USAGE:
        result[field] = add_up(message["usage"].get(key) for message in found)
    unfinished = sum(1 for message in found if message.get("stop_reason") is None)
    if found and not unfinished:
        result["tokens_out"] = add_up(message["usage"].get("output_tokens")
                                      for message in found)
        note = None
    else:
        result["tokens_out"] = None
        note = STREAM_NOTE % (unfinished, len(found)) if found else EMPTY_NOTE
    result["wall_seconds"] = wall_seconds(rows)
    result["effort"] = None
    result["note"] = note
    return result


def first_prompt(rows):
    """第一則 user 訊息的文字(content 是字串,或 list 裡 type=text 的那幾段)。"""
    for row in rows:
        message = row.get("message")
        if row.get("type") != "user" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content
                           if isinstance(part, dict) and part.get("type") == "text")
    return ""


def guess_ticket(description, prompt):
    """描述裡第一個 `#<數字>`;沒有就找 prompt 前 300 字;都沒有 → None。"""
    for text in (description or "", (prompt or "")[:PROMPT_CHARS]):
        hit = TICKET.search(text)
        if hit:
            return hit.group(1)
    return None


def earliest_role(text):
    text = (text or "").lower()
    hits = [(text.find(word), role) for word, role in ROLE_KEYWORDS if word in text]
    return min(hits)[1] if hits else None


def guess_role(description, prompt, agent_type=None):
    """描述 → prompt 前 300 字 → meta 的 agentType → "subagent"。"""
    return (earliest_role(description) or earliest_role((prompt or "")[:PROMPT_CHARS])
            or agent_type or "subagent")


def latest_costs(events):
    """讀端(A5):`agent.cost` 主線列以 session_id、子 agent 以 (session_id, agent_id)
    各取最後一筆。只看 agent.cost —— 不與 headless 的 ticket.cost / 票 cost[] 相加。
    沒有 session_id 的列合不起來,各算一筆。"""
    last = {}
    for index, row in enumerate(events):
        if not isinstance(row, dict) or row.get("kind") != "agent.cost":
            continue
        session = row.get("session_id")
        key = (session, row.get("agent_id")) if session else ("row", index)
        last.pop(key, None)
        last[key] = row
    return list(last.values())


def main(argv):
    if len(argv) != 1:
        sys.stderr.write("用法:python3 scripts/transcript-cost.py <transcript.jsonl>\n")
        return 2
    try:
        rows = read_rows(argv[0])
    except (OSError, ValueError) as exc:
        sys.stderr.write("transcript-cost: %s\n" % exc)
        return 2
    sys.stdout.write(json.dumps(summarize(rows), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
