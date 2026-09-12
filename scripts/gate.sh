#!/bin/sh
# 閘門:三層介面,專案自己填對照表 — `docs/WORKFLOW.md` §閘門。
#
#   sh scripts/gate.sh --branch          # 這條分支改動的檔對應到的模組
#   sh scripts/gate.sh --base            # 基礎組:不管改了什麼都要過的那幾支
#   sh scripts/gate.sh --branch --base   # 票收尾
#   sh scripts/gate.sh --full            # 全套(land.sh 跑的就是這一發)
#   sh scripts/gate.sh <檔> <檔> …       # 指定檔
#
# **這一份是本 repo 自己的實作。** 別的專案把 `scripts/gate.sh` 換成自己的
# (範本見 `scripts/gate.example.sh`),介面不變 —— `land.sh` 只認 `--full` 的
# 退出碼,不認它跑了什麼。
#
# ## 對照表為什麼寫死在這裡
# 誰改了對照表就是在改「哪些測試守哪些檔」這件事本身,要進 code review。自動推導
# 出來的對照表沒有這一層。
#
# ## 對不到任何模組 → 出聲,而且非零
# 前一個專案 2026-09-10 的第三次事故(那張票的原話:「這一條修的不是那三個缺口,
# 是『缺口會靜悄悄』這件事」):對照表漏了一格時,舊版印一行
# 「沒有對到任何測試模組」然後**退出碼 0** —— **印一行、看起來像跑完了**,而那正是
# 「沒有東西可跑」與「跑完了都過」長得一樣的母題(`docs/DISPATCH-TEMPLATE.md` §5.5)。
# 缺口三次都是別張票順手撞到才發現的。
#
# 所以這裡:**指名對不到的那幾個檔,退出碼非零**。混合的情況(一張票同時改了對得到
# 與對不到的檔)**照跑對得到的那些,跑完仍然非零** —— 跑掉的測試是真的證據,不該丟;
# 而那幾個沒人守的檔要當場處理:要嘛補對照表,要嘛在票裡寫明為什麼它們不需要測試。
# `--base` 不受這一條影響(它本來就是「不管改了什麼都跑這一組」)。
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=${AC_GATE_LOG:-$ROOT/gate.log}
MAIN=main

mods=""
unmapped=""
add() { for m in "$@"; do case " $mods " in *" $m "*) ;; *) mods="$mods $m" ;; esac; done; }
miss() { unmapped="$unmapped $1"; }

# 基礎組:契約層。票、事件、以及「這個 repo 裡不准出現專案名」那一條。
BASE="test_ticket test_event test_memory test_no_project_names"

# 檔 → 測試模組。越特定的規則越前面。
map() {
    f=$1
    case "$f" in
        tests/test_*.py) add "$(basename "$f" .py)" ;;
        # 沙盒共用給每一支用,所以動它就是動全部。
        tests/control_harness.py) add test_ticket test_event test_memory test_land \
                                      test_gate test_heartbeat test_new_session \
                                      test_board test_check_stale test_no_project_names ;;
        # `.gitignore` 決定「不准出現專案名」那一掃看得到哪些檔。
        .gitignore) add test_no_project_names ;;
        # ticket.py import event.py:動 event 的人要連票那一側一起跑。
        scripts/event.py) add test_event test_ticket test_land ;;
        scripts/ticket.py) add test_ticket test_land ;;
        scripts/land.sh) add test_land ;;
        scripts/gate.sh|scripts/gate.example.sh) add test_gate ;;
        scripts/heartbeat.sh) add test_heartbeat ;;
        scripts/new-session.sh) add test_new_session ;;
        scripts/memory.py) add test_memory ;;
        # 記憶檔既受上限守衛管(test_memory),也在「不准出現專案名」那一掃裡。
        memory/model/*.md) add test_memory test_no_project_names ;;
        board/board.py) add test_board ;;
        # 設定檔:看板讀它,事件與落地也讀它(埠、票目錄、事件檔、租約)。
        board/config.json) add test_board test_event test_heartbeat test_memory ;;
        code-map/check-stale.py|code-map/cards/*) add test_check_stale ;;
        tickets/*.json|tickets/SCHEMA.md) add test_ticket ;;
        docs/DISPATCH-TEMPLATE.md) add test_dispatch_template test_no_project_names ;;
        # 文件**有**一支測試真的讀它們:那條「不准出現專案名 / 絕對路徑」的守衛
        # 掃的就是整個 repo。所以這一格不是硬塞,是實話。
        *.md) add test_no_project_names ;;
        *) miss "$f" ;;
    esac
}

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

if [ "$want_full" -eq 1 ]; then
    echo "gate: 全套 —— python3 -m unittest discover -s tests"
    # 判綠先寫檔再讀退出碼,不用 `cmd | tail`:管線的退出碼是右邊那一支的
    # (`false | tail` 是 0),而那會讓「根本沒跑起來」靜靜判成綠。
    ( cd "$ROOT" && python3 -m unittest discover -s tests -v ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    [ "$rc" -eq 0 ] || echo "gate: 紅了,看 $LOG"
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
    echo "gate: python3 -m unittest$(echo " $mods" | sed 's/ *$//')"
    ( cd "$ROOT/tests" && python3 -m unittest $mods ) > "$LOG" 2>&1
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    [ "$rc" -eq 0 ] || echo "gate: 紅了,看 $LOG"
fi

if [ -n "$unmapped" ]; then
    echo "gate: 這幾個改動檔對不到任何測試模組 —— 沒有人守著它們:"
    for f in $unmapped; do echo "gate:   $f"; done
    echo "gate: 要嘛補 scripts/gate.sh 的對照表,要嘛在票裡寫明為什麼它們不需要測試。"
    exit 3
fi
if [ -z "$mods" ]; then
    echo "gate: 沒有給我任何要跑的東西(--branch 沒有改動檔?要跑基礎組加 --base)"
    exit 2
fi
exit $rc
