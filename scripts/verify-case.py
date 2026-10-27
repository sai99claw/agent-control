#!/usr/bin/env python3
"""驗證產物工具:同一份案例,在乾淨主線該紅、在 candidate 該綠 — D-014。

    scripts/verify-case.py red 7 --candidate <$W/work>     # 只驗紅(驗證者交件,stage=red)
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
其實是還沒接上**的紅有三種,而它們與「驗到了」一樣讓回歸命令回非零:

| result.json 那一列(fail / error)| 算不算 | 印什麼 |
|---|---|---|
| `exception` 是 `AssertionError`(或名字以它結尾的子類別),`file` 在 `verify.files` 之一 | **算** | `紅 <id>: <message>`,並寫進 `baseline.red_lines` |
| `exception` 是 `ImportError` / `ModuleNotFoundError`,或 `message` 以 `Failed to import` 起頭 | 不算 | `import 失敗(不算紅)` |
| 其他例外(`AttributeError` / `NameError` / `FileNotFoundError` / `TypeError` …),`file` 在案例檔 | 不算 | `紅在缺符號(不算紅):改成先 assert 它存在` |
| `file` 不在案例檔,或例外是從案例檔那一格**更深處**(產品碼)丟出來的 | 不算 | `紅在別處(不算紅)` |
| `exception` 是 `null` | **量不到** | rc 2、不寫票 —— 分不出是哪一種紅 |

D-071 C7(#163):案例不再由這一支自己 `-m unittest` 跑、再讀 log 的字;每一份副本各叫一次
`scripts/testiface.py regression --case <檔>…`(`AC_CODE_ROOT` = 那一份副本),分類只讀它交回的
`result.json`。log(`logs/<baseline|candidate>.log`)照舊留在 out dir 當證據,但不再是判準。

`file` 是 adapter 記的「最後一個在測試檔裡的 frame」:產品碼在案例那一行底下炸掉時,`file`
仍是案例檔。所以「紅在別處」另外問一次那一列自己的 `traceback`:案例檔那一格後面、例外那一行
之前,還有沒有更深的 frame(`raised_below`)。例外那一行之後的字是訊息 —— 案例把工具的輸出
當 `assertEqual` 的訊息時,那份輸出裡的 traceback 在那裡,不算(#31)。

`skip` 另外數,兩邊都不歸。**三類不算的紅任一出現就 rc=1 而且不寫票** —— 同 #22
「量不到不動票」:「驗紅沒過」與「這一趟還沒接上」在票面上長得一樣,而下一步差很多。

## 為什麼綠不是驗證者的事(D-020)
驗證者在時間上拿不到實作者的 patch,於是「證明案例做得到綠」只剩一條路:自己搭一份
拋棄式參考實作 —— 那是 #23 那 340K 的來源。所以同一格 `verify.baseline` 分兩段升級:
`red`(驗證者,`stage="red"`)只證乾淨基底該紅;`check`(閘門,`stage="check"`)在
實作者的 patch 進來時才證候選該綠。`ticket.py close` 只認 `stage=="check"`。

## 為什麼登記走 `verify/TAGS.d/<票號>.md`
所有票都往 `verify/TAGS.md` 的尾巴附加 = 每張票都要等前一張落地(D-012 認過 TAGS 是
常見衝突點)。一票一個片段檔就不會撞;`tags-merge` 把它們折進 TAGS.md,序列化的只剩
那一步,而不是整張票。`scripts/verify.py` 兩邊都認,所以片段還沒折進去也不會紅。
"""

import argparse
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
IMPORT_EXCS = ("ImportError", "ModuleNotFoundError")
IMPORT_MESSAGE = "Failed to import"
# 「紅在缺符號」那一類最常見的四個名字。**分類不靠這份名單**:`file` 在案例檔而例外
# 不是 `AssertionError` 就一律算這一類 —— 名單漏一個型別的那一刻,那條紅會被算成
# 「驗到了」,而這張表擋的就是那件事。名單只進訊息,讓人看得出在說哪一族。
MISSING_SYMBOL_EXCS = ("AttributeError", "NameError", "FileNotFoundError", "TypeError")
# 一趟案例的時限(秒):`board/config.json` 的 `verify_case.timeout_seconds`,
# 沒設就是這個數(#156)。雲端容器裡整支模組跑不完 900 秒,而寫死的數字只能改碼。
TIMEOUT = 900
TIMEOUT_KEY = "verify_case.timeout_seconds"
# testiface 量不到時一律 rc 2(D-071 C6);逾時那一種它在 stderr 說這一句。
UNMEASURED_RC = 2
TIMEOUT_SAYS = "unmeasured: timeout"


