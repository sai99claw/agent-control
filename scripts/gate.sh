#!/bin/sh
# 閘門:三層介面,專案自己填對照表 — `docs/WORKFLOW.md` §閘門。
#
#   sh scripts/gate.sh --branch          # 這條分支改動的檔對應到的模組
#   sh scripts/gate.sh --base            # 基礎組:不管改了什麼都要過的那幾支
#   sh scripts/gate.sh --branch --base   # 票收尾
#   sh scripts/gate.sh --full            # 全套(land.sh 跑的就是這一發)
#   sh scripts/gate.sh <檔> <檔> …       # 指定檔
#   sh scripts/gate.sh --branch --ticket 7   # 同上,外加狀態檔、票的回歸、flake 重跑
#   sh scripts/gate.sh --branch --ticket 7              # 紅了預設派下一輪 worker
#   sh scripts/gate.sh --branch --ticket 7 --no-auto-fix # 明說要人下場
#   sh scripts/gate.sh --branch --ticket 7 --no-cache   # 不吃同一輪的回歸快取
#   (`--branch --ticket` 手跑綠 → 票轉 InReview;complex 票叫 scripts/review.sh 派覆核;見下)
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
# ## 驗證者的案例:`check`(D-020,`docs/DESIGN-VERIFY-CASES.md` §三)
# 回歸層之後還有一層:**驗證者寫的那幾條案例,在這一輪的樹上綠了沒有**。
# `verify-case.py red`(驗證者交件那一趟)只證「乾淨基底上該紅」;候選該綠一直沒有人
# 自動量 —— 而唯一被點名去量的那個人(驗證者)在時間上拿不到實作者的 patch,於是他
# 只剩一條路:自己搭一份拋棄式參考實作(#23 那 340K 的來源)。
#
# 所以這一支在 `regression()` 之後多一步(只跑案例,不先驗案例的格式,D-054 Q4):
#   `verify-case.py check <票號> --ref <票的 base_sha> --candidate <這一輪的樹>`
#      —— 把票的 `verify.baseline.stage` 從 `red` 升成 `check`(閘門的證據;
#      `ticket.py close` 不讀它,D-054 Q4)。`ok:false` 時 candidate 那一趟的原始輸出當成 `--log` 餵給
#      狀態檔,紅榜因此落進 `failures[]`,走既有的 auto-fix 路(D-014,不重造)。
#
# **量在閘門,不在 land**:worker 的三輪修復迴圈住在閘門,量在落地等於紅了才發現,
# 而那是多一整輪的事。`verify.files` 是空的而這張票**該有**案例時,這一層印
# 「這張票沒有驗證者案例」並且**非零** —— 同 #619 那條「宣告了卻選不到案例 = 缺口」
# 的精神:一張沒有人驗的票與一張驗過的票,不該在退出碼上長得一樣。
# 「該有」的兩個例外:票上明著寫 `needs_verifier=false`、或**票上連 `verify` 這一格都
# 沒有宣告**(那是「沒有宣告」,與「宣告了卻沒有案例」不同 —— 同一支腳本對回歸層就是
# 這樣分的)。兩個例外都只管「有沒有案例」那一格:`verify.files` 非空時,check **一律照跑**。
#
# `check` 回非零的兩種要分開:**候選樹上真的有紅**才算這一輪(worker)的紅,紅榜進
# `failures[]`、走 auto-fix;`ok:false` 但候選全綠(乾淨基底沒紅、兩邊案例數不同)是
# **驗證者那一趟**(`stage=red`)的事,worker 修不動 —— 出聲、寫進 note,但不算這一輪
# 的紅;票上那一格仍是 `ok:false`,`ticket.py close` 照樣擋著。
#
# ## 測試只走五個命令(D-071 C6 §4,#162)
# 這一支不知道專案的測試長什麼樣:`scripts/testiface.py` 跑專案在 `board/config.json`
# `tests.*` 宣告的命令(沒宣告 = 內建的 unittest adapter),閘門只讀退出碼、`select` 的
# stdout 與 `result.json`。順序:硬擋 → `preflight`(rc 86 = 環境可疑、測試不跑;其他非零
# = 2「preflight 本身壞了」)→ `select --mode branch|base`(改動檔一行一個餵進去;
# `unmapped <檔>` 照舊走下面「對不到」那一段)→ `run <ids>` / `full` → 紅例 `run <id>` 單跑、
# `run <全部 ids>` 整組重跑 → `regression --tag …` → 驗證者那一層。每一步的輸出在自己的
# `AC_TEST_OUT`(有票:`reports/t<票號>/<run_id>/<步驟>/`;沒票:`<log>.out/<步驟>/`)。
# 綠 = rc 0 **而且** result 驗過;量不到(逾時、result 壞、0 個 id……)= 2,不是紅也不是綠。
#
# ## 紅榜的處理順序:先環境 fail-fast、再 flake 判定(#7)
# 內建 adapter 在同一個程序裡看每一條結果:同一引擎、同形訊息連紅達 `board/config.json` 的
# `environment_fail_fast_threshold`(預設 8)就**中止那一段**、回 rc=86;專案自己的 `run`
# 回 86 也一樣(rows 在 `result.json`)。
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
# 給了票號,跑完發 `gate.pass` / `gate.fail`;只有環境可疑(不自動派、要人動手)那一種
# 另寫一則 `reports/inbox/<票號>-<run_id>.md`(kind=decision,D-032):綠由 review.sh
# 接手、可歸因的紅由 auto-fix.sh 接手,不發頁。主線因此不必輪詢 status ——
# 每看一次背景工作就是整份上下文重送一輪。
#
# ## auto-fix:紅了預設派下一輪(D-010)
# 紅了就呼叫 `scripts/auto-fix.sh <票號>`。**閘門自己的退出碼不會因此變綠** ——
# 這個 commit 對這一組測試就是紅的,而 auto-fix 產生的是**下一個** commit。
# 修好了沒、停在哪一種,看 inbox 那一頁。
#
# ## 手跑綠:票轉 InReview、complex 票派覆核(#42,D-025 ②;#136)
# `--branch --ticket <n>` 綠、而且**不是** auto-fix 叫的(`AC_IN_AUTOFIX` 空)時,先
# `ticket.py set <n> state InReview`,complex 票再叫 `scripts/review.sh <n>`;simple 或沒標
# (= simple)的票不派覆核、停在 InReview 等 land(只有複雜的票做完後檢查)。以前手跑綠沒有人轉
# InReview —— 票停在 Running,與「還在修」長得一樣。auto-fix 叫的那一趟由 auto-fix 自己
# 轉、自己叫(免得同一個綠派兩次覆核);`--full`(land 跑的那一發)與指定檔不觸發。
# **閘門自己的 rc 不動**:覆核的結果看 review.sh 那一頁。
#
# **每個專案跑的都是這一份**(D-071):專案的知識只在 `tests.*` 命令裡(範本
# `templates/project-tests.example.sh` + `templates/project-config.example.json`)。
#
# ## 對照表住在設定裡(#161、#162)
# 「哪些測試守哪些檔」是專案的 `select`;內建版讀 `tests.unittest.map`(A 自己的那一份在
# A 的 `board/config.json`)。誰改了它就是在改這件事本身,要進 code review —— 它是設定
# 資料,一樣進版控、一樣過覆核。自動推導出來的對照表沒有這一層。
# `tests.*`(含這份對照表)一律從 **`AC_ROOT`** 的 `board/config.json` 讀,A 自己也一樣(D-071
# C2,#162 開題者裁定):一張票在自己分支上新增的對照列**落地後才生效**,所以它的 `--branch`
# 可能對新檔印 `unmapped` / 退 3(同票改了 tests/test_*.py 時由下面推得的那一手接住);
# land 跑的 `--full` 不讀對照表。這是已接受的代價,不是缺口 —— 不要在這裡為 A 開特例。
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
# 同伴腳本一律從**這一份閘門自己住的目錄**叫(不認 `AC_CONTROL_DIR`):land / auto-fix 在 A 自己的
# 票 worktree 裡跑的是 worktree 那一份閘門(D-071 C3),它的 status.py / verify.py 要是同一棵樹的。
AC=$(cd "$(dirname "$0")" && pwd)
export AC_CONTROL_DIR=$AC
. "$AC/wtbase.sh"
# 程式碼根(受測的那棵樹,D-071 §1):`AC_CODE_ROOT`,否則 cwd 的 toplevel —— 不從 `$0` 來。
# 票根(票、事件、reports)是另一個:`AC_ROOT` / cwd 的主 repo 根,見「手跑在票 worktree 裡」那一段。
ROOT=$(ac_code_root) || { echo "gate: $AC_NOT_IN_PROJECT" >&2; exit 2; }
export AC_CODE_ROOT=$ROOT
LOG=${AC_GATE_LOG:-$ROOT/gate.log}
VERIFY_LOG=${AC_VERIFY_LOG:-$ROOT/verify.log}
SUSPECT_FILE=$LOG.env-suspect.json
MAIN=main
ALL_ARGS="$*"

