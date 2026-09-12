# 交接

## 2026-09-12 v0.1 建 repo
- 由 tabby_pool 主線(Fable)設計文件與契約;程式部分(`scripts/`、`board/`)由 Opus 從 tabby_pool 抽出並去專案名,見 `docs/TODO.md` §1 打勾狀況。
- **還沒有任何專案用過這個 repo。** 第一個使用者會是 tabby_pool 自己(TODO §0)。
- 下一個 session 該做的:照 `docs/TODO.md` §1 沒打勾的順序做,每做一項 `ticket.py` 開一張票、發事件,讓控制台從第一天就有資料。

## 2026-09-12 午前:程式部分落地,quickstart 在乾淨 clone 走通
- `scripts/{ticket,event,memory}.py`、`{land,gate,heartbeat,new-session}.sh`、`board/board.py`、`code-map/check-stale.py`、通用 `DISPATCH-TEMPLATE.md`,**145 條測試綠**(主線自己跑過),11 條變異各紅在對的地方。
- **真跑過**:乾淨 clone → `new-session.sh` → `ticket.py create` → `event.py tail` → `board.py --port 0` → `heartbeat.sh` → `ticket.py verify`。README 的 quickstart 就是那一趟的指令。
- 走的時候抓到兩個新來的人會卡的地方(子指令 `--help` 空、不認得的旗標不說認得哪些),已修;`--help` 裡的範例有測試真的拿去跑。
- 裁示:D-006(模型私有記憶 2K、老師帶學生、上限可打破但要理由)、D-007(模型討論標準格式,整理沒有討論檔不准跑)。
- `docs/REHEARSAL.md` 有七條「還只在演練裡成立」的保證——**下一個 session 第一次真的用到某條時,確認過就把那一列刪掉**。
- 還沒做:`docs/TODO.md` §0 遷移(第一個使用者是 tabby_pool 自己)、§2 第二版。控制台只綁 127.0.0.1、沒有 key 驗證。
- 主線自己踩的坑記在 `memory/model/fable.md`(`git add -A` 掃進別人未完成的檔)。
