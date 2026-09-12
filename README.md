# Agent Control

一個人主導、多模型協作的軟體研發控制系統。**clone 下來、用 Claude Code 打開、說一句話,系統就開始運作。**

它把三天內從真實專案(tabby_pool,2026-09-08 → 09-12)踩出來的規矩,寫成程式與契約,而不是留在提示裡。中心論點只有一句:**不能只靠 agent 在對話中記得規則。**

## 30 秒開始

```sh
git clone <this-repo> agent-control && cd agent-control
sh scripts/new-session.sh main fable        # 站在哪個版本、最近的事、開著的票、記憶有沒有超標;發 session.start
python3 board/board.py &                    # 控制台 http://127.0.0.1:18905(只綁本機)
claude                                      # 開 Claude Code;CLAUDE.md 會告訴它自己是誰、先讀什麼
```

然後對它說:「我要做 X」。它會開票、排程、派工、落地,並在需要你裁決時把問題放進控制台的收件匣。

第一張票長這樣(旗標可重複的用單數,一次給一個值):
```sh
python3 scripts/ticket.py create \
  --subject "…" --objective "…" \
  --acceptance "斷言一" --acceptance "斷言二" \
  --in-scope "scripts/" --allowed-write-path "scripts/*" --allowed-write-path "tests/*" \
  --verify-string "只有這張票才有的字串" \
  --role worker --model opus --tool claude-code
python3 scripts/ticket.py list --open
python3 scripts/event.py tail 20
```
`ticket.py verify <id>` 會對主線 `grep` 那些 `--verify-string`;沒給就只能弱檢查,它會明說。

上面這段是在乾淨 clone 裡真的跑過一遍才寫的(2026-09-12):new-session → 開票 → 事件 → 控制台 → heartbeat → verify,全部走得通。

## 這個 repo 裡有什麼

| 目錄 | 內容 | 給誰讀 |
|---|---|---|
| `CLAUDE.md` / `AGENTS.md` | session 的自我介紹:你是主線、先讀什麼、不准做什麼 | 每個 session 第一件事 |
| `docs/DESIGN.md` | 系統設計(產品負責人的提案 v1.1 + 主線審稿) | 想改系統的人 |
| `docs/ROLES.md` | 五種角色的責任、交付物、權限牆 | 每個 agent |
| `docs/WORKFLOW.md` | 票的狀態機、閘門、落地、發版 | 排程與落地 |
| `docs/SESSION-START.md` | 開新 session 的固定動作(依角色) | 每個 session |
| `docs/DISPATCH-TEMPLATE.md` | 派工範本:副本+patch、禁區、**假綠家族**、查證規矩 | 派工的人與被派的人 |
| `docs/MEMORY.md` | 專案共用記憶 vs 模型私有記憶,整理政策 | 所有人 |
| `docs/CODE-MAP.md` | 讓 AI 追 code 的方案:模組卡片、驗證於哪個版本、過期偵測 | 讀 code 的人 |
| `docs/TODO.md` | 還沒做的事與遷移計畫 | 主線 |
| `tickets/` | 票的契約(schema)與票本身 | 控制台、排程、落地 |
| `board/` | 控制台網頁與事件 API | 你 |
| `scripts/` | land / gate / ticket / event 的可執行部分 | 落地與派工 |
| `memory/` | `project/` 共用、`model/<model>.md` 私有 | 所有人 |
| `code-map/` | 模組卡片與過期檢查 | 讀 code 的人 |

## 設計上的三個決定

1. **規則住在程式裡,不住在提示裡。** 票的欄位、落地的拒絕條件、寫入範圍、租約,都由 `scripts/` 與 `board/` 執行;提示只負責解釋為什麼。
2. **排程與落地分開。** 排程器只吐順序(可以用便宜的模型);落地是純機械流程(`scripts/land.sh`),沒有判斷。
3. **agent 的每個動作都是控制台上的一筆事件。** 派工、開始、patch 就緒、閘門結果、落地、關票,一律 `scripts/event.py emit …`;控制台只讀事件檔,不猜。

## 命名守衛
`tests/test_no_project_names.py`:程式與設定裡不准出現來源專案的名字與這台機器的絕對路徑;`.md` 可以指名來源專案(遷移計畫與裁示的來源需要),但一樣禁絕對路徑。守衛自己不寫死那些字。

## 狀態

v0.1(2026-09-12)。從 tabby_pool 抽出來、去掉專案名。**還沒有第二個專案用過**——`docs/TODO.md` 第一節就是遷移計畫。
