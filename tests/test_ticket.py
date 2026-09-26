"""`scripts/ticket.py`:票是唯一的工作單位,所以這一組釘的是「什麼樣的票開不出來、
什麼樣的票關不掉」。"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402

MIN = ("--subject", "land 對 0 commit 整批拒絕",
       "--objective", "任一支 0 commit 時 land 秒退並點名",
       "--acceptance", "0 commit → rc!=0 且輸出含分支名",
       "--allowed-write-path", "scripts/land.sh",
       "--role", "worker", "--model", "opus", "--tool", "claude-code")


class Create(Sandbox):

    def test_it_writes_a_file_and_the_id_goes_up(self):
        first = self.ticket("create", *MIN)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("#1", first.stdout)
        second = self.ticket("create", *MIN)
        self.assertIn("#2", second.stdout)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Draft")
        self.assertEqual(row["state_version"], 1)
        self.assertEqual(row["allowed_write_paths"], ["scripts/land.sh"])
        self.assertIn("ticket.created", self.kinds())

    def test_the_base_sha_is_filled_in_from_main_when_it_is_not_given(self):
        """留空的那一格看起來跟「還沒決定」一樣,而落地器會拿它去問「這個基準還是
        主線的祖先嗎」。"""
        self.ticket("create", *MIN)
        self.assertEqual(self.load_ticket("1")["base_sha"],
                         self.git("rev-parse", "main").strip())

    def test_a_ticket_without_acceptance_is_refused_and_the_missing_field_is_named(self):
        """**變異**:把 `acceptance` 從 `NOT_EMPTY` 拿掉 → 這一條紅。"""
        args = [a for a in MIN]
        at = args.index("--acceptance")
        del args[at:at + 2]
        done = self.ticket("create", *args)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("acceptance", done.stderr)
        self.assertIn("空的", done.stderr)
        self.assertFalse(self.exists("tickets/1.json"), "沒開成的票不該留下檔案")

    def test_a_ticket_without_allowed_write_paths_is_refused(self):
        args = [a for a in MIN]
        at = args.index("--allowed-write-path")
        del args[at:at + 2]
        done = self.ticket("create", *args)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("allowed_write_paths", done.stderr)

    def test_in_scope_becomes_the_default_allowed_write_paths(self):
        args = [a for a in MIN]
        at = args.index("--allowed-write-path")
        del args[at:at + 2]
        done = self.ticket("create", *args, "--in-scope", "scripts/a.py",
                           "--in-scope", "docs/b.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["allowed_write_paths"],
                         ["scripts/a.py", "docs/b.md", "tests/*"])

    def test_explicit_allowed_write_paths_override_the_in_scope_default(self):
        done = self.ticket("create", *MIN, "--in-scope", "docs/b.md")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["allowed_write_paths"],
                         ["scripts/land.sh"])

    def test_it_can_be_asked_one_field_at_a_time(self):
        answers = "\n".join([
            "互動開的票", "把每一格問出來",
            "驗收一", "", "src/a.py", "", "src/b.py", "", "src/*", "",
            "3", "",
            "worker", "opus", "claude-code"]) + "\n"
        done = self.ticket("create", stdin=answers)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["subject"], "互動開的票")
        self.assertEqual(row["acceptance"], ["驗收一"])
        self.assertEqual(row["depends_on"], [{"id": "3", "condition": ""}])

    def test_depends_on_takes_a_condition_after_a_colon(self):
        done = self.ticket("create", *MIN, "--depends-on", "3:閘門綠")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["depends_on"],
                         [{"id": "3", "condition": "閘門綠"}])


class Help(Sandbox):
    """D-001 的具體化:規則住在程式裡,而**程式要自己說得出規則**。

    2026-09-12:有人 clone 下來、照猜寫 `--allowed-write-paths`(複數),開不了票,
    而 `create --help` 只回一句「不認得 --help」—— 一個新來的人於是沒有下一步。
    """

    def test_each_subcommand_help_prints_its_flags_and_a_pasteable_example(self):
        """**變異**:把 `main()` 裡接住 `--help` 的那兩段拿掉 → 這一條紅。"""
        for verb in ("create", "list", "show", "set", "inbox", "verify",
                     "close", "import", "freeze"):
            done = self.ticket(verb, "--help")
            self.assertEqual(done.returncode, 0, verb + ":" + done.stdout + done.stderr)
            self.assertIn("用法:", done.stdout, verb + " 沒有印用法")
            self.assertIn("例:", done.stdout, verb + " 沒有印範例")
            self.assertIn("python3 scripts/ticket.py " + verb, done.stdout,
                          verb + " 的範例不是這個子指令的")

    def test_create_help_shows_how_to_give_the_repeated_flags(self):
        """`acceptance` 與 `allowed_write_paths` 是多值的,而**多值怎麼給**正是猜不
        出來的那一格(猜出來的是複數形旗標)。"""
        done = self.ticket("create", "--help")
        self.assertIn("--allowed-write-path", done.stdout)
        self.assertIn("--acceptance", done.stdout)
        self.assertIn("[可重複]", done.stdout)
        example = done.stdout.split("例:\n", 1)[1]
        self.assertEqual(example.count("--acceptance "), 2,
                         "範例要真的示範同一個旗標給兩次")
        self.assertEqual(example.count("--allowed-write-path "), 2)

    def test_the_example_in_create_help_really_opens_a_ticket(self):
        """一個貼上去開不了票的範例,跟沒有範例一樣 —— 而它讀起來像是對的。
        所以這一條**照著 `--help` 印的那段字真的跑一次**,不是抄一份在測試裡。

        **變異**:把範例裡的 `--allowed-write-path` 改成複數 → 這一條紅。
        """
        import shlex
        printed = self.ticket("create", "--help").stdout
        block = printed.split("例:\n", 1)[1].split("\n\n", 1)[0]
        args = shlex.split(block.replace("\\\n", " "))
        self.assertEqual(args[:2], ["python3", "scripts/ticket.py"])
        done = self.ticket(*args[2:])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(len(row["acceptance"]), 2)
        self.assertEqual(len(row["allowed_write_paths"]), 2)

    def test_only_the_flags_you_really_cannot_omit_are_marked_required(self):
        """標 [必填] 的判準是「不給就開不出票」,不是「schema 有這一格」。這一條
        照著那個標記只給必填的那幾個,票要真的開得出來。"""
        done = self.ticket("create", "--subject", "只給必填的", "--objective", "看它開不開得出來",
                           "--acceptance", "一條會紅的斷言", "--allowed-write-path", "src/*",
                           "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Draft")
        self.assertEqual(row["role"], "worker")
        self.assertTrue(row["base_sha"], "base_sha 該自己去取主線的")

    def test_an_unknown_flag_lists_the_ones_it_does_know(self):
        """「不認得 X」只說了它不是什麼。**下一步要寫在訊息裡**
        (`docs/DISPATCH-TEMPLATE.md` §5.7)。

        **變異**:把 `unknown_flag()` 裡列出名單那一行拿掉 → 這一條紅。
        """
        done = self.ticket("create", "--allowed-write-paths", "scripts/land.sh")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--allowed-write-path", done.stderr, "沒有列出對的那一個")
        self.assertIn("--acceptance", done.stderr)
        self.assertIn("--help", done.stderr, "沒有說下一步去哪看")
        self.assertFalse(self.exists("tickets/1.json"))

    def test_list_also_names_the_flags_it_knows(self):
        done = self.ticket("list", "--closed")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--open", done.stderr)
        self.assertIn("--state", done.stderr)

    def test_the_flag_list_is_derived_from_the_table_not_copied(self):
        """`--help` 印的那幾個就是 `create` 真的吃的那幾個 —— 兩份名單會分岔,
        一份不會。這一條把印出來的每一個都真的餵一次。"""
        printed = self.ticket("create", "--help").stdout
        for flag in ("--feature", "--outline", "--test-plan", "--workspace",
                     "--shared-resource", "--decision-ref"):
            self.assertIn(flag, printed)
            done = self.ticket("create", *MIN, flag, "x")
            self.assertEqual(done.returncode, 0, flag + ":" + done.stdout + done.stderr)


class ListAndShow(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="Ready")
        self.make_ticket(2, state="Done")
        self.make_ticket(3, state="NeedsDecision")

    def test_open_hides_the_closed_ones(self):
        done = self.ticket("list", "--open")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("#1", done.stdout)
        self.assertIn("#3", done.stdout)
        self.assertNotIn("#2", done.stdout)

    def test_state_filters_to_one(self):
        done = self.ticket("list", "--state", "NeedsDecision")
        self.assertIn("#3", done.stdout)
        self.assertNotIn("#1", done.stdout)

    def test_a_state_that_is_not_in_the_machine_is_refused(self):
        done = self.ticket("list", "--state", "Doing")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_an_empty_result_says_so(self):
        done = self.ticket("list", "--state", "Cancelled")
        self.assertIn("沒有符合的票", done.stdout)

    def test_show_prints_the_whole_json(self):
        done = self.ticket("show", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["id"], "1")


class Set(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1, state="Ready")

    def test_every_change_bumps_state_version_and_emits(self):
        """**每次變更 +1**,不管改的是哪一格 —— 只在改 state 時 +1 的版本擋不住
        「改了範圍、版本沒動」那一種。

        **變異**:把 `state_version` 那一行搬進 `if field == "state"` → 這一條紅。
        """
        self.assertEqual(self.ticket("set", "1", "state", "Running").returncode, 0)
        self.assertEqual(self.load_ticket("1")["state_version"], 2)
        self.assertEqual(self.ticket(
            "set", "1", "allowed_write_paths", '["src/*", "tests/*"]').returncode, 0)
        row = self.load_ticket("1")
        self.assertEqual(row["state_version"], 3)
        self.assertEqual(row["allowed_write_paths"], ["src/*", "tests/*"])
        self.assertEqual(self.kinds().count("ticket.state"), 2)

    def test_a_list_field_given_one_word_becomes_a_one_element_list(self):
        """字串形狀的 `allowed_write_paths` 會讓落地的 glob 比對逐字元跑過去,
        而它不會出聲。"""
        self.ticket("set", "1", "allowed_write_paths", "src/only.py")
        self.assertEqual(self.load_ticket("1")["allowed_write_paths"], ["src/only.py"])

    def test_an_unknown_state_is_refused_and_nothing_moves(self):
        done = self.ticket("set", "1", "state", "Finished")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state_version"], 1)

    def test_id_and_state_version_cannot_be_set_by_hand(self):
        for field in ("id", "state_version"):
            done = self.ticket("set", "1", field, "99")
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["id"], "1")


class Freeze(Sandbox):

    def setUp(self):
        super().setUp()
        self.make_ticket(1)

    def test_it_records_both_the_reason_and_the_way_out(self):
        done = self.ticket("freeze", "1", "--reason", "視覺方向未定",
                           "--criterion", "產出會不會因視覺方向改變而重做")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["frozen"]["reason"], "視覺方向未定")
        self.assertIn("重做", row["frozen"]["criterion"])
        self.assertEqual(row["state_version"], 2)
        self.assertIn("ticket.frozen", self.kinds())

    def test_a_freeze_without_a_way_out_is_refused(self):
        """少了解凍條件,凍結會變成一張沒有人記得要回來看的票 —— 「凍著」與
        「忘了」長得一樣。"""
        done = self.ticket("freeze", "1", "--reason", "等使用者")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn("frozen", self.load_ticket("1"))


class VerifyPlan(Sandbox):
    """票的 `verify` 欄(D-010):**驗證者寫的**「案例在哪、怎麼跑」。

    2026-09-20 的成本:驗證者交了案例卻沒寫怎麼跑,下一個人為了找它們重跑整組閘門、
    再用 sleep 迴圈等它,一次幾十萬 token。**案例在哪與怎麼跑,是交付的一部分。**
    """

    def test_the_verifier_flags_write_into_the_nested_verify_field(self):
        done = self.ticket(
            "create", "--subject", "驗證者交件", "--objective", "案例進回歸層",
            "--acceptance", "乾淨主線上紅", "--allowed-write-path", "verify/*",
            "--role", "verifier", "--model", "sonnet", "--tool", "codex",
            "--verify-file", "verify/nav/test_ticket_7.py",
            "--verify-tag", "nav-size", "--verify-tag", "report",
            "--verify-run", "python3 scripts/verify.py --tag nav-size",
            "--verify-note", "要先 npm ci --prefix tools/compat")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = self.load_ticket("1")["verify"]
        self.assertEqual(plan["files"], ["verify/nav/test_ticket_7.py"])
        self.assertEqual(plan["tags"], ["nav-size", "report"])
        self.assertEqual(plan["run"], "python3 scripts/verify.py --tag nav-size")
        self.assertIn("npm ci", plan["notes"])

    def test_a_ticket_with_no_plan_still_has_the_field_and_it_is_empty(self):
        """空著是誠實的「還沒有人寫案例」 —— **不是沒有這一格**。缺了整格的票,
        下游分不出「這張票不用驗」與「沒人寫」。"""
        self.ticket("create", "--subject", "s", "--objective", "o",
                    "--acceptance", "a", "--allowed-write-path", "src/*",
                    "--role", "worker", "--model", "opus", "--tool", "claude-code")
        self.assertEqual(self.load_ticket("1")["verify"],
                         {"files": [], "tags": [], "run": "", "notes": ""})

    def test_verify_prints_the_plan_so_the_next_person_can_run_it(self):
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         verify={"files": ["verify/nav/test_ticket_1.py"],
                                 "tags": ["nav-size"],
                                 "run": "python3 scripts/verify.py --tag nav-size",
                                 "notes": ""})
        done = self.ticket("verify", "1")
        self.assertIn("verify/nav/test_ticket_1.py", done.stdout)
        self.assertIn("python3 scripts/verify.py --tag nav-size", done.stdout)

    def test_a_ticket_nobody_wrote_cases_for_says_so_instead_of_printing_blanks(self):
        """**變異**:把 `print_verify_plan` 的那一句「還沒有人寫」改成 `return`
        → 這一條紅。空白的輸出裡,「沒人寫」與「寫好了」長得一樣。"""
        self.make_ticket(1, allowed_write_paths=["src/*"])
        done = self.ticket("verify", "1")
        self.assertIn("還沒有人寫", done.stdout)


class VerifyAndClose(Sandbox):
    """2026-09-10(前一個專案):一張票被關成完成,而它的程式碼從來沒有進主線。
    事後查證用的就是 `git show main:<檔> | grep -c <那張票獨有的字串>` = 0。"""

    def land_a_file(self, path, text):
        self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 %s 放進主線" % path)

    def test_a_string_that_is_really_on_main_verifies(self):
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("在主線上", done.stdout)
        self.assertIn("×1", done.stdout)

    def test_a_string_that_never_reached_main_does_not_verify(self):
        """**變異**:把 `verify` 的 `hits > 0` 改成 `hits >= 0` → 這一條紅。"""
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("**不在主線上**", done.stdout)

    def test_close_refuses_when_nothing_of_the_ticket_is_on_main(self):
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("exit code 0 不等於 Done", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready", "票不該被關掉")
        self.assertNotIn("ticket.closed", self.kinds())

    def done_ready(self, **extra):
        """一張**真的可以關**的票:東西在主線上、有回歸證據、有綁版本的覆核。

        `close` 與 `set state Done` 共用同一份必要條件(D-014)—— 舊版兩條路各走各
        的,弱的那一條沒有人記得。
        """
        fields = {"allowed_write_paths": ["src/*"],
                  "verify_strings": ["src/nav.py:def size_nav"],
                  "test_evidence": [{"cmd": "gate --branch", "rc": 0}]}
        fields.update(extra)
        self.make_ticket(1, **fields)
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                               ensure_ascii=False))

    def test_close_goes_through_once_the_change_is_really_on_main(self):
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready()
        before = self.load_ticket("1")["state_version"]
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Done")
        self.assertEqual(row["state_version"], before + 1)
        self.assertEqual(row["review"]["state_version"], row["state_version"],
                         "關票讓票往前一版,章要跟著蓋在新版本上")
        self.assertIn("ticket.closed", self.kinds())

    def test_close_refuses_a_ticket_nobody_reviewed(self):
        """**變異**:把 `done_blockers` 裡的 `review_problems` 拿掉 → 這一條紅。

        覆核沒有被任何程式消費過:`review` 只有 verdict/by/at/note,land 不讀它,
        `close` 也不問(2026-09-21 外部審查)。
        """
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         test_evidence=[{"cmd": "gate", "rc": 0}])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("沒有 review", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready")

    def test_close_refuses_when_there_is_no_regression_evidence(self):
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready(test_evidence=[])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("沒有回歸證據", done.stdout)

    def test_a_review_stamped_before_a_later_edit_no_longer_counts(self):
        """**變異**:把 `review_problems` 裡比 state_version 的那一段拿掉 → 這一條紅。

        修復或重新套 patch 之後,舊 review 仍然長得有效 —— 而它蓋的是另一份東西。
        """
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready()
        self.ticket("set", "1", "objective", "改了票面")
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("覆核之後票被改過", done.stdout)

    def test_an_unresolved_blocking_objection_stops_done(self):
        """實作者的反駁要有**收件與處置的契約**:沒處置的阻擋項不得落地、不得 Done。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready(objections=[{"category": "ticket-wrong", "owner": "main",
                                     "body": "驗收第二條和設計文件對不上",
                                     "evidence": "EVIDENCE.md:12", "disposition": ""}])
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("還沒處置", done.stdout)

    def test_set_state_done_goes_through_the_same_gate_as_close(self):
        """**變異**:把 `cmd_set` 裡那一段 `done_blockers` 拿掉 → 這一條紅。

        舊版 `set state Done` 只檢查狀態名對不對 —— Done 的契約有一條旁路。
        """
        self.make_ticket(1, allowed_write_paths=["src/*"])
        done = self.ticket("set", "1", "state", "Done")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("進不了 Done", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready")

    def test_a_stale_report_is_refused_instead_of_overwriting(self):
        """**變異**:把 `stale()` 的比對拿掉 → 這一條紅。

        SCHEMA 早就宣稱「遲到的回報對不上 attempt 就拒絕」,而舊版讀出來直接覆寫
        —— 宣稱與實作分岔的那一格,看起來與有守衛的那一格一模一樣。
        """
        self.make_ticket(1, attempt=2)
        done = self.ticket("set", "1", "outline", "第一次派工交回來的",
                           "--expect-attempt", "1")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("遲到", done.stderr)
        self.assertNotIn("outline", json.dumps(self.load_ticket("1")))
        ok = self.ticket("set", "1", "outline", "這一次的",
                         "--expect-attempt", "2", "--expect-state-version", "1")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_three_rounds_of_red_park_the_ticket_on_main(self):
        """**變異**:把 `cmd_round` 的 `exhausted` 那一段拿掉 → 這一條紅。

        舊規則只寫「三輪仍紅就報主線」,而「報了」與「沒報」在票上長得一樣:票停在
        Running,沒有人是它的 owner。
        """
        self.make_ticket(1, retry_limit=2, state="Running")
        self.assertEqual(self.ticket("round", "1", "2", "--red").returncode, 0)
        self.assertEqual(self.load_ticket("1")["state"], "Running")
        done = self.ticket("round", "1", "3", "--red")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Blocked")
        self.assertEqual(row["owner"], "main")
        self.assertIn("ticket.attempt.failed", self.kinds())

    def test_without_verify_strings_it_falls_back_and_says_the_check_is_weak(self):
        base = self.git("rev-parse", "main").strip()
        self.land_a_file("src/nav.py", "x = 1\n")
        self.make_ticket(1, base_sha=base, allowed_write_paths=["src/*"])
        done = self.ticket("verify", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("弱檢查", done.stdout)

    def test_a_bare_needle_is_looked_for_across_the_allowed_paths(self):
        self.land_a_file("src/a.py", "SENTINEL = 1\n")
        self.make_ticket(1, allowed_write_paths=["src/*"], verify_strings=["SENTINEL"])
        self.assertEqual(self.ticket("verify", "1").returncode, 0)


class TheNextStepAfterLanding(Sandbox):
    """G7 / #29 A7:落地之後 `verify.baseline` 還缺閘門那一趟時,**印一句可以貼的指令**。

    以前 `close` 只說「缺 baseline」,而補量要主線自己打
    `verify-case.py check <n> --ref … --candidate …` —— 兩端填錯一邊,量出來的
    「一條都沒紅」說的是**這一趟量錯了地方**,不是案例是假的(#19)。
    一個守衛給錯了下一步,比沒有守衛更糟(`docs/DISPATCH-TEMPLATE.md` §5.7)。
    """

    def landed(self, **extra):
        base = self.git("rev-parse", "main").strip()
        self.write("src/nav.py", "def size_nav():\n    return 42\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 #1 的東西放進主線")
        tip = self.git("rev-parse", "main").strip()
        fields = {"allowed_write_paths": ["src/*"],
                  "verify_strings": ["src/nav.py:def size_nav"],
                  "base_sha": base}
        fields.update(extra)
        self.make_ticket(1, **fields)
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": tip},
                               ensure_ascii=False))
        return base, tip

    def test_close_prints_the_line_with_both_shas(self):
        """**變異**:把 `cmd_close` 裡那一行 `print_baseline_next_step` 拿掉 → 這一條紅。"""
        base, tip = self.landed()
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("verify-case.py check 1", done.stdout)
        self.assertIn("--ref %s" % base, done.stdout, "ref 要寫死票的 base_sha")
        self.assertIn("--candidate %s" % tip, done.stdout, "candidate 要寫死覆核那個 sha")
        self.assertEqual(self.load_ticket("1")["state"], "Ready", "它不改票的狀態")

    def test_a_baseline_that_already_passed_says_nothing(self):
        """**它不是每次都印**:量過了還叫人再量一次,下一次就沒有人看這一行了。"""
        self.landed(verify={"files": ["verify/example/test_example.py"],
                            "baseline": {"ok": True, "stage": "check"}},
                    test_evidence=[{"cmd": "gate", "rc": 0}])
        done = self.ticket("close", "1")
        # 比對 `--ref`:`done_blockers()` 本來就有一句提到 `verify-case.py check <票號>`,
        # 而**兩句都出現時分不出是誰印的** —— 這一行要問的是那句「可以直接貼」的。
        self.assertNotIn("--ref ", done.stdout, done.stdout)

    def test_a_review_sha_that_never_reached_main_says_nothing(self):
        """**這一句的前提是「東西已經在主線上」** —— review 綁的 sha 還沒進主線時,
        補量沒有東西可量,而印出來的那一句會被照著貼(§5.7:守衛的下一步要是對的)。
        """
        self.landed()
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                               ensure_ascii=False))
        done = self.ticket("close", "1")
        # 比對 `--ref`:`done_blockers()` 本來就有一句提到 `verify-case.py check <票號>`,
        # 而**兩句都出現時分不出是誰印的** —— 這一行要問的是那句「可以直接貼」的。
        self.assertNotIn("--ref ", done.stdout, done.stdout)


