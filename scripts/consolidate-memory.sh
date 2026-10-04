#!/bin/sh
# 整理票自動起兩個模型的 session(#87 A39–A41,原 #84;D-007 / D-013)。
#
#   sh scripts/consolidate-memory.sh <票號>
#
# `memory.py check` 開出整理票之後,以前要主線自己照 `templates/dispatch-consolidator.md`
# 開兩個 session、各貼一次 —— 一張沒有人去派的整理票,與沒有開票長得一樣。主線開場
# (`new-session.sh main …`)對每張符合條件的票叫這一支一次。
#
# ## 只起一組:租約排他
# 只對 role=consolidator、state=Ready、沒有有效 lease 的票起。先 `ticket.py set <n> lease`
# 取租約(holder=consolidate-memory@<pid>,until=現在+逾時秒數),帶 `--expect-state-version`
# —— 兩個主線同時開場,兩邊讀到的都是同一版,只有先寫的那一個收得進去;另一個不起。
# 拿到了才把票轉 Running。
#
# ## 兩個 session、各自的模型、同一條起命令的路
# `board/config.json` 的 `memory.consolidators` 每個模型起一個 session(平行:第二段要互讀)。
# 命令由 `ticket.py agent-command` 決定 —— 與 auto-fix 同一支:`codex:*` 走 `codex exec`,
# 其餘把 config 的 `worker.command` 換 `--model`。派工文 = `rules.py pack consolidator` +
# 範本逐格填好,從 stdin 餵。每個 session 發 agent.start / agent.done(或 agent.failed),
# model 欄記實際起的那個;交出的討論檔路徑與模型寫進票的 `attempt_history` ——
# `memory.py consolidate` 拿它對討論檔頭的 `model:`,對不上就拒收。
#
# ## 起不來就問主線,不留在 Running
# 命令缺(PATH 上找不到)、規則包產不出來、範本找不到、session 退出非零、逾時:票轉
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
import json, os, shlex, shutil, signal, subprocess, sys, threading, time
from datetime import date, datetime, timedelta

ac, root, ident = sys.argv[1:4]
sys.path.insert(0, ac)
import event   # noqa: E402
import ticket  # noqa: E402

ROLE = "consolidator"
TEMPLATE = os.path.join("templates", "dispatch-consolidator.md")


def say(text, err=False):
    (sys.stderr if err else sys.stdout).write("consolidate-memory: %s\n" % text)


def now():
    return datetime.now().astimezone()


def tk(*args):
    return subprocess.run([sys.executable, os.path.join(ac, "ticket.py"), *args],
                          capture_output=True, text=True)


def lease_is_live(lease):
    if not isinstance(lease, dict) or not lease.get("until"):
        return False
    try:
        return datetime.fromisoformat(str(lease["until"])) > now()
    except ValueError:
        return True          # 讀不懂的租約當它還在:搶錯了是兩組 session 寫同一份檔


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
models = [str(m) for m in (memory.get("consolidators") or []) if m] \
    or ([str(memory["consolidator"])] if memory.get("consolidator") else [])
base = worker.get("command") or "claude -p --model opus"
run_id = os.environ.get("AC_RUN_ID") or "%s-%d" % (now().strftime("%Y%m%d-%H%M%S"), os.getpid())
rundir = os.path.join(root, conf.get("reports_dir") or "reports", "t%s" % ident, run_id)
os.makedirs(rundir, exist_ok=True)
target = next(iter(row.get("in_scope") or row.get("allowed_write_paths") or []), "")
today = date.today().isoformat()


def short(model):
    return model.split(":")[-1]


def discussion_of(model):
    return os.path.join("discussions", "%s-memory-%s.md" % (today, short(model)))


def fail(reason):
    tk("set", ident, "state", "NeedsDecision")
    tk("set", ident, "owner", "main")
    tk("set", ident, "lease", "null")
    title = "#%s 整理 session 起不來:%s" % (ident, reason)
    subprocess.run([sys.executable, os.path.join(ac, "inbox.py"), "post", "--ticket", ident,
                    "--run-id", run_id, "--kind", "decision", "--state", title,
                    "--what", "整理票轉 NeedsDecision、租約清空;修好原因(命令、模型名單、"
                              "逾時秒數)後把票改回 Ready,下次主線開場會再起",
                    "--where", os.path.relpath(rundir, root)], check=False)
    say(title, err=True)
    sys.exit(1)


