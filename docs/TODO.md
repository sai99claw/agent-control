# 還沒做的事

## 0. 遷移計畫(從 tabby_pool 搬過來)
1. [ ] tabby_pool 的 `CLAUDE.md` 改成:先讀本 repo 的 `CLAUDE.md`,再讀專案自己的。
2. [ ] tabby_pool 的票(`~/.claude/tasks/<id>/*.json`)轉成本 repo 的 schema(`scripts/ticket.py import`),補 `base_sha` / `allowed_write_paths`(舊票允許空,新票必填)。
3. [ ] tabby_pool 的 `scripts/{land,fullsuite,test-for}.sh` 改成呼叫本 repo 的 `scripts/land.sh` + 專案自己的 `scripts/gate.sh`。
4. [ ] `docs/DISPATCH-TEMPLATE.md` 專案特有的段落(埠號、目錄、測試陷阱)搬回 tabby_pool 的 `docs/`,本 repo 留通用版。
5. [ ] 控制台改讀本 repo 的事件檔;`board-note.py` 退役,改用 `scripts/event.py`。
6. [ ] 兩邊並行跑一週,比較 `docs/DESIGN.md` §21 的指標。

## 1. 第一版必須有(對照 `docs/DESIGN.md` §18)
- [x] 票契約(`tickets/SCHEMA.md`)
- [x] 角色、流程、session 規範、派工範本、記憶、code map 的文件
- [x] `scripts/ticket.py`:create / list / show / set / inbox / verify / close / import / freeze
- [x] `scripts/event.py`:emit / tail / grep;事件種類是一張固定的表,未知的拒收
- [x] `scripts/land.sh`:0 commit 拒絕、`base_sha` 檢查、寫入範圍檢查、先印再做
- [x] `scripts/gate.sh` 介面(專案實作)+ 範例(`scripts/gate.example.sh`);對不到模組要出聲且非零
- [x] `scripts/heartbeat.sh`:排程器 / land / attempt 的租約與死亡偵測、land worktree 殘骸
- [x] `board/board.py`:抽出、去專案名、讀 `board/config.json`;agent 時間線與「哪些保證還只在演練裡成立」(`docs/REHEARSAL.md`)
- [x] 決策收件匣填完自動發事件(`decision.answered`;主線用 `ticket.py inbox` 讀,它拿 `docs/DECISIONS.md` 判哪些還沒落成裁示)
- [x] `scripts/memory.py check|consolidate`:量每份必讀記憶檔的字元數對 front matter 的 `cap_chars`(預設 2000),超過就發 `memory.over_cap` 並開整理票;`consolidate` 必須帶 `--discussion <path>`(D-007),提高上限要有理由(D-006)

- [x] `scripts/new-session.sh`、`code-map/check-stale.py`、`docs/DISPATCH-TEMPLATE.md` 通用版、`tests/`(130 條)

## 2. 第二版
- [ ] 控制台加 `?key=` + cookie 驗證(`board/config.json` 多一格 `key_file`);v0.1 只綁 127.0.0.1
- [ ] 排程器做成腳本(讀 `allowed_write_paths` 算衝突圖),不用模型
- [ ] 推測性佇列(H+A 與 H+A+B 同時驗)
- [ ] token 歸因:Claude Code 不給資料,先顯示「未知」,不估
- [ ] 產品功能地圖
- [ ] 多 repo

## 3. 已知限制
- Claude Code 的子 agent 無法從外部啟動、觀測、中止;協調者必須自己是 Claude Code session 或走 SDK。Codex(`codex exec`)與 agy(`-p`)有非互動模式。
- 額度是真的會用完的:2026-09-10 一天內 Fable、Codex、Opus 三個都撞過。政策在 `docs/ROLES.md`。

## 票進 git 會撞鎖(2026-09-12,tabby_pool D-G112 的教訓)
tabby_pool 的票住看板資料庫、裁示住 code repo 的 DECISIONS.md;裁示與程式同一份歷史,
票每天改幾十次狀態不進 git。agent-control 把 `tickets/*.json` 放進 repo,遷移時每次
gate/review 寫回票都變成 main 寫入,會與落地鎖排隊、把歷史塞滿雜訊。遷移前二選一:
票的寫回走同一把鎖的輕量通道(只跑 schema 檢查),或票搬到 git 之外的儲存。

## LSP 接入(2026-09-13,使用者期待「用 LSP 讀 code」)
目前 code-map 是文件、不是 LSP;本機 MCP 只有 Google Calendar/Drive。候選:接一個 LSP MCP(Python 用 pyright,
JS 用 typescript-language-server),讓開題 session 有 go-to-definition / references。先在 tabby_pool 試,
量「開題一張票的 token」有沒有比 grep 少,再決定進 SESSION-START。
