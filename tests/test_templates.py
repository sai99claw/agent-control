"""`templates/` 底下的派工範本:**存在,而且說得出這張票獨有的那幾件事**。

G2 / G8(`docs/FLOW.html` §G):主線 → 開題者、主線 → 整理者這兩條線以前沒有範本檔
(`templates/` 只有驗證者那一份),於是每一次派工都重寫一遍 —— 而**重寫的那一份少了哪
一格,沒有人看得出來**(§5.5 的母題)。

所以這一組只問兩件事:範本在不在、四件事的四個佔位在不在。不比對逐字內容:
範本是給人讀的,措辭會改;**格子少一個才是回歸**。
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")


def read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as handle:
        return handle.read()


class TheOpenerTemplate(unittest.TestCase):
    """A2:主線 → 開題者的派工有範本。"""

    def test_the_template_file_is_there(self):
        self.assertTrue(os.path.exists(os.path.join(TEMPLATES, "dispatch-opener.md")),
                        "templates/dispatch-opener.md 不在 —— 沒有範本就是每次重寫一遍")

    def test_it_carries_the_four_things_that_are_unique_to_this_ticket(self):
        """`memory/role/README.md`:派工 prompt 只指路 + **這張票獨有的四件事**。

        **變異**:把範本裡「這張票獨有的四件事」那一節拿掉 → 這一條紅。
        """
        text = read("dispatch-opener.md")
        self.assertIn("四件事", text)
        block = text.split("四件事", 1)[1].split("\n## ", 1)[0]
        for slot in ("題目", "base sha", "票庫路徑", "回報對象"):
            self.assertIn(slot, block, "四件事少了「%s」那一格" % slot)
        self.assertGreaterEqual(block.count("`<"), 4,
                                "四個佔位要真的是佔位(`<…>`),不是寫死的例子")

    def test_it_points_at_the_rules_pack_instead_of_pasting_the_rules(self):
        text = read("dispatch-opener.md")
        self.assertIn("rules.py pack opener", text)
        self.assertIn("memory/role/opener.md", text)

    def test_the_summary_has_a_field_to_land_in(self):
        """開題者的摘要以前沒有檔名、沒有格式、看板讀不到 —— 一段沒有落點的交付物,
        與沒有交付長得一樣。"""
        text = read("dispatch-opener.md")
        self.assertIn("outline", text)
        self.assertIn("--outline", text)

    def test_the_last_step_is_the_docs_channel_for_its_own_ticket(self):
        """#41 A1(D-025 ③):開票最後一步由開題者自己走 docs 通道提交票檔。
        指令逐字來自 D-025 ③ 與 `land.sh` docs 的用法例,不從範本反推。

        **變異 M1**:把那一句拿掉 → 紅。
        """
        text = read("dispatch-opener.md")
        self.assertIn("你要交的兩樣", text)
        block = text.split("你要交的兩樣", 1)[1].split("\n## ", 1)[0]
        self.assertIn('sh scripts/land.sh docs "tickets: #<n> 開票" tickets/<n>.json', block,
                      "「你要交的兩樣」之後要有一句可貼的 docs 通道指令")
        self.assertIn("只准自己那張票檔", block)


class TheOpenerCardHasOneGitException(unittest.TestCase):
    """#41 A2(D-025 ③):角色卡「不 git 寫入」原句不動,只加一句例外。"""

    def test_the_ban_stays_and_the_exception_is_one_sentence(self):
        """量「不做」那一行,不是整份:例外那一句若也寫了禁令字樣,整份 grep 關不掉變異。

        **變異 M2**:把「不做」那一句的「不 git 寫入」改掉 → 紅。
        """
        with open(os.path.join(ROOT, "memory", "role", "opener.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        ban = [line for line in lines if line.startswith("**不做**")]
        self.assertEqual(len(ban), 1, "「不做」那一行要在")
        self.assertIn("不 git 寫入", ban[0], "禁令原句要留著;例外是加一句,不是改掉禁令")
        exception = [line for line in lines if "land.sh docs" in line]
        self.assertEqual(len(exception), 1, "例外只有一句")
        for phrase in ("只准 land.sh docs", "只准自己那張票檔"):
            self.assertIn(phrase, exception[0])


class TheConsolidatorTemplate(unittest.TestCase):
    """A8:整理票開出來以後,**兩個 session 怎麼被派**有一句可以貼的指令。"""

    def test_the_template_file_is_there(self):
        self.assertTrue(
            os.path.exists(os.path.join(TEMPLATES, "dispatch-consolidator.md")))

    def test_it_says_two_models_each_get_their_own_session(self):
        text = read("dispatch-consolidator.md")
        self.assertIn("兩個模型", text)
        self.assertIn("discussions/", text, "討論檔的路徑")
        self.assertIn("memory.py consolidate", text, "收的那一句")
        self.assertIn("rules.py pack consolidator", text)

    def test_the_consolidator_has_its_own_role_card_now(self):
        """以前它**借用** `implementer.md` —— 一張寫著「交 patch.diff」的角色卡,
        對一個不交 patch 的角色說話。"""
        card = os.path.join(ROOT, "memory", "role", "consolidator.md")
        self.assertTrue(os.path.exists(card))
        with open(card, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertLessEqual(len(lines), 40, "角色卡 ≤ 40 行(memory/role/README.md)")


class TheVerifierEvidenceHasAReceiver(unittest.TestCase):
    """#29 A5 / G5:`EVIDENCE-verifier.md` 以前是**一份沒有收件者的交付物** ——
    角色卡要求交,而沒有任何入口讀它。

    這一組釘的是「文件說的」與「程式做的」對得上:兩份檔提到它 ≥ 1 次,而
    `apply.sh --help` 真的認得那個旗標。**兩邊分岔的那一份看起來仍然像規格**(D-018)。
    """

    def mentions(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
            return handle.read().count("EVIDENCE-verifier")

    def test_both_documents_still_ask_for_it(self):
        card = self.mentions(os.path.join("memory", "role", "verifier.md"))
        template = self.mentions(os.path.join("templates", "dispatch-verifier.md"))
        # 只斷 ≥ 1,**不比兩份相等**(#35):提及數相等量的是措辭,不是收件入口在不在
        # —— 正確的文件多寫一句就紅。收件入口在不在,由 `tests/test_ticket_29.py` 的 A5
        # 真的跑一次 `apply.sh --evidence-verifier` 看產物。
        self.assertGreaterEqual(card, 1)
        self.assertGreaterEqual(template, 1)

    def test_the_program_entry_really_exists(self):
        """**變異**:把 `apply.sh` 的 `--evidence-verifier` 那一格拿掉 → 這一條紅。"""
        import subprocess
        done = subprocess.run(["sh", os.path.join(ROOT, "scripts", "apply.sh"), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        # 量的是**「認得的旗標」那一段**,不是整份 `--help`:範例那一行也寫著這個旗標,
        # 所以整份比對時把參數表那一列拿掉照樣是綠的(實測 2026-09-23 的變異 M18)。
        # 「變異沒生效」與「守衛有漏洞」長得一樣,而這一次是守衛量錯了地方。
        flags = done.stdout.split("認得的旗標:", 1)[1].split("\n例:", 1)[0]
        self.assertIn("--evidence-verifier", flags,
                      "文件要它交,而程式沒有旗標收 —— 那就是 G5 本身")

    def test_the_two_documents_name_the_same_entry(self):
        for rel in (os.path.join("memory", "role", "verifier.md"),
                    os.path.join("templates", "dispatch-verifier.md")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("--evidence-verifier", text, rel)
            self.assertIn("result-verifier-round", text, rel)


class TheDesignSessionHasACardNow(unittest.TestCase):
    """A1:設計 session 是 D-019 每天在派、卻**唯一沒有角色卡**的角色。"""

    def cards(self):
        return os.path.join(ROOT, "memory", "role")

    def test_the_card_is_there_and_within_forty_lines(self):
        for name in ("design.md", "reviewer.md"):
            path = os.path.join(self.cards(), name)
            self.assertTrue(os.path.exists(path), name)
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            self.assertLessEqual(len(lines), 40, name + " 超過 40 行")

    def test_the_design_card_answers_the_five_questions(self):
        with open(os.path.join(self.cards(), "design.md"), encoding="utf-8") as handle:
            text = handle.read()
        for slot in ("讀什麼", "交什麼", "不做", "誰派", "何時結束"):
            self.assertIn(slot, text, "角色卡少了「%s」" % slot)
        self.assertIn("設計 session", text)

    def test_the_fixed_sections_of_a_design_document_are_written_down(self):
        """`docs/DESIGN-<題>.md` 以前沒有固定格式 —— 少了哪一段,下一個人分不出
        「這一題沒有那個面向」與「上一個人忘了寫」。"""
        with open(os.path.join(ROOT, "docs", "DESIGN.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("設計文件的固定段", text)
        self.assertIn("未來每票省/多花", text, "D-018 那一欄")
        self.assertIn("模型", text, "後續工作的模型表")


class TheProjectTemplate(unittest.TestCase):
    """#39 A9:專案的 CLAUDE.md 範本 —— A 契約的入口在專案裡叫什麼,寫在一張對照表裡。"""

    def text(self):
        return read("project-CLAUDE.md")

    def test_a9_the_first_paragraph_still_points_at_agent_control(self):
        paragraphs = [p for p in self.text().split("\n\n")
                      if p.strip() and not p.startswith("#")]
        self.assertIn("先讀 `../agent-control/CLAUDE.md`", paragraphs[0])

    def test_a9_the_mapping_table_names_the_five_entries(self):
        text = self.text()
        self.assertIn("## 對照表", text)
        section = text.split("## 對照表", 1)[1].split("\n## ", 1)[0]
        for entry in ("land.sh", "apply.sh", "inbox.py", "event.py", "ticket.py"):
            rows = [line for line in section.splitlines() if entry in line]
            self.assertTrue(rows, "對照表少了 %s" % entry)
            self.assertIn("<專案入口>", rows[0], entry + " 沒有對應到專案入口的那一格")

    def test_a9_the_hook_has_a_line(self):
        self.assertTrue([line for line in self.text().splitlines()
                         if "session-hook.sh" in line])


if __name__ == "__main__":
    unittest.main()
