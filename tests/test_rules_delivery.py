"""#74 主守衛:**agent 實際收到合法的規則,或完全不起動**。

#74 量到的:T 的規則包 rc=0、4 KB 以內,而 worker 的角色卡、模型卡、暫存區只剩標題與
「截斷」,reviewer 少五節;`auto-fix.sh` / `review.sh` 再用 `|| echo` 吞掉產包失敗,
照樣起 agent。所以這一組不問版面,問四件事:

1. 正常的包(正本 A 與泛用專案兩種版面、每一個角色):≤ 4,096 B、固定成本 ≤ 1,024 B、
   每一個必要項都有正文 —— **正文由這一組自己從來源檔挑**,不拿受測程式的切法量;
   `inspect` 的統計加起來等於 stdout 的實際 bytes。
2. 壞的來源(少一節、空的模型卡、一段放不下的正文):非零、stdout 一個字都沒有、
   decision 頁點名缺項、同一個缺項重試只有一頁。
3. 真正的派工入口(`auto-fix.sh` 第 1 輪 / 修復輪 / 驗證者兩條路、`review.sh`):包產不出來
   時 worker / verifier / reviewer 替身**一次都沒被起** —— 每一條都有一個「包好的時候
   替身真的會被起」的對照,不然零次也可能只是替身沒接上。
4. 讀這些包的另外兩個入口:派工文裡的角色 / 模型路徑在 `roles_dir=docs/roles` 的專案裡
   都真的存在、reviewer 沒有寫記憶的動作;`new-session.sh` 讓本地主卡整理前後都看得到、
   短命角色不拿全域交接;閘門動到這幾支時選得到這一組。

期望值來源:票面 #74 的驗收與規劃(4,096 / 1,024 / 600 這三個數、必要項的定義、
「不含標題 / front matter / 指路行」的正文定義)與 `docs/SESSION-START.md` 那張表。
"""

import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_harness import DEFAULT_CONFIG, ROOT, Sandbox, write_executable  # noqa: E402

MAX_BYTES = 4096
FIXED_MAX = 1024
# 每個角色要的角色卡與節(票面 #74 規劃 3:「角色正本、指定模型卡、每個設定要求的共用章節」)。
# 名單抄自 `rules.py WANTED` 的現況,**寫死在這裡**:名單被人刪掉一節時,這一組要紅。
WANT = {
    "worker": ("implementer", ("1", "2", "3", "4", "5", "5.5", "5.7", "6.4", "7", "8")),
    "verifier": ("verifier", ("0.5", "2", "3", "5.5", "6.4", "8")),
    "opener": ("opener", ("0", "5.5", "8")),
    "main": ("main", ("0", "5.5", "5.7", "6.5")),
    "consolidator": ("consolidator", ("0.5", "5.5", "8")),
    "design": ("design", ("0.5", "5.5", "6.4", "8")),
    "reviewer": ("reviewer", ("0.5", "3", "5.5", "6.4", "8")),
}
PROJECT_RULES = {"roles_dir": "docs/roles", "models_dir": "docs/roles/model"}
FIXTURE_MODEL = "- 沙盒替身模型:照派工文做,不猜。\n"
# 讓「少一節」成真的那一刀:5.5 每個角色都要。
BROKEN_HEADING = ("## 5.5 🩸", "## 9.95 🩸")
# 替身:被起一次就記一行角色名。
COUNTING_AGENT = """#!/bin/sh
echo "${AC_ROLE:-reviewer}" >> "%s"
"""
COUNTING_TEST_DEFECT_WORKER = """#!/bin/sh
echo "$AC_ROLE" >> "%s"
if [ "$AC_ROLE" = worker ]; then
    printf 'OBJECTION: test_defect fixture 把正確結果寫成 2\\n' > "$AC_WORK/EVIDENCE-round$AC_ROUND.md"
fi
"""
RED_LOG = """test_thing (test_thing.T.test_thing) ... FAIL

======================================================================
FAIL: test_thing (test_thing.T.test_thing)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_thing.py", line 6, in test_thing
    self.assertEqual(1, 2, "第一輪是紅的")
AssertionError: 1 != 2 : 第一輪是紅的

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
"""