if len(models) < 2:
    fail("memory.consolidators 只有 %d 個模型 —— 整理要兩個不同的模型(D-013)" % len(models))
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

sessions = []
for model in models:
    made = tk("agent-command", "--model", model,
              "--tool", "codex" if model.startswith("codex:") else "claude-code",
              "--base", base, "--swap-model")
    if made.returncode != 0:
        fail("命令組不出來(%s):%s" % (model, made.stderr.strip()))
    cmd = made.stdout.strip()
    head = next((tok for tok in shlex.split(cmd) if "=" not in tok), "")
    if not shutil.which(head):
        fail("命令缺 %s(%s 不在 PATH)" % (model, head))
    pack = subprocess.run([sys.executable, os.path.join(ac, "rules.py"), "pack", ROLE,
                           "--model", model], capture_output=True, text=True,
                          env=dict(os.environ, AC_TICKET=ident))
    if pack.returncode != 0:
        fail("規則包產不出來(%s):%s" % (model, pack.stderr.strip().splitlines()[0]
                                         if pack.stderr.strip() else "rc=%d" % pack.returncode))
    other = [m for m in models if m != model]
    fill = {
        "<票號>": ident,
        "<票庫路徑>": os.path.relpath(event.tickets_dir(root), root),
        "<memory/model/x.md 或 memory/role/x.md>": target,
        "<要整理的檔>": target,
        "<N>": str(chars),
        "<cap>": cap,
        "<date>": today,
        "<你的模型>": short(model),
        "<對方模型>": ", ".join(short(m) for m in other),
        "<模型>": model,
        "<模型 A>": short(models[0]),
        "<模型 B>": short(models[1]),
        "<主線 / session 名>": "consolidate-memory.sh(主線開場起的)",
        "<先寫完的那一位>": "兩位中先寫完第二段的那一位",
        "<候選檔>": os.path.join("discussions", "%s-memory-%s.candidate.md"
                                 % (today, os.path.splitext(os.path.basename(target))[0])),
    }
    body = template
    for key, value in fill.items():
        body = body.replace(key, value)
    dispatch = os.path.join(rundir, "dispatch-consolidator-%s.md" % short(model))
    with open(dispatch, "w", encoding="utf-8") as handle:
        handle.write(pack.stdout.rstrip("\n") + "\n\n" + body)
    sessions.append((model, cmd, dispatch,
                     os.path.join(rundir, "consolidator-%s.log" % short(model))))


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


results = {}


def run(model, cmd, dispatch, log_path):
    env = dict(os.environ, AC_TICKET=ident, AC_ROLE=ROLE, AC_DISPATCH=dispatch,
               AC_MODEL=model, AC_DISCUSSION=discussion_of(model))
    event.emit("agent.start", ticket=ident, role=ROLE, model=model, run_id=run_id,
               agent="consolidate-memory")
    try:
        with open(dispatch, encoding="utf-8") as stdin, open(log_path, "wb") as log:
            proc = subprocess.Popen(cmd, shell=True, cwd=root, env=env, stdin=stdin,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                results[model] = proc.wait(timeout=float(timeout))
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                results[model] = 124
    except OSError:
        results[model] = 127


threads = [threading.Thread(target=run, args=one) for one in sessions]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

history = list(row.get("attempt_history") or [])
for model, _, _, _ in sessions:
    rc = results.get(model, 127)
    event.emit("agent.done" if rc == 0 else "agent.failed", ticket=ident, role=ROLE,
               model=model, run_id=run_id, rc=rc, agent="consolidate-memory")
    talk = discussion_of(model)
    history.append({"model": model,
                    "discussion": talk if os.path.isfile(os.path.join(root, talk)) else None,
                    "at": now().isoformat(timespec="seconds")})
tk("set", ident, "attempt_history", json.dumps(history, ensure_ascii=False))

late = [model for model, _, _, _ in sessions if results.get(model) == 124]
if late:
    fail("逾時(%s 超過 %s 秒)" % (", ".join(late), timeout))
broken = [(model, results.get(model)) for model, _, _, _ in sessions if results.get(model)]
if broken:
    fail("、".join("%s 退出 %s" % (model, rc) for model, rc in broken))
tk("set", ident, "lease", "null")
say("#%s 兩個整理 session 都回來了(%s)—— 討論檔與模型記在 attempt_history"
    % (ident, ", ".join(models)))
PY
