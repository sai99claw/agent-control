#!/bin/sh
# 整理票自動起角色表的整理者 —— 一個 session(#122,D-043 / D-049 ②;原 #87 A39–A41 的兩模型)。
#
#   sh scripts/consolidate-memory.sh <票號>
#
# `memory.py check` 開出整理票之後,以前要主線自己照 `templates/dispatch-consolidator.md`
# 開 session、貼派工文 —— 一張沒有人去派的整理票,與沒有開票長得一樣。主線開場
# (`new-session.sh main …`)對每張符合條件的票叫這一支一次。
#
# ## 只起一個:租約排他
# 只對 role=consolidator、state=Ready、沒有有效 lease 的票起。先 `ticket.py set <n> lease`
# 取租約(holder=consolidate-memory@<pid>,until=現在+逾時秒數),帶 `--expect-state-version`
# —— 兩個主線同時開場,兩邊讀到的都是同一版,只有先寫的那一個收得進去;另一個不起。
# 拿到了才把票轉 Running。
#
# ## 誰整理:角色表 consolidator 鏈,一次一個(D-043 單一高階模型路)
# 模型只由 `role-models.py resolve --role consolidator` 選;票上的 `model` 不帶進去(#131 A10
# 只套與票 role 相同的 worker 鏈),`memory.consolidator(s)` 不再讀(#122 A3,與表衝突就警告)。
# 前一個候選**額度用完 / 不可用**(provider 錯誤信封,或 CLI 不在)才換下一個;一般退出非零、
# 逾時不換。表上的鏈只有 fable:它用不了就是整串耗盡,停下發 decision,不改派別的模型。
# 命令由 `ticket.py agent-command` 決定 —— 與 auto-fix 同一支:`codex:*` 走 `codex exec`,
# 其餘把 config 的 `worker.command` 換 `--model`。派工文 = `rules.py pack consolidator` +
# 範本逐格填好,從 stdin 餵。每次起都發 agent.start / agent.done(或 agent.failed)、寫一列
# cost(role=consolidator),model 欄記實際起的那個;交出的討論檔路徑與模型寫進票的
# `attempt_history` —— `memory.py consolidate` 拿它認單一整理者的那一份討論檔。
#
# ## 起不來就問主線,不留在 Running
# 規則包產不出來、範本找不到、session 退出非零、逾時、整串候選耗盡(含命令缺):票轉
# NeedsDecision、owner=main、租約清空,收件匣一頁 decision,標題帶票號與原因。
#
# ## 退出碼
#   0 起完了(或不該起:不是 Ready、租約在別人手上)   1 起不來 / session 失敗(已送 decision)
#   2 用法錯、找不到票、不是整理票
set -u
_ac_root() {
    _d=$(cd "$(dirname "$0")" && pwd); _i=0
    while [ $_i -lt 5 ]; do
        [ -f "$_d/board/config.json" ] && { echo "$_d"; return; }
        _d=$(dirname "$_d"); _i=$((_i+1))
    done
    cd "$(dirname "$0")/.." && pwd
}
ROOT=${AC_ROOT:-$(_ac_root)}
AC=${AC_CONTROL_DIR:-$(cd "$(dirname "$0")" && pwd)}
export AC_ROOT=$ROOT AC_CONTROL_DIR=$AC

