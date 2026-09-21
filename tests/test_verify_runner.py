import os, subprocess, sys, tempfile, unittest, shutil
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = [sys.executable, os.path.join(HERE, "scripts/verify.py")]

class VerifyRunner(unittest.TestCase):
    def test_list_and_tag_filter_and_full_run(self):
        self.assertEqual(subprocess.run(RUN + ["--list"], capture_output=True, text=True).returncode, 0)
        self.assertEqual(subprocess.run(RUN + ["--tag", "example"], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(RUN + ["--tag", "no-such-tag"], capture_output=True).returncode, 3)
        self.assertEqual(subprocess.run(RUN, capture_output=True).returncode, 0)

    def test_unit_layer_without_config_is_loud_not_green(self):
        r = subprocess.run(RUN + ["--unit"], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 3), r.stdout)
        if r.returncode == 3:
            self.assertIn("未設定", r.stdout)

    def test_a_case_without_registered_tags_is_refused(self):
        p = os.path.join(HERE, "verify/example/test_zz_unregistered.py")
        with open(p, "w") as f:
            f.write("import unittest\nTAGS=['not-registered']\nclass T(unittest.TestCase):\n    def test_x(self): pass\n")
        try:
            r = subprocess.run(RUN + ["--list"], capture_output=True, text=True)
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
        env = dict(os.environ)
        env.pop("AC_RUN_ID", None)
        env.pop("AC_TICKET", None)
        done = subprocess.run(RUN + ["--tag", "example"], capture_output=True,
                              text=True, env=env, timeout=300)
        self.assertNotIn("用快取", done.stdout)


if __name__ == "__main__":
    unittest.main()
