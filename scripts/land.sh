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
# 0 commit、票對不上、`base_sha` 過期、寫入範圍越界、閘門紅。要它放寬的時候,
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
# 票要有一格**綁著票版本與分支 sha** 的 `review`(主線讀 patch 記的),否則拒絕:
# 舊版的 review 只有 verdict / by / at / note,重套一次 patch 之後它還是長得有效,
# 而 land 根本沒讀它。票的 `objections[]` 裡還有沒處置的阻擋項,也拒絕 ——
# 實作者說「這張票寫錯了」而東西照樣落地,那句話等於沒有人收。
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
# 全套紅時預設派下一輪 worker(`--no-auto-fix` 才關),**但只在這一批剛好一張票的時候** ——
# 一批裡哪一條紅對到哪一張票,要有票↔案例的對照才判得出來(能力表列著這一條未實作),
# 而**猜錯的歸責比不歸責更貴**:它會讓一個新 worker 去修一張沒有壞的票。
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
ROOT=$(cd "$(dirname "$0")/.." && pwd)

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
release() { [ -n "$HELD" ] && rm -rf "$LOCK"; }
trap 'release' EXIT INT TERM
take_lock() {   # $1 = 這一次在做什麼(寫進 holder,給撞上的人看)
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
    python3 "$ROOT/scripts/event.py" emit "$@" >/dev/null \
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
        echo "land:   產品碼走一票一分支:sh scripts/apply.sh <票號> <patch>,再 sh scripts/land.sh t<票號>" >&2
        exit 2
    fi
    here=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$here" != "$MAIN" ]; then
        echo "land: docs 通道要在 $MAIN 上跑,現在在 $here —— 進錯分支的 commit 沒有人看得出來" >&2
        exit 2
    fi
    take_lock "docs: $msg"
    ev land.start --note "docs $msg"
    git -C "$ROOT" add -- "$@" || {
        echo "land: git add 收不下這幾個檔(打錯路徑?)" >&2
        ev land.refused --note "docs add 失敗"
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
        ev land.pass --note "docs 沒有改動(已在主線上)"
        exit 0
    fi
    git -C "$ROOT" commit -q -m "$msg" -- "$@" || {
        echo "land: git commit 失敗" >&2
        ev land.fail --note "docs commit 失敗"
        exit 2
    }
    sha=$(git -C "$ROOT" rev-parse --short HEAD)
    echo "land: docs -> $MAIN $sha"
    git -C "$ROOT" show --stat --oneline -s HEAD | sed 's/^/land:   /'
    if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
        git -C "$ROOT" push -q origin "$MAIN" || {
            echo "land: 已 commit,但 push 沒成功 —— 自己推一次:git push origin $MAIN" >&2
            ev land.fail --note "docs push 沒成功" --kv "sha=$sha"
            exit 1
        }
    fi
    ev land.pass --note "docs $msg" --kv "sha=$sha"
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
        python3 "$ROOT/scripts/status.py" start --ticket "$i" --kind land \
            --run-id "$LAND_RUN" --sha "$(git -C "$ROOT" rev-parse --short "$MAIN")" \
            --base-sha "$(git -C "$ROOT" rev-parse "$MAIN")" \
            --worktree "${WT:-}" --repro "sh scripts/land.sh $ALL_BRANCHES" \
            --cwd "$ROOT" >/dev/null 2>&1 \
            || echo "land: #$i 的狀態檔寫不出來(不擋落地)" >&2
    done
}

# gate / merge / push **各記一筆**。混成一格的 `done` 會說謊:舊版在 merge 與 push
# 之前就寫 `done, rc=0`,於是「閘門綠了但沒合進去」在狀態檔上與「已經落地」一樣。
status_phase_all() {   # $1 = gate|merge|push  $2 = rc  $3 = 說明
    [ -n "$IDS" ] || return 0
    [ -n "$LAND_RUN" ] || return 0
    for i in $IDS; do
        python3 "$ROOT/scripts/status.py" phase --ticket "$i" --run-id "$LAND_RUN" \
            --phase "$1" --rc "$2" --note "$3" >/dev/null 2>&1 \
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
            python3 "$ROOT/scripts/status.py" done --ticket "$i" --kind land \
                --run-id "$LAND_RUN" --sha "${SHA:-}" --rc "$1" --note "$2" \
                --log "$WT/gate.log" >/dev/null 2>&1 \
                || echo "land: #$i 的狀態檔寫不出來(不擋落地)" >&2
        else
            python3 "$ROOT/scripts/status.py" done --ticket "$i" --kind land \
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
        python3 "$ROOT/scripts/inbox.py" post --ticket "$i" \
            --run-id "${LAND_RUN:-$STAMP-$$}" --kind decision \
            --state "$1" --what "$2" --where "$3" >/dev/null 2>&1 \
            || echo "land: #$i 的收件匣寫不出來(不擋落地)" >&2
    done
}

# 全套紅了自動派下一輪 —— **只在剛好一張票的時候**(見檔頭)。
auto_fix_all() {
    [ "$AUTOFIX" -eq 1 ] || return 0
    count=$(echo "$IDS" | wc -w | tr -d " ")
    if [ "$count" != "1" ]; then
        echo "land: auto-fix 這一批有 $count 張票 —— 不猜是誰紅的,留給主線"
        echo "land:   (票↔案例的對照還沒有;猜錯的歸責會讓新 worker 去修沒壞的票。)"
        return 0
    fi
    for i in $IDS; do
        echo "land: auto-fix —— sh scripts/auto-fix.sh $i(base=$BR)"
        AC_FIX_SOURCE="$BR" sh "$ROOT/scripts/auto-fix.sh" "$i" \
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
    # `t7` 與 `t7-那張票` 都認:`scripts/apply.sh` 預設開的就是 `t<票號>`,
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

    # 寫入範圍。排順序的人就是拿這一格判能不能平行的(docs/DESIGN.md §10),所以越界
    # 不只是「改了不該改的檔」,是**排順序當時算出來的那張衝突圖已經不成立**。
    out=$(python3 - "$ROOT" "$MAIN" "$b" "$tf" <<'PY'
import fnmatch, json, subprocess, sys
root, main, branch, path = sys.argv[1:5]
try:
    with open(path, encoding="utf-8") as handle:
        globs = json.load(handle).get("allowed_write_paths") or []
except (OSError, ValueError):
    globs = []
done = subprocess.run(["git", "-C", root, "diff", "--name-only",
                       "%s...%s" % (main, branch)],
                      capture_output=True, text=True)


def allowed(name):
    for pattern in globs:
        if fnmatch.fnmatch(name, pattern) or name == pattern:
            return True
        if pattern and not pattern.endswith("*") \
                and name.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


for name in done.stdout.splitlines():
    if name and not allowed(name):
        print(name)
PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 動到票 #$id 的 allowed_write_paths 以外的檔:"
        echo "$out" | sed 's/^/land:   /'
        STOP=1
        continue
    fi
    # 覆核與反駁。**覆核要綁被覆核的那個版本**:舊版的 `review` 只有 verdict/by/at/
    # note,重套一次 patch、改一次票面之後它仍然長得有效,而 land 從來沒有讀它。
    # 反駁(`objections[]`)同理:實作者說「這張票寫錯了」而東西照樣落地,那句話等於沒人收。
    out=$(python3 - "$tf" "$(git -C "$ROOT" rev-parse "$b")" <<'PY'
import json, sys
path, tip = sys.argv[1], sys.argv[2]
PASS = ("pass", "approved", "ok", "通過")
DISPOSED = ("accepted", "rejected", "deferred", "fixed", "已處置")
try:
    with open(path, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError) as exc:
    print("票讀不動:%s" % exc)
    raise SystemExit(0)

for index, row in enumerate(ticket.get("objections") or []):
    if not isinstance(row, dict):
        print("objections[%d] 不是 {category, body, evidence, owner, disposition}" % index)
        continue
    blocking = row.get("blocking")
    if blocking is None:
        blocking = str(row.get("category") or "").lower() in ("blocking", "阻擋", "ticket-wrong")
    if blocking and str(row.get("disposition") or "").strip().lower() not in DISPOSED:
        print("反駁 objections[%d](%s / owner=%s)還沒處置:%s"
              % (index, row.get("category") or "?", row.get("owner") or "沒人",
                 (row.get("body") or "")[:60]))

review = ticket.get("review")
if not isinstance(review, dict) or not review.get("verdict"):
    print("票上沒有 review —— 覆核是主線讀 patch 記進票的那一格,land 不替它判")
    raise SystemExit(0)
if str(review.get("verdict")).lower() not in PASS:
    print("review.verdict=%r 不是通過" % review.get("verdict"))
bound = review.get("state_version")
current = ticket.get("state_version")
if bound is None:
    print("review 沒有綁票版本(state_version)—— 票改過之後它還是長得有效")
elif str(bound) != str(current):
    print("review 綁的是票 v%s,票現在是 v%s —— 覆核之後票被改過"
          % (bound, current))
sha = str(review.get("sha") or "")
if not sha:
    print("review 沒有綁最終 patch / 分支的 sha")
elif not (tip.startswith(sha) or sha.startswith(tip)):
    print("review 綁的是 %s,這條分支的頭是 %s —— 覆核之後又 commit 過"
          % (sha[:12], tip[:12]))
PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 票 #$id 的覆核 / 反駁過不了:"
        echo "$out" | sed 's/^/land:   /'
        echo "land:   覆核記法:scripts/ticket.py set $id review '{\"verdict\":\"pass\",\"by\":\"main\",\"sha\":\"<分支頭>\"}'"
        STOP=1
        continue
    fi

    # 票的 `verify.files` 真的在這條分支上嗎(#587 那把尺)。
    # 票上寫著「案例在這幾個檔」而分支上沒有那幾個檔,代表**驗證者的交付沒有跟著進來**
    # —— 而 `verify.tags` 照樣會被閘門呼叫、照樣一個案例都選不到、照樣印一行綠。
    # 退出碼與其他拒收分開(4):呼叫者要分得出「票面沒填好」與「分支沒準備好」。
    out=$(python3 - "$tf" <<'VERIFIER_PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    ticket = {}
plan = ticket.get("verify") or {}
files = (plan.get("files") or []) if isinstance(plan, dict) else []
scope = ticket.get("in_scope") or []
infra = ("docs/", "board/", "scripts/control/")
product = [name for name in scope
           if isinstance(name, str)
           and not any(name == prefix[:-1] or name.startswith(prefix)
                       for prefix in infra)]
if ticket.get("needs_verifier") is not False and product and not files:
    print(product[0])
VERIFIER_PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 票 #$id 的 verify.files 是空的,但 in_scope 含產品碼:"
        echo "$out" | sed 's/^/land:   /'
        echo "land:   先派驗證者寫回歸案例;若這張票明確不需要,在票面設 needs_verifier=false。"
        STOP=1
        RC_REFUSE=4
        continue
    fi

    out=$(python3 - "$ROOT" "$b" "$tf" <<'VFILES_PY'
import json, subprocess, sys
root, branch, path = sys.argv[1:4]
try:
    with open(path, encoding="utf-8") as handle:
        plan = json.load(handle).get("verify") or {}
except (OSError, ValueError):
    plan = {}
files = (plan.get("files") or []) if isinstance(plan, dict) else []
for name in files:
    if not name:
        continue
    done = subprocess.run(["git", "-C", root, "cat-file", "-e",
                           "%s:%s" % (branch, name)], capture_output=True)
    if done.returncode != 0:
        print(name)
VFILES_PY
)
    if [ -n "$out" ]; then
        echo "land: $b —— 票 #$id 的 verify.files 有幾個不在這條分支上:"
        echo "$out" | sed 's/^/land:   /'
        echo "land:   驗證者的案例沒有跟著進來 —— 票的 tags 會被呼叫、卻一個案例都選不到。"
        echo "land:   先把 patch-verify 套進這條分支:sh scripts/apply.sh $id <patch> <patch-verify>"
        STOP=1
        RC_REFUSE=4
        continue
    fi
done
if [ -n "$STOP" ]; then
    echo "land: 一條都沒有落地 —— 上面那幾條先處理掉再來。"
    echo "land: (要嘛一起進去、要嘛都不進:跳掉一條會生出一個沒有人要求過的組合。)"
    ev land.refused --note "$*" --kv "stamp=$STAMP"
    status_done_all "$RC_REFUSE" "land 拒收"
    inbox_all "land 拒收(rc=$RC_REFUSE)" \
        "上面逐條寫了是哪一條不過:0 commit / base 過期 / 越界 / 覆核 / verify.files;產品票缺案例時先派驗證者" \
        "reports/t<票號>/${LAND_RUN:-} 的 status.json"
    exit "$RC_REFUSE"
fi

# ------------------------------------------------------------------- 第 5 步
# 副本根與 `auto-fix.sh` **同一種解析**(#38):環境變數 > `worktree_dir` > `../<主 repo>-wt`,
# 相對路徑以主 repo 根(`wtbase.sh` 的 `main_root`)拼。各算各的那一天,auto-fix 開副本的
# 地方與底下唸殘留清單的地方就不是同一個目錄,而「沒有殘留」與「看錯地方」長得一樣。
# 兩支共用 `scripts/wtbase.sh`(#46)。
. "$ROOT/scripts/wtbase.sh"
WTBASE=$(wtbase)
WT=$WTBASE/land-$STAMP
BR=land/$STAMP
mkdir -p "$WTBASE"
git -C "$ROOT" worktree add -q -b "$BR" "$WT" "$MAIN" || {
    ev land.fail --note "worktree add 失敗" --kv "stamp=$STAMP"
    status_done_all 2 "worktree add 失敗"
    exit 2
}
for b in "$@"; do
    if ! git -C "$WT" merge -q --no-ff "$b" -m "Merge $b (land $STAMP)"; then
        echo "land: 合 $b 時衝突 —— worktree 留在 $WT,自己看"
        ev land.fail --note "合 $b 時衝突" --kv "stamp=$STAMP"
        status_start_all
        status_phase_all merge 3 "串 $b 時衝突"
        status_done_all 3 "串接時衝突,一條都沒進主線"
        inbox_all "串接衝突" "自己解衝突:worktree 留著給你看" "$WT"
        exit 3
    fi
done
COUNT=$(git -C "$WT" log --oneline "$MAIN..HEAD" | wc -l | tr -d ' ')
SHA=$(git -C "$WT" rev-parse --short HEAD)
echo "land: $COUNT 個 commit 串好,跑全套 -> $WT/gate.log"
ev gate.start --kv mode=full --kv "sha=$SHA"
status_start_all
GATE_TICKET=""
if [ "$(echo "$IDS" | wc -w | tr -d ' ')" = "1" ]; then
    for i in $IDS; do GATE_TICKET=$i; done
fi
if ! (cd "$WT" && AC_ROOT="$ROOT" AC_GATE_TICKET="$GATE_TICKET" \
    AC_GATE_RUN_ID="$LAND_RUN-gate" AC_NO_INBOX=1 sh scripts/gate.sh --full --no-auto-fix); then
    echo "land: 全套紅,$MAIN 沒動;worktree 留在 $WT"
    echo "land: 紅榜逐條在 reports/t<票號>/<run_id>/status.json 的 failures(案例、檔、行、log、excerpt)"
    ev gate.fail --kv mode=full --kv "sha=$SHA"
    ev land.fail --note "閘門紅" --kv "stamp=$STAMP"
    status_phase_all gate 1 "全套紅"
    status_done_all 1 "閘門紅,沒有 merge、沒有 push"
    inbox_all "落地時全套紅" \
        "預設已自動派下一輪 worker;--no-auto-fix 才留給人處理" \
        "$WT/gate.log 與 reports/t<票號>/$LAND_RUN/status.json"
    auto_fix_all
    exit 1
fi
status_phase_all gate 0 "全套綠"
ev gate.pass --kv mode=full --kv "sha=$SHA"

# ------------------------------------------------------------------- 第 6 步
git -C "$ROOT" merge -q --ff-only "$BR" || {
    echo "land: $MAIN 在這中間動了,ff-only 進不去 —— worktree 留在 $WT"
    ev land.fail --note "ff-only 進不去" --kv "stamp=$STAMP"
    status_phase_all merge 1 "ff-only 進不去"
    status_done_all 1 "閘門綠了但沒合進主線"
    inbox_all "閘門綠了但沒合進主線" "主線在這中間動了 —— rebase 後重跑閘門" "$WT"
    exit 1
}
status_phase_all merge 0 "已 ff-only 合進 $MAIN"
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
    git -C "$ROOT" push -q origin "$MAIN" || {
        echo "land: 合進 $MAIN 了,但 push 沒成功 —— 自己推一次"
        ev land.fail --note "push 沒成功" --kv "stamp=$STAMP"
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
    --kv "sha=$(git -C "$ROOT" rev-parse --short HEAD)"
# **已合併、已關票**與**已合併、尚未關票**要分開講(2026-09-21 外部審查:能力表誤稱
# land 會關票)。關票仍是 `scripts/ticket.py close` 判:它自己再問一次「改動真的在主線嗎」
# 與 Done 的四條件(done_blockers),這裡只是換成 land 來打(#43,見檔頭)。
LANDED=$(git -C "$ROOT" rev-parse "$MAIN")
status_done_all 0 "已合併、已推上;關票由 ticket.py close --landed 判(結果看 stdout 與收件匣)"
# 每張票一筆 role=land 的成本(D-032):land 沒有 LLM,model 與 token 欄是 null,只有
# land.start 到 push 的秒數。寫在 close 之前;cost 不看 state、不動 state_version。
LAND_SECS=$(( $(date +%s) - LAND_T0 ))
for i in $IDS; do
    _attempt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("attempt") or 0)' \
        "$ROOT/$TICKETS/$i.json" 2>/dev/null || echo 0)
    python3 "$ROOT/scripts/ticket.py" cost "$i" --role land --round "$_attempt" --by land.sh \
        --wall-seconds "$LAND_SECS" >/dev/null 2>&1 \
        || echo "land: #$i 的 cost 寫不進票(不擋落地)" >&2
done
for i in $IDS; do
    CLOSE_OUT=$(python3 "$ROOT/scripts/ticket.py" close "$i" --landed "$LANDED" 2>&1)
    CLOSE_RC=$?
    if [ "$CLOSE_RC" -eq 0 ]; then
        echo "land: #$i 已合併、已關票(ticket.py close --landed $(echo "$LANDED" | cut -c1-12))"
        if [ -z "${AC_NO_INBOX:-}" ]; then
            python3 "$ROOT/scripts/inbox.py" done "$i" --landed "$LANDED" --by land.sh \
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
        python3 "$ROOT/scripts/inbox.py" post --ticket "$i" \
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
