"""#23:兩條環境嫌疑來源收成同一種形狀 —— `docs/DESIGN-ENV-SUSPECT.md`(D-019)。

驗證者案例(角色:verifier,不寫實作、不下 VERDICT)。十三條逐條對應
`docs/DESIGN-ENV-SUSPECT.md` §驗收 那張表(第 12 列「T 端(同步票)」不在
agent-control 這顆 repo 的範圍內,同步票另開)。

## 兩份 fixture,兩邊都用真的
- **fixture A**:`tests/test_gate.py:33` 的 `ENVIRONMENT_WAVE`(#7 同形連紅 12 條,
  門檻預設 8)—— 這裡用 `import test_gate` 直接讀常數,不重抄一份。
- **fixture T**:這支工具的下游專案(名字走 `board/config.json`,不寫在這裡 ——
  `tests/test_no_project_names.py`)的 `verify/land_preflight/test_ticket_644.py:261-284`
  的 `SUSPECT_LINE` + `a_log()`,一個字沒改;#645 那一行是那邊
  `demo/test_browsers.py:1311` 印的原句。

## 為什麼有些測試重用 test_gate / test_auto_fix / test_board 的類別
`import test_gate` 只把模組本身綁進這個檔的命名空間(不是 `from test_gate import X`),
所以 `unittest discover` 不會把它們的 `test_*` 方法在這個模組裡重跑一次 —— 只有我在這裡
真的 `class X(test_auto_fix.AutoFixBase):` 定義出來的新類別才會被收。`AutoFixBase` /
`BoardUp` 本身不帶任何 `test_` 方法,可以放心繼承它們的助手。
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402
import test_auto_fix  # noqa: E402  重用假 worker 與 auto-fix 的交接包助手
import test_board  # noqa: E402  重用看板起服務的 BoardUp 助手
import test_gate  # noqa: E402  重用 #7 的 ENVIRONMENT_WAVE fixture

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import status as status_module  # noqa: E402

TAGS = ["env-suspect"]

SEVEN_KEYS = ("source", "engine", "why", "count", "threshold", "log", "line")

# --------------------------------------------------------------- fixture T
# 去下游專案的 `verify/land_preflight/test_ticket_644.py:261-284` 讀出來的,
# 一個字都沒改;#645 那一行是 `demo/test_browsers.py:1311` 印的原句
# (`docs/DESIGN-ENV-SUSPECT.md` §驗收 fixture T)。
SUSPECT_LINE = "ENVIRONMENT-SUSPECT: safari 螢幕鎖著(#474/#644)"
SUSPECT_WHY = "螢幕鎖著(#474/#644)"
SUSPECT_LINE_645 = "ENVIRONMENT-SUSPECT: safari session 斷了(#645)"

RED_BLOCK = """\
======================================================================
FAIL: test_%(name)s (test_browsers.TheJourney.test_%(name)s) [engine=safari]
----------------------------------------------------------------------
Traceback (most recent call last):
  File "demo/test_browsers.py", line 1530, in test_%(name)s
    browser.wait_until("location.hash === '#/home'")
AssertionError: 等了 25 秒還不成立:#gate 沒有收起來
"""


def a_log(sandbox, name, suspect_lines):
    """T 的 `a_log()` 原樣搬過來:幾行宣告 + 三條真的紅 + 真的收尾摘要。"""
    body = ["verify: 選中 demo/test_browsers.py ['browsers']"]
    body += [SUSPECT_LINE] * suspect_lines
    body += [RED_BLOCK % {"name": n} for n in ("journey", "layout", "session")]
    body += ["----------------------------------------------------------------------",
             "Ran 12 tests in 902.418s", "", "FAILED (failures=3)", ""]
    return sandbox.write(name, "\n".join(body))


# 一支案例:先印一行 T 式的宣告,再自己 skip —— 模擬 #644 那種「螢幕鎖著就不判案例
# 判環境」的最小形狀。`run-tests` 要把它的 stdout 也導進 `--log`,不然這一行只會流到
# 呼叫者(gate.sh)自己的 stdout,`done --log` 永遠讀不到。
DECLARE_AND_SKIP_CASE = """import unittest