mods=""
unmapped=""
files=""
add() { for m in "$@"; do case " $mods " in *" $m "*) ;; *) mods="$mods $m" ;; esac; done; }

want_base=0; want_branch=0; want_full=0
want_autofix=1
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
        --no-auto-fix) want_autofix=0 ;;
        --no-cache) NO_CACHE=1 ;;
        --*) echo "gate: 不認得 $1(--branch / --base / --full / --ticket <票號> / --auto-fix / --no-auto-fix / --no-cache)" >&2; exit 2 ;;
        *) files="$files $1" ;;
    esac
    shift
done
[ "$ARGC" -ge 1 ] || { echo "gate: 要 --branch / --base / --full 或一串檔名" >&2; exit 2; }

# 批次落地的全套(#109,D-H51 ②):`AC_GATE_TICKETS="1 2"` 時狀態檔**每張票各寫一份**
# (同 run_id、同一份 log 剖出的 failures)。只管狀態檔:`TICKET` 不跟著設 —— 它還會打開
# 硬擋、flake 重跑、票的 gate 欄,而那些是一張票的事。`auto-fix.sh --batch` 讀第一張那份。
STATUS_IDS=$TICKET
if [ "$want_full" -eq 1 ]; then
    for t in ${AC_GATE_TICKETS:-}; do
        case " $STATUS_IDS " in *" $t "*) ;; *) STATUS_IDS="$STATUS_IDS $t" ;; esac
    done
fi

# `--branch --ticket <n>` 在主線上空比(#87 A3):主 repo 的 HEAD 是主線、票在、而除了
# 票庫底下的 *.json 之外**什麼改動都沒有** —— `main...HEAD` 是空的,能比的只剩主工作樹
# 裡的票檔,而它會讓狀態檔記一個「綠」。扣的是票庫裡**每一張**
# 票檔(別張票、已追蹤或未追蹤):`ticket.py set` 寫票不 commit,真主線上常躺著別張票的
# 未提交票檔,只扣本票那一張,這一段在真主線上幾乎不觸發。那不是這張票的閘門,是跑錯地方:
# 拒跑、指出該去的 worktree(`wtbase.sh` 同一條規則),不寫狀態檔、不動票、不發事件。
# 主線上帶著真改動跑的(沙盒、指定檔)照舊走下面那一路。
if [ "$want_branch" = 1 ] && [ -n "$TICKET" ]; then
    python3 - "$ROOT" "$TICKET" "$MAIN" <<'ON_MAIN_PY' || exit 2
import json, os, subprocess, sys

root, ident, main = sys.argv[1:4]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import event

def git(*args):
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None

if git("symbolic-ref", "--short", "-q", "HEAD") != main:
    raise SystemExit(0)
common = git("rev-parse", "--git-common-dir")
if common is None or os.path.normpath(os.path.join(root, common)) != os.path.join(
        os.path.normpath(root), ".git"):
    raise SystemExit(0)
ticket_file = os.path.join(event.tickets_dir(root), "%s.json" % ident)
if not os.path.isfile(ticket_file):
    raise SystemExit(0)
tickets_rel = os.path.relpath(event.tickets_dir(root), root)
changed = set()
for args in (("diff", "--name-only", "%s...HEAD" % main), ("diff", "--name-only", "HEAD"),
             ("ls-files", "--others", "--exclude-standard")):
    changed.update(name for name in (git(*args) or "").splitlines() if name)
changed = {name for name in changed if not (
    name.endswith(".json") and os.path.normpath(name).startswith(tickets_rel + os.sep))}
if changed:
    raise SystemExit(0)
try:
    with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
base = os.environ.get("AC_WORKTREE_DIR") or conf.get("worktree_dir") or ""
if not base:
    base = os.path.join(root, "..", os.path.basename(root) + "-wt")
elif not os.path.isabs(base):
    base = os.path.join(root, base)
target = os.path.normpath(os.path.join(base, "t%s" % ident))
sys.stderr.write("gate: --branch 不在主線上跑 —— %s 的 HEAD 是 %s,除了票檔沒有任何改動可比;"
                 "到票的 worktree 跑:cd %s && sh scripts/gate.sh --branch --ticket %s\n"
                 % (root, main, target, ident))
raise SystemExit(1)
ON_MAIN_PY
fi

# 票根(#53;D-071 C2):`AC_ROOT`,否則 cwd 的主 repo 根 —— 手跑在票 worktree 裡時它與程式碼根
# 不同,票與狀態寫主 repo 那一份,不寫 worktree 裡的。定下來就 export,子行程(status / ticket /
# inbox / verify-case)讀同一個;land / auto-fix 起的閘門本來就帶著 `AC_ROOT`。
if [ -z "${AC_ROOT:-}" ]; then
    AC_ROOT=$(ac_root gate) || exit 2
    export AC_ROOT
    if [ -n "$TICKET" ] && [ "$AC_ROOT" != "$(cd "$ROOT" && pwd -P)" ]; then
        echo "gate: $ROOT 是 worktree —— 票改讀主 repo $AC_ROOT"
    fi
fi

# 硬擋(#90 A1,D-054 Q4:preflight 只剩 `hard_block`):每一條命中都印 `gate: 硬擋命中 —— <原文>`
# 並記進 HARD_LOG,全部量完才退 4 —— 退之前寫一份**自己的**狀態檔(kind gate、rc 4、note 是
# 那幾行原文,見 status_nothing 之後那一段)。以前 exit 4 在狀態檔之前,auto-fix 讀到的最新
# 一筆是 apply 那一輪(rc 0、紅 0 條),發的是「紅了但沒有歸因」。
HARD_LOG=$LOG.hard-block
HARD_NOTE=""

if [ -n "$TICKET" ]; then
    : > "$HARD_LOG"
    python3 - "$ROOT" "$TICKET" "$MAIN" "$HARD_LOG" <<'PREFLIGHT_PY'
import os, subprocess, sys

root, ident, main, hard_log = sys.argv[1:5]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import event
import ticket as ticket_mod

# preflight 與 ticket_tags 走 ticketlib.load(#53,同 verify_plan):票根由
# `event.repo_root()`(`AC_ROOT` 蓋得掉)決定,不拿 `$ROOT` 拼。
path = ticket_mod.ticket_path(ident)
try:
    ticket = ticket_mod.load(ident)
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
for ticket_file in (path, os.path.join(event.tickets_dir(root), "%s.json" % ident)):
    try:
        ticket_rel = os.path.relpath(ticket_file, root)
    except ValueError:
        ticket_rel = ""
    if ticket_rel and not ticket_rel.startswith(".." + os.sep):
        changed = [name for name in changed if name != ticket_rel]

