# D-020(2026-09-23)驗證者的案例該長什麼樣、該證明什麼、由誰量綠(設計 session Fable)

題目:驗證者每張票自己搭一套拋棄式參考實作來證明「案例做得到綠」,每張多燒 5 萬到十幾萬 token
(#23 sonnet 340K 自報、#647 約 260K、#642 約 294K)。使用者假設:根因是回歸系統沒有乾淨的案例範本、
案例格式規範不明確。

**全篇結論皆為「讀 code 推的」**;沒有執行任何腳本、沒有動任何 repo。token 數字來自三份 EVIDENCE 自報(實測,但是 agent 自己估的)。

---

## 一、結論(先看這裡)

| # | 裁示 | 未來每票省/多花(推的) |
|---|---|---|
| C1 | **根因排序:主線派工文(a)> 沒把閘門接上 verify-case.py check(d)> 沒範本(b)> 格式不明確(c)**。使用者的假設(b)(c)成立但只占參考實作那筆帳的三分之一;三份派工文**明著或等於明著**要求「有實作時綠」,而拿不到 patch 的驗證者只剩一條路:自己寫實作。 | — |
| C2 | **驗證者只證「乾淨基底紅、而且紅在自己的斷言」**,用工具量(`verify-case.py red <n>`,新子指令),寫進票的 `verify.baseline{stage:"red"}`。**不搭參考實作、不做產品變異、不證綠**。綠由閘門在實作者 patch 進來時用既有的 `verify-case.py check` 量(#22 做好的,現在沒人自動叫它)。 | 每票省 150–200K(參考實作 50–80K + 逐條變異 40–60K + 為了搭實作而多讀的產品檔 30–60K) |
| C3 | **驗證者的變異驗紅整條拿掉**。oracle 不被騙的證據改成三件機器量的事:① 基底上紅在案例檔自己的 `AssertionError`(不是 import / AttributeError / 別的檔炸)② candidate 上綠(閘門量)③ 兩邊案例數相同。實作者對自己單元測試的變異照舊(D-009 那一半不動)。 | 每票省 40–60K;風險:一條「基底紅、候選綠、但判準選錯」的案例會漏過 —— 由 `red_lines`(每條紅的第一行寫進票)給主線覆核時看,成本 <1K |
| C4 | **一份範本 `verify/_template_ticket.py`(A 正本、同步到 T)+ 機器 lint**(`verify-case.py lint`,閘門叫):模組 docstring 固定五欄、`TAGS` 字面、每個 `test_` 的 docstring 首行是驗收編號、禁受保護埠字面與 kill、fixture 只走 `addCleanup`。lint 紅 = 閘門 rc≠0 並指名行。 | 每票省 20–40K(不必讀 2–3 份既有案例檔猜形狀;#23 自報讀了 7 份);lint 每命中一次省一輪退件(~50K) |
| C5 | **閘門接線**:`gate.sh --ticket <n>`(A)/ `land-ticket.sh gate`(T)在回歸層之後多兩步:`verify-case.py lint <verify.files>`、`verify-case.py check <n> --candidate <分支樹>`;`check` 把 `verify.baseline.stage` 從 `red` 升成 `check`。`ticket.py close` 只認 `stage=="check"`(或 `verify_waiver`)。 | 每票多花 0 agent token、閘門多 10–60 秒(案例檔在乾淨副本再跑一次);省掉的是「驗證者等 patch」那條路整個不存在 |
| C6 | **A 放:範本、lint、`red` 子指令、gate 接線、角色卡、`templates/dispatch-verifier.md`、VERIFICATION/SCHEMA 措辭。T 自備:`land-ticket.sh` 的兩行呼叫、`board/config.json` 的禁字清單(埠)、`verify/<feature>/_common.py` 之類的 fixture、`docs/DISPATCH-COMMON-RULES.md` §133 那段(它還在說 VERDICT,過期)。** | 同步一次 ~5K;不同步的話兩邊各長一種形狀(D-018 明禁) |
| C7 | **四張票,順序:#25 文字(sonnet)→ #26 工具(opus)→ #27 閘門(opus)→ T#650 同步+接線(sonnet)**。#25 今天就能派,它是省最多的那一張;#26 沒落地前 #25 給一段過渡指令(貼 `^Ran\|^FAILED\|^(FAIL\|ERROR):` 與 rc,只准基底)。 | — |

---

## 二、診斷:四個候選根因,哪幾個成立

### (a) 角色卡 / 派工文明著要求「在參考實作上綠」—— **成立,主因**

角色卡沒有這樣要求(`memory/role/verifier.md` ②:第二段「拿到指定的 patch 之後」才跑 check)。**派工文有**,三份都有:

- `dispatch/ac-23v/PROMPT.md`(主線寫的,票 #23 那一段,逐字):
  > 每條案例要在乾淨基底 `c9164ed…` 上紅(貼 rc 與紅的條數)、**在你自己的拋棄式參考實作上綠(參考實作不交付,只證明案例做得到綠)**;每條指定一個變異該紅。

  這一句就是 #23 那 340K 裡參考實作 + 13 條變異的來源。驗證者照做了,EVIDENCE §② 寫「在拋棄式參考實作(不交付,只證明案例做得到綠)上 … OK」。
- `dispatch/t647v/PROMPT.md` 與 `t642v`(兩份相同的尾段,逐字):
  > 案例要在沒有實作時紅、**有實作時綠;你拿不到實作者的 patch**,用票面描述的介面寫

  「要證有實作時綠」+「你拿不到 patch」= 只剩一條路。#647 EVIDENCE §2.3:「拿不到實作者的 patch,所以自己在另一顆拋棄式副本(`$W/green`)上 … 寫了一份參考實作」,接著 16 條變異在那顆副本上跑。#642 一樣:「影子實作(我自己寫的,只為了驗轉不轉綠)」,而且在負載下兩個方向各跑三趟。
- 更直接的證據:**t647v / t642v 拿到的是 worker 的規則包**(檔頭 `# 規則包:worker`,角色卡是 `docs/roles/implementer.md`),尾巴才補一段「這一輪的角色:驗證者」。worker 包含 §5「變異驗紅:沒驗紅的測試等於沒有測試」——對一個沒有產品碼可變異的驗證者,這一節唯一能滿足的方式就是先寫出產品碼。`rules.py pack verifier` 是存在的(`WANTED["verifier"]` 只帶 0.5/2/3/5.5/6.4/8,**沒有 §5**),主線沒用它。

**直說:主線的派工文本身就是元兇。** 角色卡設計的兩段式(①寫案例 → 結束;②拿到 patch 再 check)被派工文壓成一段「同時派工、拿不到 patch、但要證綠」。

### (d) 閘門沒有自動量「基底紅 / 候選綠」—— **成立,結構性原因**

`scripts/verify-case.py check`(#22)做的正是這件事:乾淨 ref 副本 + candidate 各跑一次同一份案例、寫 `verify.baseline`。但**只有驗證者被要求跑它**(角色卡 ②、`templates/dispatch-verifier.md` 第 2 樣)。`gate.sh` 的 `regression()` 只叫 `scripts/verify.py --tag …`;`land.sh`、`auto-fix.sh` 不碰 `verify-case`;T 的 `land-ticket.sh` 只在 1362 行叫 `tags-merge`。**沒有任何腳本在實作者 patch 到手之後自動跑 check。** 於是「綠」這一半的舉證責任落在唯一被點名的人身上,而那個人在時間上拿不到 patch。

`ticket.py close`(798–811)要求 `verify.baseline.ok`,更把驗證者逼向「現在就要弄出一份 ok:true」——用 `check --candidate $W/work` 在只有案例沒有實作的 work 上跑會得到 `candidate 上還有 N 條紅 → ok:false` 寫進票。這一點角色卡與範本都沒說怎麼辦。

### (b) 沒有範本所以每人自創 —— **成立,次要**

`verify/example/test_example.py` 是 `assertTrue(True)`,不是範本。三份案例檔各自收斂到同一種形狀(模組 docstring 講五件事、`TAGS` 字面加同一句註解、「介面字串」常數區、PATH 前綴的假 bin、`only()` 藏繼承來的 test_),但每次都是**讀既有案例檔重新推出來的**:#23 自報「讀 7 個既有測試檔全文」,#644/#647 各自抄 `_common.py` 的假 `ps` 形狀。這是 20–40K/票的帳,不是 150K 的帳。

### (c) 格式不明確所以不敢只交紅 —— **部分成立**

沒有一句話說「只交紅就算交件」。相反,三處都把紅與綠寫成一對:SCHEMA §verify.baseline「乾淨主線該紅、candidate 該綠」、EVIDENCE 五段裡「每條驗收的實測輸出」、`import 失敗不算紅` 那條 🩸(它讓驗證者知道「有一種紅不算」,卻沒說「哪一種紅就夠」)。#647 §2.3 的第一句「拿不到實作者的 patch,所以…」就是這種不安的寫照。**但它是派工文(a)的放大器,不是獨立根因**——把(a)那一句拿掉、(d)接上,(c)自然消失;只改(c)不改(a),驗證者仍會照派工文搭實作。

### 帳(#23 自報 340K,拆法讀 EVIDENCE 推的)

| 項 | 估 | 由哪一條裁示省掉 |
|---|---|---|
| 讀產品檔 + 7 份既有測試檔全文 | 80–100K | C4 範本省掉讀既有案例(~30K);讀產品檔為了搭實作那部分(~30K)由 C2 省 |
| 寫 13 條案例(509 行) | 30–40K | 不省(這是工作本體) |
| 拋棄式參考實作 | 50–80K | C2 |
| 13 條變異各套一次、跑、還原 | 40–60K | C3 |
| 兩輪 `discover` 全套(209 秒 ×2)+ 兩版 patch | 20–30K | C2(不需要全套;`red` 只跑案例檔) |
| EVIDENCE + result 區塊 | 10–15K | 不省 |

**目標:每票 60–100K(角色卡上限 60K 原本就是這個量級);現況 260–340K。**

---

## 三、驗證者到底該交什麼證明(C2/C3 的理由)

### 只證紅,夠不夠?

夠,條件是「紅在對的地方」能被機器分辨。今天 `run_cases()` 已經把 import 失敗分開數(`IMPORT_MARKS`);不夠的是另外三種**看起來紅、其實是沒接上**的形狀,#647 自己就撞到一種(D5 第一版在乾淨主線上撿到一個類別而綠——那是判準寫錯,`red` 抓不到這種「該紅卻綠」,但它抓得到「紅在錯的地方」)。分類規則(`verify-case.py red` 與 `check` 共用):

| 紅的形狀(讀 traceback 最後一個 frame 與例外型別) | 算不算「驗到了」 | 印什麼 |
|---|---|---|
| `AssertionError`(含 `self.fail`),最後一個 frame 在 `verify.files` 之一 | **算** | `紅 <案例>: <第一行>` 並寫進 `baseline.red_lines` |
| `ImportError` / `ModuleNotFoundError` / `_FailedTest` | 不算(既有) | `import 失敗(不算紅)` |
| `AttributeError` / `NameError` / `FileNotFoundError` / `TypeError`,frame 在案例檔 | 不算 | `紅在缺符號(不算紅):改成先 assert 它存在` |
| 最後一個 frame 不在案例檔(產品碼或既有測試炸) | 不算 | `紅在別處(不算紅)` |
| `skip` | 分開數(既有) | — |

`red` 的判定:`cases > 0`、算數的紅 ≥ 1、不算的三類 = 0(有就列出來、rc=1、**票不寫**——同 #22「量不到不動票」的原則)。過了才寫 `verify.baseline = {stage:"red", ok:true, files, base_ref, base_sha, baseline:{…, red_lines}}`,`candidate_run: null`。

### 綠由誰量、何時量

閘門。`gate.sh --ticket <n>`(與 T 的 `land-ticket.sh gate`)在 `regression()` 之後:
1. `verify.files` 空 → 印「這張票沒有驗證者案例」(有 `verify_waiver` 才不算缺口),照 #619 那條「宣告了 tags 卻選不到案例 = 缺口」的精神處理;
2. 非空 → `verify-case.py lint <files>`(rc≠0 就停,指名行);
3. → `verify-case.py check <n> --ref <base_sha> --candidate <這一輪的分支樹>`,`stage` 升成 `check`;`ok:false` 就是閘門紅,紅榜寫進 status.json 的 `failures[]`,走 auto-fix 的路(worker 拿到的是「你的實作讓驗證者的第 k 條紅」,或反過來 `test_defect`——那條路 D-014 已有)。

為什麼不在 land 量而在 gate:worker 三輪修復迴圈在 gate,量在 land 等於紅了才發現、多一整輪。

### 變異驗紅要不要留

**驗證者這一側不留。** 理由:
- 它需要一份綠的實作當底,而綠的實作要嘛是實作者的(那時驗證者已經結束了)、要嘛是自己搭的(這就是題目)。
- D-009 說驗證者的獨立性來自「案例是它自己從票面寫的」,變異是實作者證明「自己的單元測試有牙齒」的工具;兩邊本來就分開,是派工文把 §5 塞給了驗證者。
- 「oracle 不能被騙」剩下的風險是「基底紅、候選綠、但判準選到壞掉時碰巧為真的那一個」(DISPATCH-TEMPLATE §5 第二個例子)。這種案例的特徵是**紅的訊息與驗收句對不上**——`red_lines` 把每條紅的第一行寫進票,主線覆核 patch 時順手掃一眼(<1K),比 40–60K 的變異便宜兩個數量級。
- 想要更強:可以由**閘門**在 `check` 之後對 candidate 做一次「把票面 `verify_strings` 所在的那幾行註解掉」的粗變異再跑案例檔(機器做、零 agent token)。這一條**不在本設計裡開票**——它需要一個能安全還原的變異器,而 D-018 要求票寫全;等 C1–C7 落地、量到一次真的漏過再開。

---

## 四、案例格式規範與範本(C4)

### 規範(lint 逐條對應;專案可在 `board/config.json` `verify_lint` 加禁字)

| # | 規則 | lint 怎麼查 | 為什麼 |
|---|---|---|---|
| F1 | 模組 docstring 第一行 `#<票號> <一句話>`,底下固定四段:`## 驗收表`(每列 `A<k> \| 層 \| 輸入/步驟 \| 可觀察輸出 \| 期望值來源`)、`## 介面字串`、`## 怎麼做假`、`## 不做` | `ast.get_docstring` + 正則;四個 `## ` 標題缺一報一 | 期望值來源不從被測程式算(VERIFICATION §測試計畫);下一個人不用讀 code 就知道每條在驗什麼 |
| F2 | `TAGS = [...]` 字面 list、已登記(`TAGS.md` 或 `TAGS.d/`) | 既有(`verify.py`) | — |
| F3 | 每個 `test_*` 方法的 docstring 第一行以驗收編號開頭(`A3`、`D1-2`、`13` 都可,正則 `^[A-Z]{0,2}\d+(-\d+)?\b`);同一檔的編號集合印出來,與票的 `acceptance` 條數對照,缺的**印出來不擋** | ast 走 `FunctionDef`,名字 `test_` 開頭 | 「票面 13 條、案例宣告了 1–13」由機器說,#23 EVIDENCE 用了一段話手證這件事 |
| F4 | 原始碼不含受保護埠字面(專案 config;T 是 `1890[3-5]\|18889\|15349`)、不含 `os.kill` / `killpg` / `pkill` / `kill -`、不含 `TABBY_BROWSER_ENGINES=`(T) | 正則掃原始碼(不是 ast,字串裡也要抓) | 派工文鐵律搬進機器(D-001) |
| F5 | 每一顆 `tempfile.mkdtemp` / `TemporaryDirectory` 同一個語句或下一行有 `addCleanup` | ast(#647 D1 那條規則的形狀,它已經掃全 repo;lint 只掃這一檔) | #647 的起因 |
| F6 | 模組頂層不 `import` 票面新增的符號(拿不到票時 lint 不查;拿得到票就對 `verify_strings` 裡 `def <name>(` 的名字查頂層 import) | ast `Import`/`ImportFrom` 對名字 | 「import 失敗不算紅」那條 🩸 的預防版:與其事後不算,不如寫的時候就擋 |

不做的規則(否決):案例檔長度上限(#647 21 條 996 行是合理的密度)、強制 `_common.py`(小票不需要)、強制每條一個 class(形狀自由)。

### 登記片段最小格式(既有,寫進範本註解即可)

`verify/TAGS.d/<票號>.md`:一行 `- \`<tag>\` — <說明>(#<票號>)`,標籤小寫 kebab。

### 範本全文:`verify/_template_ticket.py`(A 正本;`sync-to-project.sh` 帶到 T 同路徑;檔名底線開頭,`verify.py` 的 `cases()` 不收它)

```python
"""#<票號> <一句話:這一組案例守住哪個行為>

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:設計文件 / 手算 / 既有 golden)
A1 | unit    | <輸入或步驟>                  | <可觀察輸出>            | <期望值從哪來,例:docs/DESIGN-X.md §3>
A2 | api     | POST /api/x {…}                | 200 且 body.y == 3      | 手算:2+1
A3 | browser | 開 #/home,等 ready()          | 主控台沒有 TypeError    | 票面驗收第 3 條原話

## 介面字串(每條斷言靠哪個字串;票面沒定的在這裡定,EVIDENCE 不重抄)
REFUSAL = "擋下:…"          ← A2 斷言 stdout 含它
NEW_FN  = "environment_suspects" ← A1 用 getattr 取,不在模組頂層 import

## 怎麼做假(不上真埠、不起真服務、不殺行程、不鎖螢幕)
<例:PATH 前綴的假 df / 假 ioreg;沙盒 server 綁埠 0;斷線用假輸入模擬>

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不碰 verify_strings 的原字(落地 grep 會命中自己)
"""
import os
import shutil
import tempfile
import unittest

TAGS = ["<tag>"]   # 字面 list;verify.py 用 ast.literal_eval 讀。登記:verify/TAGS.d/<票號>.md 一行 `- `<tag>` — 說明(#<票號>)`

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
REFUSAL = "擋下:"


def a_workdir(case):
    """拋棄式目錄:同一行綁 addCleanup,lint(F5)才問得到它。"""
    where = tempfile.mkdtemp(prefix="t<票號>-"); case.addCleanup(shutil.rmtree, where, True)
    return where


class TheBehaviourUnderTest(unittest.TestCase):
    """一個 class 守一條驗收(或一組同 fixture 的驗收);名字寫成一句話。"""

    def test_a1_the_new_symbol_is_reached_through_getattr(self):
        """A1 讀端正規化函式存在且對空輸入回 []。

        乾淨基底上這一條要紅在下面第一個 assert(缺符號),不是紅在 import。
        """
        import scripts.status as status                     # 既有模組可以在這裡 import
        fn = getattr(status, "environment_suspects", None)
        self.assertIsNotNone(fn, "status.py 還沒有 environment_suspects()")
        self.assertEqual(fn({}), [])

    def test_a2_the_guard_refuses_and_names_the_next_step(self):
        """A2 低空間時秒退,stdout 含拒絕句與下一步。"""
        where = a_workdir(self)
        out = "<在 where 裡起假世界,跑被測腳本,收 stdout>"
        self.assertIn(REFUSAL, out)
```

(範本 ≤ 60 行;A1 示範「先 assert 符號存在」——這就是 F6 與「import 失敗不算紅」的正解;A2 示範 fixture 綁 `addCleanup`。)

---

## 五、兩個 repo 誰放什麼(C6)

| 東西 | A(正本) | T(同步帶回 / 自備) |
|---|---|---|
| `verify/_template_ticket.py` | 放 | `sync-to-project.sh` 新增一條 copy(同路徑) |
| `verify-case.py red` / `lint` / `check` 的 `stage` 與 `red_lines` | 放(`SCRIPT_LIST` 已含 `verify-case.py`) | 自動到 `scripts/control/verify-case.py` |
| 閘門接線 | `scripts/gate.sh --ticket` 兩行 | **自備**:`scripts/land-ticket.sh gate` 兩行(它是專案腳本) |
| lint 禁字清單 | `board/config.json` `verify_lint.forbid`(預設:kill 家族) | **自備**:加五個埠 + `TABBY_BROWSER_ENGINES=` |
| 角色卡 `verifier.md`、`templates/dispatch-verifier.md`、`docs/VERIFICATION.md`、`tickets/SCHEMA.md` 的 `verify.baseline` 段、`docs/DECISIONS.md` 新條 | 放 | 角色卡由 sync 帶到 `docs/roles/`;`docs/DISPATCH-COMMON-RULES.md` §133「驗證者交付 = … + VERDICT」**過期,自備改** |
| `rules.py WANTED["verifier"]` | 不動(已經沒有 §5);但 **`docs/DISPATCH-TEMPLATE.md` §9 加一行「驗證者一律 `rules.py pack verifier`,不准用 worker 包」** | 同步 |
| fixture / `_common.py` / 假 bin | — | 自備(專案知識) |

D-018 檢查:兩邊只有一種 `verify.baseline` 形狀(`stage` 是同一格的升級,不是第二格)。

---

## 六、票(C7)

| 順 | 票 | 內容 | 模型 | 一行理由 | 估 |
|---|---|---|---|---|---|
| 1 | **#25** 驗證者只證紅:派工範本、角色卡、VERIFICATION、SCHEMA 措辭 + `verify/_template_ticket.py` + `sync-to-project.sh` 多一條 copy + DECISIONS D-020 | sonnet | 文字與範本本設計已給全文,是搬字;但跨六個檔、每句要對得上既有措辭,haiku 會漏 | 40K |
| 2 | **#26** `verify-case.py`:`red` 子指令、四類紅的分類、`red_lines`、`stage`、`lint`(F1–F6)、`ticket.py close` 認 `stage=="check"`;`tests/test_verify_case.py` 補案例 | opus | traceback 分類與 ast lint 有邊界(subTest、`_FailedTest`、多檔 traceback),要自己設計 fixture;sonnet 做這種會把「紅在別處」判成算數 | 120K |
| 3 | **#27** `gate.sh --ticket`:lint → check 接線,status.json 收 baseline 紅榜,`tests/test_gate.py` 沙盒案例 | opus | shell + 沙盒 repo 的整合測試,gate.sh 已 600 行、有 auto-fix 分支要繞開 | 100K |
| 4 | **T #650**(**已開**,tabby #650)同步 A 到 T + `land-ticket.sh gate` 接線 + `board/config.json` 禁字 + `DISPATCH-COMMON-RULES.md` §133 + 把 #647/#644/#642 三份既有案例過一次 lint(不改內容,只補 docstring 首行編號) | sonnet | 鏡像 A 的 diff,形狀已定;lint 對既有檔的補丁是機械的 | 60K |

**依賴**:#25 獨立(今天可派,`verify-case.py red` 未落地前派工文寫過渡指令:`cd $W/base-with-cases && python3 -m unittest <module> -v`,只貼 `^Ran|^FAILED|^(FAIL|ERROR):` 與 rc,**不准搭實作、不准變異**);#26 獨立;#27 依賴 #26;#650(**已開**,tabby #650)依賴 #25–#27 全落地。

**驗證者**:#25 `verify_waiver`(純文字 + 範本檔,lint 落地後 #650 會對範本本身跑一次);#26 / #27 有驗證者(sonnet),**用本設計的規矩派**——它們是第一批只交紅的票,派工文照 #25 改好的範本。

---

## 七、否決案

| 案 | 為什麼不 | 帳 |
|---|---|---|
| **只補範本與格式,不動派工文** | (a)是主因;派工文那一句還在,驗證者照樣搭實作 | 省 20–40K,漏 150K |
| **驗證者等實作者 patch 再跑 check(串行派工)** | 角色卡本來就是這個設計,但派工是並行的且沒有人會回頭叫它;等 = 一個活著的 agent(D-010 的教訓),或主線多一次派工(~30K/票);閘門量是零 agent token | 多 30K/票 |
| **實作者寫回歸案例、驗證者只審** | D-009 明禁(oracle 不獨立) | — |
| **驗證者保留 1–2 條變異(對案例自己的 fixture 動手,證明紅來自那句斷言)** | #642 M1 那種確實有價值,但它證明的東西 `red_lines` 已經給人看;留一條就會長回十條(#647 16 條、#23 13 條都是「每條一個」的滑坡) | 省 40–60K |
| **lint 用 `verify.py` 做(兩邊各一份 verify.py)** | T 的 `scripts/verify.py` 是專案自有(帶 #568 標記),改兩份就是兩種形狀(D-018);放 `verify-case.py`(SCRIPT_LIST 同步)兩邊只有一份 | 同步 0 vs 每次改兩份 |
| **範本放進票面(開題者每票貼一份骨架)** | 每票多 2–3K 票面、且會漂;檔在 repo 一份 | — |
| **在 land 而不是 gate 量綠** | 紅了才發現要退回 worker,多一整輪 | 多一輪 ~100K |
| **把「該紅卻綠」也交給機器(對 candidate 做自動變異)** | 需要安全還原的變異器;先量一次真的漏過再開票,票要寫全(D-018 允許「先開票之後做」但這裡連症狀都還沒發生) | — |

---

## 八、要改的原句(給 #25 用)

`memory/role/verifier.md` ②,改成:
> ② 寫完案例 —— 跑 `python3 scripts/verify-case.py red <票號> --candidate <$W/work>`(它自己做乾淨基底副本、覆上你的案例、只跑一次;算數的紅只有「案例檔自己的 AssertionError」,import / 缺符號 / 別處炸的紅會列出來、不算、票不寫)。rc=0 才算交件。**綠不是你的事**:閘門在實作者 patch 進來時用 `verify-case.py check` 量,`ticket.py close` 只認那一趟。**不搭參考實作、不做變異、不等 patch。** 交付物 `verify-case.py extract`。然後結束。

`templates/dispatch-verifier.md` 第 2 樣,整段換成上面那句;第 1 樣加「照 `verify/_template_ticket.py` 的形狀,每個 `test_` 的 docstring 第一行是驗收編號」。

`docs/DISPATCH-TEMPLATE.md` §9(專案特有)加:「驗證者派工一律 `rules.py pack verifier`;派工文裡**不准出現**『有實作時綠』『參考實作』『每條一個變異』這幾個字,看到就是舊文。」

`docs/VERIFICATION.md` §流程第二段:把「第二段(拿到指定的 patch 之後)」改成「第二段(閘門做)」。

`tickets/SCHEMA.md` `verify.baseline`:「由 `red`(驗證者,stage=red)與 `check`(閘門,stage=check)寫同一格;close 只認 check」。
