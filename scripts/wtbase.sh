# 主 repo 根與副本根**只有一種算法、一個來源**(#46,D-018)—— 由
# auto-fix.sh / land.sh / apply.sh / review.sh 以 `. "$AC/wtbase.sh"` 載入,不單獨執行。
#
# 以前四支各算各的(三份 `--git-common-dir` 內聯,apply.sh 還用 `$ROOT` 拼):在票分支的
# worktree 裡 `$ROOT` 是 worktree 自己,副本就開到 `x-wt/t1-wt/` 底下;而 land.sh 唸殘留
# 清單的地方與 auto-fix 開副本的地方不是同一個目錄時,「沒有殘留」與「看錯地方」長得一樣。
#
# 呼叫端要先有 `$ROOT` 與 `cfg`(讀 `$ROOT/board/config.json`)。
#
# 檔尾另放 code-map 索引重產的唯一實作(#129):apply.sh 與 land.sh 本來就載入這一支,
# 放這裡不必多一支要同步、要登記進閘門對照表的檔。

# 主 repo 根:`git rev-parse --git-common-dir` 的上一層 —— 在票分支的 worktree 裡它也指回
# 主 repo;不在 git 裡才退回 `$ROOT`。
main_root() {
    _common=$(git -C "$ROOT" rev-parse --git-common-dir 2>/dev/null || echo "")
    case "$_common" in
        "") echo "$ROOT" ;;
        /*) (cd "$(dirname "$_common")" 2>/dev/null && pwd) || echo "$ROOT" ;;
        *)  (cd "$ROOT/$(dirname "$_common")" 2>/dev/null && pwd) || echo "$ROOT" ;;
    esac
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
