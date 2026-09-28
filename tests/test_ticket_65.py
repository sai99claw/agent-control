"""#65:verify / close 的 glob 掃到主線上的二進位檔(PNG)就整張「讀不到」。

needs_verifier=false,這一支就是驗收。期望字樣來自票面 acceptance 原文。

**變異**(實作者自證紅):
- M1 換回修前的 ticket.py(`AC_TEST_SCRIPTS` 指 base 的 scripts)→ B1、B2、B3、B4 紅
  (rc 2、stderr 含 "can't decode byte 0x89")。
- M2 `except UnicodeDecodeError` 裡改成 `body = done.stdout.decode("utf-8", "replace")`
  (不跳過、亂碼替換)→ B3、B4 紅(PNG 裡的 needle 被算成命中)。
- M3 拿掉 `looked.append("%s(非文字檔,沒掃)" % path)` → B4 紅(靜默)。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

# PNG 檔頭 + 一段不是 UTF-8 的位元組;B3 把 needle 埋在這一段裡
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xc3\x28"


class BinaryOnMain(Sandbox):

    def land(self, png_tail=b"", app="const tag = 'NEEDLE-65';\n"):
        path = os.path.join(self.repo, "web", "icon.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(PNG + png_tail)
        if app is not None:
            self.write("web/app.js", app)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "web: icon + app")

    def verify(self, *needles):
        self.make_ticket(1, allowed_write_paths=["web/**"],
                         verify_strings=list(needles))
        return self.ticket("verify", "1")

    def test_b1_glob_over_a_png_still_counts_the_text_hit(self):
        """B1:修前 rc 2 + "can't decode";修後 rc 0、命中 web/app.js。"""
        self.land()
        done = self.verify("NEEDLE-65")
        self.assertNotIn("can't decode", done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("web/app.js×1", done.stdout)
        self.assertIn("在主線上", done.stdout)

    def test_b2_close_landed_closes_and_a_second_close_does_not_error(self):
        """B2:InReview + 有效 review → close --landed <merge sha> rc 0、Done;
        同一條再跑一次(review.sha 已被第一次蓋過)不報錯。"""
        path = self.worktree("t1-web")
        os.makedirs(os.path.join(path, "web"))
        with open(os.path.join(path, "web", "icon.png"), "wb") as handle:
            handle.write(PNG)
        with open(os.path.join(path, "web", "app.js"), "w", encoding="utf-8") as handle:
            handle.write("const tag = 'NEEDLE-65';\n")
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "web: icon + app", cwd=path)
        self.git("merge", "-q", "--no-ff", "-m", "land t1-web", "t1-web")
        merge = self.git("rev-parse", "main").strip()
        self.make_ticket(1, state="InReview", allowed_write_paths=["web/**"],
                         verify_strings=["NEEDLE-65"], needs_verifier=False)
        self.approve(1, branch="t1-web")
        for attempt in (1, 2):
            done = self.ticket("close", "1", "--landed", merge)
            self.assertNotIn("can't decode", done.stderr, attempt)
            self.assertEqual(done.returncode, 0, "%d: %s" % (attempt, done.stdout + done.stderr))
            row = self.load_ticket("1")
            self.assertEqual(row["state"], "Done", attempt)
            self.assertEqual(row["review"]["sha"], merge, attempt)

    def test_b3_a_needle_only_inside_the_png_is_not_a_hit(self):
        """B3:needle 只在 PNG 的位元組裡 → 命中 0、verify 不過,照既有「抓不到」。"""
        self.land(png_tail=b"NEEDLE-65\xff", app="nothing here\n")
        done = self.verify("NEEDLE-65")
        self.assertNotIn("can't decode", done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("零 NEEDLE-65 —— 0(哪裡都沒有)", done.stdout)
        self.assertIn("**不在主線上**", done.stdout)

    def test_b4_a_path_needle_at_the_png_says_it_is_not_text(self):
        """B4:`web/icon.png:NEEDLE-65` → 不倒、命中 0、where 含「非文字」。"""
        self.land(png_tail=b"NEEDLE-65\xff")
        done = self.verify("web/icon.png:NEEDLE-65")
        self.assertNotIn("can't decode", done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = [text for text in done.stdout.splitlines() if "NEEDLE-65" in text]
        self.assertEqual(len(line), 1, done.stdout)
        self.assertIn("—— 0(", line[0])
        self.assertIn("web/icon.png", line[0])
        self.assertIn("非文字", line[0])


if __name__ == "__main__":
    unittest.main()
