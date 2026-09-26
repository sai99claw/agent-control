import contextlib, importlib.util, os, subprocess, sys, tempfile, unittest, shutil
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = [sys.executable, os.path.join(HERE, "scripts/verify.py")]


def local_env(**overrides):
    """釘死 AC_ROOT = HERE(這個 worktree 自己),不管外層(land.sh 跑全套時)有沒有
    設 AC_ROOT(#32 第 2 輪:分支閘門用的是乾淨環境,沒有外部 AC_ROOT,這裡的測試
    才碰巧綠;land 的全套在自己的環境裡先設了 AC_ROOT 指到別的 worktree,子行程
    會原樣繼承,這幾條測試要驗的是『這個副本自己的 verify/』,不是外層那一個)。"""
    env = dict(os.environ)
    env["AC_ROOT"] = HERE
    env.update(overrides)
    return env


@contextlib.contextmanager
def no_ac_root():
    """import 進行程時暫時拿掉 AC_ROOT,讓 `_find_root()` 走到往上找
    `board/config.json` 那一段 —— 不這樣做,外層(land.sh)留在 os.environ 裡的
    AC_ROOT 會讓 override 分支先短路,fixture 造的假專案永遠不會被走到。"""
    had = "AC_ROOT" in os.environ
    old = os.environ.pop("AC_ROOT", None)
    try:
        yield
    finally:
        if had:
            os.environ["AC_ROOT"] = old

class VerifyRunner(unittest.TestCase):
    def test_list_and_tag_filter_and_full_run(self):
        self.assertEqual(subprocess.run(RUN + ["--list"], capture_output=True, text=True, env=local_env()).returncode, 0)
        self.assertEqual(subprocess.run(RUN + ["--tag", "example"], capture_output=True, env=local_env()).returncode, 0)
        self.assertEqual(subprocess.run(RUN + ["--tag", "no-such-tag"], capture_output=True, env=local_env()).returncode, 3)
        self.assertEqual(subprocess.run(RUN, capture_output=True, env=local_env()).returncode, 0)

    def test_unit_layer_without_config_is_loud_not_green(self):
        r = subprocess.run(RUN + ["--unit"], capture_output=True, text=True, env=local_env())
        self.assertIn(r.returncode, (0, 3), r.stdout)
        if r.returncode == 3:
            self.assertIn("未設定", r.stdout)

    def test_a_case_without_registered_tags_is_refused(self):
        p = os.path.join(HERE, "verify/example/test_zz_unregistered.py")
        with open(p, "w") as f:
            f.write("import unittest\nTAGS=['not-registered']\nclass T(unittest.TestCase):\n    def test_x(self): pass\n")
        try:
            r = subprocess.run(RUN + ["--list"], capture_output=True, text=True, env=local_env())
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("標籤未登記", r.stdout)
        finally:
            os.remove(p)

class RunScopedCache(unittest.TestCase):
    """同一輪裡,同一組標籤、同一個 sha 只跑一次(D-015)。

    2026-09-21 外部審查的 token 帳:「同一組 tag 回歸可能被 worker、驗證者、gate
    重跑。」驗證者那一份已經拿掉了,剩下 worker 交付前那一次與閘門那一次 ——
    同一份程式、同一組標籤、同一個 commit,兩份一模一樣的綠。
    """

    def run_with(self, root, run_id, *extra):
        # `root` 只是票根(AC_ROOT):快取檔寫去它的 reports/。案例與被測檔來自
        # scripts/verify.py 自己所在的那棵樹(HERE),所以假專案不需要自己的 verify/(#55)。
        env = dict(os.environ)
        env.update({"AC_TICKET": "77", "AC_RUN_ID": run_id, "AC_ROOT": root})
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
        """單獨跑的人拿到的永遠是真的跑。"""
        env = local_env()
        env.pop("AC_RUN_ID", None)
        env.pop("AC_TICKET", None)
        done = subprocess.run(RUN + ["--tag", "example"], capture_output=True,
                              text=True, env=env, timeout=300)
        self.assertNotIn("用快取", done.stdout)

    def test_the_second_call_still_scopes_by_run_with_an_external_ac_root_present(self):
        """`run_with()` 明確把 `AC_ROOT` 蓋成 `root`(#32 第 2 輪)—— 即使外層
        (模擬 land.sh)已經設了另一個 `AC_ROOT`,子行程收到的還是 `run_with()` 給的
        那一個,不是外層繼承下來的。"""
        outer = os.environ.get("AC_ROOT")
        os.environ["AC_ROOT"] = HERE  # 模擬 land.sh 全套跑時,行程environ 裡已經有 AC_ROOT
        try:
            with tempfile.TemporaryDirectory() as home:
                first = self.run_with(home, "r-outer-ac-root")
                self.assertEqual(first.returncode, 0, first.stdout)
                self.assertNotIn("用快取", first.stdout)
                second = self.run_with(home, "r-outer-ac-root")
                self.assertEqual(second.returncode, 0, second.stdout)
                self.assertIn("用快取", second.stdout)
        finally:
            if outer is None:
                os.environ.pop("AC_ROOT", None)
            else:
                os.environ["AC_ROOT"] = outer


