#!/bin/sh
# 回歸紅了自動派**新** worker — `docs/WORKFLOW.md` §回歸紅了之後(D-010、D-015)。
#
#   sh scripts/auto-fix.sh <票號>                       # 讀最新狀態檔,紅就派下一輪;
#                                                       # 票 Ready 且沒有狀態檔 → 起第 1 輪
#   sh scripts/auto-fix.sh <票號> --dry-run             # 只印派工文,不起 worker
#   sh scripts/auto-fix.sh <票號> --dry-run --round 1   # 第 1 輪的派工文(還沒有狀態檔時)
#   sh scripts/auto-fix.sh <票號> --no-review           # 綠了票停在 InReview,不叫 review.sh
#   AC_BATCH_RUN_ID=<run> sh scripts/auto-fix.sh --batch <整合分支> <票號…> [--dry-run] [--no-review]
#                                                       # 批次修復一輪(#108;見檔尾 batch_main)
#
# 閘門與單票落地預設會叫這一支;`--no-auto-fix` 才停給人處理。
#
# ## 第 1 輪也由這一支起(#40,D-025 C1)
# 票 **Ready 且沒有狀態檔** ⇒ 走同一個 `round_once 1`:派工文就是 `--dry-run --round 1`
# 那一份、票分支的 worktree 從主線頭開(#144;base_sha 落後先升到主線頭)、`ticket.attempt.start`
# 由這裡發、票轉 Running;之後收件(apply)→ gate → InReview → review.sh 與第 2 輪起是同一段。
# 以前第 1 輪被「一輪都還沒跑過」的 exit 2 擋在門外,主線要自己派 worker、apply、
# 帶 `AC_TICKETS_DIR` 跑閘門 —— 同一件事兩個入口。票不是 Ready 又沒有狀態檔 ⇒
# 指名 state 停下(rc=2),不猜。
#
# ## 為什麼不叫醒舊的 worker
# 它醒來一次 = 累積的整份上下文重送一輪(D-010)。所以每一輪都是**新的** worker,
# 而它手上只有一份檔:狀態檔的 `repair_context`(base_sha、票面快照、worktree、
# 第幾輪、上一輪的 EVIDENCE、重現指令)加票分支上前幾輪的 commit。少一格,它就得回頭翻對話或猜檔案位置。
#
# ## 三種停下來(每一種都寫一則 inbox,kind=decision —— 不是印一行就算)
# 1. worker 說**票寫錯 / 需要裁示** —— 它在 EVIDENCE 裡寫一行 `OBJECTION: <類別> <理由>`;
#    這一支把它記成票的 `objections[]`;`ticket-wrong` / `blocking` 先交給開題者判(#89,
#    D-041,見 `triage_objection`),判 accepted(票面改好)就接回原 session 續做、不停;
#    判 escalate(帶一題附建議的裁示題)、同票第二次、開題者沒判出來、session 接不回,
#    才票轉 Blocked、owner=main,發 `decision.asked`。
# 2. **修補輪用完仍紅**(頁名沿用「三輪耗盡」)—— 准幾輪只有 `ticket.py repair_limit` 一份算法
#    (#125 A1,D-050 ②):票的 `retry_limit`+1,沒寫 = `repair.max_rounds`(預設 3)+1;
#    `ticket.py round` 在那一輪把票轉 Blocked、owner=main。診斷輪算一輪,環境重跑不算。
# 3. **診斷輪之後仍沒有歸因 / 仍環境可疑**(#125 A3/A4,D-076)—— rc 非零卻一條紅都解析不出來
#    (log 壞了、跑都沒跑起來),或閘門說環境可疑(rc 86)。它看起來像「沒有紅」,拿空紅榜去猜的
#    worker 會改到沒壞的地方 —— 所以第一次**不照紅榜修**:環境可疑先機械重跑閘門
#    (`repair.env_rerun`,不算一輪),仍可疑或本來就沒有歸因就派**一輪診斷**(派工文 `## 診斷`
#    取代紅榜,`ticket.attempt.start` 帶 `round_kind=diagnosis`)。診斷輪之後還是一樣才停、一頁。
#    **閘門硬擋命中**(#90)不是這一種:測試沒起所以紅榜空,但 gate 狀態檔的 note 寫著
#    是哪一條 —— 頁的狀態格是「閘門硬擋命中」,歸因到交件(命中 `hard_block` 的改動),不重跑、不診斷。
#
# **覆核由 `review.sh` 派**(#42,D-025 ②),**只派 `complexity=complex` 的票**(#136,VISION
# 流程 #9):綠了之後票轉 `InReview`(寫在票 JSON)、complex 票再叫 `sh scripts/review.sh
# <票號>` —— reviewer pass 由它寫 `review`,fail 由它轉 Blocked 回主線裁示。simple 或沒標(= simple)
# 的票不派覆核、不停在等覆核:停在 InReview 等 land(land 不要它的 review),印
# `終態=green_no_review`。這一支自己不碰 `review` 那一格;`--no-review` 讓 complex 票停在
# InReview(主線手跑 review.sh)。綠了**不發頁**(D-032):接著的事是腳本做的,收件匣只收
# decision / done。覆核的結局**改**這一支的退出碼(#121 A6):fail 9、沒交件 10;complex 票
# `--no-review` 回 0 並印 `終態=awaiting_review` —— 閘門綠、還沒覆核,不是可落地。
#
# ## 退出碼
#   0 綠了且覆核 pass(simple 票:綠了就是;或 --no-review:awaiting_review)   1 修補輪用完仍紅   2 用法 / 沒有狀態檔而票不是 Ready
#   3 worker 提反駁       4 診斷輪之後仍沒有歸因 / 仍環境可疑,或閘門硬擋命中(頁的狀態格分得出)
#   5 worker 沒交件(#144:票分支沒有新 commit,或 commit 了又留著未 commit 的改動)、收件沒過
#   6 規則包產不出來(#74):worker / 驗證者一個都沒起;缺項在 rules.py 送的 decision 頁
#   7 角色表的候選整串耗盡(#121 A3:票 Blocked、一頁 decision、不再起程序),或額度後的
#     下一個候選交給 Controller / 別的驅動者接續(副本留著)
#   8 agent 越界(#115,D-048;規則 D-064 / D-065,`main_guard.py`):agent 跑的前後比四樣 —— 主線 HEAD、
#     origin 的 ref、主 repo 工作樹(porcelain + 髒檔雜湊)、hard_block.dirs 底下的檔。沒有逐 commit 歸屬:
#     HEAD 前進而期間有主線落地只記不擋(紀錄列出不屬於任何落地的 commit);沒有落地卻前進、被改寫、
#     origin 被推、工作樹在控制面與 guard_main_paths 之外有變、hard_block.paths 或 hard_block.dirs 有變
#     ⇒ 票轉 Blocked、owner=main,一頁 decision,不收件、worktree 與副本留著(與額度同時發生也是 8)
#   9 閘門綠、覆核 fail   10 閘門綠、覆核沒交件 / stale / 沒完成(#121 A6)
set -u
# 專案根不從 `$0` 來(D-071 C2,#159):`AC_ROOT`,否則 cwd 的主 repo 根,否則 rc 2 ——
# 規則只有一份,在 `wtbase.sh`。控制腳本自己住的目錄(A 的 clone)才從 `$0` 算;
# 同伴腳本一律從這裡叫,不寫死 $ROOT/scripts。
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_CONTROL_DIR=$AC
. "$AC/wtbase.sh"
ROOT=$(ac_root auto-fix) || exit 2
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
# `--batch <整合分支> <票號…>`:票號收進 B_IDS,第一張當 `ID`(票檔、模型、逾時、成本都照它),
# 旗標交回底下同一段解析。
BATCH=""
if [ "$1" = "--batch" ]; then
    shift
    [ $# -ge 2 ] || { echo "用法:AC_BATCH_RUN_ID=<run> sh scripts/auto-fix.sh --batch <整合分支> <票號…> [--dry-run] [--no-review]" >&2; exit 2; }
    BATCH=1
    B_BR=$1
    shift
    B_IDS=""
    B_FLAGS=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --dry-run|--no-review) B_FLAGS="$B_FLAGS $1" ;;
            -*) echo "auto-fix: --batch 不認得 $1(--dry-run / --no-review)" >&2; exit 2 ;;
            *) B_IDS="$B_IDS $1" ;;
        esac
        shift
    done
    B_IDS=${B_IDS# }
    [ -n "$B_IDS" ] || { echo "auto-fix: --batch 至少要一張票號" >&2; exit 2; }
    set -- "${B_IDS%% *}" $B_FLAGS
fi
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
        --batch) echo "auto-fix: --batch 要放在最前面:sh scripts/auto-fix.sh --batch <整合分支> <票號…>" >&2; exit 2 ;;
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
with_envelope() {   # $1 = 命令;`claude` / `agy` 起頭而沒指定 --output-format 的,補 stream-json
    # 信封是 `cost[]` 的 token 唯一來源(#58):純文字輸出沒有 usage,那一輪的 token 只能記
    # null —— 下游設定沒帶這一格的票,worker / verifier 的 token 全是 null。已經指定格式
    # (json / stream-json)的照用;不是 `claude` 的命令(替身、別家 CLI)不動。
    # worker 的交件是它寫在副本裡的檔,不經 stdout —— 換輸出格式不影響交件。
    # 補的是 **stream-json**(#64):`json` 只在結束時印一個信封,被砍的那一輪 log 是 0 byte;
    # stream-json 逐行印事件,最後一行 `type=result` 就是同一個信封(`ticket.py cost` 照讀)。
    # `-p` 配 stream-json 不帶 `--verbose`,claude 直接 rc=1 拒跑(#64 A7,2026-09-29 實測)。
    python3 - "$1" <<'PY'
import os, shlex, sys
cmd = sys.argv[1]
try:
    parts = shlex.split(cmd)
except ValueError:
    parts = cmd.split()
head = next((tok for tok in parts if "=" not in tok), "")
name = os.path.basename(head)
if name in ("claude", "agy") \
        and not any(tok.split("=", 1)[0] == "--output-format" for tok in parts):
    cmd += " --output-format stream-json"
    if name == "claude" and "--verbose" not in parts:
        cmd += " --verbose"
print(cmd)
PY
}

# config 的 worker.command(claude 那一句)。驗證者沒設 verifier.command 時跟它同一支 ——
# 票是 codex 的時候,codex 只換 worker 那一支(`WORKER_CMD`,下面依票決定)。
BASE_WORKER_CMD=$(with_envelope "$(cfg worker.command "claude -p --model opus")")
WORKER_CMD=$BASE_WORKER_CMD
RERUN_CMD=$(cfg gate.rerun_cmd "")
# 環境可疑時機械重跑閘門幾次(#125 A4):`repair.env_rerun`,預設只在 project_config 那一份。
ENV_RERUN=$(python3 - "$AC" "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import event, project_config
print(project_config.repair_setting(event.config(sys.argv[2]), "env_rerun"))
PY
) || ENV_RERUN=1
PRE_RED=""
DIAG=""
TF=$TDIR/$ID.json
# 主 repo 根與副本根 **只有一種定義**(#38,#46,D-018):`scripts/wtbase.sh`(檔頭已載入)。
MAINROOT=$(main_root)
# 票分支的 worktree **不是**控制根(G10 / #29 A10):cwd 規則本來就指回主 repo,但呼叫端
# 給的 `AC_ROOT` 可能是 worktree 自己,而票檔要在**那一條分支上進了版控**才找得到 ——
# 票檔是走 docs 通道進主線的,常常還沒進去(#23 第 2 輪就是這樣 rc=2 停掉的)。
# 所以找不到票時改問主 repo(`wtbase.sh` 的 `main_root`)。
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
# worker 與驗證者的逾時秒數**只解析這一次**(#64):票的 `worker.timeout_seconds` >
# `board/config.json` 的 `worker.timeout_seconds` > 3600。以前只讀 config,票上寫 7200 的
# 那一張照樣在 3600 被砍。票的值不是正數就不採用、點名那一格,退回下一層。
# 印出來的是「秒數 來源」兩個字,`逾時來源=` 那一行在起 worker 時印。
TIMEOUT_LINE=$(python3 - "$TF" "$(cfg worker.timeout_seconds "")" <<'PY'
import json, sys
path, conf = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        worker = json.load(handle).get("worker")
except (OSError, ValueError):
    worker = None
if isinstance(worker, dict) and "timeout_seconds" in worker:
    value = worker["timeout_seconds"]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        print("%g 票" % value)
        sys.exit(0)
    sys.stderr.write("auto-fix: 票的 worker.timeout_seconds=%s 不是正數 —— 不採用,退回 config / 預設\n"
                     % json.dumps(value, ensure_ascii=False))
print("%s config" % conf if conf else "3600 預設")
PY
)
WORKER_TIMEOUT=${TIMEOUT_LINE%% *}
TIMEOUT_SRC=${TIMEOUT_LINE#* }
# worker 票的 model / tool 是本票第一候選(#131),後接角色表並去重;沒寫才照原角色鏈。
# 兩格不一致仍在動票之前停(#87 A21)—— 票面自相矛盾,照哪一格猜都可能起錯。
eval "$(python3 - "$TF" <<'PY'
import json, shlex, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
print("T_MODEL=%s" % shlex.quote(str(ticket.get("model") or "")))
print("T_TOOL=%s" % shlex.quote(str(ticket.get("tool") or "")))
print("TK_EFFORT=%s" % shlex.quote(str(ticket.get("effort") or "")))
print("T_COMPLEXITY=%s" % shlex.quote(str(ticket.get("complexity") or "")))
PY
)"
# 原 worker 的模型 = 票 cost[] 最後一列沒撞額度的 worker(修補輪沿用,A7)。迴圈每接一輪重讀一次。
last_worker() {
    python3 - "$TF" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
worker = [row for row in ticket.get("cost") or []
          if isinstance(row, dict) and row.get("role") == "worker" and row.get("outcome") != "error"]
print(str(worker[-1].get("model") or "") if worker else "")
PY
}
T_LAST_WORKER=$(last_worker)
# 票的分支名**只有一個來源:票的 `branch` 欄**(#53,D-018;`apply.sh` 收件時填)。
# 空著才退回 `t<n>` —— 下游專案用 `t<n>-<slug>` 的票,寫死 `t$ID` 就找不到分支。
ticket_branch() {
    python3 - "$TF" "t$ID" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        name = json.load(handle).get("branch")
except (OSError, ValueError):
    name = None
print(name or sys.argv[2])
PY
}
if [ "$T_TOOL" = codex ] || [ "${T_MODEL#codex:}" != "$T_MODEL" ] \
        || [ "$T_TOOL" = agy ] || [ "${T_MODEL#agy:}" != "$T_MODEL" ]; then
    python3 "$AC/ticket.py" agent-command --model "$T_MODEL" --tool "$T_TOOL" >/dev/null || {
        echo "auto-fix: #$ID 票的 model=$T_MODEL 與 tool=$T_TOOL 對不起來 —— 不派、票不動" >&2
        exit 2
    }
fi
# 副本/worktree 的根:環境變數 > board/config.json 的 `worktree_dir` > 預設 `../<主 repo>-wt`;
# 相對路徑一律以**主 repo 根**解析(#38)。以前用 `$ROOT` 拼:在票 worktree 裡跑時 `$ROOT`
# 是 worktree,副本開到 `x-wt/x-wt/` 底下(9/23 實際開在 `agent-control-wt/agent-control-wt/`)。
WTBASE=$(wtbase)
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

post() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看  $4 = 補充(可空)
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "${RUN_ID:-}" \
        --kind decision --state "$1" --what "$2" --where "$3" ${4:+--note "$4"} \
        || echo "auto-fix: inbox 寫不出來($1)" >&2
}

