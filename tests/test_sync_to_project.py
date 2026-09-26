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
                         "review.sh", "inbox.py", "rules.py", "memory.py"):
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


class DidTheProjectActuallyWireItUp(unittest.TestCase):
    """#29 A11 / G11:**同步了不等於接上了**。

    上面那張「接點清單」每一次都印一模一樣的內容,於是「已經接上三支」與「一支都沒接」
    在畫面上長得一樣(§5.5 的母題)。所以這裡真的去專案自己的 `scripts/*.sh` 裡看一眼。
    """

    def project(self, dest, caller=""):
        write_config(dest)
        os.makedirs(os.path.join(dest, "scripts"), exist_ok=True)
        with open(os.path.join(dest, "scripts", "land-ticket.sh"), "w") as handle:
            handle.write("#!/bin/sh\n" + caller)

    def test_a_script_nobody_calls_is_named(self):
        """**變異**:把結尾那一段 `UNCALLED` 拿掉 → 這一條紅;把 `verify.py` 放回
        `DEPENDENCY_ONLY` → 這一條紅(#35 A3:以前這裡斷言它**不准**出現,把錯的現況
        釘死了 —— 專案的閘門直接跑 verify.py,它不是只被 import 的那一種)。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            line = [x for x in r.stdout.splitlines() if "沒有呼叫點" in x]
            self.assertTrue(line, "同步完了卻不說有沒有接上:" + r.stdout)
            for name in ("apply.sh", "inbox.py", "rules.py", "memory.py", "verify.py"):
                self.assertIn(name, line[0], name)
            for dependency in ("event.py", "ticket.py"):
                self.assertNotIn(dependency, line[0],
                                 "只被 import 的那幾支本來就沒有人直接叫,"
                                 "對它們喊「沒有呼叫點」是一句假話")

    def test_dry_run_names_verify_py_when_no_script_calls_control_verify_py(self):
        """#35 A1:專案有 `scripts/*.sh` 但沒有一支呼叫 `control/verify.py` →
        `--dry-run` 那一行「同步了但專案端沒有呼叫點」含 verify.py。

        **變異**:把 `verify.py` 放回 `DEPENDENCY_ONLY` → 這一條紅。
        """
        with tempfile.TemporaryDirectory() as d:
            self.project(d, caller='sh scripts/control/apply.sh "$N" patch.diff\n'
                                   'python3 scripts/verify.py --tag x\n')
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            line = [x for x in r.stdout.splitlines()
                    if "同步了但專案端沒有呼叫點" in x]
            self.assertTrue(line, r.stdout)
            self.assertIn("verify.py", line[0],
                          "專案叫的是自己那支 scripts/verify.py,control 那份沒人叫")
            self.assertNotIn("apply.sh", line[0], "被叫到了卻說沒有")

    def test_a_variable_form_call_counts_as_wired(self):
        """專案拉一格 `CONTROL=$ROOT/scripts/control` 再 `$CONTROL/apply.sh` 是**真的
        呼叫**(實測 2026-09-23:某個下游專案的 `land-ticket.sh` 六處都是這一種)。只認寫死
        路徑的話,會對一支被叫了六次的腳本說「沒有呼叫點」—— 一個守衛給錯了下一步,
        比沒有守衛更糟(§5.7)。

        **變異**:把比對字串改回 `scripts/control/$name` → 這一條紅。
        """
        with tempfile.TemporaryDirectory() as d:
            self.project(d, caller='CONTROL=$ROOT/scripts/control\n'
                                   'sh "$CONTROL/apply.sh" "$N" patch.diff\n')
            line = [x for x in sync(d).stdout.splitlines() if "沒有呼叫點" in x]
            self.assertTrue(line)
            self.assertNotIn("apply.sh", line[0], "被叫到了卻說沒有")
            self.assertIn("rules.py", line[0], "真的沒被叫的那幾支還是要點名")

    def test_a_mention_inside_a_comment_is_not_a_call(self):
        """**脫罪那一段故意寫得笨**:抓錯了是吵一次,放過了是靜的(§5.7)。
        實測 2026-09-23:某個下游專案的 `test-for.sh` 只在註解裡寫了
        `scripts/control/apply.sh`,而純字串 grep 會因此判它「接上了」。
        """
        with tempfile.TemporaryDirectory() as d:
            self.project(d, caller="# 這裡提到 scripts/control/apply.sh 只是說明\n")
            line = [x for x in sync(d).stdout.splitlines() if "沒有呼叫點" in x]
            self.assertTrue(line)
            self.assertIn("apply.sh", line[0], "註解不算接上了")

    def test_a_file_that_is_not_ours_is_named_instead_of_being_ignored(self):
        """`docs/roles/` 裡不在 manifest 上的檔是專案自己放的:同步不會蓋它、也不會
        退場它,而它讀起來與角色卡一模一樣(實測:某個下游專案的 `dispatcher.md`)。

        **變異**:把結尾那一段「非產出物」拿掉 → 這一條紅。
        """
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            os.makedirs(os.path.join(d, "docs", "roles"), exist_ok=True)
            with open(os.path.join(d, "docs", "roles", "dispatcher.md"), "w") as handle:
                handle.write("專案自建的告示\n")
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            said = [x for x in r.stdout.splitlines() if "非產出物" in x]
            self.assertTrue(said, "混在產出物目錄裡的自建檔沒有被唸出來:" + r.stdout)
            self.assertIn("dispatcher.md", said[0])
            self.assertTrue(os.path.exists(os.path.join(d, "docs", "roles",
                                                        "dispatcher.md")),
                            "只唸出來,不刪 —— 那是專案的東西")


class TheSessionHookIsSynced(unittest.TestCase):
    """#39 A8:開場那一頁的三支(`session-hook.sh` → `new-session.sh` → `heartbeat.sh`)
    跟著同步過去;hook 的呼叫點在 `.claude/settings.json`,不在 `scripts/*.sh`。"""

    THREE = ("new-session.sh", "heartbeat.sh", "session-hook.sh")
    HOOK = ('{"hooks":{"SessionStart":[{"hooks":[{"type":"command",'
            '"command":"sh \\"$CLAUDE_PROJECT_DIR/scripts/control/session-hook.sh\\"",'
            '"timeout":60}]}]}}')

    def project(self, dest, hook=True):
        write_config(dest)
        if hook:
            os.makedirs(os.path.join(dest, ".claude"))
            with open(os.path.join(dest, ".claude", "settings.json"), "w") as handle:
                handle.write(self.HOOK)

    def uncalled(self, out):
        line = [x for x in out.splitlines() if "同步了但專案端沒有呼叫點" in x]
        self.assertTrue(line, "這個專案沒有 scripts/*.sh,那一行本來就該出現:" + out)
        return line[0]

    def test_a8_the_three_land_executable_and_in_the_manifest(self):
        """**變異 M3**:`SCRIPT_LIST` 少了 `session-hook.sh` → 這一條紅。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            control = os.path.join(d, "scripts", "control")
            with open(os.path.join(control, ".sync-manifest")) as handle:
                manifest = handle.read().splitlines()
            for name in self.THREE:
                path = os.path.join(control, name)
                self.assertTrue(os.path.isfile(path), name)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o755, name)
                self.assertIn(name, manifest)

    def test_a8_the_hook_in_settings_counts_as_wired(self):
        with tempfile.TemporaryDirectory() as d:
            self.project(d)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            line = self.uncalled(r.stdout)
            for name in self.THREE:
                self.assertNotIn(name, line, "接在 .claude/settings.json 的 hook 被說成沒接")

    def test_a8_no_hook_in_settings_is_named(self):
        """反方向:沒接 hook 的專案要被點名 —— 不然上一條對一個根本不看 settings 的
        版本(或把 session-hook.sh 塞進 DEPENDENCY_ONLY 的版本)也是綠的。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d, hook=False)
            r = sync(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("session-hook.sh", self.uncalled(r.stdout))


class ASameNamedForkInTheProjectIsNamed(unittest.TestCase):
    """#35 A2:專案自己有一支 `scripts/verify.py`,與同步過去的 `scripts/control/verify.py`
    內容不同 —— 專案的閘門跑的是前者,後者再新也用不到,而兩份讀起來都像規格。"""

    FORK = "sync: 專案有同名分岔檔:verify.py"

    def project(self, dest, own_verify):
        write_config(dest)
        os.makedirs(os.path.join(dest, "scripts"), exist_ok=True)
        with open(os.path.join(dest, "scripts", "verify.py"), "w",
                  encoding="utf-8") as handle:
            handle.write(own_verify)

    def test_a_different_verify_py_is_named(self):
        """**變異**:把分岔檔那一段的 `echo` 拿掉 → 這一條紅。"""
        with tempfile.TemporaryDirectory() as d:
            self.project(d, "#!/usr/bin/env python3\nprint('專案自己的 verify')\n")
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(self.FORK, r.stdout.splitlines())

    def test_an_identical_verify_py_is_not_named(self):
        """內容一樣就不是分岔 —— 對它喊是一句假警報(§5.7)。

        **變異**:把 `cmp -s` 那一格拿掉(一律印)→ 這一條紅。
        """
        with open(os.path.join(HERE, "scripts", "verify.py"), encoding="utf-8") as handle:
            same = handle.read()
        with tempfile.TemporaryDirectory() as d:
            self.project(d, same)
            r = sync(d, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("同名分岔檔", r.stdout)


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
