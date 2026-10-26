"""#179 A3:`memory.py consolidate` 搬走的 inbox 備份(`memory/**/<inbox>.<run_id>.consumed`)
被 git 忽略;inbox 本身仍要被追蹤。

量的是本 repo 的 `.gitignore`(`git check-ignore --no-index`,從這棵樹的根跑);
期望值抄自票面,不是從 `.gitignore` 解析出來的。

**變異**:刪掉 `.gitignore` 的 `memory/**/*.consumed` 那一行 → 前兩條紅。
"""

import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_ignore(path):
    return subprocess.run(["git", "check-ignore", "--no-index", "-q", path],
                          cwd=ROOT, capture_output=True, text=True)


class ConsumedBackupsAreIgnored(unittest.TestCase):

    def test_role_layer_backup_is_ignored(self):
        done = check_ignore("memory/role/x.inbox.md.20261026-000000-1.consumed")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_model_layer_backup_is_ignored(self):
        done = check_ignore("memory/model/y.inbox.md.20261026-000000-2.consumed")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_inbox_itself_is_not_ignored(self):
        """rc 1 = 沒被忽略;rc 128(git 出錯)不能當成「沒被忽略」。"""
        done = check_ignore("memory/role/x.inbox.md")
        self.assertEqual(done.returncode, 1, done.stderr)


if __name__ == "__main__":
    unittest.main()
