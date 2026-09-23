"""驗證者案例 —— #20:模型的輸出一產生就是機器可讀的。

票面驗收(行為 + 功能標籤)由驗證者自己寫成案例(角色卡 D-G122),不讀實作者的
EVIDENCE。這一組獨立問票面 `test_plan` 的九件事:

1. `rules.py pack` 四個角色 + consolidator 都帶得到「結構化交付」那一段,單段
   ≤600 bytes、總量仍 ≤4096 bytes;
2. 正常路徑:worker 交出合法的 `result` 區塊,`auto-fix.sh` 在收 patch 的同一手
   把它抽成 `reports/t<票號>/<run_id>/result-round<輪>.json`,十一個鍵一個不少;
5. 同一次抽取不會把 `memory[]`(鏡像)又寫進記憶收件匣一次(#10 的邊界);
3. 三種缺漏(沒有 EVIDENCE / 有 EVIDENCE 沒那一塊 / 那一塊解不開)各留各的痕跡,
   不揉成同一個空檔(`docs/DISPATCH-TEMPLATE.md` §5.5);
4. `OBJECTION:` 那一行與 block 的 `objection.category` 對不上時,既有的收件 /
   轉 Blocked / 退出碼一個字不改,只多一格 `conflict: true`;
6. 驗證者那條路寫的是 `result-verifier-round<輪>.json`,檔名不一樣;
7. schema 的鍵名只活在 `docs/DISPATCH-TEMPLATE.md`、`tickets/SCHEMA.md` 與測試
   夾具裡 —— **判準是鍵名(`patch_sha256` + `red_first_line`),不是那個 fenced
   標記本身**:`scripts/auto-fix.sh` 與 `scripts/rules.py` 合法地帶有 `result-round`
   / `no-block` / fenced 標記等詞,但不抄鍵名(2026-09-22 派工裁示);
8. `memory/role/implementer.md`、`memory/role/verifier.md` 各多一句,但都不超過
   `board/config.json` 的 `memory.cap_chars`,`memory.py check` 不因這兩張卡開票。

worker 用一支這個案例**自己的**假可執行檔(`board/config.json` 的
`worker.command`)。沙盒借用 `tests/control_harness.Sandbox`(拋棄式真 git repo,
真的把 `auto-fix.sh` / `apply.sh` / `gate.sh` 起成子行程)—— 這是這個 repo 驗
`auto-fix.sh` 的唯一形狀,重寫一份等於測自己另外寫的模擬器
(`docs/DISPATCH-TEMPLATE.md` §5.6)。夾具的檔名、文字、`patch_sha256` 等值全部
自建,不引用實作者 EVIDENCE 裡的任何一個字。
"""

import glob
import json
import os
import re
import subprocess
import sys
import unittest

TAGS = ["agent-output"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS_DIR)
from control_harness import DEFAULT_CONFIG, Sandbox, write_executable  # noqa: E402

ROLES = ("worker", "verifier", "opener", "main", "consolidator")

# ---------------------------------------------------------------- item 1

class PackCarriesABoundedDeliverySection(unittest.TestCase):
    """#20 驗收①:規則包每個角色都帶得到「結構化交付」,單段有自己的預算。"""

    def test_every_role_and_consolidator_stay_within_budget(self):
        for role in ROLES:
            done = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "rules.py"),
                 "pack", role, "--model", "opus", "--stats"],
                cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, role + "\n" + done.stdout + done.stderr)
            self.assertIn("結構化交付", done.stderr, "%s 的 --stats 沒印那一段:%s"
                          % (role, done.stderr))
            total = len(done.stdout.encode("utf-8"))
            self.assertLessEqual(total, 4096,
                                 "%s 總量 %d bytes 超過 4096" % (role, total))
            found = re.search(r"結構化交付 (\d+) bytes", done.stderr)
            self.assertIsNotNone(found, "找不到那個數字:%s" % done.stderr)
            self.assertLessEqual(int(found.group(1)), 600,
                                 "%s 的結構化交付段 %s bytes 超過 600"
                                 % (role, found.group(1)))


