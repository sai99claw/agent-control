# 角色記憶(角色卡)

session 開場讀兩份:`memory/model/<model>.md`(這個模型會怎麼錯)與 `memory/role/<role>.md`(這個角色做什麼、不做什麼、交付物、信誰的證據、上限)。派工 prompt 只寫「你是 <role>,讀 memory/role/<role>.md」加這張票獨有的四件事(票號、base sha、副本路徑、回報對象)。

**短命角色到底載入什麼(2026-09-21 對齊 `CLAUDE.md` 與 `docs/SESSION-START.md`,三份說同一句話):**
`memory/role/<role>.md` + `memory/model/<model>.md` + `docs/DISPATCH-TEMPLATE.md` + **這張票** + 票的 `decision_refs` 指到的那幾條裁示。
**不載入**:`docs/HANDOFF.md`(那是主線的交接)、整條事件流、開著的票清單。
需要查的東西**用 grep 定位、讀那幾行**,不整份讀。

**這個目錄是誰的(2026-09-23,D-021)**:`memory/` 永遠是「這個 repo 自己寫的」,
`rules.roles_dir` 是「規矩從哪來」;兩者是同一個目錄時,**同一目錄就是正本**(agent-control
自己),不同時這個 repo 是專案,規則包會把專案的 `memory/role/<role>.md` 與 `<role>.inbox.md`
的最後幾行疊在正本角色卡之後(細節見 `docs/MEMORY.md`)。

## 分層契約(唯一一份正文;`CLAUDE.md`、`docs/MEMORY.md`、`memory/project/README.md` 只指路到這裡)
- **role / model 層**(角色卡、模型卡與各自的 `.inbox.md`):只存**通用原則與自身反思** —— 這個角色做什麼、不做什麼,這個模型會怎麼錯。
- **project 層**(`memory/project/`):只存**專案的設計理由與不變條件**。
- **具體命令、函式與符號、路徑、行號、旗標、易變事實** → repo map(`python3 scripts/repo-map.py query <主題>`)或 reference 文件;記憶裡至多留一句指路。
- **事故經過** → 票與 review 歷史(`docs/review/`);記憶只留由它得出的原則,用票號當來源。

**機械檢查**:`python3 scripts/memory.py lint [--file <記憶檔>]… [--base <ref>] [--json]`(唯讀;一份都沒掃到 rc 2)。rule id 五條:`path` / `line` / `call` / `flag` / `sha`;project 層放行 `path`,其餘各層五條都擋;`README.md` 是契約文件,不掃。誤判的例外寫在**同一行**、只豁免一條規則的一個逐字值:
`<!-- memory-allow rule=<rule id> value="<逐字值>" reason="<理由>" ref="<docs/DECISIONS.md 裡找得到的編號>" -->` —— 不准萬用字元、不准整檔豁免,例外會列在輸出裡。閘門帶票跑時只對**分支改到的**記憶檔(不含 `README.md`)跑 `lint --base`:只量本分支**新增**的行(既有正文的命中歸整理票),另擋本分支讓卡從上限內推到上限外(D-042,#90);非零就擋。lint 是語法近似:抽象過的錯層它抓不到,仍由覆核者看。

**容量**(程式現值;D-006 / D-013 / D-021):
- 角色卡與模型卡主檔:正文 ≤ 2000 字元(`board/config.json` 的 `memory.cap_chars`;front matter 不算)。要提高只能經兩個模型討論、寫進該檔 front matter 的 `cap_chars` / `cap_history`(流程見 `docs/MEMORY.md`);`memory.py check` 量。
- `.inbox.md`:非空行 ≤ 20 行(`memory.inbox_max_lines`,沒設就是 20)。
- `memory/project/`:不設上限、`check` 不量(D-013 補註);按需讀(grep 或整份讀);專案的規則包不貼它的內容,只給一個速查入口(有 `docs/CODE-MAP.md` 指它,沒有就指 grep `memory/project/`)。
- 規則包(`rules.py pack`):≤ 4096 bytes;專案端暫存區的尾巴在包裡 ≤ 600 B(D-021)。
- 角色卡每張 ≤ 40 行:文字規矩,程式不量;超過就是把流程文件抄進來了。

原則:**每個主張只被證明一次**,載體是實作者的證據帳 `EVIDENCE.md`;上游不重做下游已證明的事。
來源:tabby_pool D-G114(2026-09-13),Astra 效率審 `docs/review/20260913-efficiency/`。
