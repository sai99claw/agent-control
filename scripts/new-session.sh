#!/bin/sh
# 開 session 的固定動作,跑一遍 — `docs/SESSION-START.md`。
#
#   sh scripts/new-session.sh <role> <model>
#   sh scripts/new-session.sh main fable
#
# 為什麼要有這一支:那一節是一張清單,而**一張要靠人記得照做的清單,漏掉一項時
# 長得跟做完了一樣**。跑成腳本之後,漏掉的那一項會在畫面上缺一段。
#
# 它**只讀不寫**,唯一的寫入是最後那一筆 `session.start` 事件 —— 控制台從此看得到你。
set -u
[ $# -ge 2 ] || { echo "new-session: 要 <role> <model>,例:sh scripts/new-session.sh main fable"; exit 2; }
ROLE=$1
MODEL=$2
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export AC_ROOT=$ROOT
MAIN=main

say() { echo; echo "── $* ────────────────────────────────"; }

say "1. 站在哪個版本、工作樹乾不乾淨"
git -C "$ROOT" log --oneline -1 "$MAIN" || echo "(讀不到 $MAIN)"
git -C "$ROOT" status --short

say "2. 最近發生的事"
python3 "$ROOT/scripts/event.py" tail 20

say "3. 開著的票"
python3 "$ROOT/scripts/ticket.py" list --open

# 記憶的上限:D-006 要求每個 session 開頭量一次。**退出碼 1 不停工** ——
# 它只是讓這個 session 知道自己的記憶該整理了(docs/MEMORY.md 第 1 點)。
say "4. 記憶有沒有超過上限(D-006)"
sh -c "python3 \"$ROOT/scripts/memory.py\" check" || echo "new-session: (超過上限,不停工;整理票已經開了)"

if [ "$ROLE" = "main" ]; then
    say "5. 收件匣:使用者填過的裁示要落成 docs/DECISIONS.md 一列"
    python3 "$ROOT/scripts/ticket.py" inbox
    say "6. 心跳:上一個排程器 / land 有沒有死在半路"
    sh "$ROOT/scripts/heartbeat.sh" || true
fi

say "發事件:控制台從此看得到你"
python3 "$ROOT/scripts/event.py" emit session.start --role "$ROLE" --model "$MODEL"

say "接下來要讀的(照順序)"
echo "  docs/HANDOFF.md          最後三節 —— 上一個 session 留給你的"
echo "  docs/SESSION-START.md    你這個角色($ROLE)那一節"
echo "  memory/model/$MODEL.md   你這個模型在這個專案踩過的坑"
case "$ROLE" in
    main)
        echo "  CLAUDE.md                不可違反的那幾條"
        echo "  docs/TODO.md             §1 還沒打勾的"
        ;;
    scheduler)
        echo "  docs/ROLES.md            §排程器:只提案,不派工、不落地、不寫文件"
        echo "  票的 allowed_write_paths 判平行用的就是這一格"
        ;;
    *)
        echo "  docs/DISPATCH-TEMPLATE.md  §禁區 與 §5.5 假綠家族"
        echo "  派工文裡指定的副本路徑 —— 先 ls 確認 work/ 與 base/ 都在"
        ;;
esac
echo
echo "結束前:交接寫 docs/HANDOFF.md、發 session.end、關票前先 ticket.py verify。"
