"""`scripts/repo-map.py`:repo 地圖查得到、而且查到的不是過期的那一份(#87 A28–A32,原 #77)。

契約四件事:
1. 同一棵樹連跑兩次 `build`,產出物位元組相同;真 repo 的 `check` 是 0。
2. 新增 / 改名 / 刪除一支已管理的檔而沒重生 → `check` 非零並點名那支檔;重生後 `query`
   答得對。改檔的**內容**、動不歸地圖管的檔 → 不 churn。
3. `query memory` / `query rules` 指到真的入口與主守衛,印出來的每一條路徑都在;拼錯的
   主題非零,並給索引入口。
4. 同步到專案的 `scripts/control/` 照樣能用,專案自己的 catalog 與人工卡不被同步蓋掉;
   閘門拒收候選樹裡過期的索引。

拋棄式的樹由這裡自己搭(兩支腳本 + 一份 catalog);期望的檔名、主題、路徑都寫死在這裡,
不從 repo-map.py 讀回來。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS, TIMEOUT, Sandbox  # noqa: E402

CATALOG = {
    "version": 1,
    "managed": ["scripts/*.py", "tests/test_*.py"],
    "topics": {
        "memory": {"summary": "記憶", "match": ["memory"], "entry": "scripts/memory.py",
                   "guard": "tests/test_memory.py", "docs": ["docs/MEMORY.md"]},
        "rules": {"summary": "規則包", "match": ["rules"], "entry": "scripts/rules.py",
                  "guard": "tests/test_rules.py"},
    },
}

PASSING = """import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""


