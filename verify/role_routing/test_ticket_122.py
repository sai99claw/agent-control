"""#122 開題、設計與整理派工統一取角色表;整理只派一個 Fable;舊選路設定退場;專案同步角色表。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:票面 acceptance 原話 / 本檔夾具寫下的角色表)
A1 | cli   | new-session.sh dispatch opener / opener --complexity complex / design,表 opener=[opus]、opener_complex=design=[astra cap5, fable] | 一般起 opus 一次;複雜與設計起 astra 一次 | 票面 A1「一般 opener=opus;複雜開題=astra→fable;design=astra→fable」
A1 | cli   | 同上,假 codex 印額度信封 / 一般退出 3 | 額度 → 接著起 fable;一般失敗只起 astra、rc≠0 | 票面 A1「前者不可用才換下一個」
A1 | cli   | dispatch design --items 6(astra cap 5);dispatch opener --items 9(opus cap null) | 前者 rc=2、印 ≤5、一個程序都不起;後者照起 | 票面 A1「超過 dispatch_cap 就 rc=2、印切批建議,不起程序;cap=null 不擋」
A1 | integ | consolidate-memory.sh,票 model=opus、config memory.consolidators=[opus, astra]、表 consolidator=[fable] | 只起一個 session、--model fable;agent.start 只有 fable | 票面 A1「consolidator 只啟動一個 fable」+ A3
A1 | integ | 同上,假 fable 寫一份討論檔與候選後交件;再跑 memory.py consolidate 只帶那一份 | consolidate rc 0、主檔 = 候選 | 票面 A1「沒有雙模型互讀門檻」
A1 | integ | 表 consolidator=[fable];假 fable 印額度信封 | 只起 1 次、票 NeedsDecision / owner main / 租約清空、一頁 decision、有一列 role=consolidator 的 cost | 票面 A1「Fable 整理不可用即耗盡停」+ test_plan T1「成本證據」
A1 | integ | 表 consolidator=[fable, sonnet];fable 額度 / fable 一般退出 3 | 前者接著起 sonnet、cost 兩列;後者只起 fable、不換 | 票面 A1「前者不可用才換下一個」
A2 | integ | auto-fix 判反駁,表 opener=[fable]、opener_complex=[哨兵];票 model=opus、complexity=complex;config opener.command 寫 --model opus | 開題者 argv 的 --model 是 fable;opener cost 的 model 是 fable | 票面 A2「auto-fix 判反駁的 opener 走 #116」
A2 | cli   | new-session.sh main fable,表 main=[哨兵模型] | session.start 記 fable;輸出有一行帶哨兵模型名(建議) | 票面 A2「main 仍由啟動指定模型,僅顯示表中建議,不熱切」
A3 | cli   | role-models.py legacy --config <拷貝>(routing.open=sonnet 等、memory.consolidators、second_opinion/discuss) | 不帶 --write:列出衝突、檔不變;--write:被取代的格刪掉,second_opinion/discuss 原值 | 票面 A3「警告舊衝突設定並提供一次明確遷移;second_opinion/discuss 維持」
A4 | cli   | sync-to-project.sh 到空專案 / 合法自訂表 / 非法表 | 裝表與選路器、可 resolve;自訂表位元組不變且輸出點名不同的角色;非法表不覆蓋、輸出點名表、不會靜默可用 | 票面 A4 原話
A5 | doc   | ROLES / role-procedures / 兩份範本 / consolidator.inbox.md | 不再寫雙模型或 routing.open/design 為來源;寫出 dispatch_cap / --items;角色卡改動在 inbox | 票面 A5 原話

## 介面字串(票面沒定的照 worker patch 的介面,EVIDENCE 記一行)
DISPATCH = sh scripts/new-session.sh dispatch <opener|design> --items N --prompt <檔> [--complexity complex]
LEGACY   = python3 scripts/role-models.py legacy --config <檔> [--write]
QUOTA    = 一行頂層 JSON {"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}(#121/#132 夾具同一個信封)
CAP_HINT = "≤5"(#116 A8 的切批句)
(票面 verify_strings 的原字在本檔用拼接,不讓落地 grep 命中自己)

## 怎麼做假(不上真埠、不起真服務、不打真的模型)
control_harness.Sandbox 拋棄式 repo(PATH 最前面的 claude 是絆線)。claude-code 候選 = config 命令指向的假 agent
(python 腳本,記 argv 與模型一行 JSON,照本檔的 plan 退出);codex 候選 = stub-bin 裡同一支假 agent。
判反駁借 tests/test_objection_triage.TriageBase 的劇本 claude。sync 用 tempfile 專案目錄。

## 不做
不改產品碼;不放寬票面驗收;不刪既有案例;不呼叫真 LLM;不跑 tag 回歸。
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["role-consumers"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from control_harness import DEFAULT_CONFIG, Sandbox, fixture_candidate, write_executable  # noqa: E402
from test_objection_triage import TriageBase  # noqa: E402

# ---- 介面字串:斷言只認這裡的常數,改這裡等於改契約 ----
RM = "role-" + "models"
TABLE_REL = os.path.join("board", RM + ".json")
ROUTER = RM + ".py"
ASTRA_MODEL = "codex:gpt-6-astra"
ASTRA = fixture_candidate(ASTRA_MODEL, "codex", cap=5)
FABLE = fixture_candidate("fable", cap=None)
OPUS = fixture_candidate("opus", cap=None)
# 整理鏈的第二格:沙盒把 memory/model/opus.md 寫成超標的被整理檔,opus 的規則包產不出來(與換人無關),第二格用 sonnet。
BACKUP = fixture_candidate("sonnet", cap=None)
CAP_HINT = "≤5"
QUOTA_LINE = '{"type":"turn.failed","error":{"message":"You have hit your usage limit (429)."}}'

# 假 agent:每次被叫記一行 {tool, model, argv};照 PLAN 檔裡「模型 → 模式」退出。
# discuss 模式 = 整理者真的交件:照 memory.py snapshot 寫一份討論檔($AC_DISCUSSION)與候選。
FAKE_AGENT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
CALLS, PLAN = "@CALLS@", "@PLAN@"
argv = sys.argv[1:]
model = ""
for flag in ("--model", "-m"):
    if flag in argv and argv.index(flag) + 1 < len(argv):
        model = argv[argv.index(flag) + 1]
sys.stdin.read()
with open(CALLS, "a", encoding="utf-8") as h:
    h.write(json.dumps({"tool": os.path.basename(sys.argv[0]), "model": model, "argv": argv}) + "\n")
with open(PLAN, encoding="utf-8") as h:
    plan = json.load(h)
mode = plan.get(model, plan.get("*", "ok"))
if mode == "fail":
    sys.exit(3)
if mode == "quota":
    print(@QUOTA@)
    sys.exit(1)
if mode == "discuss":
    snap = subprocess.run([sys.executable, os.path.join(os.environ["AC_ROOT"], "scripts", "memory.py"),
                           "snapshot", plan["memory_file"]], capture_output=True, text=True).stdout
    count = int(snap.split("source_lines:")[1].split()[0])
    talk = os.environ["AC_DISCUSSION"]
    os.makedirs(os.path.dirname(talk) or ".", exist_ok=True)
    with open(talk, "w", encoding="utf-8") as h:
        h.write("---\nmodel: %s\n%s---\n\n## 第 1 輪 — %s\n- 主張:逐條處置\n\n## 結論\n%s"
                % (os.environ["AC_MODEL"].split(":")[-1], snap, model,
                   "".join("- L%d: 保留\n" % n for n in range(1, count + 1))))
    os.makedirs(os.path.dirname(plan["candidate"]), exist_ok=True)
    with open(plan["candidate"], "w", encoding="utf-8") as h:
        h.write(plan["candidate_text"])
sys.exit(0)
'''


class FakeAgents:
    """混入 Sandbox:裝一支假 claude-code agent(回傳它的路徑)與 stub-bin 的假 codex。"""

    def arm_agents(self, plan):
        self.calls_path = os.path.join(self.home, "agent-calls.jsonl")
        self.plan_path = os.path.join(self.home, "agent-plan.json")
        with open(self.plan_path, "w", encoding="utf-8") as handle:
            json.dump(plan, handle)
        body = (FAKE_AGENT.replace("@CALLS@", self.calls_path).replace("@PLAN@", self.plan_path)
                .replace("@QUOTA@", repr(QUOTA_LINE)))
        fake = os.path.join(self.home, "fake-agent")
        write_executable(fake, body)
        write_executable(os.path.join(self.stub_bin, "codex"), body)
        return fake

    def agent_calls(self):
        if not os.path.exists(self.calls_path):
            return []
        with open(self.calls_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def started(self):
        return [row.get("model") for row in self.events() if row["kind"] == "agent.start"]

    def edit_config(self, **sections):
        conf = json.loads(self.read("board/config.json"))
        conf.update(sections)
        self.write("board/config.json", json.dumps(conf, ensure_ascii=False, indent=2))


# ============================================================ A1 獨立開題 / 設計派工

class A1DispatchPicksFromTheTable(FakeAgents, Sandbox):

    def setUp(self):
        super().setUp()
        self.prompt = self.write("prompt.md", "# 派工文\n開三張票\n")
        self.set_role_models(opener=[OPUS], opener_complex=[ASTRA, FABLE], design=[ASTRA, FABLE])

    def arm(self, **plan):
        fake = self.arm_agents(plan)
        # 舊選路格故意寫成別的模型(A3:它們不再是選路來源)。
        self.edit_config(opener={"command": "%s -p --model opus" % fake, "timeout_seconds": 60},
                         routing={"open": "sonnet", "design": "sonnet", "main": "opus"})

    def dispatch(self, role, *flags):
        return self.run_sh("scripts/new-session.sh", "dispatch", role, "--prompt", self.prompt,
                           *flags)

    def test_a1_a_plain_opener_is_opus(self):
        """A1 一般 opener=opus:起一次、--model opus,不看 routing.open。

        四問:守一般開題照表 / 壞掉時:沒有 dispatch 或照 routing.open → 起 0 次或 sonnet /
        期望 [opus] 來自本檔的表 / 不加縫。"""
        self.arm()
        done = self.dispatch("opener", "--items", "3")
        calls = self.agent_calls()
        self.assertEqual([(c["tool"], c["model"]) for c in calls], [("fake-agent", "opus")],
                         done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.started(), ["opus"])

    def test_a1_complex_opener_and_design_start_astra(self):
        """A1 明確複雜開題與 design 都先起 astra(codex),一次。"""
        self.arm()
        for role, flags in (("opener", ("--complexity", "complex")), ("design", ())):
            with self.subTest(role=role):
                before = len(self.agent_calls())
                done = self.dispatch(role, "--items", "2", *flags)
                calls = self.agent_calls()[before:]
                self.assertEqual([(c["tool"], c["model"]) for c in calls],
                                 [("codex", "gpt-6-astra")], done.stdout + done.stderr)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a1_astra_out_of_quota_moves_to_fable(self):
        """A1 前者不可用才換下一個:astra 額度信封 → 接著起 fable。"""
        self.arm(**{"gpt-6-astra": "quota"})
        done = self.dispatch("design", "--items", "2")
        calls = self.agent_calls()
        self.assertEqual([c["model"] for c in calls], ["gpt-6-astra", "fable"],
                         done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.started(), [ASTRA_MODEL, "fable"])

    def test_a1_an_ordinary_astra_failure_does_not_move_on(self):
        """A1 一般失敗不換:astra 退出 3 → 只起 astra,rc≠0。"""
        self.arm(**{"gpt-6-astra": "fail"})
        done = self.dispatch("opener", "--items", "2", "--complexity", "complex")
        self.assertEqual([c["model"] for c in self.agent_calls()], ["gpt-6-astra"],
                         done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a1_more_items_than_the_astra_cap_starts_nothing(self):
        """A1 --items 超過 astra dispatch_cap 5 → rc=2、印切批建議、不起程序。"""
        self.arm()
        done = self.dispatch("design", "--items", "6")
        out = done.stdout + done.stderr
        self.assertIn(CAP_HINT, out)
        self.assertEqual(done.returncode, 2, out)
        self.assertEqual(self.agent_calls(), [])
        self.assertEqual(self.started(), [])

    def test_a1_a_null_cap_does_not_block(self):
        """A1 opus 候選 cap=null:--items 9 照起。"""
        self.arm()
        done = self.dispatch("opener", "--items", "9")
        self.assertEqual([c["model"] for c in self.agent_calls()], ["opus"],
                         done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# ============================================================ A1 整理只派一個 Fable

MEMORY_FILE = "memory/model/opus.md"
MEMORY_INBOX = "memory/model/opus.inbox.md"
CANDIDATE = "candidates/opus.md"
CANDIDATE_TEXT = "- 整理後的一條原則 (#10, 2026-10-01, fable@fable)\n"


class ConsolidatorFixture(FakeAgents, Sandbox):

    def setUp(self):
        super().setUp()
        for rel in (os.path.join("scripts", "consolidate-memory.sh"),
                    os.path.join("templates", "dispatch-consolidator.md")):
            shutil.copy(os.path.join(ROOT, rel), os.path.join(self.repo, rel))
        self.install_rules_sources()
        self.write(MEMORY_FILE, "---\ncap_chars: 2000\ncap_history: []\n---\n" + "舊" * 2100)
        self.write(MEMORY_INBOX, "- 待整理一 (#11, 2026-09-01, worker@opus)\n"
                                 "- 待整理二 (#12, 2026-09-01, worker@opus)\n")

    def arm(self, consolidators=None, **plan):
        plan.setdefault("memory_file", MEMORY_FILE)
        plan.setdefault("candidate", CANDIDATE)
        plan.setdefault("candidate_text", CANDIDATE_TEXT)
        fake = self.arm_agents(plan)
        memory = dict(DEFAULT_CONFIG["memory"])
        if consolidators:
            memory["consolidators"] = consolidators
        self.edit_config(worker={"command": "%s -p --model opus" % fake, "timeout_seconds": 600},
                         memory=memory)
        self.make_ticket(7, role="consolidator", state="Ready", model="opus",
                         in_scope=[MEMORY_FILE, MEMORY_INBOX],
                         allowed_write_paths=[MEMORY_FILE, MEMORY_INBOX])

    def launch(self):
        return self.run_sh("scripts/consolidate-memory.sh", "7")

    def costs(self):
        return [(row.get("role"), row.get("model")) for row in self.load_ticket("7").get("cost") or []]

    def decision_pages(self):
        path = os.path.join(self.repo, "reports", "inbox", "index.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return [row for row in rows if row.get("kind") == "decision"
                and str(row.get("ticket")) == "7"]

    def assert_stopped_for_main(self, done):
        ticket = self.load_ticket("7")
        out = done.stdout + done.stderr
        self.assertEqual((ticket["state"], ticket.get("owner")), ("NeedsDecision", "main"), out)
        self.assertIsNone(ticket.get("lease"), out)
        self.assertEqual(len(self.decision_pages()), 1, out)  # pin:票面「一頁 decision」


class A1OneFableConsolidates(ConsolidatorFixture):

    def test_a1_only_one_fable_session_whatever_the_ticket_and_config_say(self):
        """A1 consolidator 只啟動一個 fable;票 model=opus、config 兩模型名單都不參與(A3)。

        四問:守單一 fable / 壞掉時:照 consolidators 起兩個或照票起 opus → 次數或模型錯 /
        期望 [fable] 來自本檔的表 / 不加縫。"""
        self.set_role_models(consolidator=[FABLE])
        self.arm(consolidators=["opus", ASTRA_MODEL])
        done = self.launch()
        out = done.stdout + done.stderr
        self.assertEqual([(c["tool"], c["model"]) for c in self.agent_calls()],
                         [("fake-agent", "fable")], out)
        self.assertEqual(done.returncode, 0, out)
        self.assertEqual(self.started(), ["fable"])
        self.assertEqual(self.costs(), [("consolidator", "fable")])

    def test_a1_the_single_discussion_is_enough_to_consolidate(self):
        """A1 沒有雙模型互讀門檻:fable 一份討論檔 → memory.py consolidate 收得進去。

        四問:守單一整理者收得進 / 壞掉時:consolidate 仍要兩份 → rc≠0、主檔不變 /
        期望主檔 = 本檔的候選文字 / 不加縫(討論檔由假 fable 依 memory.py snapshot 寫)。"""
        self.set_role_models(consolidator=[FABLE])
        self.arm(fable="discuss")
        done = self.launch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        talks = [row.get("discussion") for row in self.load_ticket("7").get("attempt_history") or []
                 if isinstance(row, dict) and row.get("discussion")]
        self.assertEqual(len(talks), 1, self.load_ticket("7").get("attempt_history"))  # pin:票面「沒有雙模型」:恰一份討論
        merged = self.run_py("scripts/memory.py", "consolidate", MEMORY_FILE,
                             "--candidate", CANDIDATE, "--discussion", talks[0])
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn(CANDIDATE_TEXT, self.read(MEMORY_FILE))


class A1ConsolidatorFallback(ConsolidatorFixture):

    def test_a1_fable_unavailable_is_exhausted_and_stops(self):
        """A1 Fable 整理不可用即耗盡停:只起 fable 一次、送 decision、記一列 cost,不改派。"""
        self.set_role_models(consolidator=[FABLE])
        self.arm(fable="quota")
        done = self.launch()
        self.assertEqual([c["model"] for c in self.agent_calls()], ["fable"],
                         done.stdout + done.stderr)
        self.assert_stopped_for_main(done)
        self.assertEqual(self.costs(), [("consolidator", "fable")])

    def test_a1_a_quota_fable_moves_to_the_next_candidate(self):
        """A1 前者不可用才換下一個:鏈 [fable, sonnet],fable 額度 → 接著起 sonnet,cost 兩列。"""
        self.set_role_models(consolidator=[FABLE, BACKUP])
        self.arm(fable="quota")
        done = self.launch()
        out = done.stdout + done.stderr
        self.assertEqual([c["model"] for c in self.agent_calls()], ["fable", "sonnet"], out)
        self.assertEqual(done.returncode, 0, out)
        self.assertEqual(self.costs(), [("consolidator", "fable"), ("consolidator", "sonnet")])

    def test_a1_an_ordinary_failure_does_not_move_on(self):
        """A1 一般失敗不換:鏈 [fable, sonnet],fable 退出 3 → 只起 fable、送 decision。"""
        self.set_role_models(consolidator=[FABLE, BACKUP])
        self.arm(fable="fail")
        done = self.launch()
        self.assertEqual([c["model"] for c in self.agent_calls()], ["fable"],
                         done.stdout + done.stderr)
        self.assert_stopped_for_main(done)
        self.assertEqual(self.costs(), [("consolidator", "fable")])


# ============================================================ A2

class A2TriageOpenerComesFromTheTable(TriageBase):

    def test_a2_the_triage_opener_is_the_table_opener(self):
        """A2 auto-fix 判反駁的開題者走角色表 opener 鏈:不照 opener.command 的 --model、
        不照票的 model、不因票 complexity=complex 改走 opener_complex。

        四問:守判反駁取表 / 壞掉時:照 config 起 opus 或走複雜鏈 → --model 不是 fable /
        期望 fable 來自本檔的表 / 劇本 claude 借 TriageBase(opener=garbage → rc 3)。"""
        self.set_role_models(opener=[fixture_candidate("fable", cap=None)],
                             opener_complex=[fixture_candidate("complex-sentinel", cap=None)])
        self.arm(model="opus", complexity="complex")
        self.play(opener="garbage")
        done = self.auto_fix("--no-review")
        openers = self.calls("opener")
        self.assertEqual(len(openers), 1, done.stdout + done.stderr)  # pin:判反駁只起一個開題者
        self.assertEqual(self.flag_value(openers[0]["argv"], "--model"), "fable")
        rows = [row.get("model") for row in self.load_ticket("1").get("cost") or []
                if row.get("role") == "opener"]
        self.assertEqual(rows, ["fable"])


class A2MainKeepsItsModel(Sandbox):

    def test_a2_main_runs_the_launched_model_and_only_shows_the_suggestion(self):
        """A2 new-session 的互動 main 由啟動指定模型,只顯示表中建議、不熱切。

        四問:守 main 不熱切且有建議 / 壞掉時:不印建議 → 哨兵不在輸出;熱切 → session.start 不是 fable /
        期望值:啟動參數 fable、本檔表的哨兵 / 不加縫。"""
        self.set_role_models(main=[fixture_candidate("suggested-main-sentinel", cap=None)])
        done = self.run_sh("scripts/new-session.sh", "main", "fable", "--session", "sid-122")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        starts = [row.get("model") for row in self.events() if row["kind"] == "session.start"]
        self.assertEqual(starts, ["fable"], out)
        self.assertTrue([line for line in done.stdout.splitlines()
                         if "suggested-main-sentinel" in line], out)


# ============================================================ A3

LEGACY_CONFIG = {
    "routing": {"main": "opus", "open": "sonnet", "design": "sonnet", "bug": "fable",
                "implement": "opus", "implement_browser": "opus", "verify": "opus",
                "second_opinion": "agy:gemini-3.8-flash-high", "discuss": "agy:gemini-3.8-flash-high"},
    "memory": {"unit": "chars", "cap_chars": 2000, "consolidator": "fable",
               "consolidators": ["fable", ASTRA_MODEL]},
}
REPLACED = ("open", "design", "bug", "implement", "implement_browser", "verify")


class A3LegacySettingsMigrateOnce(unittest.TestCase):

    def setUp(self):
        self.where = tempfile.mkdtemp(prefix="t122-"); self.addCleanup(shutil.rmtree, self.where, True)
        self.conf = os.path.join(self.where, "config.json")
        with open(self.conf, "w", encoding="utf-8") as handle:
            json.dump(LEGACY_CONFIG, handle, ensure_ascii=False, indent=2)

    def legacy(self, *flags):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", ROUTER),
                               "--table", os.path.join(ROOT, TABLE_REL), "legacy",
                               "--config", self.conf, *flags],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_a3_the_tool_warns_then_migrates_once(self):
        """A3 工具警告舊衝突設定並提供一次明確遷移;second_opinion/discuss 維持原設定。

        四問:守警告 + 一次遷移 / 壞掉時:沒有工具 rc≠0;遷移漏刪或刪了 second_opinion → 鍵集合錯 /
        期望鍵集合抄自票面 A3 / 不加縫。"""
        with open(self.conf, "rb") as handle:
            before = handle.read()
        said = self.legacy()
        out = said.stdout + said.stderr
        self.assertEqual(said.returncode, 0, out)
        for key in ("routing.open", "routing.design", "memory.consolidators"):
            self.assertTrue([line for line in out.splitlines() if key in line and "衝突" in line],
                            key + " 沒被標成衝突:\n" + out)
        with open(self.conf, "rb") as handle:
            self.assertEqual(handle.read(), before, "沒帶 --write 卻改了設定")
        wrote = self.legacy("--write")
        self.assertEqual(wrote.returncode, 0, wrote.stdout + wrote.stderr)
        with open(self.conf, encoding="utf-8") as handle:
            after = json.load(handle)
        for key in REPLACED:
            self.assertNotIn(key, after["routing"])
        for key in ("consolidator", "consolidators"):
            self.assertNotIn(key, after["memory"])
        for key in ("second_opinion", "discuss"):
            self.assertEqual(after["routing"][key], LEGACY_CONFIG["routing"][key])
        self.assertEqual((after["memory"]["unit"], after["memory"]["cap_chars"]), ("chars", 2000))


# ============================================================ A4

def write_project_config(dest):
    os.makedirs(os.path.join(dest, "board"), exist_ok=True)
    with open(os.path.join(dest, "board", "config.json"), "w", encoding="utf-8") as handle:
        handle.write('{"rules":{"roles_dir":"docs/roles","models_dir":"docs/roles/model"},'
                     '"memory":{"applies_to":["memory/model/*.md"]}}')


def a_project(case):
    where = tempfile.mkdtemp(prefix="t122-proj-"); case.addCleanup(shutil.rmtree, where, True)
    write_project_config(where)
    return where


def sha_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class A4SyncCarriesTheTable(unittest.TestCase):

    def sync(self, dest):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        return subprocess.run(["sh", os.path.join(ROOT, "scripts", "sync-to-project.sh"), dest],
                              capture_output=True, text=True, env=env, timeout=180)

    def resolve(self, dest, role):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AC_")}
        return subprocess.run([sys.executable, os.path.join(dest, "scripts", "control", ROUTER),
                               "--table", os.path.join(dest, TABLE_REL), "resolve", "--role", role],
                              capture_output=True, text=True, env=env, timeout=60)

    def our_table(self):
        with open(os.path.join(ROOT, TABLE_REL), encoding="utf-8") as handle:
            return json.load(handle)

    def put_table(self, dest, table):
        with open(os.path.join(dest, TABLE_REL), "w", encoding="utf-8") as handle:
            json.dump(table, handle, ensure_ascii=False, indent=2)

    def test_a4_an_empty_project_gets_the_router_and_the_table(self):
        """A4 不存在時安裝初表,並同步共用選路器;同步完專案端選得出整理者 fable。

        四問:守表與選路器都到 / 壞掉時:只同步腳本 → 表不在、resolve 失敗 /
        期望表 = agent-control 那一份、fable 來自票面 A1 / 不加縫。"""
        dest = a_project(self)
        done = self.sync(dest)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isfile(os.path.join(dest, TABLE_REL)), done.stdout)
        self.assertEqual(sha_of(os.path.join(dest, TABLE_REL)), sha_of(os.path.join(ROOT, TABLE_REL)))
        picked = self.resolve(dest, "consolidator")
        self.assertEqual(picked.returncode, 0, picked.stdout + picked.stderr)
        self.assertEqual(json.loads(picked.stdout)["model"], "fable")
        self.assertIn(RM + ".json", done.stdout, "同步結果沒說角色表的狀態")

    def test_a4_a_legal_custom_table_is_kept_and_the_difference_named(self):
        """A4 專案已有合法自訂表 → 保留(位元組不變)並提示差異(點名不同的角色)。"""
        dest = a_project(self)
        table = self.our_table()
        table["roles"]["opener"] = [FABLE]
        self.put_table(dest, table)
        before = sha_of(os.path.join(dest, TABLE_REL))
        done = self.sync(dest)
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertEqual(sha_of(os.path.join(dest, TABLE_REL)), before, "自訂表被覆蓋")
        self.assertTrue([line for line in out.splitlines() if "opener" in line], out)

    def test_a4_an_illegal_table_is_refused_not_silently_used(self):
        """A4 非法表拒絕派工而非靜默用舊設定:不覆蓋、輸出點名角色表;同步若照常完成,專案端選路必須 rc=2。"""
        dest = a_project(self)
        table = self.our_table()
        table["roles"]["consolidator"] = []
        self.put_table(dest, table)
        before = sha_of(os.path.join(dest, TABLE_REL))
        done = self.sync(dest)
        out = done.stdout + done.stderr
        self.assertEqual(sha_of(os.path.join(dest, TABLE_REL)), before, "非法表被悄悄換掉")
        self.assertTrue([line for line in out.splitlines() if RM + ".json" in line], out)
        if done.returncode == 0:
            self.assertEqual(self.resolve(dest, "consolidator").returncode, 2, out)


# ============================================================ A5

def read_root(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def section(text, heading):
    part = text.split(heading, 1)
    if len(part) < 2:
        return ""
    return part[1].split("\n## ", 1)[0]


def row_of(text, head):
    return next((line for line in text.splitlines() if line.startswith(head)), "")


class A5DocsSayOneModel(unittest.TestCase):

    def test_a5_roles_table_names_the_role_table_not_the_old_keys(self):
        """A5 ROLES:整理者單一 fable、不寫 memory.consolidators;開題 / 設計不再寫 routing.open / design 為來源;有 dispatch_cap 與 effort。"""
        text = read_root(os.path.join("docs", "ROLES.md"))
        keeper = row_of(text, "| **整理者**")
        self.assertIn("fable", keeper)
        self.assertNotIn("memory.consolidators", keeper)
        self.assertNotIn("routing.open", row_of(text, "| **開題者**"))
        self.assertNotIn("routing.design", row_of(text, "| **設計 session**"))
        for word in ("dispatch_cap", "--items", "effort"):
            self.assertIn(word, text)

    def test_a5_procedures_and_templates_describe_one_model_and_the_cap(self):
        """A5 操作文件與範本:整理者一個 session、合併只帶一份討論;開題 / 設計派工帶 --items、受 dispatch_cap 擋。"""
        procedures = read_root(os.path.join("docs", "reference", "role-procedures.md"))
        self.assertIn("consolidator", section(procedures, "## 整理者角色操作細節"))
        for heading in ("## 開題者角色操作細節", "## 設計 session 操作細節"):
            with self.subTest(heading=heading):
                part = section(procedures, heading)
                self.assertIn("dispatch_cap", part)
                self.assertIn("--items", part)
        template = read_root(os.path.join("templates", "dispatch-consolidator.md"))
        self.assertNotIn("兩個模型各開", template)
        merge = [block for block in template.split("```") if "memory.py consolidate" in block]
        self.assertEqual(len(merge), 1, "範本的合併指令不是恰好一塊")  # pin:合併指令只有一塊
        self.assertEqual(merge[0].count("--discussion"), 1, merge[0])  # pin:票面「沒有雙模型互讀」:合併只帶一份
        opener = read_root(os.path.join("templates", "dispatch-opener.md"))
        for word in ("--items", "dispatch_cap", RM):
            self.assertIn(word, opener)

    def test_a5_the_role_card_change_goes_to_the_inbox(self):
        """A5 角色卡通用規則變更送 inbox:memory/role/consolidator.inbox.md 有一行帶 #122。"""
        path = os.path.join(ROOT, "memory", "role", "consolidator.inbox.md")
        self.assertTrue(os.path.isfile(path), "整理者 inbox 不在")
        self.assertTrue([line for line in read_root(os.path.relpath(path, ROOT)).splitlines()
                         if "#122" in line])


if __name__ == "__main__":
    unittest.main()
