#!/usr/bin/env python3
"""回歸層執行器(docs/VERIFICATION.md)。

    python3 scripts/verify.py --list                 # 標籤 → 案例檔
    python3 scripts/verify.py --tag ledger-colour    # 只跑帶這個標籤的案例(可重複 --tag)
    python3 scripts/verify.py                        # 全部回歸
    python3 scripts/verify.py --unit                 # 專案的單元層(board/config.json 的 unit_cmd)
    python3 scripts/verify.py --all                  # 單元 + 回歸
    python3 scripts/verify.py --tag x --no-cache     # 不吃同一輪的快取,真的重跑
    python3 scripts/verify.py --case verify/x/test_y.py   # 直接指名案例檔(可重複;不看標籤)
    python3 scripts/verify.py --tag x --result <out>/result.json   # 另寫一份 result.json(schema 1)

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

## 兩個根(#55;D-071 §1,#159)
程式碼根是**受測的那棵樹**:`verify/` 的案例、被測檔、unittest 的 cwd、`--unit` 的 `unit_cmd`、
快取雜湊裡的 HEAD sha,全部跟 `ROOT` —— `AC_CODE_ROOT`,否則 cwd 的 `git --show-toplevel`。
不從這支檔案的位置推:腳本住在 A 的 clone,受測的樹在專案裡(#159)。AC_ROOT 不是程式碼根
(#55):它只管票、事件與 `reports/` —— 快取檔住在 `event.repo_root()` 那棵樹。land.sh 在票
worktree 裡帶 `AC_ROOT=<主 repo>` 跑閘門時,回歸層跑的是 worktree 那份案例,不是主 repo 那份。

## `--result` 與 `--case`(D-071 §4 `regression` 的內建版)
`--result <path>` 照舊跑同一批案例,但在**同一個程序裡**經 `scripts/adapter-unittest.py` 的
`run_suite` 跑(log 寫在 result 旁邊的 `verify.log`,跑完原樣印到 stdout),再用它的
`write_result` 寫 schema 1 —— 一份 result 只有一個寫法。ids 是案例模組的 unittest id
(`verify.example.test_example.TheExampleHolds.test_true`)。rc 照 adapter:0 / 1 / 86 / 2。
快取在一輪裡時連 result 一起存(`<快取>.result.json`),命中時原樣抄回 `--result`。
`--case <file>`(相對程式碼根或絕對路徑)直接選那幾個檔,不看標籤;檔不在 → rc 3 並指名。
"""
import argparse, ast, hashlib, json, os, re, shutil, subprocess, sys

CONFIG_REL = os.path.join("board", "config.json")


NO_CODE_ROOT = "not inside a project; set AC_CODE_ROOT or run from the tree under test"


def _find_root():
    """程式碼根:`AC_CODE_ROOT`,否則 cwd 的 `git --show-toplevel`(D-071 §1)。**不認 `AC_ROOT`**
    (#55)—— 那是票根;認了它,worktree 裡的閘門跑的就是主 repo 的 `verify/`。也不從這支檔案
    的位置往上找(#159):在 A 的 clone 裡那一找永遠找到 A 自己,而且不報錯。
    兩者都沒有回 None:`main()` 說出來、rc 2,不猜一棵樹。"""
    override = os.environ.get("AC_CODE_ROOT")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
    except OSError:
        return None
    top = done.stdout.strip()
    return top if done.returncode == 0 and top else None


ROOT = _find_root()
VERIFY = os.path.join(ROOT, "verify") if ROOT else None


def head_sha():
    done = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else "no-git"


def cache_path(tags, unit, case_files=()):
    """同一輪、同一組標籤、同一個 sha 的快取檔;不在一輪裡就沒有快取。"""
    ticket, run_id = os.environ.get("AC_TICKET", ""), os.environ.get("AC_RUN_ID", "")
    if not ticket or not run_id:
        return ""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import status
        # 根走 `event.repo_root()`(認得 `AC_ROOT`),不是這支檔案的上兩層:快取是
        # **這一輪的產物**,它要跟票、reports 住在同一顆樹裡,不是住在腳本旁邊。
        where = status.run_dir(status.event.repo_root(), ticket, run_id)
    except Exception:                                          # noqa: BLE001
        where = os.path.join(ROOT, "reports", "t%s" % ticket, run_id)
    # `--case` 只在有給的時候進雜湊:沒給的那一種雜湊與舊版逐字相同(同一輪的舊快取照用)。
    key = hashlib.sha256(("|".join(sorted(tags)) + "@" + head_sha()
                          + ("+unit" if unit else "")
                          + ("".join("+case:" + c for c in sorted(case_files)))
                          ).encode("utf-8")).hexdigest()[:12]
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

