"""#101 寫入範圍改為建議範圍(hard_block.paths 仍硬擋)、info_homes、worker / 驗證者案例分開。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面原話 / 夾具自己寫的票檔與 patch / 手算)
A1 | unit | apply 單票、apply --batch、gate --branch preflight、land:patch 命中 out_of_scope | rc 0 / 非 4、主線或分支前進、檔進 extra_paths、輸出同一行點名檔與 glob | 票面 A1 原話
A1 | unit | 同上,命中 board/config.json 的 hard_block.paths                                 | apply rc 5 / gate rc 4 / land 拒絕、點名檔與格                            | 票面 A1「hard_block.paths 維持」
A2 | unit | set / lint / create --info-home                                                  | 合格的收;絕對路徑、glob、..、空字串、缺鍵、多鍵、非 list 拒收且票位元不變   | 票面 A2 原話
A3 | unit | needs_verifier=true 的 create / set allowed_write_paths / set verify / set needs_verifier / 轉 Ready / lint | 拒收、訊息含 MSG_A3 並點名 glob 與檔、票位元不變;false 與別票案例照收 | 票面 A3 原話
A4 | unit | apply <worker patch> <patch-verify> --evidence-verifier                          | verify.files 併進票、allowed_write_paths 不變、驗證者檔不在 extra_paths    | 票面 A4 + A5 的提示範圍算法
A5 | unit | patch 動 lib/a.py(有理由)/ docs/b.md(空字串,命中 out_of_scope)/ etc/c.ini(沒寫) + notes/home.md(info home) | extra_paths=[a,b,c]、extra_reasons={a:理由,b:null,c:null};land 拒絕含 MSG_A5 點名 b;只缺理由不在 allowed 的照落;覆核派工文逐檔見檔名、glob、理由 | 票面 A5 + test_plan T2(手算)
A6 | unit | 讀 SCHEMA / DISPATCH-TEMPLATE / role-procedures / dispatch-reviewer                | 舊句「硬擋只有兩格」與反向指示不在;新格 extra_reasons、EVIDENCE 小節名在   | 票面 A6 原話
A7 | unit | set info_homes                                                                   | 票只多 info_homes 一格,沒有第二組權限欄                                    | 票面 A7「不能把資訊歸屬另存成第二組可寫的權限欄位」
A8 | unit | auto-fix --dry-run --round 1                   | 派工文含 config.info_map 的路徑與票的 info_homes.home;缺設定照印 | 票面 A8 原話

## 介面字串(票面定的照抄;票面沒定的在這裡定)
MSG_A3 = "是本票驗證者的案例檔"          ← 票面 interface_contracts.messages.A3
MSG_A5 = "out_of_scope 命中缺理由"       ← 票面 interface_contracts.messages.A5_land
EVIDENCE 小節 = "## 提示清單外的改動",一檔一行 `` - `<路徑>` — <理由> ``(破折號後空白 = 空字串)
             ← 票面 A5 只給了小節名;行形狀照 verify/TAGS.d 的 `` - `x` — 說明 `` 慣例
--info-home 的值 = 一個 JSON 物件 {"what":…, "home":…}(與 set info_homes 的元素同形)
result 檔 = reports/t1/<run>/result-round1.json(既有 extra_paths 那一份);批次 = reports/batch/<stamp>/result-round1.json

## 怎麼做假(不上真埠、不起真模型)
tests/control_harness.Sandbox:拋棄式真 git repo + 絆線 claude;land 的閘門換 GATE_STUB_GREEN,
gate preflight 那一條用真 gate.sh(只跑沙盒的替身測試);reviewer 是 REVIEWER_PASS 替身。

## 不做
不改產品碼;不放寬票面驗收;不碰既有案例;A7 的「排程判重疊、未知/空值串行」那一半票面沒給 helper 名字,不猜。
"""
import difflib
import glob
import json
import os
import sys
import unittest

