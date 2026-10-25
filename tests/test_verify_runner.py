import contextlib, importlib.util, json, os, subprocess, sys, tempfile, unittest, shutil
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = [sys.executable, os.path.join(HERE, "scripts/verify.py")]


def local_env(**overrides):
    """釘死 AC_ROOT = AC_CODE_ROOT = HERE(這個 worktree 自己),不管外層(land.sh 跑全套時)
    有沒有設(#32 第 2 輪:land 的全套在自己的環境裡先設了 AC_ROOT 指到別的 worktree,子行程
    會原樣繼承,這幾條測試要驗的是『這個副本自己的 verify/』,不是外層那一個)。程式碼根是
    `AC_CODE_ROOT`,否則 cwd 的 toplevel(#159)—— 驗證者的封存副本不是 git 樹,不釘就答不出來。"""
    env = dict(os.environ)
    env["AC_ROOT"] = HERE
    env["AC_CODE_ROOT"] = HERE
    env.update(overrides)
    return env


@contextlib.contextmanager
def code_root_env(value, cwd):
    """import 進行程時暫時把 `AC_CODE_ROOT` 換成 `value`(None = 拿掉)、cwd 換成 `cwd` ——
    `ROOT = _find_root()` 在 import 那一刻執行,外層(閘門)留在 os.environ 的 `AC_CODE_ROOT`
    會讓 override 分支先短路,這裡要問的那一段永遠不會被走到(#32 第 2 輪同一種)。"""
    had = "AC_CODE_ROOT" in os.environ
    old = os.environ.pop("AC_CODE_ROOT", None)
    here = os.getcwd()
    if value is not None:
        os.environ["AC_CODE_ROOT"] = value
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(here)
        os.environ.pop("AC_CODE_ROOT", None)
        if had:
            os.environ["AC_CODE_ROOT"] = old


def copy_code_tree(tmp, only=None):
    """把 verify.py 跑得起來的那幾份從 HERE 拷進 `tmp`:要寫案例的測試寫 tmp 那一份,
    被 kill 也只在 tempfile 目錄留檔,HERE 的 verify/ 一個字都不動(#70)。

    `only` 給了就只搬 verify/ 底下那幾個功能目錄(登記檔照搬):全庫的回歸案例
    與這裡要問的事無關,只是時間(#73)。"""
    for rel in ("scripts/verify.py", "scripts/status.py", "scripts/event.py",
                "scripts/adapter-unittest.py", "board/config.json"):
        os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
        shutil.copy(os.path.join(HERE, rel), os.path.join(tmp, rel))
    src = os.path.join(HERE, "verify")

    def skip(where, names):
        dropped = set(shutil.ignore_patterns("__pycache__")(where, names))
        if only is not None and os.path.samefile(where, src):
            dropped |= {n for n in names
                        if os.path.isdir(os.path.join(where, n))
                        and n != "TAGS.d" and n not in only}
        return dropped
    shutil.copytree(src, os.path.join(tmp, "verify"), ignore=skip)


