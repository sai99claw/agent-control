# 角色記憶(角色卡)

session 開場讀兩份:`memory/model/<model>.md`(這個模型會怎麼錯)與 `memory/role/<role>.md`(這個角色做什麼、不做什麼、交付物、信誰的證據、上限)。派工 prompt 只寫「你是 <role>,讀 memory/role/<role>.md」加這張票獨有的四件事(票號、base sha、副本路徑、回報對象)。

**短命角色到底載入什麼(2026-09-21 對齊 `CLAUDE.md` 與 `docs/SESSION-START.md`,三份說同一句話):**
`memory/role/<role>.md` + `memory/model/<model>.md` + `docs/DISPATCH-TEMPLATE.md` + **這張票** + 票的 `decision_refs` 指到的那幾條裁示。
**不載入**:`docs/HANDOFF.md`(那是主線的交接)、整條事件流、開著的票清單。
需要查的東西**用 grep 定位、讀那幾行**,不整份讀。

角色卡不受 2K 上限管(它是規範不是經驗),但每張 ≤ 40 行;超過就是把流程文件抄進來了。

原則:**每個主張只被證明一次**,載體是實作者的證據帳 `EVIDENCE.md`;上游不重做下游已證明的事。
來源:tabby_pool D-G114(2026-09-13),Astra 效率審 `docs/review/20260913-efficiency/`。
