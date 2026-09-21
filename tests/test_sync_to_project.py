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


class SyncsTheScripts(unittest.TestCase):
    """遷移計畫 §0 第 3 條要專案的落地腳本「呼叫這一套」,而專案端以前**沒有東西可以
    呼叫** —— 這幾支只住在 agent-control 裡。"""

    def test_the_control_scripts_land_in_scripts_control(self):
        with tempfile.TemporaryDirectory() as d:
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = os.listdir(os.path.join(d, "scripts", "control"))
            for name in ("status.py", "verify-case.py", "apply.sh", "auto-fix.sh",
                         "inbox.py", "rules.py", "memory.py"):
                self.assertIn(name, got)
            for dependency in ("event.py", "ticket.py"):
                self.assertIn(dependency, got,
                              "少了它們,同步過去的是一組 import 就炸的檔")

    def test_the_copied_scripts_say_they_are_not_the_place_to_edit(self):
        with tempfile.TemporaryDirectory() as d:
            sync(d)
            with open(os.path.join(d, "scripts", "control", "README.md")) as handle:
                text = handle.read()
            self.assertIn("產出物", text)
            self.assertIn("board/config.json", text)

    def test_a_script_that_left_agent_control_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sync(d).returncode, 0)
            stale = os.path.join(d, "scripts", "control", "dispatcher.py")
            with open(stale, "w") as handle:
                handle.write("上一次同步留下來的\n")
            with open(os.path.join(d, "scripts", "control", ".sync-manifest"), "a") as f:
                f.write("dispatcher.py\n")
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("退場", r.stdout)
            self.assertFalse(os.path.exists(stale))

    def test_it_prints_the_lines_the_project_has_to_change(self):
        """同步只把檔搬過去,搬過去的檔**不會自己被呼叫** —— 而「同步完成」與
        「接上了」長得一樣。"""
        with tempfile.TemporaryDirectory() as d:
            out = sync(d).stdout
            self.assertIn("接點", out)
            for entry in ("status.py done", "verify-case.py check", "apply.sh",
                          "auto-fix.sh", "inbox.py list", "rules.py pack"):
                self.assertIn(entry, out)

    def test_a_project_without_a_config_is_told_to_add_one_first(self):
        """這幾支往上找 `board/config.json` 來認 repo 根;少了它,票與 reports 會寫到
        一個沒有人在看的目錄,**而且不會報錯**。"""
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("board/config.json", sync(d).stdout)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "scripts", "control",
                                                         "apply.sh")))


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
            os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
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
