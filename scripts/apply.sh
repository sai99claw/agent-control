#!/bin/sh
# 收件 → 在票分支上補 commit:**一個程式入口** — `docs/WORKFLOW.md` §交件管線(D-012、D-015、D-054 Q9)。
#
#   sh scripts/apply.sh <票號> [<patch-verify>] [--evidence <檔>]
#       [--evidence-verifier <檔>]…                   # 收 worker 的交件(分支上的 commit)
#   sh scripts/apply.sh --batch <整合分支> <票號…> [--base <sha>] [--evidence <檔>]
#                                                     # 批次修復:收整合分支上 worker 的 commit(#108、#145)
#   sh scripts/apply.sh verify-plan <票號> --evidence-verifier <檔>…
#                                                     # 只併驗證者計畫:不建分支、不 commit(#130)
#
# ## worker 自己 commit(#144,D-054 Q9)
# worker 在票分支的 worktree(`<worktree_dir>/<票分支>`,auto-fix 派工前建好)裡自己 commit;
# 「分支有新 commit、工作樹乾淨」由 auto-fix 判,這一支不再收 worker 的 patch。它剩下的事:
# 1. 驗證者的 patch-verify(驗證者仍在 `git archive` 副本交出 patch-verify,VISION 只說工作者)
#    以 `apply --index` 套進分支、補**一個** commit;這張票的改動先量 `hard_block`(rc 5);
# 2. code-map 索引重產(D-051):產出物有變就再補一個 commit;
# 3. 收 EVIDENCE(記憶、result、反駁)、把驗證者的計畫與驗紅併進票。
# 進主線仍只由 `land.sh` 在測試綠後做(落地排序、與別張票的衝突是它的事)。
#
# ## 退出碼
#   0 收好了(有 patch-verify / 重產就已 commit)
#   2 用法 / 票的問題(找不到票、沒有 base_sha;票分支或它的 worktree 不在;反駁記不進票;
#     verify-plan 的驗證者 result 解析不到)
#   3 patch-verify 套不上分支(`apply --check` 不過)
#   5 這張票的改動(分支上的 commit + patch-verify)命中 `board/config.json` 的 `hard_block`
#     (`paths` 的檔,或 tests/** verify/** 新增行命中 `patterns`;D-054 Q2:寫入範圍只剩這一道硬擋)
#   6 EVIDENCE 裡有一行 `OBJECTION:` —— 已記進票的 `objections[]`
#     (記過的同一筆 disposition 還空著也是 6;處置過的才放行成 0)
#   7 主 repo 比對拒收(#153,D-064 / D-065;規則在 `main_guard.py`):`--prepare` 留下的
#     `main-snapshot.json` 與現在比,主線 HEAD 沒有主線落地卻前進或被改寫、origin 被推、工作樹在
#     控制面與 guard_main_paths 之外有變、hard_block.paths / hard_block.dirs 有變。在任何收件動作
#     之前停:票 JSON、票分支頭、EVIDENCE 都沒動;stderr 點名第一項並說 not received,
#     紀錄在快照旁的 `main-compare.json`。沒有快照 = 沒比,印一行,照舊。
#
# ## `--evidence` 收三樣(2026-09-23,#29 A4;以前只收第一樣)
# 1. `memory.py harvest` —— EVIDENCE 記憶段那幾行;
# 2. `ticket.py result` —— 檔尾那一塊 `result` 抽成
#    `reports/t<票號>/<run_id>/result-round<輪>.json`(**與 `auto-fix.sh` 同一支抽取**;
#    `--evidence-verifier` 那一份抽成 `result-verifier-round<輪>.json`);
# 3. `ticket.py objection` —— `^OBJECTION:` 那一行記進票的 `objections[]`,rc=6 指名。
#    **記過的同一筆不會再記第二次**(`auto-fix.sh` 那條路已經先收過),但**還沒處置
#    就照樣 rc=6** —— 「已經有了」不等於「有人收了」(#36)。
# 沒給 `--evidence` 就讀輪次目錄那一份(`<worktree_dir>/fix-t<票號>/round<輪>/EVIDENCE-round<輪>.md`)。
#
# 為什麼 rc 非零:反駁是「這張票寫錯了」,而東西照樣在分支上、主線照樣往下走的那一刻,
# 那句話等於沒有人收(`docs/DISPATCH-TEMPLATE.md` §7)。退出碼說的是「這一手沒有結束」,
# 不是「什麼都沒發生」。
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

