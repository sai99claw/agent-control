"""#28(D-021):`rules.py pack` 疊專案層記憶(memory/role、memory/model 主檔 + inbox 尾巴、
memory/project 路徑清單);`sync-to-project.sh` 不再複製 `*.inbox.md`,也不碰專案 `memory/`。

驗證者案例(D-020):只證乾淨基底紅,不搭參考實作、不做變異、不等 patch。每個
`test_` 的 docstring 第一行是 `docs/DESIGN-MEMORY-INBOX.md` §驗收 / 票面 acceptance
的編號。第六條(`docs/MEMORY.md` 文字)是文件字句,不在這支跑,由 verify_strings 驗。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sync(dest, *extra):
    return subprocess.run(
        ["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), dest, *extra],
        capture_output=True, text=True, timeout=120)


def tree_hashes(root):
    """`root` 底下每個檔案的 sha256,相對路徑排序 —— 用來釘「逐位元組不變」。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            with open(path, "rb") as handle:
                out[rel] = hashlib.sha256(handle.read()).hexdigest()
    return out


# ---------------------------------------------------------------- acceptance 1-3
# 三條共用同一份 fixture(票面原話):
#   docs/roles/implementer.md(CANON-ROLE)、memory/role/implementer.md(LOCAL-ROLE)、
#   memory/role/implementer.inbox.md 三行(INBOX-3 在最後)、
#   docs/roles/model/opus.md(CANON-MODEL)、memory/model/opus.md(LOCAL-MODEL)、
#   memory/project/foo.md。


class RulesOverlayFixture(Sandbox):
    config_extra = {"rules": {"roles_dir": "docs/roles",
                              "models_dir": os.path.join("docs", "roles", "model")}}

    def setUp(self):
        super(RulesOverlayFixture, self).setUp()
        self.write(os.path.join("docs", "roles", "implementer.md"), "CANON-ROLE\n")
        self.write(os.path.join("memory", "role", "implementer.md"), "LOCAL-ROLE\n")
        self.write(os.path.join("memory", "role", "implementer.inbox.md"),
                   "inbox line one\ninbox line two\nINBOX-3\n")
        self.write(os.path.join("docs", "roles", "model", "opus.md"), "CANON-MODEL\n")
        self.write(os.path.join("memory", "model", "opus.md"), "LOCAL-MODEL\n")
        self.write(os.path.join("memory", "project", "foo.md"), "跨票都成立的專案事實\n")

    def rules(self, *args):
        return self.run_py("scripts/rules.py", *args)


class Acceptance1PackOverlaysProjectLayer(RulesOverlayFixture):

    def test_pack_overlays_canon_and_local_role_model_and_lists_project_paths(self):
        """1) roles_dir(root) != memory/role 時,pack 疊讀專案層 memory/role/<card>、
        memory/model/<model>.md 主檔與兩者 .inbox.md 尾巴,並在先讀清單多列
        memory/project/*.md 路徑;4KB 上限與 rc 不變。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = done.stdout
        for marker in ("CANON-ROLE", "LOCAL-ROLE", "INBOX-3", "CANON-MODEL", "LOCAL-MODEL"):
            self.assertIn(marker, out,
                         "rules.py 現在還沒疊讀專案層記憶 —— 輸出裡沒有 %r" % marker)
        self.assertIn(os.path.join("memory", "project", "foo.md"), out,
                     "先讀清單沒有多列 memory/project/*.md 的路徑")
        self.assertLessEqual(len(out.encode("utf-8")), 4096)


class Acceptance2SameRootIsReadOnce(RulesOverlayFixture):
    """同一 fixture,roles_dir 不設(等於 A 自己:roles_dir(root) == memory/role)。"""

    config_extra = {}

    def test_pack_does_not_duplicate_and_has_no_project_heading(self):
        """2) roles_dir(root) == memory/role(A 自己)時只讀一次、不印本專案小標,
        角色卡內容不重複。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = done.stdout
        self.assertNotIn("本專案", out,
                         "roles_dir 等於 memory/role 時不該印本專案小標")
        self.assertEqual(out.count("LOCAL-ROLE"), 1,
                         "roles_dir 預設就是 memory/role,角色卡內容不該重複出現")
        self.assertEqual(out.count("LOCAL-MODEL"), 1,
                         "models_dir 預設就是 memory/model,模型記憶不該重複出現")