[ $# -eq 1 ] || { echo "用法:sh scripts/consolidate-memory.sh <票號>" >&2; exit 2; }

python3 - "$AC" "$ROOT" "${1#\#}" <<'PY'
import json, os, shlex, shutil, signal, subprocess, sys, time
from datetime import date, datetime, timedelta

ac, root, ident = sys.argv[1:4]
sys.path.insert(0, ac)
import event   # noqa: E402
import ticket  # noqa: E402

ROLE = "consolidator"
TEMPLATE = os.path.join("templates", "dispatch-consolidator.md")
FALLBACK_KINDS = ("quota_exhausted", "provider_unavailable")


def say(text, err=False):
    (sys.stderr if err else sys.stdout).write("consolidate-memory: %s\n" % text)


def now():
    return datetime.now().astimezone()


def tk(*args):
    return subprocess.run([sys.executable, os.path.join(ac, "ticket.py"), *args],
                          capture_output=True, text=True)


def role_models(*args):
    return subprocess.run([sys.executable, os.path.join(ac, "role-models.py"), "--table",
                           os.path.join(root, "board", "role-models.json"), *args],
                          capture_output=True, text=True)


def lease_is_live(lease):
    if not isinstance(lease, dict) or not lease.get("until"):
        return False
    try:
        return datetime.fromisoformat(str(lease["until"])) > now()
    except ValueError:
        return True          # 讀不懂的租約當它還在:搶錯了是兩個 session 寫同一份檔


try:
    row = ticket.load(ident)
except (OSError, ValueError) as exc:
    say("找不到票 #%s —— %s" % (ident, exc), err=True)
    sys.exit(2)
if row.get("role") != ROLE:
    say("#%s 的 role=%s,不是整理票 —— 不起" % (ident, row.get("role")), err=True)
    sys.exit(2)
if row.get("state") != "Ready":
    say("#%s state=%s,不是 Ready —— 不起" % (ident, row.get("state")))
    sys.exit(0)
if lease_is_live(row.get("lease")):
    say("#%s 的租約在 %s 手上(到 %s)—— 不起" % (ident, row["lease"].get("holder"),
                                            row["lease"].get("until")))
    sys.exit(0)

conf = event.config(root)
worker = conf.get("worker") if isinstance(conf.get("worker"), dict) else {}
mine = row.get("worker") if isinstance(row.get("worker"), dict) else {}
timeout = mine.get("timeout_seconds") or worker.get("timeout_seconds") or 3600
holder = "consolidate-memory@%d" % os.getpid()
until = (now() + timedelta(seconds=float(timeout))).isoformat(timespec="seconds")
got = tk("set", ident, "lease", json.dumps({"holder": holder, "until": until}),
         "--expect-state-version", str(row.get("state_version") or 0))
if got.returncode != 0:
    say("#%s 的租約沒拿到(別的開場先拿了)—— 不起:%s" % (ident, got.stderr.strip()))
    sys.exit(0)
tk("set", ident, "state", "Running")
say("#%s 取得租約(%s,到 %s),起整理 session" % (ident, holder, until))

memory = conf.get("memory") if isinstance(conf.get("memory"), dict) else {}
# 舊設定與表衝突才警告(判讀只在 role-models.py legacy 一處)。
old = role_models("legacy", "--config", os.path.join(root, "board", "config.json"))
for line in old.stdout.splitlines():
    if "衝突 memory." in line:
        say("%s;整理者只照角色表 consolidator 鏈,一次遷移:python3 scripts/role-models.py "
            "legacy --write" % line.split(": ", 1)[-1], err=True)
base = worker.get("command") or "claude -p --model opus"
run_id = os.environ.get("AC_RUN_ID") or "%s-%d" % (now().strftime("%Y%m%d-%H%M%S"), os.getpid())
rundir = os.path.join(root, conf.get("reports_dir") or "reports", "t%s" % ident, run_id)
os.makedirs(rundir, exist_ok=True)
target = next(iter(row.get("in_scope") or row.get("allowed_write_paths") or []), "")
today = date.today().isoformat()
history = list(row.get("attempt_history") or [])


def short(model):
    return model.split(":")[-1]


def discussion_of(model):
    return os.path.join("discussions", "%s-memory-%s.md" % (today, short(model)))


def fail(reason):
    if history != list(row.get("attempt_history") or []):
        tk("set", ident, "attempt_history", json.dumps(history, ensure_ascii=False))
    tk("set", ident, "state", "NeedsDecision")
    tk("set", ident, "owner", "main")
    tk("set", ident, "lease", "null")
    title = "#%s 整理 session 起不來:%s" % (ident, reason)
    subprocess.run([sys.executable, os.path.join(ac, "inbox.py"), "post", "--ticket", ident,
                    "--run-id", run_id, "--kind", "decision", "--state", title,
                    "--what", "整理票轉 NeedsDecision、租約清空;修好原因(命令、角色表 consolidator "
                              "鏈、額度、逾時秒數)後把票改回 Ready,下次主線開場會再起",
                    "--where", os.path.relpath(rundir, root)], check=False)
    say(title, err=True)
    sys.exit(1)


try:
    with open(os.path.join(root, TEMPLATE), encoding="utf-8") as handle:
        template = handle.read()
except OSError:
    fail("範本 %s 找不到" % TEMPLATE)

try:
    text = open(os.path.join(root, target), encoding="utf-8").read() if target else ""
except OSError:
    text = ""
front_cap = [line.split(":", 1)[1].strip() for line in text.splitlines()[:10]
             if line.startswith("cap_chars:")]
cap = front_cap[0] if front_cap else str(memory.get("cap_chars") or 2000)
chars = len(text.split("\n---\n", 1)[1]) if text.startswith("---\n") and "\n---\n" in text \
    else len(text)


def descendants(top):
    done = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True)
    kids = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [top]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def kill_tree(proc):
    # 逾時收整棵樹(與 auto-fix.sh 的 run_agent 同一手):自開 session 的後代 killpg 碰不到。
    tree = descendants(proc.pid)
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, None)):
        tree += [pid for pid in descendants(proc.pid) if pid not in tree]
        for target_pid in [proc.pid] + tree:
            try:
                (os.killpg if target_pid == proc.pid else os.kill)(target_pid, sig)
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


