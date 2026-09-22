"""驗證者案例 —— #19:控制台直接讀已經在磁碟上的結構化檔。

票面驗收(行為 + 功能標籤)由驗證者自己寫成案例(角色卡 D-G122),不讀實作者的
EVIDENCE。API 層:起 `board/board.py --port 0`,在沙盒裡造 `reports/t<n>/<run_id>/
status.json`、`reports/inbox/{index,acked}.jsonl`、票自己的 `objections` /
`review` / `verify` 三格,斷言頁面上看得到的字面**來自這幾份自己寫進去的檔**,不是
程式現在剛好印什麼。

沙盒借用 `tests/control_harness.Sandbox`(拋棄式真 git repo、真的把 `board.py` 起成
一個子行程)—— 這是這個 repo 起 board 的唯一形狀,重寫一份等於測自己另外寫的模擬器
(`docs/DISPATCH-TEMPLATE.md` §5.6)。HTTP 客戶端與磁碟 fixture 是這支檔案自己的,
不借用 `tests/test_board.py`(那是實作者的單元層,這裡是獨立的回歸層)。
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

TAGS = ["board-reads"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import Sandbox  # noqa: E402

BOARD_PY = os.path.join(ROOT, "board", "board.py")
PORT_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/")
VERDICT = re.compile(r'<td class="verdict">(.*?)</td>')


class BoardReads(Sandbox):
    """起一顆 `board.py --port 0`,GET 它;外加磁碟 fixture 的幾個小工具。"""

    def serve(self):
        proc = subprocess.Popen(
            ["python3", os.path.join(self.repo, "board", "board.py"), "--port", "0"],
            cwd=self.repo, env=self.env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        # 只殺自己起的那一顆,不去獵行程(`docs/DISPATCH-TEMPLATE.md` §2)。
        self.addCleanup(self._stop, proc)
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stderr.readline()
            found = PORT_LINE.search(line or "")
            if found:
                return proc, int(found.group(1))
            if proc.poll() is not None:
                self.fail("看板沒起來:" + (proc.stderr.read() or ""))
        self.fail("看板 20 秒內沒有報出埠")

    def _stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                pipe.close()

    def up(self):
        self.port_cache = self.serve()

    def get(self, path="/"):
        _, port = self.port_cache
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=20) as response:
            return response.status, response.read().decode("utf-8")

    def status(self, ident, run_id, **fields):
        row = {"state": "done", "run_id": run_id, "kind": "gate",
               "ticket": str(ident), "rc": 0,
               "started": "2026-09-22T10:00:00+08:00",
               "finished": "2026-09-22T10:02:00+08:00",
               "duration_seconds": 60, "failures": []}
        row.update(fields)
        self.write(os.path.join("reports", "t%s" % ident, run_id, "status.json"),
                   json.dumps(row, ensure_ascii=False))
        return row

    def inbox_file(self, rows, name="index.jsonl"):
        self.write(os.path.join("reports", "inbox", name),
                   "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    def section(self, body, anchor):
        return body.split('id="%s"' % anchor)[1].split("</section>")[0]


class LoadersReadWhatIsOnDisk(BoardReads):
    """#19 驗收 ①②③:三個 loader 真的讀到磁碟上的檔,不是猜的。"""

    def test_1_runs_and_inbox_come_from_the_files_themselves(self):
        """期望值是自己寫進去的那幾個字 —— 不是程式現在印什麼。"""
        self.make_ticket(1, subject="驗證者19-loader票")
        self.status(1, "20260920-090000-1", rc=0)
        self.status(1, "20260921-100000-2", rc=1, failures=[
            {"case": "verify19.zzqx.CaseA.test_probe",
             "excerpt": "AssertionError: verify19-probe-failed\n第二行不該上畫面"}])
        self.inbox_file([
            {"name": "v19-open", "ticket": "1", "state": "閘門紅",
             "what": "verify19-open-item", "where": "reports/t1/x/status.json"},
            {"name": "v19-acked", "ticket": "1", "state": "收下過的",
             "what": "verify19-acked-item", "where": "reports/t1/y/status.json"}])
        self.inbox_file([{"name": "v19-acked", "ticket": "1"}], name="acked.jsonl")
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        self.assertIn("20260921-100000-2", runs, "印的要是最後那一輪")
        self.assertIn("verify19.zzqx.CaseA.test_probe", runs, "紅榜的 case 沒畫出來")
        self.assertIn("AssertionError: verify19-probe-failed", runs)
        self.assertNotIn("第二行不該上畫面", runs, "excerpt 只印首行")
        inbox = self.section(body, "inbox")
        self.assertIn("verify19-open-item", inbox)
        self.assertNotIn("verify19-acked-item", inbox, "ack 過的那一則不該還在清單上")

    def test_2_runs_are_shown_in_time_order(self):
        """`run_id` 開頭是時間戳,字典序就是時間序。"""
        self.make_ticket(2)
        self.status(2, "20260919-080000-1")
        self.status(2, "20260922-210000-2")
        self.up()
        _, body = self.get("/t/2")
        self.assertLess(body.index("20260919-080000-1"),
                        body.index("20260922-210000-2"),
                        "舊的一輪要出現在新的一輪之前")

    def test_3_result_round_json_present_and_absent_draw_differently(self):
        """同一張票、兩趟 run:一趟沒有 `result-round<r>.json`,一趟有。"""
        self.make_ticket(3)
        self.status(3, "20260920-090000-1")
        self.status(3, "20260921-090000-2")
        self.write(os.path.join("reports", "t3", "20260921-090000-2",
                                "result-round4.json"),
                   json.dumps({"rc": 1, "gate": {"ran": True},
                               "mutations": [{"a": 1}, {"a": 2}, {"a": 3}]}))
        self.up()
        _, body = self.get("/t/3")
        runs = self.section(body, "runs")
        self.assertIn("worker 沒交結構化輸出", runs, "沒有那個檔的那一趟要說這句")
        self.assertIn("result-round4.json", runs)
        self.assertIn("mutations 3 筆", runs)
        self.assertIn("gate.ran 是", runs)


