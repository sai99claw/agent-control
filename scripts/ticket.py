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
    scripts/ticket.py close 7 --landed <merge sha> # 落地後一步:蓋 review.sha 再關
    scripts/ticket.py set 7 verify '{…}' --expect-state-version 4   # 過期的回報拒收
    scripts/ticket.py round 7 3 --red             # 第三輪仍紅 -> Blocked,指派主線
    scripts/ticket.py import <舊票目錄>           # 轉成這份 schema,缺的留空並標 legacy
    scripts/ticket.py freeze 7 --reason … --criterion …

**不要手改票檔**(`tickets/README.md`):`state_version` 是遲到的回報用來認出自己
過期的那一格,而手改不會動它。
"""

import errno
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
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
# 這幾格空著等於沒填:一張沒有驗收條件、沒有寫入範圍的票,排順序的人與落地器都讀不動。
NOT_EMPTY = ("subject", "objective", "acceptance", "allowed_write_paths",
             "role", "model", "tool", "base_sha", "state")
LIST_FIELDS = ("acceptance", "in_scope", "out_of_scope", "depends_on",
               "allowed_write_paths", "shared_resources", "decision_refs",
               "test_evidence", "attempt_history", "verify_strings",
               # 實作者的反駁(D-014):每筆 {category, body, evidence, owner,
               # disposition, blocking}。沒處置的阻擋項 land 會拒絕。
               "objections", "tags")

# 覆核算通過的幾種寫法,與「算已經處置」的幾種 disposition。表在這裡,不在提示裡。
REVIEW_PASS = ("pass", "approved", "ok", "通過")
DISPOSED = ("accepted", "rejected", "deferred", "fixed", "已處置")
# 這幾格是驗證者/開題者在落地**之後**補的(補一條 verify_strings、記一筆 waiver、
# 處置一筆反駁),不是重新審過一次票面 —— `set` 這幾格時把 review 的版本跟著蓋上去,
# 不讓它因此過期(#15)。
CARRY_REVIEW_FIELDS = ("verify_waiver", "verify_strings", "objections")
LOCK_NAME = ".ticket.lock"
LOCK_TIMEOUT = 10.0

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


class Lock(object):
    """票的寫入鎖。**`mkdir` 成功與否是原子的**;`if not exists: mkdir` 不是 ——
    兩個同時回寫 verify 與 review 的 session 會雙雙通過那個檢查,後寫的整份蓋掉先寫的
    (2026-09-21 外部審查:平行回寫可能互蓋)。

    一把鎖管整個票庫而不是一票一把:遲到的回報是拿 `state_version` 認出來的,而那一格
    是讀出來 +1 再寫回去的,所以「讀」與「寫」之間不准有別人。
    """

    def __init__(self, where=None, timeout=LOCK_TIMEOUT):
        self.path = os.path.join(where or event.tickets_dir(root()), LOCK_NAME)
        self.timeout = timeout
        self.held = False

    def __enter__(self):
        deadline = time.time() + self.timeout
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        while True:
            try:
                os.mkdir(self.path)
                self.held = True
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
                if time.time() >= deadline:
                    raise RuntimeError(
                        "票庫的寫入鎖拿不到(%s)—— 有別的 session 正在回寫,"
                        "或上一個死在半路。確定死了就 rmdir 它。" % self.path)
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.held:
            try:
                os.rmdir(self.path)
            except OSError:
                pass
        return False


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
    # `verify` 那一格是**驗證者**寫的(D-010):它交的不是判決,是「案例在哪、
    # 怎麼跑」。帶點的名字寫進巢狀的 `verify` 底下,見 `put_field`。
    "--verify-run": "verify.run", "--verify-note": "verify.notes",
}
CREATE_REPEATED = {
    "--acceptance": "acceptance", "--in-scope": "in_scope",
    "--out-of-scope": "out_of_scope", "--allowed-write-path": "allowed_write_paths",
    "--depends-on": "depends_on", "--decision-ref": "decision_refs",
    "--verify-string": "verify_strings", "--shared-resource": "shared_resources",
    "--verify-file": "verify.files", "--verify-tag": "verify.tags",
}
# 旗標 → 一句話說明。**名單不在這裡** —— `--help` 要印哪幾個是從 `CREATE_FLAGS` /
# `CREATE_REPEATED` 自己數出來的,這裡只補說明。兩份名單會分岔,一份不會。
FLAG_NOTE = {
    "--subject": "一句話的標題",
    "--objective": "做完了的樣子",
    "--acceptance": "驗收;**一條一個旗標**,每條要能寫成一條會紅的斷言",
    "--in-scope": "範圍內的檔;一個一個給",
    "--out-of-scope": "明說不要動的;一個一個給",
    "--allowed-write-path": "允許寫入的路徑 glob;**一個一個給**(排順序判平行、落地器判越界,讀的都是這一格)",
    "--depends-on": "前置票號;`7` 或 `7:閘門綠`;一個一個給",
    "--decision-ref": "相關裁示編號(D-00x);一個一個給",
    "--verify-string": "關票時要在主線上抓到的字;`路徑:那串字` 或只給字;一個一個給",
    "--verify-file": "驗證者寫的回歸案例檔;一個一個給(進票的 `verify.files`)",
    "--verify-tag": "那幾個案例宣告的功能標籤;一個一個給(進票的 `verify.tags`)",
    "--verify-run": "怎麼跑那幾個案例(一句可以直接貼的指令)",
    "--verify-note": "跑的時候要知道的事(前置、已知 flaky、為什麼這樣驗)",
    "--shared-resource": "共用的執行資源(同一顆 DB、同一個埠);一個一個給",
    "--role": "角色:worker / verifier / opener / consolidator",
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
    "--expect-state-version": "這份回報是對票的哪一版說的;對不上就拒收(rc=4)",
    "--expect-attempt": "這份回報是第幾次派工的;對不上就拒收(rc=4)",
    "--red": "這一輪仍然紅(預設);第 retry_limit+1 輪仍紅 -> 轉 Blocked、指派主線",
    "--green": "這一輪綠了",
    "--landed": "落地後的合併 sha;先確認它在主線歷史裡,再蓋進 review.sha 一步關票",
}

ASK = (
    ("subject", "一句話的標題", False),
    ("objective", "目標(做完了的樣子)", False),
    ("acceptance", "驗收(每條要能寫成一條會紅的斷言;空行結束)", True),
    ("in_scope", "範圍內的檔(空行結束)", True),
    ("out_of_scope", "明說不要動的(空行結束)", True),
    ("allowed_write_paths", "允許寫入的路徑 glob(空行結束)", True),
    ("depends_on", "前置票號(空行結束)", True),
    ("role", "角色(worker / verifier / opener / …)", False),
    ("model", "模型", False),
    ("tool", "工具(claude-code / codex / …)", False),
)


AUTO_FILLED = ("base_sha",)      # 不給就自己去問主線,所以它不是「必填」

USAGE = {
    "create": "scripts/ticket.py create [旗標…]        # 一個旗標都不給就一格一格問",
    "list": "scripts/ticket.py list [--open | --state <狀態>]",
    "show": "scripts/ticket.py show <id>",
    "set": ("scripts/ticket.py set <id> <欄位> <值> "
            "[--expect-state-version N] [--expect-attempt N]"),
    "inbox": "scripts/ticket.py inbox",
    "verify": "scripts/ticket.py verify <id>",
    "close": "scripts/ticket.py close <id> [--landed <merge sha>]  # 先 verify,>0 才准關",
    "import": "scripts/ticket.py import <舊票目錄>",
    "freeze": "scripts/ticket.py freeze <id> --reason … --criterion …",
    "round": "scripts/ticket.py round <id> <第幾輪> [--red|--green]",
    "result": ("scripts/ticket.py result <EVIDENCE> <輸出.json> "
               "--ticket <id> --role worker|verifier --round N"),
    "objection": ("scripts/ticket.py objection <id> --line \"OBJECTION: …\" "
                  "--evidence <EVIDENCE>"),
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
    "close": "python3 scripts/ticket.py close 7\n"
             "python3 scripts/ticket.py close 7 --landed a1b2c3d",
    "import": "python3 scripts/ticket.py import ~/somewhere/old-tickets",
    "freeze": ('python3 scripts/ticket.py freeze 7 \\\n'
               '  --reason "視覺方向未定" \\\n'
               '  --criterion "產出會不會因視覺方向改變而重做"'),
    "round": "python3 scripts/ticket.py round 7 3 --red",
    "result": ("python3 scripts/ticket.py result EVIDENCE-round2.md \\\n"
               "  reports/t7/20260923-101500-1/result-round2.json \\\n"
               "  --ticket 7 --role worker --round 2"),
    "objection": ('python3 scripts/ticket.py objection 7 \\\n'
                  '  --line "OBJECTION: ticket-wrong 驗收 A3 指的欄位不存在" \\\n'
                  '  --evidence reports/t7/EVIDENCE.md'),
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
    if verb == "set":
        return [(flag, FLAG_NOTE.get(flag, ""), False, False)
                for flag in ("--expect-state-version", "--expect-attempt")]
    if verb == "round":
        return [(flag, FLAG_NOTE.get(flag, ""), False, False)
                for flag in ("--red", "--green")]
    if verb == "close":
        return [("--landed", FLAG_NOTE.get("--landed", ""), False, False)]
    if verb == "result":
        return [("--ticket", "票號", False, True),
                ("--role", "worker 或 verifier", False, True),
                ("--round", "第幾輪", False, True)]
    if verb == "objection":
        return [("--line", "EVIDENCE 裡那一行 `OBJECTION: <類別> <理由>`", False, True),
                ("--evidence", "那份 EVIDENCE 的路徑(記進票面當證據)", False, True)]
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
            "lease": None, "retry_limit": 2,
            # 驗證者的交付落在這一格(D-010):案例檔、標籤、怎麼跑、要知道的事。
            # **空著是誠實的「還沒有人寫案例」**,不是「這張票不用驗」。
            "verify": {"files": [], "tags": [], "run": "", "notes": ""}}


def put_field(ticket, field, value, repeated=False):
    """`a.b` 寫進巢狀的字典。**只准一層** —— 再深就是把一份 schema 塞進旗標裡,
    那時該改的是 `tickets/SCHEMA.md`,不是這裡。"""
    head, dot, tail = field.partition(".")
    if not dot:
        if repeated:
            ticket.setdefault(field, []).append(value)
        else:
            ticket[field] = value
        return
    nest = ticket.get(head)
    if not isinstance(nest, dict):
        nest = {}
        ticket[head] = nest
    if repeated:
        nest.setdefault(tail, []).append(value)
    else:
        nest[tail] = value


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
                put_field(ticket, CREATE_FLAGS[flag], argv[index])
            else:
                put_field(ticket, CREATE_REPEATED[flag], argv[index], repeated=True)
        else:
            return unknown_flag("create", flag)
        index += 1
    if interactive:
        ask_interactive(ticket, stdin, stdout)
    ticket["depends_on"] = normalise_depends(ticket.get("depends_on") or [])
    if not ticket.get("allowed_write_paths") and ticket.get("in_scope"):
        ticket["allowed_write_paths"] = list(ticket["in_scope"])
        if "tests/*" not in ticket["allowed_write_paths"]:
            ticket["allowed_write_paths"].append("tests/*")
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


def take_expected(argv):
    """`--expect-state-version N` / `--expect-attempt N` 從參數裡挑出來。

    SCHEMA 早就宣稱「遲到的回報對不上 attempt 就拒絕」,而舊版的 `set` 讀出來直接
    覆寫 —— **宣稱與實作分岔的那一格,看起來與有守衛的那一格一模一樣**
    (2026-09-21 外部審查)。
    """
    rest, want = [], {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag in ("--expect-state-version", "--expect-attempt"):
            index += 1
            if index >= len(argv):
                raise ValueError("%s 少了值" % flag)
            want[flag] = argv[index]
        else:
            rest.append(flag)
        index += 1
    return rest, want


def stale(ticket, want):
    """過期的交付:回傳一句話,或空字串。"""
    if "--expect-state-version" in want:
        got = str(ticket.get("state_version"))
        if got != str(want["--expect-state-version"]):
            return ("這份回報是對票 v%s 說的,票現在是 v%s —— 中間有人改過"
                    % (want["--expect-state-version"], got))
    if "--expect-attempt" in want:
        got = str(ticket.get("attempt"))
        if got != str(want["--expect-attempt"]):
            return ("這份回報是第 %s 次派工的,票現在在第 %s 次 —— 遲到了"
                    % (want["--expect-attempt"], got))
    return ""


def cmd_set(argv):
    try:
        argv, want = take_expected(argv)
    except ValueError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 2
    if len(argv) < 3:
        sys.stderr.write("ticket: set <id> <field> <value> "
                         "[--expect-state-version N] [--expect-attempt N]\n")
        return 2
    ident, field, raw = argv[0].lstrip("#"), argv[1], argv[2]
    if field in ("id", "state_version"):
        sys.stderr.write("ticket: %s 不給改(id 是身分,state_version 是這支腳本自己數的)\n" % field)
        return 2
    try:
        value = parse_value(field, raw)
    except ValueError as exc:
        sys.stderr.write("ticket: 讀不懂這個值 —— %s\n" % exc)
        return 2
    if field == "state" and value not in STATES:
        sys.stderr.write("ticket: 沒有 %r 這個狀態(%s)\n" % (value, "/".join(STATES)))
        return 2
    # 讀、比對、寫回**在同一把鎖裡**:中間放別人進來,`state_version` 就會有兩個人
    # 同時讀到 v3、各自寫回 v4,而後寫的那一份整份蓋掉先寫的。
    try:
        with Lock():
            try:
                ticket = load(ident)
            except (OSError, ValueError) as exc:
                sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
                return 2
            late = stale(ticket, want)
            if late:
                sys.stderr.write("ticket: #%s 這一筆不收 —— %s\n" % (ident, late))
                return 4
            if field == "state" and value == "Done":
                missing = done_blockers(ticket)
                if missing:
                    print_done_blockers(ident, missing)
                    return 1
            before = ticket.get(field)
            ticket[field] = value
            # **每次變更 +1**,不管改的是哪一格:遲到的回報拿舊的 state_version 回來,
            # 對得上的才收(SCHEMA §執行)。只在改 state 時 +1 的版本擋不住「改了範圍、
            # 版本沒動」那一種。
            ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
            if field == "review" and isinstance(value, dict):
                # 覆核**綁在它覆核的那個版本上**。蓋章的當下就把版本寫進去,之後任何
                # 一次 `set` 都會讓票往前一版,而 land 一比就知道這張章過期了。
                value.setdefault("at", now())
                value["state_version"] = ticket["state_version"]
            # #15:補 waiver / verify_strings / 處置 objections 是**落地之後的收尾**,
            # 不是重審票面 —— 既有的 review 跟著蓋到新版本,不因為這幾格被改就過期。
            # 其他欄位仍然照舊規矩讓 review 過期(state_version 對不上就是對不上)。
            carried = False
            if field in CARRY_REVIEW_FIELDS and isinstance(ticket.get("review"), dict):
                ticket["review"]["state_version"] = ticket["state_version"]
                carried = True
            save(ticket)
    except RuntimeError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 5
    event.emit("ticket.state", ticket=ident, field=field,
               **{"from": json.dumps(before, ensure_ascii=False),
                  "to": json.dumps(value, ensure_ascii=False),
                  "state_version": ticket["state_version"],
                  "review_carried": 1 if carried else None})
    sys.stdout.write("ticket: #%s %s: %s -> %s(state_version %d)\n"
                     % (ident, field, json.dumps(before, ensure_ascii=False),
                        json.dumps(value, ensure_ascii=False), ticket["state_version"]))
    if carried:
        sys.stdout.write("ticket: #%s 的 review 跟著蓋到 v%d(這格不算改票面,#15)\n"
                         % (ident, ticket["state_version"]))
    return 0


# --------------------------------------------------------- 進 Done 的必要條件


def review_problems(ticket, tip=""):
    """覆核那一格過不過得了。`tip` 給了就一起比分支 / patch 的 sha。"""
    out = []
    review = ticket.get("review")
    if not isinstance(review, dict) or not review.get("verdict"):
        return ["沒有 review —— 覆核是主線讀 patch 記進票的那一格"]
    if str(review.get("verdict")).lower() not in REVIEW_PASS:
        out.append("review.verdict=%r 不是通過" % review.get("verdict"))
    bound = review.get("state_version")
    if bound is None:
        out.append("review 沒有綁票版本(state_version)—— 票改過之後它還是長得有效")
    elif str(bound) != str(ticket.get("state_version")):
        out.append("review 綁的是票 v%s,票現在是 v%s —— 覆核之後票被改過"
                   % (bound, ticket.get("state_version")))
    sha = str(review.get("sha") or "")
    if not sha:
        out.append("review 沒有綁最終 patch / 分支的 sha")
    elif tip and not (tip.startswith(sha) or sha.startswith(tip)):
        out.append("review 綁的是 %s,現在的頭是 %s" % (sha[:12], tip[:12]))
    return out


def objection_problems(ticket):
    out = []
    for index, row in enumerate(ticket.get("objections") or []):
        if not isinstance(row, dict):
            out.append("objections[%d] 不是 {category, body, evidence, owner, disposition}"
                       % index)
            continue
        blocking = row.get("blocking")
        if blocking is None:
            blocking = str(row.get("category") or "").lower() in ("blocking", "阻擋",
                                                                 "ticket-wrong")
        if blocking and str(row.get("disposition") or "").strip().lower() not in DISPOSED:
            out.append("反駁 objections[%d](%s / owner=%s)還沒處置:%s"
                       % (index, row.get("category") or "?", row.get("owner") or "沒人",
                          (row.get("body") or "")[:60]))
    return out


def sha_on_branch(sha, branch):
    """`sha` 是不是 `branch` 歷史裡的一個祖先(含它自己)。**用 `merge-base
    --is-ancestor`**,不是逐字比對分支的頭 —— 落地是合併(或 squash)出一個新 sha,
    review 蓋章時記的那個 sha 之後主線還會再往前走,逐字比頭那條路,票一過夜就過期
    (#15)。"""
    sha = str(sha or "").strip()
    if not sha:
        return False
    done = git(["merge-base", "--is-ancestor", sha, branch])
    return done.returncode == 0


def has_valid_waiver(ticket):
    """`verify_waiver` 有沒有把話說清楚(#8 開的那一格:`{by, reason}`)。**只問這一
    格自己是不是誠實地填好了**,不問「誰有資格免驗」—— land.sh 才是把關落地的那一道,
    這裡問的是關票。"""
    waiver = ticket.get("verify_waiver")
    if not isinstance(waiver, dict):
        return False
    return bool(str(waiver.get("by") or "").strip()) and bool(str(waiver.get("reason") or "").strip())


def waiver_covers_regression(ticket):
    """免驗只在**票上的 waiver 誠實** *且* **review 綁的 sha 真的在主線歷史裡**時才
    生效 —— 少了後半,一張隨口寫的 waiver 配一個從沒進過主線的 sha 也會通過
    (#15 acceptance①)。"""
    if not has_valid_waiver(ticket):
        return False
    review = ticket.get("review")
    if not isinstance(review, dict):
        return False
    return sha_on_branch(review.get("sha"), main_branch())


def done_blockers(ticket):
    """**進 Done 只有這一份必要條件**,`set state Done` 與 `close` 共用它。

    舊版兩條路各走各的:`set state Done` 只檢查狀態名對不對,`close` 只問「東西在不
    在主線」而且弱檢查也算過 —— 於是 Done 的契約有兩個不同的把關強度,而弱的那一個
    沒有人記得(2026-09-21 外部審查:Done 的契約可以繞過)。

    #15:票有誠實的 `verify_waiver` **且** review 綁的 sha 真的在主線歷史裡,才免
    `test_evidence` / `verify.baseline` 這一關 —— review 本身該不該過(§review_problems)
    與 objections 是否處置完,不受這格豁免。

    D-020:`verify.baseline` 是**兩段**寫同一格 —— `verify-case.py red`(驗證者)只證
    乾淨基底該紅(`stage="red"`),`verify-case.py check`(閘門)在實作者的 patch 進來
    時才證候選該綠(`stage="check"`)。**close 只認 check**:驗證者交件的那一趟說的是
    「這條案例真的在驗東西」,不是「這張票的東西真的做出來了」,而少了後半的票與做完
    的票在票面上長得一樣。
    """
    out = []
    if not waiver_covers_regression(ticket):
        plan = ticket.get("verify") if isinstance(ticket.get("verify"), dict) else {}
        baseline = plan.get("baseline") if isinstance(plan.get("baseline"), dict) else None
        if not ticket.get("test_evidence") and not baseline:
            out.append("沒有回歸證據:票上既沒有 test_evidence,verify 也沒有 baseline"
                       "(`scripts/verify-case.py check <票號>` 會寫那一格;"
                       "或補一條誠實的 verify_waiver{by,reason} 且 review.sha 在主線歷史裡)")
        elif baseline and not baseline.get("ok"):
            out.append("verify.baseline 說驗紅沒過:%s"
                       % (baseline.get("why") or "乾淨主線上沒有紅"))
        elif baseline and str(baseline.get("stage") or "") != "check":
            out.append("verify.baseline 的 stage 是 %s,還缺閘門那一趟 check —— "
                       "驗證者只量了「乾淨基底該紅」,候選該綠由閘門量"
                       "(`scripts/verify-case.py check <票號> --candidate <分支>`,D-020)"
                       % (baseline.get("stage") or "空的"))
    out.extend(review_problems(ticket))
    out.extend(objection_problems(ticket))
    return out


def on_main(sha):
    """這個 sha 在主線歷史裡嗎。取不到就回 False —— **答不出來不等於答是**。"""
    if not sha:
        return False
    done = git(["merge-base", "--is-ancestor", sha, main_branch()])
    return done.returncode == 0


def baseline_next_step(ticket):
    """落地後 `verify.baseline` 還缺那一趟時,**印一句可以直接貼的指令**。

    G7(#29 A7):`land.sh` 不自動補量,而 `ticket.py close` 擋下來的時候只說「缺
    baseline」—— 一個守衛給錯了下一步,比沒有守衛更糟(§5.7):那個人會自己去猜
    `--ref` 與 `--candidate` 該填什麼,而**對主線的頭量出來的「一條都沒紅」說的是
    這一趟量錯了地方**,不是案例是假的(#19)。所以兩端都寫死:`--ref` 是票的
    `base_sha`、`--candidate` 是覆核綁的那個 sha(它已經在主線歷史裡)。

    回 `None` = 沒有東西要說。**它不改退出碼**:補量是主線的下一步,不是關票的條件
    (條件在 `done_blockers()`)。
    """
    plan = ticket.get("verify") if isinstance(ticket.get("verify"), dict) else {}
    baseline = plan.get("baseline") if isinstance(plan.get("baseline"), dict) else None
    if baseline and baseline.get("ok") is True:
        return None
    review = ticket.get("review") if isinstance(ticket.get("review"), dict) else {}
    sha = str(review.get("sha") or "")
    base = str(ticket.get("base_sha") or "")
    if not sha or not base or not on_main(sha):
        return None
    return ("python3 scripts/verify-case.py check %s --ref %s --candidate %s"
            % (ticket.get("id"), base, sha))


def print_baseline_next_step(ident, ticket):
    line = baseline_next_step(ticket)
    if not line:
        return
    sys.stdout.write("ticket: #%s 落地後的補量還沒做(verify.baseline %s)—— 貼這一句:\n"
                     % (ident, "缺" if not (ticket.get("verify") or {}).get("baseline")
                        else "的 ok 不是 true"))
    sys.stdout.write("  %s\n" % line)


def print_done_blockers(ident, missing):
    sys.stdout.write("ticket: #%s 進不了 Done —— 還缺:\n" % ident)
    for line in missing:
        sys.stdout.write("  %s\n" % line)
    sys.stdout.write("(exit code 0 不等於 Done;worker 說做完也不等於 Done。"
                     "契約見 docs/WORKFLOW.md §票的狀態機)\n")


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


def print_verify_plan(ticket):
    """把驗證者寫進票的那一格印出來。**印「還沒有人寫」而不是印一片空白** ——
    一張沒人寫過案例的票,與一張案例寫好了的票,在空白的輸出上長得一樣
    (`docs/DISPATCH-TEMPLATE.md` §5.5)。"""
    plan = ticket.get("verify")
    if not isinstance(plan, dict):
        plan = {}
    files = plan.get("files") or []
    tags = plan.get("tags") or []
    run = (plan.get("run") or "").strip()
    notes = (plan.get("notes") or "").strip()
    sys.stdout.write("  回歸案例(票的 verify 欄,驗證者寫的):\n")
    if not (files or tags or run or notes):
        sys.stdout.write("    還沒有人寫 —— 驗證者交件時要填 files / tags / run\n")
        return
    sys.stdout.write("    files: %s\n" % (", ".join(files) or "(沒填)"))
    sys.stdout.write("    tags:  %s\n" % (", ".join(tags) or "(沒填)"))
    sys.stdout.write("    run:   %s\n"
                     % (run or "(沒填 —— 沒有這一句,別人得自己猜怎麼跑)"))
    if notes:
        sys.stdout.write("    notes: %s\n" % notes)


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
    try:
        print_verify_plan(load(ident))
    except (OSError, ValueError):
        pass
    return 0 if ok else 1


def take_landed(argv):
    """`--landed <merge sha>` 從參數裡挑出來,不在乎它出現在哪個位置。"""
    rest, landed = [], None
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag == "--landed":
            index += 1
            if index >= len(argv):
                raise ValueError("--landed 少了值")
            landed = argv[index]
        else:
            rest.append(flag)
        index += 1
    return rest, landed


def stamp_landed(ident, sha):
    """`close --landed <sha>`(#15):落地是合併出一個**新**的 sha,不是分支審過的
    那個頭 —— 先確認它真的在主線歷史裡,再把它蓋進 review.sha,一步做完「補審 + 關」。
    沒有既有 review 就開一張(verdict/by 給預設,主線隨時可以事後改)。"""
    branch = main_branch()
    if not sha_on_branch(sha, branch):
        sys.stdout.write("ticket: #%s 沒關 —— %s 不在主線 %s 的歷史裡"
                         "(git merge-base --is-ancestor 判的)\n"
                         % (ident, sha[:12], branch))
        return 1
    try:
        with Lock():
            try:
                ticket = load(ident)
            except (OSError, ValueError) as exc:
                sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
                return 2
            review = ticket.get("review")
            review = dict(review) if isinstance(review, dict) else {}
            before = json.dumps(ticket.get("review"), ensure_ascii=False)
            review.setdefault("verdict", "pass")
            review.setdefault("by", "main")
            review["sha"] = sha
            review["at"] = now()
            ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
            review["state_version"] = ticket["state_version"]
            ticket["review"] = review
            save(ticket)
    except RuntimeError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 5
    event.emit("ticket.state", ticket=ident, field="review",
               **{"from": before, "to": json.dumps(review, ensure_ascii=False),
                  "state_version": ticket["state_version"], "landed": sha[:12]})
    sys.stdout.write("ticket: #%s review.sha -> %s(--landed,已確認在主線歷史裡)\n"
                     % (ident, sha[:12]))
    return 0


def cmd_close(argv):
    try:
        argv, landed = take_landed(argv)
    except ValueError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 2
    if not argv:
        sys.stderr.write("ticket: close <id> [--landed <merge sha>]\n")
        return 2
    ident = argv[0].lstrip("#")
    if landed:
        rc = stamp_landed(ident, landed)
        if rc:
            return rc
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
    if weak:
        # **弱檢查不能自動關票**(2026-09-21 外部審查)。弱檢查答的是「這幾個檔被動
        # 過」,不是「這張票的東西在主線上」—— 而那正是 2026-09-10 那次事故的縫。
        sys.stdout.write("ticket: #%s 沒關 —— 只做得了弱檢查。補一條 verify_strings "
                         "再關(`ticket.py set %s verify_strings '<那串字>'`)。\n"
                         % (ident, ident))
        return 1
    print_baseline_next_step(ident, ticket)
    missing = done_blockers(ticket)
    if missing:
        print_done_blockers(ident, missing)
        return 1
    try:
        with Lock():
            ticket = load(ident)
            ticket["state"] = "Done"
            ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
            # 關票這一動自己也讓票往前一版,所以把章重蓋在新的版本上 —— 不然下一個人
            # 讀到的會是一張「覆核過期」的已完成票,而那句話是假的。
            if isinstance(ticket.get("review"), dict):
                ticket["review"]["state_version"] = ticket["state_version"]
            save(ticket)
    except RuntimeError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 5
    event.emit("ticket.closed", ticket=ident,
               hits=sum(row["hits"] for row in rows),
               weak=1 if weak else 0, state_version=ticket["state_version"])
    sys.stdout.write("ticket: #%s -> Done(state_version %d)\n"
                     % (ident, ticket["state_version"]))
    return 0


# --------------------------------------------------------------------- round


def post_inbox(ident, state, what, where):
    """終態寫一頁收件匣。**寫不出來要出聲但不擋票的狀態轉換** —— 把一個紀錄問題
    升級成一個交付問題,會讓票卡在原狀態,而那比沒有收件匣更糟。"""
    try:
        import inbox                                       # noqa: PLC0415
        argv = ["post", "--ticket", str(ident), "--kind", "ticket",
                "--state", state, "--what", what, "--where", where]
        import io
        keep = sys.stdout
        sys.stdout = io.StringIO()
        try:
            inbox.main(argv)
        finally:
            sys.stdout = keep
    except Exception:                                      # noqa: BLE001
        sys.stderr.write("ticket: 收件匣寫不出來(票的狀態還是改好了)\n")


def cmd_round(argv):
    """修復迴圈的一輪結束了。**三輪耗盡不是一句話,是一個狀態轉換。**

    舊規則只寫「三輪仍紅就報主線」—— 而「報了」與「沒報」在票上長得一樣,票還停在
    Running,沒有人是它的 owner(2026-09-21 外部審查:三輪失敗可能只被看見)。
    """
    rest = [x for x in argv if not x.startswith("--")]
    if len(rest) < 2:
        sys.stderr.write("ticket: round <id> <第幾輪> [--red|--green]\n")
        return 2
    ident = rest[0].lstrip("#")
    try:
        number = int(rest[1])
    except ValueError:
        sys.stderr.write("ticket: 第幾輪要是數字\n")
        return 2
    red = "--green" not in argv
    try:
        with Lock():
            ticket = load(ident)
            ticket["repair_round"] = number
            limit = int(ticket.get("retry_limit") or 2) + 1
            exhausted = red and number >= limit
            if exhausted:
                ticket["state"] = "Blocked"
                ticket["owner"] = "main"
            ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
            save(ticket)
    except (OSError, ValueError) as exc:
        sys.stderr.write("ticket: 讀不到 #%s —— %s\n" % (ident, exc))
        return 2
    except RuntimeError as exc:
        sys.stderr.write("ticket: %s\n" % exc)
        return 5
    if exhausted:
        event.emit("ticket.attempt.failed", ticket=ident, attempt=number,
                   note="第 %d 輪仍紅,上限 %d —— 票轉 Blocked,owner=main" % (number, limit))
        event.emit("ticket.state", ticket=ident, field="state",
                   **{"from": "Running", "to": "Blocked",
                      "state_version": ticket["state_version"]})
        # 轉 Blocked 是一個**終態**,所以它要去叫醒主線(D-015)。
        # 「報了」與「沒報」以前在票上長得一樣;現在收件匣裡有一頁寫著要它做什麼。
        post_inbox(ident, "三輪耗盡,票轉 Blocked、owner=main",
                   "這張票由你接手:讀紅榜決定要改票面、換做法,還是拆票",
                   "reports/t%s/ 最新那一輪的 status.json" % ident)
        sys.stdout.write("ticket: #%s 第 %d 輪仍紅(上限 %d)—— 轉 Blocked,"
                         "指派主線(state_version %d)\n"
                         % (ident, number, limit, ticket["state_version"]))
        return 0
    sys.stdout.write("ticket: #%s 記下第 %d 輪 %s(state_version %d)\n"
                     % (ident, number, "紅" if red else "綠", ticket["state_version"]))
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


# ------------------------------------------------- EVIDENCE 尾端那一塊 result

# `## result` 那一塊怎麼開頭、怎麼收尾。**最後**一塊才算:前面幾塊可能是規則包裡
# 引用的範例,而尾端那一塊才是這一輪交的(`docs/DISPATCH-TEMPLATE.md` §8.5)。
RESULT_OPEN = re.compile(r"^\s*(?:`{3,}|~{3,})[ \t]*result[ \t]*$")
RESULT_CLOSE = re.compile(r"^\s*(?:`{3,}|~{3,})[ \t]*$")
RESULT_RAW_CAP = 500
OBJECTION_CATEGORIES = ("ticket-wrong", "test_defect", "blocking")
# 五段散文在不在 —— **人版**與機器版分開記(`tickets/SCHEMA.md` §result)。關鍵詞取
# 角色卡那五段的標題字,一段給幾個同義詞:只認一個詞的話,換一種寫法就變成「沒交」,
# 而**誤報缺段會讓人去補一段已經在那裡的東西**,比漏報更吵。
RESULT_SECTIONS = (
    ("patch_sha256", ("sha256", "SHA256")),
    ("gate", ("閘門", "Ran ", "rc=")),
    ("mutations", ("變異",)),
    ("excluded", ("排除的假設", "已排除")),
    ("repro", ("最小重現", "重現指令", "重現:")),
)


def result_block(lines):
    """EVIDENCE 裡**最後**那一塊 ```result 的內文;一塊都沒有回 `None`。"""
    start = None
    for index, line in enumerate(lines):
        if RESULT_OPEN.match(line):
            start = index
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        if RESULT_CLOSE.match(line):
            break
        body.append(line)
    return "\n".join(body)


def objection_line(lines):
    """EVIDENCE 裡第一行 `OBJECTION:`(沒有就回空字串)。"""
    for line in lines:
        if line.startswith("OBJECTION:"):
            return line
    return ""


def objection_parts(line):
    """`OBJECTION: <類別> <一句話>` → `(類別, 理由)`。類別不在表上就當 `ticket-wrong`
    —— 拼錯的類別與沒有反駁長得一樣,而當成最重的那一種至少會有人看。"""
    rest = line.split(":", 1)[1].strip() if ":" in line else line.strip()
    parts = rest.split(None, 1)
    category = parts[0] if parts and parts[0] in OBJECTION_CATEGORIES else "ticket-wrong"
    body = parts[1] if len(parts) > 1 else rest
    return category, body


def result_sections(text):
    """五段散文在不在 —— 回一個 `{段名: bool}`。"""
    return {name: any(word in text for word in words)
            for name, words in RESULT_SECTIONS}


def harvest_result(evidence, out, role, rnd, ident):
    """把 EVIDENCE 尾端那一塊抽成 `result-round<輪>.json`。**只讀 EVIDENCE,不改它。**

    這一支是 `auto-fix.sh`(第 2 輪起)與 `apply.sh`(第 1 輪)**共用的那一支**
    (#29 A4)。以前抽取只寫在 `auto-fix.sh` 的一段 heredoc 裡,於是走 `apply.sh` 的
    第一輪永遠沒有 `result-round1.json` —— 而看板對那一輪只印得出「沒交結構化輸出」,
    與真的沒交長得一樣。兩邊各抄一份的那一天,兩份會往不同方向漂(D-018)。

    **三種缺漏各有各的樣子**:沒有 EVIDENCE(`no-evidence`)、有 EVIDENCE 但沒有那一塊
    (`no-block`)、有那一塊但 JSON 解不開(`bad-json`,原文前 500 字留在 `raw`)。
    揉成同一個空檔的那一刻,「沒交」與「交了但都是空的」長得一樣。
    """
    miss = {"present": False, "ticket": str(ident), "role": role,
            "round": int(rnd), "evidence": os.path.basename(evidence)}
    try:
        with open(evidence, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        data = dict(miss, reason="no-evidence")
        text = ""
        lines = []
    else:
        lines = text.splitlines()
        raw = result_block(lines)
        if raw is None:
            data = dict(miss, reason="no-block")
        else:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                data = dict(miss, reason="bad-json", raw=raw[:RESULT_RAW_CAP])
            else:
                data = dict(parsed)
                data["present"] = True
                said = parsed.get("objection")
                said = said.get("category") if isinstance(said, dict) else None
                here = objection_line(lines)
                mine = objection_parts(here)[0] if here else None
                data["conflict"] = (mine or None) != (said or None)
    data["sections"] = result_sections(text) if text else {
        name: False for name, _words in RESULT_SECTIONS}
    where = os.path.dirname(out)
    if where:
        os.makedirs(where, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return data


def cmd_result(argv):
    if len(argv) < 2:
        sys.stderr.write("ticket: %s\n" % USAGE["result"])
        return 2
    evidence, out = argv[0], argv[1]
    ident, role, rnd = "", "worker", "1"
    index = 2
    while index < len(argv):
        flag = argv[index]
        if flag in ("--ticket", "--role", "--round") and index + 1 < len(argv):
            value = argv[index + 1]
            if flag == "--ticket":
                ident = value
            elif flag == "--role":
                role = value
            else:
                rnd = value
            index += 2
            continue
        return unknown_flag("result", flag)
    try:
        number = int(rnd)
    except ValueError:
        sys.stderr.write("ticket: --round 要是數字\n")
        return 2
    data = harvest_result(evidence, out, role, number, ident)
    if not data.get("present"):
        sys.stdout.write("ticket: result 抽不出來(%s)—— %s;不擋流程\n"
                         % (data.get("reason") or "?", evidence))
    # **缺段印出來、不擋流程**:五段散文是給下一輪那個新的人的,少一段他就得把
    # 你查過的路再查一次(`docs/DISPATCH-TEMPLATE.md` §8 第 5、6 點)。
    gone = [name for name, ok in sorted((data.get("sections") or {}).items()) if not ok]
    if gone:
        sys.stdout.write("ticket: EVIDENCE 少了這幾段:%s(不擋;見角色卡「必備五段」)\n"
                         % "、".join(gone))
    sys.stdout.write("ticket: result -> %s\n" % out)
    return 0


# ------------------------------------------------------------------ objection


def record_objection(ident, line, evidence):
    """把 EVIDENCE 裡那一行 `OBJECTION:` 記成票的 `objections[]` 一筆。

    **記過就不再記第二次**:`auto-fix.sh` 在叫 `apply.sh` 之前就先收過(`test_defect`
    那條路還會續跑),`apply.sh` 再收一次的話,同一句話會在票上長出兩筆,而處置的人
    分不出哪一筆是哪一輪的。回 `(類別, 是不是新的)`。
    """
    category, body = objection_parts(line)
    where = os.path.relpath(evidence, root()) if evidence else ""
    with Lock():
        ticket = load(ident)
        rows = ticket.get("objections") or []
        for row in rows:
            if isinstance(row, dict) and row.get("category") == category \
                    and (row.get("body") or "") == body:
                return category, False
        rows.append({"category": category, "body": body, "evidence": where,
                     "owner": "verifier" if category == "test_defect" else "main",
                     "disposition": "", "follow_up": ""})
        ticket["objections"] = rows
        ticket["state_version"] = int(ticket.get("state_version") or 0) + 1
        # 反駁是落地**之後**才補得上處置的那一格,不是重新審過一次票面:跟著把
        # review 的版本蓋上去,不讓既有的覆核因為收了一筆反駁而過期(#15 同一條)。
        if isinstance(ticket.get("review"), dict) \
                and ticket["review"].get("state_version") is not None:
            ticket["review"]["state_version"] = ticket["state_version"]
        save(ticket)
    event.emit("ticket.state", ticket=str(ident), field="objections",
               note=category, state_version=ticket["state_version"])
    return category, True


def cmd_objection(argv):
    if not argv:
        sys.stderr.write("ticket: %s\n" % USAGE["objection"])
        return 2
    ident = argv[0].lstrip("#")
    line = evidence = ""
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--line", "--evidence") and index + 1 < len(argv):
            if flag == "--line":
                line = argv[index + 1]
            else:
                evidence = argv[index + 1]
            index += 2
            continue
        return unknown_flag("objection", flag)
    if not line:
        sys.stderr.write("ticket: objection 要 --line \"OBJECTION: <類別> <理由>\"\n")
        return 2
    try:
        category, fresh = record_objection(ident, line, evidence)
    except (OSError, ValueError, RuntimeError) as exc:
        sys.stderr.write("ticket: 反駁記不進 #%s —— %s\n" % (ident, exc))
        return 2
    sys.stdout.write("ticket: #%s 反駁 %s —— %s\n"
                     % (ident, category, "記下了" if fresh else "已經有同一筆,沒有再記"))
    return 0 if fresh else 3


# --------------------------------------------------------------------- main


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    table = {"create": cmd_create, "list": cmd_list, "show": cmd_show,
             "set": cmd_set, "inbox": cmd_inbox, "verify": cmd_verify,
             "close": cmd_close, "import": cmd_import, "freeze": cmd_freeze,
             "round": cmd_round, "result": cmd_result,
             "objection": cmd_objection}
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
