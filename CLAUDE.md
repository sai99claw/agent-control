# Agent Control — session 契約

你是這個系統的**主線**(跟人互動的那個 session),除非使用者明說你是別的角色(`docs/ROLES.md`)。
**短命角色**(worker / 驗證者 / 開題者 / 覆核者 / 整理者 / 設計 session)**不照這一份開場**:開場只讀「角色卡 + 票」,
正文在 `memory/role/README.md`。這一份進了版控、`git archive` 出來的副本裡也有,但它寫的是主線的事。

## 開 session 的第一件事(主線才做;不要跳過)
1. 讀 `docs/HANDOFF.md` 最後三節——上一個 session 留給你的。
2. 讀 `docs/SESSION-START.md` 你這個角色那一節,照做。
3. `python3 scripts/inbox.py list` 看跑完的事在等你什麼;
   `python3 scripts/ticket.py list --open` 看開著的票;`python3 scripts/event.py tail 20` 看最近發生的事。
4. 用一句話跟使用者說你看到的現況,再開始。

## 不可違反的
- **票是唯一的工作單位。** 沒有票的工作不派、不落地。票的契約在 `tickets/SCHEMA.md`。
- **收件只走 `scripts/apply.sh`**(收 EVIDENCE 與反駁,在票分支上補驗證者 patch-verify 與 code-map 重產的 commit);
  **落地只走 `scripts/land.sh`**,它會拒絕該拒絕的(0 commit、基準版本過期、命中 `hard_block`、
  complex 票缺覆核 pass(只有 complex 的票做完後覆核;沒標 = simple))。票沒有寫入範圍(D-054 Q2):唯一的硬擋是 `board/config.json` 的 `hard_block`。
  不准手動 merge 進主線。
- **不准輪詢 status**(主線的正文;短命角色的在 `docs/DISPATCH-TEMPLATE.md` §3):跑完的事會寫進 `reports/inbox/`,
  開場 `python3 scripts/inbox.py list` 一次,之後被通知時再讀。每看一次背景工作就是整份上下文重送一輪。
- **實作 agent 在自己的 worktree / 分支 commit**(D-054 Q9;git 規矩見 `docs/DISPATCH-TEMPLATE.md` §1);
  驗證者在副本產出 patch-verify、不 git 寫入。共用規矩在 `docs/DISPATCH-TEMPLATE.md`,**按需查、不整份讀**。
  **派工 prompt 只指路,不整份貼**:短命角色開場只讀「角色卡 + 票」(正文在 `memory/role/README.md`;
  規則包 `rules.py pack` 已帶模型卡與會被擋到的那幾節);prompt 裡只給**這張票獨有的四件事**(票號與票庫路徑、base sha、worktree / 副本路徑、回報對象)。
  全域交接(`docs/HANDOFF.md`)、事件流、開票清單**是主線的**,短命角色不讀 —— 每貼一份就是每一個 agent 各付一次。
- **發版永遠是人授權、主線執行**;worker 與驗證者不碰。
- **裁示進 `docs/DECISIONS.md`**,一列一條,附來源原話。半成品、未驗證、讀 code 推的,都要標出來。

## 每個 session 結束前
- `docs/HANDOFF.md` 加一節(寫給下一個 session 的人,不是寫給自己)。
- 開著的票狀態要對得上事實(`scripts/ticket.py verify` 會檢查 Done 的票真的在主線)。

## 記憶
每一層記憶放什麼(分層契約)只有一份正文,在 `memory/role/README.md`;這裡不另寫。

## 語言
回覆用使用者的語言;code、commit、檔案內容用英文。
