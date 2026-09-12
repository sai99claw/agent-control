#!/usr/bin/env python3
"""票:唯一的工作單位。契約在 `tickets/SCHEMA.md`。

    scripts/ticket.py create --subject … --objective … --acceptance … --allowed-write-path …
    scripts/ticket.py create                      # 不給參數就一格一格問
    scripts/ticket.py list --open                 # 或 --state Running
    scripts/ticket.py show 7
    scripts/ticket.py set 7 state InReview        # 每次變更 state_version +1,並發事件
    scripts/ticket.py inbox                       # 等裁決的 + 使用者答了還沒落成裁示的
    scripts/ticket.py verify 7                    # 改動真的在主線?
    scripts/ticket.py close 7                     # 先 verify,>0 才准關
    scripts/ticket.py import <舊票目錄>           # 轉成這份 schema,缺的留空並標 legacy
    scripts/ticket.py freeze 7 --reason … --criterion …

**不要手改票檔**(`tickets/README.md`):`state_version` 是遲到的回報用來認出自己
過期的那一格,而手改不會動它。
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  同一個目錄,共用 repo 根與設定的判讀

# docs/WORKFLOW.md 的狀態機。表在這裡,不在提示裡 —— D-001。
STATES = ("Draft", "Ready", "Running", "InReview", "IntegrationQueued",
          "Integrating", "Done", "Blocked", "NeedsDecision", "Failed", "Cancelled")
CLOSED_STATES = ("Done", "Cancelled")
NEEDS_DECISION = "NeedsDecision"

# 必填 = `tickets/SCHEMA.md` 的「最小可開工範例」逐格列出來的那些。
# (SCHEMA 的表把 `shared_resources` / `attempt_history` / `branch` / `workspace` /
#  `budget` 也標了 ✓,而它自己的最小範例沒有那五格 —— 以範例為準,差異記在回報裡。)
REQUIRED = ("id", "subject", "created", "objective", "acceptance",
            "in_scope", "out_of_scope", "depends_on", "allowed_write_paths",
            "role", "model", "tool", "attempt", "base_sha",
            "state", "state_version", "lease", "retry_limit")
# 這幾格空著等於沒填:一張沒有驗收條件、沒有寫入範圍的票,排程器與落地器都讀不動。
NOT_EMPTY = ("subject", "objective", "acceptance", "allowed_write_paths",
             "role", "model", "tool", "base_sha", "state")
LIST_FIELDS = ("acceptance", "in_scope", "out_of_scope", "depends_on",
               "allowed_write_paths", "shared_resources", "decision_refs",
               "test_evidence", "attempt_history", "verify_strings")

DECISIONS_REL = os.path.join("docs", "DECISIONS.md")
DEFAULT_MAIN = "main"
GIT_TIMEOUT = 30


# ------------------------------------------------------------------ 基本零件


def root():
    return event.repo_root()


def main_branch():
    return event.config(root()).get("main_branch") or DEFAULT_MAIN


def ticket_path(ident, where=None):
    return os.path.join(where or event.tickets_dir(root()), "%s.json" % ident)


def load(ident, where=None):
    with open(ticket_path(ident, where), encoding="utf-8") as handle:
        return json.load(handle)


def load_all(where=None):
    where = where or event.tickets_dir(root())
    out = []
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(where, name), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    out.sort(key=lambda t: (0, int(t["id"])) if str(t["id"]).isdigit() else (1, 0))
    return out


def save(ticket, where=None):
    """整份重寫 —— 票檔是一份完整的 JSON,不是只加不改的 jsonl(那種要 O_APPEND,
    見 `event.py`)。先寫暫存再 rename,讓同時在讀的控制台不會讀到半份。"""
    path = ticket_path(ticket["id"], where)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(ticket, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def next_id(where=None):
    """遞增:現有最大的整數 id + 1。**不補洞** —— 補洞會讓兩張不同的票在不同的
    時間拿到同一個號碼,而回報、分支名、事件裡的 `#7` 從此指兩件事。"""
    biggest = 0
    for ticket in load_all(where):
        if str(ticket["id"]).isdigit():
            biggest = max(biggest, int(ticket["id"]))
    return str(biggest + 1)


def missing_fields(ticket):
    bad = []
    for field in REQUIRED:
        if field not in ticket:
            bad.append("%s(沒有這一格)" % field)
    for field in NOT_EMPTY:
        if field in ticket and not ticket.get(field):
            bad.append("%s(空的)" % field)
    if ticket.get("state") and ticket["state"] not in STATES:
        bad.append("state=%s 不在狀態機裡(%s)" % (ticket["state"], "/".join(STATES)))
    return bad


def git(args, cwd=None, check=False):
    done = subprocess.run(["git", *args], cwd=cwd or root(), capture_output=True,
                          text=True, timeout=GIT_TIMEOUT)
    if check and done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip())
    return done


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# -------------------------------------------------------------------- create


CREATE_FLAGS = {
    "--subject": "subject", "--objective": "objective", "--role": "role",
    "--model": "model", "--tool": "tool", "--base-sha": "base_sha",
    "--state": "state", "--feature": "feature", "--outline": "outline",
    "--test-plan": "test_plan", "--branch": "branch", "--workspace": "workspace",
}
CREATE_REPEATED = {
    "--acceptance": "acceptance", "--in-scope": "in_scope",
    "--out-of-scope": "out_of_scope", "--allowed-write-path": "allowed_write_paths",
    "--depends-on": "depends_on", "--decision-ref": "decision_refs",
    "--verify-string": "verify_strings", "--shared-resource": "shared_resources",
}
# 旗標 → 一句話說明。**名單不在這裡** —— `--help` 要印哪幾個是從 `CREATE_FLAGS` /
# `CREATE_REPEATED` 自己數出來的,這裡只補說明。兩份名單會分岔,一份不會。
FLAG_NOTE = {
    "--subject": "一句話的標題",
    "--objective": "做完了的樣子",
    "--acceptance": "驗收;**一條一個旗標**,每條要能寫成一條會紅的斷言",
    "--in-scope": "範圍內的檔;一個一個給",
    "--out-of-scope": "明說不要動的;一個一個給",
    "--allowed-write-path": "允許寫入的路徑 glob;**一個一個給**(排程器判平行、落地器判越界,讀的都是這一格)",
    "--depends-on": "前置票號;`7` 或 `7:閘門綠`;一個一個給",
    "--decision-ref": "相關裁示編號(D-00x);一個一個給",
    "--verify-string": "關票時要在主線上抓到的字;`路徑:那串字` 或只給字;一個一個給",
    "--shared-resource": "共用的執行資源(同一顆 DB、同一個埠);一個一個給",
    "--role": "角色:worker / reviewer / scheduler / consolidator",
    "--model": "模型",
    "--tool": "工具:claude-code / codex / …",
    "--base-sha": "基準 sha;不給就自己取主線的 HEAD",
    "--state": "初始狀態,預設 Draft(%s)" % " / ".join(STATES),
    "--feature": "對到哪個產品功能",
    "--outline": "高階規劃摘要(留關鍵決定,不抄整段聊天)",
    "--test-plan": "測試計畫",
    "--branch": "分支名,落地要 `t<票號>-…` 的形狀",
    "--workspace": "副本路徑",
    "--open": "只看還開著的(不是 Done / Cancelled)",
    "--state X": "只看某一個狀態",
    "--reason": "為什麼凍結",
    "--criterion": "什麼時候可以解凍 —— 少了它,「凍著」與「忘了」長得一樣",
}

ASK = (
    ("subject", "一句話的標題", False),
    ("objective", "目標(做完了的樣子)", False),
    ("acceptance", "驗收(每條要能寫成一條會紅的斷言;空行結束)", True),
    ("in_scope", "範圍內的檔(空行結束)", True),
    ("out_of_scope", "明說不要動的(空行結束)", True),
    ("allowed_write_paths", "允許寫入的路徑 glob(空行結束)", True),
    ("depends_on", "前置票號(空行結束)", True),
    ("role", "角色(worker / reviewer / …)", False),
    ("model", "模型", False),
    ("tool", "工具(claude-code / codex / …)", False),
)


AUTO_FILLED = ("base_sha",)      # 不給就自己去問主線,所以它不是「必填」

USAGE = {
    "create": "scripts/ticket.py create [旗標…]        # 一個旗標都不給就一格一格問",
    "list": "scripts/ticket.py list [--open | --state <狀態>]",
    "show": "scripts/ticket.py show <id>",
    "set": "scripts/ticket.py set <id> <欄位> <值>      # 值吃得懂 JSON 就當 JSON",
    "inbox": "scripts/ticket.py inbox",
    "verify": "scripts/ticket.py verify <id>",
    "close": "scripts/ticket.py close <id>              # 先 verify,>0 才准關",
    "import": "scripts/ticket.py import <舊票目錄>",
    "freeze": "scripts/ticket.py freeze <id> --reason … --criterion …",
}

EXAMPLE = {
    "create": """python3 scripts/ticket.py create \\
  --subject "land.sh 對 0 commit 的分支整批拒絕" \\
  --objective "任一支分支相對主線 0 commit 時,land 秒退並點名,不跑全套" \\
  --acceptance "0 commit → rc!=0 且輸出含分支名" \\
  --acceptance "多支中一支 0 → 另一支也不在 land worktree" \\
  --allowed-write-path "scripts/land.sh" \\
  --allowed-write-path "tests/test_land.py" \\
  --verify-string "scripts/land.sh:沒有新的 commit" \\
  --role worker --model opus --tool claude-code""",
    "list": "python3 scripts/ticket.py list --open",
    "show": "python3 scripts/ticket.py show 7",
    "set": """python3 scripts/ticket.py set 7 state InReview
