#!/bin/sh
# 覆核自動派 — `docs/DESIGN-MAIN-TOUCHPOINTS.md` C2(D-025 ②、D-022)。
#
#   sh scripts/review.sh <票號>                  # 票要是 InReview、分支 t<票號> 要在
#   sh scripts/review.sh <票號> --run-id <id>    # 派工文與 REVIEW.md 落在那一輪的目錄
#
# 閘門綠、票轉 `InReview` 之後由兩處叫這一支:`auto-fix.sh` 第 r 輪綠(`--no-review` 關)
# 與手跑 `gate.sh --branch --ticket <n>` 綠。主線不再組派工文、不再讀 patch、不再手抄 sha。
#
# ## 它做的事
# 派工文 = `rules.py pack reviewer --model <模型>` + `templates/dispatch-reviewer.md`(四件事
# 填好),寫到 `reports/t<n>/<run_id>/dispatch-reviewer.md`,從 stdin 餵給 `board/config.json`
# 的 `reviewer.command`(headless、唯讀:預設的工具白名單沒有 Edit / Write)。它的 stdout 就是
# 同一目錄的 `REVIEW.md`;檔尾 `## result` 由 `ticket.py result` 抽(與 worker 同一支,#29 A4):
#   pass   → `ticket.py set <n> review {verdict, by: reviewer@<模型>, sha: 派工那一刻的分支頭, note}`
#            —— `state_version` 由 set 自己蓋,land 照查;
#   fail   → REVIEW.md 每一行 `OBJECTION:` 記成一筆 blocking 反駁(owner=reviewer@<模型>)、
#            票轉 Blocked、inbox 一頁「覆核退回,裁示」;
#   沒交件 → 不寫 review、票留 InReview、inbox 一頁「覆核沒交件」。
#
# ## 「沒交」與 pass 要分得開(`docs/DISPATCH-TEMPLATE.md` §5.5)
# 沒有 `## result`、JSON 解不開、逾時、verdict 不是 pass|fail —— 四種都**不是 pass**。
# 逾時先判、不看內容:被砍之前它可能已經印了一塊像樣的 pass。verdict 說 pass 卻有
# `OBJECTION:` 行時以那幾行為準(同 §8.5「以 OBJECTION 行為準」),走 fail。
#
# ## 模型名取自 `reviewer.command` 的 `--model`,不是 routing 標籤(#21)
# `review.by` 與事件的 model 欄記**實際起的那個**;命令裡沒有 `--model` 就記 `unknown`,不猜。
#
# ## 退出碼
#   0 pass(review 已寫)   1 fail(票 Blocked,等主線裁示)   2 用法 / 前提不成立
#   3 沒交件               4 review 或反駁寫不進票
set -u
# 根的找法與 `auto-fix.sh` 同一套:`AC_ROOT` 優先;往上找 `board/config.json`;
# 在票的 worktree 裡找不到票就改問主 repo(`--git-common-dir` 的上一層)。
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
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

