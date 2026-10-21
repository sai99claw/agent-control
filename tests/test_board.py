"""`board/board.py`:控制台只讀票與事件,不猜。

埠一律 0(OS 給)—— **埠寫死的測試會在別人剛好佔著那個埠的那天紅**,而那種紅跟真的
壞掉長得一樣。
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import BOARD_DIR, Sandbox  # noqa: E402

PORT_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/")
# 那一格答的是「這張票的 run 怎麼了」:三種「沒有」各一句話,綠也是一句話。
VERDICT = re.compile(r"<td class=\"verdict\">(.*?)</td>")


class BoardUp(Sandbox):

    def serve(self):
        proc = subprocess.Popen(
            ["python3", os.path.join(self.repo, "board", "board.py"), "--port", "0"],
            cwd=self.repo, env=self.env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        # **只殺自己起的**:記下這一顆的 pid,不去獵行程。
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

    def get(self, path="/"):
        _, port = self.port_cache
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=20) as response:
            return response.status, response.read().decode("utf-8")

    def post(self, path, payload):
        _, port = self.port_cache
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def up(self):
        self.port_cache = self.serve()


class Pages(BoardUp):

    def test_token_usage_says_unknown_and_never_zero(self):
        """`docs/DESIGN.md` §15:**未知不可顯示為零**。一個估出來的數字與一個量出來
        的數字在畫面上長得一樣,而只有後者可以拿來做決定。

        **變異**:把 `UNKNOWN` 改成 `"0"` → 這一條紅。
        """
        self.up()
        _, body = self.get("/")
        self.assertIn("token 用量:未知", body)
        self.assertNotRegex(body, r"token 用量:\s*0")

    def test_a_stale_base_sha_is_called_out_and_an_unanswerable_one_is_not_guessed(self):
        """三種答案要分開:是 / 不是 / 問不出來。揉成一個布林值的話,「問不出來」
        會變成「是」或「不是」,而兩種都是假話。

        **變異**:把 `ancestor_cell` 的 `None` 那一格改成回「是」→ 這一條紅。
        """
        aside = self.worktree("aside")
        self.commit_in(aside, "src/aside", "岔出去的一個 commit")
        self.make_ticket(1, base_sha=self.git("rev-parse", "main").strip())
        self.make_ticket(2, base_sha=self.git("rev-parse", "aside").strip())
        self.make_ticket(3, base_sha="")
        self.up()
        _, body = self.get("/")
        self.assertIn("不是了", body)
        self.assertIn("問不出來", body)

    def test_the_agent_timeline_is_rebuilt_from_events(self):
        self.event("emit", "ticket.attempt.start", "--ticket", "7", "--attempt", "1",
                   "--role", "worker", "--model", "opus")
        self.event("emit", "ticket.attempt.start", "--ticket", "8", "--attempt", "1",
                   "--role", "worker", "--model", "opus")
        self.event("emit", "ticket.attempt.done", "--ticket", "7", "--attempt", "1")
        self.up()
        _, body = self.get("/")
        section = body.split("id=\"timeline\"")[1].split("</section>")[0]
        self.assertIn("完成", section)
        self.assertIn("在跑", section)

    def test_the_land_section_shows_the_queue_and_the_last_results(self):
        self.make_ticket(1, state="IntegrationQueued", subject="排在佇列的")
        self.event("emit", "land.refused", "--kv", "stamp=20260912-1", "--note", "0 個 commit")
        self.up()
        _, body = self.get("/")
        section = body.split("id=\"land\"")[1].split("</section>")[0]
        self.assertIn("排在佇列的", section)
        self.assertIn("land.refused", section)

    def test_the_css_and_js_are_served_from_the_same_origin(self):
        self.up()
        self.assertEqual(self.get("/board.css")[0], 200)
        self.assertEqual(self.get("/board.js")[0], 200)


class SavingAnAnswer(BoardUp):

    def test_it_writes_one_line_and_one_event_and_touches_nothing_else(self):
        """一顆會順手改狀態的儲存鈕,是一顆不敢按的儲存鈕。

        **變異**:讓 `do_POST` 順手把票改成別的狀態 → 這一條紅。
        """
        self.make_ticket(1, state="NeedsDecision", subject="要裁決的")
        self.up()
        status, data = self.post("/api/answer",
                                 {"ticket": "1", "answer": "成員退出就解除月繳"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        lines = [line for line in self.read("board/answers.jsonl").splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["answer"], "成員退出就解除月繳")
        self.assertIn("decision.answered", self.kinds())
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "NeedsDecision", "票的狀態不該被動到")
        self.assertEqual(row["state_version"], 1)

    def test_a_body_without_the_two_fields_is_refused(self):
        self.make_ticket(1, state="NeedsDecision")
        self.up()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/answer", {"answer": "沒說是哪一張票"})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertFalse(self.exists("board/answers.jsonl"))

    def test_two_answers_both_stay_in_the_file_and_the_last_one_shows(self):
        """那份檔是「使用者說過什麼」的歷史,畫面上顯示的是最後那一句。

        #72 併入原 test_the_saved_answer_comes_back_in_the_box:存下的回答回到框裡
        (= 下面 `assertIn("改成這樣", body)` 那一句)。"""
        self.make_ticket(1, state="NeedsDecision")
        self.up()
        self.post("/api/answer", {"ticket": "1", "answer": "第一次說的"})
        self.post("/api/answer", {"ticket": "1", "answer": "改成這樣"})
        lines = [line for line in self.read("board/answers.jsonl").splitlines() if line]
        self.assertEqual(len(lines), 2)
        _, body = self.get("/")
        self.assertIn("改成這樣", body)
        self.assertNotIn("第一次說的", body)


class ReadsWhatIsAlreadyOnDisk(BoardUp):
    """看板直接讀磁碟上已經結構化的那幾份:run 的 `status.json`、收件匣的頁、票自己的
    `objections` / `review` / `verify`。**一行都不必有人抄進來** —— 抄進來的那一份
    沒有人可以覆核(D-017 ②)。
    """

    def status(self, ident, run_id, **fields):
        row = {"state": "done", "run_id": run_id, "kind": "gate",
               "ticket": str(ident), "rc": 0,
               "started": "2026-09-22T10:00:00+08:00",
               "finished": "2026-09-22T10:02:00+08:00",
               "duration_seconds": 120, "failures": []}
        row.update(fields)
        self.write(os.path.join("reports", "t%s" % ident, run_id, "status.json"),
                   json.dumps(row, ensure_ascii=False))
        return row

    def inbox_page(self, name, acked=False, **meta):
        """收件匣一頁(#143:沒有索引檔,頁的第一行就是 `list` 讀的那份 JSON)。"""
        where = os.path.join("reports", "inbox", *(["acked"] if acked else []))
        self.write(os.path.join(where, name + ".md"),
                   "<!-- inbox %s -->\n# #%s —— %s\n"
                   % (json.dumps(meta, ensure_ascii=False), meta.get("ticket", ""),
                      meta.get("state", "")))

    def section(self, body, anchor):
        return body.split("id=\"%s\"" % anchor)[1].split("</section>")[0]

    def test_the_runs_and_the_inbox_come_from_the_files_themselves(self):
        """期望值是自己寫進去的那幾個字 —— 不是程式現在印什麼。"""
        self.make_ticket(1, subject="要接手的那一張")
        self.status(1, "20260922-100000-1")
        self.status(1, "20260922-110000-2", rc=1, failures=[
            {"case": "tests.test_x.Case.test_y",
             "excerpt": "AssertionError: 少了一段\n第二行不該上畫面"}])
        self.inbox_page("1-a", ticket="1", state="閘門紅",
                        what="讀 patch 記 review", where="reports/t1/x/status.json")
        self.inbox_page("1-b", acked=True, ticket="1", state="收下過的",
                        what="這一則 ack 過了", where="reports/t1/y/status.json")
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        self.assertIn("20260922-110000-2", runs, "印的要是最後那一輪")
        self.assertIn("tests.test_x.Case.test_y", runs, "紅榜的 case 沒畫出來")
        self.assertIn("AssertionError: 少了一段", runs)
        self.assertNotIn("第二行不該上畫面", runs, "excerpt 只印首行")
        inbox = self.section(body, "inbox")
        self.assertIn("讀 patch 記 review", inbox)
        self.assertNotIn("這一則 ack 過了", inbox, "ack 過的那一則不該還在清單上")

    def test_the_runs_are_in_time_order(self):
        """`run_id` 開頭是時間戳,所以字典序就是時間序。"""
        self.make_ticket(1)
        self.status(1, "20260922-090000-1")
        self.status(1, "20260922-230000-2")
        self.up()
        _, body = self.get("/t/1")
        self.assertLess(body.index("20260922-090000-1"),
                        body.index("20260922-230000-2"))

    def test_a_run_that_left_no_structured_output_says_so(self):
        """**空白不是答案**:#20 落地之前每一趟都會是這一句,而那是對的。"""
        self.make_ticket(1)
        self.status(1, "20260922-100000-1")
        self.up()
        _, body = self.get("/")
        self.assertIn("worker 沒交結構化輸出", self.section(body, "runs"))

    def test_a_run_with_result_round_json_draws_the_three_cells(self):
        self.make_ticket(1)
        self.status(1, "20260922-100000-1")
        self.write(os.path.join("reports", "t1", "20260922-100000-1",
                                "result-round2.json"),
                   json.dumps({"rc": 1, "gate": {"ran": True},
                               "mutations": [{"n": 1}, {"n": 2}]}))
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        self.assertIn("mutations 2 筆", runs)
        self.assertIn("gate.ran 是", runs)
        self.assertIn("rc 1", runs)
        self.assertNotIn("worker 沒交結構化輸出", runs)

    def test_a_red_run_with_an_empty_failure_list_still_reads_as_red(self):
        """land 的拒收就長這樣:`rc=4`、`state=done`、一條測試都沒倒。那一格退回去
        印 `state` 的話,一次拒收會在畫面上寫著 `done`。"""
        self.make_ticket(1)
        self.status(1, "20260922-100000-1", kind="land", rc=4, failures=[])
        self.up()
        _, body = self.get("/")
        row = self.section(body, "runs").split("data-ticket=\"1\"")[1].split("</tr>")[0]
        says = VERDICT.search(row).group(1)
        self.assertIn("rc=4", says)
        self.assertNotIn("綠", says)

    def test_the_ticket_page_shows_the_objection_the_review_and_the_verify(self):
        self.make_ticket(7, state_version=3, subject="一張有反駁的票",
                         objections=[{"category": "ticket-wrong", "owner": "worker",
                                      "body": "第三條驗收做不到,那個檔不存在",
                                      "disposition": "", "blocking": True}],
                         review={"verdict": "pass", "by": "main", "sha": "abc1234",
                                 "state_version": 3},
                         verify={"files": ["verify/board/test_reads.py"],
                                 "tags": ["board-reads"],
                                 "run": "python3 scripts/verify.py --tag board-reads",
                                 "baseline": "乾淨主線上真的紅過"})
        self.up()
        status, body = self.get("/t/7")
        self.assertEqual(status, 200)
        self.assertIn("第三條驗收做不到", body)
        self.assertIn("abc1234", body)
        self.assertIn("python3 scripts/verify.py --tag board-reads", body)
        self.assertIn("board-reads", body)

    def test_a_ticket_that_is_not_there_is_a_404_with_the_same_words_as_before(self):
        """**一張不存在的票不是一個 500** —— 500 會讓人去查一個沒有壞掉的東西。"""
        self.make_ticket(1)
        self.up()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/t/9999")
        self.assertEqual(caught.exception.code, 404)
        self.assertIn("找不到", caught.exception.read().decode("utf-8"))
        caught.exception.close()

    def test_a_ticket_number_that_climbs_out_of_the_tickets_dir_is_a_404(self):
        self.write("outside.json", json.dumps({"id": "outside",
                                               "subject": "不該被讀到"}))
        self.up()
        for raw in ("/t/..%2f..%2fetc", "/t/..%2foutside", "/t/..", "/t/a%2fb"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(raw)
            self.assertEqual(caught.exception.code, 404, raw)
            self.assertNotIn("不該被讀到",
                             caught.exception.read().decode("utf-8"), raw)
            caught.exception.close()

    def test_a_review_is_not_called_stale_and_a_simple_ticket_needs_none(self):
        """#136 刪了覆核綁定:verdict 不綁票版本,看板不再說「覆核已過期」—— 舊票上留著的
        state_version 對不上也一樣(land / close 不看它)。沒有 review 的頁分得出 complex
        (land 會拒)與 simple(不做做完後的覆核)。

        **變異**:看板照舊比 review.state_version → 票 1 那一頁紅。"""
        self.make_ticket(1, state_version=4,
                         review={"verdict": "pass", "by": "main", "sha": "abc",
                                 "state_version": 3})
        self.make_ticket(2, state_version=4, complexity="complex")
        self.make_ticket(3, state_version=4)
        self.up()
        page = self.get("/t/1")[1]
        self.assertNotIn("過期", page)
        self.assertIn("abc", page)
        self.assertIn("complex 票,land 會拒絕", self.get("/t/2")[1])
        self.assertIn("simple 票", self.get("/t/3")[1])
        self.assertNotIn("land 會拒絕", self.get("/t/3")[1])

    def test_the_three_kinds_of_nothing_do_not_share_one_sentence(self):
        """目錄不在 / 目錄在但沒有結果 / 跑完而且是綠的 —— 三件事的下一步完全不同。

        **變異**:把「目錄在但 0 個 status.json」那一支改成回「沒有跑過」→ 這一條紅。
        """
        self.make_ticket(1)
        self.make_ticket(2)
        self.make_ticket(3)
        os.makedirs(os.path.join(self.repo, "reports", "t2", "20260922-100000-1"))
        self.status(3, "20260922-100000-1", rc=0, failures=[])
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        says = {}
        for ident in ("1", "2", "3"):
            row = runs.split("data-ticket=\"%s\"" % ident)[1].split("</tr>")[0]
            says[ident] = VERDICT.search(row).group(1)
        self.assertIn("沒有跑過", says["1"])
        self.assertIn("有目錄、沒有結果", says["2"])
        self.assertIn("綠", says["3"])
        self.assertNotEqual(says["1"], says["2"], "兩種『沒有』用了同一句話")
        self.assertNotEqual(says["2"], says["3"], "兩種『沒有』用了同一句話")
        self.assertNotEqual(says["1"], says["3"], "兩種『沒有』用了同一句話")

    def test_an_environment_suspect_run_says_so_without_hiding_the_red_count(self):
        """驗收 11(D-019):那一格存在就是為了把讀的人**導去修環境**。

        一張只寫著「紅 3 條」的看板把人導去 auto-fix,而那一輪的機器跑不動;反過來,
        只寫「環境可疑」會把三條真的紅藏起來 —— **兩句都要印**。

        **變異**:`verdict_cell` 不看這一格 → 這一條紅(沒有「環境可疑」);
        把「紅 N 條」那一支換成環境那一句 → 這一條紅(少了「紅 3 條」)。
        """
        self.make_ticket(1)
        self.status(1, "20260923-100000-1", rc=1, failures=[
            {"case": "tests.test_a.C.test_a", "excerpt": "AssertionError: 一"},
            {"case": "tests.test_b.C.test_b", "excerpt": "AssertionError: 二"},
            {"case": "tests.test_c.C.test_c", "excerpt": "AssertionError: 三"}],
            environment_suspect=[
                {"source": "declared", "engine": "firefox",
                 "why": "session 斷了(#645)", "count": 1, "threshold": None,
                 "log": "gate.log",
                 "line": "ENVIRONMENT-SUSPECT: firefox session 斷了(#645)"}])
        self.up()
        _, body = self.get("/")
        runs = self.section(body, "runs")
        row = runs.split("data-ticket=\"1\"")[1].split("</tr>")[0]
        says = VERDICT.search(row).group(1)
        self.assertIn("環境可疑 1 筆", says, says)
        self.assertIn("firefox", says, says)
        self.assertIn("紅 3 條", says, "三條真的紅不准被環境那一句蓋掉:%s" % says)

    def test_an_old_dict_shaped_cell_does_not_take_the_board_down(self):
        """舊檔不改寫(D-014),所以看板一定會讀到 #7 那一版的單筆 dict。

        **變異**:`one_run()` 直接下標 `data["environment_suspect"][0]`
        → 這一條紅(那一頁變成一句壞掉的檔,或整頁 500)。
        """
        self.make_ticket(1)
        self.status(1, "20260923-100000-1", rc=1, failures=[],
                    environment_suspect={"engine": "safari", "count": 8,
                                         "message_shape": "storage empty"})
        self.up()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        runs = self.section(body, "runs")
        row = runs.split("data-ticket=\"1\"")[1].split("</tr>")[0]
        says = VERDICT.search(row).group(1)
        self.assertIn("環境可疑 1 筆", says, says)
        self.assertIn("safari", says, says)

    def test_broken_files_never_turn_into_a_500(self):
        """看板是拿來查「哪裡壞了」的。它自己先倒下去的那一刻,壞掉的那份檔就從
        看得見變成看不見。"""
        self.make_ticket(1)
        self.status(1, "20260922-100000-1")
        self.write(os.path.join("reports", "t1", "20260922-110000-2",
                                "status.json"), "{")
        self.inbox_page("1-a", ticket="1", state="閘門紅", what="看一下")
        self.write(os.path.join("reports", "inbox", "1-b.md"), "<!-- inbox {壞掉 -->\n")
        self.up()
        for path in ("/", "/t/1", "/api/state"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn("讀不動", body, path)

    def test_the_state_api_carries_the_runs_the_boxes_and_the_inbox(self):
        self.make_ticket(1, objections=[{"category": "test_defect", "body": "案例錯了",
                                         "owner": "worker", "blocking": True,
                                         "disposition": ""}])
        self.status(1, "20260922-100000-1", rc=1)
        self.inbox_page("1-a", ticket="1", state="閘門紅", what="看一下")
        self.up()
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["ticket_runs"]["1"]["runs"][0]["rc"], 1)
        self.assertEqual(data["boxes"]["1"]["objections"][0]["category"], "test_defect")
        self.assertTrue(data["inbox"]["entries"], "收件匣 entries 是空的")
        self.assertEqual(data["inbox"]["entries"][0]["name"], "1-a")
        self.assertEqual(data["tickets"][0]["id"], "1")

    def test_an_open_blocking_objection_comes_first(self):
        """沒被收進票的反駁與沒有反駁長得一樣 —— 排在最後一頁的阻擋項也是。"""
        self.make_ticket(1, objections=[
            {"category": "ticket-wrong", "owner": "worker", "blocking": False,
             "disposition": "accepted", "body": "已經處置過的那一筆"},
            {"category": "test_defect", "owner": "worker", "blocking": True,
             "disposition": "", "body": "還擋著的那一筆"}])
        self.up()
        _, body = self.get("/")
        section = self.section(body, "objections")
        self.assertLess(section.index("還擋著的那一筆"),
                        section.index("已經處置過的那一筆"))


class TheBoardStaysOnThisMachine(unittest.TestCase):
    """**純 stdlib、伺服器端字串組 HTML**:沒有外連、沒有 CDN。一頁靠外面的資源才
    長得出來的看板,在斷線的那一天與壞掉長得一樣。"""

    def test_no_outbound_host_and_no_cdn(self):
        with open(os.path.join(BOARD_DIR, "board.py"), encoding="utf-8") as handle:
            source = handle.read()
        for line in source.splitlines():
            if "http://" in line or "https://" in line:
                self.assertIn("127.0.0.1", line, "外連:" + line.strip())
        self.assertNotIn("cdn", source.lower())


class TheFourVisionMetricsComeFromTicketJson(BoardUp):
    """#143(D-054 Q5):看板第 ① 段只讀票 JSON 與收件匣目錄,畫 VISION 四個指標 ——
    每張落地票的 token 與時間、一次就過測試的比例、每張票需要主線介入幾次、等主線處理
    的事積了多少。**拿不到的顯示「未知」,不顯示 0**(VISION「看板與統計」)。

    夾具三張票,數字手算(不從 board.py 算):
    A(1)Done,`cost_total`:worker 一輪,in 100 + out 50 + cache_write 10 = 160 token,
         wall 300 秒;
    B(2)Done,只有 `cost[]`:worker round 1 與 round 2 兩列;
    C(3)Blocked、owner=main、沒有 cost、沒有 `interventions` 這一格(舊票)。
    收件匣:未 ack 一頁、ack 過一頁 → 等主線處理 = C 的 1 張 + 1 頁 = 2。
    一次就過 = Done 且有 worker round 的 A、B 中 round 最大值為 1 的 → 1/2。

    **變異**:`board.py` 把拿不到的格子(`shown` 的 None 那一支)畫成 0 → 「未知」那幾條紅。
    """

    UNKNOWN = "未知"

    def cost_row(self, round_, tokens_in, tokens_out, cache_write, cache_read, wall):
        return {"role": "worker", "round": round_, "model": "opus",
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cache_write": cache_write, "cache_read": cache_read,
                "wall_seconds": wall, "outcome": "ok", "run_id": "r%d" % round_,
                "session_id": None, "by": "auto-fix", "note": "", "effort": None,
                "at": "2026-10-19T10:00:00+08:00"}

    def three_tickets(self):
        self.make_ticket(1, state="Done", owner="worker", cost_total={
            "calendar_seconds": 900, "wall_seconds": 300, "runs": 1,
            "worker_rounds": 1, "objections": 0,
            "by_role": {"worker": {"runs": 1, "wall_seconds": 300, "tokens_in": 100,
                                   "tokens_out": 50, "cache_write": 10,
                                   "cache_read": 1000, "unknown_token_runs": 0,
                                   "outcomes": {"ok": 1}}},
            "at": "2026-10-19T10:15:00+08:00"})
        self.make_ticket(2, state="Done", owner="worker", cost=[
            self.cost_row(1, 20, 5, 0, 7, 40), self.cost_row(2, 30, 10, 5, 9, 60)])
        self.make_ticket(3, state="Blocked", owner="main")
        meta = {"ticket": "3", "run_id": "r1", "kind": "gate", "state": "閘門紅",
                "what": "看一下", "where": "reports/t3/r1/status.json",
                "at": "2026-10-19T10:00:00+08:00"}
        self.write(os.path.join("reports", "inbox", "3-r1.md"),
                   "<!-- inbox %s -->\n# #3 —— 閘門紅\n" % json.dumps(meta, ensure_ascii=False))
        meta = dict(meta, run_id="r0", what="收過了")
        self.write(os.path.join("reports", "inbox", "acked", "3-r0.md"),
                   "<!-- inbox %s -->\n# #3 —— 收過了\n" % json.dumps(meta, ensure_ascii=False))

    def vision(self):
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("vision", data, "/api/state 沒有四個指標")
        return data["vision"]

    def row_cells(self, body, ident):
        """票表裡那一張的格子,以表頭的名字取 —— 不靠欄位順序。"""
        section = body.split("id=\"tickets\"")[1].split("</section>")[0]
        heads = re.findall(r"<th>(.*?)</th>", section)
        found = re.search(r"<tr><td class=\"id\">#%s</td>(.*?)</tr>" % ident, section)
        self.assertIsNotNone(found, "票表沒有 #%s 那一列" % ident)
        cells = ["#%s" % ident] + re.findall(r"<td[^>]*>(.*?)</td>", found.group(1))
        for name in ("token", "wall 秒", "介入"):
            self.assertIn(name, heads, "票表沒有「%s」這一欄" % name)
        return {name: re.sub(r"<[^>]+>", "", cells[heads.index(name)]) for name in heads}

    def metric(self, body, n):
        found = re.search(r"data-metric=\"%d\"><b>(.*?)</b>" % n, body)
        self.assertIsNotNone(found, "看板第 ① 段沒有第 %d 個指標" % n)
        # 只取打頭的那個數(或「未知」),後面的說明字不釘。
        return re.match(r"[^\s(]*", found.group(1)).group(0)

    def test_the_state_api_carries_the_four_numbers_and_unknown_is_not_zero(self):
        self.three_tickets()
        self.up()
        numbers = self.vision()
        a, c = numbers["tickets"]["1"], numbers["tickets"]["3"]
        self.assertEqual(a["tokens"], 160)
        self.assertEqual(a["wall_seconds"], 300)
        self.assertEqual(numbers["tickets"]["2"]["tokens"], 25 + 45)
        self.assertEqual(numbers["tickets"]["2"]["wall_seconds"], 100)
        for key in ("tokens", "wall_seconds", "interventions"):
            self.assertEqual(c[key], self.UNKNOWN, "C 的 %s 拿不到,要是未知" % key)
        self.assertEqual(numbers["first_pass"]["passed"], 1)
        self.assertEqual(numbers["first_pass"]["counted"], 2)
        self.assertEqual(numbers["interventions"]["total"], self.UNKNOWN,
                         "沒有一張票記過介入,合計是未知不是 0")
        self.assertEqual(numbers["waiting"]["tickets"], 1)
        self.assertEqual(numbers["waiting"]["inbox_pages"], 1, "ack 過的那頁不算")
        self.assertEqual(numbers["waiting"]["total"], 2)

    def test_the_page_draws_the_same_numbers(self):
        self.three_tickets()
        self.up()
        _, body = self.get("/")
        a, c = self.row_cells(body, 1), self.row_cells(body, 3)
        self.assertEqual((a["token"], a["wall 秒"]), ("160", "300"))
        self.assertEqual((c["token"], c["wall 秒"], c["介入"]),
                         (self.UNKNOWN, self.UNKNOWN, self.UNKNOWN))
        self.assertEqual(self.metric(body, 2), "1/2")
        self.assertEqual(self.metric(body, 3), self.UNKNOWN)
        self.assertEqual(self.metric(body, 4), "2")

    def test_one_hand_over_to_main_turns_unknown_into_one(self):
        """一份票 JSON 改了,看板就變 —— 不另外記帳。"""
        self.three_tickets()
        done = self.ticket("set", "3", "state", "Blocked")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.up()
        numbers = self.vision()
        self.assertEqual(numbers["tickets"]["3"]["interventions"], 1)
        self.assertEqual(numbers["tickets"]["1"]["interventions"], self.UNKNOWN)
        self.assertEqual(numbers["interventions"]["total"], 1)
        _, body = self.get("/")
        self.assertEqual(self.row_cells(body, 3)["介入"], "1")
        self.assertEqual(self.metric(body, 3), "1")

    def test_the_four_numbers_stay_when_the_event_log_is_gone(self):
        """四個指標只讀票 JSON 與收件匣目錄:events.jsonl 刪掉,數字一個都不少。"""
        self.three_tickets()
        self.event("emit", "ticket.attempt.start", "--ticket", "3", "--attempt", "1",
                   "--role", "worker", "--model", "opus")
        os.remove(os.path.join(self.repo, "board", "events.jsonl"))
        self.up()
        numbers = self.vision()
        self.assertEqual(numbers["tickets"]["1"]["tokens"], 160)
        self.assertEqual(numbers["first_pass"]["text"], "1/2")
        self.assertEqual(numbers["waiting"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
