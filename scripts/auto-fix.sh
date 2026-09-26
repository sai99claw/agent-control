#!/bin/sh
# 回歸紅了自動派**新** worker — `docs/WORKFLOW.md` §回歸紅了之後(D-010、D-015)。
#
#   sh scripts/auto-fix.sh <票號>                       # 讀最新狀態檔,紅就派下一輪;
#                                                       # 票 Ready 且沒有狀態檔 → 起第 1 輪
#   sh scripts/auto-fix.sh <票號> --dry-run             # 只印派工文,不起 worker
#   sh scripts/auto-fix.sh <票號> --dry-run --round 1   # 第 1 輪的派工文(還沒有狀態檔時)
#   sh scripts/auto-fix.sh <票號> --no-review           # 綠了票停在 InReview,不叫 review.sh
#
# 閘門與單票落地預設會叫這一支;`--no-auto-fix` 才停給人處理。
#
# ## 第 1 輪也由這一支起(#40,D-025 C1)
# 票 **Ready 且沒有狀態檔** ⇒ 走同一個 `round_once 1`:派工文就是 `--dry-run --round 1`
# 那一份、副本 base 取票的 `base_sha`(分支 t<n> 還不存在時)、`ticket.attempt.start`
# 由這裡發、票轉 Running;之後 apply → gate → InReview → review.sh 與第 2 輪起是同一段。
# 以前第 1 輪被「一輪都還沒跑過」的 exit 2 擋在門外,主線要自己派 worker、apply、
# 帶 `AC_TICKETS_DIR` 跑閘門 —— 同一件事兩個入口。票不是 Ready 又沒有狀態檔 ⇒
# 指名 state 停下(rc=2),不猜。
#
# ## 為什麼不叫醒舊的 worker
# 它醒來一次 = 累積的整份上下文重送一輪(D-010)。所以每一輪都是**新的** worker,
# 而它手上只有一份檔:狀態檔的 `repair_context`(base_sha、票面快照、副本、patch 路徑
# 與雜湊、第幾輪、上一輪的 EVIDENCE、重現指令)。少一格,它就得回頭翻對話或猜檔案位置。
#
# ## 三種停下來(每一種都寫一則 inbox,kind=decision —— 不是印一行就算)
# 1. worker 說**票寫錯 / 需要裁示** —— 它在 EVIDENCE 裡寫一行 `OBJECTION: <類別> <理由>`;
#    這一支把它記成票的 `objections[]`,票轉 Blocked、owner=main,發 `decision.asked`。
# 2. **三輪仍紅** —— `ticket.py round` 在第 `retry_limit+1` 輪把票轉 Blocked、owner=main。
# 3. **failures 沒有歸因** —— rc 非零卻一條紅都解析不出來(log 壞了、跑都沒跑起來)。
#    這一種**最危險**:它看起來像「沒有紅」,而自動派下去的 worker 會拿著一份空紅榜
#    去猜。所以這裡當場停,不派。
#
# **覆核由 `review.sh` 派**(#42,D-025 ②):綠了之後票轉 `InReview`(`ticket.state` 事件)、
# 再叫 `sh scripts/review.sh <票號>` —— reviewer pass 由它寫 `review`,fail 由它轉 Blocked 回主線
# 裁示。這一支自己不碰 `review` 那一格;`--no-review` 停在 InReview(主線手跑 review.sh)。
# 綠了**不發頁**(D-032):接著的事是腳本做的,收件匣只收 decision / done。
# 覆核的結果看 review.sh 的事件與頁,不改這一支的退出碼。
#
# ## 退出碼
#   0 綠了(覆核交給 review.sh)   1 三輪耗盡仍紅   2 用法 / 沒有狀態檔而票不是 Ready
#   3 worker 提反駁       4 failures 沒有歸因   5 worker 沒交出可用的 patch
set -u
# `AC_ROOT` 優先:被 `gate.sh` 的 auto-fix 叫到時,這支檔案住在**副本**裡,
# 而票、reports 與收件匣住在主 repo。照 `$0` 算根會把它們寫進一個等一下會被
# 收掉的目錄 —— 而且不會報錯。
# 主 repo 根:找 board/config.json 往上走(同步到專案後這幾支住在 scripts/control/,
# 「上一層」不再是根);找不到才退回上一層,跟以前一樣。
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
# 控制腳本自己住的目錄:agent-control 裡是 scripts/,同步到專案後是 scripts/control/。
# 同伴腳本一律從這裡叫,不寫死 $ROOT/scripts。
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_CONTROL_DIR=$AC
export AC_ROOT=$ROOT

cfg() {   # $1 = key(巢狀用 a.b)  $2 = 預設
    python3 - "$ROOT" "$1" "$2" <<'PY'
import json, os, sys
root, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
for part in key.split("."):
    data = data.get(part) if isinstance(data, dict) else None
print(data if data not in (None, "") else default)
PY
}

[ $# -ge 1 ] || { echo "用法:sh scripts/auto-fix.sh <票號> [--dry-run] [--round 1] [--no-review]"; exit 2; }
ID=$1
shift
DRY=""
WANT_ROUND=""
NO_REVIEW=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --no-review) NO_REVIEW=1 ;;
        --round)
            shift
            [ $# -ge 1 ] || { echo "auto-fix: --round 後面要一個數字" >&2; exit 2; }
            WANT_ROUND=$1 ;;
        *) echo "auto-fix: 不認得 $1(--dry-run / --round <n> / --no-review)" >&2; exit 2 ;;
    esac
    shift
done
case "${WANT_ROUND:-}" in
    "") ;;
    1) ;;
    *) echo "auto-fix: --round 只接 1(第一輪派工文)—— 第 2 輪起由紅榜決定第幾輪,不用指定" >&2
       exit 2 ;;
esac

