#!/bin/sh
# 套 patch → 建分支 → commit:**一個程式入口** — `docs/WORKFLOW.md` §patch 管線(D-012、D-015)。
#
#   sh scripts/apply.sh <票號> <patch> [<patch-verify>] [--evidence <檔>]
#       [--evidence-verifier <檔>]…                   # 套進 t<票號> 分支並 commit
#   sh scripts/apply.sh rebase <票號> <patch> [-o <輸出>] # 套到**當前主線**的副本,出一份乾淨 diff
#   sh scripts/apply.sh --batch <整合分支> <票號…> <patch> [--evidence <檔>]
#                                                     # 批次修復:疊成整合分支頭上一個 commit(#108)
#   sh scripts/apply.sh verify-plan <票號> --evidence-verifier <檔>…
#                                                     # 只併驗證者計畫:不建分支、不 commit(#130)
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
#   0 套好、commit 好了      2 用法 / 票的問題(找不到票、沒有 base_sha;反駁記不進票;
#                              兩個 patch 槽是同一份;verify-plan 的驗證者 result 解析不到)
#   3 檔頭不合格或 `--check` 不過(rebase 那一支的三向合併衝突也是 3)
#   4 套完的比對不過(rebase 那一支的 **0 byte diff** 也是 4)
#   5 命中 `board/config.json` 的 `hard_block`(安全邊界:`paths` 的檔,或 tests/** verify/** 新增行
#     命中 `patterns`;D-050 ①:`allowed_write_paths`
#     以外與命中 `out_of_scope` 都**不擋**,列進 result 的 `extra_paths` / `extra_reasons`)
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

# 收件的 patch 記進票的 `patch_ref`(#87 A26):相對主 repo 根(repo 外以 `../` 開頭)——
# 票檔進 git,家目錄的絕對路徑不該跟著進去。**不動 `state_version`**(同 `ticket.py cost`):
# 這是收件記的帳,不是票面改了;動了它,這一手之後的覆核與遲到回報的比對都會跟著漂。
# 記不進去出聲,不擋收件。
record_patch_ref() {   # $1 = 票號  $2 = patch 路徑
    python3 - "$AC" "$1" "$2" "$(main_root)" <<'PY' || echo "apply: #$1 的 patch_ref 寫不進票 —— 不擋" >&2
import os, sys
sys.path.insert(0, sys.argv[1])
import event
import ticket
ident, patch, main = sys.argv[2:5]
ref = os.path.relpath(os.path.realpath(patch), os.path.realpath(main))
with ticket.Lock():
    fresh = ticket.load(ident)
    fresh["patch_ref"] = ref
    ticket.save(fresh)
event.emit("ticket.state", ticket=str(ident), field="patch_ref", to=ref)
print("apply: #%s patch_ref = %s" % (ident, ref))
PY
}

# 收尾提示指**真正的**落地入口(#87 A27,與 sync-to-project.sh 同一條判準):專案根有
# `scripts/land-ticket.sh` 就是它,否則是 `scripts/land.sh`。
land_entry() {   # $1 = 專案根  $2 = 分支
    if [ -f "$1/scripts/land-ticket.sh" ]; then
        echo "落地入口:sh scripts/land-ticket.sh $2"
    else
        echo "落地入口:sh scripts/land.sh $2"
    fi
}

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

# 套完逐一比對:該在的在、該刪的不在。單票與批次共用;`$WT` 由呼叫端設好。
verify_targets() {   # $1 = headers 印出來的清單
    echo "$1" | while read -r mark path; do
        [ -n "${path:-}" ] || continue
        case "$mark" in
            T) [ -f "$WT/$path" ] || echo "該在卻不在:$path" ;;
            D) [ ! -e "$WT/$path" ] || echo "該刪卻還在:$path" ;;
        esac
    done
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
    record_patch_ref "$ident" "$out"
    echo "apply: 下一步 —— sh scripts/apply.sh $ident $out"
    exit 0
}

