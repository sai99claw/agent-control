# Agent Control — session 契約

你是這個系統的**主線**(跟人互動的那個 session),除非使用者明說你是別的角色(`docs/ROLES.md`)。

## 開 session 的第一件事(不要跳過)
1. 讀 `docs/HANDOFF.md` 最後三節——上一個 session 留給你的。
2. 讀 `docs/SESSION-START.md` 你這個角色那一節,照做。
3. `python3 scripts/ticket.py list --open` 看開著的票;`python3 scripts/event.py tail 20` 看最近發生的事。
4. 用一句話跟使用者說你看到的現況,再開始。

## 不可違反的
- **票是唯一的工作單位。** 沒有票的工作不派、不落地。票的契約在 `tickets/SCHEMA.md`。
- **每個動作要發事件**(`scripts/event.py emit`),控制台只認事件。沒發事件的事,對系統而言沒發生。
- **落地只走 `scripts/land.sh`**,它會拒絕該拒絕的(0 commit、基準版本過期、寫入範圍越界)。不准手動 merge 進主線。
- **實作 agent 在副本裡工作、交 patch**,禁一切 git 寫入;規矩在 `docs/DISPATCH-TEMPLATE.md`,派工時整份給它。
- **發版永遠是人授權、主線執行**;worker 與驗證者不碰。
- **裁示進 `docs/DECISIONS.md`**,一列一條,附來源原話。半成品、未驗證、讀 code 推的,都要標出來。

## 每個 session 結束前
- `docs/HANDOFF.md` 加一節(寫給下一個 session 的人,不是寫給自己)。
- 開著的票狀態要對得上事實(`scripts/ticket.py verify` 會檢查 Done 的票真的在主線)。

## 語言
回覆用使用者的語言;code、commit、檔案內容用英文。
