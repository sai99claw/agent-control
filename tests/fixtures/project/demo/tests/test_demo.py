"""A second unittest root (`tests.unittest.roots`), the shape of a project's demo/ tests."""
import unittest


class Demo(unittest.TestCase):
    def test_demo(self):
        self.assertTrue("demo")
