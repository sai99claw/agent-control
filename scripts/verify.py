#!/usr/bin/env python3
"""回歸層執行器(docs/VERIFICATION.md)。

    python3 scripts/verify.py --list                 # 標籤 → 案例檔
    python3 scripts/verify.py --tag ledger-colour    # 只跑帶這個標籤的案例(可重複 --tag)
    python3 scripts/verify.py                        # 全部回歸
    python3 scripts/verify.py --unit                 # 專案的單元層(board/config.json 的 unit_cmd)
    python3 scripts/verify.py --all                  # 單元 + 回歸

每個 verify/<feature>/test_*.py 必須宣告 TAGS(list[str]),且每個標籤登記在 verify/TAGS.md;否則 rc=2 並指名檔案。
"""
import argparse, ast, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY = os.path.join(ROOT, "verify")

def registered_tags():
    p = os.path.join(VERIFY, "TAGS.md")
    if not os.path.exists(p):
        return set()
    return set(re.findall(r"^- `([a-z0-9-]+)`", open(p, encoding="utf-8").read(), re.M))

def tags_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TAGS" for t in node.targets):
            return [ast.literal_eval(e) for e in node.value.elts]
    return None

def cases():
    out = []
    for d, _, files in os.walk(VERIFY):
        for f in sorted(files):
            if f.startswith("test_") and f.endswith(".py"):
                out.append(os.path.join(d, f))
    return out

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--unit", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if a.unit or a.all:
        cfg = os.path.join(ROOT, "board", "config.json")
        cmd = (json.load(open(cfg)).get("unit_cmd") if os.path.exists(cfg) else None)
        if not cmd:
            print("verify: 單元層未設定(board/config.json 的 unit_cmd)"); return 3
        rc = subprocess.run(cmd, shell=True, cwd=ROOT).returncode
        print(f"verify: 單元層 rc={rc}")
        if rc or a.unit:
            return rc
    known = registered_tags()
    bad, picked = [], []
    for c in cases():
        t = tags_of(c)
        if not t:
            bad.append(f"{os.path.relpath(c, ROOT)}: 沒有 TAGS")
            continue
        unknown = [x for x in t if x not in known]
        if unknown:
            bad.append(f"{os.path.relpath(c, ROOT)}: 標籤未登記 {unknown}(登記在 verify/TAGS.md)")
            continue
        if not a.tag or set(a.tag) & set(t):
            picked.append((c, t))
    if bad:
        print("verify: 案例檔不合格:\n  " + "\n  ".join(bad)); return 2
    if a.list:
        for c, t in picked:
            print(f"{os.path.relpath(c, ROOT)}  {t}")
        print(f"verify: {len(picked)} 個案例檔,{len(known)} 個登記標籤"); return 0
    if not picked:
        print(f"verify: 沒有案例符合 {a.tag or '全部'}(登記標籤 {sorted(known)})"); return 3
    mods = [os.path.relpath(c, ROOT)[:-3].replace(os.sep, ".") for c, _ in picked]
    r = subprocess.run([sys.executable, "-m", "unittest", *mods], cwd=ROOT)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