class WaiverAndLanded(Sandbox):
    """#15:已落地的票關不掉 —— close 要認得 `verify_waiver`(#8),而 `set` 那幾格
    (`verify_waiver` / `verify_strings` / `objections`)是落地後的收尾,不該讓既有
    review 過期。"""

    def land_a_file(self, path, text):
        self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "把 %s 放進主線" % path)

    def test_a_waiver_with_a_review_sha_on_main_skips_the_regression_check(self):
        """**變異**:把 `waiver_covers_regression` 拿掉(改回舊的無條件檢查)
        → 這一條紅(沒有 test_evidence 就過不了)。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         verify_waiver={"by": "main", "reason": "控制腳本票:無獨立驗證者"})
        self.approve(1, branch="main")  # review.sha = main 的頭,state_version 跟著綁上
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.load_ticket("1")["state"], "Done")
        self.assertNotIn("test_evidence", self.load_ticket("1"))

    def test_a_waiver_does_not_cover_a_review_sha_that_never_reached_main(self):
        """免驗**只在 review 綁的 sha 真的在主線歷史裡才生效**——票寫了 waiver,但
        review 蓋的章是另一條沒進主線的分支,不該就這樣免了回歸證據。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         verify_waiver={"by": "main", "reason": "控制腳本票"})
        path = self.worktree("t1-unmerged")
        self.commit_in(path, "extra.txt", "沒有進主線的 commit")
        self.approve(1, branch="t1-unmerged")
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("沒有回歸證據", done.stdout)
        self.assertEqual(self.load_ticket("1")["state"], "Ready")

    def test_a_waiver_missing_reason_does_not_count(self):
        """票面沒說清楚(`reason` 空著)不算誠實的 waiver。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         verify_waiver={"by": "main", "reason": ""})
        self.approve(1, branch="main")
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("沒有回歸證據", done.stdout)

    def done_ready(self, **extra):
        fields = {"allowed_write_paths": ["src/*"],
                  "verify_strings": ["src/nav.py:def size_nav"],
                  "test_evidence": [{"cmd": "gate --branch", "rc": 0}]}
        fields.update(extra)
        self.make_ticket(1, **fields)
        self.ticket("set", "1", "review",
                    json.dumps({"verdict": "pass", "by": "main", "sha": "deadbeef"},
                               ensure_ascii=False))

    def test_setting_verify_waiver_carries_the_review_version_forward(self):
        """**變異**:把 `cmd_set` 裡「`field in CARRY_REVIEW_FIELDS` 跟著蓋
        `review['state_version']`」那一段拿掉 → 這一條紅
        (`覆核之後票被改過`,close 回 1)。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready()
        before_version = self.load_ticket("1")["review"]["state_version"]
        set_done = self.ticket("set", "1", "verify_waiver",
                               json.dumps({"by": "main", "reason": "後補"},
                                          ensure_ascii=False))
        self.assertEqual(set_done.returncode, 0, set_done.stdout + set_done.stderr)
        row = self.load_ticket("1")
        self.assertGreater(row["state_version"], before_version)
        self.assertEqual(row["review"]["state_version"], row["state_version"],
                         "review 該跟著蓋到新版本,不能停在舊的")
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        carried = [r for r in self.events()
                  if r.get("kind") == "ticket.state" and r.get("field") == "verify_waiver"]
        self.assertTrue(carried and carried[-1].get("review_carried") == 1)

    def test_setting_an_unrelated_field_still_lets_the_review_expire(self):
        """只有那三格(`verify_waiver`/`verify_strings`/`objections`)算收尾;
        改票面其他格(這裡改 `objective`)review 照舊規矩過期 —— 沒有把「所有 set
        都跟著蓋」誤植進去。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        self.done_ready()
        self.ticket("set", "1", "objective", "改了票面")
        done = self.ticket("close", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("覆核之後票被改過", done.stdout)

    def test_close_landed_stamps_review_sha_and_closes_in_one_step(self):
        """**變異**:把 `stamp_landed` 裡 `sha_on_branch` 的檢查拿掉 → 下一條測試
        (拒收不在主線的 sha)會紅。這一條驗的是正常路徑:`--landed` 一步關票。"""
        self.land_a_file("src/nav.py", "def size_nav():\n    return 42\n")
        sha = self.git("rev-parse", "main").strip()
        self.make_ticket(1, allowed_write_paths=["src/*"],
                         verify_strings=["src/nav.py:def size_nav"],
                         test_evidence=[{"cmd": "gate --branch", "rc": 0}])
        done = self.ticket("close", "1", "--landed", sha)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("1")
        self.assertEqual(row["state"], "Done")
        self.assertEqual(row["review"]["sha"], sha)
        self.assertEqual(row["review"]["verdict"], "pass")
        self.assertEqual(row["review"]["state_version"], row["state_version"])

    def test_close_landed_refuses_a_sha_that_never_reached_main(self):
        """**變異**:把 `sha_on_branch` 的 `merge-base --is-ancestor` 判斷改成永遠
        `True` → 這一條紅(不在主線的 sha 被誤蓋進 review)。"""
        self.make_ticket(1, allowed_write_paths=["src/*"])
        path = self.worktree("t1-x")
        self.commit_in(path, "x.txt", "沒進主線")
        off_main = self.git("rev-parse", "t1-x").strip()
        done = self.ticket("close", "1", "--landed", off_main)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("不在主線", done.stdout)
        self.assertNotIn("review", self.load_ticket("1"))


class Inbox(Sandbox):

    def answer(self, ident, text):
        self.write("board/answers.jsonl",
                   json.dumps({"ts": "2026-09-12T11:00:00+08:00",
                               "ticket": ident, "answer": text},
                              ensure_ascii=False) + "\n")

    def test_it_lists_tickets_waiting_for_a_decision(self):
        self.make_ticket(1, state="NeedsDecision", subject="兌換碼的最小單位")
        done = self.ticket("inbox")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("#1", done.stdout)
        self.assertIn("兌換碼的最小單位", done.stdout)

    def test_an_answer_that_has_not_become_a_decision_is_still_in_the_inbox(self):
        self.make_ticket(1, state="Ready")
        self.answer("1", "成員退出帳本,月繳的規定當然一起不見")
        done = self.ticket("inbox")
        self.assertIn("還沒落成", done.stdout)
        self.assertIn("月繳", done.stdout)

    def test_once_the_decision_names_the_ticket_it_leaves_the_inbox(self):
        """判準就是 `docs/DECISIONS.md` 自己 —— 不另記一格「已處理」,因為另記的
        那一格會跟事實分岔。

        **變異**:把 `decided_tickets()` 改成永遠回空集合 → 這一條紅。
        """
        self.make_ticket(1, state="Ready")
        self.answer("1", "成員退出帳本,月繳的規定當然一起不見")
        self.write("docs/DECISIONS.md",
                   "# 裁示\n\n| D-007 | 退出即解除月繳 | 使用者 #1 原話 | 2026-09-12 | ✅ |\n")
        done = self.ticket("inbox")
        self.assertIn("收件匣是空的", done.stdout)

    def test_an_empty_inbox_says_so(self):
        self.assertIn("收件匣是空的", self.ticket("inbox").stdout)


class Import(Sandbox):

    def old_ticket(self, where, ident, **fields):
        row = {"id": str(ident), "subject": "舊票 %s" % ident, "status": "completed",
               "blocks": [], "blockedBy": [], "description": "當時的描述",
               "phases": {"created": "2026-09-10T11:50:45+08:00"}}
        row.update(fields)
        self.write("%s.json" % ident, json.dumps(row, ensure_ascii=False), where=where)

    def test_old_json_becomes_this_schema_with_the_gaps_left_empty(self):
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 497)
        self.old_ticket(old, 498, status="in_progress", blockedBy=["497"])
        done = self.ticket("import", old)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        row = self.load_ticket("497")
        self.assertTrue(row["legacy"], "沒有標 legacy")
        self.assertEqual(row["state"], "Done")
        self.assertEqual(row["base_sha"], "", "舊票的 base_sha 要留空,不是猜一個")
        self.assertEqual(row["allowed_write_paths"], [])
        self.assertIn("當時的描述", row["outline"])
        self.assertEqual(self.load_ticket("498")["state"], "Running")
        self.assertEqual(self.load_ticket("498")["depends_on"],
                         [{"id": "497", "condition": ""}])

    def test_a_ticket_that_is_already_here_is_not_overwritten(self):
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 1)
        self.make_ticket(1, subject="新的那一張")
        done = self.ticket("import", old)
        self.assertIn("跳過 1 張", done.stdout)
        self.assertEqual(self.load_ticket("1")["subject"], "新的那一張")

    def test_a_file_it_cannot_read_is_counted_separately_and_is_not_zero_exit(self):
        """「跳過」是已經有了,「讀不懂」是這裡有東西沒進來 —— 揉成一句「匯入 N 張」
        會讓後者看起來像沒發生。"""
        old = os.path.join(self.home, "old")
        os.makedirs(old)
        self.old_ticket(old, 1)
        self.write("broken.json", "{這不是 JSON", where=old)
        done = self.ticket("import", old)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("讀不懂 1 個檔", done.stdout)



class ObjectionSaysWhetherItWasDisposed(Sandbox):
    """`ticket.py objection` 的「已經有了」分兩種(#36):還沒處置(rc=3)與處置過
    (rc=4)。揉成同一個 rc 的時候,`apply.sh` 分不出來,未處置的反駁第二輪就放行了。"""

    LINE = "OBJECTION: ticket-wrong 驗收 A3 指的欄位不存在"

    def test_a_duplicate_is_3_until_disposed_and_4_after(self):
        """**變異**:把 `cmd_objection` 的 `elif done:` 換成 `elif True:` → 這一條紅。"""
        self.make_ticket(1)
        first = self.ticket("objection", "1", "--line", self.LINE)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        again = self.ticket("objection", "1", "--line", self.LINE)
        self.assertEqual(again.returncode, 3, again.stdout + again.stderr)
        self.assertIn("還沒處置", again.stdout)
        rows = self.load_ticket("1")["objections"]
        rows[0]["disposition"] = "deferred"
        self.ticket("set", "1", "objections", json.dumps(rows, ensure_ascii=False))
        third = self.ticket("objection", "1", "--line", self.LINE)
        self.assertEqual(third.returncode, 4, third.stdout + third.stderr)
        self.assertIn("已處置", third.stdout)
        self.assertEqual(len(self.load_ticket("1")["objections"]), 1)


if __name__ == "__main__":
    unittest.main()