# 硬擋比的是**這張票的 patch**(#87 A4):分支相對主線的 diff 加未提交的改動。
# 票檔不論在哪一側都不算。
ticket_rels = []
for ticket_file in (path, os.path.join(event.tickets_dir(root), "%s.json" % ident)):
    try:
        ticket_rel = os.path.relpath(ticket_file, root)
    except ValueError:
        continue
    if not ticket_rel.startswith(".." + os.sep) and ticket_rel not in ticket_rels:
        ticket_rels.append(ticket_rel)
not_ticket = ["--", "."] + [":(exclude)%s" % rel for rel in ticket_rels]
added = {}   # 給 hard_block.patterns:同一份 diff 的新增行,依檔分(新檔整份算新增)
for args in (("diff", "--no-ext-diff", "--unified=0", "%s...HEAD" % main),
             ("diff", "--no-ext-diff", "--unified=0", "HEAD")):
    text = git("-c", "core.quotePath=false", *args, "--src-prefix=a/", "--dst-prefix=b/",
               *not_ticket).decode("utf-8", "replace")
    for name, lines in ticket_mod.added_lines(text).items():
        added.setdefault(name, []).extend(lines)
tracked = set(names("ls-files", "-z"))
for name in changed:
    if name in tracked:
        continue
    try:
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as handle:
            added.setdefault(name, []).extend(handle.read().splitlines())
    except OSError:
        continue

errors = []
# 硬擋(D-054 Q2):只擋 `hard_block`(`paths` 比檔名、`patterns` 比 tests/** verify/** 的新增行)。
for name, field, pattern in ticket_mod.hard_block_hits(root, changed, added):
    errors.append("%s:%s 命中 `%s`" % (field, name, pattern))

if errors:
    with open(hard_log, "a", encoding="utf-8") as handle:
        for error in errors:
            print("gate: 硬擋命中 —— " + error, file=sys.stderr)
            handle.write("gate: 硬擋命中 —— %s\n" % error)
    raise SystemExit(4)
PREFLIGHT_PY
    preflight_rc=$?
    case "$preflight_rc" in 0|4) ;; *) exit "$preflight_rc" ;; esac
    [ -s "$HARD_LOG" ] && HARD_NOTE=$(cat "$HARD_LOG")
fi

SHA=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "")
# 票 `gate` 欄的 sha 在開跑這一刻就定:紅了派出去的 auto-fix 可能在同一個 worktree 推進
# HEAD,跑完再問會把這一輪的結果記到下一個 commit 上。
HEAD_SHA=$(git -C "$ROOT" rev-parse -q --verify "HEAD^{commit}" 2>/dev/null || echo "")
BASE_SHA=$(git -C "$ROOT" rev-parse "$MAIN" 2>/dev/null || echo "")
RUN_ID=${AC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}
# 哪一版 A 跑的這一輪(D-071 §1):`gate.start` / `gate.pass` / `gate.fail` 都帶它。
AC_SHA=$(ac_sha "$(ac_home)")
FLAKY_ARGS=""
AUTO_FLAKY_ARGS=""
# `status.py done --result`:測試那一步與回歸那一步各自的 result.json(驗過才給,見 run_tests)。
RESULT_ARGS=""
REG_RESULT_ARGS=""
RUN_OUT=""
ORDER_ARGS=""
EXTRA_LOG_ARGS=""
VERIFY_LOG_ARGS=""
CHECK_LOG_ARGS=""
NOTE=""
ENV_SUSPECT=0
# 驗證者那一層的 rc(`verify_case` 設它);`VRC` 之於回歸層是什麼,它之於這一層就是
# 什麼 —— 兩層各自的 rc 分開留著,合併在呼叫的那一行做。
CRC=0
# 宣告了 verify 卻沒有案例(#126 A2):給 `status.py done` 的 `--no-cases-log …`,紅榜恰一列缺口。
CASES_ARGS=""
CASES_LOG=$LOG.no-cases
CHECK_LOG=$LOG.verify-case
CHECK_DIR=$LOG.verify-case.d

# `--suspect-file` 那一份是**一個 list**(形狀見 `docs/DESIGN-ENV-SUSPECT.md`,D-019);
# 連紅統計達門檻時只會有第一筆,所以這裡讀 `[0]`。舊的單筆 dict 也讀得懂。
suspect_field() {   # $1 = 第一筆的欄位;讀不到就印空的,不猜
    python3 - "$SUSPECT_FILE" "$1" <<'SUSPECT_PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        rows = json.load(handle)
except (OSError, ValueError):
    rows = []
if isinstance(rows, dict):
    rows = [rows]
row = rows[0] if isinstance(rows, list) and rows else {}
print(row.get(sys.argv[2], "") if isinstance(row, dict) else "")
SUSPECT_PY
}

# 這一輪的 `environment_suspect` 有幾筆。**問的是 `status.py suspects`,不是 grep
# `done` 的輸出** —— grep 不到與零筆長得一樣(`docs/DISPATCH-TEMPLATE.md` §5.5)。
# 問不出來印 `?`,不印 0:那兩件事的下一步不同(見 `auto_fix`)。
env_suspect_rows() {
    [ -n "$TICKET" ] || { echo 0; return 0; }
    python3 "$AC/status.py" suspects --ticket "$TICKET" \
        --run-id "$RUN_ID" --count 2>/dev/null || echo "?"
}

# 測試介面的每一步都走這裡(D-071 §4):`scripts/testiface.py` 跑專案宣告的命令(或內建),
# 驗過退出碼與 result.json 才回來。每一步一個**新的** `AC_TEST_OUT`(`$OUT/<步驟>`,閘門自己
# 的目錄,重跑同一個 run_id 就先清掉它);testiface 的 stderr(量不到的原因、select 自己印的話)
# 留在 `$TI_ERR`,呼叫的那一段決定要不要印。rc 86 的 rows 由 testiface 抄進 `$SUSPECT_FILE`。
testiface() {   # $1 = AC_TEST_MODE  $2 = 步驟目錄(相對 $OUT)  其餘 = testiface 的命令與參數
    TI_OUT=$OUT/$2
    TI_ERR=$TI_OUT.stderr
    ti_mode=$1
    shift 2
    rm -rf "$TI_OUT" "$TI_ERR"
    mkdir -p "$(dirname "$TI_OUT")"
    AC_TEST_OUT=$TI_OUT AC_TEST_MODE=$ti_mode AC_TICKET=$TICKET AC_RUN_ID=$RUN_ID \
        python3 "$AC/testiface.py" --suspect-file "$SUSPECT_FILE" "$@" 2>"$TI_ERR"
}

# `run <ids>` / `full`。判綠只看 rc:0 = 綠(result 驗過)、1 = 紅、86 = 環境可疑、2 = 量不到。
# 那一步的 log 抄成 `$LOG`(閘門的 log 照舊是「這一輪測試的原文」:狀態檔留它當附件、
# `grep Ran` 讀它)。result 驗過(0 / 1 / 86)才交給狀態檔 —— 量不到的那一份不准當紅榜讀。
run_tests() {   # $1 = AC_TEST_MODE  $2 = run|full  其餘 = ids;設 RUN_OUT 與 RESULT_ARGS
    rt_mode=$1
    rt_cmd=$2
    shift 2
    testiface "$rt_mode" "$rt_cmd" "$rt_cmd" "$@"
    rt_rc=$?
    RUN_OUT=$TI_OUT
    if [ -f "$RUN_OUT/$rt_cmd.log" ]; then cp "$RUN_OUT/$rt_cmd.log" "$LOG"; else : > "$LOG"; fi
    [ ! -s "$TI_ERR" ] || cat "$TI_ERR"
    case "$rt_rc" in
        0|1|86) RESULT_ARGS="--result $RUN_OUT/result.json" ;;
        *) RESULT_ARGS="" ;;
    esac
    return "$rt_rc"
}