TAGS = ["write-scope"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import (GATE_STUB_GREEN, REVIEWER_PASS, Sandbox,  # noqa: E402
                             write_executable)

MSG_A3 = "是本票驗證者的案例檔"
MSG_A5 = "out_of_scope 命中缺理由"
REASON_A = "理由甲:釘舊行為的 lib 跟著改"
REASON_DOCS = "理由乙:文件跟著新行為改"
# lint 還查 verify_strings(#91 C3);給一條合格的,紅才只來自這張票要的那一格。
LINT_OK = {"verify_strings": [{"path": "src/a.txt", "contains": "new"}]}

RESULT_BLOCK = """## result

```result
{"ticket": "1", "role": "worker", "round": 1, "rc": 0,
 "patch_sha256": null, "gate": null, "mutations": [],
 "objection": null, "excluded": [], "repro": null, "memory": []}
```
"""


def evidence(reasons):
    """實作者 EVIDENCE:`reasons` = [(路徑, 理由)];理由 "" 就是破折號後空白。"""
    lines = ["# EVIDENCE #1 第 1 輪", "", "## 提示清單外的改動"]
    lines += ["- `%s` — %s" % (path, why) for path, why in reasons]
    return "\n".join(lines) + "\n\n" + RESULT_BLOCK


def patch(*files):
    """`(路徑, 舊內容或 None, 新內容)` → `diff -ruN base work` 形狀。"""
    out = []
    for path, old, new in files:
        out.append("diff -ruN base/%s work/%s\n" % (path, path))
        out.extend(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True),
                                        "base/" + path, "work/" + path))
    return "".join(out)


SRC = ("src/a.txt", "old\n", "new\n")
LIB_A = ("lib/a.py", None, "a = 1\n")
DOCS_B = ("docs/b.md", None, "b\n")
ETC_C = ("etc/c.ini", None, "c=1\n")
HOME = ("notes/home.md", None, "資訊在這裡\n")
CASE_1 = "verify/example/test_ticket_1.py"
CASE_BODY = ("import unittest\nTAGS = [\"example\"]\n\n\nclass T(unittest.TestCase):\n"
             "    def test_one(self):\n        self.assertTrue(True)\n")


class World(Sandbox):
    """主線有 src/a.txt;patch 由 `patch()` 產生,票由 make() 寫進主線並推上 origin。"""

    def setUp(self):
        super().setUp()
        self.write("src/a.txt", "old\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "主線上先有 src/a.txt")
        self.git("push", "-q", "origin", "main")

    def make(self, ident="1", **fields):
        row = self.make_ticket(ident, **fields)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #%s" % ident)
        self.git("push", "-q", "origin", "main")
        return row

    def protect(self, *globs):
        conf = json.loads(self.read("board/config.json"))
        conf["hard_block"] = {"paths": list(globs), "dirs": [], "patterns": []}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "hard_block.paths")
        self.git("push", "-q", "origin", "main")

    def home_file(self, name, body):
        return self.write(name, body, where=self.home)

    def apply(self, *args):
        return self.run_sh("scripts/apply.sh", *args)

    def commits_on_t1(self):
        out = self.run_git_quiet("rev-list", "--count", "main..t1")
        return out.strip() if out is not None else "沒有分支 t1"

    def run_git_quiet(self, *args):
        import subprocess
        done = subprocess.run(["git", *args], cwd=self.repo, env=self.env(),
                              capture_output=True, text=True)
        return done.stdout if done.returncode == 0 else None

    def result_round1(self, where=("reports", "t1")):
        found = sorted(glob.glob(os.path.join(self.repo, *where, "*", "result-round1.json")))
        self.assertEqual(len(found), 1, "result-round1.json 要恰一份:%s" % found)
        with open(found[0], encoding="utf-8") as handle:
            return json.load(handle)

    def ticket_bytes(self, ident="1"):
        return self.read(os.path.join("tickets", "%s.json" % ident))


# ------------------------------------------------------------------ A1


