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
from control_harness import Sandbox  # noqa: E402

PORT_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/")


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

    def test_the_five_sections_are_all_there(self):
        self.make_ticket(1, state="Ready", subject="第一張票")
        self.up()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        for anchor in ("id=\"tickets\"", "id=\"timeline\"", "id=\"land\"",
                       "id=\"inbox\"", "id=\"rehearsal\""):
            self.assertIn(anchor, body, "少了一段")
        self.assertIn("第一張票", body)

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

    def test_the_rehearsal_table_is_read_from_the_file(self):
        self.write("docs/REHEARSAL.md",
                   "# 演練\n\n| 工具 | 第一次真跑 | 要看到什麼 |\n|---|---|---|\n"
                   "| `land.sh` 的過期拒絕 | 下一次 base 過期 | EXIT=2 而且沒建 worktree |\n")
        self.up()
        _, body = self.get("/")
        section = body.split("id=\"rehearsal\"")[1].split("</section>")[0]
        self.assertIn("下一次 base 過期", section)

    def test_an_empty_rehearsal_table_says_so_instead_of_being_blank(self):
        self.up()
        _, body = self.get("/")
        section = body.split("id=\"rehearsal\"")[1].split("</section>")[0]
        self.assertIn("沒有欠確認的真跑", section)

    def test_the_state_api_answers_json(self):
        self.make_ticket(1)
        self.up()
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["tokens"], "未知")
        self.assertEqual(data["tickets"][0]["id"], "1")

    def test_the_ticket_table_draws_the_metrics_without_touching_the_token_header(self):
        """**變異**:把 `state()` 的 `metrics` 那一格拿掉 → 這一條紅。

        抬頭那一句 `token 用量:未知` 問的是「這個看板知不知道整體用量」(不知道);
        票表那一欄答的是「這一張票各自量到了什麼」。**兩件事不是同一格** —— 混成
        一格的話,任何一張票量到了數字,抬頭就會變成一句它答不出來的話。
        """
        self.make_ticket(1, state="Ready", subject="有數字的那一張")
        self.write(os.path.join("reports", "t1", "r1", "status.json"),
                   json.dumps({"state": "done", "run_id": "r1", "kind": "gate",
                               "ticket": "1", "rc": 1, "round": 2,
                               "environment_suspect": {"engine": "safari"},
                               "duration_seconds": 12}, ensure_ascii=False))
        self.up()
        _, body = self.get("/")
        section = body.split("id=\"tickets\"")[1].split("</section>")[0]
        for header in ("返工輪", "紅(env/product)", "token"):
            self.assertIn(header, section, "票表少了 %s 這一欄" % header)
        self.assertIn("1 (1/0)", section, "紅的分類沒有畫出來")
        self.assertIn("token 用量:未知", body, "抬頭那一格不准被票表的數字改掉")

    def test_the_state_api_carries_the_metrics_next_to_the_unknown_token_total(self):
        self.make_ticket(1)
        self.up()
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["tokens"], "未知")
        self.assertIn("metrics", data, "/api/state 少了 metrics 這個頂層鍵")
        self.assertEqual(data["metrics"]["1"]["tokens"], "未知",
                         "一份 log 都沒有的票,token 是未知,不是 0")

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

    def test_the_saved_answer_comes_back_in_the_box(self):
        self.make_ticket(1, state="NeedsDecision")
        self.up()
        self.post("/api/answer", {"ticket": "1", "answer": "先照舊"})
        _, body = self.get("/")
        self.assertIn("先照舊", body)

    def test_a_body_without_the_two_fields_is_refused(self):
        self.make_ticket(1, state="NeedsDecision")
        self.up()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/answer", {"answer": "沒說是哪一張票"})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertFalse(self.exists("board/answers.jsonl"))

    def test_two_answers_both_stay_in_the_file_and_the_last_one_shows(self):
        """那份檔是「使用者說過什麼」的歷史,畫面上顯示的是最後那一句。"""
        self.make_ticket(1, state="NeedsDecision")
        self.up()
        self.post("/api/answer", {"ticket": "1", "answer": "第一次說的"})
        self.post("/api/answer", {"ticket": "1", "answer": "改成這樣"})
        lines = [line for line in self.read("board/answers.jsonl").splitlines() if line]
        self.assertEqual(len(lines), 2)
        _, body = self.get("/")
        self.assertIn("改成這樣", body)
        self.assertNotIn("第一次說的", body)


if __name__ == "__main__":
    unittest.main()
