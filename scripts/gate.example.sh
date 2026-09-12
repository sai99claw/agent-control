#!/bin/sh
# **範例:專案怎麼寫自己的 `scripts/gate.sh`。** 這一支不會被任何東西呼叫。
#
# `land.sh` 只認 `scripts/gate.sh --full` 的退出碼,不認它跑了什麼 —— 所以介面固定、
# 內容由專案自己填。三層:
#
#   --branch   改動檔對應到的模組(改一個錯字不該跑全套)
#   --base     基礎組:不管改了什麼都要過的那幾支(不變量、授權、入口守衛、契約)
#   --full     全套(落地前跑的就是這一發)
#
# 照抄這一支,改三個地方:BASE、map() 的對照表、最底下真正跑測試的那兩行。
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=${AC_GATE_LOG:-$ROOT/gate.log}
MAIN=main

# 0️⃣ 專案自己的目錄名。占位符寫成變數而不是 `<…>`,是為了讓這一支**自己的語法是對的**
#    —— 一份 `sh -n` 過不了的範本,照抄的人第一件事就是修語法,不是讀理由。
TESTS=tests          # 測試住哪
DATA=demo            # 資料層 / 後端
WEBDIR=demo/web      # 前端
SCRIPTSDIR=scripts   # shell 腳本

# 1️⃣ 基礎組:合併前一定要過的那幾支。挑「壞了就是資料錯 / 權限錯」的,不是挑跑得快的。
BASE="test_ledger test_invariants test_authz test_api"

mods=""
unmapped=""
add() { for m in "$@"; do case " $mods " in *" $m "*) ;; *) mods="$mods $m" ;; esac; done; }
miss() { unmapped="$unmapped $1"; }

# 2️⃣ 對照表:檔 → 測試模組。越特定的規則越前面。
#
# **故意寫死在這裡而不是自動推導**:誰改了這張表,就是在改「哪些測試守哪些檔」這件事
# 本身,要進 code review。自動推導出來的表沒有這一層,而且它會在依賴關係變模糊的那天
# 靜靜地少守一個檔。
#
# 每一格都要答得出「這幾支各問一件什麼事」。答不出來的那一格,多半是硬塞的。
map() {
    f=$1
    case "$f" in
        "$TESTS"/test_*.py)                    add "$(basename "$f" .py)" ;;
        "$DATA"/schema.*|"$DATA"/store.*)      add test_migration test_store ;;
        "$WEBDIR"/*.js|"$WEBDIR"/*.css)        add test_web test_web_states ;;
        "$SCRIPTSDIR"/*.sh)                    add test_platform_scripts ;;
        *.md)                                  miss "$f" ;;   # ← 見下面那一段
        *)                                     miss "$f" ;;
    esac
}

# 3️⃣ 「對不到任何模組」怎麼辦 —— **出聲,而且非零**。
#
# 這一格不是照抄題。前一個專案踩了三次:對照表漏了一格時,腳本印一行「沒有對到任何
# 測試模組」然後退出碼 0 —— 印一行、看起來像跑完了,而三次都是別張票順手撞到才發現。
# 「沒有東西可跑」與「跑完了都過」長得一樣(`docs/DISPATCH-TEMPLATE.md` §5.5)。
#
# 先誠實回答一個問題:**只改文件時,這個 repo 裡有沒有測試真的讀那些文件?**
# - 有(例如一支掃全 repo 的守衛、一支讀術語表的測試)→ 就把 `*.md` 對到它,那是實話。
# - 沒有 → 讓它跑空集合,但**明說**「文件變更不對應任何測試,全套仍會擋」。
#   那也比沉默好。**不要為了讓它有東西可跑而硬塞不相關的模組** —— 那會讓每一支文件
#   分支多跑兩分鐘,換到零。

want_base=0; want_branch=0; want_full=0
for a in "$@"; do
    case "$a" in
        --base) want_base=1 ;;
        --branch) want_branch=1 ;;
        --full) want_full=1 ;;
        --*) echo "gate: 不認得 $a(--branch / --base / --full)" >&2; exit 2 ;;
        *) map "$a" ;;
    esac
done
[ $# -ge 1 ] || { echo "gate: 要 --branch / --base / --full 或一串檔名" >&2; exit 2; }

# 4️⃣ 真的跑測試的那兩行。**判綠先寫檔再讀退出碼**,不用 `cmd | tail`:
#    管線的退出碼是右邊那一支的(`false | tail` 是 0)。
if [ "$want_full" -eq 1 ]; then
    ( cd "$ROOT/$TESTS" && python3 -m unittest discover -s . -p "test_*.py" ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    exit $rc
fi

if [ "$want_branch" -eq 1 ]; then
    for f in $(git -C "$ROOT" diff --name-only "$MAIN...HEAD"; \
               git -C "$ROOT" diff --name-only HEAD; \
               git -C "$ROOT" ls-files --others --exclude-standard); do
        map "$f"
    done
fi
[ "$want_base" -eq 1 ] && add $BASE

mods=$(echo "$mods" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ')
rc=0
if [ -n "$mods" ]; then
    ( cd "$ROOT/$TESTS" && python3 -m unittest $mods ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
fi
if [ -n "$unmapped" ]; then
    echo "gate: 這幾個改動檔對不到任何測試模組 —— 沒有人守著它們:"
    for f in $unmapped; do echo "gate:   $f"; done
    exit 3
fi
[ -n "$mods" ] || { echo "gate: 沒有給我任何要跑的東西"; exit 2; }
exit $rc
