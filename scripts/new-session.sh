#!/bin/sh
# 開 session 的固定動作,跑一遍 — `docs/SESSION-START.md`。
#
#   sh scripts/new-session.sh <role> <model> [--no-event] [--session <id>]
#   sh scripts/new-session.sh main fable
#
# 為什麼要有這一支:那一節是一張清單,而**一張要靠人記得照做的清單,漏掉一項時
# 長得跟做完了一樣**。跑成腳本之後,漏掉的那一項會在畫面上缺一段。
#
# 它**只讀不寫**,唯一的寫入是最後那一筆 `session.start` 事件 —— 控制台從此看得到你。
# `--no-event` 連那一筆都不發:resume / compact 後 hook 重印這一頁(`session-hook.sh`),
# 那不是新的 session,多一筆 start 會讓 heartbeat 以為有一個死在半路。
# `--session <id>`(#45):start 與 end 帶同一個 id,heartbeat 才配得起來(兩次 emit 是
# 兩個 pid)。沒給就自己產一個並印出來,結語那句 session.end 帶的就是它。
#
# ## 模型(#122 A2):這一頁只**顯示**角色表的建議
# 互動 session 的模型是啟動時指定的那個(`<model>`,事件照它記);`board/role-models.json`
# 那一列只印成一行建議,不熱切。主線開場另外列出 config 裡被角色表取代的舊選路設定。
#
# ## 獨立開題 / 設計派工(#122 A1)
#   sh scripts/new-session.sh dispatch <opener|design> --items <N> --prompt <派工文>
#        [--complexity simple|complex] [--ticket <票號>]
# 起誰只由 `role-models.py resolve` 選(一般開題 opener;`--complexity complex` 的開題與 design
# 走 astra → fable),命令照 `ticket.py agent-command`(base 是 config 的 `opener.command`),派工文
# 從 stdin 餵。`--items` = 這次要交的票數:超過候選的 `dispatch_cap` 就 rc=2、印切批建議、不起程序。
# 候選額度用完 / 不可用(provider 錯誤信封、CLI 不在)才換下一個;一般失敗不換。
# 退出碼:0 交完;1 那個 session 失敗(不換人);2 不派(表、旗標、超量);3 整串耗盡、停下。
set -u
USAGE="new-session: 要 <role> <model> [--no-event] [--session <id>],例:actl session main fable"
if [ "${1:-}" = dispatch ]; then
    shift
    # 專案根:`AC_ROOT`,否則 cwd 的主 repo 根(`wtbase.sh`,D-071 C2);不從 `$0` 來。
    AC=$(cd "$(dirname "$0")" && pwd)
    . "$AC/wtbase.sh"
    ROOT=$(ac_root new-session) || exit 2
    export AC_ROOT=$ROOT
    exec python3 - "$AC" "$ROOT" "$@" <<'PY'
import argparse, json, os, shlex, shutil, signal, subprocess, sys, time
from datetime import datetime

ac, root = sys.argv[1:3]
sys.path.insert(0, ac)
import event  # noqa: E402
import liveness  # noqa: E402  driver_pid + boot_id on agent.start (#117 A1)

top = argparse.ArgumentParser(prog="new-session.sh dispatch")
top.add_argument("role", choices=("opener", "design"))
top.add_argument("--items", type=int, required=True,
                 help="這次要交的票數;超過候選的 dispatch_cap 就不派")
top.add_argument("--prompt", required=True, help="派工文(從 stdin 餵給 agent)")
top.add_argument("--complexity", choices=("simple", "complex"))
top.add_argument("--ticket", default="")
args = top.parse_args(sys.argv[3:])
FALLBACK_KINDS = ("quota_exhausted", "provider_unavailable")


def say(text, err=False):
    (sys.stderr if err else sys.stdout).write("new-session: %s\n" % text)


def role_models(*argv):
    return subprocess.run([sys.executable, os.path.join(ac, "role-models.py"), "--table",
                           os.path.join(root, "board", "role-models.json"), *argv],
                          capture_output=True, text=True)


