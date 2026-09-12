---
module: scripts/land.sh
purpose: 把幾條綠的分支依序串起來、跑一次全套、綠了才 fast-forward 主線;不綠、不合規就整批拒絕
entry_points: [cfg, ev, 分支盤點迴圈, worktree 合併迴圈]
invariants:
  - "任何一條分支不過,整批拒絕 —— 不跳過那一條"
  - "拒絕發生在 worktree add 之前,所以拒絕的路徑不留殘骸"
  - "全套跑在 land/<時間> worktree 裡,主線的工作樹全程沒有人動"
  - "每一步都發事件;控制台只讀事件"
depends_on: [scripts/event.py, scripts/gate.sh, board/config.json, tickets/*.json]
depended_by: [scripts/heartbeat.sh, board/board.py]
source_paths: [scripts/land.sh]
verified_at_commit: e6f3ed32112440ec727c826c02eb61ade46ad39d
verified_by: opus 2026-09-12
---
**這是示範卡**(`docs/CODE-MAP.md` §2 的格式),同時也是 `check-stale.py` 的第一筆
真實輸入。

## 為什麼它的拒絕條件長這樣
四種拒絕各自對應一次事故,理由逐條寫在腳本自己的註解裡(那裡才是會被讀到的地方)。
這張卡只記**形狀**:五步檢查(分支在不在 / commit 數 / 票對不對得上 / `base_sha` 是不
是主線祖先 / 寫入範圍),全部跑完才一起決定要不要拒絕 —— **不是第一個錯就退**。
理由:一次把所有問題印出來,比讓人修一個、再撞下一個少幾次來回。

## 常見的坑
- **它從哪個工作樹執行,跑的就是哪一份。** 在主線工作樹跑 `land.sh` 時,跑的是主線那
  一份,不是分支上剛改好的那一份 —— 新行為的第一次真跑永遠在下一輪
  (`docs/DISPATCH-TEMPLATE.md` §5.5 最後一列)。
- `AC_WORKTREE_DIR` 沒設時,worktree 開在 repo 的**兄弟目錄**;測試要記得它不在
  repo 裡面。

## 這張卡自己會示範什麼叫過期
`verified_at_commit` 記的是 `scripts/land.sh` 還沒進主線的那個 commit。它一進主線,
`python3 code-map/check-stale.py` 就會把這張卡標成 `stale` —— **那正是這支工具要做的
事**,而不是一個要修掉的 bug。重新讀過卡的人把 sha 與 `verified_by` 更新成當下的。