# ------------------------------------------------------------------ batch
#
# 批次修復(#108,D-H51 ②;#145 跟上 D-054 Q9):land 一批 N 張、全套紅時,`auto-fix.sh --batch`
# 派的那一個 worker 在**整合分支的 worktree** 上修紅榜、自己 commit(與單票同一種交件,#144)。
# 這一支不收 patch:量 `<base>..<整合分支>` 那幾個 commit、補 code-map 重產的 commit、收 EVIDENCE。
# `<base>` = `--base`(auto-fix 給派工前的頭);沒給 = 整合分支 first-parent 上最近一個 merge(最後併進來的那張票)。
# 與單票那一支不同的三件事:
# 1. **不寫票**:不 `ticket.py set`、不記 branch / objection。任何一張票的
#    `state_version` 動一下,N 張的覆核同時過期;各票分支也不動(覆核綁的是它們的 sha)。
#    紀錄全在 `reports/batch/<stamp>/`。
# 2. 寫入範圍硬擋只剩 `hard_block`(D-054 Q2、Q3),量 `<base>..<整合分支>` 的 diff。
# 3. **放寬測試**硬擋:測試檔(`batch_fix.test_globs`)裡刪掉含 assert 的行、或加了含 skip /
#    expectedFailure 的行 → rc 5。修一批紅最便宜的路就是把紅的案例弄啞,派工文的一句話擋不住它。
#    案例真的錯走 `OBJECTION: test_defect <案例>`:那條案例的檔不擋,交開題者判。
#    命中兩道硬擋之一:這一支不再加 commit,worker 的 commit 留在整合分支上(land 停下、不 merge)。
# stamp:`AC_BATCH_STAMP` > EVIDENCE 就住在 `reports/batch/<stamp>/` > 現在時間。
# 退出碼:0 收好了;2 用法 / 前提(票、分支、base、工作樹髒);4 `<base>..` 沒有 commit;5 硬擋;
# 6 收好了,但 EVIDENCE 有 `OBJECTION:`(記在 result,不記進票)。
cmd_batch() {
    [ $# -ge 2 ] || { echo "apply: --batch <整合分支> <票號…> [--base <sha>] [--evidence <檔>]" >&2; exit 2; }
    B_BR=$1
    shift
    B_IDS=""; B_EVIDENCE=""; B_BASE=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --evidence|--base)
                b_flag=$1
                shift
                [ $# -ge 1 ] || { echo "apply: $b_flag 後面要值" >&2; exit 2; }
                if [ "$b_flag" = --base ]; then B_BASE=$1; else B_EVIDENCE=$1; fi ;;
            -*) echo "apply: --batch 不認得 $1" >&2; exit 2 ;;
            *)  B_IDS="$B_IDS $1" ;;
        esac
        shift
    done
    B_IDS=${B_IDS# }
    [ -n "$B_IDS" ] || { echo "apply: --batch 至少要一張票號" >&2; exit 2; }
    for b_id in $B_IDS; do
        [ -f "$TDIR/$b_id.json" ] || { echo "apply: 找不到票 #$b_id($TDIR/$b_id.json)" >&2; exit 2; }
    done
    git -C "$ROOT" rev-parse -q --verify "$B_BR^{commit}" >/dev/null \
        || { echo "apply: 整合分支 $B_BR 不存在" >&2; exit 2; }
    [ -n "$B_BASE" ] || B_BASE=$(git -C "$ROOT" rev-list --first-parent --merges -n 1 "$B_BR")
    [ -n "$B_BASE" ] && git -C "$ROOT" merge-base --is-ancestor "$B_BASE" "$B_BR" 2>/dev/null \
        || { echo "apply: base ${B_BASE:-(空)} 不在整合分支 $B_BR 的歷史上 —— 給 --base <派工前的頭>" >&2; exit 2; }
    B_BASE=$(git -C "$ROOT" rev-parse "$B_BASE")
    B_TAGS=$(for b_id in $B_IDS; do printf '#%s ' "$b_id"; done)
    B_TAGS=${B_TAGS% }
    B_ROOT=$ROOT/$(cfg reports_dir reports)/batch
    if [ -n "$B_EVIDENCE" ]; then
        B_EVIDENCE=$(cd "$(dirname "$B_EVIDENCE")" 2>/dev/null && pwd)/$(basename "$B_EVIDENCE")
        B_STAMP=${AC_BATCH_STAMP:-$(python3 -c 'import os,sys; d=os.path.realpath(os.path.dirname(sys.argv[1])); print(os.path.basename(d) if os.path.dirname(d)==os.path.realpath(sys.argv[2]) else "")' "$B_EVIDENCE" "$B_ROOT")}
    else
        B_STAMP=${AC_BATCH_STAMP:-}
    fi
    [ -n "$B_STAMP" ] || B_STAMP=$(date +%Y%m%d-%H%M%S)-$$
    B_DIR=$B_ROOT/$B_STAMP
    B_ROUND=${AC_ROUND:-1}
    mkdir -p "$B_DIR"
    [ -n "$B_EVIDENCE" ] || B_EVIDENCE=$B_DIR/EVIDENCE-round$B_ROUND.md
    B_NEW=$(git -C "$ROOT" rev-list --count "$B_BASE..$B_BR")
    echo "apply: 批次 $B_TAGS -> $B_BR  base $(echo "$B_BASE" | cut -c1-12)  $B_NEW 個 commit  紀錄 $B_DIR"
    if [ "$B_NEW" -eq 0 ]; then
        echo "apply: 整合分支 $B_BR 相對 base 沒有新 commit —— worker 的交件是它在整合分支上的 commit" >&2
        exit 4
    fi

    # 兩道硬擋都只看 `<base>..<整合分支>` 的 diff:印 `B <檔>\t<哪一格>\t<glob 或 regex>`(hard_block)、
    # `R <檔>:<行>\t<那一行>`(放寬測試)、`X <檔>\t<案例>`(反駁豁免)。
    B_OUT=$(python3 - "$AC" "$ROOT" "$B_EVIDENCE" "$B_BASE" "$B_BR" <<'PY'
import json, os, re, subprocess, sys
ac, root, evidence, base, branch = sys.argv[1:6]
sys.path.insert(0, ac)
import ticket as ticket_mod

try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
conf = conf if isinstance(conf, dict) else {}
test_globs = (conf.get("batch_fix") or {}).get("test_globs") \
    or ["tests/**", "verify/**", "**/test_*.py"]


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
lines = subprocess.run(["git", "-C", root, "diff", "--no-renames", base, branch],
                       capture_output=True, check=True).stdout.decode("utf-8", "replace").splitlines()
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
blocked = {name: (field, pattern) for name, field, pattern in ticket_mod.hard_block_hits(
    root, [row["name"] for row in sections],
    {row["name"]: [text for _, text in row["added"]] for row in sections})}
for row in sections:
    name = row["name"]
    if name in blocked:
        print("B %s\t%s\t%s" % (name, blocked[name][0], blocked[name][1]))
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
) || { echo "apply: 批次的寫入範圍 / 放寬測試量不出來" >&2; exit 2; }
    B_BLOCKED=$(echo "$B_OUT" | sed -n 's/^B //p')
    B_RELAX=$(echo "$B_OUT" | sed -n 's/^R //p')
    echo "$B_OUT" | sed -n 's/^X //p' | awk -F '\t' '{ printf "apply: %s 的測試改動不擋 —— EVIDENCE 有 OBJECTION: test_defect %s(交開題者判)\n", $1, $2 }'
    if [ -n "$B_BLOCKED" ]; then
        echo "apply: 動到這一批硬擋的檔(hard_block):" >&2
        echo "$B_BLOCKED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }' >&2
    fi
    if [ -n "$B_RELAX" ]; then
        echo "apply: 整合分支的 commit 在測試檔裡刪了斷言或加了 skip —— 修紅不准把案例弄啞:" >&2
        echo "$B_RELAX" | awk -F '\t' '{ printf "apply:   %s 放寬測試 —— %s\n", $1, $2 }' >&2
        echo "apply:   案例本身錯就在 EVIDENCE 寫 OBJECTION: test_defect <案例>,交開題者判" >&2
    fi
    if [ -n "$B_BLOCKED" ] || [ -n "$B_RELAX" ]; then
        echo "apply: 批次的 commit 被硬擋 —— 這一手沒有再加 commit,$B_BR 不該落地" >&2
        exit 5
    fi

    # 整合分支的工作樹:land 開的那一個就用它(同一條分支 git 只准一個 worktree),沒有才開。
    WT=$(git -C "$ROOT" worktree list --porcelain | awk -v ref="branch refs/heads/$B_BR" '
        /^worktree / { here = substr($0, 10) } $0 == ref { print here; exit }')
    if [ -z "$WT" ]; then
        WT=$WTBASE/batch-$(echo "$B_BR" | tr '/' '-')
        mkdir -p "$WTBASE"
        git -C "$ROOT" worktree add -q "$WT" "$B_BR" || { echo "apply: worktree add $B_BR 失敗" >&2; exit 2; }
        echo "apply: 開了整合分支 $B_BR 的 worktree $WT"
    fi
    B_DIRTY=$(git -C "$WT" status --porcelain --untracked-files=all | grep -v '^?? gate\.log')
    if [ -n "$B_DIRTY" ]; then
        echo "$B_DIRTY" | sed 's/^/apply:   /' >&2
        echo "apply: 整合分支的工作樹 $WT 有沒 commit 的改動 —— 重產會把它們一起 commit,停" >&2
        exit 2
    fi
    # code-map 索引重產(#129):與單票那一支同一份 `code_map_regen`;產出物有變就補一個 commit。
    code_map_regen "$WT"
    case $? in
        0)  git -C "$WT" add -- code-map/index.json code-map/INDEX.md || { echo "apply: git add code-map 失敗" >&2; exit 2; }
            if ! git -C "$WT" diff --cached --quiet; then
                git -C "$WT" commit -q -m "batch code-map 索引重產 round $B_ROUND $B_TAGS ($B_BR)

batch: $B_STAMP
tickets: $B_TAGS
round: $B_ROUND
regen: repo-map build (code-map/index.json, code-map/INDEX.md)" || { echo "apply: git commit 失敗(code-map 重產)" >&2; exit 2; }
                echo "apply: 批次 code-map 索引重產了,補一個 commit"
            fi ;;
        2)  echo "apply: 批次 code-map 索引沒重產(repo-map build 非零,上面 repo-map: 那幾行)—— 不擋收件(落地合併後 land 再重產一次)" >&2 ;;
    esac
    echo "apply: 批次 $B_TAGS -> $B_BR $(git -C "$WT" rev-parse --short HEAD)(各票分支與票檔都沒動)"
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
    python3 - "$B_RESULT" "$B_EVIDENCE" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(sys.argv[2], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
except OSError:
    text = ""
objections = [line for line in text.splitlines() if line.startswith("OBJECTION:")]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError) as exc:
    sys.stderr.write("apply: result 讀不到(%s)—— objections 沒寫進去:%s\n"
                     % (path, exc))
    raise SystemExit(0)