class A1OutOfScopeIsAHintNotAFence(World):

    def test_a1_single_apply_lets_an_out_of_scope_hit_through_and_names_the_glob(self):
        """A1 單票 apply:命中 out_of_scope 不再 rc=5,列進 extra_paths 並標明命中的 glob。

        四問:量 rc 0、t1 恰 1 個 commit、extra_paths 含 docs/b.md、輸出有一行同時有檔與 `docs/*`;
        紅 = 舊硬擋 rc 5;期望值 = 票面 A1 + 夾具的 out_of_scope;不測 = 理由的抽取(A5)。
        """
        self.make("1", allowed_write_paths=["src/*"], out_of_scope=["docs/*"])
        done = self.apply("1", self.home_file("p.diff", patch(SRC, DOCS_B)),
                          "--evidence", self.home_file("EVIDENCE.md",
                                                       evidence([("docs/b.md", REASON_DOCS)])))
        self.assertEqual(done.returncode, 0, "命中 out_of_scope 不再擋:\n" + done.stdout + done.stderr)
        self.assertEqual(self.commits_on_t1(), "1")
        self.assertIn("docs/b.md", self.result_round1()["extra_paths"])
        lines = [row for row in (done.stdout + done.stderr).splitlines() if "docs/b.md" in row]
        self.assertTrue(any("docs/*" in row for row in lines),
                        "要標明命中哪條 out_of_scope glob:%s" % lines)

    def test_a1_single_apply_still_refuses_hard_block_paths(self):
        """A1 hard_block.paths 維持 apply rc=5、點名檔與格(不在本票放寬;M1 移除它 → 紅)。"""
        self.protect("docs/*")
        self.make("1", allowed_write_paths=["src/*", "docs/*"])
        done = self.apply("1", self.home_file("p.diff", patch(SRC, DOCS_B)))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        line = [row for row in done.stderr.splitlines() if "docs/b.md" in row]
        self.assertEqual(len(line), 1, done.stderr)
        self.assertIn("hard_block.paths", line[0])
        self.assertIn("`docs/*`", line[0])
        self.assertIn(self.commits_on_t1(), ("0", "沒有分支 t1"), "硬擋的 patch 不留 commit")

    def test_a1_batch_apply_lets_an_out_of_scope_hit_through(self):
        """A1 --batch:out_of_scope 聯集 − allowed 聯集 不再硬擋;protected 那半照擋見下一條。

        期望值 = make_batch 夾具:docs/guarded.md 只在票 1 的 out_of_scope、不在任何 allowed。
        """
        batch = self.make_batch()
        body = patch(("docs/guarded.md", "guarded\n", "touched\n"))
        done = self.apply("--batch", "land/X", "1", "2", self.home_file("b.diff", body))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotEqual(self.git("rev-parse", "land/X").strip(), batch["land"],
                            "land/X 要前進一個 commit")
        self.assertIn("docs/guarded.md", self.result_round1(("reports", "batch"))["extra_paths"])

    def test_a1_batch_apply_still_refuses_hard_block_paths(self):
        """A1 --batch 的 protected 那一半照擋(rc 5、點名檔)。"""
        batch = self.make_batch()
        self.protect("docs/guarded.md")
        body = patch(("docs/guarded.md", "guarded\n", "touched\n"))
        done = self.apply("--batch", "land/X", "1", "2", self.home_file("b.diff", body))
        self.assertEqual(done.returncode, 5, done.stdout + done.stderr)
        self.assertIn("docs/guarded.md", done.stderr)
        self.assertIn("hard_block.paths", done.stderr)
        self.assertEqual(self.git("rev-parse", "land/X").strip(), batch["land"])


class A1GatePreflight(Sandbox):
    """真的 gate.sh;只跑沙盒裡那一支替身 tests/test_land.py。"""

    def setUp(self):
        super().setUp()
        self.write("tests/test_land.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n        pass\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "替身測試模組")

    def gate(self, *args):
        return self.run_sh("scripts/gate.sh", *args, env=self.env(AC_TEST_HOME=self.home))

    def test_a1_gate_preflight_does_not_stop_on_out_of_scope(self):
        """A1 gate preflight:out_of_scope 命中不再 rc=4「機械格不合」。"""
        self.make_ticket(7, allowed_write_paths=["src/*"], out_of_scope=["scripts/land.sh"])
        self.write("scripts/land.sh", self.read("scripts/land.sh") + "\n# outside scope\n")
        done = self.gate("--branch", "--ticket", "7")
        self.assertNotEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertNotIn("機械格不合", done.stdout + done.stderr)

    def test_a1_gate_preflight_still_stops_on_hard_block_paths(self):
        """A1 gate preflight:hard_block.paths 命中照舊 rc=4、點名檔與格。"""
        conf = json.loads(self.read("board/config.json"))
        conf["hard_block"] = {"paths": ["scripts/land.sh"], "dirs": [], "patterns": []}
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "hard_block.paths")
        self.make_ticket(7, allowed_write_paths=["scripts/land.sh"])
        self.write("scripts/land.sh", self.read("scripts/land.sh") + "\n# protected\n")
        done = self.gate("--branch", "--ticket", "7")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("scripts/land.sh", done.stderr)
        self.assertIn("hard_block.paths", done.stderr)


