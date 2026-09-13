#!/bin/sh
# 把 agent-control(主)裡的角色卡與模型記憶複製進一個專案(從)。
#   sh scripts/sync-to-project.sh <專案根目錄>
# 方向只有一個:agent-control → 專案。專案裡那份是產出物,檔頭標明「請到 agent-control 改」。
# 為什麼要有這支:規範若在兩個 repo 各改各的,三天內就會分岔(2026-09-13 tabby_pool 實測);
# 一份乾淨的 repo 當主,專案只拿複本,改了再同步,分岔就沒地方長。
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:?用法: sync-to-project.sh <專案根目錄>}
[ -d "$DEST" ] || { echo "sync: 找不到專案目錄 $DEST" >&2; exit 2; }
SRC_SHA=$(cd "$HERE" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
copy_dir() {  # $1 = 來源目錄  $2 = 目的目錄
  mkdir -p "$2"
  for f in "$1"/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    {
      printf '<!-- 由 agent-control %s 的 %s 同步產生;請到 agent-control 改,不要改這一份 -->\n' "$SRC_SHA" "${f#$HERE/}"
      cat "$f"
    } > "$2/$name"
    echo "sync: $2/$name"
  done
}
copy_dir "$HERE/memory/role"  "$DEST/docs/roles"
copy_dir "$HERE/memory/model" "$DEST/docs/roles/model"
echo "sync: 完成(來源 $SRC_SHA)。專案端用 docs lane 落地:sh scripts/land-ticket.sh docs \"sync agent-control $SRC_SHA\" docs/roles/*.md docs/roles/model/*.md"