# 環境可疑那一條路(D-019):`preflight` 回 86(測試沒跑),或 `run` / `full` 回 86(那一段被中止)。
# rows 已在 `$SUSPECT_FILE`;run 那一份的 `log` 指的是那一步自己的 log,抄成 `$LOG` 了,改指它。
env_suspect_note() {   # $1 = preflight|run
    ENV_SUSPECT=1
    if [ "$1" = preflight ]; then
        NOTE="$NOTE preflight 說環境可疑,測試沒跑(不做 flake 判定)。"
        echo "gate: 環境可疑(preflight)—— $(suspect_field engine):$(suspect_field why);測試沒跑"
        return 0
    fi
    python3 - "$SUSPECT_FILE" "$RUN_OUT" "$LOG" <<'SUSPECT_LOG_PY' || true
import json, os, sys
path, out, log = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    rows = json.load(handle)
for row in rows:
    where = row.get("log") if isinstance(row, dict) else None
    if isinstance(where, str) and where and os.path.dirname(os.path.abspath(where)) == os.path.abspath(out):
        row["log"] = log
with open(path, "w", encoding="utf-8") as handle:
    json.dump(rows, handle, ensure_ascii=False)
    handle.write("\n")
SUSPECT_LOG_PY
    NOTE="$NOTE 同一引擎同形訊息連紅達環境門檻,已中止這一段(不做 flake 判定)。"
    echo "gate: 環境可疑 —— $(suspect_field engine) 連紅 $(suspect_field count) 條同形訊息,這一段中止了"
}

# 測試開跑的那一刻發 `gate.start`(帶 `ac_sha`,#159)。land 的全套由 land.sh 自己發
# (`AC_NO_INBOX`,同 gate.pass / gate.fail);硬擋命中不起測試,也就不發。發不出去出聲、不擋閘門。
gate_start_event() {
    [ -z "${AC_NO_INBOX:-}" ] || return 0
    if [ "$want_full" -eq 1 ]; then mode=full
    elif [ "$want_branch" -eq 1 ]; then mode=branch
    elif [ "$want_base" -eq 1 ]; then mode=base
    else mode=files; fi
    set -- --kv "mode=$mode" --kv "run_id=$RUN_ID" --kv "sha=$SHA" --kv "ac_sha=$AC_SHA"
    [ -z "$TICKET" ] || set -- --ticket "$TICKET" "$@"
    python3 "$AC/event.py" emit gate.start "$@" >/dev/null 2>&1 \
        || echo "gate: 事件發不出去(gate.start,不擋閘門)" >&2
}
# 狀態檔發不出去要出聲,但**不擋閘門** —— 同 land.sh 的事件:寫不出來的那一刻正是
# 最需要紀錄的那一刻,而讓它擋住測試會把一個紀錄問題升級成一個交付問題。
status_start() {
    gate_start_event
    [ -n "$STATUS_IDS" ] || return 0
    for t in $STATUS_IDS; do
        python3 "$AC/status.py" start --ticket "$t" --kind gate \
            --run-id "$RUN_ID" --sha "$SHA" --base-sha "$BASE_SHA" \
            --worktree "$ROOT" --round "${AC_ROUND:-1}" \
            --patch "${AC_PATCH:-}" --verify-patch "${AC_VERIFY_PATCH:-}" \
            --prev-evidence "${AC_PREV_EVIDENCE:-}" \
            --repro "sh scripts/gate.sh $ALL_ARGS" --cwd "$ROOT" \
            >/dev/null 2>&1 || echo "gate: #$t 的狀態檔寫不出來(不擋閘門)" >&2
    done
}

status_done() {   # $1 = rc
    [ -n "$STATUS_IDS" ] || return 0
    state_args=""
    if [ "$ENV_SUSPECT" -eq 1 ]; then
        state_args="--state env_suspect --environment-log $SUSPECT_FILE"
    fi
    for t in $STATUS_IDS; do
        # shellcheck disable=SC2086
        python3 "$AC/status.py" done --ticket "$t" --kind gate \
            --run-id "$RUN_ID" --sha "$SHA" --rc "$1" --note "$NOTE" \
            --log "$LOG" $RESULT_ARGS $REG_RESULT_ARGS $state_args $VERIFY_LOG_ARGS $CHECK_LOG_ARGS \
            $EXTRA_LOG_ARGS $CASES_ARGS $FLAKY_ARGS $AUTO_FLAKY_ARGS $ORDER_ARGS \
            || echo "gate: #$t 的狀態檔寫不出來(不擋閘門)" >&2
    done
    inbox_post "$1"
}

# 終態叫醒主線(D-015)。**只有要人動手的終態發頁**(D-032):閘門綠(review.sh 接手)與
# 閘門紅可歸因(auto-fix.sh 接手)只發 gate.pass / gate.fail;環境可疑(不可歸因、不自動
# 派)才發一頁 decision。`AC_NO_INBOX` = 呼叫者(land.sh 全套)自己發這一則,這裡都不發。
# 寫不出來要出聲但不擋閘門 —— 同狀態檔的理由。
inbox_post() {   # $1 = rc
    [ -n "$TICKET" ] || return 0
    [ -z "${AC_NO_INBOX:-}" ] || return 0
    if [ "$ENV_SUSPECT" -ne 1 ]; then
        if [ "$1" -eq 0 ]; then verdict=gate.pass; else verdict=gate.fail; fi
        python3 "$AC/event.py" emit "$verdict" --ticket "$TICKET" \
            --kv "run_id=$RUN_ID" --kv "sha=$SHA" --kv "rc=$1" --kv "ac_sha=$AC_SHA" >/dev/null 2>&1 \
            || echo "gate: 事件發不出去($verdict,不擋閘門)" >&2
        return 0
    fi
    # 環境那一頁要能直接動手:哪個引擎、倒在哪一句、連幾條,以及**先去看哪三樣**。
    # 一句「環境可疑」不是一個可以執行的動作(DISPATCH-TEMPLATE §5.7)。
    engine=$(suspect_field engine)
    message=$(suspect_field why)
    count=$(suspect_field count)
    source=$(suspect_field source)
    state="env_suspect(gate rc=$1)"
    what="先別重跑;查 $engine 那一側的環境,修好再跑這一段"
    # `來源` 那一行不是裝飾:`statistical` 是這支程式從紅例推出來的,`declared`
    # 是案例自己說的,兩種的可信度與下一步不同(`docs/DESIGN-ENV-SUSPECT.md`)。
    note="引擎:$engine
來源:$source
同形訊息:$message
連續紅:$count
疑似原因:掛很久的 Safari --automation 行程、磁碟剩餘空間不足、測試埠被占用。"
    python3 "$AC/inbox.py" post --ticket "$TICKET" --run-id "$RUN_ID" \
        --kind decision --state "$state" --what "$what" --note "$note" \
        --where "$(python3 "$AC/status.py" rundir --ticket "$TICKET" \
                   --run-id "$RUN_ID" 2>/dev/null || echo "reports/t$TICKET/$RUN_ID")/status.json" \
        >/dev/null 2>&1 || echo "gate: 收件匣寫不出來(不擋閘門)" >&2
}