data["objections"] = objections
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
    if [ -f "$B_EVIDENCE" ] && grep -q '^OBJECTION:' "$B_EVIDENCE"; then
        echo "apply: EVIDENCE 裡有反駁 —— $(grep -m1 '^OBJECTION:' "$B_EVIDENCE")" >&2
        echo "apply:   批次不寫票:反駁沒有記進任何一張票的 objections[],由開題者 / 主線照 EVIDENCE 判" >&2
        exit 6
    fi
    echo "apply: 下一步 —— sh scripts/review.sh --batch $B_BR $B_IDS --run-id $B_STAMP --base $B_BASE"
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
    version = ticket.save_baseline(ident, record)
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

# ------------------------------------------------------------------ 收件(單票)
usage() {   # $1 = 出去的檔案描述子(1 = 有人問 --help,2 = 用錯了)
    {
        echo "用法:sh scripts/apply.sh <票號> [<patch-verify>] [旗標…]"
        echo "      sh scripts/apply.sh --batch <整合分支> <票號…> [--base <sha>] [--evidence <檔>]"
        echo "      sh scripts/apply.sh verify-plan <票號> --evidence-verifier <檔>…"
        echo "                             只把驗證者的 verify 計畫與驗紅併進票:不建分支、不 commit"
        echo ""
        echo "worker 的改動是票分支上它自己的 commit;這一支收 EVIDENCE、在分支上補 patch-verify"
        echo "與 code-map 重產的 commit。"
        echo ""
        echo "認得的旗標:"
        echo "  --evidence <檔>            實作者的 EVIDENCE:收記憶、抽 result-round<輪>.json、"
        echo "                             收 ^OBJECTION: 那一行(不給就讀輪次目錄那一份)"
        echo "  --evidence-verifier <檔>   驗證者的 EVIDENCE-verifier.md:抽 result-verifier-round<輪>.json"
        echo "                             (可重複給:verify 計畫與驗紅依給的順序逐份併進票)"
        echo ""
        echo "例:"
        echo "  sh scripts/apply.sh 7 patch-verify.diff \\"
        echo "     --evidence EVIDENCE-round1.md --evidence-verifier EVIDENCE-verifier.md"
    } >&"$1"
}
case "${1:-}" in
    --help|-h|help) usage 1; exit 0 ;;
