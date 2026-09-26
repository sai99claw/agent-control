"""`scripts/rules.py pack <角色>`:按角色裁切、帶版本的規則包(D-015)。

2026-09-21 外部審查的 token 帳:「共用規矩重複載入 —— CLAUDE 要整份派工規範一起給,
角色卡卻說 prompt 只指路。」整份共用規矩是一萬多個字,而一個實作者真的會被擋到的是
其中幾節;驗證者要的是另外幾節。**每派一次工,每個 agent 各付一次整份的錢。**

所以這一組問三件事:
1. 抽出來的是**這個角色要的那幾節**,不是全部;
2. 壓得進位元組上限;
3. 砍掉的時候**說得出砍了哪一份** —— 砍掉而不說,與那一節不存在長得一樣。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import Sandbox  # noqa: E402


class RulesBase(Sandbox):

    def setUp(self):
        super(RulesBase, self).setUp()
        self.install_rules_sources()

    def rules(self, *args):
        return self.run_py("scripts/rules.py", *args)

    def source_size(self):
        return len(self.read(os.path.join("docs", "DISPATCH-TEMPLATE.md")).encode("utf-8"))

    # 緊預算那一包。正本也帶自己的暫存區之後(#37),先讀清單多兩行、「砍過」名單多兩名,
    # 2000 B 連前言 + 「砍過」那一句都放不下(實測到 2307 才放得下)。
    TIGHT_BYTES = 2400

    def tight_pack(self):
        """暫存區寫死在這裡,不吃真的 `memory/*.inbox.md` —— 那幾份會長會縮,這一組的
        餘裕就跟著漂。兩格都比暫存區那 600 B 長,所以一定被砍、一定進「砍過」名單:
        角色卡、節錄、模型記憶、兩格暫存五份都砍過,仍然是緊預算。"""
        lessons = "".join("- 暫存第%d條 %s\n" % (i, "舊" * 60) for i in range(1, 6))
        self.write(os.path.join("memory", "role", "implementer.inbox.md"), lessons)
        self.write(os.path.join("memory", "model", "opus.inbox.md"), lessons)
        return self.rules("pack", "worker", "--model", "opus",
                          "--max-bytes", str(self.TIGHT_BYTES))


class WhatItPacks(RulesBase):

    def test_it_points_at_the_three_files_instead_of_pasting_them_whole(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for pointer in ("memory/role/implementer.md", "memory/model/opus.md",
                        "docs/DISPATCH-TEMPLATE.md"):
            self.assertIn(pointer, done.stdout, "沒有指路就等於要對方自己猜")

    def test_it_is_much_smaller_than_the_whole_thing(self):
        done = self.rules("pack", "worker", "--model", "opus")
        size = len(done.stdout.encode("utf-8"))
        self.assertLessEqual(size, 4096, "上限就是這一支的全部意義")
        self.assertLess(size, self.source_size() // 2,
                        "比整份的一半還大的話,省下來的不值得多一支腳本")

    def test_a_worker_gets_the_sections_that_bite_a_worker(self):
        """禁區與假綠家族是實作者真的會被擋到的兩節。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertIn("禁區", done.stdout)
        self.assertIn("5.5", done.stdout)

    def test_a_consolidator_gets_a_pack_with_memory(self):
        done = self.rules("pack", "consolidator", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("memory/model/opus.md", done.stdout)
        self.assertIn("共用規矩節錄", done.stdout)

    def test_the_chinese_consolidator_alias_is_recognized(self):
        done = self.rules("pack", "整理者", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("規則包:consolidator", done.stdout)

    def test_the_design_session_has_a_pack_of_its_own(self):
        """A1 / G1:設計 session 是 D-019 每天在派、卻**唯一沒有規則包**的角色 ——
        而「沒有包」與「這個角色不必給規矩」在派工文上長得一樣。

        **變異**:把 `WANTED["design"]` 那一格拿掉 → 這一條紅(rc=2,不認得角色)。
        """
        done = self.rules("pack", "design", "--model", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/role/design.md", done.stdout, "角色卡指路")
        self.assertIn("設計 session", done.stdout)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096)

    def test_the_reviewer_has_a_pack_of_its_own(self):
        """D-022:覆核改由短命 opus 做,而它上一分鐘還沒有角色卡也沒有規則包。"""
        done = self.rules("pack", "reviewer", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/role/reviewer.md", done.stdout)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096)

    def test_the_consolidator_stops_borrowing_the_implementer_card(self):
        """A8 / G8:整理者以前借 `implementer.md` —— 一張寫著「交 patch.diff」的
        角色卡,對一個不交 patch 的角色說話。

        **變異**:把 `WANTED["consolidator"]` 的第一格改回 `implementer.md` → 這一條紅。
        """
        done = self.rules("pack", "consolidator", "--model", "fable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/role/consolidator.md", done.stdout)
        self.assertNotIn("memory/role/implementer.md", done.stdout)

    def test_a_verifier_gets_a_different_cut(self):
        """驗證者不需要「副本 + patch」那一整節(它交的是案例,不是產品 patch)。"""
        worker = self.rules("pack", "worker", "--model", "opus").stdout
        verifier = self.rules("pack", "verifier", "--model", "sonnet").stdout
        self.assertNotEqual(worker, verifier)
        self.assertIn("副本 + patch", worker)
        self.assertNotIn("### 1. 副本 + patch", verifier)

    def test_it_names_the_version_it_was_cut_from(self):
        """規則包會被貼進派工文,而**一份不知道自己是哪一版的規則包**沒辦法被追。"""
        done = self.rules("pack", "worker", "--model", "opus")
        sha = self.git("rev-parse", "--short", "HEAD").strip()
        self.assertIn(sha, done.stdout)


class WhenItHasToCut(RulesBase):

    def test_cutting_says_which_file_was_cut(self):
        # 緊預算不是 1200:記憶回寫段(約 600 B)先扣,1200 連前言 + 「砍過」那一句都放不下。
        done = self.tight_pack()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), self.TIGHT_BYTES)
        self.assertIn("截斷", done.stdout)
        self.assertIn("砍過", done.stdout)

    def test_a_bigger_budget_carries_more(self):
        small = self.rules("pack", "worker", "--model", "opus",
                           "--max-bytes", "1500").stdout
        big = self.rules("pack", "worker", "--model", "opus",
                         "--max-bytes", "6000").stdout
        self.assertGreater(len(big.encode("utf-8")), len(small.encode("utf-8")))

    def test_a_section_that_is_no_longer_in_the_source_is_called_out(self):
        """名單與文件分岔時要**出聲**:一份靜靜少了兩節的規則包,與完整的那一份
        在畫面上長得一樣。

        **變異**:把 `missing` 那一段拿掉 → 這一條紅。
        """
        text = self.read(os.path.join("docs", "DISPATCH-TEMPLATE.md"))
        self.write(os.path.join("docs", "DISPATCH-TEMPLATE.md"),
                   text.replace("## 2. 禁區", "## 2222. 換了號碼的禁區"))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("找不到這幾節", done.stdout)
        self.assertIn("§2", done.stdout)


class HowItAnswers(RulesBase):

    def test_an_unknown_role_lists_the_ones_it_knows(self):
        """「不認得 X」只說了它不是什麼(`docs/DISPATCH-TEMPLATE.md` §5.7)。"""
        done = self.rules("pack", "dispatcher")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("worker", done.stderr)
        self.assertIn("verifier", done.stderr)

    def test_roles_prints_the_table_of_who_gets_what(self):
        done = self.rules("roles")
        self.assertEqual(done.returncode, 0, done.stderr)
        for role in ("worker", "verifier", "opener", "main", "consolidator"):
            self.assertIn(role, done.stdout)

    def test_a_model_name_with_a_tool_prefix_still_finds_its_memory(self):
        """路由表裡的模型帶著工具前綴(`codex:gpt-5.6-sol`),記憶檔只有模型那一半。
        指著一個永遠不存在的路徑,比不指路更糟 —— 它看起來像「那個模型還沒有記憶」。

        **變異**:把 `model_card` 的第二個候選拿掉 → 這一條紅。
        """
        done = self.rules("pack", "worker", "--model", "codex:opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("memory/model/opus.md", done.stdout)
        self.assertNotIn("memory/model/codex:opus.md", done.stdout)

    def test_a_missing_model_memory_does_not_break_the_pack(self):
        """模型記憶還沒有的那個模型也要派得出工。"""
        done = self.rules("pack", "worker", "--model", "nobody")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("memory/role/implementer.md", done.stdout)


class WhatItAlwaysCarries(RulesBase):
    """第五段「記憶回寫」是固定文字、先扣預算:每個角色都帶,砍預算時砍的是別份。

    **變異**:把 `pack()` 結尾接上 `MEMORY_NOTE` 那一行拿掉 → 這一組全紅。
    """

    ROLES = ("worker", "verifier", "opener", "main", "consolidator")
    LAST_LINE = "沒寫就寫「無」。"

    def test_every_role_gets_the_memory_writeback_section_within_the_cap(self):
        for role in self.ROLES:
            done = self.rules("pack", role, "--model", "opus")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("## 記憶回寫", done.stdout, role)
            self.assertIn(self.LAST_LINE, done.stdout, role)
            self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096, role)

    def test_the_section_says_when_to_write_and_when_not(self):
        """措辭是這一段的全部:三種時刻、預設不寫、只寫原則、model 層只寫自己。"""
        text = self.rules("pack", "worker", "--model", "opus").stdout
        for phrase in ("預設不寫", "兩次以上", "角色卡沒講", "跨票", "memory.py note",
                       "只准寫自己的", "300 字元", "EVIDENCE"):
            self.assertIn(phrase, text)

    def test_the_section_is_under_600_bytes(self):
        sys.path.insert(0, os.path.join(self.repo, "scripts"))
        try:
            import importlib
            rules = importlib.import_module("rules")
        finally:
            sys.path.pop(0)
        self.assertLessEqual(rules.MEMORY_NOTE_BYTES, 600)

    def test_a_tight_budget_cuts_the_other_parts_not_this_one(self):
        """先扣預算的意思:上限縮到緊預算時,砍的是角色卡與節錄,這一段一個字不少。"""
        done = self.tight_pack()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), self.TIGHT_BYTES)
        self.assertIn("砍過", done.stdout)
        self.assertTrue(done.stdout.rstrip("\n").endswith(self.LAST_LINE), done.stdout[-300:])

    def test_stats_reports_the_section_bytes(self):
        done = self.rules("pack", "worker", "--model", "opus", "--stats")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, r"記憶回寫 \d+ bytes")


