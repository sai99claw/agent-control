# 工作流程

## 票的狀態機
```
Draft → Ready → Running → InReview → IntegrationQueued → Integrating → Done
                  ↕            ↕
              Blocked / NeedsDecision / Failed / Cancelled(保存原因與回復點)
```
| 關卡 | 必須具備 |
|---|---|
| Ready | 範圍、可操作的驗收、依賴達成、`base_sha`、`allowed_write_paths` |
| InReview | patch、測試輸出逐字、變異驗紅、回報標明實測/推論 |
| IntegrationQueued | 審查通過、局部閘門綠、無未解阻擋 |
| Done | **同一份必要條件,`close` 與 `set state Done` 共用**(D-014):① `scripts/ticket.py verify` 證明改動真的在主線,**弱檢查不算過**(沒有 `verify_strings` 就補一條再關);② 有回歸證據(`test_evidence` 或 `verify.baseline`)——**或**票上有誠實的 `verify_waiver{by,reason}`(#8)且 review 綁的 sha 真的在主線歷史裡(`git merge-base --is-ancestor`,不是逐字比對分支的頭,#15);③ 有一格**綁著票版本與分支 sha** 的 `review`;④ `objections[]` 裡沒有未處置的阻擋項 |

**exit code 0 不等於 Done;worker 說做完不等於 Done;閘門綠是對某個 `base_sha` 說的,基準走遠就過期。**

**已落地的票關不掉(#15)**:落地之後補一條 `verify_strings`、記一筆 `verify_waiver`、或處置一筆 `objections`,都是**收尾**,不是重審票面 —— `ticket.py set <n> verify_waiver|verify_strings|objections …` 會把既有 `review.state_version` 跟著蓋到新版本(不算讓 review 過期),改票面其他格照舊規矩過期。落地那一手如果手上就是合併出來的 sha,`ticket.py close <n> --landed <merge sha>` 一步把它蓋進 `review.sha`(先確認在主線歷史裡)再走關票流程,不必先 `set review` 再 `close` 兩步。

## 一票一分支一副本
- worker 拿到 `base_sha` 的 `git archive` 副本(`work/`)與對照副本(`base/`),交 `diff -ruN base work > patch.diff`。
- **套 patch → 開 `t<票號>` 分支與 worktree → commit 走 `scripts/apply.sh <票號> <patch> [<patch-verify>]`**
  (2026-09-21,D-015)。它在 `git apply` 之前擋檔頭(只准 `base/…` / `work/…` 或 `/dev/null`,
  絕對路徑拒、`diff -ruN` 的刪檔沒改 `+++` 也拒),在 `git apply` 之後比對每個 `+++` 目標
  並 `--reverse --check`(rc=4),再檢查 `allowed_write_paths`(rc=5),commit 訊息帶票號與
  **patch 的 sha256**。主線走遠了先 `scripts/apply.sh rebase <票號> <patch>` 重生一份乾淨 diff。
  **`land.sh` 收的還是已經有 commit 的分支** —— 它不套 patch(`docs/ROLES.md`)。
- **同時只准一個 `land.sh`**:`land.sh` 自己用一把 `mkdir` 鎖擋(`.land.lock`),拿不到就指名現在是誰在落地。land 期間主線不得往主線提交。發版期間不開 land。

## 落地(`scripts/land.sh t1 t2 …`)
1. 先印每支分支的 commit 數與標題(**讓人看見它以為自己在做什麼**)。
2. 任一支 0 commit → 整批拒絕(跳過會生出沒有人要求過的組合)。
3. 任一支的 `base_sha` 有問題 → 拒絕,分兩種話講:**主線根本沒有那個 sha**(副本是拿錯的 ref 做的,回去查副本從哪來)vs **有但不是主線祖先**(主線走遠了,rebase 後重跑閘門)。兩者 `merge-base` 都非零,下一步差很多。
4. patch 動到 `allowed_write_paths` 以外 → 拒絕。
5. **票的 `verify.files` 要真的在分支上**(#587 那把尺,D-015):票說「案例在這幾個檔」而分支上沒有,
   代表驗證者的交付沒有跟著進來 —— 而 `verify.tags` 照樣會被閘門呼叫、照樣一個案例都選不到、照樣印一行綠。
   缺了 **rc=4**(與其他拒收的 2 分開:呼叫者要分得出「票面沒填好」與「分支沒準備好」)。
6. **覆核與反駁是硬閘門**(D-014):票要有一格 `review`,`verdict` 通過、`state_version` 等於票現在的版本、`sha` 對得上這條分支的頭;`objections[]` 裡有未處置的阻擋項 → 拒絕。三者任一不成立都在**開 worktree、跑九分鐘全套之前**就退回。
7. 依序合到 `land/<ts>` worktree,跑全套(`scripts/gate.sh --full`),綠才 `--ff-only` 推主線;紅則主線不動、worktree 留著給人看。
8. **gate / merge / push 各記一筆**(狀態檔的 `phases`),而且**每一條退出路徑都寫終態**,
   同時寫一則 `reports/inbox/<票號>-<run_id>.md`(D-015:終態去叫醒主線,主線不輪詢)。
9. 綠了之後 land 印「#n 已合併、尚未關票」——**land 不關票**,關票走 `scripts/ticket.py close <n>`。
10. 每一步發事件。全套紅時預設派下一輪 worker,**只在這一批剛好一張票的時候**
   (一批裡哪一條紅對到哪一張票,要有票↔案例的對照才判得出來)。

## 閘門(`scripts/gate.sh`)
專案自己定義三層:`--branch`(改動檔對應的模組)、`--base`(基礎組)、`--full`(全套)。**對不到任何模組要出聲,不准印一行綠。** 判綠先寫檔再讀退出碼,不用 `cmd | tail`。瀏覽器引擎由 `available()` 判,不在指令列收窄。

`--ticket <n>` 再多做三件事(範本 `scripts/gate.example.sh` 也示範了這一格):
1. 寫這一輪的狀態檔(見下)。
2. **真的去跑票的回歸**:票的 `verify.tags` 併 `tags` → `scripts/verify.py --tag …`,原始輸出存 `verify.log` 並進狀態檔的 `logs`。宣告了 tags 卻一個案例都選不到 = 非零(那是缺口,不是綠)。`--full` 則跑**全部**回歸。
3. flake 重跑(見下一節)。

## 發版
主線執行、人事先授權;順序:tag → build → staging → 閘門 → prod → 健康檢查 → 記事件。**發版期間不開 land。**

## 衝突
- 排順序的人(主線,或依 `allowed_write_paths` 算衝突圖的腳本)讀票的 `allowed_write_paths` 判平行;**不同檔不代表安全**,介面耦合要看票的 `interface_contracts`。
- 兩支分支都往同一份文件尾巴附加(HANDOFF 那類)一定衝突:**先等對方進去再寫**,而不是寫完再解——解衝突時讀的是 diff,先等讀的是完整的檔。
- 自動合成功不等於合對:land 輸出出現 `Auto-merging <文件>` 就把結果讀一遍。

## 狀態檔 = 交接包(2026-09-21,D-010 + D-014)
每次 gate / land / docs 開跑寫 `reports/t<n>/<run_id>/status.json`,**一輪一個目錄、不覆寫**;log 另外複製一份進 `reports/t<n>/<run_id>/logs/`(land 成功後 worktree 會被收掉,而 log 就住在那裡面)。

```
{state, run_id, kind, ticket, sha, started, finished, rc, note,
 phases: [{phase: gate|merge|push, rc, at, note}],
 logs: […], extra_logs: […], kept_logs: [{path, kept}],
 failures: [{case, kind, subtest, file, line, engine, log, excerpt, suspected_flaky}],
 suspected_flaky: […],
 repair_context: {version, base_sha, ticket: <票面快照>, worktree,
                  patch: {path, sha256}, verify_patch: {path, sha256},
                  round, prev_evidence, repro: {cmd, cwd}, env}}
```

`repair_context` 存在的理由:下一輪換的是**新的** worker,它手上只有這一份檔。少了 base_sha、票面快照、副本位置、patch 雜湊、第幾輪、上一輪排除過什麼、怎麼重現,它得回頭翻對話或猜檔案位置 —— 那一趟比整份 log 還貴。`failures` 從 `^(FAIL|ERROR):` 與其後的 Traceback 擷取(excerpt ≤ 20 行),**原生 subTest 的圓括號參數也認**(`FAIL: test_x (mod.Case.test_x) (engine='firefox')`)。

## 回歸紅了之後(2026-09-21,D-010;flake 與歸責那兩條 D-014 改寫)
1. 紅的案例**單獨重跑一次**(同 worktree、同 commit)。**單跑綠只標 `suspected_flaky`** —— 原始失敗留在紅榜、rc 一個位元都不動。接著用**原順序整組重跑一次**(`AC_FLAKE_RERUN_GROUP=0` 關掉),仍紅就是真紅;綠了也只是「疑似」,放不放行是人的判斷。
   > ⛔ 取代 D-010 原本的「全 flaky 視為綠」。2026-09-21 外部審查的實測反例:第一條測試污染共用狀態、第二條檢查乾淨狀態 —— 整組必紅、乾淨程序單跑必綠,而舊規則正是以「所有紅的案例單跑都綠」為由回傳 0。**順序依賴的 bug 於是每一次都被判成偶發。**
2. 疑似 flaky 逐筆寫進 `reports/flaky.jsonl`(持久事件帳,不隨下一輪清空)。同一條累計到門檻(`board/config.json` 的 `flaky_threshold`,預設 3)發 `decision.asked`,**並自動開一張修不穩定的票**(role=verifier,票上留 `flaky_case`;同一條案例只開一張,`flaky_auto_ticket: false` 可關)。
   > 為什麼從「只發事件」改成「自動開票」(D-015):事件沒有 owner、沒有驗收,而**一則沒有人認領的事件比一張沒有人認領的票更容易被滑過去** —— 票至少每個 session 開場都出現在 `list --open` 裡。
3. 真紅 → 起**新** worker:`scripts/auto-fix.sh <票號>`。`gate.sh` 與單票 `land.sh`
   預設就會走 auto-fix;**要人下場才明寫 `--no-auto-fix`**。
   它讀最新狀態檔,組派工文 = **規則包**(`scripts/rules.py pack worker`)+ 票面快照 + `repair_context`
   + failures 逐條 excerpt + 上一輪 EVIDENCE + 第幾輪,用 `board/config.json` 的 `worker.command`
   (預設 `claude -p --model opus`)在副本裡跑,收 `patch-round<r>.diff` 與 `EVIDENCE-round<r>.md`,
   再走 `apply.sh` → `gate.sh --branch --ticket`。每輪結束 `scripts/ticket.py round <n> <第幾輪> --red|--green`。
   專案自訂的 `gate.rerun_cmd` 會在 worktree 內執行,並收到 `AC_ROOT`(主 repo)、
   `AC_WT`(這張票的 worktree)、`AC_ROUND`(本輪)與 `AC_TICKET`(票號)。
   **綠了停在 `InReview`** —— 覆核不自動。
4. **三輪耗盡不是一句話,是一個狀態轉換**:`round` 在第 `retry_limit+1` 輪仍紅時把票轉 **Blocked**、`owner` 設成 `main`,並發 `ticket.attempt.failed`。舊規則只寫「報主線」,而「報了」與「沒報」在票上長得一樣。
5. 停下來報主線的**三種**情況,每一種都寫一則收件匣:worker 判斷**票寫錯 / 需要裁示**
   (它在 EVIDENCE 寫一行 `OBJECTION: <類別> <理由>`,`auto-fix.sh` 把它記成 `objections[]` 的一筆);
   三輪耗盡;**failures 沒有歸因**(rc 非零卻一條紅都解析不出來 —— 那一種最像「沒有紅」,
   而派下去的 worker 會拿著空紅榜去猜)。
   > ⛔ **「紅在票沒動到的檔 → 疑似他票」這一條拿掉了**(D-014)。`failures.file` 取的是 traceback 最後一個檔案,經常是既有測試或共用 helper;而產品改壞行為,本來就會紅在完全沒修改的測試檔。歸責改用**同條件的 baseline / candidate 對照**(`scripts/verify-case.py check`:同一份案例在乾淨主線與 candidate 上各跑一次)。**未完成歸因前,票由原 owner 持有** —— 不因為某個檔沒被這張票改過就轉成別人的問題。
6. 覆核不自動:主線讀 patch 把 `review` 記進票(工具自動把票的 `state_version` 蓋進去),land 才收。

## 實作者的反駁怎麼被收下(2026-09-21,D-014)
worker 說「這張票寫錯了」以前只是一句話:沒有結構化類別、沒有收件者、沒有處置期限,而**沒有人收的反駁與沒有反駁長得一樣**。現在它是票上的一格:

```json
"objections": [{"category": "ticket-wrong", "body": "驗收第二條與設計文件對不上",
                "evidence": "EVIDENCE.md:12", "owner": "main",
                "disposition": "", "follow_up": ""}]
```
`category` 是 `ticket-wrong` / `blocking`(或 `blocking: true`)就是阻擋項;`disposition` 空著 → **land 拒絕、`close` 拒絕**。處置寫 `accepted` / `rejected` / `deferred` / `fixed`,要有 owner,建議附後續票號。

## 交接類型:`test_defect`(2026-09-21,D-014)
票是對的、程式也是對的,**錯的是案例本身**(oracle 或 fixture 寫錯)時:worker **不准**放寬斷言、不准改 oracle。它交一筆 `objections[{"category": "test_defect", …}]` 附反例,auto-fix 立即派**新的 role=verifier worker**(規則包 + 紅榜 + 反駁行 + 案例檔路徑)。驗證者交 `patch-verify.diff`,腳本併入同一分支後續跑閘門;主線只從 inbox 看「案例已修,第 N 輪綠/紅」。需求本身有爭議才退回開題者。

### 這一段哪些已經是程式,哪些還只是規格(2026-09-21 逐項對照真實入口重寫)
**這張表以前漏列了四項重大未實作**,而一張漏列的能力表比沒有能力表更糟:它讓人以為那幾件事有程式在守。以下每一列都對著一個真的入口。

| 規格 | 這個 repo 的狀態 | 真實入口 |
|---|---|---|
| 狀態檔:一輪一個目錄、不覆寫、`repair_context`、`phases`、持久 log | **已實作** | `scripts/status.py`,由 `gate.sh --ticket` 與 `land.sh` 寫 |
| 單跑綠只標 `suspected_flaky` + 原順序整組重跑;rc 不因 flake 變綠 | **已實作** | `scripts/gate.sh`(`AC_NO_FLAKE_RERUN=1` / `AC_FLAKE_RERUN_GROUP=0`) |
| flake 持久事件帳 + 達門檻發 NeedsDecision | **已實作** | `reports/flaky.jsonl`;`status.py` 發 `decision.asked` |
| flake 達門檻**自動開修復票** | **已實作**(2026-09-21,D-015) | `status.py` 的 `open_flaky_ticket`;同一案例只開一張,`flaky_auto_ticket: false` 可關 |
| 局部閘門跑**票的 `verify.tags`** | **已實作**(以前漏列) | `gate.sh --ticket <n>` → `scripts/verify.py --tag …`,原始輸出存檔 |
| 全套跑**全部回歸**,不靠執行器自測間接跑 | **已實作**(以前漏列) | `gate.sh --full` → `scripts/verify.py` |
| **baseline 驗紅**(乾淨主線該紅、candidate 該綠),import 失敗不算紅 | **已實作**(以前漏列) | `scripts/verify-case.py check <n>` → 寫票的 `verify.baseline` |
| baseline 的 **ref 預設是票的 `base_sha`**;`--ref` / `--candidate` 吃 worktree 路徑 / 分支名 / sha;**量不到就不動票**,改印下一步 | **已實作**(2026-09-22,#22) | `verify-case.py check <n> [--ref <base_sha>] [--candidate <分支/sha/路徑>]` |
| 驗證產物抽成 `patch-verify.diff`;新 tag 一票一片段再合併 | **已實作** | `verify-case.py extract` / `tags-merge`;`verify/TAGS.d/<n>.md` |
| **land 檢查 review 綁票版本與分支 sha、objections 未處置就拒絕** | **已實作**(以前漏列) | `scripts/land.sh` 第 5 步 |
| **land 互斥鎖** | **已實作**(以前只寫在文件裡) | `scripts/land.sh` 的 `.land.lock`(mkdir) |
| 進 Done 的必要條件 `close` 與 `set state Done` 共用;弱檢查不准自動關票 | **已實作**(以前 `set state Done` 是一條旁路) | `ticket.py` 的 `done_blockers()` |
| 遲到的回報拿 `attempt` / `state_version` 比對後拒收;寫入走同一把鎖 | **已實作**(SCHEMA 以前宣稱過但沒有實作) | `ticket.py set --expect-attempt / --expect-state-version`;`.ticket.lock` |
| 三輪耗盡 → 票轉 Blocked 並指派主線 | **已實作** | `ticket.py round <n> <r> --red` |
| **套 patch、建分支、commit** | **已實作**(2026-09-21,D-015) | `scripts/apply.sh <票號> <patch> [<patch-verify>]`;重套走 `scripts/apply.sh rebase` |
| **關票** | **land 不關票**;它印「已合併、尚未關票」。誠實的 `verify_waiver` + review sha 在主線歷史裡可免回歸證據那格;`verify_waiver`/`verify_strings`/`objections` 的 `set` 不讓既有 review 過期 | `scripts/ticket.py close <n> [--landed <merge sha>]` |
| land 前檢查 `verify.files` 都在分支上 | **已實作**(2026-09-21,D-015) | `scripts/land.sh` 第 5 步,缺了 rc=4 |
| 用 headless `claude -p` **自動起新 worker**(三輪上限) | **已實作**(2026-09-22;gate / 單票 land 預設開,`--no-auto-fix` 關) | `scripts/auto-fix.sh <票號>`;`gate.sh`、`land.sh` |
| **終態叫醒主線**(gate done / auto-fix 停 / land done / 轉 Blocked)+ 禁止輪詢 | **已實作**(2026-09-21,D-015) | `scripts/inbox.py post\|list\|show\|ack`;`reports/inbox/<票號>-<run_id>.md`;`new-session.sh` 開場印 |
| 同一輪的回歸**只跑一次**(worker / 驗證者 / gate 共用) | **已實作**(2026-09-21,D-015) | `scripts/verify.py` 的 `reports/t<n>/<run_id>/verify-<雜湊>.log`(雜湊含標籤 + sha);`--no-cache` 關 |
| **按角色裁切、帶版本的規則包** | **已實作**(2026-09-21,D-015) | `scripts/rules.py pack <角色> --model <模型>`(≤ 4 KB,砍掉的部分會指名) |
| 一個既有專案**接上這一套** | **已實作**(步驟 + 腳本;實際遷移還沒做) | `docs/TODO.md` §0;`scripts/sync-to-project.sh` 同步到專案的 `scripts/control/` 並印出接點 |
| `land.sh` 那一側自己判 flake / 一批多張票時歸責 | **未實作,而且是刻意的** —— 一批裡哪一條紅對到哪一張票,要有票↔案例的對照才判得出來;land 因此只在**剛好一張票**時派下一輪,多張就留給主線 | `scripts/land.sh` 的 `auto_fix_all` |

## 驗證者的案例怎麼進閘門
票的 `verify.tags` 併進 `tags`,由 `gate.sh --ticket <n>` **真的呼叫** `scripts/verify.py --tag …`(原始輸出存檔)。`--full` 跑全部回歸。驗證者不出 VERDICT;紅了照上一節走。

### baseline 要對著哪一版量(2026-09-22,#22)
`--ref` **沒給時是那張票的 `base_sha`**,不是主線的頭。票一落地,主線上就已經有那份實作,對主線量出來的「一條都沒紅」說的是**這一趟量錯了地方**,不是「這條案例是假的」(#19)。落地後要補量就寫死兩端:`check <n> --ref <票的 base_sha> --candidate <merge sha>`。

`--ref` / `--candidate` 三種寫法都解得開:**worktree 路徑、分支名、sha**;同名時路徑優先(舊的叫法一律傳路徑)。以前只當路徑用,於是 `--candidate t20` 去找 `$PWD/t20`,印「candidate 裡找不到這幾個案例檔」,而檔就在 t20 上(#20)。

**量不到 ≠ 驗紅沒過**:candidate 解不開、案例檔不在 candidate 上、ref 的歷史裡已經有 candidate —— 這三種是「這一趟沒量到」,rc=2、**票一個字都不改**、印下一步。只有真的兩邊都跑完才寫 `verify.baseline`。舊版把量不到也寫成 `ok: false`,`ticket.py close` 從此擋著那張票,而票面上看不出那一格說的是哪一件事。

**新標籤一票一個片段檔** `verify/TAGS.d/<票號>.md`,`scripts/verify.py` 直接認它,`scripts/verify-case.py tags-merge` 再折進 `verify/TAGS.md`。理由:所有票都往同一份登記檔的尾巴附加,等於每張票都要等前一張落地(D-012 認過 TAGS 是最常見的衝突點);一票一個檔就不會撞,序列化的只剩合併那一步。

land 前檢查 `verify.files` 都在分支上(#587 那把尺)**已經實作**:缺了 rc=4,而且是在
開 worktree、跑九分鐘全套**之前**就退回(`scripts/land.sh` 第 5 步)。

同一組標籤在同一輪裡只跑一次:`scripts/verify.py` 看 `AC_TICKET` + `AC_RUN_ID`,把輸出與 rc
存成 `reports/t<n>/<run_id>/verify-<雜湊>.log`,雜湊含**標籤 + 這棵樹的 HEAD sha**。
sha 變了就是不同的問題,快取一定失效;單獨跑的人(沒有 `AC_RUN_ID`)拿到的永遠是真的跑。

## patch 管線:多張票接連落地時會撞什麼(2026-09-21 實測)
> **這一節的五條現在都有程式在守**(D-015):1、2、3 在 `scripts/apply.sh rebase`(三向合併、
> 衝突與 **0 byte 的 diff** 一律非零、刪檔的 `+++` 側**由工具自己改成** `/dev/null`),4、5 在
> `scripts/apply.sh`(`verify_strings` 對 patch 抓一次、檔頭只准相對形式)。下面留著的是**為什麼**。

連續落地幾張票,**幾乎一定**撞到兩個地方:所有票都往尾端附加的登記檔(`verify/TAGS.md` 那一類)
與自動產生的清單(modulepreload 那一類)。所以:

1. **落地前把 patch 重套到「當前主線」上,而不是直接 `git apply`。** 重套的方式是**三向合併**
   (2026-09-22 改,#17):祖先 = 票的 `base_sha`(patch 就是對那一版做的)、我方 = 當前主線、
   對方 = `base_sha` + 這份 patch。patch 先**嚴格**套回自己的 `base_sha`(不吃 fuzz);套不回去
   就代表票面的 `base_sha` 與這份 patch 對不起來,當場停。然後重生自動產生的清單,再出一份
   **乾淨的 diff**。答案因此與 `git rebase` 自己算出來的那一棵樹逐位元相同。
   > 以前這裡是 GNU `patch -F 2`(吃 fuzz、自己找位移)。它猜得出位移,但也會**猜錯而不說**,
   > 而且猜不動的時候整支 fatal 掉。三向合併不猜:合不起來就指名到檔與行。
2. **「沒套進去」與「套完了」不可以長得一樣 —— 看的是退出碼,不是 `.rej`。**
   🩸 2026-09-22(#17):#13 的 patch 在 #12 落地後重套,GNU `patch` 印了
   `misordered hunks! output would be garbled` 就 **fatal 退 2**,而且**一個 `.rej` 都沒留**。
   舊版把退出碼 `|| echo` 吞掉、只看 `.rej`,於是印出「乾淨的 diff」而那個檔是 **0 byte** ——
   要等到下一步 `git apply` 喊「一個檔頭都沒有」才有人發現。現在三件事都守:**退出碼看**、
   **衝突指名到檔與行(rc=3)**、**0 byte 的 diff 一律非零(rc=4)**。
3. **`diff -ruN` 對刪檔產生的 `+++` 側要手改成 `/dev/null`。** 不改的話 `git apply` 只會把檔案
   **清空**而不是刪掉,而清空的檔案在 diffstat 上看起來像是「改過」。
4. **票的 `verify_strings` 在落地前先對 patch `grep` 一次。** 字串不在 patch 裡,就不會在分支上,
   而那要等到 `land` 的最後一步才會說話。
5. **patch 檔頭只准 `base/…` / `work/…` 的相對形式;絕對路徑閘門要拒。** 🩸 真的發生過:
   檔頭帶著絕對路徑的 patch 套下去,檔案被寫進暫存目錄底下的同名路徑,而套用本身成功、
   閘門也綠 —— 因為被改的根本不是 repo 裡那一份。

## 派工的並行上限
同時最多兩個會起瀏覽器的 agent;全套並跑時不再起瀏覽器型 agent(2026-09-20 旅程「11-batch」在滿載下紅五次,每次白跑一輪全套)。優先序改變時把低優先的 agent 停掉,不要讓它自己滾。
