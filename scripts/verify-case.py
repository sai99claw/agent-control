#!/usr/bin/env python3
"""驗證產物工具:同一份案例,在乾淨主線該紅、在 candidate 該綠 — D-014。

    scripts/verify-case.py red 7 --candidate <$W/work>     # 只驗紅(驗證者交件,stage=red)
    scripts/verify-case.py lint verify/nav/test_ticket_7.py --ticket 7   # 案例格式 F1–F12
    scripts/verify-case.py lint --baseline verify/nav/*.py   # F7–F12 白名單(貼進 verify_lint.allow)
    scripts/verify-case.py check 7            # 驗紅 + 驗綠,證據寫進票的 verify.baseline
    scripts/verify-case.py check 7 --candidate t7          # candidate 是分支名
    scripts/verify-case.py check 7 --ref <base_sha> --candidate <merge sha>   # 落地後補量
    scripts/verify-case.py extract 7          # 只抽驗證檔,出 patch-verify.diff
    scripts/verify-case.py tags-merge         # 把 verify/TAGS.d/*.md 合進 verify/TAGS.md

`--ref` 沒給時是**這張票的 `base_sha`**,不是主線的頭:票落地之後主線上已經有那份
實作,對主線量出來的「一條都沒紅」說的是「這一趟量錯了地方」,不是「這條案例是假的」
(#19)。`--ref` / `--candidate` 三種寫法都解得開:worktree 路徑、分支名、sha(#20)。

## 為什麼要有這一支
範本只要求驗證者「兩份 Ran/OK 各貼一份」,而**一份貼上來的輸出沒有辦法被機器比對**:
票的 `verify` 只有 files / tags / run / notes,沒有一格說得出「乾淨主線上真的紅過」。
於是一條永遠綠的斷言,與一條真的在驗的斷言,在票面上長得一模一樣(2026-09-21 外部審查)。

## import 失敗不算紅
乾淨主線上沒有那個新函式,案例 `import` 就會炸 —— 那是**還沒接上**,不是**驗到了**。
兩者都讓 unittest 回非零,所以這裡把它們分開數,import 失敗一律明列,並且讓 `ok` 是
False:要嘛把案例寫成不依賴新符號,要嘛在票裡寫明為什麼這條驗收不適用 baseline 紅。

## 紅的四種形狀(D-020)
`red` 與 `check` 共用同一張分類表(`docs/DESIGN-VERIFY-CASES.md` §三)。**看起來紅、
其實是還沒接上**的紅有三種,而它們與「驗到了」一樣讓 unittest 回非零:

| traceback 最後一個 frame 與例外型別 | 算不算 | 印什麼 |
|---|---|---|
| `AssertionError`(含 `self.fail`),frame 在 `verify.files` 之一 | **算** | `紅 <案例>: <第一行>`,並寫進 `baseline.red_lines` |
| `ImportError` / `ModuleNotFoundError` / `_FailedTest` | 不算 | `import 失敗(不算紅)` |
| 其他例外(`AttributeError` / `NameError` / `FileNotFoundError` / `TypeError` …),frame 在案例檔 | 不算 | `紅在缺符號(不算紅):改成先 assert 它存在` |
| frame 不在案例檔(產品碼或既有測試炸了) | 不算 | `紅在別處(不算紅)` |

問的是 **traceback 本體**的最後一個 frame,不是輸出裡最後一個長得像 frame 的東西:
案例用 `subprocess` 跑工具、把工具的輸出當 `assertEqual` 的訊息時,那份輸出裡的整段
traceback 跟在例外那一行後面 —— 照字面數會指到工具裡的檔,一條算數的紅就成了「紅在
別處」(#31)。`unittest` 印在 `FAIL:` 標頭下面那一行是方法 docstring,也不是 traceback。

`skip` 另外數,兩邊都不歸。**三類不算的紅任一出現就 rc=1 而且不寫票** —— 同 #22
「量不到不動票」:「驗紅沒過」與「這一趟還沒接上」在票面上長得一樣,而下一步差很多。

## 為什麼綠不是驗證者的事(D-020)
驗證者在時間上拿不到實作者的 patch,於是「證明案例做得到綠」只剩一條路:自己搭一份
拋棄式參考實作 —— 那是 #23 那 340K 的來源。所以同一格 `verify.baseline` 分兩段升級:
`red`(驗證者,`stage="red"`)只證乾淨基底該紅;`check`(閘門,`stage="check"`)在
實作者的 patch 進來時才證候選該綠。`ticket.py close` 只認 `stage=="check"`。

## lint 的十二條(F1–F12)
`docs/DESIGN-VERIFY-CASES.md` §四那張表的可執行副本:F1 模組 docstring 的四段、
F2 `TAGS` 字面且登記過、F3 每個 `test_` 的 docstring 首行是驗收編號(**印出來不擋**)、
F4 禁字(`board/config.json` 的 `verify_lint.forbid`,預設 kill 家族)、F5 拋棄式目錄
綁 `addCleanup`、F6 模組頂層不 import 票面的新符號(`--ticket` 沒給時走檔名慣例
`test_ticket_<n>.py`;那張票也讀不到才是「拿不到票」,F6 不查)。F3 之外任一條命中 rc=1 並**指名
行號** —— 一句「格式不合」要人自己去找是哪一行,那一份退件與沒有退件一樣貴。

F7–F12 是脆弱案例的形狀(#102):F7 碰了專案沒宣告的介面或底線名字、F8 釘整數或 sha
(`# pin:<理由>` 放行)、F9 時間取兩次或在 import 時取、F10 sleep 完就正向斷言、F11 等的
訊號與斷言的字串對不上、F12 對原始碼字串 `.index(`。**預設全部只印**:擋哪幾條由
`verify_lint.block` 決定(只收 F7 / F8 / F9),既有違規走 `verify_lint.allow`
(`lint --baseline` 產生)—— 一上線就擋住所有既有案例,等於逼每一張票先去修別人的檔。

## 為什麼登記走 `verify/TAGS.d/<票號>.md`
所有票都往 `verify/TAGS.md` 的尾巴附加 = 每張票都要等前一張落地(D-012 認過 TAGS 是
常見衝突點)。一票一個片段檔就不會撞;`tags-merge` 把它們折進 TAGS.md,序列化的只剩
那一步,而不是整張票。`scripts/verify.py` 兩邊都認,所以片段還沒折進去也不會紅。
"""

import argparse
import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event    # noqa: E402
import status   # noqa: E402
import ticket as ticketlib  # noqa: E402
import verify as verifylib  # noqa: E402

TAGS_REL = os.path.join("verify", "TAGS.md")
TAGS_DIR_REL = os.path.join("verify", "TAGS.d")
TAG_LINE = re.compile(r"^- `([a-z0-9-]+)`\s*(.*)$")
RAN = re.compile(r"^Ran (\d+) test")
SKIPPED = re.compile(r"skipped=(\d+)")
IMPORT_MARKS = ("ImportError", "ModuleNotFoundError", "_FailedTest",
                "cannot import name", "No module named")
# 「紅在缺符號」那一類最常見的四個名字。**分類不靠這份名單**:frame 在案例檔而例外
# 不是 `AssertionError` 就一律算這一類 —— 名單漏一個型別的那一刻,那條紅會被算成
# 「驗到了」,而這張表擋的就是那件事。名單只進訊息,讓人看得出在說哪一族。
MISSING_SYMBOL_EXCS = ("AttributeError", "NameError", "FileNotFoundError", "TypeError")
# traceback 最後一個 frame 之後那一行 `SomeError: 訊息`:行首不縮排、名字後面直接是
# 冒號或行尾。`Traceback (most recent call last):` 與 `During handling …` 進不來
# (名字後面不是冒號),`assertEqual` 的 diff 也進不來(以 `- ` / `+ ` 開頭)。
EXC_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)(?::\s?(.*))?$")
# 例外那一行之後還有 traceback 的**唯一**合法理由:鏈起來的例外。認的是這兩句標記,
# 不是「又出現一段 `Traceback (most recent call last):`」—— 案例把工具的輸出當
# `assertEqual` 的訊息時,訊息裡貼著的那一份也長那樣(#31)。
CHAIN_MARKS = ("During handling of the above exception",
               "The above exception was the direct cause")