esac
[ $# -ge 1 ] || { usage 2; exit 2; }
if [ "$1" = "--batch" ]; then
    shift
    cmd_batch "$@"
fi
if [ "$1" = "verify-plan" ]; then
    shift
    cmd_verify_plan "$@"
fi

ID=$1
VPATCH=""
EVIDENCE=""
VEVIDENCE=""
VEVIDENCES=""
shift
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
            [ -z "$VPATCH" ] || { echo "apply: 多出的參數 $1(只收一份 patch-verify;worker 的改動在分支上)" >&2; exit 2; }
            VPATCH=$1 ;;
    esac
    shift
done
TF=$TDIR/$ID.json
[ -f "$TF" ] || { echo "apply: 找不到票 #$ID($TF)" >&2; exit 2; }
ROUND=${AC_ROUND:-1}
[ -n "$EVIDENCE" ] || EVIDENCE=$WTBASE/fix-t$ID/round$ROUND/EVIDENCE-round$ROUND.md
if [ -n "$VPATCH" ]; then
    [ -f "$VPATCH" ] || { echo "apply: 找不到 patch-verify $VPATCH" >&2; exit 2; }
    VPATCH=$(cd "$(dirname "$VPATCH")" && pwd)/$(basename "$VPATCH")
fi

# 0. 主 repo 比對(#153,D-064 / D-065):`auto-fix.sh --prepare` 在輪次目錄(worker)/ 驗證者副本根
#    留了 `main-snapshot.json` 的,這裡比一次 —— 規則只有一份,在 `main_guard.py`。拒收 = rc 7,
#    擺在任何收件動作(寫票的 branch 欄、套 patch-verify、重產 code-map、收 EVIDENCE)之前;
#    只記不擋的每一筆印一行。結果寫進快照旁的 `main-compare.json`。找不到快照(手派的
#    subagent、headless 那幾輪)印一行「沒比」,其餘照舊(D-065 Q5)。
python3 "$AC/main_guard.py" compare --root "$ROOT" --repo "$(main_root)" --label apply \
    "$WTBASE/fix-t$ID/round$ROUND" "$(dirname "$EVIDENCE")" ${VPATCH:+"$(dirname "$VPATCH")"}
case $? in
    0) ;;
    7) echo "apply: #$ID 沒有收件(主 repo 比對拒收)—— 票、票分支、EVIDENCE 都沒動;讀上面點名的那一項再決定,不要把這一輪交回 subagent" >&2
       exit 7 ;;
    *) echo "apply: 主 repo 比對跑不起來(main_guard.py)—— 沒有收件" >&2
       exit 7 ;;