# 修補輪用完(#125 A2,D-050 ②):`ticket.py round` 轉 Blocked、owner=main,一頁 decision,rc 1。
rounds_exhausted() {   # $1 = 剛紅的是第幾輪  $2 = recorded(round_receive 已經記過這一輪的紅)
    echo "auto-fix: 第 $1 輪仍紅(上限 $S_LIMIT 輪)—— 不再派,票轉 Blocked、指派主線"
    # 同一輪記兩次紅會把介入次數算兩次(count_intervention)—— 迴圈裡那一手已經記過了。
    [ "${2:-}" = recorded ] || python3 "$AC/ticket.py" round "$ID" "$1" --red >/dev/null || true
    # 頁名沿用「三輪耗盡」(停下來那一種的名字,inbox / 測試 / 文件都認它);輪數寫實數。
    post "三輪耗盡仍紅(第 $1 輪,上限 $S_LIMIT 輪)" \
         "這張票由你接手:讀紅榜決定要改票面還是換做法(上限 = repair_limit:retry_limit+1,沒寫 = repair.max_rounds+1)" \
         "reports/t$ID/$S_RUN/status.json"
    exit 1
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
# 要讀它的人已經在那個目錄了。鍵名**不寫在這一支**:schema 只有一份正文
# (`tickets/SCHEMA.md`;`docs/DISPATCH-TEMPLATE.md` §8.5 只指路,D-054 Q12),抄第二份的那一天,
# 兩份會各自往不同方向漂,而漂開的那一份看起來仍然像規格。
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

verifier_plan_given() {   # $1 = EVIDENCE-verifier;result 的 verify 有 files/tags/run/notes 任一格才算有計畫(#130)
    # 解析用 `ticket.py` 的 `result_block`(與 apply 併計畫那一段同一份判準);解析不到 = 沒有計畫。
    python3 - "$AC" "$1" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import ticket
try:
    with open(sys.argv[2], encoding="utf-8") as handle:
        raw = ticket.result_block(handle.read().splitlines())
    data = json.loads(raw) if raw is not None else None
except (OSError, ValueError):
    data = None
plan = data.get("verify") if isinstance(data, dict) else None
given = isinstance(plan, dict) and any(plan.get(key) for key in ("files", "tags", "run", "notes"))
raise SystemExit(0 if given else 1)
PY
}

write_cost() {   # $1 = 角色  $2 = 第幾輪  $3 = 模型  $4 = 信封(log)  $5 = 量到的秒數  $6 = 結局  $7 = effort(可空)  $8 = note(可空)
    # 一次 headless 派工的 token 與時鐘進票的 `cost[]`(D-032)。信封在 log 裡:stdout 與
    # stderr 混寫,`ticket.py cost` 取最後一行 `type=result` 的那一行;讀不到就記 null。
    # 結局只收登記表的字(#94 C2,tickets/SCHEMA.md 成本那一列)。
    python3 "$AC/ticket.py" cost "$ID" --role "$1" --round "$2" --model "$3" \
        --by auto-fix.sh --from-envelope "$4" --wall-seconds "$5" \
        --outcome "$6" --run-id "$RUN_ID" --effort "${7:-}" ${8:+--note "$8"} >/dev/null \
        || echo "auto-fix: #$ID 的 cost 寫不進票($1 第 $2 輪)—— 不擋流程" >&2
}

worker_cost() {   # $1 = 結局  $2 = note(可空);run_worker 記下的那一次 worker 寫一列(一次一列,寫過就清掉)
    # worker 的結局要等收件才知道(#94 C5a),所以 run_worker 只記秒數,這裡才寫。
    [ -n "${W_COST_LOG:-}" ] || return 0
    write_cost worker "$r" "$WORKER_MODEL" "$W_COST_LOG" "$W_COST_SECS" "$1" "$T_EFFORT" "${2:-}"
    W_COST_LOG=""
}

attempt_failed() {   # $1 = no-patch | apply-failed | timeout | objection | verifier-no-patch
    # 每一個 `ticket.attempt.start` 都要有配對的 done / failed:heartbeat.sh 按 attempt 配對,
    # 沒配上的那一輪永遠算「還在跑」。
    ev ticket.attempt.failed --ticket "$ID" --attempt "$r" --kv reason="$1"
}

# 越界守衛的收件(#115):`run_agent` 寫的 `<log 去掉 .log>.guard.json` 裡 violations 不空就是越界
# (violations 空的那一份是只記不擋的紀錄:main_side 與主線落地期間的 HEAD 前進,#128、#153)。這一輪
# 每一個 agent 的報告併成**一頁**;票轉 Blocked、owner=main、attempt 以 guard-violation 收;
# 不收件、worktree 與副本不收(下一個人要從它看做了什麼)。額度那一手排在它後面(A7)。
guard_stop() {   # $1… = 這一輪 agent 的 log(空字串略過);有越界回 0(ROUND_RC=8),沒有回 1
    G_OUT=$(python3 - "$@" <<'PY'
import json, os, sys
said, where = [], []
for log in sys.argv[1:]:
    path = os.path.splitext(log)[0] + ".guard.json" if log else ""
    if not path or not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    rows = report.get("violations") or []
    if not rows:
        continue
    said.append("%s:%s" % (report.get("role") or "?", ";".join(
        "%s(前 %s → 後 %s%s)" % (row.get("path"), row.get("before"), row.get("after"),
                                ";" + row["why"] if row.get("why") else "")
        for row in rows[:5]) + (" 等 %d 處" % len(rows) if len(rows) > 5 else "")))
    where.append(path)
if not said:
    raise SystemExit(1)
print(" | ".join(said))
print(" ".join(where))
PY
) || return 1
    G_WHAT=$(printf '%s\n' "$G_OUT" | sed -n 1p)
    G_WHERE=$(printf '%s\n' "$G_OUT" | sed -n 2p)
    echo "auto-fix: #$ID 第 $r 輪 agent 越界 —— $G_WHAT;不收件、worktree 與副本留著" >&2
    G_SCOPE=""
    if [ -n "${BATCH:-}" ]; then
        G_SCOPE="整批 ${B_TAGS}:"
        # 批次平常不寫票;越界是例外:整批每一張都轉 Blocked 回主線。批次沒有 attempt.start,
        # 所以不發 attempt.failed(沒配對的那一筆會讓 heartbeat 以為有一輪)。
        for b_id in $B_IDS; do
            python3 "$AC/ticket.py" set "$b_id" state Blocked >/dev/null 2>&1 \
                || echo "auto-fix: #$b_id 的狀態改不動(Blocked)" >&2
            python3 "$AC/ticket.py" set "$b_id" owner main >/dev/null 2>&1 || true
        done
        ev decision.asked --ticket "$ID" --note "批次 $B_TAGS 的 worker 越界(guard-violation)"
    else
        block "#$ID 第 $r 輪 agent 越界(guard-violation)"
    fi
    post "agent 越界(guard-violation),副本留著" \
         "${G_SCOPE}改到守衛範圍內的東西:${G_WHAT}。沒有收件;前後證據在 ${G_WHERE}。查清楚、還原或認可後再派" \
         "$G_WHERE"
    [ -n "${BATCH:-}" ] || attempt_failed guard-violation
    G_STOPPED=1
    ROUND_RC=8
    return 0
}

shed_copies() {   # $1 = 副本根(fix-t<n>/round<r> 或 verify-t<n>/round<r>)  $2 = 那一輪 worker 的 worktree(可空)
    # **副本自己收**(#29 A6,G6):`patch` / `EVIDENCE` / `dispatch` / `result` 留著,
    # `work/` 與 `base/` 刪掉。以前它們只在**下一輪開始**才 `rm -rf`,綠了停 InReview
    # 就永遠留著 —— 而「沒人收」與「收過了」在磁碟上長得一樣,直到滿的那一刻
    # (2026-09-16 某個下游專案的副本 14 GB 塞滿磁碟,整台機器 disk I/O error)。
    [ -n "${1:-}" ] || return 0
    [ -d "$1/work" ] || [ -d "$1/base" ] || [ -d "$1/control" ] || [ -d "$1/ref" ] || return 0
    # `control/` 是起 agent 時開的拋棄式控制根(`run_agent`):agent 的副作用在那裡,一起收。
    # `ref/` 是驗證者量紅的票 base_sha 樹(#182)。
    rm -rf "$1/work" "$1/base" "$1/control" "$1/ref"
    echo "auto-fix: 收掉副本 $1/{work,base}(patch 與 EVIDENCE 留著)"
    # 副本沒了,在它裡面跑的 session 就接不回了(#89):sessions.json 那一列一起轉 closed。
    # worker 在票分支的 worktree 裡跑(#144):worktree 留到落地,但這一輪收件了,下一輪是新的
    # worker —— 那一列一起轉 closed。
    session_close "$1" "${2:-}"
}

# ## agent 的 session(#89,D-041)
# 反駁要接回**同一個** worker / 驗證者 session 在原副本續做,所以 session id 由這裡先指定
# (`--session-id <uuid4>`),不從輸出撈 —— 撈不到就沒得接。每一列記在
# `reports/t<n>/sessions.json`:`cwd` 是 agent 起的目錄相對主 repo 根 —— 驗證者是副本的 `work/`,
# worker 是票分支的 worktree(#144)(工具的 cwd 是當下目錄,接回也要在那裡)。命令不是 `claude` 起頭、或帶
# `--no-session-persistence` 的,不補旗標、該列 `resumable: false` 帶 `why`。
SESSIONS=$ROOT/$(cfg reports_dir reports)/t$ID/sessions.json

session_open() {   # $1 = 角色  $2 = 命令  $3 = 副本根  $4 = 第幾輪  $5 = 模型  $6 = 起的目錄(空 = 副本根的 work/);印出要起的命令
    python3 - "$SESSIONS" "$ROOT" "$1" "$2" "$3" "$4" "$5" "${6:-}" <<'PY' || printf '%s\n' "$2"
import json, os, shlex, sys, uuid
path, root, role, cmd, copy, rnd, model, start = sys.argv[1:9]
try:
    parts = shlex.split(cmd)
except ValueError:
    parts = cmd.split()
head = os.path.basename(next((tok for tok in parts if "=" not in tok), ""))
row = {"session_id": None,
       "cwd": os.path.relpath(os.path.realpath(start or os.path.join(copy, "work")),
                              os.path.realpath(root)),
       "round": int(rnd), "model": model, "state": "open", "resumable": True}
if head != "claude":
    row["resumable"] = False
    row["why"] = "命令是 %s 不是 claude —— 沒有指定得了的 session,不能接回" % (head or "(空)")
elif "--no-session-persistence" in parts:
    row["resumable"] = False
    row["why"] = "命令帶 --no-session-persistence —— session 不存檔,不能接回"
else:
    row["session_id"] = str(uuid.uuid4())
    cmd += " --session-id " + row["session_id"]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
data[role] = row
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
print(cmd)
PY
}

session_field() {   # $1 = 角色  $2 = 欄位;沒有就印空字串
    python3 - "$SESSIONS" "$1" "$2" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        row = json.load(handle).get(sys.argv[2]) or {}
except (OSError, ValueError):
    row = {}
value = row.get(sys.argv[3])
print("" if value is None else (str(value).lower() if isinstance(value, bool) else value))
PY
}

session_close() {   # $1 = 副本根  $2 = worker 的 worktree(可空);`cwd` 是其中之一而還開著的那幾列轉 closed
    [ -f "$SESSIONS" ] || return 0
    python3 - "$SESSIONS" "$ROOT" "$1" "${2:-}" <<'PY'
import datetime, json, os, sys
path, root, copy, worktree = sys.argv[1:5]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
cwds = {os.path.relpath(os.path.realpath(where), os.path.realpath(root))
        for where in (os.path.join(copy, "work"), worktree) if where}
hit = False
for row in data.values():
    if isinstance(row, dict) and row.get("cwd") in cwds and row.get("state") == "open":
        row["state"] = "closed"
        row["closed_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        hit = True
if hit:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
PY
}

collect_from_copy() {   # $1 = 副本根  $2… = 要撿出來的檔名
    # 驗證者 / 批次 worker 有時把交付物放在 `work/` 裡(派工文說放在副本根)。**先撿出來再刪副本** ——
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
# 准幾輪只問 `ticket.py` 的 `repair_limit`(#125 A1):票的 retry_limit 優先,沒寫退回
# `repair.max_rounds`。票讀不到也照同一支算(當成沒寫)—— 不在這裡另寫一份預設。
import event
import ticket as ticket_mod
try:
    with open(tf, encoding="utf-8") as fh:
        row = json.load(fh)
except (OSError, ValueError):
    row = {}
limit = ticket_mod.repair_limit(row if isinstance(row, dict) else {}, event.config(root))


def out(name, value):
    print("%s=%s" % (name, shlex.quote(str(value))))


out("S_RUN", run_id or "")
out("S_STATE", data.get("state") or "")
out("S_KIND", data.get("kind") or "")
out("S_RC", "" if data.get("rc") is None else data.get("rc"))
out("S_FAILS", len(data.get("failures") or []))
# 紅榜只有缺口列(#126 A5;宣告了 verify 卻沒有案例):這一輪只派驗證者,不派 worker。
fails = data.get("failures") or []
out("S_GAP_ONLY", "1" if fails and all(row.get("kind") == status.GAP_KIND for row in fails)
    else "")
# 閘門硬擋命中(#90):gate 那一筆 rc 4、note 帶 `硬擋命中 —— ` 原文。**不靠 rc==4 單獨判**
# —— 測試紅也可能是 4;認的是 gate 自己寫下的那幾行。
mech = [line for line in str(data.get("note") or "").splitlines() if "硬擋命中 —— " in line] \
    if data.get("kind") == "gate" and data.get("rc") == 4 else []
out("S_MECH", "\n".join(mech))
out("S_ROUND", (ctx.get("round") or 1))
out("S_BASE", ctx.get("base_sha") or "")
out("S_PREV_EVIDENCE", ctx.get("prev_evidence") or "")
out("S_LIMIT", limit)
# 上一輪的環境嫌疑(形狀見 `docs/DESIGN-ENV-SUSPECT.md`,D-019)。經正規化函式讀,
# 不直接下標 —— 舊檔那一種非空 dict 在 `[0]` 上會炸。
suspects = status.environment_suspects(data)
first = suspects[0] if suspects else {}
out("S_ENV", len(suspects))
out("S_ENV_ENGINE", first.get("engine") or "?")
out("S_ENV_WHY", first.get("why") or "?")
# 環境可疑的紅(#125 A4):閘門寫了 env_suspect / rc 86,或有嫌疑列而紅榜是空的。有嫌疑列
# **而紅榜可歸因**(案例自己宣告的那一種)照一般修補輪走 —— 紅榜就是答案。
out("S_ENV_SUSPECT", "1" if data.get("rc") not in (None, 0) and (
    data.get("state") == "env_suspect" or data.get("rc") == 86
    or (suspects and not fails)) else "")
# 這一輪(repair_context.round)的派工是不是診斷輪、它的閘門已經機械重跑過幾次 —— 問事件流,
# 不問這個程序記得什麼:換一個程序接手(新 pid)要得到同一個答案(A2)。
this_round = str(ctx.get("round") or 1)
diag_prev, reruns = "", 0
for ev_row in event.read_events(root):
    if str(ev_row.get("ticket") or "") != ident or str(ev_row.get("attempt") or "") != this_round:
        continue
    if ev_row.get("kind") == "ticket.attempt.start":
        # `round_kind`,不是票面寫的 `kind`:事件列的 `kind` 是事件種類,`--kv kind=` 會讓
        # event.py 的 emit 撞名炸掉(實測 TypeError)。同一輪起過兩次(換候選)以最後一次為準。
        diag_prev = "1" if ev_row.get("round_kind") == "diagnosis" else ""
    elif ev_row.get("kind") == "gate.rerun" and ev_row.get("reason") == "env_suspect":
        reruns += 1
out("S_DIAG_PREV", diag_prev)
out("S_RERUNS", reruns)
PY
)"
}

# 閘門硬擋命中(#90 A2–A4):測試沒起,紅榜本來就是空的 —— 那不是「沒有歸因」,歸因就寫在
# gate 狀態檔的 note 裡。逐條點名(全是交件命中 `hard_block`),給主線接回的確切步驟;
# 不派 worker、不轉 Ready(轉 Ready 會從頭派第 1 輪),commit 留在分支上。
mechanical_stop() {   # uses S_MECH / S_RUN;票轉 Blocked、一頁、exit 4
    echo "auto-fix: 閘門硬擋命中(rc 4,測試沒起)—— 不派 worker:" >&2
    printf '%s\n' "$S_MECH" | sed 's/^/auto-fix:   /' >&2
    block "#$ID 閘門硬擋命中:$S_MECH"
    mech_page=$(python3 - "$ROOT" "$ID" "$S_RUN" "$S_MECH" "$TF" <<'PY'
import json, os, sys
root, ident, run_id, lines, tf = sys.argv[1:6]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

try:
    with open(tf, encoding="utf-8") as handle:
        branch = json.load(handle).get("branch") or "t%s" % ident
except (OSError, ValueError):
    branch = "t%s" % ident
wt = ((status.read(root, ident, run_id) or {}).get("repair_context") or {}).get("worktree") \
    or "<票 %s 的 worktree>" % ident
rows = [line for line in lines.splitlines() if line.strip()]
out = ["閘門硬擋命中(rc 4,測試沒起,所以紅榜是空的 —— 歸因在下面)。", ""]
out.append("**歸因到交件(worker)**:")
out += ["- %s" % line for line in rows]
out += ["", "交件本身不合:拿掉命中 hard_block 的改動 —— 在分支 %s 上修。" % branch, ""]
out.append("接回(commit 留在分支 %s 上;**不要轉 Ready** —— 那會從頭派第 1 輪):" % branch)
out.append("1. 修分支 %s 上的交件並 commit(或主線決定重派)" % branch)
out.append("2. `python3 scripts/ticket.py set %s state Running`" % ident)
out.append("3. 在分支副本重跑閘門:`cd %s && sh scripts/gate.sh --branch --ticket %s`"
           "(綠 → InReview → review.sh)" % (wt, ident))
print("\n".join(out))
PY
)
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "${RUN_ID:-}" \
        --kind decision --state "閘門硬擋命中" --what "$mech_page" \
        --where "reports/t$ID/$S_RUN/status.json" \
        || echo "auto-fix: inbox 寫不出來(閘門硬擋命中)" >&2
    exit 4
}

# 規則包(#74):**先產、成功才組派工文**。產不出來(缺章節、空正文、放不下)時 rules.py
# 已經非零、stdout 一個字都沒印、送了 decision 頁;這裡只負責不讓任何 agent 起來 ——
# 以前 `|| echo "(規則包產不出來 —— 自己讀 …)"` 把那一次變成一份照派的派工文。
rules_pack() {   # $1 = 派工文寫到哪(失敗就刪掉,不留半份)  $2 = 角色  $3 = 模型;成功設 RULES_TEXT
    RULES_TEXT=$(AC_TICKET=$ID python3 "$AC/rules.py" pack "$2" --model "$3") && return 0
    rm -f "$1"
    RULES_FAILED=1
    echo "auto-fix: #$ID 的 $2 規則包產不出來(模型 $3)—— 不起任何 agent;缺項見上面 rules: 那幾行與 decision 頁" >&2
    return 1
}

# 派工文的「只准跑的測試」段(#87 A18;D-073 ①):worker 交件前只跑兩項 —— 這張票的驗證者案例
# (`verify.files` 還空的時候是自己改到 / 新增的 tests/ 模組)與基礎組(閘門同一支腳本 `--base`);
# 驗證者只跑自己的案例與 `verify-case.py red`。全套、回歸、驗證者 check 是閘門與落地的事 ——
# 不寫在派工文裡,agent 就會自己跑全套把時限吃掉(#155、#159)。票的 `test_plan` 是設計
# (要做什麼、驗證者會斷言什麼),逐字放在自己的「## 票的測試計畫」段,不是要跑的清單。
only_tests_section() {   # $1 = worker / verifier
    python3 - "$TF" "$1" "$(ac_gate_script "${WT:-$WTBASE/$(ticket_branch)}")" <<'PY'
import json, sys
tf, role, gate = sys.argv[1:4]
try:
    with open(tf, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
plan = ticket.get("test_plan")
verify = ticket.get("verify") if isinstance(ticket.get("verify"), dict) else {}
files = [f for f in verify.get("files") or [] if isinstance(f, str) and f.strip()]
run = verify.get("run") if isinstance(verify.get("run"), str) else ""
modules = " ".join(f.strip()[:-3].replace("/", ".") if f.strip().endswith(".py")
                   else f.strip().replace("/", ".") for f in files)
print("")
print("## 票的測試計畫")
if isinstance(plan, str) and plan.strip():
    print(plan)
    # 案例歸屬照 test_plan 的指派(#178;正文在 DISPATCH-TEMPLATE §1):兩邊各寫各的,重複會被覆核退回。
    # 沒指派的契約只有一個寫案例者(#180):有驗證者(needs_verifier 是 true 或缺)歸驗證者,false 歸 worker
    if role == "verifier":
        print("寫案例只寫 test_plan 指給驗證者的契約與沒指派的契約(照 acceptance);指給 worker 的不寫(worker 會寫,重複會被覆核退回)。")
    elif ticket.get("needs_verifier") is False:
        print("寫案例:這張票沒有驗證者,test_plan 的每條契約(含沒指派的)都由你寫。")
    else:
        print("寫案例只寫 test_plan 指給 worker 的契約;指給驗證者的與沒指派的都不寫(驗證者照 acceptance 寫,重複會被覆核退回)。")
else:
    print("票沒有 test_plan(驗收照票面 acceptance)")
print("")
print("## 只准跑的測試")
if role == "verifier":
    print("只跑你寫的案例檔:`python3 -m unittest %s`" % (modules or "<你加進案例的那支模組>"))
    print("`python3 scripts/verify-case.py red %s`(旗標照上面範本);check、閘門那一組、全套、回歸都不是你的"
          % ticket.get("id", ""))
else:
    if files:
        print(run.strip() if run.strip() else "python3 -m unittest %s" % modules)
    else:
        print("你改到 / 新增的 tests/ 模組:`python3 -m unittest <那幾支模組>`(驗證者案例還沒交)")
    if not gate:   # 解不出閘門腳本:不印一行空路徑冒充指令(§5.5)
        sys.stderr.write("auto-fix: 解不出閘門腳本 —— 基礎組那一行退回 scripts/gate.sh\n")
    print("sh '%s' --base" % (gate or "scripts/gate.sh"))
    print("全套、回歸、驗證者 check 是閘門與落地的事;交件前只跑上面兩項(第一行是這張票的案例,"
          "第二行是基礎組,cwd 是你的 worktree)")
PY
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
    rules_pack "$1" worker "$2" || return 1
    {
        printf '%s\n' "$RULES_TEXT"
        python3 - "$ROOT" "$ID" "$TF" "$fr_fix" "$2" "$WTBASE" <<'PY'
import json, os, sys
root, ident, tf, fix, model, wtbase = sys.argv[1:7]
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
print("## 權威票面快照")
branch = ticket.get("branch") or "t%s" % ident
wt = os.path.join(wtbase, branch)
print("以下 JSON 與 `$AC_ROOT/%s` 是這一輪現況；worktree 裡的 `%s` 是分支上的版本，不能用它推翻本快照。"
      % (os.path.join("tickets", "%s.json" % ident),
         os.path.join("tickets", "%s.json" % ident)))
print("```json")
print(json.dumps(ticket, ensure_ascii=False, indent=2))
print("```")
print("")
print("## 這張票獨有的四件事")
print("1. **票號**:#%s" % ident)
print("2. **base sha**:`%s`(派工方已經 `git log --oneline -1` 對過)" % base)
print("3. **你的 worktree**:`%s`(票分支 `%s`,派工方已建好,cwd 就是它)" % (wt, branch))
print("   在這裡改、`git add` / `git commit`(可多個 commit,訊息帶 `t%s`)。" % ident)
print("   交件 = 分支相對派工前多了至少一個 commit **而且工作樹乾淨**;沒 commit 的改動不算交件,"
      "腳本不替你 commit。")
print("   git 規矩見 `docs/DISPATCH-TEMPLATE.md` §1(規則包已節錄)。")
print("4. **回報對象**:派工的那一條主線 session。")
print("")
print("## 你要交的兩樣")
print("1. 票分支 `%s` 上的 commit(閘門在同一個 worktree 跑)。" % branch)
print("2. `%s/EVIDENCE-round1.md`(worktree 外的輪次目錄,不進 repo)—— 段名照 `docs/DISPATCH-TEMPLATE.md` §8"
      "+ 記憶段 + 檔尾一塊 ```result` JSON。" % fix)
print("   主線收件那一手是:")
print("   ```sh")
print("   sh scripts/apply.sh %s --evidence %s/EVIDENCE-round1.md" % (ident, fix))
print("   ```")
print("   它會抽出 `result-round1.json`,並把 EVIDENCE 裡的 `OBJECTION:` 行記進票的 `objections[]`。")
print("")
print("## 回報格式")
print("照 `docs/DISPATCH-TEMPLATE.md` §8(規則包已節錄);**判綠只看 rc**。")
print("**測試一律前景跑加 timeout** —— 把整套丟背景後結束回合 = 什麼都沒交。")
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。")
print("**不准**放寬既有斷言、不准把期望值改成程式現在印的東西(那是假綠家族)。")
PY
        only_tests_section worker
    } > "$1"
}

# 驗證者的派工文**只有一種組法**(#51,C3):`rules.py pack verifier` + `templates/dispatch-verifier.md`
# 逐格填好。第 1 輪(needs_verifier)與 test_defect 那條路共用;後者只在尾巴多一段紅榜。
# 以前 test_defect 那條路是一段內嵌文字 —— 人讀的範本與腳本餵的派工文是兩份,少了哪一格沒有人看得出來。
verifier_packet() {   # $1 = 派工文寫到哪  $2 = 第幾輪  $3 = 驗證者副本根  $4 = base sha  $5 = 票分支(非空 = 等 worker 交件才起,#60)
                      # $6 = 副本展開的分支頭 sha(非空 = 副本是 $5 的頭,#157)  $7 = 非空 = 與 worker 平行(interface_fixed)
    vp_template=""
    for candidate in "$ROOT/templates/dispatch-verifier.md" "$AC/../templates/dispatch-verifier.md"; do
        [ -f "$candidate" ] && { vp_template=$candidate; break; }
    done
    mkdir -p "$(dirname "$1")"
    rules_pack "$1" verifier "$VERIFIER_MODEL" || return 1
    {
        printf '%s\n' "$RULES_TEXT"
        if [ -z "$vp_template" ]; then
            echo "auto-fix: 找不到 templates/dispatch-verifier.md —— 驗證者派工文少了範本那一段" >&2
            echo "(範本 templates/dispatch-verifier.md 找不到 —— 照上面規則包標題列的角色卡)"
        else
            python3 - "$vp_template" "$ID" "$TF" "$4" "$3" "$2" "$VERIFIER_MODEL" <<'PY'
import sys
template, ident, ticket_file, base, copy, rnd, model = sys.argv[1:8]
with open(template, encoding="utf-8") as handle:
    text = handle.read()
fill = {
    "@TICKET@": ident,
    "@TICKET_FILE@": ticket_file,
    "@BASE@": base or "(票面沒有 base_sha —— 先補上再派)",
    "@COPY@": copy,
    "@ROUND@": rnd,
    "@MODEL@": model,
    "@REPORT_TO@": "auto-fix.sh(交檔即回報:`%s/patch-verify.diff` 與 `%s/EVIDENCE-verifier.md`)"
                   % (copy, copy),
}
for key, value in fill.items():
    text = text.replace(key, value)
sys.stdout.write("\n" + text)
PY
        fi
        if [ -n "${5:-}" ]; then
            echo ""
            echo "## 介面以分支上的實作為準(#60:這張票 interface_fixed 不是 true)"
            echo "你是在 worker 第 $2 輪交件之後才起的:介面以分支上的實作為準(worker 的 commit 在分支 \`$5\`:\`git -C $ROOT diff $4..$5\` 讀它、不要套進你的 work/)。"
            echo "票面寫法與實作衝突時照實作、在 EVIDENCE 記一行(票面怎麼寫、實作怎麼做、你照哪個寫了案例)。"
        fi
        if [ -n "${5:-}" ] && [ -n "${6:-}" ]; then
            echo ""
            echo "## 副本來源:票分支 \`$5\` 的頭(#157)"
            echo "你的副本 \`$3/base\` 與 \`$3/work\` 是票分支 \`$5\` 的頭 \`$6\`(worker 第 $2 輪交件後 \`git archive\`),不是票的 base_sha:worker 的 commit 已經在兩份裡,上面「兩份都是 base 的內容」那一句這一輪不適用。\`diff -Nru base work\` 出的 patch-verify 只帶你的案例,收件時套在這個分支頭上。"
            echo "\`verify-case.py red --ref $3/ref\` 照舊是票的 base_sha \`$4\` 那一棵樹:證的是乾淨基底紅,不是分支頭紅。"
        fi
        if [ -n "${7:-}" ]; then
            echo ""
            echo "## 與 worker 平行:案例加在既有模組尾端的新 class(#157,D-056 1)"
            echo "你與 worker 同時從票的 base_sha 起跑,兩邊常改同一支測試模組;收件時 \`apply.sh\` 以三方合併把你的 patch-verify 併上分支頭,兩邊在同一處插入只對 *.py、而且合起來沒有重複的模組層名字才自動合。"
            echo "所以:新案例放在**既有模組尾端新加的 class**(不新開以票號命名的案例檔,D-056 1);helper 寫在你自己的 class 裡;no new module-level names(不新增模組層的函式、常數、import)。"
        fi
        only_tests_section verifier
    } > "$1"
}

# 這一輪要不要起驗證者(#51,D-032 ③;#126 起任一輪,不只第 1 輪):票 `needs_verifier` 不是
# JSON `false`、而且 `verify.baseline` 還是空的(D-018:案例還沒交)。**缺這一格當 true**
# (D-054 Q10):只有明寫 false 才不起,沒寫與寫 false 分得出來。
# 設定 V_WANT(1 / 空)、V_BASE(票的 base_sha,驗證者副本從它展開)與 V_PARALLEL:
# 票 `interface_fixed` 是 JSON `true` 才與 worker 平行起(#60,D-H38 ②);false / 缺 ⇒ 等 worker
# 第 1 輪交件(票分支有了新 commit)才起 —— 介面沒釘死的票,驗證者照票面猜的介面寫案例,worker 做出來的是另一個。
verifier_wanted() {   # $1 = 第幾輪(預設 1)
    vw_round=${1:-1}
    eval "$(python3 - "$TF" <<'PY'
import json, shlex, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
plan = ticket.get("verify") if isinstance(ticket.get("verify"), dict) else {}
flag = "no" if ticket.get("needs_verifier") is False else "true"
print("V_FLAG=%s" % flag)
print("V_HAS_BASELINE=%s" % ("1" if plan.get("baseline") else ""))
print("V_BASE=%s" % shlex.quote(str(ticket.get("base_sha") or "")))
print("V_PARALLEL=%s" % ("1" if ticket.get("interface_fixed") is True else ""))
PY
)"
    V_WANT=""
    case "$V_FLAG" in
        true)
            if [ -n "$V_HAS_BASELINE" ]; then
                echo "auto-fix: #$ID needs_verifier 不是 false,但 verify.baseline 已經有了 —— 第 $vw_round 輪不再起驗證者"
            else
                V_WANT=1
            fi ;;
    esac
}

