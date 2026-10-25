"""`scripts/rules.py pack <角色>`:按角色裁切、帶版本的規則包(D-015)。

2026-09-21 外部審查的 token 帳:「共用規矩重複載入 —— CLAUDE 要整份派工規範一起給,
角色卡卻說 prompt 只指路。」整份共用規矩是一萬多個字,而一個實作者真的會被擋到的是
其中幾節;驗證者要的是另外幾節。**每派一次工,每個 agent 各付一次整份的錢。**

所以這一組問三件事:
1. 抽出來的是**這個角色要的那幾節**,不是全部;
2. 各段原文全進包,不設位元組上限、不截斷(D-054 Q6);
3. 缺一節、卡無正文、模型卡缺就不交(rc 1、stdout 空)。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import ROOT, Sandbox  # noqa: E402


class RulesBase(Sandbox):
    """`canon_root`:受測腳本看到的 `AC_HOME`(正本角色卡與模型卡住那裡,D-071 §3)。預設是沙盒
    自己(`install_rules_sources` 把 A 的卡複製進去了,沙盒就是正本);腳本照樣從 harness 的 A 跑。"""

    def setUp(self):
        super(RulesBase, self).setUp()
        self.install_rules_sources()
        self.canon_root = self.repo

    def ac_env(self):
        env = super(RulesBase, self).ac_env()
        # 沙盒 setUp 跑 git 時還沒有 `canon_root`:那時候用 harness 的,不影響受測腳本。
        env["AC_HOME"] = getattr(self, "canon_root", env["AC_HOME"])
        return env

    def rules(self, *args):
        return self.run_py("scripts/rules.py", *args)


class WhatItPacks(RulesBase):

    def test_it_points_at_the_three_files_instead_of_pasting_them_whole(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for pointer in ("memory/role/implementer.md", "memory/model/opus.md",
                        "docs/DISPATCH-TEMPLATE.md"):
            self.assertIn(pointer, done.stdout, "沒有指路就等於要對方自己猜")

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

    def test_the_reviewer_has_a_pack_of_its_own(self):
        """D-022:覆核改由短命 opus 做,而它上一分鐘還沒有角色卡也沒有規則包。"""
        done = self.rules("pack", "reviewer", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("memory/role/reviewer.md", done.stdout)

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
        """驗證者不需要 worker 交件那一整節(§1:它交的是案例,不是分支上的 commit)。"""
        worker = self.rules("pack", "worker", "--model", "opus").stdout
        # #74:指定了卻沒有卡的模型(以前是 sonnet)現在是錯 —— 換一個有卡的。
        verifier = self.rules("pack", "verifier", "--model", "fable").stdout
        self.assertNotEqual(worker, verifier)
        self.assertIn("在自己的 worktree / 分支 commit", worker)
        self.assertTrue(verifier.strip(), "驗證者的包是空的")
        self.assertNotIn("§1 在自己的 worktree", verifier)

    def test_the_worker_pack_says_commit_on_your_own_branch(self):
        """#144(D-054 Q9、Q12 例外):worker 在自己的 worktree / 分支 commit;規則包還帶著「禁一切 git
        寫入」的話,worker 會被兩邊矛盾的指令卡住,或乾脆不 commit。期望值 = 票面驗收 4 的片語
        (`install_rules_sources` 裝的是真的 DISPATCH-TEMPLATE 與角色卡)。驗證者仍交 patch-verify、不 git 寫入。
        **變異 M3**:§1 留「禁一切 git 寫入」→ 這一條紅。"""
        worker = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        for gone in ("禁一切 git 寫入", "不碰 git", "交 patch", "副本裡沒有 .git", "副本裡沒有 `.git`"):
            self.assertNotIn(gone, worker.stdout)
        for said in ("在自己的 worktree / 分支 commit", "不碰 main", "不 push", "不動別的分支"):
            self.assertIn(said, worker.stdout)
        verifier = self.rules("pack", "verifier", "--model", "opus")
        self.assertEqual(verifier.returncode, 0, verifier.stdout + verifier.stderr)
        self.assertIn("不 git 寫入", verifier.stdout)

    def test_the_main_line_does_not_review(self):
        """#91 C5(F33):D-022 之後覆核由 review.sh 派 reviewer,主線那一句不再寫「覆核」。

        那一句(`WANTED["main"]` 第三格)只有 `rules.py roles` 印,`pack` 不印(實測
        2026-10-06),所以從 `roles` 讀;`pack main` 只驗它照常產得出來。

        **變異**:把 `WANTED["main"]` 那一句改回「接需求、覆核、決定順序、發版」→ 這一條紅。
        """
        self.assertEqual(self.rules("pack", "main", "--model", "opus").returncode, 0)
        done = self.rules("roles")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [l for l in done.stdout.splitlines() if l.split()[:1] == ["main"]]
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertIn("接需求、決定順序、發版", lines[0])
        self.assertNotIn("覆核", lines[0])

    def test_it_names_the_version_it_was_cut_from(self):
        """規則包會被貼進派工文,而**一份不知道自己是哪一版的規則包**沒辦法被追。"""
        done = self.rules("pack", "worker", "--model", "opus")
        sha = self.git("rev-parse", "--short", "HEAD").strip()
        self.assertIn(sha, done.stdout)


class WhenASourceIsMissing(RulesBase):

    def test_the_fixed_sections_close_the_pack(self):
        """記憶回寫與結構化交付兩段固定文字永遠在包尾。
        **變異**:把 `build()` 的 layout 拿掉 `MEMORY_NOTE` → 紅;拿掉 `DELIVERY_NOTE` → 紅。
        """
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.rstrip("\n").endswith(WhatItAlwaysCarries.LAST_LINE),
                        done.stdout[-300:])
        self.assertIn(TheStructuredDeliverySection.LAST_LINE, done.stdout)
        self.assertNotIn("截斷", "\n".join(done.stdout.splitlines()[:3]))

    def test_a_section_that_is_no_longer_in_the_source_is_called_out(self):
        """名單與文件分岔時要**出聲**:一份靜靜少了兩節的規則包,與完整的那一份
        在畫面上長得一樣。#74 起少一節就不交:非零、stdout 沒有半包、stderr 點名。

        **變異**:把 `build()` 裡「找不到這一節」那一格的 `problem(...)` 拿掉 → 這一條紅。
        """
        text = self.read(os.path.join("docs", "DISPATCH-TEMPLATE.md"))
        self.write(os.path.join("docs", "DISPATCH-TEMPLATE.md"),
                   text.replace("## 2. 禁區", "## 2222. 換了號碼的禁區"))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        self.assertIn("找不到這一節", done.stderr)
        self.assertIn("§2 ", done.stderr)


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

    def test_a_named_model_without_a_card_is_an_error(self):
        """#74 取代「模型記憶還沒有的模型也要派得出工」:**指定了**卻沒有卡,與「這個模型
        沒踩過坑」長得一樣 —— 派出去的包少了整格,而沒有人知道。沒指定模型才不要卡。"""
        done = self.rules("pack", "worker", "--model", "nobody")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, "")
        self.assertIn("memory/model/nobody.md", done.stderr)
        unnamed = self.rules("pack", "worker")
        self.assertEqual(unnamed.returncode, 0, unnamed.stderr)
        self.assertIn("memory/role/implementer.md", unnamed.stdout)


class WhatItAlwaysCarries(RulesBase):
    """第五段「記憶回寫」是固定文字、先扣預算:每個角色都帶,砍預算時砍的是別份。

    **變異**:把 `pack()` 結尾接上 `MEMORY_NOTE` 那一行拿掉 → 這一組全紅。
    """

    ROLES = ("worker", "verifier", "opener", "main", "consolidator")
    LAST_LINE = "沒寫就寫「無」。"

    def test_every_role_gets_the_memory_writeback_section(self):
        """一個角色包一次,三段固定文字一起量(#72 併入
        TheStructuredDeliverySection.test_every_role_gets_the_section_within_the_cap 與
        EfficiencyOverSpeed.test_every_role_carries_the_line_in_the_first_three_lines)。

        **變異**:拿掉 `MEMORY_NOTE` → 記憶回寫那兩句紅;拿掉 `DELIVERY_NOTE` → 結構化交付
        那兩句紅;`pack()` 的 head 拿掉 `EFFICIENCY_NOTE` → 前三行那兩句紅。
        """
        for role in self.ROLES:
            done = self.rules("pack", role, "--model", "opus")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("## 記憶回寫", done.stdout, role)
            self.assertIn(self.LAST_LINE, done.stdout, role)
            self.assertIn("## 結構化交付", done.stdout, role)
            self.assertIn(TheStructuredDeliverySection.LAST_LINE, done.stdout, role)
            head = "\n".join(done.stdout.splitlines()[:3])
            self.assertIn("不追求快", head, role)
            self.assertIn("token", head, role)

    def test_stats_reports_the_section_bytes(self):
        """#72:三段固定文字的 `--stats` 三條併成一次呼叫。"""
        done = self.rules("pack", "worker", "--model", "opus", "--stats")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stderr, r"記憶回寫 \d+ bytes")
        self.assertRegex(done.stderr, r"結構化交付 \d+ bytes")
        self.assertRegex(done.stderr, r"不追求快 \d+ bytes")