class LandWorld(World):
    gate_stub = GATE_STUB_GREEN

    def apply_then_approve(self, files, reasons, **ticket):
        self.make("1", **ticket)
        done = self.apply("1", self.home_file("p.diff", patch(*files)),
                          "--evidence", self.home_file("EVIDENCE.md", evidence(reasons)))
        self.assertEqual(done.returncode, 0, "apply 要收:\n" + done.stdout + done.stderr)
        self.approve(1, "t1")
        return done

    def land(self):
        return self.run_sh("scripts/land.sh", "t1")

    def on_main(self):
        return self.git("ls-tree", "-r", "--name-only", "main").splitlines()


class A1LandTakesAReasonedOutOfScopeHit(LandWorld):

    def test_a1_land_lands_an_out_of_scope_hit_that_has_a_reason(self):
        """A1 land:命中 out_of_scope、EVIDENCE 有理由 → 照落(M5 land 照拒 → 紅)。"""
        self.apply_then_approve((SRC, DOCS_B), [("docs/b.md", REASON_DOCS)],
                                allowed_write_paths=["src/*"], out_of_scope=["docs/*"])
        done = self.land()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("docs/b.md", self.on_main())

    def test_a1_land_still_refuses_hard_block_paths(self):
        """A1 land:hard_block.paths 命中照拒、點名檔與格、主線不動。"""
        self.protect("docs/*")
        self.make("1", allowed_write_paths=["src/*", "docs/*"])
        branch = self.worktree("t1")
        self.commit_in(branch, "docs/b.md", "動到受保護的檔")
        self.approve(1, "t1")
        before = self.git("rev-parse", "main").strip()
        done = self.land()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [row for row in done.stdout.splitlines() if "docs/b.md" in row]
        self.assertTrue(line, done.stdout)
        self.assertTrue(any("hard_block.paths" in row for row in line), line)
        self.assertEqual(self.git("rev-parse", "main").strip(), before)


# ------------------------------------------------------------------ A2 / A7


GOOD_HOMES = [{"what": "寫入範圍的規格", "home": "tickets/SCHEMA.md"},
              {"what": "派工共用規矩", "home": "docs/DISPATCH-TEMPLATE.md"}]
BAD_HOMES = (
    ("絕對路徑", [{"what": "x", "home": "/etc/passwd"}]),
    ("glob", [{"what": "x", "home": "docs/*.md"}]),
    ("..", [{"what": "x", "home": "../outside.md"}]),
    ("what 空", [{"what": "", "home": "docs/a.md"}]),
    ("home 空", [{"what": "x", "home": ""}]),
    ("缺 home", [{"what": "x"}]),
    ("多一鍵", [{"what": "x", "home": "docs/a.md", "writable": True}]),
    ("元素不是物件", ["docs/a.md"]),
    ("home 不是字串", [{"what": "x", "home": ["docs/a.md"]}]),
)


