#!/bin/sh
# 閘門:三層介面,專案自己填對照表 — `docs/WORKFLOW.md` §閘門。
#
#   sh scripts/gate.sh --branch          # 這條分支改動的檔對應到的模組
#   sh scripts/gate.sh --base            # 基礎組:不管改了什麼都要過的那幾支
#   sh scripts/gate.sh --branch --base   # 票收尾
#   sh scripts/gate.sh --full            # 全套(land.sh 跑的就是這一發)
#   sh scripts/gate.sh <檔> <檔> …       # 指定檔
#   sh scripts/gate.sh --branch --ticket 7   # 同上,外加狀態檔、票的回歸、flake 重跑
#   sh scripts/gate.sh --branch --ticket 7 --auto-fix   # 紅了就自動派下一輪 worker
#   sh scripts/gate.sh --branch --ticket 7 --no-cache   # 不吃同一輪的回歸快取
#
# ## `--ticket <票號>`:狀態檔、票的回歸、flake 重跑(D-010 / D-014)
# 給了票號,這一支會做三件多的事:
#
# 1. **狀態檔**:`reports/t<票號>/<run_id>/status.json`(格式見 `scripts/status.py`)。
#    **一輪一個目錄、不覆寫** —— 舊版每票只有一份覆寫檔,上一輪的結果會被這一輪蓋掉,
#    而接手的人分不出手上這份是誰的(2026-09-21 外部審查)。log 另外複製一份進去。
# 2. **跑票的回歸**:票的 `verify.tags`(併 `tags`)拿去叫 `scripts/verify.py --tag …`,
#    原始輸出存 `verify.log`。舊版只拿票號寫狀態檔,**票的回歸從來沒被閘門跑過** ——
#    「驗證者交了案例」與「案例真的在守這張票」因此長得一樣。
#    宣告了 tags 卻一個案例都選不到 = 非零(`verify.py` 的 rc=3):那是缺口,不是綠。
# 3. **flake 重跑**:紅的案例各連續單跑設定次數,再以原順序整組重跑設定次數。
#
# ## 紅榜的處理順序:先環境 fail-fast、再 flake 判定(#7)
# 測試由 `scripts/status.py run-tests` 跑(不再由這一支 shell 直接叫 unittest),它在
# 同一個程序裡看每一條結果:同一引擎、同形訊息連紅達 `board/config.json` 的
# `environment_fail_fast_threshold`(預設 8)就**中止那一段**、回 rc=86。
#
# 這一關擺在 flake 判定前面,理由是兩個:
#   * 被中止的那一段紅榜本來就**不完整** —— 拿不完整的紅榜做 flake 判定,結論不成立。
#   * flake 判定要做的事(每條紅例單跑 N 次、再原順序整組重跑)在壞掉的環境裡最貴,
#     而且只會再紅一輪 —— 那正是這張票要省下的時間,不是要加倍的時間。
# 所以 rc=86 時 `flake_rerun` 與回歸層都不跑,狀態檔寫 `env_suspect`,inbox 那一頁
# 列出引擎、同形訊息、連紅條數與疑似原因清單。
#
# ## 單跑綠**不等於**綠(2026-09-21 改;取代 D-010 的「全 flaky 視為綠」)
# 審查的實測反例:第一條測試污染共用狀態、第二條檢查乾淨狀態 —— 整組必紅,乾淨程序
# 單跑第二條必綠。舊版正是以「所有紅的案例單跑都綠」為由回傳 0,於是**順序依賴的
# bug 每一次都被判成偶發**。
#
# 所以現在:每條紅例連續單跑 `flake_auto_single_runs` 次全綠,再用**原順序整組**重跑
# `flake_auto_group_runs` 次也全綠,才標 `flaky=auto` 並放行。單跑全綠但整組仍紅就是
# 順序污染:**原始失敗留在紅榜、rc 一個位元都不動**。
# `AC_NO_FLAKE_RERUN=1` 連單跑都不做。
# 沒給票號就完全照舊 —— 不寫檔、不重跑、退出碼不變。
#
# ## 終態會叫醒主線(D-015)
# 給了票號,跑完就寫一則 `reports/inbox/<票號>-<run_id>.md` 並發 `inbox.posted`:
# **哪張票、什麼狀態、要主線做什麼、去哪看**。主線因此不必輪詢 status ——
# 每看一次背景工作就是整份上下文重送一輪。
#
# ## `--auto-fix`:紅了自動派下一輪(D-010)
# 紅了就呼叫 `scripts/auto-fix.sh <票號>`。**閘門自己的退出碼不會因此變綠** ——
# 這個 commit 對這一組測試就是紅的,而 auto-fix 產生的是**下一個** commit。
# 修好了沒、停在哪一種,看 inbox 那一頁。
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
RERUN_LOG=$LOG.rerun
VERIFY_LOG=${AC_VERIFY_LOG:-$ROOT/verify.log}
SUSPECT_FILE=$LOG.env-suspect.json
MAIN=main
ALL_ARGS="$*"

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
                                      test_board test_check_stale test_no_project_names \
                                      test_status test_verify_case test_apply \
                                      test_auto_fix test_inbox test_rules ;;
        # `.gitignore` 決定「不准出現專案名」那一掃看得到哪些檔。
        .gitignore) add test_no_project_names ;;
        # ticket.py import event.py:動 event 的人要連票那一側一起跑。
        scripts/event.py) add test_event test_ticket test_land ;;
        scripts/ticket.py) add test_ticket test_land ;;
        scripts/land.sh) add test_land ;;
        scripts/gate.sh|scripts/gate.example.sh) add test_gate test_status ;;
        scripts/apply.sh) add test_apply ;;
        scripts/auto-fix.sh) add test_auto_fix ;;
        # 收件匣:閘門與落地的終態都寫它,所以動它要連那兩側一起跑。
        scripts/inbox.py) add test_inbox test_gate test_land test_new_session ;;
        scripts/rules.py) add test_rules ;;
        scripts/heartbeat.sh) add test_heartbeat ;;
        scripts/new-session.sh) add test_new_session ;;
        scripts/memory.py) add test_memory ;;
        # 回歸層的執行器與它的登記檔:動它就是動「哪些案例會被跑到」。
        scripts/verify.py|verify/TAGS.md|verify/TAGS.d/*) add test_verify_runner test_verify_case ;;
        scripts/verify-case.py) add test_verify_case ;;
        verify/*) add test_verify_runner ;;
        scripts/sync-to-project.sh) add test_sync_to_project ;;
        # 狀態檔:閘門與落地都寫它,所以動它要連那兩側一起跑。
        scripts/status.py) add test_status test_gate test_land ;;
        # 記憶檔既受上限守衛管(test_memory),也在「不准出現專案名」那一掃裡。
        memory/model/*.md) add test_memory test_no_project_names ;;
        memory/role/*.md) add test_memory test_sync_to_project test_no_project_names ;;
        board/board.py) add test_board ;;
        # 設定檔:看板讀它,事件與落地也讀它(埠、票目錄、事件檔、租約)。
        board/config.json) add test_board test_event test_heartbeat test_memory ;;
        code-map/check-stale.py|code-map/cards/*) add test_check_stale ;;
        tickets/*.json|tickets/SCHEMA.md) add test_ticket ;;
        docs/DISPATCH-TEMPLATE.md) add test_dispatch_template test_no_project_names ;;
        templates/dispatch-verifier.md) add test_dispatch_template test_no_project_names ;;
        # 文件**有**一支測試真的讀它們:那條「不准出現專案名 / 絕對路徑」的守衛
        # 掃的就是整個 repo。所以這一格不是硬塞,是實話。
        *.md) add test_no_project_names ;;
        *) miss "$f" ;;
    esac
}

want_base=0; want_branch=0; want_full=0
want_autofix=0
NO_CACHE=${AC_NO_VERIFY_CACHE:-}
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
        --auto-fix) want_autofix=1 ;;
        --no-cache) NO_CACHE=1 ;;
        --*) echo "gate: 不認得 $1(--branch / --base / --full / --ticket <票號> / --auto-fix / --no-cache)" >&2; exit 2 ;;
        *) map "$1" ;;
    esac
    shift
done
[ "$ARGC" -ge 1 ] || { echo "gate: 要 --branch / --base / --full 或一串檔名" >&2; exit 2; }

if [ -n "$TICKET" ]; then
    python3 - "$ROOT" "$TICKET" "$MAIN" <<'PREFLIGHT_PY'
import json, os, re, subprocess, sys

root, ident, main = sys.argv[1:4]
sys.path.insert(0, os.path.join(root, "scripts"))
import event
import ticket as ticket_mod

path = os.path.join(event.tickets_dir(root), "%s.json" % ident)
try:
    with open(path, encoding="utf-8") as handle:
        ticket = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)

def git(*args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True).stdout

def names(*args):
    return [name.decode("utf-8", "surrogateescape")
            for name in git(*args).split(b"\0") if name]

changed = []
for args in (("diff", "--name-only", "-z", "%s...HEAD" % main),
             ("diff", "--name-only", "-z", "HEAD"),
             ("ls-files", "--others", "--exclude-standard", "-z")):
    for name in names(*args):
        if name not in changed:
            changed.append(name)
try:
    ticket_rel = os.path.relpath(path, root)
except ValueError:
    ticket_rel = ""
if ticket_rel and not ticket_rel.startswith(".." + os.sep):
    changed = [name for name in changed if name != ticket_rel]

content = []
for args in (("diff", "--no-ext-diff", "--unified=0", "%s...HEAD" % main),
             ("diff", "--no-ext-diff", "--unified=0", "HEAD")):
    for line in git(*args).decode("utf-8", "replace").splitlines():
        if line.startswith(("+++ ", "--- ")):
            continue
        if line.startswith(("+", "-")):
            content.append(line[1:])
tracked = set(names("ls-files", "-z"))
for name in changed:
    if name in tracked:
        continue
    try:
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as handle:
            content.append(handle.read())
    except OSError:
        pass
blob = "\n".join(content)

errors = []
for row in ticket.get("verify_strings") or []:
    needle = row.get("contains") if isinstance(row, dict) else row
    if needle and str(needle) not in blob:
        errors.append("verify_strings: %r 不在 patch 內容裡" % needle)

registered = set()
tag_paths = [os.path.join(root, "verify", "TAGS.md")]
fragments = os.path.join(root, "verify", "TAGS.d")
if os.path.isdir(fragments):
    tag_paths += [os.path.join(fragments, name) for name in sorted(os.listdir(fragments))
                  if name.endswith(".md")]
for tag_path in tag_paths:
    try:
        with open(tag_path, encoding="utf-8") as handle:
            registered.update(re.findall(r"^- `([a-z0-9-]+)`", handle.read(), re.M))
    except OSError:
        pass
plan = ticket.get("verify") if isinstance(ticket.get("verify"), dict) else {}
tags = []
for tag in list(ticket.get("tags") or []) + list(plan.get("tags") or []):
    if tag and tag not in tags:
        tags.append(str(tag))
for tag in tags:
    if tag not in registered:
        errors.append("tags: %r 未登記(登記在 verify/TAGS.md 或 verify/TAGS.d/)" % tag)

globs = ticket.get("allowed_write_paths") or []
for name in changed:
    if not ticket_mod.matches_any(name, globs):
        errors.append("allowed_write_paths: %s 在允許範圍外" % name)

if errors:
    for error in errors:
        print("gate: 機械格不合 —— " + error, file=sys.stderr)
    raise SystemExit(4)
PREFLIGHT_PY
    preflight_rc=$?
    [ "$preflight_rc" -eq 0 ] || exit "$preflight_rc"
fi

SHA=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "")
BASE_SHA=$(git -C "$ROOT" rev-parse "$MAIN" 2>/dev/null || echo "")
RUN_ID=${AC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
FLAKY_ARGS=""
AUTO_FLAKY_ARGS=""
ORDER_ARGS=""
EXTRA_LOG_ARGS=""
VERIFY_LOG_ARGS=""
NOTE=""
ENV_SUSPECT=0

suspect_field() {   # $1 = JSON 欄位;讀不到就印空的,不猜
    python3 - "$SUSPECT_FILE" "$1" <<'SUSPECT_PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
print(data.get(sys.argv[2], ""))
SUSPECT_PY
}

# 測試一律走這裡。判綠仍然只看 rc:86 = 環境可疑(那一段被中止)、1 = 紅、0 = 綠。
run_tests() {   # $1 = discover|names;其餘是模組名
    mode=$1
    shift
    python3 "$ROOT/scripts/status.py" run-tests --root "$ROOT" --log "$LOG" \
        --suspect-file "$SUSPECT_FILE" --mode "$mode" "$@"
}

env_suspect_note() {
    ENV_SUSPECT=1
    NOTE="$NOTE 同一引擎同形訊息連紅達環境門檻,已中止這一段(不做 flake 判定)。"
    echo "gate: 環境可疑 —— $(suspect_field engine) 連紅 $(suspect_field count) 條同形訊息,這一段中止了"
}

# 狀態檔發不出去要出聲,但**不擋閘門** —— 同 land.sh 的事件:寫不出來的那一刻正是
# 最需要紀錄的那一刻,而讓它擋住測試會把一個紀錄問題升級成一個交付問題。
status_start() {
    [ -n "$TICKET" ] || return 0
    python3 "$ROOT/scripts/status.py" start --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --base-sha "$BASE_SHA" \
        --worktree "$ROOT" --round "${AC_ROUND:-1}" \
        --patch "${AC_PATCH:-}" --verify-patch "${AC_VERIFY_PATCH:-}" \
        --prev-evidence "${AC_PREV_EVIDENCE:-}" \
        --repro "sh scripts/gate.sh $ALL_ARGS" --cwd "$ROOT" \
        >/dev/null 2>&1 || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
}

status_done() {   # $1 = rc
    [ -n "$TICKET" ] || return 0
    state_args=""
    if [ "$ENV_SUSPECT" -eq 1 ]; then
        state_args="--state env_suspect --environment-log $SUSPECT_FILE"
    fi
    # shellcheck disable=SC2086
    python3 "$ROOT/scripts/status.py" done --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --rc "$1" --note "$NOTE" \
        --log "$LOG" $state_args $VERIFY_LOG_ARGS $EXTRA_LOG_ARGS $FLAKY_ARGS \
        $AUTO_FLAKY_ARGS $ORDER_ARGS \
        || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
    inbox_post "$1"
}

# 終態叫醒主線(D-015)。**一頁四句**:哪張票、什麼狀態、要主線做什麼、去哪看。
# 寫不出來要出聲但不擋閘門 —— 同狀態檔的理由。
inbox_post() {   # $1 = rc
    [ -n "$TICKET" ] || return 0
    [ -z "${AC_NO_INBOX:-}" ] || return 0
    note=""
    if [ "$ENV_SUSPECT" -eq 1 ]; then
        # 環境那一頁要能直接動手:哪個引擎、倒在哪一句、連幾條,以及**先去看哪三樣**。
        # 一句「環境可疑」不是一個可以執行的動作(DISPATCH-TEMPLATE §5.7)。
        engine=$(suspect_field engine)
        message=$(suspect_field message_shape)
        count=$(suspect_field count)
        state="env_suspect(gate rc=$1)"
        what="先別重跑;查 $engine 那一側的環境,修好再跑這一段"
        note="引擎:$engine
同形訊息:$message
連續紅:$count
疑似原因:掛很久的 Safari --automation 行程、磁碟剩餘空間不足、測試埠被占用。"
    elif [ "$1" -eq 0 ]; then
        state="閘門綠(gate rc=0)"
        what="讀 patch 記 review(綁票版本與分支頭 sha),再 sh scripts/land.sh t$TICKET"
    else
        state="閘門紅(gate rc=$1)"
        what="看紅榜逐條;要自動派下一輪 worker:sh scripts/auto-fix.sh $TICKET"
    fi
    python3 "$ROOT/scripts/inbox.py" post --ticket "$TICKET" --run-id "$RUN_ID" \
        --kind gate --state "$state" --what "$what" --note "$note" \
        --where "$(python3 "$ROOT/scripts/status.py" rundir --ticket "$TICKET" \
                   --run-id "$RUN_ID" 2>/dev/null || echo "reports/t$TICKET/$RUN_ID")/status.json" \
        >/dev/null 2>&1 || echo "gate: 收件匣寫不出來(不擋閘門)" >&2
}

# 紅了自動派下一輪。**閘門自己的 rc 不動** —— 這個 commit 對這一組測試就是紅的。
auto_fix() {   # $1 = rc
    [ "$want_autofix" -eq 1 ] || return 0
    [ "$1" -eq 0 ] && return 0
    if [ -z "$TICKET" ]; then
        echo "gate: --auto-fix 要有 --ticket <票號> —— 沒有票就沒有紅榜可以派" >&2
        return 0
    fi
    if [ -n "${AC_IN_AUTOFIX:-}" ]; then
        echo "gate: 已經在 auto-fix 裡面了,不再往下派(免得自己叫自己)"
        return 0
    fi
    echo "gate: --auto-fix —— sh scripts/auto-fix.sh $TICKET"
    sh "$ROOT/scripts/auto-fix.sh" "$TICKET" \
        || echo "gate: auto-fix 停下來了(rc=$?)—— 看 reports/inbox/ 那一頁"
}

# 沒有測試可跑也要留下終態。**「這一輪根本沒有東西跑」是一個結果,不是一次沒跑**
# —— 舊版這條路直接 exit,狀態檔留在 `running`(或留著上一輪的),而那與「還在跑」
# 長得一樣(2026-09-21 外部審查:status 的生命週期會誤導接手者)。
status_nothing() {   # $1 = rc  $2 = 為什麼
    [ -n "$TICKET" ] || return 0
    NOTE=$2
    python3 "$ROOT/scripts/status.py" start --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --base-sha "$BASE_SHA" \
        --worktree "$ROOT" --repro "sh scripts/gate.sh $ALL_ARGS" --cwd "$ROOT" \
        >/dev/null 2>&1
    python3 "$ROOT/scripts/status.py" done --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --rc "$1" --note "$2" >/dev/null 2>&1 \
        || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
    inbox_post "$1"
}

# 票的 `verify.tags` 併 `tags`。**票是唯一的工作單位**,所以要跑哪幾個回歸標籤這件事
# 住在票裡,不住在呼叫者的記憶裡。
ticket_tags() {
    python3 - "$ROOT" "$TICKET" <<'PY' 2>/dev/null
import json, os, sys
root, ident = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "scripts"))
import event
path = os.path.join(event.tickets_dir(root), "%s.json" % ident)
try:
    with open(path, encoding="utf-8") as handle:
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
}

# 回歸層:**正式呼叫**,原始輸出存檔。不靠執行器自測間接跑 —— 那種跑法把失敗輸出
# 收進 `capture_output`,外層只剩「returncode 不等於 0」,紅榜於是指向執行器自測
# 而不是指向產品(2026-09-21 外部審查)。
regression() {   # $1 = ticket|full;設定 VRC
    VRC=0
    args=""
    if [ ! -f "$ROOT/scripts/verify.py" ]; then
        # 沒有回歸層的 repo(或沙盒)要**說出來**,不要讓「沒有回歸可跑」穿著
        # 「回歸綠了」的衣服走過去(§5.5 的母題)。
        echo "gate: 這顆 repo 沒有 scripts/verify.py —— 回歸層沒跑(不是綠)"
        NOTE="$NOTE 沒有 scripts/verify.py,回歸層沒跑。"
        return 0
    fi
    if [ "$1" = "ticket" ]; then
        tags=$(ticket_tags)
        if [ -z "$tags" ]; then
            echo "gate: 票 #$TICKET 沒有宣告 verify.tags —— 這一輪沒有回歸可跑"
            echo "gate:   (驗證者交件時要填票的 verify.tags;現在這個綠只涵蓋單元層。)"
            NOTE="$NOTE 票沒有宣告 verify.tags,回歸層這一輪沒跑。"
            return 0
        fi
        for t in $tags; do args="$args --tag $t"; done
        echo "gate: 票 #$TICKET 的回歸 —— python3 scripts/verify.py$args"
    else
        echo "gate: 回歸全部 —— python3 scripts/verify.py"
    fi
    [ -z "$NO_CACHE" ] || args="$args --no-cache"
    # `AC_TICKET` + `AC_RUN_ID` 讓 worker、驗證者與這裡共用同一輪的回歸快取:
    # 同一份程式、同一組標籤、同一個 sha 只跑一次(D-015)。
    # shellcheck disable=SC2086
    ( cd "$ROOT" && AC_TICKET="$TICKET" AC_RUN_ID="$RUN_ID" \
        python3 scripts/verify.py $args ) > "$VERIFY_LOG" 2>&1
    VRC=$?
    grep -aE "^Ran |^OK|^FAILED|^verify: " "$VERIFY_LOG" \
        || echo "gate: 回歸的 log 裡連 Ran 都沒有,看 $VERIFY_LOG"
    VERIFY_LOG_ARGS="--log $VERIFY_LOG"
    if [ "$VRC" -eq 3 ]; then
        if [ "$1" = "ticket" ]; then
            # 票說它有 tags,執行器卻一個案例都選不到 —— 那是**缺口**:
            # 「驗證者交了案例」與「案例根本不存在」在這裡分得開。
            echo "gate: 票宣告了 tags,但一個案例都選不到 —— 那是缺口,不是綠。"
        else
            # 整個回歸層還是空的(年輕的 repo)。出聲、記進狀態檔,但不擋 ——
            # 擋住的話第一張票永遠落不了地。
            echo "gate: 回歸層一個案例都沒有 —— 出聲但不擋(第一張帶標籤的票會補上)"
            NOTE="$NOTE 回歸層是空的。"
            VRC=0
        fi
    fi
    [ "$VRC" -eq 0 ] || echo "gate: 回歸紅了,看 $VERIFY_LOG"
    return 0
}

merge_rc() {   # $1 = 目前 rc  $2 = 另一個 rc;印出合併後的
    if [ "$1" -ne 0 ]; then echo "$1"; else echo "$2"; fi
}

# 紅例連續單跑與原順序整組都達設定門檻才自動放行。
flake_rerun() {   # $1 = rc  $2 = 整組重跑的指令描述
    [ "$1" -eq 0 ] && return 0
    [ -n "$TICKET" ] || return "$1"
    [ -z "${AC_NO_FLAKE_RERUN:-}" ] || return "$1"
    cases=$(python3 "$ROOT/scripts/status.py" failures --log "$LOG" 2>/dev/null || true)
    [ -n "$cases" ] || return "$1"
    single_runs=$(python3 - "$ROOT" <<'PY'
import json, os, sys
try:
    with open(os.path.join(sys.argv[1], "board", "config.json"), encoding="utf-8") as handle:
        value = int(json.load(handle).get("flake_auto_single_runs") or 5)
except (OSError, ValueError, TypeError):
    value = 5
print(max(1, value))
PY
)
    group_runs=$(python3 - "$ROOT" <<'PY'
import json, os, sys
try:
    with open(os.path.join(sys.argv[1], "board", "config.json"), encoding="utf-8") as handle:
        value = int(json.load(handle).get("flake_auto_group_runs") or 1)
except (OSError, ValueError, TypeError):
    value = 1
print(max(1, value))
PY
)
    total=0
    qualified=0
    for case in $cases; do
        total=$((total + 1))
        passed=0
        attempt=1
        while [ "$attempt" -le "$single_runs" ]; do
            if ( cd "$ROOT/tests" && python3 -m unittest "$case" ) >/dev/null 2>&1; then
                passed=$((passed + 1))
            else
                break
            fi
            attempt=$((attempt + 1))
        done
        if [ "$passed" -eq "$single_runs" ]; then
            echo "gate: $case 單獨重跑 $single_runs 次全綠"
            FLAKY_ARGS="$FLAKY_ARGS --suspected-flaky $case"
            qualified=$((qualified + 1))
        else
            echo "gate: $case 單獨重跑只綠 $passed/$single_runs 次 —— 真紅"
        fi
    done
    [ "$qualified" -gt 0 ] || return "$1"
    NOTE="$NOTE 單跑門檻達標 $qualified/$total 條。"
    [ "$qualified" -eq "$total" ] || return "$1"
    if [ -n "${AC_FLAKE_RERUN_GROUP:-}" ] && [ "${AC_FLAKE_RERUN_GROUP:-}" = "0" ]; then
        echo "gate: AC_FLAKE_RERUN_GROUP=0 —— 不做整組重跑;rc 維持 $1"
        return "$1"
    fi
    echo "gate: 用原順序整組重跑 $group_runs 次 —— 順序污染會在這裡再紅"
    group=1
    while [ "$group" -le "$group_runs" ]; do
        run_log=$RERUN_LOG.$group
        ( eval "$2" ) > "$run_log" 2>&1
        grc=$?
        EXTRA_LOG_ARGS="$EXTRA_LOG_ARGS --extra-log $run_log"
        grep -aE "^Ran |^OK|^FAILED" "$run_log" || true
        if [ "$grc" -ne 0 ]; then
            for case in $cases; do ORDER_ARGS="$ORDER_ARGS --order-dependent $case"; done
            echo "gate: 原順序整組第 $group 次仍然紅 —— order_dependent(log: $run_log)"
            NOTE="$NOTE 原順序整組重跑仍紅 = order_dependent。"
            return "$1"
        fi
        group=$((group + 1))
    done
    for case in $cases; do AUTO_FLAKY_ARGS="$AUTO_FLAKY_ARGS --auto-flaky $case"; done
    echo "gate: 單跑與原順序整組都達門檻 —— flaky=auto,rc 轉 0"
    NOTE="$NOTE 單跑與整組都達門檻,flaky=auto。"
    python3 "$ROOT/scripts/event.py" emit flake.auto_pass --ticket "$TICKET" \
        --kv "run_id=$RUN_ID" --kv "single_runs=$single_runs" \
        --kv "group_runs=$group_runs" --note "$cases" >/dev/null \
        || echo "gate: flake.auto_pass 事件發不出去(不擋閘門)" >&2
    return 0
}

if [ "$want_full" -eq 1 ]; then
    echo "gate: 全套 —— python3 -m unittest discover -s tests"
    status_start
    # 判綠先寫檔再讀退出碼,不用 `cmd | tail`:管線的退出碼是右邊那一支的
    # (`false | tail` 是 0),而那會讓「根本沒跑起來」靜靜判成綠。
    run_tests discover
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    # 先環境 fail-fast、再 flake 判定(見檔頭)。
    if [ "$rc" -eq 86 ]; then
        env_suspect_note
    else
        flake_rerun "$rc" "cd \"$ROOT\" && python3 -m unittest discover -s tests -v"
        rc=$?
    fi
    # 全套 = 單元全部 + **回歸全部**。回歸不靠執行器自測間接跑。
    if [ "$ENV_SUSPECT" -eq 0 ]; then
        regression full
        rc=$(merge_rc "$rc" "$VRC")
    fi
    [ "$rc" -eq 0 ] || echo "gate: 紅了,看 $LOG"
    status_done "$rc"
    auto_fix "$rc"
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
    status_start
    # shellcheck disable=SC2086
    run_tests names $mods
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    # 先環境 fail-fast、再 flake 判定(見檔頭)。
    if [ "$rc" -eq 86 ]; then
        env_suspect_note
    else
        flake_rerun "$rc" "cd \"$ROOT/tests\" && python3 -m unittest $mods"
        rc=$?
    fi
    if [ -n "$TICKET" ] && [ "$ENV_SUSPECT" -eq 0 ]; then
        regression ticket
        rc=$(merge_rc "$rc" "$VRC")
    fi
    [ "$rc" -eq 0 ] || echo "gate: 紅了,看 $LOG"
    status_done "$rc"
    auto_fix "$rc"
fi

if [ -n "$unmapped" ]; then
    # 對不到模組也要留下狀態檔:**「沒有人守著這幾個檔」是一個結果,不是一次沒跑**。
    if [ -n "$mods" ]; then
        NOTE="$NOTE 有檔對不到任何測試模組。"
        status_done 3
    else
        status_nothing 3 "這幾個改動檔對不到任何測試模組:$unmapped"
    fi
    echo "gate: 這幾個改動檔對不到任何測試模組 —— 沒有人守著它們:"
    for f in $unmapped; do echo "gate:   $f"; done
    echo "gate: 要嘛補 scripts/gate.sh 的對照表,要嘛在票裡寫明為什麼它們不需要測試。"
    exit 3
fi
if [ -z "$mods" ]; then
    status_nothing 2 "沒有任何要跑的東西(--branch 沒有改動檔?)"
    echo "gate: 沒有給我任何要跑的東西(--branch 沒有改動檔?要跑基礎組加 --base)"
    exit 2
fi
exit $rc
