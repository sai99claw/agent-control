"""`code-map/check-stale.py`:模組卡片過期了沒 — `docs/CODE-MAP.md` §2。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

CARD = """---
module: src/nav.py
purpose: 一句話
entry_points: [size_nav, paint]
invariants:
  - "只有這一支寫那個變數"
depends_on: [src/state.py]
source_paths: [src/nav.py]
verified_at_commit: %s
verified_by: opus 2026-09-12
---
正文:為什麼這樣設計。
"""


class CheckStale(Sandbox):

    def check(self, *args):
        return self.run_py("code-map/check-stale.py", *args)

    def card(self, text, name="nav.md"):
        self.write(os.path.join("code-map", "cards", name), text)

    def land(self, path, text, subject):
        self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def test_a_card_verified_at_the_current_commit_is_fresh(self):
        self.land("src/nav.py", "def size_nav():\n    pass\n", "放 nav 進去")
        self.card(CARD % self.git("rev-parse", "HEAD").strip())
        done = self.check("--all")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("還新", done.stdout)

    def test_a_card_goes_stale_once_its_source_moves(self):
        """**變異**:把 `git diff --stat` 的 `-- <paths>` 拿掉 → 這一條還是綠,但
        下一條(別的檔動了不算)會紅。
        """
        self.land("src/nav.py", "def size_nav():\n    pass\n", "放 nav 進去")
        self.card(CARD % self.git("rev-parse", "HEAD").strip())
        self.land("src/nav.py", "def size_nav():\n    return 1\n", "改 nav")
        done = self.check()
        self.assertEqual(done.returncode, 0, "過期不是錯誤,閘門不擋")
        self.assertIn("**過期**", done.stdout)
        self.assertIn("src/nav.py", done.stdout)

    def test_a_change_somewhere_else_does_not_make_the_card_stale(self):
        self.land("src/nav.py", "def size_nav():\n    pass\n", "放 nav 進去")
        self.card(CARD % self.git("rev-parse", "HEAD").strip())
        self.land("src/other.py", "x = 1\n", "改別的檔")
        done = self.check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("過期", done.stdout)

    def test_a_card_with_no_front_matter_is_broken_not_fresh(self):
        """一張讀不動的卡與一張沒過期的卡,在「過期清單是空的」那一行輸出裡長得
        一模一樣。

        **變異**:把 `parse_card` 回 None 那一格改成回 `{}` → 這一條紅。
        """
        self.card("這張卡忘了寫 front matter\n")
        done = self.check()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("壞了", done.stdout)

    def test_a_card_pointing_at_a_commit_this_repo_does_not_have_is_broken(self):
        self.card(CARD % ("0" * 40))
        done = self.check()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("這顆 repo 沒有 commit", done.stdout)

    def test_a_card_without_source_paths_is_broken(self):
        self.card("---\nmodule: src/nav.py\nverified_at_commit: %s\n---\n正文\n"
                  % self.git("rev-parse", "HEAD").strip())
        done = self.check()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("沒有 source_paths", done.stdout)

    def test_no_cards_at_all_says_so(self):
        """空清單與「都沒過期」長得一樣。"""
        done = self.check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("一張卡都沒有", done.stdout)


class TheShippedCard(unittest.TestCase):
    """本 repo 自己的那張示範卡要讀得動 —— 一張讀不動的示範卡會教出讀不動的卡。"""

    def test_the_sample_card_parses_and_names_its_source(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(here, "code-map"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "check_stale", os.path.join(here, "code-map", "check-stale.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with open(os.path.join(here, "code-map", "cards", "land-sh.md"),
                  encoding="utf-8") as handle:
            fields = module.parse_card(handle.read())
        self.assertIsNotNone(fields, "示範卡的 front matter 讀不動")
        self.assertEqual(fields["source_paths"], ["scripts/land.sh"])
        self.assertEqual(len(fields["verified_at_commit"]), 40)
        self.assertTrue(fields["invariants"], "卡上要有不變條件")


if __name__ == "__main__":
    unittest.main()