route() {   # $1 = 角色,其餘照傳 resolve;設定 R_RC 與 R_MODEL / R_TOOL / R_EFFORT / R_INDEX / R_REASON
    R_ROLE=$1
    shift
    R_OUT=$(python3 "$AC/role-models.py" --table "$ROOT/board/role-models.json" resolve \
            --role "$R_ROLE" ${T_COMPLEXITY:+--complexity "$T_COMPLEXITY"} "$@")
    R_RC=$?
    R_MODEL="" R_TOOL="" R_EFFORT="" R_INDEX="" R_REASON=""
    [ "$R_RC" -ne 2 ] || return 2
    eval "$(python3 - "$R_OUT" <<'PY'
import json, shlex, sys
got = json.loads(sys.argv[1])
for key, name in (("model", "R_MODEL"), ("tool", "R_TOOL"), ("effort", "R_EFFORT"),
                  ("candidate_index", "R_INDEX"), ("reason", "R_REASON")):
    value = got.get(key)
    print("%s=%s" % (name, shlex.quote("" if value is None else str(value))))
PY
)"
    return "$R_RC"
}


route_worker() {   # $@ = 額外旗標(--exhausted-index …);照 W_ROUTE 選 worker
    _extra=$(for arg in "$@"; do printf '%s\n' "$arg"; done)
    _old_ifs=$IFS
    IFS='
'
    # shellcheck disable=SC2086
    set -- ${W_ROUTE:-} $_extra
    IFS=$_old_ifs
    if [ -z "$BATCH" ] && [ -n "$T_MODEL" ]; then
        set -- --ticket-model "$T_MODEL" ${T_TOOL:+--ticket-tool "$T_TOOL"} "$@"
    fi
    route worker "$@"
}

use_worker() {   # route 的結果 → WORKER_CMD / WORKER_MODEL / MODEL / T_EFFORT / provider flags / W_INDEX
    WORKER_MODEL=$R_MODEL
    MODEL=$R_MODEL
    W_TOOL=$R_TOOL
    W_INDEX=$R_INDEX
    T_EFFORT=$R_EFFORT
    case "$R_REASON" in *票明寫\ effort*) EFFORT_SRC=票 ;; *) EFFORT_SRC=表 ;; esac
    # 票與表都沒給才退回 config 的 effort.implement(#114 的第二層);表有值就是表的。
    if [ -z "$T_EFFORT" ]; then
        T_EFFORT=$(cfg effort.implement "")
        EFFORT_SRC=config
    fi
    IS_CODEX=""
    IS_AGY=""
    [ "$W_TOOL" != codex ] || IS_CODEX=1
    [ "$W_TOOL" != agy ] || IS_AGY=1
    WORKER_CMD=$(python3 "$AC/ticket.py" agent-command --model "$R_MODEL" --tool "$R_TOOL" \
            --base "$BASE_WORKER_CMD" --swap-model --effort "$T_EFFORT") || {
        echo "auto-fix: #$ID 角色表選的 worker $R_MODEL(tool $R_TOOL)組不出命令 —— 不派" >&2
        return 2
    }
    echo "auto-fix: #$ID 照角色表起 worker:$R_MODEL(tool $R_TOOL${T_EFFORT:+,effort $T_EFFORT};鏈第 $R_INDEX 格:$R_REASON)"
    [ -z "$T_MODEL" ] || echo "auto-fix: #$ID 票的 model=$T_MODEL 是第一候選;本次起 $R_MODEL"
}

chain_exhausted() {   # $1 = 角色;整串候選都用不了:票 Blocked、一頁 decision,不再起程序(A3)
    echo "auto-fix: #$ID 的 $1 候選整串耗盡($R_REASON)—— 不再起程序,票轉 Blocked" >&2
    block "#$ID 的 $1 候選整串耗盡:$R_REASON"
    post "$1 候選整串耗盡" \
         "#$ID 的 $1 候選都撞到額度 / 不可用($R_REASON);改表或等額度恢復後重跑 auto-fix:sh scripts/auto-fix.sh $ID" \
         "${1:+board/role-models.json}"
}

# 啟動前就知道 CLI 不在(#87 A22 → A2):不起它,標記這一格、取下一個候選;整串沒了就停。
pick_worker() {
    route_worker "$@"
    case $R_RC in
        2) exit 2 ;;
        3) chain_exhausted worker; exit 7 ;;
    esac
    while [ "$R_TOOL" = codex ] || [ "$R_TOOL" = agy ]; do
        [ -z "$DRY" ] || break
        command -v "$R_TOOL" >/dev/null 2>&1 && break
        echo "auto-fix: #$ID 候選第 $R_INDEX 格是 $R_MODEL,但 $R_TOOL 不在 PATH —— 這一格標記不可用,換下一個候選" >&2
        route_worker --exhausted-index "$R_INDEX"
        case $R_RC in
            2) exit 2 ;;
            3) chain_exhausted worker; exit 7 ;;
        esac
    done
    # 票明寫的 effort 不合第一個候選的 tool 就在動票之前停(#114 A8);換 provider 才由 resolve 換掉。
    if [ -n "$TK_EFFORT" ] && ! python3 "$AC/ticket.py" agent-command --model "$R_MODEL" \
            --tool "$R_TOOL" --base claude --effort "$TK_EFFORT" >/dev/null; then
        echo "auto-fix: #$ID effort=$TK_EFFORT(來源 票)不合 tool=$R_TOOL —— 不派、票不動" >&2
        exit 2
    fi
    pw_cfg=""
    [ -n "$TK_EFFORT" ] || [ -n "$R_EFFORT" ] || pw_cfg=$(cfg effort.implement "")
    if [ -n "$pw_cfg" ] && ! python3 "$AC/ticket.py" agent-command --model "$R_MODEL" \
            --tool "$R_TOOL" --base claude --effort "$pw_cfg" >/dev/null; then
        echo "auto-fix: #$ID effort=$pw_cfg(來源 config)不合 tool=$R_TOOL —— 不派、票不動" >&2
        exit 2
    fi
    use_worker || exit 2
}

# 驗證者也查同一張表(A1):verifier 鏈不看 worker 模型(#116 A3)。命令是 `verifier.command`
# (沒設跟 worker.command 同一支)換上表選的 model / effort;codex 候選走 codex exec。
pick_verifier() {
    route verifier
    case $R_RC in
        2) exit 2 ;;
        3) chain_exhausted verifier; exit 7 ;;
    esac
    while [ "$R_TOOL" = codex ] || [ "$R_TOOL" = agy ]; do
        [ -z "$DRY" ] || break
        command -v "$R_TOOL" >/dev/null 2>&1 && break
        echo "auto-fix: #$ID 驗證者第 $R_INDEX 格是 $R_MODEL,但 $R_TOOL 不在 PATH —— 換下一個候選" >&2
        route verifier --exhausted-index "$R_INDEX"
        case $R_RC in
            2) exit 2 ;;
            3) chain_exhausted verifier; exit 7 ;;
        esac
    done
    use_verifier || exit 2
}

use_verifier() {   # route 的結果 → VERIFIER_MODEL / V_EFFORT / V_INDEX / VERIFIER_CMD
    VERIFIER_MODEL=$R_MODEL
    V_EFFORT=$R_EFFORT
    V_INDEX=$R_INDEX
    VERIFIER_CMD=$(python3 "$AC/ticket.py" agent-command --model "$R_MODEL" --tool "$R_TOOL" \
            --base "$(with_envelope "$(cfg verifier.command "$BASE_WORKER_CMD")")" --swap-model \
            --effort "$V_EFFORT")
}

first_round_dispatch() {
    # `--round 1`:只產、只印,不起 worker、不動票。要起第 1 輪就不帶 `--round`。
    fr_run=${AC_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
    fr_dispatch=$ROOT/$(cfg reports_dir reports)/t$ID/$fr_run/dispatch-round1.md
    route_worker
    case $R_RC in 2) return 2 ;; 3) echo "auto-fix: #$ID worker 候選整串耗盡:$R_REASON" >&2; return 7 ;; esac
    first_round_packet "$fr_dispatch" "$R_MODEL" || return 6
    cat "$fr_dispatch"
    echo "auto-fix: 第 1 輪派工文 -> $(python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$fr_dispatch" "$ROOT")" >&2
    # 驗證者那一份也在這裡產、只印路徑(#51 C3):人要看得到第 1 輪會派出去的**兩份**。
    verifier_wanted >&2
    if [ -n "$V_WANT" ]; then
        route verifier
        case $R_RC in 2) return 2 ;; 3) echo "auto-fix: #$ID 驗證者候選整串耗盡:$R_REASON" >&2; return 7 ;; esac
        VERIFIER_MODEL=$R_MODEL
        fr_vdispatch=$(dirname "$fr_dispatch")/dispatch-verifier-round1.md
        fr_after=""
        [ -n "$V_PARALLEL" ] || fr_after=$(ticket_branch)
        verifier_packet "$fr_vdispatch" 1 "$WTBASE/verify-t$ID/round1" "$V_BASE" "$fr_after" \
            "" "$V_PARALLEL" || return 6
        echo "auto-fix: 第 1 輪驗證者派工文 -> $(python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$fr_vdispatch" "$ROOT")" >&2
    fi
    echo "auto-fix: --round 1 只印派工文;票是 Ready 時不帶 --round 就由這一支起第 1 輪:sh scripts/auto-fix.sh $ID" >&2
    return 0
}

# 起什麼 agent 由 `role-models.py resolve` 給 model / tool / effort / 鏈上第幾格;
# worker 的明確票 model 是第一候選(#131),其他角色與沒寫 model 的票照 #121 角色鏈;
# 票明寫的 effort 由 resolve 自己排優先(#116 A5)。`routing.*` 不再參與(#116 A6)。
# 這次派工的 worker 選路旗標:修補輪(第 2 輪起,上一輪紅)帶 --repair-round / --last-red 與
# 原 worker 的模型(票 cost[] 最後一列沒撞額度的 worker),由 resolve 決定沿用與升級(A7)。
# 同一程序裡迴圈接下一輪也從這裡重選 —— 與重新起程序讀狀態檔走同一條路。
route_round() {   # $1 = 要派的是第幾輪;讀 S_RC / T_LAST_WORKER,設 W_ROUTE 並選 worker
    W_ROUND=$1
    set --
    [ -z "$TK_EFFORT" ] || set -- "$@" --ticket-effort "$TK_EFFORT"
    if [ "$W_ROUND" -ge 2 ]; then
        set -- "$@" --repair-round "$W_ROUND"
        [ "${S_RC:-1}" = "0" ] || set -- "$@" --last-red
        [ -z "$T_LAST_WORKER" ] || set -- "$@" --worker-model "$T_LAST_WORKER"
    fi
    W_ROUTE=$(for arg in "$@"; do printf '%s\n' "$arg"; done)
    pick_worker
}

# 批次修復(#108)不讀單票的狀態檔:紅榜在 land 那一輪,底下到「一輪的動作」整段跳過。
if [ -z "$BATCH" ]; then
read_status
if [ "${WANT_ROUND:-}" = "1" ]; then
    [ -z "${S_RUN:-}" ] || echo "auto-fix: #$ID 已經有 $S_RUN 這一輪,你要的是第 1 輪的派工文 —— 照給" >&2
    first_round_dispatch
    exit $?
fi
if [ -z "${S_RUN:-}" ]; then
    # **Ready 且沒有狀態檔 ⇒ 第 1 輪**(#40,D-025 C1)。只認 Ready:Draft 還沒開完、
    # Running 多半是有人已經手派了、Blocked 在等裁示 —— 哪一種都不該由這一支猜著起。
    eval "$(python3 - "$TF" "$ID" <<'PY'
import json, shlex, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
print("T_STATE=%s" % shlex.quote(str(ticket.get("state") or "")))
print("T_BASE=%s" % shlex.quote(str(ticket.get("base_sha") or "")))
print("T_BRANCH=%s" % shlex.quote(str(ticket.get("branch") or "t%s" % sys.argv[2])))
PY
)"
    if [ "$T_STATE" != "Ready" ]; then
        echo "auto-fix: #$ID 沒有狀態檔,而票的 state=${T_STATE:-(空)},不是 Ready —— 停,不猜要不要起第 1 輪" >&2
        echo "auto-fix:   Ready 的票才由這一支起第 1 輪;只看派工文:sh scripts/auto-fix.sh $ID --dry-run --round 1" >&2
        echo "auto-fix:   已經派過第 1 輪、要修紅的:先跑 sh scripts/gate.sh --branch --ticket $ID" >&2
        exit 2
    fi
    # 先選第 1 輪的 worker:票 / config 的 effort 不合候選的 tool 要在動票之前停(#114 A8),
    # 而底下換 base_sha 就是動票。
    route_round 1
    # **base 落後主線就換成主線的頭**(#57)。第 1 輪的票分支**從主線開**(`ticket_worktree`),
    # base_sha 留著舊的話,票上的對照組與 worker 手上的樹不是同一版。只在「還沒有分支、還沒有任何一輪」時換 —— 有分支就有人在上面
    # 做過事,換 base 等於改寫它的對照組。`--dry-run` 不動票,只說一聲。
    MAIN_HEAD=$(git -C "$ROOT" rev-parse -q --verify "$MAIN^{commit}" 2>/dev/null || echo "")
    if [ -n "$MAIN_HEAD" ] && [ -n "$T_BASE" ] \
            && ! git -C "$ROOT" rev-parse -q --verify "$T_BRANCH^{commit}" >/dev/null \
            && [ "$(git -C "$ROOT" rev-parse -q --verify "$T_BASE^{commit}" 2>/dev/null)" != "$MAIN_HEAD" ] \
            && git -C "$ROOT" merge-base --is-ancestor "$T_BASE" "$MAIN_HEAD" 2>/dev/null; then
        if [ -n "$DRY" ]; then
            echo "auto-fix: #$ID 的 base_sha $T_BASE 落後 $MAIN($MAIN_HEAD)—— 真的起跑時會換成 $MAIN 的頭(--dry-run 不動票)"
        elif python3 "$AC/ticket.py" set "$ID" base_sha "\"$MAIN_HEAD\"" >/dev/null; then
            echo "auto-fix: #$ID 的 base_sha $T_BASE 落後 $MAIN —— 還沒有分支,換成 $MAIN 的頭 $MAIN_HEAD"
            T_BASE=$MAIN_HEAD
        else
            echo "auto-fix: #$ID 的 base_sha 換不動 —— 照舊用 $T_BASE" >&2
        fi
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

# 上一輪有環境嫌疑就說一聲(D-019 驗收 12)。#125(D-076)起 `gate.sh` 不再在這裡擋 ——
# 閘門與手打走同一條路(先機械重跑一次、仍可疑派一輪診斷,見 `red_next`);這一句留著,
# 讓人看得見這一輪是踩在一份環境嫌疑上派的。
if [ "${S_ENV:-0}" != "0" ]; then
    echo "auto-fix: 上一輪環境可疑($S_ENV_ENGINE:$S_ENV_WHY),你確定要派?"
fi

if [ "${S_RC:-1}" = "0" ]; then
    echo "auto-fix: 上一輪是綠的 —— 沒有東西要修。complex 票的覆核由 review.sh 派:sh scripts/review.sh $ID"
    exit 0
fi

# 閘門硬擋命中(#90 A4)最先:紅榜空是因為測試沒起,歸因在 gate 的 note 裡 —— 機械停,
# 不重跑、不診斷(#125 A6)。第 1 輪(#40)沒有上一輪:S_MECH 空、S_RUN 空,底下都不成立。
[ -z "${S_MECH:-}" ] || mechanical_stop

# 輪數用完先停(#125 A2):診斷輪也算一輪,環境重跑不算 —— 所以用完了就不重跑、不診斷。
if [ -n "${S_RUN:-}" ] && [ "$S_ROUND" -ge "$S_LIMIT" ]; then
    rounds_exhausted "$S_ROUND"
fi

# 沒有歸因的紅 / 環境可疑(#125 A3/A4)由 `red_next` 決定下一輪派什麼 —— 它要用到底下
# 才定義的重跑閘門與綠了那一手,所以在迴圈前叫(PRE_RED)。
[ -z "${S_RUN:-}" ] || PRE_RED=1

fi

# --------------------------------------------------------------- 一輪的動作
# 第 1 輪在上面已經選過(要趕在換 base_sha 之前);其餘照狀態檔的輪數選。
[ -n "${W_ROUND:-}" ] || route_round $(( ${S_ROUND:-0} + 1 ))
pick_verifier
# 只有 complex 的票做完後覆核(#136;沒標 = simple)。判準是 `ticket.py` 的 `needs_review`,
# review.sh / land / close 問的也是它。
needs_review() {
    python3 - "$AC" "$TF" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import ticket as ticket_mod
try:
    with open(sys.argv[2], encoding="utf-8") as handle:
        row = json.load(handle)
except (OSError, ValueError):
    row = {}
sys.exit(0 if ticket_mod.needs_review(row) else 1)
PY
}

WT=$WTBASE/$(ticket_branch)

# 起一個 agent(worker / 驗證者)**只有這一條路**(#87 A9–A14)。
#
# ## 隔離:agent 寫不進真看板
# 以前 agent 繼承 auto-fix 的環境 —— `AC_ROOT` / `AC_CONTROL_DIR` 指著主 repo。它在副本裡跑
# `memory.py check`、`ticket.py create`、`rules.py pack`,事件、票、收件匣就寫進主 repo 的
# 真看板,而且不報錯。現在每一次起 agent 都在 `<副本根>/control` 開一顆**拋棄式控制根**
# (config + 這張票 + 派工文的複本),`AC_ROOT` 指它;`AC_CONTROL_DIR`、`AC_TICKETS_DIR` 與
# 任何值落在主 repo 底下的 `AC_*` 一律不帶。控制根在 `work/` 與 worker 的 worktree 外面 ——
# 副作用不會變成交件的一部分。agent 跑 `gate.sh --branch --ticket <n>` 讀得到票(那份
# 複本),狀態檔與收件匣落在控制根。auto-fix 自己起的閘門照舊帶主 repo 的 `AC_ROOT`。
#
# ## 逾時收整棵樹
# 自己一個行程群組不夠:agent 的後代可以 `setsid()` 自開 session,`killpg` 碰不到它。
# 所以逾時那一刻先拍下整棵子孫樹(`ps` 的 pid/ppid),群組與樹上每一個都送 TERM,
# 寬限後再全部 KILL —— 先死的父行程不代表後代也走了。
#
# ## 開題者不隔離(#89)
# 角色 `opener` 要改**真票**:cwd 與 `AC_ROOT` 都是主 repo 根、`AC_ROLE=opener`,不開控制根;
# 時限由第 8 個參數給(`opener.timeout_seconds`)。驗證者在副本的 `work/` 裡起;worker 在第 9 個
# 參數給的票分支 worktree 裡起(#144,D-054 Q9:它自己 commit)。session 的 cwd 就是起的那個目錄,
# 接回要在同一個目錄;`AC_WORK` 照舊指副本根(worker 那一份是輪次目錄,EVIDENCE 寫在那裡)。
run_agent() {   # $1 = 命令 $2 = 派工文 $3 = 副本根 $4 = 角色 $5 = 第幾輪 $6 = log $7 = 1:tee+pid 檔 $8 = 時限 $9 = worker 的 worktree;印出 rc
    python3 - "$1" "$2" "$3" "${8:-$WORKER_TIMEOUT}" "$ID" "$5" "$6" "$4" "${7:-}" \
        "$ROOT" "$MAINROOT" "$TF" "${9:-}" <<'PY'
import json, os, shlex, shutil, signal, subprocess, sys, threading, time
(cmd, dispatch, cwd, timeout, ident, r, log_path, role, tee_flag,
 root, mainroot, ticket_file, worktree) = sys.argv[1:14]
LABEL = {"worker": "worker ", "verifier": "驗證者", "opener": "開題者"}.get(role, role + " ")
# 指著控制面的三格一律不帶;其餘 `AC_*` 只要值是主 repo 根或它底下的絕對路徑也不帶。
CONTROL_KEYS = ("AC_ROOT", "AC_CONTROL_DIR", "AC_TICKETS_DIR")
RELATIVE_DEFAULTS = (("tickets_dir", "tickets"), ("events_file", "board/events.jsonl"),
                     ("answers_file", "board/answers.jsonl"), ("reports_dir", "reports"))


def control_root():
    where = os.path.join(cwd, "control")
    shutil.rmtree(where, ignore_errors=True)
    try:
        with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
            conf = json.load(handle)
    except (OSError, ValueError):
        conf = {}
    for key, default in RELATIVE_DEFAULTS:
        if os.path.isabs(str(conf.get(key) or "")):
            conf[key] = default
    os.makedirs(os.path.join(where, "board"))
    with open(os.path.join(where, "board", "config.json"), "w", encoding="utf-8") as handle:
        json.dump(conf, handle, ensure_ascii=False, indent=2)
    tickets = os.path.join(where, conf.get("tickets_dir") or "tickets")
    os.makedirs(tickets, exist_ok=True)
    shutil.copy(ticket_file, os.path.join(tickets, "%s.json" % ident))
    copy = os.path.join(where, os.path.basename(dispatch))
    shutil.copy(dispatch, copy)
    return where, copy


def agent_env(where, copy):
    mains = [os.path.realpath(path) for path in (root, mainroot) if path]

    def under_main(value):
        if not os.path.isabs(value):
            return False
        real = os.path.realpath(value)
        return any(real == main or real.startswith(main + os.sep) for main in mains)

    env = {key: value for key, value in os.environ.items()
           if not (key.startswith("AC_") and (key in CONTROL_KEYS or under_main(value)))}
    env.update({"AC_ROOT": where, "AC_DISPATCH": copy, "AC_TICKET": ident, "AC_ROUND": r,
                "AC_WORK": cwd, "AC_ROLE": role, "PYTHONUNBUFFERED": "1"})
    return env


def command_for_agent(command, copy):
    parts = shlex.split(command)
    head = os.path.basename(parts[0]) if parts else ""
    if parts[:2] == ["codex", "exec"] and "--add-dir" not in parts:
        at = len(parts) - 1 if parts[-1:] == ["-"] else len(parts)
        parts[at:at] = ["--add-dir", cwd]
    if head == "agy" and not any(part.startswith("-p=") for part in parts):
        parts.append("-p=Read %s and follow it." % copy)
    return shlex.join(parts), head == "agy"


def descendants(top):
    done = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True)
    kids = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [top]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def kill_tree(proc):
    tree = descendants(proc.pid)
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, None)):
        tree += [pid for pid in descendants(proc.pid) if pid not in tree]
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass
        for pid in tree:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        if grace:
            time.sleep(0.2)


def tee(stream, log):
    # **邊跑邊寫**(#57):以前 log 是 worker 的 stdout 本身,被砍的那一刻還在它緩衝裡的
    # 全部丟掉 —— 逾時的那一輪偏偏是最需要看它做到哪的那一輪。一行一 flush,並抄一份到
    # stderr 給叫這一支的人即時看(stdout 是 rc 那一行)。
    for chunk in iter(stream.readline, b""):
        log.write(chunk)
        log.flush()
        if tee_flag:
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()


# ## 越界守衛 = main-repo check (#115, D-048; one module since #153, D-064)
# The shared module `main_guard.py` snapshots before the agent starts and compares after it exits
# (HEAD / origin refs / main-repo porcelain + hash / hard_block.dirs; the rules are in its header).
# Exempt: the control plane, the agent's own copy and the worker's worktree. A reject writes
# `<log stem>.guard.json` and prints rc 8; recorded items only (main-side paths, HEAD moved during
# a main-line land: commits outside every in-window land's own main_before..sha are listed, not
# blocked) write it too, rc unchanged. The opener edits real tickets by design (#89).
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import main_guard  # noqa: E402

guard_path = os.path.splitext(log_path)[0] + ".guard.json"


