#!/usr/bin/env python3
"""環境嫌疑的列、宣告行的剖析、失敗訊息的形狀與 reports_dir —— `scripts/testiface.py`
與 `scripts/adapter-unittest.py` 共用(D-019)。

留下的是 `environment_suspect` 那一格每一筆的形狀(`suspect_row`、`SUSPECT_KEYS`)、
案例自己印的 `ENVIRONMENT-SUSPECT:` 宣告行的剖析器(`parse_environment_suspects`)、
紅例訊息的形狀(`normalized_failure_message`)與報告目錄(`reports_dir`)。沒有指令列。

## 環境壞了要當場停(rc=86)
(D-071 C6 起跑測試的是 `scripts/testiface.py` 的 `run` / `full`,內建版是
`scripts/adapter-unittest.py`;下面講的 `EnvironmentResult` / `LogStream` 住在那一支。舊的
`run-tests` 薄殼已刪,#162。)
2026-09-22 #620:safaridriver 起的 Safari 行程掛了幾小時後 storage 壞掉,26 條 safari
案例全紅、訊息同一形狀。閘門把整段跑完才說話 —— 兩輪落地白跑。所以測試不再由 shell
直接叫,改在 adapter 的同一個程序裡跑:每條紅例取「引擎 + 去掉數字與 id 的訊息」
當形狀,同一形狀連紅達 `board/config.json` 的 `environment_fail_fast_threshold`

## 環境可疑的兩條來源收成同一格(`environment_suspect`,D-019)
**形狀的唯一真實來源是 `docs/DESIGN-ENV-SUSPECT.md`**,這一份只實作它:那一格永遠是
一個 list、空值只有 `[]` 一種寫法,每一筆七個鍵(`source` / `engine` / `why` /
`count` / `threshold` / `log` / `line`)一個都不缺、缺料填 `None`。兩條來源(adapter-unittest 另有第三條 `machine`:fail/error 列的訊息或 traceback 含
「too many open files」等機器壞掉的字樣)各標
`source`:`statistical` 是上面那條連紅統計,`declared` 是跑在裡面的案例自己印的
`ENVIRONMENT-SUSPECT: <引擎> <為什麼>` 那一行。**`state` 與 `rc` 不因這一格而變。**
`ENVIRONMENT-SUSPECT: <引擎> <為什麼>` 那一行。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根與 board/config.json 的判讀

DEFAULT_REPORTS = "reports"
DEFAULT_ENV_FAIL_FAST_THRESHOLD = 8
# 86 不是隨便挑的:0 是綠、1 是 unittest 的紅、2/3 是 gate.sh 自己的用法(參數錯、
# 對不到模組)。要一個不會與那幾個撞的碼,呼叫者才分得出「紅了」與「環境壞了」。
ENV_SUSPECT_RC = 86

# 瀏覽器那一族會在案例名或輸出裡帶 `engine=chrome` / `engine='firefox'`;沒有就留空,不猜。
ENGINE = re.compile(r"engine\s*[=:]\s*['\"]?([A-Za-z0-9_.-]+)")
# 案例自己宣告環境紅的那一行:`ENVIRONMENT-SUSPECT: safari 螢幕鎖著(#474)`。
# 引擎那一格是**選填**的第一個字 —— 認得出引擎名就拆出來,認不出就整段都是「為什麼」
# (有些環境紅不屬於任何一個引擎,硬拆會把半句話當成引擎名)。前綴**只認行首**
# (去掉前導空白之後),理由寫在 `parse_environment_suspects` 的 docstring。
SUSPECT_PREFIX = "ENVIRONMENT-SUSPECT:"
SUSPECT_ENGINE = re.compile(r"^([a-z][a-z0-9_.-]*)(\s+|$)")
# `environment_suspect` 每一筆的七個鍵,順序照 `docs/DESIGN-ENV-SUSPECT.md`(D-019)。
# **那一份文件是形狀的唯一真實來源**,這裡只是它的可執行副本。
SUSPECT_KEYS = ("source", "engine", "why", "count", "threshold", "log", "line")


def normalized_failure_message(error):
    """訊息的**形狀**:去掉數字與 id。

    同一個環境故障每一條的訊息只差流水號與 case id(`localStorage id=ab-1 empty
    after 101 seconds`)。逐字比會判成 26 種不同的紅,於是「環境壞了」與「26 個真
    bug」長得一樣 —— 那正是要分開的兩件事。
    """
    message = str(error)
    message = re.sub(r"\b(?:id|case_id|request_id)\s*[=:]\s*[^\s,;)}\]]+",
                     "id=<id>", message, flags=re.IGNORECASE)
    message = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<id>", message,
                     flags=re.IGNORECASE)
    return re.sub(r"\d+", "<n>", message).strip()


def suspect_row(source, engine="", why="", count=None, threshold=None,
                log="", line=""):
    """一筆環境嫌疑。**七個鍵一個都不缺,缺料填 `None`**(`docs/DESIGN-ENV-SUSPECT.md`)。

    「有沒有這個鍵」不准當語意:讀的人只判 `source`,不必對每一筆做 `.get` 分支 ——
    一份鍵時有時無的紀錄,在 `.get(key, "")` 底下與一份根本沒記的紀錄長得一樣。
    """
    values = {"source": source, "engine": engine, "why": why, "count": count,
              "threshold": threshold, "log": log, "line": line}
    return {key: values[key] for key in SUSPECT_KEYS}


def reports_dir(root):
    # devloop runs tests on throwaway trees and points this outside the project (AC_REPORTS_DIR)
    override = os.environ.get("AC_REPORTS_DIR")
    if override:
        return override
    return os.path.join(root, event.config(root).get("reports_dir") or DEFAULT_REPORTS)


def parse_environment_suspects(log_path):
    """從一份 log 裡挑出 `ENVIRONMENT-SUSPECT:` 那幾行(`source == "declared"`)。

    這一條來源與上面那條統計是**兩件不同的事**:統計是外面這支程式從紅例推出來的,
    宣告是跑在裡面的案例自己說的 —— 螢幕中途上鎖那一種紅被案例改記成 skip,
    `addFailure` 根本不會被叫,統計看不到它,而兩端之間唯一的線就是 log 上那一行。

    切法照 `docs/DESIGN-ENV-SUSPECT.md`:宣告行**只認行首**(去掉前導空白之後以前綴
    開頭);引擎名後面要**真的還有話**才把第一個字當引擎,只有一個字的那一行整句就是
    理由。**一行一筆,不去重** —— 同一趟兩份 log 各記一次是明著要的行為。

    **為什麼只認行首而不是 `find`**:#23 第 1 輪實測,`unittest -v` 把案例 docstring
    的第一行也印進 log,而那一行裡逐字寫著前綴,於是被讀成一筆
    `engine="firefox"` / `why="假的 ... ok"` 的宣告 —— 「這一格非空就不自動派」那條
    守衛照著把那一輪的 auto-fix 擋掉了。中段出現的前綴是在**講**那一行,不是**印出**
    那一行,而位置是唯一分得開兩者的東西。案例真的印出來的那一行一定在行首:`print`
    自己起一行,而 runner 停在半行時由 `LogStream` 補一個換行。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line.startswith(SUSPECT_PREFIX):
            continue
        rest = line[len(SUSPECT_PREFIX):].strip()
        if not rest:
            continue
        engine = ""
        found = SUSPECT_ENGINE.match(rest)
        if found and rest[found.end():].strip():
            engine = found.group(1)
            rest = rest[found.end():].strip()
        # `count` 是 1(這一行就是一次觀測),`threshold` 沒有門檻可言 —— 填 `None`,
        # 不填 0:0 是一個門檻,而「不適用」不是一個數字。
        out.append(suspect_row("declared", engine=engine, why=rest, count=1,
                               threshold=None, log=log_path, line=line))
    return out
