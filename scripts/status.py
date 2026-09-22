#!/usr/bin/env python3
"""閘門與落地的狀態檔 — `docs/WORKFLOW.md` §狀態檔(D-010、D-014)。

    scripts/status.py start  --ticket 7 --kind gate --run-id 20260921-101500-42 \
                             --base-sha abc1234 --worktree /path/wt --round 2
    scripts/status.py phase  --ticket 7 --run-id … --phase merge --rc 0
    scripts/status.py failures --log gate.log        # 印可以單獨重跑的案例 id,一行一個
    scripts/status.py run-tests --root . --log gate.log --suspect-file gate.log.env-suspect.json \
                             --mode discover              # 跑測試,環境壞了當場中止(rc=86)
    scripts/status.py done   --ticket 7 --run-id … --rc 1 --log gate.log \
                             [--suspected-flaky test_x.Case.test_y …]
    scripts/status.py show   --ticket 7 [--run-id …] [--runs]
    scripts/status.py rundir --ticket 7 --run-id …   # 這一輪的目錄(回歸快取住那裡)

寫的是 `<reports_dir>/t<票號>/<run_id>/status.json`(`board/config.json` 的
`reports_dir`,預設 `reports/`),**一輪一個目錄、不覆寫**,log 另外複製一份進
`logs/` 留著 —— land 成功後會把 worktree 收掉,而 log 就住在那裡面(D-014 §2)。

## 這一份要回答的三個問題
**「跑完了沒、錯了什麼、去哪看」** —— 這三句是使用者 2026-09-20 的原話。在它之前,
一個 agent 要知道閘門怎麼了,只能把整份 log 讀進上下文(一次幾十萬 token),或者
用 `Monitor` / `sleep` 迴圈輪詢背景工作(每看一次 = 整份上下文重送一輪)。
**一份 20 行的 JSON 取代那兩種做法。**

## 為什麼 `state` 與 `rc` 是兩格
`{"state": "running"}` 與「跑完了但 rc 還沒寫」長得一樣,而那正是要分開的兩件事:
**還在跑**要等,**跑完了而且紅了**要修。所以 `start` 先寫 `running`(那一刻還沒有
rc),`done` 才覆寫成 `done` 並帶 `rc`。一份沒有 `finished` 的 `done` 是壞掉的檔,
不是「剛好跑很快」。

## 為什麼 failures 要帶 excerpt 而不是只給 log 路徑
只給路徑的話,下一個人還是得把整份 log 讀進來 —— 那就回到原本的成本。excerpt 上限
20 行:夠認出是哪一條斷言倒了,不夠讓人偷懶把整份貼進去。

## 為什麼多了 `repair_context`(2026-09-21 外部審查)
> 「目前 status 是結果摘要,不是新 worker 可直接開工的交接包。」

下一輪換一個**新的** worker,它手上只有這一份檔。少了 base_sha、票面快照、副本位置、
patch 路徑與雜湊、第幾輪、上一輪排除過什麼、怎麼重現,它就得回頭翻對話或猜檔案位置
—— 那一趟比整份 log 還貴。

## 環境壞了要當場停(`run-tests`,rc=86)
2026-09-22 #620:safaridriver 起的 Safari 行程掛了幾小時後 storage 壞掉,26 條 safari
案例全紅、訊息同一形狀。閘門把整段跑完才說話 —— 兩輪落地白跑。所以測試不再由 shell
直接叫,改由 `run-tests` 在同一個程序裡跑:每條紅例取「引擎 + 去掉數字與 id 的訊息」
當形狀,同一形狀連紅達 `board/config.json` 的 `environment_fail_fast_threshold`
(預設 8)就**中止那一段**並回 rc=86。一次環境故障最多浪費 N 條案例,不是整段。

**這一關在 flake 判定之前。** 環境壞掉時那一段是被中止的,紅榜本來就不完整;而 flake
判定要做的事(每條紅例單跑 5 次 + 原順序整組再跑)正是在壞掉的環境裡最貴、最沒有意義
的那件事 —— 它會把「環境壞了」重新量成「這些案例都是真紅」。所以 rc=86 的時候
`flake_rerun` 與回歸層都不跑,狀態檔直接寫 `env_suspect`。

## 自動 flake 與順序污染
單跑綠**不等於**那條紅是假的:第一條測試污染共用狀態、第二條檢查乾淨狀態時,整組
必紅而單跑必綠。只有每條紅例連續單跑達設定門檻、原順序整組也達門檻全綠,才標
`flaky: auto`;單跑全綠但整組紅則標 `order_dependent`,原始失敗留在紅榜、rc 不動。
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event  # noqa: E402  共用 repo 根與 board/config.json 的判讀

EXCERPT_LINES = 20
DEFAULT_REPORTS = "reports"
FLAKY_LEDGER = "flaky.jsonl"
DEFAULT_FLAKY_THRESHOLD = 3
DEFAULT_ENV_FAIL_FAST_THRESHOLD = 8
# 86 不是隨便挑的:0 是綠、1 是 unittest 的紅、2/3 是 gate.sh 自己的用法(參數錯、
# 對不到模組)。要一個不會與那幾個撞的碼,呼叫者才分得出「紅了」與「環境壞了」。
ENV_SUSPECT_RC = 86
PHASES = ("gate", "merge", "push", "verify", "docs", "apply")

# `FAIL: test_x (test_mod.Case.test_x)` / `ERROR: test_x (test_mod.Case)` /
# subTest 的兩種形狀 —— 方括號的 `[engine=firefox]`(自己組的標籤)與**原生的圓括號
# 參數** `(engine='firefox')`。第二種一定要認:2026-09-21 的外部審查實測,舊版把整
# 行尾巴吃進 qualifier,解析出來的 case 是
# `__main__.NativeSubtest.test_engine) (engine='firefox'.test_engine` —— 一個餵不回
# `python3 -m unittest` 的 id,而**餵不回去的紅榜與沒有紅榜一樣**。
HEAD = re.compile(r"^(FAIL|ERROR):\s+(\S+)\s*(.*?)\s*$")
DOTTED = re.compile(r"^[A-Za-z_][\w.]*$")
DIVIDER = re.compile(r"^(=|-){20,}\s*$")
FILE_LINE = re.compile(r'^\s*File "([^"]+)", line (\d+)')
# 瀏覽器那一族會在案例名或輸出裡帶 `engine=chrome` / `engine='firefox'`;沒有就留空,不猜。
ENGINE = re.compile(r"engine\s*[=:]\s*['\"]?([A-Za-z0-9_.-]+)")
CLOSERS = {"(": ")", "[": "]"}


def normalized_failure_message(error):
    """訊息的**形狀**:去掉數字與 id。

    同一個環境故障每一條的訊息只差流水號與 case id(`localStorage id=ab-1 empty
    after 101 seconds`)。逐字比會判成 26 種不同的紅,於是「環境壞了」與「26 個真
    bug」長得一樣 —— 那正是要分開的兩件事。
    """
    message = str(error)
    message = re.sub(r"\b(?:id|case_id|request_id)\s*[=:]\s*[^\s,;)}\]]+",
                     "id=<id>", message, flags=re.IGNORECASE)
    message = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<id>", message,
                     flags=re.IGNORECASE)
    return re.sub(r"\d+", "<n>", message).strip()


class EnvironmentResult(unittest.TextTestResult):
    """同一引擎、同形訊息連紅達門檻就中止那一段。

    只算**連續**的:中間夾一條綠或一條別的形狀就歸零。「一路都在倒同一句」是環境的
    徵狀,「散落幾條同句」是程式的 bug,這兩件事不該走同一條路。
    """

    def __init__(self, *args, threshold=DEFAULT_ENV_FAIL_FAST_THRESHOLD, **kwargs):
        super().__init__(*args, **kwargs)
        self.threshold = threshold
        self.last_shape = None
        self.streak = 0
        self.environment_suspect = None

    def _observe(self, test, err):
        if err is None:
            self.last_shape = None
            self.streak = 0
            return
        detail = "%s\n%s" % (test, err[1])
        found = ENGINE.search(detail)
        if not found:
            # 認不出引擎就不猜:這一條可能只是一個普通的紅。
            self.last_shape = None
            self.streak = 0
            return
        engine = found.group(1)
        message = normalized_failure_message(err[1])
        shape = (engine.lower(), message)
        self.streak = self.streak + 1 if shape == self.last_shape else 1
        self.last_shape = shape
        if self.streak >= self.threshold:
            self.environment_suspect = {
                "engine": engine,
                "message_shape": message,
                "count": self.streak,
                "threshold": self.threshold,
            }
            # 兩個旗標都要設:`shouldStop` 停的是**下一條測試方法**,而 subTest 的
            # 迴圈跑在同一個方法裡面 —— 只設它,那 12 條 subTest 會整組跑完才停。
            # `failfast` 才是 subTest 自己看的那一格(實測見 EVIDENCE M1)。
            self.failfast = True
            self.shouldStop = True

    def addSuccess(self, test):
        self._observe(test, None)
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._observe(test, err)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._observe(test, err)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        self._observe(subtest, err)
        super().addSubTest(test, subtest, err)


def cmd_run_tests(args):
    """跑測試並在環境可疑時中止。**shell 不再自己叫 unittest** —— 要看到一條一條的
    結果就得待在同一個程序裡;等 log 寫完再解析等於等整段跑完,那正是這張票要省下的。

    log 的寫法與舊版的 shell 一樣:先整份寫檔,再回退出碼。判綠只看 rc。
    """
    root = os.path.abspath(args.root)
    threshold = int(event.config(root).get("environment_fail_fast_threshold")
                    or DEFAULT_ENV_FAIL_FAST_THRESHOLD)
    with open(args.suspect_file, "w", encoding="utf-8") as handle:
        handle.write("")
    # cwd 照舊版兩條路各自的樣子:discover 在 repo 根、指名模組在 `tests/`。
    where = root if args.mode == "discover" else os.path.join(root, "tests")
    os.chdir(where)
    sys.path.insert(0, os.path.join(root, "tests"))
    loader = unittest.defaultTestLoader
    if args.mode == "discover":
        suite = loader.discover("tests", pattern="test_*.py")
    else:
        suite = loader.loadTestsFromNames(args.names)
    with open(args.log, "w", encoding="utf-8") as stream:
        runner = unittest.TextTestRunner(
            stream=stream, verbosity=2,
            resultclass=lambda *items, **kw: EnvironmentResult(
                *items, threshold=threshold, **kw))
        result = runner.run(suite)
    if result.environment_suspect:
        with open(args.suspect_file, "w", encoding="utf-8") as handle:
            json.dump(result.environment_suspect, handle, ensure_ascii=False)
            handle.write("\n")
        return ENV_SUSPECT_RC
    return 0 if result.wasSuccessful() else 1


def reports_dir(root):
    return os.path.join(root, event.config(root).get("reports_dir") or DEFAULT_REPORTS)


def ticket_dir(root, ticket):
    return os.path.join(reports_dir(root), "t%s" % ticket)


def run_dir(root, ticket, run_id):
    return os.path.join(ticket_dir(root, ticket), run_id)


def path_for(root, ticket, run_id):
    return os.path.join(run_dir(root, ticket, run_id), "status.json")


def runs_of(root, ticket):
    """這張票跑過哪幾輪,舊的在前。run_id 是時間戳開頭,所以字典序就是時間序。"""
    where = ticket_dir(root, ticket)
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return []
    return [name for name in names
            if os.path.exists(os.path.join(where, name, "status.json"))]


def latest_run(root, ticket):
    rows = runs_of(root, ticket)
    return rows[-1] if rows else ""


def new_run_id():
    return "%s-%d" % (datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_of(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def file_ref(path):
    """patch 那一格要帶雜湊。**一個路徑答不出「是不是同一份 patch」** —— 下一輪的
    worker 拿到的可能是被重套過、被重生過清單的另一份。"""
    if not path:
        return None
    return {"path": path, "sha256": sha256_of(path),
            "exists": os.path.exists(path)}


def split_groups(rest):
    """把 `(a.b.c) (engine='firefox')` 拆成 `['a.b.c', "engine='firefox'"]`。

    巢狀要算深度:subTest 的參數裡可以有 `size=(1, 2)`,而用非貪婪的正規表示式去抓
    會在第一個 `)` 斷掉,把剩下的塞進下一格。
    """
    groups, buf, depth, opener = [], [], 0, ""
    for ch in rest:
        if depth == 0:
            if ch in CLOSERS:
                depth, opener, buf = 1, ch, []
            continue
        if ch == opener:
            depth += 1
        elif ch == CLOSERS[opener]:
            depth -= 1
            if depth == 0:
                groups.append("".join(buf))
                continue
        buf.append(ch)
    return groups


def dotted(case, qualifier):
    """單獨重跑要用的 id。unittest 的括號裡是完整路徑(3.11 起連方法名都在裡面),
    括號外只有方法名 —— **用括號裡那一份**,它才餵得回 `python3 -m unittest`。"""
    if not qualifier:
        return case
    qualifier = qualifier.strip()
    if qualifier.endswith("." + case) or qualifier == case:
        return qualifier
    return "%s.%s" % (qualifier, case)


def parse_head(line):
    """一行 `FAIL:` / `ERROR:` → (kind, case_id, subtest 標籤) 或 None。"""
    found = HEAD.match(line)
    if not found:
        return None
    kind, case, rest = found.group(1), found.group(2), found.group(3)
    labels = [g.strip() for g in split_groups(rest)]
    qualifier = ""
    if labels and DOTTED.match(labels[0]):
        qualifier = labels.pop(0)
    return kind, dotted(case, qualifier), ", ".join(x for x in labels if x)


def parse_failures(log_path):
    """從一份 unittest 輸出裡挑出紅的案例。

    只認 `^(FAIL|ERROR):` 開頭那幾行 —— 不去猜「看起來像錯誤」的行。猜出來的那幾筆
    會讓 `failures` 變成一份沒有人敢信的清單,而**一份不能信的紅榜與沒有紅榜一樣**。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    index = 0
    while index < len(lines):
        head = parse_head(lines[index])
        if not head:
            index += 1
            continue
        kind, case, label = head
        body = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if parse_head(line) or line.startswith("Ran ") or line.startswith("OK") \
                    or line.startswith("FAILED"):
                break
            if DIVIDER.match(line):
                # 第一條分隔線是標頭與 traceback 之間的那一條;第二條是這一筆的結尾。
                if body:
                    break
                index += 1
                continue
            body.append(line)
            index += 1
        while body and not body[-1].strip():
            body.pop()
        where, line_no = "", 0
        for line in body:
            spot = FILE_LINE.match(line)
            if spot:
                where, line_no = spot.group(1), int(spot.group(2))
        engine = ""
        for line in [label, case] + body:
            hit = ENGINE.search(line)
            if hit:
                engine = hit.group(1)
                break
        out.append({
            "case": case,
            "kind": kind,
            # subTest 的參數。單獨重跑餵回去的是 `case`(subTest 沒辦法單獨叫),
            # 所以標籤另外留一格,讓人看得出紅的是哪一個子情境。
            "subtest": label,
            "file": where,
            "line": line_no,
            "engine": engine,
            "log": log_path,
            "excerpt": "\n".join(body[:EXCERPT_LINES]),
            "suspected_flaky": False,
        })
    return out