def guard_verdict(before, conf, agent_rc):
    found = main_guard.compare(before, conf)
    for note in found["notes"]:
        sys.stderr.write("auto-fix: %s%s\n" % (LABEL, note))
    if not (found["rejects"] or found["main_side"] or found["recorded"]):
        return agent_rc
    with open(guard_path, "w", encoding="utf-8") as handle:
        json.dump({"ticket": ident, "round": r, "role": role, "agent_rc": agent_rc,
                   "monitored": {"repo": before["top"], "hard_block_dirs": conf["dirs"],
                                 "guard_main_paths": conf["mains"]},
                   "exempt": conf["exempt"], "violations": found["rejects"],
                   "main_side": found["main_side"], "notes": found["recorded"] + found["notes"],
                   "non_land_commits": found["non_land_commits"]},
                  handle, ensure_ascii=False, indent=2)
    if not found["rejects"]:
        sys.stderr.write("auto-fix: %s跑的期間主線那一邊改了 %d 處、記錄 %d 筆(只記不擋)—— 報告 %s\n"
                         % (LABEL, len(found["main_side"]), len(found["recorded"]), guard_path))
        return agent_rc
    sys.stderr.write("auto-fix: %s越界 %d 處(第一處 %s)—— 報告 %s\n"
                     % (LABEL, len(found["rejects"]), found["rejects"][0]["path"], guard_path))
    return 8


guarded = None
if role != "opener":
    if os.path.exists(guard_path):
        os.remove(guard_path)
    guard_conf = main_guard.settings(root, mainroot or root, [cwd, worktree])
    guarded = main_guard.snapshot(guard_conf)

# `worker.pid` 住在副本根(#46):下一輪收前幾輪副本之前先問它還活著沒有。回來了就刪掉,
# 留著的那一份只會是「auto-fix 被砍了、worker 還在跑」的那一種。
pid_path = os.path.join(cwd, "worker.pid") if tee_flag else ""
try:
    if role == "opener":
        start_in = root
        env = dict(os.environ)
        env.update({"AC_ROOT": root, "AC_TICKET": ident, "AC_ROUND": r, "AC_ROLE": role,
                    "PYTHONUNBUFFERED": "1"})
    else:
        where, copy = control_root()
        env = agent_env(where, copy)
        start_in = worktree or os.path.join(cwd, "work")
        if not os.path.isdir(start_in):
            start_in = cwd
        # 程式碼根(D-071 §1,#159)= agent 起的那棵樹:驗證者的封存副本不是 git 樹,cwd 規則在
        # 那裡答不出來;外層(閘門)留下的 `AC_CODE_ROOT` 指的是別棵樹,不准漏進來。
        env["AC_CODE_ROOT"] = start_in
        cmd, agy = command_for_agent(cmd, copy)
    input_path = os.devnull if role != "opener" and agy else dispatch
    with open(input_path, encoding="utf-8") as handle, open(log_path, "wb") as log:
        # 自己一個行程群組:只砍 `sh -c` 那一層的話,孫行程還拿著 pipe,tee 等不到 EOF。
        proc = subprocess.Popen(cmd, shell=True, cwd=start_in, env=env, stdin=handle,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        reader = threading.Thread(target=tee, args=(proc.stdout, log), daemon=True)
        reader.start()
        if pid_path:
            with open(pid_path, "w", encoding="utf-8") as pid_file:
                pid_file.write("%d\n" % proc.pid)
        try:
            rc = proc.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise
        finally:
            reader.join(timeout=10)
            if pid_path:
                os.remove(pid_path)
except subprocess.TimeoutExpired:
    sys.stderr.write("auto-fix: %s超過 %s 秒還沒回來 —— 當它沒交\n" % (LABEL, timeout))
    rc = 124
except OSError as exc:
    sys.stderr.write("auto-fix: %s起不來 —— %s\n" % (LABEL, exc))
    rc = 127
if guarded is not None:
    rc = guard_verdict(guarded, guard_conf, rc)
print(rc)
PY
}

run_verifier() {   # $1 = 派工文  $2 = 副本根  $3 = 第幾輪  $4 = log  $5 = 命令(預設 VERIFIER_CMD);印出 rc
    run_agent "${5:-$VERIFIER_CMD}" "$1" "$2" verifier "$3" "$4"
}

verifier_done() {   # $1 = rc  $2 = 第幾輪  $3 = log  $4 = 起跑秒;事件 + cost;撞額度 / 不可用設 V_QUOTA(#132)
    V_QUOTA=""
    v_note=""
    if [ "$1" -ne 0 ] && [ "$1" -ne 124 ] && fallback_wanted verifier "$3" "$1"; then
        V_QUOTA=1
        v_note="$Q_KIND:$Q_DETAIL"
    fi
    if [ "$1" -eq 124 ]; then
        v_outcome=timeout
    elif [ "$1" -ne 0 ]; then
        v_outcome=error
    elif [ -f "$VFIX/patch-verify.diff" ] || [ -f "$VFIX/work/patch-verify.diff" ]; then
        v_outcome=handed-in
    else
        v_outcome=no-patch
    fi
    if [ "$1" -eq 0 ]; then
        ev agent.done --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
            --kv run_id="$RUN_ID" --kv round="$2" --kv rc="$1" --kv agent=auto-fix-verifier
    else
        ev agent.failed --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
            --kv run_id="$RUN_ID" --kv round="$2" --kv rc="$1" --kv agent=auto-fix-verifier
    fi
    write_cost verifier "$2" "$VERIFIER_MODEL" "$3" "$(( $(date +%s) - $4 ))" "$v_outcome" "${V_EFFORT:-}" \
        "$v_note"
}

# 驗證者執行中撞額度 / 不可用(#132 B1,與 worker 的 #121 A2/A3 同一手):判讀只在 verifier_done
# 用 `role-models.py classify`(逾時、一般失敗、沒交件不換)。可換 → 這一次的 cost 已照記(outcome=error,
# note 記原因),副本留著,依表取下一個候選、照新模型重組派工文($1),在同一份副本、同一輪重起;
# 有 Controller 只發 `agent.recovery_needed`(role=verifier)、不自己起;整串用完 → Blocked + 一頁。
# 越界照樣先收(#115 A7):呼叫端先 guard_stop 上一次的 log,重起的那一次這裡收。
verifier_fallback() {   # $1 = 重組派工文的函式;回 0 = 不用換或已換好(VWRC / VLOG 是最後那一次),回 1 = 停(V_STOP)
    V_STOP=""
    V_SWAPPED=""
    while [ -n "${VWRC:-}" ] && [ "$VWRC" -ne 0 ] && [ -n "${V_QUOTA:-}" ]; do
        route verifier --exhausted-index "$V_INDEX" --evidence "$VLOG" --evidence-rc "$VWRC"
        case $R_RC in
            2) V_STOP=refused; return 1 ;;
            3) chain_exhausted verifier; V_STOP=exhausted; return 1 ;;
        esac
        if ! mkdir "$(dirname "$VDISPATCH")/recovery-verifier-round$r-c$R_INDEX.owner" 2>/dev/null; then
            echo "auto-fix: #$ID 第 $r 輪驗證者第 $R_INDEX 格已有別的驅動者接手 —— 這裡不再起" >&2
            V_STOP=handed
            return 1
        fi
        if [ "${AC_RECOVERY_OWNER:-}" = controller ]; then
            ev agent.recovery_needed --ticket "$ID" --role verifier --model "$R_MODEL" --kv run_id="$RUN_ID" \
                --kv round="$r" --kv tool="$R_TOOL" --kv candidate_index="$R_INDEX" \
                ${R_EFFORT:+--kv effort=$R_EFFORT} --kv copy="$VFIX" --kv dispatch="$VDISPATCH" --note "$Q_KIND"
            echo "auto-fix: #$ID 第 $r 輪驗證者 $VERIFIER_MODEL 撞到 $Q_KIND —— 下一個候選 $R_MODEL 交給 Controller 接續(副本 $VFIX 留著)"
            V_STOP=handed
            return 1
        fi
        echo "auto-fix: #$ID 第 $r 輪驗證者 $VERIFIER_MODEL 撞到 $Q_KIND —— 依表換第 $R_INDEX 格 $R_MODEL,副本 $VFIX 留著、同一輪重起"
        use_verifier || { V_STOP=refused; return 1; }
        "$1" || { V_STOP=packet; return 1; }
        VLOG=$(dirname "$VDISPATCH")/verifier-round$r-c$V_INDEX.log
        ev agent.start --driver $$ --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
            --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-verifier
        VSTART=$(date +%s)
        VWRC=$(run_verifier "$VDISPATCH" "$VFIX" "$r" "$VLOG" \
            "$(session_open verifier "$VERIFIER_CMD" "$VFIX" "$r" "$VERIFIER_MODEL")")
        verifier_done "$VWRC" "$r" "$VLOG" "$VSTART"
        V_SWAPPED=1
        if guard_stop "$VLOG"; then
            V_STOP=guard
            return 1
        fi
    done
    return 0
}

# 驗證者那一手停下(V_STOP)而 worker 這一輪已經跑過:它的 commit 留在分支、EVIDENCE 先收進 reports、
# cost 照記,worktree 與副本留著;
# 設定 ROUND_RC。交給 Controller 的不轉 Blocked、不發頁(同 worker 那一支,接續的是它)。
verifier_halt() {
    [ ! -f "$FIX/EVIDENCE-round$r.md" ] || cp "$FIX/EVIDENCE-round$r.md" "$(dirname "$DISPATCH")/"
    worker_delivery
    if [ -n "$W_HANDED" ]; then worker_cost handed-in; else worker_cost error; fi
    case $V_STOP in
        guard) ;;
        exhausted) attempt_failed quota-exhausted; ROUND_RC=7 ;;
        handed)
            echo "auto-fix: 第 $r 輪的驗證者交給 Controller 接續 —— worker 的 commit 在分支 $(ticket_branch),worktree $WT 留著"
            ROUND_RC=7 ;;
        packet) attempt_failed verifier-no-patch; ROUND_RC=6 ;;
        *) attempt_failed route-refused; ROUND_RC=2 ;;
    esac
}

v_packet() {   # 這一輪(平行 / 等交件)驗證者的派工文;換候選重起時照新模型重組
    verifier_packet "$VDISPATCH" "$r" "$VFIX" "$V_BASE" "${V_HELD:+$(ticket_branch)}" \
        "${V_COPY_HEAD:-}" "${V_PARALLEL:-}"
}

# **與 worker 平行**(#51 C2)只在票 `interface_fixed=true`(#60):驗證者不等 worker(D-020),
# 所以在 worker 派出**之前**就起跑,worker 回來之後 `wait_verifier` 才收。其餘的票在 worker
# 第 1 輪交件之後才起、起了就收 —— 同一支、同一個副本做法,只差起跑的時機。
# 副本是票的 base_sha(`git archive`,與 test_defect 那條路一樣是 {work,base} 兩份)。
# 驗證者副本 `{base,work}` 兩份,都是 `git archive <ref>` 的內容(worktree 裡沒 commit 的不進去)。
archive_copy() {   # $1 = ref  $2 = 副本根;取不出回 1
    rm -rf "$2"
    mkdir -p "$2/base"
    git -C "$ROOT" archive "$1" 2>/dev/null | tar -x -C "$2/base" || return 1
    cp -R "$2/base" "$2/work"
    ref_tree "$V_BASE" "$2"
}

# 驗證者量紅的乾淨基底(#182):`<副本>/ref` = 票 base_sha 的樹 + 一個記著那個 sha 的
# `.ac-base-sha`(`verify-case.py` 的 SHA_MARKER)。驗證者的 cwd / AC_CODE_ROOT 是封存副本、
# AC_ROOT 是拋棄式控制根,`agent_env` 故意把指回主 repo 的 AC_* 拿掉 —— 它手上沒有任何
# git 樹,`red --ref <sha>` 解不開。git 由這裡(有 git 的這一手)先做完,派工文的
# `red --ref` 指這個目錄。不放在 base/ 裡:base/ 是 patch-verify 的對照組,案例疊上去就髒了。
ref_tree() {   # $1 = 票的 base_sha  $2 = 副本根;取不出回 1
    rt_sha=$(git -C "$ROOT" rev-parse -q --verify "$1^{commit}" 2>/dev/null) || return 1
    [ -n "$rt_sha" ] || return 1
    rm -rf "$2/ref"
    mkdir -p "$2/ref"
    git -C "$ROOT" archive "$rt_sha" 2>/dev/null | tar -x -C "$2/ref" || return 1
    printf '%s\n' "$rt_sha" > "$2/ref/.ac-base-sha"
}

ticket_base_sha() {   # 印出票的 base_sha(沒有就空行)
    python3 - "$TF" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("base_sha") or "")
except (OSError, ValueError):
    print("")
PY
}

# **副本從哪一版展**(#157,同 #149 那一支):平行(interface_fixed)= 票的 base_sha;等交件才起的 =
# 票分支的頭(worker 的實作在上面,patch-verify 才對得上分支),分支對 base_sha 還沒有 commit 就不起。
# archive 的那一版記在 V_COPY_SHA:收件時當三方合併的 ancestor(`apply.sh --verifier-base`)。
start_verifier_bg() {   # uses r/VFIX/VDISPATCH;設定 VPID/VLOG/VRC_FILE/VSTART/V_COPY_SHA/V_COPY_HEAD
    V_COPY_SHA=""
    V_COPY_HEAD=""
    vs_ref=$V_BASE
    if [ -z "$V_PARALLEL" ]; then
        vs_head=$(git -C "$ROOT" rev-parse -q --verify "refs/heads/$(ticket_branch)^{commit}" 2>/dev/null)
        vs_count=0
        [ -z "$vs_head" ] || [ -z "$V_BASE" ] \
            || vs_count=$(git -C "$ROOT" rev-list --count "$V_BASE..$vs_head" 2>/dev/null || echo 0)
        if [ "${vs_count:-0}" -lt 1 ]; then
            echo "auto-fix: #$ID 票分支 $(ticket_branch) 對 base_sha ${V_BASE:-(空)} 還沒有 commit —— 驗證者等 worker 的 commit(verifier waits for worker commits),不起" >&2
            return 1
        fi
        vs_ref=$vs_head
        V_COPY_HEAD=$vs_head
    fi
    if ! archive_copy "$vs_ref" "$VFIX"; then
        rm -rf "$VFIX"
        echo "auto-fix: 取不出 ${vs_ref:-(空)} 給驗證者的副本 —— 驗證者不起" >&2
        return 1
    fi
    V_COPY_SHA=$(git -C "$ROOT" rev-parse -q --verify "$vs_ref^{commit}" 2>/dev/null)
    v_packet || { rm -rf "$VFIX"; return 1; }
    VLOG=$(dirname "$VDISPATCH")/verifier-round$r.log
    VRC_FILE=$(dirname "$VDISPATCH")/verifier-round$r.rc
    rm -f "$VRC_FILE"
    if [ -n "$V_PARALLEL" ]; then
        echo "auto-fix: needs_verifier + interface_fixed —— 第 $r 輪的驗證者先起跑(背景,副本 $VFIX)"
    else
        echo "auto-fix: needs_verifier —— worker 第 $r 輪交件了,驗證者現在起跑(副本 $VFIX)"
    fi
    VCMD=$(session_open verifier "$VERIFIER_CMD" "$VFIX" "$r" "$VERIFIER_MODEL")
    ev agent.start --driver $$ --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-verifier
    VSTART=$(date +%s)
    run_verifier "$VDISPATCH" "$VFIX" "$r" "$VLOG" "$VCMD" > "$VRC_FILE" &
    VPID=$!
}

wait_verifier() {   # 設定 VPATCH / VEVIDENCE(沒交就是空字串)
    wait "$VPID"
    VPID=""
    VWRC=$(cat "$VRC_FILE" 2>/dev/null)
    [ -n "$VWRC" ] || VWRC=127
    rm -f "$VRC_FILE"
    verifier_done "$VWRC" "$r" "$VLOG" "$VSTART"
    verifier_collect
}

verifier_collect() {   # 設定 VPATCH / VEVIDENCE;換候選重起之後再收一次
    collect_from_copy "$VFIX" patch-verify.diff EVIDENCE-verifier.md
    # 沒交的那一次一樣留一份 result(`no-evidence` / `no-block`)—— 最需要它的就是那一次。
    harvest_result "$VFIX/EVIDENCE-verifier.md" \
        "$(dirname "$VDISPATCH")/result-verifier-round$r.json" verifier "$r"
    # 副本不在這裡收(#89):worker 那一輪收件或升級時一起處置 —— 反駁要接回的驗證者
    # 得在原副本續做。
    VPATCH=""
    VEVIDENCE=""
    if [ "$VWRC" -eq 0 ] && [ -f "$VFIX/patch-verify.diff" ]; then
        VPATCH=$VFIX/patch-verify.diff
        VEVIDENCE=$VFIX/EVIDENCE-verifier.md
    fi
    echo "auto-fix: 第 $r 輪的驗證者回來了(rc=$VWRC)—— patch-verify ${VPATCH:-沒交}"
}

dispatch_verifier() {   # uses r/FIX/DISPATCH/line/GAP_ROUND; sets PATCH_OUT/EVIDENCE/CASE_FIXED
    VFIX=$WTBASE/verify-t$ID/round$r
    V_STOP=""
    # 平行驗證者(interface_fixed,#51)已經在**同一個**副本交過件(專案 #775):先把它那兩份
    # 收進 reports 再清副本 —— 以前這裡第一手就 `rm -rf`,它的案例與驗紅一起沒了。
    # 它的案例由底下的 work/ 帶著走,收件時隨這一輪的 patch-verify 進分支;計畫由收件併進票。
    VKEPT_PATCH=""
    VKEPT_EVIDENCE=""
    if [ -n "$VPATCH" ] && [ -f "$VPATCH" ]; then
        VKEPT_PATCH=$(dirname "$DISPATCH")/patch-verify-round$r-parallel.diff
        VKEPT_EVIDENCE=$(dirname "$DISPATCH")/EVIDENCE-verifier-round$r-parallel.md
        cp "$VPATCH" "$VKEPT_PATCH"
        cp "$VEVIDENCE" "$VKEPT_EVIDENCE" 2>/dev/null || VKEPT_EVIDENCE=""
        echo "auto-fix: 平行驗證者的交付先收進 reports —— $(basename "$VKEPT_PATCH")、$(basename "${VKEPT_EVIDENCE:-(沒有 EVIDENCE)}")"
    fi
    # **副本 = 票分支現在的頭**(#107、#144):worker 本輪交了 commit 就從票分支展(案例要照的實作
    # 在上面);沒交(只派驗證者的那一輪、worker 沒 commit)才照 `$SRC`(round_once 那一版)。
    # 平行驗證者交過件的話 work/ 再加它的案例(#112):patch-verify 對 base/ 出,自然帶著它們。
    # `--dry-run`(只派驗證者的那一輪走得到)同 round_once(#96):副本已經在就原封不動。
    V_SRC=$SRC
    [ -z "${W_HANDED:-}" ] || V_SRC=$(ticket_branch)
    vfix_next="讀 worker 的 EVIDENCE 與分支 $V_SRC;要嘛重派 worker,要嘛人下場"
    VFIX_KEPT=""
    vfix_why=""
    if [ -n "$DRY" ] && [ -e "$VFIX" ]; then
        VFIX_KEPT=1
        echo "auto-fix: --dry-run —— 驗證者副本 $VFIX 已經在,不刪、不重建"
    else
        rm -rf "$VFIX"
        mkdir -p "$VFIX/base"
        if ! git -C "$ROOT" archive "$V_SRC" 2>/dev/null | tar -x -C "$VFIX/base"; then
            vfix_why="取不出 $V_SRC"
        else
            cp -R "$VFIX/base" "$VFIX/work"
            vd_ref=$(ticket_base_sha)
            if [ -n "$VKEPT_PATCH" ] && ! ( cd "$VFIX/work" \
                    && patch -p1 -E -f -F 0 --no-backup-if-mismatch -s -i "$VKEPT_PATCH" </dev/null ) >&2; then
                vfix_why="平行驗證者的 patch-verify($VKEPT_PATCH)套不上分支 $V_SRC"
                vfix_next="對照它與分支上 worker 的 commit 哪幾個檔撞了;要嘛人下場併,要嘛重派"
            elif ! ref_tree "$vd_ref" "$VFIX"; then
                vfix_why="取不出票的 base_sha ${vd_ref:-(空)} 給驗證者量紅(ref/)"
            fi
        fi
    fi
    if [ -n "$vfix_why" ]; then
        echo "auto-fix: 驗證者的副本建不出來 —— $vfix_why;驗證者不起" >&2
        rm -rf "$VFIX/base" "$VFIX/work" "$VFIX/ref"
        [ -n "${FIX_KEPT:-}" ] || shed_copies "$FIX" "$WT"
        block "#$ID 第 $r 輪驗證者的副本建不出來($vfix_why)"
        post "驗證者的副本建不出來" "$vfix_why,驗證者沒起。$vfix_next" "$FIX"
        return 1
    fi
    VDISPATCH=$(dirname "$DISPATCH")/dispatch-verifier-round$r.md
    if ! vd_packet; then
        shed_copies "$VFIX"
        return 1
    fi
    if [ -n "${GAP_ROUND:-}" ] && [ -n "$DRY" ]; then
        echo "auto-fix: 驗證者派工文 -> $(rel_root "$VDISPATCH")"
        echo "auto-fix: --dry-run —— 不起驗證者。派工文在上面那一份。"
        [ -n "$VFIX_KEPT" ] || shed_copies "$VFIX"
        return 2
    fi
    if [ -n "${GAP_ROUND:-}" ]; then
        echo "auto-fix: 缺口 —— 起第 $r 輪的驗證者(這一輪不派 worker)"
    else
        echo "auto-fix: test_defect —— 起第 $r 輪的新驗證者 worker"
    fi
    VLOG=$(dirname "$DISPATCH")/verifier-round$r.log
    ev agent.start --driver $$ --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-verifier
    VSTART=$(date +%s)
    VWRC=$(run_verifier "$VDISPATCH" "$VFIX" "$r" "$VLOG" \
        "$(session_open verifier "$VERIFIER_CMD" "$VFIX" "$r" "$VERIFIER_MODEL")")
    verifier_done "$VWRC" "$r" "$VLOG" "$VSTART"
    collect_from_copy "$VFIX" patch-verify.diff EVIDENCE-verifier.md
    if guard_stop "$VLOG"; then
        return 1
    fi
    # 執行中撞額度(#132 B1):同一份副本換下一個候選;停下(V_STOP)由呼叫端設 ROUND_RC,副本留著。
    verifier_fallback vd_packet || return 1
    collect_from_copy "$VFIX" patch-verify.diff EVIDENCE-verifier.md
    PATCH_OUT=$VFIX/patch-verify.diff
    EVIDENCE=$VFIX/EVIDENCE-verifier.md
    # 驗證者那條路也要留痕跡,**而且在「驗證者沒交件就回去」之前** —— 沒交的那一次
    # 正是最需要一份「沒交」的檔的那一次。
    harvest_result "$EVIDENCE" \
        "$(dirname "$DISPATCH")/result-verifier-round$r.json" verifier "$r"
    shed_copies "$VFIX"
    shed_copies "$FIX" "$WT"
    if [ "$VWRC" -ne 0 ] || [ ! -f "$PATCH_OUT" ]; then
        block "#$ID 第 $r 輪的驗證者沒交出 patch-verify"
        post "驗證者沒交出 patch-verify" \
             "讀驗證者派工文與副本;不要讓主線自己改案例" "$VFIX"
        return 1
    fi
    CASE_FIXED=1
    return 0
}