esac

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
    echo "apply: 票 #$ID 沒有 base_sha —— 答不出「這張票的交件是對哪個版本做的」" >&2
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
print(name)
PY
) || exit 2
WT=$WTBASE/$BR
RUN_ID=${AC_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}

status_start() {
    python3 "$AC/status.py" start --ticket "$ID" --kind apply \
        --run-id "$RUN_ID" --base-sha "$BASE" --worktree "$WT" \
        --verify-patch "$VPATCH" --round "$ROUND" \
        --repro "sh scripts/apply.sh $ID $VPATCH" --cwd "$ROOT" \
        >/dev/null 2>&1 || echo "apply: 狀態檔寫不出來(不擋收件)" >&2
}
status_done() {   # $1 = rc  $2 = 說明
    python3 "$AC/status.py" done --ticket "$ID" --kind apply \
        --run-id "$RUN_ID" --sha "$(git -C "$WT" rev-parse --short HEAD 2>/dev/null || echo '')" \
        --rc "$1" --note "$2" >/dev/null 2>&1 \
        || echo "apply: 狀態檔寫不出來(不擋收件)" >&2
}
die() {   # $1 = rc  $2… = 說明
    rc=$1; shift
    echo "apply: $*" >&2
    status_done "$rc" "$*"
    exit "$rc"
}

