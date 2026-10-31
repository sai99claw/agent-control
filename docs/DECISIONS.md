# 裁示(現行版)

這份只寫**現在有效**的規矩,依主題分節;每條後面的 D-編號是來源,可以到舊版查原話。
2026-10-31 依 D-084 Q16 整份重寫:舊版逐條紀錄(D-001 到 D-083,共 84 條,含 D-H38)在 git 歷史裡,
`git show 99620a1:docs/DECISIONS.md` 就是重寫前的全文;重寫時的逐條對照在 D-084 那一節。
被推翻或已過時的條文不再列出,要查就看舊版。

新的裁示照舊附在最後(D-085 起),一條一節,附來源原話;半成品、未驗證、讀 code 推的要標出來。
加新裁示時,如果它改掉了上面某一條,同一次就改那一條,不留兩個說法。

---

## 一、判準

1. `docs/VISION.md` 是最上層的判準,只有使用者能改。(D-053 ①)
2. **流程乾淨、互動介面單純優先;在乾淨的做法之間,再比 token 效率。** 行數不重要;遇到例外,先想怎麼改流程,不加分支、旗標或特殊頁。(D-078 ③、D-084 Q4;D-016、D-018 的 token 判準排在其後)
3. 規則落在程式裡,不靠 prompt 記得;規則沒送到就不派工。(D-001、D-035)
4. agent-control 自己的票,開題時要寫它改善 VISION 的哪一條、哪個指標,以及拿掉了哪一道關卡或規則(「加一道就拿掉一道」)。由 A 的 `board/config.json` 的 `devloop.required_fields` 要求開題者必填。(D-053 ④、D-084 Q12)
5. 討論過的東西寫進 agent-control,不只改 Claude 自己的設定。(D-049 ⑥)
6. 先把開發與制度做完,再回產品票。(D-024)

## 二、agent-control 是工具

1. 專案在旁邊 clone 一份 agent-control,直接拿它對專案執行;更新就 `git pull`,不再同步任何東西進專案。介面要定義完善、有彈性。(D-071、D-069 ①)
2. agent-control 的 clone 預設放在專案旁(`../agent-control`),它不依賴自己的位置。(D-072 Q2)
3. 專案的測試由專案自己的測試介面提供(`scripts/testiface.py` 的五個指令,`tests.*` 設定)。(D-072 Q1)
4. 專案設定的版本比 agent-control 新時,拒絕執行、請使用者先 pull。經 `actl` 的每個指令都檢查;直接叫 `devloop/*.py` 不檢查。(D-072 Q4、D-085)

## 三、開發迴圈(devloop)

1. 五支腳本:`ticket_creator`(開題)、`ticket_controller`(無 LLM 的唯一驅動)、`ticket_issuer`(一張票一個)、`regression`(大驗證)、`auto_fixer`(修紅)。何時叫哪一支是固定的,腳本之間不互相輪詢。(D-079)
2. 兩種謹慎度都走 DAG:每張票在 DAG 上標 `verify`,`together`(預設)可以和其他票一起驗,`alone` 只用於系統性的重大變動並寫理由。一起驗的票共用一條整合分支;**一條整合分支就是一個 PR**,用本地標記,不開 GitHub PR。(D-078 ②、D-079、D-080)
3. worker 把自己的分支合進整合分支,衝突由 worker(LLM)解;controller 是腳本,不合任何會衝突的東西。(D-079 確認題)
4. 整組合進後跑**一次** regression(全套,依核心數與剩餘記憶體平行)。紅了由 `auto_fixer` 在整合分支上修,修好再驗;綠了合進 main **並且發版**。每個專案都必須有發版腳本(`devloop.release`),controller 只呼叫它;發版失敗不重試、不回滾,發一頁給你。(D-052、D-074 ②、D-079、D-080 ③;推翻舊版「發版永遠是人授權」)
5. 環境出問題(regression 的 preflight 不過,例如鎖屏、磁碟滿)不算紅:不派 LLM 診斷,停下來發一頁給你,修好環境再接著跑。(D-084 Q3)
6. 修補上限 3 輪:issuer 裡 worker 的修補、auto_fixer 修整合分支,都是 3 輪,跨重啟不歸零;用完就回報主線。issuer 整張票最多 3 小時,每次叫 agent 最多 1 小時。(D-050 ②、D-080 ②、D-084 Q15)
7. 一組裡有一張票卡住,不拖住整組:其他票照樣驗、照樣落地,卡住的票和依賴它的票回報主線。(D-080 ②、D-084 Q15)
8. agent 一律 headless 起動、每輪一個新的,靠票上的回饋交接。(D-067)
9. worker 與驗證者同時開工(驗證者只看票、不看實作,不需要等)。(D-079、D-084 Q2)
10. worker 認為票開錯(或驗證者的案例錯),自己用指令叫開題者;auto_fixer 發現兩張票互相矛盾也一樣。開題者只有兩種結果:改好票面接回(改動為零也算這一種,等於說明票沒錯、照做),或交一題給主線。(D-041、D-054 Q8、D-084 Q7)
11. 落地時由腳本重產程式碼索引(`CODE-MAP.md`,從程式碼本身生成),agent 先讀索引再搜尋;沒有人手改它。(D-038、D-051、D-084 Q11)
12. 狀態只有一份:票庫。其他都只是 log。(D-054 Q5、D-078 ①)