# test_defect / 缺口那一輪驗證者的派工文:一般那一份 + 這一輪多的那一段(紅榜、案例檔路徑)。
# 換候選重起時照新模型重組(#132)。
# base 是**票的 base_sha**(#181),不是狀態檔的 `repair_context.base_sha`:那一格是閘門開跑時
# 主線的頭(`gate.sh` 的 `BASE_SHA`),而派工文的 `red --ref` 要與 `verify-case.py red` 的預設
# ref(票的 base_sha)是同一個 sha。#125 第 2 輪派工文印 57e170d、票是 45ec9e15,就是這一格。
vd_packet() {
    vd_base=$(ticket_base_sha)
    verifier_packet "$VDISPATCH" "$r" "$VFIX" "${vd_base:-$S_BASE}" || return 1
    python3 - "$ROOT" "$ID" "$RUN_ID" "$r" "${line:-}" "${W_HANDED:+$V_SRC}" "$VKEPT_PATCH" \
            "${GAP_ROUND:+gap}" "${V_HAS_BASELINE:-}" >> "$VDISPATCH" <<'PY'
import os, sys
root, ident, run_id, r, objection, branch, kept, mode, has_baseline = sys.argv[1:10]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

data = status.read(root, ident, run_id)
files = []
if mode == "gap" and not has_baseline:
    # 案例還沒交(baseline 空,D-018):一般驗證者派工文就是上面那一份,這裡只補上一輪的缺口列。
    print("\n## 上一輪閘門的缺口(kind=GAP,第 %s 輪只派你、不派 worker)" % r)
    for row in data.get("failures") or []:
        print("- %s %s (%s:%s)" % (row.get("kind") or "GAP", row.get("case") or "?",
                                     row.get("file") or "?", row.get("line") or 0))
        print("  " + ((row.get("excerpt") or "(沒有 excerpt)").splitlines() or [""])[0])
    raise SystemExit(0)
print("\n# 這一輪多的一件事:test_defect(第 %s 輪,修案例)" % r)
if mode == "gap":
    print("\n上一輪閘門的紅榜只有缺口列(kind=GAP):這一輪不派 worker,只派你補案例。")
else:
    print("\n產品 worker 提出:`%s`" % objection)
if branch:
    print("副本 = **分支 `%s` 現在的頭**(本輪 worker 的 commit 都在上面;base/ 與 work/ 都已含它);"
          "只修案例,不改產品程式。" % branch)
    print("patch-verify 要對這份副本的 base/ 出(增量:只有你改的案例);收件時在分支上補一個 commit。")
else:
    print("副本是**分支上**那一版(前幾輪的 commit 都在);只修案例,不改產品程式。")
if kept:
    print("work/ 另含本輪平行驗證者交的案例(`%s`,base/ 沒有):它們隨你的 patch-verify 進分支,"
          "留著、不要刪。" % kept)
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
PY
    return 0
}

run_worker() {   # $1 = 命令  $2 = 派工文;設定 WRC、WORKER_LOG 由呼叫端先給
    ev agent.start --driver $$ --ticket "$ID" --role worker --model "$WORKER_MODEL" \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix ${T_EFFORT:+--kv effort=$T_EFFORT}
    WSTART=$(date +%s)
    WRC=$(run_agent "$1" "$2" "$FIX" worker "$r" "$WORKER_LOG" 1 "" "$WT")
    if [ "$WRC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --role worker --model "$WORKER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix \
            ${T_EFFORT:+--kv effort=$T_EFFORT}
    else
        ev agent.failed --ticket "$ID" --role worker --model "$WORKER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix \
            ${T_EFFORT:+--kv effort=$T_EFFORT}
    fi
    W_COST_LOG=$WORKER_LOG
    W_COST_SECS=$(( $(date +%s) - WSTART ))
    [ "$WRC" -eq 0 ] || echo "auto-fix: worker 自己回非零 —— 還是看它交了什麼,不看它說什麼"
}

# ## worker 的反駁由開題者判(#89,D-041)
# `ticket-wrong` / `blocking` 不再直接轉 Blocked 等主線:起一個開題者判(cwd 與 `AC_ROOT` 是
# 主 repo 根 —— 它要改真票)。開題者一輪只有兩種結果(D-054 Q8):accepted = 票面改好
# (worker 讀錯票也算 —— 澄清寫進票面,下一個人才不會再讀錯)→ 接回同一個驗證者(這一輪平行
# 起的那一個)改案例、再接回同一個 worker 在原副本續做,不換輪、不丟副本、不發頁;escalate =
# 一題附建議的裁示題,判決行之後那段原文轉進收件頁與那一筆的 `follow_up`。判決行其他字樣
# = 沒交判決。只有四種進主線收件匣:開題者判 escalate、同票第二次反駁、開題者沒判出來、
# session 接不回。objections 那一筆的 disposition / triage / follow_up **只由這裡**寫。
opener_cost() {   # 開題者那一列(#94 C5c)。接回的 worker 跑完才寫:它讀到的票要與開題者讀到的同一份
    [ -n "${O_COST:-}" ] || return 0
    write_cost opener "$r" "$O_MODEL" "$OLOG" "$OSECS" "$O_COST"
    O_COST=""
}

escalate_objection() {   # $1 = 字樣  $2 = 細節  $3 = 開題者的裁示題(題目、選項、建議;可空);票轉 Blocked、一頁、ROUND_RC=3,副本與 session 都留著
    opener_cost
    ev objection.triage.done --ticket "$ID" --kv round="$r" --kv verdict=escalate --kv reason="$1"
    w_sid=$(session_field worker session_id)
    w_cwd=$(session_field worker cwd)
    if [ -n "${3:-}" ]; then
        # 裁示題原文進那一筆的 follow_up(既有空格,不加欄位):下一輪的交接包也帶著它。
        python3 - "$TF" "$ID" "$line" "$3" <<'PY'
import json, os, subprocess, sys
path, ident, line, question = sys.argv[1:5]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
from ticket import objection_parts

category, body = objection_parts(line)
with open(path, encoding="utf-8") as handle:
    rows = json.load(handle).get("objections") or []
for row in reversed(rows):
    if isinstance(row, dict) and row.get("category") == category \
            and (row.get("body") or "") == body:
        row["follow_up"] = question
        break
subprocess.run([sys.executable, os.path.join(os.environ["AC_CONTROL_DIR"], "ticket.py"),
                "set", ident, "objections", json.dumps(rows, ensure_ascii=False)],
               stdout=subprocess.DEVNULL, check=False)
PY
    fi
    block "#$ID 的 worker 提反駁:$line($1)"
    post "worker 提反駁(票寫錯 / 需裁示)" \
         "$1${2:+:$2}。${3:+開題者交了一題附建議的裁示題(見「補充」,也在那一筆的 follow_up),裁了寫進那一筆的處置;}讀 EVIDENCE 那一行反駁,處置它(處置字照 ticket.py 的 DISPOSED);沒處置的阻擋項 land 與 close 都會拒絕${V_HELD}。主線手接回:worker session ${w_sid:-(沒有)},副本 ${w_cwd:-?}(副本與 session 都留著)" \
         "$EVIDENCE" "${3:-}"
    attempt_failed objection
    ROUND_RC=3
}

ticket_changes() {   # $1 = 開題者起跑前的票;印出改過的欄位(現況全文)
    python3 - "$1" "$TF" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    before = json.load(handle)
with open(sys.argv[2], encoding="utf-8") as handle:
    after = json.load(handle)
changed = [key for key in after if key not in ("state_version", "objections", "cost")
           and after.get(key) != before.get(key)]
if not changed:
    print("(開題者沒改票面任何一格)")
for key in changed:
    print("### `%s`(現況全文)" % key)
    value = after[key]
    if isinstance(value, list):
        for item in value:
            print("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
    else:
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    print("")
PY
}

triage_objection() {   # uses r/line/category/EVIDENCE/FIX/PATCH_OUT/DISPATCH;接回了回 0,停下來回 1(ROUND_RC 已設)
    O_COST=""
    # (b) 同票第二次反駁:票上已經有一筆帶 triage 的(含同一次執行裡被接回的 worker 再交的)。
    if python3 - "$TF" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    rows = json.load(handle).get("objections") or []
sys.exit(0 if any(isinstance(row, dict) and row.get("triage") for row in rows) else 1)
PY
    then
        escalate_objection "同票第二次反駁" "第一次已由開題者判過,這一次不再起開題者"
        return 1
    fi
    # 判反駁的開題者也查角色表(#122 A2):一般開題 opener 鏈,不帶票的 model(#131 A10 只套
    # worker 鏈)、也不帶票的 complexity(那是 worker 工作的標記,不是這次開題)。命令照
    # `ticket.py agent-command`,base 是 config 的 `opener.command`(權限與輸出格式在那裡)。
    O_BASE=$(cfg opener.command "")
    O_EXHAUSTED=""
    while :; do
        _tc=$T_COMPLEXITY
        T_COMPLEXITY=""
        route opener ${O_EXHAUSTED:+--exhausted-index "$O_EXHAUSTED"}
        O_RC=$?
        T_COMPLEXITY=$_tc
        case $O_RC in
            2) ev objection.triage.start --ticket "$ID" --kv round="$r" --kv category="$category"
               escalate_objection "角色表選不出開題者" "role-models.py resolve --role opener rc=2"
               return 1 ;;
            3) ev objection.triage.start --ticket "$ID" --kv round="$r" --kv category="$category"
               escalate_objection "開題者候選整串耗盡" "$R_REASON"
               return 1 ;;
        esac
        O_MODEL=$R_MODEL
        O_TOOL=$R_TOOL
        O_INDEX=$R_INDEX
        O_HEAD=""
        OPENER_CMD=""
        if [ -n "$O_BASE" ]; then
            OPENER_CMD=$(python3 "$AC/ticket.py" agent-command --model "$R_MODEL" --tool "$R_TOOL" \
                    --base "$(with_envelope "$O_BASE")" --swap-model --effort "$R_EFFORT") || OPENER_CMD=""
            O_HEAD=$(python3 -c 'import shlex,sys
try:
    parts = shlex.split(sys.argv[1])
except ValueError:
    parts = sys.argv[1].split()
print(next((tok for tok in parts if "=" not in tok), ""))' "$OPENER_CMD")
        fi
        # codex / agy 候選的 CLI 不在 = 這一格此刻不能用,換下一格(與 pick_worker 同一手)。
        if [ -n "$O_HEAD" ] && ! command -v "$O_HEAD" >/dev/null 2>&1 \
                && { [ "$O_TOOL" = codex ] || [ "$O_TOOL" = agy ]; }; then
            echo "auto-fix: #$ID 開題者候選第 $O_INDEX 格 $O_MODEL 的 $O_HEAD 不在 PATH —— 換下一格" >&2
            O_EXHAUSTED=$O_INDEX
            continue
        fi
        break
    done
    # (c) 開題者命令缺:config 沒有 opener 區塊,或命令找不到。
    if [ -z "$O_HEAD" ] || ! command -v "$O_HEAD" >/dev/null 2>&1; then
        ev objection.triage.start --ticket "$ID" --kv round="$r" --kv category="$category"
        escalate_objection "開題者命令缺" \
            "board/config.json 的 opener.command ${O_HEAD:+($O_HEAD)找不到}${O_HEAD:-沒設}"
        return 1
    fi
    echo "auto-fix: #$ID 照角色表起開題者:$O_MODEL(tool $O_TOOL;鏈第 $O_INDEX 格:$R_REASON)"
    # (d) session 接不回:worker 那一列(與這一輪平行起的驗證者那一列)resumable 不是 true。
    if [ "$(session_field worker resumable)" != true ]; then
        escalate_objection "不能接回" "worker:$(session_field worker why)"
        return 1
    fi
    VRESUME=""
    if [ -n "$V_WANT" ] && [ -n "$V_PARALLEL" ] && [ -d "$VFIX/work" ]; then
        if [ "$(session_field verifier resumable)" != true ]; then
            escalate_objection "不能接回" "驗證者:$(session_field verifier why)"
            return 1
        fi
        VRESUME=1
    fi

    ev objection.triage.start --ticket "$ID" --kv round="$r" --kv category="$category"
    ODISPATCH=$(dirname "$DISPATCH")/dispatch-opener-round$r.md
    if ! rules_pack "$ODISPATCH" opener "$O_MODEL"; then
        escalate_objection "開題者的規則包產不出來" "模型 $O_MODEL;缺項見 rules.py 送的 decision 頁"
        return 1
    fi
    REPORTS=$(cfg reports_dir reports)
    TRIAGE_REL=$REPORTS/t$ID/triage-round$r.md
    TRIAGE_FILE=$ROOT/$TRIAGE_REL
    EV_REL=$(python3 -c 'import os,sys;print(os.path.relpath(os.path.realpath(sys.argv[1]),os.path.realpath(sys.argv[2])))' "$EVIDENCE" "$ROOT")
    {
        printf '%s\n' "$RULES_TEXT"
        echo ""
        echo "## 判反駁"
        echo "#$ID 第 $r 輪的 worker 在 EVIDENCE 提了反駁(逐字):"
        echo ""
        echo "$line"
        echo ""
        echo "- 票號:#$ID(票檔 \`$TICKETS/$ID.json\`;改票只走 \`python3 scripts/ticket.py set $ID <欄位> '<JSON>'\`)"
        echo "- EVIDENCE:\`$EV_REL\`"
        echo "- 只有兩種判決:"
        echo "  - accepted = 改好票面接回 worker。反駁成立就照它改;worker 讀錯了票也判 accepted:把澄清寫進票面,下一個人才不會再讀錯"
        echo "  - escalate = 要使用者取捨,交一題附建議的裁示題給主線:判決行之後寫「題目:」「選項:」(每個選項一行)「建議:」(選哪個、為什麼);這一段原文會進主線的收件頁與那一筆的 follow_up"
        echo "- 判決行寫其他字樣 = 沒交判決(轉主線)"
        echo "- objections 那一筆的 disposition / triage / follow_up 由 auto-fix 依你的判決檔寫,你不要寫"
        echo ""
        echo "把判決寫進 $TRIAGE_REL,第一行 TRIAGE: <accepted|escalate> <一句理由>"
    } > "$ODISPATCH"
    BEFORE=$(dirname "$DISPATCH")/ticket-before-triage-round$r.json
    cp "$TF" "$BEFORE"
    rm -f "$TRIAGE_FILE"
    O_TIMEOUT=$(cfg opener.timeout_seconds 1800)
    OLOG=$(dirname "$DISPATCH")/opener-round$r.log
    echo "auto-fix: 反駁交給開題者判 —— $OPENER_CMD(cwd $ROOT,時限 $O_TIMEOUT 秒)"
    ev agent.start --driver $$ --ticket "$ID" --role opener --model "$O_MODEL" \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-opener
    OSTART=$(date +%s)
    ORC=$(run_agent "$OPENER_CMD" "$ODISPATCH" "$ROOT" opener "$r" "$OLOG" "" "$O_TIMEOUT")
    OSECS=$(( $(date +%s) - OSTART ))
    # 沒正常退出、或沒寫出 TRIAGE 行的開題者記 undecided(#94 C5c)。
    O_COST=undecided
    if [ "$ORC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --role opener --model "$O_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$ORC" --kv agent=auto-fix-opener
    else
        ev agent.failed --ticket "$ID" --role opener --model "$O_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$ORC" --kv agent=auto-fix-opener
    fi
    if [ "$ORC" -eq 124 ]; then
        escalate_objection "開題者逾時" "超過 $O_TIMEOUT 秒,整棵子行程樹已收掉;log 在 $OLOG"
        return 1
    fi
    if [ "$ORC" -ne 0 ]; then
        escalate_objection "開題者退出 $ORC" "log 在 $OLOG"
        return 1
    fi
    eval "$(python3 - "$TRIAGE_FILE" <<'PY'
import re, shlex, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        first = handle.readline().rstrip("\n")
        rest = handle.read().strip()
except OSError:
    first = rest = ""
hit = re.match(r"TRIAGE: (accepted|escalate)(?:\s+(.*))?$", first)
print("TRIAGE_VERDICT=%s" % shlex.quote(hit.group(1) if hit else ""))
print("TRIAGE_REASON=%s" % shlex.quote((hit.group(2) or "").strip() if hit else ""))
print("TRIAGE_QUESTION=%s" % shlex.quote(rest if hit else ""))
PY
)"
    O_COST=${TRIAGE_VERDICT:-undecided}
    case "$TRIAGE_VERDICT" in
        "")
            escalate_objection "開題者沒交判決" \
                "$TRIAGE_REL 不在,或第一行不是 TRIAGE: <accepted|escalate> <理由>(其他判決字樣也算沒交)"
            return 1 ;;
        escalate)
            escalate_objection "開題者判需要裁示" "$TRIAGE_REASON" "$TRIAGE_QUESTION"
            return 1 ;;
    esac
    ev objection.triage.done --ticket "$ID" --kv round="$r" --kv verdict="$TRIAGE_VERDICT"
    echo "auto-fix: 開題者判 $TRIAGE_VERDICT —— $TRIAGE_REASON"

    # 第一段的證據改名留著,續做那一份不蓋掉它。
    OBJ_EVIDENCE=$FIX/EVIDENCE-round$r-objection.md
    mv "$EVIDENCE" "$OBJ_EVIDENCE"
    # 開題者改了票(state_version 前進):重讀現況再寫,不帶舊版本。
    python3 - "$TF" "$ID" "$line" "$TRIAGE_VERDICT" "$TRIAGE_REASON" "$r" "$OBJ_EVIDENCE" <<'PY'
import json, os, subprocess, sys
path, ident, line, verdict, reason, rnd, evidence = sys.argv[1:8]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
from ticket import objection_parts

category, body = objection_parts(line)
with open(path, encoding="utf-8") as handle:
    rows = json.load(handle).get("objections") or []
for row in reversed(rows):
    if isinstance(row, dict) and row.get("category") == category \
            and (row.get("body") or "") == body:
        row["disposition"] = verdict
        row["triage"] = {"verdict": verdict, "reason": reason, "round": int(rnd),
                         "by": "auto-fix"}
        row["evidence"] = os.path.relpath(evidence, os.environ["AC_ROOT"])
        break
subprocess.run([sys.executable, os.path.join(os.environ["AC_CONTROL_DIR"], "ticket.py"),
                "set", ident, "objections", json.dumps(rows, ensure_ascii=False)],
               stdout=subprocess.DEVNULL, check=False)
PY

    if [ -n "$VRESUME" ]; then
        VRDISPATCH=$(dirname "$DISPATCH")/dispatch-verifier-resume-round$r.md
        {
            echo "## 被接回"
            echo "你是 #$ID 第 $r 輪**同一個**驗證者 session,被接回在原副本續做(\`$VFIX\`)。"
            echo "worker 提了反駁:\`$line\`"
            echo "開題者判 accepted:$TRIAGE_REASON"
            echo ""
            echo "票面改過的欄位(現況全文,照它改你的案例):"
            echo ""
            ticket_changes "$BEFORE"
            echo "交件照原派工文:\`patch-verify.diff\` 與 \`EVIDENCE-verifier.md\` 放在 \`$VFIX\`(第一段那兩份已改名為 *-objection.*)。"
        } > "$VRDISPATCH"
        [ ! -f "$VFIX/patch-verify.diff" ] || mv "$VFIX/patch-verify.diff" "$VFIX/patch-verify-objection.diff"
        [ ! -f "$VFIX/EVIDENCE-verifier.md" ] || mv "$VFIX/EVIDENCE-verifier.md" "$VFIX/EVIDENCE-verifier-objection.md"
        VLOG=$(dirname "$DISPATCH")/verifier-resume-round$r.log
        echo "auto-fix: 接回第 $r 輪的驗證者(session $(session_field verifier session_id))"
        ev agent.start --driver $$ --ticket "$ID" --role verifier --model "$VERIFIER_MODEL" ${V_EFFORT:+--kv effort=$V_EFFORT} \
            --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix-verifier
        VSTART=$(date +%s)
        VWRC=$(run_verifier "$VRDISPATCH" "$VFIX" "$r" "$VLOG" \
            "$VERIFIER_CMD --resume $(session_field verifier session_id)")
        verifier_done "$VWRC" "$r" "$VLOG" "$VSTART"
        collect_from_copy "$VFIX" patch-verify.diff EVIDENCE-verifier.md
        harvest_result "$VFIX/EVIDENCE-verifier.md" \
            "$(dirname "$DISPATCH")/result-verifier-round$r.json" verifier "$r"
        if guard_stop "$VLOG"; then
            opener_cost
            return 1
        fi
        VPATCH=""
        VEVIDENCE=""
        if [ "$VWRC" -eq 0 ] && [ -f "$VFIX/patch-verify.diff" ]; then
            VPATCH=$VFIX/patch-verify.diff
            VEVIDENCE=$VFIX/EVIDENCE-verifier.md
        else
            echo "auto-fix: 接回的驗證者沒交出 patch-verify(rc=$VWRC)—— worker 不接回" >&2
            block "#$ID 第 $r 輪接回的驗證者沒交出 patch-verify"
            post "驗證者沒交出 patch-verify" \
                 "接回的驗證者(rc=$VWRC)沒交件,worker 沒有接回;讀它的 log $VLOG 與副本,不要讓主線自己改案例" \
                 "$VFIX"
            opener_cost
            attempt_failed verifier-no-patch
            ROUND_RC=5
            return 1
        fi
    fi

    RDISPATCH=$(dirname "$DISPATCH")/dispatch-resume-round$r.md
    {
        echo "## 被接回"
        echo "你是 #$ID 第 $r 輪**同一個** worker session,被接回在同一個 worktree 續做(\`$WT\`,不換輪)。"
        echo "你的反駁:\`$line\`"
        echo "開題者判 accepted:$TRIAGE_REASON"
        echo ""
        echo "票面改過的欄位(現況全文):"
        echo ""
        ticket_changes "$BEFORE"
        echo "照改過的票做,不要再提同一條反駁(同票第二次反駁直接轉主線)。"
        echo "交件照原派工文:在 \`$WT\` 的票分支 commit,\`EVIDENCE-round$r.md\` 放在 \`$FIX\`(第一段的 EVIDENCE 已改名為 \`EVIDENCE-round$r-objection.md\`)。"
    } > "$RDISPATCH"
    WORKER_LOG=$(dirname "$DISPATCH")/worker-resume-round$r.log
    echo "auto-fix: 接回第 $r 輪的 worker(session $(session_field worker session_id),cwd $(session_field worker cwd))"
    run_worker "$WORKER_CMD --resume $(session_field worker session_id)" "$RDISPATCH"
    opener_cost
    return 0
}

# 覆核的結局決定這一支的退出碼(#121 A6):閘門綠只是一半,review fail / missing
# 不能被 ROUND_RC=0 蓋成成功。review.sh 的 rc:0 pass、1 fail、3 沒交件、4 寫不進票。
review_outcome() {   # $1 = review.sh 的 rc;設定 ROUND_RC
    case $1 in
        0) echo "auto-fix: 終態=review_pass"; ROUND_RC=0 ;;
        1) echo "auto-fix: 終態=review_fail(票留在 review.sh 轉的狀態,等主線裁示)"; ROUND_RC=9 ;;
        3) echo "auto-fix: 終態=review_missing(reviewer 沒交件,票停在 InReview)"; ROUND_RC=10 ;;
        4) echo "auto-fix: 終態=review_unwritten(review 或反駁寫不進票)"; ROUND_RC=10 ;;
        7) echo "auto-fix: 終態=review_exhausted(覆核者候選整串耗盡,或交給 Controller 接續)"; ROUND_RC=7 ;;
        *) echo "auto-fix: 終態=review_unfinished(review.sh rc=$1)"; ROUND_RC=10 ;;
    esac
    [ "$1" -eq 0 ] || echo "auto-fix: 覆核停下來了(rc=$1)—— 看 reports/inbox/ 那一頁"
}

fallback_wanted() {   # $1 = 角色  $2 = log  $3 = rc;provider 說額度用完 / 不可用才回 0,設定 Q_KIND / Q_DETAIL
    eval "$(python3 "$AC/role-models.py" classify --evidence "$2" --rc "$3" 2>/dev/null \
        | python3 -c 'import json, shlex, sys
try:
    got = json.load(sys.stdin)
except ValueError:
    got = {}
print("Q_KIND=%s" % shlex.quote(str(got.get("kind") or "other")))
print("Q_DETAIL=%s" % shlex.quote(str(got.get("detail") or "")))')"
    case $Q_KIND in
        quota_exhausted|provider_unavailable) return 0 ;;
        auth_config) echo "auto-fix: #$ID $1 的認證設定有問題,不換候選:$Q_DETAIL" >&2 ;;
    esac
    return 1
}