if not os.path.isfile(args.prompt):
    say("派工文 %s 不在 —— 不派" % args.prompt, err=True)
    sys.exit(2)
conf = event.config(root)
opener = conf.get("opener") if isinstance(conf.get("opener"), dict) else {}
base = opener.get("command") or "claude -p --model opus"
timeout = float(opener.get("timeout_seconds") or 1800)
rundir = os.path.join(root, conf.get("reports_dir") or "reports", "dispatch",
                      "%s-%s-%d" % (args.role, datetime.now().strftime("%Y%m%d-%H%M%S"),
                                    os.getpid()))
exhausted, tried = None, []
while True:
    flags = ["--items", str(args.items)]
    if args.complexity:
        flags += ["--complexity", args.complexity]
    if exhausted is not None:
        flags += ["--exhausted-index", str(exhausted)]
    picked = role_models("resolve", "--role", args.role, *flags)
    if picked.returncode == 2:
        sys.stderr.write(picked.stderr)
        say("%s 不派(角色表 rc=2;沒有起任何程序)" % args.role, err=True)
        sys.exit(2)
    choice = json.loads(picked.stdout)
    if picked.returncode == 3:
        say("%s 候選整串耗盡(%s)—— 停下,不改派別的模型" % (
            args.role, ";".join(tried) or choice["reason"]), err=True)
        sys.exit(3)
    model, tool, index = choice["model"], choice["tool"], choice["candidate_index"]
    made = subprocess.run([sys.executable, os.path.join(ac, "ticket.py"), "agent-command",
                           "--model", model, "--tool", tool, "--base", base, "--swap-model",
                           "--effort", choice.get("effort") or ""], capture_output=True, text=True)
    if made.returncode != 0:
        say("%s 的命令組不出來(%s):%s —— 不派" % (args.role, model, made.stderr.strip()), err=True)
        sys.exit(2)
    cmd = made.stdout.strip()
    head = next((tok for tok in shlex.split(cmd) if "=" not in tok), "")
    if not shutil.which(head):
        tried.append("%s 命令缺(%s 不在 PATH)" % (model, head))
        say("候選第 %d 格 %s 命令缺(%s 不在 PATH)—— 換下一格" % (index, model, head), err=True)
        exhausted = index
        continue
    os.makedirs(rundir, exist_ok=True)
    log_path = os.path.join(rundir, "%s.log" % model.split(":")[-1])
    say("照角色表起 %s:%s(tool %s;%s)" % (args.role, model, tool, choice["reason"]))
    fields = dict(role=args.role, model=model, agent="new-session-dispatch",
                  items=args.items)
    if args.ticket:
        fields["ticket"] = args.ticket
    # 這一支 python 就是 driver:它起 agent、等它回來、發 done / failed(#117 A1)。
    event.emit("agent.start", **fields, **liveness.driver_fields())
    env = dict(os.environ, AC_ROLE=args.role, AC_MODEL=model,
               **({"AC_TICKET": args.ticket} if args.ticket else {}))
    with open(args.prompt, encoding="utf-8") as stdin, open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, shell=True, cwd=root, env=env, stdin=stdin, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            rc = 124
    event.emit("agent.done" if rc == 0 else "agent.failed", rc=rc, **fields)
    if rc == 0:
        say("%s 交完了(%s);log %s" % (args.role, model, os.path.relpath(log_path, root)))
        sys.exit(0)
    judged = role_models("classify", "--evidence", log_path, "--rc", str(rc))
    verdict = json.loads(judged.stdout) if judged.returncode == 0 else {"kind": "other",
                                                                         "detail": ""}
    if rc == 124 or verdict["kind"] not in FALLBACK_KINDS:
        say("%s(%s)退出 %d —— 不換候選(%s);log %s" % (
            args.role, model, rc, "逾時" if rc == 124 else verdict["detail"],
            os.path.relpath(log_path, root)), err=True)
        sys.exit(1)
    tried.append("%s %s" % (model, verdict["kind"]))
    say("候選第 %d 格 %s 用不了(%s)—— 換下一格" % (index, model, verdict["detail"]), err=True)
    exhausted = index
