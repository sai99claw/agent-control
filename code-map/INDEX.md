# code-map 索引

由 `python3 scripts/repo-map.py build` 依 `code-map/catalog.json` 產生,不要手改;改 catalog 再重生。
查一個主題:`python3 scripts/repo-map.py query <主題>`;新不新:`python3 scripts/repo-map.py check`。

## apply

收 patch:驗檔頭與寫入範圍、開分支、commit(帶票號與 sha256)、記 patch_ref

- 入口:[`scripts/apply.sh`](../scripts/apply.sh)
- 主守衛:[`tests/test_apply.py`](../tests/test_apply.py)

## auto-fix

回歸紅了派新 worker;第 1 輪也由它起;依票的 model/tool 起命令

- 入口:[`scripts/auto-fix.sh`](../scripts/auto-fix.sh)
- 主守衛:[`tests/test_auto_fix.py`](../tests/test_auto_fix.py)
- 來源:[`tests/test_auto_fix_codex.py`](../tests/test_auto_fix_codex.py)

## gate

閘門:對照表挑測試模組、preflight 機械格、狀態檔、索引新鮮度

- 入口:[`scripts/gate.sh`](../scripts/gate.sh)
- 主守衛:[`tests/test_gate.py`](../tests/test_gate.py)
- 來源:[`scripts/gate.example.sh`](../scripts/gate.example.sh)
- 文件:[`docs/WORKFLOW.md`](../docs/WORKFLOW.md)

## land

落地:只走這一支,拒 0 commit、過期基準、越界、過期覆核

- 入口:[`scripts/land.sh`](../scripts/land.sh)
- 主守衛:[`tests/test_land.py`](../tests/test_land.py)
- 人工卡:[`code-map/cards/land-sh.md`](../code-map/cards/land-sh.md)

## memory

記憶分層:note / harvest / check / consolidate / lint,上限守衛與整理票

- 入口:[`scripts/memory.py`](../scripts/memory.py)
- 主守衛:[`tests/test_memory.py`](../tests/test_memory.py)
- 來源:[`scripts/consolidate-memory.sh`](../scripts/consolidate-memory.sh)、[`tests/test_consolidate_memory.py`](../tests/test_consolidate_memory.py)、[`tests/test_memory_layer_policy.py`](../tests/test_memory_layer_policy.py)、[`tests/test_memory_lifecycle.py`](../tests/test_memory_lifecycle.py)、[`tests/test_memory_note_names.py`](../tests/test_memory_note_names.py)
- 文件:[`docs/MEMORY.md`](../docs/MEMORY.md)

## repo-map

這份地圖本身:catalog → index.json / INDEX.md,check 量新鮮度

- 入口:[`scripts/repo-map.py`](../scripts/repo-map.py)
- 主守衛:[`tests/test_repo_map_contract.py`](../tests/test_repo_map_contract.py)
- 文件:[`docs/CODE-MAP.md`](../docs/CODE-MAP.md)

## review

閘門綠之後派覆核者,收 REVIEW 的 result 區塊

- 入口:[`scripts/review.sh`](../scripts/review.sh)
- 主守衛:[`tests/test_review.py`](../tests/test_review.py)

## rules

按角色裁切的規則包(派工文前言)與 routing 下限

- 入口:[`scripts/rules.py`](../scripts/rules.py)
- 主守衛:[`tests/test_rules_delivery.py`](../tests/test_rules_delivery.py)
- 來源:[`tests/test_routing_cards.py`](../tests/test_routing_cards.py)、[`tests/test_rules.py`](../tests/test_rules.py)
- 文件:[`docs/DISPATCH-TEMPLATE.md`](../docs/DISPATCH-TEMPLATE.md)

## ticket

票:唯一的工作單位(create / set / cost / agent-command …)

- 入口:[`scripts/ticket.py`](../scripts/ticket.py)
- 主守衛:[`tests/test_ticket.py`](../tests/test_ticket.py)
- 來源:[`tests/test_ticket_65.py`](../tests/test_ticket_65.py)
- 文件:[`tickets/SCHEMA.md`](../tickets/SCHEMA.md)

## verify

回歸層執行器與驗證者案例的 red / check / lint

- 入口:[`scripts/verify-case.py`](../scripts/verify-case.py)
- 主守衛:[`tests/test_verify_case.py`](../tests/test_verify_case.py)
- 來源:[`scripts/verify.py`](../scripts/verify.py)、[`tests/test_verify_runner.py`](../tests/test_verify_runner.py)
