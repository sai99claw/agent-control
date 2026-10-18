"""#133 classify 認得真 codex CLI 結尾的純文字額度 ERROR 行。

## 驗收表(票面每一條一列;期望值來源獨立於被測程式:真 log / 票面 acceptance / 既有夾具表)
A1 | cli   | 複製 #122 worker / verifier 真 log 末段，各以 rc=1 跑 classify | kind=quota_exhausted，detail 含 usage limit | 兩份 reports 真 log 末兩行 + 票面 A1
A2 | integ | 假 codex 對 stderr 印兩次真額度行後 exit 1，worker 鏈 codex→opus | 下一候選在同副本啟動；argv / cost 末列 model=opus | 票面 A2 + FallbackBase 寫死的第二格
A3 | cli   | unittest 中段 ERROR、text429、一般 ERROR、rc=0 與 #115 真 log | 全部 kind=other | 票面 A3 + 三份 reports 真 log
A4 | cli   | 頂層 model 不存在信封後接真額度行，rc=1 | kind=provider_unavailable | 票面 A4「信封優先」

## 介面字串
REAL = 真 log 逐字額度行；classify stdout 是 {"kind": ..., "detail": ...}。
下一候選由 tests.test_auto_fix_codex.FallbackBase 的 worker 鏈第二格寫死為 opus / high。

## 怎麼做假(不上真埠、不起真服務、不呼叫真模型)
classify 案例把指定真 log 的逐字末段 golden 複製到 tempfile；整合案例借 FallbackBase 的拋棄式
控制根與假 opus，只把 PATH 前方的假 codex 換成印兩次 REAL 後 exit 1 的腳本。

## 不做
不改產品碼；不放寬票面驗收；不刪既有案例；不呼叫真 codex；不跑全套或 tag 回歸。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TAGS = ["role-dispatch"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS)
from control_harness import write_executable  # noqa: E402
from test_auto_fix_codex import CODEX_MODEL, FallbackBase  # noqa: E402

REAL = ("ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
        "at 11:50 PM.")
T122_TAIL = ("不跑全套(unittest discover 整組、gate.sh --full)、不跑 verify.py 全部回歸\n\n"
             + REAL + "\n" + REAL + "\n")
REPORT_LOGS = {
    "verifier": T122_TAIL,
    "worker": T122_TAIL,
    "middle-error": (
        'rc, out, err = self.command("--model", "fable", "--tool", "claude-code")\n\n'
        "ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
        "at 5:13 AM.\n"
        "ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
        "at 5:13 AM.\n"
        "tokens used\n189,715\n"),
}
PLAIN_QUOTA_CODEX = r'''#!/bin/sh
printf '%s\n' "$@" > "@ARGV@"
printf '%s\n%s\n%s\n' "$(pwd -P)" "$AC_ROLE" "$AC_ROOT" > "@ARGV@.where"
cat > "@STDIN@"
echo "codex ran $AC_ROLE round $AC_ROUND" >> "$AC_TEST_LOG"
echo "@REAL@" >&2
echo "@REAL@" >&2
exit 1
'''


def clean_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("AC_")}


class ClassifyRealCodexLogs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t133-"); self.addCleanup(shutil.rmtree, self.tmp, True)

    def copied_report(self, key):
        target = os.path.join(self.tmp, "%s.log" % key)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(REPORT_LOGS[key])
        return target

    def evidence(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def classify(self, path, rc=1):
        done = subprocess.run(
            [sys.executable, "-B", os.path.join(ROOT, "scripts", "role-models.py"),
             "classify", "--evidence", path, "--rc", str(rc)],
            capture_output=True, text=True, timeout=30, env=clean_env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def test_a1_both_real_t122_logs_are_quota_exhausted(self):
        """A1 兩份 #122 真 log 的結尾純文字額度行都判 quota_exhausted。"""
        paths = [self.copied_report(key) for key in ("verifier", "worker")]
        paths.append(self.evidence("single.log", REAL + "\n"))
        paths.append(self.evidence("dialogue.log", "OpenAI Codex v0.153.4\n對話幾行\n"
                                   + REAL + "\n" + REAL + "\n"))
        for path in paths:
            with self.subTest(report=os.path.basename(path)):
                got = self.classify(path)
                self.assertEqual(got["kind"], "quota_exhausted", got)
                self.assertIn("usage limit", got["detail"])

    def test_a3_plain_text_false_positives_and_rc_zero_stay_other(self):
        """A3 非結尾額度 ERROR、一般 ERROR、text429 與 rc=0 都維持 other。"""
        rows = (
            ("reconnect.log", "ERROR: Reconnecting... 4/5\n", 1),
            ("unrelated.log", "ERROR: something unrelated broke\n", 1),
            ("unittest.log", "ERROR: test_quota_fallback (tests.test_x.Y)\n"
             "Traceback (most recent call last):\nAssertionError\ntokens used\n1234\n", 1),
            ("text429.log", "ERROR: stream error: 429 Too Many Requests\n"
             "You've hit your usage limit.\n", 1),
            ("literal429.log", "429 rate limit\n", 1),
            ("success.log", REAL + "\n", 0),
        )
        for name, body, rc in rows:
            with self.subTest(name=name):
                got = self.classify(self.evidence(name, body), rc)
                self.assertEqual(got["kind"], "other", got)
        got = self.classify(self.copied_report("middle-error"))
        self.assertEqual(got["kind"], "other", got)

    def test_a4_provider_envelope_wins_over_the_plain_quota_tail(self):
        """A4 頂層 model 不存在信封優先於後面的純文字額度行。"""
        body = ('{"type":"error","message":"model gpt-x does not exist"}\n'
                + REAL + "\n" + REAL + "\n")
        got = self.classify(self.evidence("envelope-first.log", body))
        self.assertEqual(got["kind"], "provider_unavailable", got)


class PlainQuotaTakesTheNextCandidate(FallbackBase):

    def install_plain_quota_codex(self):
        body = (PLAIN_QUOTA_CODEX.replace("@ARGV@", self.argv_file)
                .replace("@STDIN@", self.stdin_file).replace("@REAL@", REAL))
        write_executable(os.path.join(self.stub_bin, "codex"), body)

    def test_a2_plain_quota_relaunches_the_table_second_candidate(self):
        """A2 假 codex 印兩次真額度行後，下一格 opus 在同副本接手且 cost 末列是 opus。"""
        self.install_plain_quota_codex()
        self.codex_ticket()
        done = self.auto_fix("--no-review")
        self.assertEqual(done.returncode, 0, "純文字額度行沒有啟動下一候選(rc=%s)" % done.returncode)
        self.assertEqual((len(self.codex_calls()), len(self.opus_calls())), (1, 1))
        with open(self.argv_file + ".where", encoding="utf-8") as handle:
            codex_cwd = handle.read().splitlines()[0]
        with open(self.opus_argv + ".where", encoding="utf-8") as handle:
            opus_cwd = handle.read().strip()
        self.assertEqual(opus_cwd, codex_cwd)
        argv = self.opus_args()
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual([(row["model"], row["outcome"]) for row in self.worker_cost()],
                         [(CODEX_MODEL, "error"), ("opus", "handed-in")])