# 紅了預設自動派下一輪;`--no-auto-fix` 才關。**閘門自己的 rc 不動**。
auto_fix() {   # $1 = rc
    [ "$want_autofix" -eq 1 ] || return 0
    [ "$1" -eq 0 ] && return 0
    if [ -z "$TICKET" ]; then
        echo "gate: auto-fix 要有 --ticket <票號> —— 沒有票就沒有紅榜可以派" >&2
        return 0
    fi
    # **這一格非空 → 不自動派**(D-019,理由見 `docs/DESIGN-ENV-SUSPECT.md` §理由 5):
    # 舊版對任何 rc≠0 都派,於是「機器現在不能跑」那一輪照樣送一個 worker 進去。
    # 抓與放的代價不對稱 —— 懷疑錯了的代價是人手打一行指令,派錯了的代價是一整輪
    # worker。所以**問不出來也當成有**,而且那一句要與「真的有」分得開。
    rows=$(env_suspect_rows)
    [ "$rows" = "?" ] && echo "gate: 問不出這一輪環境可疑幾筆(狀態檔讀不到)—— 當成有" >&2
    if [ "$ENV_SUSPECT" -eq 1 ] || [ "$rows" != 0 ]; then
        echo "gate: 環境可疑,不自動派(environment_suspect $rows 筆)—— 先修環境;"\
             "真的要派就手打 sh scripts/auto-fix.sh $TICKET"
        return 0
    fi
    if [ -n "${AC_IN_AUTOFIX:-}" ]; then
        echo "gate: 已經在 auto-fix 裡面了,不再往下派(免得自己叫自己)"
        return 0
    fi
    echo "gate: auto-fix —— sh scripts/auto-fix.sh $TICKET"
    sh "$AC/auto-fix.sh" "$TICKET" \
        || echo "gate: auto-fix 停下來了(rc=$?)—— 看 reports/inbox/ 那一頁"
}