echo "apply: 票 #$ID  分支 $BR${VPATCH:+  patch-verify $(basename "$VPATCH")}"
status_start

# 1. 票分支的 worktree。worker 在它裡面 commit(auto-fix 派工前建);分支在而 worktree 不在
#    (主線從別處 fetch 回來的分支)就掛上去。分支不在 = 沒有交件可收。
if [ ! -e "$WT/.git" ]; then
    git -C "$ROOT" rev-parse -q --verify "refs/heads/$BR" >/dev/null \
        || die 2 "票分支 $BR 不在 —— worker 的交件是這條分支上的 commit(auto-fix 派工前建)"
    mkdir -p "$WTBASE"
    git -C "$ROOT" worktree add -q "$WT" "$BR" || die 2 "worktree add $BR 失敗"
    echo "apply: 分支 $BR 掛上 worktree $WT"
fi
SUBJECT=$(python3 - "$TF" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print((json.load(handle).get("subject") or "").strip())
except (OSError, ValueError):
    print("")
PY
)

# 2. 驗證者的 patch-verify:`--index` 只 stage patch 自己的改動(閘門殘留等未追蹤檔不會跟進來)。
#    預檢不過就**不要套**:套一半的工作樹比沒套更難收。
if [ -n "$VPATCH" ]; then
    if ! git -C "$WT" apply -p1 --index --check "$VPATCH" 2>/tmp/ac-apply-$$.err; then
        sed 's/^/apply:   /' /tmp/ac-apply-$$.err >&2
        rm -f /tmp/ac-apply-$$.err
        die 3 "patch-verify 套不上分支 $BR(git apply --check 不過)"
    fi
    rm -f /tmp/ac-apply-$$.err
    git -C "$WT" apply -p1 --index "$VPATCH" || die 3 "patch-verify 套不進去(--check 過了卻套不進去)"
fi

# 3. 硬擋只剩 `board/config.json` 的 `hard_block`(D-054 Q2、Q3;#135 的三個執行點之一):
#    量的是這張票的改動 = 分支相對主線分岔點的 commit 加剛 stage 的 patch-verify(`paths` 比檔名,
#    `patterns` 比 tests/** verify/** 的新增行)。命中就把 patch-verify 退出來、分支不動,rc 5。
OUT=$(python3 - "$ROOT" "$WT" "$MAIN" <<'PY'
import os, subprocess, sys
root, wt, main = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticket_mod
fork = subprocess.run(["git", "-C", wt, "merge-base", main, "HEAD"],
                      capture_output=True, text=True).stdout.strip() or main
names = subprocess.run(["git", "-C", wt, "diff", "--cached", "--name-only", "-z", fork],
                       capture_output=True, text=True).stdout.split("\0")
diff = subprocess.run(["git", "-C", wt, "diff", "--cached", fork],
                      capture_output=True, text=True, errors="replace").stdout
for name, field, pattern in ticket_mod.hard_block_hits(root, [n for n in names if n],
                                                       ticket_mod.added_lines(diff)):
    print("B %s\t%s\t%s" % (name, field, pattern))
PY
)
BLOCKED=$(echo "$OUT" | sed -n 's/^B //p')
if [ -n "$BLOCKED" ]; then
    echo "apply: 動到票 #$ID 硬擋的檔:" >&2
    echo "$BLOCKED" | awk -F '\t' '{ printf "apply:   %s —— 命中%s(`%s`)\n", $1, $2, $3 }' >&2
    # 退 patch-verify 用 `reset --merge`:`apply -R` 對 `base/` `work/` 兩層 patch 的新檔只會清空、不會刪;
    # `--merge` 只動 index 與它對應的檔,未追蹤的閘門殘留與沒 stage 的改動留著。
    [ -z "$VPATCH" ] || git -C "$WT" reset -q --merge HEAD \
        || echo "apply:   patch-verify 退不出來 —— worktree $WT 要人看" >&2
    echo "apply:   分支 $BR 沒動(這一手沒有 commit)—— 拿掉命中的改動;真要動它就先改 config" >&2
    die 5 "命中硬擋(hard_block)"
fi
if [ -n "$VPATCH" ]; then
    git -C "$WT" commit -q -m "t$ID patch-verify: $SUBJECT

ticket: #$ID
round: $ROUND" || die 2 "git commit 失敗(patch-verify)"
    echo "apply: patch-verify 進分支 $BR($(git -C "$WT" rev-parse --short HEAD))"
