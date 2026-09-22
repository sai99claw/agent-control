"""回歸層:每票一行的數字守的是兩條不變式(#18,D-017 ①)。

單元層問的是「這一行印得對不對」;這裡問的是**兩件事不准被揉成一件**,而那兩件在
任何一次改寫裡都會先壞:

1. `env_runs + product_runs == red_runs` —— 少一格就是有一種紅沒有被歸類,而
   「環境爛了」與「程式錯了」的下一步完全不同。
2. 一趟都問不出來的 token 印 `未知`,不印 `0` —— `0` 與「這張票很省」長得一樣。
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import metrics  # noqa: E402

TAGS = ["metrics"]


def put(where, ident, run_id, **fields):
    data = {"state": "done", "run_id": run_id, "kind": "gate", "ticket": str(ident),
            "rc": 0, "environment_suspect": {}, "duration_seconds": 0, "round": 1}
    data.update(fields)
    target = os.path.join(where, "reports", "t%s" % ident, run_id)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "status.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return target


class MetricsLine(unittest.TestCase):

    def line(self, where, ident):
        return metrics.line_of(metrics.measure(where, str(ident), {}))

    def test_every_red_run_lands_in_exactly_one_bucket(self):
        with tempfile.TemporaryDirectory() as where:
            put(where, "1", "r1", rc=0)
            put(where, "1", "r2", rc=1, environment_suspect={"engine": "safari"})
            put(where, "1", "r3", rc=1)
            put(where, "1", "r4", rc=2)
            row = metrics.measure(where, "1", {})
            self.assertEqual(row["red_runs"], 3)
            self.assertEqual(row["env_runs"] + row["product_runs"], row["red_runs"],
                             "有一種紅沒有被歸類:%s" % metrics.line_of(row))

    def test_a_trip_nobody_recorded_is_unknown_and_never_zero(self):
        with tempfile.TemporaryDirectory() as where:
            target = put(where, "1", "r1", rc=0)
            with open(os.path.join(target, "worker-round1.log"), "w",
                      encoding="utf-8") as handle:
                handle.write("worker 跑完了,沒有人在這裡記過用量。\n")
            line = self.line(where, "1")
            self.assertIn("tokens=未知", line, line)
            self.assertNotIn("tokens=0", line, line)


if __name__ == "__main__":
    unittest.main()