def clean_env(**extra):
    env = {key: value for key, value in os.environ.items() if not key.startswith("AC_")}
    env.update(extra)
    return env


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class ThrowawayTree(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ("repo-map.py", "event.py"):
            write(self.root, os.path.join("scripts", name),
                  open(os.path.join(SCRIPTS, name), encoding="utf-8").read())
        write(self.root, os.path.join("board", "config.json"), "{}\n")
        write(self.root, os.path.join("code-map", "catalog.json"),
              json.dumps(CATALOG, ensure_ascii=False, indent=2))
        for rel in ("scripts/memory.py", "scripts/rules.py", "tests/test_memory.py",
                    "tests/test_rules.py", "docs/MEMORY.md"):
            write(self.root, rel, "# %s\n" % rel)

    def tool(self, *args, root=None):
        return subprocess.run(["python3", os.path.join(root or self.root, "scripts",
                                                       "repo-map.py"), *args],
                              cwd=root or self.root, env=clean_env(), capture_output=True,
                              text=True, timeout=TIMEOUT)

    def built(self):
        done = self.tool("build")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return (read_bytes(os.path.join(self.root, "code-map", "index.json")),
                read_bytes(os.path.join(self.root, "code-map", "INDEX.md")))

    def query_paths(self, topic):
        done = self.tool("query", topic)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return [line.split("\t", 1)[1] for line in done.stdout.splitlines() if "\t" in line]


class BuildIsDeterministic(ThrowawayTree):

    def test_a28_build_twice_is_byte_identical_and_check_is_zero(self):
        first = self.built()
        self.assertEqual(self.built(), first, "同一棵樹連跑兩次,產出物不一樣")
        done = self.tool("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class FreshnessFollowsTheManagedFiles(ThrowawayTree):
    """A29。**變異**:`stale_reasons` 只比主題、不比 managed 名單 → 新增那一格紅。"""

    def setUp(self):
        super().setUp()
        self.built()

    def assert_stale_naming(self, rel):
        done = self.tool("check")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(rel, done.stderr)
        self.assertIn("索引過期", done.stderr)

    def test_a29_adding_a_managed_source_needs_a_rebuild(self):
        write(self.root, "scripts/memory_extra.py", "# 新來源\n")
        self.assert_stale_naming("scripts/memory_extra.py")
        self.built()
        self.assertEqual(self.tool("check").returncode, 0)
        self.assertIn("scripts/memory_extra.py", self.query_paths("memory"))

    def test_a29_renaming_a_managed_source_needs_a_rebuild(self):
        write(self.root, "scripts/memory_extra.py", "# 新來源\n")
        self.built()
        os.rename(os.path.join(self.root, "scripts", "memory_extra.py"),
                  os.path.join(self.root, "scripts", "memory_more.py"))
        self.assert_stale_naming("scripts/memory_more.py")
        self.built()
        paths = self.query_paths("memory")
        self.assertIn("scripts/memory_more.py", paths)
        self.assertNotIn("scripts/memory_extra.py", paths)

    def test_a29_deleting_a_managed_source_needs_a_rebuild(self):
        write(self.root, "tests/test_memory_more.py", "# 新守衛\n")
        self.built()
        os.remove(os.path.join(self.root, "tests", "test_memory_more.py"))
        self.assert_stale_naming("tests/test_memory_more.py")
        self.built()
        self.assertNotIn("tests/test_memory_more.py", self.query_paths("memory"))

    def test_a29_renaming_the_entry_is_a_broken_link_until_the_catalog_follows(self):
        os.rename(os.path.join(self.root, "scripts", "rules.py"),
                  os.path.join(self.root, "scripts", "rulebook.py"))
        done = self.tool("check")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("scripts/rules.py", done.stderr)
        catalog = dict(CATALOG)
        catalog["topics"] = dict(CATALOG["topics"])
        catalog["topics"]["rules"] = dict(CATALOG["topics"]["rules"], entry="scripts/rulebook.py",
                                          match=["rule"])
        write(self.root, os.path.join("code-map", "catalog.json"),
              json.dumps(catalog, ensure_ascii=False, indent=2))
        self.built()
        self.assertEqual(self.tool("check").returncode, 0)
        self.assertEqual(self.query_paths("rules")[0], "scripts/rulebook.py")

    def test_a29_unrelated_changes_do_not_churn(self):
        before = self.built()
        write(self.root, "scripts/memory.py", "# 內容改了,名單沒變\nprint(1)\n")
        write(self.root, "docs/notes.md", "# 不歸地圖管\n")
        write(self.root, "scripts/notes.txt", "不是 managed 的副檔名\n")
        done = self.tool("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.built(), before, "無關的變動讓產出物變了")


class QueryTheRealRepo(unittest.TestCase):
    """A28 / A30 / A37:真 repo 的地圖是新的,查得到真的入口與主守衛。"""

    def tool(self, *args):
        return subprocess.run(["python3", os.path.join(SCRIPTS, "repo-map.py"), "--root", ROOT,
                               *args], cwd=ROOT, env=clean_env(), capture_output=True,
                              text=True, timeout=TIMEOUT)

    def test_a28_the_real_index_is_fresh(self):
        done = self.tool("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a30_memory_and_rules_point_at_real_entries_and_guards(self):
        for topic, entry, guard in (("memory", "scripts/memory.py", "tests/test_memory.py"),
                                    ("rules", "scripts/rules.py", "tests/test_rules_delivery.py")):
            with self.subTest(topic):
                done = self.tool("query", topic)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("入口\t%s" % entry, done.stdout)
                self.assertIn("主守衛\t%s" % guard, done.stdout)
                paths = [line.split("\t", 1)[1] for line in done.stdout.splitlines()
                         if "\t" in line]
                missing = [rel for rel in paths if not os.path.isfile(os.path.join(ROOT, rel))]
                self.assertEqual(missing, [], "地圖指到不存在的檔")

    def test_a30_a_misspelled_topic_is_non_zero_and_points_at_the_index(self):
        done = self.tool("query", "memroy")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("code-map/INDEX.md", done.stderr)
        self.assertIn("memory", done.stderr)


class TheSyncedCopyWorks(unittest.TestCase):
    """A31:同步到專案的 `scripts/control/` 照樣能用;專案的 catalog 與人工卡不被蓋。"""

    def test_a31_it_runs_from_scripts_control_and_keeps_the_local_catalog(self):
        project = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, project, True)
        write(project, os.path.join("board", "config.json"), "{}\n")
        local = {"version": 1, "managed": ["src/*.py"],
                 "topics": {"core": {"summary": "專案自己的", "match": ["core"],
                                     "entry": "src/core.py", "guard": "src/core_check.py"}}}
        catalog = write(project, os.path.join("code-map", "catalog.json"),
                        json.dumps(local, ensure_ascii=False, indent=2))
        card = write(project, os.path.join("code-map", "cards", "core.md"), "# 人工卡\n")
        for rel in ("src/core.py", "src/core_check.py"):
            write(project, rel, "# %s\n" % rel)
        before = (read_bytes(catalog), read_bytes(card))
        done = subprocess.run(["sh", os.path.join(ROOT, "scripts", "sync-to-project.sh"),
                               project], env=clean_env(), capture_output=True, text=True,
                              timeout=TIMEOUT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((read_bytes(catalog), read_bytes(card)), before,
                         "同步蓋掉了專案自己的 catalog / 人工卡")
        tool = os.path.join(project, "scripts", "control", "repo-map.py")
        self.assertTrue(os.path.isfile(tool), "repo-map.py 沒有同步到 scripts/control/")
        for args, rc in ((("build",), 0), (("check",), 0), (("query", "core"), 0)):
            ran = subprocess.run(["python3", tool, *args], cwd=project, env=clean_env(),
                                 capture_output=True, text=True, timeout=TIMEOUT)
            self.assertEqual(ran.returncode, rc, " ".join(args) + ran.stdout + ran.stderr)
        self.assertIn("入口\tsrc/core.py", ran.stdout)
        self.assertTrue(os.path.isfile(os.path.join(project, "code-map", "index.json")),
                        "索引沒有寫在專案根")


class TheGateRefusesAStaleIndex(Sandbox):
    """A32:`gate.sh --branch --ticket <n>` 拒收候選樹裡過期的索引(候選樹 = 票的 worktree)。"""

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(SCRIPTS, "repo-map.py"),
                    os.path.join(self.repo, "scripts", "repo-map.py"))
        catalog = {"version": 1, "managed": ["scripts/*.py", "tests/test_*.py"],
                   "topics": {"memory": {"summary": "沙盒", "match": ["memory"],
                                         "entry": "scripts/memory.py"}}}
        self.write(os.path.join("code-map", "catalog.json"),
                   json.dumps(catalog, ensure_ascii=False, indent=2))
        self.write(os.path.join("tests", "test_repo_map_contract.py"), PASSING)
        done = self.run_py("scripts/repo-map.py", "build")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.make_ticket(7, allowed_write_paths=["tests/*", "code-map/*"], state="Running")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的地圖與票 #7")

    def gate_in(self, wt):
        return self.run_sh(os.path.join(wt, "scripts", "gate.sh"), "--branch", "--ticket", "7",
                           "--no-auto-fix", cwd=wt)

    def test_a32_a_new_managed_file_without_a_rebuild_is_refused(self):
        """**變異 M13**:拿掉 gate.sh 的 freshness 那一段 → 這一條紅(rc 不是 4)。"""
        wt = self.worktree("t7")
        self.write(os.path.join("tests", "test_memory_new.py"), PASSING, where=wt)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 加了一支已管理的檔,沒重生", cwd=wt)
        done = self.gate_in(wt)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("code-map 索引過期", done.stderr)
        self.assertIn("tests/test_memory_new.py", done.stderr)
        self.assertFalse(os.path.exists(self.log), "索引過期卻跑了測試")

        rebuilt = subprocess.run(["python3", os.path.join(wt, "scripts", "repo-map.py"),
                                  "build"], cwd=wt, env=self.env(), capture_output=True,
                                 text=True, timeout=TIMEOUT)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
        self.git("add", "-A", cwd=wt)
        self.git("commit", "-q", "-m", "#7 重生索引", cwd=wt)
        again = self.gate_in(wt)
        self.assertNotIn("code-map 索引過期", again.stderr)
        self.assertNotEqual(again.returncode, 4, again.stdout + again.stderr)


if __name__ == "__main__":
    unittest.main()
