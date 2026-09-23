# D-019(2026-09-23)`environment_suspect` 只有一種形狀:一個 list,每一筆標來源

> 設計 session(Fable)裁的;實作票 #23。每一條結論都標「實測」(讀了兩個 repo 的檔、數了 reports/)或
> 「讀 code 推的」(沒有跑)。**本文件沒有改任何 repo 檔、沒有跑任何指令**。
> 使用者 2026-09-23 補的原則:**設計為未來的 token 消耗打算,不准因為眼前複雜就繞過;太複雜可以「先開票、之後做」,
> 但票要把問題完整寫下。** 每個取捨底下都有一行「未來每票省/多花」(粗估,推的)。

## 結論(唯一形狀)

`status.json` 頂層一格 `environment_suspect`,**永遠是一個 list**;每一筆是同一組七個鍵,一個都不缺、值缺料就 `null`:

```
"environment_suspect": [
  {"source": "statistical",            # "statistical" | "declared"(只有這兩個字面)
   "engine": "safari",                 # 小寫引擎名;認不出就 ""
   "why": "localStorage id=<id> empty after <n> seconds",   # 一句人讀的理由(見下)
   "count": 8,                         # 這一筆代表幾次觀測:statistical = 連紅條數;declared = 1
   "threshold": 8,                     # statistical = 觸發門檻;declared = null
   "log": "gate.log",                  # 證據住在哪一份 log(done --log 傳進來的路徑,原樣)
   "line": "AssertionError: localStorage id=ab-1 empty after 101 seconds"}   # 逐字的證據那一行
]
```

- **空值只有一種寫法:`[]`**。`start` 就寫 `[]`(現在 A 的 `start` 根本沒這一格;`done` 沒環境檔時寫 `{}`;
  檔頭 docstring 說 None —— 同一個「沒有」三種拼法,**實測** `scripts/status.py:125,483-488,671,698`)。
