# Agent Control

**一個人指揮一群 AI agent 寫軟體：測試全部通過才合併，才發版。**

你只說要什麼。agent 拆工作、寫程式、寫測試、互相驗收、修失敗的測試、合進 main、發版。
只有真的需要你決定的事，才會變成一則通知等你。

一組 Python 腳本加一個指令 `actl`,在背景叫 Claude Code(或 codex)做事。
需要 Python 3.8+、git、Claude Code CLI,在 Linux 或 macOS 上跑。你的專案要有測試指令和發版指令。

## 要解決的問題

- **agent 說「做完了」,其實沒做完。** 自己寫的測試、自己跑的綠燈不算數。
  → 驗收測試由另一個 agent 寫，而且要先證明它在舊程式上會失敗。
- **規則記不住。** 寫在對話裡的規矩，換個 session、換個模型就忘了。
  → 規則寫在腳本裡，由腳本執行，不靠 agent 記得。
- **你變成瓶頸。** 每個 agent 都停下來問你。
  → 只有要你決定的事才發通知，通常附上選項和推薦。
- **平行開發互相打架。** 幾個 agent 同時改 code,main 一直壞。
  → 先把幾張票合在一起跑全套測試，通過了才進 main。
- **不知道錢花在哪。** → 每次執行都記帳，按角色、按模型拆開。

## 怎麼運作

```
你:「我要 X」
  │
  ▼
開題者 ── 讀 code,拆成票(每件工作的目標與驗收條件)和相依圖(哪張要等哪張)
  │
  ▼
controller ── 把能一起測的票放進同一條整合分支(預設一組最多 10 張)
  │
  ▼ 每張票：驗證者與 worker 同時開工
  │   驗證者 只看票寫驗收測試，並證明它在舊程式上會失敗
  │   worker  寫程式讓它通過
  │   覆核    (複雜的票)讀 code 對票
  │
  ▼
整組跑一次全套測試 ── 失敗 → fixer 修(預設最多 3 輪)
  │ 通過
  ▼
重產程式碼索引 → 合進 main → 發版
```

## 角色

| 角色 | 是誰 | 做什麼 |
|---|---|---|
| 主線 | 你對話的那個 Claude Code session | 聽你說話、替你下指令、記下你的決定 |
| 開題者 | agent | 把需求拆成票和相依圖，寫清楚驗收條件 |
| controller | 腳本，不用 LLM | 排程、分組、合進 main、發版 |
| worker | agent | 照票寫程式；簡單的票交給便宜的模型 |
| 驗證者 | agent | 寫驗收測試(很小的改動可以不派) |
| 覆核 | agent,只讀 | 複雜的票寫完後，讀 code 對票 |
| fixer | agent | 整組測試失敗時，照票修 |
| 整理者 | agent,只讀 | 定期把 agent 交回的經驗整理成記憶(角色、模型、專案三層) |

哪個角色用哪個模型，寫在設定裡，可以換。

## 三條硬規則

- **票是唯一的工作單位。** 沒有票就沒有程式改動，票的內容只有開題者能寫。
- **程式改動只有全套測試通過才進 main**,而且只由 controller 推進。
- **禁區由腳本擋。** 改到你列的檔案、資料夾，或新增內容符合你列的樣式的票，停在合併前等你。

## 看板

`actl board serve --key-file F` 開一個網頁：要你決定的事、現在誰在跑、每張票的狀態與相依、花費。
帶 key 才能在網頁上回答，不帶只能看；畫面本身不花 LLM。

## 開始用

```sh
git clone https://github.com/sai99claw/agent-control.git ../agent-control   # 放在你的專案旁邊
export PATH="$PWD/../agent-control/bin:$PATH"
actl init            # 在你的專案寫 board/config.json:測試指令、發版指令、禁區
actl doctor          # 檢查設定
actl tickets init    # 建票庫(一個本機 git repo)

actl new --request "我要 X"     # 開題
actl run --forever              # 一路做到合進 main
actl tickets inbox              # 看等你的通知
```

平常不用自己打這些：用 Claude Code 打開你的專案，跟主線說話就好。

更多：流程與每支腳本的契約在 [`devloop/README.md`](devloop/README.md),
設計在 [`docs/design/dev-loop-v2.md`](docs/design/dev-loop-v2.md),
做過的決定在 [`docs/DECISIONS.md`](docs/DECISIONS.md)。
