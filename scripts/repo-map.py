#!/usr/bin/env python3
"""repo 地圖:「X 在哪、誰守它」查得到,而且**查到的不是過期的那一份**(#87,原 #77)。

    python3 scripts/repo-map.py build          # 依 code-map/catalog.json 重生 index.json + INDEX.md
    python3 scripts/repo-map.py check          # 索引新不新、連結在不在;不動任何檔
    python3 scripts/repo-map.py query memory   # 一個主題的入口、主守衛、來源、文件
    python3 scripts/repo-map.py --root <樹> check   # 量指定的那一棵(閘門量候選樹用)

## 三份檔
- `code-map/catalog.json` —— **手寫**:哪些檔算「已管理」(`managed` 的 glob),每個主題
  用哪幾個字認它的來源(`match`)、真正的入口(`entry`)與主守衛(`guard`)、文件與人工卡。
- `code-map/index.json`、`code-map/INDEX.md` —— **產出物**,`build` 寫、`check` 比。

## 新鮮度只看「有哪些檔」,不看檔的內容
索引記的是已管理檔的**名單**與每個主題分到哪幾支:新增 / 改名 / 刪除一支已管理的檔,
`check` 就非零;改一支檔的內容不會。把內容雜湊放進去的話,每一張改 code 的票都得重生
索引 —— 那是每一次 patch 都多一個衝突點,而它換到的資訊是零(「入口在哪」不因改一行
而變)。產出物不帶時間戳、鍵排序、清單排序:同一棵樹連跑兩次,位元組相同。

## 退出碼
`0` 新的 / 查到;`1` 過期、連結斷了、查無此主題;`2` 用法錯、catalog 讀不動、還沒 build。
"""

import argparse
import difflib
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根(同步到專案後住在 scripts/control/ 也找得到根)

CATALOG = os.path.join("code-map", "catalog.json")
INDEX_JSON = os.path.join("code-map", "index.json")
INDEX_MD = os.path.join("code-map", "INDEX.md")
BUILD = "python3 scripts/repo-map.py build"
LINK_FIELDS = ("entry", "guard")
LIST_FIELDS = ("docs", "cards")


def load_catalog(root):
    path = os.path.join(root, CATALOG)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("topics"), dict):
        raise ValueError("%s 沒有 topics" % CATALOG)
    return data


def managed_files(root, patterns):
    found = set()
    for pattern in patterns:
        for path in glob.glob(os.path.join(root, pattern), recursive=True):
            if os.path.isfile(path) and "__pycache__" not in path.split(os.sep):
                found.add(os.path.relpath(path, root).replace(os.sep, "/"))
    return sorted(found)


def build_index(root, catalog):
    managed = managed_files(root, catalog.get("managed") or [])
    topics = {}
    for name, topic in sorted(catalog["topics"].items()):
        words = [word.lower() for word in topic.get("match") or [name]]
        sources = [rel for rel in managed
                   if any(word in os.path.basename(rel).lower() for word in words)]
        row = {"summary": topic.get("summary") or "", "sources": sources}
        for field in LINK_FIELDS:
            row[field] = topic.get(field) or ""
        for field in LIST_FIELDS:
            row[field] = sorted(topic.get(field) or [])
        topics[name] = row
    return {"version": 1, "generated_by": BUILD, "catalog": CATALOG.replace(os.sep, "/"),
            "managed": managed, "topics": topics}


def render_json(index):
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_md(index):
    def link(rel):
        return "[`%s`](../%s)" % (rel, rel)

    out = ["# code-map 索引",
           "",
           "由 `%s` 依 `%s` 產生,不要手改;改 catalog 再重生。" % (BUILD, index["catalog"]),
           "查一個主題:`python3 scripts/repo-map.py query <主題>`;新不新:"
           "`python3 scripts/repo-map.py check`。",
           ""]
    for name, topic in sorted(index["topics"].items()):
        out += ["## %s" % name, ""]
        if topic["summary"]:
            out += [topic["summary"], ""]
        out.append("- 入口:%s" % (link(topic["entry"]) if topic["entry"] else "(沒有)"))
        out.append("- 主守衛:%s" % (link(topic["guard"]) if topic["guard"] else "(沒有)"))
        others = [rel for rel in topic["sources"] if rel not in (topic["entry"], topic["guard"])]
        for label, rows in (("來源", others), ("文件", topic["docs"]), ("人工卡", topic["cards"])):
            if rows:
                out.append("- %s:%s" % (label, "、".join(link(rel) for rel in rows)))
        out.append("")
    return "\n".join(out)


