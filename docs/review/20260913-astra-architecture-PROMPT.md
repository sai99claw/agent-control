> **歷史文件(2026-09-13 的快照)。** 裡面講的「調度員 / dispatcher / 排程器」這個角色
> **2026-09-21 已整個退場**(`docs/DECISIONS.md` D-010):順序由主線決定,紅了由落地器自動派新 worker。
> 這一份保留原文,不要拿它當現行規範。

你是「agent-control」(一套讓多個 AI agent 協作開發軟體的規範與工具:票系統、調度員 session、落地腳本、模型私有記憶、看板)的架構審稿人(Codex gpt-6-astra)。唯讀:只讀 ./work(repo 乾淨副本)、./TABBY-LESSONS.md(第一個使用它的專案 tabby_pool 三天內的裁示 D-G112/D-G113 與派工路由)、./DISPATCHER-STATE-SAMPLE.md(真實的調度員交接檔樣本)。不改檔、不跑 git、不連網。用繁體中文,輸出寫到 ./REVIEW.md,回覆時貼「P1」與「建議改善的前五件」。

先讀 work/README.md、work/docs/DESIGN.md、ROLES.md、WORKFLOW.md、SESSION-START.md、MEMORY.md、CODE-MAP.md、DISCUSSION.md、DISPATCH-TEMPLATE.md、REHEARSAL.md、TODO.md、DECISIONS.md、tickets/SCHEMA.md、scripts/*.sh、scripts/*.py、board/board.py、tests/。

設計者的七個核心想法:code 速查、專案共用記憶、模型種類私有記憶(2K 上限、老師帶學生式整理)、票系統、開新 session 的規範、控制儀表板、開發排程 session(調度員)+ 與人互動的 session(主線)。

請回答:
1. 整體架構合不合理?哪些部分是「規範」而沒有「機制」(靠人記得),哪些已有機制(腳本/測試/鎖)?列表。
2. 對照 TABBY-LESSONS:tabby_pool 用了三天就改了三次(落地腳本化+鎖、驗收獨立成短命覆核者、票由短命開題 session 開、調度員 20 萬 token 上限)。agent-control 的 docs 與 scripts 現在跟上了嗎?哪些還停在舊模型?
3. 票放在 repo 的 tickets/*.json 會與落地鎖排隊(TODO 有記);你的建議是什麼?
4. 模型私有記憶 2K 上限與「討論檔才能整理」機制:有沒有假保證(看起來有守、其實守不住)?
5. 看板 board.py 讀 tickets + events:agent 的行為有沒有真的反映到看板?哪些狀態看板看不到?
6. 「clone 之後就能開始用」這個承諾:一個新專案要接上,還缺什麼?
7. 最容易假綠的三個地方(規則存在、但違反時沒有東西會紅)。
最後:P1(必改)/P2(建議)/P3(可忽略)清單,以及「建議改善的前五件」各附一句怎麼驗證改對了。
