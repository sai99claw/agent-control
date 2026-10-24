#!/bin/sh
# 落地:把幾條綠的分支**依序**串起來、跑一次全套、綠了才 fast-forward 主線。
# 六步照 `docs/WORKFLOW.md` §落地,每一步發事件。
#
#   sh scripts/land.sh t7-land-refuses t9-event-kinds ...   # 依給的順序合
#   sh scripts/land.sh t7-x                                 # 全套紅了預設派下一輪 worker
#   sh scripts/land.sh t7-x --no-auto-fix                   # 明說要人下場
#   sh scripts/land.sh docs "<訊息>" tickets/7.json docs/X.md memory/role/y.inbox.md
#                                                           # 票檔 / 文件 / 記憶進主線
#
# ## docs 通道(2026-09-23,#29 A10;G10 / G17)
# 票檔、`memory/`、`docs/DECISIONS.md` 以前進主線**只有裸 commit 一條路**,而
# `memory/role/main.md` 明禁裸 commit 主線 —— 一條每天都在走、卻沒有任何守衛的路,
# 與沒有規矩長得一樣(G17:同一天兩筆)。所以給它一個入口,而且:
#   * **只收 `tickets/` `docs/` `memory/` 三個前綴** —— 產品碼走一票一分支,越界 rc=2 指名檔;
#   * **與票的落地共用同一把 `.land.lock`** —— land 跑到一半有人往主線塞 commit,
#     那條 `ff-only` 就進不去了,而失敗訊息說的是另一件事。
# 它**不跑閘門**:這三個前綴不進產品碼,跑九分鐘全套換來的是同一份綠。
#
# 這一支**沒有判斷**(docs/ROLES.md:落地器是程式;順序是主線決定的)。它只會拒絕:
# 0 commit、票對不上、`base_sha` 過期、命中 `hard_block`、閘門紅。要它放寬的時候,
# 放寬的是規矩,不是這支腳本。
#
# 每一輪都寫 `reports/t<票號>/<run_id>/status.json`(D-010、D-014):跑完了沒、rc、
# 紅了哪幾條、去哪看。讀那一份就夠了 —— 不必把整份 log 讀進上下文,也不必輪詢背景工作。
# **一輪一個目錄、不覆寫**,而且 gate / merge / push **分開記**:舊版在 merge 與 push
# 之前就寫 `done, rc=0`,所以「合進去了」與「只是閘門綠」長得一樣(2026-09-21 外部審查)。
#
# ## 互斥鎖(2026-09-21)
# `docs/WORKFLOW.md` 早就寫「同時只准一個 land」,而腳本從來沒有擋 —— 兩個 land 會各自
# 開 worktree、各跑一次九分鐘的全套,最後靠 `ff-only` 擋下**後果的一部分**。所以這裡先
# `mkdir` 一把鎖(mkdir 是原子的;`[ -e ]` 之後再建不是),拿不到就指名現在是誰在落地。
#
# ## 覆核與反駁是硬閘門(2026-09-21)
# 只有 `complexity=complex` 的票要一格 `review.verdict` 是通過的 review(#136,VISION 流程 #9:
# 只有複雜的票做完後檢查一次;沒標 = simple,不要 review),否則拒絕;verdict 不綁票版本、
# 分支頭或內容。票的 `objections[]` 裡還有沒處置的阻擋項,也拒絕 ——
# 實作者說「這張票寫錯了」而東西照樣落地,那句話等於沒有人收。判準只有 `ticket.py` 那一份:
# `review_problems` / `objection_problems`,close 問的也是它們。
#
# 做法:從主線開一個 land-<時間> worktree,逐條 --no-ff merge(衝突就停、留著
# worktree 給人看),在那個 worktree 跑 `scripts/gate.sh --full`;綠才
# `git merge --ff-only` 進主線並 push,worktree 收掉。主線的工作樹全程沒有人動、
# 沒有人在上面跑測試,所以其他票的分支閘門可以同時進行。
#
# ## 終態叫醒主線 + auto-fix(2026-09-21,D-015)
# 每一條要人動手的退出路徑(拒收 / 串接衝突 / 全套紅 / 沒合進主線 / push 沒成功 / 關不掉)
# 除了狀態檔,還寫一則 `reports/inbox/<票號>-<run_id>.md`(kind=decision,D-032):哪張票、
# 什麼狀態、要主線做什麼、去哪看。關票成功寫一頁 done 簡報。主線因此不必輪詢。
# 全套紅時預設派下一輪 worker(`--no-auto-fix` 才關):一張票就是那張票的 auto-fix。
#
# ## 一批 N≥2 張全套紅(#109,D-H51 ②)
# **不找是誰弄紅的**:同一把鎖下叫 `auto-fix.sh --batch land/<stamp> <id…>`(一個 worker 拿整批),
# 那個 worker 就在這一個 land worktree(整合分支)上自己 commit(#145,D-054 Q9),
# rc 0 就在同一個 land worktree 重跑全套;最多 `batch_fix.rounds`(預設 2)輪。鎖整段不放,所以
# ff-only 不會因為主線中途動了而白跑;代價是 docs 通道最多等 rounds × (全套 + worker + 覆核)。
# 修滿仍紅才找嫌疑票,規則純機械:最後一輪紅榜 `failures[].file` 對各票的
# `verify.files`、`git diff --name-only main...<分支>`。**恰有部分票命中** → 那幾張踢出
# (各一頁 decision,票不改 state —— 改票會讓覆核過期)、其餘自動重 land 一次(`AC_LAND_DEPTH`,
# 重 land 那一層不再踢);對不到或全中 → 停、worktree 留、一頁 decision 掛第一張票。
# 全套每一輪的 status.json 寫給這一批每張票(`AC_GATE_TICKETS`),`auto-fix.sh --batch` 讀第一張那份。
#
# ## push 之後試關票(2026-09-26,#43;D-025 ①)
# push 成功後對這一批每張票試一次 `ticket.py close <票號> --landed <合併後主線的 sha>`。
# **Done 的條件一條不放**:關得掉印「已合併、已關票」;關不掉照舊印「已合併、尚未關票」
# 並寫一頁收件匣,理由是 `ticket.py close` 印出來的**原文**(這一支不自己重寫一份理由,
# 也沒有任何放行旗標)。關不掉不改退出碼 —— 東西已經在主線上,落地這件事是成功的。
# 關得掉就 `inbox.py done <票號> --landed <sha> --by land.sh` 發整票完成簡報(D-032:subject、
# 落地 sha、做了什麼、覆核結論、cost 表),不自動 ack —— 它就是給主線讀的那一頁。
# 這一支不 ack 任何一頁:D-032 之後腳本接著會做的終態只寫事件,沒有頁可收。
set -u
[ $# -ge 1 ] || { echo "land: 給我至少一條分支"; exit 2; }
# 專案根不從 `$0` 來(D-071 C2,#159):`AC_ROOT`,否則 cwd 的主 repo 根,否則 rc 2 —— 規則只有一份,
# 在 `wtbase.sh`。同伴腳本從這一支自己住的目錄(A 的 clone)叫,不寫死 $ROOT/scripts。
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_CONTROL_DIR=$AC
. "$AC/wtbase.sh"
ROOT=$(ac_root land) || exit 2
export AC_ROOT=$ROOT

cfg() {
    python3 - "$ROOT" "$1" "$2" <<'PY'
import json, os, sys
root, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
print(data.get(key) or default)
PY
}
MAIN=$(cfg main_branch main)
TICKETS=$(cfg tickets_dir tickets)

LOCK=${AC_LAND_LOCK:-$ROOT/.land.lock}
HELD=""
# 重 land 的深度(#109):批次踢人之後自動重 land 其餘那一次是 1;那一層不再踢、不再重 land。
DEPTH=${AC_LAND_DEPTH:-0}
release() { [ -n "$HELD" ] && rm -rf "$LOCK"; }
trap 'release' EXIT INT TERM
take_lock() {   # $1 = 這一次在做什麼(寫進 holder,給撞上的人看)
    # 重 land 是持鎖的那一個 land 叫起來的子行程:沿用它的鎖(它結束時才放),不另搶。
    if [ "$DEPTH" -gt 0 ] && [ -f "$LOCK/holder" ] && grep -q "^pid=$PPID " "$LOCK/holder"; then
        echo "land: 重 land(AC_LAND_DEPTH=$DEPTH)—— 沿用上一層 land(pid=$PPID)的鎖"
        return 0
    fi
    if ! mkdir "$LOCK" 2>/dev/null; then
        echo "land: 已經有一個 land 在跑 —— 同時只准一個(docs/WORKFLOW.md)"
        [ -f "$LOCK/holder" ] && sed 's/^/land:   /' "$LOCK/holder"
        echo "land: 確定那一個已經死了(heartbeat.sh 會說),就 rm -rf $LOCK"
        exit 2
    fi
    HELD=1
    printf 'pid=%s 開始=%s 分支=%s\n' "$$" "$(date +%Y-%m-%dT%H:%M:%S)" "$1" > "$LOCK/holder"
}

ev() {
    python3 "$AC/event.py" emit "$@" >/dev/null \
        || echo "land: 事件發不出去($*)" >&2
}

# ------------------------------------------------------------------ docs 通道
DOCS_PREFIXES="tickets/ docs/ memory/"

cmd_docs() {   # $1 = commit 訊息  $2… = 檔案(repo 相對路徑)
    if [ $# -lt 2 ]; then
        echo "land: docs <訊息> <檔…>  —— 只收 $DOCS_PREFIXES 三個前綴" >&2
        echo "land:   例:sh scripts/land.sh docs \"tickets: #29 v2\" tickets/29.json" >&2
        exit 2
    fi
    msg=$1
    shift
    bad=""
    for f in "$@"; do
        case "/$f/" in
            */../*) bad="$bad $f(路徑裡有..)" ; continue ;;
        esac
        case "$f" in
            /*) bad="$bad $f(絕對路徑)" ; continue ;;
        esac
        ok=""
        for prefix in $DOCS_PREFIXES; do
            case "$f" in "$prefix"*) ok=1 ;; esac
        done
        [ -n "$ok" ] || bad="$bad $f"
    done
    if [ -n "$bad" ]; then
        echo "land: docs 通道只收 $DOCS_PREFIXES —— 這幾個不在裡面:" >&2
        for one in $bad; do echo "land:   $one" >&2; done
        echo "land:   產品碼走一票一分支:worker 在票分支 commit(sh scripts/auto-fix.sh <票號>),再 sh scripts/land.sh t<票號>" >&2
        exit 2
    fi
    # 開題者(AC_ROLE=opener)只收 `tickets/<AC_TICKET>.json` 一個檔(#91 C6,R4):它的交付
    # 就是那張票面;別張票檔、docs/、memory/ 是主線的,文字上的禁令擋不住一次手滑。
    if [ "${AC_ROLE:-}" = opener ]; then
        own=""
        [ -n "${AC_TICKET:-}" ] && own="tickets/$AC_TICKET.json"
        bad=""
        for f in "$@"; do
            [ -n "$own" ] && [ "$f" = "$own" ] || bad="$bad $f"
        done
        if [ -n "$bad" ]; then
            echo "land: 開題者只收自己那張票檔(${own:-AC_TICKET 沒設,一個都不收})—— 這幾個不收:" >&2
            for one in $bad; do echo "land:   $one" >&2; done
            exit 2
        fi
    fi
    here=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$here" != "$MAIN" ]; then
        echo "land: docs 通道要在 $MAIN 上跑,現在在 $here —— 進錯分支的 commit 沒有人看得出來" >&2
        exit 2
    fi
    take_lock "docs: $msg"
    # 一次呼叫一個 stamp、每一筆 land.* 都帶 `channel=docs`(#127 A1(b)):越界守衛靠 stamp 把
    # land.start 與 land.pass 配成一次落地,靠 pass 的 main_before 把它接在上一段的終點後面。
    DSTAMP=docs-$(date +%Y%m%d-%H%M%S)-$$
    ev land.start --note "docs $msg" --kv "stamp=$DSTAMP" --kv channel=docs
    git -C "$ROOT" add -- "$@" || {
        echo "land: git add 收不下這幾個檔(打錯路徑?)" >&2
        ev land.refused --note "docs add 失敗" --kv "stamp=$DSTAMP" --kv channel=docs
        exit 2
    }
    if git -C "$ROOT" diff --cached --quiet -- "$@"; then
        # **已經在主線上就是做完了**,所以 rc=0:這一手要的是「這幾個檔在 main 上」,
        # 而它們已經在了。第一版回 rc=3(「空的 commit 不是落地」),而那讓一個**幂等**
        # 的動作變成失敗 —— 主線重跑一次(接連落地很常重跑)就會收到一個假的紅。
        # §5.5 要的是「沒有東西可做」與「做完了」**看得出差別**,而差別寫在這兩行裡:
        # 它說的是「已經在主線上」,不是「已 commit」,而且不會多出一個 commit。
        echo "land: docs —— 這幾個檔已經在 $MAIN 上了,沒有新的 commit(沒有東西要落地)"
        echo "land:   (與「剛剛落地了」的差別在這一行,不在退出碼:上面沒有 -> $MAIN 那一句。)"
        ev land.pass --note "docs 沒有改動(已在主線上)" --kv "stamp=$DSTAMP" --kv channel=docs
        exit 0
    fi
    docs_before=$(git -C "$ROOT" rev-parse HEAD)
    git -C "$ROOT" commit -q -m "$msg" -- "$@" || {
        echo "land: git commit 失敗" >&2
        ev land.fail --note "docs commit 失敗" --kv "stamp=$DSTAMP" --kv channel=docs
        exit 2
    }
    sha=$(git -C "$ROOT" rev-parse --short HEAD)
    echo "land: docs -> $MAIN $sha"
    git -C "$ROOT" show --stat --oneline -s HEAD | sed 's/^/land:   /'
    if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
        git -C "$ROOT" push -q origin "$MAIN" || {
            echo "land: 已 commit,但 push 沒成功 —— 自己推一次:git push origin $MAIN" >&2
            # commit 已在本機 main(#128 A4):stage=push + main_before,越界守衛把它當一段落地。
            ev land.fail --note "docs push 沒成功" --kv "sha=$sha" --kv "stamp=$DSTAMP" \
                --kv channel=docs --kv stage=push --kv "main_before=$docs_before"
            exit 1
        }
    fi
    ev land.pass --note "docs $msg" --kv "sha=$sha" --kv "stamp=$DSTAMP" --kv channel=docs \
        --kv "main_before=$docs_before"
    exit 0
}

if [ "$1" = "docs" ]; then
    shift
    cmd_docs "$@"
fi

# 旗標先挑掉,剩下的才是分支。**分支名不准有空白**(`t<票號>-…` 的形狀),所以這裡
# 用字串重組位置參數是安全的。
AUTOFIX=1
REBUILT=""
for a in "$@"; do
    case "$a" in
        --auto-fix) AUTOFIX=1 ;;
        --no-auto-fix) AUTOFIX=0 ;;
        --*) echo "land: 不認得 $a(--auto-fix / --no-auto-fix)"; exit 2 ;;
        *) REBUILT="$REBUILT $a" ;;
    esac
done
# shellcheck disable=SC2086
set -- $REBUILT
[ $# -ge 1 ] || { echo "land: 給我至少一條分支"; exit 2; }

take_lock "$*"

STAMP=$(date +%Y%m%d-%H%M%S)
# 重 land 常在同一秒內開跑:worktree 與 land/<stamp> 分支不能撞上一層留著的那一個。
[ "$DEPTH" -eq 0 ] || STAMP=$STAMP-d$DEPTH
IDS=""
ALL_BRANCHES="$*"
LAND_T0=$(date +%s)
ev land.start --note "$*" --kv "stamp=$STAMP"

# 狀態檔:這一批每一張票各一份 `reports/t<票號>-status.json`(D-010,格式見
# `scripts/status.py`)。**寫不出來要出聲但不擋落地** —— 同上面那一段事件的理由。
# 為什麼一張票一份而不是一批一份:讀它的人手上有的是票號,不是這一批的時間戳。
LAND_RUN=""
status_start_all() {
    [ -n "$IDS" ] || return 0
    LAND_RUN=${LAND_RUN:-$STAMP-$$}
    for i in $IDS; do
        python3 "$AC/status.py" start --ticket "$i" --kind land \
            --run-id "$LAND_RUN" --sha "$(git -C "$ROOT" rev-parse --short "$MAIN")" \
            --base-sha "$(git -C "$ROOT" rev-parse "$MAIN")" \
            --worktree "${WT:-}" --repro "sh scripts/land.sh $ALL_BRANCHES" \
            --cwd "$ROOT" >/dev/null 2>&1 \
            || echo "land: #$i 的狀態檔寫不出來(不擋落地)" >&2
    done
}

# gate / merge / push **各記一筆**。混成一格的 `done` 會說謊:舊版在 merge 與 push
# 之前就寫 `done, rc=0`,於是「閘門綠了但沒合進去」在狀態檔上與「已經落地」一樣。
status_phase_all() {   # $1 = gate|merge|push  $2 = rc  $3 = 說明  [$4 = 抄紅榜的全套 run_id]
    [ -n "$IDS" ] || return 0
    [ -n "$LAND_RUN" ] || return 0
    for i in $IDS; do
        from=""
        [ -z "${4:-}" ] || from=$(python3 "$AC/status.py" rundir --ticket "$i" \
            --run-id "$4" 2>/dev/null)/status.json
        python3 "$AC/status.py" phase --ticket "$i" --run-id "$LAND_RUN" \
            --phase "$1" --rc "$2" --note "$3" --failures-from "$from" >/dev/null 2>&1 \
            || echo "land: #$i 的 $1 狀態寫不出來(不擋落地)" >&2
    done
}

# **每一條退出路徑都要寫終態**(2026-09-21 外部審查:停在 running 的檔與還在跑的
# 檔長得一樣)。log 由 status.py 複製一份進 run 目錄 —— 成功之後 worktree 會被收掉。
status_done_all() {   # $1 = rc  $2 = 說明
    [ -n "$IDS" ] || return 0
    if [ -z "$LAND_RUN" ]; then
        LAND_RUN=$STAMP-$$
        status_start_all
    fi
    for i in $IDS; do
        if [ -n "${WT:-}" ] && [ -f "$WT/gate.log" ]; then
            python3 "$AC/status.py" done --ticket "$i" --kind land \
                --run-id "$LAND_RUN" --sha "${SHA:-}" --rc "$1" --note "$2" \
                --log "$WT/gate.log" >/dev/null 2>&1 \
                || echo "land: #$i 的狀態檔寫不出來(不擋落地)" >&2
        else
            python3 "$AC/status.py" done --ticket "$i" --kind land \
                --run-id "$LAND_RUN" --sha "${SHA:-}" --rc "$1" --note "$2" \
                >/dev/null 2>&1 \
                || echo "land: #$i 的狀態檔寫不出來(不擋落地)" >&2
        fi
    done
}

# 終態叫醒主線(D-015)。一票一頁:哪張票、什麼狀態、要主線做什麼、去哪看。
inbox_all() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    [ -n "$IDS" ] || return 0
    [ -z "${AC_NO_INBOX:-}" ] || return 0
    for i in $IDS; do
        python3 "$AC/inbox.py" post --ticket "$i" \
            --run-id "${LAND_RUN:-$STAMP-$$}" --kind decision \
            --state "$1" --what "$2" --where "$3" >/dev/null 2>&1 \
            || echo "land: #$i 的收件匣寫不出來(不擋落地)" >&2
    done
}

# 一頁 decision 只掛第一張票(批次的終態:整批列在內文裡,不是 N 頁一樣的)。
inbox_first() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    [ -n "$IDS" ] || return 0
    [ -z "${AC_NO_INBOX:-}" ] || return 0
    first=$(echo $IDS | cut -d' ' -f1)
    python3 "$AC/inbox.py" post --ticket "$first" \
        --run-id "${LAND_RUN:-$STAMP-$$}" --kind decision \
        --state "$1" --what "$2" --where "$3" >/dev/null 2>&1 \
        || echo "land: #$first 的收件匣寫不出來(不擋落地)" >&2
}

# 全套紅了自動派下一輪 —— 一張票的那一條路;一批 N≥2 張走 `batch_fix`(見檔頭)。
auto_fix_all() {
    [ "$AUTOFIX" -eq 1 ] || return 0
    for i in $IDS; do
        echo "land: auto-fix —— sh scripts/auto-fix.sh $i(base=$BR)"
        AC_FIX_SOURCE="$BR" sh "$AC/auto-fix.sh" "$i" \
            || echo "land: auto-fix 停下來了(rc=$?)—— 看 reports/inbox/ 那一頁"
    done
}

# ---------------------------------------------------------------- 第 1〜4 步
#
# 開跑前先把「要落地的東西」逐條唸出來。
#
# 2026-09-10(前一個專案):有人套完 patch、跑完閘門(3424 條綠),然後直接 land
# ——**中間漏了 `git commit`**。腳本照樣開 worktree、照樣跑完九分鐘的全套,最後
# 照樣說「主線 -> <原本的 sha> 已推上」,而主線一個位元都沒變。一次「成功的落地」
# 交付了零。它會這樣是因為那支腳本從頭到尾**沒有一步讓人看見自己以為在做什麼**:
# 「在落地三張票」與「在落地零張」畫面上長得一模一樣。印出每條分支的 commit
# 標題,連「以為套了 patch、其實套在別的分支」那種也會當場現形。
#
# 「沒有這條分支」與「有分支、零個 commit」分開講:`git log main..打錯的名字` 一樣
# 印不出東西,而下一步差很多 —— 改名字 vs 補一個 commit。
#
# 任何一條不過就**全部拒絕**,不是跳過那一條:`land.sh a b c` 的語意是「這三張
# 一起進去」,跳掉一條會生出一個沒有人要求過的組合,而那個組合跑出來的綠只證明
# 了那個組合。(它擋到的比寫它的時候想到的多:跳過的版本會讓一張沒進主線的票被
# 關成完成,而畫面上一個徵兆都沒有。)
STOP=""
RC_REFUSE=2
for b in "$@"; do
    if ! git -C "$ROOT" rev-parse -q --verify "$b^{commit}" >/dev/null; then
        echo "land: $b —— 沒有這條分支(名字打錯了?)"
        STOP=1
        continue
    fi
    # 分支名 `t<票號>-…` 對到票。對不到就停:沒有票的東西不落地(CLAUDE.md)。
    # `t7` 與 `t7-那張票` 都認:`scripts/auto-fix.sh` 預設開的就是 `t<票號>`,
    # 而一條開得出來、land 卻對不到票的分支,是一個只會在最後一步才說話的陷阱。
    #
    # **這一步在數 commit 之前**:拒收也是終態,而終態要寫得出「是哪一張票被退回」。
    # 擺在後面的話,0 commit 那一條路的退回沒有票號,於是狀態檔與收件匣都是空的 ——
    # 「退回去了」與「沒有退回去」在票上又長得一樣。
    id=$(echo "$b" | sed -n 's/^t\([0-9][0-9]*\)\([-.].*\)\{0,1\}$/\1/p')
    if [ -z "$id" ]; then
        echo "land: $b —— 分支名對不到票(要 t<票號> 或 t<票號>-… 的形狀),沒有票的工作不落地"
        STOP=1
        continue
    fi
    IDS="$IDS $id"

    n=$(git -C "$ROOT" rev-list --count "$MAIN..$b")
    echo "land: $b —— $n 個 commit"
    git -C "$ROOT" log --oneline "$MAIN..$b" | sed 's/^/land:   /'
    if [ "$n" -eq 0 ]; then
        printf 'land: %s 相對 %s 沒有新的 commit —— 是不是忘了 `git commit`?\n' "$b" "$MAIN"
        wt=$(git -C "$ROOT" worktree list --porcelain | awk -v want="branch refs/heads/$b" '
            /^worktree / { path = substr($0, 10) }
            $0 == want   { print path; exit }')
        dirty=""
        [ -n "$wt" ] && dirty=$(git -C "$wt" status --porcelain)
        if [ -n "$dirty" ]; then
            echo "land: $wt 裡有還沒 commit 的改動 —— 東西都在,只差一個 commit:"
            echo "$dirty" | sed 's/^/land:   /'
        fi
        STOP=1
        continue
    fi

    tf=$ROOT/$TICKETS/$id.json
    if [ ! -f "$tf" ]; then
        echo "land: $b —— 找不到票 #$id($tf)"
        STOP=1
        continue
    fi

    # `base_sha` 還是不是主線的祖先。**閘門的綠是對某一個 base 說的**:分支擱著
    # 沒落地的期間主線會往前走,那個綠就從「這份改動是好的」變成「這份改動在那
    # 一天對著那時的主線是好的」—— 而畫面上那一行 OK 一個字都沒變。
    base=$(python3 - "$tf" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("base_sha") or "")
except (OSError, ValueError):
    print("")
PY
)
    if [ -z "$base" ]; then
        echo "land: $b —— 票 #$id 沒有 base_sha,答不出「閘門那一行 OK 是對誰說的」"
        STOP=1
        continue
    fi
    # 「這顆 repo 根本沒有那個 commit」與「有、但已經不是祖先」是兩件事,下一步差
    # 很多 —— 前者要回去查副本的 base 對不對,後者是 rebase 後重跑閘門。`merge-base`
    # 對兩者都是非零,所以先問一句 `cat-file -e`(同 §5.7:守衛給錯下一步比沒有守衛更糟)。
    if ! git -C "$ROOT" cat-file -e "$base^{commit}" 2>/dev/null; then
        echo "land: $b —— 票 #$id 的 base_sha $base 這顆 repo 沒有"
        echo "land:   不是「過期」,是**對不上** —— 回去查那份副本是拿哪個 ref 做的。"
        STOP=1
        continue
    fi
    if ! git -C "$ROOT" merge-base --is-ancestor "$base" "$MAIN" 2>/dev/null; then
        echo "land: $b —— 票 #$id 的 base_sha $base 已經不是 $MAIN 的祖先"
        echo "land:   閘門的綠是對那個 base 說的,主線已經走遠。先 rebase 再重跑閘門。"
        STOP=1
        continue
    fi

    # 硬擋(D-054 Q2):只剩 `board/config.json` 的 `hard_block`(`paths` 比檔名、`patterns` 比
    # tests/** verify/** 的新增行)。印 `B`(硬擋)。
    out=$(python3 - "$ROOT" "$MAIN" "$b" <<'PY'
import os, subprocess, sys
root, main, branch = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
done = subprocess.run(["git", "-C", root, "diff", "--name-only",
                       "%s...%s" % (main, branch)],
                      capture_output=True, text=True)
names = [name for name in done.stdout.splitlines() if name]
done = subprocess.run(["git", "-c", "core.quotePath=false", "-C", root, "diff", "--no-ext-diff",
                       "--unified=0", "--src-prefix=a/", "--dst-prefix=b/",
                       "%s...%s" % (main, branch)],
                      capture_output=True, text=True, errors="replace")
for name, field, pattern in ticket_mod.hard_block_hits(root, names, ticket_mod.added_lines(done.stdout)):
    print("B %s —— 命中%s(`%s`)" % (name, field, pattern))
PY
)
    fenced=$(echo "$out" | sed -n 's/^B //p')
    if [ -n "$fenced" ]; then
        echo "land: $b —— 動到票 #$id 硬擋的檔:"
        echo "$fenced" | sed 's/^/land:   /'
        STOP=1
        continue
    fi
    # 覆核與反駁:complex 票要 review.verdict 是通過(simple / 沒標不要,#136);
    # 反駁(`objections[]`):實作者說「這張票寫錯了」而東西照樣落地,那句話等於沒人收。
    # 判準只有一份,在 `ticket.py` 的 review_problems / objection_problems。
    out=$(python3 - "$ROOT" "$tf" <<'PY'
import json, os, sys
root, path = sys.argv[1:3]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
try:
    with open(path, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError) as exc:
    print("票讀不動:%s" % exc)
    raise SystemExit(0)
for line in ticket_mod.objection_problems(ticket) + ticket_mod.review_problems(ticket):
    print(line)
PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 票 #$id 的覆核 / 反駁過不了:"
        echo "$out" | sed 's/^/land:   /'
        echo "land:   覆核記法:scripts/ticket.py set $id review '{\"verdict\":\"pass\",\"by\":\"main\",\"sha\":\"<分支頭>\"}'"
        STOP=1
        continue
    fi

    # 分支**新增**的路徑,在主線工作樹裡已經是未追蹤的檔(#69)。第 6 步的 ff-only 會被
    # git 拒絕(untracked working tree files would be overwritten by merge),而那是跑完
    # 九分鐘全套之後的事 —— 這裡先查得出來就先查。判準用 git 自己的:未追蹤且未被 ignore。
    # **不替人搬、不刪**:那是使用者的檔,搬了就是另一種事故。
    out=$(python3 - "$ROOT" "$MAIN" "$b" <<'UNTRACKED_PY'
import subprocess, sys
root, main, branch = sys.argv[1:4]
added = subprocess.run(["git", "-C", root, "diff", "-z", "--name-only", "--no-renames",
                        "--diff-filter=A", "%s...%s" % (main, branch)],
                       capture_output=True, text=True)
names = [name for name in added.stdout.split("\0") if name]
if names:
    found = subprocess.run(["git", "--literal-pathspecs", "-C", root, "ls-files", "-z",
                            "--others", "--exclude-standard", "--"] + names,
                           capture_output=True, text=True)
    for name in found.stdout.split("\0"):
        if name:
            print(name)
UNTRACKED_PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 主線工作樹裡有未追蹤的檔,與這條分支新增的路徑撞在一起(ff-only 會被 git 拒絕):"
        echo "$out" | sed 's/^/land:   /'
        echo "land:   land 不會替你搬、不會刪 —— 自己把它們移走,或 commit 進 $MAIN,再重跑 land。"
        STOP=1
        continue
    fi
done
if [ -n "$STOP" ]; then
    echo "land: 一條都沒有落地 —— 上面那幾條先處理掉再來。"
    echo "land: (要嘛一起進去、要嘛都不進:跳掉一條會生出一個沒有人要求過的組合。)"
    ev land.refused --note "$*" --kv "stamp=$STAMP"
    status_done_all "$RC_REFUSE" "land 拒收"
    inbox_all "land 拒收(rc=$RC_REFUSE)" \
        "上面逐條寫了是哪一條不過:0 commit / base 過期 / 越界 / 覆核 / 未追蹤撞路徑;產品票缺案例時先派驗證者" \
        "reports/t<票號>/${LAND_RUN:-} 的 status.json"
    exit "$RC_REFUSE"
fi

# 跑了且紅的那幾條路(發 land.fail 的)每票也寫一列成本(#94 C5e):land.start 到紅的
# 秒數就是白花的那一段。refused 是還沒開始就退,不寫。
land_cost_all() {   # $1 = 結局 pass|fail
    LAND_SECS=$(( $(date +%s) - LAND_T0 ))
    for i in $IDS; do
        _attempt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("attempt") or 0)' \
            "$ROOT/$TICKETS/$i.json" 2>/dev/null || echo 0)
        python3 "$AC/ticket.py" cost "$i" --role land --round "$_attempt" --by land.sh \
            --wall-seconds "$LAND_SECS" --outcome "$1" --run-id "${LAND_RUN:-$STAMP-$$}" \
            >/dev/null 2>&1 \
            || echo "land: #$i 的 cost 寫不進票(不擋落地)" >&2
    done
}

# ------------------------------------------------------------------- 第 5 步
# 副本根與 `auto-fix.sh` **同一種解析**(#38):環境變數 > `worktree_dir` > `../<主 repo>-wt`,
# 相對路徑以主 repo 根(`wtbase.sh` 的 `main_root`)拼。各算各的那一天,auto-fix 開副本的
# 地方與底下唸殘留清單的地方就不是同一個目錄,而「沒有殘留」與「看錯地方」長得一樣。
# 兩支共用 `scripts/wtbase.sh`(#46;檔頭已載入)。
WTBASE=$(wtbase)
WT=$WTBASE/land-$STAMP
BR=land/$STAMP
# 第 6 步 ff-only 進不去時拿來比:主線真的動了,還是沒動、是 git 拒絕(#69)。
MAIN_BEFORE=$(git -C "$ROOT" rev-parse "$MAIN")
mkdir -p "$WTBASE"
git -C "$ROOT" worktree add -q -b "$BR" "$WT" "$MAIN" || {
    ev land.fail --note "worktree add 失敗" --kv "stamp=$STAMP"
    land_cost_all fail
    status_done_all 2 "worktree add 失敗"
    exit 2
}
# 這一次落地接在哪一個 commit 後面(#127 A1(a)):land.pass 帶它,越界守衛拿它把這一段
# 接在 agent 起跑時的 HEAD(或上一段的終點)後面。
LAND_BASE=$(git -C "$WT" rev-parse HEAD)

# 合併結果的 code-map 索引重產(#129)。每條分支自己 build 過、各自 check 綠,合起來仍會過期
# (兩邊各新增一支已管理的檔),以前要全套閘門退回一整輪。判準與 build 是 `wtbase.sh`
# 的 `code_map_regen`(apply 那兩處同一份);這裡**只** stage 兩支產出物,stage 完工作樹還有
# 別的差異(改動或新檔)= build 動到 code-map/ 以外 —— 不 commit、worktree 留著、rc 3。
# 退出碼:0 產出物已 stage(可能沒變);1 build 非零(不 commit regen;閘門不量索引,D-054 Q4,照常往下落地)。
land_regen() {
    code_map_regen "$WT"
    if [ $? -eq 2 ]; then
        echo "land: 合併後索引重產失敗(repo-map build 非零,上面 repo-map: 那幾行)—— 沒有 regen commit,照常落地(閘門不量索引);索引要人重產"
        return 1
    fi
    git -C "$WT" add -- code-map/index.json code-map/INDEX.md
    stray=$(git -C "$WT" diff --name-only; git -C "$WT" ls-files --others --exclude-standard)
    [ -n "$stray" ] || return 0
    echo "land: 重產動到 code-map/ 以外 —— repo-map build 寫了 code-map/index.json、INDEX.md 以外的檔,不 commit;worktree 留在 $WT:"
    echo "$stray" | sed 's/^/land:   /'
    ev land.fail --note "合併後重產動到 code-map/ 以外" --kv "stamp=$STAMP"
    land_cost_all fail
    status_start_all
    status_phase_all merge 3 "合併後重產動到 code-map/ 以外"
    status_done_all 3 "合併後重產動到 code-map/ 以外,一條都沒進主線"
    inbox_all "合併後重產動到 code-map/ 以外" \
        "repo-map build 寫了 code-map/index.json、INDEX.md 以外的檔:看 worktree 裡那幾個檔、修好產生器再重跑 land" "$WT"
    exit 3
}

REGEN_FAILED=""
for b in "$@"; do
    if ! git -C "$WT" merge -q --no-ff "$b" -m "Merge $b (land $STAMP)"; then
        # 衝突**全部**落在兩支產出物裡:取主線那一側、重產、完成這一個 merge commit。
        # 任何其他檔衝突照舊停 —— 產出物以外的衝突要人判。
        unmerged=$(git -C "$WT" diff --name-only --diff-filter=U)
        others=$(echo "$unmerged" | grep -vx -e code-map/index.json -e code-map/INDEX.md)
        if [ -n "$unmerged" ] && [ -z "$others" ] && code_map_present "$WT" \
            && git -C "$WT" checkout -q --ours -- $unmerged && git -C "$WT" add -- $unmerged; then
            if land_regen; then
                git -C "$WT" commit -q -m "Merge $b (land $STAMP)" \
                    -m "regen: repo-map build (land $STAMP)" || exit 2
                echo "land: 合併後 code-map 索引過期 —— 合 $b 時只有產出物衝突,重產進 merge commit $(git -C "$WT" rev-parse --short HEAD)"
            else
                REGEN_FAILED=1
                git -C "$WT" commit -q -m "Merge $b (land $STAMP)" || exit 2
            fi
            continue
        fi
        echo "land: 合 $b 時衝突 —— worktree 留在 $WT,自己看"
        ev land.fail --note "合 $b 時衝突" --kv "stamp=$STAMP"
        land_cost_all fail
        status_start_all
        status_phase_all merge 3 "串 $b 時衝突"
        status_done_all 3 "串接時衝突,一條都沒進主線"
        inbox_all "串接衝突" "自己解衝突:worktree 留著給你看" "$WT"
        exit 3
    fi
done
# 合併結果過期 → 重產成 land 分支上一個 commit,跟著同一次 ff-only 進主線。LAND_BASE 留在合併之前,
# land.pass 的 main_before..sha 涵蓋這一個 commit(#127 的越界守衛照樣接得上)。
if [ -z "$REGEN_FAILED" ] && code_map_present "$WT" \
    && ! python3 -B "$WT/scripts/repo-map.py" --root "$WT" check >/dev/null 2>&1; then
    if land_regen && ! git -C "$WT" diff --cached --quiet; then
        git -C "$WT" commit -q -m "regen: repo-map build (land $STAMP)" \
            -m "Merged result's code-map index was stale; rebuilt by land.sh before the full gate." \
            || exit 2
        echo "land: 合併後 code-map 索引過期 —— 重產成 $BR 上一個 commit $(git -C "$WT" rev-parse --short HEAD),跟著這一次落地進 $MAIN"
    fi
fi
COUNT=$(git -C "$WT" log --oneline "$MAIN..HEAD" | wc -l | tr -d ' ')
echo "land: $COUNT 個 commit 串好,跑全套 -> $WT/gate.log"
status_start_all
NIDS=$(echo "$IDS" | wc -w | tr -d ' ')
GATE_TICKET=""
GATE_TICKETS=""
if [ "$NIDS" = "1" ]; then
    for i in $IDS; do GATE_TICKET=$i; done
else
    GATE_TICKETS=$(echo $IDS)
fi

# 全套一輪(#109 B6):事件帶 mode / round / batch / ac_sha,狀態檔每輪一筆 gate phase。
# 第 1 輪的 run_id 照舊是 `<land run>-gate`;批次修復之後的第 r 輪是 `<land run>-gate-r<r>`。
# 跑哪一份閘門(D-071 C3,`wtbase.sh` 的 `ac_gate_script`):專案就是 A 自己時跑 worktree 那一份
# (A 的票改的正是閘門),其他專案一律跑 `$AC_HOME/scripts/gate.sh`(它們的樹裡沒有)。
ROUND=0
GATE_RUN=""
GATE=$(ac_gate_script "$WT")
AC_SHA=$(ac_sha "$(ac_home)")
run_gate() {
    ROUND=$((ROUND + 1))
    SHA=$(git -C "$WT" rev-parse --short HEAD)
    GATE_RUN=$LAND_RUN-gate
    [ "$ROUND" -eq 1 ] || GATE_RUN=$LAND_RUN-gate-r$ROUND
    ev gate.start --kv mode=full --kv "round=$ROUND" --kv "batch=$STAMP" --kv "sha=$SHA" \
        --kv "ac_sha=$AC_SHA"
    if (cd "$WT" && AC_ROOT="$ROOT" AC_GATE_TICKET="$GATE_TICKET" AC_GATE_TICKETS="$GATE_TICKETS" \
        AC_GATE_RUN_ID="$GATE_RUN" AC_NO_INBOX=1 sh "$GATE" --full --no-auto-fix); then
        status_phase_all gate 0 "全套第 $ROUND 輪綠"
        ev gate.pass --kv mode=full --kv "round=$ROUND" --kv "batch=$STAMP" --kv "sha=$SHA" \
            --kv "ac_sha=$AC_SHA"
        return 0
    fi
    status_phase_all gate 1 "全套第 $ROUND 輪紅" "$GATE_RUN"
    ev gate.fail --kv mode=full --kv "round=$ROUND" --kv "batch=$STAMP" --kv "sha=$SHA" \
        --kv "ac_sha=$AC_SHA"
    return 1
}

# 批次修復的輪數:`board/config.json` 的 `batch_fix.rounds`,預設 2。
batch_rounds() {
    python3 - "$ROOT" <<'ROUNDS_PY'
import json, os, sys
try:
    with open(os.path.join(sys.argv[1], "board", "config.json"), encoding="utf-8") as handle:
        value = int((json.load(handle).get("batch_fix") or {}).get("rounds", 2))
except (OSError, ValueError, TypeError, AttributeError):
    value = 2
print(max(0, value))
ROUNDS_PY
}

# 一批 N≥2 張全套紅(D-H51 ②,見檔頭):紅 → auto-fix --batch → 重跑全套,最多 rounds 輪;
# 綠了 return 0 走第 6 步;其餘每一條路都在這裡收尾、退出。$@ = 這一批的分支。
batch_fix() {
    rounds=$(batch_rounds)
    fixed=0
    while [ "$fixed" -lt "$rounds" ]; do
        fixed=$((fixed + 1))
        echo "land: 全套第 $ROUND 輪紅 —— 批次修復第 $fixed/$rounds 輪:AC_BATCH_RUN_ID=$LAND_RUN sh scripts/auto-fix.sh --batch $BR $GATE_TICKETS"
        # 紅榜已由 run_gate 抄進 land 這一輪的狀態檔(reports/t<第一張>/$LAND_RUN/status.json)。
        # shellcheck disable=SC2086
        AC_BATCH_RUN_ID="$LAND_RUN" AC_FIX_SOURCE="$BR" sh "$AC/auto-fix.sh" --batch "$BR" $IDS
        frc=$?
        if [ "$frc" -ne 0 ]; then
            echo "land: 批次修復停了(auto-fix --batch rc=$frc)—— 不重跑全套,$MAIN 沒動;worktree 留在 $WT"
            ev land.fail --note "批次修復停了(rc=$frc)" --kv "stamp=$STAMP"
            land_cost_all fail
            status_done_all 1 "批次修復停了(auto-fix --batch rc=$frc),沒有 merge、沒有 push"
            inbox_first "批次修復停了(auto-fix --batch rc=$frc)" \
                "整批 $ALL_BRANCHES 第 $fixed 輪修復沒交出 commit:先看 auto-fix 自己發的那一頁與 reports/batch/,處理完重跑 land" \
                "$WT/gate.log 與 reports/t<票號>/$GATE_RUN/status.json"
            exit 1
        fi
        if run_gate; then
            echo "land: 批次修復第 $fixed 輪之後全套綠"
            return 0
        fi
    done
    echo "land: 全套連紅 $ROUND 輪(批次修復 $fixed 輪)—— 找嫌疑票"
    STATUS_FILE=$(python3 "$AC/status.py" rundir --ticket "$(echo $IDS | cut -d' ' -f1)" \
        --run-id "$GATE_RUN" 2>/dev/null)/status.json
    # 第一行:嫌疑票號(空白分隔,可能是空的);其餘:逐條「案例(檔)—— 命中哪張票的哪一格」。
    SUSPECT_OUT=$(python3 - "$ROOT" "$MAIN" "$TICKETS" "$STATUS_FILE" "$@" <<'SUSPECT_PY'
import json, os, re, subprocess, sys
root, main, tickets, status = sys.argv[1:5]
branches = sys.argv[5:]
try:
    with open(status, encoding="utf-8") as handle:
        failures = json.load(handle).get("failures") or []
except (OSError, ValueError):
    failures = []
rows = []
for branch in branches:
    ident = re.sub(r"^t([0-9]+)([-.].*)?$", r"\1", branch)
    try:
        with open(os.path.join(root, tickets, ident + ".json"), encoding="utf-8") as handle:
            row = json.load(handle)
    except (OSError, ValueError):
        row = {}
    plan = row.get("verify") if isinstance(row.get("verify"), dict) else {}
    done = subprocess.run(["git", "-C", root, "diff", "--name-only", "%s...%s" % (main, branch)],
                          capture_output=True, text=True)
    rows.append((ident, plan.get("files") or [],
                 [name for name in done.stdout.splitlines() if name]))
hit, lines = [], []
for failure in failures:
    name = (failure or {}).get("file") or ""
    case = (failure or {}).get("case") or "?"
    if not name:
        lines.append("%s —— 紅榜沒有檔名,對不到票" % case)
        continue
    owners = []
    for ident, files, changed in rows:
        why = []
        if name in files:
            why.append("verify.files")
        if name in changed:
            why.append("分支改動檔")
        if why:
            owners.append(ident)
            lines.append("%s(%s)—— 命中 #%s 的 %s" % (case, name, ident, "、".join(why)))
            if ident not in hit:
                hit.append(ident)
    if not owners:
        lines.append("%s(%s)—— 對不到任何一張票" % (case, name))
print(" ".join(ident for ident, _, _ in rows if ident in hit))
for line in lines:
    print(line)
SUSPECT_PY
)
    SUSPECTS=$(echo "$SUSPECT_OUT" | sed -n 1p)
    DETAIL=$(echo "$SUSPECT_OUT" | sed 1d)
    [ -z "$DETAIL" ] || echo "$DETAIL" | sed 's/^/land:   /'
    WHY=$(echo "$DETAIL" | awk 'NF { printf "%s%s", sep, $0; sep = " ／ " }')
    NSUS=$(echo "$SUSPECTS" | wc -w | tr -d ' ')
    if [ "$DEPTH" -gt 0 ] || [ "$NSUS" -eq 0 ] || [ "$NSUS" -ge "$NIDS" ]; then
        if [ "$DEPTH" -gt 0 ]; then
            why_not="這已經是踢人之後的重 land(AC_LAND_DEPTH=$DEPTH),不再踢"
        elif [ "$NSUS" -eq 0 ]; then
            why_not="紅榜對不到任何一張票"
        else
            why_not="紅榜對到這一批每一張票"
        fi
        echo "land: 不踢、不重 land —— $why_not;$MAIN 沒動,worktree 留在 $WT"
        ev land.fail --note "批次全套連紅 $ROUND 輪,不踢($why_not)" --kv "stamp=$STAMP"
        land_cost_all fail
        status_done_all 1 "批次全套連紅 $ROUND 輪,不踢:$why_not"
        inbox_first "批次全套連紅 $ROUND 輪(整批 $ALL_BRANCHES)" \
            "$why_not —— 批次修復 $fixed 輪仍紅,留給主線:看紅榜與 worktree,決定拆批或改票。${WHY:+紅榜:$WHY}" \
            "$WT/gate.log 與 reports/t<票號>/$GATE_RUN/status.json"
        exit 1
    fi
    REST=""
    for b in "$@"; do
        id=$(echo "$b" | sed -n 's/^t\([0-9][0-9]*\)\([-.].*\)\{0,1\}$/\1/p')
        case " $SUSPECTS " in
            *" $id "*)
                echo "land: 踢出 #$id($b)—— 紅榜對到它;這張票不改 state,之後單張 land 它"
                if [ -z "${AC_NO_INBOX:-}" ]; then
                    python3 "$AC/inbox.py" post --ticket "$id" \
                        --run-id "$LAND_RUN-kick" --kind decision \
                        --state "批次落地踢出(全套連紅 $ROUND 輪)" \
                        --what "紅榜對到這張票,已從整批 $ALL_BRANCHES 踢出、其餘自動重 land;修好後單張 land:sh scripts/land.sh ${b}。紅榜:$WHY" \
                        --where "$WT/gate.log 與 reports/t<票號>/$GATE_RUN/status.json" >/dev/null 2>&1 \
                        || echo "land: #$id 的收件匣寫不出來(不擋落地)" >&2
                fi
                ;;
            *) REST="$REST $b" ;;
        esac
    done
    ev land.fail --note "批次全套連紅 $ROUND 輪,踢出 #$(echo "$SUSPECTS" | sed 's/ / #/g')" \
        --kv "stamp=$STAMP" --kv "kicked=$(echo "$SUSPECTS" | tr ' ' ',')"
    land_cost_all fail
    status_done_all 1 "批次全套連紅 $ROUND 輪,踢出 #$(echo "$SUSPECTS" | sed 's/ / #/g'),其餘重 land"
    echo "land: 重 land 其餘:sh scripts/land.sh$REST(AC_LAND_DEPTH=$((DEPTH + 1)));這一批的 worktree 留在 $WT"
    # shellcheck disable=SC2086
    AC_LAND_DEPTH=$((DEPTH + 1)) sh "$AC/land.sh" $REST
    relanded=$?
    exit "$relanded"
}

if ! run_gate; then
    if [ "$AUTOFIX" -eq 1 ] && [ "$NIDS" -ge 2 ]; then
        batch_fix "$@"
    else
        echo "land: 全套紅,$MAIN 沒動;worktree 留在 $WT"
        echo "land: 紅榜逐條在 reports/t<票號>/<run_id>/status.json 的 failures(案例、檔、行、log、excerpt)"
        ev land.fail --note "閘門紅" --kv "stamp=$STAMP"
        land_cost_all fail
        status_done_all 1 "閘門紅,沒有 merge、沒有 push"
        inbox_all "落地時全套紅" \
            "預設已自動派下一輪 worker;--no-auto-fix 才留給人處理" \
            "$WT/gate.log 與 reports/t<票號>/$LAND_RUN/status.json"
        auto_fix_all
        exit 1
    fi
fi

# ------------------------------------------------------------------- 第 6 步
# ff-only 進不去有兩種,下一步差很多(#69):主線真的動了 → rebase 後重跑閘門;主線沒動、
# 是 git 拒絕(例:主線工作樹的檔會被覆蓋)→ rebase 是錯的下一步,要看 git 的原話。
# 舊版一律說「動了」,而 git 的 stderr 沒進 stdout / 收件匣 / status.json。
MERGE_ERR=$(git -C "$ROOT" merge -q --ff-only "$BR" 2>&1 >/dev/null) || {
    MAIN_NOW=$(git -C "$ROOT" rev-parse "$MAIN")
    if [ "$MAIN_NOW" != "$MAIN_BEFORE" ]; then
        echo "land: $MAIN 在這中間動了($(git -C "$ROOT" rev-parse --short "$MAIN_BEFORE") -> $(git -C "$ROOT" rev-parse --short "$MAIN_NOW")),ff-only 進不去 —— worktree 留在 $WT"
        ev land.fail --note "ff-only 進不去(主線動了)" --kv "stamp=$STAMP"
        land_cost_all fail
        status_phase_all merge 1 "ff-only 進不去:主線動了"
        status_done_all 1 "閘門綠了但沒合進主線"
        inbox_all "閘門綠了但沒合進主線" "主線在這中間動了 —— rebase 後重跑閘門" "$WT"
        exit 1
    fi
    echo "land: ff-only 進不去,但 $MAIN 沒有動($(git -C "$ROOT" rev-parse --short "$MAIN_NOW"))—— 是 git 拒絕合併;worktree 留在 $WT"
    echo "land: git 的原話:"
    echo "$MERGE_ERR" | sed 's/^/land:   /'
    ev land.fail --note "ff-only 進不去(主線沒動,git 拒絕)" --kv "stamp=$STAMP"
    land_cost_all fail
    status_phase_all merge 1 "ff-only 進不去,主線沒動,git 的原話:$MERGE_ERR"
    status_done_all 1 "閘門綠了但沒合進主線"
    inbox_all "閘門綠了但沒合進主線" \
        "主線沒動,git 拒絕合併 —— 看 stdout / status.json 的原話,處理完重跑 land" "$WT"
    exit 1
}
status_phase_all merge 0 "已 ff-only 合進 $MAIN"
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
    git -C "$ROOT" push -q origin "$MAIN" || {
        echo "land: 合進 $MAIN 了,但 push 沒成功 —— 自己推一次"
        # 已合進本機 main(#128 A4):與 land.pass 同形狀再加 stage=push,越界守衛把它當一段落地。
        ev land.fail --note "push 沒成功" --kv "stamp=$STAMP" --kv stage=push \
            --kv "sha=$(git -C "$ROOT" rev-parse --short HEAD)" --kv "main_before=$LAND_BASE"
        land_cost_all fail
        status_phase_all push 1 "push 沒成功"
        status_done_all 1 "已合進主線,push 沒成功,票還沒關"
        inbox_all "已合進主線,push 沒成功" "自己推一次:git push origin $MAIN" "$ROOT"
        exit 1
    }
    status_phase_all push 0 "已推上 origin"
else
    status_phase_all push 0 "沒有 origin,不用推"
fi
echo "land: $MAIN -> $(git -C "$ROOT" rev-parse --short HEAD) 已推上"
ev land.pass --note "$*" --kv "stamp=$STAMP" \
    --kv "sha=$(git -C "$ROOT" rev-parse --short HEAD)" --kv "main_before=$LAND_BASE"
# **已合併、已關票**與**已合併、尚未關票**要分開講(2026-09-21 外部審查:能力表誤稱
# land 會關票)。關票仍是 `scripts/ticket.py close` 判:它自己再問一次「改動真的在主線嗎」
# 與 Done 的四條件(done_blockers),這裡只是換成 land 來打(#43,見檔頭)。
LANDED=$(git -C "$ROOT" rev-parse "$MAIN")
status_done_all 0 "已合併、已推上;關票由 ticket.py close --landed 判(結果看 stdout 與收件匣)"
# 每張票一筆 role=land 的成本(D-032):land 沒有 LLM,model 與 token 欄是 null,只有
# land.start 到 push 的秒數。寫在 close 之前;cost 不看 state、不動 state_version。
land_cost_all pass
for i in $IDS; do
    CLOSE_OUT=$(python3 "$AC/ticket.py" close "$i" --landed "$LANDED" 2>&1)
    CLOSE_RC=$?
    if [ "$CLOSE_RC" -eq 0 ]; then
        echo "land: #$i 已合併、已關票(ticket.py close --landed $(echo "$LANDED" | cut -c1-12))"
        if [ -z "${AC_NO_INBOX:-}" ]; then
            python3 "$AC/inbox.py" done "$i" --landed "$LANDED" --by land.sh \
                --run-id "${LAND_RUN:-$STAMP-$$}" >/dev/null 2>&1 \
                || echo "land: #$i 的完成簡報寫不出來(不擋落地)" >&2
        fi
        continue
    fi
    echo "land: #$i 已合併、尚未關票 —— ticket.py close 關不掉(rc=$CLOSE_RC),它說:"
    echo "$CLOSE_OUT" | sed 's/^/land:   /'
    echo "land:   補齊後:python3 scripts/ticket.py close $i --landed $LANDED"
    # 一頁的 what 是一行:原文逐行接起來,一個字都不改寫(理由只有 ticket.py 一份)。
    WHY=$(echo "$CLOSE_OUT" | awk 'NF { sub(/^[ \t]+/, ""); printf "%s%s", sep, $0; sep = " ／ " }')
    if [ -z "${AC_NO_INBOX:-}" ]; then
        python3 "$AC/inbox.py" post --ticket "$i" \
            --run-id "${LAND_RUN:-$STAMP-$$}" --kind decision --state "已合併、尚未關票" \
            --what "ticket.py close 關不掉(rc=$CLOSE_RC):$WHY ／ 補齊後:python3 scripts/ticket.py close $i --landed $LANDED" \
            --where "git log --oneline -3 $MAIN" >/dev/null 2>&1 \
            || echo "land: #$i 的收件匣寫不出來(不擋落地)" >&2
    fi
done
git -C "$ROOT" worktree remove "$WT" && git -C "$ROOT" branch -q -D "$BR"

# 還躺在 $WTBASE 底下的修復 / 驗證副本(#29 A6,G6)。**只印不刪**:這裡不知道哪一份
# 還有人在看(綠了停 InReview 的那一輪就留著),而**猜錯刪掉的是別人正在讀的證據**。
# 印出來是為了讓「沒人收」不再是靜的 —— 2026-09-16 某個下游專案的副本 14 GB 塞滿磁碟,
# 全套當場 disk I/O error,而在那之前它一聲都沒有出過。
LEFT=$(ls -d "$WTBASE"/fix-t*/ "$WTBASE"/verify-t*/ 2>/dev/null || true)
if [ -n "$LEFT" ]; then
    echo "land: $WTBASE 底下還留著這幾份副本(只印不刪,確認沒人在看再 rm -rf):"
    echo "$LEFT" | sed 's/^/land:   /'
    echo "land:   (auto-fix 每一輪收完 patch 就刪掉 work/ 與 base/;剩下的是 patch、"
    echo "land:    EVIDENCE 與派工文,那幾份是證據,不要一起掃掉。)"
fi
