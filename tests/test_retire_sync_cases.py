"""Canon rules come from `$AC_HOME`, the project layer from `<AC_ROOT>/memory` (D-071 C5, row 6).

The sync machinery used to copy A's cards into `<project>/docs/roles/` and `rules.roles_dir`
pointed there. With the sync gone, `rules.py` reads the canon cards from `$AC_HOME/memory/{role,model}`
and decides "this repo is A itself" by `realpath(root) == realpath(AC_HOME)`.

## Cases (oracle: the ticket text, not the program under test)
- a card only the project has (AC_HOME lacks it) -> pack refuses (rc != 0, stdout empty) and names
  the card on stderr; it does not quietly pack the project's copy as if it were canon.
- AC_HOME unset -> rc 2 and a stderr line naming AC_HOME.
- canon card, then a `### 本專案` heading, then the project's card (in that order).
- AC_HOME given as a symlink to the root -> canon: the card once, no `### 本專案` heading
  (a plain string compare would see two different paths and stack the card twice).
- `pack main` in A itself: AC_ROOT unset, cwd = A (a git tree), AC_HOME = A -> canon, rc 0.

Every fixture dir is made here; the script under test is the one `control_harness.SCRIPTS` names
(so mutation runs swap it). No process is stopped by signal; every run has `timeout=`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, SCRIPTS  # noqa: E402

LOCAL_HEADING = "### " + "本專案"


class RulesCanonFromAcHome(unittest.TestCase):

    CANON = "CANON-MARK the canon verifier card says this."
    LOCAL = "LOCAL-MARK the project's own verifier card says this."
    ONLY_LOCAL = "ONLY-LOCAL-MARK a card AC_HOME does not have."
    MODEL = "MODEL-MARK the canon opus memory says this."

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="retire-sync-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def make_tree(self, name, cards=None, models=None, git=False):
        """A dir holding board/config.json, the shared rules source and the given cards."""
        where = os.path.join(self.home, name)
        os.makedirs(os.path.join(where, "board"))
        os.makedirs(os.path.join(where, "docs"))
        with open(os.path.join(where, "board", "config.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        shutil.copy(os.path.join(ROOT, "docs", "DISPATCH-TEMPLATE.md"),
                    os.path.join(where, "docs", "DISPATCH-TEMPLATE.md"))
        for kind, files in (("role", cards or {}), ("model", models or {})):
            os.makedirs(os.path.join(where, "memory", kind), exist_ok=True)
            for card, text in files.items():
                with open(os.path.join(where, "memory", kind, card), "w", encoding="utf-8") as handle:
                    handle.write("# card\n- %s\n" % text)
        if git:
            done = subprocess.run(["git", "init", "-q", where], env=self.env(),
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, "fixture broke: git init\n" + done.stderr)
        return where

    def env(self, **extra):
        base = {key: value for key, value in os.environ.items()
                if not key.startswith("AC_")
                and key not in ("CLAUDE_CODE_REMOTE", "CLAUDE_PROJECT_DIR")}
        base.update({"HOME": self.home, "GIT_CONFIG_GLOBAL": os.devnull,
                     "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_TERMINAL_PROMPT": "0"})
        base.update(extra)
        return base

    def pack(self, cwd, env, role="verifier", model="opus"):
        args = ["python3", os.path.join(SCRIPTS, "rules.py"), "pack", role]
        if model:
            args += ["--model", model]
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=120)

    def canon_home(self, name="ac-home", git=False):
        return self.make_tree(name, cards={"verifier.md": self.CANON},
                              models={"opus.md": self.MODEL}, git=git)

    def test_a_card_only_the_project_has_is_refused_not_packed_as_canon(self):
        """AC_HOME has no memory/role/verifier.md; the project does -> refuse and name it."""
        home = self.make_tree("ac-home", models={"opus.md": self.MODEL})
        project = self.make_tree("project", cards={"verifier.md": self.ONLY_LOCAL},
                                 models={"opus.md": self.MODEL})
        done = self.pack(project, self.env(AC_HOME=home, AC_ROOT=project))
        self.assertNotEqual(done.returncode, 0,
                            "packed without a canon card:\n" + done.stdout[:2000])
        self.assertNotIn(self.ONLY_LOCAL, done.stdout, "the project card was packed as canon")
        self.assertEqual(done.stdout, "", "a refused pack prints no half pack")
        self.assertTrue([line for line in done.stderr.splitlines() if "verifier.md" in line],
                        "stderr does not say which card is missing:\n" + done.stderr)

    def test_ac_home_unset_is_rc_2_and_named(self):
        project = self.make_tree("project", cards={"verifier.md": self.LOCAL},
                                 models={"opus.md": self.MODEL})
        done = self.pack(project, self.env(AC_ROOT=project))
        self.assertEqual(done.returncode, 2, done.stdout[:2000] + done.stderr)
        self.assertIn("AC_HOME", done.stderr)
        self.assertNotIn(self.LOCAL, done.stdout)

    def test_canon_then_the_project_heading_then_the_project_card(self):
        home = self.canon_home()
        project = self.make_tree("project", cards={"verifier.md": self.LOCAL})
        done = self.pack(project, self.env(AC_HOME=home, AC_ROOT=project))
        self.assertEqual(done.returncode, 0, done.stderr)
        out = done.stdout
        for needle in (self.CANON, LOCAL_HEADING, self.LOCAL, self.MODEL):
            self.assertIn(needle, out)
        self.assertLess(out.index(self.CANON), out.index(LOCAL_HEADING))
        self.assertLess(out.index(LOCAL_HEADING), out.index(self.LOCAL))
        self.assertEqual(out.count(self.CANON), 1)

    def test_ac_home_as_a_symlink_to_the_root_is_canon(self):
        """realpath compare: AC_HOME = link -> root, AC_ROOT = root -> one layer only."""
        home = self.canon_home()
        link = os.path.join(self.home, "ac-home-link")
        os.symlink(home, link)
        done = self.pack(home, self.env(AC_HOME=link, AC_ROOT=home))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count(self.CANON), 1, done.stdout[:3000])
        self.assertNotIn(LOCAL_HEADING, done.stdout)

    def test_pack_main_in_a_itself_without_ac_root(self):
        """cwd = A (a git tree), AC_ROOT unset, AC_HOME = A -> canon pack, no project layer."""
        home = self.make_tree("ac-home", cards={"main.md": self.CANON}, git=True)
        done = self.pack(home, self.env(AC_HOME=home), role="main", model="")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count(self.CANON), 1, done.stdout[:3000])
        self.assertNotIn(LOCAL_HEADING, done.stdout)


if __name__ == "__main__":
    unittest.main()
