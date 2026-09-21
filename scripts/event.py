#!/usr/bin/env python3
"""事件:agent 做的每一件事,在控制台上都是一行 JSON — D-003。

    scripts/event.py emit session.start --role main --model fable
    scripts/event.py emit ticket.attempt.done --ticket 7 --attempt 2 --note "patch 在 …" --kv rc=0
    scripts/event.py tail 20
    scripts/event.py grep land        # 事件種類,或票號

控制台只讀這份檔,不猜(`board/README.md`)。**沒發事件的事,對系統而言沒發生**
—— 所以這一支要便宜到沒有理由跳過:一次 append、不鎖、不讀回、不驗證票存不存在。

## 種類是一張固定的表,不是自由字串
下面 `KINDS` 就是全部;不在表上的一律拒收(退出碼 2),並把表印出來。

理由:拼錯的種類與沒發的事件長得一樣 —— `grep gate.fail` 對一份寫成 `gate.failed`
的檔案回零筆,而「零筆」正是「一切正常」的樣子(`docs/DISPATCH-TEMPLATE.md` §5.5
的母題)。一張表擋掉的是這個,不是手誤本身。要新增一種,改這張表並說明它回答
哪個問題 —— 那是一次 code review,跟改對照表同一個性質。
"""

import json
import os
import sys
from datetime import datetime

KINDS = (
    # session:誰在線上。heartbeat.sh 拿 start 沒有配對的 end 去對租約。
    "session.start", "session.end",
    # 票的生命:建立、任一欄位變更、凍結、關閉。
    "ticket.created", "ticket.state", "ticket.frozen", "ticket.closed",
    # 一次派工的三種結局。attempt 對不上的遲到回報要能被認出來(SCHEMA §執行)。
    "ticket.attempt.start", "ticket.attempt.done", "ticket.attempt.failed",
    # 排順序只是提案(docs/ROLES.md:沒有調度員這個角色),所以它只有這一種事件。
    "schedule.proposed",
    # 閘門。綠是對某一個 base_sha 說的,所以 kv 要帶 sha。
    "gate.start", "gate.pass", "gate.fail",
    # 落地。refused 與 fail 分開:前者是「還沒開始就退回」,後者是「跑了、紅了」。
    "land.start", "land.refused", "land.pass", "land.fail",
    # 發版:人授權、主線執行。
    "release.start", "release.pass", "release.fail",
    # 決策收件匣:問出去、答回來。
    "decision.asked", "decision.answered",
    # 終態叫醒主線(D-015):一則事件 + `reports/inbox/` 一頁。主線不輪詢 status。
    "inbox.posted",
    # 記憶:量到超過上限、整理完成(docs/MEMORY.md「容量與整理」,D-006)。
    "memory.over_cap", "memory.consolidated",
)

ENV_ROOT = "AC_ROOT"
CONFIG_REL = os.path.join("board", "config.json")
DEFAULT_EVENTS = os.path.join("board", "events.jsonl")
DEFAULT_TICKETS = "tickets"
DEFAULT_ANSWERS = os.path.join("board", "answers.jsonl")
OUTSIDE = "(repo 外)"


def repo_root():
    """repo 根。`AC_ROOT` 可以蓋掉它 —— 測試要把整支腳本指到一顆拋棄式 repo,
    而**能被指到別處**正是「這一支沒有寫死任何專案」的可驗證形狀。

    沒有 `AC_ROOT` 時往上找 `board/config.json`。理由是同步過去的形狀:專案把這幾支
    放在 `scripts/control/`(`scripts/sync-to-project.sh`),而「上兩層」在那裡是
    `<專案>/scripts` —— 票、事件、reports 會通通寫到一個沒有人看的目錄,而且
    **它不會報錯**(設定讀不到就回空 dict,一路用預設值)。找不到就退回上兩層,
    行為與以前一模一樣。
    """
    override = os.environ.get(ENV_ROOT)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    here = os.path.dirname(os.path.abspath(__file__))
    walk = here
    for _ in range(5):
        walk = os.path.dirname(walk)
        if not walk or walk == os.path.dirname(walk):
            break
        if os.path.exists(os.path.join(walk, CONFIG_REL)):
            return walk
    return os.path.dirname(here)


