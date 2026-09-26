# 主 repo 根與副本根**只有一種算法、一個來源**(#46,D-018)—— 由
# auto-fix.sh / land.sh / apply.sh / review.sh 以 `. "$AC/wtbase.sh"` 載入,不單獨執行。
#
# 以前四支各算各的(三份 `--git-common-dir` 內聯,apply.sh 還用 `$ROOT` 拼):在票分支的
# worktree 裡 `$ROOT` 是 worktree 自己,副本就開到 `x-wt/t1-wt/` 底下;而 land.sh 唸殘留
# 清單的地方與 auto-fix 開副本的地方不是同一個目錄時,「沒有殘留」與「看錯地方」長得一樣。
#
# 呼叫端要先有 `$ROOT` 與 `cfg`(讀 `$ROOT/board/config.json`)。

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