# 診斷輪(#125 A3/A4,D-076):上一輪紅了卻沒有可歸因的紅榜(rc≠0、failures 空),或閘門說環境可疑
# 而機械重跑也沒救回來。派的還是一般 worker、收件 / 閘門 / 覆核照舊(H2:沒有新的 agent 種類);
# 只是派工文裡紅榜那一段換成 `## 診斷`:rc、state、嫌疑列、log 尾巴、重跑閘門的指令、規則。
# 單票與批次同一份(批次的 log 是整合分支 worktree 的 gate.log)。
diagnosis_section() {   # $1 = 狀態檔  $2 = 閘門 log(空 = 狀態檔記的那一份)  $3 = 重跑閘門的指令  $4 = env|unattributed  $5 = 紅的是第幾輪  $6 = 寫到哪
    python3 - "$ROOT" "$@" > "$6" <<'PY'
import json, os, sys
root, status_file, log, gate_cmd, why, red_round = sys.argv[1:7]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

try:
    with open(status_file, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
phases = data.get("phases") or []
rc = data.get("rc")
if rc is None and phases:
    rc = phases[-1].get("rc")
if not log:
    # 狀態檔留的複本先(worktree 收掉之後它還在),再退回原路徑。
    for row in data.get("kept_logs") or []:
        if isinstance(row, dict) and row.get("kept"):
            log = os.path.join(root, row["kept"])
            break
    else:
        log = (data.get("logs") or [""])[0]
try:
    with open(log, encoding="utf-8", errors="replace") as handle:
        tail = handle.read().splitlines()[-200:]
except OSError as exc:
    tail = None
    missing = "(讀不到 %s:%s)" % (log or "(狀態檔沒記 log)", exc.strerror or exc)
print("")
print("## 診斷")
print("")
if why == "env":
    print("第 %s 輪的閘門**說環境可疑**(rc 86 / state env_suspect),機械重跑也沒救回來;"
          "紅榜因此是空的或不完整。這一輪是**診斷輪**(D-076):不是照紅榜修,是先找出閘門為什麼"
          "產不出可歸因的紅。" % red_round)
else:
    print("第 %s 輪的閘門紅了(rc≠0),但**一條可歸因的紅都解析不出來**(failures 是空的)。"
          "這一輪是**診斷輪**(D-076):不是照紅榜修,是先找出閘門為什麼產不出可歸因的紅。"
          % red_round)
print("")
print("- 閘門 rc:%s" % ("?" if rc is None else rc))
print("- 狀態檔 state:%s(`%s`)" % (data.get("state") or "?", os.path.relpath(status_file, root)))
print("- 狀態檔 note:%s" % ((data.get("note") or "").strip() or "(空)"))
print("- 重跑閘門(你在 worktree 裡重現用這一句):`%s`" % gate_cmd)
suspects = status.environment_suspects(data)
if why == "env" or suspects:
    print("")
    print("### 環境嫌疑(environment_suspect,%d 筆)" % len(suspects))
    if not suspects:
        print("- (狀態檔沒有列;閘門只回了 rc 86 —— preflight 說的話在底下 log 裡)")
    for row in suspects:
        print("- source=%s engine=%s why=%s count=%s threshold=%s"
              % (row.get("source") or "?", row.get("engine") or "?", row.get("why") or "?",
                 "?" if row.get("count") is None else row.get("count"),
                 "?" if row.get("threshold") is None else row.get("threshold")))
print("")
print("### 閘門 log 最後 200 行(`%s`;preflight 說環境可疑的那一條路,這裡是 preflight 的輸出)"
      % (os.path.relpath(log, root) if log and os.path.isabs(log) else log or "?"))
print("```")
print("\n".join(tail) if tail is not None else missing)
print("```")
print("")
print("### 規則")
print("- 找出閘門為什麼沒產出可歸因的紅榜(測試根本沒跑起來?harness / adapter 炸了?環境?),"
      "修好讓它產得出來 —— 修好之後閘門綠或紅都可以,紅了下一輪照紅榜修。")
print("- 准許的修法:harness、測試基礎設施、repo 內的環境設定。")
print("- **不准放寬或略過測試**:不刪 assert、不加 skip / expectedFailure、不改期望值。")
print("- 原因在 repo 外(機器、權限、外部服務):在 EVIDENCE 寫一行 "
      "`OBJECTION: blocking <原因>`,**不交 commit**。")
print("- 診斷只有一輪:這一輪之後閘門還是沒有可歸因的紅,就交主線,不會再派第二輪診斷。")
PY
}

worker_packet() {   # uses r/DISPATCH/MODEL/FIX/DIAG;組這一輪 worker 的派工文,規則包照 MODEL 挑模型卡
    # 換候選(額度 / 不可用)時重組一次:規則包要跟實際起的模型相符(#121 A1)。
    if [ "$r" -eq 1 ]; then
        # 第 1 輪沒有紅榜、沒有上一輪:派工文就是 `--dry-run --round 1` 那一份(#40)。
        if ! first_round_packet "$DISPATCH" "$MODEL"; then
            ROUND_RC=6
            return 1
        fi
    else
        if ! rules_pack "$DISPATCH" worker "$MODEL"; then
            ROUND_RC=6
            return 1
        fi
        DIAG_FILE=""
        if [ -n "${DIAG:-}" ]; then
            gate_command
            DIAG_FILE=$(dirname "$DISPATCH")/diagnosis-round$r.md
            diagnosis_section "$ROOT/$(cfg reports_dir reports)/t$ID/$S_RUN/status.json" "" \
                "(cd $WT && $gate_cmd)" "$DIAG" "$((r - 1))" "$DIAG_FILE"
        fi
        {
            printf '%s\n' "$RULES_TEXT"
            python3 - "$ROOT" "$ID" "$RUN_ID" "$r" "$FIX" "$TF" "$WT" "$(ticket_branch)" \
                "$S_LIMIT" "$DIAG_FILE" <<'PY'
import json, os, sys
root, ident, run_id, r, fix, tf, wt, branch, limit, diag_file = sys.argv[1:11]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

data = status.read(root, ident, run_id)
ctx = data.get("repair_context") or {}
snap = ctx.get("ticket") or {}
print("")
print("# 這一輪:#%s 第 %s 輪(%s)" % (ident, r, "診斷" if diag_file else "修復"))
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
# `base_sha` 這一格是**票的** base_sha(#182):`repair_context.base_sha` 是閘門開跑時主線的頭
# (`gate.sh` 的 `BASE_SHA`),拿它當 `git log <base_sha>..HEAD` 的起點會把主線的 commit 算進來。
try:
    with open(tf, encoding="utf-8") as handle:
        ticket_base = json.load(handle).get("base_sha") or ""
except (OSError, ValueError):
    ticket_base = ""
print("- base_sha:`%s`(票的 base_sha)" % (ticket_base or snap.get("base_sha") or "?"))
if ctx.get("base_sha") and ctx.get("base_sha") != ticket_base:
    print("- 閘門開跑時主線的頭(main head at gate time):`%s`" % ctx["base_sha"])
print("- 上一輪的改動:票分支 `%s` 上的 commit(`git log <base_sha>..HEAD`,你的 worktree 就在它的頭)"
      % branch)
print("- 上一輪的 EVIDENCE:`%s`" % (ctx.get("prev_evidence") or "(沒有 —— 第一輪)"))
print("- 主線重現用的指令(**你不要跑它** —— 它動的是主 repo 的閘門/worktree):`%s`(cwd `%s`)"
      % ((ctx.get("repro") or {}).get("cmd") or "?",
         (ctx.get("repro") or {}).get("cwd") or "?"))
if diag_file:
    print("- 你在 worktree 裡重現:用底下 `## 診斷` 那一段給的閘門指令;沒有紅榜可照著跑。")
else:
    print("- 你在 worktree 裡重現:照底下紅榜的 case 名單跑(例 `python3 -m unittest <模組>.<類>.<案例>`,"
          "cwd 是 worktree 裡那個測試模組所在的目錄);修好後跑同一組再交,整套留給閘門。")
print("- 這是第 %s 輪,上限 %s 輪(`ticket.py repair-limit %s`)" % (r, limit, ident))
if diag_file:
    with open(diag_file, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())
else:
    print("")
    print("## 紅榜(逐條,excerpt ≤ 20 行)")
for row in [] if diag_file else data.get("failures") or []:
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
    if row.get("kind") == status.GAP_KIND:
        # 缺案例(#126 A2,D-014):這一輪的驗證者補,worker 動了會與它的 patch-verify 打架。
        bits.append("**驗證者的,你不修、不寫 verify/**")
    if bits:
        print("- " + ";".join(bits))
    print("```")
    print(row.get("excerpt") or "(沒有 excerpt)")
    print("```")
# **反駁與裁示也是交接包的一格**(#57):覆核退回的理由、主線對每一條反駁的處置與
# follow_up 只住在票的 `objections[]`(現況,不是快照)。少了這一段,新 worker 照紅榜修完,
# 會把主線已經裁掉的做法再做一次、或把覆核退回的那一點原樣交回去。
try:
    with open(tf, encoding="utf-8") as handle:
        objections = json.load(handle).get("objections") or []
except (OSError, ValueError):
    objections = []
print("")
print("## 反駁、覆核退回與主線裁示(票的 `objections[]` 現況 —— 照裁示做,不要重吵)")
if not objections:
    print("- (沒有)")
for row in objections:
    if not isinstance(row, dict):
        continue
    source = "覆核退回" if os.path.basename(str(row.get("evidence") or "")) == "REVIEW.md" \
        else "反駁"
    print("- **%s** [%s] %s(提出:%s;出處 `%s`)"
          % (source, row.get("category") or "?", row.get("body") or "(沒有內容)",
             row.get("owner") or "?", row.get("evidence") or "?"))
    print("  - 處置:%s" % (row.get("disposition") or "**還沒處置**"))
    print("  - 主線裁示 follow_up:%s" % (row.get("follow_up") or "(沒有)"))
print("")
print("## 你的 worktree")
print("- `%s`(票分支 `%s`,cwd 就是它;上一輪的 commit 都在上面)" % (wt, branch))
print("- 在這裡改、`git add` / `git commit`;交件 = 分支多了至少一個 commit **而且工作樹乾淨**,"
      "沒 commit 的改動不算交件(腳本不替你 commit)")
print("- git 規矩見 `docs/DISPATCH-TEMPLATE.md` §1(規則包已節錄)")
print("")
print("## 你要交的兩樣")
print("1. 票分支 `%s` 上的 commit(訊息帶 `t%s`)。" % (branch, ident))
print("2. `%s/EVIDENCE-round%s.md`(worktree 外的輪次目錄)—— 段名照 `docs/DISPATCH-TEMPLATE.md` §8,其中**已排除的假設**那一段"
      % (fix, r))
print("   決定下一輪的人要不要把你查過的路再查一次。")
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。")
print("看到這一行,這支腳本會把它記成票的 `objections[]`、交給開題者判(判完可能把你接回"
      "同一個 worktree 續做;判不了才轉 Blocked 指派主線),**不會**再派下一輪。")
print("**不准**放寬既有斷言、不准把期望值改成程式現在印的東西(那是假綠家族)。")
PY
            only_tests_section worker
        } > "$DISPATCH"
    fi
}

# 票分支的 worktree(#144,D-054 Q9):worker 的 cwd、閘門跑的地方、落地收的分支,同一個,
# 從第 1 輪用到落地。已經在就沿用;分支在而 worktree 不在就掛上去;分支也沒有就從主線頭開
# (第 1 輪的 base_sha 已升到主線頭,同一個起點)。
ticket_worktree() {   # 設定 WT;建不出來回 1
    WT=$WTBASE/$(ticket_branch)
    [ -e "$WT/.git" ] && return 0
    mkdir -p "$WTBASE"
    if git -C "$ROOT" rev-parse -q --verify "refs/heads/$(ticket_branch)" >/dev/null; then
        git -C "$ROOT" worktree add -q "$WT" "$(ticket_branch)" || return 1
        echo "auto-fix: 沿用分支 $(ticket_branch),worktree $WT"
    else
        git -C "$ROOT" worktree add -q -b "$(ticket_branch)" "$WT" "$MAIN" || return 1
        echo "auto-fix: 開了分支 $(ticket_branch) 與 worktree $WT(從 $MAIN)"
    fi
}

# 交件判準(#144):票分支相對派工前多了至少一個 commit,而且 worktree 乾淨。未 commit 的改動
# 不算交件 —— 不替 worker commit、不另擋(替它 commit 就是腳本替人決定交什麼)。閘門寫的
# `gate.log*` 殘留不算改動(#57)。
worker_delivery() {   # $1 = 分支(缺 = 票分支;批次給整合分支)uses WT/W_BEFORE;設定 W_HANDED(1 = 交了)與 W_WHY
    W_BR=${1:-$(ticket_branch)}
    W_NEW=$(git -C "$WT" rev-list --count "$W_BEFORE..refs/heads/$W_BR" 2>/dev/null || echo 0)
    W_DIRTY=$(git -C "$WT" status --porcelain --untracked-files=all 2>/dev/null \
        | grep -vE '^\?\? gate\.log' | cut -c4- | head -5 | tr '\n' ' ')
    W_DIRTY=${W_DIRTY% }
    W_HANDED=""
    W_WHY=""
    if [ "${W_NEW:-0}" -lt 1 ]; then
        W_WHY="分支 $W_BR 相對派工前(${W_BEFORE%"${W_BEFORE#????????????}"})沒有新 commit${W_DIRTY:+;未 commit 的改動($W_DIRTY)不算交件}"
    elif [ -n "$W_DIRTY" ]; then
        W_WHY="分支多了 $W_NEW 個 commit,但 worktree 還有未 commit 的改動($W_DIRTY)—— 當作沒交"
    else
        W_HANDED=1
    fi
}

rel_root() { python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$1" "$ROOT"; }

# 輪次目錄(#144):worker 的 EVIDENCE、拋棄式控制根與 worker.pid 住這裡,在 worktree 外 ——
# 不進 repo、不靠 .gitignore。`--dry-run` 只印(#96):已經在就原封不動。
round_dir() {   # uses FIX/DRY;設定 FIX_KEPT
    FIX_KEPT=""
    if [ -n "$DRY" ] && [ -e "$FIX" ]; then
        FIX_KEPT=1
        echo "auto-fix: --dry-run —— 輪次目錄 $FIX 已經在,不刪、不重建"
    else
        rm -rf "$FIX"
        mkdir -p "$FIX"
    fi
}

round_once() {   # $1 = 第幾輪(r);設定 ROUND_RC
    r=$1
    FIX=$WTBASE/fix-t$ID/round$r
    # 驗證者副本與只派驗證者那一輪展的那一版:票分支已經有就是它(前幾輪 worker 的 commit 都在上面),
    # 還沒有才退回 base_sha。
    SRC=${AC_FIX_SOURCE:-$(ticket_branch)}
    git -C "$ROOT" rev-parse -q --verify "$SRC^{commit}" >/dev/null || SRC=$S_BASE
    DISPATCH=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID/dispatch-round$r.md
    # 這一輪在同一個 run 裡已經起過 agent(driver 死在半路,`heartbeat --reconcile` 把票轉回 Ready):
    # 那一輪的派工文 / log / result 留在原地給人看,這一次換一個新的 run_id,不覆寫(#117 A4 vii)。
    if [ -z "$DRY" ] && { [ -e "$(dirname "$DISPATCH")/worker-round$r.log" ] \
            || [ -e "$(dirname "$DISPATCH")/verifier-round$r.log" ]; }; then
        r_was=$RUN_ID
        RUN_ID=$(date +%Y%m%d-%H%M%S)-$$
        [ "$RUN_ID" != "$r_was" ] || RUN_ID=$(date +%Y%m%d-%H%M%S)-$$$r
        DISPATCH=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID/dispatch-round$r.md
        echo "auto-fix: 第 $r 輪在 $r_was 已經起過(死在半路的那一次留在原地)—— 這一次的 run_id 換成 $RUN_ID"
    fi
    mkdir -p "$(dirname "$DISPATCH")"
    GAP_ROUND=""
    if [ "$r" -gt 1 ] && [ -n "${S_GAP_ONLY:-}" ]; then
        round_dir
        gap_round
        return $?
    fi

    # 案例還沒交的驗證者(#51;#126 A4 起**任一輪**):第 2 輪起接著跑的票(舊狀態檔)以前只剩
    # test_defect 那一條路派得到驗證者,needs_verifier=true 而 baseline 空的票就一路沒有案例。
    # 同一輪 worker 回 test_defect 時只走那一條路的驗證者(A6,底下 V_HELD / dispatch_verifier)。
    V_WANT=""
    VPID=""
    VPATCH=""
    VEVIDENCE=""
    V_HELD=""
    V_COPY_SHA=""
    V_COPY_HEAD=""
    VWRC=""
    V_QUOTA=""
    V_HANDED=""
    V_SWAPPED=""
    verifier_wanted "$r"
    if [ -n "$V_WANT" ]; then
        [ "$r" -eq 1 ] || echo "auto-fix: #$ID needs_verifier 不是 false(缺 = true)而 verify.baseline 是空的 —— 案例還沒交,第 $r 輪先派驗證者"
        VFIX=$WTBASE/verify-t$ID/round$r
        VDISPATCH=$(dirname "$DISPATCH")/dispatch-verifier-round$r.md
        # 等交件才起的那一種(#60):底下 worker 沒走到交件的每一頁都要說驗證者沒起,
        # 不然「沒起」與「起了、沒交」在 inbox 長得一樣。
        [ -n "$V_PARALLEL" ] \
            || V_HELD="。驗證者沒起:票 interface_fixed 不是 true,要等 worker 第 $r 輪交件才起"
        if ! v_packet; then
            ROUND_RC=6
            return 1
        fi
        echo "auto-fix: 驗證者派工文 -> $(rel_root "$VDISPATCH")"
    fi
    round_dir
    worker_packet || { ROUND_RC=6; return 1; }
    echo "auto-fix: 派工文 -> $(rel_root "$DISPATCH")"

    if [ -n "$DRY" ]; then
        echo "auto-fix: worker 命令 —— $WORKER_CMD"
        [ -z "$T_EFFORT" ] || echo "auto-fix: effort 來源=$EFFORT_SRC $T_EFFORT"
        echo "auto-fix: --dry-run —— 不起 worker 也不起驗證者。派工文在上面那一份。"
        ROUND_RC=0
        return 1
    fi

    if ! ticket_worktree; then
        echo "auto-fix: 票分支 $(ticket_branch) 的 worktree $WT 建不出來 —— 這一輪不派" >&2
        ROUND_RC=2
        return 1
    fi
    # 第 1 輪:票 Ready → Running(#40)。擺在 `--dry-run` 那一手**之後**(dry-run 不動票)、
    # 起 worker **之前**(worker 跑的時候票就該是 Running)。第 2 輪起票平常已經不是 Ready 了;
    # 是的話是 `heartbeat --reconcile` 收過死掉的那一輪(#117 A4 vi),這一輪照樣轉 Running。
    if [ "$r" -eq 1 ] || [ "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("state") or "")' "$TF" 2>/dev/null)" = "Ready" ]; then
        python3 "$AC/ticket.py" set "$ID" state Running >/dev/null 2>&1 \
            || echo "auto-fix: 票狀態改不動(Running)" >&2
    fi
    W_BEFORE=$(git -C "$WT" rev-parse HEAD)
    if [ -n "$IS_AGY" ]; then
        echo "auto-fix: 起第 $r 輪的 worker —— $WORKER_CMD(cwd $WT,stdin=/dev/null,提示指向派工文)"
    else
        echo "auto-fix: 起第 $r 輪的 worker —— $WORKER_CMD(cwd $WT,派工文從 stdin 餵)"
    fi
    [ -z "$T_EFFORT" ] || echo "auto-fix: effort 來源=$EFFORT_SRC $T_EFFORT"
    # 診斷輪(#125 A3)的記號 `round_kind=diagnosis`:下一個程序靠它認出「上一輪已經診斷過」。
    ev ticket.attempt.start --driver $$ --ticket "$ID" --attempt "$r" --kv run_id="$RUN_ID" \
        ${DIAG:+--kv round_kind=diagnosis --kv diagnosis="$DIAG"} --note "auto-fix 第 $r 輪${DIAG:+(診斷)}"
    # 起不來(副本取不出)照樣派 worker,收件時走「驗證者沒交出 patch-verify」那一頁。
    echo "auto-fix: 逾時來源=$TIMEOUT_SRC $WORKER_TIMEOUT 秒" >&2
    [ -z "$V_WANT" ] || [ -z "$V_PARALLEL" ] || start_verifier_bg || true
    WORKER_LOG=$(dirname "$DISPATCH")/worker-round$r.log
    run_worker "$(session_open worker "$WORKER_CMD" "$FIX" "$r" "$WORKER_MODEL" "$WT")" "$DISPATCH"
    # worker 回來了才收驗證者(#51 C2)—— 它比 worker 早回來的話這一手不用等。
    [ -z "$VPID" ] || wait_verifier

    # 越界先收(#115 A7):額度那一手會提早返回,排在前面就把同一輪的越界吞掉。
    if guard_stop "$WORKER_LOG" "${VLOG:-}"; then
        worker_cost error
        return 1
    fi

    # 平行驗證者執行中撞額度(#132 B1):worker 那一手之前判。交給 Controller 的那一種(V_HANDED)
    # 先讓 worker 那一手也判完(它也撞了就一起交出去),這一輪才停。
    if [ -n "$V_PARALLEL" ] && ! verifier_fallback v_packet; then
        if [ "$V_STOP" = handed ]; then
            V_HANDED=1
        else
            verifier_halt
            return 1
        fi
    fi
    [ -z "${V_SWAPPED:-}" ] || verifier_collect

    # 額度 / 不可用(#121 A2/A3,取代 #87 A23 / #114 A10 的「改派 opus、刪副本、只發頁」):
    # 判讀只用 `role-models.py`(D-018;以前這裡 grep 429 會把 patch 裡的字當額度)。可換 →
    # 這一次照記 cost(outcome=error,note 記原因)、副本與交接資料留著,依表取下一個候選、
    # 在同一份副本、同一輪重起;整串用完 → Blocked + 一頁,不再起。一般失敗、逾時、沒交件不換。
    # 有 Controller(`AC_RECOVERY_OWNER=controller`)就只發 `agent.recovery_needed`、不自己起;
    # 同一輪的接續由 owner 鎖保證只有一個驅動者(A3)。
    while [ "$WRC" -ne 0 ] && fallback_wanted worker "$WORKER_LOG" "$WRC"; do
        worker_cost error "$Q_KIND:$Q_DETAIL"
        route_worker --exhausted-index "$W_INDEX" --evidence "$WORKER_LOG" --evidence-rc "$WRC"
        case $R_RC in
            2) attempt_failed route-refused; ROUND_RC=2; return 1 ;;
            3) attempt_failed quota-exhausted; chain_exhausted worker; ROUND_RC=7; return 1 ;;
        esac
        if ! mkdir "$(dirname "$DISPATCH")/recovery-round$r-c$R_INDEX.owner" 2>/dev/null; then
            echo "auto-fix: #$ID 第 $r 輪第 $R_INDEX 格已有別的驅動者接手 —— 這裡不再起" >&2
            ROUND_RC=7
            return 1
        fi
        if [ "${AC_RECOVERY_OWNER:-}" = controller ]; then
            ev agent.recovery_needed --ticket "$ID" --role worker --model "$R_MODEL" --kv run_id="$RUN_ID" \
                --kv round="$r" --kv tool="$R_TOOL" --kv candidate_index="$R_INDEX" \
                ${R_EFFORT:+--kv effort=$R_EFFORT} --kv copy="$FIX" --kv dispatch="$DISPATCH" --note "$Q_KIND"
            echo "auto-fix: #$ID 第 $r 輪 $WORKER_MODEL 撞到 $Q_KIND —— 下一個候選 $R_MODEL 交給 Controller 接續(副本 $FIX 留著)"
            ROUND_RC=7
            return 1
        fi
        echo "auto-fix: #$ID 第 $r 輪 $WORKER_MODEL 撞到 $Q_KIND —— 依表換第 $R_INDEX 格 $R_MODEL,副本 $FIX 留著、同一輪重起"
        use_worker || { attempt_failed route-refused; ROUND_RC=2; return 1; }
        worker_packet || return 1
        WORKER_LOG=$(dirname "$DISPATCH")/worker-round$r-c$W_INDEX.log
        run_worker "$(session_open worker "$WORKER_CMD" "$FIX" "$r" "$WORKER_MODEL" "$WT")" "$DISPATCH"
        if guard_stop "$WORKER_LOG"; then
            worker_cost error
            return 1
        fi
    done
    if [ -n "$V_HANDED" ]; then
        V_STOP=handed
        verifier_halt
        return 1
    fi

    # 反駁判完接回的 worker(#89)交件後從這裡再走一次:同一輪、同一個 worktree。
    while :; do
    if guard_stop "$WORKER_LOG"; then
        worker_cost error
        return 1
    fi
    EVIDENCE=$FIX/EVIDENCE-round$r.md

    # **收件的同一手**把 EVIDENCE 尾端那一塊抽出來。位置在反駁那一段**之前**:
    # 反駁那條路會直接 return,而那一輪一樣要留得下一份可讀的結果。
    RESULT_JSON=$(dirname "$DISPATCH")/result-round$r.json
    harvest_result "$EVIDENCE" "$RESULT_JSON" worker "$r"
    echo "auto-fix: 結構化交付 -> $(basename "$RESULT_JSON")"
    worker_delivery

    # **逾時:worktree 不動、留半成品**(#57)。被砍的 worker 做到哪(commit 與沒 commit 的)都還在
    # worktree 裡 —— 停下來問主線,不當交付:被砍在 commit 到一半的那一份,與做完的那一份長得一樣。
    if [ "$WRC" -eq 124 ]; then
        partial_note="分支相對派工前多了 ${W_NEW:-0} 個 commit${W_DIRTY:+,未 commit 的改動:$W_DIRTY}"
        echo "auto-fix: 第 $r 輪的 worker 逾時(${WORKER_TIMEOUT} 秒)—— worktree $WT 留著;$partial_note" >&2
        block "#$ID 第 $r 輪的 worker 逾時(${WORKER_TIMEOUT} 秒)"
        post "worker 逾時(${WORKER_TIMEOUT} 秒),worktree 留著" \
             "讀 $WORKER_LOG 看它做到哪;${partial_note}。要嘛拆票或加 worker.timeout_seconds 重派,要嘛人從 worktree 接手${V_HELD}" \
             "$WT"
        worker_cost timeout
        attempt_failed timeout
        ROUND_RC=5
        return 1
    fi

    # 有反駁就先留著驗證者副本:反駁要從它接回(#89)。用**與底下那一段同一個判準**問一次,
    # 不是「反正留著」—— 留著的那一份就是沒人收的那一份。沒有反駁 = 這一輪收件,副本收掉。
    if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
        echo "auto-fix: 先留著驗證者副本 —— 反駁要從它接回"
    else
        shed_copies "$FIX" "$WT"
        [ -z "$V_WANT" ] || [ -z "$V_PARALLEL" ] || shed_copies "$VFIX"
    fi

    CASE_FIXED=""
    # 反駁比交件先看:worker 說「這張票寫錯了」而東西照樣落地,那句話等於沒人收。
    if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
        line=$(grep -m1 '^OBJECTION:' "$EVIDENCE")
        echo "auto-fix: worker 提了反駁 —— $line"
        worker_cost objection
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
            # 等交件才起的第 1 輪驗證者(#60):worker 反駁了就不起 —— 案例要照的實作不在。
            if [ -n "$V_HELD" ]; then
                echo "auto-fix: worker 提了反駁$V_HELD —— 這一輪不起"
                V_WANT=""
            fi
            if ! dispatch_verifier; then
                [ -z "${G_STOPPED:-}" ] || return 1
                [ -z "${V_STOP:-}" ] || { verifier_halt; return 1; }
                if [ -n "${RULES_FAILED:-}" ]; then
                    # 驗證者的規則包產不出來:驗證者沒起,這一輪停在 worker 的反駁上。
                    attempt_failed objection
                    ROUND_RC=6
                    return 1
                fi
                attempt_failed no-patch
                ROUND_RC=5
                return 1
            fi
        else
            # 開題者判完接回了 worker:回到收件那一手;其餘(升級、接回的驗證者沒交)停在這裡。
            triage_objection && continue
            return 1
        fi
    fi
    break
    done

    # 沒交件(#144):分支沒有新 commit,或 commit 了又留著未 commit 的改動。test_defect 那條路
    # 驗證者修好了案例(CASE_FIXED)而 worker 沒 commit 的,照只收 patch-verify 那一條走。
    if [ -z "$W_HANDED" ] && [ -z "$CASE_FIXED" ]; then
        echo "auto-fix: 第 $r 輪的 worker 沒交件 —— $W_WHY" >&2
        block "#$ID 第 $r 輪的 worker 沒交件"
        post "worker 沒交件" "${W_WHY}。worktree $WT 照原樣留著(腳本不替它 commit);要嘛重派,要嘛人下場${V_HELD}" "$WT"
        worker_cost no-patch
        attempt_failed no-patch
        ROUND_RC=5
        return 1
    fi

    # 等交件才起的那一種(#60):worker 的 commit 在分支上了,現在起、起了就收。
    if [ -n "$V_WANT" ] && [ -n "$V_HELD" ]; then
        if start_verifier_bg; then wait_verifier; fi
        if guard_stop "${VLOG:-}"; then
            worker_cost handed-in
            return 1
        fi
        if ! verifier_fallback v_packet; then
            verifier_halt
            return 1
        fi
        [ -z "${V_SWAPPED:-}" ] || verifier_collect
        shed_copies "$VFIX"
    fi

    # 驗證者沒交件(#51 C6):worker 的 commit 留在分支上、EVIDENCE **先收進 reports**(下一輪或
    # 人下場還用得到),再停下來問主線 —— 只有 worker 那一份等於沒有案例就量綠。
    # test_defect 的驗證者交了件(CASE_FIXED)就不算沒交:平行那一位沒交也照 #107 的路收。
    if [ -n "$V_WANT" ] && [ -z "$VPATCH" ] && [ -z "$CASE_FIXED" ]; then
        [ ! -f "$EVIDENCE" ] || cp "$EVIDENCE" "$(dirname "$DISPATCH")/"
        echo "auto-fix: 第 $r 輪的驗證者沒交出 patch-verify —— worker 的 commit 留在分支 $(ticket_branch)" >&2
        block "#$ID 第 $r 輪的驗證者沒交出 patch-verify"
        post "驗證者沒交出 patch-verify" \
             "讀驗證者派工文與它的 log;worker 的 commit 在分支 $(ticket_branch) 上,不要讓主線自己寫案例" \
             "${VDISPATCH:-$(dirname "$DISPATCH")}"
        worker_cost handed-in
        attempt_failed verifier-no-patch
        ROUND_RC=5
        return 1
    fi

    round_receive
}

# 只派驗證者的那一輪(#126 A5,D-014):上一輪閘門的紅榜**只有**缺口列(kind=GAP,「宣告了
# verify 卻沒有案例」)。那是驗證者的紅,worker 修不動也不該碰 —— 派 worker 等於付一整輪去修
# 一個沒有壞掉的東西。副本是分支上那一版;baseline 空用一般驗證者派工文、非空走 test_defect
# 那一份(紅榜列出缺口列);交件後照 test_defect 那條路收件、重跑閘門。
# 照樣算一輪(A7):attempt 照發、`ticket.py round` 照記、上限照算,不歸零。
gap_round() {   # uses r/SRC/FIX/DISPATCH;設定 ROUND_RC
    GAP_ROUND=1
    V_WANT=""
    VPID=""
    VPATCH=""
    VEVIDENCE=""
    VKEPT_EVIDENCE=""
    V_HELD=""
    V_COPY_SHA=""
    V_COPY_HEAD=""
    W_COST_LOG=""
    W_HANDED=""
    CASE_FIXED=""
    PATCH_OUT=""
    line=""
    verifier_wanted "$r" >/dev/null 2>&1
    V_WANT=""
    echo "auto-fix: 上一輪的紅榜只有缺口列(kind=GAP)—— 第 $r 輪不派 worker,只派驗證者"
    [ -n "$DRY" ] || ev ticket.attempt.start --driver $$ --ticket "$ID" --attempt "$r" --kv run_id="$RUN_ID" \
        --note "auto-fix 第 $r 輪(只派驗證者)"
    dispatch_verifier
    lr_rc=$?
    if [ "$lr_rc" -eq 2 ]; then
        [ -n "$FIX_KEPT" ] || shed_copies "$FIX"
        ROUND_RC=0
        return 1
    fi
    if [ "$lr_rc" -ne 0 ]; then
        # 越界(G_STOPPED)已由 guard_stop 收;規則包產不出來是 6,其餘是驗證者沒交(5)。
        [ -z "${G_STOPPED:-}" ] || return 1
        [ -z "${V_STOP:-}" ] || { verifier_halt; return 1; }
        attempt_failed verifier-no-patch
        if [ -n "${RULES_FAILED:-}" ]; then ROUND_RC=6; else ROUND_RC=5; fi
        return 1
    fi
    # 這一輪只有它那一份:空的 patch-verify 就是案例沒修,套下去只會得到 apply 的「空的 patch 不是綠」。
    # result 帶了 verify 計畫的不算(#130):round_receive 走只併計畫那條路。
    if [ ! -s "$PATCH_OUT" ] && ! verifier_plan_given "$EVIDENCE"; then
        echo "auto-fix: 第 $r 輪的驗證者交了一份空的 patch-verify —— 缺的案例沒補,不套" >&2
        block "#$ID 第 $r 輪的驗證者交了空的 patch-verify"
        post "驗證者沒交出 patch-verify" \
             "上一輪缺的案例沒補(patch-verify 是空的);讀驗證者派工文與它的 log,不要讓主線自己改案例" \
             "$(dirname "$DISPATCH")"
        attempt_failed verifier-no-patch
        ROUND_RC=5
        return 1
    fi
    RESULT_JSON=$(dirname "$DISPATCH")/result-verifier-round$r.json
    round_receive
}

# 只併驗證者計畫(#130):計畫由 `apply.sh verify-plan` 併進票(與 `--evidence-verifier` 同一段),
# 不套 patch、不 commit;閘門在既有分支的 worktree 上重跑。worktree 不在 = 沒有分支可重跑,停下。
plan_only_receive() {   # uses r/WT/EVIDENCE/VKEPT_EVIDENCE;停下時設定 ROUND_RC
    if [ ! -d "$WT" ]; then
        echo "auto-fix: 只併驗證者計畫,但分支 $(ticket_branch) 的副本 $WT 不在 —— 閘門沒地方重跑" >&2
        block "#$ID 第 $r 輪只併驗證者計畫,分支副本不在"
        post "只併驗證者計畫,分支副本不在" \
             "驗證者這輪沒有改動,計畫沒有併;$WT 不在,閘門沒地方重跑。要嘛重派,要嘛人下場" \
             "$EVIDENCE"
        attempt_failed verifier-no-patch
        ROUND_RC=5
        return 1
    fi
    if ! sh "$AC/apply.sh" verify-plan "$ID" \
            ${VKEPT_EVIDENCE:+--evidence-verifier "$VKEPT_EVIDENCE"} --evidence-verifier "$EVIDENCE"; then
        echo "auto-fix: 第 $r 輪的驗證者計畫併不進票(apply.sh verify-plan 非零)" >&2
        block "#$ID 第 $r 輪的驗證者沒交出 patch-verify"
        post "驗證者沒交出 patch-verify" \
             "patch-verify 是 0 byte,而它的 result 併不進票(見 apply 的輸出);讀驗證者派工文與它的 log,不要讓主線自己改案例" \
             "$EVIDENCE"
        attempt_failed verifier-no-patch
        ROUND_RC=5
        return 1
    fi
}

# 收件 → apply → 閘門:round_once 的後半,只派驗證者的那一輪(gap_round)也走這裡。
round_receive() {   # uses r/CASE_FIXED/PATCH_OUT/EVIDENCE/RESULT_JSON/VPATCH/GAP_ROUND;設定 ROUND_RC
    # worker 的改動已經是分支上的 commit(#144);apply 只收 EVIDENCE / result / 反駁、併驗證者計畫,
    # 驗證者交了 patch-verify 才在分支上補一個 commit。
    # test_defect 先判(#112):平行驗證者交過件時 VPATCH 還指著它在 verify-t<n>/round<r> 的那份,
    # 而那個副本已經是 test_defect 驗證者的 —— 先判 VPATCH 就是把同一份 patch-verify 收兩次。
    # 驗證者的計畫兩條路都併進票:平行那一位保留下來的那份在前,test_defect 那一位的在後。
    # 驗證者的 patch-verify **0 byte** = 這輪它沒改案例(#130,#98 第 2 輪):不交給 apply 套。
    # 驗證者是唯一交件者(gap_round、test_defect 而 worker 沒 commit)而 result 帶 verify →
    # 只併計畫、分支不動。
    VSLOT=""
    PLAN_ONLY=""
    if [ -n "$CASE_FIXED" ]; then
        VSLOT=$PATCH_OUT
        if [ ! -s "$PATCH_OUT" ] && [ -z "$W_HANDED" ] && verifier_plan_given "$EVIDENCE"; then
            PLAN_ONLY=1
            echo "auto-fix: 第 $r 輪的驗證者這輪沒有改動(patch-verify 0 byte)—— 只併驗證者計畫:分支不動,直接重跑閘門"
        fi
    else
        VSLOT=$VPATCH
    fi
    if [ -n "$VSLOT" ] && [ ! -s "$VSLOT" ]; then
        VSLOT=""
        [ -n "$PLAN_ONLY" ] \
            || echo "auto-fix: 第 $r 輪的驗證者這輪沒有改動(patch-verify 0 byte)—— 分支上只有 worker 的 commit,驗證者的計畫照併"
    fi
    if [ -n "$PLAN_ONLY" ]; then
        plan_only_receive || return 1
    elif [ -n "$CASE_FIXED" ]; then
        AC_ROUND=$r AC_PREV_EVIDENCE=$EVIDENCE AC_RESULT_DONE=1 AC_RESULT_JSON=$RESULT_JSON \
            sh "$AC/apply.sh" "$ID" ${VSLOT:+"$VSLOT"} --evidence "$EVIDENCE" \
            ${VKEPT_EVIDENCE:+--evidence-verifier "$VKEPT_EVIDENCE"} --evidence-verifier "$EVIDENCE"
    elif [ -n "$VPATCH" ]; then
        # 三方合併的 ancestor = 驗證者副本展開的那一版(#157;平行 = base_sha,等交件 = 分支頭)。
        AC_ROUND=$r AC_PREV_EVIDENCE=$EVIDENCE AC_RESULT_DONE=1 AC_RESULT_JSON=$RESULT_JSON \
            sh "$AC/apply.sh" "$ID" ${VSLOT:+"$VSLOT"} --evidence "$EVIDENCE" \
            --evidence-verifier "$VEVIDENCE" ${V_COPY_SHA:+--verifier-base "$V_COPY_SHA"}
    else
        AC_ROUND=$r AC_PREV_EVIDENCE=$EVIDENCE AC_RESULT_DONE=1 AC_RESULT_JSON=$RESULT_JSON \
            sh "$AC/apply.sh" "$ID" --evidence "$EVIDENCE"
    fi
    arc=$?
    if [ "$arc" -ne 0 ]; then
        case $arc in
            3) a_what="patch-verify 套不上(apply rc=3)" ;;
            5) a_what="交件命中硬擋(apply rc=5)" ;;
            *) a_what="收件沒過(apply rc=$arc)" ;;
        esac
        echo "auto-fix: 第 $r 輪的$a_what" >&2
        post "第 $r 輪的$a_what" \
             "看 apply 的輸出:patch-verify 套不上分支、worktree 不在,還是 EVIDENCE 的反駁沒處置" \
             "${VSLOT:-$EVIDENCE}"
        worker_cost apply-failed
        attempt_failed apply-failed
        ROUND_RC=5
        return 1
    fi
    worker_cost handed-in
    # 只派驗證者的那一輪沒有 worker 的反駁可以結(#126):別把一筆不相干的 test_defect 標成 fixed。
    if [ -n "$CASE_FIXED" ] && [ -z "${GAP_ROUND:-}" ]; then
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

    gate_command
    echo "auto-fix: 第 $r 輪的閘門 —— (cd $WT && $gate_cmd)"
    ev gate.rerun --ticket "$ID" --attempt "$r" --note "$gate_cmd" \
        --kv run_id="$RUN_ID" --kv round="$r"
    # `AC_ROOT=$ROOT`:閘門在**副本**裡跑,但狀態檔與收件匣要寫回**主 repo**
    # —— 不然這一輪的結果留在一個等一下會被收掉的目錄裡,而讀它的人在主 repo。
    ( cd "$WT" && AC_ROOT=$ROOT AC_WT=$WT AC_ROUND=$r AC_TICKET=$ID \
        AC_VERIFY_PATCH=${VSLOT:-} \
        AC_PREV_EVIDENCE=$EVIDENCE AC_IN_AUTOFIX=1 \
        sh -c "$gate_cmd" )
    grc=$?
    # 閘門跑過了 —— 綠或紅,這一次派工都算收工(紅的下一輪是另一個 attempt)。
    ev ticket.attempt.done --ticket "$ID" --attempt "$r" --kv rc="$grc"
    if [ "$grc" -eq 0 ]; then
        gate_green
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