def config(root=None):
    """`board/config.json`。讀不到就回空 dict:設定檔壞掉不該讓事件發不出去
    (發不出去的那一刻,正是最需要留下紀錄的那一刻)。"""
    path = os.path.join(root or repo_root(), CONFIG_REL)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_config(key, default, root=None):
    root = root or repo_root()
    rel = config(root).get(key) or default
    return os.path.join(root, rel) if not os.path.isabs(rel) else rel


def events_path(root=None):
    return _from_config("events_file", DEFAULT_EVENTS, root)


def answers_path(root=None):
    return _from_config("answers_file", DEFAULT_ANSWERS, root)


def tickets_dir(root=None):
    return _from_config("tickets_dir", DEFAULT_TICKETS, root)


def relative_cwd(root=None):
    """cwd 記成 repo 相對路徑。

    不寫絕對路徑有兩個理由:一是事件檔會被貼進回報與 commit,裡面不該有這台機器
    的家目錄;二是「在哪個 worktree 跑的」才是要問的事,而 `../<repo>-wt/land-…`
    這種相對形狀直接答得出來。repo 外面跑的記成一句話,不記路徑。
    """
    # 兩邊都 realpath:macOS 的 tempdir 是 `/var` → `/private/var` 的 symlink,
    # 而 `getcwd()` 給的是解過的、`__file__` 給的是沒解的。不統一的話,同一個目錄
    # 會被判成「repo 外」—— 而那是一句假話。
    root = os.path.realpath(root or repo_root())
    here = os.path.realpath(os.getcwd())
    try:
        if os.path.commonpath([here, root]) != root:
            return OUTSIDE
    except ValueError:            # 不同的掛載點,commonpath 會拋
        return OUTSIDE
    return os.path.relpath(here, root) or "."


def emit(kind, root=None, **fields):
    """append 一行。`O_APPEND` 一次 write,不讀回、不重寫。

    同一台機器上會有好幾條線同時發事件(主線、land、worker),而拿暫存檔重寫一份
    只加不改的檔,會把別人在那中間 append 的那一行整行吃掉。
    """
    if kind not in KINDS:
        raise ValueError(kind)
    root = root or repo_root()
    row = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "kind": kind, "pid": os.getpid(), "cwd": relative_cwd(root)}
    for key, value in fields.items():
        if value is not None and value != "":
            row[key] = value
    path = events_path(root)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, line)
    finally:
        os.close(handle)
    return row


def read_events(root=None):
    """壞掉的一行跳過 —— 那多半是正在被寫的那一刻,不是資料壞了。"""
    rows = []
    try:
        with open(events_path(root), encoding="utf-8") as handle:
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
    except OSError:
        return []
    return rows


def one_line(row):
    bits = [str(row.get("ts", "")), str(row.get("kind", ""))]
    if row.get("ticket"):
        bits.append("#%s" % row["ticket"])
    for key in ("role", "model", "attempt"):
        if row.get(key):
            bits.append("%s=%s" % (key, row[key]))
    extras = [k for k in sorted(row)
              if k not in ("ts", "kind", "ticket", "role", "model", "attempt",
                           "note", "pid", "cwd")]
    for key in extras:
        bits.append("%s=%s" % (key, row[key]))
    if row.get("note"):
        bits.append(str(row["note"]))
    return "  ".join(bits)


EMIT_FLAGS = (
    ("--ticket", "票號"),
    ("--role", "角色:main / opener / worker / verifier / consolidator"),
    ("--model", "模型"),
    ("--attempt", "第幾次派工(遲到的回報對不上 attempt 就拒絕)"),
    ("--note", "一句給人看的話"),
    ("--kv", "任何一格 `k=v`;可重複(例:`--kv sha=abc1234 --kv mode=full`)"),
)

USAGE = {
    "emit": "scripts/event.py emit <kind> [旗標…]",
    "tail": "scripts/event.py tail [N]                 # 預設 20",
    "grep": "scripts/event.py grep <kind 或票號>       # kind 吃前綴,票號可帶 #",
}

EXAMPLE = {
    "emit": ('python3 scripts/event.py emit session.start --role main --model fable\n'
             'python3 scripts/event.py emit ticket.attempt.done --ticket 7 '
             '--attempt 2 --note "patch 在 …" --kv rc=0'),
    "tail": "python3 scripts/event.py tail 20",
    "grep": "python3 scripts/event.py grep land\npython3 scripts/event.py grep #7",
}


