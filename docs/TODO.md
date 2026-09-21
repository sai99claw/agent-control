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
- [x] `scripts/heartbeat.sh`:session / land / attempt 的租約與死亡偵測、land worktree 殘骸
- [x] `board/board.py`:抽出、去專案名、讀 `board/config.json`;agent 時間線與「哪些保證還只在演練裡成立」(`docs/REHEARSAL.md`)
- [x] 決策收件匣填完自動發事件(`decision.answered`;主線用 `ticket.py inbox` 讀,它拿 `docs/DECISIONS.md` 判哪些還沒落成裁示)
- [x] `scripts/memory.py check|consolidate`:量每份必讀記憶檔的字元數對 front matter 的 `cap_chars`(預設 2000),超過就發 `memory.over_cap` 並開整理票;`consolidate` 必須帶 `--discussion <path>`(D-007),提高上限要有理由(D-006)

- [x] `scripts/new-session.sh`、`code-map/check-stale.py`、`docs/DISPATCH-TEMPLATE.md` 通用版、`tests/`
- [x] `scripts/status.py`:一輪一個 run 目錄、`repair_context` 交接包、`phases`、持久 log、`reports/flaky.jsonl`(D-014)
- [x] `scripts/gate.sh --ticket`:寫狀態檔、**真的跑票的 `verify.tags`**、單跑綠只標 `suspected_flaky` + 原順序整組重跑(D-014)
- [x] `scripts/verify-case.py`:`check`(乾淨主線該紅 / candidate 該綠,import 失敗不算紅)、`extract`、`tags-merge`;`verify/TAGS.d/<票號>.md` 片段登記(D-014)
- [x] `scripts/land.sh`:互斥鎖(mkdir)、覆核綁票版本與分支 sha、未處置的阻擋反駁拒收、gate/merge/push 分開記、每條退出路徑寫終態(D-014)
- [x] `scripts/ticket.py`:`objections[]`、`--expect-attempt` / `--expect-state-version` 拒收過期回報、票庫寫入鎖、`close` 與 `set state Done` 共用必要條件、`round` 三輪耗盡轉 Blocked(D-014)

## 2. 第二版
- [ ] 控制台加 `?key=` + cookie 驗證(`board/config.json` 多一格 `key_file`);v0.1 只綁 127.0.0.1
- [ ] 排序做成腳本(讀 `allowed_write_paths` 算衝突圖)**提案**給主線,不用模型 —— 調度員這個角色已退場(D-010),這一格是它留下來的那一成機械工作
- [ ] 落地器紅了自動起新 worker(headless `claude -p`,三輪上限):規格在 `docs/WORKFLOW.md` §回歸紅了之後,腳本還沒做
      —— **2026-09-21 主線裁示:這一輪先不做**。硬閘門、取消 flake 自動判綠、交接閉環先上,免得把現有的漏接自動放大
      (外部審查總評的原話:「這三件先完成,再上自動派 worker」)。
- [ ] land 前檢查票的 `verify.files` 都在分支上(#587 那把尺):規格已定、未實作
- [ ] flake 達門檻**自動開修復票**:現在只發 `decision.asked`,開不開由主線決定
- [ ] 套 patch → 建分支 → commit 做成一個程式入口(現在是主線手動做;`land.sh` 收的是已有 commit 的分支)
- [ ] 推測性佇列(H+A 與 H+A+B 同時驗)
- [ ] token 歸因:Claude Code 不給資料,先顯示「未知」,不估
- [ ] 產品功能地圖
- [ ] 多 repo

## 3. 已知限制
- Claude Code 的子 agent 無法從外部啟動、觀測、中止;協調者必須自己是 Claude Code session 或走 SDK。Codex(`codex exec`)與 agy(`-p`)有非互動模式。
- 額度是真的會用完的:2026-09-10 一天內 Fable、Codex、Opus 三個都撞過。政策在 `docs/ROLES.md`。

## 明天用哪一份(2026-09-21,外部審查要求先指定)
票庫 = 本 repo 的 `tickets/*.json`(`board/config.json` 的 `tickets_dir`);事件流 = `board/events.jsonl`;
閘門 = 本 repo 的 `scripts/gate.sh`(專案自己的照 `scripts/gate.example.sh` 改,**要接 `--ticket`**)。
演練用一張拋棄式票走完:交付 → `verify-case.py check` 驗紅 → `ticket.py set review` 覆核 → `land.sh` → `ticket.py close`。

## 票進 git 會撞鎖(2026-09-12,tabby_pool D-G112 的教訓)
tabby_pool 的票住看板資料庫、裁示住 code repo 的 DECISIONS.md;裁示與程式同一份歷史,
票每天改幾十次狀態不進 git。agent-control 把 `tickets/*.json` 放進 repo,遷移時每次
gate/review 寫回票都變成 main 寫入,會與落地鎖排隊、把歷史塞滿雜訊。遷移前二選一:
票的寫回走同一把鎖的輕量通道(只跑 schema 檢查),或票搬到 git 之外的儲存。
**2026-09-21 進度**:`ticket.py` 的寫回已經走自己那把 `.ticket.lock`(與落地的 `.land.lock` 是兩把,所以票的回寫
不會跟程式落地互相阻塞);「票要不要進 git」本身仍未裁示。

## LSP 接入(2026-09-13,使用者期待「用 LSP 讀 code」)
目前 code-map 是文件、不是 LSP;本機 MCP 只有 Google Calendar/Drive。候選:接一個 LSP MCP(Python 用 pyright,
JS 用 typescript-language-server),讓開題 session 有 go-to-definition / references。先在 tabby_pool 試,
量「開題一張票的 token」有沒有比 grep 少,再決定進 SESSION-START。