class VerifyRunner(unittest.TestCase):
    def test_list_and_tag_filter_run_in_a_sandbox_with_only_example(self):
        """沙盒只放 verify/example:`--list` 只列得出 example 的案例,無參數的全跑
        也只跑那一份(以前跑的是真 verify/ 全庫)。"""
        with tempfile.TemporaryDirectory() as tmp:
            copy_code_tree(tmp, only=("example",))
            run = [sys.executable, os.path.join(tmp, "scripts", "verify.py")]
            env = local_env(AC_ROOT=tmp, AC_CODE_ROOT=tmp)
            env.pop("AC_TICKET", None)
            env.pop("AC_RUN_ID", None)
            listed = subprocess.run(run + ["--list"], capture_output=True, text=True, env=env)
            self.assertEqual(listed.returncode, 0, listed.stdout)
            cases = [line.split()[0] for line in listed.stdout.splitlines()
                     if not line.startswith("verify:")]
            self.assertEqual(cases, [os.path.join("verify", "example", "test_example.py")],
                             listed.stdout)
            self.assertEqual(subprocess.run(run + ["--tag", "example"], capture_output=True, env=env).returncode, 0)
            self.assertEqual(subprocess.run(run + ["--tag", "no-such-tag"], capture_output=True, env=env).returncode, 3)
            self.assertEqual(subprocess.run(run, capture_output=True, env=env).returncode, 0)

    def test_unit_layer_without_config_is_loud_not_green(self):
        """沒有 `unit_cmd` 的單元層不准回 0:config 整份拿掉、或在但沒有那一格,都要
        rc 非 0 並點名 board/config.json 的 unit_cmd。

        **變異**:verify.py 未設定那一句改回 `return 0` → 這一條紅。
        """
        for shape in ("no config", "config without unit_cmd"):
            with self.subTest(shape), tempfile.TemporaryDirectory() as tmp:
                copy_code_tree(tmp, only=("example",))
                cfg = os.path.join(tmp, "board", "config.json")
                if shape == "no config":
                    os.remove(cfg)
                else:
                    with open(cfg, "w", encoding="utf-8") as f:
                        f.write("{}")
                r = subprocess.run([sys.executable, os.path.join(tmp, "scripts", "verify.py"),
                                    "--unit"], capture_output=True, text=True,
                                   env=local_env(AC_ROOT=tmp, AC_CODE_ROOT=tmp))
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("單元層未設定(board/config.json 的 unit_cmd)", r.stdout)

    def test_a_case_without_registered_tags_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_code_tree(tmp)
            p = os.path.join(tmp, "verify/example/test_zz_unregistered.py")
            with open(p, "w") as f:
                f.write("import unittest\nTAGS=['not-registered']\nclass T(unittest.TestCase):\n    def test_x(self): pass\n")
            r = subprocess.run([sys.executable, os.path.join(tmp, "scripts", "verify.py"), "--list"],
                               capture_output=True, text=True,
                               env=local_env(AC_ROOT=tmp, AC_CODE_ROOT=tmp))
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("標籤未登記", r.stdout)