class CaseUnmeasured(Exception):
    """一趟案例**沒量到**:testiface rc 2、result.json 缺或壞、一列 `exception` 是 null(#163)。
    不是紅也不是綠 —— 呼叫者走 `unmeasured()`,不寫票。"""


class CaseTimeout(CaseUnmeasured):
    """一趟案例跑超過時限 —— 那一趟**沒量到**,不是紅也不是綠(#156)。"""


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def code_tree():
    """不帶 `--candidate` 時那棵候選樹:`AC_CODE_ROOT`,否則 cwd 的 `git --show-toplevel`
    (D-071 §1,同 `verify.py`);兩者都沒有才退回專案根。"""
    override = os.environ.get("AC_CODE_ROOT")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    top = git(["rev-parse", "--show-toplevel"], os.getcwd())
    if top.returncode == 0 and top.stdout.strip():
        return top.stdout.strip()
    return event.repo_root()


def code_repo():
    """`--ref` / `--candidate` / 票的 base_sha 去哪一顆 repo 解(#181):程式碼那一顆的主 repo,
    不是 `AC_ROOT`。D-071 起專案根(票、事件)與程式碼 repo 可以不是同一處:驗證者的
    `AC_ROOT` 是一個不是 git 的拋棄式控制根,在那裡解 ref 一律「既不是一個目錄,也不是這個
    repo 裡的 ref」(#125 第 2 輪實測)。依序問 `AC_CODE_ROOT`、cwd、`AC_ROOT`,第一個在 git
    裡的那一顆;都不在 git 裡就照舊是專案根(底下每一支 git 都會說它解不開)。"""
    root = event.repo_root()
    for where in (os.environ.get("AC_CODE_ROOT"), os.getcwd(), root):
        found = event.main_root_of(os.path.abspath(os.path.expanduser(where))) if where else None
        if found:
            return found
    return root


def module_of(rel):
    return rel[:-3].replace(os.sep, ".").replace("/", ".")


def frame_in(where, rels):
    """traceback 裡的檔路徑是絕對的(副本在 tempdir 裡),票面的是 repo 相對路徑 ——
    直接比會**一條都對不上**,而「一條都在別處」與「一條都沒紅」的下一步不一樣。"""
    got = str(where or "").replace(os.sep, "/")
    for rel in rels:
        want = str(rel).replace(os.sep, "/")
        if got == want or got.endswith("/" + want):
            return True
    return False


def raised_below(row):
    """這一列的例外是不是從 `file` 那一格**更深處**丟出來的(產品碼、被叫的函式)。

    讀的是那一列自己的 `traceback`:找 `file` / `line` 那一個 frame 的**最後**一次出現(鏈起來
    的例外,算數的是最後一段),往下看到的第一個不縮排的行是例外那一行 —— 在那之前又出現
    `File "…"` 就是更深的 frame。例外那一行之後是訊息,不看(#31)。沒有 `traceback` 時
    答不出來,照 `file` 說的算(那一格本來就是命令說「紅在這個檔」)。
    """
    text = row.get("traceback")
    if not isinstance(text, str) or not row.get("file"):
        return False
    lines = text.splitlines()
    at = -1
    for index, line in enumerate(lines):
        spot = status.FILE_LINE.match(line)
        if spot and frame_in(spot.group(1), [row["file"]]) \
                and str(spot.group(2)) == str(row.get("line")):
            at = index
    if at < 0:
        return False
    for line in lines[at + 1:]:
        if status.FILE_LINE.match(line):
            return True
        if line.strip() and not line[:1].isspace():
            return False
    return False


