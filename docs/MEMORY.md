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

| 目錄 | 誰寫 | 規則包(`rules.py pack`)怎麼疊 | 上限 |
|---|---|---|---|
| `memory/role/<role>.md` + `<role>.inbox.md` | 專案裡的任何 session(`memory.py note role …`) | 疊在 `roles_dir` 的正本角色卡**之後**;暫存區只帶**最後幾行**(≤ 600 B,砍了會點名) | 2K / 暫存 20 行 |
| `memory/model/<model>.md` + `<model>.inbox.md` | 該模型的 session(model 層只准寫自己) | 疊在 `models_dir` 的正本模型記憶**之後**;暫存區同上 | 2K / 暫存 20 行 |
| `memory/project/<主題>.md` | 任何 session(`note project …` 直接進主檔) | **不貼內容**,只給一個速查入口(見分層契約「容量」)—— 貼進 4 KB 包會把別的擠掉,要看就 grep | 不設上限 |

`note` 的名稱寫 `X` 或 `X.md` 都落到同一份(只去一個 `.md`);空、`.`/`..`、含路徑分隔、多一個 `.md` 或帶 inbox 後綴(`.inbox.md`/`.inbox`)的名稱 rc 2 `名稱不合法`,推得出本意時附「改用 X」。同層、同名、同正文、同票號再送一次 rc 0 `已有同一條`、不追加;換票號照寫,兩行各帶自己的 `#票號`(#75)。

**同步(`sync-to-project.sh`)一個位元組都不碰 `<專案>/memory/`**,也不再複製 A 的
`*.inbox.md`(A 的暫存區是 A 的,不是規矩)。專案的 `memory.applies_to` 因此要指
`memory/role/*.md`、`memory/model/*.md`,不是 `docs/roles/*.md` —— 指到同步產出物的話,
上限量的是一份專案改不了、下一次同步就蓋掉的檔,而它開出來的整理票沒有人能執行。

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

## 容量與整理:老師帶學生(產品負責人 2026-09-12 裁示;2026-09-21 D-013 擴充)

**上限適用 `memory/model/` 與 `memory/role/`**,不只模型私有那一層;`board/config.json` 的
`memory.applies_to` 是那張名單,名單上即使列了 `memory/project/`,`memory.py check` 也跳過它
(本檔末補註:project 不設上限)。紀錄類文件不受上限管,它們有自己的形狀。容量數字的正文在分層契約「容量」。

**整理由兩個不同的模型討論**(`memory.consolidators`,例如 Fable + Codex astra),或一個明確更高階的模型帶。
**不准單一 session 自己刪自己的記憶** —— 一個 session 最先刪掉的是它自己看不懂的那幾條,而那正是別的模型
看得出價值的那幾條。`scripts/memory.py check` 量到超標時**自動開一張整理票**,票面就指定那兩個模型。


**每一份記憶檔預設上限 2K**(單位:字元,`board/config.json` 的 `memory.cap_chars`;字元是唯一不需要 tokenizer 的決定性量法)。適用 `memory.applies_to` 名單上的 role / model 層(D-013;project 層見補註);**紀錄類文件**(`docs/DECISIONS.md`、`HANDOFF.md`、`docs/review/`)不受這條上限管,它們有各自的形狀(裁示一列一條、交接一天一節)。

理由:這些檔是**每個新 session 的第一口空氣**,多一個字就是每一個 session 都多讀一個字。上限不是為了省,是為了逼人分辨「值得每次都讀」與「查得到就好」。

### 超過上限時發生什麼
1. `scripts/memory.py check`(主線開場跑)量到某檔超過它的上限、inbox 超過行數,或 inbox 裡有來源票已結案的條目(needs-review)→ 發事件 `memory.over_cap`、開一張整理票,**指派給 `memory.consolidators` 那兩個模型**(舊設定只有單數 `memory.consolidator` 時退回一個人,票面會說出來)。一份主檔一張票。**「已有整理票」的判準**:任何一張開著的票(state 不是 Done / Cancelled)、`allowed_write_paths` 蓋得到這份主檔(glob 也算,`ticket.first_match`)就算,**不只 role=consolidator** —— 有就不新開票(consolidator 那一張優先點名,其餘印出它的票號與 role);查核與開票在 `memory/.lock` 裡。**該模型的 session 不因此停工**,只是知道自己的記憶該整理了。
   - 短命角色開場跑 `check --read-only`:同樣的量法與輸出,不開票、不發事件、不拿鎖(#76)。
   - `check-stale --read-only [--file <主檔或 inbox>]` 逐條印 `memory: <inbox>:<行號> <分類> <來源>`;分類 `needs-review`(`#N` 在本 repo 票庫且已 Done/Cancelled)、`fresh`、`unknown-ticket`、`no-source`、`foreign`(`#` 後不是純數字)、`unparsed`。有 needs-review 退出碼 1。
2. 那兩個模型**討論,用 `docs/DISCUSSION.md` 的標準格式,存成 `discussions/<date>-memory-<model>.md`**:哪些要合併、哪些降級成「查得到就好」(搬去 `code-map/` 或 `docs/`,留一行指路)、哪些是反例必須留、哪些已經過期。這一步像老師帶學生:不是替它刪,是幫它分辨。
3. 討論的產出兩種,都要寫回檔案:
   - **壓縮後的新版**(舊版在 git 歷史,不另存)。
   - **或者提高上限**:如果討論結論是「這些每一條都值得每個 session 讀」,上限可以被打破。新的上限與理由寫進該檔的 front matter:
     ```
     ---
     cap_chars: 2600
     cap_history:
       - {date: 2026-09-12, from: 2000, to: 2600, by: fable, reason: "四條 land 事故的反例各不相同,合併會失去可辨識性"}
     ---
     ```
   **提高是掙來的,不是自動的**:理由要寫得出「多讀的那幾百字替每個未來 session 省了什麼」。沒有理由的提高,`memory.py check` 視為未整理。
4. 整理期間的新筆記照樣寫 `<model>.inbox.md`(不受上限),下一輪處置。
5. 收的那一手(#76):
   ```sh
   python3 scripts/memory.py snapshot <主檔>        # 印 source_lines: K / source_sha256: <前 K 行的 sha256>
   python3 scripts/memory.py consolidate <主檔> --candidate <新版> \
       --discussion <模型 A 的討論檔> --discussion <模型 B 的討論檔> [--new-cap N --reason …] [--by …]
   ```
   兩份討論檔的 `model:` 不同、`source_lines` / `source_sha256` 與鎖內重算的 inbox 前 K 行一致、結論區對每一行都有處置、候選正文不超過上限 —— 全部驗過才把主檔換成候選,並只把前 K 行移進 `<inbox>.<run>.consumed`;第 K+1 行以後(整理期間新記的)留在 inbox。任何一項不過:退出碼非 0,主檔、inbox、事件一個位元組都不動。
6. 事件 `memory.consolidated` 記錄整理者、前後大小、上限有沒有變、`source_lines`、**兩份討論檔路徑**與兩個 model。

### 整理的原則
- **產出照分層契約**(`memory/role/README.md`;D-013):整理後的每一條都要過得了它。
- 保留**反例與盲點**優先於保留成功經驗——成功的做法會被範本吸收,盲點只有記憶記得。
- 每條保留「日期 + 來源票號 + 實測/推論」三個標記,壓縮不能壓掉它們。
- 未達共識的討論不寫成事實;**兩個模型的分歧保留在討論檔裡,不硬合**(D-007)。

## 不可以的
- 未驗證的經驗不進共用層。
- 摘要不把未達共識的討論寫成事實。
- 原生工具的記憶(Claude Code auto-memory、Codex 的 AGENTS.md)各自管理;這裡的檔案是**外部保存、按需讀取**,不是取代它們。

**補註(2026-09-21,使用者裁示)**:`memory/project/`(專案共識 = 前人踩坑的經驗)**不設大小上限**,它本來就會長得比較快,之後的人進去 grep 或整份讀都可以;內容照分層契約(`memory/role/README.md`)。有上限、超標要兩個模型整理的是 `memory/model/` 與 `memory/role/`(每個 session 都要載入的那兩層)。