[ $# -ge 1 ] || { echo "用法:sh scripts/review.sh <票號> [--run-id <id>]" >&2; exit 2; }
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

TICKETS=${AC_TICKETS_DIR:-$(cfg tickets_dir tickets)}
case $TICKETS in /*) TDIR=$TICKETS ;; *) TDIR=$ROOT/$TICKETS ;; esac
TF=$TDIR/$ID.json
if [ ! -f "$TF" ] && [ -z "${AC_TICKETS_DIR:-}" ]; then
    _common=$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null || echo "")
    case "$_common" in
        "") MAINROOT=$ROOT ;;
        /*) MAINROOT=$(cd "$(dirname "$_common")" 2>/dev/null && pwd) || MAINROOT=$ROOT ;;
        *)  MAINROOT=$(cd "$ROOT/$(dirname "$_common")" 2>/dev/null && pwd) || MAINROOT=$ROOT ;;
    esac
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

REVIEWER_CMD=$(cfg reviewer.command "claude -p --model opus --allowedTools Read Glob Grep Bash --output-format json")
REVIEWER_TIMEOUT=$(cfg reviewer.timeout_seconds 1800)
MODEL=$(python3 - "$REVIEWER_CMD" <<'PY'
import shlex, sys
try:
    parts = shlex.split(sys.argv[1])
except ValueError:
    parts = sys.argv[1].split()
model = "unknown"
for i, tok in enumerate(parts):
    if tok == "--model" and i + 1 < len(parts):
        model = parts[i + 1]
        break
print(model)
PY
)
BY=reviewer@$MODEL

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
rel() { python3 -c 'import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))' "$1" "$ROOT"; }

ev() {
    python3 "$AC/event.py" emit "$@" >/dev/null \
        || echo "review: 事件發不出去($*)" >&2
}

post() {   # $1 = 狀態  $2 = 要主線做什麼  $3 = 去哪看
    python3 "$AC/inbox.py" post --ticket "$ID" --run-id "$RUN_ID" \
        --kind review --state "$1" --what "$2" --where "$3" \
        || echo "review: inbox 寫不出來($1)" >&2
}

refuse() {   # $1 = 為什麼沒派成  $2 = 下一步;一頁 inbox,rc=2
    echo "review: #$ID 覆核沒派成 —— $1" >&2
    post "覆核沒派成($1)" "$2" "$(rel "$TF")"
    exit 2
}

# 只在 InReview 派:Running 的票還在改、Blocked 的票在等裁示 —— 在哪一種上蓋一張 pass
# 都是替一份還沒定案的東西作保。
STATE=$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1],encoding="utf-8")).get("state") or ""))' "$TF" 2>/dev/null || echo "")
[ "$STATE" = "InReview" ] || refuse "票的 state=${STATE:-(空)},不是 InReview" \
    "閘門綠之後票才轉 InReview;要覆核就先確認閘門綠:sh scripts/gate.sh --branch --ticket $ID"

# review 綁的就是**這一刻**的頭:派工文寫它、pass 寫它。reviewer 讀的時候分支又動了,
# land 比對得出這張章過期 —— 比事後再 rev-parse 一次、蓋上一個沒人讀過的 sha 誠實。
SHA=$(git -C "$ROOT" rev-parse -q --verify "t$ID^{commit}" 2>/dev/null || echo "")
[ -n "$SHA" ] || refuse "分支 t$ID 不存在" \
    "先 sh scripts/apply.sh $ID <patch>(它建分支 t$ID),閘門綠了再覆核"

TEMPLATE=""
for candidate in "$ROOT/templates/dispatch-reviewer.md" "$AC/../templates/dispatch-reviewer.md"; do
    [ -f "$candidate" ] && { TEMPLATE=$candidate; break; }
done
[ -n "$TEMPLATE" ] || refuse "找不到 templates/dispatch-reviewer.md" \
    "從 agent-control 複製 templates/dispatch-reviewer.md 到 $ROOT/templates/,再 sh scripts/review.sh $ID"

# 四件事的另外兩件:worktree(問 git,不照命名規則猜)、EVIDENCE 與狀態檔(問狀態檔)。
WT=""; STATUS_FILE=""; EVIDENCE=""; ROUND=1
eval "$(python3 - "$AC" "$ROOT" "$ID" "$RUN_ID" <<'PY'
import glob, os, shlex, subprocess, sys
ac, root, ident, run_id = sys.argv[1:5]
sys.path.insert(0, ac)
import status

listing = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
worktree, here = "", ""
for line in listing.splitlines():
    if line.startswith("worktree "):
        here = line[len("worktree "):]
    elif line == "branch refs/heads/t%s" % ident:
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

{
    python3 "$AC/rules.py" pack reviewer --model "$MODEL" 2>/dev/null \
        || echo "(規則包產不出來 —— 自己讀 memory/role/reviewer.md)"
    python3 - "$TEMPLATE" "$ID" "$SHA" "$WT" "$EVIDENCE" "$STATUS_FILE" "$REVIEW" \
        "$MODEL" "$ROUND" "$(rel "$TF")" <<'PY'
import sys
template, ident, sha, wt, evidence, state_file, review, model, rnd, ticket_file = sys.argv[1:11]
with open(template, encoding="utf-8") as handle:
    text = handle.read()
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
# 「等覆核」那幾頁的 what 就是這一支接著要做的事 —— 開跑就由這一支收掉(#43,inbox.py 檔頭)。
# 只收 state 含「等覆核」的:同一張票的 Blocked / 裁示頁是主線的,不動。
python3 "$AC/inbox.py" ack "$ID" --state 等覆核 --by review.sh >/dev/null \
    || echo "review: 「等覆核」那幾頁 ack 不掉(不擋覆核)" >&2
echo "review: #$ID 派覆核 —— $REVIEWER_CMD(cwd $CWD,分支 t$ID @ $(echo "$SHA" | cut -c1-12),派工文 $(rel "$DISPATCH"))"

ev agent.start --ticket "$ID" --role reviewer --model "$MODEL" \
    --kv run_id="$RUN_ID" --kv round="$ROUND" --kv agent=review
# stdin / timeout / rc 與 `auto-fix.sh` 起 headless worker 那一段同形:派工文從 stdin 餵、
# 逾時 124、起不來 127。stdout 是交付物(REVIEW.md),stderr 另存 reviewer.log。
RRC=$(python3 - "$REVIEWER_CMD" "$DISPATCH" "$CWD" "$REVIEWER_TIMEOUT" "$ID" "$ROUND" \
        "$REVIEW" "$RLOG" <<'PY'
import os, subprocess, sys
cmd, dispatch, cwd, timeout, ident, r, out_path, log_path = sys.argv[1:9]
env = dict(os.environ)
env.update({"AC_DISPATCH": dispatch, "AC_TICKET": ident, "AC_ROUND": r,
            "AC_WORK": cwd, "AC_ROLE": "reviewer"})
try:
    with open(dispatch, encoding="utf-8") as handle, \
            open(out_path, "w", encoding="utf-8") as out, \
            open(log_path, "w", encoding="utf-8") as log:
        done = subprocess.run(cmd, shell=True, cwd=cwd, env=env, stdin=handle,
                              stdout=out, stderr=log, timeout=float(timeout))
    rc = done.returncode
except subprocess.TimeoutExpired:
    sys.stderr.write("review: reviewer 超過 %s 秒還沒回來 —— 當它沒交\n" % timeout)
    rc = 124
except OSError as exc:
    sys.stderr.write("review: reviewer 起不來 —— %s\n" % exc)
    rc = 127
print(rc)
PY
)
if [ "$RRC" -eq 0 ]; then
    ev agent.done --ticket "$ID" --role reviewer --model "$MODEL" \
        --kv run_id="$RUN_ID" --kv round="$ROUND" --kv rc="$RRC" --kv agent=review
else
    ev agent.failed --ticket "$ID" --role reviewer --model "$MODEL" \
        --kv run_id="$RUN_ID" --kv round="$ROUND" --kv rc="$RRC" --kv agent=review
fi

# `--output-format json` 的 stdout 是一個信封 `{"type":"result","result":"<全文>",…}`:
# 全文拆出來放回 REVIEW.md(`## result` 那一塊才看得到行首),信封原樣留在 reviewer.json
# (token 用量在裡面)。不是信封就原樣。
python3 - "$REVIEW" "$RUNDIR/reviewer.json" <<'PY'
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

python3 "$AC/ticket.py" result "$REVIEW" "$RESULT_JSON" --ticket "$ID" --role reviewer \
    --round "$ROUND" >/dev/null || echo "review: result 抽不出來($REVIEW)" >&2

VERDICT=""; WHY=""
eval "$(python3 - "$RESULT_JSON" "$REVIEW" "$RRC" "$REVIEWER_TIMEOUT" <<'PY'
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
REL_REVIEW=$(rel "$REVIEW")

if [ -z "$VERDICT" ]; then
    echo "review: #$ID 覆核沒交件 —— $WHY;不寫 review,票留 InReview" >&2
    post "覆核沒交件($WHY)" \
         "沒交不是 pass:看 $(rel "$RLOG") 與 $REL_REVIEW,再重派 sh scripts/review.sh $ID" \
         "$(rel "$RUNDIR")"
    exit 3
fi

if [ "$VERDICT" = "pass" ]; then
    VALUE=$(python3 -c 'import json,sys; print(json.dumps({"verdict": "pass", "by": sys.argv[1], "sha": sys.argv[2], "note": sys.argv[3]}, ensure_ascii=False))' \
        "$BY" "$SHA" "$REL_REVIEW")
    python3 "$AC/ticket.py" set "$ID" review "$VALUE" >/dev/null || {
        echo "review: #$ID 的 review 寫不進票" >&2
        exit 4
    }
    echo "review: #$ID 覆核通過($BY,sha $(echo "$SHA" | cut -c1-12))—— review 已寫進票"
    post "覆核通過($BY)" \
         "sh scripts/land.sh t$ID(review 綁分支頭 $(echo "$SHA" | cut -c1-12) 與票現在的版本)" \
         "$REL_REVIEW"
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
python3 "$AC/ticket.py" set "$ID" state Blocked >/dev/null 2>&1 \
    || echo "review: 票狀態改不動(Blocked)" >&2
python3 "$AC/ticket.py" set "$ID" owner main >/dev/null 2>&1 || true
ev decision.asked --ticket "$ID" --note "#$ID 覆核退回($BY):$COUNT 條阻擋"
echo "review: #$ID 覆核退回($BY)—— $COUNT 條阻擋記進 objections[],票轉 Blocked${WHY:+;$WHY}"
post "覆核退回($COUNT 條阻擋)" \
     "裁示:讀 $REL_REVIEW 與票的 objections[],逐筆處置(accepted / rejected / deferred / fixed);沒處置的阻擋項 land 與 close 都會拒絕" \
     "$REL_REVIEW"
exit 1
