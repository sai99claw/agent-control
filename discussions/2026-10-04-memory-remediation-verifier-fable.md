---
topic: 整理 memory/role/verifier.md 與 verifier.inbox.md(票 #78,D-013 3 / D-043 fable 單模型)
kind: memory-consolidation
parties: [fable / worker(D-043:單一高階模型,不跑 consolidate)]
stop:
  max_rounds: 1
  budget: "一輪"
started: 2026-10-04T12:00:00+08:00
---
model: fable
source_lines: 4
source_sha256: b4702621163bc87279de3685aa2f561c0f0e51bc7e884d3f81956983bc7d920b

## 背景
- 改前(實測 2026-10-04):正文 1788 / 2000、17 行;lint 14 條(L5 ×7 path/flag、L7 ×3、L10 ×2、L11 ×1、L13 ×1);inbox 4 行(1 unknown-ticket #630、3 no-source)。
- 目標(票 B1/B2/B3(c)):lint 0、正文 ≤ 1850(留 ≥ 150 給 #88 P15)、與 D-041 衝突的句子改寫;不寫 P15「被接回時只改受票面差異影響的案例」。
- 判準:D-013 3;指令、路徑移到 `docs/reference/role-procedures.md`「驗證者角色操作細節」。

## 第 1 輪 — fable
- 主張:17 行壓成 13 行、1532 字元;工具名與路徑全部出卡,卡上以「驗紅工具 / 對勾工具 / 套 patch 的腳本 / 自動派工」稱呼;D-041 衝突兩句(L14「不回到驗證者」、L16「退回開題者」)改寫;inbox 4 條:2 升格、1 重複、1 歸檔。
- 證據等級:實測(`memory.py lint` 0 條、`check` 1532 / 2000、`rules.py inspect verifier --model opus` ok、四支守衛綠)。
- 什麼會讓我改變主意:覆核指出某個被我改成通稱的工具名在派工文裡**沒有**出現(驗證者會找不到工具)→ 加回卡上並寫 memory-allow 理由。

## 正文逐行處置(memory/role/verifier.md,改前行號)
- L1: 保留 —— 標題;「D-G122」補「依來源專案」:它是來源專案的裁示編號,不是本 repo 的 D 編號。
- L2: 保留 —— 空行。
- L3: 保留 —— 「做(兩段)」標題,與 L4、L5 合為一段(規則包以段落為單位,三行併一段才一起被包入)。
- L4: 保留 —— ① 原則原樣;路徑 `verify/<feature>/test_ticket_<n>.py`、`verify/TAGS.d/<票號>.md` 移至 reference「案例與交付物」,卡上寫「verify 目錄裡以票號命名的案例檔」「新標籤寫成片段」。
- L5: 保留(改寫) —— ② 的原則(自己做乾淨基底、只跑一次、算數的紅只有案例自己的斷言、綠不是你的事、不搭參考實作 / 不做變異 / 不等 patch、result 欄位不編)全留;`verify-case.py red/check/extract`、`--candidate`、`baseline-red.json`、`ticket.py close`、`EVIDENCE-verifier.md`(lint path ×5、flag ×1)移至 reference;「主線收件時併進票」改為「套 patch 的腳本收件時併進票」—— D-041 / #89 後收件不經主線。
- L6: 保留(改寫) —— 「它有收件者」原則保留;「主線套 patch 時走」改「交付物由套 patch 的腳本抽成」(D-041:主線不在這條路上)。
- L7: 移至 reference「案例與交付物 / 收件」—— 整行是 `sh scripts/apply.sh … --evidence-verifier …` 指令(lint path ×2、flag ×1)。
- L8: 移至 reference「案例與交付物 / 收件」—— `reports/t<票號>/<run_id>/result-verifier-round<輪>.json` 路徑;原則「看板畫的就是那一份」留在卡上。
- L9: 保留 —— 「沒有人收的交付物不要交」原樣,併入 L6 那段。
- L10: 保留 —— 誰派;`auto-fix.sh`、`apply.sh`(lint path ×2)改稱「自動派工」「套 patch 的腳本」;`patch-round1` 改「第 1 輪 patch」;#51 / #60 票號與「非平行時介面照 worker 的 patch」原則保留;第 2 輪改寫成「介面照那份 patch,票面與它衝突時照 patch」—— 原字樣是 #60 派工文那一節的標題,`test_auto_fix` 的 interface_fixed=true 案例斷言平行派工文不含它,卡被包進每一份派工文,逐字留著就紅。
- L11: 保留 —— 四問;「§5.8」補「派工範本」;`verify/` 去斜線;反例 T #681 的 `from === 當月-01` 改白話「起點等於當月 1 日」(判準句不變)。
- L12: 保留 —— 不做,原樣。
- L13: 保留 —— 不跑 tag 回歸;`verify-case.py check`(lint path)改「閘門用的那支對勾工具」;刪日期。
- L14: 保留(改寫,票 B3(c)) —— 原句「紅了走 WORKFLOW 的自動派工,不回到驗證者」改為「閘門紅由自動派工下一輪處理,不回到你。案例的 oracle 或 fixture 錯了(test_defect)則由你修 —— 實作者不准放寬斷言、不准改 oracle 本身(D-041)」。依據:D-041 接回同一個驗證者改案例;實作者卡 B3(b) 保留的 test_defect 半句在這裡有了對應的另一半。不寫 P15 字樣。
- L15: 保留 —— 信誰,原樣。
- L16: 保留(改寫,票 B3(c)) —— 「就退回開題者,不猜」改「就提反駁、由開題者判,不猜(D-041)」:退回不再經主線,由自動派工起開題者判。
- L17: 保留 —— import 失敗不算紅,原樣(去反引號)。

## 結論
- 結論:verifier 正文 1788 → 1532 字元(17 → 13 行),lint 14 → 0,D-041 衝突兩句改寫;inbox 4 行全部處置、清空。
- 採用的證據:實測 `memory.py lint --json` ok、`check --read-only` 1532 / 2000、`check-stale` rc 0、`rules.py inspect` 角色卡 4 個正文單位被包入、四支守衛 Ran 87 OK。
- 保留的分歧:無(單模型,D-043)。
- 產出:memory/role/verifier.md 新版;docs/reference/role-procedures.md「驗證者角色操作細節」;docs/review/memory-remediation-a.md。
- 這次討論教了誰什麼:
  - fable:工具名出卡後,卡上要留一個穩定的通稱(驗紅工具 / 對勾工具),否則下一個讀卡的人連要去 reference 查什麼都不知道。
- L1: 升格 memory/role/verifier.md「patch 與案例檔的歸屬」—— #630「案例檔由你獨占、同名檔落地以你的為準、verify.files 記你那份」是歸屬規則;路徑樣式進 reference。
- L2: 重複 —— D-016 由規則包前言每份都印(rules.py EFFICIENCY_NOTE),卡上不重抄。
- L3: 歸檔 —— D-018「設計為未來 token 打算」是設計取捨的判準;驗證者不改產品碼、不放寬驗收、不做設計裁示,這條對它沒有可執行的行為;原文在 DECISIONS D-018,開題者卡與模型卡已載。
- L4: 升格 memory/role/verifier.md「patch 與案例檔的歸屬」(原則:在副本根做 base 對 work 的遞迴 diff、檔頭只准相對形式、絕對路徑套不上 / 建錯地方,#25 #26 #28)+ 移至 reference「案例與交付物」(`diff -ruN`、`$W`、`work/private/…` 細節)。
