# 記憶

## 兩層
**每一層記憶放什麼(分層契約)正文在 `memory/role/README.md`**;下表只列位置與誰能寫,「內容」一欄指回那裡。

| 層 | 放哪 | 誰能寫 | 內容 |
|---|---|---|---|
| **專案共用** | `docs/DECISIONS.md`、`docs/HANDOFF.md`、`code-map/`、`memory/project/*.md` | 主線(經裁示)、知識維護 | 裁示、交接、repo map;`memory/project/` 照分層契約 |
| **模型私有** | `memory/model/<model>.md` | 該模型的任何 session | 照分層契約(role / model 層) |
| **專案端**(接 agent-control 的專案) | `memory/role/<role>.md`、`memory/model/<model>.md`(各帶一份 `.inbox.md` 暫存)、`memory/project/<主題>.md` | 那個專案裡的任何 session(`scripts/control/memory.py note`) | 這個專案自己的記憶,分層照同一份契約;規則包在正本規矩之外**疊**這一層 |

### 專案端那三個目錄(2026-09-23,D-021)

`memory/` 永遠是「**這個 repo 自己寫的**」;`rules.roles_dir` 是「**規矩從哪來**」。
兩者是同一個目錄時,**同一目錄就是正本**(agent-control 自己),規則包只讀一次;不同時,
這個 repo 是專案,規則包兩層都疊。一條規則,沒有第二個設定鍵、沒有旗標。

| 目錄 | 誰寫 | 規則包(`rules.py pack`)怎麼疊 | 整理 |
|---|---|---|---|
| `memory/role/<role>.md` + `<role>.inbox.md` | 專案裡的任何 session(`memory.py note role …`) | 疊在 `roles_dir` 的正本角色卡**之後**;暫存區整份帶 | 暫存區批次整理(見下) |
| `memory/model/<model>.md` + `<model>.inbox.md` | 該模型的 session(model 層只准寫自己) | 疊在 `models_dir` 的正本模型記憶**之後**;暫存區同上 | 暫存區批次整理(見下) |
| `memory/project/<主題>.md` | 任何 session(`note project …` 直接進主檔) | **不貼內容**,只給一個速查入口(見分層契約「整理與長度」)—— 貼進包會讓派工文跟著長,要看就 grep | 不整理 |

