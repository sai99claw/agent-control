#!/bin/sh
# 心跳:誰死在半路、誰還活著,以及有沒有 land 的殘骸。
#
#   sh scripts/heartbeat.sh               # 只讀
#   sh scripts/heartbeat.sh --reconcile   # 把死掉的那幾輪對回事實(#117;只動 dead awaiting-takeover)
#
# 主線 session 開頭跑這一支(`docs/SESSION-START.md`)。它回答一個問題:
# **上一個 session / land / worker 是不是死在半路了?**
#
# 為什麼需要它:協調者自己會撞額度(`docs/DESIGN.md` 主線審稿第 3 點 —— 一天內三個
# 模型都撞過),而**一個撞了額度靜停的 session,與一個正在思考的 session,在事件檔上
# 長得一模一樣** —— 兩者都是「有 start、沒有 end」。#117 起 start 事件帶 driver 的 pid 與
# boot_id:讀 /proc 就分得出 alive / dead(不發任何訊號,判準與 schedule.py 同一份 liveness.py);
# 沒帶的舊列才退回時間(`board/config.json` 的 `lease_seconds`),記成 unknown。
#
# 退出碼:0 = 沒有要處理的;1 = 有(要人處理,不是錯誤);2 = 找不到專案根 / 參數錯。
#
# 本體在 heartbeat.py(判決、--reconcile 的每一步寫在那裡的檔頭)。
set -u
# 專案根不從 `$0` 來(D-071 C2,#159):`AC_ROOT`,否則 cwd 的主 repo 根,否則 rc 2 ——
# 規則只有一份,在 `wtbase.sh`。控制腳本自己住的目錄(A 的 clone)才從 `$0` 算;
# 同伴腳本一律從這裡叫,不寫死 $ROOT/scripts。
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_CONTROL_DIR=$AC
. "$AC/wtbase.sh"
ROOT=$(ac_root heartbeat) || exit 2
export AC_ROOT=$ROOT

# 副本根與 auto-fix / land 同一條規則(`wtbase.sh`):--reconcile 要找那一輪的 worker.pid。
cfg() {
    python3 - "$ROOT" "$1" "$2" <<'PY'
import json, os, sys
try:
    with open(os.path.join(sys.argv[1], "board", "config.json"), encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
print(data.get(sys.argv[2]) or sys.argv[3])
PY
}
AC_WTBASE=$(wtbase) python3 "$AC/heartbeat.py" "$@"