class TheStructuredDeliverySection(RulesBase):
    """第六段「結構化交付」是固定文字、先扣預算(D-017,#20):交出來的東西一產生
    就要是機器讀得懂的,所以每個角色都帶得到這一句,而砍預算時砍的是別份。

    **變異**:把 `pack()` 結尾接上 `DELIVERY_NOTE` 那一段拿掉 → 這一組全紅。
    """

    LAST_LINE = "寫不出的留 `null`,不要編。"

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


class EfficiencyOverSpeed(RulesBase):
    """第一段「不追求快,只看效率」是固定文字、先扣預算(D-016,#16):產品負責人
    2026-09-21 原話 —— 因為「這樣比較快」而做事之前先算 token。放在標題之後、
    先讀清單之前,每個角色都帶,而且量在最前面三行裡,讀的人第一眼就看得到。

    **變異**:把 `pack()` 的 head 拿掉 `EFFICIENCY_NOTE` 那一行 → 這一組五個角色都紅
    (golden:第一條就是「拿掉這一段」的那一個)。
    """

    def test_it_sits_after_the_title_and_before_the_reading_list(self):
        done = self.rules("pack", "worker", "--model", "opus")
        lines = done.stdout.splitlines()
        self.assertTrue(lines[0].startswith("# 規則包:"), lines[0])
        title_at = done.stdout.index(lines[0])
        note_at = done.stdout.index("不追求快")
        # #74:逐檔的先讀清單併進各段標題(標題列就是路徑),第一段是角色卡。
        list_at = done.stdout.index("## 角色卡")
        self.assertTrue(title_at < note_at < list_at,
                        "D-016 那一行要在標題之後、第一段之前")


