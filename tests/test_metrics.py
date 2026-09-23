"""`scripts/metrics.py`:每票一行的數字(D-017 ①)。

這一組問的不是「數字算得對不對」,是**數字答不出來的時候會不會被發現**:

- `tokens=0` 與「那一趟的 log 沒有記」揉成同一格的那一刻,「這張票很省」與「我沒去
  量」長得一樣 —— 而只有前者可以拿來做決定(`docs/DESIGN.md` §15:未知不可顯示為零)。
- 紅了幾趟只印一個總數的話,「環境爛了四次」與「程式錯了四次」長得一樣;所以
  `env_runs + product_runs` 必須恰好等於 `red_runs`,少一格就是有一種紅沒有歸類。
- `runs:` 數的是趟、`objections:` 數的是筆,**兩個單位不准相加**;一串沒有分段標示的
  `a=1 b=2 c=3` 會被下一個人 `awk` 起來加總,而那個總和看起來完全正常。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# `claude -p --output-format json` 的尾巴形狀:一個物件、一格 `usage`。
JSON_LOG = """開場講了幾句話
{
  "type": "result",
  "is_error": false,
  "usage": {"input_tokens": 1200, "output_tokens": 345,
            "cache_read_input_tokens": 999}
}
"""
# 另一種產出端:散文裡夾一行總計。
COUNTED_LOG = "worker 跑完了\ntokens used 123\n收工\n"
# 什麼都沒記的那一種 —— 這個 repo 2026-09-22 磁碟上那一份真的長這樣。
PROSE_LOG = "worker 跑完了,交了 patch 與 EVIDENCE。\n沒有人在這裡記過用量。\n"

# 12 個鍵字面。少一個就是控制台某一欄沒有來源。
KEYS = ("ticket=", "gate_runs=", "land_runs=", "apply_runs=", "fix_rounds=",
        "red_runs=", "env_runs=", "product_runs=", "wall_seconds=",
        "objections_ticket_wrong=", "objections_test_defect=", "tokens=")


class Metrics(Sandbox):

    def metrics(self, *args):
        return self.run_py("scripts/metrics.py", *args)

    def status(self, *args):
        return self.run_py("scripts/status.py", *args)

    def line(self, ident):
        done = self.metrics("line", str(ident))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = done.stdout.splitlines()
        self.assertEqual(len(rows), 1, "一張票就是一行:%r" % done.stdout)
        return rows[0]

    def put_run(self, ident, run_id, **fields):
        """手工造一輪。**只有需要指定 `status.py` 算出來的那幾格時才用它** ——
        其他地方走真的 `status.py`,不然測到的是這份 fixture 對格式的理解。"""
        data = {"state": "done", "run_id": run_id, "kind": "gate",
                "ticket": str(ident), "rc": 0, "started": None, "finished": None,
                # 空值只有 `[]` 一種寫法(D-019,`docs/DESIGN-ENV-SUSPECT.md`)。
                "phases": [], "failures": [], "environment_suspect": [],
                "duration_seconds": None, "round": 1}
        data.update(fields)
        self.write(os.path.join("reports", "t%s" % ident, run_id, "status.json"),
                   json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def put_worker_log(self, ident, run_id, body, rounds=1):
        return self.write(os.path.join("reports", "t%s" % ident, run_id,
                                       "worker-round%d.log" % rounds), body)

    # ------------------------------------------------------- 一行、十二個鍵

    def test_a_ticket_with_two_runs_prints_exactly_one_line_with_every_key(self):
        """**變異**:從 `line_of` 拿掉任何一個鍵 → 這一條紅。

        走的是真的 `status.py`:這一行的來源必須是那一支真的寫出來的檔,不是這份
        測試自己捏的 JSON(`docs/DISPATCH-TEMPLATE.md` §5.6)。
        """
        self.make_ticket(7)
        self.status("start", "--ticket", "7", "--kind", "gate", "--run-id", "r1")
        self.status("done", "--ticket", "7", "--run-id", "r1", "--rc", "0")
        self.status("start", "--ticket", "7", "--kind", "land", "--run-id", "r2")
        self.status("done", "--ticket", "7", "--run-id", "r2", "--rc", "0")
        line = self.line(7)
        for key in KEYS:
            self.assertIn(key, line, "少了 %s 這一格" % key)
        self.assertIn("gate_runs=1", line)
        self.assertIn("land_runs=1", line)

    def test_the_wall_clock_seconds_come_from_the_status_files(self):
        self.make_ticket(7)
        self.put_run(7, "r1", duration_seconds=140)
        self.put_run(7, "r2", duration_seconds=278)
        self.assertIn("wall_seconds=418", self.line(7))

    def test_the_rework_rounds_are_the_highest_round_any_run_reached(self):
        """返工輪數要跨輪看:一張票的最後一輪可能是綠的第 3 輪,而「跑了 3 輪」
        正是要回答的事。"""
        self.make_ticket(7)
        self.put_run(7, "r1", round=1, rc=1)
        self.put_run(7, "r2", round=2, rc=1)
        self.put_run(7, "r3", round=3, rc=0)
        self.assertIn("fix_rounds=3", self.line(7))

    def test_a_status_file_from_before_this_ticket_still_has_numbers(self):
        """**變異**:拿掉 `round_of` / `duration_of` 的退路 → 這一條紅。

        `duration_seconds` 與頂層的 `round` 是這張票才加的,而磁碟上那幾十份檔是
        之前寫的 —— 只問新的那兩格,控制台會對每一張舊票印 `返工輪 0`、
        `wall_seconds=0`,而**那是一句假話,不是一格空白**。兩個時間戳與
        `repair_context.round` 在舊檔裡一直都在。
        """
        self.make_ticket(7)
        old_shape = {"state": "done", "run_id": "r1", "kind": "gate", "ticket": "7",
                     "rc": 0, "started": "2026-09-22T16:38:51+08:00",
                     "finished": "2026-09-22T16:41:11+08:00",
                     "repair_context": {"round": 2}}
        self.write(os.path.join("reports", "t7", "r1", "status.json"),
                   json.dumps(old_shape, ensure_ascii=False))
        line = self.line(7)
        self.assertIn("fix_rounds=2", line, line)
        self.assertIn("wall_seconds=140", line, line)

    # ---------------------------------------------------------- 紅的分類

    def test_env_and_product_add_up_to_red(self):
        """**變異**:把 `environment_suspect` 那一問拿掉(兩支都記進 product)
        → 這一條紅。

        少了這一格,「環境爛了」與「程式錯了」在同一個 `red_runs=2` 裡長得一樣,
        而那兩件事的下一步完全不同。
        """
        self.make_ticket(7)
        self.put_run(7, "r1", rc=0)
        self.put_run(7, "r2", rc=1, environment_suspect=[
            {"source": "statistical", "engine": "safari",
             "why": "localStorage id=<id> empty after <n> seconds",
             "count": 8, "threshold": 8, "log": "gate.log",
             "line": "AssertionError: localStorage id=ab-1 empty after 101 seconds"}])
        self.put_run(7, "r3", rc=1, environment_suspect=[])
        line = self.line(7)
        self.assertIn("red_runs=2", line)
        self.assertIn("env_runs=1", line)
        self.assertIn("product_runs=1", line)
        numbers = dict(part.split("=", 1) for part in line.split()
                       if part.count("=") == 1 and part.split("=")[0].endswith("_runs"))
        self.assertEqual(int(numbers["env_runs"]) + int(numbers["product_runs"]),
                         int(numbers["red_runs"]),
                         "有一種紅沒有被歸類:%s" % line)

    def test_an_old_dict_shaped_run_still_counts_as_an_environment_red(self):
        """驗收 10 的後半:**舊檔不改寫**(D-014),相容性由讀端正規化吃掉。

        `reports/` 裡有 95 份舊狀態檔,而分類是回頭讀整個 `reports/` 算出來的 ——
        一份舊檔在新的讀法下被算成「程式紅」,那張票的歷史數字就從此說謊。

        **變異**:`metrics.py` 不經 `status.environment_suspects()`,直接看那一格的
        真值 → 這一條還綠(舊 dict 也是真的);把分類改成讀 `[0]["source"]`
        → 這一條炸在下標上。兩種都要有人接得住,所以一律經正規化函式。
        """
        self.make_ticket(7)
        self.put_run(7, "r1", rc=1, environment_suspect=[
            {"source": "declared", "engine": "firefox", "why": "session 斷了",
             "count": 1, "threshold": None, "log": "gate.log",
             "line": "ENVIRONMENT-SUSPECT: firefox session 斷了"}])
        # #7 那一版寫的單筆 dict,`message_shape` 是今天的 `why`。
        self.put_run(7, "r2", rc=1,
                     environment_suspect={"engine": "safari", "count": 8,
                                          "message_shape": "storage empty"})
        self.put_run(7, "r3", rc=1, environment_suspect=[])
        line = self.line(7)
        self.assertIn("red_runs=3", line)
        self.assertIn("env_runs=2", line)
        self.assertIn("product_runs=1", line)

    def test_a_run_still_going_is_not_counted_as_red(self):
        """`rc` 是 `null` 的那一份是**還在跑**。算成紅等於把「要等」講成「要修」。"""
        self.make_ticket(7)
        self.put_run(7, "r1", state="running", rc=None)
        line = self.line(7)
        self.assertIn("red_runs=0", line)

    # ------------------------------------------------------ 兩種單位不相加

    def test_runs_and_objections_are_printed_in_two_labelled_halves(self):
        """**變異**:拿掉 `objections:` 那個分段標示 → 這一條紅。"""
        self.make_ticket(7, objections=[
            {"category": "ticket-wrong", "body": "票面那一條做不到", "owner": "main"},
            {"category": "test_defect", "body": "oracle 錯了", "owner": "main"},
            {"category": "test_defect", "body": "fixture 也錯了", "owner": "main"}])
        self.put_run(7, "r1", rc=1)
        line = self.line(7)
        self.assertIn("runs:", line, "沒有分段標示,兩種單位會被加在一起")
        self.assertIn("objections:", line, "沒有分段標示,兩種單位會被加在一起")
        self.assertIn("objections_ticket_wrong=1", line)
        self.assertIn("objections_test_defect=2", line)
        self.assertLess(line.index("runs:"), line.index("objections:"))

    # --------------------------------------------------------- token 三條路

    def test_the_three_token_shapes_and_the_one_that_stays_unknown(self):
        """**變異**:把 `tokens_in_log` 最後那一句 `return None` 改成 `return 0`
        → 這一條紅,訊息含「未知」。

        這是 D-017 ① 的那一句話的可執行形狀:**拿不到 token 的那一趟寫「未知」,
        不估、不寫 0。**
        """
        self.make_ticket(7)
        self.put_run(7, "r1")
        self.put_worker_log(7, "r1", JSON_LOG)
        self.assertIn("tokens=已知1趟=1545", self.line(7),
                      "尾端 JSON 的 usage 要取 input 加 output")

        self.put_worker_log(7, "r1", COUNTED_LOG)
        self.assertIn("tokens=已知1趟=123", self.line(7),
                      "沒有 JSON 但有一行 `tokens used N` 就取 N")

        self.put_worker_log(7, "r1", PROSE_LOG)
        line = self.line(7)
        self.assertIn("tokens=未知", line,
                      "兩種形狀都認不出來的那一趟是未知,不是 0:%s" % line)
        self.assertNotIn("tokens=0", line,
                         "未知被寫成 0 了 —— 那與「這一趟很省」長得一樣:%s" % line)

    def test_a_known_trip_and_an_unknown_trip_are_both_reported(self):
        """一趟問得出來、一趟問不出來時,總和**不可以**把問不出來的那一趟當 0 吃掉。"""
        self.make_ticket(7)
        self.put_run(7, "r1")
        self.put_worker_log(7, "r1", JSON_LOG, rounds=1)
        self.put_worker_log(7, "r1", PROSE_LOG, rounds=2)
        line = self.line(7)
        self.assertIn("tokens=已知1趟=1545 未知1趟", line, line)

    # --------------------------------------------- 沒在看 vs 看了、沒有東西

    def test_no_directory_and_an_empty_directory_do_not_read_the_same(self):
        """**變異**:把 `note` 那兩句話寫成同一句 → 這一條紅。

        `[]` 是「看著,而一發都沒有」,`None` 是「這台根本沒在看」
        (`docs/DISPATCH-TEMPLATE.md` §5.5)。
        """
        self.make_ticket(7)
        self.make_ticket(8)
        os.makedirs(os.path.join(self.repo, "reports", "t8"))

        def without_the_id(line, ident):
            # 票號本來就不一樣,不把它洗掉的話兩行永遠不相等 —— 那條斷言就變成
            # 「答不出來的時候看起來也是通過」的那一種。
            return line.replace("t%d" % ident, "tN").replace("ticket=%d" % ident,
                                                             "ticket=N")
        self.assertNotEqual(without_the_id(self.line(7), 7),
                            without_the_id(self.line(8), 8),
                            "目錄不在與目錄空著印了同一句話")

    def test_a_ticket_with_no_reports_prints_zero_runs_but_unknown_tokens(self):
        """`runs` 是「看了,而一趟都沒有」→ 0;`tokens` 是「根本沒有 log 可以看」
        → 未知。兩者揉成同一個 0 就是這一節在擋的事。"""
        self.make_ticket(7)
        line = self.line(7)
        for key in ("gate_runs=0", "land_runs=0", "apply_runs=0", "fix_rounds=0",
                    "red_runs=0", "env_runs=0", "product_runs=0", "wall_seconds=0"):
            self.assertIn(key, line, line)
        self.assertIn("tokens=未知", line, line)
        self.assertNotIn("tokens=0", line, line)

    # ------------------------------------------------------------- all

    def test_all_prints_one_line_per_ticket_in_numeric_order(self):
        """**變異**:把排序改成字典序 → 這一條紅(`10` 會排到 `2` 前面)。"""
        for ident in (2, 10, 3):
            self.make_ticket(ident)
        done = self.metrics("all")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = done.stdout.splitlines()
        self.assertEqual(len(rows), 3, done.stdout)
        self.assertEqual([row.split()[0] for row in rows],
                         ["ticket=2", "ticket=3", "ticket=10"])

    def test_all_json_carries_the_same_numbers_the_board_draws(self):
        self.make_ticket(7)
        self.put_run(7, "r1", rc=1, round=2, environment_suspect=[
            {"source": "declared", "engine": "safari", "why": "螢幕鎖著(#474)",
             "count": 1, "threshold": None, "log": "gate.log",
             "line": "ENVIRONMENT-SUSPECT: safari 螢幕鎖著(#474)"}])
        done = self.metrics("all", "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = json.loads(done.stdout)["7"]
        self.assertEqual(row["fix_rounds"], 2)
        self.assertEqual((row["red_runs"], row["env_runs"], row["product_runs"]),
                         (1, 1, 0))
        self.assertEqual(row["tokens"], "未知")


if __name__ == "__main__":
    unittest.main()