# ------------------------------------------------------------------ batch
#
# 批次修復(#108,D-H51 ②):land 一批 N 張、全套紅時,`auto-fix.sh --batch` 派的那一個
# worker 在**整合分支**的副本上修紅榜;它的 patch 由這裡疊成整合分支頭上**恰一個** commit。
# 與單票那一支不同的三件事:
# 1. **不寫票**:不 `ticket.py set`、不記 patch_ref / branch / objection。任何一張票的
#    `state_version` 動一下,N 張的覆核同時過期;各票分支也不動(覆核綁的是它們的 sha)。
#    紀錄全在 `reports/batch/<stamp>/`。
# 2. 寫入範圍硬擋只剩 `hard_block`(D-050 ①、D-054 Q3);各票 `out_of_scope` 聯集 − 各票提示範圍聯集
#    (票 1 不准碰、票 2 本來就要改的檔不算)命中**不擋**,與聯集外的檔一起列進 result 的
#    `extra_paths` / `extra_reasons`,標明命中哪條 glob。在 `git apply` **之前**看檔頭量,
#    擋下來的那一手整合分支的工作樹一個位元都沒動。
# 3. **放寬測試**硬擋:測試檔(`batch_fix.test_globs`)裡刪掉含 assert 的行、或加了含 skip /
#    expectedFailure 的行 → rc 5。修一批紅最便宜的路就是把紅的案例弄啞,派工文的一句話擋不住它。
#    案例真的錯走 `OBJECTION: test_defect <案例>`:那條案例的檔不擋,交開題者判。
# stamp:`AC_BATCH_STAMP` > patch 就住在 `reports/batch/<stamp>/` > 現在時間。
# 退出碼同單票那一張表;6 = 套好也 commit 了,但 EVIDENCE 有 `OBJECTION:`(記在 result,不記進票)。
cmd_batch() {
    [ $# -ge 3 ] || { echo "apply: --batch <整合分支> <票號…> <patch> [--evidence <檔>]" >&2; exit 2; }
    B_BR=$1
    shift
    B_IDS=""; B_PATCH=""; B_EVIDENCE=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --evidence)
                shift
                [ $# -ge 1 ] || { echo "apply: --evidence 後面要路徑" >&2; exit 2; }
                B_EVIDENCE=$1 ;;
            -*) echo "apply: --batch 不認得 $1" >&2; exit 2 ;;
            *)  [ -z "$B_PATCH" ] || B_IDS="$B_IDS $B_PATCH"
                B_PATCH=$1 ;;
        esac
        shift
    done
    B_IDS=${B_IDS# }
    [ -n "$B_IDS" ] || { echo "apply: --batch 至少要一張票號,最後一個參數是 patch" >&2; exit 2; }
    for b_id in $B_IDS; do
        [ -f "$TDIR/$b_id.json" ] || { echo "apply: 找不到票 #$b_id($TDIR/$b_id.json)" >&2; exit 2; }
    done
    [ -f "$B_PATCH" ] || { echo "apply: 找不到 patch $B_PATCH" >&2; exit 2; }
    B_PATCH=$(cd "$(dirname "$B_PATCH")" && pwd)/$(basename "$B_PATCH")
    [ -n "$B_EVIDENCE" ] || B_EVIDENCE=$(dirname "$B_PATCH")/EVIDENCE.md
    git -C "$ROOT" rev-parse -q --verify "$B_BR^{commit}" >/dev/null \
        || { echo "apply: 整合分支 $B_BR 不存在" >&2; exit 2; }
    B_TAGS=$(for b_id in $B_IDS; do printf '#%s ' "$b_id"; done)
    B_TAGS=${B_TAGS% }
    B_ROOT=$ROOT/$(cfg reports_dir reports)/batch
    B_STAMP=${AC_BATCH_STAMP:-$(python3 -c 'import os,sys; d=os.path.realpath(os.path.dirname(sys.argv[1])); print(os.path.basename(d) if os.path.dirname(d)==os.path.realpath(sys.argv[2]) else "")' "$B_PATCH" "$B_ROOT")}
    [ -n "$B_STAMP" ] || B_STAMP=$(date +%Y%m%d-%H%M%S)-$$
    B_DIR=$B_ROOT/$B_STAMP
    B_ROUND=${AC_ROUND:-1}
    mkdir -p "$B_DIR"
    B_SHA=$(sha256_of "$B_PATCH")
    echo "apply: 批次 $B_TAGS -> $B_BR  patch $(basename "$B_PATCH")  sha256 ${B_SHA%"${B_SHA#??????????}"}…  紀錄 $B_DIR"

    B_TARGETS=$(headers "$B_PATCH") || { echo "apply: patch 檔頭不合格(見上面逐條)" >&2; exit 3; }

    # 兩道硬擋都只看 patch 本身:印 `B <檔>\t<哪一格>\t<glob 或 regex>`(hard_block)、
    # `R <檔>:<行>\t<那一行>`(放寬測試)、`E <檔>`(聯集外,不擋)、`O <檔>\t<哪一格>\t<glob>`
    # (命中 out_of_scope,不擋、要理由)、`X <檔>\t<案例>`(反駁豁免)。
    B_OUT=$(python3 - "$AC" "$ROOT" "$TDIR" "$B_PATCH" "$B_EVIDENCE" $B_IDS <<'PY'
import json, os, re, sys
ac, root, tdir, patch, evidence = sys.argv[1:6]
ids = sys.argv[6:]
sys.path.insert(0, ac)
import ticket as ticket_mod

rows = {}
for ident in ids:
    with open(os.path.join(tdir, "%s.json" % ident), encoding="utf-8") as handle:
        rows[ident] = json.load(handle)
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
conf = conf if isinstance(conf, dict) else {}
test_globs = (conf.get("batch_fix") or {}).get("test_globs") \
    or ["tests/**", "verify/**", "**/test_*.py"]
scope = []
for ident in ids:
    for pattern in ticket_mod.hint_scope(rows[ident]):
        if pattern not in scope:
            scope.append(pattern)


def is_test(name):
    # `**/test_*.py` 也要蓋到根目錄的 test_x.py:fnmatch 的 `**/` 要求前面至少一層。
    return ticket_mod.first_match(name, test_globs) is not None or ticket_mod.first_match(
        name, [pattern[3:] for pattern in test_globs if pattern.startswith("**/")]) is not None


def case_files(case):
    case = case.split("::", 1)[0]
    if case.endswith(".py"):
        return {case}
    parts = case.split(".")
    return {"/".join(parts[:k]) + ".py" for k in range(1, len(parts) + 1)}


exempt = {}
try:
    with open(evidence, encoding="utf-8", errors="replace") as handle:
        for line in handle.read().splitlines():
            found = re.match(r"^OBJECTION:\s*test_defect\s+(\S+)", line)
            if found:
                for name in case_files(found.group(1).strip("`")):
                    exempt.setdefault(name, found.group(1).strip("`"))
except OSError:
    pass

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
with open(patch, encoding="utf-8", errors="replace") as handle:
    lines = handle.read().splitlines()
sections, current = [], None
for line in lines:
    if line.startswith("--- "):
        current = {"old": line[4:].split("\t")[0].strip(), "new": "", "removed": [], "added": []}
        sections.append(current)
        old_no = new_no = 0
    elif line.startswith("+++ ") and current is not None and not current["new"]:
        current["new"] = line[4:].split("\t")[0].strip()
    elif line.startswith("@@") and current is not None:
        found = HUNK.match(line)
        if found:
            old_no, new_no = int(found.group(1)), int(found.group(2))
    elif current is None or line.startswith("\\"):
        continue
    elif line.startswith("-"):
        current["removed"].append((old_no, line[1:]))
        old_no += 1
    elif line.startswith("+"):
        current["added"].append((new_no, line[1:]))
        new_no += 1
    elif line.startswith(" "):
        old_no += 1
        new_no += 1

for row in sections:
    gone = row["new"] == "/dev/null"
    row["name"] = (row["old"] if gone else row["new"]).split("/", 1)[1]
hits = ticket_mod.out_of_scope_hits([rows[ident] for ident in ids],
                                    [row["name"] for row in sections])
blocked = {name: (field, pattern) for name, field, pattern in ticket_mod.hard_block_hits(
    root, [row["name"] for row in sections],
    {row["name"]: [text for _, text in row["added"]] for row in sections})}
for row in sections:
    name = row["name"]
    if name in blocked:
        print("B %s\t%s\t%s" % (name, blocked[name][0], blocked[name][1]))
    elif name in hits:
        print("E %s" % name)
        print("O %s\t%s\t%s" % (name, hits[name][0], hits[name][1]))
    elif not ticket_mod.matches_any(name, scope):
        print("E %s" % name)
    if not is_test(name):
        continue
    # 搬位置不算刪:同一份檔裡原樣加回來的那一行(去掉縮排比)抵掉一次。
    kept = [text.strip() for _, text in row["added"]]
    hits = []
    for number, text in row["removed"]:
        if "assert" not in text.lower():
            continue
        if text.strip() in kept:
            kept.remove(text.strip())
            continue
        hits.append((number, text))
    for number, text in row["added"]:
        if re.search(r"skip|expectedfailure", text, re.I):
            hits.append((number, text))
    if hits and name in exempt:
        print("X %s\t%s" % (name, exempt[name]))
        continue
    for number, text in hits:
        print("R %s:%d\t%s" % (name, number, text.strip()))
PY
) || { echo "apply: 批次的寫入範圍 / 放寬測試量不出來(票讀不動?)" >&2; exit 2; }
    B_BLOCKED=$(echo "$B_OUT" | sed -n 's/^B //p')
    B_RELAX=$(echo "$B_OUT" | sed -n 's/^R //p')
    B_EXTRA=$(echo "$B_OUT" | sed -n 's/^E //p')
    B_FENCED=$(echo "$B_OUT" | sed -n 's/^O //p')
    echo "$B_OUT" | sed -n 's/^X //p' | awk -F '\t' '{ printf "apply: %s 的測試改動不擋 —— EVIDENCE 有 OBJECTION: test_defect %s(交開題者判)\n", $1, $2 }'
    if [ -n "$B_BLOCKED" ]; then
        echo "apply: 動到這一批硬擋的檔(hard_block):" >&2
        echo "$B_BLOCKED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }' >&2
    fi
    if [ -n "$B_RELAX" ]; then
        echo "apply: patch 在測試檔裡刪了斷言或加了 skip —— 修紅不准把案例弄啞:" >&2
        echo "$B_RELAX" | awk -F '\t' '{ printf "apply:   %s 放寬測試 —— %s\n", $1, $2 }' >&2
        echo "apply:   案例本身錯就在 EVIDENCE 寫 OBJECTION: test_defect <案例>,交開題者判" >&2
    fi
    if [ -n "$B_BLOCKED" ] || [ -n "$B_RELAX" ]; then
        echo "apply: 批次 patch 被硬擋,$B_BR 沒動(沒有套、沒有 commit)" >&2
        exit 5
    fi

    # 整合分支的工作樹:land 開的那一個就用它(同一條分支 git 只准一個 worktree),沒有才開。
    WT=$(git -C "$ROOT" worktree list --porcelain | awk -v ref="branch refs/heads/$B_BR" '
        /^worktree / { here = substr($0, 10) } $0 == ref { print here; exit }')
    if [ -z "$WT" ]; then
        WT=$WTBASE/batch-$(echo "$B_BR" | tr '/' '-')
        mkdir -p "$WTBASE"
        git -C "$ROOT" worktree add -q "$WT" "$B_BR" || { echo "apply: worktree add $B_BR 失敗" >&2; exit 2; }
        echo "apply: 開了整合分支 $B_BR 的副本 $WT"
    else
        echo "apply: 沿用整合分支的副本 $WT"
    fi
    B_RESIDUE=$(git -C "$WT" ls-files --others --exclude-standard -- 'gate.log*')
    B_DIRTY=$(git -C "$WT" status --porcelain --untracked-files=all | grep -v '^?? gate\.log')
    if [ -n "$B_DIRTY" ]; then
        echo "$B_DIRTY" | sed 's/^/apply:   /' >&2
        echo "apply: 整合分支的工作樹 $WT 有沒 commit 的改動 —— 疊上去會把它們一起 commit,停" >&2
        exit 2
    fi
    if ! git -C "$WT" apply -p1 --check "$B_PATCH" 2>/tmp/ac-apply-$$.err; then
        sed 's/^/apply:   /' /tmp/ac-apply-$$.err >&2
        rm -f /tmp/ac-apply-$$.err
        echo "apply: git apply --check 不過 —— 這份 patch 不是對 $B_BR 的頭做的" >&2
        exit 3
    fi
    rm -f /tmp/ac-apply-$$.err
    git -C "$WT" apply -p1 "$B_PATCH" || { echo "apply: git apply 失敗(--check 過了卻套不進去)" >&2; exit 3; }
    B_BAD=$(verify_targets "$B_TARGETS")
    [ -z "$B_BAD" ] || { echo "$B_BAD" | sed 's/^/apply:   /' >&2; echo "apply: 套完的檔案與 patch 說的對不上" >&2; exit 4; }
    git -C "$WT" apply -p1 --reverse --check "$B_PATCH" 2>/dev/null \
        || { echo "apply: 反著套回不去 —— 工作樹上的改動不等於這份 patch" >&2; exit 4; }
    # code-map 索引重產(#129):與單票那一支第 7 步同一份 `code_map_regen`;產出物有變就併進
    # 這一個 batch fix commit。範圍在 `git apply` 之前已經對 patch 量完,產出物不進 extra_paths。
    B_REGEN=""
    code_map_regen "$WT"
    case $? in
        0)  B_REGEN="regen: repo-map build (code-map/index.json, code-map/INDEX.md)"
            echo "apply: 批次 code-map 索引重產了,併進同一個 batch fix commit" ;;
        2)  echo "apply: 批次 code-map 索引沒重產(repo-map build 非零,上面 repo-map: 那幾行)—— 不擋套用,閘門的機械格會攔" >&2 ;;
    esac
    git -C "$WT" add -A || { echo "apply: git add 失敗" >&2; exit 2; }
    for name in $B_RESIDUE; do
        git -C "$WT" reset -q -- "$name" || { echo "apply: git reset $name 失敗" >&2; exit 2; }
    done
    if git -C "$WT" diff --cached --quiet; then
        echo "apply: 套完之後工作樹沒有任何改動 —— 這份 patch 等於什麼都沒做" >&2
        exit 4
    fi
    git -C "$WT" commit -q -m "batch fix round $B_ROUND $B_TAGS ($B_BR)

batch: $B_STAMP
tickets: $B_TAGS
patch: $(basename "$B_PATCH") sha256=$B_SHA
round: $B_ROUND${B_REGEN:+
$B_REGEN}" || { echo "apply: git commit 失敗" >&2; exit 2; }
    echo "apply: 批次 $B_TAGS -> $B_BR $(git -C "$WT" rev-parse --short HEAD) 已 commit(各票分支與票檔都沒動)"
    if [ -f "$B_EVIDENCE" ]; then
        python3 "$AC/memory.py" harvest "$B_EVIDENCE" \
            || echo "apply: EVIDENCE 記憶收割失敗($B_EVIDENCE)—— 不擋 apply" >&2
    else
        echo "apply: 找不到 EVIDENCE $B_EVIDENCE —— 記憶 0 筆" >&2
    fi
    B_FIRST=${B_IDS%% *}
    B_RESULT=$B_DIR/result-round$B_ROUND.json
    python3 "$AC/ticket.py" result "$B_EVIDENCE" "$B_RESULT" --ticket "$B_FIRST" \
        --role worker --round "$B_ROUND" \
        || echo "apply: result 抽不出來($B_EVIDENCE)—— 不擋 apply" >&2
    # 反駁只留在 EVIDENCE 與這一份 result(A4):`ticket.py objection` 會 +1 state_version,N 張覆核同時過期。
    python3 - "$B_RESULT" "$B_EXTRA" "$B_EVIDENCE" "$AC" <<'PY'
import json, sys
path, extra = sys.argv[1], [line for line in sys.argv[2].splitlines() if line]
sys.path.insert(0, sys.argv[4])
import ticket as ticket_mod
try:
    with open(sys.argv[3], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
except OSError:
    text = ""
objections = [line for line in text.splitlines() if line.startswith("OBJECTION:")]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as exc:
    sys.stderr.write("apply: result 讀不到(%s)—— extra_paths / extra_reasons 沒寫進去:%s\n"
                     % (path, exc))
    raise SystemExit(0)
data["extra_paths"] = extra
data["extra_reasons"] = ticket_mod.extra_reasons(text, extra)
data["objections"] = objections
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
    if [ -n "$B_EXTRA" ]; then
        echo "apply: 聯集外的改動(不擋,記進 $(basename "$B_RESULT") 的 extra_paths / extra_reasons、交覆核者逐檔看):"
        echo "$B_EXTRA" | sed 's/^/apply:   /'
    fi
    if [ -n "$B_FENCED" ]; then
        echo "apply: 其中命中 out_of_scope 的(建議範圍,D-050 ①;EVIDENCE「提示清單外的改動」逐檔寫理由):"
        echo "$B_FENCED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }'
    fi
    if [ -f "$B_EVIDENCE" ] && grep -q '^OBJECTION:' "$B_EVIDENCE"; then
        echo "apply: EVIDENCE 裡有反駁 —— $(grep -m1 '^OBJECTION:' "$B_EVIDENCE")" >&2
        echo "apply:   批次不寫票:反駁沒有記進任何一張票的 objections[],由開題者 / 主線照 EVIDENCE 判" >&2
        exit 6
    fi
    echo "apply: 下一步 —— sh scripts/review.sh --batch $B_BR $B_IDS --run-id $B_STAMP"
    exit 0
}

# **驗證者的計畫與驗紅由這一手寫進票**(#36,FLOW G13;#51):`verify-case.py red` 只交證據檔,
# 驗證者把它抄進 result 的 `baseline`、把 `files` / `tags` / `run` / `notes` 寫在 result 的
# `verify`;這裡在鎖裡併進票。**直接讀 EVIDENCE 那一塊**,不讀抽出來的檔 —— 上游抽過
# (`AC_RESULT_DONE`,auto-fix 第 1 輪)時那一份落在別的 run 目錄,以前整段因此被跳過,
# 票的 `verify` 一格都沒併進去,閘門的驗證者那一層就量不到案例(#51 實測)。
# 只收 `stage=red` —— 驗證者量不到綠,一份自稱 `check` 的會讓 `close` 放行。
# 給了幾份 `--evidence-verifier` 就依給的順序逐份併(#112)。
# `verify-plan` 子命令(#130)跑的就是這一段:不套 patch 的那一輪也走同一把鎖、同一份解析。
merge_verifier_plans() {   # $1 = 票號  $2 = 換行分隔的 EVIDENCE-verifier;有一份解析不到 → rc 非零
    printf '%s\n' "$2" | (
    mrc=0
    while IFS= read -r VEV; do
        [ -n "$VEV" ] || continue
        python3 - "$AC" "$1" "$VEV" <<'PY' >&2 || mrc=$?
import json, sys
sys.path.insert(0, sys.argv[1])
import event
import ticket
ident, path = sys.argv[2], sys.argv[3]
# 解析不到要出聲(#100 A7):以前三種樣子都靜靜變成 `{}`,票上的 verify 與 baseline 一格
# 都沒併進去,要到落地後 close 被拒「沒有回歸證據」才看得出來。判準與 `ticket.py result`
# 同一份(`result_block` / `RESULT_OPEN`),原因字也同一組,不在這裡另抄 regex。
WHY = {"no-evidence": "讀不到這份 EVIDENCE",
       "no-block": "沒有語言標記是 result 的那一塊(`## result` 底下用 ```json 也算沒有)",
       "bad-json": "那一塊的 JSON 解不開或不是物件"}
reason = None
try:
    with open(path, encoding="utf-8") as handle:
        raw = ticket.result_block(handle.read().splitlines())
except OSError:
    reason = "no-evidence"
else:
    if raw is None:
        reason = "no-block"
    else:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            reason = "bad-json"
if reason:
    print("apply: 驗證者的 result 解析不到(%s:%s;%s)—— verify 計畫與驗紅沒有併進票"
          % (reason, WHY[reason], path))
    sys.exit(2)


def head(value):
    text = str(value)
    return text if len(text) <= 40 else text[:40] + "…"


# 併進票**不蓋掉票上既有的**(#100 A9):以前 `merged.update(keep)` 把 tags 整組換成
# 驗證者給的(#706 四個標籤剩一個,回歸層少跑 P6)。files / tags 取聯集(既有的在前、
# 新的接後面、去重);run / notes 既有非空就保留,空的才採驗證者給的。
plan = data.get("verify")
if isinstance(plan, dict):
    given = {key: plan[key] for key in ("files", "tags", "run", "notes") if plan.get(key)}
    if given:
        added, kept, differ = [], [], []
        try:
            with ticket.Lock():
                fresh = ticket.load(ident)
                merged = dict(fresh.get("verify") if isinstance(fresh.get("verify"), dict) else {})
                for key in ("files", "tags"):
                    if key not in given:
                        continue
                    have = merged.get(key) if isinstance(merged.get(key), list) else []
                    more = given[key] if isinstance(given[key], list) else [given[key]]
                    union = list(have)
                    for item in more:
                        if item not in union:
                            union.append(item)
                    kept.extend("%s:%s" % (key, item) for item in have)
                    added.extend("%s:%s" % (key, item) for item in union[len(have):])
                    merged[key] = union
                for key in ("run", "notes"):
                    if key not in given:
                        continue
                    if not merged.get(key):
                        merged[key] = given[key]
                        added.append(key)
                    else:
                        kept.append(key)
                        if merged[key] != given[key]:
                            differ.append("%s 票上「%s」/ 驗證者「%s」"
                                          % (key, head(merged[key]), head(given[key])))
                if added:
                    fresh["verify"] = merged
                    fresh["state_version"] = int(fresh.get("state_version") or 0) + 1
                    ticket.save(fresh)
        except (OSError, ValueError, RuntimeError) as exc:
            print("apply: 驗證者的 verify 計畫併不進票 #%s —— %s(不擋 apply)" % (ident, exc))
        else:
            if differ:
                print("apply: 驗證者給的 run/notes 與票上不同,保留票上 —— %s" % ";".join(differ))
            if added:
                event.emit("ticket.state", ticket=str(ident), field="verify",
                           **{"to": ",".join(added), "state_version": fresh["state_version"]})
                print("apply: 驗證者的 verify 併進票 #%s(state_version=%s)—— 新增 %s;保留 %s"
                      % (ident, fresh["state_version"], "、".join(added),
                         "、".join(kept) or "(票上原本沒有)"))
            else:
                print("apply: 驗證者的 verify 票上都已經有了(保留 %s)—— 票沒有動"
                      % "、".join(kept))
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
    done
    exit $mrc
    )
}

# ------------------------------------------------------------- verify-plan(#130)
# 驗證者這一輪 patch-verify 是 0 byte、只改了 result 的 verify(#98 第 2 輪):沒有 patch 可套,
# 計畫照樣要進票。**只跑上面那一段合併** —— 不建分支、不 commit、不碰 patch。
# 解析不到 rc=2:呼叫端(auto-fix 的只併計畫那條路)據此停下。
cmd_verify_plan() {
    [ $# -ge 1 ] || { echo "apply: verify-plan <票號> --evidence-verifier <檔>…" >&2; exit 2; }
    P_ID=$1
    P_EVIDENCES=""
    shift
    while [ $# -gt 0 ]; do
        case "$1" in
            --evidence-verifier)
                shift
                [ $# -ge 1 ] || { echo "apply: --evidence-verifier 後面要路徑" >&2; exit 2; }
                P_EVIDENCES=${P_EVIDENCES:+$P_EVIDENCES
}$1 ;;
            *) echo "apply: verify-plan 不認得 $1" >&2; exit 2 ;;
        esac
        shift
    done
    [ -n "$P_EVIDENCES" ] || { echo "apply: verify-plan 要至少一份 --evidence-verifier" >&2; exit 2; }
    [ -f "$TDIR/$P_ID.json" ] || { echo "apply: 找不到票 #$P_ID($TDIR/$P_ID.json)" >&2; exit 2; }
    if ! merge_verifier_plans "$P_ID" "$P_EVIDENCES"; then
        echo "apply: verify-plan #$P_ID —— 驗證者的計畫沒有全併進票(見上面那一行)" >&2
        exit 2
    fi
    echo "apply: verify-plan #$P_ID —— 只併驗證者計畫,分支與 patch 都沒動"
    exit 0
}

# ------------------------------------------------------------------ apply
usage() {   # $1 = 出去的檔案描述子(1 = 有人問 --help,2 = 用錯了)
    {
        echo "用法:sh scripts/apply.sh <票號> <patch> [<patch-verify>] [旗標…]"
        echo "      sh scripts/apply.sh rebase <票號> <patch> [-o <輸出>]"
        echo "      sh scripts/apply.sh --batch <整合分支> <票號…> <patch> [--evidence <檔>]"
        echo "      sh scripts/apply.sh verify-plan <票號> --evidence-verifier <檔>…"
        echo "                             只把驗證者的 verify 計畫與驗紅併進票:不建分支、不 commit"
        echo ""
        echo "認得的旗標:"
        echo "  --evidence <檔>            實作者的 EVIDENCE:收記憶、抽 result-round<輪>.json、"
        echo "                             收 ^OBJECTION: 那一行(不給就找 patch 旁邊的 EVIDENCE.md)"
        echo "  --evidence-verifier <檔>   驗證者的 EVIDENCE-verifier.md:抽 result-verifier-round<輪>.json"
        echo "                             (可重複給:verify 計畫與驗紅依給的順序逐份併進票)"
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
if [ "$1" = "--batch" ]; then
    shift
    cmd_batch "$@"
fi
if [ "$1" = "verify-plan" ]; then
    shift
    cmd_verify_plan "$@"
fi

[ $# -ge 2 ] || { echo "apply: 要 <票號> 與 <patch>" >&2; exit 2; }
ID=$1
PATCH=$2
VPATCH=""
EVIDENCE=""
VEVIDENCE=""
VEVIDENCES=""
shift 2
while [ $# -gt 0 ]; do
    case "$1" in
        --evidence)
            shift
            [ $# -ge 1 ] || { echo "apply: --evidence 後面要路徑" >&2; exit 2; }
            EVIDENCE=$1 ;;
        --evidence-verifier)
            # 可重複給(#112):平行驗證者與 test_defect 驗證者各一份,依給的順序逐份併進票。
            shift
            [ $# -ge 1 ] || { echo "apply: --evidence-verifier 後面要路徑" >&2; exit 2; }
            VEVIDENCE=$1
            VEVIDENCES=${VEVIDENCES:+$VEVIDENCES
}$1 ;;
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
    # 兩個槽是同一份(同一路徑,或內容逐 byte 相同)就拒(#112,專案 #775):第二份套的是
    # 已經套過的改動,`--check` 只會說「不過」,看不出是收件端把同一檔給了兩次。
    if [ "$VPATCH" = "$PATCH" ] || [ "$(sha256_of "$VPATCH")" = "$(sha256_of "$PATCH")" ]; then
        echo "apply: $PATCH 與 $VPATCH 是同一份 patch —— 兩個槽要一份 worker 的、一份 patch-verify;分支沒動" >&2
        exit 2
    fi
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
# 閘門的殘留(#57):第 2 輪起沿用同一個 worktree,上一輪閘門寫的 `gate.log*`(未追蹤)
# 還躺在那裡。它們不是 patch 的改動 —— 不算越界、不進 commit。清單在套 patch **之前**取,
# patch 自己新增的同名檔因此不會被當成殘留。
RESIDUE=$(git -C "$WT" ls-files --others --exclude-standard -- 'gate.log*')
[ -z "$RESIDUE" ] || echo "apply: 略過上一輪閘門的殘留:$(echo $RESIDUE)"

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

# 5. 寫入範圍(D-H38 ①、D-050 ①)。`allowed_write_paths` 與 `out_of_scope` 都是**建議範圍**:
#    提示範圍(`ticket.hint_scope`)外、或命中 `out_of_scope` 的檔**不擋**,記進 result 的
#    `extra_paths` / `extra_reasons`、由覆核者逐檔看;out_of_scope 命中缺理由由 land 拒絕。
#    硬擋只剩 `board/config.json` 的 `hard_block`(安全邊界;`paths` 比檔名,`patterns` 比兩份
#    patch 裡 tests/** verify/** 的新增行)。
#    patch-verify 帶進來的檔是驗證者的交付(#101 A4),不算 worker 的 extra(hard_block 照樣量)。
#    印 `B <檔>\t<哪一格>\t<glob 或 regex>`(硬擋)、`E <檔>`(提示範圍外)、`O <檔>\t<哪一格>\t<glob>`(命中 out_of_scope)。
OUT=$(python3 - "$ROOT" "$WT" "$TF" "$RESIDUE" "$TARGETS" "$VTARGETS" "$PATCH" "$VPATCH" <<'PY'
import json, os, subprocess, sys
root, wt, path, residue = sys.argv[1:5]
residue = set(residue.splitlines())
own = {line[2:] for line in sys.argv[5].splitlines() if len(line) > 2}
theirs = {line[2:] for line in sys.argv[6].splitlines() if len(line) > 2} - own
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
try:
    with open(path, encoding="utf-8") as handle:
        row = json.load(handle)
except (OSError, ValueError):
    row = {}
done = subprocess.run(["git", "-C", wt, "status", "--porcelain", "-z",
                       "--untracked-files=all"],
                      capture_output=True, text=True)
names = []
for item in done.stdout.split("\0"):
    if len(item) < 4:
        continue
    name = item[3:]
    if item.startswith("?? ") and name in residue:
        continue
    if name:
        names.append(name)
added = {}
for patch in sys.argv[7:9]:
    if not patch:
        continue
    with open(patch, encoding="utf-8", errors="replace") as handle:
        for name, lines in ticket_mod.added_lines(handle.read()).items():
            added.setdefault(name, []).extend(lines)
blocked, extra = ticket_mod.write_scope(row, root, names, added)
extra = [name for name in extra if name not in theirs]
for name, field, pattern in blocked:
    print("B %s\t%s\t%s" % (name, field, pattern))
for name in extra:
    print("E %s" % name)
for name, (field, pattern) in ticket_mod.out_of_scope_hits([row], extra).items():
    print("O %s\t%s\t%s" % (name, field, pattern))
PY
)
BLOCKED=$(echo "$OUT" | sed -n 's/^B //p')
EXTRA=$(echo "$OUT" | sed -n 's/^E //p')
FENCED=$(echo "$OUT" | sed -n 's/^O //p')
if [ -n "$BLOCKED" ]; then
    echo "apply: 動到票 #$ID 硬擋的檔:" >&2
    echo "$BLOCKED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }' >&2
    echo "apply:   工作樹留在 $WT(沒有 commit)—— 改 patch;真要動它就先改 config" >&2
    die 5 "寫入範圍命中硬擋(hard_block)"
fi
if [ -n "$EXTRA" ]; then
    echo "apply: 提示範圍外的改動(不擋,記進 result 的 extra_paths / extra_reasons、交覆核者逐檔看):"
    echo "$EXTRA" | sed 's/^/apply:   /'
fi
if [ -n "$FENCED" ]; then
    echo "apply: 其中命中 out_of_scope 的(建議範圍,D-050 ①;EVIDENCE「提示清單外的改動」缺理由的,land 會拒絕):"
    echo "$FENCED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }'
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

# 7. code-map 索引重產(#96)。候選樹有地圖(與 gate.sh 的新鮮度格同一個判準)就跑一次
#    `repo-map.py build`:交件新增 / 改名 / 刪掉已管理的檔而沒重生,以前要閘門擋下一整輪。
#    產出物有變才併進同一個 commit、訊息多一行 `regen:`;它們不是交件改的,不算 extra_paths
#    (範圍在上面第 5 步已經量完)。build 非零**不擋**:印一行、照樣 commit,攔它是閘門的事。
#    判準與動作只有一份:`wtbase.sh` 的 `code_map_regen`(`--batch` 與 land 也叫它,#129)。
REGEN=""
code_map_regen "$WT"
case $? in
    0)  REGEN="regen: repo-map build (code-map/index.json, code-map/INDEX.md)"
        echo "apply: code-map 索引重產了,併進同一個 commit" ;;
    2)  echo "apply: code-map 索引沒重產(repo-map build 非零,上面 repo-map: 那幾行)—— 不擋套用,閘門的機械格會攔" >&2 ;;
esac

# 8. commit。訊息帶票號與 patch 的 sha256 —— 下一個人要答得出「分支上這一手是哪一份
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
for name in $RESIDUE; do
    git -C "$WT" reset -q -- "$name" || die 2 "git reset $name 失敗(閘門殘留退不出 index)"
done
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
[ -z "$REGEN" ] || MSG="$MSG
$REGEN"
git -C "$WT" commit -q -m "$MSG" || die 2 "git commit 失敗"
SHA=$(git -C "$WT" rev-parse --short HEAD)
echo "apply: #$ID -> $BR $SHA 已 commit($(git -C "$WT" rev-list --count "$MAIN..HEAD") 個 commit)"
record_patch_ref "$ID" "$PATCH"
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
# 提示範圍外的檔記進**這一輪那一份** result 的 `extra_paths`(D-H38 ①;空的也寫 `[]` ——
# 沒有這一格與「量過、沒有」長得一樣),同一手從 EVIDENCE「提示清單外的改動」逐檔抽
# `extra_reasons`(#101 A5;沒寫、空字串 → null)。上游抽過的那一份由它用 `AC_RESULT_JSON`
# 指路;沒指路就不寫,不另開第二份 `result-round<r>.json`。
if [ -n "${AC_RESULT_DONE:-}" ] && [ -z "${AC_RESULT_JSON:-}" ]; then
    echo "apply: 上游抽過 result 但沒給 AC_RESULT_JSON —— extra_paths / extra_reasons 沒寫進去" >&2
else
    python3 - "${AC_RESULT_JSON:-$REPORTS/result-round$ROUND.json}" "$EXTRA" "$EVIDENCE" "$FENCED" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
path, extra = sys.argv[1], [line for line in sys.argv[2].splitlines() if line]
fenced = [line.split("\t")[0] for line in sys.argv[4].splitlines() if line]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as exc:
    sys.stderr.write("apply: result 讀不到(%s)—— extra_paths / extra_reasons 沒寫進去:%s\n"
                     % (path, exc))
    raise SystemExit(0)
try:
    with open(sys.argv[3], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
except OSError:
    text = ""
reasons = ticket_mod.extra_reasons(text, extra)
data["extra_paths"] = extra
data["extra_reasons"] = reasons
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
missing = [name for name in extra if not reasons[name]]
for name in missing:
    if name in fenced:
        print("apply: %s 命中 out_of_scope 而 EVIDENCE「提示清單外的改動」沒寫理由 —— land 會拒絕"
              "(out_of_scope 命中缺理由);補一行 `- `%s` — <理由>` 再收件" % (name, name))
    else:
        print("apply: %s 在提示範圍外、沒寫理由 —— 警告,不擋(覆核者逐檔看)" % name)
PY
fi
# 驗證者的計畫與驗紅併進票(`merge_verifier_plans`,上面那一段的說明)。解析不到照樣出聲,
# 但不擋 apply —— patch 已經 commit 了。
merge_verifier_plans "$ID" "$VEVIDENCES"
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
echo "apply:       閘門綠了主線覆核記 review,再落地:"
land_entry "$(main_root)" "$BR"
exit 0