def shapes(rows, rels):
    """result.json 的 ids → 四種形狀 + skip 數。**不准揉成一句「N 條紅」**:揉起來的那一刻,
    「驗到了」與「還沒接上」長得一樣(`docs/DESIGN-VERIFY-CASES.md` §三)。

    fail / error 一列的 `exception` 是 null → `CaseUnmeasured`:那一列說不出是哪一種紅,
    猜成任何一類都是一個長得像判決的猜測(§5.5「拿不到就當空的」)。
    """
    out = {"red": [], "red_lines": [], "import_failures": [], "missing_symbol": [],
           "elsewhere": [], "skipped": 0}
    for row in rows:
        state = row.get("status")
        if state == "skip":
            out["skipped"] += 1
            continue
        if state not in ("fail", "error"):
            continue
        exc = row.get("exception")
        label = "%s: %s" % (row["id"], row.get("message") or "")
        if not exc:
            raise CaseUnmeasured(
                "unmeasured: %s 是 %s,但 exception 是 null —— 分不出是哪一種紅(%s)"
                % (row["id"], state, row.get("message") or "沒有 message"))
        if exc in IMPORT_EXCS or str(row.get("message") or "").startswith(IMPORT_MESSAGE):
            out["import_failures"].append(label)
        elif not frame_in(row.get("file"), rels) or raised_below(row):
            out["elsewhere"].append(label)
        elif exc.endswith("AssertionError"):
            out["red"].append(row["id"])
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


def case_timeout(root):
    """案例那一趟的時限:`board/config.json` 的 `verify_case.timeout_seconds`(#156)。

    沒設 → 900;正整數 → 照用;其他(0、負數、小數、字串、`true`、`null`)→ 印一行
    警告指名那一格與那個值,再退回 900(同 `worker.timeout_seconds` 的形狀,#64)。
    `bool` 是 `int` 的子類別,不先擋掉的話 `true` 會被當成 1 秒。
    """
    section = event.config(root).get("verify_case")
    if not isinstance(section, dict) or "timeout_seconds" not in section:
        return TIMEOUT
    value = section["timeout_seconds"]
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    sys.stderr.write("verify-case: board/config.json 的 %s=%s 不是正整數 —— 不採用,退回 %d\n"
                     % (TIMEOUT_KEY, json.dumps(value, ensure_ascii=False), TIMEOUT))
    return TIMEOUT


