# A 規格全面審查:角色卡、模型卡、派工範本(2026-10-04,#88 落地後)

審查者:獨立 opus,唯讀(main `1d3543b`)。只寫本檔;沒改其他檔、沒跑測試、沒改票。
範圍:`memory/role/*.md`(含 inbox)、`memory/model/*.md`(含 inbox)、`docs/DISPATCH-TEMPLATE.md`、
`docs/reference/role-procedures.md`、`templates/dispatch-*.md` 與 `templates/project-CLAUDE.md`、`CLAUDE.md`「不可違反的」。
對照:`docs/DECISIONS.md`、`tickets/SCHEMA.md`、`memory/role/README.md`(分層契約)、`scripts/`、`tests/`。

嚴重度記號:**W** = 會讓 agent 做錯事;**T** = 會讓 agent 多花 token;**C** = 純整潔。
證據欄的「實測」= 這次 session 跑過的唯讀指令(grep / sed / `memory.py lint --json`,事後 `git status --short` 為空);「讀 code」= 讀程式推的。

## 摘要

- 發現 **42 條**:W 10、T 21、C 11。
- 最嚴重的三條:
  1. **F2** 驗證者的規則包帶 DISPATCH-TEMPLATE §8 第 4 點(變異驗紅表)與 §3(`gate.sh --branch --base`),跟 D-020「驗證者不做變異、不跑閘門」直接衝突。
  2. **F3** 開題者卡與 reference 規定 `verify_strings` 只能是 `list[str]`、自檢那一行會拒收 dict;但 `ticket.py create` 會把 `路徑:字串` 轉成 `{path, contains}`,而且 `--help` 說這是合法寫法。照卡走,工具產的合法票會被自檢擋掉。
  3. **F7/F8** 實作者卡與 DISPATCH-TEMPLATE 一邊說「不跑全套、只跑『只准跑的測試』」(#87 A18),一邊教怎麼分批跑全套,還推薦在副本裡跑 `gate.sh --branch --base`(副本沒有 `.git`,跑出來是空閘門)。
- 建議開 **3 張票**:2 張 prompt 票、1 張 code 票(見最後一節)。另有 2 條 DECISIONS 整潔項,建議主線自己走 docs 通道,不開票。

---

## 一、會讓 agent 做錯事(W)

### F1 驗證者該不該跑 `verify-case.py check`:三處說法互相打架
- 檔:行:`docs/reference/role-procedures.md:124`(「驗證者只跑自己的案例與 `verify-case.py check`」)、`memory/role/verifier.md:8`(「只跑自己的案例與閘門用的那支對勾工具」)、`templates/dispatch-verifier.md:75`(「你只跑自己的案例與 `verify-case.py red`;`check` 是閘門的事,不是你的」)。
- 問題:reference 叫驗證者跑 `check`,範本禁止。驗證者手上沒有 patch,跑 `check` 是在沒有實作的候選上量綠,沒有意義。
- 證據:D-020 C2/C5(`check` 由閘門叫);讀 code:`scripts/gate.sh:34-36,518` 是閘門在叫 check;`scripts/verify-case.py:4,7` red 給驗證者、check 給 candidate 分支。
- 處置:改文字。reference:124 與 verifier.md:8 改成「`verify-case.py red`」,兩處都只寫一次。
- 嚴重度:W

### F2 驗證者規則包帶到「做變異」與「跑閘門」那兩節
- 檔:行:`docs/DISPATCH-TEMPLATE.md:416`(§8 第 4 點「變異驗紅表」)、`:123`(§3 範例 `sh scripts/gate.sh --branch --base`);規則包名單在 `scripts/rules.py:97-99`,`WANTED["verifier"]` 包含 `"3"` 與 `"8"`。
- 問題:D-020 C3 把驗證者的變異整條拿掉;`verifier.md:3,7` 與 `dispatch-verifier.md:73-75` 都說不做變異、不跑閘門。規則包卻把這兩節原文餵給驗證者。DISPATCH-TEMPLATE:503-507 拿掉 §5 的理由(會逼驗證者寫產品碼),同樣適用於 §8 第 4 點。
- 證據:實測 `sed -n 93,118p scripts/rules.py`。
- 處置:改文字。§8 第 4 點標成「worker 限定」,§3 範例改成中性的「`<指令> > log 2>&1; rc=$?`」。只有在改文字擋不住時,才另開 code 票改 `WANTED`。
- 嚴重度:W

### F3 `verify_strings` 的形狀:卡說只能是 `list[str]`,工具卻產出 dict
- 檔:行:`memory/role/opener.md:9`、`docs/reference/role-procedures.md:18`(「不是 `{path, contains}`」)、`:26`(自檢 `all(isinstance(x,str) …)`)。
- 問題:`ticket.py create --verify-string 'scripts/x.sh:字'` 會存成 `{path, contains}`(`scripts/ticket.py:497-512` `normalise_verify`),`--help` 也把它列為三種合法寫法之一(`scripts/ticket.py:239-243`);`tickets/SCHEMA.md:16` 也寫了 `./README:那串字`。照卡上那一行自檢,工具產的合法票會 assert 失敗,開題者就會去改寫一張本來沒錯的票。D-031 的原意是擋手寫 `{path,contains}` 的 JSON,不是擋工具產的形狀。
- 證據:實測 `sed -n 236,245p; 495,520p scripts/ticket.py`。
- 處置:改文字(卡與 reference 改成「純字串,或用 `ticket.py create` 的 `路徑:字` 寫法;不要手寫 dict」,自檢放行工具產的 dict);code 票 C1 做 `ticket.py lint` 取代手寫自檢那一行。
- 嚴重度:W

### F4 開題者的「不改檔、不執行整支腳本」跟它自己的必要動作矛盾
- 檔:行:`memory/role/opener.md:4`(不改檔、不執行整支腳本),對照同一張卡 `:5`(`ticket.py create`)、`:9`(「票面引用的指令先跑一次」)、`:11`(D-033「前置狀態自己造一次」)、`:14`(判反駁要 `ticket.py set` 改票);`templates/dispatch-opener.md:8` 同樣這句話。
- 問題:照字面讀,D-033 與 D-031 要求的實測不能做;判反駁時又非寫檔不可(`scripts/auto-fix.sh:1316-1321` 要它用 `ticket.py set` 並寫 triage 檔)。`board/config.json` 的 `opener.command` 本來就有 `Write Bash`。另外 `opener.md:3` 要它「派 Opus 子工作者」,但 headless 開題者的工具白名單沒有 Agent;D-039 之後開題者自己就是 opus。
- 證據:實測 `board/config.json` 的 `opener.command` = `claude -p --model opus … --allowedTools Read Glob Grep Bash Write`。
- 處置:改文字。改成「不改產品碼與 repo 檔;票檔、triage 檔、暫存目錄裡的實測可以;唯讀的指令與 `--help` 可以跑」;子工作者那一句加上「互動 session 才有;headless 判反駁時自己 grep」。
- 嚴重度:W(F4 的子工作者那半句是 T)

### F6 Fable 模型卡教主線裸 commit
- 檔:行:`memory/model/fable.md:10-11`(共用工作樹只 `git add <檔>`;commit 到主線前先確認沒人在落地)、`memory/role/main.inbox.md:8,19`(「任何裸 commit 前先跑…」「主線得裸 commit」)。
- 問題:跟 `CLAUDE.md:17`(不准手動 merge)、`main.md:4`(不裸 commit 主線)矛盾;`scripts/guard-main.sh:130-132,201` 對主線的 `git commit` / `merge` 一律 deny。照卡做會撞 hook,或改用 `AC_MAIN_OVERRIDE` 繞過去。
- 證據:實測 `grep -n commit scripts/guard-main.sh`;`.claude/settings.json` 的 PreToolUse 有掛這支 hook。
- 處置:改文字。fable.md 那兩條刪掉或改成「走 `land.sh docs`」;main.inbox 那兩行在整理時處置掉(D-007)。
- 嚴重度:W

### F7 實作者「不跑全套」與「怎麼分批跑全套」並存
- 檔:行:`memory/role/implementer.md:13`(只跑「只准跑的測試」,不跑全套)vs `:14`(全套超過前景時限就逐模組分批)、`:18`(在加了 git 的暫存複本跑);`docs/reference/role-procedures.md:53-54,84`。
- 問題:#87 A18 之後,實作者不該跑全套。分批跑的指引等於在教它跑全套,一跑就是十幾分鐘,外加整份上下文。
- 證據:`scripts/auto-fix.sh:545-557` 會逐字抄 `test_plan` 進「## 只准跑的測試」。
- 處置:改文字。分批那一條限定成「票的 test_plan 明列全套時」,或直接刪掉;`:18` 的「全套」改成「靠 git 清單的那幾個測試」。
- 嚴重度:W

### F8 DISPATCH-TEMPLATE §3 推薦的閘門指令,在副本裡是空閘門
- 檔:行:`docs/DISPATCH-TEMPLATE.md:123`。
- 問題:worker 與驗證者的規則包都帶 §3(`rules.py:94-99`),reviewer 也帶。副本沒有 `.git`,`--branch` 形式是空閘門(`role-procedures.md:48` 自己也這樣寫),跑出來的綠是假綠;這也跟 §1:83-87「只准跑的測試」矛盾。
- 證據:讀 code:`role-procedures.md:48`、`implementer.md:18`。
- 處置:改文字(同 F2,範例改成中性指令)。
- 嚴重度:W

### F12 整理者卡:派工方式與合併指令都過期
- 檔:行:`memory/role/consolidator.md:3-5`(主線照範本手開 session)、`:14`(`memory.py consolidate <檔> --discussion <討論檔>`)。
- 問題:主線開場時 `new-session.sh` 已經會自動叫 `consolidate-memory.sh`(`scripts/new-session.sh:265-266`;範本 `dispatch-consolidator.md:4-8`);`consolidate` 必須帶 `--candidate`,而且要給兩次 `--discussion`(`scripts/memory.py:88-89,104-105`)。照卡上的指令打一定被拒。
- 證據:實測 `grep -n` 了 memory.py 的 USAGE 與 new-session.sh。
- 處置:改文字。卡只留原則,指令指到範本(D-036)。
- 嚴重度:W

### F19 主線 inbox 的過期條目會讓主線做多餘或錯的事
- 檔:行:`memory/role/main.inbox.md:6`(關票前手跑 `verify-case.py check`,但閘門已經接上:`gate.sh:34-36,518`)、`:14`(引用 `gate.sh:229` / `:491`,那兩行現在內容已經不同)、`:16`(說覆核者上限 40K,卡上已是 120K:`reviewer.md:34`)、`:17`(dispatcher 已列進 `RETIRED_ROLES`:`sync-to-project.sh:124`)、`:18`(`review.sh:150` 已收 AwaitingReview;`sync-to-project.sh:120` 已同步 dispatch-reviewer)。
- 問題:主線照 `:6` 會在每張票多手跑一次 check;其餘幾條會讓主線去開早就做完的票。
- 證據:實測 `sed -n 229p;491p scripts/gate.sh`、`grep -n` review.sh / sync-to-project.sh。
- 處置:改文字(整理 inbox;D-007 要兩模型或高階模型整理,依 D-043 前例可由 fable)。
- 嚴重度:W

### R2 `needs_verifier` 必填只寫在文字上
- 檔:行:`tickets/SCHEMA.md:17`(✓ 必填)、`memory/role/opener.md:9`、D-028(漏寫 = 票不完整)。
- 問題:`ticket.py` 的 `REQUIRED`(`scripts/ticket.py:47-50`)沒有這一格,所以 Ready 也不擋;`auto-fix.sh:699-720` 遇到缺這一格只印一行,然後第 1 輪就不起驗證者。產品票漏寫時,驗證者會被靜靜跳過,要等到 gate / land 才被攔下。
- 證據:實測 `sed -n 47,52p scripts/ticket.py`、`grep needs_verifier scripts/auto-fix.sh`。
- 處置:開 code 票(C1:`set state Ready` 時檢查)。
- 嚴重度:W

---

## 二、會讓 agent 多花 token(T)

### F5 開題者的模型標示沒跟上 D-039
- `memory/role/opener.md:1`(「開題者(Fable…簡單題可派 Opus」)、`docs/ROLES.md:38-39`(主線、開題的預設都還是 Fable)、`memory/model/fable.md:15-23`(「Fable 只做開題、定判準、定案」)。實測 `routing.open` 與 `routing.main` 都已是 `opus`。
- 另外「不准派 Sonnet 開題」(D-023)**已經有程式在擋**:`model_tiers=[haiku,sonnet,opus,fable]`,加上 `rules.py:628-650 below_floor`。卡上那句可以改成指路。
- 處置:改文字。嚴重度:T

### F9 DISPATCH-TEMPLATE §1 的手工套 patch 與 archive 步驟已經過時
- `docs/DISPATCH-TEMPLATE.md:69-81`(套 patch 前看 `-p` 幾、`find .rej`、`git archive` 前先唸一次)。現在由 `apply.sh` 用 `git apply` 套(D-012、D-015 1),副本由 auto-fix 展開;`CLAUDE.md:15` 也規定套 patch 只走 `apply.sh`。worker 的規則包帶 §1,所以每派一次就讀一次。
- 處置:改文字(縮成一句「派工方的事,見 WORKFLOW」)。嚴重度:T

### F10 誰寫 review:覆核者卡與 SCHEMA 都還說是主線
- `memory/role/reviewer.md:31-32`(「主線把 verdict 寫進票(`ticket.py set … review`)」)、`tickets/SCHEMA.md:20`(「主線讀 patch 之後記的那一格」)。這兩處跟 D-022、D-025 ②、`main.md:7` 以及 `review.sh:460`(pass 時由腳本寫)矛盾。D-010 ③「覆核不自動:主線讀 patch」也沒標 ⛔ 被取代。
- 處置:改文字;DECISIONS 那一列由主線補「⛔ 由 D-022/D-025 取代」。嚴重度:T

### F11 覆核者的反駁形狀:卡與範本不一致
- `memory/role/reviewer.md:15-16` 要它寫結構化的 objection(`category/body/evidence/owner`);`templates/dispatch-reviewer.md:32` 要的是行首 `OBJECTION: blocking`。覆核者沒有 Write 工具,寫不進票。
- 處置:改文字(卡指到範本)。嚴重度:T

### F13 整理者「一定要兩個模型」跟 D-013 ②、D-043 不一致
- `memory/role/consolidator.md:1,7-8` 寫死兩個模型;D-013 ② 允許「或一個高階模型」,D-043 就是用 fable 單獨整理的。`memory.py consolidate` 拒收兩份 model 相同的討論檔(`dispatch-consolidator.md:40`),所以單一高階模型這條路沒有工具可走(#78 是改走 worker 管線)。
- 處置:改文字(卡寫明兩條路與各自的入口);要不要讓工具支援單模型,交主線裁。嚴重度:T

### F14 每個短命角色都被要求讀整份 DISPATCH-TEMPLATE(33,029 bytes)
- `CLAUDE.md:22`、`memory/role/README.md:6`、`docs/SESSION-START.md:13`(表格打 ✓),對照 `docs/SESSION-START.md:52`(只讀 §禁區、§假綠)與 `DISPATCH-TEMPLATE.md:21-26`(規則包就是為了不讓每個 agent 付整份)。D-015 6 的帳在這裡被抵消了。
- 證據:實測 `wc -c docs/DISPATCH-TEMPLATE.md` = 33029。
- 處置:改文字。三處統一成「規則包 + 用 grep 讀包裡指名的節」,正文只留在 README:6。嚴重度:T(這是單筆影響最大的 token 項:每派一次工約 8–10K token)

### F15 `CLAUDE.md` 的開場四步沒限定是主線,而它會進到每一份副本
- `CLAUDE.md:5-10`(讀 HANDOFF、`inbox.py list`、`ticket.py list`、`event.py tail`)寫成所有人都要做;`CLAUDE.md` 有進版控,`git archive` 出來的副本都有,worker / 驗證者在 `work/` 起(`auto-fix.sh:1019`)時也會讀到。這跟 `CLAUDE.md:24`(短命角色不讀這些)矛盾。另外 `CLAUDE.md:14`「每個動作要發事件」跟 `SESSION-START.md:53`「副本裡不發事件」也衝突。
- 處置:改文字(第一步之前加一句「主線才做;短命角色見 SESSION-START」)。嚴重度:T

### F16 `CLAUDE.md` 對寫入範圍的說法,比程式實際的硬擋還寬
- `CLAUDE.md:15-17`(apply 驗「寫入範圍」、land 拒「寫入範圍越界」)。依 D-H38 ①,`allowed_write_paths` 越出不擋,只擋 `out_of_scope` 與 `protected_paths`(`apply.sh:585`、`land.sh:381`)。讀 CLAUDE.md 的實作者可能為了白名單外的檔多提一次反駁。
- 處置:改文字(「越過 out_of_scope / protected_paths」)。嚴重度:T

### F17 卡上引用的 D-H38 在 A 的 DECISIONS 找不到
- `memory/role/implementer.md:9`、`opener.md:10,12`、`DISPATCH-TEMPLATE.md:395,405`、`tickets/SCHEMA.md:9,11,18`,程式註解也有多處。
- 問題:`grep -n "D-H38" docs/DECISIONS.md` 沒有任何結果。分層契約(README:22)要求引用的編號在 DECISIONS 找得到;讀卡的人查不到來源,只能去翻 review。
- 處置:主線在 DECISIONS 補一列 D-H38(或改成 A 自己的編號);不開票。嚴重度:T

### F18 主線卡提到一支不存在的「落地順序提案」腳本
- `memory/role/main.md:3`(「或讀腳本依 `allowed_write_paths` 算出來的提案」)。實測:`scripts/` 與 `tests/` 都 grep 不到任何地方發 `schedule.proposed`(只在 `event.py:38` 的名單裡出現)。
- 處置:改文字(刪掉那半句,或標「未實作,D-010 1」)。嚴重度:T

### F20 分層 lint:#78 只清了三張卡,其他七份仍有 74 條命中
- 實測 `memory.py lint --file <檔> --json`:consolidator 11、design 4、main.inbox 32、main 11、reviewer 6、fable 7、opus 3;opener、implementer、verifier 與其餘 inbox 都是 0。
- 違反 D-036 與 README:18。閘門只量分支新增的行(D-042),所以這些舊命中會一直留著,直到有一張整理票去處理。
- 處置:prompt 票 P2(依 D-007 整理,指令與路徑搬進 reference)。嚴重度:T

### F22 inbox 有重複條目與測試殘留
- `memory/model/fable.inbox.md:2-3` 跟 `main.inbox.md:4,11` 逐字相同,而且內容已經寫進 `design.md:19-21`、`opener.md:13`、`main.md:3`。
- `memory/model/opus.inbox.md:2-5` 是同一句重複 4 次;`:1` 的「第二條原則 (#1…)」是測試殘留。
- `memory.py note` 現在已經會去重(`memory.py` 的 `already_noted`),所以這些是歷史殘留,清文字就好。
- 處置:prompt 票 P2。嚴重度:T

### F23 「只准跑的測試」這條規則有五份正文
- `implementer.md:8` 與 `:13`(同一張卡寫了兩次)、`DISPATCH-TEMPLATE.md:83-87`、`templates/dispatch-verifier.md:76-79`、`role-procedures.md:49`。
- 建議只留 `DISPATCH-TEMPLATE.md §1`,加上 auto-fix 寫進派工文的那一段;卡上只留一句指路。
- 處置:改文字。嚴重度:T

### F25 驗證者什麼時候起:四處說法,其中一處過期
- `verifier.md:5`、`role-procedures.md:122-123`、`tickets/SCHEMA.md:18`、`docs/SESSION-START.md:50`。SESSION-START 還寫「與 worker 平行,#51」,沒更新成 #60 的 `interface_fixed`。
- 建議只留 SCHEMA:18,其他三處改成指路。
- 處置:改文字。嚴重度:T

### F27 result 區塊的鍵表漏了 `verify` 與 `verdict`
- `tickets/SCHEMA.md:56-69` 與 `DISPATCH-TEMPLATE.md:451-464` 兩張表都沒有 `verify`;但 `dispatch-verifier.md:44-46` 要驗證者寫,`apply.sh:763` 也真的會讀。reviewer 的 `verdict` 也不在表裡。
- 兩張表由 `tests/test_dispatch_template.py:70` 釘成一致,所以兩張要一起改。
- 處置:改文字(加兩個鍵,測試一起過)。嚴重度:T

### F29 副本環境變數的理由已經過時
- `implementer.md:18`、`role-procedures.md:74-77` 說「派工帶的 `AC_ROOT` 讓它量主 repo、寫回主線」。auto-fix 現在把 `AC_ROOT` 設成 `<副本>/control` 這個拋棄式控制根,並拿掉 `AC_TICKETS_DIR` 與 `AC_CONTROL_DIR`(`auto-fix.sh:916-955`;`SESSION-START.md:58-60`)。要做的動作沒變(拿掉或指到副本),但後果改成「量到空的控制根」。
- 處置:改文字。嚴重度:T

### F33 主線規則包的一句話還寫著「覆核」
- `scripts/rules.py:103-105` `WANTED["main"]` 寫「接需求、覆核、決定順序、發版」,跟 D-022 矛盾;主線每次開場的包裡都看得到這句。
- 處置:開 code 票(C1,只改一個字串)。嚴重度:T

### R1 反駁行不准帶家目錄路徑:只有文字規定
- `implementer.md:16`。`ticket.py objection`、`apply.sh`、`auto-fix.sh` 都沒有替換成佔位字(實測 `grep -n "HOME\|佔位"` 沒有結果);要到 land 時才被 `test_no_project_names` 擋下,多燒一輪。`main.inbox.md:20` 已經提出這個需求。
- 處置:開 code 票(C1:收件時把家目錄路徑換成佔位字)。嚴重度:T

### R3 D-031 的票面四格自檢沒有工具
- `opener.md:9`、`role-procedures.md:24-27` 是手寫的一行 python;D-031 原文就說「要做成 `ticket.py lint`,另開票」。
- 處置:開 code 票(C1,連同 F3 的形狀一起統一)。嚴重度:T

### R4 「開題者只提交自己那張票檔」沒有程式在擋
- D-025 ③、`dispatch-opener.md:31-32`。`land.sh docs` 只檢查前綴(`land.sh:101-126`),不看 `AC_ROLE`,也不看票號。
- 處置:開 code 票(C1,低優先;`AC_ROLE=opener` 時只收 `tickets/<AC_TICKET>.json`)。嚴重度:T

### R6 「同時最多兩個會起瀏覽器的 agent」沒有程式在擋
- `main.md:10`、D-010 5。scripts 裡 grep 不到任何相關檢查。
- 處置:不處置。理由:這是主線派工時的判斷;A 端目前沒有起瀏覽器的票,真的咬到人再開票(D-030 凍結原則)。嚴重度:T

---

## 三、純整潔(C)

| # | 檔:行 | 問題 | 處置 |
|---|---|---|---|
| F21 | `reviewer.md:19-20`、`verifier.md:6`、`role-procedures.md:125` | 三處都抄了 T #681 的案例原文(D-013:案例只用票號指路) | 卡上改成「(T #681)」,原文只留在 reference |
| F21b | `fable.md:15,21-23`;`opus.md:7,16` | 寫了案例經過(114 萬、#627 三條反駁全對、96 張截圖、「發生過兩次」) | P2 整理時改成原則 + 票號 |
| F24 | `CLAUDE.md:18`、`main.md:10`、`implementer.md:14`、`verifier.md:7`、`dispatch-verifier.md:80-81` | 「不准輪詢」寫了五處 | 主線留 CLAUDE.md,短命角色留 DISPATCH-TEMPLATE;卡上刪掉 |
| F26 | `implementer.md:11`、`verifier.md:14`、`role-procedures.md:59-60,117-118`、`DISPATCH-TEMPLATE §1` | patch 檔頭的規則寫了多處,而 `apply.sh` 已經在擋(D-015 1) | 卡上只留一句「apply 會拒」 |
| F28 | `DISPATCH-TEMPLATE.md:413` | §8 第 1 點要回報 patch 的「絕對路徑」,跟不准帶家目錄路徑的精神相反 | 改成相對副本根的路徑 |
| F30 | `dispatch-verifier.md:38,62` | 「主線收件時 / 主線套 patch」:實際是 auto-fix 在跑 apply | 改字 |
| F31 | `design.md:8` | 「§6.4」沒寫是哪一份文件 | 寫成 `DISPATCH-TEMPLATE §6.4` |
| F32 | `memory/model/gemini-3.8-flash-high.md:4` | 內容是路由事實,不是「這個模型會怎麼錯」 | 改成「尚無條目」;路由交給 config |
| R5 | `dispatch-reviewer.md:7-8` | 「不改任何檔」:Edit / Write 已經拿掉,但 Bash 還在 | 不處置:唯讀覆核要用 Bash 跑 `git diff`,風險可接受 |
| R7 | `memory/role/README.md:29` | 角色卡 ≤ 40 行明寫「程式不量」 | 不處置:已經是有意的文字規矩(目前最長的 reviewer 是 34 行) |
| R8/R9 | `opener.md:5`(outline ≤300 字)、各卡 token 上限 | 程式不量 | 不處置;`cost[]` 已有數字,主線抽查就好 |

---

## 四、逐項查證指令(實測摘要)

- `memory.py lint --file <每一份卡> --json`:命中數見 F20;跑完 `git status --short` 是空的。
- `grep -n "D-H38" docs/DECISIONS.md` → 0 筆(F17)。
- `sed -n 93,118p scripts/rules.py` → `WANTED` 名單(F2、F33);`sed -n 625,660p` → `below_floor`(F5)。
- `sed -n 47,52p;236,245p;495,520p scripts/ticket.py` → `REQUIRED` 沒有 `needs_verifier`;`normalise_verify` 會產 dict(F3、R2)。
- `grep -n "consolidate\|--candidate\|--discussion" scripts/memory.py`;`grep -n consolidate-memory scripts/new-session.sh`(F12)。
- `grep -n "InReview\|AwaitingReview\|review.pass" scripts/review.sh`;`grep -n "RETIRED_ROLES\|TEMPLATE_LIST" scripts/sync-to-project.sh`(F10、F19)。
- `grep -n "commit\|merge" scripts/guard-main.sh`;`.claude/settings.json` 的 PreToolUse(F6)。
- `sed -n 896,960p scripts/auto-fix.sh` → 控制根與 `AC_*` 剝除(F15、F29);`sed -n 1305,1330p` → 判反駁派工文(F4)。
- `grep -rn "schedule.proposed" scripts/ tests/` → 只有 `event.py:38`(F18)。
- `wc -c docs/DISPATCH-TEMPLATE.md docs/HANDOFF.md` → 33029 / 8361(F14)。
- `board/config.json`:`routing.open/main = opus`、`opener.command` 的工具含 `Write Bash`、`reviewer.command` 只有 `Read Glob Grep Bash`(F4、F5、R5)。

---

## 建議開票

依使用者 10/03 的分法,code 票與 prompt 票分開,合併成最少張數。

### P1(prompt)短命角色的規格對齊:卡、reference、範本、DISPATCH-TEMPLATE、CLAUDE.md
- 收:F1、F2、F3(文字的部分)、F4、F5、F7、F8、F9、F10、F11、F12、F13、F14、F15、F16、F18、F23、F25、F26、F27、F28、F29、F30、F31。
- 寫入範圍:`memory/role/{opener,implementer,verifier,reviewer,consolidator,design,main}.md`、`docs/reference/role-procedures.md`、`docs/DISPATCH-TEMPLATE.md`、`templates/dispatch-*.md`、`CLAUDE.md`、`docs/SESSION-START.md`、`docs/ROLES.md`、`tickets/SCHEMA.md`。會牽動的測試:`tests/test_dispatch_template.py`(兩張 result 表要一致、每個角色的包都切得出節)、`test_rules_delivery.py`、`test_templates.py`。
- 注意:`implementer.md` 目前 2425/2700、`opener.md` 1984/2000,只能減字;閘門會 lint 新增的行(D-042),新寫的句子不能帶路徑或指令。
- `needs_verifier`:true(F27 要改兩張表,有測試釘著)。

### P2(prompt)記憶整理:主線卡、模型卡、三份 inbox
- 收:F6、F19、F20、F21、F21b、F22、F24、F32。
- 依 D-007 / D-013 走整理流程(兩模型,或比照 D-043 由 fable 單獨整理);產出要讓 `memory.py lint` 對這七份降到 0 條,或每條都有 `memory-allow`。
- 依賴:P1 先落地。兩張都會動 `main.md` 與 `consolidator.md`,先後做才不會衝突。

### C1(code)票面與規則包的機械檢查
- 收:R2(`set state Ready` 時缺 `needs_verifier` 就拒絕)、R3 + F3(`ticket.py lint` 取代手寫自檢,`verify_strings` 認字串與工具產的 dict 兩種形狀)、R1(`ticket.py objection` 收件時把家目錄路徑換成佔位字)、F33(`rules.py` 主線那一句拿掉「覆核」)、R4(低優先:`land.sh docs` 在 `AC_ROLE=opener` 時只收自己那張票檔)。
- 寫入範圍:`scripts/ticket.py`、`scripts/rules.py`、`scripts/land.sh`、`tests/test_ticket.py`、`tests/test_rules.py`、`tests/test_land.py`。
- 跟 P1 互不依賴,可以平行;但 P1 改 `opener.md` 自檢段時,要指到 C1 的新指令。建議 C1 先落地,或 P1 那一句先寫「工具上線前照舊」。

### 不開票(主線自己走 docs 通道)
- F17:DECISIONS 補 D-H38 一列(或改成 A 的編號)。
- F10 的後半:D-010 ③ 標「⛔ 由 D-022 / D-025 ② 取代」。