- `source` 是**每一筆**的欄位,不是頂層分兩格。`statistical` = 由紅例統計推出來的(#7 的 `EnvironmentResult`:同一引擎、
  同形訊息連紅達門檻);`declared` = 跑在裡面的案例自己宣告的(#644 的 `ENVIRONMENT-SUSPECT: <引擎> <為什麼>` 那一行)。
- `why` 兩種來源各自的定義:statistical = 去掉數字與 id 的**訊息形狀**(原 `message_shape`,改名);declared = 那一行前綴與引擎名之後的**原樣文字**。
  `line` 兩種來源各自的定義:statistical = 觸發門檻那一條紅的**原始**(未正規化)訊息第一行;declared = 整行原樣(含前綴)。
- **`state` 與 `rc` 不因這一格而變**(沿用 T 的裁示⑤與 A 的「只記不判」):`state == "env_suspect"` ⇔ `rc == 86` ⇔ run-tests
  當場中止了那一段,**必然**帶至少一筆 `statistical`;反過來,`declared` 筆可以出現在 `state == "done"`、rc 1 的檔裡。
  「這一段被中止」與「這一趟有人懷疑環境」是兩件事,前者記在 `state`,後者記在這一格。
- 排序:照 `done --log` 的順序、同一份 log 內照行序;**一行一筆,不合併**(T 現有案例要求兩份 log 各記一次,
  `demo/test_control.py:484-491`,實測)。
- **declared 那一行只認行首**(去掉前導空白之後以前綴開頭;#23 第 2 輪補的)。本文件上面說「搬 T 的
  `parse_environment_suspects`」,而 T 那一版用的是 `find`(前綴可以在中段)—— 那條切法在 A 這一側會把**在講**那一行的字
  讀成**印出**那一行:#23 第 1 輪實測,`unittest -v` 把驗收 6 案例 docstring 的第一行印進 `gate.log`,而那一行裡逐字寫著前綴,
  於是被讀成一筆 `engine="firefox"` / `why="假的 ... ok"`,接著「這一格非空就不自動派」照著把那一輪的 auto-fix 擋掉
  (狀態檔 `reports/t23/20260923-123307-89768/status.json`)。**位置是唯一分得開兩者的東西。**
  兩道守衛一起:(a) 解析只認行首;(b) `tests/` 的 docstring 不准逐字寫出前綴(要提就用 `status.SUSPECT_PREFIX` 拼)。
  T 那一側不受影響 —— 它的兩個產出點都是 `print` 自己起一行(`demo/test_browsers.py:1293,1311`),而它自己的守衛
  (`demo/test_browsers.py:12930`)本來就用 `startswith`(實測,讀 T 的檔)。A 的 `run-tests` 由 `LogStream` 在 runner
  停在半行(`verbosity=2` 的 `test_x (…) ... `)時補一個換行,讓案例印的那一行落在行首、`line` 也才是整行原樣。
- 事件 `env.suspect`:**只要這一格非空就發一則**(現在只有 rc=86 才發),kv 改成 `rows=<筆數> sources=<逗號> engines=<逗號> why=<第一筆>`。

未來每票省/多花(推的):形狀本身 0 token;它省的錢在下面「auto-fix 不派」那一條。

## 理由

1. **同一件事有兩個來源,而且會同時出現。** A 的 dict 只裝得下「第一個達門檻的形狀」(達了就中止),T 的 list 裝的是案例逐條宣告。
   #645 之後 T 的一趟可以同時有「safari session 斷了」(declared)與 firefox 連紅同形(statistical,T 之後接 run-tests 就會有);
   一格只能放一筆的形狀在那一刻得選一個丟掉。list 不用選。
2. **空值要只有一種。** `metrics.py:202` 用真值分類 env/product(實測),`[]`、`{}`、`None`、缺格四種在真值上都是假 ——
   今天剛好沒炸,是運氣不是設計。一種空值,一條斷言就驗得完。
3. **鍵固定、值可 null,不要「有沒有這個鍵」當語意。** 看板與 metrics 之後要畫 engine/why,對每一筆做 `.get` 分支是 bug 的溫床;
   七個鍵固定,讀的人只判 `source`。
4. **鍵名取 T 的超集合(engine/why/line/log),A 的 message_shape 併入 why。** T 端 `test_control.py:484-513`、
   `verify/land_preflight/test_ticket_644.py:288-360` 只斷言這四個鍵與 `[]`(實測)—— 同步後**一條都不用改**;
   要改的是 A 的三處 dict 下標(`tests/test_gate.py:165-167,207`)。改動落在正本,不落在副本,合乎 sync 單向的規矩。
5. **auto-fix 對這一格要有反應,否則這一格白記。** 讀 code 推的:A 的 `gate.sh:584,619` 對任何 rc≠0 都叫 `auto_fix`,
   `auto_fix()`(366-382)與 `auto-fix.sh`(228-245)都**沒有看** `state == env_suspect` 或這一格 —— rc=86 那一輪照樣派 worker
   進一個「機器現在不能跑」的環境(#7 的案例是帶 `--no-auto-fix` 跑的,所以沒抓到)。T 那邊 #642 落地四次三次環境紅(HANDOFF 2026-09-23 03:0x)
   就是這種錢。**裁:這一格非空 → gate 不自動派、inbox 走環境那一頁;人要派就手打 `auto-fix.sh`。**
   未來每票省/多花(推的):每次環境紅省一輪 opus worker(150–400K token);懷疑錯了的代價是一句手打指令(≈0)。不對稱,所以裁這樣。

## 否決的替代方案

- **B. 留 A 的 dict,加一個 `declared: [...]` 子鍵。** 一格裡兩種形狀;`{"declared": []}` 在真值上是真 → metrics 把沒有環境紅的
  一輪記成 env。否決。多花:每一個讀的人都要寫兩段解析(推的:每票 +1–2K 維護 token,永久)。
- **C. 兩格:`environment_suspect`(dict)+ `environment_declared`(list)。** metrics 要 OR 兩格、看板兩欄、事件兩種;
  「同一件事的兩個來源」被做成兩件事。否決。多花同 B。
- **D. dict 以引擎為鍵 `{"safari": {...}}`。** 丟掉多筆與順序;引擎認不出的那一筆(`""`)當鍵很怪;T 現有「兩份 log 各記一次」做不到。否決。
- **E. 只留 declared,拿掉 statistical。** #620 那種 safaridriver storage 壞掉、26 條同形紅,沒有任何案例會「宣告」—— 它們是真的斷言倒了。
  否決。多花:每次這種故障整段跑完 + 一輪 worker(推的 200–500K)。
- **F. 只留 statistical,叫 T 的案例不要印那一行。** 螢幕鎖住的紅被 #644 改記成 skip,`addFailure` 根本不會被叫,統計看不到它;
  #645 同理。否決。多花:回到 #474(每次上鎖一輪 worker)。
- **G. 頂層 dict `{"rows": [...], "aborted": bool}`。** `aborted` 與 `state=env_suspect` / rc=86 三處講同一句話。否決。
- **H. 改 T 的印行格式,讓行裡自帶 `source=`/JSON。** 前綴是兩端各留一份的契約(`test_browsers.py:838-841`、`test_ticket_645.py:506`),
  改格式要動 T 五處案例,而 `source` 由「是從哪條路進來的」就決定了,不需要行裡說。否決。**印行格式不改。**
- **I. 合併同形的 declared 行成一筆帶 count。** 少一點筆數,但 T 現有案例要求兩份 log 各一筆、兩行兩筆(實測);
  合併的規則(同 why?同 engine?跨 log?)本身要再裁一次。否決;真的太多筆(#645 latch 前 23 條)時**由 board 摺疊顯示**,不在檔裡合併。
  多花(推的):每筆 ≈ 60 token,一趟最多二十幾筆,主線讀一次 +1.5K;可接受。

## 遷移

### A(正本,`agent-control`)—— 票 #23

1. `scripts/status.py`
   - `EnvironmentResult._observe`:達門檻時 `environment_suspect = [row]`,row 照上面七鍵(`source="statistical"`,
     `why=message`,`line=str(err[1]).splitlines()[0]`,`log=args.log` 由 `cmd_run_tests` 補)。suspect file 寫 **list**,
     沒事時寫 `[]`(現在寫空字串,實測 183 行)。
   - 搬 T 的 `SUSPECT_PREFIX` / `SUSPECT_ENGINE` / `parse_environment_suspects()`(`scripts/control/status.py:93-97,268-296`)進來,
     每筆補 `source="declared"`、`count=1`、`threshold=None`。**`cmd_run_tests` 要把測試的 stdout 也導進 `--log`**
     (`contextlib.redirect_stdout(stream)`):讀 code 推的 —— `TextTestRunner(stream=…)` 只收 runner 的輸出,案例 `print()` 的那一行
     現在會流到 gate.sh 的 stdout 而**不在 log 裡**,於是 `done --log` 永遠讀不到 declared 行。T 今天沒中招是因為 T 不走 run-tests、
     整段 stdout 被 test-for.sh 收進 gate.log。
   - `cmd_done`:`environment_suspect = 環境檔的 list + 每份 --log 的 declared 行`;沒有就 `[]`。事件 `env.suspect` 改成非空就發(kv 見上)。
   - `cmd_start`:寫 `"environment_suspect": []`。
   - 新增讀端正規化 `environment_suspects(data) -> list`:缺格 / `None` / `{}` → `[]`;**非空 dict(舊形狀)→ 包成一筆
     `statistical`**(`why=message_shape`,`line=""`,`log=""`);list 照回。metrics 與 board 一律經它讀。
   - 檔頭 docstring 與 `docs/WORKFLOW.md` §狀態檔 的 schema 補這一格。
2. `scripts/gate.sh`:`suspect_field` 改讀 `[0]`(engine / why / count);`status_done` 之後若 `status.py suspects --ticket --run-id` 印出非 0
   (新子指令,印筆數與逐筆一行;或 `done` 的 rc 不動、另外印 `status: environment_suspect=<n>` 讓 gate grep —— **選子指令**,grep 輸出是
   §5.5 的坑)→ `ENV_SUSPECT=1`,`auto_fix` 開頭多一行 `[ "$ENV_SUSPECT" -eq 0 ] || { echo "gate: 環境可疑,不自動派";return 0; }`,
   inbox 頁走環境那一段(`message_shape` 改印 `why`,並列出 `source`)。
3. `scripts/auto-fix.sh`:讀最新一輪時若 `environment_suspects` 非空,印一行「上一輪環境可疑(<engine>:<why>),你確定要派?」**仍然照派**
   (手打就是覆寫),但那一行要進 stdout 讓人看見。
4. 看板:**實測 `board/board.py` 沒有讀這一格**(#19 讀的是 failures/rc/state;只有 `scripts/metrics.py:202` 讀,經真值)。要改:
   `metrics.py:202` 改經 `environment_suspects()`;`one_run()` 多回 `env_suspects=[{source,engine,why}]`,`verdict_cell` 在 `failure_total`
   之前加一支:非空 → `環境可疑 N 筆(engine:why 第一筆)`,再接原本的「紅 N 條」。理由:這一格存在就是為了把讀的人導去修環境;
   一張寫著「紅 26 條」的看板把人導去 auto-fix。未來每票省(推的):主線少開一份 status.json(1–2K)+ 少一次誤派(150–400K)。
5. 案例:`tests/test_gate.py:165-167,207` 改 `[0][…]`;`tests/test_metrics.py:61,137,138,255` 與 `tests/test_board.py:169` 的 fixture
   改成 list;**另留一條用舊 dict fixture 走正規化**(驗相容)。
6. `docs/DECISIONS.md` 收本條;`docs/WORKFLOW.md` schema;`docs/REHEARSAL.md` 多一列(第一次真的在 A 看到 declared 行時要看到什麼)。

### T(副本,`tabby_pool`)—— 同步票(HANDOFF 已列的那一張)

- `sync-to-project.sh` 會整檔覆蓋 `scripts/control/status.py`(manifest 實測含 status.py)。#23 落地前同步 = T 失去 `parse_environment_suspects`;
  **所以順序是 #23 先落,再同步**。同步後 T 自動得到:declared 解析(原本就有)、statistical 的形狀、正規化函式、`start` 寫 `[]`。
- 同步後 T **還缺**:
  1. `scripts/land-ticket.sh` 不走 `run-tests`(實測 grep 無 `run-tests`/86)→ T 沒有 statistical 來源,也沒有 fail-fast。這是 HANDOFF 列的
     「#12 fail-fast」那一項,**獨立一張 T 票**,不併進來;票要寫:T 的測試由 `test-for.sh` 起、engines 由 `TABBY_BROWSER_ENGINES` 控(D-G127),
     run-tests 接進去要保留 stdout 進 log。
  2. land-ticket.sh 的 auto-fix 派工(1217-1247)要加同一條「這一格非空就不自動派」。獨立小票或併進同步票。
  3. `demo/test_browsers.py` 的印行格式:**不改**(否決案 H)。`test_control.py` / `test_ticket_644.py` / `test_ticket_645.py`:**不改**(鍵是超集合)。
     同步票落地時要**真的跑**這三組,當作相容的證據。
- 未來每票省/多花(推的):T 同步一次 ≈ 一張機械票(sonnet 30–60K);之後 A 的每一次改動 T 免費拿到,不再各改各的(D-015 的理由)。

### 舊 status.json 相容

- 實測:A `reports/` 95 份,48 份沒這一格、47 份 `{}`、**0 份非空 dict**;T `../tabby_pool_wt/reports/` 80 份,72 份沒這一格、其餘 `[]`、0 份非空。
- 舊 dict 沒有 `message_shape` 時 `why` 是 **`null` 而不是 `""`**(#23 第 2 輪裁的;上面第 9 列原本寫「`""` 或 `message_shape` 的值」,
  以 §結論「缺料就 `null`」為準)。`""` 在說「理由是一句空話」,`null` 在說「那一版根本沒記理由」,而只有後者是真的
  —— 這就是 `docs/DISPATCH-TEMPLATE.md` §5.5「拿不到就當空的」那一格。`line` / `log` 那兩格文件與票面都**逐字**釘成 `""`,照釘的寫。
- 裁:**不改寫舊檔**(一輪一目錄不覆寫,是 D-014 的規矩),讀端正規化吃掉四種空與非空 dict。正規化函式 ≈ 10 行,永久留著
  (metrics 會回頭讀整個 reports/)。多花(推的):≈ 0;省的是一支「一次性 migration 腳本」與它的案例(≈ 一張 haiku 票)。

## 驗收(機器可驗;fixture 兩邊各用真的)

fixture A:`tests/test_gate.py:33` 的 `ENVIRONMENT_WAVE`(#7 同形連紅,12 條 subTest 同引擎同句)。
fixture T:`verify/land_preflight/test_ticket_644.py:261-284` 的 `SUSPECT_LINE` + `a_log()`(2 行宣告 + 3 條真的紅 + 真的收尾),
再加一行 #645 的字面 `ENVIRONMENT-SUSPECT: safari session 斷了(#645)`(`demo/test_browsers.py:1311` 印的就是這句)。

| # | 案例(層) | 斷言 | 變異(拿掉哪一支就該紅) |
|---|---|---|---|
| 1 | gate 跑 ENVIRONMENT_WAVE,`--ticket 7 --no-auto-fix`(test_gate,沙盒) | `state=="env_suspect"`;`environment_suspect` 是 list、長度 1;`[0]` 七鍵齊全、`source=="statistical"`、`engine=="safari"`、`count==8`、`threshold==8`、`why` 不含數字、`line` 含 `101 seconds` 之類的原字 | run-tests 仍寫 dict → `isinstance(list)` 紅 |
| 2 | `status.py done --rc 1 --log <a_log 2 行>`(test_status,沙盒;T 的 A4 原樣搬上來) | 2 筆、都 `source=="declared"`、`count==1`、`threshold is None`、`engine=="safari"`、`why in line`、`log` 是傳進去的路徑;`rc==1`、`failures` 3 條 | 拿掉 parse → 0 筆 |
| 3 | 同一趟兩份 log(`gate.log`、`gate.log.rerun`)各含 1 行 | 2 筆,`log` 的 basename 排序等於兩個檔名 | 去重 → 1 筆 |
| 4 | `done` 沒有環境檔、log 無宣告行 | 這一格**存在**且 `== []`;`start` 寫出的檔也 `== []` | `start` 不寫 → KeyError |
| 5 | 環境檔 1 筆 statistical + log 2 行 declared 同一趟 | 3 筆,順序 statistical 在前,`sources` 集合 == {statistical, declared} | 只讀其中一邊 |
| 6 | run-tests 跑一條會 `print("ENVIRONMENT-SUSPECT: firefox 假的宣告")` 然後 skip 的案例 | `--log` 檔裡找得到那一行;接著 `done --log` 得 1 筆 declared | 不 redirect stdout → log 裡沒那一行 |
| 7 | 事件:#2 之後 `events.jsonl` 有一則 `env.suspect`,kv `rows=2 sources=declared engines=safari` | 只在 rc=86 發 → 沒事件 |
| 8 | gate 跑 ENVIRONMENT_WAVE **不帶** `--no-auto-fix`(沙盒 worker.command 換成記一行的假指令) | 假 worker **沒被叫**;stdout 含「環境可疑,不自動派」 | 拿掉 `auto_fix` 那一行守衛 → 假 worker 被叫 |
| 9 | 正規化:三份手造 status.json(缺格 / `{}` / 舊 dict `{"engine":"safari","count":8}`) | 前兩份 → `[]`;第三份 → 1 筆 statistical、`why` = `message_shape` 的值(舊檔沒有那一格就是 `null`,見下)、`line==""`、`log==""` | 拿掉 dict 分支 → 第三份 `[]` |
| 10 | metrics(test_metrics 既有那條)fixture 改 list 後 `env_runs=1 product_runs=1`;另加一份舊 dict 的 run → `env_runs=2` | 不經正規化 → 舊 dict 仍算 env(真值)但 `[0]` 下標炸 |
| 11 | board `/`:一份 run 帶 1 筆 declared(engine firefox)且 failures 3 條 | 該列含「環境可疑 1 筆」與「firefox」,且仍含「紅 3 條」 | `verdict_cell` 不看這一格 → 沒有「環境可疑」 |
| 12 | T 端(同步票):`demo/test_control.py` 那一組 + `test_ticket_644.py` A4 + `test_ticket_645.py` ③ 原樣跑 | 全綠(鍵是超集合) | — |

## 後續每件工作的模型

| 工作 | 模型 | 一行理由 |
|---|---|---|
| #23 開票(把本文件寫成 acceptance / test_plan / verify_strings) | opus | 票面十二條要對得上檔案行號,錯一條返工一輪;開題者角色卡在 opus |
| #23 實作(status.py / gate.sh / auto-fix.sh / metrics / board / docs) | opus | 動 #7 的 `EnvironmentResult`(subTest failfast 有坑,EVIDENCE M1)與 gate.sh 流程,sonnet 在這種 shell + Python 交錯處的返工率高 |
| #23 驗證者(照上表寫 12 條案例,含 8 的假 worker) | sonnet | 案例形狀已定、fixture 都指了行號;純機械,不需要判斷 |
| T 同步票(跑 sync、跑三組既有案例、HANDOFF 一行) | sonnet | 機械;唯一判斷是「#23 先落再同步」,已寫在票上 |
| T 的 land-ticket.sh「非空不自動派」 | sonnet | 十行 shell + 一條案例,對照 A 的 gate.sh 抄 |
| T 接 run-tests(fail-fast,獨立票) | opus | 要動 test-for.sh 與 engines 環境變數的交錯,而且第一次真跑要有人在場(§5.55) |
| 舊檔正規化 + 案例 9 | haiku | 10 行純函式、三份手造 fixture |
| 本文件進 `docs/DECISIONS.md` 與 WORKFLOW schema | haiku | 抄寫 |
