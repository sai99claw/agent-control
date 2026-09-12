# 哪些保證還只在演練裡成立

**這不是待辦清單。** 這是「發出去了、但只在受控環境裡被觸發過」的東西
(`docs/DISPATCH-TEMPLATE.md` §5.55)。演練綠只證明「在受控環境裡這樣寫是對的」;
**新行為第一次真的被觸發的那一刻,才是它第一次在真的路徑上成立** —— 而那兩件事在畫面
上長得一樣。

規矩:發出新守衛或新工具的人,寫下**下一次它真的被觸發時要看到什麼**。
**誰在場誰確認一眼,確認過就把那一列刪掉。** 控制台第 ⑤ 段畫的就是這張表。

| 工具 | 第一次真跑是什麼時候 | 那時要看到什麼 |
|---|---|---|
| `scripts/land.sh` 的 `base_sha` 過期拒絕 | 下一次落地一條票上 `base_sha` 已經不是主線祖先的分支 | 印出那個 sha 並說「先 rebase 再重跑閘門」,`EXIT=2`,**沒有建 land worktree** |
| `scripts/land.sh` 的寫入範圍拒絕 | 下一次有分支改到票的 `allowed_write_paths` 以外 | 逐條指名越界的檔,`EXIT=2`,整批都沒進去 |
| `scripts/gate.sh` 的「對不到任何模組」 | 下一次有人加了一種新的檔而對照表還沒有那一格 | 指名那幾個檔、`EXIT=3`,而且**對得到的那些照樣跑完** |
| `scripts/ticket.py verify` 擋下的關票 | 下一次有人想關一張東西沒進主線的票 | 逐條印出 `0`,拒絕關,票停在原狀態 |
| `scripts/memory.py check` 開的整理票 | 下一次某份 `memory/model/<model>.md` 真的超過上限 | 事件 `memory.over_cap`、票開出來一次(**第二次跑不再開第二張**) |
| `scripts/memory.py consolidate` 的討論檔必填(D-007) | 下一次真的要整理一份記憶 | 沒有 `--discussion` 直接拒絕;討論檔沒有結論區是**另一句話** |
| `scripts/heartbeat.sh` 的租約到期 | 下一次有 session 或 land 真的死在半路 | 指名那一筆 start、已經幾秒、租約幾秒,並說出處置 |
| `board/board.py` 的收件匣儲存 | 下一次使用者在網頁上填一則裁示 | `answers.jsonl` 多一行、事件多一筆 `decision.answered`、**票的狀態一個字都沒變** |
