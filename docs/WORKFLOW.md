# 工作流程

## 票的狀態機
```
Draft → Ready → Running → InReview → IntegrationQueued → Integrating → Done
                  ↕            ↕
              Blocked / NeedsDecision / Failed / Cancelled(保存原因與回復點)
```
| 關卡 | 必須具備 |
|---|---|
| Ready | 範圍、可操作的驗收、依賴達成、`base_sha`、`allowed_write_paths` |
| InReview | patch、測試輸出逐字、變異驗紅、回報標明實測/推論 |
| IntegrationQueued | 審查通過、局部閘門綠、無未解阻擋 |
| Done | **`scripts/ticket.py verify` 證明改動真的在主線**、文件已更新、事件齊全 |

**exit code 0 不等於 Done;worker 說做完不等於 Done;閘門綠是對某個 `base_sha` 說的,基準走遠就過期。**

## 一票一分支一副本
- worker 拿到 `base_sha` 的 `git archive` 副本(`work/`)與對照副本(`base/`),交 `diff -ruN base work > patch.diff`。
- 主線或落地器把 patch 套進 `wt/<ticket>` worktree,跑局部閘門(`scripts/gate.sh --branch`),commit,排隊。
- **同時只准一個 `land.sh`**;land 期間主線不得往主線提交。發版期間不開 land。

## 落地(`scripts/land.sh t1 t2 …`)
1. 先印每支分支的 commit 數與標題(**讓人看見它以為自己在做什麼**)。
2. 任一支 0 commit → 整批拒絕(跳過會生出沒有人要求過的組合)。
3. 任一支的 `base_sha` 不是主線祖先 → 拒絕,要求 rebase 後重跑閘門。
4. patch 動到 `allowed_write_paths` 以外 → 拒絕。
5. 依序合到 `land/<ts>` worktree,跑全套(`scripts/gate.sh --full`),綠才 `--ff-only` 推主線;紅則主線不動、worktree 留著給人看。
6. 每一步發事件。

## 閘門(`scripts/gate.sh`)
專案自己定義三層:`--branch`(改動檔對應的模組)、`--base`(基礎組)、`--full`(全套)。**對不到任何模組要出聲,不准印一行綠。** 判綠先寫檔再讀退出碼,不用 `cmd | tail`。瀏覽器引擎由 `available()` 判,不在指令列收窄。

## 發版
主線執行、人事先授權;順序:tag → build → staging → 閘門 → prod → 健康檢查 → 記事件。**發版期間排程器暫停 land。**

## 衝突
- 排程器讀票的 `allowed_write_paths` 判平行;**不同檔不代表安全**,介面耦合要看票的 `interface_contracts`。
- 兩支分支都往同一份文件尾巴附加(HANDOFF 那類)一定衝突:**先等對方進去再寫**,而不是寫完再解——解衝突時讀的是 diff,先等讀的是完整的檔。
- 自動合成功不等於合對:land 輸出出現 `Auto-merging <文件>` 就把結果讀一遍。
