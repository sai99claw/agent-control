"""`scripts/sync-to-project.sh`:主 → 專案的單向同步。

2026-09-21 外部審查:同步只覆寫現有卡、不刪已退役的,所以專案可能**留著一張已經
收掉的角色卡**,而留下來的那一份讀起來與還在用的一模一樣;最後一行又要人去跑本 repo
沒有提供的 `land-ticket.sh` —— 一句指不到東西的下一步,比沒有下一步更糟。
"""

import os, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sync(dest, *extra):
    return subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), dest, *extra],
                          capture_output=True, text=True, timeout=120)


class SyncToProject(unittest.TestCase):
    def test_copies_role_and_model_cards_with_a_provenance_header(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            roles = os.listdir(os.path.join(d, "docs/roles"))
            self.assertIn("verifier.md", roles)
            self.assertNotIn("dispatcher.md", roles,
                             "調度員這個角色 2026-09-21 已退場(D-010)")
            self.assertIn("model", roles)
            with open(os.path.join(d, "docs/roles/verifier.md")) as f:
                first = f.readline()
            self.assertIn("請到 agent-control 改", first)

    def test_a_card_that_left_agent_control_is_removed_from_the_project(self):
        """**變異**:把 manifest 那一段拿掉 → 這一條紅。"""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sync(d).returncode, 0)
            stale = os.path.join(d, "docs/roles/dispatcher.md")
            with open(stale, "w") as f:
                f.write("上一次同步留下來的\n")
            with open(os.path.join(d, "docs/roles/.sync-manifest"), "a") as f:
                f.write("dispatcher.md\n")
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("退場", r.stdout, "靜悄悄的刪除與沒發生的刪除長得一樣")
            self.assertFalse(os.path.exists(stale))

    def test_it_names_an_entry_point_that_really_exists_there(self):
        with tempfile.TemporaryDirectory() as d:
            r = sync(d)
            self.assertIn("還沒有 docs 通道", r.stdout,
                          "空專案不該被叫去跑一個不存在的入口")
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "scripts/land.sh"), "w") as f:
                f.write("#!/bin/sh\n")
            self.assertIn("scripts/land.sh", sync(d).stdout)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("(dry-run)", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, "docs/roles/verifier.md")))

    def test_refuses_a_missing_project_dir(self):
        r = subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), "/nonexistent/x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

if __name__ == "__main__":
    unittest.main()
