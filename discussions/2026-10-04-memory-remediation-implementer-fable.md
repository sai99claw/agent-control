---
topic: 整理 memory/role/implementer.md 與 implementer.inbox.md(票 #78,D-013 3 / D-043 fable 單模型)
kind: memory-consolidation
parties: [fable / worker(D-043:單一高階模型,不跑 consolidate)]
stop:
  max_rounds: 1
  budget: "一輪"
started: 2026-10-04T12:00:00+08:00
---
model: fable
source_lines: 14
source_sha256: 677bd8c64191a3160ffcd21408a1e567c6a1922da3940ceeed4f9c21eb4f6e31

## 背景
- 改前(實測 2026-10-04):正文 2700 / 2700 **到頂**(檔上的上限,cap_history 一筆 2000 → 2700,2026-09-27)、19 行(L1–L5 是 front matter);lint 30 條(sha ×13、flag ×9、path ×6、call ×3,分布 L6、L8、L10–L12、L14–L19);inbox 14 行(6 條 needs-review:#72 ×2、#73、#76、#89 與 #68 之外的來源票已結案)。
- 目標(票 B1/B2/B3(a)(b)):lint 0、正文 ≤ 2550(上限 2700 不動,留 ≥ 150 給 #88 P14)、L14「交付前的回歸」與 L17「票寫錯」兩句照 #87 A18 與 D-041 改寫;不寫 P14「被接回時從原副本續做」。
- 判準:D-013 3;指令、旗標、路徑、sha 移到 `docs/reference/role-procedures.md`「實作者角色操作細節」;cap_chars / cap_history 一字不動。

## 第 1 輪 — fable
- 主張:19 行(含 front matter 5 行)壓成 18 行、2307 字元;13 個 commit 指紋全部出卡(來源票號留、指紋進 reference「歷史來源」);14 條 inbox:11 升格(其中 2 條一半進 reference)、3 移至 reference。
- 證據等級:實測(`memory.py lint` 0 條、`check` 2307 / 2700、`rules.py inspect implementer --model opus` ok、四支守衛 Ran 87 OK)。
- 什麼會讓我改變主意:覆核指出被我移至 reference 的 #62 / #76 曾讓一般票(不是跑 new-session / 改 control_harness 的票)多跑一輪 → 升格回卡。

## 正文逐行處置(memory/role/implementer.md,改前行號)
- L1: 保留 —— front matter 起始,一字不動。
- L2: 保留 —— cap_chars 2700,一字不動(票 B2)。
- L3: 保留 —— cap_history 標頭,一字不動。
- L4: 保留 —— cap_history 那一筆,一字不動。
- L5: 保留 —— front matter 結束,一字不動。
- L6: 保留 —— 標題;刪尾巴 `(84864f3,2026-09-13,實測)`(lint sha):指紋進 reference「歷史來源」。
- L7: 保留 —— 空行。
- L8: 保留(改寫,票 B3(a) 的一半) —— 「照票面做 / 變異四步 / 票面是規格現況是事實」原樣;「跑票閘門對照表挑到的那一組」改「只跑派工文『只准跑的測試』段列的測試(票沒有 test_plan 就只跑自己改到的測試檔)」(#87 A18 落地的派工文段名);`--branch` 空閘門改白話「分支形式的閘門是空閘門(用檔名形式叫)」;把原 L18 的 #28/#49(base 複本對照證明紅來自環境)與 inbox L14 的 #89(副本沒有 git、靠 git 清單的測試必紅)併成一句放這裡 —— 它們是「怎麼跑測試」的同一題;sha / 日期刪。
- L9: 保留 —— 寫入範圍(D-H38),原樣。
- L10: 保留 —— 不做;sha / 日期刪;`CLAUDE.md`(lint path)改「專案規範」;`verify/**` 改「verify 目錄」;「反向查夾具預設值」併入 inbox L7 後半(#70:grep 有沒有案例靠「沒設就退回」)。
- L11: 保留 —— 交付物;`patch.diff` / `EVIDENCE.md`(lint path、sha)改「patch 檔」「EVIDENCE」;「檔頭只准 base/… / work/… 的相對形式」改白話「檔頭只准 base 與 work 的相對形式」。
- L12: 保留 —— EVIDENCE 五段;sha 刪;「每條新增或改動的案例旁一行 `四問:`(派工範本 §5.8,#68)」併入 ③(inbox L1);與 L13 合為一段。
- L13: 保留 —— result 塊(D-017、#20 驗收⑦)併入 L12 段尾,字句不變。
- L14: 保留(改寫,票 B3(a)) —— 原句「交付前必跑 `scripts/verify.py --tag <…>`、整支瀏覽器模組;時序類的票再在負載下(`nice -n 19` + 4 支 `yes`)跑 3 次;三組 Ran/OK 貼進 EVIDENCE」改為「**回歸不是你的事**(#87 A18):只跑派工文『只准跑的測試』段列的測試;回歸是閘門的事,閘門紅由自動派工下一輪處理,全套只在落地跑一次;負載下重跑只在票的 test_plan 列了才做」。依據:#87 A18 已落地(派工文逐字抄票的 test_plan + 固定一行「不跑全套」),使用者 2026-10-03 裁示 worker 時限與回歸切開;#88 P4 是同一條規矩的流程文字,照本卡對齊。原句的指令、負載配方、3 次、#642 / #639 窗口要求移至 reference「票閘門的叫法」(票 test_plan 列了負載重跑時照那裡做)。「一輪把同一層的 bug 全露出來」一句刪:它是舊「全跑一遍」的理由,與新規矩相反。
- L15: 保留 —— 不准輪詢;sha 刪;`^Ran |^OK|…` 擷取 regex 與 `claude -p`(lint flag)移至 reference,卡上改「只擷取 Ran / OK / FAILED / FAIL / ERROR 那幾行」「非互動 session 不會再醒」;併入 inbox L7 前半(#70 全套超過前景時限就逐模組分批)。
- L16: 保留 —— 紅了誰修;sha 刪;`reports/t<票號>/<run_id>/status.json`(lint path)改「那一輪的狀態檔」,路徑進 reference「交付物的形狀」;併入 inbox L11(#72 同一輪被重派、副本已重展的重放與對照)。
- L17: 保留(改寫,票 B3(b)) —— 原句「停,寫成票的 objections[] 一筆(…)再報主線」改為「寫成票的 objections 一筆(category、body、evidence、owner;阻擋就 blocking)後交件停下,不報主線 —— 反駁由自動派工起開題者判、判完接回(D-041,#89)」。依據:D-041(2026-10-04)反駁不進主線收件匣,#89 已落地 auto-fix 起開題者判。保留:「沒被收進票的反駁與沒有反駁長得一樣」、test_defect 那一半(不准放寬斷言、不准改 oracle,案例由驗證者修)、「太複雜提反駁開票、不准繞路」(D-018)。不寫「被接回時從原副本續做」(#88 P14)。併入 inbox L3(#53 反駁行不准有絕對家目錄路徑或專案名)。sha / 日期刪。
- L18: 保留(改寫) —— 副本環境的原則句保留(派工帶著 AC 環境變數、重現對不上先看它們 #7、base 一字不動、交件前列出只在 work 有的檔);所有指令(`env | grep ^AC_`、`env -u …`、`git ls-files`、`cp -R work` + `git init`、`PYTHONDONTWRITEBYTECODE=1`、`diff -rq … | grep '^Only in work'`、`.env-suspect.json`、「交完刪掉 work/ 與 base/」;lint flag ×5、path ×1、call ×2、sha ×1)移至 reference「副本環境」;#28/#49 那句移到 L8;併入 inbox L2(#53 會發事件的腳本在暫存拷貝跑)、L5(#59)、L8(#71)、L9(#71 子行程釘根目錄);卡上指路「指令見 reference『實作者角色操作細節』」。
- L19: 保留 —— 測試與 patch 的寫法;`diff(舊 work, 新 work)`(lint call)改白話「以新舊 work 的 diff 反向核對」;併入 inbox L4(#57)、L10(#72 恆真斷言)、L12(#73 合併案例);日期刪。

## 結論
- 結論:implementer 正文 2700 → 2307 字元(19 → 18 行,front matter 5 行一字不動),lint 30 → 0,L14 / L17 照 #87 A18 與 D-041 改寫;inbox 14 行全部處置、清空;留給 #88 P14 的空間 393 字元(≥ 150)。
- 採用的證據:實測 `memory.py lint --json` ok、`check --read-only` 2307 / 2700(檔上的上限)、`check-stale` rc 0(改前 6 條 needs-review)、`rules.py inspect` 角色卡 3 個正文單位被包入(改前 2)、四支守衛 Ran 87 OK。
- 保留的分歧:無(單模型,D-043)。
- 產出:memory/role/implementer.md 新版;docs/reference/role-procedures.md「實作者角色操作細節」;docs/review/memory-remediation-a.md。
- 這次討論教了誰什麼:
  - fable:規則包 4096 B 只包得下 implementer 卡前 3 段 —— 卡的段落順序就是優先序,「做 / 寫入範圍 / 不做」要排最前;指令出卡後正文仍 2307,下一次要騰空間先看「副本環境」與「測試與 patch 的寫法」兩段能不能再往 reference 搬。
- L1: 升格 memory/role/implementer.md「EVIDENCE 必備五段」③ —— #68 四問是每條新增 / 改動案例的行為準則;§5.8 出處保留為「派工範本 §5.8」,檔路徑進 reference。
- L2: 升格 memory/role/implementer.md「副本環境」(原則:會發事件的腳本在暫存拷貝跑,#53)+ 移至 reference「副本環境」(`git archive` 對 base `diff -r` 的細節)。
- L3: 升格 memory/role/implementer.md「票寫錯 / 需要裁示 / 案例本身錯」—— #53「反駁行會被原文抄進票檔,裡面不准有絕對家目錄路徑或專案名」;掃它的測試名進 reference「反駁的形狀」。
- L4: 升格 memory/role/implementer.md「測試與 patch 的寫法」—— #57 縮成「shell 變數後面緊接全形標點要加大括號,否則位元組被吃進變數名」;`${var}` / `set -u` 字樣進 reference。
- L5: 升格 memory/role/implementer.md「副本環境」—— #59 與 L8 合成一句「在副本跑任何腳本前把它們拿掉或指到副本自己,否則量的是主 repo 的檔(假綠)、票與事件寫回主線」;`env -u …` 指令進 reference。
- L6: 移至 reference「副本環境」—— #62 只在實跑 new-session / session-hook 的票會用到(真專案根先複製 board 資料到 TMPDIR),不是每票的行為準則。
- L7: 升格 memory/role/implementer.md「不准輪詢」(前半:全套超過前景時限就逐模組分批、合計 Ran 與各批 rc,#70)與「不做」(後半:給共用夾具加預設值前查有沒有案例靠「沒設就退回」,#70);數字(600s、878 條、270s、<590s)與 `control_harness` / `DEFAULT_CONFIG` 名稱進 reference。
- L8: 升格 memory/role/implementer.md「副本環境」—— #71「寫票指令前先把根指到副本自己,否則票與事件寫回主線、事件行收不回」,與 L5 合成一句;`AC_ROOT=$PWD` 進 reference。
- L9: 升格 memory/role/implementer.md「副本環境」—— #71「測試子行程呼叫本樹腳本時要釘根目錄,不釘就讀主 repo、變異驗不紅」;`env AC_ROOT=ROOT` / `event.repo_root()` 進 reference。
- L10: 升格 memory/role/implementer.md「測試與 patch 的寫法」—— #72「稽核票改寫恆真斷言,同一變異也在 base 的舊案例上跑一次,舊綠新紅才證明改寫有對象」,原句就是原則。
- L11: 升格 memory/role/implementer.md「紅了誰修」—— #72「同一輪被重派、副本已重展:先重放上一次的逐字替換,再用方法數 / 斷言數 / 逐檔 diffstat 對上一次 EVIDENCE 證明一致;變異與閘門本輪重跑,數字不沿用」;`assert count==1` 腳本與 AST 字樣進 reference。
- L12: 升格 memory/role/implementer.md「測試與 patch 的寫法」—— #73「合併案例時舊 docstring 寫的『變異 → 紅』要在 base 重跑一次再抄,抄不過去就換一條真的會紅的變異」。
- L13: 移至 reference「副本環境」—— #76 `control_harness` 沙盒 config 的 `memory.applies_to` 只有 model 層,是測試夾具的專屬細節,只對改 memory 守衛的票有用。
- L14: 升格 memory/role/implementer.md「做」—— #89「副本沒有 git,靠 git 清單的測試必紅」併入「在加了 git 的暫存複本跑,並用 base 複本同條對照證明紅來自環境(#28/#49/#89)」;掃到 0 個檔的測試名與「改動檔自己 grep 那四個字樣」進 reference。
