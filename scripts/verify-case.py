#!/usr/bin/env python3
"""驗證產物工具:同一份案例,在乾淨主線該紅、在 candidate 該綠 — D-014。

    scripts/verify-case.py red 7 --candidate <$W/work>     # 只驗紅(驗證者交件,stage=red)
    scripts/verify-case.py lint verify/nav/test_ticket_7.py --ticket 7   # 案例格式 F1–F6
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

## lint 的六條(F1–F6)
`docs/DESIGN-VERIFY-CASES.md` §四那張表的可執行副本:F1 模組 docstring 的四段、
F2 `TAGS` 字面且登記過、F3 每個 `test_` 的 docstring 首行是驗收編號(**印出來不擋**)、
F4 禁字(`board/config.json` 的 `verify_lint.forbid`,預設 kill 家族)、F5 拋棄式目錄
綁 `addCleanup`、F6 模組頂層不 import 票面的新符號(`--ticket` 沒給時走檔名慣例
`test_ticket_<n>.py`;那張票也讀不到才是「拿不到票」,F6 不查)。F3 之外任一條命中 rc=1 並**指名
行號** —— 一句「格式不合」要人自己去找是哪一行,那一份退件與沒有退件一樣貴。

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
        path = write_red_record(args.ticket, record, where)
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
        rc = write_baseline(args.ticket, record, "ok" if ok else "紅不起來")
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


def cmd_lint(args):
    """案例格式的機器版(F1–F6)。**指名行號**:一句「格式不合」要人自己找是哪一行,
    那一份退件與沒有退件一樣貴(D-020 C4)。"""
    root = event.repo_root()
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
    for line in bad + notes:
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
