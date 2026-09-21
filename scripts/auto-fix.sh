#!/bin/sh
# 回歸紅了自動派**新** worker — `docs/WORKFLOW.md` §回歸紅了之後(D-010、D-015)。
#
#   sh scripts/auto-fix.sh <票號>              # 讀最新狀態檔,紅就派下一輪
#   sh scripts/auto-fix.sh <票號> --dry-run    # 只印派工文,不起 worker
#
# 也可以掛在閘門與落地後面:`sh scripts/gate.sh --branch --ticket 7 --auto-fix`、
# `sh scripts/land.sh t7-x --auto-fix`。
#
# ## 為什麼不叫醒舊的 worker
# 它醒來一次 = 累積的整份上下文重送一輪(D-010)。所以每一輪都是**新的** worker,
# 而它手上只有一份檔:狀態檔的 `repair_context`(base_sha、票面快照、副本、patch 路徑
# 與雜湊、第幾輪、上一輪的 EVIDENCE、重現指令)。少一格,它就得回頭翻對話或猜檔案位置。
#
# ## 三種停下來(每一種都寫一則 inbox,不是印一行就算)
# 1. worker 說**票寫錯 / 需要裁示** —— 它在 EVIDENCE 裡寫一行 `OBJECTION: <類別> <理由>`;
#    這一支把它記成票的 `objections[]`,票轉 Blocked、owner=main,發 `decision.asked`。
# 2. **三輪仍紅** —— `ticket.py round` 在第 `retry_limit+1` 輪把票轉 Blocked、owner=main。
# 3. **failures 沒有歸因** —— rc 非零卻一條紅都解析不出來(log 壞了、跑都沒跑起來)。
#    這一種**最危險**:它看起來像「沒有紅」,而自動派下去的 worker 會拿著一份空紅榜
#    去猜。所以這裡當場停,不派。
#
# **覆核不自動**(D-010):綠了之後票轉 `InReview` 並寫 inbox 停在那裡,等主線讀 patch
# 記 `review`。這一支不碰 `review` 那一格。
#
# ## 退出碼
#   0 綠了(停在等覆核)   1 三輪耗盡仍紅   2 用法 / 沒有狀態檔可讀
#   3 worker 提反駁       4 failures 沒有歸因   5 worker 沒交出可用的 patch
set -u
# `AC_ROOT` 優先:被 `gate.sh --auto-fix` 叫到的時候,這支檔案住在**副本**裡,
# 而票、reports 與收件匣住在主 repo。照 `$0` 算根會把它們寫進一個等一下會被
# 收掉的目錄 —— 而且不會報錯。
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

[ $# -ge 1 ] || { echo "用法:sh scripts/auto-fix.sh <票號> [--dry-run]"; exit 2; }
ID=$1
shift
DRY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        *) echo "auto-fix: 不認得 $1(--dry-run)" >&2; exit 2 ;;
    esac
    shift
done

