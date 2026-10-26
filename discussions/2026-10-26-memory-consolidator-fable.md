---
topic: 整理 memory/role/consolidator.md 與 consolidator.inbox.md(票 #174;D-053 ⑥ / D-007 / D-043 單一高階整理者)
kind: memory-consolidation
parties: [fable / consolidator(D-043 / D-049 ②:角色表 consolidator 鏈起的一個 session,沒有第二位)]
stop:
  max_rounds: 1
  budget: "一輪,≤ 40K token"
started: 2026-10-26T11:00:00+00:00
---
model: fable
source_lines: 1
source_sha256: dbd21ba23a8f7d661e0017c41d0a99e60ff68df6f130e7bc6dc9e157415845de

## 背景
- 改前(實測 2026-10-26):`memory/role/consolidator.md` 37 行,lint 0 條;inbox 1 行(`check-stale`:needs-review,來源 #122 Done)。
- 開票原因:暫存區最舊一條放滿 3 天(D-055 ①)。
- 那一條說卡上「兩條路,擇一」已過時:整理只由角色表 consolidator 鏈起一個 session、沒有互讀;合併工具收啟動端記在
  整理票 attempt_history 的那一份討論檔;要換掉「單一高階模型走 worker 管線、不叫合併工具」與「列得出雙方的分歧」。

## 第 1 輪 — fable
- 主張:那一條是對的,而且卡上過時的不只它點名的兩句 —— 「做 ③ 合併:單模型那條路在票分支 commit」、
  「不做:不刪自己看不懂的那一條(先寫進討論檔問對方)」都預設有第二位,也一併改寫。
  卡改成單一整理者的三段流程(快照 → 逐行處置寫理由 → 候選檔 + 合併工具),並把合併工具會擋的那幾件寫進卡,
  讓整理者在跑合併前先自查、省一輪拒絕。
- 證據等級:讀 code 推的(`scripts/memory.py` `launcher_recorded` 只認 attempt_history 有記、檔頭 model 一致的一份;
  `cmd_consolidate` 一份 + 啟動端紀錄就放行,否則要兩份不同 model;`scripts/consolidate-memory.sh` 檔頭「一個 session」、
  起 session 前先把討論檔路徑與模型寫進 attempt_history)+ 引用(`docs/reference/role-procedures.md`「整理者角色操作細節」
  同一說法;D-043、D-049 ② 記憶整理者 fable)+ 實測(本票:啟動端已把 `discussions/2026-10-26-memory-fable.md` 記進 #174
  attempt_history,`snapshot` 印 1 行、sha 如檔頭)。
- 什麼會讓我改變主意:角色表 consolidator 鏈若還有第二格、或啟動腳本有任何路徑起兩個 session,
  「兩條路」那段就該留著 —— 讀過的 code 裡沒有。
- 對 D-013 ②「兩個模型互讀」那段理由的處置:它解釋的是「為什麼不能自己整理自己」,仍成立,但單一高階整理者的風險
  方向相反 —— 它不是作者,容易看漏作者看得懂、自己看不懂的那幾條。卡上留這一句當思考方式,配「作者沒寫清楚的那一條,
  寫出你猜它要守什麼、為什麼仍刪或仍留」(派工範本原話),取代「問對方」。

## 正文逐行處置(memory/role/consolidator.md,改前行號)
- L1: 保留 —— 標題補 D-043。
- L3: 保留 —— 開場只讀卡 + 票,原樣。
- L5–L6: 保留(改寫) —— 誰派:補「起一個 session、模型照角色表 consolidator 鏈、票上的 model 不參與」(#122)。
- L8–L12: 歸檔 —— 「兩條路,擇一」整段:兩模型互讀的路已沒有程式起它(#122);「走 worker 管線、票分支 commit、不叫合併工具」
  與現行合併工具的單一討論檔路徑相反。D-013 ② 的理由句改寫成單一整理者的盲點,留在卡上。
- L14–L17: 保留(改寫) —— 做:① 讀原則留原則(原樣);② 「列得出雙方的分歧」改「每一行寫處置與為什麼;作者沒寫清楚的那一條,
  寫出你猜它要守什麼」;③ 合併改「候選檔 + 合併工具,討論檔給啟動端記進票的那一份;人工派的照舊兩份」。
- L19–L21: 保留(改寫) —— 不做:「問對方」改「先在討論檔寫出它可能要守什麼,再判」;補「不改模型政策」(派工範本原話)。
- L23–L24: 保留 —— 判準句原樣。
- L26: 保留 —— 交付物原樣。
- L28: 保留 —— 何時結束補「關整理票」。
- L30: 保留 —— 上限原樣。
- L32–L33: 保留 —— 遇到就查原樣。

## 結論
- 結論:卡從「兩條路擇一」改成單一整理者(角色表 consolidator 鏈起一個 session)的三段流程;inbox 1 行處置如下。
- 採用的證據:讀 code 推的(合併工具與啟動腳本)+ 引用(reference 同節、D-043 / D-049 ②)+ 實測(本票啟動端紀錄與快照)。
  讀 code 推的不單獨撐結論:本票本身就是走這條路起的,合併那一句 rc 0 即為實測。
- 保留的分歧:無(單一整理者,D-043)。
- 產出:`memory/role/consolidator.md` 新版(候選 `discussions/2026-10-26-memory-consolidator.candidate.md`)。
- 這次討論教了誰什麼:
  - fable:角色卡上描述「流程長什麼樣」的段落,比原則更容易過時;流程改了要連「誰派 / 做 / 不做」三段一起對,只換點名的兩句會留下自相矛盾。
- L1: 保留 —— 併入卡的「誰派」「做」「不做」三段(#122,2026-10-18,實測;2026-10-26 整理時對著合併工具與啟動腳本的 code 核過)。
  它要守的是:卡不能把整理者引到一條程式已不走的路(票分支 commit、不叫合併工具),否則整理者會做完卻消耗不了 inbox。
