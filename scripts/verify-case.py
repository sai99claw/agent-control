#!/usr/bin/env python3
"""驗證產物工具:同一份案例,在乾淨主線該紅、在 candidate 該綠 — D-014。

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

TAGS_REL = os.path.join("verify", "TAGS.md")
TAGS_DIR_REL = os.path.join("verify", "TAGS.d")
TAG_LINE = re.compile(r"^- `([a-z0-9-]+)`\s*(.*)$")
RAN = re.compile(r"^Ran (\d+) test")
SKIPPED = re.compile(r"skipped=(\d+)")
IMPORT_MARKS = ("ImportError", "ModuleNotFoundError", "_FailedTest",
                "cannot import name", "No module named")
TIMEOUT = 900


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def module_of(rel):
    return rel[:-3].replace(os.sep, ".").replace("/", ".")


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
    rows = status.parse_failures(log_path)
    imports = [row for row in rows
               if any(mark in (row["case"] + row["excerpt"]) for mark in IMPORT_MARKS)]
    red = [row for row in rows if row not in imports]
    count, skipped = 0, 0
    for line in text.splitlines():
        hit = RAN.match(line)
        if hit:
            count = int(hit.group(1))
        hit = SKIPPED.search(line)
        if hit:
            skipped = int(hit.group(1))
    return {"rc": done.returncode, "cases": count, "skipped": skipped,
            "red": [row["case"] for row in red],
            "import_failures": ["%s: %s" % (row["case"], row["excerpt"].splitlines()[-1]
                                            if row["excerpt"] else "")
                                for row in imports],
            "log": log_path}


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


def unmeasured(ident, why, ref):
    """**量不到就不要在票上留一個長得像判決的紀錄。**

    #19 落地之後 `check` 在主線上量不到紅,把 `ok: false` 蓋回票的 `verify.baseline`,
    `ticket.py close` 從此擋著那張票 —— 而那一格說的其實是「這一趟沒量到」,不是
    「這條案例驗不到東西」,兩者的下一步差很多。所以量不到的路徑**不寫票**,改印
    下一步該敲什麼。
    """
    sys.stderr.write("verify-case: #%s 量不到基準,票沒有動 —— %s\n" % (ident, why))
    sys.stderr.write("  下一步:scripts/verify-case.py check %s --ref <票的 base_sha> "
                     "--candidate <分支名 / sha / worktree 路徑>"
                     "(這一趟用的 ref 是 %s)\n" % (ident, ref))
    return 2


def cmd_check(args):
    root = event.repo_root()
    try:
        data = ticketlib.load(args.ticket)
    except (OSError, ValueError) as exc:
        sys.stderr.write("verify-case: 讀不到票 #%s —— %s\n" % (args.ticket, exc))
        return 2
    plan = data.get("verify") if isinstance(data.get("verify"), dict) else {}
    rels = list(plan.get("files") or [])
    if not rels:
        sys.stderr.write("verify-case: 票 #%s 的 verify.files 是空的 —— "
                         "驗證者還沒交案例,沒有東西可以驗紅\n" % args.ticket)
        return 3
    # 預設的 ref 是**票的 base_sha**:對主線量,票一落地就量不到紅了(#19)。
    ref = args.ref or data.get("base_sha") or ticketlib.main_branch()
    where = args.out_dir or tempfile.mkdtemp(prefix="verify-case-")
    logs = os.path.join(where, "logs")
    os.makedirs(logs, exist_ok=True)

    candidate, cand_sha, why = resolve_tree(
        root, args.candidate, os.path.join(where, "candidate"), "candidate")
    if why:
        return unmeasured(args.ticket, why, ref)
    base_dir, base_sha, why = resolve_tree(
        root, ref, os.path.join(where, "base"), "ref")
    if why:
        return unmeasured(args.ticket, why, ref)
    # ref 的歷史裡已經有 candidate = 那棵樹上**本來就有這份實作**,再怎麼跑也紅不
    # 起來。舊版把那一趟的「一條都沒紅」當判決蓋進票,`ticket.py close` 從此擋著那
    # 張票(#19)。兩個 sha 一樣時不算 —— 那是「candidate 是同一棵樹上未 commit 的
    # 改動」,驗證者交件時的正常形狀。
    if cand_sha and base_sha and cand_sha != base_sha and git(
            ["merge-base", "--is-ancestor", cand_sha, base_sha], root).returncode == 0:
        return unmeasured(args.ticket,
                          "ref(%s / %s)的歷史裡已經有 candidate(%s / %s)——"
                          "那棵樹上本來就有這份實作,量不出紅"
                          % (ref, base_sha[:12], args.candidate or root, cand_sha[:12]), ref)
    # ref 那棵樹上本來就不會有這幾個案例檔(案例是這張票才加的),所以一律把
    # candidate 的那一份疊上去 —— **兩邊跑的一定要是同一份檔**。
    missing = overlay(rels, candidate, base_dir)
    if missing:
        return unmeasured(args.ticket,
                          "candidate(%s)裡找不到這幾個案例檔:%s"
                          % (args.candidate or root, ", ".join(missing)), ref)

    baseline = run_cases(base_dir, rels, os.path.join(logs, "baseline.log"))
    cand = run_cases(candidate, rels, os.path.join(logs, "candidate.log"))

    why = []
    if baseline["import_failures"]:
        why.append("乾淨主線上有 %d 個 import 失敗 —— 那是還沒接上,不是驗到了"
                   % len(baseline["import_failures"]))
    if not baseline["red"]:
        why.append("乾淨主線上一條都沒紅 —— 一個永遠綠的案例與一個真的在驗的案例長得一樣")
    if cand["red"] or cand["import_failures"]:
        why.append("candidate 上還有 %d 條紅 / %d 個 import 失敗"
                   % (len(cand["red"]), len(cand["import_failures"])))
    if baseline["cases"] != cand["cases"]:
        why.append("兩邊跑到的案例數不一樣(%d vs %d)—— 比的不是同一組"
                   % (baseline["cases"], cand["cases"]))
    ok = not why

    record = {
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
    plan = dict(plan)
    plan["baseline"] = record
    try:
        with ticketlib.Lock():
            fresh = ticketlib.load(args.ticket)
            fresh["verify"] = plan
            fresh["state_version"] = int(fresh.get("state_version") or 0) + 1
            ticketlib.save(fresh)
    except (OSError, ValueError, RuntimeError) as exc:
        sys.stderr.write("verify-case: 證據寫不回票 #%s —— %s\n" % (args.ticket, exc))
        return 2
    event.emit("ticket.state", ticket=args.ticket, field="verify.baseline",
               **{"to": "ok" if ok else "紅不起來",
                  "state_version": fresh["state_version"]})

    sys.stdout.write("verify-case: #%s 案例 %d 個\n" % (args.ticket, baseline["cases"]))
    sys.stdout.write("  乾淨主線 %s(%s):紅 %d、skip %d、import 失敗 %d -> %s\n"
                     % (ref, base_sha[:12], len(baseline["red"]),
                        baseline["skipped"], len(baseline["import_failures"]),
                        baseline["log"]))
    for name in baseline["red"]:
        sys.stdout.write("    紅 %s\n" % name)
    for name in baseline["import_failures"]:
        sys.stdout.write("    import 失敗(不算紅) %s\n" % name)
    sys.stdout.write("  candidate %s(%s):紅 %d、skip %d -> %s\n"
                     % (args.candidate or root, cand_sha[:12], len(cand["red"]),
                        cand["skipped"], cand["log"]))
    for name in cand["red"]:
        sys.stdout.write("    紅 %s\n" % name)
    if ok:
        sys.stdout.write("verify-case: #%s baseline 成立(證據寫進票的 verify.baseline)\n"
                         % args.ticket)
        return 0
    sys.stdout.write("verify-case: #%s baseline **不成立**:%s\n" % (args.ticket, record["why"]))
    return 1


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