class A2InfoHomesShape(World):

    def test_a2_set_accepts_a_well_formed_info_homes(self):
        """A2 合格的 list[{what,home}] 照收、原樣存;A7 票上不多出第二組權限欄。"""
        self.make("1")
        before = set(json.loads(self.ticket_bytes()))
        done = self.ticket("set", "1", "info_homes", json.dumps(GOOD_HOMES, ensure_ascii=False))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = json.loads(self.ticket_bytes())
        self.assertEqual(after["info_homes"], GOOD_HOMES)
        self.assertEqual(set(after) - before, {"info_homes"},
                         "A7:資訊歸屬不能另存成第二組可寫的權限欄位")

    def test_a2_set_refuses_each_malformed_info_homes_and_leaves_the_ticket_alone(self):
        """A2 絕對路徑 / glob / .. / 空值 / 缺鍵 / 多鍵 / 非物件,set 一律拒收,票位元不變。"""
        self.make("1")
        for label, value in BAD_HOMES:
            with self.subTest(label=label):
                before = self.ticket_bytes()
                done = self.ticket("set", "1", "info_homes", json.dumps(value, ensure_ascii=False))
                self.assertNotEqual(done.returncode, 0, "%s 不該收:%s" % (label, done.stdout))
                self.assertIn("info_homes", done.stderr)
                self.assertEqual(self.ticket_bytes(), before, "拒收不改票、不動 state_version")

    def test_a2_lint_uses_the_same_rule(self):
        """A2 lint 同判準:手寫進票檔的壞 info_homes,lint rc 1 並點名 info_homes。"""
        for label, value in BAD_HOMES:
            with self.subTest(label=label):
                self.make_ticket("1", needs_verifier=False, info_homes=value, **LINT_OK)
                done = self.ticket("lint", "1")
                self.assertEqual(done.returncode, 1, "%s:%s" % (label, done.stdout + done.stderr))
                self.assertIn("info_homes", done.stderr)
        self.make_ticket("1", needs_verifier=False, info_homes=GOOD_HOMES, **LINT_OK)
        done = self.ticket("lint", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def create_args(self, *extra):
        return ["create", "--subject", "s", "--objective", "o", "--acceptance", "a",
                "--role", "worker", "--model", "opus", "--tool", "claude-code",
                "--allowed-write-path", "src/*", *extra]

    def test_a2_create_takes_info_home_more_than_once(self):
        """A2 create --info-home 可重複,依序進 info_homes。"""
        args = self.create_args()
        for item in GOOD_HOMES:
            args += ["--info-home", json.dumps(item, ensure_ascii=False)]
        done = self.ticket(*args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = [row for row in self.tickets_on_disk() if row.get("subject") == "s"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("info_homes"), GOOD_HOMES)

    def test_a2_create_refuses_a_malformed_info_home(self):
        """A2 create 同判準:絕對路徑的 home 拒收、不開票(不是「不認得旗標」那一種拒)。"""
        count = len(self.tickets_on_disk())
        done = self.ticket(*self.create_args(
            "--info-home", json.dumps({"what": "x", "home": "/abs/path.md"})))
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("不認得", done.stderr, "--info-home 要是認得的旗標")
        self.assertIn("info_homes", done.stderr)
        self.assertEqual(len(self.tickets_on_disk()), count)


# ------------------------------------------------------------------ A3


class A3WorkerScopeMustNotClaimTheVerifiersCases(World):

    def nv_ticket(self, **fields):
        row = dict(allowed_write_paths=["src/*"], needs_verifier=True, state="Draft")
        row.update(fields)
        return self.make("1", **row)

    def assert_refused(self, done, before, glob_text, file_text, ident="1"):
        out = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, out)
        line = [row for row in out.splitlines() if MSG_A3 in row]
        self.assertTrue(line, "拒收訊息要含「%s」:%s" % (MSG_A3, out))
        self.assertIn(glob_text, out, "點名 glob")
        self.assertIn(file_text, out, "點名檔")
        self.assertEqual(self.ticket_bytes(ident), before, "拒收不改票或 state_version")

    def test_a3_set_allowed_write_paths_refuses_the_convention_case_path(self):
        """A3 set allowed_write_paths:命中 verify/**/test_ticket_<id>.py 慣例 → 拒收。"""
        self.nv_ticket()
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "allowed_write_paths",
                           '["src/*", "verify/**/test_ticket_1.py"]')
        self.assert_refused(done, before, "verify/**/test_ticket_1.py", "test_ticket_1.py")

    def test_a3_set_allowed_write_paths_refuses_the_tags_fragment(self):
        """A3 慣例的另一半:verify/TAGS.d/<id>.md(M2 漏慣例檔 → 紅)。"""
        self.nv_ticket()
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "allowed_write_paths", '["src/*", "verify/TAGS.d/*"]')
        self.assert_refused(done, before, "verify/TAGS.d/*", "verify/TAGS.d/1.md")

    def test_a3_set_allowed_write_paths_refuses_a_glob_over_verify_files(self):
        """A3 命中本票 verify.files(不在慣例路徑上的那一份)→ 拒收、點名那一份。"""
        self.nv_ticket(verify={"files": ["checks/case_for_one.py"], "tags": [], "run": "",
                               "notes": ""})
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "allowed_write_paths", '["src/*", "checks/*"]')
        self.assert_refused(done, before, "checks/*", "checks/case_for_one.py")

    def test_a3_set_verify_refuses_files_the_worker_scope_already_covers(self):
        """A3 set verify:新的 verify.files 落在 worker glob 裡 → 拒收。"""
        self.nv_ticket(allowed_write_paths=["src/*", "checks/*"])
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "verify", json.dumps(
            {"files": ["checks/case_for_one.py"], "tags": [], "run": "", "notes": ""}))
        self.assert_refused(done, before, "checks/*", "checks/case_for_one.py")

    def test_a3_set_needs_verifier_true_refuses_an_existing_overlap(self):
        """A3 set needs_verifier true:既有 allowed 已含慣例路徑 → 拒收。"""
        self.make("1", allowed_write_paths=["src/*", "verify/**/test_ticket_1.py"],
                  needs_verifier=False, state="Draft")
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "needs_verifier", "true")
        self.assert_refused(done, before, "verify/**/test_ticket_1.py", "test_ticket_1.py")

    def test_a3_turning_ready_refuses_an_existing_overlap(self):
        """A3 轉 Ready:手寫進票檔的重疊,轉 Ready 那一關拒收。"""
        self.nv_ticket(allowed_write_paths=["src/*", "verify/TAGS.d/1.md"])
        before = self.ticket_bytes()
        done = self.ticket("set", "1", "state", "Ready")
        self.assert_refused(done, before, "verify/TAGS.d/1.md", "verify/TAGS.d/1.md")

    def test_a3_lint_reports_the_overlap(self):
        """A3 lint:rc 1、同一句拒收訊息。"""
        self.nv_ticket(allowed_write_paths=["src/*", "verify/**/test_ticket_1.py"], **LINT_OK)
        before = self.ticket_bytes()
        done = self.ticket("lint", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_refused(done, before, "verify/**/test_ticket_1.py", "test_ticket_1.py")

    def test_a3_create_refuses_the_overlap_and_opens_nothing(self):
        """A3 create:沙盒票庫是空的 → 新票是 #1;慣例路徑進 worker 範圍 → 拒收、不開票。"""
        self.assertEqual(self.tickets_on_disk(), [], "前提:票庫是空的,下一張是 #1")
        done = self.ticket("create", "--subject", "s", "--objective", "o", "--acceptance", "a",
                           "--role", "worker", "--model", "opus", "--tool", "claude-code",
                           "--needs-verifier", "true",
                           "--allowed-write-path", "src/*",
                           "--allowed-write-path", "verify/**/test_ticket_1.py")
        out = done.stdout + done.stderr
        self.assertNotEqual(done.returncode, 0, out)
        self.assertIn(MSG_A3, out)
        self.assertIn("verify/**/test_ticket_1.py", out)
        self.assertEqual(self.tickets_on_disk(), [])

    def test_a3_needs_verifier_false_and_another_tickets_case_are_accepted(self):
        """A3 反向:needs_verifier=false 照收;別票(#2)的案例路徑照收。"""
        self.make("1", allowed_write_paths=["src/*"], needs_verifier=False, state="Draft")
        done = self.ticket("set", "1", "allowed_write_paths",
                           '["src/*", "verify/**/test_ticket_1.py"]')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.nv_ticket()
        done = self.ticket("set", "1", "allowed_write_paths",
                           '["src/*", "verify/**/test_ticket_2.py", "verify/TAGS.d/2.md"]')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# ------------------------------------------------------------------ A4


VERIFIER_EVIDENCE = """# EVIDENCE-verifier

## result

```result
{"ticket": "1", "role": "verifier", "round": 1, "rc": 0,
 "verify": {"files": ["%s"], "tags": ["example"],
            "run": "python3 scripts/verify.py --tag example", "notes": "夾具"},
 "baseline": null, "objection": null}
```
""" % CASE_1


class A4TheVerifierHandInStillMerges(World):

    def test_a4_verifier_files_merge_without_entering_the_worker_scope(self):
        """A4 驗證者收件併 verify.files,不被 A3 擋、不加進 allowed_write_paths;
        A5 提示範圍含 verify.files → 那一份不在 extra_paths(M3 在通用 save() 擋 → 紅)。"""
        self.make("1", allowed_write_paths=["src/*"], needs_verifier=True, state="Running")
        worker = self.home_file("p.diff", patch(SRC))
        verifier = self.home_file("patch-verify.diff", patch((CASE_1, None, CASE_BODY)))
        done = self.apply("1", worker, verifier,
                          "--evidence", self.home_file("EVIDENCE.md", evidence([])),
                          "--evidence-verifier", self.home_file("EV.md", VERIFIER_EVIDENCE))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ticket = json.loads(self.ticket_bytes())
        self.assertEqual((ticket.get("verify") or {}).get("files"), [CASE_1])
        self.assertEqual(ticket["allowed_write_paths"], ["src/*"])
        self.assertNotIn(CASE_1, self.result_round1()["extra_paths"],
                         "提示範圍 = allowed ∪ info_homes.home ∪ verify.files")


# ------------------------------------------------------------------ A5


class A5ReasonsPerFile(LandWorld):
    """T2 夾具:a 有理由、b 空字串且命中 out_of_scope、c 沒寫;另有一份 info home。"""

    TICKET = dict(allowed_write_paths=["src/*"], out_of_scope=["docs/*"],
                  info_homes=[{"what": "這張票的筆記", "home": "notes/home.md"}])

    def apply_t2(self):
        self.make("1", **self.TICKET)
        return self.apply("1", self.home_file("p.diff", patch(SRC, LIB_A, DOCS_B, ETC_C, HOME)),
                          "--evidence", self.home_file(
                              "EVIDENCE.md", evidence([("lib/a.py", REASON_A), ("docs/b.md", "")])))

    def test_a5_extra_paths_and_extra_reasons_are_extracted_file_by_file(self):
        """A5 / T2:extra_paths = [a,b,c](info home 不算),extra_reasons = {a:理由, b:null, c:null}。

        期望值手算:提示範圍 = src/* ∪ notes/home.md;b 的空字串與 c 的沒寫都正規化成 null。
        M4(null 當有理由)不在這一條,在 land 那條。
        """
        done = self.apply_t2()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        result = self.result_round1()
        self.assertEqual(sorted(result["extra_paths"]), ["docs/b.md", "etc/c.ini", "lib/a.py"])
        self.assertEqual(result.get("extra_reasons"),
                         {"lib/a.py": REASON_A, "docs/b.md": None, "etc/c.ini": None})

    def test_a5_land_refuses_an_out_of_scope_hit_without_a_reason(self):
        """A5 land:命中 out_of_scope 的 docs/b.md 理由是空字串 → 拒絕,訊息含 MSG_A5 並點名它;
        c 只是不在 allowed、缺理由 → 不在拒絕那幾行(M4 / M6 → 紅)。"""
        done = self.apply_t2()
        self.assertEqual(done.returncode, 0, "apply 要收:\n" + done.stdout + done.stderr)
        self.approve(1, "t1")
        before = self.git("rev-parse", "main").strip()
        landed = self.land()
        out = landed.stdout + landed.stderr
        self.assertNotEqual(landed.returncode, 0, out)
        self.assertIn(MSG_A5, out)
        named = [row for row in out.splitlines() if "docs/b.md" in row]
        self.assertTrue(named, "要點名缺理由的檔:" + out)
        refusal = out.split(MSG_A5, 1)[1].split("\n\n")[0]
        self.assertNotIn("etc/c.ini", refusal, "只是不在 allowed 的缺理由是警告、不擋")
        self.assertEqual(self.git("rev-parse", "main").strip(), before)

    def test_a5_land_only_warns_when_a_plain_extra_file_has_no_reason(self):
        """A5 land:不在 allowed、沒命中 out_of_scope 的檔缺理由 → 照落。"""
        self.apply_then_approve((SRC, LIB_A, ETC_C), [("lib/a.py", REASON_A)],
                                allowed_write_paths=["src/*"], out_of_scope=["docs/*"])
        done = self.land()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("etc/c.ini", self.on_main())

    def test_a5_the_review_packet_shows_each_file_with_its_glob_and_reason(self):
        """A5 覆核派工文逐檔:檔名、命中的 glob(b 的 `docs/*`)、理由(a 的理由甲)。"""
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "規則包的來源")
        done = self.apply_t2()
        self.assertEqual(done.returncode, 0, "apply 要收:\n" + done.stdout + done.stderr)
        self.assertEqual(self.ticket("set", "1", "state", "InReview").returncode, 0)
        # 只有 complex 的票派覆核(#136);這一條量的是派工文,不是派不派。
        self.assertEqual(self.ticket("set", "1", "complexity", "complex").returncode, 0)
        self.set_reviewer(REVIEWER_PASS)
        reviewed = self.run_sh("scripts/review.sh", "1")
        self.assertEqual(reviewed.returncode, 0, reviewed.stdout + reviewed.stderr)
        found = glob.glob(os.path.join(self.repo, "reports", "t1", "*", "dispatch-reviewer.md"))
        self.assertEqual(len(found), 1, found)
        with open(found[0], encoding="utf-8") as handle:
            packet = handle.read()
        for name in ("lib/a.py", "docs/b.md", "etc/c.ini"):
            self.assertIn(name, packet)
        line_a = [row for row in packet.splitlines() if "lib/a.py" in row]
        self.assertTrue(any(REASON_A in row for row in line_a), "a 的理由要跟著檔名:%s" % line_a)
        line_b = [row for row in packet.splitlines() if "docs/b.md" in row]
        self.assertTrue(any("docs/*" in row for row in line_b), "b 命中的 glob:%s" % line_b)