class TheProjectLayer(RulesBase):
    """疊在正本規矩上的**專案自己那一層**(D-021,#28)。

    `memory/` 永遠是「這個 repo 自己寫的」,`AC_HOME` 是「規矩從哪來」(#166)。根就是 `AC_HOME`
    時這個 repo 就是正本(agent-control 自己),不是時它是專案 —— 而專案的教訓寫在
    `memory/`,以前 `pack` 一個字都沒讀到:路徑錯 + 暫存區本來就不進包。少讀一次的代價是一輪落地紅。

    正本在測試自己搭的 `<home>/canon-home`(只有 `memory/{role,model}`),不是沙盒。

    **變異**:把 `pack()` 裡 `local = not is_canon(root)` 改成 `local = False`
    → 這一組除了「A 自己」那兩條之外全紅。
    """

    CANON_ROLE = "CANON-ROLE 正本角色卡說的那一句。"
    LOCAL_ROLE = "LOCAL-ROLE 這個 repo 自己補的那一句。"
    CANON_MODEL = "CANON-MODEL 正本模型記憶說的那一句。"
    LOCAL_MODEL = "LOCAL-MODEL 這個 repo 自己補的那一句。"
    PROJECT_NOTE = "PROJECT-BODY 專案備忘的內文不該進包。"

    def setUp(self):
        super(TheProjectLayer, self).setUp()
        self.canon_root = os.path.join(self.home, "canon-home")
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "# 正本角色卡\n%s\n" % self.CANON_ROLE, where=self.canon_root)
        self.write(os.path.join("memory", "model", "opus.md"),
                   "# 正本模型記憶\n%s\n" % self.CANON_MODEL, where=self.canon_root)
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "%s\n" % self.LOCAL_ROLE)
        self.write(os.path.join("memory", "role", "implementer.inbox.md"),
                   "- 第一條 INBOX-1\n- 第二條 INBOX-2\n- 第三條 INBOX-3\n")
        self.write(os.path.join("memory", "model", "opus.md"),
                   "%s\n" % self.LOCAL_MODEL)
        self.write(os.path.join("memory", "project", "foo.md"),
                   "- %s\n" % self.PROJECT_NOTE)

    def single_layer(self):
        """根就是 `AC_HOME` = 這個 repo 自己就是正本(agent-control)。"""
        self.canon_root = self.repo

    def test_both_layers_are_in_the_pack(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for phrase in (self.CANON_ROLE, self.LOCAL_ROLE, "INBOX-3",
                       self.CANON_MODEL, self.LOCAL_MODEL):
            self.assertIn(phrase, done.stdout, "疊上去的那一層漏了:" + phrase)

    def test_project_notes_are_a_path_not_a_paste(self):
        """專案層不設上限、會長;逐檔貼進包會讓派工文跟著長。所以只給**一個**速查入口,要看的那一次用 grep(D-013 第 4 條)。#74 量到
        逐檔清單在 T 占 1,121 B —— 檔名也不逐個列。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertIn("`memory/project/`", done.stdout)
        self.assertNotIn(os.path.join("memory", "project", "foo.md"), done.stdout)
        self.assertIn("memory/project/",
                      done.stdout[:done.stdout.index("## 角色卡")], "入口要在包頭")
        self.assertNotIn(self.PROJECT_NOTE, done.stdout)

    def test_the_inbox_is_carried_whole(self):
        """暫存區每一條都進包,沒有被截掉的行、沒有省略標記。"""
        self.write(os.path.join("memory", "role", "implementer.inbox.md"),
                   "".join("- 第%d條 INBOX-%d\n" % (i, i) for i in range(1, 60)))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        for i in range(1, 60):
            self.assertIn("- 第%d條 INBOX-%d\n" % (i, i), done.stdout)
        self.assertNotIn("\n…\n", done.stdout)

    def test_being_the_canon_repo_itself_reads_the_card_once(self):
        """根與 `AC_HOME` 是同一個目錄 = 這個 repo 就是正本。疊兩次的話
        角色卡會整份出現兩遍,而讀的人會以為那是兩份不同的規矩。"""
        self.single_layer()
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count(self.LOCAL_ROLE), 1)
        self.assertNotIn("本專案", done.stdout, "A 自己不該有「本專案」這個小標")
        # #72 併入原 test_being_the_canon_repo_does_not_list_the_project_layer(同一包)。
        self.assertNotIn(os.path.join("memory", "project", "foo.md"), done.stdout)

    def test_the_canon_repo_packs_its_own_inbox_too(self):
        """正本(A 自己)也帶自己的暫存區(#37):以前暫存區只從專案層取,而同步刻意
        不帶 inbox(D-021)—— 正本的 `*.inbox.md` 兩邊都沒有讀者。

        用開題者 + fable(票面 A1 量的那一組)。兩格都有字、每一行都是真實長度(最後
        一行約 190 B / 250 B)。

        **變異**:inbox 來源綁回 `local`(`(local_card_rel, local_model_rel)`)→ 紅。
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
        self.assertNotIn("本專案", done.stdout, "A 自己不該有「本專案」這個小標")

    def test_an_inbox_without_a_main_file_is_not_an_error(self):
        """主檔還沒有人寫、只有暫存區 —— 那不是壞掉,不要印「找不到」。"""
        os.remove(os.path.join(self.repo, "memory", "role", "implementer.md"))
        os.remove(os.path.join(self.repo, "memory", "model", "opus.md"))
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("找不到 memory/role", done.stdout)
        self.assertIn("INBOX-3", done.stdout, "主檔不在,暫存區還是要讀得到")

    def test_the_reading_list_points_at_both_layers(self):
        done = self.rules("pack", "worker", "--model", "opus")
        # #74:沒有獨立的先讀清單了,每一格的標題列就是它的路徑。
        head = "\n".join(line for line in done.stdout.splitlines() if line.startswith("#"))
        for pointer in ("## 角色卡 `$AC_HOME/memory/role/implementer.md`",
                        "### 本專案 `memory/role/implementer.md`",
                        "## 模型記憶 `$AC_HOME/memory/model/opus.md`",
                        "### 本專案 `memory/model/opus.md`"):
            self.assertIn(pointer, head)

    def test_roles_prints_the_canon_directory_not_a_hardcoded_one(self):
        """`roles` 那張表印 `$AC_HOME/memory/role/` 的實際目錄 —— 在專案裡指向專案的
        `memory/role/` 的話,讀的人會去改那一份,而那一份不是正本(#166 A2)。"""
        done = self.rules("roles")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(os.path.join(self.canon_root, "memory", "role", "implementer.md"),
                      done.stdout)
        self.assertNotIn(os.path.join(self.repo, "memory", "role"), done.stdout)


class CanonFromAcHome(RulesBase):
    """#166 A2:正本卡讀 `$AC_HOME/memory/{role,model}`,專案那一層讀 `<AC_ROOT>/memory/`,
    中間是 `### 本專案` 小標;根就是 `AC_HOME` 時只讀一次;`AC_HOME` 沒設 rc 2。

    票面寫 `memory/role/worker.md`;worker 的卡檔名是 `implementer.md`(`rules.py WANTED`),照實用它。

    **變異 m2**:pack 把專案的卡當正本讀 → CANON-MARK 不見,紅。"""

    def setUp(self):
        super(CanonFromAcHome, self).setUp()
        self.canon_root = os.path.join(self.home, "canon-home")
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "- CANON-MARK 正本那一句。\n", where=self.canon_root)
        self.write(os.path.join("memory", "model", "opus.md"),
                   "- CANON-MODEL 正本模型那一句。\n", where=self.canon_root)
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "- LOCAL-MARK 專案那一句。\n")

    def test_a_project_gets_canon_then_its_own_layer(self):
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = done.stdout
        at = [out.find("CANON-MARK"), out.find("### 本專案"), out.find("LOCAL-MARK")]
        self.assertNotIn(-1, at, out[:2000])
        self.assertEqual(at, sorted(at), "正本 → 本專案小標 → 專案那一句")

    def test_the_canon_repo_reads_its_card_once(self):
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "- CANON-MARK 正本那一句。\n")
        self.canon_root = self.repo
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count("CANON-MARK"), 1)
        self.assertNotIn("### 本專案", done.stdout)

    def test_a_model_only_the_project_has_is_its_card_and_says_so(self):
        """專案自己選的模型 A 沒有正本:專案那一張就是模型卡,小標說出 A 沒有;兩邊都沒有才是錯。

        **變異**:拿掉 build() 的「A 沒有正本」那一路 → 第一段 rc 1,紅。"""
        self.write(os.path.join("memory", "model", "house-model.md"), "- HOUSE-MODEL 專案模型那一句。\n")
        done = self.rules("pack", "worker", "--model", "house-model")
        self.assertEqual(done.returncode, 0, done.stderr)
        heads = [line for line in done.stdout.splitlines() if "HOUSE-MODEL" in line
                 or line.startswith("## 模型記憶")]
        self.assertTrue(heads and "A 沒有" in heads[0], heads)
        self.assertIn("HOUSE-MODEL", done.stdout)
        os.remove(os.path.join(self.repo, "memory", "model", "house-model.md"))
        done = self.rules("pack", "worker", "--model", "house-model")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn("memory/model/house-model.md", done.stderr)

    def test_without_ac_home_it_is_rc_2_and_says_so(self):
        env = self.env()
        env.pop("AC_HOME")
        done = self.run_py("scripts/rules.py", "pack", "worker", "--model", "opus", env=env)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn("AC_HOME", done.stderr)