def broken_links(root, catalog):
    problems = []
    for name, topic in sorted(catalog["topics"].items()):
        rows = [(field, topic.get(field)) for field in LINK_FIELDS if topic.get(field)]
        rows += [(field, rel) for field in LIST_FIELDS for rel in topic.get(field) or []]
        for field, rel in rows:
            if not os.path.isfile(os.path.join(root, rel)):
                problems.append("主題 %s 的 %s 指到不存在的 %s" % (name, field, rel))
    return problems


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def stale_reasons(root, index):
    """磁碟上那一份與現在重生出來的差在哪 —— 逐檔說,不是一句「過期了」。"""
    disk = read_text(os.path.join(root, INDEX_JSON))
    if disk is None:
        return ["%s 不在(還沒 build)" % INDEX_JSON]
    reasons = []
    try:
        old = json.loads(disk)
    except ValueError:
        return ["%s 讀不動(不是 JSON)" % INDEX_JSON]
    before, now = set(old.get("managed") or []), set(index["managed"])
    reasons += ["新增的已管理檔沒進索引:%s" % rel for rel in sorted(now - before)]
    reasons += ["索引裡的檔已經不在:%s" % rel for rel in sorted(before - now)]
    if not reasons and disk != render_json(index):
        reasons.append("%s 與 catalog 重生出來的不一樣(主題或連結改了)" % INDEX_JSON)
    if read_text(os.path.join(root, INDEX_MD)) != render_md(index):
        reasons.append("%s 與重生出來的不一樣" % INDEX_MD)
    return reasons


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def cmd_build(root, args):
    catalog = load_catalog(root)
    problems = broken_links(root, catalog)
    if problems:
        for line in problems:
            sys.stderr.write("repo-map: %s\n" % line)
        sys.stderr.write("repo-map: 先修 %s 的連結再 build(索引沒有寫)\n" % CATALOG)
        return 1
    index = build_index(root, catalog)
    write(os.path.join(root, INDEX_JSON), render_json(index))
    write(os.path.join(root, INDEX_MD), render_md(index))
    sys.stdout.write("repo-map: 寫好 %s 與 %s(已管理 %d 支、主題 %d 個)\n"
                     % (INDEX_JSON, INDEX_MD, len(index["managed"]), len(index["topics"])))
    return 0


def cmd_check(root, args):
    catalog = load_catalog(root)
    index = build_index(root, catalog)
    problems = broken_links(root, catalog) + stale_reasons(root, index)
    if problems:
        for line in problems:
            sys.stderr.write("repo-map: %s\n" % line)
        sys.stderr.write("repo-map: code-map 索引過期 —— 重生:%s,連同產出物一起 commit\n" % BUILD)
        return 1
    sys.stdout.write("repo-map: 索引是新的(已管理 %d 支、主題 %d 個)\n"
                     % (len(index["managed"]), len(index["topics"])))
    return 0


def cmd_query(root, args):
    text = read_text(os.path.join(root, INDEX_JSON))
    if text is None:
        sys.stderr.write("repo-map: %s 不在 —— 先跑 %s\n" % (INDEX_JSON, BUILD))
        return 2
    topics = json.loads(text).get("topics") or {}
    topic = topics.get(args.topic) or topics.get(args.topic.lower())
    if topic is None:
        near = difflib.get_close_matches(args.topic.lower(), sorted(topics), n=3)
        sys.stderr.write("repo-map: 沒有主題 %r%s —— 認得的:%s\n"
                         % (args.topic, "(是不是 %s?)" % " / ".join(near) if near else "",
                            " / ".join(sorted(topics))))
        sys.stderr.write("repo-map: 索引入口:%s\n" % INDEX_MD.replace(os.sep, "/"))
        return 1
    name = args.topic if args.topic in topics else args.topic.lower()
    sys.stdout.write("%s —— %s\n" % (name, topic.get("summary") or "(沒有摘要)"))
    rows = [("入口", topic.get("entry")), ("主守衛", topic.get("guard"))]
    rows += [("來源", rel) for rel in topic.get("sources") or []
             if rel not in (topic.get("entry"), topic.get("guard"))]
    rows += [("文件", rel) for rel in topic.get("docs") or []]
    rows += [("人工卡", rel) for rel in topic.get("cards") or []]
    for label, rel in rows:
        if rel:
            sys.stdout.write("  %s\t%s\n" % (label, rel))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="repo-map.py")
    parser.add_argument("--root", default="", help="量哪一棵樹(預設:這支腳本認的 repo 根)")
    subs = parser.add_subparsers(dest="verb")
    subs.add_parser("build").set_defaults(run=cmd_build)
    subs.add_parser("check").set_defaults(run=cmd_check)
    query = subs.add_parser("query")
    query.add_argument("topic")
    query.set_defaults(run=cmd_query)
    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    root = os.path.abspath(args.root) if args.root else event.repo_root()
    try:
        return args.run(root, args)
    except (OSError, ValueError) as exc:
        sys.stderr.write("repo-map: %s 讀不動 —— %s\n" % (CATALOG, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
