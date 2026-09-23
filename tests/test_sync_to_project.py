"""`scripts/sync-to-project.sh`:主 → 專案的單向同步。

2026-09-21 外部審查:同步只覆寫現有卡、不刪已退役的,所以專案可能**留著一張已經
收掉的角色卡**,而留下來的那一份讀起來與還在用的一模一樣;最後一行又要人去跑本 repo
沒有提供的 `land-ticket.sh` —— 一句指不到東西的下一步,比沒有下一步更糟。
"""

import hashlib, json, os, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sync(dest, *extra):
    return subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), dest, *extra],
                          capture_output=True, text=True, timeout=120)


def write_config(dest):
    os.makedirs(os.path.join(dest, "board"), exist_ok=True)
    with open(os.path.join(dest, "board", "config.json"), "w") as handle:
        handle.write('{"rules":{"roles_dir":"docs/roles","models_dir":"docs/roles/model"},'
                     '"memory":{"applies_to":["memory/model/*.md"]}}')


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
            write_config(d)
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
            write_config(d)
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("(dry-run)", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, "docs/roles/verifier.md")))

    def test_dry_run_refuses_a_project_missing_the_three_config_keys(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "board"))
            with open(os.path.join(d, "board", "config.json"), "w") as handle:
                handle.write('{"rules": {}, "memory": {}}')
            r = sync(d, "--dry-run")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            for key in ("rules.roles_dir", "rules.models_dir", "memory.applies_to"):
                self.assertIn(key, r.stderr)

    def test_refuses_a_missing_project_dir(self):
        r = subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), "/nonexistent/x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

class TheProjectsOwnMemoryIsNotOurs(unittest.TestCase):
    """同步**不碰 `<專案>/memory/`**,也不再複製 A 的暫存區(D-021,#28)。

    A 的 `*.inbox.md` 是 A 自己還沒併進主檔的筆記,不是規矩:同步過去的話,專案拿到的是
    一份檔頭寫著「不要改這一份」、內容是別人的專案事實、而且**沒有任何讀者**的檔(實測
    2026-09-23 T 就躺著兩份)。專案自己的備忘住 `<專案>/memory/`,規則包會疊上去讀。
    """

    def project(self, dest, applies=("memory/role/*.md", "memory/model/*.md")):
        os.makedirs(os.path.join(dest, "board"), exist_ok=True)
        os.makedirs(os.path.join(dest, "memory", "role"), exist_ok=True)
        os.makedirs(os.path.join(dest, "memory", "project"), exist_ok=True)
        os.makedirs(os.path.join(dest, "docs", "roles"), exist_ok=True)
        with open(os.path.join(dest, "board", "config.json"), "w") as handle:
            handle.write(json.dumps(
                {"rules": {"roles_dir": "docs/roles",
                           "models_dir": "docs/roles/model"},
                 "memory": {"applies_to": list(applies)}}, ensure_ascii=False))
        # 專案自己寫的那兩個檔 + 上一次同步留下來的那一份同名暫存(這正是「乙原樣」
        # 會踩到的形狀:同名同目錄,下一次同步直接蓋掉)。
        with open(os.path.join(dest, "memory", "role", "main.inbox.md"), "w") as handle:
            handle.write("- 專案自己寫的暫存\n")
        with open(os.path.join(dest, "memory", "project", "x.md"), "w") as handle:
            handle.write("- 專案共用備忘\n")
        with open(os.path.join(dest, "docs", "roles", "main.inbox.md"), "w") as handle:
            handle.write("上一次同步搬過來的\n")
        with open(os.path.join(dest, "docs", "roles", ".sync-manifest"), "w") as handle:
            handle.write("main.md\nmain.inbox.md\n")

    def fingerprint(self, dest):
        where = os.path.join(dest, "memory")
        rows = []
        for base, _dirs, names in os.walk(where):
            for name in sorted(names):
                path = os.path.join(base, name)
                with open(path, "rb") as handle:
                    rows.append((os.path.relpath(path, where),
                                 hashlib.sha256(handle.read()).hexdigest()))
        return sorted(rows)

    def test_the_projects_memory_directory_is_byte_for_byte_untouched(self):
        """**變異**:在 `copy_dir` 的目的地加一行寫進 `$DEST/memory/` → 這一條紅。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            before = self.fingerprint(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(before, self.fingerprint(d),
                             "`memory/` 是專案自己寫的,同步一個位元組都不該碰")

    def test_no_inbox_lands_in_the_synced_directory(self):
        """**變異**:把 `copy_dir` 裡跳過 `*$INBOX_SUFFIX` 的那一段拿掉 → 這一條紅。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            left = []
            for base, _dirs, names in os.walk(os.path.join(d, "docs", "roles")):
                left += [name for name in names if name.endswith(".inbox.md")]
            self.assertEqual(left, [], "A 的暫存區不是規矩,不該出現在專案裡")

    def test_the_inbox_that_used_to_be_synced_is_retired_out_loud(self):
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            said = [line for line in r.stdout.splitlines()
                    if "退場" in line and "main.inbox.md" in line]
            self.assertTrue(said, "靜悄悄的刪除與沒發生的刪除長得一樣:" + r.stdout)
            with open(os.path.join(d, "docs", "roles", ".sync-manifest")) as handle:
                manifest = handle.read()
            self.assertNotIn(".inbox.md", manifest,
                             "manifest 留著 inbox 條目的話,下一次同步又會唸一次退場")

    def test_a_project_measuring_the_synced_copy_is_refused_by_name(self):
        """`memory.applies_to` 指到 `docs/roles/` = 專案在量一份自己改不了的檔,而超標
        開出來的整理票沒有人能執行。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d, applies=("docs/roles/*.md",))
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("memory.applies_to", r.stderr)
            self.assertIn("memory/role/*.md", r.stderr, "說得出該改成什麼")


if __name__ == "__main__":
    unittest.main()
