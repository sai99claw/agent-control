# <專案名>

本專案使用 Agent Control(`../agent-control`)。**先讀 `../agent-control/CLAUDE.md`**,再讀下面的專案特有部分。

## 專案特有
- 閘門:`scripts/gate.sh --branch|--base|--full`(對照表在裡面;對不到要出聲)。
- 禁區埠:<prod/staging/portal 埠>;禁區目錄:<資料與秘密>。
- 副本裡**不准跑**的腳本:<會寫到 repo 外或 prod 的那幾支>。
- 發版:`scripts/release.sh <tag>`,人授權、主線執行。
- 開場:`.claude/settings.json` 的 SessionStart hook 叫 `scripts/control/session-hook.sh`,主線那一頁在第一個 prompt 之前就在上下文裡(model 讀 `board/config.json` 的 `routing.main`;副本裡不觸發)。

## 對照表
`../agent-control/CLAUDE.md` 說的是 A 的入口;在這個專案裡叫的是右邊那一支。**右邊還是佔位就是還沒接上**,不要照左邊的路徑跑。

| A 的入口 | 這個專案的入口 |
|---|---|
| `scripts/land.sh` | `<專案入口>` |
| `scripts/apply.sh` | `<專案入口>` |
| `scripts/inbox.py` | `<專案入口>` |
| `scripts/event.py` | `<專案入口>` |
| `scripts/ticket.py` | `<專案入口>` |