def help_for(verb, out=sys.stdout):
    """一個子指令的說明。**程式要自己說得出規則**(D-001)。"""
    out.write("用法:%s\n" % USAGE.get(verb, "scripts/event.py %s" % verb))
    if verb == "emit":
        out.write("\n認得的參數:\n")
        for flag, note in EMIT_FLAGS:
            out.write("  %-12s %s\n" % (flag, note))
    out.write("\n例:\n%s\n" % EXAMPLE.get(verb, ""))
    if verb == "emit":
        out.write("\n事件種類是一張固定的表,不在表上的拒收:\n")
        for kind in KINDS:
            out.write("  %s\n" % kind)
    return 0


def unknown_flag(flag):
    """不認得的參數 —— **把認得的那幾個列出來**。「不認得 X」只說了它不是什麼。"""
    sys.stderr.write("event: emit 不認得 %r\n" % flag)
    sys.stderr.write("event: emit 認得的是:%s\n"
                     % "  ".join(name for name, _ in EMIT_FLAGS))
    sys.stderr.write("event: 看範例:python3 scripts/event.py emit --help\n")
    return 2


def usage(out=sys.stderr):
    out.write(__doc__.split("## 種類")[0].rstrip() + "\n\n事件種類:\n")
    for kind in KINDS:
        out.write("  %s\n" % kind)


def cmd_emit(argv):
    if not argv:
        usage()
        return 2
    kind = argv[0]
    if kind not in KINDS:
        sys.stderr.write("event: 不認得的事件種類 %r —— 種類是一張固定的表:\n" % kind)
        for known in KINDS:
            sys.stderr.write("  %s\n" % known)
        return 2
    fields = {}
    rest = argv[1:]
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--kv":
            index += 1
            if index >= len(rest) or "=" not in rest[index]:
                sys.stderr.write("event: --kv 要 k=v(例:--kv sha=abc1234)\n")
                return 2
            key, _, value = rest[index].partition("=")
            fields[key] = value
        elif arg.startswith("--") and arg[2:] in ("ticket", "role", "model",
                                                  "attempt", "note"):
            name = arg[2:]
            index += 1
            if index >= len(rest):
                sys.stderr.write("event: %s 少了值\n" % arg)
                return 2
            fields[name] = rest[index]
        else:
            return unknown_flag(arg)
        index += 1
    row = emit(kind, **fields)
    sys.stdout.write(one_line(row) + "\n")
    return 0


def cmd_tail(argv):
    try:
        count = int(argv[0]) if argv else 20
    except ValueError:
        sys.stderr.write("event: tail 要一個數字\n")
        return 2
    rows = read_events()
    if not rows:
        sys.stdout.write("event: 還沒有事件\n")
        return 0
    for row in rows[-count:] if count > 0 else []:
        sys.stdout.write(one_line(row) + "\n")
    return 0


def cmd_grep(argv):
    """種類或票號都吃。**一發都沒有要說出來** —— 空輸出與「沒這種事件」長得一樣。"""
    if not argv:
        sys.stderr.write("event: grep 要一個事件種類或票號\n")
        return 2
    want = argv[0].lstrip("#")
    hits = [row for row in read_events()
            if str(row.get("kind", "")).startswith(want)
            or str(row.get("ticket", "")) == want]
    for row in hits:
        sys.stdout.write(one_line(row) + "\n")
    if not hits:
        sys.stdout.write("event: %s 一發都沒有\n" % argv[0])
    return 0


def main(argv):
    if not argv:
        usage()
        return 2
    verb, rest = argv[0], argv[1:]
    if verb in ("emit", "tail", "grep") and ("--help" in rest or "-h" in rest):
        return help_for(verb)
    if verb in ("--help", "-h", "help") and rest and rest[0] in ("emit", "tail", "grep"):
        return help_for(rest[0])
    if verb == "emit":
        return cmd_emit(rest)
    if verb == "tail":
        return cmd_tail(rest)
    if verb == "grep":
        return cmd_grep(rest)
    if verb in ("kinds", "--help", "-h", "help"):
        usage(sys.stdout)
        return 0
    sys.stderr.write("event: 不認得 %r(emit / tail / grep / kinds)\n" % verb)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