class Acceptance3MissingCardKeepsInboxAndNamesItWhenCut(RulesOverlayFixture):
    """同一 fixture,拿掉專案主檔只留 inbox。"""

    def setUp(self):
        super(Acceptance3MissingCardKeepsInboxAndNamesItWhenCut, self).setUp()
        os.remove(os.path.join(self.repo, "memory", "role", "implementer.md"))

    def test_missing_local_card_is_quiet_and_inbox_tail_still_shows(self):
        """3a) 專案主檔不存在只留 inbox 時,pack 不印「找不到 memory/role/…」這樣的
        字樣,inbox 尾巴的內容仍在輸出裡。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        out = done.stdout
        self.assertNotIn("找不到 memory/role", out,
                         "專案主檔本來就可以不存在,不該印成找不到的錯誤")
        self.assertIn("INBOX-3", out,
                     "rules.py 現在還沒讀專案層 inbox 尾巴 —— INBOX-3 不在輸出裡")

    def test_inbox_slot_names_its_own_path_when_it_gets_clipped(self):
        """3b) --max-bytes 1500 時如果 inbox 那格被砍,輸出裡點名 inbox 那個路徑
        (不是默默消失)。"""
        done = self.rules("pack", "worker", "--model", "opus", "--max-bytes", "1500")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(os.path.join("memory", "role", "implementer.inbox.md"), done.stdout,
                     "rules.py 現在還沒讀專案層 inbox,1500 bytes 上限下完全沒有點名它")


# ---------------------------------------------------------------- acceptance 4


class Acceptance4InboxOverflowOpensTicketOnProjectPath(Sandbox):

    config_extra = {"rules": {"roles_dir": "docs/roles",
                              "models_dir": os.path.join("docs", "roles", "model")},
                    "memory": {"applies_to": [os.path.join("memory", "role", "*.md")],
                               "consolidator": "fable"}}

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def test_check_opens_ticket_with_project_layer_allowed_write_path(self):
        """4) memory.applies_to=[memory/role/*.md],對同一角色 note role implementer
        21 次之後,check 的 rc == 1,且它開出的整理票 allowed_write_path 是
        memory/role/implementer.md,不是 docs/roles/ 開頭的路徑。"""
        for index in range(21):
            noted = self.memory("note", "role", "implementer",
                                "第 %d 條備忘" % index, "--ticket", "28", "--by", "verifier@opus")
            self.assertEqual(noted.returncode, 0, noted.stdout + noted.stderr)
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        row = self.load_ticket("1")
        paths = row.get("allowed_write_paths") or []
        self.assertIn(os.path.join("memory", "role", "implementer.md"), paths)
        for one in paths:
            self.assertFalse(one.startswith("docs/roles"),
                             "整理票的 allowed_write_path 不該指到同步來的正本:%s" % one)


# ---------------------------------------------------------------- acceptance 5


class Acceptance5SyncSkipsInboxAndLeavesProjectMemoryAlone(unittest.TestCase):

    def make_previously_synced_project(self, d):
        """假專案:上一次同步留下 docs/roles/main.inbox.md,而它自己的
        memory/role/main.inbox.md、memory/project/x.md 是專案自己寫的,不該被同步動到。"""
        os.makedirs(os.path.join(d, "board"), exist_ok=True)
        with open(os.path.join(d, "board", "config.json"), "w", encoding="utf-8") as handle:
            json.dump({"rules": {"roles_dir": "docs/roles",
                                 "models_dir": "docs/roles/model"},
                      "memory": {"applies_to": [os.path.join("memory", "model", "*.md")]}},
                     handle)
        os.makedirs(os.path.join(d, "docs", "roles", "model"), exist_ok=True)
        with open(os.path.join(d, "docs", "roles", "main.inbox.md"), "w",
                 encoding="utf-8") as handle:
            handle.write("<!-- 上一次同步留下來的 -->\n舊的一行\n")
        with open(os.path.join(d, "docs", "roles", ".sync-manifest"), "w",
                 encoding="utf-8") as handle:
            handle.write("main.md\nmain.inbox.md\n")
        os.makedirs(os.path.join(d, "memory", "role"), exist_ok=True)
        os.makedirs(os.path.join(d, "memory", "project"), exist_ok=True)
        with open(os.path.join(d, "memory", "role", "main.inbox.md"), "w",
                 encoding="utf-8") as handle:
            handle.write("- 這一行是專案自己寫的,不是同步來的\n")
        with open(os.path.join(d, "memory", "project", "x.md"), "w",
                 encoding="utf-8") as handle:
            handle.write("跨票都成立的專案事實\n")

    def test_sync_retires_stale_inbox_copies_and_leaves_project_memory_byte_identical(self):
        """5a) sync 後 docs/roles/ 底下找不到任何 *.inbox.md、stdout 有一行點名退場
        main.inbox.md、新寫回的 manifest 不含任何 inbox 條目,且
        <專案>/memory/** 的每個檔案 sha256 與 sync 前逐一相同。"""
        with tempfile.TemporaryDirectory() as d:
            self.make_previously_synced_project(d)
            before = tree_hashes(os.path.join(d, "memory"))
            done = sync(d)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

            leftover_inbox = []
            for base, _dirs, files in os.walk(os.path.join(d, "docs", "roles")):
                leftover_inbox += [name for name in files if name.endswith(".inbox.md")]
            self.assertEqual(leftover_inbox, [],
                             "sync 現在還沒有跳過 *.inbox.md —— docs/roles/ 底下還留著: %s"
                             % leftover_inbox)

            self.assertIn("main.inbox.md", done.stdout)
            self.assertIn("退場", done.stdout)

            manifest_path = os.path.join(d, "docs", "roles", ".sync-manifest")
            with open(manifest_path, encoding="utf-8") as handle:
                manifest = handle.read()
            self.assertNotIn("inbox", manifest,
                             "新寫回的 manifest 不該再含任何 inbox 條目")

            after = tree_hashes(os.path.join(d, "memory"))
            self.assertEqual(before, after,
                             "sync 不該碰專案自己的 memory/**")

    def test_dry_run_refuses_applies_to_pointing_at_the_synced_canonical_dir(self):
        """5b) --dry-run 前置檢查多一條:applies_to 不得含 roles_dir 底下的路徑
        (專案在量一份自己改不了、同步就會被蓋掉的檔)—— rc == 2 且輸出點名
        applies_to 這一鍵。"""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "board"), exist_ok=True)
            with open(os.path.join(d, "board", "config.json"), "w", encoding="utf-8") as handle:
                json.dump({"rules": {"roles_dir": "docs/roles",
                                     "models_dir": "docs/roles/model"},
                          "memory": {"applies_to": [os.path.join("docs", "roles", "*.md")]}},
                         handle)
            done = sync(d, "--dry-run")
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("applies_to", done.stdout + done.stderr,
                         "sync-to-project.sh 現在還沒有這一條前置檢查")


if __name__ == "__main__":
    unittest.main()
