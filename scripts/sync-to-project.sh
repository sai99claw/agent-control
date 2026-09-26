#!/bin/sh
# 把 agent-control(主)裡的角色卡、模型記憶與**控制腳本**複製進一個專案(從)。
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
# ## 腳本也同步(2026-09-21,D-015):`<專案>/scripts/control/`
# 遷移計畫 §0 的第 3 條要專案的落地腳本改成呼叫這一套,而「呼叫這一套」以前沒有東西
# 可以呼叫 —— 專案端根本沒有這幾支。所以這裡把它們同步過去,放在 `scripts/control/`
# (與專案自己的 `scripts/` 分開:**看得出哪幾支是產出物**,改錯地方的人當場知道)。
#
# 名單裡有 `event.py` / `ticket.py`,不是因為專案要直接叫它們,是因為另外那幾支
# `import` 它們 —— 少了它們,同步過去的是一組 import 就炸的檔。`verify.py` 也被 import,
# 但它**同時是入口**:專案的閘門要跑它(#35),所以它跟入口一起查接線。
#
# ## 暫存區不同步、`<專案>/memory/` 一個位元組都不碰(2026-09-23,D-021)
# `memory/` 永遠是「這個 repo 自己寫的」;`roles_dir` 是「規矩從哪來」。所以 A 的
# `*.inbox.md` 是 **A 自己的暫存區,不是規矩** —— 同步過去的話,專案會拿到一份檔頭寫著
# 「不要改這一份」、內容是別人的專案事實、而且沒有任何讀者的檔(2026-09-23 實測 T 就躺著
# 兩份)。專案自己的備忘住 `<專案>/memory/{role,model,project}/`,規則包(`rules.py pack`)
# 會疊上去讀;這一支**不讀也不寫那個目錄**,測試逐位元組釘住這件事。
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
if [ "$DRY" = "--dry-run" ]; then
  python3 - "$DEST/board/config.json" <<'PY'
import json
import os
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
except (OSError, ValueError):
    config = {}
rules = config.get("rules") or {}
applies = (config.get("memory") or {}).get("applies_to")
checks = (
    ("rules.roles_dir", rules.get("roles_dir") == "docs/roles"),
    ("rules.models_dir", rules.get("models_dir") == "docs/roles/model"),
    ("memory.applies_to", isinstance(applies, list)),
)
missing = [name for name, present in checks if not present]
if missing:
    sys.stderr.write("sync: 專案 board/config.json 缺少必要設定:\n")
    for name in missing:
        sys.stderr.write("  %s\n" % name)
    sys.exit(2)
# `memory.applies_to` 指到 `roles_dir` 底下 = 專案在量一份**自己改不了的檔**:那一層是
# 同步產出物,下一次 sync 就蓋掉,而超標開出來的整理票沒有人能執行(D-021)。上限與整理
# 票管的是專案自己寫的 `memory/`。
where = [rules.get("roles_dir"), rules.get("models_dir")]
inside = [one for one in applies
          if any(w and os.path.normpath(one).startswith(os.path.normpath(w) + os.sep)
                 for w in where)]
if inside:
    sys.stderr.write("sync: 專案 board/config.json 的 memory.applies_to 指到 "
                     "rules.roles_dir 底下:%s\n" % "、".join(inside))
    sys.stderr.write("sync:   那一層是同步產出物,專案改不了也留不住 —— 改成 "
                     "memory/role/*.md、memory/model/*.md(專案自己寫的那一層)。\n")
    sys.exit(2)