# 票上的 `gate` 欄(#56,D-H38):`--branch --ticket` 綠紅都寫進 auto-fix 綠了寫的**同一個**
# 欄、同一個形狀 {rc, sha, run_id, at},sha 是開跑時的 HEAD。閘門只有一個真相 —— 以前這條路
# 只寫狀態檔,review.sh 看票上沒有這個 sha 就說「沒跑過閘門」。寫在 auto_fix 與
# review_after_green 之前:紅了派出去的下一輪綠會寫新 sha,不能被這裡事後蓋回去;覆核要
# 讀到這一筆。寫不進去要出聲但不擋閘門 —— 同狀態檔的理由。
ticket_gate() {   # $1 = rc
    [ -n "$TICKET" ] || return 0
    [ "$want_branch" -eq 1 ] || return 0
    if [ -z "$HEAD_SHA" ]; then
        echo "gate: 問不到 HEAD —— 票的 gate 欄不寫" >&2
        return 0
    fi
    python3 "$AC/ticket.py" set "$TICKET" gate "$(python3 -c '
import datetime, json, sys
print(json.dumps({"rc": int(sys.argv[1]), "sha": sys.argv[2], "run_id": sys.argv[3],
                  "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}))
' "$1" "$HEAD_SHA" "$RUN_ID")" >/dev/null 2>&1 \
        || echo "gate: 票的 gate 欄寫不進去(不擋閘門)—— review.sh 會說沒跑過閘門" >&2
}

# 手跑 `--branch --ticket` 綠 → 票轉 InReview → complex 票派覆核(見檔頭)。**閘門自己的 rc 不動**。
review_after_green() {   # $1 = rc
    [ "$1" -eq 0 ] || return 0
    [ -n "$TICKET" ] || return 0
    [ "$want_branch" -eq 1 ] || return 0
    if [ -n "${AC_IN_AUTOFIX:-}" ]; then
        echo "gate: 在 auto-fix 裡面 —— InReview 與覆核由 auto-fix 自己做"
        return 0
    fi
    python3 "$AC/ticket.py" set "$TICKET" state InReview >/dev/null 2>&1 \
        || echo "gate: 票狀態改不動(InReview)" >&2
    # 判準是 `ticket.py` 的 `needs_review`(#136;沒標 = simple),review.sh / land / close 同一支。
    if ! python3 - "$AC" "$TICKET" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import ticket as ticket_mod
try:
    row = ticket_mod.load(sys.argv[2])
except (OSError, ValueError):
    row = {}
sys.exit(0 if ticket_mod.needs_review(row) else 1)
PY
    then
        echo "gate: 綠了 —— 票轉 InReview;票不是 complex(沒標 = simple),不派覆核,等 land"
        return 0
    fi
    echo "gate: 綠了 —— 票轉 InReview,派覆核:sh scripts/review.sh $TICKET"
    sh "$AC/review.sh" "$TICKET" --run-id "$RUN_ID" \
        || echo "gate: 覆核停下來了(rc=$?)—— 看 reports/inbox/ 那一頁"
}

# 沒有測試可跑也要留下終態。**「這一輪根本沒有東西跑」是一個結果,不是一次沒跑**
# —— 舊版這條路直接 exit,狀態檔留在 `running`(或留著上一輪的),而那與「還在跑」
# 長得一樣(2026-09-21 外部審查:status 的生命週期會誤導接手者)。
status_nothing() {   # $1 = rc  $2 = 為什麼
    [ -n "$TICKET" ] || return 0
    NOTE=$2
    python3 "$AC/status.py" start --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --base-sha "$BASE_SHA" \
        --worktree "$ROOT" --repro "sh scripts/gate.sh $ALL_ARGS" --cwd "$ROOT" \
        >/dev/null 2>&1
    python3 "$AC/status.py" done --ticket "$TICKET" --kind gate \
        --run-id "$RUN_ID" --sha "$SHA" --rc "$1" --note "$2" >/dev/null 2>&1 \
        || echo "gate: 狀態檔寫不出來(不擋閘門)" >&2
    inbox_post "$1"
}

# 硬擋命中(見 preflight 那一段):不起測試、不發 gate.start,只留終態 —— 狀態檔的 note
# 是 stderr 上那幾行 `硬擋命中 —— ` 原文(多條全列),auto-fix 靠它歸因(#90 A1/A2)。
if [ -n "$HARD_NOTE" ]; then
    status_nothing 4 "$HARD_NOTE"
    exit 4
fi

# 票的 `verify.tags` 併 `tags`。**票是唯一的工作單位**,所以要跑哪幾個回歸標籤這件事
# 住在票裡,不住在呼叫者的記憶裡。
ticket_tags() {
    python3 - "$ROOT" "$TICKET" <<'PY' 2>/dev/null
import os, sys
root, ident = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticketlib
try:
    data = ticketlib.load(ident)
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
    if [ ! -f "$AC/verify.py" ]; then
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
        echo "gate: 票 #$TICKET 的回歸 —— regression$args"
    else
        echo "gate: 回歸全部 —— regression"
    fi
    [ -z "$NO_CACHE" ] || args="$args --no-cache"
    # `AC_TICKET` + `AC_RUN_ID`(testiface 帶給命令)讓 worker、驗證者與這裡共用同一輪的
    # 回歸快取:同一份程式、同一組標籤、同一個 sha 只跑一次(D-015)。
    # shellcheck disable=SC2086
    testiface "$TEST_MODE" regression regression $args > /dev/null
    VRC=$?
    if [ -f "$TI_OUT/regression.log" ]; then cp "$TI_OUT/regression.log" "$VERIFY_LOG"; else : > "$VERIFY_LOG"; fi
    if [ -s "$TI_ERR" ]; then cat "$TI_ERR" >> "$VERIFY_LOG"; cat "$TI_ERR"; fi
    case "$VRC" in 0|1|86) REG_RESULT_ARGS="--result $TI_OUT/result.json" ;; esac
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

# 票的 `verify.files` + 「這張票到底需不需要驗證者案例」。**一趟問完**:這幾件事住在
# 同一張票裡,分三次起直譯器換不到任何新的資訊。
#
# 第一行是**為什麼不需要**(`needs-verifier-false` / `undeclared`,都不是就
# `required`)、第二行是票的 `base_sha`、其餘每行一個案例檔。讀不到票就**非零**
# (同 preflight:沒有票檔的呼叫照舊,不要憑空長出一個缺口)。
#
# **verify_plan 與 candidate_reds 走同一條路**(#40):`ticketlib.load`,票的根由
# `event.repo_root()`(`AC_ROOT` 蓋得掉)決定。以前這裡拿 `$ROOT` 拼票檔路徑,閘門在
# 票 worktree 裡跑時讀到的是 worktree 那一份舊票 —— 主 repo 上後來補的
# `needs_verifier=false` 看不到,要人手帶 `AC_TICKETS_DIR` 才對;沒帶的那一次,一張明說
# 不要案例的票在這裡被判成缺口。
verify_plan() {
    python3 - "$ROOT" "$TICKET" <<'TICKET_PY' 2>/dev/null
import os, sys
root, ident = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
import ticket as ticketlib
try:
    data = ticketlib.load(ident)
except (OSError, ValueError):
    raise SystemExit(3)
plan = data.get("verify") if isinstance(data.get("verify"), dict) else None
if data.get("needs_verifier") is False:
    print("needs-verifier-false")
elif plan is None:
    # 票上連 `verify` 這一格都沒有 = **沒有宣告**,不是「宣告了卻沒有案例」。
    # 同一支腳本對回歸層就是這樣分的(`regression()`:「沒有宣告 verify.tags」出聲
    # 不擋,「宣告了卻一個案例都選不到」才是缺口)。
    print("undeclared")
else:
    print("required")
print(str(data.get("base_sha") or ""))
plan = plan or {}
for rel in plan.get("files") or []:
    if rel:
        print(rel)
TICKET_PY
}

# `check` 剛寫回票的 `verify.baseline.candidate_run`:**候選樹上算得出來的紅有幾條**
# (紅 + import 失敗 + 缺符號 + 別處 —— `cmd_check` 判 `cand_bad` 用的就是這四格)。
# 讀不到印 `?`,不印 0:那兩件事的下一步不同(同 `env_suspect_rows`)。
#
# **用 `ticketlib.load` 而不是自己拼路徑**:`verify-case.py` 寫回票時走的是
# `event.repo_root()`(`AC_ROOT` 蓋得掉),而閘門可能跑在副本裡 —— 自己拼 `$ROOT`
# 那一份讀到的會是上一輪的舊值,而舊值與「這一輪真的沒紅」長得一樣。
candidate_reds() {
    python3 - "$ROOT" "$TICKET" <<'CAND_PY' 2>/dev/null || echo "?"
import os, sys
# `$AC` 進 path,票的根由 `event.repo_root()`(`AC_ROOT` 蓋得掉)自己決定 ——
# 與 `verify-case.py` 寫回票時走的是同一條路。
sys.path.insert(0, os.environ["AC_CONTROL_DIR"])
try:
    import ticket as ticketlib
    plan = ticketlib.load(sys.argv[2]).get("verify") or {}
    record = plan.get("baseline") or {}
    run = record.get("candidate_run") or {}
    if not run:
        raise ValueError("沒有 candidate_run")
    print(sum(len(run.get(key) or []) for key in
              ("red", "import_failures", "missing_symbol", "elsewhere")))
except Exception:                                      # noqa: BLE001
    print("?")
CAND_PY
}

# 驗證者那一層:`check`(檔頭那一節;`docs/DESIGN-VERIFY-CASES.md` §三)。
# 設定 `CRC`。**這一層與回歸層一樣不靠別人間接跑**:原始輸出各自存檔,candidate 那
# 一份還要當成 `--log` 餵進狀態檔 —— 紅榜要指得到是哪一條案例紅,而不是只剩一個
# 「verify-case 回了 1」。
verify_case() {
    CRC=0
    CASES_ARGS=""
    [ -n "$TICKET" ] || return 0
    if [ ! -f "$AC/verify-case.py" ]; then
        # 同 `regression()` 的態度:沒有這一層要**說出來**,不要讓「沒驗」穿著
        # 「驗過了」的衣服走過去(`docs/DISPATCH-TEMPLATE.md` §5.5)。
        echo "gate: 這顆 repo 沒有 scripts/verify-case.py —— 驗證者那一層沒跑(不是綠)"
        NOTE="$NOTE 沒有 scripts/verify-case.py,驗證者的案例沒跑。"
        return 0
    fi
    plan=$(verify_plan) || return 0
    why=$(echo "$plan" | sed -n 1p)
    ticket_base=$(echo "$plan" | sed -n 2p)
    files=$(echo "$plan" | sed -n '3,$p')
    # 例外只管「有沒有案例」這一格,check 不看:`verify.files` 非空的票一律照跑
    # (#27 第 3 輪:驗證者的 A2–A6 就是量這件事)。
    if [ -z "$files" ]; then
        case "$why" in
            needs-verifier-false)
                # `land.sh` 也認這一格(#8):兩支對同一張票不該給出不同的答案。
                echo "gate: 票 #$TICKET 明著寫了 needs_verifier=false —— 這一層不要求案例"
                NOTE="$NOTE 票 needs_verifier=false,這一層不要求案例。"
                return 0 ;;
            undeclared)
                # 票上連 `verify` 這一格都沒有。**出聲但不擋**,與 `regression()` 對
                # 「票沒有宣告 verify.tags」的處理同一個形狀 —— 而宣告了 `verify`
                # 卻沒有案例(下面那一段)才是缺口(#619 的精神)。
                echo "gate: 票 #$TICKET 連 verify 這一格都沒有宣告 —— 驗證者那一層這一輪沒跑"
                NOTE="$NOTE 票沒有宣告 verify 這一格,驗證者那一層沒跑。"
                return 0 ;;
        esac
        {
            echo "gate: 這張票沒有驗證者案例(#$TICKET 的 verify.files 是空的)—— 那是缺口,不是綠。"
            echo "gate:   (要嘛派驗證者補案例,要嘛票上寫 needs_verifier=false。)"
        } > "$CASES_LOG"
        cat "$CASES_LOG"
        # 缺口也是驗證者的紅(#126 A2):紅榜恰一列 `verify-case:(no-cases)`,不然 rc 3 而
        # failures[] 空,auto-fix 讀成「紅了但沒有歸因」轉 Blocked —— 而下一步明明是派驗證者。
        EXTRA_LOG_ARGS="$EXTRA_LOG_ARGS --extra-log $CASES_LOG"
        CASES_ARGS="--no-cases-log $CASES_LOG"
        NOTE="$NOTE 票宣告了 verify 卻沒有案例 = 缺口。"
        CRC=3
        return 0
    fi
    # `--ref` 是**票的 `base_sha`**,不是主線的頭:票一落地,主線上就已經有那份實作,
    # 對主線量出來的「一條都沒紅」說的是「量錯了地方」(#19)。`--candidate` 是這一輪
    # 的樹本身(含還沒 commit 的改動)—— 閘門問的就是「現在手上這一份」。
    rm -rf "$CHECK_DIR"
    ref_args=""
    [ -z "$ticket_base" ] || ref_args="--ref $ticket_base"
    echo "gate: 驗證者案例的綠 —— python3 scripts/verify-case.py check $TICKET $ref_args --candidate $ROOT"
    # shellcheck disable=SC2086
    ( cd "$ROOT" && python3 "$AC/verify-case.py" check "$TICKET" $ref_args \
        --candidate "$ROOT" --out-dir "$CHECK_DIR" ) > "$CHECK_LOG" 2>&1
    CRC=$?
    cat "$CHECK_LOG"
    EXTRA_LOG_ARGS="$EXTRA_LOG_ARGS --extra-log $CHECK_LOG"
    if [ "$CRC" -eq 0 ]; then
        return 0
    fi
    if [ ! -f "$CHECK_DIR/logs/candidate.log" ]; then
        # rc≠0 卻連 candidate 那一趟都沒跑到(量不到:ref 解不開、案例檔不在候選樹上)。
        # 那**不是**「驗證者的案例紅了」,下一步也不同 —— 兩者混成一句話的那一刻,
        # 下一輪的 worker 會去修一個沒有壞掉的東西。**rc 維持非零**:量不到不是綠。
        echo "gate: 這一趟量不到驗證者的案例(rc=$CRC)—— 看 $CHECK_LOG 的下一步"
        NOTE="$NOTE 這一趟量不到驗證者的案例(verify-case check rc=$CRC)。"
        return 0
    fi
    bad=$(candidate_reds)
    if [ "$bad" = "0" ]; then
        # `ok:false`,但**候選樹上一條都沒紅**:baseline 不成立的理由在另一邊
        # (乾淨基底上沒紅、或兩邊案例數不同)。那是**驗證者那一趟**(`stage=red`)
        # 要證的事,worker 這一輪修不動它 —— 把它算成 worker 的紅,下一輪的人會去修
        # 一個沒有壞掉的東西(D-014 §紅了誰修)。
        #
        # **這不是把它吞掉**:票上那一格仍然是 `ok:false`,下面這一行與狀態檔的 note 都寫著。
        echo "gate: 驗證者的案例在這一輪的樹上全綠,但 baseline 仍然不成立 —— 那是驗證者"
        echo "gate:   那一趟(stage=red)的事,不算這一輪的紅;票上仍是 ok:false。"
        NOTE="$NOTE 驗證者的案例候選全綠但 baseline 不成立(驗證者那一趟的事)。"
        CRC=0
        return 0
    fi
    # `ok:false` 而且候選真的紅了。紅榜住在 candidate 的原始輸出裡,**餵給狀態檔的是
    # 它**,不是一句「verify-case 回了 1」—— 下一輪的 worker 讀的是 `failures[]`,而
    # 一個沒有案例名字的紅榜與沒有紅榜一樣(D-014)。
    CHECK_LOG_ARGS="--red-log $CHECK_DIR/logs/candidate.log"
    echo "gate: 驗證者的案例在這一輪的樹上紅了($bad 條)—— 紅榜進狀態檔的 failures[]"
    NOTE="$NOTE 驗證者的案例這一輪沒綠(verify-case check rc=$CRC,候選 $bad 條)。"
    return 0
}

merge_rc() {   # $1 = 目前 rc  $2 = 另一個 rc;印出合併後的
    if [ "$1" -ne 0 ]; then echo "$1"; else echo "$2"; fi
}

# 紅例連續單跑與原順序整組都達設定門檻才自動放行。紅例 = 這一趟 result.json 的 fail / error 列
# (id 照原樣交回 `run <id>`,D-071 §4);每次單跑進 `$RUN_OUT/rerun-<k>/`,整組重跑是
# `run <原跑的全部 ids>`(`full` 那一趟沒有給 ids,用它 result 裡的每一條)進 `rerun-group-<g>/`。
# 只有可歸因的紅(rc 1)才重跑:2 是量不到、86 是環境,重跑只會把浪費加倍。
flake_rerun() {   # $1 = rc  $2 = run|full(原跑的那一個)  其餘 = 原跑給的 ids
    fr_rc=$1
    fr_cmd=$2
    shift 2
    [ "$fr_rc" -eq 0 ] && return 0
    [ "$fr_rc" -eq 1 ] || return "$fr_rc"
    [ -n "$TICKET" ] || return "$fr_rc"
    [ -z "${AC_NO_FLAKE_RERUN:-}" ] || return "$fr_rc"
    first_out=$RUN_OUT
    cases=$(python3 "$AC/status.py" failures --result "$first_out/result.json" 2>/dev/null || true)
    [ -n "$cases" ] || return "$fr_rc"
    if [ "$fr_cmd" = full ]; then
        group_ids=$(python3 - "$first_out/result.json" <<'IDS_PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(" ".join(row["id"] for row in json.load(handle)["ids"]))
IDS_PY
)
    else
        group_ids="$*"
    fi
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
    rerun=0
    total=0
    qualified=0
    for case in $cases; do
        total=$((total + 1))
        passed=0
        attempt=1
        while [ "$attempt" -le "$single_runs" ]; do
            rerun=$((rerun + 1))
            if testiface rerun "$fr_cmd/rerun-$rerun" run "$case" >/dev/null; then
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
    [ "$qualified" -gt 0 ] || return "$fr_rc"
    NOTE="$NOTE 單跑門檻達標 $qualified/$total 條。"
    [ "$qualified" -eq "$total" ] || return "$fr_rc"
    if [ -n "${AC_FLAKE_RERUN_GROUP:-}" ] && [ "${AC_FLAKE_RERUN_GROUP:-}" = "0" ]; then
        echo "gate: AC_FLAKE_RERUN_GROUP=0 —— 不做整組重跑;rc 維持 $fr_rc"
        return "$fr_rc"
    fi
    echo "gate: 用原順序整組重跑 $group_runs 次 —— 順序污染會在這裡再紅"
    group=1
    while [ "$group" -le "$group_runs" ]; do
        # shellcheck disable=SC2086
        testiface rerun "$fr_cmd/rerun-group-$group" run $group_ids >/dev/null
        grc=$?
        run_log=$TI_OUT/run.log
        [ -f "$run_log" ] || run_log=$TI_ERR
        EXTRA_LOG_ARGS="$EXTRA_LOG_ARGS --extra-log $run_log"
        grep -aE "^Ran |^OK|^FAILED" "$run_log" || true
        if [ "$grc" -ne 0 ]; then
            for case in $cases; do ORDER_ARGS="$ORDER_ARGS --order-dependent $case"; done
            echo "gate: 原順序整組第 $group 次仍然紅 —— order_dependent(log: $run_log)"
            NOTE="$NOTE 原順序整組重跑仍紅 = order_dependent。"
            return "$fr_rc"
        fi
        group=$((group + 1))
    done
    for case in $cases; do AUTO_FLAKY_ARGS="$AUTO_FLAKY_ARGS --auto-flaky $case"; done
    echo "gate: 單跑與原順序整組都達門檻 —— flaky=auto,rc 轉 0"
    NOTE="$NOTE 單跑與整組都達門檻,flaky=auto。"
    python3 "$AC/event.py" emit flake.auto_pass --ticket "$TICKET" \
        --kv "run_id=$RUN_ID" --kv "single_runs=$single_runs" \
        --kv "group_runs=$group_runs" --note "$cases" >/dev/null \
        || echo "gate: flake.auto_pass 事件發不出去(不擋閘門)" >&2
    return 0
}

# 這一輪每一步的 `AC_TEST_OUT` 住哪(D-071 §4):有票 = 狀態檔那一輪的目錄
# (`reports/t<票號>/<run_id>/<步驟>/`,證據跟著狀態檔留著);沒票 = `<log>.out/<步驟>/`
# —— 沒給票號的呼叫照舊**不寫 reports/**,那一份與 `$LOG` 一樣每趟覆寫。
OUT=""
if [ -n "$TICKET" ]; then
    OUT=$(python3 "$AC/status.py" rundir --ticket "$TICKET" --run-id "$RUN_ID" 2>/dev/null) || OUT=""
fi
if [ -z "$OUT" ]; then
    OUT=$LOG.out
    rm -rf "$OUT"
fi
mkdir -p "$OUT"
# `--suspect-file` 那一份**空值只有 `[]`**(`docs/DESIGN-ENV-SUSPECT.md`):上一趟留下的 rows
# 不准被這一趟讀成自己的。
echo "[]" > "$SUSPECT_FILE"
if [ "$want_full" -eq 1 ]; then TEST_MODE=full
elif [ "$want_base" -eq 1 ] && [ "$want_branch" -eq 0 ] && [ -z "$files" ]; then TEST_MODE=base
else TEST_MODE=branch; fi

# preflight(專案說「這台機器現在能不能跑」):86 → 環境可疑那一條路(狀態檔 env_suspect、
# 不做 flake、不自動派、一頁 decision),測試一條都不跑;其他非零 → 2,preflight 本身壞了。
testiface "$TEST_MODE" preflight preflight
prc=$?
if [ "$prc" -eq 86 ]; then
    [ ! -s "$TI_ERR" ] || cat "$TI_ERR"
    status_start
    env_suspect_note preflight
    status_done 86
    ticket_gate 86
    auto_fix 86
    exit 86
fi
if [ "$prc" -ne 0 ]; then
    [ ! -s "$TI_ERR" ] || cat "$TI_ERR"
    echo "gate: preflight itself failed(testiface rc=$prc)—— 測試一條都沒跑"
    status_nothing 2 "preflight 本身失敗(testiface rc=$prc),測試沒跑。"
    ticket_gate 2
    exit 2
fi

if [ "$want_full" -eq 1 ]; then
    echo "gate: 全套 —— full"
    status_start
    # 判綠先寫檔再讀退出碼,不用 `cmd | tail`:管線的退出碼是右邊那一支的
    # (`false | tail` 是 0),而那會讓「根本沒跑起來」靜靜判成綠。
    run_tests full full
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    # 先環境 fail-fast、再 flake 判定(見檔頭)。
    if [ "$rc" -eq 86 ]; then
        env_suspect_note run
    else
        flake_rerun "$rc" full
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

# `select`:改動檔一行一個餵進去,印回來的每一行是一個 id 或 `unmapped <檔>`(§4)。
# rc 3 = 沒有東西可選(專案自己說為什麼,原話照印)、量不到 = 2;兩種都留終態再退。
select_ids() {   # $1 = branch|base  $2 = 步驟目錄;stdin = 改動檔
    testiface "$TEST_MODE" "$2" select --mode "$1" > "$OUT/$2.ids"
    src=$?
    if [ "$src" -ne 0 ]; then
        [ ! -s "$TI_ERR" ] || cat "$TI_ERR"
        why=$(head -n 1 "$TI_ERR" 2>/dev/null)
        if [ "$src" -eq 3 ]; then
            echo "gate: select --mode $1 沒有可選的(rc 3)—— 上面是專案的原話"
            status_nothing 3 "select --mode $1 沒有可選的(rc 3):$why"
            ticket_gate 3
            exit 3
        fi
        echo "gate: select --mode $1 量不到(testiface rc=$src)—— 測試沒跑"
        status_nothing 2 "select --mode $1 量不到(testiface rc=$src):$why"
        ticket_gate 2
        exit 2
    fi
    while IFS= read -r line; do
        case "$line" in
            "") ;;
            "unmapped "*) unmapped="$unmapped ${line#unmapped }" ;;
            *) add "$line" ;;
        esac
    done < "$OUT/$2.ids"
}

