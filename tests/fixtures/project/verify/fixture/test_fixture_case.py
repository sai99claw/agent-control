"""#0 fixture 專案的回歸案例:回歸層選得到一條、而且是綠的。

## 驗收表(期望值來源獨立於被測程式)
A1 | unit | 跑這一條 | 綠 | fixture 專案用:只證「回歸層選得到案例」

## 介面字串
(沒有;這一條不斷言任何字串)

## 怎麼做假
不上真埠、不起真服務、不殺行程。

## 不做
不改產品碼;不放寬任何票面驗收。
"""
import unittest

TAGS = ["example"]


class T(unittest.TestCase):
    def test_fixture(self):
        """A1 fixture 專案的回歸案例是綠的。"""
        self.assertTrue(True)