def run_cases(where, rels, log_path, timeout=TIMEOUT, mode="verify-check"):
    """在 `where` 這份副本裡跑這幾個案例檔,回傳一份可以比對的結果(D-071 C7,#163)。

    跑的是 `scripts/testiface.py regression --case <檔>…`:專案的 `tests.regression`,沒宣告
    就是內建的 `verify.py --case … --result`。`AC_CODE_ROOT` 設成這份副本(D-071 §1):命令
    量的是它,不是呼叫端 cwd 的那棵樹。`AC_TEST_OUT` 是 `log_path` 去掉 `.log` 的目錄(一份
    副本一個,跑之前清空 —— testiface 只收空的),`AC_TEST_TIMEOUT` 是 `timeout`,testiface
    照它停下整個行程群組。分類只讀 `result.json`;`<command>.log` 抄成 `log_path` 當證據。

    逾時丟 `CaseTimeout`(逾時前的輸出照樣在 `log_path`);其他量不到(rc 2、rc 86、rc 3、
    result.json 讀不回來、一列 `exception` 是 null)丟 `CaseUnmeasured`。
    """
    mods = [module_of(rel) for rel in rels]
    out_dir = log_path[:-len(".log")] if log_path.endswith(".log") else log_path + ".d"
    shutil.rmtree(out_dir, ignore_errors=True)
    env = dict(os.environ, AC_ROOT=event.repo_root(), AC_CODE_ROOT=os.path.abspath(where),
               AC_TEST_OUT=out_dir, AC_TEST_MODE=mode, AC_TEST_TIMEOUT=str(timeout))
    argv = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "testiface.py"), "regression"]
    for rel in rels:
        argv += ["--case", rel]
    expired = False
    try:
        # testiface 自己照 `AC_TEST_TIMEOUT` 停下命令;這裡多給的一分鐘是煞車,不是時限。
        done = subprocess.run(argv, cwd=where, env=env, capture_output=True, text=True,
                              timeout=timeout + 60)
        rc, said = done.returncode, (done.stderr or "")
    except subprocess.TimeoutExpired:
        rc, said, expired = None, "", True
    text = ""
    command_log = os.path.join(out_dir, "regression.log")
    if os.path.exists(command_log):
        with open(command_log, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(text + said)
    if expired or (rc == UNMEASURED_RC and TIMEOUT_SAYS in said):
        raise CaseTimeout("案例跑超過 %d 秒(%s)被中止 —— 時限在 board/config.json 的 %s;"
                          "逾時前的輸出 -> %s"
                          % (timeout, " ".join(mods), TIMEOUT_KEY, log_path))
    if rc not in (0, 1):
        raise CaseUnmeasured("unmeasured: testiface regression rc %s(%s)%s -> %s"
                             % (rc, " ".join(mods),
                                ":" + said.strip().splitlines()[-1] if said.strip() else "",
                                log_path))
    try:
        with open(os.path.join(out_dir, "result.json"), encoding="utf-8") as handle:
            rows = json.load(handle)["ids"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # testiface rc 0/1 已經驗過它;讀不回來還是量不到,不是零條紅。
        raise CaseUnmeasured("unmeasured: %s 讀不回來(%s)" % (
            os.path.join(out_dir, "result.json"), exc)) from None
    out = shapes(rows, rels)
    out.update({"rc": rc, "cases": len(rows), "log": log_path})
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


def resolve_tree(repo, tree, spec, where, label):
    """把 `--ref` / `--candidate` 這一格解成「一棵可以跑測試的樹」+ 它的 sha。

    三種寫法都要解得開:**worktree 路徑、分支名、sha**。舊版一律當路徑用,於是
    `--candidate t20` 去找 `$PWD/t20`,印出「candidate 裡找不到這幾個案例檔」——
    而檔就在 t20 上,只是沒有人去 git 裡拿(#20)。

    順序是**路徑先於 ref**:既有的叫法一律傳路徑,反過來會讓一個剛好與分支同名的
    目錄被解成 ref,而那一趟跑的樹與呼叫者指的不是同一棵。

    `repo` 是解 ref 的那一顆(`code_repo()`),`tree` 是沒給 spec 時的那棵樹(`code_tree()`)。
    回傳 `(樹的路徑, sha, 解不開的理由)`;解不開時前兩格不保證有值。
    """
    if not spec:
        return tree, git(["rev-parse", "HEAD"], tree).stdout.strip(), ""
    if os.path.isdir(spec):
        path = os.path.abspath(spec)
        return path, git(["rev-parse", "HEAD"], path).stdout.strip(), ""
    seen = git(["rev-parse", "--verify", "--quiet", "%s^{commit}" % spec], repo)
    if seen.returncode != 0 or not seen.stdout.strip():
        return None, "", "%s「%s」既不是一個目錄,也不是這個 repo 裡的 ref" % (label, spec)
    sha = seen.stdout.strip()
    if not clean_copy(repo, sha, where):
        return None, sha, "做不出 %s(%s)的乾淨副本" % (spec, sha[:12])
    return where, sha, ""


def main_repo_root(root):
    """主 repo 根:`git rev-parse --git-common-dir` 的上一層(`wtbase.sh` 的 `main_root`
    同一條)。在票的 worktree 裡它指回主 repo;不在 git 裡就是 `root`。"""
    common = git(["rev-parse", "--git-common-dir"], root).stdout.strip()
    if not common:
        return root
    return os.path.dirname(os.path.join(root, common).rstrip(os.sep))


def portable(record, repo, tree, candidate_spec):
    """寫進票 / 證據檔之前,把三格路徑改成相對主 repo 根(#87 A2):`candidate`、
    `baseline.log`、`candidate_run.log`。票檔進 git,家目錄的絕對路徑不該跟著進去;
    worktree 在 `../<repo>-wt/t<n>` 時就是 `../` 開頭。`--candidate` 給的是分支名 / sha
    就照原字串 —— 那不是路徑。"""
    main = os.path.realpath(main_repo_root(repo))

    def rel(path):
        return os.path.relpath(os.path.realpath(path), main)

    out = dict(record)
    if not candidate_spec or os.path.isdir(candidate_spec):
        out["candidate"] = rel(candidate_spec or tree)
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
    repo, tree = code_repo(), code_tree()
    candidate, cand_sha, why = resolve_tree(
        repo, tree, args.candidate, os.path.join(where, "candidate"), "candidate")
    if why:
        return None, "", None, "", unmeasured(args.ticket, why, ref, verb)
    base_dir, base_sha, why = resolve_tree(
        repo, tree, ref, os.path.join(where, "base"), "ref")
    if why:
        return None, "", None, "", unmeasured(args.ticket, why, ref, verb)
    # ref 的歷史裡已經有 candidate = 那棵樹上**本來就有這份實作**,再怎麼跑也紅不
    # 起來。舊版把那一趟的「一條都沒紅」當判決蓋進票,`ticket.py close` 從此擋著那
    # 張票(#19)。兩個 sha 一樣時不算 —— 那是「candidate 是同一棵樹上未 commit 的
    # 改動」,驗證者交件時的正常形狀。
    if cand_sha and base_sha and cand_sha != base_sha and git(
            ["merge-base", "--is-ancestor", cand_sha, base_sha], repo).returncode == 0:
        return None, "", None, "", unmeasured(
            args.ticket,
            "ref(%s / %s)的歷史裡已經有 candidate(%s / %s)——"
            "那棵樹上本來就有這份實作,量不出紅"
            % (ref, base_sha[:12], args.candidate or tree, cand_sha[:12]), ref, verb)
    # ref 那棵樹上本來就不會有這幾個案例檔(案例是這張票才加的),所以一律把
    # candidate 的那一份疊上去 —— **兩邊跑的一定要是同一份檔**。
    missing = overlay(rels, candidate, base_dir)
    if missing:
        return None, "", None, "", unmeasured(
            args.ticket, "candidate(%s)裡找不到這幾個案例檔:%s"
            % (args.candidate or tree, ", ".join(missing)), ref, verb)
    return candidate, cand_sha, base_dir, base_sha, None


def write_baseline(ident, record):
    """證據寫回票的 `verify.baseline`(同一格,`stage` 是它的升級)。**只有 `check`
    叫它**;`red` 交的是證據檔(`write_red_record`),不碰票(#36)。"""
    try:
        ticketlib.save_baseline(ident, record)
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
        try:
            run = run_cases(base_dir, rels, os.path.join(logs, "baseline.log"),
                            case_timeout(root), "verify-red")
        except CaseUnmeasured as exc:
            return unmeasured(args.ticket, str(exc), ref, "red")
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
            "candidate": args.candidate or code_tree(),
            "candidate_sha": cand_sha,
            "baseline": run,
            "candidate_run": None,
        }
        path = write_red_record(args.ticket, portable(record, code_repo(), code_tree(), args.candidate), where)
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
        # 兩趟各算各的時限:base 與 candidate 是兩次獨立的 regression。
        timeout = case_timeout(root)
        try:
            baseline = run_cases(base_dir, rels, os.path.join(logs, "baseline.log"), timeout,
                                 "verify-check")
            cand = run_cases(candidate, rels, os.path.join(logs, "candidate.log"), timeout,
                             "verify-check")
        except CaseUnmeasured as exc:
            return unmeasured(args.ticket, str(exc), ref, "check")

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
            "candidate": args.candidate or code_tree(),
            "candidate_sha": cand_sha,
            "baseline": baseline,
            "candidate_run": cand,
        }
        rc = write_baseline(args.ticket, portable(record, code_repo(), code_tree(), args.candidate))
        if rc:
            return rc

        sys.stdout.write("verify-case: #%s 案例 %d 個\n" % (args.ticket, baseline["cases"]))
        sys.stdout.write("  乾淨主線 %s(%s):紅 %d、skip %d、import 失敗 %d -> %s\n"
                         % (ref, base_sha[:12], len(baseline["red"]),
                            baseline["skipped"], len(baseline["import_failures"]),
                            baseline["log"]))
        print_shapes(baseline)
        sys.stdout.write("  candidate %s(%s):紅 %d、skip %d -> %s\n"
                         % (args.candidate or code_tree(), cand_sha[:12], len(cand["red"]),
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


def cmd_extract(args):
    """只抽驗證檔出一份 `patch-verify.diff`。

    範本要求驗證者交「只含自己 verify 檔」的 diff,而它的 `work/` 裡已經有實作者的
    patch —— **沒有給差分基準**,手工挑檔遲早挑錯一個(2026-09-21 外部審查)。
    差分基準在這裡說死:乾淨的 `<ref>`。
    """
    repo = code_repo()
    candidate = os.path.abspath(args.candidate or code_tree())
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
        old = git(["show", "%s:%s" % (ref, rel)], repo)
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