def body_lines(text):
    """這一組自己的「正文行」:去掉 front matter、標題、空行;只留夠長、認得出來的行。
    不拿 `rules.units()` —— 那是受測的切法。"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = [i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"]
        lines = lines[end[0] + 1:] if end else lines
    return [line.strip() for line in lines
            if line.strip() and not line.lstrip().startswith("#")
            and len(line.strip().encode("utf-8")) >= 12]


def section_text(doc, num):
    """共用規矩裡 `num` 那一節的內文:從標題到下一個標題,自己用 regex 切。"""
    found = re.search(r"^#{2,4} %s\.?\s.*$" % re.escape(num), doc, re.M)
    assert found, "夾具的共用規矩裡沒有 §%s" % num
    rest = doc[found.end():]
    nxt = re.search(r"^#{1,4} ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class RulesBox(Sandbox):
    """裝好正本 A 的規則來源(`install_rules_sources`)+ 假模型的卡。"""

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"), FIXTURE_MODEL)

    def rules(self, *args, **env):
        return self.run_py("scripts/rules.py", *args, env=self.env(**env))

    def break_section(self, rel=os.path.join("docs", "DISPATCH-TEMPLATE.md")):
        text = self.read(rel)
        self.assertEqual(text.count(BROKEN_HEADING[0]), 1, "夾具前提:§5.5 的標題只有一個")
        self.write(rel, text.replace(*BROKEN_HEADING))

    def decision_rows(self):
        path = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        if not os.path.exists(path):
            return []
        return [json.loads(line) for line in read(path).splitlines() if line.strip()]

    def commit(self, message="沙盒:規則包的來源"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        self.git("push", "-q", "origin", "main")


def make_project(box, local_cards=("implementer",), local_model=True):
    """泛用專案的版面:正本角色卡 / 模型卡同步在 `docs/roles/`,共用規矩叫
    `docs/DISPATCH-COMMON-RULES.md`,`memory/` 只有這個專案自己寫的(主卡、暫存區、備忘)。"""
    repo = box.repo
    for kind, dest in (("role", os.path.join("docs", "roles")),
                       ("model", os.path.join("docs", "roles", "model"))):
        os.makedirs(os.path.join(repo, dest), exist_ok=True)
        where = os.path.join(repo, "memory", kind)
        for name in sorted(os.listdir(where)):
            if name.endswith(".md") and not name.endswith(".inbox.md") and name != "README.md":
                shutil.move(os.path.join(where, name), os.path.join(repo, dest, name))
    shutil.move(os.path.join(repo, "docs", "DISPATCH-TEMPLATE.md"),
                os.path.join(repo, "docs", "DISPATCH-COMMON-RULES.md"))
    for card in local_cards:
        box.write(os.path.join("memory", "role", "%s.md" % card),
                  "- LOCAL-%s 本專案:跑測試前先確認連線字串不是正式環境。\n" % card.upper())
    if local_model:
        box.write(os.path.join("memory", "model", "opus.md"),
                  "- LOCAL-OPUS 本專案:改設定前先備份那一份檔。\n")
    box.write(os.path.join("memory", "project", "facts.md"), "- 專案事實一條\n")
    box.write(os.path.join("docs", "CODE-MAP.md"), "# 速查\n- 入口在 src/\n")


class NormalPacks(RulesBox):
    """驗收 1:正常的包,每一個角色都是合法的包。"""

    def sources(self, role, model="opus"):
        """`[(這一格要在包裡留下正文的來源檔, 那一份的文字)]` —— 由這一組自己決定,
        照票面「必要項」的定義:角色卡、指定的模型卡、名單上每一節、存在且有正文的本地
        主卡與暫存區。"""
        conf = json.loads(self.read("board/config.json")).get("rules") or {}
        roles = conf.get("roles_dir") or os.path.join("memory", "role")
        models = conf.get("models_dir") or os.path.join("memory", "model")
        card, nums = WANT[role]
        out = [(os.path.join(roles, card + ".md"), None),
               (os.path.join(models, model + ".md"), None)]
        rules_rel = next(rel for rel in (os.path.join("docs", "DISPATCH-COMMON-RULES.md"),
                                         os.path.join("docs", "DISPATCH-TEMPLATE.md"))
                         if self.exists(rel))
        doc = self.read(rules_rel)
        out += [("%s §%s" % (rules_rel, num), section_text(doc, num)) for num in nums]
        local = os.path.normpath(roles) != os.path.normpath(os.path.join("memory", "role"))
        extra = [os.path.join("memory", "role", card + ".inbox.md"),
                 os.path.join("memory", "model", model + ".inbox.md")]
        if local:
            extra += [os.path.join("memory", "role", card + ".md"),
                      os.path.join("memory", "model", model + ".md")]
        out += [(rel, None) for rel in extra if self.exists(rel) and self.read(rel).strip()]
        return [(rel, text if text is not None else self.read(rel)) for rel, text in out]

    def assert_legal(self, role, model="opus"):
        seen = self.rules("inspect", role, "--model", model, "--json")
        self.assertEqual(seen.returncode, 0, seen.stdout + seen.stderr)
        data = json.loads(seen.stdout)
        done = self.rules("pack", role, "--model", model)
        self.assertEqual(done.returncode, 0, done.stderr)
        size = len(done.stdout.encode("utf-8"))
        self.assertLessEqual(size, MAX_BYTES, role)
        self.assertLessEqual(data["fixed_bytes"], FIXED_MAX, role)
        self.assertEqual(data["total_bytes"], size, "%s:inspect 的 total 與 stdout 對不上" % role)
        self.assertEqual(data["fixed_bytes"] + sum(row["rendered_bytes"]
                                                   for row in data["sections"]),
                         size, "%s:固定成本 + 各格正文 ≠ 實際 bytes" % role)
        for row in data["sections"]:
            self.assertGreaterEqual(row["content_units"], 1, "%s %s" % (role, row))
            self.assertGreater(row["rendered_bytes"], 0, "%s %s" % (role, row))
        # 逐項:每一個必要來源至少有一行正文**原樣**出現在包裡。
        lines = set(line.strip() for line in done.stdout.splitlines())
        for rel, text in self.sources(role, model):
            candidates = body_lines(text)
            self.assertTrue(candidates, "夾具前提:%s 有正文" % rel)
            if rel.endswith(".inbox.md"):
                candidates = candidates[-1:]
            self.assertTrue([one for one in candidates if one in lines],
                            "%s 的 %s 在包裡沒有任何一行正文:\n%s" % (role, rel, done.stdout))
        return done.stdout

    def test_every_role_in_the_canon_layout(self):
        """四問:守「正本 A 每個角色的包都合法」;回歸是 #74 那種只剩標題的包(固定文字
        2,349 B 把正文擠空);舊測試只看總長與標題字串;不加測試專用 API(`inspect` 是
        派工與維護的介面)。"""
        for role in WANT:
            with self.subTest(role=role):
                self.assert_legal(role)

    def test_every_role_in_a_generic_project_layout(self):
        """四問:守「`roles_dir=docs/roles` 的專案每個角色的包都合法、本地主卡與暫存區
        都留正文」;回歸是專案層疊上去後固定成本超標或本地格被砍成標題;舊測試只用
        一句話的假卡;不加測試專用 API。"""
        conf = json.loads(self.read("board/config.json"))
        conf["rules"] = PROJECT_RULES
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        make_project(self, local_cards=tuple(card for card, _nums in WANT.values()))
        for role in WANT:
            with self.subTest(role=role):
                text = self.assert_legal(role)
                self.assertIn("LOCAL-%s" % WANT[role][0].upper(), text)
                self.assertIn("LOCAL-OPUS", text)
                self.assertNotIn("memory/project/facts.md", text, "專案備忘不逐檔列")


class BrokenSourcesStopThePack(RulesBox):
    """驗收 2:壞的來源 —— 非零、stdout 沒有半包、decision 頁點名、重試不重送。"""

    def assert_refused(self, done, *named):
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "", "stdout 留了半包")
        rows = [row for row in self.decision_rows() if row.get("kind") == "decision"]
        self.assertEqual(len(rows), 1, rows)
        for one in named:
            self.assertIn(one, done.stderr)
            self.assertIn(one, rows[0]["what"], "decision 頁沒點名 %s" % one)
        return rows[0]

    def test_a_missing_section(self):
        """四問:守「名單上的一節不在 → 不交」;回歸是舊版印一句警告照樣 rc=0;舊測試
        斷言的正是 rc=0;不加測試專用 API。

        **變異**:`cmd_pack` 拿掉 `return 1`(改回印包、rc=0)→ 這一條紅。
        """
        self.break_section()
        row = self.assert_refused(self.rules("pack", "worker", "--model", "opus"), "§5.5")
        self.assertEqual(row["ticket"], "rules", "沒有 AC_TICKET 時綁維護識別,不偽造票號")
        again = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(again.returncode, 1)
        self.assertEqual(len(self.decision_rows()), 1, "同一個缺項重試又送了一頁")
        self.assertIn("不重送", again.stderr)
        bound = self.rules("pack", "worker", "--model", "opus", AC_TICKET="7")
        self.assertEqual(bound.returncode, 1)
        self.assertEqual([row["ticket"] for row in self.decision_rows()], ["rules", "7"])

    def test_an_empty_model_card(self):
        """四問:守「指定的模型卡只剩 front matter 與標題 = 缺項」;回歸是 #74 的模型卡
        被擠成只有標題;舊測試沒有這一格;不加測試專用 API。"""
        self.write(os.path.join("memory", "model", "opus.md"),
                   "---\ncap_chars: 2000\ncap_history: []\n---\n# Opus 的行為準則\n\n"
                   "- `docs/DISPATCH-TEMPLATE.md`\n來源:整理票 #12\n")
        self.assert_refused(self.rules("pack", "worker", "--model", "opus"),
                            "memory/model/opus.md", "沒有正文")

    def test_an_unbreakable_unit_that_does_not_fit(self):
        """四問:守「一段放不下又不能切半句的正文 → 不交」;回歸是舊版在行中間砍或整份
        從尾巴砍;舊測試只看總長;不加測試專用 API。"""
        self.write(os.path.join("memory", "role", "implementer.md"),
                   "# 實作者\n" + "這一條規矩很長" * 300 + "。\n")
        self.assert_refused(self.rules("pack", "worker", "--model", "opus"),
                            "memory/role/implementer.md", "不可分")

    def test_a_page_that_cannot_be_written_is_still_nonzero(self):
        """四問:守「decision 頁寫不出來時照樣非零、說原因」;回歸是寫頁失敗被吞成 rc=0;
        舊測試沒有這條路;不加測試專用 API。"""
        self.break_section()
        os.makedirs(os.path.join(self.repo, "reports"))
        self.write(os.path.join("reports", "inbox"), "不是目錄\n")
        done = self.rules("pack", "worker", "--model", "opus")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertIn("decision 頁寫不出來", done.stderr)

    def test_inspect_is_read_only_and_names_the_same_gap(self):
        """四問:守「inspect 唯讀、與 pack 判同一件事」;回歸是診斷介面自己發頁或說法與
        pack 不同;舊測試沒有 inspect;inspect 是票面要的維護介面,不是測試專用。"""
        self.break_section()
        before = len(self.events())
        seen = self.rules("inspect", "worker", "--model", "opus", "--json")
        self.assertEqual(seen.returncode, 1, seen.stderr)
        data = json.loads(seen.stdout)
        self.assertFalse(data["ok"])
        self.assertIn("§5.5", [row["item"] for row in data["missing"]])
        self.assertEqual(self.decision_rows(), [], "inspect 送了頁")
        self.assertEqual(len(self.events()), before, "inspect 發了事件")


class TheDispatchEntriesStartNothing(RulesBox):
    """驗收 3:經真正的派工入口,包產不出來時替身的啟動計數是零。每一條先跑對照組
    (包是好的 → 替身真的被起),再跑壞的那一組。"""

    def setUp(self):
        super().setUp()
        self.starts = os.path.join(self.home, "starts.log")
        self.agent = os.path.join(self.home, "agent.sh")
        write_executable(self.agent, COUNTING_AGENT % self.starts)
        self.set_config()
        self.commit()

    def set_config(self, routing_verify=None, agent=None):
        conf = json.loads(self.read("board/config.json"))
        conf["worker"] = {"command": "sh %s" % (agent or self.agent), "timeout_seconds": 60}
        conf["reviewer"] = {"command": "sh %s --model fixture-model" % self.agent,
                            "timeout_seconds": 60}
        conf["routing"] = dict(DEFAULT_CONFIG["routing"])
        if routing_verify:
            conf["routing"]["verify"] = routing_verify
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def started(self):
        if not os.path.exists(self.starts):
            return []
        return [line.strip() for line in read(self.starts).splitlines() if line.strip()]

    def ready_ticket(self, ident="1", verifier=True):
        self.make_ticket(ident, allowed_write_paths=["tests/*"], needs_verifier=verifier,
                         interface_fixed=True)
        self.commit("開票 #%s" % ident)

    def red_round(self, ident="2"):
        """第 1 輪已經紅過的票:狀態檔直接寫(要測的是 auto-fix 怎麼讀它)。"""
        self.make_ticket(ident, allowed_write_paths=["tests/*"], state="Running",
                         needs_verifier=False)
        self.commit("開票 #%s" % ident)
        log = self.write("round.log", RED_LOG, where=self.home)
        base = self.git("rev-parse", "main").strip()
        run = "20261001-100000-%s" % ident
        self.run_py("scripts/status.py", "start", "--ticket", ident, "--kind", "gate",
                    "--run-id", run, "--base-sha", base, "--round", "1",
                    "--worktree", self.repo)
        self.run_py("scripts/status.py", "done", "--ticket", ident, "--kind", "gate",
                    "--run-id", run, "--rc", "1", "--log", log)

    def test_first_round_worker_and_verifier(self):
        """四問:守「第 1 輪 worker 與平行驗證者在包壞時都不起」;回歸是 `|| echo` 吞掉
        rc 照派;舊測試沒有包壞的案例;不加測試專用 API。

        **變異**:`first_round_packet` 的 `rules_pack ... || return 1` 改成 `|| true` → 紅。
        """
        self.ready_ticket("1")
        good = self.run_sh("scripts/auto-fix.sh", "1")
        self.assertIn("worker", self.started(), "對照組:包是好的時候替身要被起\n" + good.stderr)
        self.assertIn("verifier", self.started(), "對照組:平行驗證者要被起\n" + good.stderr)
        os.remove(self.starts)
        # 壞的那一張不要驗證者:驗證者的包也會壞,它的失敗會替 worker 那一條擋下來,
        # 拿掉 worker 那一條的錯誤傳遞也不會紅(驗證者那一條另有一個案例)。
        self.ready_ticket("3", verifier=False)
        self.break_section()
        done = self.run_sh("scripts/auto-fix.sh", "3")
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        self.assertEqual(self.started(), [], "包產不出來卻起了 agent")
        self.assertEqual(self.load_ticket("3")["state"], "Ready", "沒起 agent,票不該轉 Running")
        dry = self.run_sh("scripts/auto-fix.sh", "3", "--dry-run", "--round", "1")
        self.assertEqual(dry.returncode, 6, dry.stdout + dry.stderr)
        self.assertNotIn("# 規則包", dry.stdout, "--dry-run 印出了半份派工文")

    def test_first_round_a_verifier_pack_alone_stops_the_worker(self):
        """四問:守「驗證者的包壞(模型卡不在)時 worker 也不起」;回歸是只看 worker 那一份;
        舊測試沒有;不加測試專用 API。

        **變異**:`round_once` 裡 `verifier_packet ... || { ROUND_RC=6; return 1; }` 拿掉 → 紅。
        """
        self.set_config(routing_verify="ghost-model")
        self.commit("驗證者的模型沒有卡")
        self.ready_ticket("1")
        done = self.run_sh("scripts/auto-fix.sh", "1")
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        self.assertEqual(self.started(), [])
        self.assertIn("ghost-model", done.stderr)

    def test_a_repair_round(self):
        """四問:守「修復輪(第 2 輪起)包壞不起 worker」;回歸是修復輪那一份另有 `|| echo`;
        舊測試沒有;不加測試專用 API。

        **變異**:`round_once` 修復輪的 `rules_pack` 失敗分支拿掉 → 紅。
        """
        self.red_round("2")
        good = self.run_sh("scripts/auto-fix.sh", "2")
        self.assertEqual(self.started(), ["worker"], "對照組\n" + good.stdout + good.stderr)
        os.remove(self.starts)
        self.red_round("4")
        self.break_section()
        done = self.run_sh("scripts/auto-fix.sh", "4")
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        self.assertEqual(self.started(), [])

    def test_the_test_defect_verifier(self):
        """四問:守「worker 提 test_defect 後,驗證者的包壞就不起驗證者」;回歸是
        `dispatch_verifier` 照起;舊測試沒有;不加測試專用 API。

        **變異**:`dispatch_verifier` 的 `verifier_packet` 失敗分支拿掉 → 紅(verifier 被起)。
        """
        script = os.path.join(self.home, "defect.sh")
        write_executable(script, COUNTING_TEST_DEFECT_WORKER % self.starts)
        self.set_config(routing_verify="ghost-model", agent=script)
        self.commit("驗證者的模型沒有卡")
        self.red_round("2")
        done = self.run_sh("scripts/auto-fix.sh", "2")
        self.assertEqual(done.returncode, 6, done.stdout + done.stderr)
        self.assertEqual(self.started(), ["worker"], "worker 起了一次、驗證者不該起")

    def test_the_reviewer(self):
        """四問:守「review.sh 包壞不起 reviewer」;回歸是 `|| echo` 照派;舊測試沒有;
        不加測試專用 API。

        **變異**:`review.sh` 的 `|| refuse ...` 拿掉 → 紅。
        """
        for ident in ("1", "5"):
            self.make_ticket(ident, state="InReview")
        self.commit("開票")
        for ident in ("1", "5"):
            self.commit_in(self.worktree("t%s" % ident), "thing%s.txt" % ident, "#%s" % ident)
        good = self.run_sh("scripts/review.sh", "1")
        self.assertEqual(self.started(), ["reviewer"], "對照組\n" + good.stdout + good.stderr)
        os.remove(self.starts)
        self.break_section()
        done = self.run_sh("scripts/review.sh", "5")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.started(), [])
        self.assertIn("review.refused", [row["kind"] for row in self.events()])


class ProjectPacketsPointAtRealFiles(RulesBox):
    """驗收 4 前半:`roles_dir=docs/roles` 的專案裡,派工文提到的角色 / 模型路徑都真的存在。
    本地只有 implementer 的主卡、沒有本地模型卡 —— 寫死 `memory/role/verifier.md`、
    `memory/model/<模型>.md` 的派工文在這裡會指到不存在的檔。"""

    config_extra = {"rules": PROJECT_RULES}
    PATH = re.compile(r"`((?:memory|docs/roles)/[^`\s<>@]*\.md)`")

    def setUp(self):
        super().setUp()
        make_project(self, local_cards=("implementer",), local_model=False)

    def assert_paths_exist(self, text, must):
        found = set(self.PATH.findall(text))
        self.assertIn(must, found, "派工文沒有指到角色卡:\n" + text)
        gone = sorted(rel for rel in found if not self.exists(rel))
        self.assertEqual(gone, [], "派工文指到不存在的檔")

    def test_the_first_round_worker_and_verifier_packets(self):
        """四問:守「worker / verifier 派工文的角色與模型入口存在」;回歸是範本寫死
        `memory/role/*.md`;舊測試只在正本版面跑;不加測試專用 API。"""
        self.make_ticket("1", allowed_write_paths=["tests/*"], needs_verifier=True,
                         interface_fixed=True)
        self.commit("開票 #1")
        done = self.run_sh("scripts/auto-fix.sh", "1", "--dry-run", "--round", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        run = os.path.join(self.repo, "reports", "t1")
        packets = {name: read(os.path.join(run, sub, name)) for sub in os.listdir(run)
                   for name in os.listdir(os.path.join(run, sub)) if name.startswith("dispatch")}
        self.assertEqual(sorted(packets), ["dispatch-round1.md", "dispatch-verifier-round1.md"])
        self.assert_paths_exist(packets["dispatch-round1.md"], "docs/roles/implementer.md")
        self.assert_paths_exist(packets["dispatch-verifier-round1.md"], "docs/roles/verifier.md")

    def test_the_reviewer_packet_has_no_memory_write(self):
        """四問:守「reviewer 派工文的入口存在、而且沒有叫唯讀角色寫記憶」;回歸是包裡的
        `memory.py note` 一段照給 reviewer;舊測試沒看這一格;不加測試專用 API。

        **變異**:`build()` 不分角色一律接 `MEMORY_NOTE` → 紅。
        """
        conf = json.loads(self.read("board/config.json"))
        stdin = os.path.join(self.home, "reviewer-stdin.md")
        agent = os.path.join(self.home, "reviewer.sh")
        write_executable(agent, "#!/bin/sh\ncat > %s\n" % stdin)
        conf["reviewer"] = {"command": "sh %s --model opus" % agent, "timeout_seconds": 60}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket("1", state="InReview")
        self.commit("開票 #1")
        self.commit_in(self.worktree("t1"), "thing.txt", "#1")
        self.run_sh("scripts/review.sh", "1")
        text = read(stdin)
        self.assert_paths_exist(text, "docs/roles/reviewer.md")
        self.assertNotIn("memory.py note", text)
        self.assertIn("候選記憶", text)


class TheTemplatesDoNotHardcodeTheRoleDirectory(unittest.TestCase):
    """驗收 4 前半的另一面:人手派的兩份範本(開題者、整理者)不在沙盒裡跑,直接讀。"""

    def test_no_template_names_a_memory_card_path(self):
        """四問:守「範本引用 pack 解析的來源,不寫死角色卡 / 模型卡路徑」;回歸是專案裡
        指到不存在的 `memory/role/*.md`;舊測試沒掃;不加測試專用 API。"""
        where = os.path.join(ROOT, "templates")
        for name in sorted(os.listdir(where)):
            if not name.startswith("dispatch-"):
                continue
            text = read(os.path.join(where, name))
            self.assertFalse(re.findall(r"`memory/(?:role|model)/[^`]*`", text), name)


class NewSessionForEachRole(RulesBox):
    """驗收 5:`new-session.sh` 的本地主卡整理前後都看得到;短命角色沒有全域交接。"""

    config_extra = {"rules": PROJECT_RULES}
    LESSON = "- LOCAL-MAIN-LESSON-74 本專案:落地前先看 git status。"

    def setUp(self):
        super().setUp()
        self.write(os.path.join("docs", "roles", "contract.md"), "## 不可違反的\n- RULE-MARK\n")
        self.write(os.path.join("docs", "roles", "main.md"), "# 主線\n- 正本主線卡的一條。\n")
        self.write(os.path.join("docs", "roles", "model", "fable.md"), "- fable 的一條。\n")
        self.write(os.path.join("docs", "HANDOFF.md"), "# 交接\n")
        self.make_ticket(9, state="Ready", subject="OPEN-TICKET-74")
        # 主卡還沒有,而 `install_rules_sources` 帶進來的正本主卡在這個專案裡不是它的。
        os.remove(os.path.join(self.repo, "memory", "role", "main.md"))
        # 工作樹乾淨:第 1 段的 `git status` 不然會把沒 commit 的 `docs/HANDOFF.md` 列出來。
        self.commit()

    def page(self, role, model):
        done = self.run_sh("scripts/new-session.sh", role, model, "--no-event")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def test_the_local_card_is_visible_before_and_after_consolidation(self):
        """四問:守「本地教訓整理進主卡後主線頁還看得到」;回歸是頁面只印暫存區尾巴,
        整理完那一條就消失;舊測試只有暫存區;不加測試專用 API。

        **變異**:`top()` 拿掉印本地主卡那一段 → 「整理後」那一句紅。
        """
        self.write(os.path.join("memory", "role", "main.inbox.md"), self.LESSON + "\n")
        before = self.page("main", "fable")
        self.assertIn(self.LESSON, before.split("1. 站在哪個版本")[0], "整理前看不到")
        self.write(os.path.join("memory", "role", "main.md"), self.LESSON + "\n")
        self.write(os.path.join("memory", "role", "main.inbox.md"), "")
        after = self.page("main", "fable")
        self.assertIn(self.LESSON, after.split("1. 站在哪個版本")[0], "整理後看不到")

    def test_short_lived_roles_get_no_global_handoff(self):
        """四問:守「短命角色不拿 HANDOFF、事件流、開票清單」;回歸是 new-session 對每個
        角色印同一頁;舊測試斷言的正是 worker 也有;不加測試專用 API。

        **變異**:`new-session.sh` 第 2–3 段外面的 `if [ "$ROLE" = "main" ]` 拿掉 → 紅。
        """
        main = self.page("main", "fable")
        for marker in ("docs/HANDOFF.md", "最近發生的事", "OPEN-TICKET-74"):
            self.assertIn(marker, main, "對照組:主線要有 %s" % marker)
        for role in ("worker", "verifier", "opener", "reviewer"):
            out = self.page(role, "fable")
            for marker in ("docs/HANDOFF.md", "最近發生的事", "OPEN-TICKET-74"):
                self.assertNotIn(marker, out, "%s 拿到了 %s" % (role, marker))


PASSING = """import os
import unittest


