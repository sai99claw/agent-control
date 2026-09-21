# Agent Control

一個人主導、多模型協作的軟體研發控制系統。**clone 下來、用 Claude Code 打開、說一句話,系統就開始運作。**

它把三天內從真實專案(tabby_pool,2026-09-08 → 09-12)踩出來的規矩,寫成程式與契約,而不是留在提示裡。中心論點只有一句:**不能只靠 agent 在對話中記得規則。**

## 30 秒開始

```sh
git clone https://github.com/sai99claw/agent-control.git && cd agent-control
sh scripts/new-session.sh main fable        # 站在哪個版本、最近的事、開著的票、記憶有沒有超標;發 session.start
python3 board/board.py &                    # 控制台 http://127.0.0.1:18905(只綁本機)
claude                                      # 開 Claude Code;CLAUDE.md 會告訴它自己是誰、先讀什麼
```

然後對它說:「我要做 X」。它會派開題者寫票、決定順序、派工、落地,並在需要你裁決時把問題放進控制台的收件匣。

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

票開好、worker 把 `patch.diff` 交回來之後,**一路到落地都有入口,不必手動套 patch**:
```sh
sh scripts/apply.sh 1 /path/to/patch.diff          # 套進 t1 分支 + commit(檔頭與寫入範圍先驗過)
(cd ../agent-control-wt/t1 && sh scripts/gate.sh --branch --ticket 1 --auto-fix)
                                                    # 閘門;紅了自動派下一輪 worker(三輪上限)
python3 scripts/inbox.py list                       # 跑完的事在這裡等你(主線不輪詢)
python3 scripts/ticket.py set 1 review '{"verdict":"pass","by":"main","sha":"<分支頭>"}'
sh scripts/land.sh t1                               # 全套綠才進主線;land 不關票
python3 scripts/ticket.py close 1
```
主線走遠了先 `sh scripts/apply.sh rebase 1 <patch>` 重生一份乾淨 diff 再套。

上面這兩段是在乾淨 clone 裡真的跑過一遍才寫的(2026-09-12 第一段;2026-09-21 連新入口
再走一次:new-session → 開票 → apply → gate → inbox → review → land → close,全部走得通)。

## 這個 repo 裡有什麼

| 目錄 | 內容 | 給誰讀 |
|---|---|---|
| `CLAUDE.md` / `AGENTS.md` | session 的自我介紹:你是主線、先讀什麼、不准做什麼 | 每個 session 第一件事 |
| `docs/DESIGN.md` | 系統設計(產品負責人的提案 v1.1 + 主線審稿) | 想改系統的人 |
| `docs/ROLES.md` | 七種角色的責任、交付物、權限牆、模型路由 | 每個 agent |
| `docs/WORKFLOW.md` | 票的狀態機、閘門、落地、發版、回歸紅了之後 | 主線與落地 |
| `docs/SESSION-START.md` | 開新 session 的固定動作(依角色) | 每個 session |
| `docs/DISPATCH-TEMPLATE.md` | 派工範本:副本+patch、禁區、**假綠家族**、查證規矩 | 派工的人與被派的人 |
| `docs/MEMORY.md` | 專案共用記憶 vs 模型私有記憶,整理政策 | 所有人 |
| `docs/CODE-MAP.md` | 讓 AI 追 code 的方案:模組卡片、驗證於哪個版本、過期偵測 | 讀 code 的人 |
| `docs/TODO.md` | 還沒做的事與遷移計畫 | 主線 |
| `tickets/` | 票的契約(schema)與票本身 | 控制台、排程、落地 |
| `board/` | 控制台網頁與事件 API | 你 |
| `scripts/` | apply / gate / auto-fix / land / ticket / event / status / inbox / rules 的可執行部分 | 落地與派工 |
| `templates/` | 派工文與討論的範本(`dispatch-verifier.md`、`project-CLAUDE.md`、`discussion.md`) | 派工的人 |
| `memory/` | `project/` 共用、`model/<model>.md` 私有、`role/<role>.md` 角色卡 | 所有人 |
| `code-map/` | 模組卡片與過期檢查 | 讀 code 的人 |

## 一張票從頭到尾會碰到哪幾支

| 這一步 | 入口 | 它擋住什麼 |
|---|---|---|
| 開票 | `scripts/ticket.py create` | 沒有驗收、沒有寫入範圍的票開不出來 |
| 派工文前言 | `scripts/rules.py pack <角色>` | 整份共用規矩重複載入(≤ 4 KB,砍掉的會指名) |
| 套 patch、建分支、commit | `scripts/apply.sh` | 絕對路徑檔頭、只清空不刪檔、越界、套一半 |
| 局部閘門 | `scripts/gate.sh --branch --ticket <n>` | 對不到模組不准印綠;票的 `verify.tags` 真的被呼叫 |
| 紅了修 | `scripts/auto-fix.sh <n>`(或閘門 `--auto-fix`) | 三輪上限;反駁 / 沒有歸因會停下來報主線 |
| 主線被叫醒 | `scripts/inbox.py list` | 輪詢(每看一次背景工作 = 整份上下文重送一輪) |
| 覆核 | `scripts/ticket.py set <n> review …` | 沒有人讀過 patch 的票進主線 |
| 落地 | `scripts/land.sh` | 0 commit、基準過期、越界、覆核過期、`verify.files` 沒帶進來、閘門紅 |
| 關票 | `scripts/ticket.py close <n>` | 改動其實不在主線上的票被關成完成 |

## 設計上的三個決定

1. **規則住在程式裡,不住在提示裡。** 票的欄位、落地的拒絕條件、寫入範圍、租約,都由 `scripts/` 與 `board/` 執行;提示只負責解釋為什麼。
2. **判斷與落地分開。** 順序由主線決定(或由腳本依 `allowed_write_paths` 算衝突圖提案);落地是純機械流程(`scripts/land.sh`),沒有判斷。**沒有調度員這個角色**(D-010)。
3. **agent 的每個動作都是控制台上的一筆事件。** 派工、開始、patch 就緒、閘門結果、落地、關票,一律 `scripts/event.py emit …`;控制台只讀事件檔,不猜。

## 命名守衛
`tests/test_no_project_names.py`:程式與設定裡不准出現來源專案的名字與這台機器的絕對路徑;`.md` 可以指名來源專案(遷移計畫與裁示的來源需要),但一樣禁絕對路徑。守衛自己不寫死那些字。

## 狀態

v0.2(2026-09-21)。從 tabby_pool 抽出來、去掉專案名。2026-09-21 補上七個原本「規格已定、未實作」的入口
(套 patch、自動派 worker、終態收件匣、flake 自動開票、`verify.files` 檢查、角色規則包、同輪回歸去重;D-015)。
**還沒有第二個專案真的接上去**——`docs/TODO.md` §0 是可執行的遷移步驟。

## 把規範與腳本同步進專案(agent-control 是主,專案是從)
```sh
sh scripts/sync-to-project.sh /path/to/project   # 角色卡 → docs/roles/,模型記憶 → docs/roles/model/
                                                 # 控制腳本 → scripts/control/,並印出專案端要改的接點
```
專案裡那份檔頭標「請到 agent-control 改」;改規範永遠在這個 repo 改,再同步。
退場的角色卡與腳本會**先唸出來再刪**(靜悄悄的刪除與沒發生的刪除長得一樣)。
一個既有專案怎麼接上來,逐步在 `docs/TODO.md` §0。
