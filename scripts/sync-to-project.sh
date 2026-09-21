#!/bin/sh
# 把 agent-control(主)裡的角色卡與模型記憶複製進一個專案(從)。
#   sh scripts/sync-to-project.sh <專案根目錄> [--dry-run]
# 方向只有一個:agent-control → 專案。專案裡那份是產出物,檔頭標明「請到 agent-control 改」。
# 為什麼要有這支:規範若在兩個 repo 各改各的,三天內就會分岔(2026-09-13 第一個專案實測);
# 一份乾淨的 repo 當主,專案只拿複本,改了再同步,分岔就沒地方長。
#
# ## manifest:**退場的角色卡要真的消失**(2026-09-21 外部審查)
# 舊版只覆寫現有的檔,不刪 —— 一個 2026-09-21 收掉的角色(調度員)會**留在專案裡**,
# 而留下來的那一份讀起來與還在用的角色卡一模一樣。所以這裡留一份 manifest:上一次
# 同步過哪幾個檔;這一次不在名單上的,刪掉並唸出來。
#
# ## 專案端能力檢查:**不要叫人去跑一個不存在的入口**
# 舊版最後一行要求專案跑 `land-ticket.sh docs …`,而本 repo 從來沒有提供那一支 ——
# 一句指不到東西的下一步,比沒有下一步更糟:它讓人以為自己漏裝了什麼。
# 這裡先看專案有什麼,再說得出對它成立的那一句。
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:?用法: sync-to-project.sh <專案根目錄> [--dry-run]}
DRY=${2:-}
[ -d "$DEST" ] || { echo "sync: 找不到專案目錄 $DEST" >&2; exit 2; }
SRC_SHA=$(cd "$HERE" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
ROLES=$DEST/docs/roles
MANIFEST=$ROLES/.sync-manifest
NEW_LIST=""

copy_dir() {  # $1 = 來源目錄  $2 = 目的目錄  $3 = manifest 前綴
  mkdir -p "$2"
  for f in "$1"/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    NEW_LIST="$NEW_LIST$3$name
"
    [ "$DRY" = "--dry-run" ] && { echo "sync: (dry-run) $2/$name"; continue; }
    {
      printf '<!-- 由 agent-control %s 的 %s 同步產生;請到 agent-control 改,不要改這一份 -->\n' "$SRC_SHA" "${f#$HERE/}"
      cat "$f"
    } > "$2/$name"
    echo "sync: $2/$name"
  done
}
copy_dir "$HERE/memory/role"  "$ROLES"       ""
copy_dir "$HERE/memory/model" "$ROLES/model" "model/"

# 退場的角色卡。**先唸出來再刪** —— 一次靜悄悄的刪除與一次沒發生的刪除長得一樣。
if [ -f "$MANIFEST" ]; then
  while IFS= read -r old; do
    [ -n "$old" ] || continue
    case "$NEW_LIST" in
      *"$old"*) ;;
      *)
        echo "sync: 退場 $ROLES/$old(agent-control 已經沒有這一份)"
        [ "$DRY" = "--dry-run" ] || rm -f "$ROLES/$old"
        ;;
    esac
  done < "$MANIFEST"
fi
[ "$DRY" = "--dry-run" ] || printf '%s' "$NEW_LIST" > "$MANIFEST"

# 專案端有什麼:說得出對**這個**專案成立的下一句。
echo "sync: 完成(來源 $SRC_SHA)。"
if [ -x "$DEST/scripts/land-ticket.sh" ] || [ -f "$DEST/scripts/land-ticket.sh" ]; then
  echo "sync: 專案端落地:sh scripts/land-ticket.sh docs \"sync agent-control $SRC_SHA\" docs/roles/*.md docs/roles/model/*.md"
elif [ -f "$DEST/scripts/land.sh" ]; then
  echo "sync: 專案端落地:開一張 docs 票、一條 t<票號>-sync-roles 分支,commit 後 sh scripts/land.sh t<票號>-sync-roles"
else
  echo "sync: 這個專案沒有 scripts/land.sh 也沒有 scripts/land-ticket.sh —— **它還沒有 docs 通道**。"
  echo "sync:   同步出來的檔現在只是工作樹裡的改動;要進它的主線,專案得先有一條落地入口。"
fi
