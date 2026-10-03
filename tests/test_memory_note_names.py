"""`memory.py note` 的名稱與重送 — #75。

`note` 把 `name + 後綴` 串起來:`implementer.md` 會落成 `implementer.md.inbox.md`,
讀端只認 `implementer.inbox.md`,那一條教訓沒有讀者。auto-fix 每輪 harvest 同一份
EVIDENCE,同一條會再寫一次。期望值都照票面寫死,不從 memory.py 算。
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


class NoteNames(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def lines(self, rel):
        return self.read(rel).splitlines()

    def noted(self):
        return [row for row in self.events() if row["kind"] == "memory.noted"]

    def snapshot(self):
        found = {}
        for top in ("memory", "tickets"):
            for base, _, files in os.walk(os.path.join(self.repo, top)):
                for name in files:
                    path = os.path.join(base, name)
                    with open(path, "rb") as handle:
                        found[os.path.relpath(path, self.repo)] = \
                            hashlib.sha256(handle.read()).hexdigest()
        events = os.path.join(self.repo, "board", "events.jsonl")
        if os.path.exists(events):
            with open(events, "rb") as handle:
                found["board/events.jsonl"] = hashlib.sha256(handle.read()).hexdigest()
        return found

    def test_a_name_with_or_without_md_lands_in_the_one_file(self):
        """四問:守實際落檔路徑(帶不帶 .md 同一份)與事件/stdout 的名稱;可信回歸是多接
        一個副檔名;既有案例只用不帶 .md 的名稱;無測試專用縫。

        **變異** M1:`note_name` 不去 `.md`(回 `name`)→ 這一條紅(落成
        `implementer.md.inbox.md`)。M4:作者檢查改用原始名稱比 → `opus.md` 被拒,紅。
        """
        cases = [
            ("role", "implementer", "implementer.md", "memory/role/implementer.inbox.md"),
            ("model", "opus", "opus.md", "memory/model/opus.inbox.md"),
            ("project", "gate", "gate.md", "memory/project/gate.md"),
        ]
        for layer, bare, with_md, target in cases:
            for index, name in enumerate((bare, with_md)):
                done = self.memory("note", layer, name, "%s 第%d條" % (layer, index),
                                   "--ticket", "2", "--by", "worker@opus")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(done.stdout, "memory: noted %s/%s\n" % (layer, bare))
            self.assertEqual(len(self.lines(target)), 2, target)
        strays = [os.path.join(base, name)
                  for base, _, files in os.walk(os.path.join(self.repo, "memory"))
                  for name in files
                  if name.endswith(".md.inbox.md") or name.endswith(".md.md")]
        self.assertEqual(strays, [])
        self.assertEqual([row["name"] for row in self.noted()],
                         ["implementer"] * 2 + ["opus"] * 2 + ["gate"] * 2)

    def test_an_ambiguous_name_is_refused_and_nothing_moves(self):
        """四問:守「不合法的名稱什麼都不寫」與錯誤訊息給可重試的名稱;可信回歸是放過
        inbox 後綴、寫出孤兒檔;既有案例沒有名稱規則;無測試專用縫。

        **變異** M3:`note_name` 不擋 `.inbox` 結尾 → `implementer.inbox` 與
        `implementer.inbox.md` 兩格紅。
        """
        self.write("memory/role/implementer.inbox.md", "- 原有的一條 (2026-10-01, worker@opus)\n")
        retry = ("implementer.inbox.md", "implementer.inbox", "implementer.md.md")
        for name in retry + ("a/b", "..", ".", ""):
            with self.subTest(name=name):
                before = self.snapshot()
                done = self.memory("note", "role", name, "一條", "--by", "worker@opus")
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn("名稱不合法", done.stderr)
                if name in retry:
                    self.assertIn("改用 implementer", done.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_the_same_note_twice_is_written_once(self):
        """四問:守來源可追溯(不同票各一行)與同來源不重寫;可信回歸是錯誤去重吞掉
        不同票、或 harvest 把重送記成拒絕;既有案例都是不同正文或單次寫入;無測試專用縫。

        **變異** M2:`already_noted` 恆回 False → 這一條紅(2 行 / 2 筆事件)。
        """
        target = "memory/role/implementer.inbox.md"
        args = ("note", "role", "implementer", "同一條原則", "--by", "worker@opus")
        first = self.memory(*args, "--ticket", "2")
        again = self.memory(*args, "--ticket", "2")
        self.assertEqual((first.returncode, again.returncode), (0, 0),
                         first.stderr + again.stderr)
        self.assertIn("已有同一條", again.stdout)
        self.assertEqual(len(self.lines(target)), 1)
        self.assertEqual(len(self.noted()), 1)
        other = self.memory(*args, "--ticket", "3")
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        lines = self.lines(target)
        self.assertEqual(len(lines), 2)
        self.assertIn("#2", lines[0])
        self.assertIn("#3", lines[1])

        evidence = self.write("EVIDENCE.md", "## 記憶\n"
                              "memory.py note project gate \"收割一次就好\" "
                              "--ticket 5 --by worker@opus\n")
        for _ in range(2):
            done = self.memory("harvest", evidence)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertNotIn("拒絕 evidence 行", done.stderr)
        self.assertEqual(len(self.lines("memory/project/gate.md")), 1)

    def test_another_models_writer_is_still_refused(self):
        """四問:守「model 層只寫自己的」不因正規化放寬;可信回歸是名稱去 `.md` 後作者
        檢查被繞過;既有案例只測不帶 .md 的名稱;無測試專用縫。
        """
        before = sorted(os.listdir(os.path.join(self.repo, "memory", "model")))
        done = self.memory("note", "model", "opus.md", "x", "--by", "worker@fable")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("只能由同模型寫入", done.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, "memory", "model"))),
                         before)


if __name__ == "__main__":
    unittest.main()