class TheStructuredDeliverySection(RulesBase):
    """第六段「結構化交付」是固定文字、先扣預算(D-017,#20):交出來的東西一產生
    就要是機器讀得懂的,所以每個角色都帶得到這一句,而砍預算時砍的是別份。

    **變異**:把 `pack()` 結尾接上 `DELIVERY_NOTE` 那一段拿掉 → 這一組全紅。
    """

    ROLES = ("worker", "verifier", "opener", "main", "consolidator")
    LAST_LINE = "編一個數字進去,與量過那個數字長得一樣。"

    def test_every_role_gets_the_section_within_the_cap(self):
        for role in self.ROLES:
            done = self.rules("pack", role, "--model", "opus")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("## 結構化交付", done.stdout, role)
            self.assertIn(self.LAST_LINE, done.stdout, role)
            self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096, role)

    def test_the_section_says_where_the_block_goes_and_who_picks_it_up(self):
        """措辭是這一段的全部:檔尾、那個語言標記、auto-fix 抽成哪一個檔、留空不要編。"""
        text = self.rules("pack", "worker", "--model", "opus").stdout
        for phrase in ("檔尾", "result", "auto-fix", "result-round", "照實留空"):
            self.assertIn(phrase, text)

    def test_it_points_at_the_two_places_instead_of_copying_the_keys(self):
        """schema 只寫兩處。規則包抄第三份的那一天,三份會各自往不同方向漂 ——
        而漂開的那一份看起來仍然像規格。

        **變異**:把 `DELIVERY_NOTE` 改成列出鍵名 → 這一條紅。
        """
        text = self.rules("pack", "worker", "--model", "opus").stdout
        for pointer in ("docs/DISPATCH-TEMPLATE.md", "tickets/SCHEMA.md"):
            self.assertIn(pointer, text)
        for key in ("patch_sha256", "red_first_line", "no-block"):
            self.assertNotIn(key, text, "規則包裡出現了第三份 schema")

    def test_the_section_is_under_600_bytes(self):
        sys.path.insert(0, os.path.join(self.repo, "scripts"))
        try:
            import importlib
            rules = importlib.import_module("rules")
        finally:
            sys.path.pop(0)
        self.assertLessEqual(rules.DELIVERY_NOTE_BYTES, 600)

    def test_a_tight_budget_cuts_the_other_parts_not_this_one(self):
        done = self.tight_pack()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), self.TIGHT_BYTES)
        self.assertIn("砍過", done.stdout)
        self.assertIn(self.LAST_LINE, done.stdout)

    def test_stats_reports_the_section_bytes(self):
        done = self.rules("pack", "worker", "--model", "opus", "--stats")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, r"結構化交付 \d+ bytes")