class RunScopedCache(unittest.TestCase):
    """同一輪裡,同一組標籤、同一個 sha 只跑一次(D-015)。

    2026-09-21 外部審查的 token 帳:「同一組 tag 回歸可能被 worker、驗證者、gate
    重跑。」驗證者那一份已經拿掉了,剩下 worker 交付前那一次與閘門那一次 ——
    同一份程式、同一組標籤、同一個 commit,兩份一模一樣的綠。
    """

    def run_with(self, root, run_id, *extra):
        # `root` 只是票根(AC_ROOT):快取檔寫去它的 reports/。案例與被測檔來自
        # 程式碼根 HERE(`AC_CODE_ROOT`,#159),所以假專案不需要自己的 verify/(#55)。
        env = dict(os.environ)
        env.update({"AC_TICKET": "77", "AC_RUN_ID": run_id, "AC_ROOT": root,
                    "AC_CODE_ROOT": HERE})
        return subprocess.run(RUN + ["--tag", "example", *extra],
                              capture_output=True, text=True, env=env, timeout=300)

    def test_the_second_call_in_the_same_run_reuses_the_log(self):
        with tempfile.TemporaryDirectory() as home:
            first = self.run_with(home, "r1")
            self.assertEqual(first.returncode, 0, first.stdout)
            self.assertNotIn("用快取", first.stdout)
            second = self.run_with(home, "r1")
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertIn("用快取", second.stdout)
            self.assertIn("Ran", second.stdout, "快取要印回原始輸出,不是印一句「跳過」")

    def test_no_cache_really_reruns(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_with(home, "r1")
            again = self.run_with(home, "r1", "--no-cache")
            self.assertNotIn("用快取", again.stdout)

    def test_a_different_run_does_not_inherit_the_cache(self):
        """快取的危險不是省太多,是省錯 —— 所以它綁在一輪裡,不跨輪。"""
        with tempfile.TemporaryDirectory() as home:
            self.run_with(home, "r1")
            other = self.run_with(home, "r2")
            self.assertNotIn("用快取", other.stdout)

    def test_without_a_run_id_nothing_is_cached(self):
        """單獨跑的人拿到的永遠是真的跑。

        先把快取 prime 好(同一張票、有 run id 的一次;再一次沒有 run id 的),
        第二次沒有 run id 的呼叫仍然不准說「用快取」。

        **變異**:`cache_path()` 不看 `AC_RUN_ID`(只要有票號就快取)→ 這一條紅。
        """
        with tempfile.TemporaryDirectory() as home:
            self.run_with(home, "r1")
            env = dict(os.environ)
            env.update({"AC_TICKET": "77", "AC_ROOT": home, "AC_CODE_ROOT": HERE})
            env.pop("AC_RUN_ID", None)
            for _ in range(2):
                done = subprocess.run(RUN + ["--tag", "example"], capture_output=True,
                                      text=True, env=env, timeout=300)
                self.assertNotIn("用快取", done.stdout)


class CodeRootIsNotTheTicketRoot(unittest.TestCase):
    """#55:land.sh 在票 worktree 裡帶 `AC_ROOT=<主 repo>` 跑閘門,verify.py 子行程繼承
    它 —— 舊版的 `_find_root()` 先讀 `AC_ROOT`,於是 worktree 的閘門跑的是主 repo 的
    `verify/`,worktree 裡的紅看不見。兩個根分開:程式碼根是 `AC_CODE_ROOT`(否則 cwd 的
    toplevel,#159),`AC_ROOT` 只管票、事件與 reports。"""

    def test_ac_code_root_runs_the_copys_red_case_while_ac_root_is_here(self):
        """程式碼根是受測的那棵樹(#55;D-071 §1,#159)。

        A1:把 HERE 的必要部分拷到 tmp,tmp 那份 example 案例改成紅(字串由這裡寫);
        `AC_CODE_ROOT=tmp`、`AC_ROOT=HERE` 跑 HERE 的 verify.py —— 紅只可能來自 tmp。
        HERE 的 verify/ 不動。

        **變異 M1**:`_find_root()` 放回 `AC_ROOT` override 分支(或從 `__file__` 往上找)
        → 紅(跑到 HERE 那份綠案例,rc 0)。
        """
        with tempfile.TemporaryDirectory() as tmp:
            copy_code_tree(tmp)
            with open(os.path.join(tmp, "verify", "example", "test_example.py"), "w",
                      encoding="utf-8") as f:
                f.write("import unittest\nTAGS = ['example']\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_red(self):\n"
                        "        self.fail('copy-only red')\n")
            env = local_env(AC_CODE_ROOT=tmp)
            env.pop("AC_TICKET", None)
            env.pop("AC_RUN_ID", None)
            done = subprocess.run(
                RUN + ["--tag", "example", "--no-cache"],
                capture_output=True, text=True, env=env, timeout=300)
            self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("copy-only red", done.stdout + done.stderr)

    def test_ac_root_is_still_where_the_cache_lives(self):
        """AC_ROOT 不再是程式碼根(#55),但仍是快取的根。

        A2:`AC_ROOT` 指到一顆只有 board/config.json、沒有 verify/ 的 tmp2 —— 案例照樣
        跑得到(來自 HERE),快取落在 `<tmp2>/reports/t77/<run_id>/`。

        **變異 M2**:`cache_path()` 改成用 `ROOT` 拼 reports/ → 紅(log 落到 HERE)。
        """
        with tempfile.TemporaryDirectory() as tmp2:
            os.makedirs(os.path.join(tmp2, "board"))
            with open(os.path.join(tmp2, "board", "config.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            run_id = "r-ticket-root-%d" % os.getpid()
            env = dict(os.environ)
            env.update({"AC_TICKET": "77", "AC_RUN_ID": run_id, "AC_ROOT": tmp2,
                        "AC_CODE_ROOT": HERE})
            try:
                done = subprocess.run(RUN + ["--tag", "example"], capture_output=True,
                                      text=True, env=env, timeout=300)
                self.assertFalse(os.path.exists(os.path.join(tmp2, "verify")))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertRegex(done.stdout + done.stderr, r"Ran [1-9]")
                where = os.path.join(tmp2, "reports", "t77", run_id)
                logs = [n for n in (os.listdir(where) if os.path.isdir(where) else [])
                        if n.startswith("verify-") and n.endswith(".log")]
                self.assertTrue(logs, "快取該住在 AC_ROOT 那棵樹:%s" % where)
            finally:
                # 變異下 log 會落到 HERE/reports —— 不留垃圾在 HERE。
                shutil.rmtree(os.path.join(HERE, "reports", "t77", run_id), True)


class RootIsTheTreeUnderTestNotTheFilesHome(unittest.TestCase):
    """#159(D-071 C2/§1):腳本住在 A 的 clone,受測的樹在專案裡 —— 程式碼根不從這支檔案
    的位置往上找(舊版那一找在 A 的 clone 裡永遠找到 A 自己)。同一份 verify.py 放在哪裡都
    算出同一個根:`AC_CODE_ROOT`,否則 cwd 的 toplevel,都沒有就 None(`main()` rc 2)。
    """

    def _fake_project(self, tmp, tag):
        os.makedirs(os.path.join(tmp, "board"))
        with open(os.path.join(tmp, "board", "config.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.makedirs(os.path.join(tmp, "verify"))
        with open(os.path.join(tmp, "verify", "TAGS.md"), "w", encoding="utf-8") as f:
            f.write("- `%s` — fixture\n" % tag)

    def _load_verify_copied_to(self, dest_dir, code_root, cwd):
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "verify.py")
        shutil.copy(os.path.join(HERE, "scripts", "verify.py"), dest)
        spec = importlib.util.spec_from_file_location("verify_under_test_%d" % id(dest), dest)
        mod = importlib.util.module_from_spec(spec)
        with code_root_env(code_root, cwd):
            spec.loader.exec_module(mod)
        return mod

    def test_two_homes_of_the_file_agree_on_the_code_root(self):
        """`<A>/scripts/` 與別處更深一層的一份複本,`AC_CODE_ROOT=<專案>` 都算出專案。

        **變異**:`_find_root()` 放回從 `__file__` 往上找 → 兩份算出各自的家,紅。"""
        with tempfile.TemporaryDirectory() as fake, tempfile.TemporaryDirectory() as home:
            self._fake_project(fake, "example-fake")
            self._fake_project(home, "home-only")
            in_home = self._load_verify_copied_to(os.path.join(home, "scripts"), fake, home)
            in_control = self._load_verify_copied_to(
                os.path.join(home, "x", "scripts", "control"), fake, home)
            self.assertEqual(os.path.realpath(in_home.ROOT), os.path.realpath(fake))
            self.assertEqual(os.path.realpath(in_control.ROOT), os.path.realpath(fake))
            self.assertIn("example-fake", in_home.registered_tags())
            self.assertNotIn("home-only", in_home.registered_tags())

    def test_without_ac_code_root_it_is_cwd_toplevel_or_nothing(self):
        """沒有 `AC_CODE_ROOT`:cwd 在一棵 git 樹裡 → 它的 toplevel;不在 → None(不猜)。"""
        with tempfile.TemporaryDirectory() as tree, tempfile.TemporaryDirectory() as bare:
            self._fake_project(tree, "in-tree")
            subprocess.run(["git", "init", "-q", tree], check=True, capture_output=True)
            os.makedirs(os.path.join(tree, "docs"))
            mod = self._load_verify_copied_to(os.path.join(bare, "a", "scripts"), None,
                                              os.path.join(tree, "docs"))
            self.assertEqual(os.path.realpath(mod.ROOT), os.path.realpath(tree))
            mod = self._load_verify_copied_to(os.path.join(bare, "b", "scripts"), None, bare)
            self.assertIsNone(mod.ROOT)


class ResultAndCase(unittest.TestCase):
    """#161 A5:`--result` 寫 schema 1(經 adapter 的寫法)、`--case` 直接指名案例檔。
    沙盒同 `test_list_and_tag_filter_run_in_a_sandbox_with_only_example`:只搬 verify/example,
    `AC_CODE_ROOT` = 沙盒。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        copy_code_tree(self.tmp, only=("example",))
        self.run_cmd = [sys.executable, os.path.join(self.tmp, "scripts", "verify.py")]
        self.env = local_env(AC_ROOT=self.tmp, AC_CODE_ROOT=self.tmp)
        self.env.pop("AC_TICKET", None)
        self.env.pop("AC_RUN_ID", None)
        self.out = os.path.join(self.tmp, "out", "result.json")

    def verify(self, *args, env=None):
        return subprocess.run(self.run_cmd + list(args), capture_output=True, text=True,
                              env=env or self.env, timeout=300)

    def result(self):
        with open(self.out, encoding="utf-8") as handle:
            return json.load(handle)

    def assert_example_result(self, done):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = self.result()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["command"], "regression")
        ids = [row["id"] for row in data["ids"]]
        self.assertTrue([i for i in ids if i.startswith("verify.example.test_example.")], ids)
        self.assertEqual({row["status"] for row in data["ids"]}, {"pass"})
        self.assertEqual(data["environment_suspect"], [])
        self.assertIn("Ran ", done.stdout, "跑的輸出照舊印到 stdout")

    def test_case_with_result(self):
        self.assert_example_result(self.verify(
            "--case", "verify/example/test_example.py", "--result", self.out, "--no-cache"))

    def test_tag_with_result_writes_the_same_shape(self):
        self.assert_example_result(self.verify("--tag", "example", "--result", self.out))

    def test_case_ignores_tags(self):
        """`--case` 不看標籤:沒有 TAGS 的檔照跑,紅的那一條 file/line 指回案例檔。"""
        body = "import unittest\nclass T(unittest.TestCase):\n    def test_red(self):\n" \
               "        self.assertEqual(1, 2)\n"
        with open(os.path.join(self.tmp, "verify", "example", "test_untagged.py"), "w",
                  encoding="utf-8") as handle:
            handle.write(body)
        done = self.verify("--case", "verify/example/test_untagged.py", "--result", self.out)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        row, = self.result()["ids"]
        self.assertEqual((row["id"], row["status"], row["file"], row["line"]),
                         ("verify.example.test_untagged.T.test_red", "fail",
                          "verify/example/test_untagged.py", 4))
        # 沒有 --case 時,同一個沒 TAGS 的檔照舊讓全跑 rc 2(標籤檢查沒被拿掉)。
        self.assertEqual(self.verify("--tag", "example").returncode, 2)

    def test_missing_case_is_rc_3_naming_the_path(self):
        done = self.verify("--case", "verify/example/test_nope_zzz.py")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("verify/example/test_nope_zzz.py", done.stdout + done.stderr)

    def test_a_cached_run_still_writes_the_result(self):
        env = dict(self.env, AC_TICKET="77", AC_RUN_ID="r1")
        self.assert_example_result(self.verify("--tag", "example", "--result", self.out,
                                               env=env))
        os.remove(self.out)
        again = self.verify("--tag", "example", "--result", self.out, env=env)
        self.assertIn("用快取", again.stdout)
        self.assert_example_result(again)


class TestsSectionOfAsConfig(unittest.TestCase):
    """#161 A5 靜態:A 的 `board/config.json` 的 `tests.unittest` 是 `gate.sh map()` / `BASE`
    的資料版。#161 時兩份並存,這一條逐列比對讓它們不准漂;#162(設計 row 3b)把 `map()`
    與 `BASE` 從 gate.sh 刪了,設定是唯一的一份 —— 比對的對象沒了,改成斷言 gate.sh 不再
    帶第二份(漂移的前提就是兩份並存)。"""

    def setUp(self):
        with open(os.path.join(HERE, "board", "config.json"), encoding="utf-8") as handle:
            self.unit = json.load(handle)["tests"]["unittest"]
        with open(os.path.join(HERE, "scripts", "gate.sh"), encoding="utf-8") as handle:
            self.gate = handle.read()

    def test_base_is_the_four_base_ids(self):
        self.assertEqual(self.unit["base"],
                         ["test_ticket", "test_event", "test_memory", "test_no_project_names"])
        self.assertFalse('\nBASE="' in self.gate, "gate.sh carries a second copy of the base ids")
        self.assertEqual(self.unit["roots"], ["tests"])

    def test_map_is_the_only_copy(self):
        """50 列 = #161 / #160 的 46 列(那時逐列等於 gate.sh `map()`)加 #162 的三列
        (`scripts/testiface.py`、`tests/fixtures/project/*`、`templates/project-*.example.*`)
        加 #164 的一列(`scripts/hooks.py`)。
        gate.sh 不再有 `map()`:對照表只住在設定裡(#162 A4 靜態那一條的同一件事)。"""
        rows = [(row["match"], row["ids"]) for row in self.unit["map"]]
        self.assertEqual(len(rows), 50)
        self.assertEqual(len({match for match, _ in rows}), len(rows), "duplicate match rows")
        for row in (("scripts/testiface.py",
                     ["test_testiface", "test_gate", "test_status", "test_actl"]),
                    ("tests/fixtures/project/*", ["test_testiface", "test_no_project_names"]),
                    ("templates/project-*.example.*",
                     ["test_testiface", "test_no_project_names"])):
            self.assertIn(row, rows)
        self.assertFalse("\nmap() {" in self.gate, "gate.sh carries a second copy of the map")

    def test_project_config_check_accepts_it(self):
        """`project_config.py` 是 #160(設計 row 2,並行、不是 depends_on),base 3bdba76 上沒有。
        不是 #161 的驗收閘(round-1 反駁 accepted):檔在才跑、斷言 rc 0;#160 / #161 哪張後落地,
        這一條就在那一刻變成真的在量。"""
        tool =os.path.join(HERE, "scripts", "project_config.py")
        if not os.path.exists(tool):
            self.skipTest("scripts/project_config.py (design row 2) is not on this base yet")
        done = subprocess.run([sys.executable, tool, "check", "."], cwd=HERE, env=local_env(),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
