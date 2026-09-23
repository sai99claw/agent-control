# D-021(2026-09-23)專案端記憶備忘的唯一去處:專案自己的 `memory/`,規則包疊兩層

設計 session(Fable)。A = agent-control(正本),T = tabby_pool(專案)。
每條結論標「實測」(這一輪讀檔、跑指令看到的)或「推的」(讀 code 推、未跑)。
每個取捨附一行「未來每票省/多花」,粗估、推的。

## 結論(先講)

**丙:專案備忘的唯一去處就是專案自己的 `<專案>/memory/{role,model,project}/`——也就是 `memory.py note` 現在實際寫進去的地方;寫入端不動,改的是讀端與同步端。**

| 層 | 正本(A 的,唯讀、同步來的) | 專案自己的(可寫、進專案 git) |
|---|---|---|
| role | `docs/roles/<role>.md`(`rules.roles_dir`) | `memory/role/<role>.md` + `<role>.inbox.md` |
| model | `docs/roles/model/<model>.md`(`rules.models_dir`) | `memory/model/<model>.md` + `<model>.inbox.md` |
| project | (A 沒有專案層可同步) | `memory/project/<主題>.md` |

規則:**`memory/` 永遠是「這個 repo 自己寫的」;`roles_dir` 是「規矩從哪來」。兩者是同一個目錄時,這個 repo 就是正本(A);不同時,這個 repo 是專案,規則包兩層都疊。** 一條規則,沒有第二個欄位、沒有旗標。

1. **唯一去處**:`<專案>/memory/`。`memory.py note` 一行不改(它已經寫對地方;實測 T/memory/ 8 檔 19 行 = 15 條不同的備忘,D-G128 那句重複 5 次)。
2. **`rules.py pack`** 除了 `roles_dir` 的角色卡與 `models_dir` 的模型記憶,再讀 `memory/role/<role>.md`、`memory/model/<model>.md`(專案層主檔)與兩者的 `.inbox.md` 尾巴,並在「先讀這幾份」列出 `memory/project/*.md` 的路徑(不貼內容)。上限仍 4 KB。
3. **`sync-to-project.sh`** 不再複製 `*.inbox.md`(A 的 inbox 是 A 的暫存區,不是規矩);manifest 只管 `docs/roles/` 與 `scripts/control/`,**永遠不碰 `<專案>/memory/`**——加一條測試釘死。
4. **T 的 `board/config.json`** `memory.applies_to` 從 `docs/roles/*.md` 改成 `memory/role/*.md`、`memory/model/*.md`:上限與整理票管的是專案自己寫的那層,不是同步來、下次會被蓋掉的正本。
5. **那 15 條不用搬**:它們已經在唯一去處,只是 T 沒 `git add`。#648(sonnet)加一行 `git add memory/`。
6. **A ticket(新開,opus)改 A 正本 + 測試 + `docs/MEMORY.md` 一節;T #648(sonnet)只做同步後的接線。** 順序:A #23 → A 新票 → sync → #648。

## 現況(實測,2026-09-23)