## 四、票與開題

1. 主線把你的需求交給 `ticket_creator`:它帶一串 prompt、參考格式的票和叫起開題者的指令;開題者完成後回報主線。(D-079)
2. **一張票 = 一個用一句話講得完的目的**(就是 `objective`);不暗示開發時長,但要估 token(`token_estimate` 與 `token_basis`)。(D-081)
3. 開題者遇到大方向、商業邏輯、使用者體驗未定的地方,交一題給主線,不自己猜。(D-080 ①)
4. 票上的欄位:`needs_verifier`(沒寫當 true)、`complexity`(`simple` 或 `complex`;complex 的票做完後覆核,simple 的票用較便宜的實作模型)、`effort`(選填,推理強度)。開題者交票前格式由腳本驗,不合就退回。(D-028、D-031、D-047、D-054 Q10、D-075)
5. DAG 是獨立檔案,開題時一定要填;相依的條件是「已落地」或「已合進同一條整合分支」。(D-074 ①、D-079)
6. 票庫有自己的 git:讀之前先更新,push 成功才算寫入;預設是 agent-control 的 sub repo(`pools/<專案>`)。(D-079、D-080 ④)
7. **只有開題者那條路能寫票的內容。** 其他腳本只更新狀態(做到第幾輪、合進了沒)。(D-083)
8. 開題者開票前,先在既有測試與資料上實測票面假設;驗收寫使用者看得到的結果。(D-033、D-H38 ⑥、D-084 Q15)

## 五、角色與模型

1. 每個角色一串有順序的指令,前一個找不到或額度用完,換下一個。票可以指定推理強度。(D-049 ②、D-047、D-084 Q5)
2. 預設:worker 用 opus、推理強度中;簡單票用 sonnet 中;開題者用 opus 超高(xhigh);開題者不准用 Sonnet;整理記憶用 fable,用不到退 opus。worker 想試 `agy` high 時,把它排在 worker 那串的前面。(D-023、D-039、D-049 ②、D-084 Q5)
3. claude 與 codex 的 worker 權限一樣。(D-048)
4. 執行位置(本機 / 雲端)由主線依額度決定。(D-063 ④)
5. 給 astra 的工作要切小批,不能一次給太多。(D-049 補;devloop 尚未實作)

## 六、測試與驗證

1. worker 為自己寫的程式寫單元測試;驗收項目的案例**只歸驗證者**。驗證者的案例一律帶 `T<票號>Acceptance` 這個名字(Python unittest:放在同名的 class),和 worker 的測試不會撞名。(D-009、D-054 Q4、D-084 Q8)
2. 驗證者的案例必須在改動前的程式上紅,而且是紅在自己的斷言、不是 import 或缺名字;issuer 在合併前用腳本實際跑一次確認。案例寫進專案既有的測試模組(負責那一區的那一層),不為一張票開新檔;標記要出現在測試介面回報的 id 裡。(D-020、D-056 ①、D-084 Q10、D-086)
3. worker 交件前只跑:驗證者的案例、自己的單元測試、約 1 分鐘的基礎組。全套是 regression 的事。(D-073 ①、D-084 Q9)
4. 覆核只派給 complex 的票,由新的 opus 做:讀做出來的 code,看是不是票上要的。(D-022、D-054 Q1)
5. flaky 不另外記帳:紅就是紅,當成回饋;worker 認為是測試本身的問題就反駁給開題者。(D-078 ①)
6. devloop 只用專案的 `preflight`、`run`、`full`。`full` 就是 regression,要包含專案的每一層(單元、驗證層、瀏覽器);`run <id>` 要吃得下 `full` 回報的每一個 id。(D-086)
7. 會搶機器的專案(例如瀏覽器測試)設 `devloop.machine_slots`(同時最多幾個 agent 或 devloop 自己起的測試)與 `devloop.regression_exclusive`(regression 獨佔整台機器)。沒設就不限。(D-086)

