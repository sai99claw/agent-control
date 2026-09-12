# 記憶

## 兩層
| 層 | 放哪 | 誰能寫 | 內容 |
|---|---|---|---|
| **專案共用** | `docs/DECISIONS.md`、`docs/HANDOFF.md`、`code-map/`、`memory/project/*.md` | 主線(經裁示)、知識維護 | 已接受的需求、介面、不變條件、裁示、交接 |
| **模型私有** | `memory/model/<model>.md` | 該模型的任何 session | **跟別的模型討論時發現的自身盲點**、有效的除錯法、失敗的嘗試、角色技巧 |

模型私有記憶的重點是**同模型跨 session 共享**:Opus 的下一個 session 應該知道 Opus 上次在這裡犯過什麼。每一條附:日期、來源(票號或對話)、適用範圍(專案 / 角色)、是實測還是推論。

## 容量與整理:老師帶學生(產品負責人 2026-09-12 裁示)

**模型私有記憶(每一份 `memory/model/<model>.md`)預設上限 2K**(單位:字元,`board/config.json` 的 `memory.cap_chars`;字元是唯一不需要 tokenizer 的決定性量法)。**只適用於模型私有記憶**;專案共用層(`docs/DECISIONS.md`、`HANDOFF.md`、`memory/project/`)不受這條上限管,它們有各自的形狀(裁示一列一條、交接一天一節)。

理由:這些檔是**每個新 session 的第一口空氣**,多一個字就是每一個 session 都多讀一個字。上限不是為了省,是為了逼人分辨「值得每次都讀」與「查得到就好」。

### 超過上限時發生什麼
1. `scripts/memory.py check`(session 開頭與每次寫入後跑)量到某檔超過它的上限 → 發事件 `memory.over_cap`、開一張整理票,指派給高階模型(`memory.consolidator`)。**該模型的 session 不因此停工**,只是知道自己的記憶該整理了。
2. 高階模型跟該模型(同 session 或下一個 session)**討論,用 `docs/DISCUSSION.md` 的標準格式,存成 `discussions/<date>-memory-<model>.md`**:哪些要合併、哪些降級成「查得到就好」(搬去 `code-map/` 或 `docs/`,留一行指路)、哪些是反例必須留、哪些已經過期。這一步像老師帶學生:不是替它刪,是幫它分辨。
3. 討論的產出兩種,都要寫回檔案:
   - **壓縮後的新版**(舊版在 git 歷史,不另存)。
   - **或者提高上限**:如果討論結論是「這些每一條都值得每個 session 讀」,上限可以被打破。新的上限與理由寫進該檔的 front matter:
     ```
     ---
     cap_chars: 2600
     cap_history:
       - {date: 2026-09-12, from: 2000, to: 2600, by: fable, reason: "四條 land 事故的反例各不相同,合併會失去可辨識性"}
     ---
     ```
   **提高是掙來的,不是自動的**:理由要寫得出「多讀的那幾百字替每個未來 session 省了什麼」。沒有理由的提高,`memory.py check` 視為未整理。
4. 整理期間的新筆記寫 `<model>.inbox.md`(不受上限),下一輪併入。
5. 事件 `memory.consolidated` 記錄整理者、前後大小、上限有沒有變、**討論檔路徑**。`memory.py consolidate` 沒有 `--discussion <path>` 拒絕執行。

### 整理的原則
- 保留**反例與盲點**優先於保留成功經驗——成功的做法會被範本吸收,盲點只有記憶記得。
- 每條保留「日期 + 來源票號 + 實測/推論」三個標記,壓縮不能壓掉它們。
- 未達共識的討論不寫成事實。

## 不可以的
- 未驗證的經驗不進共用層。
- 摘要不把未達共識的討論寫成事實。
- 原生工具的記憶(Claude Code auto-memory、Codex 的 AGENTS.md)各自管理;這裡的檔案是**外部保存、按需讀取**,不是取代它們。
