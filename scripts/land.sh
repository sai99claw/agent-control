#!/bin/sh
# 落地:把幾條綠的分支**依序**串起來、跑一次全套、綠了才 fast-forward 主線。
# 六步照 `docs/WORKFLOW.md` §落地,每一步發事件。
#
#   sh scripts/land.sh t7-land-refuses t9-event-kinds ...   # 依給的順序合
#
# 這一支**沒有判斷**(docs/ROLES.md:落地器是程式,排程器才是提案)。它只會拒絕:
# 0 commit、票對不上、`base_sha` 過期、寫入範圍越界、閘門紅。要它放寬的時候,
# 放寬的是規矩,不是這支腳本。
#
# 做法:從主線開一個 land-<時間> worktree,逐條 --no-ff merge(衝突就停、留著
# worktree 給人看),在那個 worktree 跑 `scripts/gate.sh --full`;綠才
# `git merge --ff-only` 進主線並 push,worktree 收掉。主線的工作樹全程沒有人動、
# 沒有人在上面跑測試,所以其他票的分支閘門可以同時進行。
set -u
[ $# -ge 1 ] || { echo "land: 給我至少一條分支"; exit 2; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)

# 設定走 `board/config.json`,不寫死 —— 這支腳本要能被別的專案原樣拿走。
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

# 事件發不出去要出聲,但不擋落地:發不出去的那一刻正是最需要紀錄的那一刻,而
# 「靜靜地沒發」與「發了」在控制台上長得一樣(D-003)。
ev() {
    python3 "$ROOT/scripts/event.py" emit "$@" >/dev/null \
        || echo "land: 事件發不出去($*)" >&2
}

STAMP=$(date +%Y%m%d-%H%M%S)
ev land.start --note "$*" --kv "stamp=$STAMP"

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
for b in "$@"; do
    if ! git -C "$ROOT" rev-parse -q --verify "$b^{commit}" >/dev/null; then
        echo "land: $b —— 沒有這條分支(名字打錯了?)"
        STOP=1
        continue
    fi
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

    # 分支名 `t<票號>-…` 對到票。對不到就停:沒有票的東西不落地(CLAUDE.md)。
    id=$(echo "$b" | sed -n 's/^t\([0-9][0-9]*\)[-.].*$/\1/p')
    if [ -z "$id" ]; then
        echo "land: $b —— 分支名對不到票(要 t<票號>-… 的形狀),沒有票的工作不落地"
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
    if ! git -C "$ROOT" merge-base --is-ancestor "$base" "$MAIN" 2>/dev/null; then
        echo "land: $b —— 票 #$id 的 base_sha $base 已經不是 $MAIN 的祖先"
        echo "land:   閘門的綠是對那個 base 說的,主線已經走遠。先 rebase 再重跑閘門。"
        STOP=1
        continue
    fi

    # 寫入範圍。排程器就是拿這一格判能不能平行的(docs/DESIGN.md §10),所以越界
    # 不只是「改了不該改的檔」,是**排程當時算出來的那張衝突圖已經不成立**。
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
done
if [ -n "$STOP" ]; then
    echo "land: 一條都沒有落地 —— 上面那幾條先處理掉再來。"
    echo "land: (要嘛一起進去、要嘛都不進:跳掉一條會生出一個沒有人要求過的組合。)"
    ev land.refused --note "$*" --kv "stamp=$STAMP"
    exit 2
fi

# ------------------------------------------------------------------- 第 5 步
WTBASE=${AC_WORKTREE_DIR:-$ROOT/../$(basename "$ROOT")-wt}
WT=$WTBASE/land-$STAMP
BR=land/$STAMP
mkdir -p "$WTBASE"
git -C "$ROOT" worktree add -q -b "$BR" "$WT" "$MAIN" || {
    ev land.fail --note "worktree add 失敗" --kv "stamp=$STAMP"
    exit 2
}
for b in "$@"; do
    if ! git -C "$WT" merge -q --no-ff "$b" -m "Merge $b (land $STAMP)"; then
        echo "land: 合 $b 時衝突 —— worktree 留在 $WT,自己看"
        ev land.fail --note "合 $b 時衝突" --kv "stamp=$STAMP"
        exit 3
    fi
done
COUNT=$(git -C "$WT" log --oneline "$MAIN..HEAD" | wc -l | tr -d ' ')
SHA=$(git -C "$WT" rev-parse --short HEAD)
echo "land: $COUNT 個 commit 串好,跑全套 -> $WT/gate.log"
ev gate.start --kv mode=full --kv "sha=$SHA"
if ! (cd "$WT" && sh scripts/gate.sh --full); then
    echo "land: 全套紅,$MAIN 沒動;worktree 留在 $WT"
    ev gate.fail --kv mode=full --kv "sha=$SHA"
    ev land.fail --note "閘門紅" --kv "stamp=$STAMP"
    exit 1
fi
ev gate.pass --kv mode=full --kv "sha=$SHA"

# ------------------------------------------------------------------- 第 6 步
git -C "$ROOT" merge -q --ff-only "$BR" || {
    echo "land: $MAIN 在這中間動了,ff-only 進不去 —— worktree 留在 $WT"
    ev land.fail --note "ff-only 進不去" --kv "stamp=$STAMP"
    exit 1
}
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
    git -C "$ROOT" push -q origin "$MAIN" || {
        echo "land: 合進 $MAIN 了,但 push 沒成功 —— 自己推一次"
        ev land.fail --note "push 沒成功" --kv "stamp=$STAMP"
        exit 1
    }
fi
echo "land: $MAIN -> $(git -C "$ROOT" rev-parse --short HEAD) 已推上"
ev land.pass --note "$*" --kv "stamp=$STAMP" \
    --kv "sha=$(git -C "$ROOT" rev-parse --short HEAD)"
git -C "$ROOT" worktree remove "$WT" && git -C "$ROOT" branch -q -D "$BR"
