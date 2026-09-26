#!/bin/sh
# 套 patch → 建分支 → commit:**一個程式入口** — `docs/WORKFLOW.md` §patch 管線(D-012、D-015)。
#
#   sh scripts/apply.sh <票號> <patch> [<patch-verify>] [--evidence <檔>]
#       [--evidence-verifier <檔>]                    # 套進 t<票號> 分支並 commit
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
#   0 套好、commit 好了      2 用法 / 票的問題(找不到票、沒有 base_sha;反駁記不進票)
#   3 檔頭不合格或 `--check` 不過(rebase 那一支的三向合併衝突也是 3)
#   4 套完的比對不過(rebase 那一支的 **0 byte diff** 也是 4)
#   5 動到 `allowed_write_paths` 以外
#   6 patch 套好了,但 EVIDENCE 裡有一行 `OBJECTION:` —— 已記進票的 `objections[]`
#     (記過的同一筆 disposition 還空著也是 6;處置過的才放行成 0)
#
# ## `--evidence` 收三樣(2026-09-23,#29 A4;以前只收第一樣)
# 1. `memory.py harvest` —— EVIDENCE 記憶段那幾行;
# 2. `ticket.py result` —— 檔尾那一塊 `result` 抽成
#    `reports/t<票號>/<run_id>/result-round<輪>.json`(**與 `auto-fix.sh` 同一支抽取**;
#    `--evidence-verifier` 那一份抽成 `result-verifier-round<輪>.json`);
# 3. `ticket.py objection` —— `^OBJECTION:` 那一行記進票的 `objections[]`,rc=6 指名。
#    **記過的同一筆不會再記第二次**(`auto-fix.sh` 那條路已經先收過),但**還沒處置
#    就照樣 rc=6** —— 「已經有了」不等於「有人收了」(#36)。
#
# 為什麼 rc 非零:反駁是「這張票寫錯了」,而東西照樣套進分支、主線照樣往下走的那一刻,
# 那句話等於沒有人收(`docs/DISPATCH-TEMPLATE.md` §7)。patch 該 commit 的還是 commit 了
# —— 退出碼說的是「這一手沒有結束」,不是「什麼都沒發生」。
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
# 副本/worktree 的根:環境變數 > board/config.json 的 `worktree_dir` > 預設 `../<主 repo>-wt`;
# 相對路徑以**主 repo 根**拼(#46,與 auto-fix / land / review 同一個來源 `scripts/wtbase.sh`)。
. "$AC/wtbase.sh"
WTBASE=$(wtbase)

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
# (D-012)。所以落地前把 patch 重套到「當前主線」上,重生清單,再出一份乾淨的 diff。
#
# ## 重套的方式是**三向合併**,不是模糊比對(#17)
#   祖先 = 票的 `base_sha`(patch 就是對那一版做的)
#   我方 = 當前主線      對方 = `base_sha` + 這份 patch
# 所以 patch 先**嚴格**套回它自己的 base_sha(`git apply`,不吃 fuzz);套不上就代表
# 票面的 `base_sha` 與這份 patch 對不起來,當場停。
#
# 🩸 以前這裡是 GNU `patch -F 2`,而判失敗只看 `.rej`:上下文走遠的時候 `patch` 會整支
# **fatal** 掉(「misordered hunks! output would be garbled」),退出碼 2、**一個 `.rej`
# 都不留**。於是那個 `|| echo` 把退出碼吞掉、`.rej` 檢查也是空的,最後印出「乾淨的
# diff」而檔案是 **0 byte** —— 下一步 `git apply` 才喊「一個檔頭都沒有」(#17)。
# 現在三件事都守:**退出碼看**、**衝突指名到檔與行**、**空的 diff 一律非零**。
cmd_rebase() {
    ident=$1
    patch_file=$2
    out=${3:-}
    tf=$TDIR/$ident.json
    [ -f "$tf" ] || { echo "apply: 找不到票 #$ident($tf)" >&2; exit 2; }
    base=$(python3 - "$tf" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("base_sha") or "")
except (OSError, ValueError):
    print("")
PY
)
    if [ -z "$base" ]; then
        echo "apply: 票 #$ident 缺 base_sha —— 答不出原 patch 是對哪一版做的,不能 rebase" >&2
        exit 2
    fi
    [ -f "$patch_file" ] || { echo "apply: 找不到 patch $patch_file" >&2; exit 2; }
    patch_file=$(cd "$(dirname "$patch_file")" && pwd)/$(basename "$patch_file")
    # 祖先取不出來就不能三向合併。**在做副本之前**問,不然留下一份看似可用的副本。
    if ! git -C "$ROOT" rev-parse -q --verify "$base^{tree}" >/dev/null 2>&1; then
        echo "apply: 票 #$ident 的 base_sha=$base 不在這個 repo 裡 —— 取不出三向合併的祖先" >&2
        exit 2
    fi
    [ -n "$out" ] || out=$ROOT/$(cfg reports_dir reports)/t$ident/patch-rebased.diff
    mkdir -p "$(dirname "$out")"
    WORK=$WTBASE/rebase-t$ident
    rm -rf "$WORK"
    mkdir -p "$WORK/base" "$WORK/work"
    git -C "$ROOT" archive "$MAIN" | tar -x -C "$WORK/base" || {
        echo "apply: 取不出 $MAIN 的副本" >&2; exit 2; }
    git -C "$ROOT" archive "$base" | tar -x -C "$WORK/work" || {
        echo "apply: 取不出 base_sha=$base 的副本" >&2; exit 2; }
    echo "apply: 票 #$ident patch base_sha=$base"
    echo "apply: 副本 $WORK(base = 當前主線 $(git -C "$ROOT" rev-parse --short "$MAIN"))"

    # `$WORK/work` 先當成一顆**拋棄式的 git**:祖先一個 commit、對方一個 commit,
    # 我方換成當前主線再一個 commit,然後讓 git 自己做那次三向合併。最後 `.git` 會被
    # 刪掉,交出去的還是一個單純的目錄(`diff -ruN base work` 照舊)。
    gitw() {
        git -C "$WORK/work" -c user.name=agent-control \
            -c user.email=agent-control@invalid -c commit.gpgsign=false "$@"
    }
    gitw init -q >/dev/null 2>&1 || { echo "apply: 副本裡開不了暫時的 git" >&2; exit 2; }
    gitw symbolic-ref HEAD refs/heads/ac-rebase-base
    # `-f`:repo 自己的 `.gitignore` 蓋不到這一顆暫時的 git —— 少一個被忽略的檔,
    # 最後那份 diff 就多一段假的刪檔。
    gitw add -A -f >/dev/null || { echo "apply: 暫時的 git 收不進祖先那一版" >&2; exit 2; }
    gitw commit -q -m "base_sha $base" >/dev/null \
        || { echo "apply: 暫時的 git commit 不了祖先那一版" >&2; exit 2; }
    gitw checkout -q -b ac-rebase-ticket

    # 對方:patch **嚴格**套回自己的 base_sha。這裡不吃 fuzz —— 這一步要是要靠猜,
    # 那後面三向合併的「對方」就不是這份 patch 真正的意思。
    if ! gitw apply -p1 "$patch_file" 2>"$WORK/apply.err"; then
        sed 's/^/apply:   /' "$WORK/apply.err" >&2
        echo "apply: patch 套不回它自己的 base_sha=$base —— 這份 patch 不是對那一版做的" >&2
        echo "apply: 副本留在 $WORK" >&2
        exit 3
    fi
    gitw add -A -f >/dev/null
    if gitw diff --cached --quiet; then
        echo "apply: patch 套進 base_sha=$base 之後一個位元都沒變 —— 空的 patch 不是綠" >&2
        exit 4
    fi
    gitw commit -q -m "t$ident patch" >/dev/null \
        || { echo "apply: 暫時的 git commit 不了 patch 那一版" >&2; exit 2; }

    # 我方:把工作樹換成當前主線。`.git` 以外全刪再倒進去,刪檔才進得了 commit。
    gitw checkout -q ac-rebase-base
    find "$WORK/work" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    cp -R "$WORK/base/." "$WORK/work/"
    gitw add -A -f >/dev/null
    if ! gitw diff --cached --quiet; then
        gitw commit -q -m "當前主線 $(git -C "$ROOT" rev-parse --short "$MAIN")" >/dev/null \
            || { echo "apply: 暫時的 git commit 不了當前主線" >&2; exit 2; }
    fi

    # 三向合併。**衝突要指名到檔與行** —— 「有問題」不是一個可以執行的動作。
    if ! gitw merge --no-ff --no-edit ac-rebase-ticket >"$WORK/merge.out" 2>&1; then
        echo "apply: 三向合併有衝突 —— 祖先 base_sha=$base / 我方 當前主線 / 對方 這份 patch"
        gitw diff --name-only --diff-filter=U | while read -r name; do
            [ -n "$name" ] || continue
            echo "apply:   衝突檔 $name"
            grep -n '^<<<<<<<\|^=======$\|^>>>>>>>' "$WORK/work/$name" 2>/dev/null \
                | sed 's/^\([0-9][0-9]*\):/apply:     第 \1 行:/'
        done
        sed 's/^/apply:   /' "$WORK/merge.out"
        echo "apply:   副本留在 $WORK(衝突標記還在檔案裡),自己看那幾塊要怎麼進去"
        echo "apply: 三向合併有衝突,沒有出 diff" >&2
        exit 3
    fi
    rm -rf "$WORK/work/.git" "$WORK/apply.err" "$WORK/merge.out"
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
    # 🩸 **0 byte 不是乾淨**(#17)。重生出來的 diff 空掉的時候,前面每一句都還是
    # 成功的樣子,而「沒有東西可做」與「做完了」在這裡長得一模一樣 —— 一路要等到
    # 下一步 `git apply` 喊「一個檔頭都沒有」才會有人發現。
    if [ ! -s "$out" ]; then
        echo "apply: 重生出來的 diff 是 **0 byte** —— 空的 diff 不是乾淨的 diff" >&2
        echo "apply:   重套之後的樹與當前主線一模一樣:這份 patch 的改動可能已經在主線上了" >&2
        echo "apply:   副本留在 $WORK,$out 沒有東西可以餵給下一步" >&2
        exit 4
    fi
    echo "apply: 乾淨的 diff -> $out"
    echo "apply: 下一步 —— sh scripts/apply.sh $ident $out"
    exit 0
}

