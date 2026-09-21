# agent-control × tabby 看板:記憶提示 / race / 看板直吃檔案 —— 評估報告

評估者:Fable(獨立)。對象:agent-control HEAD bd8e503;tabby_pool main 5c8718c。
日期:2026-09-22。未改任何檔、未做 git 寫入、未碰任何 127.0.0.1 埠、未跑整支腳本。
證據由三個 Opus 唯讀子工作者搜集(agent-control 端、tabby 看板端、原始 log 格式),我做交叉核對與結論。
每條標「實測」(有人跑指令看到輸出)或「讀 code 推的」。路徑一律絕對路徑;`AC/` = <home>/ai_workspace/claude_workspace/agent-control,`TB/` = <home>/ai_workspace/claude_workspace/tabby_pool,`WT/` = <home>/ai_workspace/claude_workspace/tabby_pool_wt。

---

## 0. 一句話結論

三項**今天都做不到**,但三項的地基都已經有,缺的是「接線」而不是「重蓋」:

| 項 | 現況 | 缺什麼 | 做錯的 |
|---|---|---|---|
| 1 記憶提示 | pack 只**讀**記憶餵給 agent,沒有任何一句叫它**寫**;memory.py 沒有寫入指令 | 一段觸發規則進 pack、一個 `memory.py note` 追加指令 | `consolidator` 角色 memory.py 會開票指派、rules.py 不認得;`memory/project` 被納入上限檢查(違反 D-013 補註);「先寫 inbox.md」這唯一的寫入規則躺在沒人載入的 README |
| 2 race | 事件/收件匣索引/AGENTS/SPAWNS 都是 `O_APPEND` 單次 write,安全;票 set/close/round 有 mkdir 鎖;land 有 mkdir 鎖 | 記憶檔、`status.json` 同輪 RMW、`ticket.py create` 的 id 配號、`STATUS.json` RMW、`flaky.jsonl` 沒鎖 | 票檔被三方(board-note / land-ticket.sh / ticket.py)各自 tmp+replace,鎖不共用 |
| 3 看板直吃 | SPAWNS.jsonl 已由 launchd 採樣器每分鐘從 Claude transcript + `agent-*.meta.json` 與 Codex rollout 自動產生 —— **這條路是通的**;AGENTS.jsonl 靠主線手打 `board-note.py agent`,09-09 起沒人打 | 採樣器把**票號**帶進 SPAWNS;看板讀 `WT/events.jsonl`、`WT/reports/inbox/index.jsonl`、`WT/reports/t<n>/<run>/status.json` | 看板押在需要人手抄的 AGENTS.jsonl 上,而不押在自動產生的檔上 |

---

## A. 逐項現況

### A1 記憶提示(agent 開場拿到什麼)

**唯一會起 agent 的入口是 `AC/scripts/auto-fix.sh`**(`apply.sh` 全檔無 claude/codex 字樣;讀 code 推的)。派工文 = `rules.py pack worker --model $MODEL`(auto-fix.sh:206)+ 現場票面/紅榜/副本路徑/交付物(:208-277),經 stdin 餵 `claude -p`(:289-299;tabby 端 `TB/board/config.json:11` 為 `claude -p --model opus --permission-mode acceptEdits --allowedTools …`,實測)。

pack 的組成(`rules.py:203-276`):標頭「先讀這幾份」+ 角色卡節錄 35% + 共用規矩節錄 45% + 剩餘給「你這個模型的記憶(節錄)」;總預算 `DEFAULT_MAX=4096` bytes(:38)。**四段裡沒有任何一句是「什麼時候、寫什麼、怎麼寫回記憶」**(實測 grep `記憶|memory` 在 `docs/DISPATCH-TEMPLATE.md` 只中 :21、:25 兩行,都是「pack 會接上模型記憶」;§8 回報格式 :370-386 七項交付物無記憶項)。