PY
fi
SRC_SHA=$(cd "$HERE" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
# 暫存區的副檔名讀 **A 自己**的設定(`memory.inbox_suffix`),與 `memory.py` 同一格:
# 寫死第二份的那一天,兩份會各自往不同方向漂,而漂開的那一份看起來仍然像規格。
INBOX_SUFFIX=$(python3 - "$HERE/board/config.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        conf = json.load(handle)
except (OSError, ValueError):
    conf = {}
memory = conf.get("memory") if isinstance(conf.get("memory"), dict) else {}
sys.stdout.write(str(memory.get("inbox_suffix") or ".inbox.md"))
PY
)
ROLES=$DEST/docs/roles
MANIFEST=$ROLES/.sync-manifest
NEW_LIST=""
CTRL=$DEST/scripts/control
CTRL_MANIFEST=$CTRL/.sync-manifest
NEW_SCRIPTS=""
# 專案端要用到的那幾支 + 它們 import 的。順序無所謂,名單本身要進 code review。
SCRIPT_LIST="status.py verify-case.py apply.sh auto-fix.sh review.sh inbox.py rules.py memory.py event.py ticket.py verify.py new-session.sh heartbeat.sh session-hook.sh"
# 這兩支**不是入口**,是被上面那幾支 `import` 的(見檔頭)。查「接了沒」時要把它們挑掉
# —— 對一支本來就沒有人直接叫的檔說「沒有呼叫點」,是一句假話,而假警報會讓真的那幾條
# 被一起跳過(`docs/DISPATCH-TEMPLATE.md` §5.7)。
# `verify.py` **不在這裡**(#35):專案的閘門直接跑它,而下游專案的閘門跑的是它自己那支
# `scripts/verify.py`(與 control 那份內容不同)—— 把它當成「只被 import」,就看不見
# 那個分岔,既有測試還把「不准點名它」釘死了。
# `heartbeat.sh` 由 `new-session.sh` 叫、`new-session.sh` 由 `session-hook.sh` 叫(#39):
# 專案要接的只有 hook 那一支(接在 `.claude/settings.json`),另外兩支是它帶進來的。
DEPENDENCY_ONLY="event.py ticket.py heartbeat.sh new-session.sh"

copy_dir() {  # $1 = 來源目錄  $2 = 目的目錄  $3 = manifest 前綴
  mkdir -p "$2"
  for f in "$1"/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    # 暫存區留在 A:它是 A 自己還沒併進主檔的筆記,不是規矩(D-021)。**不進 NEW_LIST**,
    # 所以上一次同步過去的那幾份會走底下的「退場」被刪掉並唸出來。
    case "$name" in
      *"$INBOX_SUFFIX")
        echo "sync: 不同步 $name(agent-control 自己的暫存區,不是規矩)"
        continue
        ;;
    esac
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

# 腳本:**不加檔頭**(會踩到 shebang),改在目錄裡留一份 README 說它是產出物。
copy_scripts() {
  mkdir -p "$CTRL"
  for name in $SCRIPT_LIST; do
    src=$HERE/scripts/$name
    if [ ! -f "$src" ]; then
      echo "sync: 主 repo 沒有 scripts/$name —— 名單與事實分岔了(先修名單)" >&2
      continue
    fi
    NEW_SCRIPTS="$NEW_SCRIPTS$name
"
    if [ "$DRY" = "--dry-run" ]; then
      echo "sync: (dry-run) $CTRL/$name"
      continue
    fi
    cp "$src" "$CTRL/$name"
    chmod 755 "$CTRL/$name"
    echo "sync: $CTRL/$name"
  done
  [ "$DRY" = "--dry-run" ] && return 0
  cat > "$CTRL/README.md" <<'EOF'
# scripts/control/ —— 產出物,不要在這裡改

這幾支是 `sync-to-project.sh` 從 agent-control 同步過來的。改了會在下一次同步被蓋掉;
要改就到 agent-control 改,再同步一次。

它們找 repo 根的方式是**往上找 `board/config.json`**,所以這個專案要有一份
`board/config.json`(票目錄、事件檔、reports 目錄、`flaky_threshold`、`worker.command`)。
指定別的根:`AC_ROOT=<專案根> python3 scripts/control/status.py …`。
EOF
}
copy_scripts

# 範本:D-020(2026-09-23)起 verify/_template_ticket.py 是 A 正本,同步到 T 同路徑
# ——**不加檔頭**(檔頭會被 verify.py 的 lint / py_compile 當成第一行 docstring 的一部分)。
copy_template() {
  src=$HERE/verify/_template_ticket.py
  if [ ! -f "$src" ]; then
    echo "sync: 主 repo 沒有 verify/_template_ticket.py —— 名單與事實分岔了(先修這支腳本)" >&2
    return 0
  fi
  if [ "$DRY" = "--dry-run" ]; then
    echo "sync: (dry-run) $DEST/verify/_template_ticket.py"
    return 0
  fi
  mkdir -p "$DEST/verify"
  cp "$src" "$DEST/verify/_template_ticket.py"
  echo "sync: $DEST/verify/_template_ticket.py"
}
copy_template

# 退場的角色卡。**先唸出來再刪** —— 一次靜悄悄的刪除與一次沒發生的刪除長得一樣。
if [ -f "$MANIFEST" ]; then
  while IFS= read -r old; do
    [ -n "$old" ] || continue
    case "$NEW_LIST" in
      *"$old"*) ;;
      *)
        case "$old" in
          *"$INBOX_SUFFIX")
            # 說得出**對的理由**:A 還有這一份,是「暫存區不再同步」,不是「已經沒有」。
            echo "sync: 退場 $ROLES/$old(暫存區不再同步;專案自己的備忘寫 memory/)"
            ;;
          *) echo "sync: 退場 $ROLES/$old(agent-control 已經沒有這一份)" ;;
        esac
        [ "$DRY" = "--dry-run" ] || rm -f "$ROLES/$old"
        ;;
    esac
  done < "$MANIFEST"
