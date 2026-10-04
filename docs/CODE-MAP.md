# 讓 AI 追 code 的方案

## 判斷:先不要建整套 wiki
tabby_pool 三天的實測:agent 讀整份設計文件並沒有出大錯;出錯的是**文件裡的行號過期**這種小事,以及**票面用近似條件盤點、數出假的下界**。所以第一版只做兩件事,都便宜、都可驗證:

## 0. repo map(`scripts/repo-map.py`,#87)
查「X 在哪、誰守它」先問它,查不到再針對性 grep。三個子命令:
- `build`:依手寫的 `code-map/catalog.json` 重生產出物 `code-map/index.json` 與 `code-map/INDEX.md`。新增 / 改名 / 刪除已管理的檔之後跑,產出物連同改動一起 commit。
- `check`:唯讀;索引過期或 entry / guard 連結斷了 rc 1。閘門帶票跑時(`gate.sh --ticket`)對候選樹跑它,**過期必擋**(機械格,不跑測試)。
- `query <主題>`:印那個主題的入口、主守衛、來源、文件、人工卡;**查不到 rc 1** 並列出認得的主題。只認 catalog 的主題名,不查符號。

專案的 `code-map/catalog.json`、`code-map/index.json` 與人工卡**歸專案所有**:`sync-to-project.sh` 只把 `repo-map.py` 同步進專案的 `scripts/control/`,不覆蓋專案的 `code-map/`。

## 1. 模組卡片(只在查找失敗過的地方建)
`code-map/cards/<module>.md`,每張:
```
---
module: demo/web/nav.js
purpose: 一句話
entry_points: [sizeNav, paint, go]
invariants:
  - "--navh 只有 nav.js 寫"
  - "view 之間不互相 import"
depends_on: [state.js, api.js]
depended_by: [views/*.js]
source_paths: [demo/web/nav.js]
verified_at_commit: <sha>
verified_by: <who> <date>
---
正文:為什麼這樣設計、常見的坑(附票號)。
```
**觸發建卡的條件**:某張票的 agent 回報「找不到 X 在哪」或「以為 Y 在 Z 其實不是」→ 主線建一張卡。不預先鋪滿。

## 2. 過期偵測(`code-map/check-stale.py`)
- 對每張卡,`git diff --stat <verified_at_commit>..HEAD -- <source_paths>`;有變動就標 `stale`。
- 控制台顯示 stale 的卡;閘門不擋(卡片是輔助,不是契約)。
- 卡片裡引用的**行號一律不寫**,寫符號名(`grep -n` 得到的東西會過期,符號名不會)。

## 3. 查證規矩(給讀 code 的 agent)
- **要數某條規則會抓到多少,用那條規則本身去數**;做不到就標「至少 N(用 X 條件數的)」。
- 回報裡每個結論標「(實測:指令、輸出)」或「(讀 code 推的,未實測)」——**兩者在回報裡長得一樣,錯的時候誰會發現不一樣**。
- 外部模型交回來的文案提到產品功能,一律 `grep` 一次再用。

## 之後才考慮
符號索引、依賴圖、向量檢索——等「查找失敗」累積到看得出形狀,而且要算維護成本。
