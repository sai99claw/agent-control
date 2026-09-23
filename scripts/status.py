#!/usr/bin/env python3
"""閘門與落地的狀態檔 — `docs/WORKFLOW.md` §狀態檔(D-010、D-014)。

    scripts/status.py start  --ticket 7 --kind gate --run-id 20260921-101500-42 \
                             --base-sha abc1234 --worktree /path/wt --round 2
    scripts/status.py phase  --ticket 7 --run-id … --phase merge --rc 0
    scripts/status.py failures --log gate.log        # 印可以單獨重跑的案例 id,一行一個
    scripts/status.py run-tests --root . --log gate.log --suspect-file gate.log.env-suspect.json \
                             --mode discover              # 跑測試,環境壞了當場中止(rc=86)
    scripts/status.py done   --ticket 7 --run-id … --rc 1 --log gate.log \
                             [--suspected-flaky test_x.Case.test_y …]
    scripts/status.py show   --ticket 7 [--run-id …] [--runs]
    scripts/status.py suspects --ticket 7 [--run-id …] [--count]   # 環境可疑幾筆
    scripts/status.py rundir --ticket 7 --run-id …   # 這一輪的目錄(回歸快取住那裡)

寫的是 `<reports_dir>/t<票號>/<run_id>/status.json`(`board/config.json` 的
`reports_dir`,預設 `reports/`),**一輪一個目錄、不覆寫**,log 另外複製一份進
`logs/` 留著 —— land 成功後會把 worktree 收掉,而 log 就住在那裡面(D-014 §2)。

## 這一份要回答的三個問題
**「跑完了沒、錯了什麼、去哪看」** —— 這三句是使用者 2026-09-20 的原話。在它之前,
一個 agent 要知道閘門怎麼了,只能把整份 log 讀進上下文(一次幾十萬 token),或者
用 `Monitor` / `sleep` 迴圈輪詢背景工作(每看一次 = 整份上下文重送一輪)。
**一份 20 行的 JSON 取代那兩種做法。**

## 為什麼 `state` 與 `rc` 是兩格
`{"state": "running"}` 與「跑完了但 rc 還沒寫」長得一樣,而那正是要分開的兩件事:
**還在跑**要等,**跑完了而且紅了**要修。所以 `start` 先寫 `running`(那一刻還沒有
rc),`done` 才覆寫成 `done` 並帶 `rc`。一份沒有 `finished` 的 `done` 是壞掉的檔,
不是「剛好跑很快」。

## 為什麼 failures 要帶 excerpt 而不是只給 log 路徑
只給路徑的話,下一個人還是得把整份 log 讀進來 —— 那就回到原本的成本。excerpt 上限
20 行:夠認出是哪一條斷言倒了,不夠讓人偷懶把整份貼進去。

## 為什麼多了 `repair_context`(2026-09-21 外部審查)
> 「目前 status 是結果摘要,不是新 worker 可直接開工的交接包。」

下一輪換一個**新的** worker,它手上只有這一份檔。少了 base_sha、票面快照、副本位置、
patch 路徑與雜湊、第幾輪、上一輪排除過什麼、怎麼重現,它就得回頭翻對話或猜檔案位置
—— 那一趟比整份 log 還貴。

## 環境壞了要當場停(`run-tests`,rc=86)
2026-09-22 #620:safaridriver 起的 Safari 行程掛了幾小時後 storage 壞掉,26 條 safari
案例全紅、訊息同一形狀。閘門把整段跑完才說話 —— 兩輪落地白跑。所以測試不再由 shell
直接叫,改由 `run-tests` 在同一個程序裡跑:每條紅例取「引擎 + 去掉數字與 id 的訊息」
當形狀,同一形狀連紅達 `board/config.json` 的 `environment_fail_fast_threshold`
(預設 8)就**中止那一段**並回 rc=86。一次環境故障最多浪費 N 條案例,不是整段。

**這一關在 flake 判定之前。** 環境壞掉時那一段是被中止的,紅榜本來就不完整;而 flake
判定要做的事(每條紅例單跑 5 次 + 原順序整組再跑)正是在壞掉的環境裡最貴、最沒有意義
的那件事 —— 它會把「環境壞了」重新量成「這些案例都是真紅」。所以 rc=86 的時候
`flake_rerun` 與回歸層都不跑,狀態檔直接寫 `env_suspect`。

## 環境可疑的兩條來源收成同一格(`environment_suspect`,D-019)
**形狀的唯一真實來源是 `docs/DESIGN-ENV-SUSPECT.md`**,這一份只實作它:那一格永遠是
一個 list、空值只有 `[]` 一種寫法,每一筆七個鍵(`source` / `engine` / `why` /
`count` / `threshold` / `log` / `line`)一個都不缺、缺料填 `None`。兩條來源各標
`source`:`statistical` 是上面那條連紅統計,`declared` 是跑在裡面的案例自己印的
`ENVIRONMENT-SUSPECT: <引擎> <為什麼>` 那一行。**`state` 與 `rc` 不因這一格而變。**

## 自動 flake 與順序污染
單跑綠**不等於**那條紅是假的:第一條測試污染共用狀態、第二條檢查乾淨狀態時,整組
必紅而單跑必綠。只有每條紅例連續單跑達設定門檻、原順序整組也達門檻全綠,才標
`flaky: auto`;單跑全綠但整組紅則標 `order_dependent`,原始失敗留在紅榜、rc 不動。
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import traceback
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根與 board/config.json 的判讀

EXCERPT_LINES = 20
DEFAULT_REPORTS = "reports"
FLAKY_LEDGER = "flaky.jsonl"
DEFAULT_FLAKY_THRESHOLD = 3
DEFAULT_ENV_FAIL_FAST_THRESHOLD = 8
# 86 不是隨便挑的:0 是綠、1 是 unittest 的紅、2/3 是 gate.sh 自己的用法(參數錯、
# 對不到模組)。要一個不會與那幾個撞的碼,呼叫者才分得出「紅了」與「環境壞了」。
ENV_SUSPECT_RC = 86
PHASES = ("gate", "merge", "push", "verify", "docs", "apply")

# `FAIL: test_x (test_mod.Case.test_x)` / `ERROR: test_x (test_mod.Case)` /
# subTest 的兩種形狀 —— 方括號的 `[engine=firefox]`(自己組的標籤)與**原生的圓括號
# 參數** `(engine='firefox')`。第二種一定要認:2026-09-21 的外部審查實測,舊版把整
# 行尾巴吃進 qualifier,解析出來的 case 是
# `__main__.NativeSubtest.test_engine) (engine='firefox'.test_engine` —— 一個餵不回
# `python3 -m unittest` 的 id,而**餵不回去的紅榜與沒有紅榜一樣**。
HEAD = re.compile(r"^(FAIL|ERROR):\s+(\S+)\s*(.*?)\s*$")
DOTTED = re.compile(r"^[A-Za-z_][\w.]*$")
DIVIDER = re.compile(r"^(=|-){20,}\s*$")
FILE_LINE = re.compile(r'^\s*File "([^"]+)", line (\d+)')
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
CLOSERS = {"(": ")", "[": "]"}


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


class EnvironmentResult(unittest.TextTestResult):
    """同一引擎、同形訊息連紅達門檻就中止那一段。

    只算**連續**的:中間夾一條綠或一條別的形狀就歸零。「一路都在倒同一句」是環境的
    徵狀,「散落幾條同句」是程式的 bug,這兩件事不該走同一條路。
    """

    def __init__(self, *args, threshold=DEFAULT_ENV_FAIL_FAST_THRESHOLD, **kwargs):
        super().__init__(*args, **kwargs)
        self.threshold = threshold
        self.last_shape = None
        self.streak = 0
        # **空值只有 `[]` 一種寫法**(D-019)。`None` 與 `[]` 揉在一起的那一刻,
        # 「還沒有量到」與「量了而沒有」在真值上長得一樣。
        self.environment_suspect = []

    def _observe(self, test, err):
        if err is None:
            self.last_shape = None
            self.streak = 0
            return
        detail = "%s\n%s" % (test, err[1])
        found = ENGINE.search(detail)
        if not found:
            # 認不出引擎就不猜:這一條可能只是一個普通的紅。
            self.last_shape = None
            self.streak = 0
            return
        engine = found.group(1)
        message = normalized_failure_message(err[1])
        shape = (engine.lower(), message)
        self.streak = self.streak + 1 if shape == self.last_shape else 1
        self.last_shape = shape
        if self.streak >= self.threshold:
            # 形狀見 `docs/DESIGN-ENV-SUSPECT.md`:一個 list,每一筆標來源。
            # `why` 是**正規化過的**訊息形狀(原 `message_shape`,改名併進來),
            # `line` 是觸發那一條紅的**原始**訊息第一行 —— 形狀認得出「同一句話」,
            # 原文才答得出「那台機器當時到底說了什麼」,兩格都要留。
            # `log` 那一格由 `cmd_run_tests` 補:這裡看不到 `--log` 是哪一份檔。
            # `line` 用 `format_exception_only` 的第一行:那正是 log 上的
            # `AssertionError: <原話>`,而形狀(`why`)已經把數字換成 `<n>` 了。
            raw = "".join(traceback.format_exception_only(err[0], err[1])).splitlines()
            self.environment_suspect = [suspect_row(
                "statistical", engine=engine, why=message, count=self.streak,
                threshold=self.threshold, log="",
                line=raw[0].strip() if raw else "")]
            # 兩個旗標都要設:`shouldStop` 停的是**下一條測試方法**,而 subTest 的
            # 迴圈跑在同一個方法裡面 —— 只設它,那 12 條 subTest 會整組跑完才停。
            # `failfast` 才是 subTest 自己看的那一格(實測見 EVIDENCE M1)。
            self.failfast = True
            self.shouldStop = True

    def addSuccess(self, test):
        self._observe(test, None)
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._observe(test, err)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._observe(test, err)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        self._observe(subtest, err)
        super().addSubTest(test, subtest, err)


class LogStream:
    """同一份 log 收兩種輸出:runner 的,與案例自己 `print` 的。

    `verbosity=2` 的 runner 把 `test_x (…) ... ` 寫完**不換行**就去跑案例,所以案例
    `print` 出來的字會黏在那一行尾巴。實測(#23 第 2 輪)那一行長這樣:

        test_it_declares_and_skips (…) ... ENVIRONMENT-SUSPECT: firefox 假的宣告

    於是兩件事同時壞:`line` 那一格前面多帶了一截案例名(文件要的是**整行原樣**),
    而宣告行不在行首 —— 而行首是唯一分得開「印出那一行」與「在講那一行」的東西
    (`parse_environment_suspects`)。所以這裡追蹤這份 log 現在停在第幾欄,**換寫入者
    而且還停在半行**的時候補一個換行。
    """

    def __init__(self, handle):
        self.handle = handle
        self.column = 0
        self.who = None

    def side(self, who):
        return LogSide(self, who)

    def write(self, who, text):
        if not text:
            return 0
        if self.column and who != self.who:
            self.handle.write("\n")
            self.column = 0
        self.who = who
        self.handle.write(text)
        if "\n" in text:
            self.column = len(text.rsplit("\n", 1)[1])
        else:
            self.column += len(text)
        return len(text)


class LogSide:
    """`LogStream` 的一個寫入者;runner 與 `redirect_stdout` 各拿一個。"""

    def __init__(self, shared, who):
        self.shared = shared
        self.who = who

    def write(self, text):
        return self.shared.write(self.who, text)

    def __getattr__(self, name):
        return getattr(self.shared.handle, name)


def cmd_run_tests(args):
    """跑測試並在環境可疑時中止。**shell 不再自己叫 unittest** —— 要看到一條一條的
    結果就得待在同一個程序裡;等 log 寫完再解析等於等整段跑完,那正是這張票要省下的。

    log 的寫法與舊版的 shell 一樣:先整份寫檔,再回退出碼。判綠只看 rc。
    """
    root = os.path.abspath(args.root)
    threshold = int(event.config(root).get("environment_fail_fast_threshold")
                    or DEFAULT_ENV_FAIL_FAST_THRESHOLD)
    with open(args.suspect_file, "w", encoding="utf-8") as handle:
        # **空值只有 `[]`**(`docs/DESIGN-ENV-SUSPECT.md`)。舊版寫空字串,於是
        # 「這一趟沒有嫌疑」與「這個檔壞了」在 `json.load` 底下都是同一個例外。
        handle.write("[]\n")
    # cwd 照舊版兩條路各自的樣子:discover 在 repo 根、指名模組在 `tests/`。
    where = root if args.mode == "discover" else os.path.join(root, "tests")
    os.chdir(where)
    sys.path.insert(0, os.path.join(root, "tests"))
    loader = unittest.defaultTestLoader
    if args.mode == "discover":
        suite = loader.discover("tests", pattern="test_*.py")
    else:
        suite = loader.loadTestsFromNames(args.names)
    with open(args.log, "w", encoding="utf-8") as handle:
        # **案例自己 `print` 的那一行也要進 log。** `TextTestRunner(stream=…)` 只收
        # runner 的輸出,而 `ENVIRONMENT-SUSPECT:` 那一行是案例印的 —— 不導進來,
        # 它會流到呼叫者的 stdout 而不在 log 裡,於是 `done --log` 永遠讀不到
        # declared 那一條來源(`docs/DESIGN-ENV-SUSPECT.md` §遷移)。兩邊共用一個
        # `LogStream`,它負責讓案例印的那一行落在**行首**(理由見那個 class)。
        shared = LogStream(handle)
        runner = unittest.TextTestRunner(
            stream=shared.side("runner"), verbosity=2,
            resultclass=lambda *items, **kw: EnvironmentResult(
                *items, threshold=threshold, **kw))
        with contextlib.redirect_stdout(shared.side("case")):
            result = runner.run(suite)
    if result.environment_suspect:
        for row in result.environment_suspect:
            # `log` 只有這裡知道:嫌疑是在跑的時候量到的,而檔名是呼叫者給的。
            row["log"] = args.log
        with open(args.suspect_file, "w", encoding="utf-8") as handle:
            json.dump(result.environment_suspect, handle, ensure_ascii=False)
            handle.write("\n")
        return ENV_SUSPECT_RC
    return 0 if result.wasSuccessful() else 1


def reports_dir(root):
    return os.path.join(root, event.config(root).get("reports_dir") or DEFAULT_REPORTS)


def ticket_dir(root, ticket):
    return os.path.join(reports_dir(root), "t%s" % ticket)


def run_dir(root, ticket, run_id):
    return os.path.join(ticket_dir(root, ticket), run_id)


def path_for(root, ticket, run_id):
    return os.path.join(run_dir(root, ticket, run_id), "status.json")


def runs_of(root, ticket):
    """這張票跑過哪幾輪,舊的在前。run_id 是時間戳開頭,所以字典序就是時間序。"""
    where = ticket_dir(root, ticket)
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return []
    return [name for name in names
            if os.path.exists(os.path.join(where, name, "status.json"))]


def latest_run(root, ticket):
    rows = runs_of(root, ticket)
    return rows[-1] if rows else ""


def new_run_id():
    return "%s-%d" % (datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp(text):
    """ISO 時間戳 → datetime,讀不懂就是讀不懂(回 None),不猜一個。"""
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def seconds_between(first, last):
    """兩個時間戳差幾秒。**任一格缺就回 None,不回 0**(D-017 ①)。

    0 秒是「跑得很快」,而「算不出來」不是一個秒數 —— 揉成同一個 0 的那一刻,
    一份缺了 `started` 的壞檔與一趟真的在同一秒內跑完的閘門長得一樣。
    """
    start, end = stamp(first), stamp(last)
    if start is None or end is None:
        return None
    try:
        return int((end - start).total_seconds())
    except (TypeError, ValueError, OverflowError):
        # 一邊帶時區一邊不帶,相減會拋 —— 那也是「算不出來」。
        return None


def sha256_of(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def file_ref(path):
    """patch 那一格要帶雜湊。**一個路徑答不出「是不是同一份 patch」** —— 下一輪的
    worker 拿到的可能是被重套過、被重生過清單的另一份。"""
    if not path:
        return None
    return {"path": path, "sha256": sha256_of(path),
            "exists": os.path.exists(path)}


def split_groups(rest):
    """把 `(a.b.c) (engine='firefox')` 拆成 `['a.b.c', "engine='firefox'"]`。

    巢狀要算深度:subTest 的參數裡可以有 `size=(1, 2)`,而用非貪婪的正規表示式去抓
    會在第一個 `)` 斷掉,把剩下的塞進下一格。
    """
    groups, buf, depth, opener = [], [], 0, ""
    for ch in rest:
        if depth == 0:
            if ch in CLOSERS:
                depth, opener, buf = 1, ch, []
            continue
        if ch == opener:
            depth += 1
        elif ch == CLOSERS[opener]:
            depth -= 1
            if depth == 0:
                groups.append("".join(buf))
                continue
        buf.append(ch)
    return groups


def dotted(case, qualifier):
    """單獨重跑要用的 id。unittest 的括號裡是完整路徑(3.11 起連方法名都在裡面),
    括號外只有方法名 —— **用括號裡那一份**,它才餵得回 `python3 -m unittest`。"""
    if not qualifier:
        return case
    qualifier = qualifier.strip()
    if qualifier.endswith("." + case) or qualifier == case:
        return qualifier
    return "%s.%s" % (qualifier, case)


def parse_head(line):
    """一行 `FAIL:` / `ERROR:` → (kind, case_id, subtest 標籤) 或 None。"""
    found = HEAD.match(line)
    if not found:
        return None
    kind, case, rest = found.group(1), found.group(2), found.group(3)
    labels = [g.strip() for g in split_groups(rest)]
    qualifier = ""
    if labels and DOTTED.match(labels[0]):
        qualifier = labels.pop(0)
    return kind, dotted(case, qualifier), ", ".join(x for x in labels if x)


def parse_failures(log_path):
    """從一份 unittest 輸出裡挑出紅的案例。

    只認 `^(FAIL|ERROR):` 開頭那幾行 —— 不去猜「看起來像錯誤」的行。猜出來的那幾筆
    會讓 `failures` 變成一份沒有人敢信的清單,而**一份不能信的紅榜與沒有紅榜一樣**。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    index = 0
    while index < len(lines):
        head = parse_head(lines[index])
        if not head:
            index += 1
            continue
        kind, case, label = head
        body = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if parse_head(line) or line.startswith("Ran ") or line.startswith("OK") \
                    or line.startswith("FAILED"):
                break
            if DIVIDER.match(line):
                # 第一條分隔線是標頭與 traceback 之間的那一條;第二條是這一筆的結尾。
                if body:
                    break
                index += 1
                continue
            body.append(line)
            index += 1
        while body and not body[-1].strip():
            body.pop()
        where, line_no = "", 0
        for line in body:
            spot = FILE_LINE.match(line)
            if spot:
                where, line_no = spot.group(1), int(spot.group(2))
        engine = ""
        for line in [label, case] + body:
            hit = ENGINE.search(line)
            if hit:
                engine = hit.group(1)
                break
        out.append({
            "case": case,
            "kind": kind,
            # subTest 的參數。單獨重跑餵回去的是 `case`(subTest 沒辦法單獨叫),
            # 所以標籤另外留一格,讓人看得出紅的是哪一個子情境。
            "subtest": label,
            "file": where,
            "line": line_no,
            "engine": engine,
            "log": log_path,
            "excerpt": "\n".join(body[:EXCERPT_LINES]),
            "suspected_flaky": False,
        })
    return out


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


def environment_suspects(data):
    """讀端正規化:一份 `status.json` → 這一格的 list(`docs/DESIGN-ENV-SUSPECT.md`)。

    舊檔有**四種「沒有」**(缺這一格 / `null` / `{}` / `start` 根本沒寫)與**一種舊的
    非空 dict**(#7 的單筆)。裁的是「不改寫舊檔」,所以相容性全靠這一支 —— `metrics`
    與看板一律經它讀,不直接下標。少了它,舊 dict 在真值上仍是真、卻在 `[0]` 下標
    炸掉,而那兩種壞法在畫面上都是一格空白。
    """
    raw = (data or {}).get("environment_suspect")
    if not raw:
        return []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        # 舊形狀:`message_shape` 就是今天的 `why`;那一版沒有留原始那一行與 log。
        # 沒有 `message_shape` 的舊檔,`why` 是 **`None` 而不是 `""`**:`""` 在說
        # 「理由是一句空話」,`None` 在說「那一版根本沒記理由」,而只有後者是真的
        # (`docs/DESIGN-ENV-SUSPECT.md` §結論「缺料就 null」、DISPATCH-TEMPLATE §5.5
        # 「拿不到就當空的」)。`line` / `log` 那兩格是文件與票面**逐字**釘成 `""` 的,
        # 照釘的寫。
        return [suspect_row("statistical", engine=raw.get("engine") or "",
                            why=raw.get("message_shape") or raw.get("why"),
                            count=raw.get("count"), threshold=raw.get("threshold"),
                            log="", line="")]
    return []


# ------------------------------------------------------------------ 讀寫


def write(root, ticket, run_id, data):
    path = path_for(root, ticket, run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def read(root, ticket, run_id):
    try:
        with open(path_for(root, ticket, run_id), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def keep_logs(root, ticket, run_id, logs):
    """把 log 複製進這一輪的目錄。**land 成功後 worktree 會被收掉**,而 log 就在
    那裡面 —— 只留路徑等於留了一個明天不存在的路徑(審查:status 的生命週期)。"""
    kept = []
    where = os.path.join(run_dir(root, ticket, run_id), "logs")
    for log in logs:
        if not os.path.exists(log):
            kept.append({"path": log, "kept": "", "note": "跑完時這個檔不在"})
            continue
        os.makedirs(where, exist_ok=True)
        target = os.path.join(where, os.path.basename(log))
        stem, ext = os.path.splitext(target)
        serial = 1
        while os.path.exists(target):
            target = "%s-%d%s" % (stem, serial, ext)
            serial += 1
        with open(log, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
        kept.append({"path": log, "kept": os.path.relpath(target, root), "note": ""})
    return kept


def ticket_snapshot(root, ticket):
    """票面快照:下一輪的 worker 不必再去讀票檔,也認得出票在它開工後被改過。"""
    path = os.path.join(event.tickets_dir(root), "%s.json" % ticket)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    keep = ("id", "subject", "objective", "state", "state_version", "attempt",
            "base_sha", "allowed_write_paths", "acceptance", "verify",
            "objections", "review", "retry_limit")
    return {key: data[key] for key in keep if key in data}


def repair_context(root, args):
    env = {}
    for item in args.env or []:
        key, _, value = item.partition("=")
        if key:
            env[key] = value
    return {
        "version": 1,
        "base_sha": args.base_sha or "",
        "ticket": ticket_snapshot(root, args.ticket),
        "worktree": args.worktree or "",
        "patch": file_ref(args.patch),
        "verify_patch": file_ref(args.verify_patch),
        "round": args.round,
        "prev_evidence": args.prev_evidence or "",
        "repro": {"cmd": args.repro or "", "cwd": args.cwd or ""},
        "env": env,
    }


# ------------------------------------------------------------------ 子指令


def cmd_start(args):
    root = event.repo_root()
    run_id = args.run_id or new_run_id()
    data = {"state": "running", "run_id": run_id, "kind": args.kind,
            "ticket": args.ticket, "sha": args.sha or "", "started": now(),
            "finished": None, "rc": None, "note": "",
            "report": args.report or "", "logs": [], "kept_logs": [],
            "phases": [], "failures": [], "suspected_flaky": [],
            # 這一格從 `start` 就在:**缺這一格與「這一趟沒有嫌疑」是兩件事**,
            # 而讀的人用 `data["environment_suspect"]` 問的時候前者是 KeyError。
            "environment_suspect": [],
            "repair_context": repair_context(root, args)}
    path = write(root, args.ticket, run_id, data)
    print("status: %s" % os.path.relpath(path, root))
    print("run_id: %s" % run_id)
    return 0


def cmd_phase(args):
    """gate / merge / push 各記一筆。**混成一格的 `done` 會說謊**:land 舊版在 merge
    與 push 之前就寫 `done, rc=0`,所以「合進去了」與「只是閘門綠」長得一樣。"""
    root = event.repo_root()
    run_id = os.environ.get("AC_RUN_ID") or args.run_id
    if not run_id:
        sys.stderr.write("status: 警告:沒有 --run-id 或 AC_RUN_ID,改用最新一輪\n")
        run_id = latest_run(root, args.ticket)
    if not run_id:
        sys.stderr.write("status: #%s 沒有任何一輪可以記 phase\n" % args.ticket)
        return 2
    data = read(root, args.ticket, run_id)
    if not data:
        sys.stderr.write("status: #%s 的 %s 讀不到\n" % (args.ticket, run_id))
        return 2
    phases = data.setdefault("phases", [])
    # 上一筆 phase 的 `at`,沒有上一筆就是這一輪的 `started` —— 一段的牆鐘秒數是
    # 「從上一個記號到這個記號」,而第一段的上一個記號是開跑那一刻。
    since = phases[-1].get("at") if phases else data.get("started")
    at = now()
    phases.append({
        "phase": args.phase, "rc": args.rc, "at": at,
        "seconds": seconds_between(since, at),
        "note": args.note or ""})
    write(root, args.ticket, run_id, data)
    print("status: #%s %s %s rc=%d" % (args.ticket, run_id, args.phase, args.rc))
    return 0


def flaky_ledger_path(root):
    return os.path.join(reports_dir(root), FLAKY_LEDGER)


def record_flakes(root, ticket, run_id, rows):
    """持久事件帳。**舊版把單跑的證據丟掉**,所以同一條案例每天疑似一次,累計次數
    永遠是 1,沒有人會去修它的不穩定(審查:flake 只被看見,沒有追到底)。"""
    if not rows:
        return []
    path = flaky_ledger_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    for row in rows:
        lines.append(json.dumps({
            "at": now(), "ticket": ticket, "run_id": run_id,
            "case": row["case"], "kind": row["kind"],
            "subtest": row.get("subtest", ""), "log": row.get("log", ""),
            "classification": row.get("flaky") or "suspected",
        }, ensure_ascii=False) + "\n")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, "".join(lines).encode("utf-8"))
    finally:
        os.close(handle)
    counts = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)["case"]
                except (ValueError, KeyError):
                    continue
                counts[case] = counts.get(case, 0) + 1
    except OSError:
        return []
    threshold = event.config(root).get("flaky_threshold") or DEFAULT_FLAKY_THRESHOLD
    hot = []
    for row in rows:
        seen = counts.get(row["case"], 0)
        if seen >= threshold:
            hot.append((row["case"], seen))
    for case, seen in hot:
        try:
            event.emit("decision.asked", ticket=ticket, case=case, seen=seen,
                       note="疑似 flaky 累計 %d 次(門檻 %d)—— 已開修復票"
                            % (seen, threshold))
        except Exception:                                  # noqa: BLE001
            pass
        print("status: %s 疑似 flaky 累計 %d 次(門檻 %d)—— 已發 decision.asked"
              % (case, seen, threshold))
        made = open_flaky_ticket(root, case, seen, threshold,
                                 row.get("log", ""), ticket)
        if made:
            print("status: 自動開了修不穩定的票 #%s(role=verifier)—— "
                  "`python3 scripts/ticket.py show %s`" % (made, made))
    return hot


def open_flaky_ticket(root, case, seen, threshold, log, from_ticket):
    """達門檻就**自動開一張修復票**(D-015)。

    以前只發 `decision.asked` 停在那裡,理由是「自動開票會在門檻誤判時生出一堆沒人
    認領的票」。實際跑起來相反:`decision.asked` 沒有 owner、沒有驗收,而**一則沒有
    人認領的事件比一張沒有人認領的票更容易被滑過去** —— 票至少會出現在 `list --open`
    裡,每個 session 開場都看得到。

    誤判那一半用**同一條案例只開一張**擋住:票上留 `flaky_case`,還開著就不再開。
    要完全關掉的人設 `board/config.json` 的 `flaky_auto_ticket: false`。
    """
    conf = event.config(root)
    if conf.get("flaky_auto_ticket") is False:
        return ""
    try:
        import ticket as ticket_mod                        # noqa: PLC0415
    except ImportError:
        return ""
    try:
        for row in ticket_mod.load_all():
            if str(row.get("flaky_case") or "") == case and ticket_mod.is_open(row):
                return ""
    except OSError:
        return ""
    model = (conf.get("routing") or {}).get("verify") or "sonnet"
    out = io.StringIO()
    argv = [
        "--subject", "把 %s 修穩 —— 疑似 flaky 累計 %d 次" % (case, seen),
        "--objective",
        "同一條案例連續 %d 次單跑綠、整組紅(門檻 %d)。找出它依賴的共用狀態或時序,"
        "修到同一組在原順序下跑十次都綠。" % (seen, threshold),
        "--acceptance", "原順序整組連跑 10 次,%s 沒有一次紅" % case,
        "--acceptance", "說得出它不穩的原因(共用狀態 / 時序 / 外部資源),寫進 EVIDENCE",
        "--acceptance", "不是靠放寬斷言或加 retry 讓它綠的",
        "--in-scope", case,
        "--allowed-write-path", "verify/*",
        "--allowed-write-path", "tests/*",
        "--role", "verifier", "--model", str(model), "--tool", "claude-code",
        "--state", "Ready",
    ]
    try:
        rc = ticket_mod.cmd_create(argv, stdout=out)
    except (OSError, ValueError, RuntimeError):
        return ""
    if rc != 0:
        return ""
    found = re.search(r"#(\S+)", out.getvalue())
    ident = found.group(1) if found else ""
    if not ident:
        return ""
    try:
        with ticket_mod.Lock():
            data = ticket_mod.load(ident)
            data["flaky_case"] = case
            data["flaky_seen"] = seen
            data["flaky_log"] = log
            data["flaky_from_ticket"] = from_ticket
            ticket_mod.save(data)
    except (OSError, ValueError, RuntimeError):
        pass
    return ident


def cmd_done(args):
    root = event.repo_root()
    run_id = os.environ.get("AC_RUN_ID") or args.run_id
    if not run_id:
        sys.stderr.write("status: 警告:沒有 --run-id 或 AC_RUN_ID,改用最新一輪\n")
        run_id = latest_run(root, args.ticket) or new_run_id()
    before = read(root, args.ticket, run_id)
    failures = []
    for log in args.log or []:
        failures.extend(parse_failures(log))
    suspect_names = set(args.suspected_flaky or [])
    auto_names = set(args.auto_flaky or [])
    order_names = set(args.order_dependent or [])
    for row in failures:
        row["suspected_flaky"] = row["case"] in suspect_names
        row["flaky"] = "auto" if row["case"] in auto_names else ""
        row["order_dependent"] = row["case"] in order_names
    # **留在 failures 裡**,不搬走:單跑綠只降級成「疑似」,紅還是紅(D-014 §1)。
    suspected = [row for row in failures if row["suspected_flaky"]]
    automatic = [row for row in failures if row["flaky"] == "auto"]
    failures = [row for row in failures if row["flaky"] != "auto"]
    finished = now()
    # 兩條來源收成同一個 list(`docs/DESIGN-ENV-SUSPECT.md`,D-019):環境檔的
    # `statistical` 在前,每一份 `--log` 的 `declared` 照傳進來的順序接在後面。
    # **不是「後到的整格覆寫」** —— 那樣單獨跑任一條路的測試都會綠,而同一趟兩條都
    # 有料時會靜靜地少掉一邊。
    suspects = []
    if args.environment_log:
        try:
            with open(args.environment_log, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            loaded = None
        suspects.extend(environment_suspects({"environment_suspect": loaded}))
    for log in args.log or []:
        suspects.extend(parse_environment_suspects(log))
    context = before.get("repair_context") or repair_context(root, args)
    data = {
        "state": args.state,
        "run_id": run_id,
        "kind": args.kind or before.get("kind") or "",
        "ticket": args.ticket,
        "sha": args.sha or before.get("sha") or "",
        "started": before.get("started") or finished,
        "finished": finished,
        "rc": args.rc,
        "note": args.note or before.get("note") or "",
        "report": args.report or before.get("report") or "",
        "logs": list(args.log or []),
        # 重跑的原始輸出**不再丟掉**(審查:「重跑輸出直接丟掉」)。它不進 failures
        # 的解析 —— 同一條紅解析兩遍會讓紅榜看起來有兩條 —— 但檔要留著。
        "extra_logs": list(args.extra_log or []),
        "kept_logs": keep_logs(root, args.ticket, run_id,
                               list(args.log or []) + list(args.extra_log or [])),
        "phases": before.get("phases") or [],
        "failures": failures,
        "suspected_flaky": suspected,
        "environment_suspect": suspects,
        "auto_flaky": automatic,
        "flaky": "auto" if automatic else "",
        "order_dependent": bool(order_names),
        "order_dependent_cases": sorted(order_names),
        "repair_context": context,
        # **秒數從 `before` 的 `started` 算,不從上面那一格算** —— 那一格缺料時
        # 會退回 `finished`,而拿它相減得到的 0 是一句假話(D-017 ①)。
        "duration_seconds": seconds_between(before.get("started"), finished),
        # 第幾輪。`repair_context.round` 裡本來就有,但跨票數返工輪的人要為它開
        # 一份檔、鑽一層 —— 一個數不出來的數字與一個沒有人去數的數字長得一樣。
        "round": context.get("round") if isinstance(context, dict) else None,
    }
    path = write(root, args.ticket, run_id, data)
    record_flakes(root, args.ticket, run_id, automatic or suspected)
    if suspects:
        # **這一格非空就發**(D-019),不再只有 `state == env_suspect` 才發:
        # `state` 記的是「這一段被中止」,這一格記的是「這一趟有人懷疑環境」——
        # 前者必然帶 `statistical`,而 `declared` 那幾筆會出現在 rc=1 的正常紅裡,
        # 只看 `state` 的那一版把它們全部漏掉了。
        # 事件發不出去不該讓狀態檔白寫:那份 JSON 已經在磁碟上了,而它才是接手的人
        # 要讀的東西。出聲,不改 rc(同 gate.sh 對狀態檔的態度)。
        try:
            event.emit("env.suspect", ticket=args.ticket, run_id=run_id,
                       rows=len(suspects),
                       sources=",".join(sorted({str(row.get("source") or "")
                                                for row in suspects})),
                       engines=",".join(sorted({str(row.get("engine") or "")
                                                for row in suspects
                                                if row.get("engine")})),
                       why=str(suspects[0].get("why") or ""))
        except Exception:                                  # noqa: BLE001
            sys.stderr.write("status: env.suspect 事件發不出去(狀態檔仍已寫入)\n")
    print("status: %s rc=%d 紅 %d 條(疑似 flaky %d 條,自動 flaky %d 條)"
          % (os.path.relpath(path, root), args.rc, len(failures), len(suspected),
             len(automatic)))
    return 0


def cmd_failures(args):
    """印出可以單獨重跑的案例 id,一行一個。**呼叫它的是 shell 迴圈** —— 所以這裡
    只印 id,不印別的;多印一個字,那個迴圈就會拿它當案例名去跑。"""
    seen = []
    for log in args.log or []:
        for row in parse_failures(log):
            if row["case"] not in seen:
                seen.append(row["case"])
    for case in seen:
        print(case)
    return 0


def cmd_suspects(args):
    """這一輪的 `environment_suspect` 有幾筆、各是什麼。

    **`gate.sh` 要問的是這一支,不是 grep `done` 的那一行輸出** —— 一份 grep 不到的
    輸出與一趟沒有嫌疑長得一樣(`docs/DISPATCH-TEMPLATE.md` §5.5)。所以三種答案要
    分得開:`--count` 印一個整數是「數過了」;**答不出來**(這一輪連狀態檔都沒有)
    回 2 而且**一個字都不印**,呼叫者才分得出「零筆」與「問不到」。
    """
    root = event.repo_root()
    run_id = args.run_id or latest_run(root, args.ticket)
    data = read(root, args.ticket, run_id) if run_id else {}
    if not data:
        sys.stderr.write("status: #%s 還沒有狀態檔,問不出環境可疑幾筆\n" % args.ticket)
        return 2
    rows = environment_suspects(data)
    if args.count:
        sys.stdout.write("%d\n" % len(rows))
        return 0
    sys.stdout.write("rows=%d\n" % len(rows))
    for row in rows:
        sys.stdout.write("%s\t%s\t%s\n" % (row.get("source") or "",
                                            row.get("engine") or "",
                                            row.get("why") or ""))
    return 0


def cmd_rundir(args):
    """這一輪的目錄。**回歸快取住在這裡**(`verify-<tags 雜湊>.log`),所以呼叫者
    要問得出路徑 —— 自己拼一次 `reports/t<n>/<run_id>` 的人會拼錯 `reports_dir`。"""
    root = event.repo_root()
    run_id = args.run_id or latest_run(root, args.ticket)
    if not run_id:
        sys.stderr.write("status: #%s 還沒有任何一輪\n" % args.ticket)
        return 2
    sys.stdout.write(run_dir(root, args.ticket, run_id) + "\n")
    return 0


def cmd_show(args):
    root = event.repo_root()
    if args.runs:
        rows = runs_of(root, args.ticket)
        if not rows:
            sys.stderr.write("status: #%s 一輪都還沒跑過\n" % args.ticket)
            return 2
        for name in rows:
            data = read(root, args.ticket, name)
            sys.stdout.write("%s  %s  %s  rc=%s\n"
                             % (name, data.get("kind", "?"), data.get("state", "?"),
                                data.get("rc")))
        return 0
    run_id = args.run_id or latest_run(root, args.ticket)
    data = read(root, args.ticket, run_id) if run_id else {}
    if not data:
        sys.stderr.write("status: #%s 還沒有狀態檔(%s)—— 這一輪閘門根本沒開跑?\n"
                         % (args.ticket,
                            os.path.relpath(ticket_dir(root, args.ticket), root)))
        return 2
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


def add_context_flags(parser):
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--worktree", default="")
    parser.add_argument("--patch", default="")
    parser.add_argument("--verify-patch", default="")
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--prev-evidence", default="")
    parser.add_argument("--repro", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--env", action="append", default=[])


def main(argv):
    parser = argparse.ArgumentParser(prog="status.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    start = subs.add_parser("start")
    start.add_argument("--ticket", required=True)
    start.add_argument("--run-id", default="")
    start.add_argument("--kind", default="gate")
    start.add_argument("--sha", default="")
    start.add_argument("--report", default="")
    add_context_flags(start)
    start.set_defaults(run=cmd_start)

    phase = subs.add_parser("phase")
    phase.add_argument("--ticket", required=True)
    phase.add_argument("--run-id", default="")
    phase.add_argument("--phase", required=True, choices=PHASES)
    phase.add_argument("--rc", type=int, required=True)
    phase.add_argument("--note", default="")
    phase.set_defaults(run=cmd_phase)

    done = subs.add_parser("done")
    done.add_argument("--ticket", required=True)
    done.add_argument("--run-id", default="")
    done.add_argument("--kind", default="")
    done.add_argument("--sha", default="")
    done.add_argument("--rc", type=int, required=True)
    done.add_argument("--state", choices=("done", "env_suspect"), default="done")
    done.add_argument("--environment-log", default="")
    done.add_argument("--note", default="")
    done.add_argument("--report", default="")
    done.add_argument("--log", action="append", default=[])
    done.add_argument("--extra-log", action="append", default=[])
    done.add_argument("--suspected-flaky", action="append", default=[])
    done.add_argument("--auto-flaky", action="append", default=[])
    done.add_argument("--order-dependent", action="append", default=[])
    add_context_flags(done)
    done.set_defaults(run=cmd_done)

    run_tests = subs.add_parser("run-tests")
    run_tests.add_argument("--root", required=True)
    run_tests.add_argument("--log", required=True)
    run_tests.add_argument("--suspect-file", required=True)
    run_tests.add_argument("--mode", choices=("discover", "names"), required=True)
    run_tests.add_argument("names", nargs="*")
    run_tests.set_defaults(run=cmd_run_tests)

    fails = subs.add_parser("failures")
    fails.add_argument("--log", action="append", default=[])
    fails.set_defaults(run=cmd_failures)

    suspects = subs.add_parser("suspects")
    suspects.add_argument("--ticket", required=True)
    suspects.add_argument("--run-id", default="")
    suspects.add_argument("--count", action="store_true")
    suspects.set_defaults(run=cmd_suspects)

    rundir = subs.add_parser("rundir")
    rundir.add_argument("--ticket", required=True)
    rundir.add_argument("--run-id", default="")
    rundir.set_defaults(run=cmd_rundir)

    show = subs.add_parser("show")
    show.add_argument("--ticket", required=True)
    show.add_argument("--run-id", default="")
    show.add_argument("--runs", action="store_true")
    show.set_defaults(run=cmd_show)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