fi

# 4. code-map 索引重產(#96、D-051)。候選樹有地圖就跑一次 `repo-map.py build`:交件新增 / 改名 /
#    刪掉已管理的檔而沒重生,以前要閘門擋下一整輪。產出物有變就補一個 commit;build 非零**不擋**
#    (閘門不量索引新鮮度,D-054 Q4;落地合併後 land 會再重產一次)。
#    判準與動作只有一份:`wtbase.sh` 的 `code_map_regen`(`--batch` 與 land 也叫它,#129)。
code_map_regen "$WT"
case $? in
    0)  git -C "$WT" add -- code-map/index.json code-map/INDEX.md || die 2 "git add code-map 失敗"
        if ! git -C "$WT" diff --cached --quiet; then
            git -C "$WT" commit -q -m "t$ID code-map 索引重產

ticket: #$ID
round: $ROUND
regen: repo-map build (code-map/index.json, code-map/INDEX.md)" || die 2 "git commit 失敗(code-map 重產)"
            echo "apply: code-map 索引重產了,補一個 commit"
        fi ;;
    2)  echo "apply: code-map 索引沒重產(repo-map build 非零,上面 repo-map: 那幾行)—— 不擋收件(落地合併後 land 再重產一次)" >&2 ;;
esac
SHA=$(git -C "$WT" rev-parse --short HEAD)
echo "apply: #$ID -> $BR $SHA($(git -C "$WT" rev-list --count "$MAIN..HEAD") 個 commit)"
REPORTS=$ROOT/$(cfg reports_dir reports)/t$ID/$RUN_ID
if [ -f "$EVIDENCE" ]; then
    python3 "$AC/memory.py" harvest "$EVIDENCE" \
        || echo "apply: EVIDENCE 記憶收割失敗($EVIDENCE)—— 不擋收件" >&2
else
    echo "apply: 找不到 EVIDENCE $EVIDENCE —— 記憶 0 筆" >&2
fi
# **收件的同一手抽 result**(#29 A4)。抽不出來的三種樣子由 `ticket.py result`
# 分開記(`no-evidence` / `no-block` / `bad-json`),一律不改退出碼、不擋流程 ——
# 揉成同一個空檔的那一刻,「沒交」與「交了但都是空的」長得一樣。
# `AC_RESULT_DONE=1` = 上游(`auto-fix.sh`)在收件的同一手已經抽過了。兩邊都抽的
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
# 驗證者的計畫與驗紅併進票(`merge_verifier_plans`,上面那一段的說明)。解析不到照樣出聲,
# 但不擋收件 —— 交件已經在分支上了。
merge_verifier_plans "$ID" "$VEVIDENCES"
# **反駁放在最後**:交件已經在分支上了(那是事實),但這一手沒有結束 ——
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
            status_done 6 "交件在分支上,但 worker 提了反駁"
            exit 6 ;;
        3)
            # 同一筆第二次收到,而**還沒處置**:與第一次收到一樣擋。以前這裡放行成 0,
            # 未處置的反駁第二輪就過去了(D-014 硬閘門)。
            echo "apply: #$ID 這一筆反駁票上已經有了,但還沒處置 —— 處置它(accepted / rejected / deferred / fixed)" >&2
            echo "apply:   沒處置的阻擋項 land 與 close 都會拒絕。" >&2
            status_done 6 "交件在分支上,但 worker 的反駁還沒處置"
            exit 6 ;;
        4)
            echo "apply: #$ID 這一筆反駁票上已經有了,而且已處置 —— 沒有再記一次" >&2 ;;
        *)
            # 記不進票(rc=2)不是「已經有了」:沒人收的反駁,與沒有反駁長得一樣(§7)。
            echo "apply: #$ID 的反駁記不進票(ticket.py objection rc=$orc)—— 修好之後手動記:" >&2
            echo "apply:   python3 scripts/ticket.py objection $ID --line \"$OLINE\" --evidence $EVIDENCE" >&2
            status_done 2 "交件在分支上,但 worker 的反駁記不進票"
            exit 2 ;;
    esac
fi
status_done 0 "收件完成,分支頭 $SHA"
echo "apply: 下一步 —— (cd $WT && sh scripts/gate.sh --branch --ticket $ID)"
echo "apply:       閘門綠了主線覆核記 review,再落地:"
land_entry "$(main_root)" "$BR"
exit 0