python3 scripts/ticket.py set 7 allowed_write_paths '["scripts/land.sh", "tests/*"]'""",
    "inbox": "python3 scripts/ticket.py inbox",
    "verify": "python3 scripts/ticket.py verify 7",
    "close": "python3 scripts/ticket.py close 7",
    "import": "python3 scripts/ticket.py import ~/somewhere/old-tickets",
    "freeze": ('python3 scripts/ticket.py freeze 7 \\\n'
               '  --reason "視覺方向未定" \\\n'
               '  --criterion "產出會不會因視覺方向改變而重做"'),
}


def known_flags(verb):
    """這個子指令認得哪幾個旗標。**從真正在用的那幾份表數出來**,不另抄一份 ——
    抄的那一份會跟事實分岔,而分岔的那天使用者看到的是一份說謊的 `--help`。"""
    if verb == "create":
        out = []
        blank = blank_ticket()
        for flag in sorted(set(CREATE_FLAGS) | set(CREATE_REPEATED)):
            field = CREATE_FLAGS.get(flag) or CREATE_REPEATED[flag]
            # 標 [必填] 的判準是「不給就開不出票」,不是「schema 有這一格」:
            # `--role` / `--tool` / `--state` 空白票就有預設值,`--base-sha` 不給會
            # 自己去取主線 —— 把這四個標成必填,新來的人會以為少一個就開不了票。
            need = (field in NOT_EMPTY and not blank.get(field)
                    and field not in AUTO_FILLED)
            out.append((flag, FLAG_NOTE.get(flag, "(還沒寫說明)"),
                        flag in CREATE_REPEATED, need))
        return out
    if verb == "list":
        return [(flag, FLAG_NOTE.get(flag, ""), False, False)
                for flag in ("--open", "--state X")]
    if verb == "freeze":
        return [(flag, FLAG_NOTE.get(flag, ""), False, True)
                for flag in ("--reason", "--criterion")]
    return []


def flag_names(verb):
    return [row[0] for row in known_flags(verb)]


def help_for(verb, out=sys.stdout):
    """一個子指令的說明:用法、認得的參數、一個**可以直接貼**的範例。

    為什麼範例要能直接貼:D-001 說規則住在程式裡,而**程式要自己說得出規則** ——
    一份只列得出參數名的說明,跟沒有說明的差別,是使用者要猜幾次才會對。
    (2026-09-12:有人照著猜 `--allowed-write-paths`,複數,開不了票。)
    """
    out.write("用法:%s\n" % USAGE.get(verb, "scripts/ticket.py %s" % verb))
    flags = known_flags(verb)
    if flags:
        out.write("\n認得的參數:\n")
        for flag, note, repeated, required in flags:
            marks = "".join(["  [必填]" if required else "",
                             "  [可重複]" if repeated else ""])
            out.write("  %-22s %s%s\n" % (flag, note, marks))
    example = EXAMPLE.get(verb)
    if example:
        out.write("\n例:\n%s\n" % example)
    if verb == "create":
        out.write("\n必填的那幾格在 tickets/SCHEMA.md;缺的話這支腳本會逐條說是哪一格。\n")
    return 0


def unknown_flag(verb, flag):
    """不認得的參數 —— **把認得的那幾個列出來**。「不認得 X」只說了它不是什麼。"""
    sys.stderr.write("ticket: %s 不認得 %r\n" % (verb, flag))
    names = flag_names(verb)
    if names:
        sys.stderr.write("ticket: %s 認得的是:%s\n" % (verb, "  ".join(names)))
    sys.stderr.write("ticket: 看範例:python3 scripts/ticket.py %s --help\n" % verb)
    return 2


def blank_ticket():
    return {"id": "", "subject": "", "created": "", "objective": "",
            "acceptance": [], "in_scope": [], "out_of_scope": [],
            "depends_on": [], "allowed_write_paths": [],
            "role": "worker", "model": "", "tool": "claude-code", "attempt": 0,
            "base_sha": "", "state": "Draft", "state_version": 1,
            "lease": None, "retry_limit": 2}


def normalise_depends(values):
    """`--depends-on 7` 與 `--depends-on 7:閘門綠` 都吃;schema 要的是
    `{id, condition}`。條件空著是誠實的「還沒寫」,不是「沒有條件」。"""
    out = []
    for raw in values:
        if isinstance(raw, dict):
            out.append(raw)
            continue
        ident, _, condition = str(raw).lstrip("#").partition(":")
        out.append({"id": ident.strip(), "condition": condition.strip()})
    return out


def normalise_verify(values):
    """`--verify-string 'path:那串字'` → `{path, contains}`;沒有冒號就是「在
    allowed_write_paths 底下任一個檔裡找這串字」。"""
    out = []
    for raw in values:
        if isinstance(raw, dict):
            out.append(raw)
            continue
        text = str(raw)
        path, sep, needle = text.partition(":")
        if sep and needle.strip():
            out.append({"path": path.strip(), "contains": needle})
        else:
            out.append(text)
    return out


def ask_interactive(ticket, stdin, stdout):
    for field, prompt, many in ASK:
        if many:
            stdout.write("%s:\n" % prompt)
            stdout.flush()
            values = []
            while True:
                line = stdin.readline()
                if not line or not line.strip():
                    break
                values.append(line.strip())
            ticket[field] = values
        else:
            stdout.write("%s: " % prompt)
            stdout.flush()
            line = stdin.readline()
            ticket[field] = (line or "").strip()
    ticket["depends_on"] = normalise_depends(ticket["depends_on"])


def head_sha(branch):
    done = git(["rev-parse", "--verify", "-q", "%s^{commit}" % branch])
    return done.stdout.strip() if done.returncode == 0 else ""


def cmd_create(argv, stdin=sys.stdin, stdout=sys.stdout):
    ticket = blank_ticket()
    index = 0
    interactive = not argv
    while index < len(argv):
        flag = argv[index]
        if flag in CREATE_FLAGS or flag in CREATE_REPEATED:
            index += 1
            if index >= len(argv):
                sys.stderr.write("ticket: %s 少了值\n" % flag)
                return 2
            if flag in CREATE_FLAGS:
                ticket[CREATE_FLAGS[flag]] = argv[index]
            else:
                ticket.setdefault(CREATE_REPEATED[flag], []).append(argv[index])
        else:
            return unknown_flag("create", flag)
        index += 1
    if interactive:
        ask_interactive(ticket, stdin, stdout)
    ticket["depends_on"] = normalise_depends(ticket.get("depends_on") or [])
    if ticket.get("verify_strings"):
        ticket["verify_strings"] = normalise_verify(ticket["verify_strings"])
    ticket["id"] = next_id()
    ticket["created"] = now()
    if not ticket.get("base_sha"):
        # 派工那一刻的主線 sha。閘門的綠只對它有效(SCHEMA §版本),所以寧可自己
        # 去問一次,也不要留空 —— 留空的那一格看起來跟「還沒決定」一樣。
        ticket["base_sha"] = head_sha(main_branch())
    bad = missing_fields(ticket)
    if bad:
        sys.stderr.write("ticket: 這張票還開不了工,缺:\n")
        for line in bad:
            sys.stderr.write("  %s\n" % line)
        sys.stderr.write("(契約見 tickets/SCHEMA.md)\n")
        return 2
    path = save(ticket)
    event.emit("ticket.created", ticket=ticket["id"], role=ticket["role"],
               model=ticket["model"], state=ticket["state"])
    stdout.write("ticket: #%s 開好了 -> %s\n"
                 % (ticket["id"], os.path.relpath(path, root())))
    return 0


# ---------------------------------------------------------------- list / show


def is_open(ticket):
    return ticket.get("state") not in CLOSED_STATES


def cmd_list(argv):
    for index, arg in enumerate(argv):
        if arg.startswith("--") and arg != "--open" and arg != "--state" \
                and (index == 0 or argv[index - 1] != "--state"):
            return unknown_flag("list", arg)
    want_open = "--open" in argv
    state = None
    if "--state" in argv:
        at = argv.index("--state")
        if at + 1 >= len(argv):
            sys.stderr.write("ticket: --state 少了值\n")
            return 2
        state = argv[at + 1]
        if state not in STATES:
            sys.stderr.write("ticket: 沒有 %r 這個狀態(%s)\n" % (state, "/".join(STATES)))
            return 2
    rows = load_all()
    if want_open:
        rows = [t for t in rows if is_open(t)]
    if state:
        rows = [t for t in rows if t.get("state") == state]
    if not rows:
        # 「一張都沒有」要說出來 —— 空輸出跟「跑起來了、都過」長得一樣。
        sys.stdout.write("ticket: 沒有符合的票\n")
        return 0
    for ticket in rows:
        frozen = " ❄%s" % ticket["frozen"].get("reason", "") if ticket.get("frozen") else ""
        sys.stdout.write("#%-4s %-17s v%-3s %s%s\n"
                         % (ticket["id"], ticket.get("state", "?"),
                            ticket.get("state_version", "?"),
                            ticket.get("subject", ""), frozen))
    return 0


def cmd_show(argv):
    if not argv:
        sys.stderr.write("ticket: show 要票號\n")
        return 2
    try:
        ticket = load(argv[0].lstrip("#"))
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (argv[0], exc))
        return 2
    sys.stdout.write(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n")
    return 0


# --------------------------------------------------------------------- set


def parse_value(field, raw):
    """吃得懂 JSON 就當 JSON(`["a","b"]`、`3`、`null`),不然就是一串字。

    list 的欄位給一串字會被包成單元素 list —— 比靜靜存成字串好:一個字串形狀的
    `allowed_write_paths` 會讓 land 的 glob 比對逐字元跑過去,而它不會出聲。
    """
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    if field in LIST_FIELDS and not isinstance(value, list):
        value = [value]
    if field == "depends_on":
        value = normalise_depends(value)
    if field == "verify_strings":
        value = normalise_verify(value)
    return value


def cmd_set(argv):
    if len(argv) < 3:
        sys.stderr.write("ticket: set <id> <field> <value>\n")
        return 2
    ident, field, raw = argv[0].lstrip("#"), argv[1], argv[2]
    try:
        ticket = load(ident)
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
        return 2
    if field in ("id", "state_version"):
        sys.stderr.write("ticket: %s 不給改(id 是身分,state_version 是這支腳本自己數的)\n" % field)
        return 2
    value = parse_value(field, raw)
    if field == "state" and value not in STATES:
        sys.stderr.write("ticket: 沒有 %r 這個狀態(%s)\n" % (value, "/".join(STATES)))
        return 2
    before = ticket.get(field)
    ticket[field] = value
    # **每次變更 +1**,不管改的是哪一格:遲到的回報拿舊的 state_version 回來,
    # 對得上的才收(SCHEMA §執行)。只在改 state 時 +1 的版本擋不住「改了範圍、
    # 版本沒動」那一種。
    ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
    save(ticket)
    event.emit("ticket.state", ticket=ident, field=field,
               **{"from": json.dumps(before, ensure_ascii=False),
                  "to": json.dumps(value, ensure_ascii=False),
                  "state_version": ticket["state_version"]})
    sys.stdout.write("ticket: #%s %s: %s -> %s(state_version %d)\n"
                     % (ident, field, json.dumps(before, ensure_ascii=False),
                        json.dumps(value, ensure_ascii=False), ticket["state_version"]))
    return 0


# ------------------------------------------------------------------- freeze


def cmd_freeze(argv):
    if not argv:
        sys.stderr.write("ticket: freeze <id> --reason … --criterion …\n")
        return 2
    ident = argv[0].lstrip("#")
    reason = criterion = ""
    index = 1
    while index < len(argv):
        if argv[index] in ("--reason", "--criterion") and index + 1 < len(argv):
            if argv[index] == "--reason":
                reason = argv[index + 1]
            else:
                criterion = argv[index + 1]
            index += 2
            continue
        return unknown_flag("freeze", argv[index])
    if not reason or not criterion:
        # criterion 是「什麼時候可以解凍」。少了它,凍結會變成一張沒有人記得要回來
        # 看的票 —— 而那正是「凍著」與「忘了」長得一樣的形狀。
        sys.stderr.write("ticket: --reason 與 --criterion 兩格都要\n")
        return 2
    try:
        ticket = load(ident)
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
        return 2
    ticket["frozen"] = {"reason": reason, "criterion": criterion}
    ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
    save(ticket)
    event.emit("ticket.frozen", ticket=ident, note=reason, criterion=criterion,
               state_version=ticket["state_version"])
    sys.stdout.write("ticket: #%s 凍結 —— %s(解凍條件:%s)\n" % (ident, reason, criterion))
    return 0


# -------------------------------------------------------------------- inbox


def latest_answers():
    """`board/answers.jsonl`:同一張票**最後一筆為準**(改過三次的答案三次都留著,
    那份檔是「使用者說過什麼」的歷史)。"""
    latest = {}
    path = event.answers_path(root())
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ident = str(row.get("ticket") or "").strip()
                if ident:
                    latest[ident] = row
    except OSError:
        return {}
    return latest


def decided_tickets():
    """哪些票的答案**已經落成裁示**。

    判準就是 `docs/DECISIONS.md` 本身:那一列的來源提到這張票(`#7` 或 `t7`)。
    用那份檔去問,而不是另記一格「已處理」—— 另記的那一格會跟事實分岔,而分岔
    的那天沒有人會知道(同「一張要靠人記得更新的名單」)。
    """
    try:
        with open(os.path.join(root(), DECISIONS_REL), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return set()
    return set(re.findall(r"(?:#|\bt)(\d{1,6})\b", text))


def cmd_inbox(argv):
    del argv
    answers = latest_answers()
    decided = decided_tickets()
    tickets = {t["id"]: t for t in load_all()}
    waiting = [t for t in tickets.values() if t.get("state") == NEEDS_DECISION]
    pending = [(ident, row) for ident, row in sorted(answers.items())
               if ident not in decided]
    if not waiting and not pending:
        sys.stdout.write("ticket: 收件匣是空的(沒有等裁決的票,也沒有答了還沒落成裁示的)\n")
        return 0
    if waiting:
        sys.stdout.write("等使用者裁決(state=%s):\n" % NEEDS_DECISION)
        for ticket in waiting:
            answered = answers.get(ticket["id"])
            mark = " ← 使用者已經答了" if answered else ""
            sys.stdout.write("  #%-4s %s%s\n" % (ticket["id"], ticket.get("subject", ""), mark))
    if pending:
        sys.stdout.write("答了、還沒落成 %s 的一列:\n" % DECISIONS_REL)
        for ident, row in pending:
            subject = tickets.get(ident, {}).get("subject", "(沒有這張票)")
            first = str(row.get("answer") or "").strip().splitlines()
            sys.stdout.write("  #%-4s %s\n      %s:%s\n"
                             % (ident, subject, row.get("ts", "?"),
                                first[0] if first else "(空的)"))
    return 0


# ------------------------------------------------------------------- verify


def main_files(branch):
    """主線上有哪些檔。`-z` 不是效能參數,是正確性參數:不加它,非 ASCII 路徑會被
    逃逸印出來,而逃逸過的字串拿去比對會安靜地變成「掃不到」。"""
    done = git(["ls-tree", "-r", "-z", "--name-only", branch])
    if done.returncode != 0:
        return []
    return [name for name in done.stdout.split("\0") if name]


def matches_any(path, globs):
    for pattern in globs:
        if fnmatch.fnmatch(path, pattern) or path == pattern:
            return True
        # `scripts` 這種目錄形狀的 glob 要蓋住底下的檔
        if pattern and not pattern.endswith("*") and path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def verify(ident):
    """關票之前問一句:**那張票的改動,真的在主線上嗎?**

    理由是 2026-09-10 的一件事(來自前一個專案):`land.sh a b` 裡的 b 忘了
    `git commit`,而 a 有 commit,所以落地印的是「4 個 commit 串好」而不是 0 ——
    **一個徵兆都沒有**。全套綠、主線前進、那張票被關成完成,而**它的程式碼從來
    沒有進主線**。兩天後下一張票的實作者說「我的 base 裡沒有那張票」才浮出來。
    當時事後查證用的就是這一行:`git show main:<檔> | grep -c <那張票獨有的字串>`
    = 0。

    所以這一支把那一行變成關票的前置條件:
    - 票上有 `verify_strings` → 每一條都要在主線的那個檔裡抓到 **> 0** 次;
    - 沒有 → 退回弱檢查:`base_sha..main` 的改動檔裡,有幾個落在
      `allowed_write_paths` 裡。**弱**在於它認不出那幾個檔是不是這張票改的,
      所以輸出會明說。

    回傳 `(ok, 每一條的結果, 是不是弱檢查)`。
    """
    ticket = load(ident)
    branch = main_branch()
    # 手寫的票也吃得到 `路徑:那串字` 這個形狀 —— 正規化在讀的這一側做,不是只在
    # `create` 那一側做:票檔是人會直接編輯的東西(SCHEMA 的最小範例就是手寫的)。
    needles = normalise_verify(ticket.get("verify_strings") or [])
    globs = ticket.get("allowed_write_paths") or []
    rows = []
    if needles:
        tree = main_files(branch)
        for item in needles:
            if isinstance(item, dict):
                paths = [item.get("path", "")]
                needle = item.get("contains", "")
            else:
                paths = [p for p in tree if matches_any(p, globs)]
                needle = str(item)
            hits = 0
            looked = []
            for path in paths:
                if not path:
                    continue
                done = git(["show", "%s:%s" % (branch, path)])
                if done.returncode != 0:
                    looked.append("%s(主線上沒有這個檔)" % path)
                    continue
                found = sum(1 for line in done.stdout.splitlines() if needle in line)
                hits += found
                if found:
                    looked.append("%s×%d" % (path, found))
            rows.append({"what": needle, "hits": hits, "where": looked})
        return all(row["hits"] > 0 for row in rows), rows, False
    base = ticket.get("base_sha") or ""
    if not base:
        return False, [{"what": "base_sha", "hits": 0,
                        "where": ["票上沒有 base_sha,弱檢查也做不了"]}], True
    done = git(["diff", "--name-only", "%s..%s" % (base, branch)])
    if done.returncode != 0:
        return False, [{"what": "git diff", "hits": 0,
                        "where": [(done.stderr or done.stdout).strip()]}], True
    touched = [name for name in done.stdout.splitlines()
               if name and matches_any(name, globs)]
    rows.append({"what": "%s..%s 落在 allowed_write_paths 裡的改動檔" % (base[:12], branch),
                 "hits": len(touched), "where": touched})
    return len(touched) > 0, rows, True


def print_verify(ident, ok, rows, weak):
    for row in rows:
        mark = "OK " if row["hits"] > 0 else "零 "
        sys.stdout.write("  %s%s —— %d(%s)\n"
                         % (mark, row["what"], row["hits"],
                            ", ".join(row["where"]) or "哪裡都沒有"))
    if weak:
        sys.stdout.write("  (弱檢查:票上沒有 verify_strings,這一問答不出「那幾個檔是不是"
                         "這張票改的」。要關票就補一條 verify_strings。)\n")
    sys.stdout.write("ticket: #%s %s\n" % (ident, "在主線上" if ok else "**不在主線上**"))


def cmd_verify(argv):
    if not argv:
        sys.stderr.write("ticket: verify <id>\n")
        return 2
    ident = argv[0].lstrip("#")
    try:
        ok, rows, weak = verify(ident)
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
        return 2
    print_verify(ident, ok, rows, weak)
    return 0 if ok else 1


def cmd_close(argv):
    if not argv:
        sys.stderr.write("ticket: close <id>\n")
        return 2
    ident = argv[0].lstrip("#")
    try:
        ticket = load(ident)
        ok, rows, weak = verify(ident)
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
        return 2
    print_verify(ident, ok, rows, weak)
    if not ok:
        sys.stdout.write("ticket: #%s 沒關 —— 主線上抓不到這張票的東西。"
                         "exit code 0 不等於 Done,worker 說做完也不等於 Done"
                         "(docs/WORKFLOW.md)。\n" % ident)
        return 1
    ticket["state"] = "Done"
    ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
    save(ticket)
    event.emit("ticket.closed", ticket=ident,
               hits=sum(row["hits"] for row in rows),
               weak=1 if weak else 0, state_version=ticket["state_version"])
    sys.stdout.write("ticket: #%s -> Done(state_version %d)\n"
                     % (ident, ticket["state_version"]))
    return 0


# ------------------------------------------------------------------- import


LEGACY_STATE = {"completed": "Done", "in_progress": "Running",
                "pending": "Draft", "cancelled": "Cancelled"}


def convert(old):
    """舊票 → 這份 schema。**缺的欄位留空並標 `legacy: true`**。

    留空而不是猜:一張補了假 `base_sha` 的舊票,跟一張真的記過 base 的票長得一樣,
    而落地器會拿它去問「這個基準還是主線的祖先嗎」—— 猜來的答案會是一句假話。
    `legacy` 那一格就是給落地器看的:這張票的空格是歷史,不是有人偷懶。
    """
    ticket = blank_ticket()
    ticket["id"] = str(old.get("id") or "")
    ticket["subject"] = str(old.get("subject") or "")
    phases = old.get("phases") or {}
    ticket["created"] = str(phases.get("created") or "")
    ticket["objective"] = ""
    ticket["outline"] = str(old.get("description") or "")
    ticket["depends_on"] = normalise_depends(old.get("blockedBy") or [])
    ticket["state"] = LEGACY_STATE.get(str(old.get("status") or ""), "Draft")
    ticket["role"] = ""
    ticket["model"] = ""
    ticket["tool"] = ""
    ticket["legacy"] = True
    ticket["legacy_blocks"] = [str(x) for x in (old.get("blocks") or [])]
    ticket["legacy_phases"] = phases
    return ticket


def cmd_import(argv):
    if not argv:
        sys.stderr.write("ticket: import <舊票目錄>\n")
        return 2
    where = os.path.abspath(os.path.expanduser(argv[0]))
    try:
        names = sorted(name for name in os.listdir(where) if name.endswith(".json"))
    except OSError as exc:
        sys.stderr.write("ticket: 讀不到 %s —— %s\n" % (where, exc))
        return 2
    made = skipped = broken = 0
    for name in names:
        try:
            with open(os.path.join(where, name), encoding="utf-8") as handle:
                old = json.load(handle)
        except (OSError, ValueError):
            broken += 1
            continue
        if not isinstance(old, dict) or not old.get("id"):
            broken += 1
            continue
        ticket = convert(old)
        if os.path.exists(ticket_path(ticket["id"])):
            skipped += 1
            continue
        save(ticket)
        event.emit("ticket.created", ticket=ticket["id"], legacy=1)
        made += 1
    # 三個數字分開講:「跳過」是已經有了,「讀不懂」是這裡有東西沒進來 ——
    # 揉成一句「匯入 N 張」會讓後者看起來像沒發生。
    sys.stdout.write("ticket: 匯入 %d 張;已經有了跳過 %d 張;讀不懂 %d 個檔\n"
                     % (made, skipped, broken))
    return 0 if broken == 0 else 1


# --------------------------------------------------------------------- main


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    table = {"create": cmd_create, "list": cmd_list, "show": cmd_show,
             "set": cmd_set, "inbox": cmd_inbox, "verify": cmd_verify,
             "close": cmd_close, "import": cmd_import, "freeze": cmd_freeze}
    if verb in ("--help", "-h", "help"):
        if rest and rest[0] in table:
            return help_for(rest[0])
        sys.stdout.write(__doc__)
        return 0
    if verb not in table:
        sys.stderr.write("ticket: 不認得 %r(%s)\n" % (verb, " / ".join(sorted(table))))
        return 2
    if "--help" in rest or "-h" in rest:
        return help_for(verb)
    return table[verb](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