def hundred_byte_lines(tag, count):
    """`count` 行、每行剛好 100 B(含換行);行首 `- <tag>-NN` 認得出是哪一格的哪一行。"""
    out = []
    for index in range(count):
        head = "- %s-%02d " % (tag, index)
        out.append(head + "x" * (99 - len(head)) + "\n")
    return "".join(out)


class TheWholeTextIsCarried(RulesBase):
    """沒有位元組上限(D-054 Q6):角色卡 + 暫存區合計 20 KB 也全進包,每一節的每一行都在。

    夾具自己寫,不吃真的 `memory/` 與共用規矩(那幾份會長會縮):角色卡 100 行 × 100 B、
    角色暫存區 100 行 × 100 B、模型記憶 15 行、共用規矩十節各 8 行。模型暫存區不在夾具裡 ——
    `install_rules_sources` 複製進來的那一份刪掉。
    """

    NUMS = ("1", "2", "3", "4", "5", "5.5", "5.7", "6.4", "7", "8")
    TITLE = "節" * 18
    INBOX = os.path.join("memory", "role", "implementer.inbox.md")

    def setUp(self):
        super(TheWholeTextIsCarried, self).setUp()
        self.write(os.path.join("memory", "role", "implementer.md"),
                   hundred_byte_lines("CARD", 100))
        self.write(os.path.join("memory", "model", "opus.md"), hundred_byte_lines("MODEL", 15))
        self.write(self.INBOX, hundred_byte_lines("INBOX", 100))
        os.remove(os.path.join(self.repo, "memory", "model", "opus.inbox.md"))
        self.write(os.path.join("docs", "DISPATCH-TEMPLATE.md"), "# 假的共用規矩\n\n" + "".join(
            "## %s. %s\n\n%s\n" % (num, self.TITLE, hundred_byte_lines("RULE" + num, 8))
            for num in self.NUMS))
        # 前提:卡 + 暫存區合計 20 KB。
        total = sum(len(self.read(rel).encode("utf-8"))
                    for rel in (os.path.join("memory", "role", "implementer.md"), self.INBOX))
        self.assertEqual(total, 20000)

    def lines_of(self, text, tag):
        return [line for line in text.split("\n") if line.startswith("- %s-" % tag)]

    def test_nothing_is_cut(self):
        """**變異 M1**:`build()` 又只留暫存區的尾巴 →  INBOX 那一句紅。"""
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        text = done.stdout
        self.assertGreater(len(text.encode("utf-8")), 20000)
        for tag, count in (("CARD", 100), ("MODEL", 15), ("INBOX", 100)) + tuple(
                ("RULE" + num, 8) for num in self.NUMS):
            self.assertEqual(len(self.lines_of(text, tag)), count, "%s:%s" % (tag, text[:300]))
        self.assertNotIn("\n…\n", text)


