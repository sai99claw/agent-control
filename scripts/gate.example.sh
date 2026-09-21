#!/bin/sh
# **範例:專案怎麼寫自己的 `scripts/gate.sh`。** 這一支不會被任何東西呼叫。
#
# `land.sh` 只認 `scripts/gate.sh --full` 的退出碼,不認它跑了什麼 —— 所以介面固定、
# 內容由專案自己填。三層:
#
#   --branch   改動檔對應到的模組(改一個錯字不該跑全套)
#   --base     基礎組:不管改了什麼都要過的那幾支(不變量、授權、入口守衛、契約)
#   --full     全套(落地前跑的就是這一發)
#   --ticket <票號>   多寫一份狀態檔、多跑票的回歸(照抄的人常常漏這一格)
#
# 照抄這一支,改三個地方:BASE、map() 的對照表、最底下真正跑測試的那兩行。
#
# 5️⃣ `--ticket`:**照抄舊版的人拿不到新狀態功能**(2026-09-21 外部審查)。少了這一格,
#    專案的 gate 收到 `--ticket 7` 會回「不認得」而整個閘門退出碼 2 —— 而 2 與「紅了」
#    在呼叫者眼裡長得很像。所以這一支把它一起示範掉。
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
TICKET=${AC_GATE_TICKET:-}
ARGC=$#
while [ $# -gt 0 ]; do
    case "$1" in
        --base) want_base=1 ;;
        --branch) want_branch=1 ;;
        --full) want_full=1 ;;
        --ticket)
            shift
            [ $# -ge 1 ] || { echo "gate: --ticket 後面要票號" >&2; exit 2; }
            TICKET=$1 ;;
        --*) echo "gate: 不認得 $1(--branch / --base / --full / --ticket <票號>)" >&2; exit 2 ;;
        *) map "$1" ;;
    esac
    shift
done
[ "$ARGC" -ge 1 ] || { echo "gate: 要 --branch / --base / --full 或一串檔名" >&2; exit 2; }

# 狀態檔與票的回歸。`AC` 這一組腳本在哪由專案填(同一顆 repo 就是 $ROOT/scripts)。
AC=${AC_CONTROL_DIR:-$ROOT/scripts}
RUN_ID=${AC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
# 寫不出來要出聲,但**不擋閘門**:讓一個紀錄問題升級成一個交付問題是划不來的。
status_start() {
    [ -n "$TICKET" ] || return 0
    python3 "$AC/status.py" start --ticket "$TICKET" --kind gate --run-id "$RUN_ID" \
        --sha "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '')" \
        --repro "sh scripts/gate.sh $*" --cwd "$ROOT" >/dev/null 2>&1 \
        || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
}
status_done() {   # $1 = rc
    [ -n "$TICKET" ] || return 0
    python3 "$AC/status.py" done --ticket "$TICKET" --kind gate --run-id "$RUN_ID" \
        --rc "$1" --log "$LOG" >/dev/null 2>&1 \
        || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
}

# 4️⃣ 真的跑測試的那兩行。**判綠先寫檔再讀退出碼**,不用 `cmd | tail`:
#    管線的退出碼是右邊那一支的(`false | tail` 是 0)。
if [ "$want_full" -eq 1 ]; then
    ( cd "$ROOT/$TESTS" && python3 -m unittest discover -s . -p "test_*.py" ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    status_done "$rc"
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
    status_start
    ( cd "$ROOT/$TESTS" && python3 -m unittest $mods ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    # 票的回歸:`verify.tags` 併 `tags` 拿去叫執行器。**不跑等於沒有守著這張票**
    # —— 「驗證者交了案例」與「案例真的在守這張票」在閘門的綠上長得一樣。
    if [ -n "$TICKET" ] && [ -f "$AC/verify.py" ]; then
        tags=$(python3 - "$ROOT" "$TICKET" <<'PY' 2>/dev/null || true
import json, os, sys
root, ident = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "scripts"))
import event
try:
    with open(os.path.join(event.tickets_dir(root), "%s.json" % ident),
              encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)
plan = data.get("verify") if isinstance(data.get("verify"), dict) else {}
out = []
for tag in list(data.get("tags") or []) + list(plan.get("tags") or []):
    if tag and tag not in out:
        out.append(str(tag))
print(" ".join(out))
PY
)
        if [ -n "$tags" ]; then
            targs=""
            for t in $tags; do targs="$targs --tag $t"; done
            # shellcheck disable=SC2086
            ( cd "$ROOT" && python3 "$AC/verify.py" $targs ) > "$LOG.verify" 2>&1
            vrc=$?
            grep -aE "^Ran |^OK|^FAILED|^verify: " "$LOG.verify" || true
            [ "$rc" -ne 0 ] || rc=$vrc
        else
            echo "gate: 票 #$TICKET 沒有宣告 verify.tags —— 這一輪沒有回歸可跑"
        fi
    fi
    status_done "$rc"
fi
if [ -n "$unmapped" ]; then
    echo "gate: 這幾個改動檔對不到任何測試模組 —— 沒有人守著它們:"
    for f in $unmapped; do echo "gate:   $f"; done
    exit 3
fi
if [ -z "$mods" ]; then
    # 「這一輪根本沒有東西跑」也是一個結果:狀態檔留在 running 與「還在跑」長得一樣。
    status_start
    status_done 2
    echo "gate: 沒有給我任何要跑的東西"
    exit 2
fi
exit $rc
