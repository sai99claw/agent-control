#!/usr/bin/env python3
"""回歸層執行器(docs/VERIFICATION.md)。

    python3 scripts/verify.py --list                 # 標籤 → 案例檔
    python3 scripts/verify.py --tag ledger-colour    # 只跑帶這個標籤的案例(可重複 --tag)
    python3 scripts/verify.py                        # 全部回歸
    python3 scripts/verify.py --unit                 # 專案的單元層(board/config.json 的 unit_cmd)
    python3 scripts/verify.py --all                  # 單元 + 回歸
    python3 scripts/verify.py --tag x --no-cache     # 不吃同一輪的快取,真的重跑

每個 verify/<feature>/test_*.py 必須宣告 TAGS(list[str]),且每個標籤登記在 verify/TAGS.md;否則 rc=2 並指名檔案。

## 同一輪只跑一次(D-015)
2026-09-21 外部審查的 token 帳:「同一組 tag 回歸可能被 worker、驗證者、gate 重跑。」
驗證者那一份已經拿掉了(它只跑自己的案例),剩下的兩份還在 —— worker 交付前跑一次、
閘門再跑一次,**同一份程式、同一組標籤、同一個 commit**,兩份一模一樣的綠。

所以:環境裡有 `AC_TICKET` 與 `AC_RUN_ID` 時,這一支把輸出與 rc 存成
`reports/t<票號>/<run_id>/verify-<雜湊>.log`。雜湊 = 排序過的標籤 + 這棵樹的 HEAD sha
+ 跑不跑單元層。同一輪裡第二次問同一個問題就**印回上一次的原始輸出**,不重跑。

**為什麼雜湊要含 sha**:快取的危險不是省太多,是省錯 —— 「上一次綠」與「這一次也會綠」
之間隔著一次 commit。sha 變了就是不同的問題,快取一定失效。
沒有 `AC_RUN_ID` 就完全不快取(單獨跑的人拿到的永遠是真的跑)。

## 兩個根(#55)
程式碼根是這支檔案所在的那棵樹(#55):`verify/` 的案例、被測檔、unittest 的 cwd、
`--unit` 的 `unit_cmd`、快取雜湊裡的 HEAD sha,全部跟 `ROOT`(從 `__file__` 往上找
`board/config.json`)。AC_ROOT 不再是程式碼根(#55):它只管票、事件與 `reports/`
—— 快取檔住在 `event.repo_root()` 那棵樹。land.sh 在票 worktree 裡帶
`AC_ROOT=<主 repo>` 跑閘門時,回歸層跑的是 worktree 那份案例,不是主 repo 那份。
"""
import argparse, ast, hashlib, json, os, re, subprocess, sys

CONFIG_REL = os.path.join("board", "config.json")


def _find_root():
    """程式碼根:從這支檔案往上找 `board/config.json`。**不認 `AC_ROOT`**(#55)——
    那是票根;認了它,worktree 裡的閘門跑的就是主 repo 的 `verify/`。
    不 import event.py,因為這支會被
    `scripts/sync-to-project.sh` 單獨同步到 `<專案>/scripts/control/`,那裡不保證
    有 event.py 陪著。寫死 `dirname(dirname(__file__))` 在那個位置算出的是
    `<專案>/scripts`,不是專案根,`verify/` 找不到、`registered_tags()` 回空集合。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    walk = here
    for _ in range(5):
        walk = os.path.dirname(walk)
        if not walk or walk == os.path.dirname(walk):
            break
        if os.path.exists(os.path.join(walk, CONFIG_REL)):
            return walk
    return os.path.dirname(here)


ROOT = _find_root()
VERIFY = os.path.join(ROOT, "verify")


def head_sha():
    done = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else "no-git"


def cache_path(tags, unit):
    """同一輪、同一組標籤、同一個 sha 的快取檔;不在一輪裡就沒有快取。"""
    ticket, run_id = os.environ.get("AC_TICKET", ""), os.environ.get("AC_RUN_ID", "")
    if not ticket or not run_id:
        return ""
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import status
        # 根走 `event.repo_root()`(認得 `AC_ROOT`),不是這支檔案的上兩層:快取是
        # **這一輪的產物**,它要跟票、reports 住在同一顆樹裡,不是住在腳本旁邊。
        where = status.run_dir(status.event.repo_root(), ticket, run_id)
    except Exception:                                          # noqa: BLE001
        where = os.path.join(ROOT, "reports", "t%s" % ticket, run_id)
    key = hashlib.sha256(("|".join(sorted(tags)) + "@" + head_sha()
                          + ("+unit" if unit else "")).encode("utf-8")).hexdigest()[:12]
    return os.path.join(where, "verify-%s.log" % key)

def registered_tags():
    """`verify/TAGS.md` **加上** `verify/TAGS.d/*.md` 的片段。

    一票一個片段檔是為了讓「登記新標籤」不再讓每張票排隊等前一張落地(D-012 認過
    TAGS 是最常見的衝突點)。片段還沒被 `verify-case.py tags-merge` 折進去之前也算數
    —— 不然合併那一步就變成新的排隊點。
    """
    out = set()
    paths = [os.path.join(VERIFY, "TAGS.md")]
    d = os.path.join(VERIFY, "TAGS.d")
    if os.path.isdir(d):
        paths += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".md")]
    for p in paths:
        if not os.path.exists(p):
            continue
        out |= set(re.findall(r"^- `([a-z0-9-]+)`", open(p, encoding="utf-8").read(), re.M))
    return out

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
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args(argv)
    cache = "" if (a.no_cache or a.list) else cache_path(a.tag, a.unit or a.all)
    if cache and os.path.exists(cache) and os.path.exists(cache + ".rc"):
        with open(cache, encoding="utf-8", errors="replace") as handle:
            sys.stdout.write(handle.read())
        with open(cache + ".rc", encoding="utf-8") as handle:
            rc = int((handle.read().strip() or "0"))
        print("verify: 這一組標籤在 %s 已經跑過同一個 sha —— 用快取,不重跑"
              "(要真的重跑加 --no-cache)" % os.environ.get("AC_RUN_ID", "?"))
        return rc
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
    if not cache:
        r = subprocess.run([sys.executable, "-m", "unittest", *mods], cwd=ROOT)
        return r.returncode
    # 要存快取就得先收下輸出 —— 收下之後**原樣印出來**,一個字都不吃掉
    # (審查 3.1 抓到的正是「失敗輸出被 capture_output 收走」那一種)。
    r = subprocess.run([sys.executable, "-m", "unittest", *mods], cwd=ROOT,
                       capture_output=True, text=True)
    blob = (r.stdout or "") + (r.stderr or "")
    sys.stdout.write(blob)
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as handle:
            handle.write(blob)
        with open(cache + ".rc", "w", encoding="utf-8") as handle:
            handle.write(str(r.returncode))
    except OSError:
        print("verify: 快取寫不出來(不擋回歸)")
    return r.returncode

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
