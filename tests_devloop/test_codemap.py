"""codemap.build: test files by path only, no private module functions"""

import os
import subprocess
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loop_harness import LOOP, GitIsolatedCase  # noqa: E402

from core import codemap  # noqa: E402


class CodeMapBuild(GitIsolatedCase):
    def setUp(self):
        super().setUp()
        self.tree = tempfile.mkdtemp(prefix="codemap-")
        self.addCleanup(shutil.rmtree, self.tree, True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.tree, check=True)
        files = {
            "tests/test_a.py": '"""Alpha tests."""\n\nclass Alpha:\n    """A class."""\n\n    def check(self):\n        """Checks."""\n\ndef run_it(x):\n    """Runs."""\n',
            "pkg/test_b.py": '"""Beta."""\n\ndef b():\n    pass\n',
            "tests/helper.py": '"""Helps."""\n\nclass Help:\n    """Helper class."""\n\n    def go(self, n):\n        """Goes."""\n\ndef util(y):\n    """Utility."""\n',
            "pkg/mod.py": '"""Mod."""\n\ndef _private(x):\n    """Hidden."""\n\ndef public(a, *r):\n    """Shown."""\n',
            "pkg/run.sh": "#!/bin/sh\n# Runs it.\necho hi\n",
        }
        for rel, text in files.items():
            path = os.path.join(self.tree, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
        subprocess.run(["git", "add", "-A"], cwd=self.tree, check=True)
        self.text = codemap.build(self.tree)

    def test_test_files_are_listed_by_path_only(self):
        self.assertTrue(self.text.endswith("## tests/test_a.py\n"))
        self.assertIn("## pkg/test_b.py\n\n", self.text)
        for gone in ("Alpha", "check", "run_it", "Beta", "`b(", "tests/test_a.py -"):
            self.assertNotIn(gone, self.text)

    def test_other_files_under_a_test_root_are_listed_as_before(self):
        self.assertIn("## tests/helper.py - Helps.\n- class `Help` Helper class.\n"
                      "  - `go(n)` Goes.\n- `util(y)` Utility.\n\n", self.text)

    def test_private_module_functions_are_not_listed(self):
        self.assertIn("## pkg/mod.py - Mod.\n- `public(a, *r)` Shown.\n\n", self.text)
        self.assertNotIn("_private", self.text)

    def test_other_source_files_and_header_are_unchanged(self):
        self.assertIn("## pkg/run.sh - Runs it.\n", self.text)
        self.assertTrue(self.text.startswith(codemap.HEADER))
