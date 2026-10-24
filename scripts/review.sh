#!/bin/sh
# 覆核自動派 — `docs/DESIGN-MAIN-TOUCHPOINTS.md` C2(D-025 ②、D-022)。
#
#   sh scripts/review.sh <票號>                  # 票要是 InReview / AwaitingReview、分支 t<票號> 要在
#   sh scripts/review.sh <票號> --run-id <id>    # 派工文與 REVIEW.md 落在那一輪的目錄
#   sh scripts/review.sh --batch <整合分支> <票號…> --run-id <stamp> [--base <sha>]
#                                                # 批次修復的 commit(#108、#145;見下面 batch 那一段)
#
# 閘門綠、票轉 `InReview` 之後由兩處叫這一支:`auto-fix.sh` 第 r 輪綠(`--no-review` 關)
# 與手跑 `gate.sh --branch --ticket <n>` 綠。主線不再組派工文、不再讀 patch、不再手抄 sha。
# **只派 `complexity=complex` 的票**(#136,VISION 流程 #9:只有複雜的票做完後檢查一次);
# simple 或沒標(= simple)走既有的 refuse(review.refused、rc=2),兩個呼叫端也不叫。
#
# ## 它做的事
# 派工文 = `rules.py pack reviewer --model <模型>` + `templates/dispatch-reviewer.md`(四件事
# 填好),寫到 `reports/t<n>/<run_id>/dispatch-reviewer.md`,從 stdin 餵給 `board/config.json`
# 的 `reviewer.command`(headless、唯讀:預設的工具白名單沒有 Edit / Write)。它的 stdout 就是
# 同一目錄的 `REVIEW.md`;檔尾 `## result` 由 `ticket.py result` 抽(與 worker 同一支,#29 A4):
#   pass   → `ticket.py set <n> review {verdict, by: reviewer@<模型>, sha: 派工那一刻的分支頭, note}`
#            (`at` 由 set 補)—— 給人看的五格,不綁票版本 / 分支頭 / 內容(#136);
#   fail   → REVIEW.md 每一行 `OBJECTION:` 記成一筆 blocking 反駁(owner=reviewer@<模型>)、
#            票轉 Blocked、inbox 一頁 decision「覆核退回,裁示」、事件 review.fail;
#   沒交件 → 不寫 review、票留 InReview、事件 review.missing(不發頁)。
# pass 發 review.pass、沒派成發 review.refused,都不發頁:收件匣只收 decision / done(D-032)。
# 三種都在收尾寫一筆 `ticket.py cost --role reviewer`(信封 reviewer.json;D-032)。
#
# ## 「沒交」與 pass 要分得開(`docs/DISPATCH-TEMPLATE.md` §5.5)
# 沒有 `## result`、JSON 解不開、逾時、verdict 不是 pass|fail —— 四種都**不是 pass**。
# 逾時先判、不看內容:被砍之前它可能已經印了一塊像樣的 pass。verdict 說 pass 卻有
# `OBJECTION:` 行時以那幾行為準(同 `tickets/SCHEMA.md` result 區塊「以 OBJECTION 行為準」),走 fail。
#
# ## 模型名取自角色表(#121;以前取 `reviewer.command` 的 `--model`,#21),不是 routing 標籤
# `review.by` 與事件的 model 欄記**實際起的那個**;命令裡沒有 `--model` 就記 `unknown`,不猜。
#
# ## 退出碼
#   0 pass(review 已寫)   1 fail(票 Blocked,等主線裁示)   2 用法 / 前提不成立
#     (含規則包產不出來 —— reviewer 沒起,#74)
#   3 沒交件               4 review 或反駁寫不進票
#   7 reviewer 執行中撞額度 / 不可用而候選整串耗盡(票 Blocked + 一頁;批次只發頁),
#     或下一個候選交給 Controller(`AC_RECOVERY_OWNER=controller`)接續(#132)
set -u
# 專案根不從 `$0` 來(D-071 C2,#159):`AC_ROOT`,否則 cwd 的主 repo 根,否則 rc 2 ——
# 規則只有一份,在 `wtbase.sh`。控制腳本自己住的目錄(A 的 clone)才從 `$0` 算;
# 同伴腳本一律從這裡叫,不寫死 $ROOT/scripts。
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_CONTROL_DIR=$AC
. "$AC/wtbase.sh"
ROOT=$(ac_root review) || exit 2
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

[ $# -ge 1 ] || { echo "用法:sh scripts/review.sh <票號> [--run-id <id>]" >&2; exit 2; }
BATCH=""
if [ "$1" = "--batch" ]; then
    # 批次修復的覆核(#108):sh scripts/review.sh --batch <整合分支> <票號…> --run-id <stamp> [--base <sha>]
    shift
    [ $# -ge 2 ] || { echo "用法:sh scripts/review.sh --batch <整合分支> <票號…> --run-id <stamp> [--base <sha>]" >&2; exit 2; }
    BATCH=1
    B_BR=$1
    shift
    B_IDS=""
    B_BASE=""
    RUN_ID=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --run-id)
                shift
                [ $# -ge 1 ] || { echo "review: --run-id 後面要一個 stamp" >&2; exit 2; }
                RUN_ID=$1 ;;
            --base)
                shift
                [ $# -ge 1 ] || { echo "review: --base 後面要一個 sha" >&2; exit 2; }
                B_BASE=$1 ;;
            -*) echo "review: --batch 不認得 $1(--run-id <stamp> [--base <sha>])" >&2; exit 2 ;;
            *) B_IDS="$B_IDS $1" ;;
        esac
        shift
    done
    B_IDS=${B_IDS# }
    [ -n "$B_IDS" ] || { echo "review: --batch 至少要一張票號" >&2; exit 2; }
    [ -n "$RUN_ID" ] || { echo "review: --batch 要 --run-id <stamp>(reports/batch/<stamp>/ 那一格)" >&2; exit 2; }
    ID=${B_IDS%% *}
else
ID=$1
shift
RUN_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)
            shift
            [ $# -ge 1 ] || { echo "review: --run-id 後面要一個 run_id" >&2; exit 2; }
            RUN_ID=$1 ;;
        *) echo "review: 不認得 $1(--run-id <id>)" >&2; exit 2 ;;
    esac
    shift
done
fi