def run(model, effort, cmd, dispatch, log_path):
    talk = discussion_of(model)
    # 起 session 之前先記:session 在裡面跑 `memory.py consolidate --discussion <這一份>`,
    # 那時 launcher_recorded 就要讀得到這一筆;退出後沒有檔再改成 None。
    record = {"model": model, "discussion": talk, "at": now().isoformat(timespec="seconds")}
    history.append(record)
    tk("set", ident, "attempt_history", json.dumps(history, ensure_ascii=False))
    env = dict(os.environ, AC_TICKET=ident, AC_ROLE=ROLE, AC_DISPATCH=dispatch,
               AC_MODEL=model, AC_DISCUSSION=talk)
    event.emit("agent.start", ticket=ident, role=ROLE, model=model, run_id=run_id,
               agent="consolidate-memory")
    started = time.time()
    try:
        with open(dispatch, encoding="utf-8") as stdin, open(log_path, "wb") as log:
            proc = subprocess.Popen(cmd, shell=True, cwd=root, env=env, stdin=stdin,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=float(timeout))
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                rc = 124
    except OSError:
        rc = 127
    event.emit("agent.done" if rc == 0 else "agent.failed", ticket=ident, role=ROLE,
               model=model, run_id=run_id, rc=rc, agent="consolidate-memory")
    outcome = "handed-in" if rc == 0 else "timeout" if rc == 124 else "error"
    cost = tk("cost", ident, "--role", ROLE, "--round", "1", "--model", model,
              "--by", "consolidate-memory.sh", "--from-envelope", log_path,
              "--wall-seconds", str(int(round(time.time() - started))), "--outcome", outcome,
              "--run-id", run_id, "--effort", effort or "")
    if cost.returncode != 0:
        say("#%s 的 cost 寫不進票(%s)—— 不擋流程" % (ident, cost.stderr.strip()), err=True)
    if not os.path.isfile(os.path.join(root, talk)):
        record["discussion"] = None
    return rc