# ------------------------------------------------------------------ A6


def doc(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


class A6DocsSayTheNewRule(unittest.TestCase):

    def test_a6_schema_drops_two_fences_and_names_the_new_fields(self):
        """A6 SCHEMA:「硬擋只有兩格」改掉;寫明 info_homes 與 extra_reasons。"""
        text = doc("tickets/SCHEMA.md")
        self.assertNotIn("硬擋只有兩格", text)
        self.assertIn("extra_reasons", text)
        self.assertIn("info_homes", text)

    def test_a6_role_procedures_drop_the_reverse_instruction(self):
        """A6 刪掉「把本票驗證者檔加入 worker 範圍」的反向指示。"""
        self.assertNotIn("要含驗證者會建立的案例檔", doc("docs/reference/role-procedures.md"))

    def test_a6_dispatch_template_names_the_evidence_section_and_drops_the_old_fence(self):
        """A6 DISPATCH-TEMPLATE:worker 要寫的 EVIDENCE 小節名在;舊的「硬擋只有 out_of_scope 與…」不在。"""
        text = doc("docs/DISPATCH-TEMPLATE.md")
        self.assertIn("提示清單外的改動", text)
        self.assertNotIn("硬擋只有 `out_of_scope` 與", text)

    def test_a6_reviewer_template_asks_for_the_reasons(self):
        """A6 reviewer 範本:out_of_scope 改成看理由,不再只問「有沒有被碰」。"""
        text = doc("templates/dispatch-reviewer.md")
        self.assertIn("理由", text)
        self.assertNotIn("`out_of_scope` 有沒有被碰", text)


# ------------------------------------------------------------------ A8


class A8TheWorkerPacketShowsWhereInfoLives(Sandbox):
    """auto-fix.sh --dry-run --round 1 印第 1 輪派工文(不起 worker)。"""

    INFO_MAP = "docs/INFO-MAP.md#寫入範圍"
    HOME = "notes/where-scope-lives.md"

    def setUp(self):
        super().setUp()
        self.install_rules_sources()
        self.write(os.path.join("memory", "model", "fixture-model.md"),
                   "- 沙盒替身模型:照派工文做,不猜。\n")
        worker = os.path.join(self.home, "fake-worker.sh")
        write_executable(worker, "#!/bin/sh\necho \"worker ran\" >> \"$AC_TEST_LOG\"\nexit 0\n")
        self.conf = json.loads(self.read("board/config.json"))
        self.conf["worker"] = {"command": "sh %s" % worker, "timeout_seconds": 120}

    def ready(self, info_map):
        conf = dict(self.conf)
        if info_map:
            conf["info_map"] = info_map
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))
        self.make_ticket("1", allowed_write_paths=["tests/*"], needs_verifier=False,
                         info_homes=[{"what": "寫入範圍放在哪", "home": self.HOME}])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "開票 #1")
        self.git("push", "-q", "origin", "main")
        return self.run_sh("scripts/auto-fix.sh", "1", "--dry-run", "--round", "1")

    def test_a8_packet_shows_info_map_and_info_homes(self):
        """A8 有設定:派工文含 config.info_map(帶 #節名)與票的 info_homes.home。"""
        done = self.ready(self.INFO_MAP)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(self.INFO_MAP, done.stdout)
        self.assertIn(self.HOME, done.stdout)
        self.assertFalse(os.path.exists(self.log) and "worker ran" in self.read(
            "calls.log", where=self.home), "dry-run 不起 worker")

    def test_a8_packet_without_info_map_still_goes_out(self):
        """A8 缺設定:只提示,派工文照印(rc 0),info_homes.home 照列。"""
        done = self.ready(None)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(self.HOME, done.stdout)
        self.assertIn("info_map", done.stdout + done.stderr, "缺設定要提示一句")


if __name__ == "__main__":
    unittest.main()