class T(unittest.TestCase):
    def test_screen_locked(self):
        print("ENVIRONMENT-SUSPECT: firefox 假的宣告")
        self.skipTest("環境判斷,不是案例判斷 —— #644 的裁示")
"""

FAKE_WORKER = """#!/bin/sh
echo "worker ran round manual" >> "$AC_TEST_LOG"
exit 0
"""


class DoneAndStartShapes(Sandbox):
    """`status.py start` / `done`:讀 log 上的宣告行,與 `--environment-log` 的
    統計筆,合成同一格 `environment_suspect`(D-019)。"""

    def status(self, *args):
        return self.run_py("scripts/status.py", *args)

    def load(self, ticket="7"):
        return self.status_of(ticket)

    def assert_seven_keys(self, row):
        self.assertEqual(set(row), set(SEVEN_KEYS), "七鍵要一個不缺:%r" % (row,))

    # ---------------------------------------------------------- 驗收 4

    def test_acceptance_04_the_empty_value_has_exactly_one_spelling(self):
        """驗收 4:`done` 沒有環境檔、log 裡也沒有宣告行 → 這一格**存在**且
        `== []`;`start` 寫出來的檔也 `== []`(空值只有 `[]` 一種寫法)。

        **變異**:`cmd_start` 不寫這一格 → `data["environment_suspect"]` 直接
        `KeyError`,這一條紅在第一個 subTest。
        """
        with self.subTest("start"):
            self.status("start", "--ticket", "7", "--kind", "gate")
            self.assertEqual(self.load()["environment_suspect"], [])
        with self.subTest("done,沒有環境檔也沒有宣告行"):
            log = a_log(self, "clean.log", 0)
            done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            data = self.load()
            self.assertEqual(data["environment_suspect"], [])
            self.assertEqual(len(data["failures"]), 3,
                             "假 log 裡三條真紅不准被這一格影響")

    # ---------------------------------------------------------- 驗收 2

    def test_acceptance_02_two_declared_lines_in_one_log_become_two_rows(self):
        """驗收 2:`status.py done --rc 1 --log <a_log 兩行宣告>` → 2 筆、都
        `source=="declared"`、`count==1`、`threshold is None`、`engine=="safari"`、
        `why in line`、`log` 是傳進去的路徑;`rc==1`、`failures` 仍 3 條。

        **變異**:把 `parse_environment_suspects` 整支改成 `return []` → 0 筆,
        這一條紅在 `len(rows)`。
        """
        log = a_log(self, "gate.log", 2)
        self.status("start", "--ticket", "7", "--kind", "gate")
        done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.load()
        rows = data["environment_suspect"]
        self.assertEqual(len(rows), 2, rows)
        for row in rows:
            self.assert_seven_keys(row)
            self.assertEqual(row["source"], "declared")
            self.assertEqual(row["count"], 1)
            self.assertIsNone(row["threshold"])
            self.assertEqual(row["engine"], "safari")
            self.assertEqual(row["why"], SUSPECT_WHY)
            self.assertEqual(row["line"], SUSPECT_LINE)
            self.assertEqual(row["log"], log)
        self.assertEqual(data["rc"], 1, "環境嫌疑不准改 rc")
        self.assertEqual(len(data["failures"]), 3, "假 log 裡三條真紅一條都不准被搬走")

    # ---------------------------------------------------------- 驗收 3

    def test_acceptance_03_two_logs_each_with_one_line_are_not_merged(self):
        """驗收 3:同一趟兩份 log(`gate.log` 與 `gate.log.rerun`)各含 1 行宣告 →
        2 筆,`log` 的 basename 排序等於那兩個檔名 —— 一行一筆,不去重(T 的
        `demo/test_control.py:484-491` 明著要求)。

        **變異**:合成那一手依 `line` 內容去重(`{row["line"]: row for row in rows}`)
        → 2 筆變 1 筆,這一條紅。
        """
        log1 = a_log(self, "gate.log", 1)
        log2 = a_log(self, "gate.log.rerun", 1)
        self.status("start", "--ticket", "7", "--kind", "gate")
        done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log1,
                           "--log", log2)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.load()["environment_suspect"]
        self.assertEqual(len(rows), 2, "兩份 log 各一行,一行一筆,不去重:%r" % rows)
        self.assertEqual(sorted(os.path.basename(row["log"]) for row in rows),
                         sorted([os.path.basename(log1), os.path.basename(log2)]))

    # ---------------------------------------------------------- 驗收 5

    def test_acceptance_05_statistical_and_declared_can_both_have_evidence(self):
        """驗收 5:同一趟環境檔 1 筆 statistical + log 2 行 declared → 3 筆,順序
        statistical 在前,`sources` 集合 == `{statistical, declared}`。

        **這是本票最重要的變異**(D-019 outline 原話):把合成邏輯改成「後到的整
        格覆寫」(`environment_suspect = declared_rows or statistical_rows`)而不是
        併 —— 只跑驗收 2 或驗收 9 單獨那一路的測試會全綠,**只有這一條紅**。
        """
        statistical = self.write("suspect.json", json.dumps([
            {"source": "statistical", "engine": "safari",
             "why": "localStorage id=<id> empty after <n> seconds",
             "count": 8, "threshold": 8, "log": "gate.log",
             "line": "AssertionError: localStorage id=ab-1 empty after 101 seconds"}],
            ensure_ascii=False))
        log = a_log(self, "gate.log", 2)
        self.status("start", "--ticket", "7", "--kind", "gate")
        done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log,
                           "--environment-log", statistical)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = self.load()["environment_suspect"]
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual(rows[0]["source"], "statistical", "statistical 排最前面")
        self.assertEqual({row["source"] for row in rows}, {"statistical", "declared"})
        self.assertEqual({row["engine"] for row in rows}, {"safari"})

    # ---------------------------------------------------------- 驗收 7

    def test_acceptance_07_the_event_fires_whenever_non_empty_not_only_rc_86(self):
        """驗收 7:驗收 2 那一趟之後,`events.jsonl` 有一則 `env.suspect`,欄位是
        `rows=2 sources=declared engines=safari` —— 改成**這一格非空就發**,不再
        只有 `rc=86` 才發。

        **變異**:退回舊守衛 `if args.state == "env_suspect":` → `state` 仍是
        `done`(rc=1)時這一條紅,`env.suspect` 不會出現在 `events.jsonl`。
        """
        log = a_log(self, "gate.log", 2)
        self.status("start", "--ticket", "7", "--kind", "gate")
        done = self.status("done", "--ticket", "7", "--rc", "1", "--log", log)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("env.suspect", self.kinds())
        row = [r for r in self.events() if r["kind"] == "env.suspect"][-1]
        self.assertEqual(row.get("rows"), 2)
        self.assertEqual(row.get("sources"), "declared")
        self.assertEqual(row.get("engines"), "safari")
        self.assertTrue(row.get("why"), "第一筆的 why 要帶在事件裡")


class RunTestsCapturesDeclaredLines(Sandbox):
    """`run-tests`:案例自己 `print()` 的宣告行要落進同一份 `--log`,不然
    `done --log` 永遠讀不到它(#644 的形狀;D-019 §遷移 `contextlib.redirect_stdout`)。
    """

    def status(self, *args):
        return self.run_py("scripts/status.py", *args)

    def test_acceptance_06_a_printed_declaration_survives_into_the_log(self):
        """驗收 6:run-tests 跑一條會印一行宣告(前綴 + `firefox 假的宣告`)然後
        skip 的案例 → `--log` 那份檔裡找得到那一行(`cmd_run_tests` 要把案例 stdout
        也導進 log),接著 `done --log` 得 1 筆 declared。

        這一段**故意不逐字寫出那個前綴**:`unittest -v` 會把 docstring 的第一行印進
        同一份 log,而逐字寫的那一行會被讀成一筆宣告 —— #23 第 1 輪就是這樣把自己
        那一輪的 auto-fix 擋掉的。要提到它就用 `status.SUSPECT_PREFIX` 拼。

        **變異**:不包 `contextlib.redirect_stdout` → `print()` 那一行流到
        `run-tests` 呼叫者自己的 stdout,不進 `--log`。這一條紅在「log 裡找不到
        那一行」(T 今天沒中招是因為 T 不走 `run-tests`,整段 stdout 被
        `test-for.sh` 收進 `gate.log`)。
        """
        self.write(os.path.join("tests", "test_declare_and_skip.py"),
                   DECLARE_AND_SKIP_CASE)
        log = os.path.join(self.repo, "gate.log")
        suspect = os.path.join(self.repo, "gate.log.env-suspect.json")
        done = self.status("run-tests", "--root", self.repo, "--log", log,
                           "--suspect-file", suspect, "--mode", "names",
                           "test_declare_and_skip")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(log, encoding="utf-8") as handle:
            log_text = handle.read()
        self.assertIn("ENVIRONMENT-SUSPECT: firefox 假的宣告", log_text,
                      "案例自己印的宣告行要落進同一份 log,不是流到呼叫者的 stdout")
        self.status("start", "--ticket", "7", "--kind", "gate")
        self.status("done", "--ticket", "7", "--rc", "0", "--log", log)
        rows = self.status_of("7")["environment_suspect"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source"], "declared")
        self.assertEqual(rows[0]["engine"], "firefox")
        self.assertEqual(rows[0]["why"], "假的宣告")


class GateEndToEnd(test_auto_fix.AutoFixBase):
    """`gate.sh` 端到端:#7 的連紅偵測(fixture A = `ENVIRONMENT_WAVE`)落到
    `status.json` 的新形狀,以及 auto-fix 對這一格該有的反應。繼承
    `AutoFixBase` 只是借它的 `set_worker` / `worker_rounds`(它自己不帶任何
    `test_` 方法,不會被重跑)。
    """

    def gate(self, *args):
        return self.run_sh("scripts/gate.sh", *args)

    def test_acceptance_01_environment_wave_lands_as_one_statistical_row(self):
        """驗收 1:gate 跑 `tests/test_gate.py:33` 的 `ENVIRONMENT_WAVE`
        (#7 同形連紅 12 條,門檻預設 8),`--ticket 7 --no-auto-fix` →
        `status.json` 的 `state=="env_suspect"`;`environment_suspect` 是
        list、長度 1;`[0]` 七鍵齊全、`source=="statistical"`、
        `engine=="safari"`、`count==8`、`threshold==8`、`why` 不含數字
        (正規化過)、`line` 含原始未正規化訊息的字(例如 `101 seconds`)。

        **變異**:run-tests 對這一筆繼續寫成裸 dict(不包成 `[row]`)→
        `environment_suspect` 不是 list,`assertIsInstance` 這一條紅。
        """
        self.write(os.path.join("tests", "test_env_wave.py"), test_gate.ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        done = self.gate("tests/test_env_wave.py", "--ticket", "7", "--no-auto-fix")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.status_of("7")
        self.assertEqual(data["state"], "env_suspect")
        rows = data["environment_suspect"]
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(set(row), set(SEVEN_KEYS))
        self.assertEqual(row["source"], "statistical")
        self.assertEqual(row["engine"], "safari")
        self.assertEqual(row["count"], 8)
        self.assertEqual(row["threshold"], 8)
        self.assertIsNone(re.search(r"\d", row["why"]),
                          "why 是正規化過的形狀,不准帶數字:%r" % row["why"])
        # `line` = 觸發門檻那一條紅**未正規化**的訊息第一行(D-019 §結論;文件的
        # 範例就是 `AssertionError: localStorage id=ab-1 empty after 101 seconds`)。
        # 所以這一格**必須帶得回數字** —— 那正是 `why` 被抹掉的東西,也是「那台機器
        # 當時說了什麼」唯一還答得出來的地方。期望值從 fixture 那一句
        # `self.fail("localStorage id=%d empty after %d seconds")` 來
        # (`tests/test_gate.py` 的 `ENVIRONMENT_WAVE`),不從程式現在吐什麼來。
        self.assertRegex(row["line"], r"localStorage id=\d+ empty after \d+ seconds")
        self.assertNotEqual(row["line"], row["why"],
                            "line 是原文、why 是形狀,兩格不准是同一個字串")

    def test_acceptance_08_gate_without_no_auto_fix_refuses_to_dispatch(self):
        """驗收 8:gate 跑 `ENVIRONMENT_WAVE` **不帶** `--no-auto-fix`(沙盒
        `worker.command` 換成一支只記一行的假指令)→ 假 worker **一次都沒被
        叫**,stdout 含「環境可疑,不自動派」。

        **變異**:拿掉 `auto_fix()` 開頭那一行守衛(`[ "$ENV_SUSPECT" -eq 0 ] ||
        { … return 0; }`)→ 假 worker 被叫,`worker_rounds()` 不再是空的,這一條
        紅在最後一個斷言。
        """
        self.write(os.path.join("tests", "test_env_wave.py"), test_gate.ENVIRONMENT_WAVE)
        self.make_ticket(7, allowed_write_paths=["tests/*"])
        self.set_worker(FAKE_WORKER)
        # `set_worker` 改了 `board/config.json`;committed 才不會被 gate.sh 的
        # preflight 當成越界的改動檔(它只認票的 `allowed_write_paths`)。
        self.git("add", "board/config.json")
        self.git("commit", "-q", "-m", "沙盒的假 worker.command")
        done = self.gate("tests/test_env_wave.py", "--ticket", "7")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("環境可疑,不自動派", done.stdout)
        self.assertEqual(self.worker_rounds(), [],
                         "環境可疑的時候不准派 worker 進一台跑不動的機器")


class NormalizeOnRead(unittest.TestCase):
    """驗收 9:讀端正規化 `environment_suspects(data)` —— 四種舊「沒有」與一種
    舊「非空」都要吃得下(`docs/DISPATCH-TEMPLATE.md` §5.5「拿不到就當空的」)。
    """

    def test_acceptance_09_three_hand_built_shapes_normalize_correctly(self):
        """三份手造 `status.json`(缺這一格 / `{}` / 舊形狀非空 dict
        `{"engine":"safari","count":8}`)→ 前兩份得 `[]`,第三份得 1 筆
        `statistical`。

        **變異**:拿掉「非空 dict → 包成一筆 statistical」那一支分支 → 第三份也
        變成 `[]`,這一條紅在第三個 subTest。
        """
        with self.subTest("缺這一格"):
            self.assertEqual(status_module.environment_suspects({}), [])
        with self.subTest("空 dict"):
            self.assertEqual(
                status_module.environment_suspects({"environment_suspect": {}}), [])
        with self.subTest("舊形狀非空 dict"):
            rows = status_module.environment_suspects(
                {"environment_suspect": {"engine": "safari", "count": 8}})
            self.assertEqual(len(rows), 1, rows)
            row = rows[0]
            self.assertEqual(set(row), set(SEVEN_KEYS))
            self.assertEqual(row["source"], "statistical")
            self.assertEqual(row["engine"], "safari")
            self.assertEqual(row["count"], 8)
            self.assertIsNone(row["threshold"], "舊形狀沒有的欄位是 null,不是猜一個")
            self.assertIsNone(row["why"])
            self.assertEqual(row["line"], "")
            self.assertEqual(row["log"], "")


class MetricsClassification(Sandbox):
    """`scripts/metrics.py`:紅的分類一律經 `environment_suspects()` 正規化讀。"""

    def metrics(self, *args):
        return self.run_py("scripts/metrics.py", *args)

    def status(self, *args):
        return self.run_py("scripts/status.py", *args)

    def put_run(self, ident, run_id, **fields):
        data = {"state": "done", "run_id": run_id, "kind": "gate", "ticket": str(ident),
                "rc": 1, "started": None, "finished": None, "phases": [], "failures": [],
                "environment_suspect": [], "duration_seconds": None, "round": 1}
        data.update(fields)
        self.write(os.path.join("reports", "t%s" % ident, run_id, "status.json"),
                   json.dumps(data, ensure_ascii=False, indent=2))

    def line(self, ident):
        done = self.metrics("line", str(ident))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout.splitlines()[0]

    def test_acceptance_10_env_and_product_classify_through_the_normalizer(self):
        """驗收 10:既有那條 fixture 改成 list 之後 `env_runs=1 product_runs=1`;
        另加一份舊 dict 形狀的 run → `env_runs=2`(metrics 與 board 一律經正規化
        函式讀,不直接下標)。

        r2 走**真的** `status.py done --log`(不是手造 `status.json`):在乾淨
        基底上 `parse_environment_suspects` 不存在,`environment_suspect` 停在
        `{}`,這一趟會被誤算進 product —— dict 與 list 對 `if data.get(...)`
        都是真值,單靠真值判斷分不出兩邊,所以這裡刻意接真的 `done --log` 讓
        「有沒有實作宣告行解析」成為這一條的紅/綠分野。

        **變異**:分類只認 `isinstance(value, list)`(不把舊 dict 包成一筆)→
        第三輪(舊 dict)掉回 product,`env_runs` 停在 1,這一條紅在第二次
        `assertIn("env_runs=2", …)`。
        """
        self.make_ticket(7)
        self.put_run(7, "r1", rc=0)
        log = a_log(self, "gate.log", 1)
        self.status("start", "--ticket", "7", "--run-id", "r2")
        self.status("done", "--ticket", "7", "--run-id", "r2", "--rc", "1", "--log", log)
        line = self.line(7)
        self.assertIn("red_runs=1", line)
        self.assertIn("env_runs=1", line, "log 上的宣告行要能把這一趟算進 env")
        self.assertIn("product_runs=0", line)
        self.put_run(7, "r3", rc=1, environment_suspect={"engine": "safari", "count": 8})
        line = self.line(7)
        self.assertIn("red_runs=2", line)
        self.assertIn("env_runs=2", line, "舊 dict 形狀的那一輪也要被正規化算進 env")
        self.assertIn("product_runs=0", line)


class BoardShowsEnvironmentSuspicion(test_board.BoardUp):
    """`board/board.py`:⑥ 每票最後一輪那一列要看得到環境嫌疑,而且不蓋掉紅榜。
    繼承 `BoardUp` 只借它起服務的助手,它自己不帶任何 `test_` 方法。
    """

    def test_acceptance_11_the_run_row_shows_the_suspicion_and_still_shows_the_reds(self):
        """驗收 11:一份 run 帶 1 筆 declared(engine firefox)且 `failures` 3 條
        → 該列含「環境可疑 1 筆」與 firefox,且**仍然**含「紅 3 條」。

        **變異**:`verdict_cell` 不看 `env_suspects` → 那一列只剩「紅 3 條」,
        沒有「環境可疑」,這一條紅。
        """
        self.make_ticket(1)
        self.write(os.path.join("reports", "t1", "20260923-100000-1", "status.json"),
                   json.dumps({
                       "state": "done", "run_id": "20260923-100000-1", "kind": "gate",
                       "ticket": "1", "rc": 1,
                       "started": "2026-09-23T10:00:00+08:00",
                       "finished": "2026-09-23T10:02:00+08:00",
                       "duration_seconds": 120,
                       "failures": [{"case": "t.a"}, {"case": "t.b"}, {"case": "t.c"}],
                       "environment_suspect": [
                           {"source": "declared", "engine": "firefox",
                            "why": "session 斷了", "count": 1, "threshold": None,
                            "log": "gate.log",
                            "line": "ENVIRONMENT-SUSPECT: firefox session 斷了"}],
                   }, ensure_ascii=False))
        self.up()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        runs = body.split("id=\"runs\"")[1].split("</section>")[0]
        row = runs.split("data-ticket=\"1\"")[1].split("</tr>")[0]
        self.assertIn("環境可疑 1 筆", row)
        self.assertIn("firefox", row)
        self.assertIn("紅 3 條", row, "環境可疑不准把原本的紅榜蓋掉")


class AutoFixManualDispatch(test_auto_fix.AutoFixBase):
    """`auto-fix.sh` 被人手打:非空這一格是**警告**,不是**擋門**(gate.sh 的自動
    路徑才擋;手打就是覆寫)。繼承 `AutoFixBase` 借 `set_worker` /
    `ticket_ready` / `status` / `auto_fix` / `worker_rounds`,它自己不帶任何
    `test_` 方法。
    """

    def test_acceptance_12_manual_auto_fix_warns_but_still_dispatches(self):
        """驗收 12:`auto-fix.sh` 被手打時,最新一輪這一格非空 → stdout 印一行
        「上一輪環境可疑(<engine>:<why>),你確定要派?」,但**仍然照派**。

        **變異**:把警告後的邏輯改成硬擋(仿 gate.sh 自動路徑那樣直接
        `return`)→ 假 worker 沒被叫,`worker_rounds()` 是空的,這一條紅在最後
        一個斷言。
        """
        self.set_worker(test_auto_fix.WORKER_FIXES)
        self.ticket_ready()
        done = self.status(1, test_auto_fix.RED_LOG)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        run_id = "20260921-100000-1"
        rel = os.path.join("reports", "t1", run_id, "status.json")
        data = json.loads(self.read(rel))
        data["environment_suspect"] = [
            {"source": "declared", "engine": "safari", "why": "session 斷了(#645)",
             "count": 1, "threshold": None, "log": "round.log",
             "line": SUSPECT_LINE_645}]
        self.write(rel, json.dumps(data, ensure_ascii=False))
        result = self.auto_fix()
        self.assertIn("上一輪環境可疑(safari:session 斷了(#645)),你確定要派?",
                      result.stdout)
        self.assertEqual(self.worker_rounds(), ["worker ran round 2"],
                         "手打就是覆寫 —— 仍然要派")


class SingleSourceOfShapeTruth(unittest.TestCase):
    """驗收 13:`docs/DESIGN-ENV-SUSPECT.md` 是形狀的唯一真實來源。"""

    def test_acceptance_13_the_shape_lives_in_exactly_one_document(self):
        """`status.py` / `gate.sh` / `auto-fix.sh` / `metrics.py` / `board.py`
        碰這一格的地方註解指得到它;`docs/WORKFLOW.md` 的狀態檔 schema 也指得到
        它;`docs/DECISIONS.md` 收本條(D-019)。

        **變異**:把 `scripts/status.py` 裡那一句引用拿掉 → 這一條紅在第一輪
        迭代(`scripts/status.py`)。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("scripts/status.py", "scripts/gate.sh", "scripts/auto-fix.sh",
                    "scripts/metrics.py", "board/board.py", "docs/WORKFLOW.md"):
            with open(os.path.join(root, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("DESIGN-ENV-SUSPECT", text,
                          "%s 碰 environment_suspect 卻沒有指到唯一的形狀文件" % rel)
        with open(os.path.join(root, "docs", "DECISIONS.md"), encoding="utf-8") as handle:
            self.assertIn("D-019", handle.read())


if __name__ == "__main__":
    unittest.main()