# 這張票的閘門怎麼跑(round_receive 與環境重跑同一句):`gate.rerun_cmd`,沒設退回預設那一支。
gate_command() {   # uses WT;設定 gate_cmd
    if [ -n "$RERUN_CMD" ]; then
        gate_cmd=$RERUN_CMD
    else
        # 預設跑哪一份閘門與 land.sh 同一條(D-071 C3,`wtbase.sh` 的 `ac_gate_script`):
        # 專案就是 A 自己才跑 worktree 那一份,其他專案跑 AC_HOME 那一份。
        gate_cmd="sh '$(ac_gate_script "$WT")' --branch --ticket $ID"
        echo "auto-fix: 警告:沒有設 gate.rerun_cmd —— 退回 $gate_cmd" >&2
    fi
}

# 第 r 輪的閘門綠了(round_receive,或環境重跑綠了 —— #125 A4):記綠、票轉 InReview、票的 gate 欄、
# complex 票派覆核。設定 ROUND_RC。
gate_green() {   # uses r
    python3 "$AC/ticket.py" round "$ID" "$r" --green || true
    python3 "$AC/ticket.py" set "$ID" state InReview >/dev/null 2>&1 || true
    read_status
    RUN_ID=$S_RUN
    # 閘門只有一個真相(#56,D-H38):這一輪的綠寫進票上**同一個** `gate` 欄,sha 是
    # 分支此刻的頭。以前副本裡的綠只留在狀態檔,land 只認 `gate.rc` 就拒收,主線只剩
    # `gate --redo` —— 它改了分支 sha,綁在舊 sha 的覆核跟著失效。
    GATE_SHA=$(git -C "$ROOT" rev-parse -q --verify "$(ticket_branch)^{commit}" 2>/dev/null || echo "")
    if [ -n "$GATE_SHA" ]; then
        python3 "$AC/ticket.py" set "$ID" gate "$(python3 -c '
import datetime, json, sys
print(json.dumps({"rc": 0, "sha": sys.argv[1], "run_id": sys.argv[2],
              "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}))
' "$GATE_SHA" "$RUN_ID")" >/dev/null 2>&1 \
            || echo "auto-fix: 票的 gate 欄寫不進去 —— land 會拒收,review.sh 會說沒跑過閘門" >&2
    else
        echo "auto-fix: 問不到分支 $(ticket_branch) 的頭 —— 票的 gate 欄不寫" >&2
    fi
    ROUND_RC=0
    if ! needs_review; then
        echo "auto-fix: 第 $r 輪綠了 —— 票不是 complex(沒標 = simple),不派覆核;票停在 InReview 等 land"
        echo "auto-fix: 終態=green_no_review(simple 票:閘門綠就可落地,不做做完後的覆核)"
    elif [ -n "$NO_REVIEW" ]; then
        echo "auto-fix: 第 $r 輪綠了 —— --no-review,覆核不派,票停在 InReview:sh scripts/review.sh $ID"
        echo "auto-fix: 終態=awaiting_review(閘門綠、還沒覆核 —— 不是可落地)"
    else
        echo "auto-fix: 第 $r 輪綠了 —— 覆核交給 review.sh(sh scripts/review.sh $ID)"
        sh "$AC/review.sh" "$ID" --run-id "$RUN_ID"
        review_outcome $?
    fi
}

# 環境可疑(#125 A4,D-076 取代 D-019 理由 5 的「永不派」):同一支閘門**機械重跑**一次 —— 不派
# agent、不算一輪(`repair.env_rerun`,預設 1;0 = 不重跑)。發 `gate.rerun` reason=env_suspect;
# 那一筆就是「這一輪已經重跑過」的紀錄,換程序接手也數得到(read_status 的 S_RERUNS)。
env_rerun() {   # uses S_ROUND / S_PREV_EVIDENCE;重讀狀態檔
    if ! ticket_worktree; then
        echo "auto-fix: 票分支 $(ticket_branch) 的 worktree $WT 建不出來 —— 環境重跑跳過" >&2
        return 1
    fi
    gate_command
    echo "auto-fix: 第 $S_ROUND 輪的閘門環境可疑($S_ENV_ENGINE:$S_ENV_WHY)—— 同一支閘門機械重跑一次(不算一輪):(cd $WT && $gate_cmd)"
    ev gate.rerun --ticket "$ID" --attempt "$S_ROUND" --note "$gate_cmd" \
        --kv run_id="$RUN_ID" --kv round="$S_ROUND" --kv reason=env_suspect
    ( cd "$WT" && AC_ROOT=$ROOT AC_WT=$WT AC_ROUND=$S_ROUND AC_TICKET=$ID \
        AC_PREV_EVIDENCE=${S_PREV_EVIDENCE:-} AC_IN_AUTOFIX=1 \
        sh -c "$gate_cmd" )
    erc=$?
    read_status
    RUN_ID=$S_RUN
    # 重跑的閘門沒寫狀態檔(或寫了別輪)也不准讀成綠:綠只認重跑的 rc 與新狀態檔兩邊都是 0。
    [ "$erc" -eq 0 ] && [ "${S_RC:-1}" = "0" ] && return 0
    [ "$erc" -eq 0 ] && echo "auto-fix: 重跑的閘門 rc 0,但最新狀態檔 rc=${S_RC:-?} —— 照狀態檔走" >&2
    return 2
}

# 紅了之後下一輪派什麼(#125 A3/A4,D-076)。讀 S_*;設 DIAG(空 = 一般修補輪、env / unattributed =
# 診斷輪)。停下來的幾種在這裡收尾、exit(各恰一頁);環境重跑綠了走 gate_green、exit。
#   可歸因的紅                        → 一般修補輪
#   環境可疑,這一輪還沒重跑夠         → 機械重跑(不算一輪),再判一次
#   沒有歸因 / 環境可疑,上一輪不是診斷 → 診斷輪(算一輪)
#   診斷輪之後還是沒有歸因 / 環境可疑   → 一頁、rc 4(不連派第二輪診斷,H3)
red_next() {
    while :; do
        [ -z "${S_MECH:-}" ] || mechanical_stop
        DIAG=""
        if [ -n "${S_ENV_SUSPECT:-}" ]; then
            DIAG=env
        elif [ "$S_FAILS" -eq 0 ]; then
            DIAG=unattributed
        else
            return 0
        fi
        if [ "$DIAG" = env ] && [ -z "$S_DIAG_PREV" ] && [ "${S_RERUNS:-0}" -lt "$ENV_RERUN" ]; then
            if [ -n "$DRY" ]; then
                echo "auto-fix: --dry-run —— 環境可疑:真的起跑時會先機械重跑閘門 $ENV_RERUN 次(不算一輪),仍可疑才派這一份診斷輪"
            else
                env_rerun
                case $? in
                    0) r=$S_ROUND
                       echo "auto-fix: 環境重跑綠了 —— 第 $r 輪照綠的路走,不派 worker"
                       gate_green
                       exit "$ROUND_RC" ;;
                    2) continue ;;
                esac
            fi
        fi
        if [ -n "$S_DIAG_PREV" ]; then
            if [ "$DIAG" = env ]; then
                echo "auto-fix: 第 $S_ROUND 輪是診斷輪,閘門仍環境可疑 —— 環境要人看,不再派" >&2
                block "#$ID 第 $S_ROUND 輪(診斷輪)之後閘門仍環境可疑:環境要人看"
                post "環境要人看(environment: needs a human)" \
                     "第 $S_ROUND 輪診斷輪之後閘門仍環境可疑(rc=${S_RC:-?},$S_ENV_ENGINE:$S_ENV_WHY):機械重跑與一輪診斷都沒救回來,先修機器 / 環境,再 python3 scripts/ticket.py set $ID state Running 並重跑閘門" \
                     "reports/t$ID/$S_RUN/status.json 與它的 logs/"
            else
                echo "auto-fix: 第 $S_ROUND 輪是診斷輪,紅仍沒有歸因 —— 不連派第二輪診斷" >&2
                block "#$ID 第 $S_ROUND 輪診斷輪之後紅仍沒有歸因"
                post "診斷輪之後紅仍沒有歸因(第 $S_ROUND 輪的診斷沒讓紅變成可歸因)" \
                     "diagnosis round $S_ROUND did not make the red attributable:rc=${S_RC:-?} 而 failures 仍是空的;看那一輪的 EVIDENCE 與閘門 log,決定修 harness 還是改票" \
                     "reports/t$ID/$S_RUN/status.json 與它的 logs/"
            fi
            exit 4
        fi
        echo "auto-fix: 第 $S_ROUND 輪紅了但$([ "$DIAG" = env ] && echo "閘門環境可疑" || echo "一條可歸因的紅都沒有")(rc=${S_RC:-?})—— 下一輪是診斷輪(D-076)"
        return 0
    done
}

# --------------------------------------------------------------- 批次修復(#108)
# land 一批 N 張、全套紅 → `AC_BATCH_RUN_ID=<land 那一輪的 run_id> sh scripts/auto-fix.sh --batch
# <整合分支> <id…>`。**不猜哪張票弄紅**(D-H51 ②):一個 worker 拿整批 —— 紅榜(第一張票的
# `reports/t<id>/<AC_BATCH_RUN_ID>/status.json`)、整批 diff、N 張票面 —— 在整合分支的
# worktree 上修、自己 commit(#145,與單票同一種交件:分支相對派工前多了 commit、worktree 乾淨);
# `apply.sh --batch` 量那幾個 commit、補 code-map 重產,`review.sh --batch` 覆核 `<派工前>..<頭>`。
# 只跑一輪:rc 0 之後呼叫端重跑全套,紅了再叫一次就是下一輪。
# **不寫票**:不 set state / attempt / objections —— 任何一張票的 state_version 一動,N 張的覆核同時
# 過期。只有 worker 的成本一列記在第一張票(`ticket.py cost` 不動版本)。紀錄全在 reports/batch/<stamp>/。
# 退出碼同單票那一張表:0 fix 已 commit 且覆核 pass(`--no-review` 是已 commit);3 worker 提反駁;
# 4 紅榜讀不到(空的 = 這一輪派診斷,#125);5 沒交 / 逾時 / 收件被擋;6 規則包產不出來;覆核不是 pass 就是 review.sh 的 rc。
batch_main() {
    [ -n "${AC_BATCH_RUN_ID:-}" ] || {
        echo "auto-fix: --batch 要 env AC_BATCH_RUN_ID(land 那一輪的 run_id;紅榜從 reports/t$ID/<它>/status.json 讀)" >&2
        exit 2
    }
    for b_id in $B_IDS; do
        [ -f "$TDIR/$b_id.json" ] || { echo "auto-fix: 找不到票 #$b_id($TDIR/$b_id.json)" >&2; exit 2; }
    done
    git -C "$ROOT" rev-parse -q --verify "$B_BR^{commit}" >/dev/null \
        || { echo "auto-fix: 整合分支 $B_BR 不存在" >&2; exit 2; }
    B_TAGS=$(for b_id in $B_IDS; do printf '#%s ' "$b_id"; done)
    B_TAGS=${B_TAGS% }
    B_STAMP=${AC_BATCH_STAMP:-$(date +%Y%m%d-%H%M%S)-$$}
    RUN_ID=$AC_BATCH_RUN_ID
    r=1
    BDIR=$ROOT/$(cfg reports_dir reports)/batch/$B_STAMP
    mkdir -p "$BDIR"
    B_STATUS=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID/status.json
    B_FAILS=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1],encoding="utf-8")).get("failures") or []))' "$B_STATUS" 2>/dev/null || echo "")
    if [ -z "$B_FAILS" ]; then
        echo "auto-fix: 批次 $B_TAGS 的紅榜讀不到($B_STATUS)—— 沒有歸因,不派" >&2
        post "批次紅了但沒有歸因($B_TAGS)" \
             "整批 $B_TAGS 在 $B_BR 上的紅榜讀不到:自己看那一輪的 log,不要讓 worker 拿空紅榜去猜" \
             "$(rel_root "$B_STATUS")"
        exit 4
    fi
    # 紅榜是空的(#125 A3/A4,D-076):這一輪派**診斷**(`## 診斷` 取代紅榜),不停。連第二次也沒有
    # 歸因由 land.sh 停(它知道上一輪是不是診斷;這一支一次只跑一輪)。全套那一輪 rc 86 / 狀態
    # env_suspect 的是環境那一種(嫌疑列由 land 抄進同一份狀態檔)。
    B_DIAG=""
    if [ "$B_FAILS" -eq 0 ]; then
        B_DIAG=$(python3 - "$B_STATUS" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
phases = [row for row in data.get("phases") or [] if row.get("phase") == "gate"]
env = data.get("state") == "env_suspect" or data.get("rc") == 86 \
    or (phases and phases[-1].get("rc") == 86)
print("env" if env else "unattributed")
PY
)
        echo "auto-fix: 批次 $B_TAGS 的紅榜是空的($B_DIAG)—— 這一輪派診斷,不照紅榜修"
    fi
    # 輪次目錄(EVIDENCE、拋棄式控制根、worker.pid)在 worktree 外;worker 的 cwd 是整合分支的
    # worktree —— land 開的那一個就用它(同一條分支 git 只准一個 worktree),沒有才開。
    FIX=$WTBASE/fix-batch-$B_STAMP/round$r
    rm -rf "$FIX"
    mkdir -p "$FIX"
    WT=$(git -C "$ROOT" worktree list --porcelain | awk -v ref="branch refs/heads/$B_BR" '
        /^worktree / { here = substr($0, 10) } $0 == ref { print here; exit }')
    if [ -z "$WT" ]; then
        WT=$WTBASE/batch-$(echo "$B_BR" | tr '/' '-')
        mkdir -p "$WTBASE"
        git -C "$ROOT" worktree add -q "$WT" "$B_BR" || { echo "auto-fix: 整合分支 $B_BR 的 worktree 開不出來" >&2; exit 2; }
    fi
    W_BEFORE=$(git -C "$ROOT" rev-parse "$B_BR")
    B_DIFF=$BDIR/batch.diff
    git -C "$ROOT" diff "$MAIN...$B_BR" > "$B_DIFF"
    DISPATCH=$BDIR/dispatch-round$r.md
    batch_packet() {   # 規則包照 MODEL 挑模型卡;換候選(#132 B3)時重組一次
        rules_pack "$DISPATCH" worker "$MODEL" || return 1
        B_DIAG_FILE=""
        if [ -n "$B_DIAG" ]; then
            B_DIAG_FILE=$BDIR/diagnosis-round$r.md
            diagnosis_section "$B_STATUS" "$WT/gate.log" \
                "(cd $WT && sh '$(ac_gate_script "$WT")' --full --no-auto-fix)" "$B_DIAG" "$r" \
                "$B_DIAG_FILE"
        fi
        {
            printf '%s\n' "$RULES_TEXT"
            python3 - "$ROOT" "$TDIR" "$B_STATUS" "$B_BR" "$FIX" "$B_DIFF" \
                "$(git -C "$ROOT" diff --stat "$MAIN...$B_BR")" "$MAIN" "$WT" "$B_DIAG_FILE" $B_IDS <<'PY'
import json, os, sys
root, tdir, status_file, branch, fix, diff_file, stat, main, wt, diag_file = sys.argv[1:11]
ids = sys.argv[11:]
tags = " ".join("#%s" % ident for ident in ids)
rows = []
for ident in ids:
    with open(os.path.join(tdir, "%s.json" % ident), encoding="utf-8") as handle:
        rows.append(json.load(handle))
with open(status_file, encoding="utf-8") as handle:
    failures = json.load(handle).get("failures") or []
print("")
print("# 這一輪:批次%s %s 第 1 輪(整合分支 `%s`)" % ("診斷" if diag_file else "修復", tags, branch))
print("")
print("你是 **role=worker** 的實作者,**短命**:交付那一回合結束,不會再被叫醒。")
print("這 %d 張票各自綠過、覆核過,合在 `%s` 上跑全套卻紅了。**不要猜是哪一張弄紅的** —— "
      "照紅榜修整合分支;修法是你在整合分支上的 commit,各票分支與票檔都不動。" % (len(ids), branch))
print("")
print("## 票面(%d 張快照;`tickets/<id>.json` 是現況)" % len(ids))
for row in rows:
    print("")
    print("### #%s %s" % (row.get("id"), row.get("subject") or ""))
    print("- objective:%s" % (row.get("objective") or "(空)"))
    print("- acceptance:")
    for item in row.get("acceptance") or []:
        print("  - %s" % item)
print("")
if diag_file:
    with open(diag_file, encoding="utf-8") as handle:
        sys.stdout.write(handle.read().lstrip("\n"))
else:
    print("## 紅榜(逐條;來源 `%s`)" % os.path.relpath(status_file, root))
for row in [] if diag_file else failures:
    print("")
    print("### %s %s" % (row.get("kind") or "FAIL", row.get("case") or "?"))
    print("- file:%s:%s" % (row.get("file") or "?", row.get("line") or "?"))
    print("```")
    print(row.get("excerpt") or "(沒有 excerpt)")
    print("```")
print("")
print("## 整批 diff(`git diff --stat %s...%s`)" % (main, branch))
print("```")
print(stat.rstrip("\n") or "(空的)")
print("```")
print("整批 diff 檔:`%s`" % diff_file)
print("")
print("## 規則")
print("**不得放寬測試**:測試檔裡不刪含 assert 的行、不加 skip / expectedFailure —— `apply.sh --batch` "
      "量你的 commit,會硬擋(rc 5)。案例本身錯就在 EVIDENCE 寫一行 `OBJECTION: test_defect <案例>`(例 "
      "`tests.test_x.T.test_one`),不要自己改案例。")
print("")
print("## 你的 worktree")
print("- `%s` —— 整合分支 `%s` 的 worktree,派工方已建好,cwd 就是它" % (wt, branch))
print("- 只在這條分支 commit、不 merge / rebase;其餘 git 規矩見 `docs/DISPATCH-TEMPLATE.md` §1(規則包已節錄)")
print("")
print("## 你要交的兩樣")
print("1. 整合分支 `%s` 上的 commit,訊息以這次修到的票號 `t<n>` 開頭(例 `t%s fix: …`);"
      "交件時 worktree 要乾淨(沒 commit 的改動不算交件)。" % (branch, ids[0]))
print("2. `%s/EVIDENCE-round1.md`(worktree 外,不進 repo)—— 段名照 `docs/DISPATCH-TEMPLATE.md` §8 + 記憶段 + 檔尾一塊 ```result` JSON。"
      % fix)
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。"
      "看到這一行,這一輪停下、整批交主線(批次不寫票)。")
print("")
print("## 只准跑的測試")
print("紅榜上那幾條(照 case 名單跑;診斷輪沒有紅榜:照 `## 診斷` 的指令重現);"
      "不跑全套 —— 全套是呼叫端在 fix commit 之後重跑的事。")
PY
        } > "$DISPATCH"
    }
    batch_packet || exit 6
    echo "auto-fix: 批次 $B_TAGS 派工文 -> $(rel_root "$DISPATCH")"
    if [ -n "$DRY" ]; then
        echo "auto-fix: worker 命令 —— $WORKER_CMD"
        [ -z "$T_EFFORT" ] || echo "auto-fix: effort 來源=$EFFORT_SRC $T_EFFORT"
        echo "auto-fix: --dry-run —— 不起 worker。派工文在上面那一份。"
        exit 0
    fi

    batch_cost() {   # $1 = 結局  $2 = 原因(可空,換候選那一次記 quota 種類)
        python3 "$AC/ticket.py" cost "$ID" --role worker --round "$r" --model "$WORKER_MODEL" \
            --by auto-fix.sh --from-envelope "$WORKER_LOG" --wall-seconds "$W_COST_SECS" \
            --outcome "$1" --run-id "$RUN_ID" --note "batch $B_STAMP $B_TAGS${2:+;$2}" \
            --effort "$T_EFFORT" >/dev/null \
            || echo "auto-fix: #$ID 的 cost 寫不進票(批次 worker)—— 不擋流程" >&2
    }
    keep() {   # 輪次目錄的 EVIDENCE 收進紀錄目錄(不論交得怎樣都留)
        [ ! -f "$FIX/EVIDENCE-round$r.md" ] || cp "$FIX/EVIDENCE-round$r.md" "$BDIR/EVIDENCE-round$r.md"
    }
    batch_run() {   # 起一次批次 worker(WORKER_CMD / WORKER_LOG);設定 WRC / W_COST_SECS,交付收進紀錄目錄
        ev agent.start --driver $$ --ticket "$ID" --role worker --model "$WORKER_MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix --kv batch="$B_TAGS" \
            ${T_EFFORT:+--kv effort=$T_EFFORT} ${B_DIAG:+--kv round_kind=diagnosis}
        WSTART=$(date +%s)
        WRC=$(run_agent "$WORKER_CMD" "$DISPATCH" "$FIX" worker "$r" "$WORKER_LOG" 1 "" "$WT")
        W_COST_SECS=$(( $(date +%s) - WSTART ))
        if [ "$WRC" -eq 0 ]; then
            ev agent.done --ticket "$ID" --role worker --model "$WORKER_MODEL" \
                --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix --kv batch="$B_TAGS" \
                ${T_EFFORT:+--kv effort=$T_EFFORT}
        else
            ev agent.failed --ticket "$ID" --role worker --model "$WORKER_MODEL" \
                --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix --kv batch="$B_TAGS" \
                ${T_EFFORT:+--kv effort=$T_EFFORT}
        fi
        keep
        harvest_result "$EVIDENCE" "$BDIR/result-round$r.json" worker "$r"
    }
    echo "auto-fix: 起批次 $B_TAGS 的 worker —— $WORKER_CMD(cwd $WT,派工文從 stdin 餵)"
    echo "auto-fix: 逾時來源=$TIMEOUT_SRC $WORKER_TIMEOUT 秒" >&2
    WORKER_LOG=$BDIR/worker-round$r.log
    EVIDENCE=$BDIR/EVIDENCE-round$r.md
    batch_run
    if guard_stop "$WORKER_LOG"; then
        batch_cost error
        exit 8
    fi
    # 額度 / 不可用(#132 B3,照 round_once 的 #121 A2/A3 那一手):這一次照記 cost,同一份副本換下一個
    # 候選重起;有 Controller 只發 `agent.recovery_needed`;整串用完 → 一頁、rc 7。cost 與事件只掛
    # 第一張票(批次一向如此),其他批次票不動。
    while [ "$WRC" -ne 0 ] && fallback_wanted worker "$WORKER_LOG" "$WRC"; do
        batch_cost error "$Q_KIND:$Q_DETAIL"
        route_worker --exhausted-index "$W_INDEX" --evidence "$WORKER_LOG" --evidence-rc "$WRC"
        case $R_RC in
            2) exit 2 ;;
            3) chain_exhausted worker; exit 7 ;;
        esac
        if ! mkdir "$BDIR/recovery-round$r-c$R_INDEX.owner" 2>/dev/null; then
            echo "auto-fix: 批次 $B_TAGS 第 $R_INDEX 格已有別的驅動者接手 —— 這裡不再起" >&2
            exit 7
        fi
        if [ "${AC_RECOVERY_OWNER:-}" = controller ]; then
            ev agent.recovery_needed --ticket "$ID" --role worker --model "$R_MODEL" --kv run_id="$RUN_ID" \
                --kv round="$r" --kv tool="$R_TOOL" --kv candidate_index="$R_INDEX" \
                ${R_EFFORT:+--kv effort=$R_EFFORT} --kv copy="$FIX" --kv dispatch="$DISPATCH" \
                --kv batch="$B_TAGS" --note "$Q_KIND"
            echo "auto-fix: 批次 $B_TAGS 的 $WORKER_MODEL 撞到 $Q_KIND —— 下一個候選 $R_MODEL 交給 Controller 接續(副本 $FIX 留著)"
            exit 7
        fi
        echo "auto-fix: 批次 $B_TAGS 的 $WORKER_MODEL 撞到 $Q_KIND —— 依表換第 $R_INDEX 格 $R_MODEL,副本 $FIX 留著、重起"
        use_worker || exit 2
        batch_packet || exit 6
        WORKER_LOG=$BDIR/worker-round$r-c$W_INDEX.log
        batch_run
        if guard_stop "$WORKER_LOG"; then
            batch_cost error
            exit 8
        fi
    done

    worker_delivery "$B_BR"
    if [ "$WRC" -eq 124 ]; then
        partial_note="整合分支相對派工前多了 ${W_NEW:-0} 個 commit${W_DIRTY:+,未 commit 的改動:$W_DIRTY}"
        echo "auto-fix: 批次 $B_TAGS 的 worker 逾時(${WORKER_TIMEOUT} 秒)—— worktree $WT 留著;$partial_note" >&2
        post "批次 worker 逾時($B_TAGS)" \
             "整批 $B_TAGS 在 $B_BR 上的修復 worker 逾時(${WORKER_TIMEOUT} 秒);${partial_note}。重派或人從 worktree $WT 接手" \
             "$(rel_root "$BDIR")"
        batch_cost timeout
        exit 5
    fi
    shed_copies "$FIX" "$WT"
    if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
        line=$(grep -m1 '^OBJECTION:' "$EVIDENCE")
        echo "auto-fix: 批次 worker 提了反駁 —— $line" >&2
        post "批次 worker 提了反駁($B_TAGS)" \
             "整批 ${B_TAGS}:${line}。批次不寫票,這一筆沒有記進任何一張票的 objections[] —— 開題者 / 主線判;判 test_defect 成立、commit 照收就跑 sh scripts/apply.sh --batch $B_BR $B_IDS --base $W_BEFORE --evidence $(rel_root "$EVIDENCE")" \
             "$(rel_root "$EVIDENCE")"
        batch_cost objection
        exit 3
    fi
    if [ -z "$W_HANDED" ]; then
        echo "auto-fix: 批次 $B_TAGS 的 worker 沒交件 —— $W_WHY" >&2
        post "批次 worker 沒交件($B_TAGS)" \
             "整批 $B_TAGS:${W_WHY}(worker rc=$WRC);看 $(rel_root "$WORKER_LOG"),worktree $WT 照原樣留著(腳本不替它 commit),重派或人下場" \
             "$(rel_root "$BDIR")"
        batch_cost no-patch
        exit 5
    fi
    AC_ROUND=$r AC_BATCH_STAMP=$B_STAMP sh "$AC/apply.sh" --batch "$B_BR" $B_IDS --base "$W_BEFORE" \
        --evidence "$EVIDENCE"
    arc=$?
    if [ "$arc" -ne 0 ]; then
        echo "auto-fix: 批次 $B_TAGS 的收件被擋(apply rc=$arc)" >&2
        post "批次收件被擋($B_TAGS,apply rc=$arc)" \
             "整批 $B_TAGS:看 apply 的輸出 —— 命中 hard_block、放寬測試,還是整合分支的工作樹髒" \
             "$(rel_root "$BDIR")"
        batch_cost apply-failed
        exit 5
    fi
    batch_cost handed-in
    if [ -n "$NO_REVIEW" ]; then
        echo "auto-fix: 批次 $B_TAGS 的 fix 已 commit —— --no-review,覆核不派:sh scripts/review.sh --batch $B_BR $B_IDS --run-id $B_STAMP --base $W_BEFORE"
        exit 0
    fi
    sh "$AC/review.sh" --batch "$B_BR" $B_IDS --run-id "$B_STAMP" --base "$W_BEFORE"
    rrc=$?
    [ "$rrc" -eq 0 ] || echo "auto-fix: 批次覆核沒過(review rc=$rrc)—— 看 reports/inbox/ 那一頁" >&2
    exit "$rrc"
}
[ -z "$BATCH" ] || batch_main

