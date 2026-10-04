---
topic: 整理 memory/role/opener.md 與 opener.inbox.md(票 #78,D-013 3 / D-043 fable 單模型)
kind: memory-consolidation
parties: [fable / worker(D-043:單一高階模型,不跑 consolidate)]
stop:
  max_rounds: 1
  budget: "一輪"
started: 2026-10-04T12:00:00+08:00
---
model: fable
source_lines: 7
source_sha256: 268061be2e5cb08e35a10704d7282b4cdda4572f23dc35fe50fc23faf3ee24d9

## 背景
- 改前(實測 2026-10-04,`memory.py check --read-only` / `lint --json`):正文 2449 / 2000 **超標**、17 行;lint 16 條
  (L5 ×2 path、L6 ×5 path/call/flag、L11 ×9 path/call/flag);inbox 7 行(1 條 needs-review #68、其餘 no-source)。
- 目標(票 B1/B2/B4):lint 0、正文 ≤ 1800(上限 2000 不動,留 ≥ 200 給 #88 P13)、D-031 那一句改成「比的是改動內文」說法。
- 判準:D-013 3 —— 只留原則、行為準則、思考方式;案例只留票號;指令、旗標、路徑移到
  `docs/reference/role-procedures.md`「開題者角色操作細節」。
- 本票不寫 #88 P13「判反駁」那一節(#88 自己加)。D-041 衝突句:opener 正文裡沒有「反駁 → 等主線 / 報主線」那一類的句子(逐行讀過,L3–L17 無),無需改。

## 第 1 輪 — fable
- 主張:17 行壓成 13 行、1776 字元;指令與路徑全部出卡;三條 D-H38 規則合成一段(反例票號保留);inbox 7 條:4 升格、1 重複、1 移至 reference、1 一半升格一半移至 reference。
- 證據等級:實測(`memory.py lint` 0 條、`check` 1776 / 2000、`rules.py inspect opener --model fable` ok、四支守衛綠)。
- 什麼會讓我改變主意:覆核指出某一條被我縮掉的字曾在某張票擋過一次重跑(給票號)→ 改回。

## 正文逐行處置(memory/role/opener.md,改前行號)
- L1: 保留 —— 標題,原樣。
- L2: 保留 —— 空行。
- L3: 保留 —— 「做」是核心行為準則;縮寫:刪層次例舉「(單元、API、瀏覽器)」與「跑競爭迴圈、算色組」兩個案例詞,原則一字不少。
- L4: 保留 —— 「不做」;刪腳本名 `(fullsuite/land)`(path 命中來源之一的同類);把 L5 的原則句併進來:「唯一的 git 例外:用 docs 通道落地自己那張票檔(D-025 ③)」。
- L5: 移至 reference —— 整行是一條指令(`sh scripts/land.sh docs …`,lint path ×2);原則併入 L4,指令進 reference「開題者角色操作細節 / 票的欄位契約與派工範本」。
- L6: 保留 —— 交付物;`tickets/SCHEMA.md`、`templates/dispatch-opener.md`、`ticket.py create --outline`(lint path ×3、call、flag)移至 reference,卡上改「欄位契約、範本與指令見 reference『開題者角色操作細節』」。
- L7: 保留 —— 「摘要寫進票」併入 L6 尾,保留 #29 A2 票號與「與沒交付長得一樣」那一句判準。
- L8: 保留 —— 信誰,原樣縮標點。
- L9: 保留 —— 上限,原樣。
- L10: 保留 —— 第二輪假設標已排除;刪「第 2 輪」字樣與「診斷本身有價值」的冗詞。
- L11: 保留(改寫,票 B4) —— verify_strings 說法改為「閘門比的是改動內文(diff 的增刪行與新檔全文),diff 檔頭與檔名不算,新檔就挑檔內一定會有的字,如類名、函式名」,與 #90 落地後的 `templates/dispatch-opener.md` 與 `ticket.py create --help` 一致;原句「落地對整個分支 `git grep -F`,不是檔名、不是 {path,contains}」與整條 python 自檢指令(lint call ×5、flag ×3、path ×1)移至 reference「票面四格自檢」;日期刪。
- L12: 保留 —— interface_fixed 併入新段「needs_verifier 怎麼判」(與 inbox L1/L7 同一題),原則與「拿不準寫 false」的理由保留;日期刪。
- L13: 保留 —— D-033 三個動作原樣;刪日期與「三張產品票第 1 輪全 ticket-wrong」(案例,D-033 本文有)。
- L14: 保留 —— 三條規則的標題;刪「2026-09-28;稽核 R6 / F1、F2」來源碼。
- L15: 保留 —— (a) 縮寫;「驗證者會建立的案例檔」與 #23 併入(inbox L5);反例票號 #658–#662 保留。
- L16: 保留 —— (b) 縮寫;反例 #660、#663 保留。
- L17: 保留 —— (c) 縮寫;反例 #664 保留。

## 結論
- 結論:opener 正文 2449 → 1776 字元(17 → 13 行),lint 16 → 0,D-031 那句改成「比的是改動內文」;inbox 7 行全部處置、清空。
- 採用的證據:實測 `memory.py lint --json` ok、`check --read-only` 1776 / 2000、`check-stale` rc 0、`rules.py inspect` 角色卡 6 個正文單位被包入、四支守衛 Ran 87 OK。
- 保留的分歧:無(單模型,D-043)。
- 產出:memory/role/opener.md 新版;docs/reference/role-procedures.md「開題者角色操作細節」;docs/review/memory-remediation-a.md。
- 這次討論教了誰什麼:
  - fable:ASCII 詞緊接括號會被 call 規則當成函式呼叫,寫卡時詞與括號之間要隔一個中文字。
- L1: 升格 memory/role/opener.md「needs_verifier 怎麼判」—— #8「預設要驗證者案例,不需要的只能是寫明理由的那種(改名、小修、設定同步)」是行為準則;verify_waiver 字樣改成 D-028 的 needs_verifier + 理由。
- L2: 重複 —— D-016「不追求快,只看效率」由規則包前言每份都印(rules.py EFFICIENCY_NOTE,2026-09-27 討論檔已指出),卡上再抄是白花字元。
- L3: 升格 memory/role/opener.md「取捨看之後的 token」—— D-018 是開題者下設計裁示時的判準;縮成一句,反例形狀(改名、加第二個欄位、各做各的形狀)保留。
- L4: 升格 memory/role/opener.md「取捨看之後的 token」(「跨 repo 依賴不寫進 depends_on 欄,#648」一句)+ 移至 reference「跨 repo」(sync-to-project 名單只十支、dry-run 不比內容、`cmp -s` 判準 —— 工具細節,不是原則)。
- L5: 升格 memory/role/opener.md 三條規則 (a) —— 「驗證者會建立的案例檔」列進 allowed_write_paths,#23 票號保留;「apply.sh 秒退」細節進 reference「寫入範圍」。
- L6: 移至 reference「needs_verifier 怎麼判」—— 本 repo 的驗證者案例住 tests/ 時 verify.tags 留空、rc=3 缺口,是 agent-control 專屬量法與工具行為(#29),不是通用原則。
- L7: 升格 memory/role/opener.md「needs_verifier 怎麼判」—— §5.8 第 3 問的判法與 T #679 先例縮成「驗證者案例只會重演 worker 自己案例的同一契約才寫 false」(#68)。
