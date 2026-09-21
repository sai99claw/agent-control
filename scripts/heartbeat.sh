#!/bin/sh
# 心跳:誰的租約到期了還沒回來,以及有沒有 land 的殘骸。
#
#   sh scripts/heartbeat.sh
#
# 主線 session 開頭跑這一支(`docs/SESSION-START.md`)。它回答一個問題:
# **上一個 session / land / worker 是不是死在半路了?**
#
# 為什麼需要它:協調者自己會撞額度(`docs/DESIGN.md` 主線審稿第 3 點 —— 一天內三個
# 模型都撞過),而**一個撞了額度靜停的 session,與一個正在思考的 session,在事件檔上
# 長得一模一樣** —— 兩者都是「有 start、沒有 end」。分辨它們的唯一辦法是時間:
# `board/config.json` 的 `lease_seconds`。
#
# 退出碼:0 = 沒有到期的租約也沒有殘骸;1 = 有(要人處理,不是錯誤)。
#
# JSON 交給 python3(POSIX sh 沒有解析器,而自己用 sed 剖 JSON 是在測自己寫的
# 剖析器 —— 同 `docs/DISPATCH-TEMPLATE.md` §5.6「不要造一個自己寫的模擬器」)。
set -u
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

python3 - "$ROOT" <<'PY'
import json
import os
import subprocess
import sys
from datetime import datetime

root = sys.argv[1]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import event  # noqa: E402

conf = event.config(root)
leases = conf.get("lease_seconds") or {}
DEFAULT_LEASE = 7200

# 開始 → 什麼算它回來了。land 用 stamp 配對(同一輪落地的四種結局);session 用
# pid;attempt 用票號加 attempt。**配對的鍵要選得夠細** —— 用「最近一筆 end」去
# 配所有 start,兩條同時在跑的線會互相把對方銷掉,而那看起來就像大家都回來了。
CLOSERS = {
    "session.start": ("session.end",),
    "land.start": ("land.pass", "land.fail", "land.refused"),
    "ticket.attempt.start": ("ticket.attempt.done", "ticket.attempt.failed"),
}
LEASE_KEY = {"land.start": "land", "ticket.attempt.start": "worker"}


def key_of(row):
    kind = row.get("kind", "")
    if kind.startswith("land."):
        return ("land", str(row.get("stamp", "")))
    if kind.startswith("ticket.attempt."):
        return ("attempt", str(row.get("ticket", "")), str(row.get("attempt", "")))
    return ("session", str(row.get("pid", "")), str(row.get("role", "")))


def lease_for(row):
    kind = row.get("kind", "")
    if kind in LEASE_KEY:
        return int(leases.get(LEASE_KEY[kind]) or DEFAULT_LEASE)
    role = str(row.get("role") or "")
    return int(leases.get(role) or leases.get("worker") or DEFAULT_LEASE)


rows = event.read_events(root)
closed = set()
for row in rows:
    for starter, enders in CLOSERS.items():
        if row.get("kind") in enders:
            closed.add(key_of(row))
now = datetime.now().astimezone()
stale = []
for row in rows:
    if row.get("kind") not in CLOSERS:
        continue
    if key_of(row) in closed:
        continue
    when = row.get("ts", "")
    try:
        started = datetime.fromisoformat(when)
    except ValueError:
        continue
    if started.tzinfo is None:
        started = started.astimezone()
    age = (now - started).total_seconds()
    lease = lease_for(row)
    if age > lease:
        stale.append((row, int(age), lease))

if stale:
    print("heartbeat: 租約到期還沒回來的:")
    for row, age, lease in stale:
        who = " ".join(filter(None, [
            str(row.get("kind")),
            "#%s" % row["ticket"] if row.get("ticket") else "",
            "role=%s" % row["role"] if row.get("role") else "",
            "model=%s" % row["model"] if row.get("model") else "",
            "pid=%s" % row["pid"] if row.get("pid") else "",
            "stamp=%s" % row["stamp"] if row.get("stamp") else ""]))
        print("heartbeat:   %s —— %s,已經 %d 秒(租約 %d 秒)"
              % (row.get("ts", "?"), who, age, lease))
    print("heartbeat: 處置:確認那個 session 真的沒了,再把票的狀態對回事實"
          "(scripts/ticket.py set … / verify)。")
else:
    print("heartbeat: 沒有到期的租約")

# land 的殘骸:閘門紅或衝突時 worktree 是**故意留著給人看的**,所以「有殘骸」不是
# 錯誤,是一件還沒有人處理完的事。分支名認得出來(`land/<時間>`),不必知道它在哪。
done = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                      capture_output=True, text=True)
leftovers = []
path = ""
for line in done.stdout.splitlines():
    if line.startswith("worktree "):
        path = line[len("worktree "):]
    elif line.startswith("branch refs/heads/land/"):
        leftovers.append((line[len("branch refs/heads/"):], path))
if leftovers:
    print("heartbeat: land 的 worktree 還留著:")
    for branch, where in leftovers:
        print("heartbeat:   %s -> %s" % (branch, where))
    print("heartbeat: 處置:看完 gate.log 再 `git worktree remove` 並刪掉那條分支。")
else:
    print("heartbeat: 沒有 land 殘骸")

sys.exit(1 if (stale or leftovers) else 0)
PY
