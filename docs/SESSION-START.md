# 開 session 的固定動作

## 所有角色
1. `git log --oneline -1 main` 與 `git status --short`:知道自己站在哪個版本、工作樹乾不乾淨。
2. 讀 `docs/HANDOFF.md` 最後三節。
3. `python3 scripts/event.py tail 20`:最近發生的事;`python3 scripts/ticket.py list --open`。
4. **發一筆事件**:`scripts/event.py emit session.start --role <role> --model <model>`。控制台從此看得到你。
5. 讀 `memory/model/<你的模型>.md`(你這個模型在這個專案踩過的坑)。

## 主線
- 看收件匣 `python3 scripts/ticket.py inbox`:使用者填過的裁示要落成 `docs/DECISIONS.md` 一列。
- 檢查 `scripts/heartbeat.sh`:上一個排程器 / land 有沒有死在半路(有的話清 worktree、把票狀態對回事實)。
- 用一句話跟使用者說現況。

## 排程器
- 只讀:票、`allowed_write_paths`、主線 HEAD。
- 產出一行:「可平行:#a #b;序列:#c → #d;理由:…」,發事件 `schedule.proposed`。
- **不派工、不落地、不寫文件。**

## Worker / Reviewer
- 讀派工文裡指定的副本路徑;先 `ls` 確認 `work/`、`base/` 都在。
- 讀 `docs/DISPATCH-TEMPLATE.md` §禁區與 §假綠家族。
- 開工發 `ticket.attempt.start`,交付發 `ticket.attempt.done`(附 patch 路徑)。

## 結束前(所有角色)
- 交接:主線寫 `docs/HANDOFF.md`;worker 的交接寫在回報裡。
- 發 `session.end`。
- **關票前**:`scripts/ticket.py verify <id>`(它會 `git show main:<檔> | grep` 那張票獨有的字串)。

## 角色卡(2026-09-13 起)
開場除了模型記憶,再讀 `memory/role/<role>.md`。派工 prompt 不重貼規則,只指路 + 四件票獨有的事。
主線是溝通者(不查 code、不改票面、不驗證、不逐則轉述);開題者可派子工作者搜集;調度員只排序 + git 腳本;
覆核者讀證據帳、只重播最關鍵一兩條。細節見 `memory/role/README.md`。
