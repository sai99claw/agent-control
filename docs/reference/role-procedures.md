# 角色操作細節(reference)

角色卡只留原則、行為準則、思考方式(D-013 3);卡上原本夾帶的指令、旗標、路徑、行號、
commit 指紋搬到這裡,依角色分節。卡上用「reference『<角色>角色操作細節』」指到本檔的那一節。
這一份是**紀錄類**:用 grep 定位、讀那幾行,不整份讀(D-013 4)。
來源:#78(2026-10-04,fable 單模型整理,D-043);逐句去處見 `docs/review/memory-remediation-a.md`。

## 開題者角色操作細節

### 票的欄位契約與派工範本
- 欄位契約:`tickets/SCHEMA.md`;開題者派工範本:`templates/dispatch-opener.md`。
- outline 欄:`python3 scripts/ticket.py create --outline '<≤300 字摘要與建議順序>'`。
- 自己那張票檔的落地(D-025 ③,唯一的 git 例外):
  `sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json`;裸 commit 與 land 的其他用法不做。

### 票面四格自檢(D-031;#651/#652 各退三輪全是這幾格)
- `needs_verifier`:布林 + 一句理由(D-028)。
- `verify_strings`:`list[str]`,是**內容字串**,不是檔名、不是 `{path, contains}`。閘門與落地比的是改動內文(diff 的增刪行與新檔全文),
  diff 檔頭與檔名不算;新檔就挑檔內一定會有的字(類名、
  函式名)。完整認法見 `python3 scripts/ticket.py create --help` 的 `--verify-string` 說明與
  `templates/dispatch-opener.md`「票面的機械格要能被機器驗」(#90 A5)。
- `tags`:只能用 `verify/TAGS.md`(與 `verify/TAGS.d/`)登記過的;動到 `verify/` 案例的票一定要有。
- 票面引用的指令先 `--help` 一次再寫。
- 自檢一行(D-031):
  ```
  python3 -c 'import json;t=json.load(open("tickets/<n>.json"));assert isinstance(t.get("needs_verifier"),bool),"needs_verifier";v=t.get("verify_strings");assert v and all(isinstance(x,str) for x in v),"verify_strings";print("ok")'
  ```

### needs_verifier 怎麼判
- `docs/DISPATCH-TEMPLATE.md` §5.8 第 3 問:驗證者案例只會重演 worker 自己案例的同一契約就寫
  `false` + 一句理由(T #679 先例;#68)。
- 本 repo(agent-control)的驗證者案例住 `tests/` 時,票的 `verify.tags` 要留空:gate 會拿 tags 跑
  `verify.py --tag`,選不到案例算缺口 rc=3;TAGS 字面只為 lint F2(#29)。

### 寫入範圍
- `allowed_write_paths` 要含驗證者會建立的案例檔(票 `verify.files` 那幾個);漏了 `apply.sh` 秒退一輪(#23)。
- 反例(舊測試 / 期望檔 / 版號釘著舊行為卻不在範圍裡):#658、#659、#661、#662;
  驗收寫實作細節而撞案例:#660(封閉鍵集)、#663(標籤、regex、「不在 DOM」、幣別);
  「其餘斷言不變」沒查 oracle:#664(主線改裁三次)。

### 跨 repo
- 跨 repo 依賴不能寫 `depends_on`(兩邊票號撞)。`sync-to-project.sh` 名單只十支,
  `gate.sh` / `land.sh` / `metrics.py` 不會到專案端;dry-run 不比內容,判準是同步後逐檔 `cmp -s`(#648)。

## 實作者角色操作細節

### 票閘門的叫法
- 票閘門對照表挑到的那一組(`scripts/gate.sh`):副本裡沒有 `.git`,`--branch` 是空閘門,要用檔名形式叫。
- 只跑派工文「## 只准跑的測試」段列的測試(#87 A18 由 `auto-fix.sh` 逐字抄票的 `test_plan`);
  不跑全套(`unittest discover` 整組、`gate.sh --full`)、不跑 `verify.py` 全部回歸。
- 負載下重跑(只在票的 `test_plan` 列了才做):`nice -n 19` + 4 支 `yes`,記 PID 只 kill 自己的,
  新案例跑 3 次;窗口要蓋得住負載下受測那一包的 parse/exec,前提不成立要大聲紅(#642,比照 #639)。
- 全套超過前景 600s 上限(#70 實測 878 條、單 `test_auto_fix` 270s):逐模組分批前景跑、每批 <590s,
  合計 Ran 與各批 rc。
- 擷取測試輸出只看 `^Ran |^OK|^FAILED|^(FAIL|ERROR):`。
- `claude -p` 丟背景就收回合、不會再醒(#26)。

### 交付物的形狀
- `patch.diff`:在副本根 `diff -ruN base work > patch.diff`,檔頭只准 `base/…` / `work/…` 的相對形式;
  絕對路徑檔頭 `apply.sh` 秒退、`patch -p1` 會把檔案建到 `work/private/…`(#25 #26 #28)。
- `EVIDENCE.md` 五段:① patch sha256;② 閘門指令、`Ran N`、rc;③ 變異表(編號 / 替換 count / 目標斷言 /
  紅訊息首行 / 還原後 sha256)+ 每條新增或改動的案例一行 `四問:`(`docs/DISPATCH-TEMPLATE.md` §5.8,#68);
  ④ 已排除的假設;⑤ 最小重現。`result` 塊的欄位見規則包 D-017 段(#20 驗收⑦)。
- 不寫 `verify/**`(驗證者的,同名檔落地會打架,#630)。
- 紅了被重派時先讀 `reports/t<票號>/<run_id>/status.json`(票 + `repair_context` + 紅榜)。

### 反駁(objection)的形狀
- 寫進票的 `objections[]` 一筆:`category`、`body`、`evidence`、`owner`;阻擋就 `blocking: true`。
  `category: test_defect` = 案例的 oracle / fixture 錯了,由驗證者修。
- 反駁由 `auto-fix.sh` 起開題者判、判完接回(D-041,#89);不報主線。
- OBJECTION 行會被原文抄進 `tickets/<n>.json`,`test_no_project_names` 會掃:不准寫絕對家目錄路徑或專案名(#53)。

### 副本環境
- 派工帶著 `AC_*`(`AC_ROOT`、`AC_TICKETS_DIR`、`AC_CONTROL_DIR`):重現對不上紅榜先 `env | grep ^AC_`(#7)。
- 副本裡跑任何 `scripts/*`(含 `memory.py check`)前先 `env -u AC_ROOT -u AC_TICKETS_DIR -u AC_CONTROL_DIR`,
  或 `AC_ROOT=$PWD` 指到副本自己:派工帶的 `AC_ROOT` 讓它量主 repo 的檔(量到舊的就是假綠),
  還會把票、`board/events.jsonl` 寫回主線 —— 事後只能 git checkout 票檔,事件行收不回(#44、#59、#71)。
- 測試子行程呼叫本樹腳本讀「真文件」時要傳 `env AC_ROOT=ROOT`:腳本經 `event.repo_root()` 吃派工帶的
  `AC_ROOT`,不釘就讀主 repo,副本裡的變異驗不紅、案例假綠(#71)。
- 會發事件的腳本(連 `memory.py check`)在 `base/` 或 `work/` 跑都會新建 `board/events.jsonl`:
  要跑就在 base 的暫存拷貝裡跑,交件前拿 `git archive` 對 base 做一次 `diff -r`(#53)。
- 副本無 `.git`:靠 `git ls-files` 的測試必紅(`test_no_project_names` 掃到 0 個檔、
  `test_the_scan_sees_something_at_all` 必紅,其餘是空掃描 —— 改動檔要自己 grep 那四個字樣,#89)。
  全套在 `cp -R work` + `git init` 的複本跑,用 base 複本同條對照證明紅來自環境(#28/#49)。
- base 一字不動:對照那趟加 `PYTHONDONTWRITEBYTECODE=1` 或跑完刪 `__pycache__`(#40);
  交付前 `diff -rq base work | grep '^Only in work'`(`gate.log`、`*.env-suspect.json` 會被 `diff -ruN`
  收進 patch,#29)。交完刪掉自己的 `work/` 與 `base/`,只留 patch 與 EVIDENCE。
- 對真實專案根實跑 `new-session.sh` / session-hook 前,先把專案與它的 board 資料(`tickets_dir`、
  `events_file`、`reports_dir` 可能在根外)複製到 TMPDIR 再跑:`memory.py check` 在 `--no-event` 下仍會
  emit `memory.over_cap`、還可能開整理票進專案票庫(#62)。
- `control_harness` 沙盒 config 的 `memory.applies_to` 只有 `memory/model/*.md`:角色卡超標的案例要先把
  `memory/role/*.md` 加進去,否則 check 不量那份檔、輸出是空的而不是紅的(#76)。
- 給 `control_harness` 的 `DEFAULT_CONFIG` 加預設值前先 grep `verify/` 與 `tests/` 有沒有靠「沒設就退回」的案例(#70)。

### 測試與 patch 的寫法(細節)
- sh 腳本裡 `$var` 後面緊接全形字(。、,)時 bash 會把 UTF-8 位元組吃進變數名,`set -u` 下變成
  unbound variable;新增的變數一律寫成 `${var}`(#57)。
- 同一輪被重派、副本已重展:先從上一次的 worker log 抽出逐字 replace(assert count==1)腳本重放,
  再用 AST 方法數 / 斷言數 / 逐檔 diffstat 對上一次 EVIDENCE 證明重放一致(#72)。
- 移植舊 patch 到新 base:逐檔 `patch`、讀 `.rej` 手解,並以 `diff <舊 work> <新 work>` 反向核對上游 delta(#13)。

### 歷史來源(卡上原本夾帶的 commit 指紋)
- 84864f3 / b65ee79(2026-09-13~21)做 / 不做 / 交付物;82a8b1d(2026-09-21)EVIDENCE 五段與反駁;
  db32c07(2026-09-21)回歸與不准輪詢。都是實測來源;原文在 `git log` 與 `docs/review/`。

## 驗證者角色操作細節

### 案例與交付物
- 案例檔:`verify/<feature>/test_ticket_<n>.py`(檔頂宣告 `TAGS`);新標籤寫成片段 `verify/TAGS.d/<票號>.md`;
  `files` / `tags` / `run` / `notes` 寫進票的 `verify` 欄。
- 驗紅:`python3 scripts/verify-case.py red <票號> --candidate <$W/work>`;rc=0 才算交件。它不寫票(#36):
  印出的 `baseline-red.json` 原封抄進 `result` 的 `baseline`,`apply.sh` 收件時併進票。
- 閘門量綠:`verify-case.py check`;`ticket.py close` 只認那一趟。交付物 `verify-case.py extract`。
- `EVIDENCE-verifier.md` 檔尾 `result` 區塊;`四問:` 行寫在每個案例名底下(`docs/DISPATCH-TEMPLATE.md` §5.8)。
- 收件(#29 A5):`sh scripts/apply.sh <票號> <patch> <patch-verify> --evidence-verifier EVIDENCE-verifier.md`,
  抽成 `reports/t<票號>/<run_id>/result-verifier-round<輪>.json`(看板 `/t/<票號>` 畫的就是那一份)。
- `patch-verify`:在 `$W` 裡 `diff -ruN base work`,檔頭只准 `base/…` / `work/…`;絕對路徑檔頭 `apply.sh`
  秒退、`patch -p1` 會把檔案建到 `work/private/…`(#25 #26 #28)。
- 案例檔由驗證者獨占:實作者交的同名檔落地時以驗證者的為準,票的 `verify.files` 記驗證者那份(#630)。

### 誰派、誰跑
- `needs_verifier=true` 的票由 `auto-fix.sh` 第 1 輪自動派;票 `interface_fixed=true` 才與 worker 平行(#51),
  否則等 worker 交出 `patch-round1` 才派(#60)。
- 票的 `tags`(含驗證者登記的)由 `gate.sh` 跑;驗證者只跑自己的案例與 `verify-case.py check`。
- 反例 T #681:等 `from === 當月-01` 在 30 號的近 30 天檢視本來就成立 —— 判準要「壞掉時必然為假」。