MAIN=$(cfg main_branch main)
TICKETS=${AC_TICKETS_DIR:-$(cfg tickets_dir tickets)}
case $TICKETS in /*) TDIR=$TICKETS ;; *) TDIR=$ROOT/$TICKETS ;; esac
WORKER_CMD=$(cfg worker.command "claude -p --model opus")
WORKER_TIMEOUT=$(cfg worker.timeout_seconds 3600)
# 副本/worktree 的根:環境變數 > board/config.json 的 `worktree_dir`(相對 repo 根)> 預設 `../<repo>-wt`。
WTBASE=${AC_WORKTREE_DIR:-$(cfg worktree_dir "")}
case "$WTBASE" in "") WTBASE=$ROOT/../$(basename "$ROOT")-wt ;; /*) ;; *) WTBASE=$ROOT/$WTBASE ;; esac
TF=$TDIR/$ID.json
[ -f "$TF" ] || { echo "auto-fix: 找不到票 #$ID($TF)" >&2; exit 2; }

ev() {
    python3 "$AC/event.py" emit "$@" >/dev/null \
        || echo "auto-fix: 事件發不出去($*)" >&2
}

post() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "${RUN_ID:-}" \
        --kind auto-fix --state "$1" --what "$2" --where "$3" \
        || echo "auto-fix: inbox 寫不出來($1)" >&2
}

block() {   # $1 = 為什麼;票轉 Blocked、指派主線
    python3 "$AC/ticket.py" set "$ID" state Blocked >/dev/null 2>&1 \
        || echo "auto-fix: 票狀態改不動(Blocked)" >&2
    python3 "$AC/ticket.py" set "$ID" owner main >/dev/null 2>&1 || true
    ev decision.asked --ticket "$ID" --note "$1"
}

# 最新一輪的狀態檔。**讀的是檔,不是輪詢** —— 每看一次背景工作就是整份上下文重送一輪。
read_status() {
    eval "$(python3 - "$ROOT" "$ID" "$TF" <<'PY'
import json, os, shlex, sys
root, ident, tf = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

run_id = status.latest_run(root, ident)
data = status.read(root, ident, run_id) if run_id else {}
ctx = data.get("repair_context") or {}
patch = (ctx.get("patch") or {}).get("path") or ""
try:
    with open(tf, encoding="utf-8") as fh:
        limit = int(json.load(fh).get("retry_limit") or 2) + 1
except (OSError, ValueError, TypeError):
    limit = 3


def out(name, value):
    print("%s=%s" % (name, shlex.quote(str(value))))


out("S_RUN", run_id or "")
out("S_STATE", data.get("state") or "")
out("S_KIND", data.get("kind") or "")
out("S_RC", "" if data.get("rc") is None else data.get("rc"))
out("S_FAILS", len(data.get("failures") or []))
out("S_ROUND", (ctx.get("round") or 1))
out("S_BASE", ctx.get("base_sha") or "")
out("S_PATCH", patch)
out("S_PREV_EVIDENCE", ctx.get("prev_evidence") or "")
out("S_LIMIT", limit)
PY
)"
}

read_status
if [ -z "${S_RUN:-}" ]; then
    echo "auto-fix: #$ID 一輪都還沒跑過 —— 沒有紅榜就沒有東西可以派" >&2
    echo "auto-fix:   先跑 sh scripts/gate.sh --branch --ticket $ID" >&2
    exit 2
fi
RUN_ID=$S_RUN
echo "auto-fix: #$ID 最新一輪 $S_RUN($S_KIND)state=$S_STATE rc=${S_RC:-?} 紅 $S_FAILS 條,第 $S_ROUND 輪(上限 $S_LIMIT)"

if [ "${S_RC:-1}" = "0" ]; then
    echo "auto-fix: 上一輪是綠的 —— 沒有東西要修。覆核不自動:主線讀 patch 記 review。"
    exit 0
fi

# **沒有歸因的紅**:rc 非零卻一條都解析不出來。它看起來最像「沒紅」,而派下去的
# worker 會拿著空紅榜去猜 —— 猜出來的修法會改到沒有壞的地方。
if [ "$S_FAILS" -eq 0 ]; then
    echo "auto-fix: rc=${S_RC:-?} 卻一條紅都解析不出來 —— **沒有歸因**,不派 worker" >&2
    block "#$ID rc=${S_RC:-?} 但 failures 是空的:紅榜解析不出案例,要人看 log"
    post "紅了但沒有歸因" \
         "自己看 log:紅榜是空的,可能是測試根本沒跑起來(import 炸了、指令打錯)" \
         "reports/t$ID/$S_RUN/status.json 與它的 logs/"
    exit 4
fi

if [ "$S_ROUND" -ge "$S_LIMIT" ]; then
    echo "auto-fix: 已經是第 $S_ROUND 輪(上限 $S_LIMIT)—— 不再派,轉主線"
    python3 "$AC/ticket.py" round "$ID" "$S_ROUND" --red || true
    post "三輪耗盡仍紅" "這張票由你接手:讀紅榜決定要改票面還是換做法" \
         "reports/t$ID/$S_RUN/status.json"
    exit 1
fi

# --------------------------------------------------------------- 一輪的動作
MODEL=$(cfg routing.implement opus)
WT=$WTBASE/t$ID

round_once() {   # $1 = 第幾輪(r);設定 ROUND_RC
    r=$1
    FIX=$WTBASE/fix-t$ID/round$r
    rm -rf "$FIX"
    mkdir -p "$FIX/base"
    # **`base/` 是目前分支上的內容,不是 base_sha 的內容。**
    # worker 交的 diff 下一步要餵給 `apply.sh` 套在**分支上**,而分支已經有前幾輪了
    # —— 拿 base_sha 當對照組的話,第二輪的 patch 會宣稱自己在新建一個已經存在的檔,
    # 而 `git apply --check` 會在那裡整輪停住。分支還不存在(第一輪就紅)才退回 base_sha。
    SRC=t$ID
    git -C "$ROOT" rev-parse -q --verify "$SRC^{commit}" >/dev/null || SRC=$S_BASE
    if ! git -C "$ROOT" archive "$SRC" 2>/dev/null | tar -x -C "$FIX/base"; then
        echo "auto-fix: 取不出 $SRC 的副本 —— 這張票的 base 對不上這顆 repo" >&2
        ROUND_RC=2
        return 1
    fi
    cp -R "$FIX/base" "$FIX/work"
    if [ "$SRC" = "$S_BASE" ] && [ -n "$S_PATCH" ] && [ -f "$S_PATCH" ]; then
        ( cd "$FIX/work" && patch -p1 -F 2 --no-backup-if-mismatch -i "$S_PATCH" ) \
            >/dev/null 2>&1 || echo "auto-fix: 上一輪的 patch 有幾塊沒套進副本(worker 要自己看)"
    fi
    echo "auto-fix: 副本 $FIX(base = $SRC)"

    DISPATCH=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID/dispatch-round$r.md
    mkdir -p "$(dirname "$DISPATCH")"
    {
        python3 "$AC/rules.py" pack worker --model "$MODEL" 2>/dev/null \
            || echo "(規則包產不出來 —— 自己讀 memory/role/implementer.md)"
        python3 - "$ROOT" "$ID" "$RUN_ID" "$r" "$FIX" <<'PY'
import json, os, sys
root, ident, run_id, r, fix = sys.argv[1:6]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import status

data = status.read(root, ident, run_id)
ctx = data.get("repair_context") or {}
snap = ctx.get("ticket") or {}
print("")
print("# 這一輪:#%s 第 %s 輪(修復)" % (ident, r))
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
print("- base_sha:`%s`" % (ctx.get("base_sha") or "?"))
print("- 上一輪的 patch:`%s`(sha256 %s)"
      % ((ctx.get("patch") or {}).get("path") or "(沒有)",
         ((ctx.get("patch") or {}).get("sha256") or "")[:16]))
print("- 上一輪的 EVIDENCE:`%s`" % (ctx.get("prev_evidence") or "(沒有 —— 第一輪)"))
print("- 主線重現用的指令(**你不要跑它** —— 它動的是主 repo 的閘門/worktree):`%s`(cwd `%s`)"
      % ((ctx.get("repro") or {}).get("cmd") or "?",
         (ctx.get("repro") or {}).get("cwd") or "?"))
print("- 你在副本裡重現:照底下紅榜的 case 名單跑(例 `python3 -m unittest <模組>.<類>.<案例>`,"
      "cwd 是副本裡那個測試模組所在的目錄);修好後跑同一組再交,整套留給閘門。")
print("- 這是第 %s 輪,上限 %s 輪" % (r, int(snap.get("retry_limit") or 2) + 1))
print("")
print("## 紅榜(逐條,excerpt ≤ 20 行)")
for row in data.get("failures") or []:
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
    if bits:
        print("- " + ";".join(bits))
    print("```")
    print(row.get("excerpt") or "(沒有 excerpt)")
    print("```")
print("")
print("## 副本")
print("- `%s/work` —— 改這個(上一輪的 patch 已經套進去了)" % fix)
print("- `%s/base` —— 一個字都不准動,它是 diff 的對照組" % fix)
print("")
print("## 你要交的兩樣(放在 `%s`)" % fix)
print("1. `patch-round%s.diff` —— `cd %s && diff -ruN base work > patch-round%s.diff`"
      % (r, fix, r))
print("   檔頭只准 `base/…` / `work/…` 的相對形式;刪檔的 `+++` 側要改成 `/dev/null`。")
print("2. `EVIDENCE-round%s.md` —— 必備五段(見角色卡),其中**已排除的假設**那一段"
      % r)
print("   決定下一輪的人要不要把你查過的路再查一次。")
print("")
print("## 票寫錯 / 需要裁示怎麼說")
print("在 EVIDENCE 裡寫**一行**:`OBJECTION: <ticket-wrong|test_defect|blocking> <一句話>`。")
print("看到這一行,這支腳本會把它記成票的 `objections[]`、把票轉 Blocked 並指派主線,"
      "**不會**再派下一輪。")
print("**不准**放寬既有斷言、不准把期望值改成程式現在印的東西(那是假綠家族)。")
PY
    } > "$DISPATCH"
    echo "auto-fix: 派工文 -> $(python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$DISPATCH" "$ROOT")"

    if [ -n "$DRY" ]; then
        echo "auto-fix: --dry-run —— 不起 worker。派工文在上面那一份。"
        ROUND_RC=0
        return 1
    fi

    echo "auto-fix: 起第 $r 輪的 worker —— $WORKER_CMD(cwd $FIX,派工文從 stdin 餵)"
    ev ticket.attempt.start --ticket "$ID" --attempt "$r" --note "auto-fix 第 $r 輪"
    WORKER_LOG=$(dirname "$DISPATCH")/worker-round$r.log
    ev agent.start --ticket "$ID" --model "$MODEL" \
        --kv run_id="$RUN_ID" --kv round="$r" --kv agent=auto-fix
    WRC=$(python3 - "$WORKER_CMD" "$DISPATCH" "$FIX" "$WORKER_TIMEOUT" "$ID" "$r" "$WORKER_LOG" <<'PY'
import subprocess, sys
cmd, dispatch, cwd, timeout, ident, r, log_path = sys.argv[1:8]
env_extra = {"AC_DISPATCH": dispatch, "AC_TICKET": ident, "AC_ROUND": r, "AC_WORK": cwd}
import os
env = dict(os.environ)
env.update(env_extra)
try:
    with open(dispatch, encoding="utf-8") as handle, open(log_path, "w", encoding="utf-8") as log:
        done = subprocess.run(cmd, shell=True, cwd=cwd, env=env, stdin=handle,
                              stdout=log, stderr=subprocess.STDOUT, timeout=float(timeout))
    rc = done.returncode
except subprocess.TimeoutExpired:
    sys.stderr.write("auto-fix: worker 超過 %s 秒還沒回來 —— 當它沒交\n" % timeout)
    rc = 124
except OSError as exc:
    sys.stderr.write("auto-fix: worker 起不來 —— %s\n" % exc)
    rc = 127
print(rc)
PY
)
    if [ "$WRC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --model "$MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix
    else
        ev agent.failed --ticket "$ID" --model "$MODEL" \
            --kv run_id="$RUN_ID" --kv round="$r" --kv rc="$WRC" --kv agent=auto-fix
    fi
    [ "$WRC" -eq 0 ] || echo "auto-fix: worker 自己回非零 —— 還是看它交了什麼,不看它說什麼"

    PATCH_OUT=$FIX/patch-round$r.diff
    EVIDENCE=$FIX/EVIDENCE-round$r.md
    [ -f "$PATCH_OUT" ] || PATCH_OUT=$FIX/work/patch-round$r.diff
    [ -f "$EVIDENCE" ] || EVIDENCE=$FIX/work/EVIDENCE-round$r.md

    # 反駁比 patch 先看:worker 說「這張票寫錯了」而東西照樣落地,那句話等於沒人收。
    if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
        line=$(grep -m1 '^OBJECTION:' "$EVIDENCE")
        echo "auto-fix: worker 提了反駁 —— $line"
        python3 - "$ROOT" "$ID" "$line" "$EVIDENCE" "$TF" <<'PY'
import json, os, subprocess, sys
root, ident, line, evidence, path = sys.argv[1:6]
rest = line.split(":", 1)[1].strip()
parts = rest.split(None, 1)
category = parts[0] if parts and parts[0] in (
    "ticket-wrong", "test_defect", "blocking") else "ticket-wrong"
body = parts[1] if len(parts) > 1 else rest
with open(path, encoding="utf-8") as handle:
    rows = json.load(handle).get("objections") or []
rows.append({"category": category, "body": body,
             "evidence": os.path.relpath(evidence, root), "owner": "main",
             "disposition": "", "follow_up": ""})
subprocess.run([sys.executable, os.path.join(os.environ["AC_CONTROL_DIR"], "ticket.py"),
                "set", ident, "objections",
                json.dumps(rows, ensure_ascii=False)], check=False)
PY
        block "#$ID 的 worker 提反駁:$line"
        post "worker 提反駁(票寫錯 / 需裁示)" \
             "讀 EVIDENCE 那一行反駁,處置它(accepted / rejected / deferred / fixed);沒處置的阻擋項 land 與 close 都會拒絕" \
             "$EVIDENCE"
        ROUND_RC=3
        return 1
    fi

    if [ ! -f "$PATCH_OUT" ]; then
        echo "auto-fix: 第 $r 輪的 worker 沒有交出 patch-round$r.diff" >&2
        block "#$ID 第 $r 輪的 worker 沒交出 patch"
        post "worker 沒交出 patch" "自己看副本裡有什麼;要嘛重派,要嘛人下場" "$FIX"
        ROUND_RC=5
        return 1
    fi

    AC_ROUND=$r AC_PREV_EVIDENCE=$EVIDENCE \
        sh "$AC/apply.sh" "$ID" "$PATCH_OUT"
    arc=$?
    if [ "$arc" -ne 0 ]; then
        echo "auto-fix: 第 $r 輪的 patch 套不上(apply rc=$arc)" >&2
        post "第 $r 輪的 patch 套不上(apply rc=$arc)" \
             "看 apply 的輸出:檔頭不合格、主線走遠(走 apply.sh rebase),還是越界" \
             "$PATCH_OUT"
        ROUND_RC=5
        return 1
    fi

    echo "auto-fix: 第 $r 輪的閘門 —— (cd $WT && sh scripts/gate.sh --branch --ticket $ID)"
    # `AC_ROOT=$ROOT`:閘門在**副本**裡跑,但狀態檔與收件匣要寫回**主 repo**
    # —— 不然這一輪的結果留在一個等一下會被收掉的目錄裡,而讀它的人在主 repo。
    ( cd "$WT" && AC_ROOT=$ROOT AC_ROUND=$r AC_PATCH=$PATCH_OUT \
        AC_PREV_EVIDENCE=$EVIDENCE AC_IN_AUTOFIX=1 \
        sh scripts/gate.sh --branch --ticket "$ID" )
    grc=$?
    if [ "$grc" -eq 0 ]; then
        python3 "$AC/ticket.py" round "$ID" "$r" --green || true
        python3 "$AC/ticket.py" set "$ID" state InReview >/dev/null 2>&1 || true
        read_status
        RUN_ID=$S_RUN
        echo "auto-fix: 第 $r 輪綠了 —— **覆核不自動**,停在這裡等主線"
        post "第 $r 輪綠了,等覆核" \
             "讀 patch 記 review(綁票版本與分支頭 sha),再 sh scripts/land.sh t$ID" \
             "$WT 與 reports/t$ID/$S_RUN/status.json"
        ROUND_RC=0
        return 1
    fi
    python3 "$AC/ticket.py" round "$ID" "$r" --red || true
    read_status
    RUN_ID=$S_RUN
    ROUND_RC=1
    return 0
}

r=$((S_ROUND + 1))
while :; do
    ROUND_RC=0
    if round_once "$r"; then
        # 還紅,而且沒有停下來的理由 —— 看還有沒有下一輪。
        if [ "$S_FAILS" -eq 0 ]; then
            echo "auto-fix: 第 $r 輪 rc 非零卻解析不出紅榜 —— 沒有歸因,停" >&2
            block "#$ID 第 $r 輪 rc 非零但 failures 是空的"
            post "紅了但沒有歸因" "自己看 log:紅榜是空的" \
                 "reports/t$ID/$S_RUN/status.json"
            exit 4
        fi
        if [ "$r" -ge "$S_LIMIT" ]; then
            echo "auto-fix: 第 $r 輪仍紅,上限 $S_LIMIT —— 票已轉 Blocked、指派主線"
            post "三輪耗盡仍紅" "這張票由你接手:讀紅榜決定要改票面還是換做法" \
                 "reports/t$ID/$S_RUN/status.json"
            exit 1
        fi
        r=$((r + 1))
        continue
    fi
    exit "$ROUND_RC"
done
