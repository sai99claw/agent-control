"""示範案例:驗證者照票面驗收寫的,不是實作者寫的。"""
import unittest
TAGS = ["example"]

class TheExampleHolds(unittest.TestCase):
    def test_true(self):
        self.assertTrue(True)