`memory.py` 只有 `check` 與 `consolidate`(實測 `--help`),**沒有 add/append/note**。全 repo 唯一的寫入規則是 `AC/memory/model/README.md:2`「新筆記先寫 `<model>.inbox.md`,整理時併入」—— 這份 README 不進任何 pack、又被 `memory.py:49,165` 排除在上限檢查外。consolidate 確實會併入 `<檔>.inbox.md` 再刪掉(`memory.py:368-377`),所以「inbox 檔」這條路只有下半段,沒有上半段。

四張角色卡(`AC/memory/role/{main,implementer,verifier,opener}.md`,1.7–2.9 KB,實測)與兩份模型記憶(`opus.md` 1259 B、`fable.md` 2129 B,front matter `cap_chars: 2000`)形狀已經符合 D-013:原則 + 票號指路。**但沒有一行寫「何時該寫」**(實測 grep `記憶` 在 role/*.md 只中 README)。

三個明確做錯的地方(讀 code 推的):
1. `memory.py:50,227` 開整理票 role 寫死 `consolidator`;`rules.py:43-56` 的 `WANTED` 與 `:98-100` 的 `ALIASES` 沒有它 → 整理票的 agent 跑 `rules.py pack consolidator` 得 rc=2「不認得角色」。
2. `memory.py:47` `DEFAULT_APPLIES` 含 `memory/project/*.md`,`AC/board/config.json:20-24` 也列;D-013 補註(`docs/DECISIONS.md:82`)與 D-G123 ⑤ 都說 project 層不設限。今天沒炸只因該目錄只有被排除的 README。
3. `sync-to-project.sh` 把角色卡搬到 `<專案>/docs/roles/`、模型記憶到 `docs/roles/model/`(:38,55-56),但 `rules.py:70-76` 與 `memory.py:47` 預設仍找 `memory/*`;專案端沒在 `board/config.json` 補 `rules.roles_dir/models_dir` 與 `memory.applies_to` 就會**零筆通過**,而零筆與「都沒超標」長得一樣。sync 最後印的六條接點(:141-156)沒提這件事。tabby 端實測:`TB/memory/` 不存在,`TB/board/config.json` 無 `memory` 段(D-G123 ④ 落地票 #618 未生效)。

### A2 race condition

平行度事實(實測 grep):`auto-fix.sh` 自己是 `while :` 單線(:392-414),全 repo `scripts/*.sh` 無 `&`/`xargs -P`/`parallel`。**平行全部由主線人工發起**(同時開票、各自 apply/gate,以及主線的 Agent 工具派 Opus/Codex,上限 D-G121 三張 Codex)。所以撞的不是兩支 auto-fix,而是「主線 + 一支 auto-fix + 採樣器」三方。

| 共用檔 | 寫法 | 鎖 | 判定 |
|---|---|---|---|
| `WT/events.jsonl`(event.py:155-160) | `O_APPEND` 單次 `os.write` | 刻意不鎖(:137-142) | 安全(單行 < PIPE_BUF 內原子) |
| `WT/reports/inbox/index.jsonl`、`acked.jsonl`(inbox.py:83-91) | 同上 | 無 | 安全 |
| `<tickets>/AGENTS.jsonl`(board-note.py:60-71)、`SPAWNS/METRICS/USAGE.jsonl`(board-sampler.py:819-833) | 同上 | 無 | 安全;**但 sampler `prune`(:836-869)整份重寫,前提「唯一寫者」在人手動跑 sampler 時破功** |
| `tickets/<id>.json` set/close/round(ticket.py:649,1036,1095) | tmp+replace | mkdir 鎖 `tickets/.ticket.lock` 10s(:60-61,109-148) | 安全 |
| `tickets/<id>.json` **create**(ticket.py:467-503) | `next_id()`→`save()` | **無鎖** | 併發 create 可撞同一 id |
| `tickets/<id>.json` 由 `board-note.py edit_ticket`(:124-141)、`land-ticket.sh:1688` 直寫 | 各自 tmp+replace | **不拿 ticket.py 的鎖** | 三方互蓋一次更新;不會壞檔 |
| `reports/t<n>/<run>/status.json`(status.py:342-357,480-515) | RMW,tmp+replace | 無 | 省略 `--run-id` 時 fallback `latest_run()`(:346,482),同票兩條線寫進同一 run 目錄互蓋 |
| `reports/flaky.jsonl`(status.py:373-379) | `open("a")` 逐行 | 無 | 多行可交錯 |
| `reports/inbox/<票>-<run>.md`(inbox.py:142-146) | `while exists` 加序號 | 無 | TOCTOU,機率低 |
| `memory/**.md`(memory.py:132-136,356-388) | consolidate 整份 RMW + 刪 inbox.md | **無鎖** | 若之後加 `note` 追加到 inbox.md,consolidate 讀→刪之間的 append 會**遺失** |
| `<tickets>/STATUS.json`(board-note.py:74-94) | RMW,tmp+replace | 無 | 兩支 board-note 同跑丟一次更新 |
| main merge/push | — | `WT/land-ticket.lock` mkdir + holder(land-ticket.sh:1157-1200;實測此刻 pid=3105 #623 握著) | 安全 |
| `usage-offsets.json`(sampler :358-369) | tmp+replace | 無 | 兩支 sampler 併跑互退進度 |

結論:**append-only 那條線是對的、也夠**;要補的鎖只有四處(記憶檔、status.json 同輪、ticket create、STATUS.json),其他是「知道就好」。

### A3 看板直吃檔案

看板讀什麼(`TB/scripts/board.py`,實測 + 讀 code):`<tickets>/`(= `~/.claude/tasks/57571b28-…`,實測 617 檔)下 `*.json`、`STATUS.json`(:441)、`AGENTS.jsonl`(:638;唯一必填 `event ∈ start|done|failed|resumed`,選填 ts/ticket/model/agent_id/parent/note/result_summary/tokens/duration_s,未知欄位忽略、壞行 continue)、`SPAWNS.jsonl`(:918-952;`SPAWN_FIELDS` 固定表 + `agent_id`,同 id 多行合併、每格取最後非空)、`METRICS/USAGE/EVENTS/ANSWERS.jsonl`、6 條 git、2 個 health。**不讀** `WT/events.jsonl`、`WT/reports/**`、`inbox`。快取:行程內 5 秒 TTL 整份重讀(含 133 MB METRICS `readlines()`,:879);`timeline()` 不吃快取(:1177)。

寫者:
- `AGENTS.jsonl` 唯一寫者 `board-note.py agent`,**由主線人工下指令**;無 hook(`.claude/` 無 hooks、`~/.claude/settings.json` hooks 空、七個 plist 無 board-note,實測)。**實測 34 行,只有 09-08/09-09 兩天,最後一行 09-09T05:21 #402 start**。後果:總覽 Agents 區今天/本週恆 0;時間軸只剩 transcript 半邊(無票號、無 token)。
- `SPAWNS.jsonl` 由 `board-sampler.py` 寫,launchd `com.franksai.tabby-board-sampler` 每分鐘(`StartCalendarInterval` 60 格),**實測在跑**(mtime 09-22 01:28,11,240 行)。來源:`~/.claude/projects/<slug>/<session>/subagents/agent-<id>.jsonl` + `.meta.json`(取 toolUseId/spawnDepth/model,**description 明文在 meta 裡但刻意不抄**,sampler:69,:628)、Codex `~/.codex/sessions/**/rollout-*.jsonl`(:918)。派工/收尾配對靠父 transcript 的 `tool_use{name:"Agent"}` → `toolUseResult.agentId` → `queue-operation` task-notification(:544-618)。
- `STATUS.json` 也只有 board-note,mtime 09-20,內容落後(`next` 停在 #407-409,實測)。

可直接吃的原始材料(實測,子工作者三):
- `~/.claude/projects/<slug>/<session>/subagents/agent-<id>.meta.json`:≈135 B,`{agentType, description, toolUseId, spawnDepth, model}`,481 份。**最便宜的 agent 索引**。
- `agent-<id>.jsonl`:每行 `type/timestamp/agentId/sessionId/uuid/parentUuid/message`;第一行 `type:user` 的 `message.content` 就是**整份派工 prompt**(票號在裡面);每筆 assistant 帶 `message.usage{input/output/cache_*}`;**沒有 cost/duration**;工具呼叫完整含 `name/input`。`/private/tmp/…/tasks/*.output` 是這些檔的 symlink(實測)。581 MB / 481 份,單檔 0.3–11 MB。
- `WT/events.jsonl`:18 行,`{ts,kind,pid,cwd,+ticket/role/model/attempt/note/--kv}`,`kind` 封閉 26 值(event.py:26-47)。
- `WT/reports/inbox/index.jsonl`:`{name,ticket,run_id,kind,state,what,where,at,page}` —— **本身就是一列待辦**。
- `WT/reports/t<n>/<run_id>/status.json`:`state/run_id/kind(gate|land|auto-fix)/ticket/sha/started/finished/rc/phases[]/failures[{case,kind,file,line,log,excerpt}]/repair_context{…}`,≈8 KB。
- Codex:tabby 端由主線手動 `codex exec … -o RESULT.md > codex.log`(`TB/scripts/astra-review.sh:53` 同型;實測 `claude_workspace/tabby_pool_codex/<名>/codex*.log` 存在),**無 `--json`**,stdout 是散文;結構化的那份是 `~/.codex/sessions/**/rollout-*.jsonl`,sampler 已在讀。
- auto-fix 的 `claude -p` stdout **沒有導向任何檔**(`subprocess.run` 無 stdout 參數,auto-fix.sh:289-308,實測),也沒有 `--output-format`;它留下的可吃物是 `dispatch-round<r>.md`、`fix-t<n>/round<r>/{patch,EVIDENCE}`、status.json、inbox。

診斷:**看板押錯檔**。它把「誰在跑、跑哪張票」押在需要人手抄的 AGENTS.jsonl,而自動產生的 SPAWNS 只缺一格票號;控制流(gate/land/auto-fix/收件匣)已經有結構化、append-only 的檔在 `WT/`,看板一個都沒讀。

---

## B. 改動清單(可拆票;順序 = 依賴)

標示:【AC】= agent-control 改、經 sync 進 tabby `scripts/control/`;【TB】= tabby 看板/採樣器。每項:改哪、介面、誰寫誰讀、鎖處置、驗收。

### B0【AC】修三個做錯的(無依賴,先做,小)
- `rules.py`:`WANTED` 加 `consolidator`(§0.5, 5.5, 8 + 記憶段),`ALIASES` 加 `整理者`。
- `memory.py`:`DEFAULT_APPLIES` 拿掉 `memory/project/*.md`;`check` 對 config 裡出現的 project 路徑印警告並跳過(D-013 補註)。`AC/board/config.json` 同步拿掉。
- `sync-to-project.sh`:結尾接點清單加兩條「專案 `board/config.json` 必填 `rules.roles_dir=docs/roles`、`rules.models_dir=docs/roles/model`、`memory.applies_to`」;`--dry-run` 時若目標 config 缺這三鍵就 rc≠0 提醒。
- 驗收:`rules.py pack consolidator` rc=0;`memory.py check` 對 project 檔不開票(單元測試放 `AC/tests/`);sync 到臨時目錄缺鍵 → 非零。

### B1【AC】`memory.py note` —— 記憶的唯一寫入口(依賴 B0)
- 介面:`memory.py note <model|role|project> <名> "<一行>" [--ticket N] [--by <role>@<model>]`。
  - 目標檔:model/role → `<dir>/<名>.inbox.md`;project → `<dir>/<名>.md` **直接追加**(不設限、不整理)。
  - 一行格式:`- <一句原則> (#N, <YYYY-MM-DD>, <role>@<model>)`;長度上限 300 字元,超過 rc=2;禁止換行;內容含「案例式」關鍵字(例:`當時`、`那次`)只警告不擋。
  - 寫法:`O_WRONLY|O_APPEND|O_CREAT` 單次 `os.write`(與 event.py 同 idiom);**不 RMW 主檔**。
  - 同時 `event.py emit memory.noted --kv layer= name= ticket=`(新 kind)。
- 誰寫:任何 agent(worker/verifier/opener/main)。誰讀:`consolidate`(併入)、`check`(inbox 行數 > N 也算超標,另加 `inbox_max_lines` 預設 20)。
- 鎖:追加不用鎖;**`consolidate` 改成拿 `memory/.lock`(mkdir,同 ticket.py 的 Lock 類)**,在鎖內:讀 inbox → 併入 → 寫主檔 → `os.rename(inbox, inbox.<run_id>.consumed)`(不 remove,留稽核);鎖外 note 仍可 append —— 因為 note 在 rename 後 `O_CREAT` 會開新 inbox,不會丟。
- 驗收:兩個行程各 note 50 行到同一 inbox → 100 行無交錯;consolidate 中途另一行程 note → 那行落在新 inbox 而非消失(測試用 `time.sleep` 卡在鎖內)。

### B2【AC】pack 加「記憶回寫」段(依賴 B1)
- `rules.py`:`pack()` 第五段,固定文字(見 §C,≤ 600 B),**不吃預算比例、先扣**(與 260 B 鷹架同列);四個角色 + consolidator 都帶。`--stats` 印出該段 bytes。
- `docs/DISPATCH-TEMPLATE.md` §8 加第八項交付「記憶:有寫就列 `memory.py note` 的那幾行,沒寫就寫『無』」—— 讓 EVIDENCE 可 grep。
- 驗收:`rules.py pack worker --stats` 總量仍 ≤ 4096 且含「記憶回寫」;`tests/` 加 golden 測試。

### B3【AC】status.py / ticket.py 補鎖(無依賴,可與 B1 平行)
- `status.py`:`cmd_phase`/`cmd_done` 若環境有 `AC_RUN_ID` 則**強制**用它,無 `--run-id` 且無 env 才 fallback `latest_run()` 並印警告;`flaky.jsonl` 改單次 `os.write`。
- `ticket.py`:`cmd_create` 的 `next_id()`→`save()` 包進 `with Lock()`。
- 驗收:兩個行程同時 create → id 不同;同票兩個 done 各帶 run_id → 兩個目錄各自完整。

### B4【AC】event.py 加 agent 事件 + auto-fix 發事件(依賴 B3 的 run_id 紀律)
- `KINDS` 加 `agent.start / agent.done / agent.failed / memory.noted`。
- `auto-fix.sh:289-308` 的 `subprocess.run` 前後 emit `agent.start/done|failed --ticket --model --kv run_id= round= rc= agent=auto-fix`;**同時把 worker stdout/stderr 導到 `reports/t<n>/<run_id>/worker-round<r>.log`**(現在丟掉了)。
- 這是「機器派的 agent」的紀錄;「主線派的 agent」由 B5 採樣器補,兩邊靠 `ticket` + `ts` 對上。
- 驗收:跑 `auto-fix.sh --dry-run`(若無則加)看 events.jsonl 多兩行;log 檔存在。

### B5【TB】採樣器把票號帶進 SPAWNS(無依賴;**這是最省力、收益最大的一張**)
- `board-sampler.py read_meta`(:624-656):從 `description` 用 regex `#?(\d{3,4})\b|t(\d{3,4})` 抽票號 → 新欄 `ticket`;抽不到再看 transcript **第一行**(已在增量讀的 chunk 內)的 `message.content` 前 2 KB。仍**不抄 description 原文**(守 :69 的隱私規矩);多加一欄 `role`(從 description/prompt 認 `verifier|opener|implementer|dispatcher` 關鍵字,認不到留空)。
- `SPAWN_FIELDS`(board.py:920)加 `ticket`、`role`;`join_runs`(:1042-1090)已有「同 agent_id / 同 provider+180 秒」對法,加「同 ticket」一層;**`agent_runs` 在 AGENTS 沒對應 run 時,以 SPAWNS 節點合成一趟 run**(model/started/ended/status 都有,tokens 從 USAGE.jsonl 已能算),讓總覽 Agents 區復活。
- Codex 那半邊:rollout `session_meta` 的 `cwd` 含 `tabby_pool_codex/sol-461` 之類 → regex 抽 `\b(\d{3,4})\b` 當票號。
- 鎖:仍是 append;`prune` 前先檢查 `usage-offsets.json` 的 mtime 距現在 < 90 秒就跳過(粗糙但擋掉「launchd 一輪正在跑」)。
- 驗收:`demo/test_board.py` 加案例:一份合成 meta.json `description:"verifier #623"` + 空 AGENTS.jsonl → `/api/timeline` 節點帶 ticket=623、總覽 Agents 今天=1。實測 SPAWNS 2 MB 重跑一次(`--dry-run`)看 ticket 命中率,寫進 EVIDENCE。

### B6【TB】看板讀 `WT/` 三份控制檔(依賴 B4 定 kind;不依賴 B5)
- `board.py` 新增三個 loader,路徑從 `TB/board/config.json` 的 `reports_dir/events_file` 解析(與 event.py 同一套 `AC_ROOT`/往上找 config 的規則,別再開新環境變數):
  1. `events.jsonl` → 「控制事件」時間軸(kind 白名單 26+4,未知 kind 顯示灰色不丟);
  2. `inbox/index.jsonl` ⊖ `acked.jsonl` → 「收件匣」區塊(state/what/where/page 直接渲染,page 內容以 `<pre>` 顯示、上限 4 KB);
  3. 每票最新 `t<n>/<run>/status.json` → 票頁「最後一輪」格(rc、phases、failures 前 5 條 excerpt)。
- 快取:這三類檔**以 (mtime,size) 為 key 各自快取**,不跟 5 秒整份重讀綁在一起;events/index 用 offset 增量讀(檔是 append-only,可以)。
- 寫者不變(event.py/inbox.py/status.py),看板純讀;`board-note.py agent` 與 `STATUS.json.agents` 標 deprecated(README 一行),不刪。
- 驗收:`demo/test_board.py` 三個案例(合成三檔 → 對應區塊出現;壞行不 500;acked 的不出現);瀏覽器一引擎截圖(D-G89 三層測試)。

### B7【TB】tabby 建 `memory/` 與 config(依賴 B0;對應 D-G123 ④ #618)
- `TB/memory/project/tabby.md`(不設限)、`TB/board/config.json` 加 `memory:{cap_chars:2000, applies_to:["docs/roles/*.md","docs/roles/model/*.md"], consolidators:[...]}` 與 `rules:{roles_dir:"docs/roles", models_dir:"docs/roles/model"}`;`land-ticket.sh` 本身不呼叫 `rules.py pack`(實測 grep 零筆),pack 是經 `auto-fix.sh:206` 帶 `--model $(cfg routing.implement)` 呼叫;tabby 的 `board/config.json` 無 `routing` 段(實測),所以會退到預設 `opus` —— 補 `routing.implement` 一併處理。
- 驗收:`scripts/control/memory.py check` 在 tabby 根跑,`--stats` 顯示掃到 ≥ 6 份檔(不是 0)。

### 順序
B0 → B1 → B2(記憶主線,串行,同一人做最省);B3 → B4(鎖與事件,串行);B5(獨立,可第一天就做);B6(等 B4 定 kind,否則先讀現有 26 kind 也能做);B7 最後。B5 與 B6 是使用者第 3 點的正解,B1+B2 是第 1 點,B3 + B1 的鎖是第 2 點。

---

## C. 記憶更新觸發規則草案 + 寫入協定

### C1 pack 段文字(放進 `rules.py` 固定段;實測 wc -c 見檔尾)

```
## 記憶回寫(D-G123)
只在三種時刻寫,其他時候不寫:
1 同一類錯絆你兩次以上 → 模型層
2 角色卡沒講、這輪靠自己補的規矩 → 角色層
3 跨票都成立的專案事實 → 專案層
寫法:memory.py note <model|role|project> <名> "<一句原則> (#票號)"
一次一行、只寫原則,案例用票號指路;不改主檔、不改別人的行。
超標由整理票的兩個模型併,不是你。收工前在 EVIDENCE 列出你寫的行。
```

### C2 寫入協定
- **誰能寫哪一層**:model 層只能寫**自己模型**的 inbox(`--by` 的 model 必須等於檔名,否則 rc=2);role 層任何角色都能寫任一角色的 inbox(驗證者常替實作者補規矩);project 層任何人直接追加主檔。主檔(`<名>.md`)**只有 consolidate 能改**。
- **怎麼避免同時寫**:inbox 與 project 主檔一律 `O_APPEND` 單次 write(一行 ≤ 300 字元 < PIPE_BUF 4096,POSIX 保證不交錯);consolidate 持 `memory/.lock`(mkdir,10s timeout,holder 記 pid),鎖內讀 inbox → 併主檔(tmp+replace)→ rename inbox 為 `.consumed`;不在鎖內的 note 若撞到 rename 後,會 `O_CREAT` 新 inbox,零遺失。整理票兩個模型**串行**:第一個模型寫 `docs/DISCUSSION-<檔>-<日期>.md` 提案,第二個模型讀提案後才跑 consolidate(D-007 已要求討論檔;缺的是「第二個模型才有鎖」這一句,寫進整理票模板)。
- **超標怎麼觸發**:`memory.py check`(new-session.sh:34 已每 session 跑)改成也數 inbox 行數(> 20 行算超標);超標 → `memory.over_cap` 事件 + 開整理票 role=consolidator、model=`memory.consolidators` 兩個(現有邏輯 :256-264 不動);**加一個觸發點**:`land.sh`/`land-ticket.sh` 落地成功後也跑一次 check(因為 note 多半在票收工前寫)。整理票 pack(B0 加的 consolidator)前言固定加:「只留原則、案例改票號;分歧兩條並列不硬合(D-007);project 層不動」。

---

## D. 派工建議

| 票 | 模型 | 一行理由 |
|---|---|---|
| B0 三處修錯 | Codex sol | 純機械:改常數、加 alias、改印字;驗收是單元測試 |
| B1 `memory.py note` + consolidate 鎖 | Codex sol | 邏輯清楚、有現成 idiom(event.py 的 append、ticket.py 的 Lock)可抄;併發測試用 subprocess 就能寫 |
| B2 pack 記憶段 + DISPATCH-TEMPLATE §8 | **Fable** | 600 B 內的措辭決定 agent 會不會亂寫記憶,這是判斷不是機械;golden 測試由同一票順手加 |
| B3 status/ticket 補鎖 | Codex sol | 兩處各十行內,驗收可自動化 |
| B4 event kinds + auto-fix 發事件/導 log | Codex sol | shell + python 小改;但**驗收要主線親跑一次 auto-fix dry-run** 看 events.jsonl |
| B5 採樣器帶票號 + board 合成 run | Codex sol(採樣器)→ **Opus**(board `agent_runs`/`join_runs` 那半,要看瀏覽器總覽區復活) | 採樣器是 regex + 一欄;看板端要看畫面,D-G122 瀏覽器票派 Opus |
| B6 看板讀 WT 三檔 + 三個區塊 | **Opus** | 新 UI 區塊 + 瀏覽器截圖驗收;loader 部分可先由 Codex 出 patch 再讓 Opus 接 |
| B7 tabby memory/ + config | Codex sol | 建檔與 config;驗收是 `memory.py check --stats` 數字 |
| C2 協定寫進 `AC/docs/MEMORY.md` + D-016 決議 | **Fable** | 決議文;順便裁 project 層是否真的永不整理 |

---

## E. 要主線裁的點

1. **project 層真的不整理?** D-G123 ⑤ 說不設限;但 project 主檔任何人直接追加,一年後會變紀錄不是記憶。建議:不設「上限」但設「每 50 行由整理票分節」,或明說「project 層就是紀錄,pack 只帶最後 30 行」。
2. **role 層誰能寫**:我提案「任何角色可寫任一角色 inbox」;若主線覺得驗證者不該替實作者立規矩,改成只能寫自己角色。
3. **AGENTS.jsonl / `board-note.py agent` 是否直接退役**(B5 之後它的資訊 SPAWNS 都有,差 `note`/`result_summary` 兩格)。留著 = 兩套真相;建議 B5 落地後標 deprecated,一個月無人手打就刪。
4. **sampler 抽票號讀 transcript 第一行**會碰到派工 prompt 內容(雖只做 regex、不落地);若隱私規矩(sampler:69)要嚴守,就只從 meta.json 的 description 抽,命中率會低一截 —— 主線裁哪一邊。
5. **auto-fix 的 `claude -p` 要不要開 `--output-format stream-json`**:能把工具呼叫也進看板,但每輪 log 會多幾 MB;我建議先只導純 stdout(B4),等看板真的要看工具流再開。

---

## F. 證據索引(方便 grep)
- pack 無寫入指示:`AC/scripts/rules.py:203-276`;`AC/docs/DISPATCH-TEMPLATE.md:21,25,370-386`
- memory.py 無 note、無鎖:`AC/scripts/memory.py:62-65,132-136,356-388`
- consolidator 不在 WANTED:`AC/scripts/memory.py:50,227` vs `AC/scripts/rules.py:43-56,98-100,286-290`
- project 層被納上限:`AC/scripts/memory.py:47`;`AC/board/config.json:20-24`;`AC/docs/DECISIONS.md:82`
- sync 目錄分岔:`AC/scripts/sync-to-project.sh:38,55-56,141-156`;`AC/scripts/rules.py:57-61,70-76`
- append idiom:`AC/scripts/event.py:137-160`;`AC/scripts/inbox.py:83-91`;`TB/scripts/board-note.py:26-32,60-71`;`TB/scripts/board-sampler.py:819-833`
- 無鎖 RMW:`AC/scripts/status.py:249-256,342-357,480-515`;`AC/scripts/ticket.py:467-503`;`TB/scripts/board-note.py:74-94,124-141`
- 鎖:`AC/scripts/ticket.py:60-61,109-148`;`TB/scripts/land-ticket.sh:101,1157-1200`
- 看板輸入:`TB/scripts/board.py:111,191,214-248,278-285,441,638-742,918-952,1042-1090,1177-1200,2087-2095`
- 採樣器:`TB/scripts/board-sampler.py:34-69,100-115,358-369,544-656,836-869,892-944`
- AGENTS.jsonl 停更 09-09、SPAWNS 09-22 在跑:實測 `ls -la ~/.claude/tasks/57571b28-2167-4d48-b38d-ec8e2669d577/`
- WT 控制檔:`TB/board/config.json`(tickets_dir/reports_dir/events_file 指向 `../tabby_pool_wt`);實測 `WT/events.jsonl` 18 行、`WT/reports/inbox/index.jsonl`、`WT/reports/t623/20260922-000213-79471/status.json`
- transcript 形狀:`~/.claude/projects/<slug>/<session>/subagents/agent-<id>.{jsonl,meta.json}`;`/private/tmp/…/tasks/*.output` 為其 symlink(實測)
- auto-fix 不存 worker stdout、無 --output-format:`AC/scripts/auto-fix.sh:289-308`
- Codex 無 --json:`TB/scripts/astra-review.sh:53`;`TB/docs/DISPATCH-TEMPLATE.md:49`

---
實測:C1 pack 段 `wc -c` = 478 bytes(含標題行,≤ 600)。