MAIN=$(cfg main_branch main)
TICKETS=${AC_TICKETS_DIR:-$(cfg tickets_dir tickets)}
case $TICKETS in /*) TDIR=$TICKETS ;; *) TDIR=$ROOT/$TICKETS ;; esac
WORKER_CMD=$(cfg worker.command "claude -p --model opus")
WORKER_TIMEOUT=$(cfg worker.timeout_seconds 3600)
RERUN_CMD=$(cfg gate.rerun_cmd "")
TF=$TDIR/$ID.json
# 主 repo 根 **只有一種定義**(#38,D-018):`git rev-parse --git-common-dir` 的上一層。
# 在票分支的 worktree 裡它也指回主 repo;不在 git 裡才退回 `$ROOT`。
main_root() {
    _common=$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null || echo "")
    case "$_common" in
        "") echo "$ROOT" ;;
        /*) (cd "$(dirname "$_common")" 2>/dev/null && pwd) || echo "$ROOT" ;;
        *)  (cd "$ROOT/$(dirname "$_common")" 2>/dev/null && pwd) || echo "$ROOT" ;;
    esac
}
MAINROOT=$(main_root)
# 票分支的 worktree **不是**控制根(G10 / #29 A10):`_ac_root()` 往上找
# `board/config.json`,在 worktree 裡找到的是 worktree 自己,於是票檔要在**那一條分支上
# 進了版控**才找得到 —— 而票檔是走 docs 通道進主線的,常常還沒進去(#23 第 2 輪就是這樣
# rc=2 停掉的)。所以找不到票時改問主 repo:`--git-common-dir` 的上一層。
if [ ! -f "$TF" ] && [ -z "${AC_TICKETS_DIR:-}" ]; then
    if [ "$MAINROOT" != "$ROOT" ] \
            && [ -f "$MAINROOT/board/config.json" ] && [ -f "$MAINROOT/$TICKETS/$ID.json" ]; then
        echo "auto-fix: $ROOT 是 worktree —— 票 / reports / 事件改用主 repo $MAINROOT"
        ROOT=$MAINROOT
        export AC_ROOT=$ROOT
        TDIR=$ROOT/$TICKETS
        TF=$TDIR/$ID.json
    fi
fi
[ -f "$TF" ] || { echo "auto-fix: 找不到票 #$ID($TF)" >&2; exit 2; }
# 副本/worktree 的根:環境變數 > board/config.json 的 `worktree_dir` > 預設 `../<主 repo>-wt`;
# 相對路徑一律以**主 repo 根**解析(#38)。以前用 `$ROOT` 拼:在票 worktree 裡跑時 `$ROOT`
# 是 worktree,副本開到 `x-wt/x-wt/` 底下(9/23 實際開在 `agent-control-wt/agent-control-wt/`)。
WTBASE=${AC_WORKTREE_DIR:-$(cfg worktree_dir "")}
case "$WTBASE" in "") WTBASE=$MAINROOT/../$(basename "$MAINROOT")-wt ;; /*) ;; *) WTBASE=$MAINROOT/$WTBASE ;; esac
# 上層不存在就**指名停下**:以前 `cd` 失敗後前綴變成空字串,副本根算成檔案系統根下的
# `/x-wt`,而且不報錯 —— 接下來的 `rm -rf "$FIX"` 就對著一個沒人預期的地方跑。
_wtparent=$(cd "$(dirname "$WTBASE")" 2>/dev/null && pwd) || {
    echo "auto-fix: 副本根 $WTBASE 的上層不存在 —— 停(worktree_dir 以主 repo $MAINROOT 解析;先建好上層)" >&2
    exit 2
}
WTBASE=$_wtparent/$(basename "$WTBASE")

ev() {
    python3 "$AC/event.py" emit "$@" >/dev/null \
        || echo "auto-fix: 事件發不出去($*)" >&2
}

post() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "${RUN_ID:-}" \
        --kind decision --state "$1" --what "$2" --where "$3" \
        || echo "auto-fix: inbox 寫不出來($1)" >&2
}

block() {   # $1 = 為什麼;票轉 Blocked、指派主線
    python3 "$AC/ticket.py" set "$ID" state Blocked >/dev/null 2>&1 \
        || echo "auto-fix: 票狀態改不動(Blocked)" >&2
    python3 "$AC/ticket.py" set "$ID" owner main >/dev/null 2>&1 || true
    ev decision.asked --ticket "$ID" --note "$1"
}

# EVIDENCE 尾端那一塊 `result`(D-017,#20)。**只讀 EVIDENCE,不改它。**
#
# 抽出來的落在 `reports/t<票號>/<run_id>/` 裡,與那一輪的 `status.json` 同目錄 ——
# 要讀它的人已經在那個目錄了。鍵名**不寫在這一支**:schema 只有兩份
# (`docs/DISPATCH-TEMPLATE.md` §8.5 與 `tickets/SCHEMA.md`),抄第三份的那一天,
# 三份會各自往不同方向漂,而漂開的那一份看起來仍然像規格。
#
# ## 三種缺漏要有三種樣子
# 沒有 EVIDENCE 檔 / 有 EVIDENCE 但沒有那一塊 / 有那一塊但 JSON 解不開。揉成同一個
# 空檔的那一刻,「沒交」與「交了但都是空的」長得一樣(`DISPATCH-TEMPLATE` §5.5)。
# 三種都**不改變退出碼、也不擋流程**:這一手是留痕跡,不是新的一道閘門。
harvest_result() {   # $1 = EVIDENCE(可以不存在) $2 = 輸出 json $3 = 角色 $4 = 第幾輪
    # **與 `apply.sh` 共用同一支抽取**(#29 A4):實作在 `ticket.py result`。
    # 以前這裡是一段 heredoc,而走 `apply.sh` 的第 1 輪根本沒有這一手 —— 看板對那一輪
    # 只印得出「沒交結構化輸出」,與真的沒交長得一樣。抄第二份的那一天,兩份會往不同
    # 方向漂,而漂開的那一份看起來仍然像規格(D-018)。
    python3 "$AC/ticket.py" result "$1" "$2" --ticket "$ID" --role "$3" --round "$4" \
        || echo "auto-fix: result 抽不出來($1)—— 不擋流程" >&2
}

write_cost() {   # $1 = 角色  $2 = 第幾輪  $3 = 模型  $4 = 信封(log)  $5 = 量到的秒數
    # 一次 headless 派工的 token 與時鐘進票的 `cost[]`(D-032)。信封在 log 裡:stdout 與
    # stderr 混寫,`ticket.py cost` 取最後一行 `type=result` 的那一行;讀不到就記 null。
    python3 "$AC/ticket.py" cost "$ID" --role "$1" --round "$2" --model "$3" \
        --by auto-fix.sh --from-envelope "$4" --wall-seconds "$5" >/dev/null \
        || echo "auto-fix: #$ID 的 cost 寫不進票($1 第 $2 輪)—— 不擋流程" >&2
}

attempt_failed() {   # $1 = no-patch | apply-failed | timeout | objection
    # 每一個 `ticket.attempt.start` 都要有配對的 done / failed:heartbeat.sh 按 attempt 配對,
    # 沒配上的那一輪永遠算「還在跑」。
    ev ticket.attempt.failed --ticket "$ID" --attempt "$r" --kv reason="$1"
}

shed_copies() {   # $1 = 副本根(fix-t<n>/round<r> 或 verify-t<n>/round<r>)
    # **副本自己收**(#29 A6,G6):`patch` / `EVIDENCE` / `dispatch` / `result` 留著,
    # `work/` 與 `base/` 刪掉。以前它們只在**下一輪開始**才 `rm -rf`,綠了停 InReview
    # 就永遠留著 —— 而「沒人收」與「收過了」在磁碟上長得一樣,直到滿的那一刻
    # (2026-09-16 某個下游專案的副本 14 GB 塞滿磁碟,整台機器 disk I/O error)。
    [ -n "${1:-}" ] || return 0
    [ -d "$1/work" ] || [ -d "$1/base" ] || return 0
    rm -rf "$1/work" "$1/base"
    echo "auto-fix: 收掉副本 $1/{work,base}(patch 與 EVIDENCE 留著)"
}

collect_from_copy() {   # $1 = 副本根  $2… = 要撿出來的檔名
    # worker 有時把交付物放在 `work/` 裡(派工文說放在副本根)。**先撿出來再刪副本** ——
    # 反過來的話,刪掉的是這一輪唯一的一份 patch。
    where=$1
    shift
    for name in "$@"; do
        [ -f "$where/$name" ] && continue
        [ -f "$where/work/$name" ] || continue
        cp "$where/work/$name" "$where/$name" \
            && echo "auto-fix: 從 work/ 撿出 $name"
    done
}

read_status() {
    eval "$(python3 - "$ROOT" "$ID" "$TF" <<'PY'
import json, os, shlex, sys
root, ident, tf = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

# 帶判決的那幾種 run:**只有它們答得出「這棵樹紅不紅」**。`apply` 那一筆的 rc=0
# 說的是「patch 套上了」,不是「測試過了」。
VERDICT_KINDS = ("gate", "land")


def run_key(name):
    """「最新一輪」的排序鍵 —— **同一秒裡不准靠運氣**(#29 第 4 輪)。

    `run_id` 的形狀是 `<YYYYMMDD-HHMMSS>-<pid>`,而 `sorted()` 比的是整個字串:
    第二段因此按**十進位字面**排,`…-99993` 會排在 `…-100017` 後面(`9` > `1`)。
    同一秒裡誰算「最新」於是由 pid 的位數決定 —— 而那是運氣,不是時序。

    🩸 實測(#29 第 3 輪的閘門):`apply` 與 `gate` 落在同一秒,`apply` 那一筆
    (`rc=0`、紅 0 條)排到最後,auto-fix 讀成「上一輪是綠的 —— 沒有東西要修」就
    不派下一輪;而樹其實是紅的。**它不是每次都發生**:同一份 code 在別台機器、
    別個 pid 寬度下是綠的,所以這一條在自己跑的時候看起來沒問題。

    鍵有四段,由粗到細:
    1. **有沒有判決** —— `gate` / `land` 勝過 `apply`,**不分同秒跨秒**(#34):問的是
       「紅不紅」,而 `apply` 從來不回答那件事。🩸 只在同秒生效的那一版(#29 第 4 輪)
       擋不住晚一秒以上的 apply:它的 rc=0 蓋掉紅 gate,auto-fix 說「沒有東西要修」
       就 exit 0;rc=6 那一筆則被讀成「沒有歸因」。一筆判決都沒有才輪到 apply;
    2. **秒**(字串前綴,本來就是時間序);
    3. **`status.json` 的 mtime** —— 比秒細,同秒同類時還原得出誰後寫;
    4. **pid 當數字比**,不是當字串:到這裡已經沒有真相可還原了,但至少**是決定性的**。
    """
    stamp, _, tail = name.rpartition("-")
    try:
        serial = int(tail)
    except ValueError:
        serial = -1
    try:
        mtime = os.path.getmtime(status.path_for(root, ident, name))
    except OSError:
        mtime = 0.0
    kind = (status.read(root, ident, name) or {}).get("kind") or ""
    return (1 if kind in VERDICT_KINDS else 0, stamp, mtime, serial)


rows = status.runs_of(root, ident)
run_id = max(rows, key=run_key) if rows else ""
# 判決之後又有一筆沒判決的(套了新 patch 還沒跑閘門):那棵樹**未驗**,不是綠。
# 照判決走,但要說出來 —— 靜靜地略過它,與它不存在長得一樣。
newest = max(rows, key=lambda name: run_key(name)[1:]) if rows else ""
if newest != run_id:
    sys.stderr.write("auto-fix: %s(%s)晚於最後一次判決 %s(%s)—— 它沒有判決,"
                     "視為未驗、不當成綠;照判決走\n"
                     % (newest, (status.read(root, ident, newest) or {}).get("kind") or "?",
                        run_id, (status.read(root, ident, run_id) or {}).get("kind") or "?"))
data = status.read(root, ident, run_id) if run_id else {}
ctx = data.get("repair_context") or {}
patch = (ctx.get("patch") or {}).get("path") or ""
try:
    with open(tf, encoding="utf-8") as fh:
        limit = int(json.load(fh).get("retry_limit") or 2) + 1
except (OSError, ValueError, TypeError):
    limit = 3


def out(name, value):
    print("%s=%s" % (name, shlex.quote(str(value))))


out("S_RUN", run_id or "")
out("S_STATE", data.get("state") or "")
out("S_KIND", data.get("kind") or "")
out("S_RC", "" if data.get("rc") is None else data.get("rc"))
out("S_FAILS", len(data.get("failures") or []))
out("S_ROUND", (ctx.get("round") or 1))
out("S_BASE", ctx.get("base_sha") or "")
out("S_PATCH", patch)
out("S_PREV_EVIDENCE", ctx.get("prev_evidence") or "")
out("S_LIMIT", limit)
# 上一輪的環境嫌疑(形狀見 `docs/DESIGN-ENV-SUSPECT.md`,D-019)。經正規化函式讀,
# 不直接下標 —— 舊檔那一種非空 dict 在 `[0]` 上會炸。
suspects = status.environment_suspects(data)
first = suspects[0] if suspects else {}
out("S_ENV", len(suspects))
out("S_ENV_ENGINE", first.get("engine") or "?")
out("S_ENV_WHY", first.get("why") or "?")
PY
)"
}

first_round_packet() {   # $1 = 派工文寫到哪  $2 = 模型
    # **第 1 輪的派工文也由工具產**(#29 A3,G3)。以前第 1 輪是主線手寫、只有
    # `docs/DISPATCH-TEMPLATE.md` §8 的散文可抄,第 2 輪起才有 `dispatch-round<r>.md`
    # —— 同一個角色的兩輪因此拿到兩種形狀的派工文,而**少了哪一格沒有人看得出來**。
    #
    # `--dry-run --round 1`(給人看)與 Ready 票的 `round_once 1`(餵給 worker)**共用
    # 這一份**(#40):兩條路各產一份的那一天,人讀過的與 worker 拿到的就不是同一份。
    fr_fix=$WTBASE/fix-t$ID/round1
    mkdir -p "$(dirname "$1")"
    {
        python3 "$AC/rules.py" pack worker --model "$2" 2>/dev/null \
            || echo "(規則包產不出來 —— 自己讀 memory/role/implementer.md)"
        python3 - "$ROOT" "$ID" "$TF" "$fr_fix" "$2" <<'PY'
import json, os, sys
root, ident, tf, fix, model = sys.argv[1:6]
try:
    with open(tf, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError) as exc:
    ticket = {}
    sys.stderr.write("auto-fix: 票讀不動 —— %s\n" % exc)
base = ticket.get("base_sha") or "(票面沒有 base_sha —— 先補上再派)"
print("")
print("# 這一輪:#%s 第 1 輪(第一次實作)" % ident)
print("")
print("你是 **role=worker** 的實作者,**短命**:交付那一回合結束,不會再被叫醒。")
print("")
print("## 票面(`%s` 是現況)" % os.path.join("tickets", "%s.json" % ident))
print("```json")
print(json.dumps(ticket, ensure_ascii=False, indent=2))
print("```")
print("")
print("## 這張票獨有的四件事(`memory/role/README.md`)")
print("1. **票號**:#%s" % ident)
print("2. **base sha**:`%s`(派工方已經 `git log --oneline -1` 對過)" % base)
print("3. **副本路徑**:`%s/work`(改這個)、`%s/base`(一個字都不准動,它是 diff 的對照組)"
      % (fix, fix))
print("   兩個都這樣展:")
print("   ```sh")
print("   mkdir -p %s/base %s/work" % (fix, fix))
print("   git -C %s archive %s | tar -x -C %s/base" % (root, base, fix))
print("   git -C %s archive %s | tar -x -C %s/work" % (root, base, fix))
print("   ```")
print("   副本裡**沒有 `.git`**:想 git 寫入也寫不了,而票閘門的 `--branch` 在那裡是空閘門。")
print("4. **回報對象**:派工的那一條主線 session。")
print("")
print("## 你要交的兩樣(放在 `%s`)" % fix)
print("1. `patch-round1.diff` —— `cd %s && diff -ruN -x __pycache__ -x '*.pyc' base work > patch-round1.diff`"
      % fix)
print("   檔頭只准 `base/…` / `work/…` 的相對形式(絕對路徑 `apply.sh` 檔頭秒退);"
      "刪檔的 `+++` 側要改成 `/dev/null`。")
print("2. `EVIDENCE-round1.md` —— 必備五段(見角色卡)+ 記憶段 + 檔尾一塊 ```result` JSON。")
print("   主線收件那一手是:")
print("   ```sh")
print("   sh scripts/apply.sh %s %s/patch-round1.diff --evidence %s/EVIDENCE-round1.md"
      % (ident, fix, fix))
print("   ```")
print("   它會抽出 `result-round1.json`,並把 EVIDENCE 裡的 `OBJECTION:` 行記進票的 `objections[]`。")
print("")
print("## 回報格式")
print("① patch 絕對路徑 + diffstat;② 閘門指令、`Ran N`、`rc=`(**判綠只看 rc**);"
      "③ 變異驗紅表;④ 已排除的假設;⑤ 最小重現。")
print("**測試一律前景跑加 timeout** —— 把整套丟背景後結束回合 = 什麼都沒交。")
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。")
print("**不准**放寬既有斷言、不准把期望值改成程式現在印的東西(那是假綠家族)。")
PY
    } > "$1"
}

first_round_dispatch() {
    # `--round 1`:只產、只印,不起 worker、不動票。要起第 1 輪就不帶 `--round`。
    fr_run=${AC_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
    fr_dispatch=$ROOT/$(cfg reports_dir reports)/t$ID/$fr_run/dispatch-round1.md
    first_round_packet "$fr_dispatch" "$(cfg routing.implement opus)"
    cat "$fr_dispatch"
    echo "auto-fix: 第 1 輪派工文 -> $(python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$fr_dispatch" "$ROOT")" >&2
    echo "auto-fix: --round 1 只印派工文;票是 Ready 時不帶 --round 就由這一支起第 1 輪:sh scripts/auto-fix.sh $ID" >&2
    return 0
}

read_status
if [ "${WANT_ROUND:-}" = "1" ]; then
    [ -z "${S_RUN:-}" ] || echo "auto-fix: #$ID 已經有 $S_RUN 這一輪,你要的是第 1 輪的派工文 —— 照給" >&2
    first_round_dispatch
    exit $?
fi
if [ -z "${S_RUN:-}" ]; then
    # **Ready 且沒有狀態檔 ⇒ 第 1 輪**(#40,D-025 C1)。只認 Ready:Draft 還沒開完、
    # Running 多半是有人已經手派了、Blocked 在等裁示 —— 哪一種都不該由這一支猜著起。
    eval "$(python3 - "$TF" <<'PY'
import json, shlex, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
print("T_STATE=%s" % shlex.quote(str(ticket.get("state") or "")))
print("T_BASE=%s" % shlex.quote(str(ticket.get("base_sha") or "")))
PY
)"
    if [ "$T_STATE" != "Ready" ]; then
        echo "auto-fix: #$ID 沒有狀態檔,而票的 state=${T_STATE:-(空)},不是 Ready —— 停,不猜要不要起第 1 輪" >&2
        echo "auto-fix:   Ready 的票才由這一支起第 1 輪;只看派工文:sh scripts/auto-fix.sh $ID --dry-run --round 1" >&2
        echo "auto-fix:   已經派過第 1 輪、要修紅的:先跑 sh scripts/gate.sh --branch --ticket $ID" >&2
        exit 2
    fi
    # 迴圈從 `S_ROUND+1` 起;副本 base 在分支 t<n> 還不存在時取 `S_BASE` = 票的 base_sha
    # (`round_once` 既有那一格)。RUN_ID 自鑄:派工文、worker log、result 住在這一輪的目錄。
    S_ROUND=0
    S_BASE=$T_BASE
    RUN_ID=${AC_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
    echo "auto-fix: #$ID 是 Ready、還沒有狀態檔 —— 起第 1 輪(票的 base_sha ${S_BASE:-?})"
else
    RUN_ID=$S_RUN
    echo "auto-fix: #$ID 最新一輪 $S_RUN($S_KIND)state=$S_STATE rc=${S_RC:-?} 紅 $S_FAILS 條,第 $S_ROUND 輪(上限 $S_LIMIT)"
fi

# 手打就是覆寫:`gate.sh` 這一格非空時不自動派(D-019),但人自己打這一支的時候
# **照派** —— 只是要看得見他覆寫了什麼。一句警告加一次照派,比一次靜靜的拒絕好:
# 拒絕的那一版會讓人以為腳本壞了,然後去改腳本。
if [ "${S_ENV:-0}" != "0" ]; then
    echo "auto-fix: 上一輪環境可疑($S_ENV_ENGINE:$S_ENV_WHY),你確定要派?"
fi

if [ "${S_RC:-1}" = "0" ]; then
    echo "auto-fix: 上一輪是綠的 —— 沒有東西要修。覆核由 review.sh 派:sh scripts/review.sh $ID"
    exit 0
fi

# **沒有歸因的紅**:rc 非零卻一條都解析不出來。它看起來最像「沒紅」,而派下去的
# worker 會拿著空紅榜去猜 —— 猜出來的修法會改到沒有壞的地方。
# 第 1 輪(#40)沒有上一輪:環境嫌疑 0 條、rc 是空的、S_ROUND=0,前後三格本來就不成立;
# 只有這一格要明著跳過 —— 不然「還沒有紅榜」會被讀成「紅了但沒有歸因」。
if [ -n "${S_RUN:-}" ] && [ "$S_FAILS" -eq 0 ]; then
    echo "auto-fix: rc=${S_RC:-?} 卻一條紅都解析不出來 —— **沒有歸因**,不派 worker" >&2
    block "#$ID rc=${S_RC:-?} 但 failures 是空的:紅榜解析不出案例,要人看 log"
    post "紅了但沒有歸因" \
         "自己看 log:紅榜是空的,可能是測試根本沒跑起來(import 炸了、指令打錯)" \
         "reports/t$ID/$S_RUN/status.json 與它的 logs/"
    exit 4
fi

if [ "$S_ROUND" -ge "$S_LIMIT" ]; then
    echo "auto-fix: 已經是第 $S_ROUND 輪(上限 $S_LIMIT)—— 不再派,轉主線"
    python3 "$AC/ticket.py" round "$ID" "$S_ROUND" --red || true
    post "三輪耗盡仍紅" "這張票由你接手:讀紅榜決定要改票面還是換做法" \
         "reports/t$ID/$S_RUN/status.json"
    exit 1
fi

# --------------------------------------------------------------- 一輪的動作
MODEL=$(cfg routing.implement opus)
VERIFIER_MODEL=$(cfg routing.verify "$MODEL")
VERIFIER_CMD=$(cfg verifier.command "$WORKER_CMD")
# `MODEL`(routing.implement)只是路由標籤;`agent.start`/`agent.done` 的 model 欄要記
# **實際起的那個** —— 也就是 `worker.command` 裡 `--model`後面那個值。兩者不一致時
# 舊版只印 routing 那個標籤,events.jsonl 就記著一個從沒跑過的模型(#21)。
WORKER_MODEL=$(python3 - "$WORKER_CMD" "$MODEL" <<'PY'
import shlex, sys
cmd, default = sys.argv[1], sys.argv[2]
try:
    parts = shlex.split(cmd)
except ValueError:
    parts = cmd.split()
model = default
for i, tok in enumerate(parts):
    if tok == "--model" and i + 1 < len(parts):
        model = parts[i + 1]
        break
print(model)
PY
)
if [ "$WORKER_MODEL" != "$MODEL" ]; then
    echo "auto-fix: 警告:routing.implement=$MODEL,但 worker.command 實際起的是 $WORKER_MODEL —— agent.start/done/failed 的 model 記後者"
fi
WT=$WTBASE/t$ID

dispatch_verifier() {   # uses r/FIX/DISPATCH/line; sets PATCH_OUT/EVIDENCE/CASE_FIXED
    python3 - "$TF" "$ID" <<'PY'
import json, os, subprocess, sys
path, ident = sys.argv[1:3]
with open(path, encoding="utf-8") as handle:
    ticket = json.load(handle)
allowed = list(ticket.get("allowed_write_paths") or [])
for item in list(ticket.get("in_scope") or []) + ["tests/*"]:
    if item not in allowed:
        allowed.append(item)
subprocess.run([sys.executable, os.path.join(os.environ["AC_CONTROL_DIR"], "ticket.py"),
                "set", ident, "allowed_write_paths",
                json.dumps(allowed, ensure_ascii=False)], check=False)
PY
    VFIX=$WTBASE/verify-t$ID/round$r
    rm -rf "$VFIX"
    mkdir -p "$VFIX"
    cp -R "$FIX/base" "$VFIX/base"
    cp -R "$FIX/base" "$VFIX/work"
    VDISPATCH=$(dirname "$DISPATCH")/dispatch-verifier-round$r.md
    {
        python3 "$AC/rules.py" pack verifier --model "$VERIFIER_MODEL" 2>/dev/null \
            || echo "(規則包產不出來 —— 自己讀 memory/role/verifier.md)"
        python3 - "$ROOT" "$ID" "$RUN_ID" "$r" "$VFIX" "$line" <<'PY'
import os, sys
root, ident, run_id, r, fix, objection = sys.argv[1:7]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

data = status.read(root, ident, run_id)
files = []
print("\n# 這一輪:#%s 第 %s 輪(獨立驗證者修案例)" % (ident, r))
print("\n產品 worker 提出:`%s`" % objection)
print("你是**新的 role=verifier worker**;只修案例,不改產品程式。")
print("\n## 紅榜")
for row in data.get("failures") or []:
    path = row.get("file") or ""
    if path and path not in files:
        files.append(path)
    print("- %s %s (%s:%s)" % (row.get("kind") or "FAIL", row.get("case") or "?",
                                 path or "?", row.get("line") or "?"))
    print("  " + (row.get("excerpt") or "(沒有 excerpt)").splitlines()[0])
print("\n## 案例檔路徑")
for path in files:
    print("- `%s`" % path)
if not files:
    print("- `(紅榜沒有 file;從 case 名定位,不猜 oracle)`")
print("\n## 副本與交付")
print("- 只改 `%s/work`;`%s/base` 是對照組。" % (fix, fix))
print("- 交 `%s/patch-verify.diff` 與 `%s/EVIDENCE-verifier.md`。" % (fix, fix))
print("- patch 檔頭只准 `base/…` / `work/…`。")
PY
    } > "$VDISPATCH"
    echo "auto-fix: test_defect —— 起第 $r 輪的新驗證者 worker"
    VLOG=$(dirname "$DISPATCH")/verifier-round$r.log
    ev agent.start --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-verifier
    VSTART=$(date +%s)
    VWRC=$(python3 - "$VERIFIER_CMD" "$VDISPATCH" "$VFIX" "$WORKER_TIMEOUT" "$ID" "$r" "$VLOG" <<'PY'
import os, subprocess, sys
cmd, dispatch, cwd, timeout, ident, r, log_path = sys.argv[1:8]
env = dict(os.environ)
env.update({"AC_DISPATCH": dispatch, "AC_TICKET": ident, "AC_ROUND": r,
            "AC_WORK": cwd, "AC_ROLE": "verifier"})
try:
    with open(dispatch, encoding="utf-8") as handle, open(log_path, "w", encoding="utf-8") as log:
        done = subprocess.run(cmd, shell=True, cwd=cwd, env=env, stdin=handle,
                              stdout=log, stderr=subprocess.STDOUT, timeout=float(timeout))
    rc = done.returncode
except subprocess.TimeoutExpired:
    rc = 124
except OSError:
    rc = 127
print(rc)
PY
)
    if [ "$VWRC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$VWRC" --kv agent=auto-fix-verifier
    else
        ev agent.failed --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$VWRC" --kv agent=auto-fix-verifier
    fi
    write_cost verifier "$r" "$VERIFIER_MODEL" "$VLOG" "$(( $(date +%s) - VSTART ))"
    collect_from_copy "$VFIX" patch-verify.diff EVIDENCE-verifier.md
    PATCH_OUT=$VFIX/patch-verify.diff
    EVIDENCE=$VFIX/EVIDENCE-verifier.md
    # 驗證者那條路也要留痕跡,**而且在「沒交 patch 就回去」之前** —— 沒交的那一次
    # 正是最需要一份「沒交」的檔的那一次。
    harvest_result "$EVIDENCE" \
        "$(dirname "$DISPATCH")/result-verifier-round$r.json" verifier "$r"
    shed_copies "$VFIX"
    shed_copies "$FIX"
    if [ "$VWRC" -ne 0 ] || [ ! -f "$PATCH_OUT" ]; then
        block "#$ID 第 $r 輪的驗證者沒交出 patch-verify"
        post "驗證者沒交出 patch-verify" \
             "讀驗證者派工文與副本;不要讓主線自己改案例" "$VFIX"
        return 1
    fi
    CASE_FIXED=1
    return 0
}

round_once() {   # $1 = 第幾輪(r);設定 ROUND_RC
    r=$1
    FIX=$WTBASE/fix-t$ID/round$r
    rm -rf "$FIX"
    mkdir -p "$FIX/base"
    # **`base/` 是目前分支上的內容,不是 base_sha 的內容。**
    # worker 交的 diff 下一步要餵給 `apply.sh` 套在**分支上**,而分支已經有前幾輪了
    # —— 拿 base_sha 當對照組的話,第二輪的 patch 會宣稱自己在新建一個已經存在的檔,
    # 而 `git apply --check` 會在那裡整輪停住。分支還不存在(第一輪就紅)才退回 base_sha。
    SRC=${AC_FIX_SOURCE:-t$ID}
    git -C "$ROOT" rev-parse -q --verify "$SRC^{commit}" >/dev/null || SRC=$S_BASE
    if ! git -C "$ROOT" archive "$SRC" 2>/dev/null | tar -x -C "$FIX/base"; then
        echo "auto-fix: 取不出 $SRC 的副本 —— 這張票的 base 對不上這顆 repo" >&2
        ROUND_RC=2
        return 1
    fi
    cp -R "$FIX/base" "$FIX/work"
    if [ "$SRC" = "$S_BASE" ] && [ -n "$S_PATCH" ] && [ -f "$S_PATCH" ]; then
        ( cd "$FIX/work" && patch -p1 -F 2 --no-backup-if-mismatch -i "$S_PATCH" ) \
            >/dev/null 2>&1 || echo "auto-fix: 上一輪的 patch 有幾塊沒套進副本(worker 要自己看)"
    fi
    echo "auto-fix: 副本 $FIX(base = $SRC)"

    DISPATCH=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID/dispatch-round$r.md
    mkdir -p "$(dirname "$DISPATCH")"
    if [ "$r" -eq 1 ]; then
        # 第 1 輪沒有紅榜、沒有上一輪:派工文就是 `--dry-run --round 1` 那一份(#40)。
        first_round_packet "$DISPATCH" "$MODEL"
    else
        {
            python3 "$AC/rules.py" pack worker --model "$MODEL" 2>/dev/null \
                || echo "(規則包產不出來 —— 自己讀 memory/role/implementer.md)"
            python3 - "$ROOT" "$ID" "$RUN_ID" "$r" "$FIX" <<'PY'
import json, os, sys
root, ident, run_id, r, fix = sys.argv[1:6]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

data = status.read(root, ident, run_id)
ctx = data.get("repair_context") or {}
snap = ctx.get("ticket") or {}
print("")
print("# 這一輪:#%s 第 %s 輪(修復)" % (ident, r))
print("")
print("上一輪紅了。你是**新的** worker —— 上一輪那個不會醒來,所以下面這幾格就是你手上的全部。")
print("")
print("## 票面快照(`%s` 是現況,這裡是上一輪看到的那一版)"
      % os.path.join("tickets", "%s.json" % ident))
print("```json")
print(json.dumps(snap, ensure_ascii=False, indent=2))
print("```")
print("")
print("## 交接包 repair_context")
print("- base_sha:`%s`" % (ctx.get("base_sha") or "?"))
print("- 上一輪的 patch:`%s`(sha256 %s)"
      % ((ctx.get("patch") or {}).get("path") or "(沒有)",
         ((ctx.get("patch") or {}).get("sha256") or "")[:16]))
print("- 上一輪的 EVIDENCE:`%s`" % (ctx.get("prev_evidence") or "(沒有 —— 第一輪)"))
print("- 主線重現用的指令(**你不要跑它** —— 它動的是主 repo 的閘門/worktree):`%s`(cwd `%s`)"
      % ((ctx.get("repro") or {}).get("cmd") or "?",
         (ctx.get("repro") or {}).get("cwd") or "?"))
print("- 你在副本裡重現:照底下紅榜的 case 名單跑(例 `python3 -m unittest <模組>.<類>.<案例>`,"
      "cwd 是副本裡那個測試模組所在的目錄);修好後跑同一組再交,整套留給閘門。")
print("- 這是第 %s 輪,上限 %s 輪" % (r, int(snap.get("retry_limit") or 2) + 1))
print("")
print("## 紅榜(逐條,excerpt ≤ 20 行)")
for row in data.get("failures") or []:
    print("")
    print("### %s %s" % (row.get("kind") or "FAIL", row.get("case") or "?"))
    bits = []
    if row.get("subtest"):
        bits.append("子情境 %s" % row["subtest"])
    if row.get("engine"):
        bits.append("engine=%s" % row["engine"])
    if row.get("file"):
        bits.append("%s:%s" % (row["file"], row.get("line") or "?"))
    if row.get("suspected_flaky"):
        bits.append("**疑似 flaky**(單跑綠,但紅榜與 rc 都不動)")
    if bits:
        print("- " + ";".join(bits))
    print("```")
    print(row.get("excerpt") or "(沒有 excerpt)")
    print("```")
print("")
print("## 副本")
print("- `%s/work` —— 改這個(上一輪的 patch 已經套進去了)" % fix)
print("- `%s/base` —— 一個字都不准動,它是 diff 的對照組" % fix)
print("")
print("## 你要交的兩樣(放在 `%s`)" % fix)
print("1. `patch-round%s.diff` —— `cd %s && diff -ruN base work > patch-round%s.diff`"
      % (r, fix, r))
print("   檔頭只准 `base/…` / `work/…` 的相對形式;刪檔的 `+++` 側要改成 `/dev/null`。")
print("2. `EVIDENCE-round%s.md` —— 必備五段(見角色卡),其中**已排除的假設**那一段"
      % r)
print("   決定下一輪的人要不要把你查過的路再查一次。")
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。")
print("看到這一行,這支腳本會把它記成票的 `objections[]`、把票轉 Blocked 並指派主線,"
      "**不會**再派下一輪。")
print("**不准**放寬既有斷言、不准把期望值改成程式現在印的東西(那是假綠家族)。")
PY
        } > "$DISPATCH"
    fi
    echo "auto-fix: 派工文 -> $(python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$DISPATCH" "$ROOT")"

    if [ -n "$DRY" ]; then
        echo "auto-fix: --dry-run —— 不起 worker。派工文在上面那一份。"
        ROUND_RC=0
        return 1
    fi

    echo "auto-fix: 起第 $r 輪的 worker —— $WORKER_CMD(cwd $FIX,派工文從 stdin 餵)"
    # 第 1 輪:票 Ready → Running(#40)。擺在 `--dry-run` 那一手**之後**(dry-run 不動票)、
    # 起 worker **之前**(worker 跑的時候票就該是 Running)。第 2 輪起票已經不是 Ready 了。
    if [ "$r" -eq 1 ]; then
        python3 "$AC/ticket.py" set "$ID" state Running >/dev/null 2>&1 \
            || echo "auto-fix: 票狀態改不動(Running)" >&2
    fi
    ev ticket.attempt.start --ticket "$ID" --attempt "$r" --note "auto-fix 第 $r 輪"
    WORKER_LOG=$(dirname "$DISPATCH")/worker-round$r.log
    ev agent.start --ticket "$ID" --model "$WORKER_MODEL" \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix
    WSTART=$(date +%s)
    WRC=$(python3 - "$WORKER_CMD" "$DISPATCH" "$FIX" "$WORKER_TIMEOUT" "$ID" "$r" "$WORKER_LOG" <<'PY'
import subprocess, sys
cmd, dispatch, cwd, timeout, ident, r, log_path = sys.argv[1:8]
env_extra = {"AC_DISPATCH": dispatch, "AC_TICKET": ident, "AC_ROUND": r,
             "AC_WORK": cwd, "AC_ROLE": "worker"}
import os
env = dict(os.environ)
env.update(env_extra)
try:
    with open(dispatch, encoding="utf-8") as handle, open(log_path, "w", encoding="utf-8") as log:
        done = subprocess.run(cmd, shell=True, cwd=cwd, env=env, stdin=handle,
                              stdout=log, stderr=subprocess.STDOUT, timeout=float(timeout))
    rc = done.returncode
except subprocess.TimeoutExpired:
    sys.stderr.write("auto-fix: worker 超過 %s 秒還沒回來 —— 當它沒交\n" % timeout)
    rc = 124
except OSError as exc:
    sys.stderr.write("auto-fix: worker 起不來 —— %s\n" % exc)
    rc = 127
print(rc)
PY
)
    if [ "$WRC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --model "$WORKER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix
    else
        ev agent.failed --ticket "$ID" --model "$WORKER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix
    fi
    write_cost worker "$r" "$WORKER_MODEL" "$WORKER_LOG" "$(( $(date +%s) - WSTART ))"
    [ "$WRC" -eq 0 ] || echo "auto-fix: worker 自己回非零 —— 還是看它交了什麼,不看它說什麼"

    collect_from_copy "$FIX" "patch-round$r.diff" "EVIDENCE-round$r.md"
    PATCH_OUT=$FIX/patch-round$r.diff
    EVIDENCE=$FIX/EVIDENCE-round$r.md

    # **收 patch 的同一手**把 EVIDENCE 尾端那一塊抽出來。位置在反駁那一段**之前**:
    # 反駁那條路會直接 return,而那一輪一樣要留得下一份可讀的結果。
    RESULT_JSON=$(dirname "$DISPATCH")/result-round$r.json
    harvest_result "$EVIDENCE" "$RESULT_JSON" worker "$r"
    echo "auto-fix: 結構化交付 -> $(basename "$RESULT_JSON")"

    # `base/` 只剩一個用途:`test_defect` 那條路要拿它做驗證者的副本。用**與底下那一段
    # 同一個判準**問一次(類別那個字),不是「反正留著」—— 留著的那一份就是沒人收的那一份。
    if [ -f "$EVIDENCE" ] && grep -qE '^OBJECTION:[[:space:]]*test_defect' "$EVIDENCE"; then
        echo "auto-fix: 先留著 $FIX/base —— 驗證者的副本要從它做"
    else
        shed_copies "$FIX"
    fi

    CASE_FIXED=""
    # 反駁比 patch 先看:worker 說「這張票寫錯了」而東西照樣落地,那句話等於沒人收。
    if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
        line=$(grep -m1 '^OBJECTION:' "$EVIDENCE")
        echo "auto-fix: worker 提了反駁 —— $line"
        # **與 `apply.sh` 共用同一支收件**(#29 A4):實作在 `ticket.py objection`,
        # 它記過的同一筆不會再記第二次 —— 兩邊各記一次的話,同一句話會在票上長成兩筆,
        # 而處置的人分不出哪一筆是哪一輪的。
        python3 "$AC/ticket.py" objection "$ID" --line "$line" --evidence "$EVIDENCE" \
            >/dev/null 2>&1 || true
        category=$(python3 - "$line" <<'PY'
import sys
rest = sys.argv[1].split(":", 1)[1].strip() if ":" in sys.argv[1] else sys.argv[1].strip()
parts = rest.split(None, 1)
print(parts[0] if parts and parts[0] in ("ticket-wrong", "test_defect", "blocking")
      else "ticket-wrong")
PY
)
        if [ "$category" = "test_defect" ]; then
            if ! dispatch_verifier; then
                attempt_failed no-patch
                ROUND_RC=5
                return 1
            fi
        else
            block "#$ID 的 worker 提反駁:$line"
            post "worker 提反駁(票寫錯 / 需裁示)" \
                 "讀 EVIDENCE 那一行反駁,處置它(accepted / rejected / deferred / fixed);沒處置的阻擋項 land 與 close 都會拒絕" \
                 "$EVIDENCE"
            attempt_failed objection
            ROUND_RC=3
            return 1
        fi
    fi

    if [ ! -f "$PATCH_OUT" ]; then
        echo "auto-fix: 第 $r 輪的 worker 沒有交出 patch-round$r.diff" >&2
        block "#$ID 第 $r 輪的 worker 沒交出 patch"
        post "worker 沒交出 patch" "自己看副本裡有什麼;要嘛重派,要嘛人下場" "$FIX"
        if [ "$WRC" -eq 124 ]; then attempt_failed timeout; else attempt_failed no-patch; fi
        ROUND_RC=5
        return 1
    fi

    AC_ROUND=$r AC_PREV_EVIDENCE=$EVIDENCE AC_RESULT_DONE=1 \
        sh "$AC/apply.sh" "$ID" "$PATCH_OUT" --evidence "$EVIDENCE"
    arc=$?
    if [ "$arc" -ne 0 ]; then
        echo "auto-fix: 第 $r 輪的 patch 套不上(apply rc=$arc)" >&2
        post "第 $r 輪的 patch 套不上(apply rc=$arc)" \
             "看 apply 的輸出:檔頭不合格、主線走遠(走 apply.sh rebase),還是越界" \
             "$PATCH_OUT"
        attempt_failed apply-failed
        ROUND_RC=5
        return 1
    fi
    if [ -n "$CASE_FIXED" ]; then
        python3 - "$TF" "$ID" "$PATCH_OUT" <<'PY'
import json, os, subprocess, sys
path, ident, patch = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    ticket = json.load(handle)
rows = ticket.get("objections") or []
for row in reversed(rows):
    if row.get("category") == "test_defect" and not row.get("disposition"):
        row["disposition"] = "fixed"
        row["follow_up"] = os.path.relpath(patch, os.environ["AC_ROOT"])
        break
subprocess.run([sys.executable, os.path.join(os.environ["AC_CONTROL_DIR"], "ticket.py"),
                "set", ident, "objections", json.dumps(rows, ensure_ascii=False)], check=False)
PY
    fi

    if [ -n "$RERUN_CMD" ]; then
        gate_cmd=$RERUN_CMD
    else
        gate_cmd="sh scripts/gate.sh --branch --ticket $ID"
        echo "auto-fix: 警告:沒有設 gate.rerun_cmd —— 退回 $gate_cmd" >&2
    fi
    echo "auto-fix: 第 $r 輪的閘門 —— (cd $WT && $gate_cmd)"
    ev gate.rerun --ticket "$ID" --attempt "$r" --note "$gate_cmd" \
        --kv run_id="$RUN_ID" --kv round="$r"
    # `AC_ROOT=$ROOT`:閘門在**副本**裡跑,但狀態檔與收件匣要寫回**主 repo**
    # —— 不然這一輪的結果留在一個等一下會被收掉的目錄裡,而讀它的人在主 repo。
    ( cd "$WT" && AC_ROOT=$ROOT AC_WT=$WT AC_ROUND=$r AC_TICKET=$ID \
        AC_PATCH=$PATCH_OUT \
        AC_PREV_EVIDENCE=$EVIDENCE AC_IN_AUTOFIX=1 \
        sh -c "$gate_cmd" )
    grc=$?
    # 閘門跑過了 —— 綠或紅,這一次派工都算收工(紅的下一輪是另一個 attempt)。
    ev ticket.attempt.done --ticket "$ID" --attempt "$r" --kv rc="$grc"
    if [ "$grc" -eq 0 ]; then
        python3 "$AC/ticket.py" round "$ID" "$r" --green || true
        python3 "$AC/ticket.py" set "$ID" state InReview >/dev/null 2>&1 || true
        read_status
        RUN_ID=$S_RUN
        if [ -n "$NO_REVIEW" ]; then
            echo "auto-fix: 第 $r 輪綠了 —— --no-review,覆核不派,票停在 InReview:sh scripts/review.sh $ID"
        else
            echo "auto-fix: 第 $r 輪綠了 —— 覆核交給 review.sh(sh scripts/review.sh $ID)"
            sh "$AC/review.sh" "$ID" --run-id "$RUN_ID" \
                || echo "auto-fix: 覆核停下來了(rc=$?)—— 看 reports/inbox/ 那一頁"
        fi
        ROUND_RC=0
        return 1
    fi
    python3 "$AC/ticket.py" round "$ID" "$r" --red || true
    read_status
    RUN_ID=$S_RUN
    if [ -n "$CASE_FIXED" ]; then
        post "案例已修,第 $r 輪仍紅" \
             "auto-fix 會帶新紅榜進下一輪;主線不用自己判斷或改案例" \
             "reports/t$ID/$RUN_ID/status.json"
    fi
    ROUND_RC=1
    return 0
}

# 迴圈從 `S_ROUND+1` 起,碰不到前面那幾輪的副本 —— 主線用 Agent 手建的第 1 輪就在這裡
# (#38 A4)。派下一輪之前一併收;先撿再刪,同 `round_once`。`--dry-run` 不刪東西。
if [ -z "$DRY" ]; then
    p=1
    while [ "$p" -le "$S_ROUND" ]; do
        collect_from_copy "$WTBASE/fix-t$ID/round$p" "patch-round$p.diff" "EVIDENCE-round$p.md"
        shed_copies "$WTBASE/fix-t$ID/round$p"
        p=$((p + 1))
    done
fi

r=$((S_ROUND + 1))
while :; do
    ROUND_RC=0
    if round_once "$r"; then
        # 還紅,而且沒有停下來的理由 —— 看還有沒有下一輪。
        if [ "$S_FAILS" -eq 0 ]; then
            echo "auto-fix: 第 $r 輪 rc 非零卻解析不出紅榜 —— 沒有歸因,停" >&2
            block "#$ID 第 $r 輪 rc 非零但 failures 是空的"
            post "紅了但沒有歸因" "自己看 log:紅榜是空的" \
                 "reports/t$ID/$S_RUN/status.json"
            exit 4
        fi
        if [ "$r" -ge "$S_LIMIT" ]; then
            echo "auto-fix: 第 $r 輪仍紅,上限 $S_LIMIT —— 票已轉 Blocked、指派主線"
            post "三輪耗盡仍紅" "這張票由你接手:讀紅榜決定要改票面還是換做法" \
                 "reports/t$ID/$S_RUN/status.json"
            exit 1
        fi
        r=$((r + 1))
        continue
    fi
    exit "$ROUND_RC"
done
