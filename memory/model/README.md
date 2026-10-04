每個模型一份:`opus.md`、`fable.md`、`gpt-5.6-sol.md`、`gpt-6-astra.md`、`gemini-3.8-flash-high.md`。格式見 `docs/MEMORY.md`;每一層放什麼見 `memory/role/README.md`「分層契約」。
卡名規則:`board/config.json` 的 routing 值去掉冒號前的工具前綴就是檔名(`agy:gemini-3.8-flash-high` → `gemini-3.8-flash-high.md`;沒有前綴的 `opus` 就是 `opus.md`),規則包找模型卡用的是同一個取法(`scripts/rules.py` 的 `model_card`)。
新筆記先寫 `<model>.inbox.md`,整理時併入。