TICKETS=${AC_TICKETS_DIR:-$(cfg tickets_dir tickets)}
case $TICKETS in /*) TDIR=$TICKETS ;; *) TDIR=$ROOT/$TICKETS ;; esac
TF=$TDIR/$ID.json
if [ ! -f "$TF" ] && [ -z "${AC_TICKETS_DIR:-}" ]; then
    MAINROOT=$(main_root)
    if [ "$MAINROOT" != "$ROOT" ] \
            && [ -f "$MAINROOT/board/config.json" ] && [ -f "$MAINROOT/$TICKETS/$ID.json" ]; then
        echo "review: $ROOT 是 worktree —— 票 / reports 改用主 repo $MAINROOT"
        ROOT=$MAINROOT
        export AC_ROOT=$ROOT
        TDIR=$ROOT/$TICKETS
        TF=$TDIR/$ID.json
    fi
fi
[ -f "$TF" ] || { echo "review: 找不到票 #$ID($TF)" >&2; exit 2; }

REVIEWER_BASE_CMD=$(cfg reviewer.command "claude -p --model opus --allowedTools Read Glob Grep Bash --output-format json")
REVIEWER_TIMEOUT=$(cfg reviewer.timeout_seconds 1800)
# 覆核者也查角色表(#121 A1,#116):model / tool / effort 只由 `role-models.py resolve
# --role reviewer` 選,`reviewer.command` 的 `--model` 換成表選的那一個(codex 候選走
# codex exec);BY、規則包、事件、cost 記的都是實際起的那一個。選不出就不派。
# CLI 已知不在(codex 不在 PATH)就在啟動前標記那一格、取下一個候選(A2);整串沒了不派。
# 執行中撞額度 / 不可用的換候選在 `reviewer_fallback`(#132)。
pick_reviewer() {   # $@ = 給 resolve 的額外旗標;設定 R_RC 與 MODEL / R_TOOL / R_EFFORT / R_INDEX / R_REASON
    R_OUT=$(python3 "$AC/role-models.py" --table "$ROOT/board/role-models.json" resolve \
            --role reviewer "$@")
    R_RC=$?
    [ "$R_RC" -eq 0 ] || [ "$R_RC" -eq 3 ] || return 1
    eval "$(python3 - "$R_OUT" <<'PY'
import json, shlex, sys
got = json.loads(sys.argv[1])
for key, name in (("model", "MODEL"), ("tool", "R_TOOL"), ("effort", "R_EFFORT"),
                  ("candidate_index", "R_INDEX"), ("reason", "R_REASON")):
    value = got.get(key)
    print("%s=%s" % (name, shlex.quote("" if value is None else str(value))))
PY
)"
    [ "$R_RC" -eq 0 ]
}
use_reviewer() {   # pick_reviewer 的結果 → REVIEWER_CMD / BY
    REVIEWER_CMD=$(python3 "$AC/ticket.py" agent-command --model "$MODEL" --tool "$R_TOOL" \
            --base "$REVIEWER_BASE_CMD" --swap-model --effort "$R_EFFORT") || {
        echo "review: #$ID 角色表選的 reviewer $MODEL(tool $R_TOOL)組不出命令 —— 不派" >&2
        return 1
    }
    BY=reviewer@$MODEL
}
review_rules() {   # 規則包照 MODEL 挑模型卡(命令裡沒寫 `--model` 的 unknown 不指定);設定 RULES_MODEL / RULES_TEXT
    RULES_MODEL=$MODEL
    [ "$RULES_MODEL" != "unknown" ] || RULES_MODEL=""
    RULES_TEXT=$(AC_TICKET=$ID python3 "$AC/rules.py" pack reviewer --model "$RULES_MODEL")
}
pick_reviewer || {
    echo "review: #$ID 角色表選不出 reviewer(role-models.py rc=$R_RC)—— 不派" >&2
    exit 2
}
while [ "$R_TOOL" = codex ] && ! command -v codex >/dev/null 2>&1; do
    echo "review: #$ID 角色表第 $R_INDEX 格是 $MODEL,但 codex 不在 PATH —— 這一格標記不可用,換下一個候選" >&2
    pick_reviewer --exhausted-index "$R_INDEX" || {
        echo "review: #$ID 角色表選不出 reviewer(role-models.py rc=$R_RC)—— 不派" >&2
        exit 2
    }
done
use_reviewer || exit 2

rel() { python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$1" "$ROOT"; }

ev() {
    python3 "$AC/event.py" emit "$@" >/dev/null \
        || echo "review: 事件發不出去($*)" >&2
}

post() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "$RUN_ID" \
        --kind decision --state "$1" --what "$2" --where "$3" \
        || echo "review: inbox 寫不出來($1)" >&2
}

refuse() {   # $1 = 為什麼沒派成  $2 = 下一步;只發事件(D-032),rc=2
    echo "review: #$ID 覆核沒派成 —— $1" >&2
    ev review.refused --ticket "$ID" --kv run_id="$RUN_ID" --note "$1;$2"
    exit 2
}

# stdin / timeout / rc 與 `auto-fix.sh` 起 headless worker 那一段同形:派工文從 stdin 餵、
# 逾時 124、起不來 127。stdout 是交付物(REVIEW.md),stderr 另存 reviewer.log。單票與批次共用。
run_reviewer() {   # $1 = 命令 $2 = 派工文 $3 = cwd $4 = 時限 $5 = 票號 $6 = 第幾輪 $7 = REVIEW.md $8 = log;印出 rc
    python3 - "$@" <<'PY'
import os, signal, subprocess, sys, time
cmd, dispatch, cwd, timeout, ident, r, out_path, log_path = sys.argv[1:9]
env = dict(os.environ)
env.update({"AC_DISPATCH": dispatch, "AC_TICKET": ident, "AC_ROUND": r,
            "AC_WORK": cwd, "AC_ROLE": "reviewer"})


def descendants(top):
    done = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True)
    kids = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [top]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def kill_tree(proc):
    # 逾時收整棵樹(#87 A14,與 auto-fix.sh 的 `run_agent` 同一手):`setsid()` 自開 session
    # 的後代不在 reviewer 的行程群組裡,`killpg` 碰不到 —— 先拍下子孫樹再一個一個送。
    tree = descendants(proc.pid)
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, None)):
        tree += [pid for pid in descendants(proc.pid) if pid not in tree]
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass
        for pid in tree:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        if grace:
            time.sleep(0.2)


try:
    with open(dispatch, encoding="utf-8") as handle, \
            open(out_path, "w", encoding="utf-8") as out, \
            open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdin=handle,
                                stdout=out, stderr=log, start_new_session=True)
        try:
            rc = proc.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise
except subprocess.TimeoutExpired:
    sys.stderr.write("review: reviewer 超過 %s 秒還沒回來 —— 當它沒交\n" % timeout)
    rc = 124
except OSError as exc:
    sys.stderr.write("review: reviewer 起不來 —— %s\n" % exc)
    rc = 127
print(rc)
PY
}

reviewer_run() {   # $1 = 第幾輪  $2… = 事件多帶的旗標;起一次 reviewer(REVIEWER_CMD → REVIEW / RLOG),設定 RRC / RSECS
    rr_round=$1
    shift
    ev agent.start --ticket "$ID" --role reviewer --model "$MODEL" ${R_EFFORT:+--kv effort=$R_EFFORT} \
        --kv run_id="$RUN_ID" --kv round="$rr_round" --kv agent=review "$@"
    # 上一次派(同一個 run_id 重派、或換候選前那一次)留下的信封不准被這一次的成本讀到。
    rm -f "$RUNDIR/reviewer.json"
    RSTART=$(date +%s)
    RRC=$(run_reviewer "$REVIEWER_CMD" "$DISPATCH" "$CWD" "$REVIEWER_TIMEOUT" "$ID" "$rr_round" \
            "$REVIEW" "$RLOG")
    RSECS=$(( $(date +%s) - RSTART ))
    if [ "$RRC" -eq 0 ]; then
        ev agent.done --ticket "$ID" --role reviewer --model "$MODEL" \
            --kv run_id="$RUN_ID" --kv round="$rr_round" --kv rc="$RRC" --kv agent=review "$@"
    else
        ev agent.failed --ticket "$ID" --role reviewer --model "$MODEL" \
            --kv run_id="$RUN_ID" --kv round="$rr_round" --kv rc="$RRC" --kv agent=review "$@"
    fi
}

fallback_wanted() {   # uses RRC / REVIEW / RLOG;provider 說額度用完 / 不可用才回 0,設定 Q_KIND / Q_DETAIL / Q_FILE
    # stdout(REVIEW.md,codex / claude 的錯誤信封在這裡)與 stderr(reviewer.log)各判一次。
    [ "$RRC" -ne 0 ] && [ "$RRC" -ne 124 ] || return 1
    for Q_FILE in "$REVIEW" "$RLOG"; do
        [ -f "$Q_FILE" ] || continue
        eval "$(python3 "$AC/role-models.py" classify --evidence "$Q_FILE" --rc "$RRC" 2>/dev/null \
            | python3 -c 'import json, shlex, sys
try:
    got = json.load(sys.stdin)
except ValueError:
    got = {}
print("Q_KIND=%s" % shlex.quote(str(got.get("kind") or "other")))
print("Q_DETAIL=%s" % shlex.quote(str(got.get("detail") or "")))')"
        case $Q_KIND in
            quota_exhausted|provider_unavailable) return 0 ;;
            auth_config) echo "review: #$ID reviewer 的認證設定有問題,不換候選:$Q_DETAIL" >&2; return 1 ;;
        esac
    done
    return 1
}

# 執行中撞額度 / 不可用(#132 B2,與 auto-fix 的 worker 同一套 #121):判讀只用 `role-models.py classify`
# (逾時、一般失敗、字面 429 不換)。可換 → 這一次照記 cost(outcome=missing,note 記原因)、輸出與 log
# 改名留著,依表取下一個候選、規則包照新模型重組派工文($2),同一輪重起,收最後那一次的結論。
# 有 Controller 只發 `agent.recovery_needed`;整串耗盡由呼叫端停(RF_STOP)。
reviewer_fallback() {   # $1 = 寫 cost 的函式  $2 = 重組派工文的函式  $3 = 第幾輪  $4… = 事件多帶的旗標;回 1 = 停(RF_STOP)
    rf_cost=$1
    rf_packet=$2
    rf_round=$3
    shift 3
    RF_STOP=""
    while fallback_wanted; do
        "$rf_cost" missing "$Q_KIND:$Q_DETAIL"
        rf_was=$MODEL
        rf_kind=$Q_KIND
        rf_index=$R_INDEX
        pick_reviewer --exhausted-index "$R_INDEX" --evidence "$Q_FILE" --evidence-rc "$RRC"
        case $R_RC in
            0) ;;
            3) RF_STOP=exhausted; return 1 ;;
            *) RF_STOP=refused; return 1 ;;
        esac
        if [ "${AC_RECOVERY_OWNER:-}" = controller ]; then
            ev agent.recovery_needed --ticket "$ID" --role reviewer --model "$MODEL" --kv run_id="$RUN_ID" \
                --kv round="$rf_round" --kv tool="$R_TOOL" --kv candidate_index="$R_INDEX" \
                ${R_EFFORT:+--kv effort=$R_EFFORT} --kv copy="$CWD" --kv dispatch="$DISPATCH" \
                "$@" --note "$rf_kind"
            echo "review: #$ID reviewer $rf_was 撞到 $rf_kind —— 下一個候選 $MODEL 交給 Controller 接續"
            RF_STOP=handed
            return 1
        fi
        echo "review: #$ID reviewer $rf_was 撞到 $rf_kind —— 依表換第 $R_INDEX 格 $MODEL,同一輪重起"
        use_reviewer || { RF_STOP=refused; return 1; }
        mv "$REVIEW" "$RUNDIR/REVIEW-c$rf_index.md" 2>/dev/null
        mv "$RLOG" "$RUNDIR/reviewer-c$rf_index.log" 2>/dev/null
        "$rf_packet" || { RF_STOP=refused; return 1; }
        reviewer_run "$rf_round" "$@"
    done
    return 0
}

# `--output-format json` 的 stdout 是一個信封 `{"type":"result","result":"<全文>",…}`:
# 全文拆出來放回 REVIEW.md(`## result` 那一塊才看得到行首),信封原樣留在 reviewer.json
# (token 用量在裡面)。不是信封就原樣。
unwrap_envelope() {   # $1 = REVIEW.md  $2 = 信封留在哪(reviewer.json)
    python3 - "$1" "$2" <<'PY'
import json, sys
path, envelope = sys.argv[1:3]
with open(path, encoding="utf-8", errors="replace") as handle:
    raw = handle.read()
try:
    data = json.loads(raw)
except ValueError:
    data = None
if isinstance(data, dict) and isinstance(data.get("result"), str):
    with open(envelope, "w", encoding="utf-8") as handle:
        handle.write(raw)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data["result"])
PY
}

# 判 verdict(單票與批次共用):沒交件的四種樣子都**不是 pass**(§5.5)。
read_verdict() {   # $1 = result json  $2 = REVIEW.md  $3 = reviewer rc;設定 VERDICT / WHY
    VERDICT=""; WHY=""
    eval "$(python3 - "$1" "$2" "$3" "$REVIEWER_TIMEOUT" <<'PY'
import json, shlex, sys
result, review, rc, timeout = sys.argv[1:5]
try:
    with open(result, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
try:
    with open(review, encoding="utf-8", errors="replace") as handle:
        lines = [line for line in handle.read().splitlines() if line.startswith("OBJECTION:")]
except OSError:
    lines = []
verdict, why = "", ""
if rc == "124":
    why = "逾時(reviewer.timeout_seconds=%s)" % timeout
elif not data.get("present"):
    why = "%s(reviewer rc=%s)" % (data.get("reason") or "沒有 result", rc)
else:
    said = str(data.get("verdict") or "").strip().lower()
    if said not in ("pass", "fail"):
        why = "verdict=%r 不是 pass|fail" % data.get("verdict")
    elif said == "pass" and lines:
        verdict, why = "fail", "verdict 說 pass,但有 %d 行 OBJECTION:—— 以那幾行為準" % len(lines)
    else:
        verdict = said
print("VERDICT=%s" % shlex.quote(verdict))
print("WHY=%s" % shlex.quote(why))
PY
)"
}

# ------------------------------------------------------------------ batch
# 批次修復的覆核(#108,D-H51 ②):批次 worker 在整合分支上的 commit(#145,D-054 Q9),連同
# `apply.sh --batch` 補的 code-map 重產 = `<base>..<整合分支頭>` 的 diff。`<base>` = `--base`
# (auto-fix 給派工前的頭);沒給 = 整合分支 first-parent 上最近一個 merge(最後併進來的那張票)。
# 與單票那一條不同:**不寫票**(不 `set review`、不記反駁、不轉 Blocked —— 任何一張票的
# `state_version` 一動,N 張的覆核同時過期);pass 寫 `reports/batch/<stamp>/review.json`,
# sha 是派工那一刻的整合分支頭(不是哪一張票的分支頭);fail / 沒交件一頁 decision 掛第一張票、
# 內文列整批。成本一列只記在第一張票(note 帶 batch <stamp> 與全部票號)。
# 退出碼同單票:0 pass、1 fail、2 用法 / 前提、3 沒交件。
if [ -n "$BATCH" ]; then
    for b_id in $B_IDS; do
        [ -f "$TDIR/$b_id.json" ] || { echo "review: 找不到票 #$b_id($TDIR/$b_id.json)" >&2; exit 2; }
    done
    B_TAGS=$(for b_id in $B_IDS; do printf '#%s ' "$b_id"; done)
    B_TAGS=${B_TAGS% }
    RUNDIR=$ROOT/$(cfg reports_dir reports)/batch/$RUN_ID
    mkdir -p "$RUNDIR"
    DISPATCH=$RUNDIR/dispatch-reviewer.md
    REVIEW=$RUNDIR/REVIEW.md
    RLOG=$RUNDIR/reviewer.log
    RESULT_JSON=$RUNDIR/result-reviewer.json
    SHA=$(git -C "$ROOT" rev-parse -q --verify "$B_BR^{commit}" 2>/dev/null || echo "")
    [ -n "$SHA" ] || refuse "整合分支 $B_BR 不存在" "先 sh scripts/auto-fix.sh --batch $B_BR $B_IDS"
    [ -n "$B_BASE" ] || B_BASE=$(git -C "$ROOT" rev-list --first-parent --merges -n 1 "$SHA")
    [ -n "$B_BASE" ] && git -C "$ROOT" merge-base --is-ancestor "$B_BASE" "$SHA" 2>/dev/null \
        || refuse "base ${B_BASE:-(空)} 不在整合分支 $B_BR 的歷史上" \
                  "給 --base <批次 worker 派工前的頭> 再覆核"
    B_BASE=$(git -C "$ROOT" rev-parse "$B_BASE")
    [ "$(git -C "$ROOT" rev-list --count "$B_BASE..$SHA")" -gt 0 ] \
        || refuse "整合分支 $B_BR 相對 base $(echo "$B_BASE" | cut -c1-12) 沒有批次修復的 commit" \
                  "先 sh scripts/auto-fix.sh --batch $B_BR $B_IDS(worker 在整合分支上 commit),再覆核"
    TEMPLATE=""
    for candidate in "$ROOT/templates/dispatch-reviewer.md" "$AC/../templates/dispatch-reviewer.md"; do
        [ -f "$candidate" ] && { TEMPLATE=$candidate; break; }
    done
    [ -n "$TEMPLATE" ] || refuse "找不到 templates/dispatch-reviewer.md" \
        "從 agent-control 複製 templates/dispatch-reviewer.md 到 $ROOT/templates/"
    CWD=$(git -C "$ROOT" worktree list --porcelain | awk -v ref="branch refs/heads/$B_BR" '
        /^worktree / { here = substr($0, 10) } $0 == ref { print here; exit }')
    CWD=${CWD:-$ROOT}
    review_rules \
        || refuse "規則包產不出來(模型 ${RULES_MODEL:-未指定})" \
                  "補齊 rules.py 列的缺項(decision 頁)再跑 sh scripts/review.sh --batch $B_BR $B_IDS --run-id $RUN_ID --base $B_BASE"
    batch_packet() {   # 派工文 = 規則包 + 這一批的那一段;換候選(#132)時先重產規則包再組一次
        {
            printf '%s\n' "$RULES_TEXT"
            python3 - "$TEMPLATE" "$TDIR" "$ROOT" "$B_BR" "$SHA" "$REVIEW" "$MODEL" "$RUN_ID" "$B_BASE" $B_IDS <<'PY'
import json, os, subprocess, sys
template, tdir, root, branch, sha, review, model, stamp, base = sys.argv[1:10]
ids = sys.argv[10:]
tags = " ".join("#%s" % ident for ident in ids)
print("")
print("# 批次修復的覆核:%s(整合分支 `%s`)" % (tags, branch))
print("")
print("這一批 land 時全套紅了;一個 worker 在整合分支上修了紅榜,修法是整合分支上 `%s..%s` 的 commit。"
      "你覆核的是**這一段的 diff**:有沒有把這批票各自的驗收修壞、有沒有放寬測試、"
      "有沒有動到與修紅無關的檔。各票分支已各自覆核過,不重看。" % (base[:12], sha[:12]))
print("")
print("## 這一批獨有的四件事")
print("1. **票號**:%s(票面在下面;不寫任何一張票)" % tags)
print("2. **sha**:`%s`(整合分支 `%s` 的頭;修復前是 `%s`)" % (sha, branch, base))
print("3. **紀錄目錄**:`%s`(worker 的 EVIDENCE / result 都在這裡)"
      % os.path.relpath(os.path.dirname(review), root))
print("4. **交付**:你的整份回覆就是 REVIEW.md,存到 `%s`" % review)
print("")
print("## 票面(%d 張)" % len(ids))
for ident in ids:
    with open(os.path.join(tdir, "%s.json" % ident), encoding="utf-8") as handle:
        row = json.load(handle)
    print("")
    print("### #%s %s" % (ident, row.get("subject") or ""))
    print("- objective:%s" % (row.get("objective") or "(空)"))
    print("- acceptance:")
    for item in row.get("acceptance") or []:
        print("  - %s" % item)
diff = subprocess.run(["git", "-C", root, "diff", base, sha],
                      capture_output=True, text=True).stdout
print("")
print("## 批次修復的 diff(`git diff %s %s`)" % (base[:12], sha[:12]))
print("```diff")
print(diff.rstrip("\n") or "(空的 diff)")
print("```")
with open(template, encoding="utf-8") as handle:
    text = handle.read()
start = text.find("## 交付:固定格式")
end = text.find("## 之後誰做什麼")
section = text[start:end if end > start else len(text)] if start >= 0 else ""
for key, value in (("@REVIEW@", review), ("@TICKET@", ids[0]), ("@ROUND@", "1"),
                   ("@MODEL@", model), ("@SHA@", sha)):
    section = section.replace(key, value)
print("")
print(section.rstrip("\n"))
print("")
print("## 之後誰做什麼(你不用做)")
print("`scripts/review.sh --batch` 讀這一塊:`pass` ⇒ 寫 `reports/batch/%s/review.json`"
      "(sha `%s`),呼叫端重跑全套;`fail` ⇒ 一頁 decision 給主線裁示。**兩條都不寫票。**"
      % (stamp, sha[:12]))
PY
        } > "$DISPATCH"
    }
    batch_repack() { review_rules && batch_packet; }
    batch_packet
    echo "review: 批次 $B_TAGS 派覆核 —— $REVIEWER_CMD(cwd $CWD,$B_BR @ $(echo "$SHA" | cut -c1-12),派工文 $(rel "$DISPATCH"))"
    batch_cost() {   # $1 = 結局 pass|fail|missing  $2 = 原因(可空,換候選那一次記 quota 種類)
        _attempt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("attempt") or 0)' "$TF" 2>/dev/null || echo 0)
        python3 "$AC/ticket.py" cost "$ID" --role reviewer --round "$_attempt" --model "$MODEL" \
            --by review.sh --from-envelope "$RUNDIR/reviewer.json" --wall-seconds "$RSECS" \
            --outcome "$1" --run-id "${AC_BATCH_RUN_ID:-$RUN_ID}" --note "batch $RUN_ID $B_TAGS${2:+;$2}" \
            --effort "$R_EFFORT" \
            >/dev/null 2>&1 || echo "review: #$ID 的 cost 寫不進票 —— 不擋覆核" >&2
    }
    rm -f "$RUNDIR/review.json"
    reviewer_run 1 --kv batch="$B_TAGS"
    if ! reviewer_fallback batch_cost batch_repack 1 --kv batch="$B_TAGS"; then
        case $RF_STOP in
            exhausted)
                echo "review: 批次 $B_TAGS 的 reviewer 候選整串耗盡($R_REASON)—— 不再起" >&2
                ev review.missing --ticket "$ID" --kv run_id="$RUN_ID" --kv batch="$B_TAGS" \
                    --note "reviewer 候選整串耗盡($R_REASON)"
                post "批次 reviewer 候選整串耗盡($B_TAGS)" \
                     "整批 $B_TAGS 的 reviewer 候選都撞到額度 / 不可用($R_REASON);改表或等額度恢復後重派 sh scripts/review.sh --batch $B_BR $B_IDS --run-id $RUN_ID --base $B_BASE" \
                     "board/role-models.json"
                exit 7 ;;
            handed) exit 7 ;;
            *) refuse "換候選後的 reviewer $MODEL 組不出命令或規則包產不出來" \
                      "改表或補規則包後重派 sh scripts/review.sh --batch $B_BR $B_IDS --run-id $RUN_ID --base $B_BASE" ;;
        esac
    fi
    unwrap_envelope "$REVIEW" "$RUNDIR/reviewer.json"
    python3 "$AC/ticket.py" result "$REVIEW" "$RESULT_JSON" --ticket "$ID" --role reviewer \
        --round 1 >/dev/null || echo "review: result 抽不出來($REVIEW)" >&2
    read_verdict "$RESULT_JSON" "$REVIEW" "$RRC"
    REL_REVIEW=$(rel "$REVIEW")
    if [ -z "$VERDICT" ]; then
        echo "review: 批次 $B_TAGS 覆核沒交件 —— $WHY;review.json 不寫" >&2
        ev review.missing --ticket "$ID" --kv run_id="$RUN_ID" --kv batch="$B_TAGS" --note "沒交不是 pass($WHY)"
        batch_cost missing
        post "批次覆核沒交件($B_TAGS)" \
             "整批 $B_TAGS 的批次修復(頭 $(echo "$SHA" | cut -c1-12)) 在 $B_BR 上沒有覆核結論($WHY):看 $(rel "$RLOG") 與 $REL_REVIEW,再重派 sh scripts/review.sh --batch $B_BR $B_IDS --run-id $RUN_ID --base $B_BASE" \
             "$REL_REVIEW"
        exit 3
    fi
    if [ "$VERDICT" = "pass" ]; then
        python3 - "$RUNDIR/review.json" "$BY" "$SHA" "$REL_REVIEW" <<'PY' || { echo "review: review.json 寫不出來" >&2; exit 4; }
import datetime, json, sys
path, by, sha, note = sys.argv[1:5]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"verdict": "pass", "sha": sha, "by": by,
               "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "note": note}, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
        batch_cost pass
        echo "review: 批次 $B_TAGS 覆核通過($BY,$B_BR @ $(echo "$SHA" | cut -c1-12))—— $(rel "$RUNDIR/review.json")"
        ev review.pass --ticket "$ID" --kv run_id="$RUN_ID" --kv by="$BY" \
            --kv sha="$(echo "$SHA" | cut -c1-12)" --kv batch="$B_TAGS" --note "$REL_REVIEW"
        exit 0
    fi
    batch_cost fail
    ev review.fail --ticket "$ID" --kv run_id="$RUN_ID" --kv by="$BY" --kv batch="$B_TAGS" --note "$REL_REVIEW"
    ev decision.asked --ticket "$ID" --note "批次 $B_TAGS 覆核退回($BY)"
    echo "review: 批次 $B_TAGS 覆核退回($BY)—— 票檔沒動,一頁 decision 掛 #$ID${WHY:+;$WHY}" >&2
    post "批次覆核退回($B_TAGS)" \
         "裁示:整批 $B_TAGS 的批次修復(頭 $(echo "$SHA" | cut -c1-12)) 在 $B_BR 上被覆核退回;讀 $REL_REVIEW 的 OBJECTION: 行,決定重派批次修復或拆批。各票分支與票檔都沒動" \
         "$REL_REVIEW"
    exit 1
fi

if [ -z "$RUN_ID" ]; then
    RUN_ID=$(python3 -c 'import os,sys; sys.path.insert(0, sys.argv[1]); import status; print(status.latest_run(sys.argv[2], sys.argv[3]))' \
        "$AC" "$ROOT" "$ID" 2>/dev/null || echo "")
    [ -n "$RUN_ID" ] || RUN_ID=$(date +%Y%m%d-%H%M%S)-$$
fi
RUNDIR=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID
mkdir -p "$RUNDIR"
DISPATCH=$RUNDIR/dispatch-reviewer.md
REVIEW=$RUNDIR/REVIEW.md
RLOG=$RUNDIR/reviewer.log

# 只在 InReview / AwaitingReview 派:Running 的票還在改、Blocked 的票在等裁示 —— 在哪一種
# 上蓋一張 pass 都是替一份還沒定案的東西作保。AwaitingReview 是下游專案閘門綠寫的字面值
# (D-032);這裡讀票 JSON 的字面值,ticket.py 的 STATES 不認它。
STATE=$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1],encoding="utf-8")).get("state") or ""))' "$TF" 2>/dev/null || echo "")
case "$STATE" in
    InReview|AwaitingReview) ;;
    *) refuse "票的 state=${STATE:-(空)},不是 InReview / AwaitingReview" \
    "閘門綠之後票才轉 InReview;要覆核就先確認閘門綠:sh scripts/gate.sh --branch --ticket $ID" ;;
esac
# 只有 complex 的票做完後覆核(#136);沒標 = simple。判準與 land / close 同一支 `needs_review`。
if ! python3 - "$AC" "$TF" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
import ticket as ticket_mod
with open(sys.argv[2], encoding="utf-8") as handle:
    sys.exit(0 if ticket_mod.needs_review(json.load(handle)) else 1)
PY
then
    refuse "票的 complexity 不是 complex(沒標 = simple)—— 只有 complex 的票做完後覆核" \
        "simple 票閘門綠就等 land,不要 review;要覆核就先 python3 scripts/ticket.py set $ID complexity complex"
fi

# review 記的就是**這一刻**的頭:派工文寫它、pass 寫它 —— 比事後再 rev-parse 一次、蓋上一個
# 沒人讀過的 sha 誠實(給人看;#136 起 land 不拿它比分支頭)。
# 分支名**只有一個來源:票的 `branch` 欄**(#53,D-018;`apply.sh` 收件時填),空著才 `t<n>`。
BR=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("branch") or "t"+sys.argv[2])' "$TF" "$ID" 2>/dev/null || echo "t$ID")
SHA=$(git -C "$ROOT" rev-parse -q --verify "$BR^{commit}" 2>/dev/null || echo "")
[ -n "$SHA" ] || refuse "分支 $BR 不存在" \
    "先 sh scripts/auto-fix.sh $ID(它建分支 ${BR}、worker 在上面 commit),閘門綠了再覆核"

TEMPLATE=""
for candidate in "$ROOT/templates/dispatch-reviewer.md" "$AC/../templates/dispatch-reviewer.md"; do
    [ -f "$candidate" ] && { TEMPLATE=$candidate; break; }
done
[ -n "$TEMPLATE" ] || refuse "找不到 templates/dispatch-reviewer.md" \
    "從 agent-control 複製 templates/dispatch-reviewer.md 到 $ROOT/templates/,再 sh scripts/review.sh $ID"

# 四件事的另外兩件:worktree(問 git,不照命名規則猜)、EVIDENCE 與狀態檔(問狀態檔)。
WT=""; STATUS_FILE=""; EVIDENCE=""; ROUND=1
eval "$(python3 - "$AC" "$ROOT" "$ID" "$RUN_ID" "$BR" <<'PY'
import glob, os, shlex, subprocess, sys
ac, root, ident, run_id, branch = sys.argv[1:6]
sys.path.insert(0, ac)
import status

listing = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
worktree, here = "", ""
for line in listing.splitlines():
    if line.startswith("worktree "):
        here = line[len("worktree "):]
    elif line == "branch refs/heads/%s" % branch:
        worktree = here
        break

own = status.read(root, ident, run_id) or {}
ctx = own.get("repair_context") or {}
state_file = status.path_for(root, ident, run_id) if own else ""
evidence = ctx.get("prev_evidence") or ""
# 手跑閘門的那一輪沒帶 EVIDENCE:往回找這張票最近一次記得到的(auto-fix 的閘門記在
# `prev_evidence`;apply 記的是 patch,EVIDENCE 住在它旁邊)。找不到就說找不到。
if not evidence:
    for name in reversed(status.runs_of(root, ident)):
        other = (status.read(root, ident, name) or {}).get("repair_context") or {}
        if other.get("prev_evidence"):
            evidence = other["prev_evidence"]
            break
        patch = (other.get("patch") or {}).get("path") or ""
        found = sorted(glob.glob(os.path.join(os.path.dirname(patch), "EVIDENCE*.md"))) if patch else []
        if found:
            evidence = found[-1]
            break


def out(name, value):
    print("%s=%s" % (name, shlex.quote(str(value))))


out("WT", worktree)
out("STATUS_FILE", state_file)
out("EVIDENCE", evidence)
out("ROUND", ctx.get("round") or 1)
PY
)"
CWD=${WT:-$ROOT}
RESULT_JSON=$RUNDIR/result-reviewer-round$ROUND.json

# 規則包先產、成功才組派工文(#74):產不出來時 rules.py 已經非零、stdout 沒有半包、送了
# decision 頁 —— 這裡不准 fallback 起 reviewer。命令裡沒寫 `--model`(MODEL=unknown)就不指定
# 模型卡:指定一張不存在的卡是錯,而「不知道是哪個模型」不是指定。
review_rules \
    || refuse "規則包產不出來(模型 ${RULES_MODEL:-未指定})" \
              "補齊 rules.py 列的缺項(decision 頁)再跑 sh scripts/review.sh $ID"
review_packet() {   # 派工文 = 規則包 + 範本四件事填好;換候選(#132)時先重產規則包再組一次
    {
        printf '%s\n' "$RULES_TEXT"
        python3 - "$TEMPLATE" "$ID" "$SHA" "$WT" "$EVIDENCE" "$STATUS_FILE" "$REVIEW" \
            "$MODEL" "$ROUND" "$(rel "$TF")" "$TF" <<'PY'
import json, sys
template, ident, sha, wt, evidence, state_file, review, model, rnd, ticket_file, tf = sys.argv[1:12]
with open(template, encoding="utf-8") as handle:
    text = handle.read()
# 「已對 <sha> 跑過閘門」只在票上 `gate.sha` 就是分支頭時說(#56,D-H38)。以前範本無條件
# 說「閘門已經對這個 sha 跑過」,而 auto-fix 在副本跑的綠沒寫回票 —— 覆核者被告知不必
# 看紅榜,實際上這個 sha 沒有人跑過(#661)。
try:
    with open(tf, encoding="utf-8") as handle:
        row = json.load(handle)
except (OSError, ValueError):
    row = {}
gate = row.get("gate")
gate = gate if isinstance(gate, dict) else {}
if gate.get("sha") == sha:
    gate_line = "已對 %s 跑過閘門(rc=%s,run %s)" % (sha, gate.get("rc"), gate.get("run_id") or "?")
else:
    gate_line = ("這個 sha 沒跑過閘門,覆核者要自己看紅榜(票上 gate.sha=%s,分支頭 %s)"
                 % ((gate.get("sha") or "(空)")[:12], sha[:12]))
    text = text.replace("(閘門已經對這個 sha 跑過,重跑是把\n同一份綠買第二遍)",
                        "(這個 sha 沒跑過閘門 —— 見下面「閘門」那一行)")
marker = "## 這張票獨有的四件事"
if marker in text:
    text = text.replace(marker, "**閘門**:%s\n\n%s" % (gate_line, marker), 1)
else:
    text = text.rstrip("\n") + "\n\n**閘門**:%s\n" % gate_line
slots = {
    "@TICKET@": ident,
    "@TICKET_FILE@": ticket_file,
    "@SHA@": sha,
    "@WORKTREE@": wt or "(分支 t%s 沒有 worktree —— 用 git show %s:<檔> 讀)" % (ident, sha[:12]),
    "@EVIDENCE@": evidence or "(狀態檔沒記到 EVIDENCE —— 覆核照讀 code,疑慮清單寫一句「沒有 EVIDENCE」)",
    "@STATUS@": state_file or "(這一輪沒有 status.json)",
    "@REVIEW@": review,
    "@MODEL@": model,
    "@ROUND@": rnd,
}
for key, value in slots.items():
    text = text.replace(key, value)
sys.stdout.write("\n" + text)
PY
    } > "$DISPATCH"
}
review_repack() { review_rules && review_packet; }
review_packet
echo "review: #$ID 派覆核 —— $REVIEWER_CMD(cwd $CWD,分支 $BR @ $(echo "$SHA" | cut -c1-12),派工文 $(rel "$DISPATCH"))"

write_cost() {   # $1 = 結局 pass|fail|missing(與 review.* 同字,#94 C5d) $2 = 附註(可省)
    # 覆核者的 token 與時鐘進票的 `cost[]`(D-032)。pass / fail / 沒交件都寫;
    # pass 那條路要寫在 `set review` **之後**:順序照事實,覆核先落,成本後記。
    _attempt=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8")).get("attempt") or 0)' "$TF" 2>/dev/null || echo 0)
    if [ -n "${2:-}" ]; then
        set -- "$1" --note "$2"
    fi
    python3 "$AC/ticket.py" cost "$ID" --role reviewer --round "$_attempt" --model "$MODEL" \
        --by review.sh --from-envelope "$RUNDIR/reviewer.json" --wall-seconds "$RSECS" \
        --effort "$R_EFFORT" --outcome "$@" --run-id "$RUN_ID" >/dev/null 2>&1 \
        || echo "review: #$ID 的 cost 寫不進票 —— 不擋覆核" >&2
}

reviewer_run "$ROUND"
# 執行中撞額度(#132 B2):停在這裡的每一種,票的 review 一格不動。
if ! reviewer_fallback write_cost review_repack "$ROUND"; then
    case $RF_STOP in
        handed) exit 7 ;;
        exhausted) RF_WHY="reviewer 候選整串耗盡($R_REASON)" ;;
        *) RF_WHY="換候選後的 reviewer $MODEL 組不出命令或規則包產不出來" ;;
    esac
    [ "$RF_STOP" = exhausted ] || refuse "$RF_WHY" "改表或補規則包後重派 sh scripts/review.sh $ID --run-id <新的>"
    echo "review: #$ID 的 $RF_WHY —— 不再起,票轉 Blocked" >&2
    python3 "$AC/ticket.py" set "$ID" state Blocked >/dev/null 2>&1 \
        || echo "review: 票狀態改不動(Blocked)" >&2
    python3 "$AC/ticket.py" set "$ID" owner main >/dev/null 2>&1 || true
    ev review.missing --ticket "$ID" --kv run_id="$RUN_ID" --note "$RF_WHY"
    ev decision.asked --ticket "$ID" --note "#$ID 的 $RF_WHY"
    post "reviewer 候選整串耗盡" \
         "#$ID 的 reviewer 候選都撞到額度 / 不可用($R_REASON);改表或等額度恢復後重派 sh scripts/review.sh $ID --run-id <新的>" \
         "board/role-models.json"
    exit 7
fi

unwrap_envelope "$REVIEW" "$RUNDIR/reviewer.json"

python3 "$AC/ticket.py" result "$REVIEW" "$RESULT_JSON" --ticket "$ID" --role reviewer \
    --round "$ROUND" >/dev/null || echo "review: result 抽不出來($REVIEW)" >&2

read_verdict "$RESULT_JSON" "$REVIEW" "$RRC"
REL_REVIEW=$(rel "$REVIEW")

if [ -z "$VERDICT" ]; then
    echo "review: #$ID 覆核沒交件 —— $WHY;不寫 review,票留 InReview" >&2
    ev review.missing --ticket "$ID" --kv run_id="$RUN_ID" \
        --note "沒交不是 pass($WHY):看 $(rel "$RLOG") 與 $REL_REVIEW,再重派 sh scripts/review.sh $ID"
    write_cost missing
    exit 3
fi

if [ "$VERDICT" = "pass" ]; then
    VALUE=$(python3 -c 'import json,sys; print(json.dumps({"verdict": "pass", "by": sys.argv[1], "sha": sys.argv[2], "note": sys.argv[3]}, ensure_ascii=False))' \
        "$BY" "$SHA" "$REL_REVIEW")
    python3 "$AC/ticket.py" set "$ID" review "$VALUE" >/dev/null || {
        echo "review: #$ID 的 review 寫不進票" >&2
        exit 4
    }
    write_cost pass
    echo "review: #$ID 覆核通過($BY,sha $(echo "$SHA" | cut -c1-12))—— review 已寫進票"
    ev review.pass --ticket "$ID" --kv run_id="$RUN_ID" --kv by="$BY" \
        --kv sha="$(echo "$SHA" | cut -c1-12)" --note "$REL_REVIEW"
    exit 0
fi

# fail:每一行 `OBJECTION:` 一筆 blocking(多條就多次呼叫既有的 `ticket.py objection`);
# 一行都沒有也要有一筆 —— 「fail 但沒有阻擋項」會讓 land 以為沒有東西擋著。
COUNT=$(python3 - "$AC" "$ID" "$REVIEW" "$BY" "$TF" <<'PY'
import json, subprocess, sys
ac, ident, review, owner, tf = sys.argv[1:6]
KNOWN = ("ticket-wrong", "test_defect", "blocking")
with open(review, encoding="utf-8", errors="replace") as handle:
    lines = [line for line in handle.read().splitlines() if line.startswith("OBJECTION:")]
bodies = []
for line in lines:
    rest = line.split(":", 1)[1].strip()
    parts = rest.split(None, 1)
    if parts and parts[0] in KNOWN:
        rest = parts[1].strip() if len(parts) > 1 else ""
    if rest and rest not in bodies:
        bodies.append(rest)
if not bodies:
    bodies.append("覆核 fail 但沒有逐條理由 —— 讀 REVIEW.md")
for body in bodies:
    subprocess.run([sys.executable, "%s/ticket.py" % ac, "objection", ident,
                    "--line", "OBJECTION: blocking %s" % body, "--evidence", review],
                   stdout=subprocess.DEVNULL, check=False)
# 既有的 `objection` 把 owner 記成 main(誰該處置);這幾筆是**誰提的**要看得出來 ——
# 用既有的 `set objections` 補上,不動 `objection` 的契約。
with open(tf, encoding="utf-8") as handle:
    rows = json.load(handle).get("objections") or []
for row in rows:
    if isinstance(row, dict) and row.get("category") == "blocking" \
            and not row.get("disposition") and (row.get("body") or "") in bodies:
        row["owner"] = owner
done = subprocess.run([sys.executable, "%s/ticket.py" % ac, "set", ident, "objections",
                       json.dumps(rows, ensure_ascii=False)],
                      stdout=subprocess.DEVNULL, check=False)
print(len(bodies) if done.returncode == 0 else -1)
PY
)
[ "${COUNT:--1}" -ge 0 ] || { echo "review: #$ID 的反駁寫不進票" >&2; exit 4; }
write_cost fail
python3 "$AC/ticket.py" set "$ID" state Blocked >/dev/null 2>&1 \
    || echo "review: 票狀態改不動(Blocked)" >&2
python3 "$AC/ticket.py" set "$ID" owner main >/dev/null 2>&1 || true
ev review.fail --ticket "$ID" --kv run_id="$RUN_ID" --kv by="$BY" --note "$REL_REVIEW"
ev decision.asked --ticket "$ID" --note "#$ID 覆核退回($BY):$COUNT 條阻擋"
echo "review: #$ID 覆核退回($BY)—— $COUNT 條阻擋記進 objections[],票轉 Blocked${WHY:+;$WHY}"
post "覆核退回($COUNT 條阻擋)" \
     "裁示:讀 $REL_REVIEW 與票的 objections[],逐筆處置(accepted / rejected / deferred / fixed);沒處置的阻擋項 land 與 close 都會拒絕" \
     "$REL_REVIEW"
exit 1
