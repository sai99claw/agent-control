# 角色操作細節(reference)

角色卡只留原則、行為準則、思考方式(D-013 3);卡上原本夾帶的指令、旗標、路徑、行號、
commit 指紋搬到這裡,依角色分節。卡上用「reference『<角色>角色操作細節』」指到本檔的那一節。
這一份是**紀錄類**:用 grep 定位、讀那幾行,不整份讀(D-013 4)。短命角色開場只讀「角色卡 + 票」
(正文 `memory/role/README.md`),這裡是遇到才查的。
來源:#78(2026-10-04,fable 單模型整理,D-043);逐句去處見 `docs/review/memory-remediation-a.md`;
#92(2026-10-06)依 `docs/review/20261004-a-spec-audit.md` 對齊。

## 開題者角色操作細節

### 票的欄位契約與派工範本
- 誰起(#122):角色表 `opener`(opus);明確複雜的開題 `opener_complex`(astra → fable)。主線獨立派工:
  `sh scripts/new-session.sh dispatch opener --items <這次要交的票數> --prompt <派工文> [--complexity complex]`;
  超過候選 `dispatch_cap` rc=2、印切批建議、不起程序(切批由主線做);auto-fix 判反駁的開題者走 `opener` 鏈,不帶票的 model / complexity。
- 欄位契約:`tickets/SCHEMA.md`;開題者派工範本:`templates/dispatch-opener.md`。
- outline 欄:`python3 scripts/ticket.py create --outline '<≤300 字摘要與建議順序>'`。
- complexity 欄(#136):開題者每張票標 simple / complex;只有 complex 做完後覆核;沒標 = simple(`ticket.py create --complexity simple|complex`;不加 Ready 關、不進 lint)。
- 自己那張票檔的落地(D-025 ③,唯一的 git 例外):
  `sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json`;`AC_ROLE=opener` 時它只收 `tickets/<AC_TICKET>.json`,
  別的檔 **land 會擋**(#91 C6)。裸 commit 與 land 的其他用法不做。

### 票面三格自檢(D-031;#651/#652 各退三輪全是這幾格)
- `needs_verifier`:JSON 布林 + 一句理由寫在 `outline`(D-028)。沒寫 = true(auto-fix 照樣起驗證者),
  不擋 Ready、不進 lint(D-054 Q10);只有要免驗證者才明寫 `false`。
- `tags`:只能用 `verify/TAGS.md`(與 `verify/TAGS.d/`)登記過的;動到 `verify/` 案例的票一定要有。
- 票面引用的指令先 `--help` 一次再寫。
- 自檢(D-031 原話「要做成 `ticket.py lint`」;#91 C3 做出來了):
  ```
  python3 scripts/ticket.py lint <n>
  ```
  唯讀、不動 `state_version`;逐條印 `needs_verifier` / 標籤的問題,全過印 `ok`。不再手寫 python 一行自檢。

### needs_verifier 怎麼判
- 驗證者案例只會重演 worker 自己案例的同一契約就寫 `false` + 一句理由(T #679 先例;#68)。
- 本 repo(agent-control)的驗證者案例住 `tests/` 時,票的 `verify.tags` 要留空:gate 會拿 tags 跑
  `verify.py --tag`,選不到案例算缺口 rc=3(#29)。

### 引用端與驗收
- 票沒有寫入範圍(D-054 Q2):會跟著動的檔寫進 `outline`,漏寫不擋;唯一的硬擋是 `board/config.json` 的 `hard_block`。
- 反例(舊測試 / 期望檔 / 版號釘著舊行為卻沒列出來):#658、#659、#661、#662;
  驗收寫實作細節而撞案例:#660(封閉鍵集)、#663(標籤、regex、「不在 DOM」、幣別);
  「其餘斷言不變」沒查 oracle:#664(主線改裁三次)。

### 判反駁時(D-041,#89)
- headless 開題者的工具白名單沒有 Agent(`board/config.json` 的 `opener.command`),子工作者只有互動 session 才有;判反駁時自己 grep。
- 一輪只有兩種判決(D-054 Q8):accepted = 改好票面接回 worker(worker 讀錯票也是,澄清寫進票面);escalate = 一題附建議的裁示題給主線。判決行與題目格式照 auto-fix 給的派工文;流程正文 `docs/WORKFLOW.md`「實作者的反駁怎麼被收下」。

### 跨 repo
- 跨 repo 依賴不能寫 `depends_on`(兩邊票號撞)。

## 實作者角色操作細節

### 測試怎麼叫
- 只准跑的測試:正文在 `docs/DISPATCH-TEMPLATE.md` §1(派工文「## 只准跑的測試」段由 `auto-fix.sh` 逐字抄票的 `test_plan`)。
- 票閘門對照表挑到的那一組(`scripts/gate.sh --branch --ticket <n>`):在自己的 worktree 裡量得到分支;只在 `test_plan` 列了才跑。
- 負載下重跑(**只在票的 `test_plan` 列了才做**):`nice -n 19` + 4 支 `yes`,記 PID 只 kill 自己的,
  新案例跑 3 次;窗口要蓋得住負載下受測那一包的 parse/exec,前提不成立要大聲紅(#642,比照 #639)。
- 全套(**只在票的 `test_plan` 明列全套時才跑**;#70 實測 878 條、單 `test_auto_fix` 270s):逐模組分批前景跑、每批 <590s,
  合計 Ran 與各批 rc。
- 擷取測試輸出只看 `^Ran |^OK|^FAILED|^(FAIL|ERROR):`;前景跑、給 `timeout`(不准輪詢的正文在 `docs/DISPATCH-TEMPLATE.md` §3)。

### 交付物的形狀
- 票分支上的 commit(D-054 Q9,#144):cwd 是派工前建好的票分支 worktree;交件判準與 git 規矩見 `docs/DISPATCH-TEMPLATE.md` §1(越界守衛 rc 8)。
- `EVIDENCE-round<r>.md` 寫在派工文給的輪次目錄(worktree 外,不 commit);段名照 `docs/DISPATCH-TEMPLATE.md` §8,
  檔尾 `result` 塊照 `tickets/SCHEMA.md`(#20 驗收⑦)。
- 不寫 `verify/**`(驗證者的,同名檔落地會打架,#630)。
- 紅了被重派時先讀 `reports/t<票號>/<run_id>/status.json`(票 + `repair_context` + 紅榜);前幾輪的改動是分支上的 commit。
- 回報寫票分支名與分支頭 sha,不寫家目錄絕對路徑。

### 反駁(objection)的形狀
- 寫進票的 `objections[]` 一筆:`category`、`body`、`evidence`、`owner`;阻擋就 `blocking: true`。
  `category: test_defect` = 案例的 oracle / fixture 錯了,由驗證者修。
- 反駁由 `auto-fix.sh` 起開題者判、判完接回(D-041,#89);不報主線。
- OBJECTION 行會被原文抄進 `tickets/<n>.json`;`ticket.py objection` 收件時把家目錄前綴換成 `~`(#91 C4),
  專案名不換 —— `test_no_project_names` 會掃,兩種都不要寫(#53)。

### worktree 環境
- auto-fix 派的 `AC_ROOT` 指輪次目錄 `<worktree_dir>/fix-t<n>/round<r>/control` 這個**拋棄式控制根**(`AC_TICKETS_DIR` /
  `AC_CONTROL_DIR` 已拿掉;`docs/SESSION-START.md` Worker 那一節):worktree 裡跑 `scripts/*`(含 `memory.py check`)
  量到、寫到的是那個空控制根 —— 票、事件、記憶檢查都落在那裡。要量 worktree 自己的檔就 `AC_ROOT=$PWD`;
  重現對不上紅榜先 `env | grep ^AC_`(#7)。手派時派工的人若自帶主 repo 的 `AC_ROOT`,寫的才是主線(事件行收不回,#44、#59、#71)。
- 測試子行程呼叫本樹腳本讀「真文件」時要傳 `env AC_ROOT=ROOT`:腳本經 `event.repo_root()` 吃環境裡的
  `AC_ROOT`,不釘就讀錯樹,變異驗不紅、案例假綠(#71)。
- 會發事件的腳本(連 `memory.py check`)在 worktree 跑會新建 `board/events.jsonl`(.gitignore 掉的不會進 commit);
  commit 前 `git status --short` 看一次,`gate.log`、`*.env-suspect.json` 這類執行產物不要 commit 進去(#29、#53)。
- 對照組是 `base_sha`:對照那趟在 `git worktree` 以外的暫存拷貝(`git archive <base_sha>`)跑,加
  `PYTHONDONTWRITEBYTECODE=1` 或跑完刪 `__pycache__`(#40)。
- 對真實專案根實跑 `new-session.sh` / session-hook 前,先把專案與它的 board 資料(`tickets_dir`、
  `events_file`、`reports_dir` 可能在根外)複製到 TMPDIR 再跑:`memory.py check` 在 `--no-event` 下仍會
  emit `memory.pending`、還可能開整理票進專案票庫(#62)。
- `control_harness` 沙盒 config 的 `memory.applies_to` 只有 `memory/model/*.md`:角色卡的案例要先把
  `memory/role/*.md` 加進去,否則 check 不列那份主檔的字元數(有 inbox 的主檔仍會列、仍會觸發,#76)。
- 給 `control_harness` 的 `DEFAULT_CONFIG` 加預設值前先 grep `verify/` 與 `tests/` 有沒有靠「沒設就退回」的案例(#70)。

### 測試與交件的寫法(細節)
- sh 腳本裡 `$var` 後面緊接全形字(。、,)時 bash 會把 UTF-8 位元組吃進變數名,`set -u` 下變成
  unbound variable;新增的變數一律寫成 `${var}`(#57)。
- 同一輪被重派、worktree 已重建:先從上一次的 worker log 抽出逐字 replace(assert count==1)腳本重放,
  再用 AST 方法數 / 斷言數 / 逐檔 diffstat 對上一次 EVIDENCE 證明重放一致(#72)。
- 移植舊 patch 到新 base:逐檔 `patch`、讀 `.rej` 手解,並以 `diff <舊 work> <新 work>` 反向核對上游 delta(#13)。

### 歷史來源(卡上原本夾帶的 commit 指紋)
- 84864f3 / b65ee79(2026-09-13~21)做 / 不做 / 交付物;82a8b1d(2026-09-21)EVIDENCE 五段與反駁;
  db32c07(2026-09-21)回歸與不准輪詢。都是實測來源;原文在 `git log` 與 `docs/review/`。

## 驗證者角色操作細節

### 案例與交付物
- 案例:加進最貼近的既有測試模組(`tests/` 或既有 `verify/` 模組),不為單張票新開以票號命名的案例檔(D-056);
  不新增標籤、不寫登記片段(`tests/` 模組 `tags` 留空);`files` / `tags` / `run` / `notes` 寫進 `result` 的 `verify` 格(形狀在 `templates/dispatch-verifier.md`),收件併進票的 `verify` 欄。
- 驗紅:`python3 scripts/verify-case.py red <票號> --ref <base_sha> --candidate <$W/work>`;rc=0 才算交件。它不寫票(#36):
  印出的 `baseline-red.json` 原封抄進 `result` 的 `baseline`,`apply.sh --evidence-verifier` 收件時併進票。
- **驗證者只跑自己的案例與 `verify-case.py red`**;`check`(乾淨主線紅、candidate 綠)是閘門 `gate.sh` 叫的、`ticket.py close` 只認那一趟,
  不是驗證者跑的(D-020;F1)。交付物 `verify-case.py extract`。
- `EVIDENCE-verifier.md` 檔尾 `result` 區塊。
- 收件(#29 A5;auto-fix 跑,人手派時由派工的人跑):`sh scripts/apply.sh <票號> <patch-verify> --evidence-verifier EVIDENCE-verifier.md`
  (在票分支上補一個 commit),
  抽成 `reports/t<票號>/<run_id>/result-verifier-round<輪>.json`(看板 `/t/<票號>` 畫的就是那一份)。
- `patch-verify`:怎麼產照 `templates/dispatch-verifier.md` 第四樣;檔頭只准 `base/…` / `work/…` 的相對形式(`apply.sh` 以 `git apply -p1 --check` 套不上就 rc 3)。
- 案例檔由驗證者獨占:實作者交的同名檔落地時以驗證者的為準,票的 `verify.files` 記驗證者那份(#630)。

### 誰派、何時起
- 正文只在 `tickets/SCHEMA.md` 的 `needs_verifier` 與 `interface_fixed` 兩列(F25);派工由 `auto-fix.sh` 做。
- 票的 `tags`(含驗證者登記的)由 `gate.sh` 跑。
- 反例 T #681:等 `from === 當月-01` 在 30 號的近 30 天檢視本來就成立 —— 判準要「壞掉時必然為假」(卡上只寫票號)。

## 覆核者角色操作細節
- 派不派(#136):開題者每張票標 simple / complex;只有 complex 做完後覆核;沒標 = simple。`review.sh` 對非 complex 的票走 refuse(rc=2、`review.refused`),
  `auto-fix.sh` / `gate.sh` 綠後也不叫它;land / close 只對 complex 要 `review.verdict=pass`。
- 誰派、怎麼派:`sh scripts/review.sh <票號>`(票要是 InReview / AwaitingReview、complex、分支 `t<票號>` 要在);派工文逐字讀
  `templates/dispatch-reviewer.md`,規則包 `rules.py pack reviewer` 在它之前;工具白名單在 `board/config.json` 的 `reviewer.command`
  (Read / Glob / Grep / Bash,沒有 Edit / Write)。
- 交付物:整份回覆就是 REVIEW.md,存到派工文給的 `@REVIEW@` 路徑;檔尾 `## result` 多一格 `verdict`(`pass` / `fail`)。
- 反駁形狀:擋落地的每一條寫成行首、不縮排的 `OBJECTION: blocking <一句話>`;`review.sh` 把每一行記成一筆 blocking 反駁、票轉 Blocked;
  `pass` 由它 `ticket.py set <n> review …`(verdict、by `reviewer@<模型>`、sha、note、at 五格,給人看)。覆核者沒有 Write,寫不進票 —— 不要自己寫 objections JSON。
- 上限的來源:#42 首跑 118K、#29 實測 35–118K(卡上 120K)。

## 整理者角色操作細節
- 觸發與判準(D-053 ⑥):暫存區(`*.inbox.md`)有未整理條目,主線開場的 `memory.py check` 就開整理票;不設固定字數上限,
  判準只有「每個 session 開場都會讀，只留值得這個成本的」,寫什麼、留多長由整理者決定。
- 範本:`templates/dispatch-consolidator.md`(自動由 `scripts/consolidate-memory.sh` 填、`new-session.sh` 主線開場起;人工整份複製)。
- 誰整理(#122,D-043 / D-049 ②):角色表 `consolidator` 鏈(`python3 scripts/role-models.py resolve --role consolidator`,現在只有 fable),
  `consolidate-memory.sh` 只起**一個** session、沒有雙模型互讀;票上的 `model` 與 `memory.consolidator(s)` 都不參與。
  候選額度用完 / 不可用才換下一格;fable 用不了 = 整串耗盡 → 票 NeedsDecision、一頁 decision,不改派別的模型。每次起都記一列 cost(role=consolidator)。
- 開場快照:`python3 scripts/memory.py snapshot <檔>`;逐條來源分類:`python3 scripts/memory.py check-stale --read-only --file <檔>`。
- 討論檔:`discussions/<date>-memory-<模型>.md`,格式 `docs/DISCUSSION.md`;啟動端把路徑與模型記進整理票 `attempt_history`。
- 合併(單一整理者):`python3 scripts/memory.py consolidate <檔> --candidate <候選檔> --discussion <啟動端記下的那一份>`;
  一份討論只收 `attempt_history` 有記、檔頭 model 一致的那一份。人工派(沒有啟動端紀錄)照舊要兩份不同 model:
  `--discussion <A> --discussion <B>`。快照對不上、缺某一行的處置、候選有錯層內容都拒絕且不動檔(D-007 / D-013);候選多長不量(D-053 ⑥)。
- 角色卡通用規則要改(例:卡上「單一高階模型走 worker 管線」那段):寫一行進 `memory/role/consolidator.inbox.md`,由整理流程消化;
  worker 不自改模型政策(`board/role-models.json`、`docs/ROLES.md` 模型表)。
- 收工檢查:合併那一句 rc 0(snapshot 那 K 行已移進 `.consumed`);整理票 `ticket.py close <票號>`。
- 分層契約與 lint:`memory/role/README.md`;`python3 scripts/memory.py lint --file <檔> --json`。
- 討論檔目前不在 `land.sh docs` 的前綴白名單(`tickets/ docs/ memory/`),整理票的 `discussions/` 要走票的一般落地(#52)。

## 設計 session 操作細節
- 誰起(#122):角色表 `design` 鏈 astra → fable。主線用 `sh scripts/new-session.sh dispatch design --items <這次要交的份數> --prompt <派工文>` 派;
  超過候選 `dispatch_cap`(astra 5)rc=2、印切批建議、不起程序;astra 額度用完 / 不可用才換 fable;整串耗盡 rc=3 停下。
- 讀:`docs/DECISIONS.md` 相關的那幾條、`docs/DESIGN.md`(總綱)與題目對到的 `docs/DESIGN-<題>.md`。
- 交:一份 `docs/DESIGN-<題>.md`,固定段見 `docs/DESIGN.md` §設計文件的固定段。
- 回報標記「實測 / 讀 code 推的」:`docs/DISPATCH-TEMPLATE.md` §6.4。

## 主線操作細節
- 開場那一頁由 hook 帶(`docs/SESSION-START.md` 主線那一節);收件匣 `python3 scripts/inbox.py list` / `inbox.py ack <票號>`。
- 覆核:閘門綠(auto-fix 第 r 輪綠、或手跑 `sh scripts/gate.sh --branch --ticket <n>` 綠)→ `sh scripts/review.sh <n>`;
  事件 `review.pass` / `review.missing`(沒交件就重跑 review.sh)。
- 狀態檔:`reports/t<n>/<run_id>/status.json`;票檔與文件落地:`sh scripts/land.sh docs "<訊息>" <檔…>`(前綴 `tickets/ docs/ memory/`)。
- 反駁處置:`objections[]` 的 `disposition`(合法字只在 `scripts/ticket.py` 的 `DISPOSED`);`test_defect` 派驗證者。