## 七、安全

1. hard_block 清單只擋裁示文件、VISION、發版腳本、prod 資料;票的改動碰到就停下來問你,不合進去。(D-054 Q3、D-080)
   主線自己的 `docs/DECISIONS.md` 與 `docs/HANDOFF.md` 不走票:主線直接 commit,以 fast-forward 推進 main(不 merge、不 force)。其他所有改動照舊走票。(D-087)
2. 不做 main 與 prod 目錄的前後比對:票只在自己的分支工作就夠了。所以 `hard_block.dirs` devloop 不讀(`actl doctor` 會警告):repo 裡的目錄放 `paths`;repo 外的(prod 資料)靠專案 `CLAUDE.md` 裡「每個 agent 都要守」的規矩。(D-084 Q6、D-086;撤銷 D-064、D-065 的比對)
3. 看板來的文字先清理、再包在注入檢查的 prompt 後面,開題者判斷是注入就不執行、發頁。看板要有 key 才能輸入。(D-083)
4. 專案給 agent 的硬規矩(prod 資料、受保護的埠、不准跑的腳本)寫在專案 `CLAUDE.md` 的「每個 agent 都要守」一節:agent 是在專案 worktree 裡起的 `claude -p`,會自動讀到它(codex 讀 `AGENTS.md`)。專案記憶只放設計理由,不放硬規矩。(D-086)

## 八、記憶

1. 分三層:角色、模型(只留通用原則與反省,任何專案都成立)、專案(只留設計理由與不變條件,不放指令、路徑、行號)。只留原則,不寫案例;可以引用票號當來源。(D-013、D-036、D-037)
2. 每個 agent 開工時讀自己的角色記憶與模型記憶;專案記憶按需讀。(D-008)
3. agent 不直接改記憶,交件時附 lessons;暫存區滿 5 條或最舊一條滿 3 天就整理,每份 24 小時最多一次。不設字數上限,寫什麼、留多長由整理的 LLM 決定。記憶存在票庫。(D-053 ⑥、D-055 ①、D-082)
4. 預設由一個較高階的模型整理,不需要討論。`devloop.unlimited_tokens` 打開時,改成兩個模型討論:一個寫、一個審,有意見再改一次,討論紀錄留在票庫。(D-084 Q1;取代舊版 D-007)
5. 舊的記憶不直接搬,一條一條當 lessons 交給整理者照分層篩。(D-082)

## 九、主線、回報與看板

