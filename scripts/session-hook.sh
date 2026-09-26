#!/bin/sh
# Claude Code 的 SessionStart hook:主線開場那一頁**不靠自律** —— `.claude/settings.json`
# 叫這一支,這一支再叫 `new-session.sh main <model>`(`docs/SESSION-START.md` §主線)。
#
#   echo '{"source":"startup","cwd":"<根>","hook_event_name":"SessionStart"}' \
#       | sh scripts/session-hook.sh
#
# 為什麼要有這一支:開場那一頁以前要主線自己記得跑,而**漏跑的那一個 session 與跑過的
# 長得一樣** —— 沒有任何東西把 new-session.sh 叫起來,整段開場就被跳過,而且沒人發現。
# 不靠自律就生效的只有 CLAUDE.md、記憶索引與 hook;hook 的 stdout 在 SessionStart 會
# 進上下文(code.claude.com/docs/en/hooks),所以那一頁在第一個 prompt 之前就在。
#
# ## 副本不觸發:rc 0、一個位元組都不印、一筆事件都不發
# 三條各自成立就停(`AC_SESSION_HOOK=0` / `AC_ROLE` 設了且不是 main / cwd 有一層叫
# `worktree_dir` 的 basename)。短命 worker 的 session 開在副本裡,它要的是派工文,
# 不是主線那一頁 —— 印進去是白花它的上下文,發一筆 start 是在控制台上假造一個主線。
# 不用 `claude --bare`:它連 CLAUDE.md 都跳過。
#
# ## startup / clear 才發 session.start
# resume / compact / fork 只重印那一頁(`--no-event`)。每次 compact 多一筆 start 的話,
# 有 start 沒 end 的那幾筆在 heartbeat 看來就是死在半路的 session。
#
# ## model:`AC_MODEL` > board/config.json 的 `routing.main` > unknown
# 缺的時候印一行警告,不靜默 —— 一筆 model=unknown 的事件與一筆量過的長得一樣。
#
# ## 上限 16384 bytes
# 超過就截掉**中間**:開頭與「接下來要讀的」那一段留著(那一段是這一頁的目的),
# 最後一行說原始多大、全文怎麼看。
set -u
[ "${AC_SESSION_HOOK:-}" = "0" ] && exit 0
[ -n "${AC_ROLE:-}" ] && [ "$AC_ROLE" != "main" ] && exit 0

# 根與同伴腳本的找法與 heartbeat.sh 同一種:同步到專案後這幾支住在 scripts/control/。
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
AC=$(cd "$(dirname "$0")" && pwd)
LIMIT=16384

# stdin 是 hook 的 JSON;手動在終端機跑時沒有,不要卡在讀 stdin。
if [ -t 0 ]; then INPUT=""; else INPUT=$(cat); fi

PLAN=$(python3 - "$ROOT" "$INPUT" "$PWD" "${AC_MODEL:-}" <<'PY'
import json
import os
import shlex
import sys

root, raw, pwd, env_model = sys.argv[1:5]
try:
    hook = json.loads(raw) if raw.strip() else {}
except ValueError:
    hook = {}
hook = hook if isinstance(hook, dict) else {}
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
conf = conf if isinstance(conf, dict) else {}

# 預設與 apply.sh / auto-fix.sh / land.sh 同一格:`../<repo>-wt`。比的是 basename ——
# 副本自己的根就在那個目錄底下,以根解析出來的完整路徑在副本裡指的是另一個地方。
worktree = conf.get("worktree_dir") or "../%s-wt" % os.path.basename(root)
guard = os.path.basename(os.path.normpath(worktree))
cwd = hook.get("cwd") or pwd
if guard and guard in os.path.abspath(cwd).split(os.sep):
    print("MODE=skip")
    sys.exit(0)

mode = "emit" if hook.get("source") in ("startup", "clear") else "quiet"
routing = conf.get("routing") if isinstance(conf.get("routing"), dict) else {}
model = env_model or routing.get("main") or ""
warn = ""
if not model:
    warn = ("session-hook: board/config.json 沒有 routing.main、也沒有設 AC_MODEL —— "
            "這個 session 記成 model=unknown;補 routing.main(或 export AC_MODEL=<模型>)。")
print("MODE=%s MODEL=%s WARN=%s" % (mode, shlex.quote(model or "unknown"), shlex.quote(warn)))
PY
)
MODE=""; MODEL=unknown; WARN=""
eval "$PLAN"
[ "$MODE" = "skip" ] && exit 0

FLAG=""
[ "$MODE" = "emit" ] || FLAG=--no-event
AGAIN="sh ${AC#"$ROOT"/}/new-session.sh main $MODEL --no-event"

PAGE=$(
    [ -z "$WARN" ] || echo "$WARN"
    # shellcheck disable=SC2086
    AC_ROOT=$ROOT sh "$AC/new-session.sh" main "$MODEL" $FLAG 2>&1
    rc=$?
    [ "$rc" -eq 0 ] || echo "session-hook: new-session.sh 退出碼 $rc —— 上面那一頁可能缺段;重跑:$AGAIN"
)
printf '%s\n' "$PAGE" | python3 -c '
import sys

limit, again = int(sys.argv[1]), sys.argv[2]
data = sys.stdin.buffer.read()
if len(data) <= limit:
    sys.stdout.buffer.write(data)
    sys.exit(0)
notice = ("session-hook: 截斷 —— 原始 %d bytes 超過上限 %d,中間略去;全文:%s\n"
          % (len(data), limit, again)).encode("utf-8")
gap = "session-hook: …(中間略去,見最後一行)…\n".encode("utf-8")
at = data.rfind("── 接下來要讀的".encode("utf-8"))
tail = data[data.rfind(b"\n", 0, at) + 1:] if at >= 0 else b""
if len(tail) + len(notice) + len(gap) > limit // 2:
    tail = b""
head = data[:limit - len(tail) - len(notice) - len(gap)]
head = head[:head.rfind(b"\n") + 1]
sys.stdout.buffer.write(head + gap + tail + notice)
' "$LIMIT" "$AGAIN"
exit 0