def load_adapter():
    """`adapter-unittest.py`(連字號檔名,走 importlib);只有 `--result` 用得到。"""
    import importlib.util
    where = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter-unittest.py")
    spec = importlib.util.spec_from_file_location("adapter_unittest", where)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_with_result(mods, result_path):
    """在這個程序裡經 adapter 跑 `mods`,寫 `result_path`;回 (rc, 給 stdout 的原文)。"""
    import time, unittest
    adapter = load_adapter()
    out_dir = os.path.dirname(os.path.abspath(result_path))
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "verify.log")
    # 程式碼根排第一:這支檔案所在的 `scripts/` 也在 sys.path 上,而 `scripts/verify.py` 這個
    # 模組會蓋過 `verify/` 這個 package,`verify.example.test_example` 就載不起來。
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    suite, aliases, wanted = adapter.load_names(unittest.defaultTestLoader, mods)
    started, clock = adapter.now(), time.monotonic()
    _, rows, suspects = adapter.run_suite(suite, log_path, adapter.threshold(), ROOT, [ROOT],
                                          aliases=aliases, wanted=wanted)
    doc = adapter.result_doc("regression", started, round(time.monotonic() - clock, 3), rows,
                             suspects, os.path.relpath(log_path, out_dir))
    adapter.write_result(result_path, doc)
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        blob = handle.read()
    rc = adapter.exit_code(rows, doc["environment_suspect"]) if rows else 2
    return rc, blob


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--unit", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--result", default="")
    a = ap.parse_args(argv)
    if not ROOT:
        print("verify: " + NO_CODE_ROOT, file=sys.stderr)
        return 2
    case_files = []
    for c in a.case:
        path = os.path.abspath(os.path.join(ROOT, c))
        if not os.path.isfile(path):
            print(f"verify: --case {c}: 檔不在(程式碼根 {ROOT})", file=sys.stderr)
            return 3
        if os.path.relpath(path, ROOT).startswith(".." + os.sep) or not path.endswith(".py"):
            print(f"verify: --case {c}: 不是程式碼根底下的 .py 檔", file=sys.stderr)
            return 2
        if path not in case_files:
            case_files.append(path)
    case_rel = [os.path.relpath(c, ROOT) for c in case_files]
    cache = "" if (a.no_cache or a.list) else cache_path(a.tag, a.unit or a.all, case_rel)
    if cache and os.path.exists(cache) and os.path.exists(cache + ".rc") \
            and (not a.result or os.path.exists(cache + ".result.json")):
        with open(cache, encoding="utf-8", errors="replace") as handle:
            sys.stdout.write(handle.read())
        with open(cache + ".rc", encoding="utf-8") as handle:
            rc = int((handle.read().strip() or "0"))
        if a.result:
            os.makedirs(os.path.dirname(os.path.abspath(a.result)), exist_ok=True)
            shutil.copyfile(cache + ".result.json", a.result)
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
    # `--case` 直接指名,不看標籤(只給 `--case` 時標籤與登記的檢查整段不跑);同時給 `--tag`
    # 就是兩邊的聯集,標籤那一側照舊檢查。
    for c in cases() if (a.tag or not case_files) else []:
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
    if case_files and not a.tag:
        picked = []
    for c in case_files:
        if c not in [p for p, _ in picked]:
            picked.append((c, tags_of(c) or []))
    if bad:
        print("verify: 案例檔不合格:\n  " + "\n  ".join(bad)); return 2
    if a.list:
        for c, t in picked:
            print(f"{os.path.relpath(c, ROOT)}  {t}")
        print(f"verify: {len(picked)} 個案例檔,{len(known)} 個登記標籤"); return 0
    if not picked:
        print(f"verify: 沒有案例符合 {a.tag or '全部'}(登記標籤 {sorted(known)})"); return 3
    mods = [os.path.relpath(c, ROOT)[:-3].replace(os.sep, ".") for c, _ in picked]
    if a.result:
        rc, blob = run_with_result(mods, a.result)
        sys.stdout.write(blob)
        if cache:
            try:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(cache, "w", encoding="utf-8") as handle:
                    handle.write(blob)
                with open(cache + ".rc", "w", encoding="utf-8") as handle:
                    handle.write(str(rc))
                shutil.copyfile(a.result, cache + ".result.json")
            except OSError:
                print("verify: 快取寫不出來(不擋回歸)")
        return rc
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
