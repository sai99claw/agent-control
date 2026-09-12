# <專案名>

本專案使用 Agent Control(`../agent-control`)。**先讀 `../agent-control/CLAUDE.md`**,再讀下面的專案特有部分。

## 專案特有
- 閘門:`scripts/gate.sh --branch|--base|--full`(對照表在裡面;對不到要出聲)。
- 禁區埠:<prod/staging/portal 埠>;禁區目錄:<資料與秘密>。
- 副本裡**不准跑**的腳本:<會寫到 repo 外或 prod 的那幾支>。
- 發版:`scripts/release.sh <tag>`,人授權、主線執行。
