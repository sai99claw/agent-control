"""`scripts/memory.py`:記憶的暫存區與整理 — D-053 ⑥(取代 D-006 的字數上限)。

這一組釘三件事:量的是正文不是整份檔(只是報數)、暫存區達批次門檻(≥5 條或最舊滿 3 天,D-055 ①)就開票
而且只開一張、長度本身不觸發也不拒收。
"""

import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import memory as memory_module  # noqa: E402

FRONT = "---\nowner: opus\n---\n"
# #148(D-055 ①):1 條要放滿 3 天才觸發,所以這一條的日期固定是 3 天前
ONE_NOTE = "- 一條原則 (#1, %s, worker@opus)\n" % (date.today() - timedelta(days=3)).isoformat()


class MemoryCheck(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def note(self, model, body):
        return self.write(os.path.join("memory", "model", "%s.md" % model), body)

    def inbox(self, model, body=ONE_NOTE):
        return self.write(os.path.join("memory", "model", "%s.inbox.md" % model), body)

    def test_a_file_with_no_inbox_is_quiet_and_green(self):
        self.note("opus", "一條短短的教訓。\n")
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 9 字元 / 暫存區 0 條未整理\n", done.stdout)
        self.assertEqual(self.kinds(), [])

    def test_a_long_file_with_no_inbox_does_not_trigger(self):
        """D-053 ⑥:長度不再觸發。**變異**:check 對正文 > 2000 字元的主檔也開票 → 這一條紅。"""
        self.note("opus", "坑" * 2500)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 2500 字元 / 暫存區 0 條未整理\n", done.stdout)
        self.assertNotIn("超過", done.stdout)
        self.assertEqual(self.kinds(), [])
        self.assertFalse(self.exists("tickets/1.json"))

    def test_one_old_note_emits_pending_and_opens_one_consolidation_ticket(self):
        """1 條放滿 3 天觸發。**變異**:把觸發改回 `entries > 20` → 這一條紅。"""
        self.note("opus", "短的\n")
        self.inbox("opus")
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 3 字元 / 暫存區 1 條未整理\n", done.stdout)
        rows = [row for row in self.events() if row["kind"] == "memory.pending"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "memory/model/opus.inbox.md")
        self.assertEqual(rows[0]["entries"], 1)

        row = self.load_ticket("1")
        self.assertEqual(row["subject"], "整理 memory/model/opus.md(暫存區 1 條未整理)")
        self.assertEqual(row["role"], "consolidator")
        self.assertEqual(row["model"], "fable", "整理要派給 config 指定的高階模型")
        self.assertEqual(row.get("memory_file"), "memory/model/opus.md")

    def test_the_ticket_gives_the_criterion_and_no_length_target(self):
        """D-053 ⑥:整理者只拿判準一句,寫什麼、留多長由它決定。

        **變異**:`open_ticket_for` 的 objective 拿掉判準句 → 這一條紅。
        """
        self.note("opus", "短的\n")
        self.inbox("opus")
        self.memory("check")
        row = self.load_ticket("1")
        self.assertIn("每個 session 開場都會讀，只留值得這個成本的", row["objective"])
        self.assertIn("寫什麼、留多長由你決定,沒有字數目標", row["objective"])
        text = row["objective"] + "\n".join(row["acceptance"])
        for word in ("上限", "cap_history", "字元"):
            self.assertNotIn(word, text)
        self.assertIn("`memory.py consolidate memory/model/opus.md` 退出碼 0"
                      "(snapshot 那 K 行已移進 `.consumed`)", row["acceptance"])

    def test_the_consolidation_ticket_names_two_different_models(self):
        """**變異**:把 `consolidators()` 改回只讀單數的 `memory.consolidator` → 這一條紅。

        D-013:整理不准由單一 session 自己做 —— 一個 session 最先刪掉的是它自己看不懂
        的那幾條,而那正是別的模型看得出價值的那幾條。
        """
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["consolidators"] = ["fable", "codex:gpt-6-astra"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.inbox("opus")
        self.memory("check")
        row = self.load_ticket("1")
        self.assertIn("fable", row["model"])
        self.assertIn("codex:gpt-6-astra", row["model"])

    def test_the_ticket_says_how_the_two_sessions_get_dispatched(self):
        """A8 / G8:票面以前只寫「由兩個模型討論」,而**兩個 session 怎麼被派、
        討論檔誰先寫**沒有任何一份檔說得出來 —— 一張沒有人知道怎麼執行的票,與沒有
        開票長得一樣。

        **變異**:把 `open_ticket_for()` 裡那一格 `--outline` 拿掉 → 這一條紅。
        """
        self.inbox("opus")
        self.memory("check")
        row = self.load_ticket("1")
        self.assertIn("templates/dispatch-consolidator.md", row.get("outline") or "",
                      "整理票要指得出那一份範本")
        self.assertIn("actl memory consolidate", row.get("outline") or "",
                      "收的那一句要可以直接貼")

    def test_a_role_card_with_an_inbox_is_watched_too(self):
        """D-013:整理適用角色卡與模型卡兩層,不只模型私有那一層。"""
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["applies_to"] = ["memory/model/*.md", "memory/role/*.md"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("memory/role/implementer.md", "規矩" * 2500)
        self.write("memory/role/implementer.inbox.md", ONE_NOTE)
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory: memory/role/implementer.md 5000 字元 / 暫存區 1 條未整理\n",
                      done.stdout)

    def test_the_second_run_does_not_open_a_second_ticket(self):
        """**變異**:把 `open_consolidation()` 改成永遠回 None → 這一條紅。"""
        self.inbox("opus")
        self.memory("check")
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("已經有一張開著的整理票 #1", done.stdout)
        self.assertFalse(self.exists("tickets/2.json"))

    def test_an_old_cap_left_in_the_front_matter_is_just_front_matter(self):
        """D-053 ⑥:檔上殘留的 `cap_chars` 不再是上限 —— 只報正文字元數,不比。"""
        self.note("opus", "---\ncap_chars: 100\n---\n" + "坑" * 500)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 500 字元 / 暫存區 0 條未整理\n", done.stdout)
        self.assertEqual(self.kinds(), [])

    def test_the_front_matter_itself_is_not_counted(self):
        """**變異**:把 `body_chars` 改成 `len(text)`(把 front matter 算進去)
        → 這一條紅。
        """
        head = "---\nowner: opus\nnote: %s\n---\n" % ("中繼資料" * 50)
        body = "坑" * 1990
        self.write("memory/model/opus.md", head + body)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 1990 字元 / 暫存區 0 條未整理\n", done.stdout)

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
        self.assertEqual(self.events(), [], "不該發 memory.pending")
        self.assertFalse(self.exists("tickets/1.json"), "不該開整理票")

    def test_an_inbox_with_only_blank_lines_does_not_trigger(self):
        self.note("opus", "短的\n")
        self.inbox("opus", "\n  \n\n")
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 3 字元 / 暫存區 0 條未整理\n", done.stdout)
        self.assertEqual(self.kinds(), [])
        self.assertFalse(self.exists("tickets/1.json"))

    def test_an_inbox_without_its_main_file_is_still_listed_and_triggers(self):
        """第一條 note 落地時只有 inbox:主檔不存在記 0 字元,照樣待整理。"""
        self.inbox("opus")
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 0 字元 / 暫存區 1 條未整理\n", done.stdout)
        self.assertTrue(self.exists("tickets/1.json"))

    def test_read_only_reports_but_opens_nothing(self):
        self.inbox("opus")
        done = self.memory("check", "--read-only")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("暫存區 1 條未整理", done.stdout)
        self.assertEqual(self.kinds(), [])
        self.assertFalse(self.exists("tickets/1.json"))

    def test_project_memory_is_a_record_and_is_not_capped(self):
        conf = json.loads(self.read("board/config.json"))
        conf["memory"]["applies_to"] = ["memory/model/*.md", "memory/project/*.md"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write("memory/project/decisions.md", "紀錄" * 2500)
        done = self.memory("check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/project/*.md 是 project 紀錄層", done.stdout)
        self.assertFalse(self.exists("tickets/1.json"))


class Consolidate(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def snapshot(self):
        done = self.memory("snapshot", "memory/model/opus.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return dict(line.split(": ", 1) for line in done.stdout.splitlines())

    def talk(self, model="fable", conclusion=True):
        """一份討論檔:檔頭抄 `snapshot` 的兩行,結論區對每一行寫一個處置(#76)。"""
        snap = self.snapshot()
        name = "discussions/2026-09-12-memory-opus-%s.md" % model
        body = ("---\ntopic: opus 的記憶暫存區整理\nkind: memory-consolidation\n"
                "parties: [fable / 老師, opus / 學生]\nmodel: %s\n"
                "source_lines: %s\nsource_sha256: %s\n---\n\n"
                "## 第 1 輪 — %s\n- 主張:四條可以合併成兩條\n"
                % (model, snap["source_lines"], snap["source_sha256"], model))
        if conclusion:
            body += "\n## 結論\n- 結論:四條合併成兩條\n- 採用的證據:實測\n"
            body += "".join("- L%d: 保留\n" % n
                            for n in range(1, int(snap["source_lines"]) + 1))
        self.write(name, body)
        return name

    def both(self):
        """兩個不同 model 的討論檔,`--discussion` 各一次。"""
        a, b = self.talk("fable"), self.talk("opus")
        return ["--discussion", a, "--discussion", b]

    def candidate(self, body):
        self.write("discussions/2026-09-12-memory-opus.candidate.md", body)
        return "discussions/2026-09-12-memory-opus.candidate.md"

    def test_consolidate_help_prints_its_flags_and_a_pasteable_example(self):
        done = self.memory("consolidate", "--help")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for flag in ("--candidate", "--discussion", "--by"):
            self.assertIn(flag, done.stdout)
        for flag in ("--new-cap", "--reason"):
            self.assertNotIn(flag, done.stdout)
        self.assertIn("actl memory consolidate", done.stdout)

    def test_an_unknown_flag_lists_the_ones_it_does_know(self):
        done = self.memory("consolidate", "memory/model/opus.md", "--talk", "x.md")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--discussion", done.stderr)
        self.assertIn("--help", done.stderr)

    def test_consolidating_without_a_discussion_file_is_refused(self):
        """「討論過了」與「沒討論就刪了」在結果檔案上長得一模一樣 —— 兩者都是一份
        變短的記憶。唯一分得開的東西是那份紀錄(D-007)。

        **變異**:把 `if not [one for one in discussions …]` 那一段拿掉 → 這一條紅
        (落到「要兩份」那一句,不說 D-007 的理由)。
        """
        self.write("memory/model/opus.md", FRONT + "坑\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--candidate", self.candidate("新版\n"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--discussion", done.stderr)
        self.assertIn("沒討論就刪了", done.stderr)
        self.assertIn("坑", self.read("memory/model/opus.md"), "被拒絕就不該動到檔案")

    def test_a_discussion_path_that_is_not_there_is_refused_in_its_own_words(self):
        self.write("memory/model/opus.md", FRONT + "坑\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--discussion", "discussions/沒有這一份.md")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("找不到討論檔", done.stderr)

    def test_a_discussion_with_no_conclusion_yet_is_a_different_sentence(self):
        """一份沒有結論的討論檔,與一場沒談完的討論長得一樣。而下一步差很多:
        去開一份 vs 回去把結論寫完(§5.7:守衛給錯下一步比沒有守衛更糟)。

        **變異**:把 `read_discussion` 裡 `if not heading` 那一段拿掉 → 這一條紅。
        """
        self.write("memory/model/opus.md", FRONT + "坑\n")
        name = self.talk("fable", conclusion=False)
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--candidate", self.candidate("新版\n"),
                           "--discussion", name, "--discussion", self.talk("opus"))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("還沒有結論區", done.stderr)
        self.assertNotIn("找不到討論檔", done.stderr)

    def test_the_old_cap_flags_are_unknown_and_touch_nothing(self):
        """D-053 ⑥ 拿掉 `--new-cap` / `--reason`:走既有的不認得參數(rc 2),檔一個位元組都不動。"""
        self.write("memory/model/opus.md", FRONT + "坑\n")
        self.write("memory/model/opus.inbox.md", ONE_NOTE)
        talks = self.both()
        for extra in (["--new-cap", "4000", "--reason", "x"], ["--reason", "x"]):
            done = self.memory("consolidate", "memory/model/opus.md",
                               "--candidate", self.candidate("坑\n"), *talks, *extra)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("不認得 %r" % extra[0], done.stderr)
            self.assertEqual(self.read("memory/model/opus.md"), FRONT + "坑\n")
            self.assertEqual(self.read("memory/model/opus.inbox.md"), ONE_NOTE)
        self.assertEqual([row for row in self.events() if row["kind"] == "memory.consolidated"], [])

    def test_a_long_candidate_is_taken_and_the_event_carries_no_cap(self):
        """D-053 ⑥:候選多長由整理者決定。**變異**:留「候選 > 2000 字元拒收」→ 這一條紅。"""
        self.write("memory/model/opus.md", FRONT + "坑\n")
        self.write("memory/model/opus.inbox.md", ONE_NOTE)
        talks = self.both()
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--candidate", self.candidate("坑" * 5000), *talks, "--by", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 2 -> 5000 字元、消耗 inbox 前 1 行、討論 %s + %s\n"
                      % (talks[1], talks[3]), done.stdout)
        self.assertEqual(self.read("memory/model/opus.md"), FRONT + "坑" * 5000)
        rows = [row for row in self.events() if row["kind"] == "memory.consolidated"]
        self.assertEqual(len(rows), 1)
        for key in ("cap_from", "cap_to", "note"):
            self.assertNotIn(key, rows[0])
        self.assertEqual(rows[0]["discussions"], [talks[1], talks[3]])
        self.assertEqual((rows[0]["before"], rows[0]["after"]), (2, 5000))
        again = self.memory("check")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("memory: memory/model/opus.md 5000 字元 / 暫存區 0 條未整理\n", again.stdout)
        self.assertFalse(self.exists("tickets/1.json"))

    def test_the_inbox_is_merged_in_and_then_gone(self):
        """#76:inbox 不再整份併進主檔 —— 主檔換成候選,討論過的那幾行移進 `.consumed`。

        **變異**:把 `if rest:` 那一段改成把整份 inbox 寫回 → 這一條紅(inbox 還在)。
        """
        self.write("memory/model/opus.md", FRONT + "舊的一條\n")
        self.write("memory/model/opus.inbox.md", "- 整理期間新記的一條 (#1, 2026-09-12, worker@opus)\n")
        done = self.memory("consolidate", "memory/model/opus.md",
                           "--candidate", self.candidate("舊的一條\n整理期間新記的一條\n"),
                           *self.both())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = self.read("memory/model/opus.md")
        self.assertEqual(text, FRONT + "舊的一條\n整理期間新記的一條\n")
        self.assertFalse(self.exists("memory/model/opus.inbox.md"),
                         "處置過的 inbox 要收掉,不然下一輪會再處置一次")
        consumed = [name for name in os.listdir(os.path.join(self.repo, "memory", "model"))
                    if name.startswith("opus.inbox.md.") and name.endswith(".consumed")]
        self.assertEqual(len(consumed), 1)
        self.assertEqual(self.read(os.path.join("memory", "model", consumed[0])),
                         "- 整理期間新記的一條 (#1, 2026-09-12, worker@opus)\n")

    def test_a_note_during_consolidation_lands_in_a_new_inbox(self):
        self.write("memory/model/opus.md", FRONT + "舊的一條\n")
        self.write("memory/model/opus.inbox.md", "- 待整理的一條 (#1, 2026-09-12, worker@opus)\n")
        argv = ["memory/model/opus.md", "--candidate", self.candidate("新版\n")] + self.both()
        entered = threading.Event()
        release = threading.Event()
        original = memory_module.write

        def blocked_write(path, text):
            entered.set()
            self.assertTrue(release.wait(5))
            original(path, text)

        result = []
        env = {"AC_ROOT": self.repo}

        def consolidate():
            with mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch.object(memory_module, "write", side_effect=blocked_write):
                result.append(memory_module.cmd_consolidate(argv))

        thread = threading.Thread(target=consolidate)
        thread.start()
        self.assertTrue(entered.wait(5), "consolidate 沒有在鎖內走到主檔寫入")
        noted = self.memory("note", "model", "opus", "整理同時寫入的新原則",
                            "--ticket", "2", "--by", "worker@opus")
        self.assertEqual(noted.returncode, 0, noted.stdout + noted.stderr)
        release.set()
        thread.join(5)
        self.assertEqual(result, [0])
        inbox = self.read("memory/model/opus.inbox.md")
        self.assertIn("整理同時寫入的新原則", inbox)
        self.assertNotIn("待整理的一條", inbox, "已被整理的舊 inbox 不該混進新 inbox")

class Note(Sandbox):

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def test_two_processes_append_fifty_complete_lines_each(self):
        code = ("import os,sys; sys.path.insert(0,sys.argv[1]); import memory; "
                "[memory.cmd_note(['role','worker',sys.argv[2]+str(i),"
                "'--ticket','2','--by','worker@opus']) for i in range(50)]")
        env = self.env(AC_ROOT=self.repo)
        procs = [subprocess.Popen([sys.executable, "-c", code,
                                  self.scripts, prefix],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True) for prefix in ("A-", "B-")]
        outputs = [proc.communicate(timeout=30) for proc in procs]
        self.assertEqual([proc.returncode for proc in procs], [0, 0], outputs)
        lines = self.read("memory/role/worker.inbox.md").splitlines()
        self.assertEqual(len(lines), 100)
        self.assertTrue(all(line.startswith("- ") and "worker@opus" in line
                            for line in lines))

    def test_model_memory_rejects_a_different_writers_model(self):
        done = self.memory("note", "model", "opus", "只寫自己的模型層",
                           "--by", "worker@fable")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("只能由同模型寫入", done.stderr)

    def test_a_note_over_three_hundred_characters_is_refused(self):
        done = self.memory("note", "role", "worker", "x" * 301,
                           "--by", "worker@opus")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("300", done.stderr)

    def test_project_notes_append_the_main_record(self):
        first = self.memory("note", "project", "decisions", "第一條",
                            "--ticket", "2", "--by", "worker@opus")
        second = self.memory("note", "project", "decisions", "第二條",
                             "--ticket", "2", "--by", "verifier@fable")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        lines = self.read("memory/project/decisions.md").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("第一條", lines[0])
        self.assertIn("第二條", lines[1])

    def test_twenty_one_notes_are_handled_the_same_as_one(self):
        """D-053 ⑥:沒有行數上限 —— 21 條也只是「21 條未整理」、開一張票。"""
        self.write("memory/model/opus.md", "短的\n")
        self.write("memory/model/opus.inbox.md", "".join("- 第%d條\n" % i for i in range(21)))
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory: memory/model/opus.md 3 字元 / 暫存區 21 條未整理\n", done.stdout)
        self.assertNotIn("超過", done.stdout)
        self.assertEqual([one["subject"] for one in self.tickets_on_disk()],
                         ["整理 memory/model/opus.md(暫存區 21 條未整理)"])


class TheProjectSideIsWhatGetsWatched(Sandbox):
    """整理票管的是**專案自己寫的那一層**(D-021,#28)。

    `memory.applies_to` 指到 `docs/roles/*.md`(同步產出物)的時候,待整理會開一張
    專案**改不了**的整理票:那一層下一次同步就被蓋掉。所以這一條釘住:名單指
    `memory/role/*.md` 時,開出來的票寫得可以動的那個路徑。

    **變異**:把 `open_ticket_for` 補的 `memory_file` 換成 `docs/roles/…` → 這一條紅。
    """

    config_extra = {"memory": {"consolidators": ["fable", "codex:astra"],
                               "applies_to": ["memory/role/*.md"],
                               "inbox_suffix": ".inbox.md"}}

    def memory(self, *args):
        return self.run_py("scripts/memory.py", *args)

    def test_five_notes_open_a_ticket_the_project_can_actually_write(self):
        self.write(os.path.join("memory", "role", "implementer.md"), "短短一句。\n")
        for number in range(1, 6):
            done = self.memory("note", "role", "implementer", "第 %d 條原則" % number,
                               "--ticket", "28", "--by", "worker@opus")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = self.memory("check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("memory: memory/role/implementer.md 6 字元 / 暫存區 5 條未整理\n", done.stdout)
        opened = [one for one in self.tickets_on_disk()
                  if one.get("role") == "consolidator"]
        self.assertEqual(len(opened), 1, opened)
        self.assertEqual(opened[0].get("memory_file"),
                         os.path.join("memory", "role", "implementer.md"),
                         "整理票不該指向同步產出物 —— 改了下一次同步就被蓋掉")

    def test_the_notes_land_in_the_project_memory_not_in_the_canon_dir(self):
        """`note` 寫哪裡本票不改,這一條只是把「它已經寫對地方」釘住。

        #72:負向那一句要有對象 —— 設定真的把正本指到 `docs/roles`、那裡真的有一張
        角色卡;以前沒設 `roles_dir`,「沒寫進 docs/roles」因無關原因成立。
        """
        conf = json.loads(self.read("board/config.json"))
        conf["rules"] = {"roles_dir": os.path.join("docs", "roles"),
                         "models_dir": os.path.join("docs", "roles", "model")}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.write(os.path.join("docs", "roles", "implementer.md"), "# 正本角色卡\n")
        done = self.memory("note", "role", "implementer", "一條原則",
                           "--ticket", "28", "--by", "worker@opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.exists(os.path.join("memory", "role",
                                                 "implementer.inbox.md")))
        self.assertEqual(sorted(os.listdir(os.path.join(self.repo, "docs", "roles"))),
                         ["implementer.md"], "note 寫進了正本那一層")
        self.assertEqual(self.read(os.path.join("docs", "roles", "implementer.md")),
                         "# 正本角色卡\n")



class TheModelTiersAllHaveCards(unittest.TestCase):
    """#111:`model_tiers` 上的每個名字都要有卡 —— 少一張,`rules.py pack --model <name>`
    就以「模型卡 檔案不存在」拒派。讀的是這個 repo 自己的設定與卡,不是沙盒。"""

    def test_every_tier_has_a_card_in_memory_model(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as fh:
            tiers = json.load(fh)["model_tiers"]
        self.assertTrue(tiers, "model_tiers 是空的,這一條什麼都沒量")
        missing = [name for name in tiers
                   if not os.path.isfile(os.path.join(root, "memory", "model", "%s.md" % name))]
        self.assertEqual(missing, [], "model_tiers 上這些名字在 memory/model/ 沒有卡")


if __name__ == "__main__":
    unittest.main()
