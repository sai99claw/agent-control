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
| Done | **`scripts/ticket.py verify` 證明改動真的在主線**(票要有 `verify_strings`;沒有的話只能弱檢查,verify 會明說,主線要自己補 grep)、文件已更新、事件齊全 |

**exit code 0 不等於 Done;worker 說做完不等於 Done;閘門綠是對某個 `base_sha` 說的,基準走遠就過期。**

## 一票一分支一副本
- worker 拿到 `base_sha` 的 `git archive` 副本(`work/`)與對照副本(`base/`),交 `diff -ruN base work > patch.diff`。
- 主線或落地器把 patch 套進 `wt/<ticket>` worktree,跑局部閘門(`scripts/gate.sh --branch`),commit,排隊。
- **同時只准一個 `land.sh`**;land 期間主線不得往主線提交。發版期間不開 land。

## 落地(`scripts/land.sh t1 t2 …`)
1. 先印每支分支的 commit 數與標題(**讓人看見它以為自己在做什麼**)。
2. 任一支 0 commit → 整批拒絕(跳過會生出沒有人要求過的組合)。
3. 任一支的 `base_sha` 有問題 → 拒絕,分兩種話講:**主線根本沒有那個 sha**(副本是拿錯的 ref 做的,回去查副本從哪來)vs **有但不是主線祖先**(主線走遠了,rebase 後重跑閘門)。兩者 `merge-base` 都非零,下一步差很多。
4. patch 動到 `allowed_write_paths` 以外 → 拒絕。
5. 依序合到 `land/<ts>` worktree,跑全套(`scripts/gate.sh --full`),綠才 `--ff-only` 推主線;紅則主線不動、worktree 留著給人看。
6. 每一步發事件。

## 閘門(`scripts/gate.sh`)
專案自己定義三層:`--branch`(改動檔對應的模組)、`--base`(基礎組)、`--full`(全套)。**對不到任何模組要出聲,不准印一行綠。** 判綠先寫檔再讀退出碼,不用 `cmd | tail`。瀏覽器引擎由 `available()` 判,不在指令列收窄。

## 發版
主線執行、人事先授權;順序:tag → build → staging → 閘門 → prod → 健康檢查 → 記事件。**發版期間不開 land。**

## 衝突
- 排順序的人(主線,或依 `allowed_write_paths` 算衝突圖的腳本)讀票的 `allowed_write_paths` 判平行;**不同檔不代表安全**,介面耦合要看票的 `interface_contracts`。
- 兩支分支都往同一份文件尾巴附加(HANDOFF 那類)一定衝突:**先等對方進去再寫**,而不是寫完再解——解衝突時讀的是 diff,先等讀的是完整的檔。
- 自動合成功不等於合對:land 輸出出現 `Auto-merging <文件>` 就把結果讀一遍。

## 狀態檔(2026-09-21,D-010)
每次 gate / land / docs 開跑寫 `reports/t<n>-status.json` `{state:"running", kind, sha, started}`,跑完覆寫 `{state:"done", rc, report, logs:[…], failures:[{case, file, engine, log, line, excerpt}], flaky:[…]}`。failures 從 `^(FAIL|ERROR):` 與其後的 Traceback 擷取(excerpt ≤ 20 行)。agent 讀這一份就知道跑完了沒、錯了什麼、去哪看;不必看全套輸出。

## 回歸紅了之後(2026-09-21,D-010)
1. 紅的案例**單獨重跑一次**(同 worktree、同 commit);單跑綠的標 flaky 移出 failures;全 flaky → gate 視為綠,land 自動再跑一次全套(上限一次)。
2. 真紅 → 落地器用 headless `claude -p` 起**新** worker:派工文 = 共用規矩 + 票面 + 目前 patch 路徑 + failures 逐條 excerpt + 上一輪 EVIDENCE + 「第 r 輪」;交回 `patch-round<r+1>.diff` 後自動 redo / land。
3. 停下來報主線的三種情況:worker 標「票寫錯 / 需要裁示」;三輪仍紅;failures 裡有票沒動到的檔(疑似他票或環境)。
4. 覆核不自動:主線讀 patch 記 review 後才 land。

### 這一段哪些已經是程式,哪些還只是規格
| 規格 | 這個 repo 的狀態 |
|---|---|
| `reports/t<n>-status.json`(running / done、rc、failures 逐條、flaky) | **已實作**:`scripts/status.py`,由 `scripts/gate.sh --ticket <n>` 與 `scripts/land.sh` 寫 |
| 紅的案例單獨重跑一次判 flake;全 flaky 視為綠 | **已實作**:`scripts/gate.sh --ticket <n>`(`AC_NO_FLAKE_RERUN=1` 可關)。`land.sh` 那一側**只寫紅榜、不重跑** —— 一批裡哪一條紅對到哪一張票,要有票↔案例的對照才判得出來,還沒做。 |
| 落地器用 headless `claude -p` 起新 worker、三輪上限、三種停下來報主線 | **規格已定、腳本未實作**(第一個使用它的專案 #615 / #616 實作中)。在那之前這一步由主線手動做,做的時候照上面 1–4 條。 |
| land 前檢查 `verify.files` 都在分支上 | **規格已定、腳本未實作**;`land.sh` 現在只檢查 `allowed_write_paths` 與 `verify_strings`。 |

## 驗證者的案例怎麼進閘門
票的 `verify.tags` 併進 `tags` 一起跑;land 前檢查 `verify.files` 都在分支上(#587 那把尺:patch 裡列的每個 `+++` 檔都要真的出現在 worktree)。驗證者不出 VERDICT;紅了照上一節走。

## patch 管線:多張票接連落地時會撞什麼(2026-09-21 實測)
連續落地幾張票,**幾乎一定**撞到兩個地方:所有票都往尾端附加的登記檔(`verify/TAGS.md` 那一類)
與自動產生的清單(modulepreload 那一類)。所以:

1. **落地前把 patch 套到「當前主線」的副本上,而不是直接 `git apply`。** 用 GNU `patch`(它吃 fuzz、
   會自己找位移),重生自動產生的清單,再從那份副本出一份**乾淨的 diff**。
2. **`.rej` 數量 ≠ 0 一律當失敗。** 套完 `find . -name '*.rej' -o -name '*.orig'`,有就停 ——
   「套了但有幾塊沒進去」與「全套進去了」在退出碼上長得一樣。
3. **`diff -ruN` 對刪檔產生的 `+++` 側要手改成 `/dev/null`。** 不改的話 `git apply` 只會把檔案
   **清空**而不是刪掉,而清空的檔案在 diffstat 上看起來像是「改過」。
4. **票的 `verify_strings` 在落地前先對 patch `grep` 一次。** 字串不在 patch 裡,就不會在分支上,
   而那要等到 `land` 的最後一步才會說話。
5. **patch 檔頭只准 `base/…` / `work/…` 的相對形式;絕對路徑閘門要拒。** 🩸 真的發生過:
   檔頭帶著絕對路徑的 patch 套下去,檔案被寫進暫存目錄底下的同名路徑,而套用本身成功、
   閘門也綠 —— 因為被改的根本不是 repo 裡那一份。

## 派工的並行上限
同時最多兩個會起瀏覽器的 agent;全套並跑時不再起瀏覽器型 agent(2026-09-20 旅程「11-batch」在滿載下紅五次,每次白跑一輪全套)。優先序改變時把低優先的 agent 停掉,不要讓它自己滾。