# 鏈的標記是 unittest 緊接著印的:例外那一行、空行、標記、空行。往下看三行就夠 ——
# 再遠就會掃進訊息本文,而那正是這一條要擋的東西。
CHAIN_LOOKAHEAD = 3
# lint F1:模組 docstring 固定這四段(`docs/DESIGN-VERIFY-CASES.md` §四)。
DOC_SECTIONS = ("## 驗收表", "## 介面字串", "## 怎麼做假", "## 不做")
# lint F3:`A3`、`D1-2`、`13` 都算。**不加 `\b`**:docstring 首行是「A1 讀端…」這種
# 中文,而 CJK 在 Python 的 `\w` 裡面 —— 加了 `\b` 之後「A1讀端」(少一個空白)會被
# 報成沒編號。設計文件 §四寫的是 `\b`,票面沒有;這裡照票面,理由寫在這一行。
CASE_NUMBER = re.compile(r"^[A-Z]{0,2}[0-9]+(-[0-9]+)?")
# lint F4:禁字的預設是 kill 家族。埠與專案特有的字走 `board/config.json` 的
# `verify_lint.forbid`(T 自備五個埠)—— 專案特有的東西不寫進這一份(§9)。
DEFAULT_FORBID = (r"os\.kill", r"killpg", r"pkill", r"kill -")
# lint F5:拋棄式目錄只有這兩種做法。
TEMP_CALLS = ("mkdtemp", "TemporaryDirectory")
# lint F6:票面 `verify_strings` 裡的 `def <name>(` 就是這張票才會有的符號。
DEF_NAME = re.compile(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# lint F6:`--ticket` 沒給時,案例檔名 `test_ticket_<n>.py` 就是那張票。
CASE_FILE_TICKET = re.compile(r"^test_ticket_([0-9]+)\.py$")
# lint F7–F12:可擋的只有前三條;後三條是時序形狀,誤報率未知,只印(#102)。
BLOCKABLE = ("F7", "F8", "F9")
PRINT_ONLY = ("F10", "F11", "F12")
# lint F8:同一函式(或模組層)提到這幾個字,一串十六進位字面才算 sha。
SHA_LITERAL = re.compile(r"[0-9a-f]{7,40}")
SHA_CONTEXT = re.compile(r"git show|sha256|hexdigest")
PIN = re.compile(r"#\s*pin:(.*)$")
TIMEOUT = 900


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def module_of(rel):
    return rel[:-3].replace(os.sep, ".").replace("/", ".")


def parse_reds(log_path):
    """一份 log → 一份紅榜:就是 `status.parse_failures`,**不再自己先刪說明行**。

    `unittest` 在 `FAIL:` 標頭下印的方法 docstring 第一行,#31 在這裡餵進去之前拿掉;
    #34 把那一步搬回 `status.parse_failures` 本身 —— 一份紅榜只該有一個解析器,兩份會
    各自往不同方向漂,而閘門寫的 `status.json` 當時就是沒修到的那一份(G16)。
    """
    return status.parse_failures(log_path)


def traceback_end(body):
    """一段 traceback 文字 → `(最後一個 frame 的 index, 例外那一行的 index)`,沒有就 -1。

    「最後一個 frame」指的是 **traceback 本體**的最後一個,不是這段文字裡最後一個長得
    像 frame 的東西:案例用 `subprocess` 跑工具、再把工具的輸出當 `assertEqual` 的訊息
    時,那份輸出裡的整段 traceback 會跟在例外那一行**後面**。照字面取最後一個,指到的
    是工具裡的檔,於是一條紅在案例檔自己斷言的紅被算成「紅在別處」(#31)。

    鏈起來的例外要跟到最後一段(算數的是最後被丟出來的那一個),而**鏈是有標記的**:
    看到 `CHAIN_MARKS` 才往下一段數,沒看到就停在這裡。
    """
    frame, out, index = -1, (-1, -1), 0
    while index < len(body):
        line = body[index]
        if status.FILE_LINE.match(line):
            frame = index
        elif frame >= 0 and line.strip() and EXC_LINE.match(line):
            out = (frame, index)
            window = body[index + 1:index + 1 + CHAIN_LOOKAHEAD]
            if not any(mark in text for text in window for mark in CHAIN_MARKS):
                return out
            frame = -1
        index += 1
    return out


def red_shape(row):
    """一筆紅 → `(例外型別, 紅訊息的第一行, 最後一個 frame 的檔路徑)`。

    型別與 frame 讀的是**同一段** traceback 本體(`traceback_end`):兩邊各讀各的那
    一刻,「型別是 AssertionError」與「frame 在案例檔」會說的是兩個不同的例外。認不出
    型別時第一行退回「最後一行非空白」—— 空字串會讓 `red_lines` 只剩案例名,而主線
    覆核時要看的就是那句話。
    """
    body = (row.get("excerpt") or "").splitlines()
    frame, exc_at = traceback_end(body)
    where = ""
    if frame >= 0:
        spot = status.FILE_LINE.match(body[frame])
        where = spot.group(1) if spot else ""
    if exc_at >= 0:
        line = body[exc_at].strip()
        return EXC_LINE.match(body[exc_at]).group(1).rsplit(".", 1)[-1], line, where
    tail = [line for line in body if line.strip()]
    return "", tail[-1].strip() if tail else "", where


def frame_in(where, rels):
    """traceback 裡的檔路徑是絕對的(副本在 tempdir 裡),票面的是 repo 相對路徑 ——
    直接比會**一條都對不上**,而「一條都在別處」與「一條都沒紅」的下一步不一樣。"""
    got = str(where or "").replace(os.sep, "/")
    for rel in rels:
        want = str(rel).replace(os.sep, "/")
        if got == want or got.endswith("/" + want):
            return True
    return False


def shapes(rows, rels):
    """一份紅榜 → 四種形狀。**不准揉成一句「N 條紅」**:揉起來的那一刻,「驗到了」與
    「還沒接上」長得一樣(`docs/DESIGN-VERIFY-CASES.md` §三)。"""
    imports = [row for row in rows
               if any(mark in (row["case"] + row["excerpt"]) for mark in IMPORT_MARKS)]
    out = {"red": [], "red_lines": [], "import_failures": [], "missing_symbol": [],
           "elsewhere": []}
    for row in rows:
        if row in imports:
            out["import_failures"].append(
                "%s: %s" % (row["case"], row["excerpt"].splitlines()[-1]
                            if row["excerpt"] else ""))
            continue
        exc, first, where = red_shape(row)
        label = "%s: %s" % (row["case"], first)
        if not frame_in(where, rels):
            out["elsewhere"].append(label)
        elif row["kind"] == "FAIL" or exc.endswith("AssertionError"):
            # unittest 只把 `failureException`(= `AssertionError`,`self.fail` 也是它)
            # 報成 `FAIL:`,其他例外一律 `ERROR:` —— 所以這一格問的就是票面那一句
            # 「例外是 AssertionError」,而不是一份型別名單。
            out["red"].append(row["case"])
            out["red_lines"].append(label)
        else:
            out["missing_symbol"].append(label)
    return out


def print_shapes(run, prefix="    "):
    for line in run["red_lines"]:
        sys.stdout.write("%s紅 %s\n" % (prefix, line))
    for line in run["import_failures"]:
        sys.stdout.write("%simport 失敗(不算紅) %s\n" % (prefix, line))
    for line in run["missing_symbol"]:
        sys.stdout.write("%s紅在缺符號(不算紅):改成先 assert 它存在 —— %s\n"
                         % (prefix, line))
    for line in run["elsewhere"]:
        sys.stdout.write("%s紅在別處(不算紅) %s\n" % (prefix, line))


def not_counted(run, where):
    """三類不算的紅,一類一句。**列得出來才擋得住** —— 一句「驗紅沒過」給不出下一步。"""
    out = []
    if run["import_failures"]:
        out.append("%s上有 %d 個 import 失敗 —— 那是還沒接上,不是驗到了"
                   % (where, len(run["import_failures"])))
    if run["missing_symbol"]:
        out.append("%s上有 %d 條紅在缺符號(%s 那一族)—— 改成先 assert 它存在"
                   % (where, len(run["missing_symbol"]), "/".join(MISSING_SYMBOL_EXCS)))
    if run["elsewhere"]:
        out.append("%s上有 %d 條紅在別處(產品碼或既有測試炸了,不是這幾條案例在說話)"
                   % (where, len(run["elsewhere"])))
    return out


def run_cases(where, rels, log_path):
    """在 `where` 這份副本裡跑這幾個案例檔,回傳一份可以比對的結果。

    **原始輸出存檔**,不只回傳摘要:少了原文,下一個人要重現這一趟只能重跑一次。
    """
    mods = [module_of(rel) for rel in rels]
    done = subprocess.run([sys.executable, "-m", "unittest", "-v", *mods],
                          cwd=where, capture_output=True, text=True, timeout=TIMEOUT)
    text = (done.stdout or "") + (done.stderr or "")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    out = shapes(parse_reds(log_path), rels)
    count, skipped = 0, 0
    for line in text.splitlines():
        hit = RAN.match(line)
        if hit:
            count = int(hit.group(1))
        hit = SKIPPED.search(line)
        if hit:
            skipped = int(hit.group(1))
    out.update({"rc": done.returncode, "cases": count, "skipped": skipped,
                "log": log_path})
    return out


def clean_copy(root, ref, where):
    """乾淨主線的拋棄式副本。`git archive` 而不是 worktree:worktree 會在主 repo 裡
    留一筆登記,而這一趟是一次性的,不該讓別人的 `worktree list` 多一行殘骸。"""
    # tar 是二進位:`text=True` 讀回來的那一份解不開(而解不開的錯訊息會被當成
    # 「這個 ref 不存在」)。所以這一趟不共用上面那支 `git()`。
    archive = subprocess.run(["git", "archive", "--format=tar", ref],
                             cwd=root, capture_output=True, timeout=300)
    if archive.returncode != 0:
        return False
    os.makedirs(where, exist_ok=True)
    done = subprocess.run(["tar", "-x", "-C", where], input=archive.stdout,
                          capture_output=True, timeout=300)
    return done.returncode == 0


def discard_scratch_trees(where):
    """`red` / `check` 跑完就砍 `base` / `candidate` 這兩份 `clean_copy` 出來的拋棄式
    樹,`logs` 留著。

    `gate.sh` 的 `--out-dir` 指的是 worktree 內的 `gate.log.verify-case.d`(不是系統
    tempdir),而這兩份副本從來沒被清掉過:同一個 worktree 第二次跑閘門時,全樹掃描
    的守衛(`git ls-files` 以外的那幾支)會把上一次留下的 `base/` 當成工作樹裡真的檔
    案掃到而紅(#33)。"""
    for name in ("base", "candidate"):
        shutil.rmtree(os.path.join(where, name), ignore_errors=True)


def overlay(rels, src, dst):
    """同一份案例覆加到副本上。**兩邊跑的一定要是同一份檔**,不然比的是兩份案例。"""
    missing = []
    for rel in rels:
        source = os.path.join(src, rel)
        if not os.path.exists(source):
            missing.append(rel)
            continue
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(source, target)
        # 沒有 `__init__.py` 的目錄餵不回 `python3 -m unittest <dotted>`。
        walk = os.path.dirname(rel)
        while walk:
            stub = os.path.join(dst, walk, "__init__.py")
            if not os.path.exists(stub):
                open(stub, "a", encoding="utf-8").close()
            walk = os.path.dirname(walk)
    return missing


def resolve_tree(root, spec, where, label):
    """把 `--ref` / `--candidate` 這一格解成「一棵可以跑測試的樹」+ 它的 sha。

    三種寫法都要解得開:**worktree 路徑、分支名、sha**。舊版一律當路徑用,於是
    `--candidate t20` 去找 `$PWD/t20`,印出「candidate 裡找不到這幾個案例檔」——
    而檔就在 t20 上,只是沒有人去 git 裡拿(#20)。

    順序是**路徑先於 ref**:既有的叫法一律傳路徑,反過來會讓一個剛好與分支同名的
    目錄被解成 ref,而那一趟跑的樹與呼叫者指的不是同一棵。

    回傳 `(樹的路徑, sha, 解不開的理由)`;解不開時前兩格不保證有值。
    """
    if not spec:
        return root, git(["rev-parse", "HEAD"], root).stdout.strip(), ""
    if os.path.isdir(spec):
        path = os.path.abspath(spec)
        return path, git(["rev-parse", "HEAD"], path).stdout.strip(), ""
    seen = git(["rev-parse", "--verify", "--quiet", "%s^{commit}" % spec], root)
    if seen.returncode != 0 or not seen.stdout.strip():
        return None, "", "%s「%s」既不是一個目錄,也不是這個 repo 裡的 ref" % (label, spec)
    sha = seen.stdout.strip()
    if not clean_copy(root, sha, where):
        return None, sha, "做不出 %s(%s)的乾淨副本" % (spec, sha[:12])
    return where, sha, ""


def main_repo_root(root):
    """主 repo 根:`git rev-parse --git-common-dir` 的上一層(`wtbase.sh` 的 `main_root`
    同一條)。在票的 worktree 裡它指回主 repo;不在 git 裡就是 `root`。"""
    common = git(["rev-parse", "--git-common-dir"], root).stdout.strip()
    if not common:
        return root
    return os.path.dirname(os.path.join(root, common).rstrip(os.sep))


def portable(record, root, candidate_spec):
    """寫進票 / 證據檔之前,把三格路徑改成相對主 repo 根(#87 A2):`candidate`、
    `baseline.log`、`candidate_run.log`。票檔進 git,家目錄的絕對路徑不該跟著進去;
    worktree 在 `../<repo>-wt/t<n>` 時就是 `../` 開頭。`--candidate` 給的是分支名 / sha
    就照原字串 —— 那不是路徑。"""
    main = os.path.realpath(main_repo_root(root))

    def rel(path):
        return os.path.relpath(os.path.realpath(path), main)

    out = dict(record)
    if not candidate_spec or os.path.isdir(candidate_spec):
        out["candidate"] = rel(candidate_spec or root)
    for key in ("baseline", "candidate_run"):
        run = out.get(key)
        if isinstance(run, dict) and run.get("log"):
            out[key] = dict(run, log=rel(run["log"]))
    return out


def unmeasured(ident, why, ref, verb="check"):
    """**量不到就不要在票上留一個長得像判決的紀錄。**

    #19 落地之後 `check` 在主線上量不到紅,把 `ok: false` 蓋回票的 `verify.baseline`,
    `ticket.py close` 從此擋著那張票 —— 而那一格說的其實是「這一趟沒量到」,不是
    「這條案例驗不到東西」,兩者的下一步差很多。所以量不到的路徑**不寫票**,改印
    下一步該敲什麼。
    """
    sys.stderr.write("verify-case: #%s 量不到基準,票沒有動 —— %s\n" % (ident, why))
    sys.stderr.write("  下一步:scripts/verify-case.py %s %s --ref <票的 base_sha> "
                     "--candidate <分支名 / sha / worktree 路徑>"
                     "(這一趟用的 ref 是 %s)\n" % (verb, ident, ref))
    return 2


def plan_of(ident):
    """票的 `verify` 那一格 + 案例檔清單;`(data, plan, rels, rc)`,rc 非 None 就回它。"""
    try:
        data = ticketlib.load(ident)
    except (OSError, ValueError) as exc:
        sys.stderr.write("verify-case: 讀不到票 #%s —— %s\n" % (ident, exc))
        return None, {}, [], 2
    plan = data.get("verify") if isinstance(data.get("verify"), dict) else {}
    rels = list(plan.get("files") or [])
    if not rels:
        sys.stderr.write("verify-case: 票 #%s 的 verify.files 是空的 —— "
                         "驗證者還沒交案例,沒有東西可以驗紅\n" % ident)
        return data, plan, rels, 3
    return data, plan, rels, None


def clean_base(args, rels, ref, where, verb):
    """`red` 與 `check` 共用的前置:解 candidate、解 ref、把案例疊到乾淨基底上。

    回傳 `(candidate, candidate sha, base 樹, base sha, rc)`;rc 非 None 就是**這一趟
    量不到**,呼叫者直接回它 —— 量不到的路徑一律不寫票(`unmeasured`)。
    """
    root = event.repo_root()
    candidate, cand_sha, why = resolve_tree(
        root, args.candidate, os.path.join(where, "candidate"), "candidate")
    if why:
        return None, "", None, "", unmeasured(args.ticket, why, ref, verb)
    base_dir, base_sha, why = resolve_tree(
        root, ref, os.path.join(where, "base"), "ref")
    if why:
        return None, "", None, "", unmeasured(args.ticket, why, ref, verb)
    # ref 的歷史裡已經有 candidate = 那棵樹上**本來就有這份實作**,再怎麼跑也紅不
    # 起來。舊版把那一趟的「一條都沒紅」當判決蓋進票,`ticket.py close` 從此擋著那
    # 張票(#19)。兩個 sha 一樣時不算 —— 那是「candidate 是同一棵樹上未 commit 的
    # 改動」,驗證者交件時的正常形狀。
    if cand_sha and base_sha and cand_sha != base_sha and git(
            ["merge-base", "--is-ancestor", cand_sha, base_sha], root).returncode == 0:
        return None, "", None, "", unmeasured(
            args.ticket,
            "ref(%s / %s)的歷史裡已經有 candidate(%s / %s)——"
            "那棵樹上本來就有這份實作,量不出紅"
            % (ref, base_sha[:12], args.candidate or root, cand_sha[:12]), ref, verb)
    # ref 那棵樹上本來就不會有這幾個案例檔(案例是這張票才加的),所以一律把
    # candidate 的那一份疊上去 —— **兩邊跑的一定要是同一份檔**。
    missing = overlay(rels, candidate, base_dir)
    if missing:
        return None, "", None, "", unmeasured(
            args.ticket, "candidate(%s)裡找不到這幾個案例檔:%s"
            % (args.candidate or root, ", ".join(missing)), ref, verb)
    return candidate, cand_sha, base_dir, base_sha, None


def write_baseline(ident, record, to):
    """證據寫回票的 `verify.baseline`(同一格,`stage` 是它的升級)。**只有 `check`
    叫它**;`red` 交的是證據檔(`write_red_record`),不碰票(#36)。"""
    try:
        ticketlib.save_baseline(ident, record, to)
    except (OSError, ValueError, RuntimeError) as exc:
        sys.stderr.write("verify-case: 證據寫不回票 #%s —— %s\n" % (ident, exc))
        return 2
    return 0


def write_red_record(ident, record, where):
    """`red` 的證據:`<out-dir>/baseline-red.json`,**不寫票**(#36,FLOW G13)。

    驗證者是不准 git 寫入的角色;以前 `red` 走 `ticketlib.save` 改活 repo 的票並
    `state_version+1`,主線的覆核立刻過期,而票檔在 git 裡多一個沒人 commit 的改動。
    這一份由驗證者抄進 EVIDENCE 的 `result` 區塊(`baseline` 那一格),`apply.sh
    --evidence-verifier` 在鎖裡把它併進票的 `verify.baseline`。
    """
    path = os.path.join(where, "baseline-red.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        sys.stderr.write("verify-case: 驗紅的證據寫不出來 #%s —— %s\n" % (ident, exc))
        return None
    return path


def cmd_red(args):
    """**只驗紅**:乾淨基底上套案例、只跑一次,四種形狀分類(D-020 C2/C3)。

    綠不在這裡量 —— 驗證者在時間上拿不到實作者的 patch,要它證綠就等於要它自己搭一份
    參考實作(#23 那 340K)。過了交一份 `stage="red"` 的證據檔(不寫票,#36);
    `apply.sh --evidence-verifier` 把它併進票,閘門之後用 `check` 把同一格升成
    `check`,`ticket.py close` 只認那一趟。
    """
    root = event.repo_root()
    data, plan, rels, rc = plan_of(args.ticket)
    if rc is not None:
        return rc
    ref = args.ref or data.get("base_sha") or ticketlib.main_branch()
    where = args.out_dir or tempfile.mkdtemp(prefix="verify-case-red-")
    logs = os.path.join(where, "logs")
    os.makedirs(logs, exist_ok=True)
    _, cand_sha, base_dir, base_sha, rc = clean_base(args, rels, ref, where, "red")
    if rc is not None:
        return rc

    try:
        run = run_cases(base_dir, rels, os.path.join(logs, "baseline.log"))
        sys.stdout.write("verify-case: #%s 案例 %d 個(乾淨基底 %s / %s)\n"
                         % (args.ticket, run["cases"], ref, base_sha[:12] or "無 sha"))
        print_shapes(run)
        sys.stdout.write("  算數的紅 %d、skip %d、不算的紅 %d -> %s\n"
                         % (len(run["red"]), run["skipped"],
                            len(run["import_failures"]) + len(run["missing_symbol"])
                            + len(run["elsewhere"]), run["log"]))

        why = not_counted(run, "乾淨基底")
        if not run["cases"]:
            why.append("乾淨基底上一個案例都沒跑到 —— 零個案例與「都過了」長得一樣")
        elif not run["red"]:
            why.append("乾淨基底上一條算數的紅都沒有 —— 一個永遠綠的案例與一個真的在驗的"
                       "案例長得一樣")
        if why:
            # **量不到的路徑不寫票**(#22):一句蓋進票的 `ok:false` 會讓 `close` 從此
            # 擋著那張票,而那一格說的其實是「這一趟還沒接上」。
            sys.stdout.write("verify-case: #%s 驗紅**不成立**(票沒有動):%s\n"
                             % (args.ticket, ";".join(why)))
            return 1
        record = {
            "stage": "red",
            "ok": True,
            "why": "",
            "at": now(),
            "files": rels,
            "base_ref": ref,
            "base_sha": base_sha,
            "candidate": args.candidate or root,
            "candidate_sha": cand_sha,
            "baseline": run,
            "candidate_run": None,
        }
        path = write_red_record(args.ticket, portable(record, root, args.candidate), where)
        if not path:
            return 2
        sys.stdout.write("verify-case: #%s 驗紅成立(stage=red,票沒有動;綠由閘門的 check 量)\n"
                         "  證據 -> %s(抄進 EVIDENCE 的 result 區塊 `baseline`)\n"
                         % (args.ticket, path))
        return 0
    finally:
        # `base` 是拋棄式的乾淨副本,用完就砍 —— 不留在 worktree 裡讓下一次全樹掃描
        # 的守衛掃到(#33)。`logs` 留著。
        discard_scratch_trees(where)


def cmd_check(args):
    root = event.repo_root()
    data, plan, rels, rc = plan_of(args.ticket)
    if rc is not None:
        return rc
    # 預設的 ref 是**票的 base_sha**:對主線量,票一落地就量不到紅了(#19)。
    ref = args.ref or data.get("base_sha") or ticketlib.main_branch()
    where = args.out_dir or tempfile.mkdtemp(prefix="verify-case-")
    logs = os.path.join(where, "logs")
    os.makedirs(logs, exist_ok=True)
    candidate, cand_sha, base_dir, base_sha, rc = clean_base(
        args, rels, ref, where, "check")
    if rc is not None:
        return rc

    try:
        baseline = run_cases(base_dir, rels, os.path.join(logs, "baseline.log"))
        cand = run_cases(candidate, rels, os.path.join(logs, "candidate.log"))

        why = not_counted(baseline, "乾淨主線")
        if not baseline["red"]:
            why.append("乾淨主線上一條都沒紅 —— 一個永遠綠的案例與一個真的在驗的案例長得一樣")
        # candidate 這一邊**四種形狀都算紅**:一條 `AttributeError` 說的是實作還沒接上,
        # 而「不算紅」那張表問的是「基底紅得對不對」,不是「候選綠不綠」。
        cand_bad = (len(cand["red"]) + len(cand["import_failures"])
                    + len(cand["missing_symbol"]) + len(cand["elsewhere"]))
        if cand_bad:
            why.append("candidate 上還有 %d 條紅(其中 import 失敗 %d、缺符號 %d、別處 %d)"
                       % (cand_bad, len(cand["import_failures"]),
                          len(cand["missing_symbol"]), len(cand["elsewhere"])))
        if baseline["cases"] != cand["cases"]:
            why.append("兩邊跑到的案例數不一樣(%d vs %d)—— 比的不是同一組"
                       % (baseline["cases"], cand["cases"]))
        ok = not why

        record = {
            # 閘門量的是同一格的**升級**,不是第二格:`red`(驗證者)→ `check`(閘門),
            # 而 `ticket.py close` 只認 `check`(D-020 C5)。兩格會長成兩種形狀(D-018)。
            "stage": "check",
            "ok": ok,
            "why": ";".join(why),
            "at": now(),
            "files": rels,
            "base_ref": ref,
            "base_sha": base_sha,
            "candidate": args.candidate or root,
            "candidate_sha": cand_sha,
            "baseline": baseline,
            "candidate_run": cand,
        }
        rc = write_baseline(args.ticket, portable(record, root, args.candidate),
                            "ok" if ok else "紅不起來")
        if rc:
            return rc

        sys.stdout.write("verify-case: #%s 案例 %d 個\n" % (args.ticket, baseline["cases"]))
        sys.stdout.write("  乾淨主線 %s(%s):紅 %d、skip %d、import 失敗 %d -> %s\n"
                         % (ref, base_sha[:12], len(baseline["red"]),
                            baseline["skipped"], len(baseline["import_failures"]),
                            baseline["log"]))
        print_shapes(baseline)
        sys.stdout.write("  candidate %s(%s):紅 %d、skip %d -> %s\n"
                         % (args.candidate or root, cand_sha[:12], len(cand["red"]),
                            cand["skipped"], cand["log"]))
        print_shapes(cand)
        if ok:
            sys.stdout.write("verify-case: #%s baseline 成立(證據寫進票的 verify.baseline)\n"
                             % args.ticket)
            return 0
        sys.stdout.write("verify-case: #%s baseline **不成立**:%s\n" % (args.ticket, record["why"]))
        return 1
    finally:
        # `base` / `candidate`(candidate 是分支或 sha 時才有)用完就砍,不留在
        # worktree 裡讓下一次全樹掃描的守衛掃到(#33)。`logs` 留著。
        discard_scratch_trees(where)


def forbidden_patterns(root):
    """F4 的禁字:`board/config.json` 的 `verify_lint.forbid`,沒設就是 kill 家族。

    每一條當**正則**編(埠那一族要寫成 `1890[3-5]` 才一條頂五個);編不起來的退回當
    字面字串 —— 一條編不起來就靜靜不生效的規則,與沒有那條規則長得一樣。
    """
    raw = (event.config(root).get("verify_lint") or {}).get("forbid")
    out = []
    for item in (raw if isinstance(raw, list) and raw else list(DEFAULT_FORBID)):
        text = str(item)
        try:
            out.append((text, re.compile(text)))
        except re.error:
            out.append((text, re.compile(re.escape(text))))
    return out


def new_symbols(ident):
    """F6:票面 `verify_strings` 裡的 `def <name>(` = 這張票才會有的符號名字。"""
    data = ticketlib.load(ident)
    out = set()
    for row in ticketlib.normalise_verify(data.get("verify_strings") or []):
        text = row.get("contains") if isinstance(row, dict) else row
        for hit in DEF_NAME.finditer(str(text or "")):
            out.add(hit.group(1))
    return out


def ticket_of_case(rel):
    """F6 拿票的第二條路:檔名慣例 `test_ticket_<n>.py`(§四的例子就長這樣)。

    `--ticket` 沒給時才走。**讀不到那張票就是拿不到票**,F6 照設計不查 —— 猜出來的
    符號名單會擋掉既有模組。
    """
    hit = CASE_FILE_TICKET.match(os.path.basename(rel))
    if not hit:
        return None
    ident = hit.group(1)
    try:
        ticketlib.load(ident)
    except (OSError, ValueError):
        return None
    return ident


def tags_node(tree):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "TAGS" for target in node.targets):
            return node
    return None


def lint_one(path, rel, text, tree, forbid, known, symbols):
    """一個案例檔的 F1–F6。回傳 `(擋的, 不擋的, 這一檔宣告的驗收編號)`。"""
    bad, notes, numbers = [], [], []

    def say(line_no, what):
        bad.append("%s:%d %s" % (rel, line_no, what))

    doc = ast.get_docstring(tree)
    if not doc:
        say(1, "F1 模組沒有 docstring —— 要有 %s" % "、".join(DOC_SECTIONS))
    else:
        for want in DOC_SECTIONS:
            if want not in doc:
                say(tree.body[0].lineno, "F1 模組 docstring 缺 %s" % want)

    node = tags_node(tree)
    if node is None:
        say(1, "F2 沒有 TAGS(verify.py 只認字面 list)")
    else:
        try:
            tags = verifylib.tags_of(path)
        except (ValueError, AttributeError, TypeError, SyntaxError):
            tags = None
            say(node.lineno, "F2 TAGS 不是字面 list(verify.py 用 ast.literal_eval 讀)")
        unknown = [tag for tag in (tags or []) if tag not in known]
        if unknown:
            say(node.lineno, "F2 標籤未登記 %s —— 一行 `- `<tag>` — 說明(#<票號>)` 寫進 "
                             "verify/TAGS.d/<票號>.md" % unknown)

    lines = text.splitlines()
    for spot in ast.walk(tree):
        if isinstance(spot, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and spot.name.startswith("test_"):
            head = (ast.get_docstring(spot) or "").strip().splitlines()
            hit = CASE_NUMBER.match(head[0] if head else "")
            if hit:
                numbers.append(hit.group(0))
            else:
                # **F3 不擋**:編號對不上票面是主線覆核要看的事,而一條沒編號的案例
                # 仍然在驗東西 —— 擋下去只會讓人為了過 lint 編一個號碼。
                notes.append("%s:%d F3 %s 的 docstring 首行不是驗收編號(不擋)"
                             % (rel, spot.lineno, spot.name))
        if isinstance(spot, ast.Call):
            name = getattr(spot.func, "attr", "") or getattr(spot.func, "id", "")
            if name in TEMP_CALLS:
                end = getattr(spot, "end_lineno", None) or spot.lineno
                window = "\n".join(lines[spot.lineno - 1:end + 1])
                if "addCleanup" not in window:
                    say(spot.lineno, "F5 %s() 同一語句或下一行沒有 addCleanup —— "
                                     "跑完不收的拋棄式目錄會留在磁碟上" % name)

    for index, line in enumerate(lines, 1):
        for raw, pattern in forbid:
            if pattern.search(line):
                say(index, "F4 禁字 %s(派工文鐵律:不碰受保護的埠、不殺不是自己起的"
                           "行程)" % raw)

    for spot in tree.body:
        names = []
        if isinstance(spot, ast.Import):
            names = [alias.name.split(".")[-1] for alias in spot.names]
        elif isinstance(spot, ast.ImportFrom):
            names = [alias.name for alias in spot.names]
        for name in names:
            if name in symbols:
                say(spot.lineno, "F6 模組頂層 import 了票面的新符號 %s —— 乾淨基底上那是"
                                 "import 失敗(不算紅),改成 getattr 再先 assert 它存在"
                                 % name)
    return bad, notes, numbers


def lint_settings(root):
    """`verify_lint` 的 api / block / allow(#102)。回傳 `(api, block, allow, 提示行)`。

    block 寫了 F10–F12 或別的編號都印一行再丟掉 —— 寫了卻靜靜不生效的設定,與沒寫長得一樣。
    """
    raw = event.config(root).get("verify_lint") or {}
    api = raw.get("api") if isinstance(raw.get("api"), dict) else {}
    wanted = [str(rule) for rule in (raw.get("block") or [])] \
        if isinstance(raw.get("block"), list) else []
    allow = {str(item) for item in (raw.get("allow") or [])} \
        if isinstance(raw.get("allow"), list) else set()
    notices = []
    printed = [rule for rule in wanted if rule in PRINT_ONLY]
    if printed:
        notices.append("F10–F12 只印不擋,block 忽略 %s" % printed)
    unknown = [rule for rule in wanted if rule not in BLOCKABLE + PRINT_ONLY]
    if unknown:
        notices.append("verify_lint.block 只收 F7 / F8 / F9,忽略 %s" % unknown)
    lists = {}
    for module, rel in sorted(api.items()):
        lists[module] = verify_api(os.path.join(root, str(rel)))
        if lists[module] is None:
            notices.append("F7 %s 沒宣告 VERIFY_API,名單檢查略過" % module)
    block = {rule for rule in wanted if rule in BLOCKABLE}
    return lists, block, allow, notices


def verify_api(path):
    """F7:專案檔頂層 `VERIFY_API` 的字面 tuple / list of str。**用 ast 讀、不 import**
    —— import 一支專案測試檔會跑它的模組層。檔不在、沒宣告、不是字面都回 None。"""
    try:
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "VERIFY_API" for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                return None
            if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
                return set(value)
            return None
    return None


def dotted(node):
    """`a.b.c` → `"a.b.c"`;不是一串名字就回空字串。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def last_name(node):
    return getattr(node, "attr", "") or getattr(node, "id", "")


def private(name):
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def own_nodes(top, into_classes=True):
    """`top` 底下、不進任何 def / lambda 的節點(`top` 自己是 def 時從它的本體走)。"""
    stack = list(ast.iter_child_nodes(top))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.ClassDef) and not into_classes:
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def is_assert(node):
    """`assert …` 語句或 `<…>.assert*(…)` 呼叫。"""
    return isinstance(node, ast.Assert) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("assert"))


def clock_call(node):
    """F9 的「取時間」:回傳印得出來的名字,不是就回空字串。"""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Name):
        return "iso()" if func.id == "iso" else ""
    if not isinstance(func, ast.Attribute):
        return ""
    owner = last_name(func.value)
    if func.attr == "today" and owner == "date" \
            or func.attr in ("now", "utcnow") and owner == "datetime" \
            or func.attr in ("strftime", "localtime") and dotted(func.value) == "time" \
            or func.attr == "iso":
        return "%s()" % dotted(func) if dotted(func) else "%s()" % func.attr
    return ""


def fragile_f7(tree, lists):
    """F7:(i) 專案模組的名字不在 VERIFY_API;(ii) 繼承專案類別後 `self.<X>()` 的
    `類別.X` 不在 VERIFY_API;(iii) 底線名字不看名單(案例自己定義的除外)。"""
    hits = []
    own = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            own.add(node.name)
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    targets = item.targets if isinstance(item, ast.Assign) else \
                        [item.target] if isinstance(item, ast.AnnAssign) else []
                    own.update(t.id for t in targets if isinstance(t, ast.Name))
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Attribute) and dotted(sub.value) == "self":
                    own.add(sub.attr)

    modules, classes = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in lists:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            for alias in node.names:
                full = "%s.%s" % (source, alias.name) if source else alias.name
                for module in (full, alias.name):
                    if module in lists:
                        modules[alias.asname or alias.name] = module
                        break
                if private(alias.name):
                    if alias.name not in own:
                        hits.append((node.lineno, "F7", "from %s import %s 是底線名字 —— "
                                     "私有實作,不看名單一律命中" % (source, alias.name)))
                elif source in lists and alias.name != "*":
                    classes[alias.asname or alias.name] = (source, alias.name)
                    names = lists[source]
                    if names is not None and alias.name not in names:
                        hits.append((node.lineno, "F7", "from %s import %s 不在 %s 的 "
                                     "VERIFY_API —— 案例只碰專案宣告的介面"
                                     % (source, alias.name, source)))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if private(node.attr):
            if node.attr not in own:
                hits.append((node.lineno, "F7", "%s 是底線名字 —— 私有實作,不看名單"
                             "一律命中" % (dotted(node) or "." + node.attr)))
            continue
        module = modules.get(dotted(node.value))
        if module and lists[module] is not None and node.attr not in lists[module]:
            hits.append((node.lineno, "F7", "%s.%s 不在 %s 的 VERIFY_API —— 案例只碰"
                         "專案宣告的介面" % (dotted(node.value), node.attr, module)))

    local = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    def roots(cls, seen):
        out = []
        for base in cls.bases:
            name = dotted(base)
            head, _, attr = name.rpartition(".")
            if name in classes:
                out.append(classes[name])
            elif head in modules:
                out.append((modules[head], attr))
            elif name in local and name not in seen:
                out.extend(roots(local[name], seen | {name}))
        return out

    for cls in local.values():
        found = [(module, name) for module, name in roots(cls, {cls.name})
                 if lists.get(module) is not None]
        if not found:
            continue
        for node in ast.walk(cls):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and dotted(node.func.value) == "self"):
                continue
            name = node.func.attr
            if private(name) or name in own or hasattr(unittest.TestCase, name):
                continue
            if not any("%s.%s" % (base, name) in lists[module] for module, base in found):
                hits.append((node.lineno, "F7", "self.%s() 不在 VERIFY_API(%s)—— 繼承來"
                             "的方法也要專案宣告過" % (name, " / ".join(
                                 "%s.%s" % (base, name) for _, base in found))))
    return hits


def fragile_f8(tree, lines, parents):
    """F8:`assertEqual(len(…) / <…>.count(…), <整數>)`、`<…>.count(…) == <整數>`、
    sha 字面。語句首行或末行 `# pin:<理由>` 放行。"""
    def integer(node):
        return isinstance(node, ast.Constant) and type(node.value) is int

    def counted(node):
        return isinstance(node, ast.Call) and (
            dotted(node.func) == "len"
            or isinstance(node.func, ast.Attribute) and node.func.attr == "count")

    in_def = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            in_def.update(range(node.lineno, node.end_lineno + 1))
    module_text = "\n".join(line for no, line in enumerate(lines, 1) if no not in in_def)

    def region(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return "\n".join(lines[node.lineno - 1:node.end_lineno])
        return module_text

    hits = []
    for node in ast.walk(tree):
        what = ""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("assertEqual", "assertEquals") and len(node.args) >= 2:
            first, second = node.args[:2]
            if counted(first) and integer(second) or counted(second) and integer(first):
                what = "%s(len(…) / <…>.count(…), 整數) 釘了數量 —— 改成下限或包含" \
                    % node.func.attr
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], ast.Eq):
            pair = (node.left, node.comparators[0])
            if any(isinstance(side, ast.Call) and isinstance(side.func, ast.Attribute)
                   and side.func.attr == "count" for side in pair) \
                    and any(integer(side) for side in pair):
                what = "<…>.count(…) == 整數 釘了次數 —— 改成下限或包含"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and not isinstance(parents.get(node), ast.JoinedStr) \
                and SHA_LITERAL.fullmatch(node.value) and SHA_CONTEXT.search(region(node)):
            what = "sha 字面 %r 釘了某一版的雜湊 —— 改成從同一個來源算出來再比" % node.value
        if not what:
            continue
        stmt = node
        while not isinstance(stmt, ast.stmt) and stmt in parents:
            stmt = parents[stmt]
        last = node.end_lineno if hasattr(stmt, "body") else stmt.end_lineno
        reasons = [hit.group(1).strip() for hit in
                   (PIN.search(lines[no - 1]) for no in {stmt.lineno, last}) if hit]
        if any(reasons):
            continue
        hits.append((node.lineno, "F8", what + (
            "(pin 沒寫理由)" if reasons else ";真的要釘就行尾 `# pin:<理由>`")))
    return hits


def fragile_f9(tree):
    """F9:(i) 同一函式取時間兩次以上且至少一次在斷言裡;(ii) 模組層或 class 本體取時間。"""
    hits = []
    for node in own_nodes(tree):
        label = clock_call(node)
        if label:
            hits.append((node.lineno, "F9", "%s 在模組層 / class 本體 —— import 那一刻就定格,"
                         "跨日跑就紅;搬進 setUp" % label))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls, asserted = 0, []
        stack = [(child, False) for child in ast.iter_child_nodes(fn)]
        while stack:
            node, inside = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            inside = inside or is_assert(node)
            if clock_call(node):
                calls += 1
                if inside:
                    asserted.append(node)
            stack.extend((child, inside) for child in ast.iter_child_nodes(node))
        if calls >= 2 and asserted:
            first = min(asserted, key=lambda n: (n.lineno, n.col_offset))
            hits.append((first.lineno, "F9", "%s() 裡取時間 %d 次、斷言裡又取一次(%s)—— "
                         "跨秒 / 跨日就對不上;取一次存起來再比"
                         % (fn.name, calls, clock_call(first))))
    return hits


def fragile_f10_f12(tree):
    """F10–F12(只印):sleep 完正向斷言、等的訊號與斷言字串對不上、對原始碼 `.index(`。"""
    hits = []
    positive = []
    for node in ast.walk(tree):
        if not is_assert(node):
            continue
        if isinstance(node, ast.Assert):
            negative = isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)
        else:
            negative = node.func.attr.startswith("assertNot") \
                or node.func.attr in ("assertFalse", "assertIsNone")
        before = any("before" in last_name(sub).lower() for sub in ast.walk(node)
                     if isinstance(sub, (ast.Name, ast.Attribute)))
        if not negative and not before:
            positive.append(node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and dotted(node.func) == "time.sleep":
            if any(node.lineno < check.lineno <= node.lineno + 5 for check in positive):
                hits.append((node.lineno, "F10", "time.sleep() 之後 5 行內就正向斷言 —— "
                             "睡夠沒有看機器快慢;改成等一個訊號"))

    def literals(node, at_least):
        return [sub.value for sub in ast.walk(node) if isinstance(sub, ast.Constant)
                and isinstance(sub.value, str) and len(sub.value) >= at_least]

    for block in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(block, field, None)
            if not isinstance(seq, list):
                continue
            for index, stmt in enumerate(seq):
                if hasattr(stmt, "body"):
                    continue
                waits = [sub for sub in ast.walk(stmt) if isinstance(sub, ast.Call)
                         and last_name(sub.func) == "wait_until" and sub.args
                         and isinstance(sub.args[0], ast.Constant)
                         and isinstance(sub.args[0].value, str)]
                if not waits:
                    continue
                signal = waits[0].args[0].value
                check = next((later for later in seq[index + 1:]
                              if not hasattr(later, "body")
                              and any(is_assert(sub) for sub in ast.walk(later))), None)
                if check is None:
                    continue
                if not any(text in signal for text in literals(check, 3)):
                    hits.append((waits[0].lineno, "F11", "wait_until(%r) 等的訊號裡沒有第 %d "
                                 "行斷言的任何一段字串 —— 等到的不是要驗的那件事"
                                 % (signal, check.lineno)))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "index"):
            continue
        receiver = node.func.value
        name = last_name(receiver) if isinstance(receiver, (ast.Name, ast.Attribute)) else ""
        read = isinstance(receiver, ast.Call) and last_name(receiver.func) in ("read", "read_text")
        if name == "src" or name.endswith(("_CODE", "_SRC")) or read:
            hits.append((node.lineno, "F12", "對原始碼字串 .index( —— 釘了位置,改一行"
                         "排版就紅;找不到時還是 ValueError 不是斷言紅"))
    return hits


def fragile(tree, text, lists):
    """一個案例檔的 F7–F12,`[(行, 規則, 說明)]` 依行號排、去重。"""
    lines = text.splitlines()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    hits = fragile_f7(tree, lists) + fragile_f8(tree, lines, parents) \
        + fragile_f9(tree) + fragile_f10_f12(tree)
    return sorted(set(hits), key=lambda hit: (hit[0], int(hit[1][1:]), hit[2]))


def cmd_lint(args):
    """案例格式的機器版(F1–F12)。**指名行號**:一句「格式不合」要人自己找是哪一行,
    那一份退件與沒有退件一樣貴(D-020 C4)。"""
    root = event.repo_root()
    lists, block, allow, notices = lint_settings(root)
    if args.baseline:
        return lint_baseline(root, args.files, lists, notices)
    forbid = forbidden_patterns(root)
    known = verifylib.registered_tags()
    symbols = set()
    if args.ticket:
        try:
            symbols = new_symbols(args.ticket)
        except (OSError, ValueError) as exc:
            sys.stderr.write("verify-case: 讀不到票 #%s —— %s\n" % (args.ticket, exc))
            return 2
    bad, notes, numbers = [], [], []
    for spec in args.files:
        path = spec if os.path.isabs(spec) else os.path.join(root, spec)
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            sys.stderr.write("verify-case lint: 讀不到 %s —— %s\n" % (spec, exc))
            return 2
        try:
            tree = ast.parse(text, path)
        except SyntaxError as exc:
            bad.append("%s:%d 解不開 —— %s" % (rel, exc.lineno or 1, exc.msg))
            continue
        mine = symbols
        if not args.ticket:
            ident = ticket_of_case(rel)
            mine = new_symbols(ident) if ident else set()
        one_bad, one_notes, one_numbers = lint_one(
            path, rel, text, tree, forbid, known, mine)
        bad.extend(one_bad)
        notes.extend(one_notes)
        numbers.extend(one_numbers)
        for line_no, rule, what in fragile(tree, text, lists):
            line = "%s:%d %s %s" % (rel, line_no, rule, what)
            if "%s:%s" % (rel, rule) in allow:
                notes.append(line + "(白名單,不擋)")
            elif rule in block:
                bad.append(line)
            else:
                notes.append(line)
    for line in notices + bad + notes:
        sys.stdout.write("verify-case lint: %s\n" % line)
    sys.stdout.write("verify-case lint: %d 個檔,驗收編號 %s\n"
                     % (len(args.files), " ".join(numbers) or "(一條都沒宣告)"))
    if args.ticket:
        try:
            count = len(ticketlib.load(args.ticket).get("acceptance") or [])
        except (OSError, ValueError):
            count = 0
        sys.stdout.write("verify-case lint: 票 #%s 有 %d 條驗收,案例宣告了 %d 個編號"
                         "(對不上不擋,主線覆核時看)\n"
                         % (args.ticket, count, len(numbers)))
    if bad:
        sys.stdout.write("verify-case lint: **不過**(%d 條)\n" % len(bad))
        return 1
    sys.stdout.write("verify-case lint: 過\n")
    return 0


def lint_baseline(root, files, lists, notices):
    """`lint --baseline`:F7–F12 命中的 `"<路徑>:<規則>"`,排序去重的 JSON 陣列,
    整段貼進 `verify_lint.allow`。stdout 只放那個陣列,其他的走 stderr。"""
    found = set()
    for line in notices:
        sys.stderr.write("verify-case lint: %s\n" % line)
    for spec in files:
        path = spec if os.path.isabs(spec) else os.path.join(root, spec)
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            sys.stderr.write("verify-case lint: 讀不到 %s —— %s\n" % (spec, exc))
            return 2
        try:
            tree = ast.parse(text, path)
        except SyntaxError as exc:
            sys.stderr.write("verify-case lint: %s:%d 解不開 —— %s\n"
                             % (rel, exc.lineno or 1, exc.msg))
            continue
        for _, rule, _ in fragile(tree, text, lists):
            found.add("%s:%s" % (rel, rule))
    sys.stdout.write(json.dumps(sorted(found), ensure_ascii=False) + "\n")
    return 0


def cmd_extract(args):
    """只抽驗證檔出一份 `patch-verify.diff`。

    範本要求驗證者交「只含自己 verify 檔」的 diff,而它的 `work/` 裡已經有實作者的
    patch —— **沒有給差分基準**,手工挑檔遲早挑錯一個(2026-09-21 外部審查)。
    差分基準在這裡說死:乾淨的 `<ref>`。
    """
    root = event.repo_root()
    candidate = os.path.abspath(args.candidate or root)
    try:
        data = ticketlib.load(args.ticket)
    except (OSError, ValueError) as exc:
        sys.stderr.write("verify-case: 讀不到票 #%s —— %s\n" % (args.ticket, exc))
        return 2
    plan = data.get("verify") if isinstance(data.get("verify"), dict) else {}
    rels = list(plan.get("files") or [])
    if not rels:
        sys.stderr.write("verify-case: 票 #%s 的 verify.files 是空的\n" % args.ticket)
        return 3
    ref = args.ref or data.get("base_sha") or ticketlib.main_branch()
    chunks = []
    for rel in rels:
        old = git(["show", "%s:%s" % (ref, rel)], root)
        before = old.stdout.splitlines(keepends=True) if old.returncode == 0 else []
        target = os.path.join(candidate, rel)
        if not os.path.exists(target):
            sys.stderr.write("verify-case: candidate 裡沒有 %s\n" % rel)
            return 2
        with open(target, encoding="utf-8") as handle:
            after = handle.readlines()
        # 檔頭只准 `base/…` / `work/…` 的相對形式(D-012:絕對路徑的檔頭讓檔案被
        # 寫進暫存目錄,套用成功、閘門也綠,而被改的不是 repo 裡那一份)。
        chunks.extend(difflib.unified_diff(
            before, after, fromfile="base/%s" % rel, tofile="work/%s" % rel))
    out = args.out or os.path.join(candidate, "patch-verify.diff")
    with open(out, "w", encoding="utf-8") as handle:
        handle.writelines(chunks)
    sys.stdout.write("verify-case: #%s -> %s(%d 個檔,%d 行)\n"
                     % (args.ticket, out, len(rels), len(chunks)))
    return 0


def read_fragment(path):
    out = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            hit = TAG_LINE.match(line.rstrip("\n"))
            if hit:
                out[hit.group(1)] = hit.group(2).strip()
    return out


def cmd_tags_merge(args):
    root = event.repo_root()
    tags_path = os.path.join(root, TAGS_REL)
    where = os.path.join(root, TAGS_DIR_REL)
    merged = read_fragment(tags_path) if os.path.exists(tags_path) else {}
    clash = []
    names = sorted(os.listdir(where)) if os.path.isdir(where) else []
    for name in names:
        if not name.endswith(".md") or name == "README.md":
            continue
        for tag, note in read_fragment(os.path.join(where, name)).items():
            if tag in merged and merged[tag] and note and merged[tag] != note:
                # 同名不同說明 = 兩張票對同一個標籤的理解不一樣。**靜靜取其中一份**
                # 會讓那個分歧在半年後以「這個 tag 到底在守什麼」的形式回來。
                clash.append("%s:%s 已登記為 %r,片段說的是 %r"
                             % (name, tag, merged[tag], note))
                continue
            merged.setdefault(tag, note)
            if note:
                merged[tag] = note
    if clash:
        sys.stderr.write("verify-case: 標籤撞名而且說明不一樣:\n  %s\n"
                         % "\n  ".join(clash))
        return 2
    lines = ["# 功能標籤登記(開票時登記;verify.py 只認這裡列的)",
             "",
             "一票一個片段檔 `verify/TAGS.d/<票號>.md`,`scripts/verify-case.py tags-merge`",
             "折進這一份 —— 所有票都往同一份檔的尾巴附加會讓每張票排隊等前一張落地(D-012)。",
             ""]
    for tag in sorted(merged):
        note = merged[tag]
        lines.append("- `%s`%s" % (tag, (" " + note) if note else ""))
    if args.dry_run:
        sys.stdout.write("\n".join(lines) + "\n")
        return 0
    with open(tags_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    sys.stdout.write("verify-case: %s 共 %d 個標籤(片段 %d 個)\n"
                     % (TAGS_REL, len(merged), len(names)))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="verify-case.py")
    subs = parser.add_subparsers(dest="verb")

    red = subs.add_parser("red")
    red.add_argument("ticket")
    red.add_argument("--ref", default="")
    red.add_argument("--candidate", default="")
    red.add_argument("--out-dir", default="")
    red.set_defaults(run=cmd_red)

    lint = subs.add_parser("lint")
    lint.add_argument("files", nargs="+")
    lint.add_argument("--ticket", default="")
    lint.add_argument("--baseline", action="store_true",
                      help="只跑 F7–F12,stdout 印排序去重的 [\"<路徑>:<規則>\", …],"
                           "貼進 board/config.json 的 verify_lint.allow")
    lint.set_defaults(run=cmd_lint)

    check = subs.add_parser("check")
    check.add_argument("ticket")
    check.add_argument("--ref", default="")
    check.add_argument("--candidate", default="")
    check.add_argument("--out-dir", default="")
    check.set_defaults(run=cmd_check)

    extract = subs.add_parser("extract")
    extract.add_argument("ticket")
    extract.add_argument("--ref", default="")
    extract.add_argument("--candidate", default="")
    extract.add_argument("--out", default="")
    extract.set_defaults(run=cmd_extract)

    merge = subs.add_parser("tags-merge")
    merge.add_argument("--dry-run", action="store_true")
    merge.set_defaults(run=cmd_tags_merge)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    # `--ref` 的預設不在這裡填:它要看**那張票的 base_sha**,而票是子指令才讀的。
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
