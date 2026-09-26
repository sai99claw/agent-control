---
topic: 整理 memory/role/implementer.md 與 implementer.inbox.md(票 #52)
kind: memory-consolidation
parties: [fable / consolidator, opus / consolidator(主線裁示代替 config 的 codex:gpt-6-astra,codex 反覆 429)]
stop:
  max_rounds: 3
  budget: "每方 40K token"
started: 2026-09-27T03:00:00+08:00
---

## 背景
- 票 #52(`tickets/52.json`)主旨寫「23 字元,上限 20」,實際是 **inbox 行數**:`memory.py check` 現在量到
  `memory/role/implementer.inbox.md` **24 / 20 行**(#44 那一行是開票後才進的);主檔
  `memory/role/implementer.md` 1675 / 2000 字元(未超,但併入 inbox 後一定超)。
- 主檔 17 行,inbox 24 行;inbox 有 4 條重複(#29 M18 ×4)、2 條重複(#40 __pycache__ ×2)、2 條測試殘渣(#1)。
- 判準:只留具體原則 / 行為準則 / 思考方式;案例用票號指路;每條帶日期 / 來源 / 實測或推論(D-013)。
- 額外觀察(實測,`rules.py pack consolidator` 輸出第 2 行):D-016「不追求快只看效率」由 rules.py 印在**每個角色包的前言**,
  角色卡再抄一次是白花字元。

## 第 1 輪 — fable
- 主張:**inbox 24 行 → 併成 10 條進主檔,刪 4 條;主檔 17 行合併成 12 段**;先壓,壓不到 2000 才提上限(建議上限 2500,理由見下)。
- 證據等級:讀 code 推的(逐行讀兩份檔)+ 實測(`memory.py check`、`rules.py pack` 輸出)。
- 什麼會讓我改變主意:(a) 對方指出我標「刪」的某條在某張票救過人(給票號)→ 改留;(b) 對方壓完實測 ≤ 2000 → 不提上限。

### 主檔 memory/role/implementer.md(逐行)
| 行 | 處置 | 為什麼 |
|---|---|---|
| L1 標題(預設 Opus / 可派 Codex / 短命沙盒) | **併**(縮) | 「派哪個模型」是派工者的判斷,不是實作者的行為;只留「短命、沙盒副本、不碰主 repo」。 |
| L3 做 | **留** | 行為準則核心;把 inbox #28/#49「副本沒有 .git」併進「--branch 是空閘門」同一句。 |
| L4 不做 | **留** | 全是禁止句;把 inbox #27(夾具預設值)與 #29(守衛量那一段)併進「不放寬斷言 / 守衛要有煞車」旁邊。 |
| L5 交付物 | **留** | 併 inbox #25(`$W` 裡 `diff -ruN base work`)與 #630(測試只寫單元層,verify/** 是驗證者的)。 |
| L6–L8 EVIDENCE 五段 | **留** | 具體格式,少一段下一輪重查;文字可縮。 |
| L9 result 區塊 | **併** 進交付物 | 同屬交付格式;不用獨立一行。 |
| L10 另附 / 長度上限 | **併** 進交付物 | 同上。 |
| L11 信誰 | **併** 進 L3 | 「票面是規格、現況是事實、衝突以事實回報」就是 L3 「回報不硬做」的判準,同一句。 |
| L12 交付前的回歸 | **留** | 原則(綠 ≠ 回歸綠);併 inbox #642 時序窗口要蓋得住 parse/exec(只留原則,150/600ms 用票號指路)。 |
| L13 不准輪詢 | **留** | 併 inbox #26(丟背景就結束回合 = 什麼都沒交)當作理由。 |
| L14 紅了誰修 | **留** | 併 L16(紅在沒動到的檔還是你的)——同一主題「紅的歸屬」。 |
| L15 objections | **留** | 併 inbox D-018 的「太複雜不准繞路(改名 / 第二欄位 / 各做各的形狀)」——繞路就是該寫 objection 的時刻。 |
| L16 | **併** 進 L14 | 見上。 |
| L17 交付後清副本 | **併** 成「副本衛生」一段 | 與 inbox #29(gate.log / env-suspect 被 diff 收進 patch)、#40(base/ 長 __pycache__)同主題:base 一字不動、work 只含改動、交完刪。 |

### inbox memory/role/implementer.inbox.md(逐行;標記 = 我對來源的判讀)
| 行 | 來源 | 處置 | 為什麼 / 標記 |
|---|---|---|---|
| 1 verify/** 是驗證者的 | #630, 09-22, main@fable | **併** 進交付物 | 原則:實作者只寫單元 / 瀏覽器層測試,同名檔落地會打架。實測(#630 打過架)。 |
| 2 「第一條原則」 | #1 | **刪** | 測試殘渣,無內容。 |
| 3 「仍會收這行」 | #1 | **刪** | 同上。 |
| 4 `env \| grep ^AC_` | #7, worker@opus | **併** 與 24 | 同一件事的兩面:AC_* 會帶進副本 shell;重現對不上先看它、副本外自建 git 跑 gate 前 `env -u AC_ROOT AC_TICKET`,否則寫回主 repo。實測(#7、#44)。 |
| 5 D-016 不追求快 | main@fable | **刪**(自角色卡) | rules.py pack 已印在每個包前言第 2 行(實測);角色卡重抄佔 2000 字元額度。原則不丟,只是不重複。 |
| 6 移植舊 patch 不信自動合併 | #13, worker@opus | **留**(縮) | 具體做法:逐檔套、讀 rej 手解、`diff(舊work,新work)` 反向核對上游 delta。實測(#13 靜默掉行)。 |
| 7 瀏覽器案例讓 session 失效只改 token | #642, verifier@opus | **刪**(建議移) | tabby_pool 專案細節(test_browsers.GUESTS、logout-all),不是跨專案的實作者原則;且寫的人是驗證者。建議主線移到 tabby_pool 的 `memory/project/`;本檔留原文以免遺失。推論。 |
| 8 時序案例窗口要蓋得住 parse/exec | #642, verifier@opus | **併** 進 L12 | 原則留(窗口蓋得住受測那一包、負載下驗),150ms/600ms 數字用 #639/#642 指路。實測。 |
| 9 D-018 為未來 token 打算、不准繞路 | main@fable | **併** 進 L15 | 前半(取捨、開票)是主線的;後半「不准繞過去」是實作者該寫 objection 的觸發條件。引用(D-018)。 |
| 10 patch 檔頭 base/… work/… | #25, main@fable | **併** 進交付物 | L5 已寫「只准相對形式」,補「在 $W 裡 `diff -ruN base work`」這個做法。實測(#25 apply.sh 秒退)。 |
| 11 副本沒 .git → git ls-files 的測試必紅 | #28, main@fable | **併** 與 22 | #49 是同一條的完整版(cp -R work + git init 拿乾淨 rc、base 複本對照證明紅來自環境)。實測(#28、#49)。 |
| 12 測試前景 + timeout,丟背景就沒交 | #26, main@fable | **併** 進 L13 | L13 已寫規則,這條是理由(claude -p 不會再醒)。實測(#26 第 2 輪)。 |
| 13 夾具預設值會關掉受測層 | #27, main@fable | **併** 進 L4 | 「不放寬斷言」的變體:夾具預設值要跟受測行為反向檢查一次。實測(#27 第 2 輪)。 |
| 14/15/16/18 守衛量那一段不量整份 | #29 M18, worker@opus ×4 | **併**(四→一)進 L4 | 守衛原則;`--help` 範例行讓變異存活是案例,用 #29 M18 指路。實測。 |
| 17 gate.log / env-suspect 被收進 patch | #29, main@fable | **併** 進副本衛生 | 交付前 `diff -rq base work \| grep '^Only in work'`。實測(#29)。 |
| 19/20 base/ 長 __pycache__ | #40, worker@opus ×2 | **併**(二→一)進副本衛生 | `PYTHONDONTWRITEBYTECODE=1` 或跑完刪。實測(#40)。 |
| 21 起 headless 模型的腳本先給測試替身 | #42, worker@opus | **留**(縮) | 原則「新腳本會起模型 → 先給替身,否則綠路測試真的起模型」跨票成立;DEFAULT_CONFIG / SCRIPT_FILES 細節用 #42 指路。偏 agent-control 專案事實,對方若主張移到 project 層我不反對。實測。 |
| 22 | #49, worker@opus | **併** 與 11 | 見上。 |
| 23 證「同時在跑」要雙向會合 | #51, worker@opus | **留**(縮) | 測試思考方式:單向標記只擋一種串行。實測(#51)。 |
| 24 `env -u AC_ROOT AC_TICKET` | #44, worker@opus | **併** 與 4 | 見上。 |

### 統計
- 主檔 17 行:留 8、併 7、刪 0(L1 縮不算刪)。
- inbox 24 行:留 3(#13、#42、#51)、併 17(收成 9 條)、刪 4(#1 ×2、D-016、#642 session 那條)。
- 併入後主檔預估 **2200–2400 字元**(推論,未實作);壓到 2000 要再削 EVIDENCE 段的措辭。

### 上限立場(若壓不到 2000)
提到 **2500**,`cap_history` 寫:「implementer 是每張票都派的角色;多讀 ~400 字省的是一輪 auto-fix ——
#26(背景跑沒交)、#29(gate.log 進 patch)、#40(__pycache__)、#44(AC_* 寫回主 repo)各因缺一條多跑一輪,
一輪 auto-fix 的 token 遠大於每次多讀 400 字。」推論(沒量過一輪 auto-fix 的 token;對方若有數字請補)。

## 第 1 輪 — opus(fable 互讀 `discussions/2026-09-27-memory-opus.md` 後逐條寫;第二段)
- 對上一輪的回應(逐條;「同意」也寫理由):

### 兩人一致的(同意,理由是…)
| 條 | 兩人判 | 同意的理由 |
|---|---|---|
| inbox 2、3(#1 殘渣) | 刪 | 無內容,兩人都讀原文確認。 |
| inbox 5(D-016) | 刪 | 兩人各自實測 pack 前言第 2 行已印;opus 還指到 `rules.py` 67–70 行,證據比我強。 |
| inbox 4+24(AC_*)、11+21/22(無 .git)、16/17(gate.log)、18–20(__pycache__) | 併成「副本環境」一段 | 同一類「副本 ≠ 主 repo」;opus 的草稿把五條收成一段,比我「副本衛生 + L3」拆兩處好找,採 opus 版。 |
| inbox 6(#13)、13(#27)、23(#51)、14 系(#29 M18 四→一) | 留(壓短) | 都是跨專案的測試 / patch 思考方式;opus 收成「測試與 patch 的寫法」一段,採用。 |
| inbox 8(#642 時序)、9(D-018)、12(#26) | 併進 L12 / L15 / L13 | 位置與理由完全相同;D-018 前半在 design.md 已有(opus 讀到的,我沒查)是額外證據。 |
| 主檔 L9(result 塊) | 縮成指路 | opus 實測 pack 已注入整段 `## 結構化交付(D-017)`(我在 consolidator 包也看到同一段),留一句「見規則包 D-017 段,留空不編」即可,省 ~150 字元。 |

### 分歧(逐條;→ 我的回應)
1. **inbox 10(#25 patch 檔頭)**:opus 刪(L5 已寫相對形式);我併(補「在 `$W` 跑 `diff -ruN base work`」)。
   → **讓步,改判刪**。理由:#25 那次是 apply.sh **秒退**、錯誤訊息明確,worker 當場就知道;多讀這句省不了一輪。**已收斂。**
2. **inbox 7(#642 session 失效)**:opus 抽成原則「破壞性動作只動自己擁有的狀態,不撤共用夾具」併進測試寫法;我刪 / 移 project。
   → **同意 opus 的抽象句留在主檔**(它是跨專案的測試思考方式,與 #27「夾具預設值」同族);
   原文(test_browsers.GUESTS / logout-all)照主線裁示 (b) 列在結論段搬 tabby_pool project 層。**已收斂。**
   opus 問的「該不該整條搬去 verifier 記憶」:實作者也寫 demo/ 層瀏覽器測試,原則兩邊都用得到;原文搬 project 層後兩個角色的包都拿得到,不必再搬 verifier。
3. **inbox 22(#42 headless 替身)**:opus 刪→移 `memory/project/sandbox.md`(那裡已有同一支檔的 #46 規矩);我留(縮)。
   → **同意 opus**。理由:sandbox.md 已有 #46 是實測證據,同一支檔的兩條規矩拆兩處才是浪費;主線裁示 (b) 也定了。原文列結論段。**已收斂。**
4. **inbox 1(#630)放哪**:opus 併「不做」;我併「交付物」。→ **採 opus**,禁止句放不做。**已收斂。**
5. **主檔要不要動**:opus「除 result 外原文不動」;我提 L1 縮(派哪個模型是派工者的事)、L11 併 L3、L16 併 L14、L17 併副本環境。
   → L17 併副本環境 opus 草稿其實已做(「交付前 diff -rq…」在那一段);L16→L14、L11→L3 是零損失的合併(同主題各省一個標題),
   我仍主張做;**L1 縮**我主張做但不堅持(省 ~40 字元)。判準照主線 (a):合併不減少任何一條原則,只省字元 → 少提上限 100。
   **未收斂(小)**:待 opus 表態;若 opus 反對,我以量出的字元數為準,不為此耗第三輪。
6. **上限 2500 vs 2700**:opus 有實測(草稿 889 字元、合併後 ≈ 2400,補三標記 ≈ 2600–2700);我的 2500 是推論。
   → 主線裁示 (c) 對不到票號的標「?」,一個字元,所以補標記遠不到 200–300;再加第 5 點的合併,**我估 2500–2600**。
   **收斂的做法**:上限 = 第三段量出的實際字元數**向上取整到百位**,不超過 2700;不預先定數字。
   `cap_history` 理由採 opus 版(#44 / #26 / #29 / #13 各一輪重跑 vs 每票多讀 ≤700 字元),我第一段列的 #40 併進去。**已收斂(方法),數字待量。**
7. **主檔 13 條的來源票號**(opus 說「需要對方或主線補,不要猜」):
   → 我實測 `git log -S<句子> -- memory/role/implementer.md`:L12 回歸、L13 不准輪詢 → `db32c07`(2026-09-21);
   L14 紅了誰修、L17 清副本 → `b65ee79`(2026-09-21);L15 objections、L16 紅在沒動到的檔 → `82a8b1d`(2026-09-21);
   L11 信誰、L4 守衛煞車 → `84864f3`(2026-09-13,D-008)。L6–L8 EVIDENCE 五段 `-S` 沒對到(標題含反引號,查法問題),標「?」。
   這些是 **commit,不是票號**;我主張來源欄寫 commit sha(可追、非編造),主線若要只認票號就標「?」。**已收斂(不編)。**
8. **opus「改變主意」條件 2**(留的條若 pack 節錄 §1/§3/§5/§7 已講就刪):
   → 實測 `rules.py pack implementer | grep objections|test_defect|count|還原|sha256|任何動詞` **零命中**:
   節錄沒帶 L3 的變異步驟、L4 的「任何動詞」、L15 的 objections 格式與 test_defect 規則,這幾條**不刪**。**已收斂。**
9. **行號對不上**(事實,不影響判):opus 表的 21=#49、22=#42、16=gate.log、18/20=__pycache__;我 `cat -n` 讀到 21=#42、22=#49、17=gate.log、19/20=__pycache__。
   內容一致,只是行號;第三段 consolidate 前以內容(票號)對,不以行號對。

- 主張(第二段後):採 opus 的兩段草稿(副本環境、測試與 patch 寫法)為併入形狀;加我第 5 點的三個零損失合併;上限以量定、≤2700;#42 與 #642 原文列結論段搬 project 層。
- 證據等級:實測(`git log -S`、`rules.py pack implementer` grep)+ 讀 code 推的(字元估算)。
- 什麼會讓我改變主意:opus 量出第 5 點的合併不到 100 字元 → 我放棄 L1 縮,只做 L11→L3、L16→L14。

## 結論(草稿;第三段量完再定數字)
- 結論:inbox 清空,18 條不同內容 → 留 4 / 併 9 / 刪 5(採 opus 計法;我第一段的「刪 4」差在 #42 我原本留);主檔 result 縮指路 + 三個同題合併;上限以實測定、≤2700。
- 採用的證據:`memory.py check`(24/20 行、1675/2000)、`rules.py pack` 兩份輸出(D-016 前言、D-017 整段、§ 節錄無 objections/變異細節)、opus 的 889 字元草稿實測、`git log -S` 的來源 commit。
- 保留的分歧:主檔 L1 標題是否縮(fable 主張縮,opus 主張原文不動)—— 主線第三段裁示採 fable 的三個零損失合併(字元少 = 之後每票少讀,D-018 判準),已照做。
- 產出(第三段實測,2026-09-27):implementer.md 新版 **2640 字元**(12 段,每段帶日期 / 來源 commit 或票號 / 實測或引用;來源全部以 `git log -S` 對到,無「?」);inbox 24 行全部處理完(留 4 / 併 9 / 刪 5,清空後由 consolidate 改名 `.consumed`);上限 **2000 → 2700**(2640 向上取整到百位,等於主線給的天花板),`cap_history` 一列 discussion 指本檔;`memory.py check` rc=0;`memory.consolidated` 事件由 consolidate 發。`ticket.py close 52` 因改動未上主線而拒關(主線走 docs 通道 commit 後再關)。
- **搬 project 層的原文(主線用 `memory.py note` 補,不擋本票)**:
  - tabby_pool project:「需要讓 session 失效的瀏覽器案例,只改這個分頁 localStorage 上那把 token(加一個字元);不要打 logout/logout-all —— 那會撤掉 test_browsers.GUESTS 的共用訪客,--target 下所有 class 共用它 (#642, 2026-09-22, verifier@opus)」
  - tabby_pool project:「注延遲的時序案例,窗口要蓋得住 app 那一包的 parse/exec:150ms 在負載下開不出窗口(前提大聲紅),600ms 兩邊都穩 —— 比照 #639 的 T1 (#642, 2026-09-22, verifier@opus)」(主檔留抽象句,數字在這)
  - agent-control `memory/project/sandbox.md`:「新增會起 headless 模型的腳本(讀 board/config.json 的 *.command)時,先在 tests/control_harness.py 的 DEFAULT_CONFIG 放一支替身命令,再把腳本加進 SCRIPT_FILES —— 缺那一段就退回真的 claude -p,既有的綠路測試會真的起模型 (#42, 2026-09-26, worker@opus)」
- 這次討論教了誰什麼:
  - fable:估字元前先量(opus 量了 889,我用估的);「專案事實 vs 角色準則」的判準是「同一支檔的規矩已在 project 層」——有實測指標就不用推論。
  - opus:(待對方填)