class CodeRootIsNotTheTicketRoot(unittest.TestCase):
    """#55:land.sh 在票 worktree 裡帶 `AC_ROOT=<主 repo>` 跑閘門,verify.py 子行程繼承
    它 —— 舊版的 `_find_root()` 先讀 `AC_ROOT`,於是 worktree 的閘門跑的是主 repo 的
    `verify/`,worktree 裡的紅看不見。兩個根分開:程式碼根跟檔案走,`AC_ROOT` 只管
    票、事件與 reports。"""

    def test_ac_root_elsewhere_still_runs_the_cases_beside_this_file(self):
        """程式碼根是這支檔案所在的那棵樹(#55)。

        A1:把 HERE 的必要部分拷到 tmp,tmp 那份 example 案例改成紅(字串由這裡寫);
        `AC_ROOT=HERE` 跑 tmp 的 verify.py —— 紅只可能來自 tmp。HERE 的 verify/ 不動。

        **變異 M1**:`_find_root()` 放回 `AC_ROOT` override 分支 → 紅(跑到 HERE 那份綠
        案例,rc 0)。
        """
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("scripts/verify.py", "scripts/status.py", "scripts/event.py",
                        "board/config.json"):
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                shutil.copy(os.path.join(HERE, rel), os.path.join(tmp, rel))
            shutil.copytree(os.path.join(HERE, "verify"), os.path.join(tmp, "verify"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            with open(os.path.join(tmp, "verify", "example", "test_example.py"), "w",
                      encoding="utf-8") as f:
                f.write("import unittest\nTAGS = ['example']\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_red(self):\n"
                        "        self.fail('copy-only red')\n")
            env = local_env()
            env.pop("AC_TICKET", None)
            env.pop("AC_RUN_ID", None)
            done = subprocess.run(
                [sys.executable, os.path.join(tmp, "scripts", "verify.py"),
                 "--tag", "example", "--no-cache"],
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
            env.update({"AC_TICKET": "77", "AC_RUN_ID": run_id, "AC_ROOT": tmp2})
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


class RootFindsProjectRootWhenSyncedToControl(unittest.TestCase):
    """`scripts/sync-to-project.sh` 把這支同步到 `<專案>/scripts/control/verify.py`
    之後,寫死 `dirname(dirname(__file__))` 算出的是 `<專案>/scripts`,不是專案根 ——
    `verify/` 找不到、`registered_tags()` 回空集合(#32)。ROOT 要跟 `scripts/event.py`
    的 `repo_root()` 一樣:往上找 `board/config.json`,兩個位置都要算出同一個根。
    """

    def _fake_project(self, tmp, tag):
        os.makedirs(os.path.join(tmp, "board"))
        with open(os.path.join(tmp, "board", "config.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.makedirs(os.path.join(tmp, "verify"))
        with open(os.path.join(tmp, "verify", "TAGS.md"), "w", encoding="utf-8") as f:
            f.write("- `%s` — fixture\n" % tag)

    def _load_verify_copied_to(self, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "verify.py")
        shutil.copy(os.path.join(HERE, "scripts", "verify.py"), dest)
        spec = importlib.util.spec_from_file_location("verify_under_test_%d" % id(dest), dest)
        mod = importlib.util.module_from_spec(spec)
        # import 這一刻就是 `ROOT = _find_root()` 執行的那一刻 —— 外層(land.sh 全套)
        # 留在 os.environ 的 AC_ROOT 會讓 override 分支先短路,fixture 的假專案永遠不會
        # 被走到,這支測試就測不到往上找 board/config.json 那段邏輯(#32 第 2 輪)。
        with no_ac_root():
            spec.loader.exec_module(mod)
        return mod

    def test_registered_tags_found_at_scripts_control(self):
        with tempfile.TemporaryDirectory() as fake:
            self._fake_project(fake, "example-fake")
            mod = self._load_verify_copied_to(os.path.join(fake, "scripts", "control"))
            self.assertIn("example-fake", mod.registered_tags(),
                          "registered_tags() 在 scripts/control/verify.py 這個位置該找得到 <fake>/verify/TAGS.md")

    def test_scripts_and_scripts_control_agree_on_the_same_root(self):
        """放在 `<root>/scripts/` 與 `<root>/scripts/control/` 兩處都要算出同一個 root。"""
        with tempfile.TemporaryDirectory() as fake:
            self._fake_project(fake, "example-fake")
            at_scripts = self._load_verify_copied_to(os.path.join(fake, "scripts"))
            at_control = self._load_verify_copied_to(os.path.join(fake, "scripts", "control"))
            self.assertEqual(os.path.realpath(at_scripts.ROOT), os.path.realpath(at_control.ROOT))
            self.assertIn("example-fake", at_scripts.registered_tags())
            self.assertIn("example-fake", at_control.registered_tags())


if __name__ == "__main__":
    unittest.main()