class EfficiencyOverSpeed(RulesBase):
    """第一段「不追求快,只看效率」是固定文字、先扣預算(D-016,#16):產品負責人
    2026-09-21 原話 —— 因為「這樣比較快」而做事之前先算 token。放在標題之後、
    先讀清單之前,每個角色都帶,而且量在最前面三行裡,讀的人第一眼就看得到。

    **變異**:把 `pack()` 的 head 拿掉 `EFFICIENCY_NOTE` 那一行 → 這一組五個角色都紅
    (golden:第一條就是「拿掉這一段」的那一個)。
    """

    ROLES = ("worker", "verifier", "opener", "main", "consolidator")

    def test_every_role_carries_the_line_in_the_first_three_lines(self):
        for role in self.ROLES:
            done = self.rules("pack", role, "--model", "opus")
            self.assertEqual(done.returncode, 0, done.stderr)
            head = "\n".join(done.stdout.splitlines()[:3])
            self.assertIn("不追求快", head, role)
            self.assertIn("token", head, role)
            self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096, role)

    def test_it_sits_after_the_title_and_before_the_reading_list(self):
        done = self.rules("pack", "worker", "--model", "opus")
        lines = done.stdout.splitlines()
        self.assertTrue(lines[0].startswith("# 規則包:"), lines[0])
        title_at = done.stdout.index(lines[0])
        note_at = done.stdout.index("不追求快")
        list_at = done.stdout.index("## 先讀這幾份")
        self.assertTrue(title_at < note_at < list_at,
                        "D-016 那一行要在標題之後、先讀清單之前")

    def test_stats_reports_the_section_bytes(self):
        done = self.rules("pack", "worker", "--model", "opus", "--stats")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, r"不追求快 \d+ bytes")

    def test_the_wording_says_count_tokens_not_speed(self):
        text = self.rules("pack", "worker", "--model", "opus").stdout
        for phrase in ("D-016", "這樣比較快", "token", "判準"):
            self.assertIn(phrase, text)