`note` 的名稱寫 `X` 或 `X.md` 都落到同一份(只去一個 `.md`);空、`.`/`..`、含路徑分隔、多一個 `.md` 或帶 inbox 後綴(`.inbox.md`/`.inbox`)的名稱 rc 2 `名稱不合法`,推得出本意時附「改用 X」。同層、同名、同正文、同票號再送一次 rc 0 `已有同一條`、不追加;換票號照寫,兩行各帶自己的 `#票號`(#75)。

**同步(`sync-to-project.sh`)一個位元組都不碰 `<專案>/memory/`**,也不再複製 A 的
`*.inbox.md`(A 的暫存區是 A 的,不是規矩)。專案的 `memory.applies_to` 因此要指
`memory/role/*.md`、`memory/model/*.md`,不是 `docs/roles/*.md` —— 指到同步產出物的話,
整理的是一份專案改不了、下一次同步就蓋掉的檔,而它開出來的整理票沒有人能執行。

模型私有記憶的重點是**同模型跨 session 共享**:Opus 的下一個 session 應該知道 Opus 上次在這裡犯過什麼。每一條附:日期、來源(票號或對話)、適用範圍(專案 / 角色)、是實測還是推論。

## 記憶不是紀錄(2026-09-21,D-013)
**兩種東西住在不同的地方,而混在一起的那一天,每個 session 都開始為別人的歷史付錢。**

| | 誰載入 | 怎麼讀 | 內容長什麼樣 |
|---|---|---|---|
| **記憶**(`memory/model/`、`memory/role/`、`memory/project/`) | role / model 層 agent **每次開場都載入**;project 層按需 | role / model 整份讀;project grep 或整份讀(本檔末補註) | 照分層契約(`memory/role/README.md`):原則,不是案例 |
| **紀錄**(`docs/DECISIONS.md`、`docs/HANDOFF.md`、`docs/review/`、`discussions/`) | 主線;短命角色**不載入** | **grep 定位,讀那幾行** | 事件原文、裁示來源原話、審查全文 |

一條合格的記憶長這樣:

> 快照層只畫說得出處的畫面(#585)

一條不合格的記憶長這樣(它是紀錄):

> 2026-09-10 #585 快照測試把還沒載入的那一版畫進去,因為 `waitFor` 沒等到 …(下略三行)

**為什麼**:原則每次讀都在用,案例只有寫的那一天在用。案例的價值是「怎麼得出這條原則」,
而那個問題一年問不到一次 —— 一年問一次的東西,用 grep 查,不用每個 session 載入。
票號那一格就是給那一次用的:`grep -n "#585" docs/` 找得回全文。

## 整理:老師帶學生(產品負責人 2026-09-12 裁示;2026-09-21 D-013 擴充;2026-10-19 D-053 ⑥ 改觸發)

**整理適用 `memory/model/` 與 `memory/role/`**,不只模型私有那一層;`board/config.json` 的
`memory.applies_to` 是那張名單,名單上即使列了 `memory/project/`,`memory.py check` 也跳過它
(本檔末補註:project 不整理)。紀錄類文件(`docs/DECISIONS.md`、`HANDOFF.md`、`docs/review/`)不歸這裡管,它們有各自的形狀(裁示一列一條、交接一天一節)。

**整理由兩個不同的模型討論**(`memory.consolidators`,例如 Fable + Codex astra),或一個明確更高階的模型帶。
**不准單一 session 自己刪自己的記憶** —— 一個 session 最先刪掉的是它自己看不懂的那幾條,而那正是別的模型
看得出價值的那幾條。`scripts/memory.py check` 看到暫存區達批次門檻時**自動開一張整理票**(門檻見下「暫存區有條目時發生什麼」)。

**不設固定字數上限(D-053 ⑥,取代 D-006 的 2000 字元)**。判準只有一句:**每個 session 開場都會讀，只留值得這個成本的**;
寫什麼、留多長由整理的 LLM 決定,不給字數目標。這些檔是**每個新 session 的第一口空氣**,多一個字就是每一個 session 都多讀一個字 ——
所以要分辨「值得每次都讀」與「查得到就好」;但用固定數字逼它,會把有用的句子壓掉、讓暫存區的教訓等很久才進卡。

### 暫存區有條目時發生什麼
1. `scripts/memory.py check`(主線開場跑)每份受管主檔印一行 `memory: <主檔> <c> 字元 / 暫存區 <n> 條未整理`(c = front matter 以外的正文字元數,只是報數,不跟任何數字比;n = 同名 `.inbox.md` 的非空行數)。**批次觸發(D-055 ①):≥5 條或最舊一條滿 3 天;每份主檔 24 小時最多一次。** `memory/model`、`memory/role` 底下任何一份 inbox 的未消耗條目 ≥ 5,或其中最舊一條(note 括號裡的 YYYY-MM-DD;沒有可解析日期的條目只算條數、不算年齡)距今 ≥ 3 天 → 發事件 `memory.pending`(`file` = inbox、`entries` = n)、為它的主檔開一張整理票。一份主檔一張票。這份主檔最近一張整理票(role=consolidator、`memory_file` 是它,任何 state 含 Cancelled)的 `created` 在 24 小時內 → 這次不開、不發事件(條目留著,下次再看);開著的整理票照下面的查重。三個數字是 `memory.py` 的常數(`BATCH_MIN_ENTRIES` / `BATCH_MAX_AGE_DAYS` / `BATCH_COOLDOWN_HOURS`),不進 config。**「已有整理票」的判準**:任何一張開著的票(state 不是 Done / Cancelled)的 `memory_file`(整理票專用,`memory.py` 開票時寫)是這份主檔就算 —— 有就不新開票、印出它的票號;查核與開票在 `memory/.lock` 裡。**session 不因此停工**,只是知道有暫存區待整理。
   - 短命角色開場跑 `check --read-only`:同樣的量法與輸出,不開票、不發事件、不拿鎖(#76)。
   - `check-stale --read-only [--file <主檔或 inbox>]` 逐條印 `memory: <inbox>:<行號> <分類> <來源>`;分類 `needs-review`(`#N` 在本 repo 票庫且已 Done/Cancelled)、`fresh`、`unknown-ticket`、`no-source`、`foreign`(`#` 後不是純數字)、`unparsed`。有 needs-review 退出碼 1。整理者用它分類;它不是另一個觸發。
2. 整理者**討論,用 `docs/DISCUSSION.md` 的標準格式,存成 `discussions/<date>-memory-<model>.md`**:哪些要合併、哪些降級成「查得到就好」(搬去 `code-map/` 或 `docs/`,留一行指路)、哪些是反例必須留、哪些已經過期。這一步像老師帶學生:不是替它刪,是幫它分辨。
3. 討論的產出是**整理後的新版**(舊版在 git 歷史,不另存);多長由判準決定。
4. 整理期間的新筆記照樣寫 `<model>.inbox.md`,留給下一張整理票:那張票關了之後,下一次主線開場才再開。
5. 收的那一手(#76):
   ```sh
   python3 scripts/memory.py snapshot <主檔>        # 印 source_lines: K / source_sha256: <前 K 行的 sha256>
   python3 scripts/memory.py consolidate <主檔> --candidate <新版> \
       --discussion <模型 A 的討論檔> --discussion <模型 B 的討論檔> [--by …]
   ```
   兩份討論檔的 `model:` 不同、`source_lines` / `source_sha256` 與鎖內重算的 inbox 前 K 行一致、結論區對每一行都有處置、候選過得了分層 lint —— 全部驗過才把主檔換成候選,並只把前 K 行移進 `<inbox>.<run>.consumed`;第 K+1 行以後(整理期間新記的)留在 inbox。任何一項不過:退出碼非 0,主檔、inbox、事件一個位元組都不動。候選多長不量。
6. 事件 `memory.consolidated` 記錄整理者、前後大小、`source_lines`、**兩份討論檔路徑**與兩個 model。

### 整理的原則
- **產出照分層契約**(`memory/role/README.md`;D-013):整理後的每一條都要過得了它。
- 保留**反例與盲點**優先於保留成功經驗——成功的做法會被範本吸收,盲點只有記憶記得。
- 每條保留「日期 + 來源票號 + 實測/推論」三個標記,壓縮不能壓掉它們。
- 未達共識的討論不寫成事實;**兩個模型的分歧保留在討論檔裡,不硬合**(D-007)。

## 不可以的
- 未驗證的經驗不進共用層。
- 摘要不把未達共識的討論寫成事實。
- 原生工具的記憶(Claude Code auto-memory、Codex 的 AGENTS.md)各自管理;這裡的檔案是**外部保存、按需讀取**,不是取代它們。

**補註(2026-09-21,使用者裁示)**:`memory/project/`(專案共識 = 前人踩坑的經驗)**不整理、不設大小上限**,它本來就會長得比較快,之後的人進去 grep 或整份讀都可以;內容照分層契約(`memory/role/README.md`)。暫存區批次整理的是 `memory/model/` 與 `memory/role/`(每個 session 都要載入的那兩層)。
