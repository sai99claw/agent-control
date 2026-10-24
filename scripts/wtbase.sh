# 主 repo 根與副本根**只有一種算法、一個來源**(#46,D-018)—— 由 `bin/actl` 與每一支要找根的
# scripts/*.sh 以 `. "$AC/wtbase.sh"` 載入,不單獨執行。
#
# 以前四支各算各的(三份 `--git-common-dir` 內聯,apply.sh 還用 `$ROOT` 拼):在票分支的
# worktree 裡 `$ROOT` 是 worktree 自己,副本就開到 `x-wt/t1-wt/` 底下;而 land.sh 唸殘留
# 清單的地方與 auto-fix 開副本的地方不是同一個目錄時,「沒有殘留」與「看錯地方」長得一樣。
#
# `main_root` / `wtbase` 要呼叫端先有 `$ROOT` 與 `cfg`(讀 `$ROOT/board/config.json`);
# `ac_*` 那幾個不要。
#
# ## 專案根不從 `$0` 來(D-071 C2,#159)
# 腳本住在 A 的 clone(`AC_HOME`),專案在別處:以前每支各一份、從 `$0` 往上找
# `board/config.json`,在 A 的 clone 裡跑永遠找到 A 自己那一份 —— 票與事件寫進 A,而且不報錯。
# 現在只有兩條:`AC_ROOT`(`actl` 替每個子行程設好),否則 cwd 所在 git 樹的主 repo 根
# (`--git-common-dir` 的上一層,所以票的 worktree 也指回主 repo);都沒有就一行 stderr、rc 2。
# `event.repo_root()` 是同一條規則的 Python 那一份。`-C` 與 `CLAUDE_PROJECT_DIR` 是 `actl`
# 的事(session-hook.sh 另認後者)。
AC_NOT_IN_PROJECT="not inside a project; use actl -C <dir> or set AC_ROOT"

# 一個目錄所在 git 樹的主 repo 根(實體路徑);不在 git 裡 rc 1、不印。
ac_main_root_of() {   # $1 = 目錄
    _amr=$(git -C "$1" rev-parse --git-common-dir 2>/dev/null) || return 1
    [ -n "$_amr" ] || return 1
    case "$_amr" in /*) ;; *) _amr=$1/$_amr ;; esac
    (cd "$(dirname "$_amr")" 2>/dev/null && pwd -P)
}

# 專案根(票根):`AC_ROOT` > cwd 的主 repo 根 > stderr 一行、rc 2。$1 = 訊息前綴(腳本名)。
ac_root() {
    if [ -n "${AC_ROOT:-}" ]; then echo "$AC_ROOT"; return 0; fi
    ac_main_root_of . && return 0
    echo "${1:-actl}: $AC_NOT_IN_PROJECT" >&2
    return 2
}

# 程式碼根(受測的那棵樹):`AC_CODE_ROOT` > cwd 的 `--show-toplevel`;都沒有 rc 1、不印。
ac_code_root() {
    if [ -n "${AC_CODE_ROOT:-}" ]; then echo "$AC_CODE_ROOT"; return 0; fi
    git rev-parse --show-toplevel 2>/dev/null
}

# A 的 clone:`AC_HOME` > 控制腳本目錄(`$AC`)的上一層。
ac_home() {
    if [ -n "${AC_HOME:-}" ]; then echo "$AC_HOME"; return 0; fi
    (cd "$AC/.." && pwd -P)
}

# 哪一版 A 產出這筆事件(D-071 §1):`AC_HOME` 的 HEAD;它不是一棵 git 樹的頂層(複本、
# 封存展開、別的 repo 的子目錄)就是字面 `unknown` —— 永不空、永不缺。
ac_sha() {   # $1 = AC_HOME
    _ash_top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || { echo unknown; return 0; }
    _ash_home=$(cd "$1" 2>/dev/null && pwd -P) || { echo unknown; return 0; }
    [ "$(cd "$_ash_top" && pwd -P)" = "$_ash_home" ] || { echo unknown; return 0; }
    git -C "$1" rev-parse HEAD 2>/dev/null || echo unknown
}
#
# 檔尾另放 code-map 索引重產的唯一實作(#129):apply.sh 與 land.sh 本來就載入這一支,
# 放這裡不必多一支要同步、要登記進閘門對照表的檔。

# 主 repo 根:`git rev-parse --git-common-dir` 的上一層 —— 在票分支的 worktree 裡它也指回
# 主 repo;不在 git 裡才退回 `$ROOT`。
main_root() {
    ac_main_root_of "$ROOT" || echo "$ROOT"
}

# 跑哪一份閘門(D-071 C3,#159):專案**就是** A 自己(主 repo 根與 `AC_HOME` 實體路徑相同)時
# 跑票 worktree 裡那一份 —— A 的票改的正是閘門;其他專案一律跑 `AC_HOME` 那一份(它們的樹裡
# 沒有閘門)。land.sh 的全套與 auto-fix 的預設重跑同一條。要 `$ROOT`(主 repo 根)。
ac_gate_script() {   # $1 = 票 worktree
    _ags_home=$(cd "$(ac_home)" 2>/dev/null && pwd -P)
    _ags_main=$(cd "$(main_root)" 2>/dev/null && pwd -P)
    if [ -n "$_ags_home" ] && [ "$_ags_main" = "$_ags_home" ]; then
        echo "$1/scripts/gate.sh"
    else
        echo "$(ac_home)/scripts/gate.sh"
    fi
}

# 副本根:`AC_WORKTREE_DIR` > config `worktree_dir` > `<主 repo 根>/../<主 repo 名>-wt`;
# 相對路徑一律以主 repo 根拼。
wtbase() {
    _mr=$(main_root)
    _wb=${AC_WORKTREE_DIR:-$(cfg worktree_dir "")}
    case "$_wb" in
        "") echo "$_mr/../$(basename "$_mr")-wt" ;;
        /*) echo "$_wb" ;;
        *)  echo "$_mr/$_wb" ;;
    esac
}

# code-map 索引重產(#96、#129,D-018):apply 單票、`apply --batch` 與 land 合併後三處
# **同一個判準、同一份動作**,不各寫一份。判準同 gate.sh 的新鮮度格:樹裡有
# `scripts/repo-map.py` 與 `code-map/catalog.json`。只跑 `repo-map.py build`,不 stage、不 commit
# —— 收不收、怎麼收是呼叫端的事。`-B`:呼叫端緊接著要看工作樹,不留位元組碼。
# 退出碼:0 產出物變了;1 沒變;2 build 非零(它的 stderr 照印);3 樹裡沒有地圖。
code_map_present() {   # $1 = 樹根
    [ -f "$1/scripts/repo-map.py" ] && [ -f "$1/code-map/catalog.json" ]
}

code_map_regen() {   # $1 = 樹根
    code_map_present "$1" || return 3
    _cm_before=$(cat "$1/code-map/index.json" "$1/code-map/INDEX.md" 2>/dev/null | cksum)
    python3 -B "$1/scripts/repo-map.py" --root "$1" build >/dev/null || return 2
    [ "$(cat "$1/code-map/index.json" "$1/code-map/INDEX.md" 2>/dev/null | cksum)" != "$_cm_before" ] \
        || return 1
    return 0
}
