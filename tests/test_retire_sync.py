"""#166 A1(D-071 C5 / C8 / C10):把專案端複本那一套機制退場之後,樹裡沒有任何一條路還假設它。

量的是這棵樹本身(這支檔的上一層),不是沙盒。要找的字一律拆開拼(與
`verify/hard_block/test_ticket_135.py` 同一個做法),這支檔自己才不會命中自己。

**變異 m5**:`board/config.json` 留一列對照到被刪的測試模組 → `test_no_map_row_names_the_deleted_files`
與 grep 那一條都紅(閘門也會去叫一個不存在的模組)。
"""

import json
import os
import subprocess
import unittest

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 120

SYNC_SCRIPT = "sync-to-" + "project"
GATE_EXAMPLE = "gate." + "example"
SYNC_TEST = "test_sync_" + "to_project"
DELETED = ("scripts/%s.sh" % SYNC_SCRIPT, "tests/%s.py" % SYNC_TEST,
           "scripts/%s.sh" % GATE_EXAMPLE)
NEEDLES = (SYNC_SCRIPT, "scripts/" + "control", r"\.synced" + "-from", "SCRIPT" + "_LIST",
           "DEPENDENCY" + "_ONLY", GATE_EXAMPLE.replace(".", r"\."), SYNC_TEST)
WHERE = ("scripts", "board", "templates", "README.md", "docs/reference/role-procedures.md",
         "tests", "verify", "code-map")


def grep(pattern, *where):
    """`grep -rlE`;回命中的檔(相對樹根)。rc 2(grep 自己壞了)不當成「沒命中」。"""
    done = subprocess.run(["grep", "-rlE", "--exclude-dir=__pycache__", "--", pattern, *where],
                          cwd=TREE, capture_output=True, text=True, timeout=TIMEOUT)
    if done.returncode not in (0, 1):
        raise AssertionError("grep 沒跑成(rc %d):%s" % (done.returncode, done.stderr))
    return sorted(done.stdout.splitlines())


class TheSyncMachineryIsGone(unittest.TestCase):

    def test_the_three_files_are_deleted(self):
        self.assertTrue(os.path.isfile(os.path.join(TREE, "scripts", "rules.py")),
                        "前提:量到的是一棵 A 的樹")
        left = [rel for rel in DELETED if os.path.exists(os.path.join(TREE, rel))]
        self.assertEqual(left, [])

    def test_nothing_names_them_any_more(self):
        for rel in WHERE:
            self.assertTrue(os.path.exists(os.path.join(TREE, rel)), "前提:%s 在" % rel)
        self.assertEqual(grep("|".join(NEEDLES), *WHERE), [])

    def test_rules_has_no_roles_dir_key(self):
        self.assertEqual(grep("roles" + "_dir|models" + "_dir", "scripts/rules.py"), [])

    def test_no_paste_this_hint_is_left(self):
        self.assertEqual(grep("請" + "貼|print" + "_hooks", "scripts"), [])

    def test_the_code_map_is_current(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
        env.update(AC_ROOT=TREE, AC_HOME=TREE)
        done = subprocess.run(["python3", os.path.join(TREE, "scripts", "repo-map.py"), "check"],
                              cwd=TREE, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_hard_block_paths_are_the_two_rulings(self):
        with open(os.path.join(TREE, "board", "config.json"), encoding="utf-8") as handle:
            conf = json.load(handle)
        self.assertEqual(conf["hard_block"]["paths"], ["docs/DECISIONS.md", "docs/VISION.md"])

    def test_no_map_row_names_the_deleted_files(self):
        with open(os.path.join(TREE, "board", "config.json"), encoding="utf-8") as handle:
            rows = json.load(handle)["tests"]["unittest"]["map"]
        self.assertGreater(len(rows), 10, "前提:讀到的是對照表")
        bad = [row for row in rows
               if row["match"] in DELETED or SYNC_TEST in row["ids"]]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
