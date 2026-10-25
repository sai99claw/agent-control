"""#165 acceptance cases written by the verifier (D-071 C9, V14): the optional board.key_file gate.

The edges the worker's A1-A5 in tests/test_board.py do not pin: a Cookie header carrying other
cookies around ac_board_key, a key file with surrounding spaces and CRLF, '/healthz/' versus
'/HEALTHZ', an absolute key_file path, a key containing '=' and '%', '/board.css' and
'/board.js' gated like '/', and the 401 / 302 header shape.

Every expected key comes from the file this module writes itself; every expected code, cookie
name and header text comes from the ticket's objective (interface_fixed). The client is
http.client, so a 302 is seen as a 302 (nothing follows redirects). The board's stderr goes to a
file, not a pipe, so a chatty board can never block on a full pipe. A served board is stopped only
through its own Popen handle.
"""

import http.client
import json
import os
import re
import subprocess
import sys
import time
import unittest
from urllib.parse import parse_qsl, quote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


class KeyGateCases(Sandbox):
    """Sandbox whose board/config.json carries board.key_file; helpers live in this class."""

    KEY = "k-secret"
    SUBJECT = "gate-case subject 165"
    PORT_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/")

    # ------------------------------------------------------------ fixture

    def configure(self, key_file, key_text=None, where=None):
        """Set board.key_file; write key_text to the key file when given (None = leave it absent)."""
        conf = json.loads(self.read("board/config.json"))
        conf["board"] = {"key_file": key_file}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        if key_text is not None:
            path = where or os.path.join(self.repo, key_file)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(key_text)
        self.make_ticket(1, subject=self.SUBJECT)

    def start(self):
        """Run the board with --port 0; return the Popen handle (stderr goes to a file)."""
        self.err_path = os.path.join(self.home, "board-stderr.log")
        err = open(self.err_path, "w", encoding="utf-8")
        self.addCleanup(err.close)
        proc = subprocess.Popen(
            ["python3", self.board_py, "--port", "0"],
            cwd=self.repo, env=self.env(), stdout=subprocess.DEVNULL,
            stderr=err, text=True)
        self.addCleanup(self.halt, proc)
        return proc

    def halt(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    def stderr_text(self):
        with open(self.err_path, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def serve(self):
        proc = self.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            found = self.PORT_LINE.search(self.stderr_text())
            if found:
                self.port = int(found.group(1))
                return proc
            if proc.poll() is not None:
                self.fail("board did not start (rc %s): %s" % (proc.returncode, self.stderr_text()))
            time.sleep(0.05)
        self.fail("board printed no port line within 20 s: " + self.stderr_text())

    def refused(self):
        """Run the board expecting it to refuse to start; return (rc, stderr)."""
        proc = self.start()
        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("board kept running with an unusable key_file: " + self.stderr_text())
        return rc, self.stderr_text()

    def call(self, path, cookie=None, method="GET", body=None):
        """(status, headers as a list of (name, value), body bytes); redirects are not followed."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            headers = {}
            if cookie is not None:
                headers["Cookie"] = cookie
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            conn.close()

    def header(self, headers, name):
        """Every value of one header (case-insensitive name); [] when the header is absent."""
        return [value for key, value in headers if key.lower() == name.lower()]

    def assert_served(self, status, body):
        self.assertEqual(status, 200)
        self.assertIn(self.SUBJECT, body.decode("utf-8"),
                      "a 200 that is not the board page is not 'served as today'")

    def assert_locked(self, status, headers, body):
        self.assertEqual(status, 401)
        self.assertEqual(body, b"")
        self.assertEqual(self.header(headers, "Set-Cookie"), [])

    # ------------------------------------------------------------ cases

    def test_the_key_cookie_is_found_among_other_cookies(self):
        self.configure("board/key.txt", self.KEY + "\n")
        self.serve()
        self.assert_locked(*self.call("/"))
        status, _, body = self.call(
            "/", cookie="theme=dark; ac_board_key=%s; other=1" % self.KEY)
        self.assert_served(status, body)
        status, _, body = self.call("/", cookie="ac_board_key=%s; theme=dark" % self.KEY)
        self.assert_served(status, body)
        # A cookie whose name merely ends in ac_board_key is not the key cookie.
        self.assert_locked(*self.call("/", cookie="x_ac_board_key=%s" % self.KEY))
        self.assert_locked(*self.call("/", cookie="theme=%s; other=1" % self.KEY))

    def test_the_key_is_the_first_line_stripped_of_spaces_and_crlf(self):
        self.configure("board/key.txt", "  %s   \r\nsecond line ignored\r\n" % self.KEY)
        self.serve()
        status, _, body = self.call("/", cookie="ac_board_key=%s" % self.KEY)
        self.assert_served(status, body)
        status, headers, body = self.call("/?key=%s" % self.KEY)
        self.assertEqual(status, 302)
        self.assertEqual(self.header(headers, "Location"), ["/"])
        # The stripped spaces are not part of the key, and neither is the second line.
        self.assert_locked(*self.call("/?key=%s" % quote("  %s   " % self.KEY)))
        self.assert_locked(*self.call("/?key=" + quote("second line ignored")))
        self.assert_locked(*self.call("/", cookie="ac_board_key=second"))

    def test_healthz_with_a_trailing_slash_is_open_and_uppercase_is_gated(self):
        self.configure("board/key.txt", self.KEY + "\n")
        self.serve()
        for path in ("/healthz", "/healthz/"):
            status, _, body = self.call(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True}, path)
        self.assert_locked(*self.call("/HEALTHZ"))
        self.assert_locked(*self.call("/healthz/x"))

    def test_an_absolute_key_file_path_outside_the_project_is_used(self):
        outside = os.path.join(self.home, "elsewhere", "board.key")
        self.configure(outside, self.KEY + "\n", where=outside)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "elsewhere")))
        self.serve()
        self.assert_locked(*self.call("/"))
        status, _, body = self.call("/", cookie="ac_board_key=%s" % self.KEY)
        self.assert_served(status, body)

    def test_an_absolute_key_file_that_is_missing_refuses_to_start(self):
        outside = os.path.join(self.home, "elsewhere", "missing.key")
        self.configure(outside)
        rc, err = self.refused()
        self.assertEqual(rc, 2)
        self.assertIn("board: key_file", err)
        self.assertIn(outside, err)
        self.assertIn("missing or empty", err)
        self.assertNotRegex(err, r"http://127\.0\.0\.1:")

    def test_a_key_file_whose_first_line_is_only_spaces_refuses_to_start(self):
        self.configure("board/key.txt", "   \r\n%s\n" % self.KEY)
        rc, err = self.refused()
        self.assertEqual(rc, 2)
        self.assertIn("board/key.txt", err)
        self.assertIn("missing or empty", err)
        self.assertNotRegex(err, r"http://127\.0\.0\.1:")

    def test_a_key_with_equals_and_percent_round_trips(self):
        key = "a=b%c=="
        self.configure("board/key.txt", key + "\n")
        self.serve()
        status, headers, body = self.call("/?key=" + quote(key, safe=""))
        self.assertEqual(status, 302)
        self.assertEqual(body, b"")
        self.assertEqual(self.header(headers, "Location"), ["/"])
        cookies = self.header(headers, "Set-Cookie")
        self.assertEqual(len(cookies), 1, cookies)
        # What a browser sends back is the name=value pair before the first ';'.
        pair = cookies[0].split(";")[0].strip()
        self.assertTrue(pair.startswith("ac_board_key="), pair)
        status, _, body = self.call("/", cookie=pair)
        self.assert_served(status, body)
        status, _, body = self.call("/", cookie="other=1; ac_board_key=%s" % key)
        self.assert_served(status, body)
        # A prefix of the key (cut at '=' or at '%') is not the key.
        self.assert_locked(*self.call("/", cookie="ac_board_key=a"))
        self.assert_locked(*self.call("/?key=" + quote("a=b", safe="")))
        self.assert_locked(*self.call("/?key=" + quote("a=b%c", safe="")))

    def test_the_css_and_the_js_are_gated_like_the_page(self):
        self.configure("board/key.txt", self.KEY + "\n")
        self.serve()
        for path in ("/board.css", "/board.js"):
            self.assert_locked(*self.call(path))
            self.assert_locked(*self.call(path, cookie="ac_board_key=wrong"))
            status, _, body = self.call(path, cookie="ac_board_key=%s" % self.KEY)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)
            status, headers, body = self.call(path + "?key=" + self.KEY)
            self.assertEqual(status, 302, path)
            self.assertEqual(self.header(headers, "Location"), [path])

    def test_the_401_and_the_302_carry_exactly_the_pinned_headers(self):
        self.configure("board/key.txt", self.KEY + "\n")
        self.serve()
        status, headers, body = self.call("/api/state?key=wrong")
        self.assert_locked(status, headers, body)
        self.assertEqual(self.header(headers, "WWW-Authenticate"), [])
        status, headers, body = self.call("/api/state?a=1&key=%s&b=two" % self.KEY)
        self.assertEqual(status, 302)
        self.assertEqual(body, b"")
        location = self.header(headers, "Location")
        self.assertEqual(len(location), 1, location)
        moved = urlsplit(location[0])
        self.assertEqual(moved.path, "/api/state")
        self.assertEqual(parse_qsl(moved.query), [("a", "1"), ("b", "two")])
        cookies = self.header(headers, "Set-Cookie")
        self.assertEqual(len(cookies), 1, cookies)
        parts = [part.strip() for part in cookies[0].split(";")]
        self.assertEqual(parts[0], "ac_board_key=" + self.KEY)
        self.assertEqual(sorted(parts[1:]), ["HttpOnly", "Path=/", "SameSite=Strict"])

    def test_a_post_carrying_the_key_in_the_query_is_redirected_not_saved(self):
        self.configure("board/key.txt", self.KEY + "\n")
        self.serve()
        payload = json.dumps({"ticket": "1", "answer": "gated"}).encode("utf-8")
        status, headers, body = self.call("/api/answer", method="POST", body=payload,
                                          cookie="ac_board_key=wrong")
        self.assert_locked(status, headers, body)
        status, headers, body = self.call("/api/answer?key=" + self.KEY, method="POST",
                                          body=payload)
        self.assertEqual(status, 302)
        self.assertEqual(body, b"")
        self.assertEqual(self.header(headers, "Location"), ["/api/answer"])
        self.assertFalse(self.exists("board/answers.jsonl"))
        self.assertNotIn(self.KEY, self.stderr_text())

    def test_a_null_key_file_is_the_ungated_board(self):
        conf = json.loads(self.read("board/config.json"))
        conf["board"] = {"key_file": None}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket(1, subject=self.SUBJECT)
        self.serve()
        status, headers, body = self.call("/")
        self.assert_served(status, body)
        self.assertEqual(self.header(headers, "Set-Cookie"), [])
        self.assertEqual(self.call("/board.css")[0], 200)


if __name__ == "__main__":
    unittest.main()