class TheProjectLayer(RulesBase):
    """疊在正本規矩上的**專案自己那一層**(D-021,#28)。

    `memory/` 永遠是「這個 repo 自己寫的」,`roles_dir` 是「規矩從哪來」。同一個目錄時
    這個 repo 就是正本(agent-control 自己),不同時它是專案 —— 而專案的教訓寫在
    `memory/`,以前 `pack` 一個字都沒讀到:路徑錯(它只讀 `roles_dir`)+ 暫存區本來就
    不進包。少讀一次的代價是一輪落地紅。

    **變異**:把 `pack()` 裡 `local = not is_canon(root)` 改成 `local = False`
    → 這一組除了「A 自己」那兩條之外全紅。
    """

    config_extra = {"rules": {"roles_dir": os.path.join("docs", "roles"),
                              "models_dir": os.path.join("docs", "roles", "model")}}
    CANON_ROLE = "CANON-ROLE 正本角色卡說的那一句。"
    LOCAL_ROLE = "LOCAL-ROLE 這個 repo 自己補的那一句。"
    CANON_MODEL = "CANON-MODEL 正本模型記憶說的那一句。"
    LOCAL_MODEL = "LOCAL-MODEL 這個 repo 自己補的那一句。"
    PROJECT_NOTE = "PROJECT-BODY 專案備忘的內文不該進包。"

    def setUp(self):
        super(TheProjectLayer, self).setUp()
        self.write(os.path.join("docs", "roles", "implementer.md"),
                   "# 正本角色卡\n%s\n" % self.CANON_ROLE)
        self.write(os.path.join("docs", "roles", "model", "opus.md"),
                   "# 正本模型記憶\n%s\n" % self.CANON_MODEL)
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "%s\n" % self.LOCAL_ROLE)
        self.write(os.path.join("memory", "role", "implementer.inbox.md"),
                   "- 第一條 INBOX-1\n- 第二條 INBOX-2\n- 第三條 INBOX-3\n")
        self.write(os.path.join("memory", "model", "opus.md"),
                   "%s\n" % self.LOCAL_MODEL)
        self.write(os.path.join("memory", "project", "foo.md"),
                   "- %s\n" % self.PROJECT_NOTE)

    def single_layer(self):
        """`roles_dir` 不設 = 這個 repo 自己就是正本(agent-control)。"""
        conf = json.loads(self.read(os.path.join("board", "config.json")))
        conf["rules"] = {}
        self.write(os.path.join("board", "config.json"),
                   json.dumps(conf, ensure_ascii=False, indent=2))

    def test_both_layers_are_in_the_pack_and_it_still_fits(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for phrase in (self.CANON_ROLE, self.LOCAL_ROLE, "INBOX-3",
                       self.CANON_MODEL, self.LOCAL_MODEL):
            self.assertIn(phrase, done.stdout, "疊上去的那一層漏了:" + phrase)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096,
                            "疊第二層不准把上限撐開")

    def test_project_notes_are_a_path_not_a_paste(self):
        """專案層不設上限、會長;貼進 4 KB 包會把角色卡擠掉,而砍到只剩標題與沒貼
        一樣。所以只列路徑,要看的那一次用 grep(D-013 第 4 條)。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertIn(os.path.join("memory", "project", "foo.md"), done.stdout)
        self.assertIn("## 先讀這幾份",
                      done.stdout[:done.stdout.index("memory/project/foo.md")],
                      "專案備忘的路徑要在先讀清單裡")
        self.assertNotIn(self.PROJECT_NOTE, done.stdout)

    def test_the_inbox_only_brings_its_last_lines(self):
        """暫存區一行一條、越新越下面:從頭留會只留到最舊的那幾條。"""
        self.write(os.path.join("memory", "role", "implementer.inbox.md"),
                   "".join("- 第%d條 INBOX-%d\n" % (i, i) for i in range(1, 60)))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("INBOX-59", done.stdout, "最後一行才是最新的那一條")
        self.assertNotIn("INBOX-1 ", done.stdout)
        self.assertIn("只留最後幾行", done.stdout, "砍了要出聲")

    def test_being_the_canon_repo_itself_reads_the_card_once(self):
        """`roles_dir` 與 `memory/role` 是同一個目錄 = 這個 repo 就是正本。疊兩次的話
        角色卡會整份出現兩遍,而讀的人會以為那是兩份不同的規矩。"""
        self.single_layer()
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count(self.LOCAL_ROLE), 1)
        self.assertNotIn("本專案", done.stdout, "A 自己不該有「本專案」這個小標")

    def test_the_canon_repo_packs_its_own_inbox_too(self):
        """正本(A 自己)也帶自己的暫存區(#37):以前暫存區只從專案層取,而同步刻意
        不帶 inbox(D-021)—— 正本的 `*.inbox.md` 兩邊都沒有讀者。

        用開題者 + fable(票面 A1 量的那一組)。兩格都有字、每一行都是真實長度(最後
        一行約 190 B / 250 B):平分 600 B 的時候後面那一格只剩一句「砍過」。

        **變異**:inbox 來源綁回 `local`(`(local_card_rel, local_model_rel)`)→ 紅;
        前一格用剩的不給下一格(`inbox_room // len(inboxes)`)→ 紅。
        """
        self.single_layer()
        role_last = "- ROLE-INBOX-LAST " + "角" * 56
        model_last = "- MODEL-INBOX-LAST " + "模" * 76
        # 舊的幾條也是真實長度(一條教訓約 150–260 B),不是一兩個字的填充。
        older = "".join("- 舊的第%d條 %s\n" % (i, "舊" * 60) for i in range(1, 6))
        self.write(os.path.join("memory", "role", "opener.inbox.md"),
                   older + role_last + "\n")
        self.write(os.path.join("memory", "model", "fable.inbox.md"),
                   older + model_last + "\n")
        done = self.rules("pack", "opener", "--model", "fable")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(role_last, done.stdout, "正本的角色暫存區沒進包")
        self.assertIn(model_last, done.stdout, "正本的模型暫存區沒進包")
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 4096)
        self.assertNotIn("本專案", done.stdout, "A 自己不該有「本專案」這個小標")

    def test_being_the_canon_repo_does_not_list_the_project_layer(self):
        self.single_layer()
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertNotIn(os.path.join("memory", "project", "foo.md"), done.stdout)

    def test_an_inbox_without_a_main_file_is_not_an_error(self):
        """主檔還沒有人寫、只有暫存區 —— 那不是壞掉,不要印「找不到」。"""
        os.remove(os.path.join(self.repo, "memory", "role", "implementer.md"))
        os.remove(os.path.join(self.repo, "memory", "model", "opus.md"))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("找不到 memory/role", done.stdout)
        self.assertIn("INBOX-3", done.stdout, "主檔不在,暫存區還是要讀得到")

    def test_a_budget_too_small_for_the_inbox_still_names_it(self):
        """**默默消失的一格與從來沒有過的一格長得一樣**:預算縮到連暫存區都放不下時,
        輸出裡仍然要點得出是哪一個檔被砍掉。

        **變異**:把最後那一刀的指路改回不含 `cut` 名單 → 這一條紅。
        """
        os.remove(os.path.join(self.repo, "memory", "role", "implementer.md"))
        done = self.rules("pack", "worker", "--model", "opus", "--max-bytes", "1500")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLessEqual(len(done.stdout.encode("utf-8")), 1500)
        self.assertIn(os.path.join("memory", "role", "implementer.inbox.md"),
                      done.stdout, "砍掉了卻沒點名 = 讀的人以為本來就沒有這一格")

    def test_the_reading_list_points_at_both_layers(self):
        done = self.rules("pack", "worker", "--model", "opus")
        head = done.stdout[:done.stdout.index("## 角色卡")]
        for pointer in (os.path.join("docs", "roles", "implementer.md"),
                        os.path.join("memory", "role", "implementer.md"),
                        os.path.join("docs", "roles", "model", "opus.md"),
                        os.path.join("memory", "model", "opus.md")):
            self.assertIn(pointer, head)

    def test_roles_prints_the_canon_directory_not_a_hardcoded_one(self):
        """`roles` 那張表以前寫死 `memory/role/` —— 在專案裡那句話指向一個不是正本的
        目錄,而讀的人會去改那一份(改了下一次同步就被蓋掉)。"""
        done = self.rules("roles")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(os.path.join("docs", "roles", "implementer.md"), done.stdout)
        self.assertNotIn(os.path.join("memory", "role", "implementer.md"), done.stdout)


if __name__ == "__main__":
    unittest.main()
