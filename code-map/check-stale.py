#!/usr/bin/env python3
"""模組卡片的過期偵測 — `docs/CODE-MAP.md` §2。

    python3 code-map/check-stale.py            # 列出過期的卡
    python3 code-map/check-stale.py --all      # 連沒過期的也列

每張卡記著「我是在哪個 commit 上被驗證過的」(`verified_at_commit`)。這一支問:
**從那個 commit 到現在,卡上列的 `source_paths` 動過嗎?** 動過就是 `stale`。

## 為什麼卡片不是契約
閘門**不擋** stale 的卡。卡片是輔助:它省的是「這東西在哪」的查找時間,而一張過期的
卡最壞的後果是害人多查一次 —— 把它升級成閘門條件,會讓每一次改 code 都得先改卡片,
而那個成本會讓大家不寫卡片。**維護若比省下的工作貴,就縮小範圍**(D-004)。

## 退出碼
- `0`:沒有過期的卡(或者只有過期的卡 —— 過期不是錯誤)。
- `2`:**卡片本身壞了** —— 沒有 front matter、缺 `verified_at_commit`、那個 commit
  這顆 repo 沒有。這一種要非零:一張讀不動的卡與一張沒過期的卡,在「過期清單是空的」
  那一行輸出裡長得一模一樣(`docs/DISPATCH-TEMPLATE.md` §5.5)。
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AC_ROOT") or os.path.dirname(HERE)
CARDS = os.path.join(HERE, "cards")
FENCE = "---"
INLINE_LIST = re.compile(r"^\[(.*)\]$")


def parse_card(text):
    """front matter 的最小剖析:`key: value`、`key: [a, b]`、以及

        key:
          - "a"
          - "b"

    三種形狀。不引入 YAML 函式庫(零依賴),也不假裝支援更多 —— 支援得比實際用到
    的多,會讓卡片寫出這支剖析器讀不懂的形狀,而**讀不懂的那一格會靜靜變成空的**。
    """
    if not text.startswith(FENCE + "\n"):
        return None
    end = text.find("\n" + FENCE, len(FENCE))
    if end < 0:
        return None
    fields = {}
    key = None
    for line in text[len(FENCE) + 1:end].splitlines():
        if not line.strip():
            continue
        if line.startswith(("  - ", "- ")) and key:
            fields.setdefault(key, [])
            if isinstance(fields[key], list):
                fields[key].append(line.split("-", 1)[1].strip().strip('"\''))
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip()
        value = value.strip()
        found = INLINE_LIST.match(value)
        if found:
            fields[key] = [part.strip().strip('"\'')
                           for part in found.group(1).split(",") if part.strip()]
        elif value:
            fields[key] = value.strip('"\'')
        else:
            fields[key] = []
    return fields


def git(args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                          text=True, timeout=30)


def check(name, fields):
    """回傳 `(狀態, 說明)`,狀態是 `stale` / `fresh` / `broken`。"""
    sha = fields.get("verified_at_commit")
    paths = fields.get("source_paths") or []
    if not sha:
        return "broken", "沒有 verified_at_commit"
    if not paths:
        return "broken", "沒有 source_paths"
    if git(["rev-parse", "--verify", "-q", "%s^{commit}" % sha]).returncode != 0:
        return "broken", "這顆 repo 沒有 commit %s" % sha
    done = git(["diff", "--stat", "%s..HEAD" % sha, "--", *paths])
    if done.returncode != 0:
        return "broken", (done.stderr or done.stdout).strip()
    changed = done.stdout.strip()
    if changed:
        return "stale", changed.splitlines()[-1].strip()
    return "fresh", "%s..HEAD 沒有動到 %s" % (sha[:12], ", ".join(paths))


def main(argv):
    show_all = "--all" in argv
    try:
        names = sorted(n for n in os.listdir(CARDS) if n.endswith(".md"))
    except OSError as exc:
        sys.stderr.write("check-stale: 讀不到 %s —— %s\n" % (CARDS, exc))
        return 2
    names = [n for n in names if n != "README.md"]
    if not names:
        # 一張卡都沒有要說出來:空清單與「都沒過期」長得一樣。
        sys.stdout.write("check-stale: 一張卡都沒有(卡片只在查找失敗過的地方建)\n")
        return 0
    broken = 0
    stale = 0
    for name in names:
        with open(os.path.join(CARDS, name), encoding="utf-8") as handle:
            fields = parse_card(handle.read())
        if fields is None:
            sys.stdout.write("check-stale: %s 壞了 —— 沒有 front matter\n" % name)
            broken += 1
            continue
        status, why = check(name, fields)
        module = fields.get("module", name)
        if status == "broken":
            sys.stdout.write("check-stale: %s 壞了 —— %s\n" % (name, why))
            broken += 1
        elif status == "stale":
            sys.stdout.write("check-stale: %s **過期** —— %s\n" % (module, why))
            stale += 1
        elif show_all:
            sys.stdout.write("check-stale: %s 還新 —— %s\n" % (module, why))
    if not stale and not broken:
        sys.stdout.write("check-stale: %d 張卡都還新\n" % len(names))
    return 2 if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
