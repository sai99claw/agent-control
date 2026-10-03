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
set -u
USAGE="new-session: 要 <role> <model> [--no-event] [--session <id>],例:sh scripts/new-session.sh main fable"
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
# 根:`AC_ROOT` 有設就用,否則往上找 board/config.json(與 heartbeat.sh 同一種)——
# 同步到專案後這一支住在 scripts/control/,「上一層」不再是根。
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
export AC_ROOT=$ROOT
# 同伴腳本從這一支自己住的目錄叫,不寫死 $ROOT/scripts。
AC=$(cd "$(dirname "$0")" && pwd)
MAIN=main

CTL=${AC#"$ROOT"/}

say() { echo; echo "── $* ──"; }

# 會長的那幾段(規則、票、收件匣、讀單)在這裡組:讀設定與票庫要 Python,而同一格設定
# (`rules.roles_dir` / `models_dir` / `memory.inbox_suffix`)只准從 rules.py 讀一份 ——
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
roles = os.path.normpath(rules.roles_dir(root))
models = os.path.normpath(rules.models_dir(root))
canon = rules.is_canon(root)
suffix = rules.inbox_suffix(root)
local_role = rules.local_dir("role")
local_model = rules.local_dir("model")


def read(rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def contract():
    """「不可違反的」那一節。正本讀 CLAUDE.md 那一節(到下一個 `## `);專案讀 sync 產生的
    `<roles_dir>/contract.md` —— 與角色卡同一條同步,不會手抄分岔(D-026 丙)。"""
    if not canon:
        rel = os.path.join(roles, "contract.md")
        text = read(rel)
        if text is None:
            return ("new-session: 缺 %s —— 到 agent-control 跑 "
                    "sh scripts/sync-to-project.sh <這個專案的根> 產生它\n" % rel)
        return text
    text = read("CLAUDE.md")
    out, inside = [], False
    for line in (text or "").splitlines(True):
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith("## 不可違反的")
        if inside:
            out.append(line)
    if not out:
        return "new-session: CLAUDE.md 沒有「## 不可違反的」那一節 —— 規則沒有印出來\n"
    return "".join(out)


def top():
    sys.stdout.write(contract().rstrip("\n") + "\n\n")
    rel = os.path.join(roles, card + ".md")
    text = read(rel)
    if text is None:
        sys.stdout.write("new-session: 缺角色卡 %s%s\n" % (rel, "" if canon else
                         " —— 到 agent-control 跑 sh scripts/sync-to-project.sh <這個專案的根>"))
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")
    # 專案自己整理過的主卡(#74):暫存區整理進 `memory/role/<卡>.md` 之後,那幾條從暫存區
    # 消失 —— 只印暫存區的話,整理前看得到的規矩整理後就看不到了。正本的主卡就是上面那一張。
    if not canon:
        rel = os.path.join(local_role, card + ".md")
        text = read(rel)
        if text and text.strip():
            sys.stdout.write("\n%s(本專案整理過的主卡):\n%s\n" % (rel, text.strip("\n")))
    # 暫存區住在 memory/(這個 repo 自己寫的),同步刻意不帶它(D-021):只印尾巴。
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
    def say(rel, why):
        gone = "" if os.path.exists(os.path.join(root, rel)) else "(不在)"
        sys.stdout.write("  %-26s %s%s\n" % (rel, why, gone))

    rel = os.path.join(roles, card + ".md")
    if canon:
        say(rel, "你這個角色的角色卡")
    else:
        say(rel, "你這個角色的角色卡(agent-control 的 %s 同步本)"
            % os.path.join(local_role, card + ".md"))
        if os.path.exists(os.path.join(root, local_role, card + ".md")):
            say(os.path.join(local_role, card + ".md"), "這個專案整理過的角色規矩")
        say(os.path.join(local_role, card + suffix), "這個專案疊上去的角色規矩")
    name = os.path.basename(rules.model_card(root, model) or model + ".md")
    rel = os.path.join(local_model, name)
    pending = rel[:-len(".md")] + suffix
    if not os.path.exists(os.path.join(root, rel)) and os.path.exists(os.path.join(root, pending)):
        say(pending, "你這個模型踩過的坑(主檔還沒有,讀暫存區)")
    else:
        say(rel, "你這個模型在這個專案踩過的坑")
    if not canon:
        say(os.path.join(models, name), "你這個模型在 agent-control 踩過的坑(同步本)")
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

# 記憶的上限:D-006 要求每個 session 開頭量一次。**退出碼 1 不停工** ——
# 它只是讓這個 session 知道自己的記憶該整理了(docs/MEMORY.md 第 1 點)。
say "4. 記憶有沒有超過上限(D-006)"
sh -c "python3 \"$AC/memory.py\" check" || echo "new-session: (超過上限,不停工;整理票已經開了)"

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
    python3 "$AC/event.py" emit session.start --role "$ROLE" --model "$MODEL" --session "$SID"
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
