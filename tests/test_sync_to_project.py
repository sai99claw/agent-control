import os, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

    def test_refuses_a_missing_project_dir(self):
        r = subprocess.run(["sh", os.path.join(HERE, "scripts/sync-to-project.sh"), "/nonexistent/x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

if __name__ == "__main__":
    unittest.main()