# 一次一個候選;只有額度 / 不可用證據才往下一格(D-049 ②),一般失敗停在這一格。
exhausted, skipped = None, []
while True:
    flags = [] if exhausted is None else ["--exhausted-index", str(exhausted)]
    picked = role_models("resolve", "--role", ROLE, *flags)
    if picked.returncode == 2:
        fail("角色表選不出整理者:%s" % (picked.stderr.strip() or "rc=2"))
    choice = json.loads(picked.stdout)
    if picked.returncode == 3:
        fail("整理者候選整串耗盡(%s)—— 不改派別的模型" % ";".join(skipped or [choice["reason"]]))
    model, tool, effort = choice["model"], choice["tool"], choice.get("effort") or ""
    index = choice["candidate_index"]
    made = tk("agent-command", "--model", model, "--tool", tool, "--base", base, "--swap-model",
              "--effort", effort)
    if made.returncode != 0:
        fail("命令組不出來(%s):%s" % (model, made.stderr.strip()))
    cmd = made.stdout.strip()
    head = next((tok for tok in shlex.split(cmd) if "=" not in tok), "")
    if not shutil.which(head):
        # CLI 不在 = 這個候選此刻不能用(與 rc=127 同一種),標記它、換下一格。
        skipped.append("%s 命令缺(%s 不在 PATH)" % (model, head))
        say("候選第 %d 格 %s 命令缺(%s 不在 PATH)—— 換下一格" % (index, model, head), err=True)
        exhausted = index
        continue
    pack = subprocess.run([sys.executable, os.path.join(ac, "rules.py"), "pack", ROLE,
                           "--model", model], capture_output=True, text=True,
                          env=dict(os.environ, AC_TICKET=ident))
    if pack.returncode != 0:
        fail("規則包產不出來(%s):%s" % (model, pack.stderr.strip().splitlines()[0]
                                         if pack.stderr.strip() else "rc=%d" % pack.returncode))
    fill = {
        "<票號>": ident,
        "<票庫路徑>": os.path.relpath(event.tickets_dir(root), root),
        "<要整理的檔>": target,
        "<N>": str(chars),
        "<cap>": cap,
        "<date>": today,
        "<你的模型>": short(model),
        "<主線 / session 名>": "consolidate-memory.sh(主線開場起的)",
        "<候選檔>": os.path.join("discussions", "%s-memory-%s.candidate.md"
                                 % (today, os.path.splitext(os.path.basename(target))[0])),
    }
    body = template
    for key, value in fill.items():
        body = body.replace(key, value)
    dispatch = os.path.join(rundir, "dispatch-consolidator-%s.md" % short(model))
    with open(dispatch, "w", encoding="utf-8") as handle:
        handle.write(pack.stdout.rstrip("\n") + "\n\n" + body)
    log_path = os.path.join(rundir, "consolidator-%s.log" % short(model))
    say("#%s 照角色表起整理者 %s(tool %s;%s)" % (ident, model, tool, choice["reason"]))
    rc = run(model, effort, cmd, dispatch, log_path)
    if rc == 0:
        break
    if rc == 124:
        fail("逾時(%s 超過 %s 秒)" % (model, timeout))
    judged = role_models("classify", "--evidence", log_path, "--rc", str(rc))
    kind, detail = "other", ""
    if judged.returncode == 0:
        verdict = json.loads(judged.stdout)
        kind, detail = verdict["kind"], verdict["detail"]
    if kind not in FALLBACK_KINDS:
        fail("%s 退出 %s%s" % (model, rc, "(%s)" % detail if kind == "auth_config" else ""))
    skipped.append("%s %s(%s)" % (model, kind, detail))
    say("候選第 %d 格 %s 用不了(%s)—— 換下一格" % (index, model, detail), err=True)
    exhausted = index

tk("set", ident, "attempt_history", json.dumps(history, ensure_ascii=False))
tk("set", ident, "lease", "null")
say("#%s 整理 session 回來了(%s)—— 討論檔與模型記在 attempt_history" % (ident, model))
PY