fi
[ "$DRY" = "--dry-run" ] || printf '%s' "$NEW_LIST" > "$MANIFEST"

# 退場的腳本同理:**唸出來再刪**。一支留在專案裡、agent-control 已經沒有的腳本,
# 讀起來與還在用的一模一樣,而它守的是一份過期的規矩。
if [ -f "$CTRL_MANIFEST" ]; then
  while IFS= read -r old; do
    [ -n "$old" ] || continue
    case "$NEW_SCRIPTS" in
      *"$old"*) ;;
      *)
        echo "sync: 退場 $CTRL/$old(agent-control 已經沒有這一支)"
        [ "$DRY" = "--dry-run" ] || rm -f "$CTRL/$old"
        ;;
    esac
  done < "$CTRL_MANIFEST"
fi
[ "$DRY" = "--dry-run" ] || printf '%s' "$NEW_SCRIPTS" > "$CTRL_MANIFEST"

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

# 接點:**專案端要自己改的那幾行**。同步只把檔搬過去,搬過去的檔不會自己被呼叫 ——
# 而「同步完成」與「接上了」長得一樣,那正是這一段在擋的事(遷移計畫 §0 第 3 條)。
echo "sync: 專案端要改的接點(這幾行要出現在專案自己的腳本裡):"
if [ ! -f "$DEST/board/config.json" ]; then
  echo "sync:   0. **先補 $DEST/board/config.json** —— 這幾支往上找它來認 repo 根;"
  echo "sync:      少了它,票 / 事件 / reports 會寫到你沒在看的目錄,而且不會報錯。"
fi
cat <<'EOF'
sync:   1. 閘門跑完(專案的 gate):
sync:        python3 scripts/control/status.py done --ticket <n> --run-id <run> \
sync:            --kind gate --rc $rc --log <log 路徑>
sync:      跑之前先 `status.py start --ticket <n> --kind gate --run-id <run> --base-sha <sha>`,
sync:      不然接手的人分不出「還在跑」與「跑完了沒寫 rc」。
sync:   2. 驗證者交件(案例是對的的證明):
sync:        python3 scripts/control/verify-case.py check <n> --candidate <work 副本>
sync:   3. 套 patch → 建分支 → commit(以前是人手動做的那一手):
sync:        sh scripts/control/apply.sh <n> patch.diff [patch-verify.diff]
sync:   4. 閘門紅了自動派下一輪 worker(三輪上限):
sync:        sh scripts/control/auto-fix.sh <n>
sync:   5. 主線開場讀終態收件匣(不要輪詢 status):
sync:        python3 scripts/control/inbox.py list
sync:   6. 派工文前言(按角色裁切,≤ 4 KB,不整份貼):
sync:        python3 scripts/control/rules.py pack worker --model <模型>
sync:   7. 主線開場那一頁(SessionStart hook,寫在專案的 .claude/settings.json):
sync:        "command": "sh \"$CLAUDE_PROJECT_DIR/scripts/control/session-hook.sh\""
sync:      model 讀 board/config.json 的 routing.main(或 AC_MODEL);副本裡不觸發。
sync:   8. 閘門手跑綠 → 票轉 InReview → 派覆核(auto-fix 綠的那一趟它自己會叫):
sync:        python3 scripts/control/ticket.py set <n> state InReview && sh scripts/control/review.sh <n>
sync:      review.sh 讀 templates/dispatch-reviewer.md(從 agent-control 複製一份)與 board/config.json 的 reviewer 段。
EOF