class TicketPage(BoardReads):
    """#19 驗收 ④:`/t/<票號>` 一頁,含 404 與路徑跳脫。"""

    def test_4_shows_objection_review_verify_and_404s_safely(self):
        self.make_ticket(4, state_version=1, subject="驗證者19-票頁",
                         objections=[{"category": "ticket-wrong", "owner": "verifier",
                                      "body": "verify19-objection-prefix-一二三",
                                      "disposition": "", "blocking": True}],
                         review={"verdict": "verify19-verdict-通過標記", "by": "main",
                                 "sha": "deadbee", "state_version": 1},
                         verify={"files": ["verify/board/test_ticket_19.py"],
                                 "tags": ["board-reads"],
                                 "run": "python3 scripts/verify.py --tag board-reads-19marker",
                                 "baseline": "verify19-baseline-note"})
        self.write("outside.json", json.dumps(
            {"id": "outside", "secret": "verify19-secret-should-not-leak"}))
        self.up()
        status, body = self.get("/t/4")
        self.assertEqual(status, 200)
        self.assertIn("verify19-objection-prefix", body)
        self.assertIn("verify19-verdict-通過標記", body)
        self.assertIn("python3 scripts/verify.py --tag board-reads-19marker", body)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/t/9999")
        self.assertEqual(caught.exception.code, 404)
        self.assertIn("找不到", caught.exception.read().decode("utf-8"))
        caught.exception.close()

        for raw in ("/t/..%2f..%2fetc", "/t/..%2foutside", "/t/..", "/t/a%2fb"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(raw)
            self.assertEqual(caught.exception.code, 404, raw)
            leaked = caught.exception.read().decode("utf-8")
            self.assertNotIn("verify19-secret-should-not-leak", leaked, raw)
            caught.exception.close()

    def test_5_stale_review_sentence_only_when_versions_differ(self):
        self.make_ticket(5, state_version=3,
                         review={"verdict": "pass", "by": "main", "sha": "abc",
                                 "state_version": 2})
        self.make_ticket(6, state_version=3,
                         review={"verdict": "pass", "by": "main", "sha": "abc",
                                 "state_version": 3})
        self.up()
        self.assertIn("這份覆核已過期", self.get("/t/5")[1])
        self.assertNotIn("這份覆核已過期", self.get("/t/6")[1])


class ThreeKindsOfNothing(BoardReads):

    def test_6_the_three_kinds_of_nothing_are_three_different_sentences(self):
        """目錄不在 / 目錄在但沒有結果 / 跑完而且是綠的 —— 三件事的下一步完全不同。

        **變異(見 EVIDENCE)**:把「目錄在但 0 個 status.json」那一支改成回「沒有
        跑過」那一句 → 這一條該紅。
        """
        self.make_ticket(7)
        self.make_ticket(8)
        self.make_ticket(9)
        os.makedirs(os.path.join(self.repo, "reports", "t8", "20260922-100000-1"))
        self.status(9, "20260922-100000-1", rc=0, failures=[])
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        says = {}
        for ident in ("7", "8", "9"):
            row = runs.split('data-ticket="%s"' % ident)[1].split("</tr>")[0]
            says[ident] = VERDICT.search(row).group(1)
        self.assertIn("沒有跑過", says["7"])
        self.assertIn("有目錄、沒有結果", says["8"])
        self.assertIn("綠", says["9"])
        self.assertNotEqual(says["7"], says["8"], "兩種『沒有』用了同一句話")
        self.assertNotEqual(says["8"], says["9"], "兩種『沒有』用了同一句話")
        self.assertNotEqual(says["7"], says["9"], "兩種『沒有』用了同一句話")


class BrokenDiskFilesNeverCauseA500(BoardReads):

    def test_7_broken_files_stay_200_and_say_unreadable(self):
        self.make_ticket(10)
        self.status(10, "20260920-090000-1")
        self.write(os.path.join("reports", "t10", "20260921-090000-2",
                                "status.json"), "{")
        self.write(os.path.join("reports", "inbox", "index.jsonl"),
                   json.dumps({"name": "v19-broken-inbox", "ticket": "10",
                              "state": "閘門紅", "what": "verify19-broken-item"},
                             ensure_ascii=False) + "\nnot-json-verify19\n")
        self.assertFalse(self.exists("reports/inbox/acked.jsonl"))
        self.up()
        for path in ("/", "/t/10", "/api/state"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("讀不動", body, path)


class NoOutboundHostAndNoProjectLockIn(unittest.TestCase):
    """#19 驗收 ⑧:純 stdlib、不連外、不引 CDN —— 不用起伺服器,讀原始碼就夠。"""

    def test_9_board_py_has_no_external_host_and_no_cdn(self):
        with open(BOARD_PY, encoding="utf-8") as handle:
            source = handle.read()
        for line in source.splitlines():
            if "http://" in line or "https://" in line:
                self.assertIn("127.0.0.1", line, "外連:" + line.strip())
        self.assertNotIn("cdn", source.lower())


if __name__ == "__main__":
    unittest.main()
