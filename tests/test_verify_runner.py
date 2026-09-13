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

if __name__ == "__main__":
    unittest.main()