changed=""
for f in $files; do changed="$changed $f"; done
if [ "$want_branch" -eq 1 ]; then
    diff_tests=""
    for f in $(git -C "$ROOT" diff --name-only "$MAIN...HEAD"; \
               git -C "$ROOT" diff --name-only HEAD; \
               git -C "$ROOT" ls-files --others --exclude-standard); do
        case " $changed " in *" $f "*) ;; *) changed="$changed $f" ;; esac
        case "$f" in tests/test_*.py) [ -f "$ROOT/$f" ] && diff_tests="$diff_tests $(basename "$f" .py)" ;; esac
    done
fi
if [ -n "$changed" ]; then
    # shellcheck disable=SC2086
    printf '%s\n' $changed > "$OUT/changed-files.txt"
    select_ids branch select-branch < "$OUT/changed-files.txt"
fi
if [ "$want_branch" -eq 1 ]; then
    # 對照表沒列的檔(#45 B2):同一份 diff 改到的 tests/test_*.py 就是它的守衛 ——
    # 出聲、照那幾支跑、rc 照測試結果。推不到(diff 沒有 tests)才走下面退 3 那一段。
    if [ -n "$unmapped" ] && [ -n "$diff_tests" ]; then
        diff_tests=$(echo "$diff_tests" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ *$//')
        for f in $unmapped; do
            echo "gate: 對照表沒列 ${f},依同票 tests 改動推到 ${diff_tests}(請補 scripts/gate.sh 對照表)"
            NOTE="$NOTE 推得的對照:${f}→${diff_tests}。"
        done
        # shellcheck disable=SC2086
        add $diff_tests
        unmapped=""
    fi
fi
[ "$want_base" -eq 1 ] && select_ids base select-base < /dev/null

mods=$(echo "$mods" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ')
rc=0
if [ -n "$mods" ]; then
    echo "gate: run$(echo " $mods" | sed 's/ *$//')"
    status_start
    # shellcheck disable=SC2086
    run_tests "$TEST_MODE" run $mods
    rc=$?
    grep -aE "^Ran |^OK|^FAILED" "$LOG" || echo "gate: log 裡連 Ran 都沒有,看 $LOG"
    # 先環境 fail-fast、再 flake 判定(見檔頭)。
    if [ "$rc" -eq 86 ]; then
        env_suspect_note run
    else
        # shellcheck disable=SC2086
        flake_rerun "$rc" run $mods
        rc=$?
    fi
    if [ -n "$TICKET" ] && [ "$ENV_SUSPECT" -eq 0 ]; then
        regression ticket
        rc=$(merge_rc "$rc" "$VRC")
        # **回歸層之後**才是驗證者那一層(D-020 C5):兩層問的不是同一件事,而
        # 「這一輪的樹讓驗證者的第 k 條紅」要與回歸的紅分得開。
        verify_case
        rc=$(merge_rc "$rc" "$CRC")
    fi
    [ "$rc" -eq 0 ] || echo "gate: 紅了,看 $LOG"
    status_done "$rc"
    # 有檔對不到模組時這一輪下面退 3 —— 票上記的是閘門真正的退出碼。
    if [ -n "$unmapped" ]; then ticket_gate 3; else ticket_gate "$rc"; fi
    auto_fix "$rc"
fi

if [ -n "$unmapped" ]; then
    # 對不到模組也要留下狀態檔:**「沒有人守著這幾個檔」是一個結果,不是一次沒跑**。
    if [ -n "$mods" ]; then
        NOTE="$NOTE 有檔對不到任何測試模組。"
        status_done 3
    else
        status_nothing 3 "這幾個改動檔對不到任何測試模組:$unmapped"
        ticket_gate 3
    fi
    echo "gate: 這幾個改動檔對不到任何測試模組 —— 沒有人守著它們:"
    for f in $unmapped; do echo "gate:   $f"; done
    echo "gate: 要嘛補 scripts/gate.sh 的對照表,要嘛在票裡寫明為什麼它們不需要測試。"
    exit 3
fi
if [ -z "$mods" ]; then
    status_nothing 2 "沒有任何要跑的東西(--branch 沒有改動檔?)"
    ticket_gate 2
    echo "gate: 沒有給我任何要跑的東西(--branch 沒有改動檔?要跑基礎組加 --base)"
    exit 2
fi
review_after_green "$rc"
exit $rc
