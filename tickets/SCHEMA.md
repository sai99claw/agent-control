# 票的契約

檔名 `tickets/<id>.json`。**worker 只靠這張票與可查詢的來源就能開工。**

| 群組 | 欄位 | 必填 | 說明 |
|---|---|---|---|
| 識別 | `id`, `subject`, `created` | ✓ | id 遞增整數字串 |
| 產品關聯 | `feature`, `requirement_version` | 建議 | 對到產品功能;需求改版時辨識受影響票 |
| 目標與範圍 | `objective`, `acceptance` (list), `in_scope`, `out_of_scope` | ✓ | acceptance 每條要能寫成一條會紅的斷言 |
| 高階規劃 | `decision_refs` (list), `outline`, `interface_contracts`, `test_plan` | 建議 | 留關鍵決定,不抄整段規劃聊天 |
| 依賴與衝突 | `depends_on` (list of {id, condition}), **`allowed_write_paths`** (list of globs) | ✓ | 排程器與落地器都讀;`shared_resources` 建議 |
| 執行 | `role`, `model`, `tool`, **`attempt`** (int) | ✓ | 每次派工 attempt+1;遲到的回報對不上 attempt 就拒絕;`attempt_history` 建議 |
| 版本 | **`base_sha`** | ✓ | 閘門結果只對 base_sha 有效;`branch`、`workspace` 派工時由工具填 |
| 控制 | `state`, **`state_version`**, `lease` ({holder, until}), `retry_limit` | ✓ | state 見 `docs/WORKFLOW.md`;state_version 每次變更 +1;`budget` 建議 |
| 交付 | `patch_ref`, `review_ref`, `test_evidence` (list), `handoff`, `knowledge_updates`, **`verify_strings`** (list) | Done 前 | evidence 每筆帶 base_sha、指令、rc、log 路徑;**`verify_strings` 是只有這張票才有的字串,`ticket.py verify` 對 `git show main:<檔>` grep 它們——沒給的話 verify 只能做弱檢查(看 `allowed_write_paths` 有沒有被動過),並會明說** |
| 凍結 | `frozen` ({reason, criterion}) | 選 | 例:「產出會不會因視覺方向改變而重做」 |

## 最小可開工範例
```json
{
  "id": "1",
  "subject": "land.sh 對 0 commit 的分支整批拒絕",
  "created": "2026-09-12T10:00:00+08:00",
  "objective": "任一支分支相對主線 0 commit 時,land 秒退並點名,不跑全套",
  "acceptance": ["0 commit → rc!=0 且輸出含分支名", "多支中一支 0 → 全部不合(另一支也不在 land worktree)", "正常路徑輸出與退出碼不變"],
  "in_scope": ["scripts/land.sh", "tests/test_land.py"],
  "out_of_scope": ["gate.sh"],
  "depends_on": [],
  "allowed_write_paths": ["scripts/land.sh", "tests/test_land.py", "docs/WORKFLOW.md"],
  "role": "worker", "model": "opus", "tool": "claude-code", "attempt": 1,
  "base_sha": "<main sha at dispatch>",
  "state": "Ready", "state_version": 1, "lease": null, "retry_limit": 2
}
```

## 為什麼是這幾個欄位(每一個都對應一次事故)
- `base_sha`:2026-09-10 用過期的 branch ref 做副本,agent 拿到看起來正常但缺了上一張票的樹。
- `allowed_write_paths`:五張票共用 index.html、三張共用同一支測試檔,只能靠人讀 code 排序。
- `attempt` / `state_version`:一張票被關成 Done 但從沒進主線,兩天後才發現。
- `test_evidence.base_sha`:一支分支 3470 條綠,跑在三小時前的基準上,主線早已走遠。