# 接了沒:**同步了但沒有呼叫點**(2026-09-23,#29 A11;G11)。
# 上面那張接點清單是「要改的那幾行」,而它每一次都印一模一樣的內容 —— 於是「已經接上三支」
# 與「一支都沒接」在畫面上長得一樣。這裡真的去專案自己的 `scripts/*.sh` 裡 grep 一次
# (**排除 `scripts/control/` 自己** —— 產出物互相呼叫不算專案接上了)。
# hook 的呼叫點不在 `scripts/*.sh`,在 `.claude/settings.json`(#39),所以那一份也看。
UNCALLED=""
for name in $SCRIPT_LIST; do
  case " $DEPENDENCY_ONLY " in *" $name "*) continue ;; esac
  hit=""
  # 要找的形狀有兩種,而**只認第一種會把真的呼叫判成沒有**:寫死的
  # `scripts/control/status.py`,與專案自己拉一格變數的 `$CONTROL/status.py`
  # (實測 2026-09-23:某個下游專案的 `land-ticket.sh` 六處都是後者)。所以比對的是
  # `control/<檔名>`,大小寫不分(`$CONTROL/` 的那個 CONTROL 是大寫的變數名)。
  # `.` 要跳脫 —— 不跳的話 `status.py` 會對上 `statusXpy`,而那種命中沒有人看得出來。
  esc=$(printf '%s' "$name" | sed 's/\./\\./g')
  for caller in "$DEST"/scripts/*.sh "$DEST/.claude/settings.json"; do
    [ -f "$caller" ] || continue
    case "$caller" in "$CTRL"/*) continue ;; esac
    # **註解不算接上了**:`^[^#]*` 要求那一行在提到它之前沒有 `#`。實測 2026-09-23:
    # 某個下游專案的 `scripts/test-for.sh` 只在一行註解裡寫了 `scripts/control/apply.sh`,
    # 而純字串 grep 會因此判它「接上了」—— 抓錯了是吵一次,**放過了是靜的**(§5.7),
    # 所以脫罪那一段故意寫得笨,寧可多唸一次。
    if grep -liE "^[^#]*control/$esc" "$caller" >/dev/null 2>&1; then
      hit=1
      break
    fi
  done
  [ -n "$hit" ] || UNCALLED="$UNCALLED $name"
done
if [ -n "$UNCALLED" ]; then
  # shellcheck disable=SC2086
  echo "sync: 同步了但專案端沒有呼叫點:$(echo $UNCALLED)"
  echo "sync:   (搬過去的檔不會自己被呼叫;上面第 1–8 點就是那幾行要寫在哪。"
  echo "sync:    主線手打的那幾支這裡分辨不出來 —— 這一行說的是「專案的腳本裡沒有」。)"
else
  echo "sync: 專案端每一支都有呼叫點(排除只被別支 import / 呼叫的 $DEPENDENCY_ONLY)。"
fi

# 同名分岔檔(#35):專案自己的 `scripts/<名>.py` 與同步過去的 `scripts/control/<名>.py`
# 內容不同 —— 專案的腳本叫的是自己那一支,同步過去的那一份再新也用不到,而兩份讀起來
# 都像規格。該留哪一份由專案裁,這裡只說出來(不動專案的檔)。
for name in $SCRIPT_LIST; do
  case "$name" in *.py) ;; *) continue ;; esac
  [ -f "$DEST/scripts/$name" ] && [ -f "$HERE/scripts/$name" ] || continue
  if ! cmp -s "$DEST/scripts/$name" "$HERE/scripts/$name"; then
    echo "sync: 專案有同名分岔檔:$name"
    echo "sync:   scripts/$name 與 scripts/control/$name 內容不同;專案的腳本叫的是前者 ——"
    echo "sync:   改成呼叫 scripts/control/$name,或刪掉其中一份(專案裁)。"
  fi
done

# `docs/roles/` 裡**不在 manifest 上**的檔:那是專案自己放的,不是產出物。
# 混在產出物目錄裡的自建檔,下一次同步不會被蓋、也不會被退場,而它讀起來與角色卡一樣 ——
# 唸出來,讓放的人自己決定要搬走還是登記。
if [ -d "$ROLES" ]; then
  for f in "$ROLES"/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    case "$NEW_LIST" in
      *"$name"*) ;;
      *) echo "sync: 非產出物:$ROLES/$name(不在 manifest 上,是專案自己放的 —— 同步不管它)" ;;
    esac
  done
fi