- `memory.py note`(A 與 T 副本同一段 code)寫 `<root>/memory/<layer>/<名>.inbox.md`(project 層直接 `<名>.md`);root = 往上找 `board/config.json`。在 T 跑就落在 `T/memory/`。`check` 的 inbox 行數檢查也寫死 `memory/<layer>/*.inbox.md`;只有主檔容量檢查看 `memory.applies_to`。
- `rules.py pack` 讀 `roles_dir/<card>.md` 與 `models_dir/<model>.md`;**整支沒有 `inbox` 這個字**——就算在 A,inbox 的內容也要等整理票併進主檔才會進規則包。所以「沒人讀到」有兩層:路徑錯(T 專屬)+ inbox 本來就不進包(A 全域)。
- `sync-to-project.sh` 用 `memory/role/*.md` glob,所以 A 的 `*.inbox.md` 也被同步到 `T/docs/roles/main.inbox.md`、`model/fable.inbox.md`,檔頭寫「不要改這一份」,而沒有任何讀者。manifest 的刪除只作用在 `$ROLES/` 與 `$CTRL/`。
- T 的 `applies_to = docs/roles/*.md`:`check` 量的是同步來的正本,超標會開一張 T 改不了的整理票。
- A 的 inbox 已經混進 T 的專案事實:`memory/role/implementer.inbox.md` 第 7 行(#642,`test_browsers.GUESTS` 那條)是 T 專屬;第 1 行(#630)雖引 T 票號但內容是 A 通則。反方向(T 的教訓寫進 A)就是甲會制度化的污染。

## 理由

### 為什麼不是甲(寫回 A 再同步下來)
- 專案事實(`verify_strings` 是純字串陣列、`land-ticket.sh` 不跑 safari、`test_browsers.GUESTS`)進 A 的角色卡,**每個接 A 的專案每票都讀一次別人的專案事實**。每票多花:每條約 100–200 B × 專案數;三個專案各 20 條就是每票 ~4 KB 的無效讀取(推的)。
- 寫回 A 要 cd 到 A 的根跑 `note`,worker 在副本裡沒有 A;apply.sh 的 `harvest` 只認自己的根。要做甲得另建上行通道(一支腳本 + 鎖 + 事件),每票多一次跨 repo 寫入,而它解的問題(讓專案教訓進規則包)乙/丙用讀端一段 code 就解。
- 角色卡是 A 的正本這件事沒錯,但**正本說的是「規矩」**;專案教訓是「這個專案的事實」,`docs/MEMORY.md` 兩層表裡本來就叫它專案共用層,住專案。

### 為什麼不是乙原樣(專案本地 inbox 放 `docs/roles/`)
- `docs/roles/` 是同步產出物目錄,manifest 會刪它認為退場的檔;專案自己的 `main.inbox.md` 與同步來的 `main.inbox.md` **同名同目錄**,下一次 sync 直接蓋掉(實測現在 T 就躺著一份同步來的 `docs/roles/main.inbox.md`)。要讓乙成立得在 manifest 加白名單——白名單靜默失效那天沒人知道(rules.py 自己的 docstring 講過同一件事)。
- 丙是乙的骨幹(pack 讀 config + 附加專案層)加上「專案層固定住 `memory/`」這一條,少一個設定鍵、少一段白名單。

### 為什麼 pack 也讀 inbox 尾巴(這一條可單獨否決)
- 現況 inbox 要累到 21 行或主檔超 2K 才開整理票(`inbox_max_lines=20`),之前寫的教訓誰都讀不到。T 的 `main.inbox.md` 8 行裡「落地全套不要跟起瀏覽器的 agent 同時跑 (#642)」這種,少讀一次的代價是一輪落地紅(閘門 + 重派,推的 30–80K tokens、30–60 分鐘)。
- 代價:規則包內給 inbox 一格固定 ≤ 600 B(最後幾行優先,砍了要出聲,沿用 `clip`),**4 KB 上限不變**,所以每票讀取成本 +0,只是角色卡節錄少 600 B(角色卡全文路徑仍在)。每票省:每 5–10 票避開一輪重來(推的)。
- 若否決:改成把 T 的 `inbox_max_lines` 降到 5——每 5 條就一張兩模型整理票(每張推的 50–100K tokens),比讀尾巴貴得多。

### 為什麼專案層(`memory/project/`)只列路徑
- 使用者 2026-09-21 補註:專案層不設上限、可以長。塞內容進 4 KB 包會把它砍到只剩標題;列路徑讓 agent 需要時 grep(D-013 第 4 條)。每票多花:每檔一行 ~60 B。

## 要改什麼(A 正本改,T 靠 sync 帶回)

### `scripts/rules.py`(A)
- 新增 `local_dir(layer)` = 固定 `memory/<layer>`;`pack()` 在 `roles_dir(root) != memory/role` 時多讀 `memory/role/<card>`、`memory/model/<model>.md`,以及兩者的 `.inbox.md`(尾巴 ≤ 600 B);相同時(A 自己)只讀一次,不出現「本專案」小標。
- 「先讀這幾份」多列:`memory/role/<card>` —— 本專案對這個角色的補充(檔在才列)、`memory/model/<model>.md` 同理、`memory/project/*.md` 逐檔一行。
- 預算:角色卡那 35% 在有專案主檔時拆成正本 20% / 專案 15%;模型記憶那一份同理;inbox 尾巴固定 600 B 先扣,與 MEMORY_NOTE 同列。全部仍受最後那一刀與 `size > max_bytes → rc 1` 守住。
- `cmd_roles` 印的「角色卡 memory/role/%s」改印 `roles_dir`(順手,一行)。

### `scripts/memory.py`(A)
- **`note` 不改。** `check` 主檔容量照 `applies_to`;inbox 行數檢查本來就寫死 `memory/`,正好是專案層——不改。
- `open_ticket_for` 開的整理票 `--allowed-write-path` 指 `memory/role/<role>.md`——在 T 是專案主檔,合理;不改。

### `scripts/sync-to-project.sh`(A)
- `copy_dir` 跳過 `*${INBOX_SUFFIX}`(讀 A 自己 config 的 `memory.inbox_suffix`,預設 `.inbox.md`)。舊 manifest 裡的 inbox 條目下一次 sync 走既有「退場」路徑刪掉,並唸出來。
- `--dry-run` 前置檢查多一條:`memory.applies_to` 不得含 `roles_dir` 底下的路徑(專案在量一份自己改不了的檔)。
- 不新增任何對 `<專案>/memory/` 的讀寫;測試釘住「sync 前後 `<專案>/memory/**` 逐位元組相同」。

### `docs/MEMORY.md`(A)
- 兩層表加一列「專案端」:三個目錄、誰寫、規則包怎麼疊;一句「`memory/` 是這個 repo 自己寫的;`roles_dir` 是規矩來源;同一目錄就是正本」。`memory/role/README.md` 同步一句。

### T 端(#648 帶)
- `board/config.json`:`memory.applies_to` → `["memory/role/*.md", "memory/model/*.md"]`。
- `git add memory/`(8 檔)。`docs/roles/*.inbox.md`、`docs/roles/model/*.inbox.md` 由 sync 的退場刪掉,不手刪。
- `docs/DISPATCH-TEMPLATE.md` 第 199 行附近加半句:規則包會疊 `memory/` 的專案層;教訓用 `scripts/control/memory.py note` 寫,落在 `memory/`。

## 那 15 條怎麼遷、由誰遷

- **T/memory/ 的 19 行(15 條)原地不動**,#648 的 sonnet worker `git add memory/` 進主線。這不是整理(沒有刪、沒有合併),不觸 D-013。D-G128 重複 5 次留給第一次整理票併。
- **A inbox 裡的 T 專屬行**(實測 1 行:`implementer.inbox.md` 第 7 行 #642 `test_browsers.GUESTS`;#630 那行是 A 通則,留 A):A 新票的 opus worker 在 T 根以 `scripts/control/memory.py note role implementer "<原句>" --ticket 642 --by verifier@opus` 重寫一次,A 原行**不刪**——刪由 A 下一張整理票的兩個模型做(D-013:單一 session 不刪記憶;搬與刪的界線我不替使用者裁)。判準寫進票面:「內容指名 T 的檔或機制(demo/、test_browsers、verify_strings、land-ticket.sh、TABBY_*)」,不是票號(#630/#648 引 T 票號但內容是 A 規矩)。
- 用 opus 不用 sonnet:一行要判「是專案事實還是通則」,判錯就是把規矩降級成專案事實;量只有 1–2 行,貴不到哪去。

## 驗收怎麼機器驗(fixture)

A 的 `tests/control_harness.py Sandbox` 已能造假 repo;新案例全部用它,**不碰真 T**。

1. `tests/test_rules.py`:假專案 `board/config.json` 設 `rules.roles_dir=docs/roles`、`models_dir=docs/roles/model`;放 `docs/roles/implementer.md`(含 `CANON-ROLE`)、`memory/role/implementer.md`(`LOCAL-ROLE`)、`memory/role/implementer.inbox.md` 三行(`INBOX-3` 在最後)、`docs/roles/model/opus.md`(`CANON-MODEL`)、`memory/model/opus.md`(`LOCAL-MODEL`)、`memory/project/foo.md`。`pack worker --model opus` 期望:四個字串都在、`memory/project/foo.md` 路徑在先讀清單、`INBOX-3` 在、總長 ≤ 4096、rc 0。
2. 同檔:`roles_dir` 不設(= A 自己)→ 輸出沒有「本專案」小標、角色卡內容只出現一次。
3. 同檔:專案主檔不存在只有 inbox → 不印「找不到 memory/role/…」,inbox 尾巴仍在;`--max-bytes 1500` → inbox 那格被砍時砍過那句話點名 inbox 路徑。
4. `tests/test_memory.py`:假專案 `applies_to=["memory/role/*.md"]`,`note role implementer` 21 次 → `check` rc 1、開的整理票 `allowed_write_path` 是 `memory/role/implementer.md`,不是 `docs/roles/…`。
5. `tests/test_sync_to_project.py`:A 側(HERE)有 `memory/role/main.inbox.md`;假專案先放 `memory/role/main.inbox.md`、`memory/project/x.md`,且舊 manifest 含 `main.inbox.md`、`docs/roles/main.inbox.md` 存在。sync 後:`docs/roles/` 沒有任何 `*.inbox.md`、stdout 有「退場 …main.inbox.md」、新 manifest 沒有 inbox 條目、**`<專案>/memory/**` 的 sha256 清單與 sync 前相同**;`--dry-run` 對 `applies_to=["docs/roles/*.md"]` 的專案 rc 2 並點名那一鍵。
6. T 端(#648 票面,一句指令):`python3 scripts/control/rules.py pack main --model fable | grep -c "memory/role/main"` ≥ 1;`python3 scripts/control/memory.py check; echo rc=$?` 輸出不含 `docs/roles`;`ls docs/roles docs/roles/model | grep -c inbox` = 0;`git ls-files memory | wc -l` = 8。

## 併 #648 還是另開 A 票

- **另開 A 票(opus)**:改 `rules.py` 預算分配 + `sync-to-project.sh` + 三個測試檔 + `docs/MEMORY.md`。#648 的 out_of_scope 明寫「要改 A 就另開 A 的票」;而且 #648 的驗收 S1 是「T 副本與 A 逐檔 cmp 相同」,A 沒先改,T 改了就是手改、下一次 sync 被蓋掉——正是 D-018 說的「兩邊各做各的形狀」。用 opus:預算算式與最後那一刀互相牽制,test_rules 有 40 條案例在守,sonnet 改壞的回合比省下的貴。
- **#648(sonnet,已開)追加三件**:config `applies_to` 一行、`git add memory/`、DISPATCH-TEMPLATE 半句 + 驗收第 6 條。加一條前置:A 新票落地後的 sha 才是 #648 的同步目標(票面本來就留了「派工時填」那格)。
- 每票帳:A 票一次性 ~40–80K(opus 實作 + 閘門,推的);之後每票 +0 讀取(4 KB 不變)、每 5–10 票少一輪重來。

## 否決案
- **甲**:見上;另加一條——A 的 inbox 已實測混進 T 專屬行,甲把這種混入變成常態。
- **乙原樣(專案 inbox 住 `docs/roles/`)**:與同步產出物同名同目錄,靠白名單活。
- **pack 直接整份貼 inbox / project 層**:4 KB 包塞不下,砍到剩標題與沒貼一樣;每票多讀 1–3 KB 卻沒讀到重點。
- **新增 config 鍵 `memory.local_dir`**:多一個要對齊的欄位;「`memory/` = 自己寫的」一條規則就夠,D-018 不准加第二個欄位繞路。
- **T 手改 `scripts/control/rules.py` 先擋著**:sync 一跑就消失,#648 S1 會紅。
