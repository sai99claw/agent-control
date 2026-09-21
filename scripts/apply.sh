#!/bin/sh
# 套 patch → 建分支 → commit:**一個程式入口** — `docs/WORKFLOW.md` §patch 管線(D-012、D-015)。
#
#   sh scripts/apply.sh <票號> <patch> [<patch-verify>]   # 套進 t<票號> 分支並 commit
#   sh scripts/apply.sh rebase <票號> <patch> [-o <輸出>] # 套到**當前主線**的副本,出一份乾淨 diff
#
# 這一手以前是主線手動做的(`docs/ROLES.md` 的引言框:落地器**不**套 patch)。手動的
# 問題不是慢,是**每一次都重新決定要不要檢查那五件事** —— 而 2026-09-10 的事故裡,
# 漏掉的那一次長得跟做過的一模一樣(`land.sh` 收到一條 0 commit 的分支,照樣跑完九分鐘)。
#
# ## 它在 `git apply` 之前擋的(檔頭)
# patch 的檔頭只准 `base/…` / `work/…` 的相對形式或 `/dev/null`。**絕對路徑拒**:
# 🩸 真的發生過 —— 檔頭帶絕對路徑的 patch 套下去,檔案被寫進暫存目錄底下的同名路徑,
# 套用成功、閘門也綠,而**被改的根本不是 repo 裡那一份**(D-012)。
# 順便擋 `diff -ruN` 的刪檔形狀:`+++` 側不是 `/dev/null` 的話,`git apply` 只會把檔案
# **清空**而不是刪掉,而清空的檔案在 diffstat 上看起來像「改過」。
#
# ## 它在 `git apply` 之後擋的(套了但沒全套進去)
# 逐一比對每個 `+++` 目標:該在的在、該刪的不在,再用 `git apply --reverse --check`
# 問一次「這棵樹減掉這份 patch 回得去嗎」。回不去 = 套進去的與 patch 說的不是同一件事
# (rc=4)。**退出碼 0 只證明 `git apply` 沒有喊叫。**
#
# ## 退出碼
#   0 套好、commit 好了      2 用法 / 票的問題(找不到票、沒有 base_sha)
#   3 檔頭不合格或 `--check` 不過(rebase 那一支的 `.rej`≠0 也是 3)
#   4 套完的比對不過         5 動到 `allowed_write_paths` 以外
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