class T(unittest.TestCase):
    def test_it_ran(self):
        with open(os.environ["AC_TEST_LOG"], "a", encoding="utf-8") as handle:
            handle.write("%s\\n")
"""


class TheGatePicksThisGuard(Sandbox):
    """驗收 6 的一半:閘門的對照表動到這幾支時選得到這一組(替身模組只記名字)。"""

    MODULES = ("test_rules_delivery", "test_rules", "test_auto_fix", "test_auto_fix_codex",
               "test_review",
               "test_new_session", "test_templates", "test_dispatch_template",
               "test_no_project_names")

    def setUp(self):
        super().setUp()
        for name in self.MODULES:
            self.write(os.path.join("tests", "%s.py" % name), PASSING % name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "沙盒的替身測試模組")

    def test_each_entry_selects_the_guard(self):
        """四問:守「改規則包或任一個派工入口時,閘門會跑這一組」;回歸是對照表只掛
        test_rules,主守衛永遠不被選到;舊測試沒有;不加測試專用 API。"""
        for rel in ("scripts/rules.py", "scripts/auto-fix.sh", "scripts/review.sh",
                    "scripts/new-session.sh", "templates/dispatch-verifier.md",
                    "templates/dispatch-reviewer.md", "templates/dispatch-opener.md"):
            with self.subTest(rel=rel):
                if os.path.exists(self.log):
                    os.remove(self.log)
                done = self.run_sh("scripts/gate.sh", rel)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                ran = read(self.log).split() if os.path.exists(self.log) else []
                self.assertIn("test_rules_delivery", ran, done.stdout)


if __name__ == "__main__":
    unittest.main()
