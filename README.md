# Agent Control

一個人主導、多模型協作的研發工具。你說要什麼,開題者寫票,devloop 把票做到落地並發版;要你決定的事變成一頁等你。
中心論點只有一句:**不能只靠 agent 在對話中記得規則**——規則住在腳本裡。

## 接上一個專案

```sh
git clone https://github.com/sai99claw/agent-control.git ../agent-control   # 放在專案旁邊
export PATH="$PWD/../agent-control/bin:$PATH"
actl init                     # 新專案:寫 board/config.json(既有的用 actl doctor / actl migrate)
# 編 board/config.json:tests.*(專案的測試指令)、devloop.release(發版指令)、hard_block
actl doctor                   # 設定對不對得上這一版 agent-control
actl tickets init             # 建票庫(預設 ../agent-control/pools/<專案>,一個 git repo)
```

票庫要跨機器共用時,給它一個 remote(`git -C <票庫> remote add origin <url>`),寫入就是 push。

## 每天用

```sh
actl new --request "我要 X"    # 開題者讀 code、寫票與 DAG
actl run                      # 做到沒事為止(--forever 一直看著)
actl tickets inbox            # 等你的頁:created / landed / decision / env
actl tickets list             # 票與整合分支
actl board serve --key-file F # 看板:VISION 四個指標、票、整合分支;有 key 才能從網頁送需求與答覆
```

一般是用 Claude Code 打開專案,跟主線說話;主線替你下這些指令(`CLAUDE.md`)。

## 一張票怎麼走

開題者寫票(一句話講得完的目的、驗收、token 估計、`simple`/`complex`)→ controller 把可以一起驗的票放進同一條
整合分支 → 每張票一個 issuer:worker 和驗證者同時開工,驗證者的案例先在舊 code 上確認會紅 → worker 的分支合進整合
分支(衝突由 worker 解)→ 整組做完跑一次 regression → 紅了 auto_fixer 在整合分支上修 → 綠了 main fast-forward、
重產 `CODE-MAP.md`、跑專案的發版指令。細節與每個腳本的契約在 `devloop/README.md`。

## 這個 repo 裡有什麼

| 路徑 | 內容 |
|---|---|
| `CLAUDE.md` / `AGENTS.md` | 主線的契約 |
| `bin/actl` | 唯一入口 |
| `devloop/` | 五支腳本、票庫、看板、agent 的 prompt |
| `scripts/testiface.py`、`scripts/adapter-unittest.py` | 專案的測試介面(五個指令、一份結果檔) |
| `scripts/project_config.py` | `board/config.json` 的版本、檢查、遷移 |
| `scripts/release.sh` | agent-control 自己的發版(打 tag) |
| `memory/model/` | 各模型的舊記憶卡;新票庫建立時當成待整理的教訓匯入 |
| `docs/VISION.md` | 最上層的判準(只有使用者能改) |
| `docs/DECISIONS.md` | 現行的裁示 |
| `docs/design/` | 設計文件 |
| `pools/` | 各專案的票庫(各自是 git repo,不進 agent-control 的版控) |

## 測試

```sh
python3 -m unittest tests_devloop.test_devloop        # devloop:假 agent、真的 git 與測試介面
python3 -m unittest discover -s tests -t .           # 測試介面、設定、入口
```

## 命名守衛
`tests/test_no_project_names.py`:程式與設定裡不准出現來源專案的名字與這台機器的絕對路徑。