cfg() {   # $1 = key  $2 = 預設;巢狀用 a.b
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
print(data if data else default)
PY
}
MAIN=$(cfg main_branch main)
TICKETS=${AC_TICKETS_DIR:-$(cfg tickets_dir tickets)}
case $TICKETS in /*) TDIR=$TICKETS ;; *) TDIR=$ROOT/$TICKETS ;; esac
# 副本/worktree 的根:環境變數 > board/config.json 的 `worktree_dir`(相對 repo 根)> 預設 `../<repo>-wt`。
WTBASE=${AC_WORKTREE_DIR:-$(cfg worktree_dir "")}
case "$WTBASE" in "") WTBASE=$ROOT/../$(basename "$ROOT")-wt ;; /*) ;; *) WTBASE=$ROOT/$WTBASE ;; esac

sha256_of() {
    python3 - "$1" <<'PY'
import hashlib, sys
try:
    with open(sys.argv[1], "rb") as handle:
        print(hashlib.sha256(handle.read()).hexdigest())
except OSError:
    print("")
PY
}

# 檔頭守衛。印 `T <路徑>`(套完該在)與 `D <路徑>`(套完該不在);不合格就寫 stderr 並非零。
headers() {   # $1 = patch
    python3 - "$1" <<'PY'
import re, sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
except OSError as exc:
    sys.stderr.write("apply: 讀不到 patch —— %s\n" % exc)
    raise SystemExit(2)

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ALLOWED = ("base/", "work/", "a/", "b/")
bad, out = [], []
sections, current = [], None
for line in lines:
    if line.startswith("--- "):
        current = {"old": line[4:].split("\t")[0].strip(), "new": "",
                   "adds": 0, "newlines": None}
        sections.append(current)
    elif line.startswith("+++ ") and current is not None and not current["new"]:
        current["new"] = line[4:].split("\t")[0].strip()
    elif line.startswith("@@") and current is not None:
        found = HUNK.match(line)
        if found:
            count = 1 if found.group(2) is None else int(found.group(2))
            current["newlines"] = (current["newlines"] or 0) + count
    elif current is not None and line.startswith("+"):
        current["adds"] += 1

if not sections:
    sys.stderr.write("apply: 這份 patch 裡一個檔頭都沒有 —— 空的 patch 不是綠\n")
    raise SystemExit(3)

for index, row in enumerate(sections):
    for side, name in (("---", row["old"]), ("+++", row["new"])):
        if not name:
            bad.append("第 %d 段少了 %s 檔頭" % (index + 1, side))
            continue
        if name == "/dev/null":
            continue
        if name.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", name):
            bad.append("%s %s —— **絕對路徑**;只准 base/… / work/… 的相對形式或 /dev/null"
                       % (side, name))
            continue
        if ".." in name.split("/"):
            bad.append("%s %s —— 檔頭裡有 `..`,它會把檔案寫到副本外面" % (side, name))
            continue
        if not name.startswith(ALLOWED):
            bad.append("%s %s —— 檔頭要 base/… 或 work/… 的形狀(diff -ruN base work)"
                       % (side, name))
    # `diff -ruN` 的刪檔:新側零行、一個 `+` 都沒有,而 `+++` 側還指著真的路徑。
    # `git apply` 對它的處置是**把檔案清空**,不是刪掉 —— 而清空的檔在 diffstat 上
    # 看起來像「改過」(`docs/WORKFLOW.md` §patch 管線第 3 點)。
    if row["newlines"] == 0 and row["adds"] == 0 and row["new"] != "/dev/null":
        bad.append("%s 是刪檔,但 `+++` 側不是 /dev/null —— git apply 只會把它清空。"
                   "把那一行改成 `+++ /dev/null`" % row["new"])

if bad:
    for line in bad:
        sys.stderr.write("apply:   %s\n" % line)
    raise SystemExit(3)

for row in sections:
    if row["new"] == "/dev/null":
        out.append("D %s" % row["old"].split("/", 1)[1])
    else:
        out.append("T %s" % row["new"].split("/", 1)[1])
sys.stdout.write("\n".join(out) + "\n")
PY
}

# ------------------------------------------------------------------ rebase
#
# 連續落地幾張票,**幾乎一定**撞到所有票都往尾端附加的登記檔與自動產生的清單
# (D-012)。所以落地前把 patch 套到「當前主線」的副本上,用 GNU `patch`(它吃 fuzz、
# 會自己找位移),重生清單,再從那份副本出一份乾淨的 diff。
# **`.rej` 數量 ≠ 0 一律當失敗** —— 「套了但有幾塊沒進去」與「全套進去了」在退出碼上
# 長得一樣。
cmd_rebase() {
    ident=$1
    patch_file=$2
    out=${3:-}
    [ -f "$patch_file" ] || { echo "apply: 找不到 patch $patch_file" >&2; exit 2; }
    patch_file=$(cd "$(dirname "$patch_file")" && pwd)/$(basename "$patch_file")
    [ -n "$out" ] || out=$ROOT/$(cfg reports_dir reports)/t$ident/patch-rebased.diff
    mkdir -p "$(dirname "$out")"
    WORK=$WTBASE/rebase-t$ident
    rm -rf "$WORK"
    mkdir -p "$WORK/base"
    git -C "$ROOT" archive "$MAIN" | tar -x -C "$WORK/base" || {
        echo "apply: 取不出 $MAIN 的副本" >&2; exit 2; }
    cp -R "$WORK/base" "$WORK/work"
    echo "apply: 副本 $WORK(base = 當前主線 $(git -C "$ROOT" rev-parse --short "$MAIN"))"
    ( cd "$WORK/work" && patch -p1 -F 2 --no-backup-if-mismatch -i "$patch_file" ) \
        || echo "apply: patch 有幾塊沒進去(往下看 .rej)"
    rej=$(cd "$WORK/work" && find . \( -name '*.rej' -o -name '*.orig' \) | sed 's/^\.\///')
    if [ -n "$rej" ]; then
        echo "apply: .rej / .orig 不是零 —— 這一份**沒有全套進去**(D-012 第 2 點):" >&2
        echo "$rej" | sed 's/^/apply:   /' >&2
        echo "apply: 副本留在 $WORK,自己看那幾塊要怎麼進去" >&2
        exit 3
    fi
    hook=$(cfg apply.regen_cmd "")
    if [ -n "$hook" ]; then
        echo "apply: 重生清單 —— $hook"
        ( cd "$WORK/work" && sh -c "$hook" ) || {
            echo "apply: 清單重生指令非零 —— 停在這裡,不出 diff" >&2; exit 3; }
    else
        echo "apply: 沒有設 apply.regen_cmd —— **自動產生的清單這一輪沒有重生**"
        echo "apply:   (有清單的專案要在 board/config.json 補 apply.regen_cmd。)"
    fi
    ( cd "$WORK" && diff -ruN base work ) > "$out"
    # `diff -ruN` 的刪檔那一側要是 /dev/null,不然下一步的 `git apply` 只會清空它。
    # 這裡自己改,而不是叫人手改:手改的那一步漏掉時,畫面上一個徵兆都沒有。
    python3 - "$out" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8", errors="replace") as handle:
    lines = handle.read().splitlines(True)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,(\d+))? @@")
starts = [i for i, line in enumerate(lines) if line.startswith("--- ")]
fixed = 0
for pos, start in enumerate(starts):
    end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
    block = lines[start:end]
    adds = sum(1 for line in block if line.startswith("+") and not line.startswith("+++"))
    counts = [HUNK.match(line) for line in block]
    newlines = sum(int(m.group(1) or 1) for m in counts if m)
    if adds == 0 and newlines == 0:
        for offset, line in enumerate(block):
            if line.startswith("+++ ") and "/dev/null" not in line:
                lines[start + offset] = "+++ /dev/null\n"
                fixed += 1
                break
with open(path, "w", encoding="utf-8") as handle:
    handle.writelines(lines)
if fixed:
    print("apply: 把 %d 段刪檔的 `+++` 側改成 /dev/null(不改的話 git apply 只會清空)" % fixed)
PY
    echo "apply: 乾淨的 diff -> $out"
    echo "apply: 下一步 —— sh scripts/apply.sh $ident $out"
    exit 0
}

# ------------------------------------------------------------------ apply
[ $# -ge 1 ] || {
    echo "用法:sh scripts/apply.sh <票號> <patch> [<patch-verify>]"
    echo "      sh scripts/apply.sh rebase <票號> <patch> [-o <輸出>]"
    exit 2
}
if [ "$1" = "rebase" ]; then
    shift
    [ $# -ge 2 ] || { echo "apply: rebase <票號> <patch> [-o <輸出>]" >&2; exit 2; }
    R_ID=$1; R_PATCH=$2; R_OUT=""
    shift 2
    while [ $# -gt 0 ]; do
        case "$1" in
            -o) shift; [ $# -ge 1 ] || { echo "apply: -o 後面要路徑" >&2; exit 2; }
                R_OUT=$1 ;;
            *) echo "apply: rebase 不認得 $1" >&2; exit 2 ;;
        esac
        shift
    done
    cmd_rebase "$R_ID" "$R_PATCH" "$R_OUT"
fi

[ $# -ge 2 ] || { echo "apply: 要 <票號> 與 <patch>" >&2; exit 2; }
ID=$1
PATCH=$2
VPATCH=${3:-}
TF=$TDIR/$ID.json
[ -f "$TF" ] || { echo "apply: 找不到票 #$ID($TF)" >&2; exit 2; }
[ -f "$PATCH" ] || { echo "apply: 找不到 patch $PATCH" >&2; exit 2; }
PATCH=$(cd "$(dirname "$PATCH")" && pwd)/$(basename "$PATCH")
if [ -n "$VPATCH" ]; then
    [ -f "$VPATCH" ] || { echo "apply: 找不到 patch-verify $VPATCH" >&2; exit 2; }
    VPATCH=$(cd "$(dirname "$VPATCH")" && pwd)/$(basename "$VPATCH")
fi

BASE=$(python3 - "$TF" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("base_sha") or "")
except (OSError, ValueError):
    print("")
PY
)
if [ -z "$BASE" ]; then
    echo "apply: 票 #$ID 沒有 base_sha —— 答不出「這份 patch 是對哪個版本做的」" >&2
    exit 2
fi

BR=${AC_BRANCH:-t$ID}
WT=$WTBASE/$BR
RUN_ID=${AC_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
PATCH_SHA=$(sha256_of "$PATCH")
NOTE=""

status_start() {
    python3 "$AC/status.py" start --ticket "$ID" --kind apply \
        --run-id "$RUN_ID" --base-sha "$BASE" --worktree "$WT" \
        --patch "$PATCH" --verify-patch "$VPATCH" --round "${AC_ROUND:-1}" \
        --repro "sh scripts/apply.sh $ID $PATCH $VPATCH" --cwd "$ROOT" \
        >/dev/null 2>&1 || echo "apply: 狀態檔寫不出來(不擋套用)" >&2
}
status_done() {   # $1 = rc  $2 = 說明
    python3 "$AC/status.py" done --ticket "$ID" --kind apply \
        --run-id "$RUN_ID" --sha "$(git -C "$WT" rev-parse --short HEAD 2>/dev/null || echo '')" \
        --rc "$1" --note "$2" >/dev/null 2>&1 \
        || echo "apply: 狀態檔寫不出來(不擋套用)" >&2
}
die() {   # $1 = rc  $2… = 說明
    rc=$1; shift
    echo "apply: $*" >&2
    status_done "$rc" "$*"
    exit "$rc"
}

echo "apply: 票 #$ID  patch $(basename "$PATCH")  sha256 ${PATCH_SHA%"${PATCH_SHA#??????????}"}…"
status_start

# 1. 檔頭。**在 `git apply` 之前**,因為 `git apply` 對絕對路徑的檔頭是成功的。
TARGETS=$(headers "$PATCH") || die 3 "patch 檔頭不合格(見上面逐條)"
if [ -n "$VPATCH" ]; then
    VTARGETS=$(headers "$VPATCH") || die 3 "patch-verify 檔頭不合格(見上面逐條)"
else
    VTARGETS=""
fi

# 2. 分支與 worktree。**從主線開**(不是從 base_sha):閘門的綠要對得上今天的主線。
if [ ! -d "$WT" ]; then
    mkdir -p "$WTBASE"
    if git -C "$ROOT" rev-parse -q --verify "$BR^{commit}" >/dev/null; then
        git -C "$ROOT" worktree add -q "$WT" "$BR" || die 2 "worktree add $BR 失敗"
        echo "apply: 沿用已經有的分支 $BR(第二輪以後就是這樣)"
    else
        git -C "$ROOT" worktree add -q -b "$BR" "$WT" "$MAIN" || die 2 "worktree add -b $BR 失敗"
        echo "apply: 開了分支 $BR 與副本 $WT(從 $MAIN)"
    fi
else
    echo "apply: 沿用副本 $WT"
fi

# 3. 預檢。不過就**不要套**:套一半的工作樹比沒套更難收。
if ! git -C "$WT" apply -p1 --check "$PATCH" 2>/tmp/ac-apply-$$.err; then
    sed 's/^/apply:   /' /tmp/ac-apply-$$.err >&2
    rm -f /tmp/ac-apply-$$.err
    echo "apply:   主線可能已經走遠 —— 重套走 sh scripts/apply.sh rebase $ID $PATCH" >&2
    die 3 "git apply --check 不過"
fi
rm -f /tmp/ac-apply-$$.err
git -C "$WT" apply -p1 "$PATCH" || die 3 "git apply 失敗(--check 過了卻套不進去)"

# 4. 套完逐一比對:該在的在、該刪的不在,而且整份 patch 反著套回得去。
#    **`git apply` 的 0 只說它沒有喊叫**,不說套進去的與 patch 說的是同一件事。
verify_targets() {   # $1 = headers 印出來的清單
    echo "$1" | while read -r mark path; do
        [ -n "${path:-}" ] || continue
        case "$mark" in
            T) [ -f "$WT/$path" ] || echo "該在卻不在:$path" ;;
            D) [ ! -e "$WT/$path" ] || echo "該刪卻還在:$path" ;;
        esac
    done
}
BAD=$(verify_targets "$TARGETS")
[ -z "$BAD" ] || { echo "$BAD" | sed 's/^/apply:   /' >&2; die 4 "套完的檔案與 patch 說的對不上"; }
git -C "$WT" apply -p1 --reverse --check "$PATCH" 2>/dev/null \
    || die 4 "反著套回不去 —— 工作樹上的改動不等於這份 patch"

if [ -n "$VPATCH" ]; then
    git -C "$WT" apply -p1 --check "$VPATCH" || die 3 "patch-verify 的 --check 不過"
    git -C "$WT" apply -p1 "$VPATCH" || die 3 "patch-verify 套不進去"
    BAD=$(verify_targets "$VTARGETS")
    [ -z "$BAD" ] || { echo "$BAD" | sed 's/^/apply:   /' >&2; die 4 "patch-verify 套完對不上"; }
    git -C "$WT" apply -p1 --reverse --check "$VPATCH" 2>/dev/null \
        || die 4 "patch-verify 反著套回不去"
fi

# 5. 寫入範圍。排順序的人就是拿這一格判平行的(`docs/DESIGN.md` §10),所以越界不只是
#    「改了不該改的檔」,是**排順序當時算出來的那張衝突圖已經不成立**。
OUT=$(python3 - "$ROOT" "$WT" "$TF" <<'PY'
import json, os, subprocess, sys
root, wt, path = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
try:
    with open(path, encoding="utf-8") as handle:
        globs = json.load(handle).get("allowed_write_paths") or []
except (OSError, ValueError):
    globs = []
done = subprocess.run(["git", "-C", wt, "status", "--porcelain", "-z"],
                      capture_output=True, text=True)
for item in done.stdout.split("\0"):
    if len(item) < 4:
        continue
    name = item[3:]
    if name and not ticket_mod.matches_any(name, globs):
        print(name)
PY
)
if [ -n "$OUT" ]; then
    echo "apply: 動到票 #$ID 的 allowed_write_paths 以外的檔:" >&2
    echo "$OUT" | sed 's/^/apply:   /' >&2
    echo "apply:   工作樹留在 $WT(沒有 commit)—— 要嘛改票面,要嘛改 patch" >&2
    die 5 "寫入範圍越界"
fi

# 6. 票的 `verify_strings` 先對 patch 抓一次。**不擋**,但要現在說:
#    不說的話,「字串不在分支上」要等到 `ticket.py close` 的最後一步才會講話(D-012 第 4 點)。
python3 - "$TF" "$PATCH" "$VPATCH" <<'PY'
import json, sys
path, patch, vpatch = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle).get("verify_strings") or []
except (OSError, ValueError):
    rows = []
blob = ""
for name in (patch, vpatch):
    if not name:
        continue
    try:
        with open(name, encoding="utf-8", errors="replace") as handle:
            blob += handle.read()
    except OSError:
        pass
missing = []
for row in rows:
    needle = row.get("contains") if isinstance(row, dict) else row
    if needle and needle not in blob:
        missing.append(needle)
for needle in missing:
    print("apply: 票的 verify_strings 有一條**不在這份 patch 裡**:%r" % needle)
if missing:
    print("apply:   不在 patch 裡就不會在分支上,而那要等到 close 才會說話 —— 先看一眼。")
PY

# 7. commit。訊息帶票號與 patch 的 sha256 —— 下一個人要答得出「分支上這一手是哪一份
#    patch 做的」,而路徑答不出來(重套過的那一份路徑一樣、內容不一樣)。
SUBJECT=$(python3 - "$TF" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print((json.load(handle).get("subject") or "").strip())
except (OSError, ValueError):
    print("")
PY
)
git -C "$WT" add -A || die 2 "git add 失敗"
if git -C "$WT" diff --cached --quiet; then
    die 4 "套完之後工作樹沒有任何改動 —— 這份 patch 等於什麼都沒做"
fi
MSG="t$ID $SUBJECT

ticket: #$ID
patch: $(basename "$PATCH") sha256=$PATCH_SHA"
if [ -n "$VPATCH" ]; then
    MSG="$MSG
patch-verify: $(basename "$VPATCH") sha256=$(sha256_of "$VPATCH")"
fi
MSG="$MSG
round: ${AC_ROUND:-1}"
git -C "$WT" commit -q -m "$MSG" || die 2 "git commit 失敗"
SHA=$(git -C "$WT" rev-parse --short HEAD)
echo "apply: #$ID -> $BR $SHA 已 commit($(git -C "$WT" rev-list --count "$MAIN..HEAD") 個 commit)"
status_done 0 "套好並 commit 成 $SHA"
echo "apply: 下一步 —— (cd $WT && sh scripts/gate.sh --branch --ticket $ID)"
echo "apply:       閘門綠了主線覆核記 review,再 sh scripts/land.sh $BR"
exit 0
