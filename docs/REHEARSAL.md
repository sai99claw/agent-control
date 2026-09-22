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
| `scripts/gate.sh --ticket` 的 flake 重跑 | 下一次真閘門紅在一條偶發的案例上 | 印「<案例> 單獨重跑是綠的 —— 標成 flaky」,`reports/t<n>-status.json` 的 `flaky` 有它、`failures` 沒有它;**全 flaky 時 rc 才是 0** |
| `scripts/land.sh` 的 `reports/t<n>-status.json` | 下一次真的落地一批票(綠或紅都算) | 批次裡**每一張票各一份**檔,`state=done`、`kind=land`、rc 對得上落地的結果;紅的那一次 `failures` 逐條有案例名與 excerpt |
| `scripts/metrics.py` 的 token 解析 | 下一次 auto-fix 真的用帶 `--output-format json` 的 `worker.command` 派出一輪 | 那一輪的 `worker-round<r>.log` 檔尾是一個含 `usage` 的 JSON 物件,而 `python3 scripts/metrics.py line <票號>` 的 `tokens=` 從 `未知` 變成 `已知<K>趟=<input 加 output>`;**還是 `未知` 就是產出端沒照設定跑**,不是解析壞了 —— 那兩件事在同一個「未知」上長得一樣 |
| `board/board.py` 的收件匣儲存 | 下一次使用者在網頁上填一則裁示 | `answers.jsonl` 多一行、事件多一筆 `decision.answered`、**票的狀態一個字都沒變** |
| `board/board.py` 的 `/t/<票號>` | 下一次主線真的拿這一頁去接手一張紅票(不開終端機、不貼任何一份輸出進看板) | 那一頁上四件事各自答得出來:全部 run 一列一趟(`kind`/`state`/`rc`/秒)、紅榜前 5 條的 case 與 excerpt 首行、還擋著的反駁排在最前面、`review.state_version` 與票對不上時印「這份覆核已過期」;而 `#20` 落地之前每一趟都印「worker 沒交結構化輸出」——**那一句是對的**,不是讀不到 |
| `scripts/auto-fix.sh` 的 `result` 收割 | 下一次真的有 worker 在 `EVIDENCE-round<輪>.md` 尾端交出一塊 `result` | 那一輪的 `reports/t<票號>/<run_id>/` 多一份 `result-round<輪>.json`(驗證者那條路是 `result-verifier-round<輪>.json`),裡面 `rc`、`gate.ran`、`mutations` 三格**有值而不是缺鍵**;看板 `/t/<票號>` 那一趟那一列從「worker 沒交結構化輸出」變成 `rc … ・ gate.ran … ・ mutations N 筆`。**還是那一句**的時候先看那份 json:有 `present: false` 就是產出端沒照規則包寫(`reason` 分得出是 `no-evidence` / `no-block` / `bad-json`),連 json 都沒有才是收割那一手沒跑到 —— 那兩件事在同一句「沒交」上長得一樣 |
