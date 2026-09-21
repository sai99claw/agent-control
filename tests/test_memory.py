"""`scripts/memory.py`:模型私有記憶的上限與整理 — D-006。

上限管的是**每個新 session 的第一口空氣**。這一組釘三件事:量的是正文不是整份檔、
超過會開票而且只開一張、提高上限要有理由。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

FRONT = "---\ncap_chars: %d\ncap_history: []\n---\n"


class MemoryCheck(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def note(self, model, body, cap=None):
        head = FRONT % cap if cap else ""
        return self.write(os.path.join("memory", "model", "%s.md" % model), head + body)

    def test_a_file_inside_its_cap_is_quiet_and_green(self):
        self.note("opus", "一條短短的教訓。\n")
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/model/opus.md", done.stdout)
        self.assertNotIn("超過", done.stdout)
        self.assertEqual(self.kinds(), [])

    def test_going_over_emits_the_event_and_opens_one_consolidation_ticket(self):
        """**變異**:把 `if chars <= cap` 改成 `if chars <= cap * 100` → 這一條紅。"""
        self.note("opus", "坑" * 2500)
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("超過", done.stdout)
        rows = [row for row in self.events() if row["kind"] == "memory.over_cap"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "memory/model/opus.md")
        self.assertEqual(rows[0]["chars"], 2500)
        self.assertEqual(rows[0]["cap"], 2000)

        row = self.load_ticket("1")
        self.assertEqual(row["role"], "consolidator")
        self.assertEqual(row["model"], "fable", "整理要派給 config 指定的高階模型")
        self.assertEqual(sorted(row["allowed_write_paths"]),
                         ["memory/model/opus.inbox.md", "memory/model/opus.md"])

    def test_the_consolidation_ticket_names_two_different_models(self):
        """**變異**:把 `consolidators()` 改回只讀單數的 `memory.consolidator` → 這一條紅。

        D-013:整理不准由單一 session 自己做 —— 一個 session 最先刪掉的是它自己看不懂
        的那幾條,而那正是別的模型看得出價值的那幾條。
        """
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["consolidators"] = ["fable", "codex:gpt-6-astra"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.note("opus", "坑" * 2500)
        self.memory("check")
        row = self.load_ticket("1")
        self.assertIn("fable", row["model"])
        self.assertIn("codex:gpt-6-astra", row["model"])
        text = json.dumps(row, ensure_ascii=False)
        self.assertIn("兩個不同的模型", text)
        self.assertIn("不直接寫案例", text, "整理的產出是原則,不是案例(D-013)")

    def test_a_role_card_over_the_cap_is_watched_too(self):
        """D-013:上限適用**任一層記憶**,不只模型私有那一層。"""
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["applies_to"] = ["memory/model/*.md", "memory/role/*.md"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("memory/role/implementer.md", "規矩" * 2500)
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory/role/implementer.md", done.stdout)

    def test_the_second_run_does_not_open_a_second_ticket(self):
        """**變異**:把 `open_consolidation()` 改成永遠回 None → 這一條紅。"""
        self.note("opus", "坑" * 2500)
        self.memory("check")
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("已經有一張開著的整理票 #1", done.stdout)
        self.assertFalse(self.exists("tickets/2.json"))

    def test_a_cap_on_the_file_itself_beats_the_one_in_the_config(self):
        """提高過的上限寫在**那份檔自己身上**,下一個要再提高的人看得到上一次的理由。"""
        self.note("opus", "坑" * 2500, cap=2600)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("檔上的上限", done.stdout)

    def test_the_front_matter_itself_is_not_counted(self):
        """**變異**:把 `body_chars` 改成 `len(text)`(把 front matter 算進去)
        → 這一條紅。

        為什麼這件事重要:算進去的話,**寫一次提高上限的理由,就把自己往上限推近
        一步** —— 誠實記錄會被制度懲罰。
        """
        reason = "四條 land 事故的反例各不相同,合併會失去可辨識性" * 8
        head = ("---\ncap_chars: 2000\ncap_history:\n"
                "  - {date: 2026-09-12, from: 1000, to: 2000, by: fable, reason: \"%s\"}\n"
                "---\n" % reason)
        body = "坑" * 1990
        self.assertGreater(len(head) + len(body), 2000, "沒有造出「整份超過」的情況")
        self.write("memory/model/opus.md", head + body)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("1990 / 2000", done.stdout)

    def test_a_readme_in_the_memory_directory_is_not_a_memory(self):
        """`memory/model/README.md` 躺在同一個目錄、副檔名也對,所以 glob 抓得到它
        —— 但它是說明檔,不是誰的「第一口空氣」。量它量出來的數字沒有人要用,而一張
        「整理 README」的票會真的被派出去。

        **變異**:把 `os.path.basename(rel) == NOT_A_MEMORY` 那一格拿掉 → 這一條紅。
        """
        self.write("memory/model/README.md", "說明" * 2500)
        self.note("opus", "短的\n")
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("README", done.stdout, "README 根本不該被量")
        self.assertEqual(self.events(), [], "不該發 memory.over_cap")
        self.assertFalse(self.exists("tickets/1.json"), "不該開整理票")

    def test_the_inbox_file_is_not_capped(self):
        """整理期間的新筆記寫 `.inbox.md`,不受上限(docs/MEMORY.md 第 4 點)。"""
        self.note("opus", "短的\n")
        self.write("memory/model/opus.inbox.md", "新筆記" * 3000)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("inbox", done.stdout)


class Consolidate(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def talk(self, name="discussions/2026-09-12-memory-opus.md", conclusion=True):
        body = ("---\ntopic: opus 的記憶超過上限\nkind: memory-consolidation\n"
                "parties: [fable / 老師, opus / 學生]\n---\n\n"
                "## 第 1 輪 — fable\n- 主張:四條可以合併成兩條\n")
        if conclusion:
            body += "\n## 結論\n- 結論:上限提高到 2600\n- 採用的證據:實測\n"
        self.write(name, body)
        return name

    def test_consolidate_help_prints_its_flags_and_a_pasteable_example(self):
        done = self.memory("consolidate", "--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for flag in ("--discussion", "--new-cap", "--reason", "--by"):
            self.assertIn(flag, done.stdout)
        self.assertIn("python3 scripts/memory.py consolidate", done.stdout)

    def test_an_unknown_flag_lists_the_ones_it_does_know(self):
        done = self.memory("consolidate", "memory/model/opus.md", "--talk", "x.md")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--discussion", done.stderr)
        self.assertIn("--help", done.stderr)

    def test_consolidating_without_a_discussion_file_is_refused(self):
        """「討論過了」與「沒討論就刪了」在結果檔案上長得一模一樣 —— 兩者都是一份
        變短的記憶。唯一分得開的東西是那份紀錄(D-007)。

        **變異**:把 `if not discussion.strip()` 那一段拿掉 → 這一條紅。
        """
        self.write("memory/model/opus.md", FRONT % 2000 + "坑\n")
        done = self.memory("consolidate", "memory/model/opus.md")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--discussion", done.stderr)
        self.assertIn("坑", self.read("memory/model/opus.md"), "被拒絕就不該動到檔案")

    def test_a_discussion_path_that_is_not_there_is_refused_in_its_own_words(self):
        self.write("memory/model/opus.md", FRONT % 2000 + "坑\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", "discussions/沒有這一份.md")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("找不到討論檔", done.stderr)

    def test_a_discussion_with_no_conclusion_yet_is_a_different_sentence(self):
        """一份沒有結論的討論檔,與一場沒談完的討論長得一樣。而下一步差很多:
        去開一份 vs 回去把結論寫完(§5.7:守衛給錯下一步比沒有守衛更糟)。

        **變異**:把 `"## 結論" not in talk_text` 那一段拿掉 → 這一條紅。
        """
        self.write("memory/model/opus.md", FRONT % 2000 + "坑\n")
        name = self.talk(conclusion=False)
        done = self.memory("consolidate", "memory/model/opus.md", "--discussion", name)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("還沒有結論區", done.stderr)
        self.assertNotIn("找不到討論檔", done.stderr)

    def test_raising_the_cap_without_a_reason_is_refused(self):
        """提高是掙來的。一個沒有理由的 `cap_chars: 4000` 與一場真的做過的討論長得
        一模一樣,而前者是把「還沒整理」重新命名成「上限比較高」。

        **變異**:把 `if new_cap is not None and not reason.strip()` 拿掉 → 這一條紅。
        """
        self.write("memory/model/opus.md", FRONT % 2000 + "坑\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", self.talk(), "--new-cap", "4000")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("提高上限是掙來的", done.stderr)
        self.assertIn("cap_chars: 2000", self.read("memory/model/opus.md"),
                      "被拒絕的提高不該留下痕跡")

    def test_a_reasoned_raise_is_written_into_the_file_with_its_history(self):
        self.write("memory/model/opus.md", FRONT % 2000 + "坑\n")
        name = self.talk()
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", name, "--new-cap", "2600", "--by", "fable",
                           "--reason", "四條反例各不相同,合併會失去可辨識性")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read("memory/model/opus.md")
        self.assertIn("cap_chars: 2600", text)
        self.assertIn("from: 2000, to: 2600", text)
        self.assertIn("by: fable", text)
        self.assertIn("discussion: %s" % name, text,
                      "cap_history 那一列要指得回支撐它的討論(D-007)")
        self.assertIn("失去可辨識性", text)
        rows = [row for row in self.events() if row["kind"] == "memory.consolidated"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cap_from"], 2000)
        self.assertEqual(rows[0]["cap_to"], 2600)
        self.assertEqual(rows[0]["discussion"], name)

    def test_the_inbox_is_merged_in_and_then_gone(self):
        self.write("memory/model/opus.md", FRONT % 2000 + "舊的一條\n")
        self.write("memory/model/opus.inbox.md", "整理期間新記的一條\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", self.talk())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read("memory/model/opus.md")
        self.assertIn("舊的一條", text)
        self.assertIn("整理期間新記的一條", text)
        self.assertFalse(self.exists("memory/model/opus.inbox.md"),
                         "併過的 inbox 要收掉,不然下一輪會再併一次")

    def test_still_over_the_cap_after_consolidating_is_not_reported_as_done(self):
        """「整理完了」與「整理完還是超過」不能都是退出碼 0。"""
        self.write("memory/model/opus.md", FRONT % 2000 + "坑" * 2500)
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", self.talk())
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("還是超過上限", done.stdout)


if __name__ == "__main__":
    unittest.main()