# ------------------------------------------------------------------ apply
usage() {   # $1 = 出去的檔案描述子(1 = 有人問 --help,2 = 用錯了)
    {
        echo "用法:sh scripts/apply.sh <票號> <patch> [<patch-verify>] [旗標…]"
        echo "      sh scripts/apply.sh rebase <票號> <patch> [-o <輸出>]"
        echo ""
        echo "認得的旗標:"
        echo "  --evidence <檔>            實作者的 EVIDENCE:收記憶、抽 result-round<輪>.json、"
        echo "                             收 ^OBJECTION: 那一行(不給就找 patch 旁邊的 EVIDENCE.md)"
        echo "  --evidence-verifier <檔>   驗證者的 EVIDENCE-verifier.md:抽 result-verifier-round<輪>.json"
        echo ""
        echo "例:"
        echo "  sh scripts/apply.sh 7 patch.diff patch-verify.diff \\"
        echo "     --evidence EVIDENCE.md --evidence-verifier EVIDENCE-verifier.md"
    } >&"$1"
}
case "${1:-}" in
    --help|-h|help) usage 1; exit 0 ;;
esac
[ $# -ge 1 ] || { usage 2; exit 2; }
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
VPATCH=""
EVIDENCE=""
VEVIDENCE=""
shift 2
while [ $# -gt 0 ]; do
    case "$1" in
        --evidence)
            shift
            [ $# -ge 1 ] || { echo "apply: --evidence 後面要路徑" >&2; exit 2; }
            EVIDENCE=$1 ;;
        --evidence-verifier)
            shift
            [ $# -ge 1 ] || { echo "apply: --evidence-verifier 後面要路徑" >&2; exit 2; }
            VEVIDENCE=$1 ;;
        --help|-h)
            usage 1; exit 0 ;;
        -*)
            echo "apply: 不認得 $1" >&2; usage 2; exit 2 ;;
        *)
            [ -z "$VPATCH" ] || { echo "apply: 多出的參數 $1" >&2; exit 2; }
            VPATCH=$1 ;;
    esac
    shift