# ---------------------------------------------------------------- item 7

class SchemaKeysLiveInOnlyTwoDocs(unittest.TestCase):
    """#20 驗收⑦:鍵名只活在兩處 —— 判準是鍵名,不是 fenced 標記本身。

    2026-09-22 派工裁示:`scripts/auto-fix.sh`、`scripts/rules.py` 合法地帶有
    fenced 標記字面(範例、註解),但**不抄鍵名**——`harvest_result()` 那段註解
    自己就寫著「鍵名不寫在這一支」。所以判準改成同時出現 `patch_sha256` 與
    `red_first_line` 這兩個只有 schema 範例才會湊在一起的鍵名。

    2026-09-23(#24):`tickets/*.json` 引用鍵名是合法的 —— 票的 `acceptance`
    要逐字講清楚案例要守住的 schema 形狀本來就得抄鍵名(#20 自己的票檔就是
    這樣),不算多抄了一份 schema。`tickets/SCHEMA.md` 仍是精確比對那一份
    規格檔,`tickets/*.json` 另外放行。
    """

    SKIP_DIRS = {".git", "__pycache__", "reports", "wt", "land", ".land.lock"}
    ALLOWED_EXACT = {"docs/DISPATCH-TEMPLATE.md", "tickets/SCHEMA.md"}

    def test_only_the_two_schema_docs_and_test_fixtures_carry_both_keys(self):
        hits = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for name in filenames:
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                try:
                    with open(path, encoding="utf-8") as handle:
                        text = handle.read()
                except (UnicodeDecodeError, OSError):
                    continue
                if "patch_sha256" in text and "red_first_line" in text:
                    hits.append(rel)
        third_copy = [rel for rel in hits
                     if rel not in self.ALLOWED_EXACT
                     and not rel.startswith("tests/")
                     and not rel.startswith("verify/")
                     and not (rel.startswith("tickets/") and rel.endswith(".json"))]
        self.assertEqual(third_copy, [], "schema 鍵名多抄了一份:%s" % third_copy)
        self.assertTrue(self.ALLOWED_EXACT <= set(hits),
                        "兩份規格檔本身都要帶得到鍵名範例:命中 %s" % hits)

    def test_auto_fix_and_rules_keep_the_fenced_marker_without_the_key_names(self):
        """反面:那兩支腳本**可以**合法出現 fenced 標記字樣,只是不抄鍵名。"""
        for rel in ("scripts/auto-fix.sh", "scripts/rules.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("patch_sha256", text, rel)
            self.assertNotIn("red_first_line", text, rel)


# ---------------------------------------------------------------- item 8

class RoleCardsCarryTheSentenceWithinCap(unittest.TestCase):
    """#20 驗收⑧(前半):兩張卡各多一句,但都不超過字元上限。"""

    def test_implementer_and_verifier_cards_stay_under_cap(self):
        cfg_path = os.path.join(ROOT, "board", "config.json")
        with open(cfg_path, encoding="utf-8") as handle:
            cap = int((json.load(handle).get("memory") or {}).get("cap_chars") or 2000)
        for rel in ("memory/role/implementer.md", "memory/role/verifier.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("寫不出來的欄位照實留空,不要編", text, rel)
            self.assertLessEqual(len(text), cap,
                                 "%s %d 字元超過上限 %d" % (rel, len(text), cap))


class MemoryCheckDoesNotOpenATicketForTheTouchedCards(Sandbox):
    """#20 驗收⑧(後半):`memory.py check` 不因這兩張卡開出整理票。

    只裝這兩張卡進沙盒(不借 `install_rules_sources()` 整批搬):那支會連
    `memory/role/main.md`(2026-09-22 實測 2341 字元,已經超過 2000 的預設上限)
    一起搬進來,而那份超標與這張票無關 —— 混進來會讓這個案例的紅綠跟著一份
    自己沒動過的檔案漂移。
    """

    config_extra = {"memory": {"unit": "chars", "cap_chars": 2000,
                                "consolidator": "fable", "raise_allowed": True,
                                "applies_to": ["memory/model/*.md", "memory/role/*.md"],
                                "inbox_suffix": ".inbox.md"}}

    def setUp(self):
        super(MemoryCheckDoesNotOpenATicketForTheTouchedCards, self).setUp()
        os.makedirs(os.path.join(self.repo, "memory", "role"), exist_ok=True)
        for name in ("implementer.md", "verifier.md"):
            with open(os.path.join(ROOT, "memory", "role", name), encoding="utf-8") as handle:
                text = handle.read()
            self.write(os.path.join("memory", "role", name), text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "裝進兩張角色卡(#20 夾具)")

    def test_neither_card_is_reported_over_cap_or_opens_a_ticket(self):
        done = self.run_py("scripts/memory.py", "check")
        for rel in ("memory/role/implementer.md", "memory/role/verifier.md"):
            lines = [line for line in done.stdout.splitlines() if rel in line]
            self.assertTrue(lines, "%s 沒有出現在輸出裡:%s" % (rel, done.stdout))
            for line in lines:
                self.assertNotIn("超過", line, line)
        self.assertEqual(self.tickets_on_disk(), [], "不准因為這兩張卡開出整理票")


# ---------------------------------------------------------------- items 2/3/4/5/6

# 這個案例自己的新檔:round 1 本來就是紅的(與實作者的夾具無關)。
PROBE_PATCH = ("diff -ruN base/tests/test_agentout_probe.py "
              "work/tests/test_agentout_probe.py\n"
              "--- base/tests/test_agentout_probe.py\t1970-01-01 08:00:00\n"
              "+++ work/tests/test_agentout_probe.py\t2026-09-22 10:00:00\n"
              "@@ -0,0 +1,6 @@\n"
              "+import unittest\n"
              "+\n"
              "+\n"
              "+class Probe(unittest.TestCase):\n"
              "+    def test_probe(self):\n"
              "+        self.assertEqual(1, 2, \"agentout 夾具:第一輪本來就是紅的\")\n")

RED_LOG = """test_probe (test_agentout_probe.Probe.test_probe) ... FAIL

======================================================================
FAIL: test_probe (test_agentout_probe.Probe.test_probe)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/sandbox/tests/test_agentout_probe.py", line 6, in test_probe
    self.assertEqual(1, 2, "agentout 夾具:第一輪本來就是紅的")
AssertionError: 1 != 2 : agentout 夾具:第一輪本來就是紅的

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)
"""

# 假 worker:把那條紅的改綠,依 §8.5 在 EVIDENCE 尾端交一塊合法的 `result`。
# **沒有**「## 記憶」那個散文段 —— 這是刻意的:`memory[]` 只是鏡像,這份夾具要
# 證明抽它不會把記憶收件匣寫第二次(真正的入口是 §8 第 8 點那幾行 `memory.py note`)。
WORKER_SHIPS_RESULT = """#!/bin/sh
set -e
echo "agentout-worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > work/tests/test_agentout_probe.py <<'CASE'
import unittest


class Probe(unittest.TestCase):
    def test_probe(self):
        self.assertEqual(1, 1)
CASE
diff -ruN base work > "patch-round$AC_ROUND.diff" || true
sed "s/@R@/$AC_ROUND/g; s/@T@/$AC_TICKET/g" > "EVIDENCE-round$AC_ROUND.md" <<'EV'
# agentout 夾具 —— 第 @R@ 輪
已排除的假設:不是副本沒同步 —— base/ 與 work/ 只差那一個檔。

## result

```result
{"ticket": "@T@", "role": "worker", "round": @R@, "rc": 0,
 "patch_sha256": "agentout-fixture-sha",
 "gate": {"cmd": "python3 -m unittest test_agentout_probe", "ran": 1, "rc": 0},
 "mutations": [{"id": "AO1", "count": 1, "case": "Probe.test_probe",
                "red_first_line": "AssertionError: 1 != 2"}],
 "objection": null,
 "excluded": ["不是副本沒同步(agentout 夾具)"],
 "repro": {"cmd": "python3 -m unittest test_agentout_probe", "expect": "Ran 1 test ... OK"},
 "memory": [{"layer": "role", "name": "verifier", "line": "agentout 夾具一句原則", "ticket": "@T@"},
            {"layer": "model", "name": "sonnet", "line": "agentout 夾具另一句", "ticket": "@T@"}]}
```
EV
"""

# 假 worker:什麼都沒交(連 EVIDENCE 都沒有)。
WORKER_SILENT = """#!/bin/sh
echo "agentout-worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
exit 0
"""

# 假 worker:交了 EVIDENCE,散文照舊,但**沒有**那一塊 fenced json。
WORKER_PROSE_WITHOUT_BLOCK = """#!/bin/sh
set -e
echo "agentout-worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
printf '# 第 %s 輪\\n已排除的假設:沒有 —— 這份夾具故意不交那一塊\\n' \\
    "$AC_ROUND" > "EVIDENCE-round$AC_ROUND.md"
"""

# 假 worker:那一塊在,但裡面不是合法 JSON。
WORKER_UNPARSEABLE_BLOCK = """#!/bin/sh
set -e
echo "agentout-worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > "EVIDENCE-round$AC_ROUND.md" <<'EV'
# 夾具:那一塊解不開

## result

```result
{"ticket": "1", "role": "worker", "rc": 0, agentout-marker-這裡不是合法-json}
```
EV
"""

# 假 worker:`OBJECTION:` 那一行說 ticket-wrong,block 卻說 test_defect。
WORKER_CONFLICTING_OBJECTION = """#!/bin/sh
set -e
echo "agentout-worker ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
cat > "EVIDENCE-round$AC_ROUND.md" <<'EV'
OBJECTION: ticket-wrong agentout 夾具:票面與設計文件對不上

## result

```result
{"ticket": "1", "role": "worker", "round": 2, "rc": 1, "patch_sha256": "",
 "gate": {"cmd": "", "ran": null, "rc": null}, "mutations": [],
 "objection": {"category": "test_defect", "body": "agentout 夾具:block 說是案例本身的問題"},
 "excluded": [], "repro": {"cmd": "", "expect": ""}, "memory": []}
```
EV
"""

# 假 worker:說 test_defect(走驗證者那條路);新派的驗證者交了 EVIDENCE,
# 但不交 patch-verify —— 那正是最需要留痕跡的那一次。
WORKER_ROUTES_TO_A_VERIFIER = """#!/bin/sh
set -e
echo "agentout-$AC_ROLE ran round $AC_ROUND" >> "$AC_TEST_LOG"
cd "$AC_WORK"
if [ "$AC_ROLE" = worker ]; then
    printf 'OBJECTION: test_defect agentout 夾具:worker 說案例本身錯了\\n' \\
        > "EVIDENCE-round$AC_ROUND.md"
    exit 0
fi
cat > EVIDENCE-verifier.md <<'EV'
# agentout 夾具:新派的驗證者交了 EVIDENCE,但沒有 patch-verify

## result

```result
{"ticket": "1", "role": "verifier", "round": 2, "rc": 0, "patch_sha256": "",
 "gate": {"cmd": "", "ran": null, "rc": null}, "mutations": [],
 "objection": null, "excluded": [], "repro": {"cmd": "", "expect": ""}, "memory": []}
```
EV
"""


class AgentOutputHarness(Sandbox):
    """這一組的共用機關:開票、樁一支 worker、直接寫一輪紅榜、起 `auto-fix.sh`。"""

    def stub_worker(self, body):
        path = os.path.join(self.home, "agentout-worker.sh")
        write_executable(path, body)
        conf = dict(DEFAULT_CONFIG)
        conf["worker"] = {"command": "sh %s" % path, "timeout_seconds": 120}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))

    def ticket_ready(self):
        row = self.make_ticket("1", allowed_write_paths=["tests/*"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1(agentout 夾具)")
        self.git("push", "-q", "origin", "main")
        return row

    def write_round(self, rc, log_text, run_id="20260922-090000-1", round_no=1):
        """直接寫一輪的狀態檔 —— 要驗的是 `auto-fix.sh` 怎麼讀它,不是閘門怎麼寫它。

        `--base-sha` 一定要帶目前 main 的 sha:`round_once` 在分支 `t1` 還不存在
        時會退回它去抓副本(`git archive` 對空字串直接 `fatal`,實測過)。
        """
        log = self.write("round.log", log_text, where=self.home)
        base = self.git("rev-parse", "main").strip()
        self.run_py("scripts/status.py", "start", "--ticket", "1", "--kind", "gate",
                    "--run-id", run_id, "--base-sha", base, "--round", str(round_no),
                    "--worktree", self.repo)
        return self.run_py("scripts/status.py", "done", "--ticket", "1", "--kind",
                           "gate", "--run-id", run_id, "--rc", str(rc), "--log", log)

    def first_red_round(self):
        """要走到真的 apply + 閘門重跑那一段(綠的那條路)才需要這個 —— 其他停下
        來的路(沒交 patch)在 `apply.sh`/`gate.sh` 之前就已經 return 了。"""
        patch = self.write("round1.diff", PROBE_PATCH, where=self.home)
        applied = self.run_sh("scripts/apply.sh", "1", patch)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        wt = os.path.join(self.home, "repo-wt", "t1")
        gate = self.run_sh(os.path.join(wt, "scripts", "gate.sh"),
                           "--branch", "--ticket", "1", "--no-auto-fix", cwd=wt,
                           env=self.env(AC_ROOT=self.repo))
        self.assertNotEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        return wt

    def auto_fix(self):
        return self.run_sh("scripts/auto-fix.sh", "1")

    def result_files(self, name):
        return sorted(glob.glob(os.path.join(self.repo, "reports", "t1", "*", name)))

    def result_json(self, name="result-round2.json"):
        found = self.result_files(name)
        self.assertEqual(len(found), 1, "%s:找不到或不只一份(%s)" % (name, found))
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def memory_inbox_lines(self, rel="memory/role/implementer.inbox.md"):
        path = os.path.join(self.repo, rel)
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            return len(handle.read().splitlines())


class ANormalRoundShipsTheWholeBlockAndDoesNotDoubleWriteMemory(AgentOutputHarness):
    """#20 驗收②⑤:正常路徑十一個鍵一個不少,`memory[]` 只是鏡像不是入口。"""

    KEYS = ("ticket", "role", "round", "rc", "patch_sha256", "gate",
            "mutations", "objection", "excluded", "repro", "memory")

    def test_the_block_lands_next_to_status_json_with_all_keys_and_memory_is_a_mirror(self):
        self.stub_worker(WORKER_SHIPS_RESULT)
        self.ticket_ready()
        self.first_red_round()
        before = self.memory_inbox_lines()

        done = self.auto_fix()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = self.result_files("result-round2.json")
        self.assertEqual(len(found), 1, done.stdout)
        self.assertTrue(os.path.exists(
            os.path.join(os.path.dirname(found[0]), "status.json")),
            "抽出來的要與那一輪的 status.json 同目錄")
        data = self.result_json()
        for key in self.KEYS:
            self.assertIn(key, data, key)
        self.assertEqual(data["ticket"], "1")
        self.assertEqual(data["round"], 2)
        self.assertEqual(data["role"], "worker")
        self.assertIs(data["present"], True)
        self.assertIs(data["conflict"], False)
        self.assertEqual(data["rc"], 0)
        self.assertEqual(data["gate"]["ran"], 1)
        self.assertEqual(len(data["mutations"]), 1)
        self.assertIsNone(data["objection"])
        # `memory[]` 是鏡像:抽得出來(2 項),但收件匣一行都不該多 —— 真正的寫入
        # 是 `apply.sh` 叫的那一支 `memory.py harvest`(#10),這份夾具沒有寫
        # 「## 記憶」那個散文段,所以它沒有東西可收。
        self.assertEqual(len(data["memory"]), 2)
        self.assertEqual(self.memory_inbox_lines(), before,
                         "記憶收件匣的行數不准因為這一手而變多")
        self.assertEqual(self.kinds().count("memory.noted"), 0)


class TheThreeKindsOfMissingResultLookDifferent(AgentOutputHarness):
    """#20 驗收③:沒有 EVIDENCE / 有 EVIDENCE 沒那一塊 / 那一塊解不開,三種樣子。"""

    def test_no_evidence_at_all_leaves_a_no_evidence_trace(self):
        self.stub_worker(WORKER_SILENT)
        self.ticket_ready()
        self.write_round(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "no-evidence")
        self.assertEqual(data["round"], 2)
        self.assertNotIn("raw", data)

    def test_an_evidence_without_the_block_says_so_in_its_own_words(self):
        """**變異(見 EVIDENCE)**:把 `no-block` 那一支改成與 `no-evidence` 同一個
        reason → 這一條紅(兩種缺漏會變成同一句話)。"""
        self.stub_worker(WORKER_PROSE_WITHOUT_BLOCK)
        self.ticket_ready()
        self.write_round(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "no-block")
        self.assertNotEqual(data["reason"], "no-evidence",
                            "交了 EVIDENCE 卻忘了那一塊,與根本沒交不是同一件事")
        self.assertNotIn("raw", data, "沒有那一塊就沒有原文可留")

    def test_a_block_that_will_not_parse_keeps_the_first_500_characters(self):
        self.stub_worker(WORKER_UNPARSEABLE_BLOCK)
        self.ticket_ready()
        self.write_round(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        data = self.result_json()
        self.assertIs(data["present"], False)
        self.assertEqual(data["reason"], "bad-json")
        self.assertIn("agentout-marker-這裡不是合法-json", data["raw"])
        self.assertLessEqual(len(data["raw"]), 500)


class TheObjectionLineStillWinsWhenTheyDisagree(AgentOutputHarness):
    """#20 驗收④:`OBJECTION:` 那一行照舊說了算,分岔只多一格 `conflict`。"""

    def test_a_conflicting_block_does_not_open_a_second_path(self):
        self.stub_worker(WORKER_CONFLICTING_OBJECTION)
        self.ticket_ready()
        self.write_round(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        ticket = self.load_ticket("1")
        self.assertEqual(len(ticket["objections"]), 1, "只准多一筆")
        self.assertEqual(ticket["objections"][0]["category"], "ticket-wrong",
                         "以 OBJECTION: 那一行為準")
        self.assertEqual(ticket["state"], "Blocked")
        data = self.result_json()
        self.assertIs(data["conflict"], True)
        self.assertEqual(data["objection"]["category"], "test_defect",
                         "block 裡那一句照實留著 —— 分岔要看得見,不是被蓋掉")


class TheVerifierPathWritesAFileOfItsOwnName(AgentOutputHarness):
    """#20 驗收⑥:`result-verifier-round<輪>.json`,檔名跟 worker 那條路不一樣。"""

    def test_verifier_result_file_has_its_own_name_and_worker_path_still_traced(self):
        self.stub_worker(WORKER_ROUTES_TO_A_VERIFIER)
        self.ticket_ready()
        self.write_round(1, RED_LOG)

        done = self.auto_fix()

        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        verifier = self.result_json("result-verifier-round2.json")
        self.assertEqual(verifier["role"], "verifier")
        self.assertIs(verifier["present"], True)
        worker = self.result_json("result-round2.json")
        self.assertEqual(worker["reason"], "no-block",
                         "worker 只交了一行反駁,那一份也要留得下痕跡")


if __name__ == "__main__":
    unittest.main()
