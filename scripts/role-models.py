#!/usr/bin/env python3
"""角色 → 有序模型候選的唯一一張表(`board/role-models.json`,#116,D-049 ②)與它的選路器。

    scripts/role-models.py resolve --role worker
    scripts/role-models.py resolve --role worker --complexity complex
    scripts/role-models.py resolve --role worker --repair-round 2 --last-red --worker-model opus
    scripts/role-models.py resolve --role worker --exhausted-index 0 --evidence <worker log> --evidence-rc 1
    scripts/role-models.py resolve --role opener --complexity complex --items 5
    scripts/role-models.py classify --evidence <log> --rc 1
    scripts/role-models.py check
    scripts/role-models.py legacy [--config board/config.json] [--write]

## 退出碼
`resolve`:0 = 選到一個候選;3 = 整串耗盡(JSON 照印,`exhausted=true`,消費端停下發一次
decision,不回任意 Opus);2 = 不派(表無效、未知角色、旗標錯、超過每次派工上限、證據不是
額度 / 不可用、認證設定錯誤)。

## 為什麼換下一個候選要帶證據
一般 rc≠0、測試紅、逾時、沒交 patch、patch 文字裡出現的 `429` 都是**這張票**的事;換模型
重跑只是多付一次錢。只有 provider 自己的錯誤信封(log 裡頂層的一行 JSON)說額度用完 /
模型不可用、或 CLI 根本不在(rc=127),才算這個候選此刻不能用。認證設定錯了換誰都一樣
錯,停下並印出那一句設定問題,不盲試下一個。

表只管派出去的 agent;`main` 那一列只是開 session 時的建議,不熱切已在跑的互動 session。
這一支只讀表、不動票、不發事件 —— 純選路;派工、記帳、發 decision 是消費端的事(#121/#122)。

## 舊設定(#122 A3)
`board/config.json` 的 `routing.main/open/implement/implement_browser/verify/design/bug` 與
`memory.consolidator/consolidators` 被這張表取代,不再是選路來源。`legacy` 列出還在的那幾格,
與表的首選不同的標「衝突」;`legacy --write` 是那一次明確遷移:刪掉它們、別的一格不動。
`routing.main` 例外留著 —— `session-hook.sh` 拿它當 session 的模型標籤(不是選路),刪了
開場就記成 unknown。`routing.second_opinion / discuss` 沒有 D-049 裁示的角色,不在名單上。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  repo 根的判讀共用一份
from ticket import COMPLEXITY, EFFORT_LEVELS, effort_problem  # noqa: E402  effort 表只在 ticket.py

TABLE_REL = os.path.join("board", "role-models.json")
SCHEMA_VERSION = 1
CANDIDATE_KEYS = ("model", "tool", "effort", "effort_escalated", "dispatch_cap")
CODEX_PREFIX = "codex:"
AGY_PREFIX = "agy:"
# 一次只做一張票的角色:每次派工上限固定 1(A8)。
SINGLE_ITEM_ROLES = ("worker", "worker_simple", "verifier", "reviewer")
POLICY_VALUES = {"fallback": "next_candidate_only_on_quota_or_unavailable",
                 "exhausted": "stop_and_decision"}
COMPLEX = "ticket.complexity == complex"
RED_REPAIR = "repair_round >= 2 and previous round red"
ESCALATE_WHEN = {
    COMPLEX: lambda ask: ask["complexity"] == "complex",
    RED_REPAIR: lambda ask: ask["repair_round"] >= 2 and ask["last_red"],
}
FALLBACK_KINDS = ("quota_exhausted", "provider_unavailable")
AUTH = "auth_config"
OTHER = "other"
# 只對 provider 錯誤信封裡的字比對;順序就是判讀順序 —— 認證先判,它要停下不換人。
ERROR_WORDS = (
    (AUTH, re.compile(r"authentication|permission_error|unauthori[sz]ed|invalid[ _.-]?api[ _.-]?key"
                      r"|api key|\b40[13]\b|not logged in|log ?in|invalid --effort"
                      r"|conflicts with --effort|has no .* effort", re.I)),
    ("quota_exhausted", re.compile(r"rate[ _.-]?limit|usage[ _.-]?limit|quota|\b429\b"
                                   r"|resource exhausted|too many requests", re.I)),
    ("provider_unavailable", re.compile(r"not_found_error|model_not_found|\b404\b"
                                        r"|not recognized as a known model"
                                        r"|model\b.*\b(not found|does not exist|not available"
                                        r"|not supported|unavailable)", re.I)),
)
EXIT_CLI_MISSING = 127


class TableError(ValueError):
    pass


def table_path(root=None):
    return os.path.join(root or event.repo_root(), TABLE_REL)


def is_cap(value):
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value > 0)


def candidate_problems(role, index, row):
    where = "roles.%s[%d]" % (role, index)
    if not isinstance(row, dict):
        return ["%s 不是物件" % where]
    if sorted(row) != sorted(CANDIDATE_KEYS):
        return ["%s 的鍵要恰是 %s,現在是 %s" % (where, "/".join(CANDIDATE_KEYS),
                                                 "/".join(sorted(row)) or "(空)")]
    problems = []
    model, tool = row["model"], row["tool"]
    if not isinstance(model, str) or not model or not isinstance(tool, str) or not tool:
        return ["%s 的 model / tool 要是非空字串" % where]
    if tool not in EFFORT_LEVELS:
        problems.append("%s 的 tool=%s 不在 %s" % (where, tool, " ".join(sorted(EFFORT_LEVELS))))
    else:
        for prefix, paired_tool in ((CODEX_PREFIX, "codex"), (AGY_PREFIX, "agy")):
            if model.startswith(prefix) != (tool == paired_tool):
                problems.append("%s 的 model=%s 與 tool=%s 不一致(`%s` 前綴 ⇔ tool=%s)"
                                % (where, model, tool, prefix, paired_tool))
    for key in ("effort", "effort_escalated"):
        if row[key] is not None:
            why = effort_problem(tool, row[key])
            if why:
                problems.append("%s.%s:%s" % (where, key, why))
    cap = row["dispatch_cap"]
    if not is_cap(cap):
        problems.append("%s.dispatch_cap=%r 要是正整數或 null" % (where, cap))
    elif role in SINGLE_ITEM_ROLES and cap != 1:
        problems.append("%s.dispatch_cap=%r:%s 一次只做一張票,固定 1" % (where, cap, role))
    return problems


def table_problems(table):
    """整張表的毛病,一條一句;空 list = 可以用。**任何一格壞就整張不用**(A4):
    跳過壞的那一個候選等於把鏈默默縮短。"""
    if not isinstance(table, dict):
        return ["表不是物件"]
    problems = []
    if table.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version=%r,這一支只讀 %d" % (table.get("schema_version"),
                                                           SCHEMA_VERSION))
    if table.get("candidate_keys") != list(CANDIDATE_KEYS):
        problems.append("candidate_keys 要恰是 %s" % json.dumps(list(CANDIDATE_KEYS)))
    roles = table.get("roles")
    if not isinstance(roles, dict) or not roles:
        return problems + ["roles 要是非空物件"]
    for role, chain in roles.items():
        if not isinstance(chain, list) or not chain:
            problems.append("roles.%s 要是非空 list(空鏈不派)" % role)
            continue
        for index, row in enumerate(chain):
            problems.extend(candidate_problems(role, index, row))
    policies = table.get("policies")
    if not isinstance(policies, dict):
        return problems + ["policies 要是物件"]
    for key, want in POLICY_VALUES.items():
        if policies.get(key) != want:
            problems.append("policies.%s=%r,這一支只會做 %r" % (key, policies.get(key), want))
    when = policies.get("escalate_effort_when")
    if not isinstance(when, list) or any(cond not in ESCALATE_WHEN for cond in when):
        problems.append("policies.escalate_effort_when 只收 %s" % json.dumps(list(ESCALATE_WHEN),
                                                                         ensure_ascii=False))
    for key in ("repair_round_keeps_worker_model_and_effort", "verifier_prefer_different_model"):
        if not isinstance(policies.get(key), bool):
            problems.append("policies.%s 要是 true / false" % key)
    return problems


def load_table(path=None):
    path = path or table_path()
    try:
        with open(path, encoding="utf-8") as handle:
            table = json.load(handle)
    except (OSError, ValueError) as exc:
        raise TableError("讀不到角色模型表 %s(%s)" % (path, exc))
    problems = table_problems(table)
    if problems:
        raise TableError("角色模型表 %s 無效,不派:\n  %s" % (path, "\n  ".join(problems)))
    return table


# ------------------------------------------------------------------ 證據分類


def provider_error(row):
    """log 裡一行頂層 JSON 是不是 provider 的錯誤信封;是回 `(種類, 訊息)`,不是回 None。
    認得的形狀:`{"type":"error","error":{"type"|"code","message"}}` / `{"type":"error","message"}`
    (claude API 錯誤、`codex exec --json`)、`{"type":"turn.failed","error":{"message"}}`
    (codex)、`{"type":"result","is_error":true,"result"}`(claude -p 的信封)。"""
    if row.get("event") == "result" and isinstance(row.get("result"), dict):
        row = row["result"]
    if row.get("status") == "ERROR" and row.get("error") is not None:
        return "", str(row.get("error"))
    kind = row.get("type")
    if kind in ("error", "turn.failed"):
        err = row.get("error")
        if isinstance(err, dict):
            return str(err.get("type") or err.get("code") or ""), str(err.get("message") or "")
        return str(row.get("code") or ""), str(row.get("message") or err or "")
    if kind == "result" and row.get("is_error") is True:
        return str(row.get("subtype") or ""), "%s %s" % (row.get("api_error_status") or "",
                                                         row.get("result") or "")
    return None


def classify(rc, text):
    """一次失敗的派工能不能換下一個候選。回 `(種類, 說明)`;種類是 `quota_exhausted` /
    `provider_unavailable`(可換)、`auth_config`(停下,設定問題)、`other`(這張票的事,不換)。"""
    if rc == 0:
        return OTHER, "rc=0,不是失敗"
    if rc == EXIT_CLI_MISSING:
        return "provider_unavailable", "CLI 不在(rc=127)"
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        found = provider_error(row) if isinstance(row, dict) else None
        if found is None:
            continue
        said = " ".join(part for part in found if part).strip()
        for verdict, words in ERROR_WORDS:
            if words.search(said):
                return verdict, "provider 錯誤信封:%s" % said
        return OTHER, "provider 錯誤信封沒說額度 / 不可用 / 認證:%s" % said
    return OTHER, "log 裡沒有 provider 錯誤信封(rc=%s);一般失敗不換候選" % rc


# ------------------------------------------------------------------ 選路


def chain_role(role, complexity):
    """票的 complexity 選哪一條鏈(A7):worker 的 simple → worker_simple;opener 的
    complex → opener_complex;其他照角色名。"""
    if role == "worker" and complexity == "simple":
        return "worker_simple"
    if role == "opener" and complexity == "complex":
        return "opener_complex"
    return role


def result(role, reason, index=None, row=None, effort=None, exhausted=False):
    row = row or {}
    return {"role": role, "candidate_index": index, "model": row.get("model"),
            "tool": row.get("tool"), "effort": effort, "dispatch_cap": row.get("dispatch_cap"),
            "reason": reason, "exhausted": exhausted}


def resolve(table, role, complexity=None, repair_round=0, last_red=False, items=1,
            ticket_effort=None, ticket_model=None, ticket_tool=None, worker_model=None,
            exhausted_index=None):
    """選一個候選。回 `(rc, 結果)`;rc 2 時結果是一句為什麼不派。表要先過 `load_table`。

    `exhausted_index` = 已被可信證據標記用不了的那一格(含它之前的):鏈是有序的,走到第 I
    格就表示前面都已經換過。證據的判讀在 `classify`:CLI 帶 `--evidence` 就當場判,不帶就是
    呼叫端已經判過的標記 —— 消費端不准拿一般 rc≠0 去標。
    `worker_model` = 修補輪原 worker 的模型(角色是 worker 才讀;verifier 不看它,A3)。"""
    if complexity is not None and complexity not in COMPLEXITY:
        return 2, "complexity=%s 不合法;只收 %s" % (complexity, "|".join(COMPLEXITY))
    if not isinstance(items, int) or isinstance(items, bool) or items < 1:
        return 2, "--items=%r 要是正整數" % (items,)
    name = chain_role(role, complexity)
    chain = table["roles"].get(name)
    if chain is None:
        return 2, "未知角色 %s;表裡有 %s" % (name, " ".join(table["roles"]))
    if role == "worker" and ticket_model:
        same = next((row for row in chain if row["model"] == ticket_model), None)
        explicit = dict(same) if same else {
            "model": ticket_model, "tool": ticket_tool or "claude-code", "effort": None,
            "effort_escalated": None, "dispatch_cap": 1}
        explicit["tool"] = ticket_tool or explicit["tool"]
        problems = candidate_problems("票", 0, dict(explicit, effort=ticket_effort)
                                      if ticket_effort else explicit)
        if problems:
            return 2, ";".join(problems)
        chain = [explicit] + [row for row in chain if row["model"] != ticket_model]
    policies = table["policies"]
    ask = {"complexity": complexity, "repair_round": repair_round or 0, "last_red": last_red}
    escalate = [cond for cond in policies["escalate_effort_when"] if ESCALATE_WHEN[cond](ask)]
    start, why = 0, ["%s 鏈第 0 格" % name]
    if exhausted_index is not None:
        if not 0 <= exhausted_index < len(chain):
            return 2, "--exhausted-index=%d 不在 %s 鏈的範圍(0..%d)" % (
                exhausted_index, name, len(chain) - 1)
        start = exhausted_index + 1
        why = ["第 %d 格(%s)已被額度 / 不可用證據標記,換下一格" % (
            exhausted_index, chain[exhausted_index]["model"])]
    elif (name == "worker" and repair_round and worker_model
          and policies["repair_round_keeps_worker_model_and_effort"]):
        models = [row["model"] for row in chain]
        if worker_model not in models:
            return 2, ("原 worker 模型 %s 不在 %s 鏈(%s);表外模型先改表或由主線裁示"
                       % (worker_model, name, " → ".join(models)))
        start = models.index(worker_model)
        why = ["修補第 %d 輪沿用原 worker 模型 %s(第 %d 格)" % (repair_round, worker_model, start)]
    if start >= len(chain):
        tried = " → ".join(row["model"] for row in chain)
        return 3, result(name, "%s 鏈全部耗盡(%s);停下發一次 decision" % (name, tried),
                         exhausted=True)
    row = chain[start]
    effort = row["effort"]
    if escalate and row["effort_escalated"] is not None:
        effort = row["effort_escalated"]
        why.append("effort 升級為 %s(%s)" % (effort, ";".join(escalate)))
    elif escalate:
        why.append("符合升級條件但候選沒有 effort_escalated,沿用 %s" % effort)
    if ticket_effort:
        problem = effort_problem(row["tool"], ticket_effort)
        if problem:
            why.append("票的 effort 不跨工具硬套(%s),採候選 effort %s" % (problem, effort))
        else:
            effort = ticket_effort
            why.append("票明寫 effort=%s,優先於表" % ticket_effort)
    cap = row["dispatch_cap"]
    if cap is not None and items > cap:
        return 2, "超過 %s 的每次派工上限 %d,請切成 ≤%d 的批(--items=%d)" % (
            row["model"], cap, cap, items)
    return 0, result(name, ";".join(why), index=start, row=row, effort=effort)


# ------------------------------------------------------------------ 舊設定


# 被表取代的設定格 → 表裡對應的角色(None = 表裡沒有這個角色,留著也沒人讀)。
LEGACY_KEYS = (
    (("routing", "main"), "main"),
    (("routing", "open"), "opener"),
    (("routing", "implement"), "worker"),
    (("routing", "implement_browser"), "worker"),
    (("routing", "verify"), "verifier"),
    (("routing", "design"), "design"),
    (("routing", "bug"), None),
    (("memory", "consolidator"), "consolidator"),
    (("memory", "consolidators"), "consolidator"),
)
# session-hook.sh 的模型標籤讀它;遷移不刪(見檔頭)。
LEGACY_KEPT = (("routing", "main"),)


def legacy_rows(conf, table):
    """config 裡還在的舊選路格,一格一筆 `(鍵, 值, 表的首選, 衝突?)`。"""
    rows = []
    for path, role in LEGACY_KEYS:
        section = conf.get(path[0]) if isinstance(conf, dict) else None
        if not isinstance(section, dict) or path[1] not in section:
            continue
        value = section[path[1]]
        chain = table["roles"].get(role) if role else None
        first = chain[0]["model"] if chain else None
        given = [str(m) for m in value] if isinstance(value, list) else [str(value)]
        rows.append((".".join(path), value, first, first is None or given != [first]))
    return rows


def legacy(conf_path, table, write):
    """`legacy` 的本體:印每一格,衝突的標出來;`write` 才刪(LEGACY_KEPT 除外)。回 rc。"""
    try:
        with open(conf_path, encoding="utf-8") as handle:
            conf = json.load(handle)
    except (OSError, ValueError) as exc:
        return fail("讀不到 %s(%s)" % (conf_path, exc))
    rows = legacy_rows(conf, table)
    if not rows:
        print("role-models: %s 沒有被角色表取代的舊選路設定" % conf_path)
        return 0
    for key, value, first, clash in rows:
        said = json.dumps(value, ensure_ascii=False)
        if tuple(key.split(".")) in LEGACY_KEPT and not clash:
            print("role-models: %s=%s 只當 session-hook 的模型標籤,不選路 —— 遷移保留" % (key, said))
        elif first is None:
            print("role-models: 舊設定 %s=%s —— 角色表沒有對應角色,已不讀" % (key, said))
        elif clash:
            print("role-models: 舊設定衝突 %s=%s,角色表首選 %s —— 派工只照角色表" % (key, said, first))
        else:
            print("role-models: 舊設定 %s=%s 與角色表首選相同 —— 已不讀" % (key, said))
    if not write:
        print("role-models: 一次遷移:python3 scripts/role-models.py legacy --write"
              "(刪掉上面那幾格;routing.main 留給 session-hook 當模型標籤)")
        return 0
    dropped = []
    for key, _, _, _ in rows:
        path = tuple(key.split("."))
        if path in LEGACY_KEPT:
            continue
        del conf[path[0]][path[1]]
        dropped.append(key)
    tmp = conf_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(conf, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, conf_path)
    print("role-models: 已遷移 %s:刪掉 %s" % (conf_path, " ".join(dropped) or "(無)"))
    return 0


# ------------------------------------------------------------------ CLI


def parser():
    top = argparse.ArgumentParser(prog="role-models.py", description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    top.add_argument("--table", help="表的路徑(預設 <repo 根>/board/role-models.json)")
    verbs = top.add_subparsers(dest="verb", required=True)
    res = verbs.add_parser("resolve", help="選這次派工的候選;印一行 JSON")
    res.add_argument("--role", required=True,
                     help="表裡的角色(worker / verifier / opener / reviewer / …)")
    res.add_argument("--complexity", help="票的 complexity:simple|complex(選填)")
    res.add_argument("--repair-round", type=int, default=0, metavar="N",
                     help="第幾個修補輪;0 / 不給 = 首輪")
    res.add_argument("--last-red", action="store_true", help="上一輪閘門紅")
    res.add_argument("--worker-model", metavar="M",
                     help="修補輪原 worker 用的模型(只有 worker 鏈讀;verifier 不看)")
    res.add_argument("--ticket-effort", metavar="E", help="票明寫的 effort(優先於表,A5)")
    res.add_argument("--ticket-model", metavar="M", help="worker 票明寫的 model(作為第一候選)")
    res.add_argument("--ticket-tool", metavar="T", help="worker 票明寫 model 配對的 tool")
    res.add_argument("--items", type=int, default=1, metavar="N",
                     help="這次要交幾張票 / 幾個項目;不給 = 1")
    res.add_argument("--exhausted-index", type=int, metavar="I",
                     help="已被額度 / 不可用證據標記的候選格(含之前);帶 --evidence 就由這一支判證據")
    res.add_argument("--evidence", metavar="LOG", help="那一次派工的 log(含 provider 信封)")
    res.add_argument("--evidence-rc", type=int, metavar="RC", help="那一次派工的退出碼")
    cls = verbs.add_parser("classify", help="一次失敗的派工能不能換候選;印一行 JSON")
    cls.add_argument("--evidence", required=True, metavar="LOG")
    cls.add_argument("--rc", type=int, required=True)
    verbs.add_parser("check", help="只驗表;壞了 rc=2 並逐條列出")
    old = verbs.add_parser("legacy", help="列出 config 裡被角色表取代的舊選路設定;--write 一次遷移")
    old.add_argument("--config", metavar="PATH",
                     help="設定檔路徑(預設 <repo 根>/board/config.json)")
    old.add_argument("--write", action="store_true", help="刪掉那幾格(routing.main 除外)")
    return top


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def fail(message):
    sys.stderr.write("role-models: %s\n" % message)
    return 2


def main(argv):
    args = parser().parse_args(argv)
    if args.verb == "classify":
        try:
            kind, detail = classify(args.rc, read_text(args.evidence))
        except OSError as exc:
            return fail("讀不到 %s(%s)" % (args.evidence, exc))
        print(json.dumps({"kind": kind, "detail": detail}, ensure_ascii=False))
        return 0
    try:
        table = load_table(args.table)
    except TableError as exc:
        return fail(str(exc))
    if args.verb == "check":
        print("role-models: 表可用(%d 個角色)" % len(table["roles"]))
        return 0
    if args.verb == "legacy":
        return legacy(args.config or os.path.join(event.repo_root(), "board", "config.json"),
                      table, args.write)
    detail = "第 %s 格由呼叫端標記(未附 --evidence;標記前要先用 classify 判)" % args.exhausted_index
    if args.evidence is not None:
        if args.exhausted_index is None or args.evidence_rc is None:
            return fail("--evidence 要與 --exhausted-index、--evidence-rc 一起給")
        try:
            kind, detail = classify(args.evidence_rc, read_text(args.evidence))
        except OSError as exc:
            return fail("讀不到 %s(%s)" % (args.evidence, exc))
        if kind == AUTH:
            return fail("認證設定錯誤,停下修設定、不換下一個候選:%s" % detail)
        if kind not in FALLBACK_KINDS:
            return fail("證據不是額度 / 不可用(%s),不換候選:%s" % (kind, detail))
    rc, got = resolve(table, args.role, complexity=args.complexity,
                      repair_round=args.repair_round, last_red=args.last_red, items=args.items,
                      ticket_effort=args.ticket_effort, ticket_model=args.ticket_model,
                      ticket_tool=args.ticket_tool, worker_model=args.worker_model,
                      exhausted_index=args.exhausted_index)
    if rc == 2:
        return fail(got)
    if args.exhausted_index is not None:
        got["reason"] = "%s;%s" % (detail, got["reason"])
    print(json.dumps(got, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