class TheFrontMatterIsNotContent(RulesBase):
    """front matter 不算正文(#110;#166 拿掉同步檔頭那一半,front matter 這一半留著)。

    **變異**:把 `units()` 剝 front matter 的那一段拿掉 → 紅。"""

    FRONT = ("---\nowner: implementer\nhistory:\n"
             "  - {date: 2026-09-30, ticket: \"#99\"}\n---\n")
    ROLE_FIRST = "FRONT-ROLE-1 角色卡正文的第一句。"
    ROLE_SECOND = "FRONT-ROLE-2 角色卡正文的第二句。"

    def test_units_start_at_the_first_sentence(self):
        rules = rules_module(ROOT)
        found = rules.units(self.FRONT + "%s\n%s\n" % (self.ROLE_FIRST, self.ROLE_SECOND))
        self.assertEqual(found, [self.ROLE_FIRST, self.ROLE_SECOND], found)


def rules_module(repo):
    sys.path.insert(0, os.path.join(repo, "scripts"))
    try:
        import importlib
        return importlib.import_module("rules")
    finally:
        sys.path.pop(0)


class TheRoutingFloorIsGone(RulesBase):
    """#121 A8(原 #87 A8 的下限拆除):`board/config.json` 還留著 `routing` 與 `model_tiers`,
    `pack` 也不再拿它們擋任何角色 —— 模型合不合法只由 `role-models.py` 判。期望值是這裡
    寫進 config 的 tiers 與 routing,不由 rules.py 算。"""

    TIERS = ["haiku", "sonnet", "opus", "fable"]

    def setUp(self):
        super(TheRoutingFloorIsGone, self).setUp()
        conf = json.loads(self.read("board/config.json"))
        conf["model_tiers"] = list(self.TIERS)
        conf["routing"] = {"implement": "opus", "verify": "opus", "open": "fable",
                           "main": "opus", "design": "fable"}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        for model in ("haiku", "sonnet"):
            self.write(os.path.join("memory", "model", "%s.md" % model),
                       "- %s 的沙盒卡\n" % model)

    def test_a_worker_below_the_old_floor_still_gets_a_pack(self):
        """T4。**變異 M5**:cmd_pack 留著舊下限(sonnet < routing.implement=opus 就 rc=2)
        → 這一條紅(rc 2、stdout 空)。量 rc 與 stdout;值 = A8(simple 票選到 sonnet
        照產包);舊值會在 rc=2 時紅;不靠沒設就退回(config 真的帶 routing + tiers)。"""
        for role, model in (("worker", "sonnet"), ("opener", "opus"),
                            ("verifier", "haiku"), ("design", "opus")):
            with self.subTest(role=role, model=model):
                done = self.rules("pack", role, "--model", model)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertTrue(done.stdout.strip(), done.stderr)
                self.assertNotIn("下限", done.stderr)

    def test_a_model_outside_the_tiers_is_not_mentioned(self):
        done = self.rules("pack", "worker", "--model", "codex:opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("model_tiers", done.stderr)
        self.assertNotIn("下限", done.stderr)

    def test_without_model_tiers_nothing_changes(self):
        conf = json.loads(self.read("board/config.json"))
        del conf["model_tiers"]
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        done = self.rules("pack", "worker", "--model", "nobody")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertNotIn("model_tiers", done.stderr)
        self.assertNotIn("下限", done.stderr)


if __name__ == "__main__":
    unittest.main()