# 迴圈從 `S_ROUND+1` 起,碰不到前面那幾輪的輪次目錄 —— 主線用 Agent 手建的第 1 輪就在這裡
# (#38 A4)。派下一輪之前一併收(控制根;EVIDENCE 留著)。`--dry-run` 不刪東西。
# 那一輪的 worker 還活著(`worker.pid` 的 pid 在 /proc,liveness.py —— 不發訊號,#117)就不收:
# 收了等於從它腳下抽走副本(#46)。
if [ -z "$DRY" ]; then
    p=1
    while [ "$p" -le "$S_ROUND" ]; do
        _pid=$(cat "$WTBASE/fix-t$ID/round$p/worker.pid" 2>/dev/null || echo "")
        if [ -n "$_pid" ] && ! python3 "$AC/liveness.py" gone "$_pid"; then
            echo "auto-fix: 第 $p 輪的 worker(pid $_pid)還在跑,不收副本"
            p=$((p + 1))
            continue
        fi
        shed_copies "$WTBASE/fix-t$ID/round$p" "$WT"
        p=$((p + 1))
    done
fi

# 上一輪(狀態檔那一輪)紅了:這一次要派的是一般修補輪還是診斷輪,環境可疑先機械重跑(#125)。
[ -z "$PRE_RED" ] || red_next

r=$((S_ROUND + 1))
while :; do
    ROUND_RC=0
    if round_once "$r"; then
        # 還紅,而且沒有停下來的理由 —— 看還有沒有下一輪。硬擋命中最先(A6),再看輪數(A2:
        # 診斷輪也算一輪),最後由 red_next 決定下一輪的種類(A3/A4)。
        [ -z "${S_MECH:-}" ] || mechanical_stop
        [ "$r" -lt "$S_LIMIT" ] || rounds_exhausted "$r" recorded
        red_next
        r=$((r + 1))
        T_LAST_WORKER=$(last_worker)
        route_round "$r"
        continue
    fi
    exit "$ROUND_RC"
done