PY
fi
[ $# -ge 2 ] || { echo "$USAGE"; exit 2; }
ROLE=$1
MODEL=$2
shift 2
EMIT=1
SID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-event) EMIT=0 ;;
        --session)
            [ $# -ge 2 ] && [ -n "$2" ] || { echo "$USAGE(--session 少了值)"; exit 2; }
            SID=$2; shift ;;
        *) echo "$USAGE(不認得 $1)"; exit 2 ;;
    esac
    shift
done
[ -n "$SID" ] || SID=$(date +%Y%m%d-%H%M%S)-$$
# 同伴腳本從這一支自己住的目錄(A 的 clone)叫,不寫死 $ROOT/scripts。專案根不從 `$0` 來
# (D-071 C2,#159):`AC_ROOT`,否則 cwd 的主 repo 根(`wtbase.sh`);都沒有 rc 2。
AC=$(cd "$(dirname "$0")" && pwd)
. "$AC/wtbase.sh"
ROOT=$(ac_root new-session) || exit 2
export AC_ROOT=$ROOT
# 正本(「不可違反的」、角色卡、模型卡)住 A 的 clone(D-071 §3):`AC_HOME`,沒設就是這一支的上一層。
AC_HOME=$(ac_home)
export AC_HOME
MAIN=main