done
TF=$TDIR/$ID.json
[ -f "$TF" ] || { echo "apply: 找不到票 #$ID($TF)" >&2; exit 2; }
[ -f "$PATCH" ] || { echo "apply: 找不到 patch $PATCH" >&2; exit 2; }
PATCH=$(cd "$(dirname "$PATCH")" && pwd)/$(basename "$PATCH")
if [ -z "$EVIDENCE" ]; then
    EVIDENCE=$(dirname "$PATCH")/EVIDENCE.md
fi
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

# 分支名**只有一個來源:票的 `branch` 欄**(#53,D-018)。`AC_BRANCH` > 票的 `branch` >
# `t<n>`;用到的名字在鎖裡寫回票(已有且相同不動)。`AC_BRANCH` 與票上已有的不同 →
# rc=2 印兩個名字 —— 靜悄悄換掉,auto-fix / review 讀票就找不到這條分支。
# `branch` 是工具填的欄位(SCHEMA:「派工時由工具填」),同 `ticket.py cost` 不動
# `state_version`;寫不回去只警告、不擋 apply。
BR=$(python3 - "$AC" "$TDIR" "$ID" "${AC_BRANCH:-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import event
import ticket
where, ident, wanted = sys.argv[2:5]


def refuse(have):
    print("apply: 票 #%s 的 branch 是 %s,AC_BRANCH 卻是 %s —— 不換;要換先改票"
          % (ident, have, wanted), file=sys.stderr)
    raise SystemExit(2)


have = ticket.load(ident, where).get("branch") or ""
if wanted and have and wanted != have:
    refuse(have)
name = wanted or have or "t%s" % ident
if name != have:
    try:
        with ticket.Lock(where):
            fresh = ticket.load(ident, where)
            have = fresh.get("branch") or ""
            if have and have != name:
                refuse(have)
            fresh["branch"] = name
            ticket.save(fresh, where)
    except (OSError, ValueError, RuntimeError) as exc:
        print("apply: 票 #%s 的 branch 寫不回去 —— %s(不擋 apply)" % (ident, exc),
              file=sys.stderr)
    else:
        event.emit("ticket.state", ticket=str(ident), field="branch", to=name)
print(name)
PY
) || exit 2
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
done = subprocess.run(["git", "-C", wt, "status", "--porcelain", "-z",
                       "--untracked-files=all"],
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
    FIX=$(python3 - "$TF" "$OUT" <<'PY'
import json, shlex, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    ticket = json.load(handle)
paths = ticket.get("allowed_write_paths") or []
for path in sys.argv[2].splitlines():
    if path not in paths:
        paths.append(path)
value = json.dumps(paths, ensure_ascii=False)
print("python3 scripts/ticket.py set %s allowed_write_paths %s"
      % (ticket["id"], shlex.quote(value)))
PY
)
    echo "apply:   要把這些檔加入票面可貼:$FIX" >&2
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
ROUND=${AC_ROUND:-1}
REPORTS=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID
if [ -f "$EVIDENCE" ]; then
    python3 "$AC/memory.py" harvest "$EVIDENCE" \
        || echo "apply: EVIDENCE 記憶收割失敗($EVIDENCE)—— 不擋 apply" >&2
else
    echo "apply: 找不到 EVIDENCE $EVIDENCE —— 記憶 0 筆" >&2
fi
# **收 patch 的同一手抽 result**(#29 A4)。抽不出來的三種樣子由 `ticket.py result`
# 分開記(`no-evidence` / `no-block` / `bad-json`),一律不改退出碼、不擋流程 ——
# 揉成同一個空檔的那一刻,「沒交」與「交了但都是空的」長得一樣。
# `AC_RESULT_DONE=1` = 上游(`auto-fix.sh`)在收 patch 的同一手已經抽過了。兩邊都抽的
# 話,同一輪會有兩份 `result-round<r>.json` 躺在兩個 run 目錄裡,而看板的「輪數最大
# 那一份」就有兩個答案 —— 一份說綠、一份說綠,看起來沒問題,直到它們不一樣的那一天。
if [ -n "${AC_RESULT_DONE:-}" ]; then
    echo "apply: result 由上游抽過了(AC_RESULT_DONE)—— 這裡不抽第二份"
else
python3 "$AC/ticket.py" result "$EVIDENCE" "$REPORTS/result-round$ROUND.json" \
    --ticket "$ID" --role worker --round "$ROUND" \
    || echo "apply: result 抽不出來($EVIDENCE)—— 不擋 apply" >&2
if [ -n "$VEVIDENCE" ]; then
    python3 "$AC/ticket.py" result "$VEVIDENCE" \
        "$REPORTS/result-verifier-round$ROUND.json" \
        --ticket "$ID" --role verifier --round "$ROUND" \
        || echo "apply: 驗證者的 result 抽不出來($VEVIDENCE)—— 不擋 apply" >&2
fi
fi
# **驗證者的計畫與驗紅由這一手寫進票**(#36,FLOW G13;#51):`verify-case.py red` 只交證據檔,
# 驗證者把它抄進 result 的 `baseline`、把 `files` / `tags` / `run` / `notes` 寫在 result 的
# `verify`;這裡在鎖裡併進票。**直接讀 EVIDENCE 那一塊**,不讀抽出來的檔 —— 上游抽過
# (`AC_RESULT_DONE`,auto-fix 第 1 輪)時那一份落在別的 run 目錄,以前整段因此被跳過,
# 票的 `verify` 一格都沒併進去,閘門的驗證者那一層就量不到案例(#51 實測)。
# 只收 `stage=red` —— 驗證者量不到綠,一份自稱 `check` 的會讓 `close` 放行。
if [ -n "$VEVIDENCE" ]; then
    python3 - "$AC" "$ID" "$VEVIDENCE" <<'PY' >&2
import json, sys
sys.path.insert(0, sys.argv[1])
import event
import ticket
ident, path = sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as handle:
        raw = ticket.result_block(handle.read().splitlines())
    data = json.loads(raw) if raw is not None else {}
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
plan = data.get("verify")
if isinstance(plan, dict):
    keep = {key: plan[key] for key in ("files", "tags", "run", "notes") if plan.get(key)}
    if keep:
        try:
            with ticket.Lock():
                fresh = ticket.load(ident)
                merged = dict(fresh.get("verify") if isinstance(fresh.get("verify"), dict) else {})
                merged.update(keep)
                fresh["verify"] = merged
                fresh["state_version"] = int(fresh.get("state_version") or 0) + 1
                ticket.save(fresh)
        except (OSError, ValueError, RuntimeError) as exc:
            print("apply: 驗證者的 verify 計畫併不進票 #%s —— %s(不擋 apply)" % (ident, exc))
        else:
            event.emit("ticket.state", ticket=str(ident), field="verify",
                       **{"to": ",".join(sorted(keep)), "state_version": fresh["state_version"]})
            print("apply: 驗證者的 verify(%s)併進票 #%s(state_version=%s)"
                  % ("、".join(sorted(keep)), ident, fresh["state_version"]))
record = data.get("baseline")
if record is None:
    sys.exit(0)
if not isinstance(record, dict) or record.get("stage") != "red":
    print("apply: 驗證者 result 的 baseline 不是 stage=red 的紀錄 —— 沒有併進票")
    sys.exit(0)
try:
    version = ticket.save_baseline(ident, record, "red")
except (OSError, ValueError, RuntimeError) as exc:
    print("apply: 驗證者的驗紅併不進票 #%s —— %s(不擋 apply)" % (ident, exc))
    sys.exit(0)
print("apply: 驗證者的驗紅(stage=red)併進票 #%s 的 verify.baseline(state_version=%s)"
      % (ident, version))
PY
fi
# **反駁放在最後**:patch 已經 commit 了(那是事實),但這一手沒有結束 ——
# 一個「票寫錯了」的說法沒有人收,與沒有那個說法長得一樣(§7)。
# 狀態檔只寫**最後那一個** rc:先寫 0 再改 6 的那一段空檔裡,讀的人看到的是綠(#36)。
if [ -f "$EVIDENCE" ] && grep -q '^OBJECTION:' "$EVIDENCE"; then
    OLINE=$(grep -m1 '^OBJECTION:' "$EVIDENCE")
    echo "apply: EVIDENCE 裡有一行反駁 —— $OLINE" >&2
    python3 "$AC/ticket.py" objection "$ID" --line "$OLINE" --evidence "$EVIDENCE"
    orc=$?
    case $orc in
        0)
            echo "apply: #$ID 已記進 objections[] —— 處置它(accepted / rejected / deferred / fixed)" >&2
            echo "apply:   沒處置的阻擋項 land 與 close 都會拒絕。" >&2
            status_done 6 "patch 已 commit,但 worker 提了反駁"
            exit 6 ;;
        3)
            # 同一筆第二次收到,而**還沒處置**:與第一次收到一樣擋。以前這裡放行成 0,
            # 未處置的反駁第二輪就過去了(D-014 硬閘門)。
            echo "apply: #$ID 這一筆反駁票上已經有了,但還沒處置 —— 處置它(accepted / rejected / deferred / fixed)" >&2
            echo "apply:   沒處置的阻擋項 land 與 close 都會拒絕。" >&2
            status_done 6 "patch 已 commit,但 worker 的反駁還沒處置"
            exit 6 ;;
        4)
            echo "apply: #$ID 這一筆反駁票上已經有了,而且已處置 —— 沒有再記一次" >&2 ;;
        *)
            # 記不進票(rc=2)不是「已經有了」:沒人收的反駁,與沒有反駁長得一樣(§7)。
            echo "apply: #$ID 的反駁記不進票(ticket.py objection rc=$orc)—— 修好之後手動記:" >&2
            echo "apply:   python3 scripts/ticket.py objection $ID --line \"$OLINE\" --evidence $EVIDENCE" >&2
            status_done 2 "patch 已 commit,但 worker 的反駁記不進票"
            exit 2 ;;
    esac
fi
status_done 0 "套好並 commit 成 $SHA"
echo "apply: 下一步 —— (cd $WT && sh scripts/gate.sh --branch --ticket $ID)"
echo "apply:       閘門綠了主線覆核記 review,再 sh scripts/land.sh $BR"
exit 0
