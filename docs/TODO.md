# 還沒做的事

## 0. 遷移計畫:一個既有專案怎麼接上這一套(可執行步驟)

以 tabby_pool 為例(本節是文件,所以指得出名字;程式與設定一律不准出現專案名 ——
`tests/test_no_project_names.py` 在守)。**每一步都有一句可以直接貼的指令,與一句「怎麼知道它成了」。**

| # | 做什麼 | 指令 | 怎麼知道它成了 |
|---|---|---|---|
| 1 | 把 A 複製到專案旁邊(`templates/project-CLAUDE.md` 已寫 `../agent-control`) | `git clone <A> ../agent-control && alias actl=../agent-control/bin/actl` | `actl doctor` 能跑起來(回滾:刪掉那份 clone) |
| 2 | 體檢、遷移設定與收件匣,補 `hard_block`、`board.key_file`、`scripts/ac-tests.sh`(先只有 `preflight`)、`tests.unittest.roots` | `actl doctor && actl migrate && actl inbox migrate` | `actl doctor` 的列全綠:config 版本、未知鍵(`land` / `protected_paths` / `verify_lint`)、`index.jsonl`、舊 hook、舊的控制腳本目錄 都被點名處理過;`git diff board/config.json` 看得到 `hard_block` 與 `tests` 段(回滾:`git checkout board/config.json`) |
| 3 | 裝 Claude Code hooks(換掉 guard-main hook,加 SubagentStop / SessionEnd) | `actl hooks install` | `.claude/settings.json` 裡 PreToolUse 不再指 guard-main hook,多了 SubagentStop 與 SessionEnd(回滾:`git checkout .claude/settings.json`) |
| 4 | 演練,由便宜到貴:(a) 預檢 (b) `select` 對照舊閘門挑的 (c) 一條分支過閘門 (d) 落一張 docs 票 (e) 一張 simple 票從頭走到尾;演練期間兩邊的 lander 共用同一把鎖(`AC_LAND_LOCK`) | `actl test preflight` → `git diff --name-only main...HEAD \| actl test select --mode branch` → `actl gate --branch --ticket <n>` → `actl land docs "…" tickets/<n>.json` → `actl land t<n>` | (a) 鎖屏時 rc 86 並印出列;(b) 挑出的模組與舊閘門一致;(c) `reports/t<n>/<run_id>/status.json` 有 `failures` 逐條案例、檔、行,且真的跑了票的 `verify.tags`;(d)(e) `land.pass` 事件 `by=land.sh`、帶 `ac_sha`(回滾:專案原本的 lander 還在) |
| 5 | 連續三張票由 `actl land` 落地(D-017 門檻)後,刪舊的控制腳本目錄與專案自己的 lander | `actl event grep land.pass` | 三筆連續的 `land.pass` 都是 `by=land.sh`;之後的 docs 票刪掉舊的控制腳本目錄、舊 lander、各自的接合腳本、`docs/roles/` 副本(專案特有的補充搬進 `memory/role/`),專案 `CLAUDE.md` 改完,該目錄不存在(回滾:`git revert` 那張票) |
| 6 | 看板放到 portal 後面、用 `key_file`;比 `docs/DESIGN.md` §21 的四個指標 | `actl board` | 另一個埠上的看板讀得到事件檔,四個指標有數字可以比,而不是感覺;比完才退役專案自己的看板 |

**專案特有的段落**(埠號、目錄、測試陷阱)留在專案自己的 `docs/`;本 repo 的
`docs/DISPATCH-TEMPLATE.md` 留通用版,派工時用 `rules.py pack` 裁切。

**還沒做的是遷移本身**(上表在那個專案裡做,不在這個 repo)。

## 1. 第一版必須有(對照 `docs/DESIGN.md` §18)
- [x] 票契約(`tickets/SCHEMA.md`)
- [x] 角色、流程、session 規範、派工範本、記憶、code map 的文件
- [x] `scripts/ticket.py`:create / list / show / set / inbox / verify / close / import / freeze
- [x] `scripts/event.py`:emit / tail / grep;事件種類是一張固定的表,未知的拒收
- [x] `scripts/land.sh`:0 commit 拒絕、`base_sha` 檢查、寫入範圍檢查、先印再做
- [x] `scripts/gate.sh` 介面(專案實作)+ 範例(`templates/project-tests.example.sh`);對不到模組要出聲且非零
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
- [x] 落地器紅了自動起新 worker(headless `claude -p`,三輪上限):`scripts/auto-fix.sh`、
      `gate.sh --auto-fix`、`land.sh --auto-fix`(D-015)。前提的那三件(硬閘門、取消 flake 自動判綠、
      交接閉環)是 D-014 做完的,順序照外部審查總評
- [x] land 前檢查票的 `verify.files` 都在分支上(#587 那把尺):`land.sh` 第 5 步,缺了 rc=4
- [x] flake 達門檻**自動開修復票**:`status.py` 的 `open_flaky_ticket`(同一案例只開一張)
- [x] 套 patch → 建分支 → commit 做成一個程式入口:`scripts/apply.sh`(含 `rebase` 重生)
- [x] 終態叫醒主線:`scripts/inbox.py` + `new-session.sh` 開場印;主線不輪詢
- [x] 同一輪的回歸只跑一次:`scripts/verify.py` 的快取(標籤 + sha),`--no-cache` 關
- [x] 按角色裁切、帶版本的規則包:`scripts/rules.py pack <角色>`(≤ 4 KB)
- [ ] 推測性佇列(H+A 與 H+A+B 同時驗)
- [ ] token 歸因:Claude Code 不給資料,先顯示「未知」,不估
- [ ] 產品功能地圖
- [ ] 多 repo

## 3. 已知限制
- Claude Code 的子 agent 無法從外部啟動、觀測、中止;協調者必須自己是 Claude Code session 或走 SDK。Codex(`codex exec`)與 agy(`-p`)有非互動模式。
- 額度是真的會用完的:2026-09-10 一天內 Fable、Codex、Opus 三個都撞過。政策在 `docs/ROLES.md`。

## 明天用哪一份(2026-09-21,外部審查要求先指定)
票庫 = 本 repo 的 `tickets/*.json`(`board/config.json` 的 `tickets_dir`);事件流 = `board/events.jsonl`;
閘門 = 本 repo 的 `scripts/gate.sh`(專案自己填 `board/config.json` 的 `tests` 段,**要接 `--ticket`**)。
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