CTL=${AC#"$ROOT"/}

say() { echo; echo "── $* ──"; }

# 會長的那幾段(規則、票、收件匣、讀單)在這裡組:讀設定與票庫要 Python,而正本在哪、
# 是不是正本、`memory.inbox_suffix` 只准從 rules.py 讀一份 ——
# 寫死第二份的那一天,兩份會往不同方向漂(#62:讀單寫死 memory/role/,專案那一條指到空)。
#
# ## 上限(#62):這一頁由 session-hook.sh 塞進 10,000 字元的 hook 輸出
# 票只列 Draft 以外的、最多 PAGE_ROWS 張;收件匣一則一行、最多 PAGE_ROWS 則;事件 10 筆;
# git status 10 行;心跳每一塊 5 列。沒列的**說出數字與全文怎麼看** —— 砍掉而不說,與「只有這幾張」長得一樣。
part() {
    python3 - "$AC" "$ROOT" "$CTL" "$ROLE" "$MODEL" "$@" <<'PY'
import os
import sys

ac, root, ctl, role, model, what = sys.argv[1:7]
sys.path.insert(0, ac)
import inbox  # noqa: E402
import rules  # noqa: E402
import ticket  # noqa: E402

PAGE_ROWS = 20
INBOX_TAIL = 5
# 角色卡的檔名跟角色名不一定一樣(worker 的卡是 implementer.md;對照表在 rules.py WANTED)。
card = "implementer" if role == "worker" else role
# 正本(角色卡、模型卡、「不可違反的」)住 A 的 clone(D-071 §3);專案自己那一層住根的 memory/。
home = rules.ac_home()
roles = rules.canon_dir("role")
models = rules.canon_dir("model")
canon = rules.is_canon(root)
suffix = rules.inbox_suffix(root)
local_role = rules.local_dir("role")
local_model = rules.local_dir("model")


def read(rel, base=None):
    try:
        with open(os.path.join(base or root, rel), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def shown(rel):
    """正本裡的 `rel` 在頁上怎麼寫:正本自己寫相對路徑,專案寫 `$AC_HOME/…`。"""
    return rules.canon_rel(root, rel)


PULL = "git -C <AC_HOME> pull" if not home else "git -C %s pull" % home


def contract():
    """「不可違反的」那一節,一律讀 `$AC_HOME/CLAUDE.md` 那一節(到下一個 `## `)——
    專案自己的 CLAUDE.md 不是這一節的來源(D-071 §3:正本不能被專案蓋)。"""
    text = read("CLAUDE.md", home) if home else None
    out, inside = [], False
    for line in (text or "").splitlines(True):
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith("## 不可違反的")
        if inside:
            out.append(line)
    if not out:
        return ("new-session: %s 沒有「## 不可違反的」那一節 —— 規則沒有印出來(%s 更新 A)\n"
                % (shown("CLAUDE.md"), PULL))
    return "".join(out)


def top():
    sys.stdout.write(contract().rstrip("\n") + "\n\n")
    rel = os.path.join(roles, card + ".md")
    text = read(rel, home) if home else None
    if text is None:
        sys.stdout.write("new-session: 缺角色卡 %s —— %s 更新 A\n" % (shown(rel), PULL))
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")
    # 專案自己整理過的主卡(#74):暫存區整理進 `memory/role/<卡>.md` 之後,那幾條從暫存區
    # 消失 —— 只印暫存區的話,整理前看得到的規矩整理後就看不到了。正本的主卡就是上面那一張。
    if not canon:
        rel = os.path.join(local_role, card + ".md")
        text = read(rel)
        if text and text.strip():
            sys.stdout.write("\n%s(本專案整理過的主卡):\n%s\n" % (rel, text.strip("\n")))
    # 暫存區住在 memory/(這個 repo 自己寫的,D-021):只印尾巴。
    rel = os.path.join(local_role, card + suffix)
    lines = [line for line in (read(rel) or "").splitlines() if line.strip()]
    if lines:
        sys.stdout.write("\n%s(最後 %d 行,共 %d 行):\n"
                         % (rel, min(INBOX_TAIL, len(lines)), len(lines)))
        sys.stdout.write("\n".join(lines[-INBOX_TAIL:]) + "\n")


def tickets():
    rows = [t for t in ticket.load_all() if ticket.is_open(t)]
    if not rows:
        sys.stdout.write("ticket: 沒有符合的票\n")
        return
    drafts = [t for t in rows if t.get("state") == "Draft"]
    shown = [t for t in rows if t.get("state") != "Draft"]
    for one in shown[:PAGE_ROWS]:
        subject = str(one.get("subject", ""))
        subject = subject if len(subject) <= 60 else subject[:59] + "…"
        frozen = " ❄" if one.get("frozen") else ""
        sys.stdout.write("#%-4s %-17s %s%s\n" % (one["id"], one.get("state", "?"),
                                                subject, frozen))
    if len(shown) > PAGE_ROWS:
        sys.stdout.write("ticket: 還有 %d 張沒列 —— 全部:python3 %s/ticket.py list --open\n"
                         % (len(shown) - PAGE_ROWS, ctl))
    if drafts:
        sys.stdout.write("ticket: Draft %d 張不逐張列 —— python3 %s/ticket.py list --state Draft\n"
                         % (len(drafts), ctl))


def inbox_part():
    hint = inbox.legacy_hint(root)
    if hint:
        sys.stdout.write(hint)
    rows = inbox.entries(root)
    if not rows:
        sys.stdout.write("inbox: 沒有等你的東西(ack 過的用 --all 看)\n")
        return
    sys.stdout.write("inbox: %d 則(還沒 ack)\n" % len(rows))
    # 最新的 PAGE_ROWS 則,照原本的先後。
    for row in rows[-PAGE_ROWS:]:
        sys.stdout.write("  • #%-4s %-22s %s\n" % (row.get("ticket", "?"), row.get("state", "?"),
                                                   str(row.get("what", ""))[:60]))
    if len(rows) > PAGE_ROWS:
        sys.stdout.write("inbox: 還有 %d 則沒列(較舊的)—— 全部:python3 %s/inbox.py list\n"
                         % (len(rows) - PAGE_ROWS, ctl))
    sys.stdout.write("inbox: 讀一頁 python3 %s/inbox.py show <票號>;收下 python3 %s/inbox.py ack <票號>\n"
                     % (ctl, ctl))


def events():
    # 一筆事件可以帶一整份 review 欄(實測 T 一列 300+ 字元):一列最多 160 字元。
    import subprocess
    done = subprocess.run([sys.executable, os.path.join(ac, "event.py"), "tail", "10"],
                          stdout=subprocess.PIPE, universal_newlines=True)
    for line in done.stdout.splitlines():
        sys.stdout.write((line if len(line) <= 160 else line[:159] + "…") + "\n")


def reading():
    """讀單:① 角色卡 ② 專案疊層 ③ 多層記憶 ④ HANDOFF 現況(使用者 2026-09-29 裁示)。
    每一條相對於根解析,不在的標出來 —— 指到一個不存在的檔,比沒有那一行更糟。"""
    def say(rel, why, base=None):
        gone = "" if os.path.exists(os.path.join(base or root, rel)) else "(不在)"
        sys.stdout.write("  %-26s %s%s\n" % (rel if base is None else shown(rel), why, gone))

    rel = os.path.join(roles, card + ".md")
    if canon:
        say(rel, "你這個角色的角色卡")
    else:
        say(rel, "你這個角色的角色卡(agent-control 的正本)", home)
        if os.path.exists(os.path.join(root, local_role, card + ".md")):
            say(os.path.join(local_role, card + ".md"), "這個專案整理過的角色規矩")
        say(os.path.join(local_role, card + suffix), "這個專案疊上去的角色規矩")
    name = os.path.basename(rules.model_card(model) or model + ".md")
    rel = os.path.join(local_model, name)
    pending = rel[:-len(".md")] + suffix
    if not os.path.exists(os.path.join(root, rel)) and os.path.exists(os.path.join(root, pending)):
        say(pending, "你這個模型踩過的坑(主檔還沒有,讀暫存區)")
    else:
        say(rel, "你這個模型在這個專案踩過的坑")
    if not canon:
        say(os.path.join(models, name), "你這個模型在 agent-control 踩過的坑(正本)", home)
        say(local_role + "/", "這個專案自己寫的角色備忘")
        say(rules.local_dir("project") + "/", "這個專案的專案事實")
    # 全域交接與收件匣是主線的(`docs/SESSION-START.md` 那張表):短命角色不列(#74)。
    if role != "main":
        return
    say(os.path.join("docs", "HANDOFF.md"), "現況 —— 上一個 session 留給你的(舊的在 docs/handoff/)")
    # 收件匣住在設定的 reports_dir(專案可以放在根外面),照 inbox.py 同一格解析。
    say(os.path.relpath(inbox.inbox_dir(root), root) + "/", "上面印的那幾則(inbox.py show <票號> 讀一頁)")


{"top": top, "tickets": tickets, "inbox": inbox_part, "events": events,
 "reading": reading}[what]()
PY
}

if [ "$ROLE" = "main" ]; then
    # 規則置頂(#62):compact 後 hook 重印這一頁,規則就在上下文最前面 ——
    # 主線不必自覺去讀,而「忘了讀」與「讀過」長得一樣。
    say "0. 不可違反的 + 你的角色卡(每次開場與 compact 後都在最前面)"
    part top
fi

say "1. 站在哪個版本、工作樹乾不乾淨"
git -C "$ROOT" log --oneline -1 "$MAIN" || echo "(讀不到 $MAIN)"
git -C "$ROOT" status --short | awk '
    NR <= 10 { print }
    END { if (NR > 10) printf "git: 還有 %d 行沒列 —— 全部:git status --short\n", NR - 10 }'

# 角色表只給建議(#122 A2):這個 session 的模型是啟動時指定的 $MODEL,事件照它記,不熱切。
say "角色表的建議(board/role-models.json;本 session 照啟動指定的 $MODEL 跑,不熱切)"
python3 - "$AC" "$ROOT" "$ROLE" "$MODEL" <<'PY' || true
import json, os, subprocess, sys

ac, root, role, model = sys.argv[1:5]
table = os.path.join(root, "board", "role-models.json")
# 開 session 的角色名 → 表裡的鏈(開題者多看一條複雜開題鏈)。
chains = {"main": [("main", None)], "opener": [("opener", None), ("opener", "complex")],
          "design": [("design", None)], "consolidator": [("consolidator", None)],
          "worker": [("worker", None)], "verifier": [("verifier", None)],
          "reviewer": [("reviewer", None)]}.get(role, [])
if not chains:
    print("role-models: 表裡沒有 %s 這個角色 —— 沒有建議" % role)
for name, complexity in chains:
    done = subprocess.run([sys.executable, os.path.join(ac, "role-models.py"), "--table", table,
                           "resolve", "--role", name]
                          + (["--complexity", complexity] if complexity else []),
                          capture_output=True, text=True)
    if done.returncode not in (0, 3):
        print("role-models: 角色表選不出 %s —— 派工會拒絕:%s"
              % (name, done.stderr.strip().splitlines()[0] if done.stderr.strip() else "rc=2"))
        continue
    got = json.loads(done.stdout)
    same = "" if got["model"] == model else ";與本 session 的 %s 不同,不熱切" % model
    print("role-models: %s 建議 %s(tool %s%s)%s" % (
        got["role"], got["model"], got["tool"],
        "" if got["dispatch_cap"] is None else ",每次派工上限 %d" % got["dispatch_cap"], same))
if role == "main":
    old = subprocess.run([sys.executable, os.path.join(ac, "role-models.py"), "--table", table,
                          "legacy", "--config", os.path.join(root, "board", "config.json")],
                         capture_output=True, text=True)
    lines = [line for line in old.stdout.splitlines() if "衝突" in line or "已不讀" in line
             or "一次遷移" in line]
    if any("衝突" in line or "沒有對應角色" in line for line in lines):
        print("\n".join(lines))
PY

# 事件流與開票清單是主線的全域交接(`docs/SESSION-START.md` 那張表,#74):短命角色
# 每貼一份就是每一個 agent 各付一次,而它們用不到。
if [ "$ROLE" = "main" ]; then
    say "2. 最近發生的事"
    part events

    say "3. 開著的票"
    part tickets
else
    say "2–3. 略過:事件流與開票清單是主線的全域交接,$ROLE 不讀(docs/SESSION-START.md)"
fi

# 記憶的暫存區:D-053 ⑥ / D-055 ① 條目累積或放太久(批次門檻在 memory.py)就整理,每個 session 開頭量一次。**退出碼 1 不停工** ——
# 它只是讓這個 session 知道有暫存區待整理(docs/MEMORY.md 第 1 點)。
# 整理票只由主線開(#76):短命角色量的是同一份記憶,各開一張就是同一件事派好幾次。
say "4. 記憶暫存區有沒有待整理(D-053 ⑥)"
# 雲端副本(CLAUDE_CODE_REMOTE=true,Claude Code 官方變數)也走唯讀:整理票只在主線本機的主 repo 開(D-057 ②)。
if [ "$ROLE" = "main" ] && [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    sh -c "python3 \"$AC/memory.py\" check" || echo "new-session: (有待整理的暫存區,不停工;整理票已開或沿用)"
else
    sh -c "python3 \"$AC/memory.py\" check --read-only" \
        || echo "new-session: (有待整理的暫存區,不停工;整理票由主線開場開,$ROLE 不開)"
fi
# 整理票自動起角色表的整理者(#122,原 #87 A41 的兩模型):只有主線開場叫,短命角色不叫 ——
# 同一張票被每個開場各起一次,就是同一件事派好幾次。每張 role=consolidator、Ready、沒有有效租約的票叫一次
# consolidate-memory.sh,背景跑(整理要幾十分鐘,不擋這一頁),輸出留在 reports/。
if [ "$ROLE" = "main" ] && [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ -f "$AC/consolidate-memory.sh" ]; then
    for cid in $(python3 - "$AC" <<'PY'
import sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
import ticket


def live(lease):
    if not isinstance(lease, dict) or not lease.get("until"):
        return False
    try:
        return datetime.fromisoformat(str(lease["until"])) > datetime.now().astimezone()
    except ValueError:
        return True


for one in ticket.load_all():
    if one.get("role") == "consolidator" and one.get("state") == "Ready" \
            and not live(one.get("lease")):
        print(one["id"])
PY
); do
        clog=$ROOT/reports/t$cid/consolidate-memory-$SID.log
        mkdir -p "$(dirname "$clog")"
        echo "new-session: 整理票 #$cid 是 Ready —— 背景起角色表的整理者:sh $CTL/consolidate-memory.sh $cid(log $clog)"
        nohup sh "$AC/consolidate-memory.sh" "$cid" > "$clog" 2>&1 < /dev/null &
    done
fi

if [ "$ROLE" = "main" ]; then
    # 終態收件匣(D-015)。**開場印一次,之後只在被通知時讀** —— 主線不輪詢 status,
    # 因為每看一次背景工作就是整份上下文重送一輪。跑完的事自己會來這裡排隊。
    say "5. 收件匣:跑完的事在等你(閘門、auto-fix、落地、轉 Blocked)"
    part inbox || true
    say "6. 決策收件匣:使用者填過的裁示要落成 docs/DECISIONS.md 一列"
    python3 "$AC/ticket.py" inbox
    say "7. 心跳:上一個 session / land 有沒有死在半路"
    # 到期的租約一列一筆,積了幾十筆的那一天這一段比整頁還長:每一塊留前 5 列,其餘說數字。
    sh "$AC/heartbeat.sh" | awk -v again="sh $CTL/heartbeat.sh" '
        function rest() { if (n > 5) printf "heartbeat:   …還有 %d 列沒列 —— 全部:%s\n", n - 5, again; n = 0 }
        /^heartbeat:   / { n++; if (n <= 5) print; next }
        { rest(); print }
        END { rest() }' || true
fi

if [ "$EMIT" -eq 1 ]; then
    say "發事件:控制台從此看得到你"
    # driver(#117 A1):session 本身那一個行程。這一支一印完就走,不是它;session-hook.sh 從
    # hook 的父行程算出來交給 `AC_DRIVER_PID`。手打的沒有 —— 那一筆在 heartbeat 是 unknown,
    # 照租約判,不會被當成死了收掉。
    _drv=""
    case "${AC_DRIVER_PID:-}" in ''|*[!0-9]*) ;; *) _drv=$AC_DRIVER_PID ;; esac
    python3 "$AC/event.py" emit session.start --role "$ROLE" --model "$MODEL" --session "$SID" \
        ${_drv:+--driver "$_drv"} --kv "ac_sha=$(ac_sha "$(ac_home)")"
else
    say "發事件:略過(--no-event —— 同一個 session 重印這一頁,不是新的開場)"
fi

say "接下來要讀的(照順序)"
part reading
# 讀單的每一條相對於根解析,不在的標出來(#62)—— 與 part reading 同一個格式。
item() {
    [ -e "$ROOT/$1" ] && gone="" || gone="(不在)"
    printf '  %-26s %s%s\n' "$1" "$2" "$gone"
}
item docs/SESSION-START.md "你這個角色($ROLE)那一節"
case "$ROLE" in
    main)
        item CLAUDE.md "其餘各節(「不可違反的」那一節印在這一頁最上面)"
        item docs/TODO.md "§1 還沒打勾的"
        ;;
    opener)
        item docs/ROLES.md "§開題者:寫完整票面(驗收 = 測試計畫),開完就結束"
        item docs/VERIFICATION.md "§測試計畫:每條驗收一列,期望值來源要獨立於被測程式"
        ;;
    verifier)
        item docs/ROLES.md "§驗證者:只寫案例、證明案例是對的,不判 PASS/FAIL、無 VERDICT"
        item docs/VERIFICATION.md "§回歸層:TAGS 要登記,票的 verify 欄要寫怎麼跑"
        ;;
    *)
        item docs/DISPATCH-TEMPLATE.md "§禁區 與 §5.5 假綠家族"
        echo "  派工文裡指定的副本路徑 —— 先 ls 確認 work/ 與 base/ 都在"
        ;;
esac
echo
if [ "$ROLE" = "main" ]; then
    echo "結束前:交接寫 docs/HANDOFF.md、關票前先 ticket.py verify。"
else
    echo "結束前:交接寫在你的回報裡(全域交接是主線的)。"
fi
echo "session: $SID"
echo "結束前:python3 $CTL/event.py emit session.end --role $ROLE --session $SID"
