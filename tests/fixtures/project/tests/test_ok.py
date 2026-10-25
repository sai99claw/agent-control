"""The fixture project's own unit test: green, so a run that reaches it has a pass row."""
import unittest


class T(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1 + 1, 2)
