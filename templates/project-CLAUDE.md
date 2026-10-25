# <專案名>

本專案使用 Agent Control(`../agent-control`)。**主線**先讀 `../agent-control/CLAUDE.md`,再讀下面的專案特有部分。
**短命角色**(worker / 驗證者 / 開題者 / 覆核者)開場只讀「角色卡 + 票」(正文 `../agent-control/memory/role/README.md`),
這一份只看「專案特有」那一節 —— 禁區與陷阱是你會被擋到的;共用規矩按角色卡指的節去查,不整份讀。

## 專案特有
A 的入口一律 `actl <命令>`;專案特有的只有本節。
- 閘門:`board/config.json` 的 `tests` 段宣告 `preflight` / `select` / `run` / `full` / `regression`,閘門是 A 的 `actl gate`(對不到要出聲)。
- 禁區埠:<prod/staging/portal 埠>;禁區目錄:<資料與秘密>。
- 副本裡**不准跑**的腳本:<會寫到 repo 外或 prod 的那幾支>。
- 發版:`scripts/release.sh <tag>`,人授權、主線執行;全套是 `actl test full`(`AC_TEST_MODE=release`)。回歸紅在既有案例上 → 發版腳本叫 `actl status regression-red --source release --result <result.json>`,發版照擋。
- 開場:`actl hooks install` 把 SessionStart hook 寫進 `.claude/settings.json`,主線那一頁在第一個 prompt 之前就在上下文裡(model 讀 `board/config.json` 的 `routing.main`;副本裡不觸發)。