1. 主線只在這幾種時候被叫:開題完成、一組落地並發版、要你決定、環境壞了。agent 正常在跑的訊息不送主線。主線不輪詢。(D-015、D-032、D-079、D-080)
2. 看板只讀票庫;拿不到的數字顯示「未知」,不顯示 0。看板上的輸入跟你在主線對話是同一個入口:交給開題者;還沒送出的草稿只存在瀏覽器。(D-003、D-017 ②、D-083)
3. 每次叫 agent 記一列成本:角色、票、模型、推理強度、輪次、時間、token(含快取讀、快取寫)、金額、結果;被中途停掉的,從 transcript 補讀;票落地時加總。主線 session 自己的成本目前不記。(D-045、D-076 #124、D-084 Q14)

## 十、待辦(不是規矩,做完就從這裡拿掉)

- 票庫的遠端 repo(A、T 各一個),之後掛成 submodule。
- T 改用 devloop;T 跑完 10 張票後,補每票成本與減法前 30 天的對比。(D-063 ②、D-017 ①)
- astra 切小批(五.5)尚未實作。
- `board/config.json` 的舊鍵(`routing`、`worker`、`opener`、`repair`、`schedule` 等舊迴圈用的)還在 schema 裡、不會報錯;
  下一版設定(config_version 3)拿掉,`actl migrate` 一併刪。`scripts/status.py` 裡舊閘門的部分也還沒瘦身(測試介面只用到它的一小段)。

---

## D-084(2026-10-31)裁示整理的回覆

- 原話:
  「Q1 a 照 D-082,D-007 標成被推翻(建議) / 預設不用討論 agent-control 的config 有個 config: 無限token mode 選這個的化才要求討論
  Q2 a 同時開工,D-H38 ② 作廢(建議:驗證者只看票不看實作,本來就不需要等)
  Q3 a 停下發頁(建議:鎖屏、磁碟滿這類事 LLM 修不了,診斷只是多花 token)
  Q4 a 流程乾淨優先,在乾淨的做法之間再比 token(建議)
  Q5 a 補最小版:每個角色可設一串指令,前一個找不到或額度用完換下一個;票可帶 effort(建議) / worker 我想嘗試agy high, 但預設用opus 中, 簡單票 sonnet中, 開票者 opus 超高
  Q6 b 撤銷,票只在自己的分支工作就夠了
  Q7 a 保留,把它當成「改好票(改動為零)接回」(建議)
  Q8 a worker 為自己寫的內部程式寫單元測試;驗收項目的案例只歸驗證者(建議,我照這個改 prompt) / 2編寫的case 名稱要區別 避免撞在一起
  Q9 a 補(建議)
  Q10 a issuer 在合併前用腳本實際跑一次確認會紅(建議;多一次小測試) / 不過這件事可行嗎?
  Q11 b 保留,devloop 要在合併時重產 / agent 直接讀 code 與 grep 消耗的token 感覺會很多? 索引要是寫在script中生成的
  Q12 a 補:agent-control 自己的票庫,開題者必填(建議)
  Q13 a 補(建議,小改)
  Q14 b 現在全部補
  Q15 a 全部追認
  Q16 c 整份重寫 / 整份重寫+過時的去git舊版找的到」
- 題目來源:84 條舊裁示的稽核(獨立 agent 唯讀稽核、主線抽查):有效 26、部分推翻 27、被推翻 8、devloop 後過時 19、衝突 3。
- Q15「全部追認」涵蓋的主線自裁:D-008、D-019、D-020、D-033、D-041 主線預設、D-045 主線補充、D-049 模型用完停下發頁、D-065 主線裁 Q3–Q5、D-069、D-072 Q1/Q2/Q4、D-072「派 worker 期間不寫 DECISIONS」(devloop 下已無意義,不列入現行)、D-078 主線解讀、D-079 主線解讀、D-080 主線解讀與每次叫 agent 1 小時、D-082 主線解讀、devloop 的預設值(同時 2 個 worker、regression 上限 3 小時)。其中 D-065 的比對已由 Q6 撤銷。
- Q10「可行嗎?」:可行。驗證者的分支就是「舊程式 + 案例」,issuer 在那一版上跑那幾個案例:應該全部紅、而且是斷言失敗(unittest 的 fail,不是 error);名字帶 `T<票號>Acceptance` 的案例才算,所以不會被舊測試干擾。多花一次只跑那幾個案例的時間。
- Q11 主線解讀:索引由腳本從程式碼生成(Python 列出 class / 函式與 docstring 第一行),在落地前一刻 commit 到整合分支上,所以永遠跟 main 一致,票不會因為它衝突。

---

## D-085(2026-10-31)devloop 成為正式迴圈

使用者原話(回覆「A 本身還缺的」三點):「1. 還沒併進 A 的 main … 要切過去需要你點頭 … 2. 用真模型跑過的只有簡單的情況 … 3. A 自己沒有發版腳本 … 這些先做」

1. **真模型實測**(demo 專案,2026-10-31,實測):網頁送需求 → 開題者開 3 張票與 DAG;驗證者案例的紅燈檢查(第 1 輪退回、第 2 輪過);
   同組兩張票改同一批檔案的合併衝突由 worker 自己解;regression 紅 → auto_fixer 修一輪 → 重驗綠 → 落地;記憶整理含兩個模型討論;
   落地重產 code map、發版。實測中修掉的:agent 繼承主線 session 的身分變數(用量記到主線的 session 下)、驗證者案例被退回時
   worker 也被重叫一次(現在只重叫驗證者)、簡單票的 worker 拿到 opus 的模型記憶(現在拿 sonnet 的)、案例模組載入失敗時紅燈檢查的訊息說錯原因。
2. **agent-control 的發版**:`scripts/release.sh`,給落地的 commit 打 annotated tag `a-<日期>-<組>-<短 sha>`(列出票號),有 remote 就推 tag;
   專案照舊 `git pull` 拿 main,要釘版本就用 tag。(推論:A 沒有別的「產出物」,main 本身就是發出去的東西,tag 只是給它名字)
3. **切換**:舊迴圈的腳本(apply / land / gate / auto-fix / review / schedule / ticket / rules / memory / inbox / event 的 CLI 等)、
   舊票(`tickets/`)、角色卡(`memory/role/`)、專案記憶(`memory/project/`)、驗證案例庫(`verify/`)、`code-map/`、舊流程文件刪除,都在 git 歷史裡。
   `actl` 的指令改成 `new` / `run` / `tickets` / `board` / `test` / `check` / `doctor` / `migrate` / `init`。
   SessionStart hook 只印「等你的頁」與票;devloop 起的 agent 不觸發。保留:測試介面(`testiface.py`、`adapter-unittest.py`,
   以及它們用到的 `status.py`、`event.py`)、`project_config.py`、`memory/model/`(新票庫的模型記憶從這裡匯入)。
4. A 的舊票與舊記憶沒有搬進 A 的票庫;A 的票庫在第一次 `actl tickets init` 時建立。(推論:舊記憶大多是舊迴圈的操作細節,已不適用)

---

## D-086(2026-10-31)T 開跑前的九個問題

來源:使用者轉來 T 主線讀 A 的 main(1de150a)後的提問(讀 code 推的,未實跑),原文九題:regression 不跑 `regression` 指令、驗證者案例放哪、
瀏覽器並行、`hard_block.dirs` 沒人讀、專案規矩怎麼送到 agent、舊票、T 自己的記憶、發版失敗後重發、舊設定鍵。

主線的回答(主線判斷;A 的改動走 devloop,8 張票一輪落地,main = dfc0e96,實測只在 Linux):
1. `full` 就是 regression,要包每一層;`regression` / `select` 指令 devloop 不用。→ 六.6
2. 案例寫進負責那一區的既有模組,不一票一檔;T 的「一票一檔」lint 要拿掉。→ 六.2
3. 新增 `devloop.machine_slots` 與 `devloop.regression_exclusive`(`devloop/core/slots.py`,flock)。T 建議 `machine_slots: 2`、`regression_exclusive: true`。→ 六.7
4. `dirs` 是 D-084 Q6 的結果,刻意不讀;`actl doctor` 現在會警告。→ 七.2
5. 專案 `CLAUDE.md` 第一行告訴 agent 主線段落不是給它的,另有「每個 agent 都要守」一節;範本已改。→ 七.4
6. 舊票不匯入(同 A):挑出還要做的寫進一個需求檔,一次 `actl new --request-file`,只付一次開題成本。
7. `actl tickets lessons --layer L --name N --file F`:把一份舊記憶的每條 bullet 排進整理暫存區。
8. `actl tickets release <group>`:對同一個 sha 重跑發版;`devloop.release_timeout_seconds`(預設 3600)。
9. 舊設定鍵可以現在手動刪;`actl migrate` 只處理版本 1 → 2,不受影響。

T 自己決定、主線同意的:`devloop.remote: null`(GitHub 上的 main 由使用者手動推,要自動推再說)、發版只做 `build.sh`、
worktree 放外接碟(絕對路徑)。

---

## D-087(2026-10-31)主線的文件直接進 main

使用者原話:「ok 推」(回覆主線的提議:「主線自己的這兩份文件可以由主線直接以 fast-forward 推進 main,其他所有改動照舊走票」)

- 原因:`docs/DECISIONS.md` 在 hard_block 裡,票不能改;而 main 只由 controller 前進,主線寫的裁示與交接沒有路進 main。
- 規矩寫在 七.1 與 `CLAUDE.md`。只限這兩份;`CLAUDE.md` 這次的一行例外說明隨這條裁示一起推,之後改 `CLAUDE.md` 照舊走票。

