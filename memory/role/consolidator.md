# 整理者(consolidator;兩個不同的模型各一個 session,D-006 / D-007 / D-013)

**誰派**:`scripts/memory.py check` 量到記憶檔超 `cap_chars`(或 inbox 超行數)時**自動開票**,
role=consolidator、`model` 是 `board/config.json` 的 `memory.consolidators` 兩個模型;
主線照 `templates/dispatch-consolidator.md` 各開一個 session。

**為什麼是兩個模型**:一個 session 刪自己的記憶時,**最先刪掉的是它自己看不懂的那幾條** ——
而那正是別的模型看得出價值的那幾條(D-013)。

**做**:
① 各自讀超標的那一份與它的 `.inbox.md`,把**具體的原則、行為準則、思考方式**留下來;
② 照 `docs/DISCUSSION.md` 的格式寫 `discussions/<date>-memory-<model>.md`,
   **列得出雙方的分歧**(沒有分歧就寫「同意,理由是…」,不要留白);
③ 兩份討論檔都在了,跑 `python3 scripts/memory.py consolidate <檔> --discussion <討論檔>`。

**不做**:**不直接寫案例**(案例用票號指路,例「快照層只畫說得出處的畫面(#585)」);
不刪自己看不懂的那一條(先寫進討論檔問對方);沒有討論檔不准 consolidate(D-007,工具會擋);
不改 `docs/DECISIONS.md`(案例原文留在紀錄類文件);不 git 寫入。

**上限提高也是一種結論**:壓不下來就把上限提高,但 `cap_history` 要有一列寫得出
**多讀的那幾百字省了什麼**(D-007)。

**交付物**:壓縮後的記憶檔(或提高上限 + `cap_history` 一列)、兩份討論檔、
`memory.consolidated` 事件(`consolidate` 自己發)。整理票走 `ticket.py close`。

**何時結束**:`scripts/memory.py check` 對那一份退出碼 0(或上限已合法提高)就結束。

**上限**:一次整理 ≤ 40K 自己的 token。