# ------------------------------------------------------------------ 讀寫


def write(root, ticket, run_id, data):
    path = path_for(root, ticket, run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def read(root, ticket, run_id):
    try:
        with open(path_for(root, ticket, run_id), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def keep_logs(root, ticket, run_id, logs):
    """把 log 複製進這一輪的目錄。**land 成功後 worktree 會被收掉**,而 log 就在
    那裡面 —— 只留路徑等於留了一個明天不存在的路徑(審查:status 的生命週期)。"""
    kept = []
    where = os.path.join(run_dir(root, ticket, run_id), "logs")
    for log in logs:
        if not os.path.exists(log):
            kept.append({"path": log, "kept": "", "note": "跑完時這個檔不在"})
            continue
        os.makedirs(where, exist_ok=True)
        target = os.path.join(where, os.path.basename(log))
        stem, ext = os.path.splitext(target)
        serial = 1
        while os.path.exists(target):
            target = "%s-%d%s" % (stem, serial, ext)
            serial += 1
        with open(log, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
        kept.append({"path": log, "kept": os.path.relpath(target, root), "note": ""})
    return kept


def ticket_snapshot(root, ticket):
    """票面快照:下一輪的 worker 不必再去讀票檔,也認得出票在它開工後被改過。"""
    path = os.path.join(event.tickets_dir(root), "%s.json" % ticket)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    keep = ("id", "subject", "objective", "state", "state_version", "attempt",
            "base_sha", "allowed_write_paths", "acceptance", "verify",
            "objections", "review", "retry_limit")
    return {key: data[key] for key in keep if key in data}


def repair_context(root, args):
    env = {}
    for item in args.env or []:
        key, _, value = item.partition("=")
        if key:
            env[key] = value
    return {
        "version": 1,
        "base_sha": args.base_sha or "",
        "ticket": ticket_snapshot(root, args.ticket),
        "worktree": args.worktree or "",
        "patch": file_ref(args.patch),
        "verify_patch": file_ref(args.verify_patch),
        "round": args.round,
        "prev_evidence": args.prev_evidence or "",
        "repro": {"cmd": args.repro or "", "cwd": args.cwd or ""},
        "env": env,
    }


# ------------------------------------------------------------------ 子指令


def cmd_start(args):
    root = event.repo_root()
    run_id = args.run_id or new_run_id()
    data = {"state": "running", "run_id": run_id, "kind": args.kind,
            "ticket": args.ticket, "sha": args.sha or "", "started": now(),
            "finished": None, "rc": None, "note": "",
            "report": args.report or "", "logs": [], "kept_logs": [],
            "phases": [], "failures": [], "suspected_flaky": [],
            "repair_context": repair_context(root, args)}
    path = write(root, args.ticket, run_id, data)
    print("status: %s" % os.path.relpath(path, root))
    print("run_id: %s" % run_id)
    return 0


def cmd_phase(args):
    """gate / merge / push 各記一筆。**混成一格的 `done` 會說謊**:land 舊版在 merge
    與 push 之前就寫 `done, rc=0`,所以「合進去了」與「只是閘門綠」長得一樣。"""
    root = event.repo_root()
    run_id = os.environ.get("AC_RUN_ID") or args.run_id
    if not run_id:
        sys.stderr.write("status: 警告:沒有 --run-id 或 AC_RUN_ID,改用最新一輪\n")
        run_id = latest_run(root, args.ticket)
    if not run_id:
        sys.stderr.write("status: #%s 沒有任何一輪可以記 phase\n" % args.ticket)
        return 2
    data = read(root, args.ticket, run_id)
    if not data:
        sys.stderr.write("status: #%s 的 %s 讀不到\n" % (args.ticket, run_id))
        return 2
    data.setdefault("phases", []).append({
        "phase": args.phase, "rc": args.rc, "at": now(),
        "note": args.note or ""})
    write(root, args.ticket, run_id, data)
    print("status: #%s %s %s rc=%d" % (args.ticket, run_id, args.phase, args.rc))
    return 0


def flaky_ledger_path(root):
    return os.path.join(reports_dir(root), FLAKY_LEDGER)


def record_flakes(root, ticket, run_id, rows):
    """持久事件帳。**舊版把單跑的證據丟掉**,所以同一條案例每天疑似一次,累計次數
    永遠是 1,沒有人會去修它的不穩定(審查:flake 只被看見,沒有追到底)。"""
    if not rows:
        return []
    path = flaky_ledger_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    for row in rows:
        lines.append(json.dumps({
            "at": now(), "ticket": ticket, "run_id": run_id,
            "case": row["case"], "kind": row["kind"],
            "subtest": row.get("subtest", ""), "log": row.get("log", ""),
            "classification": row.get("flaky") or "suspected",
        }, ensure_ascii=False) + "\n")
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, "".join(lines).encode("utf-8"))
    finally:
        os.close(handle)
    counts = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)["case"]
                except (ValueError, KeyError):
                    continue
                counts[case] = counts.get(case, 0) + 1
    except OSError:
        return []
    threshold = event.config(root).get("flaky_threshold") or DEFAULT_FLAKY_THRESHOLD
    hot = []
    for row in rows:
        seen = counts.get(row["case"], 0)
        if seen >= threshold:
            hot.append((row["case"], seen))
    for case, seen in hot:
        try:
            event.emit("decision.asked", ticket=ticket, case=case, seen=seen,
                       note="疑似 flaky 累計 %d 次(門檻 %d)—— 已開修復票"
                            % (seen, threshold))
        except Exception:                                  # noqa: BLE001
            pass
        print("status: %s 疑似 flaky 累計 %d 次(門檻 %d)—— 已發 decision.asked"
              % (case, seen, threshold))
        made = open_flaky_ticket(root, case, seen, threshold,
                                 row.get("log", ""), ticket)
        if made:
            print("status: 自動開了修不穩定的票 #%s(role=verifier)—— "
                  "`python3 scripts/ticket.py show %s`" % (made, made))
    return hot


def open_flaky_ticket(root, case, seen, threshold, log, from_ticket):
    """達門檻就**自動開一張修復票**(D-015)。

    以前只發 `decision.asked` 停在那裡,理由是「自動開票會在門檻誤判時生出一堆沒人
    認領的票」。實際跑起來相反:`decision.asked` 沒有 owner、沒有驗收,而**一則沒有
    人認領的事件比一張沒有人認領的票更容易被滑過去** —— 票至少會出現在 `list --open`
    裡,每個 session 開場都看得到。

    誤判那一半用**同一條案例只開一張**擋住:票上留 `flaky_case`,還開著就不再開。
    要完全關掉的人設 `board/config.json` 的 `flaky_auto_ticket: false`。
    """
    conf = event.config(root)
    if conf.get("flaky_auto_ticket") is False:
        return ""
    try:
        import ticket as ticket_mod                        # noqa: PLC0415
    except ImportError:
        return ""
    try:
        for row in ticket_mod.load_all():
            if str(row.get("flaky_case") or "") == case and ticket_mod.is_open(row):
                return ""
    except OSError:
        return ""
    model = (conf.get("routing") or {}).get("verify") or "sonnet"
    out = io.StringIO()
    argv = [
        "--subject", "把 %s 修穩 —— 疑似 flaky 累計 %d 次" % (case, seen),
        "--objective",
        "同一條案例連續 %d 次單跑綠、整組紅(門檻 %d)。找出它依賴的共用狀態或時序,"
        "修到同一組在原順序下跑十次都綠。" % (seen, threshold),
        "--acceptance", "原順序整組連跑 10 次,%s 沒有一次紅" % case,
        "--acceptance", "說得出它不穩的原因(共用狀態 / 時序 / 外部資源),寫進 EVIDENCE",
        "--acceptance", "不是靠放寬斷言或加 retry 讓它綠的",
        "--in-scope", case,
        "--allowed-write-path", "verify/*",
        "--allowed-write-path", "tests/*",
        "--role", "verifier", "--model", str(model), "--tool", "claude-code",
        "--state", "Ready",
    ]
    try:
        rc = ticket_mod.cmd_create(argv, stdout=out)
    except (OSError, ValueError, RuntimeError):
        return ""
    if rc != 0:
        return ""
    found = re.search(r"#(\S+)", out.getvalue())
    ident = found.group(1) if found else ""
    if not ident:
        return ""
    try:
        with ticket_mod.Lock():
            data = ticket_mod.load(ident)
            data["flaky_case"] = case
            data["flaky_seen"] = seen
            data["flaky_log"] = log
            data["flaky_from_ticket"] = from_ticket
            ticket_mod.save(data)
    except (OSError, ValueError, RuntimeError):
        pass
    return ident


def cmd_done(args):
    root = event.repo_root()
    run_id = os.environ.get("AC_RUN_ID") or args.run_id
    if not run_id:
        sys.stderr.write("status: 警告:沒有 --run-id 或 AC_RUN_ID,改用最新一輪\n")
        run_id = latest_run(root, args.ticket) or new_run_id()
    before = read(root, args.ticket, run_id)
    failures = []
    for log in args.log or []:
        failures.extend(parse_failures(log))
    suspect_names = set(args.suspected_flaky or [])
    auto_names = set(args.auto_flaky or [])
    order_names = set(args.order_dependent or [])
    for row in failures:
        row["suspected_flaky"] = row["case"] in suspect_names
        row["flaky"] = "auto" if row["case"] in auto_names else ""
        row["order_dependent"] = row["case"] in order_names
    # **留在 failures 裡**,不搬走:單跑綠只降級成「疑似」,紅還是紅(D-014 §1)。
    suspected = [row for row in failures if row["suspected_flaky"]]
    automatic = [row for row in failures if row["flaky"] == "auto"]
    failures = [row for row in failures if row["flaky"] != "auto"]
    environment = {}
    if args.environment_log:
        try:
            with open(args.environment_log, encoding="utf-8") as handle:
                environment = json.load(handle)
        except (OSError, ValueError):
            environment = {}
    data = {
        "state": args.state,
        "run_id": run_id,
        "kind": args.kind or before.get("kind") or "",
        "ticket": args.ticket,
        "sha": args.sha or before.get("sha") or "",
        "started": before.get("started") or now(),
        "finished": now(),
        "rc": args.rc,
        "note": args.note or before.get("note") or "",
        "report": args.report or before.get("report") or "",
        "logs": list(args.log or []),
        # 重跑的原始輸出**不再丟掉**(審查:「重跑輸出直接丟掉」)。它不進 failures
        # 的解析 —— 同一條紅解析兩遍會讓紅榜看起來有兩條 —— 但檔要留著。
        "extra_logs": list(args.extra_log or []),
        "kept_logs": keep_logs(root, args.ticket, run_id,
                               list(args.log or []) + list(args.extra_log or [])),
        "phases": before.get("phases") or [],
        "failures": failures,
        "suspected_flaky": suspected,
        "environment_suspect": environment,
        "auto_flaky": automatic,
        "flaky": "auto" if automatic else "",
        "order_dependent": bool(order_names),
        "order_dependent_cases": sorted(order_names),
        "repair_context": before.get("repair_context")
                          or repair_context(root, args),
    }
    path = write(root, args.ticket, run_id, data)
    record_flakes(root, args.ticket, run_id, automatic or suspected)
    if args.state == "env_suspect":
        # 事件發不出去不該讓狀態檔白寫:那份 JSON 已經在磁碟上了,而它才是接手的人
        # 要讀的東西。出聲,不改 rc(同 gate.sh 對狀態檔的態度)。
        try:
            event.emit("env.suspect", ticket=args.ticket, run_id=run_id,
                       engine=environment.get("engine", ""),
                       message_shape=environment.get("message_shape", ""),
                       count=environment.get("count", ""),
                       threshold=environment.get("threshold", ""))
        except Exception:                                  # noqa: BLE001
            sys.stderr.write("status: env.suspect 事件發不出去(狀態檔仍已寫入)\n")
    print("status: %s rc=%d 紅 %d 條(疑似 flaky %d 條,自動 flaky %d 條)"
          % (os.path.relpath(path, root), args.rc, len(failures), len(suspected),
             len(automatic)))
    return 0


def cmd_failures(args):
    """印出可以單獨重跑的案例 id,一行一個。**呼叫它的是 shell 迴圈** —— 所以這裡
    只印 id,不印別的;多印一個字,那個迴圈就會拿它當案例名去跑。"""
    seen = []
    for log in args.log or []:
        for row in parse_failures(log):
            if row["case"] not in seen:
                seen.append(row["case"])
    for case in seen:
        print(case)
    return 0


def cmd_rundir(args):
    """這一輪的目錄。**回歸快取住在這裡**(`verify-<tags 雜湊>.log`),所以呼叫者
    要問得出路徑 —— 自己拼一次 `reports/t<n>/<run_id>` 的人會拼錯 `reports_dir`。"""
    root = event.repo_root()
    run_id = args.run_id or latest_run(root, args.ticket)
    if not run_id:
        sys.stderr.write("status: #%s 還沒有任何一輪\n" % args.ticket)
        return 2
    sys.stdout.write(run_dir(root, args.ticket, run_id) + "\n")
    return 0


def cmd_show(args):
    root = event.repo_root()
    if args.runs:
        rows = runs_of(root, args.ticket)
        if not rows:
            sys.stderr.write("status: #%s 一輪都還沒跑過\n" % args.ticket)
            return 2
        for name in rows:
            data = read(root, args.ticket, name)
            sys.stdout.write("%s  %s  %s  rc=%s\n"
                             % (name, data.get("kind", "?"), data.get("state", "?"),
                                data.get("rc")))
        return 0
    run_id = args.run_id or latest_run(root, args.ticket)
    data = read(root, args.ticket, run_id) if run_id else {}
    if not data:
        sys.stderr.write("status: #%s 還沒有狀態檔(%s)—— 這一輪閘門根本沒開跑?\n"
                         % (args.ticket,
                            os.path.relpath(ticket_dir(root, args.ticket), root)))
        return 2
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


def add_context_flags(parser):
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--worktree", default="")
    parser.add_argument("--patch", default="")
    parser.add_argument("--verify-patch", default="")
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--prev-evidence", default="")
    parser.add_argument("--repro", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--env", action="append", default=[])


def main(argv):
    parser = argparse.ArgumentParser(prog="status.py", add_help=True)
    subs = parser.add_subparsers(dest="verb")

    start = subs.add_parser("start")
    start.add_argument("--ticket", required=True)
    start.add_argument("--run-id", default="")
    start.add_argument("--kind", default="gate")
    start.add_argument("--sha", default="")
    start.add_argument("--report", default="")
    add_context_flags(start)
    start.set_defaults(run=cmd_start)

    phase = subs.add_parser("phase")
    phase.add_argument("--ticket", required=True)
    phase.add_argument("--run-id", default="")
    phase.add_argument("--phase", required=True, choices=PHASES)
    phase.add_argument("--rc", type=int, required=True)
    phase.add_argument("--note", default="")
    phase.set_defaults(run=cmd_phase)

    done = subs.add_parser("done")
    done.add_argument("--ticket", required=True)
    done.add_argument("--run-id", default="")
    done.add_argument("--kind", default="")
    done.add_argument("--sha", default="")
    done.add_argument("--rc", type=int, required=True)
    done.add_argument("--state", choices=("done", "env_suspect"), default="done")
    done.add_argument("--environment-log", default="")
    done.add_argument("--note", default="")
    done.add_argument("--report", default="")
    done.add_argument("--log", action="append", default=[])
    done.add_argument("--extra-log", action="append", default=[])
    done.add_argument("--suspected-flaky", action="append", default=[])
    done.add_argument("--auto-flaky", action="append", default=[])
    done.add_argument("--order-dependent", action="append", default=[])
    add_context_flags(done)
    done.set_defaults(run=cmd_done)

    run_tests = subs.add_parser("run-tests")
    run_tests.add_argument("--root", required=True)
    run_tests.add_argument("--log", required=True)
    run_tests.add_argument("--suspect-file", required=True)
    run_tests.add_argument("--mode", choices=("discover", "names"), required=True)
    run_tests.add_argument("names", nargs="*")
    run_tests.set_defaults(run=cmd_run_tests)

    fails = subs.add_parser("failures")
    fails.add_argument("--log", action="append", default=[])
    fails.set_defaults(run=cmd_failures)

    rundir = subs.add_parser("rundir")
    rundir.add_argument("--ticket", required=True)
    rundir.add_argument("--run-id", default="")
    rundir.set_defaults(run=cmd_rundir)

    show = subs.add_parser("show")
    show.add_argument("--ticket", required=True)
    show.add_argument("--run-id", default="")
    show.add_argument("--runs", action="store_true")
    show.set_defaults(run=cmd_show)

    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
