#!/usr/bin/env python3
"""Main-repo check around a dispatched round (#153, D-064 / D-065): one copy, two callers.

`auto-fix.sh` (`run_agent`, headless worker / verifier / batch worker: reject = exit 8) and
`apply.sh` (rounds prepared with `auto-fix.sh --prepare`, compared on receipt: reject = exit 7)
both call this module; neither walks porcelain, hard_block.dirs or `ls-remote` itself.

A snapshot holds four things, and `compare` checks the same four against now:
1. main HEAD (40 hex) and the branch it is on. Unchanged -> ok. Moved forward while the events
   file has a land.pass, or a land.fail with stage=push, stamped at or after the snapshot ->
   recorded, not blocked; the record lists the commits in old..new that no in-window land event
   covers (its own `main_before..sha`, or its sha alone) - a set difference, no chain walk, never
   a reject (D-065 (1)). A path on hard_block.paths changed in old..new -> reject, land or not
   (D-065 (2)). Moved with no such event, or no longer a descendant (reset / rewrite) -> reject.
2. origin refs (`git ls-remote origin`, bounded by a timeout): any ref created / moved / deleted
   -> reject, except the main branch (and HEAD) moving while a land is in the window. No origin
   -> one line, not compared; ls-remote failing at either end -> one line 'push not compared'
   (D-065 Q4). Other local branches are not compared (D-065 Q3).
3. the main repo working tree (`git status --porcelain=v1 -z --untracked-files=all` plus a content
   hash per listed path, so a dirty file written again shows): a difference -> reject, except the
   control plane (tickets / events / answers / reports as config resolves them), the agent's own
   copy / worktree, and paths under guard_main_paths (default tickets/ docs/ memory/), which are
   recorded. A path on hard_block.paths is never exempt (D-065 (2)).
4. every file under hard_block.dirs (~ expanded, relative to the main repo): any difference ->
   reject, no exception.
Only what is listed is looked at: files ignored by .gitignore and directories outside
hard_block.dirs are not claimed as seen.

CLI (exit 0 ok, 7 reject, 2 usage):
  main_guard.py snapshot --root <control root> [--repo <main repo>] [--exempt <dir>]… --out <file>
  main_guard.py compare --root <control root> [--repo <main repo>] [--label <who>] <dir>…
      compares every <dir>/main-snapshot.json, writes <dir>/main-compare.json beside each one;
      none found -> one line that the comparison was not made, exit 0 (D-065 Q5).
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ticket as ticket_mod  # noqa: E402

SNAPSHOT = "main-snapshot.json"
COMPARE = "main-compare.json"
CONTROL_PLANE = (("tickets_dir", "tickets"), ("events_file", "board/events.jsonl"),
                 ("answers_file", "board/answers.jsonl"), ("reports_dir", "reports"))
MAIN_PATHS_DEFAULT = ["tickets/", "docs/", "memory/"]   # = land.sh DOCS_PREFIXES
LS_REMOTE_TIMEOUT = 20
PUSH_FAILED = "(push 失敗)"


def now_ts():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_conf(root):
    try:
        with open(os.path.join(root, "board", "config.json"), encoding="utf-8") as handle:
            conf = json.load(handle)
    except (OSError, ValueError):
        conf = {}
    return conf if isinstance(conf, dict) else {}


def settings(root, repo=None, exempt=()):
    """What to watch and what to let through, from the control root's board/config.json."""
    conf = load_conf(root)
    repo = repo or root
    block = conf.get("hard_block") if isinstance(conf.get("hard_block"), dict) else {}
    dirs = block.get("dirs") or []
    if not isinstance(dirs, list) or not all(isinstance(item, str) for item in dirs):
        sys.stderr.write("main-guard: hard_block.dirs 不是字串 list(%r)—— 這一次只查主 repo\n"
                         % (dirs,))
        dirs = []
    mains = conf.get("guard_main_paths", MAIN_PATHS_DEFAULT)
    if not isinstance(mains, list) or not all(isinstance(item, str) for item in mains):
        sys.stderr.write("main-guard: guard_main_paths 不是字串 list(%r)—— 這一次照預設 %s\n"
                         % (mains, MAIN_PATHS_DEFAULT))
        mains = MAIN_PATHS_DEFAULT
    return {
        "repo": repo,
        "exempt": [os.path.realpath(os.path.join(root, str(conf.get(key) or default)))
                   for key, default in CONTROL_PLANE]
                  + [os.path.realpath(path) for path in exempt if path],
        "dirs": [os.path.realpath(os.path.join(repo, os.path.expanduser(item))) for item in dirs],
        "events": os.path.join(root, str(conf.get("events_file") or "board/events.jsonl")),
        # a prefix ends at a slash: `docs` and `docs/` are the same and neither covers `docs-x/`.
        "mains": [item.strip("/") + "/" for item in mains if item.strip("/")],
        "main_branch": str(conf.get("main_branch") or "main"),
        "hard_paths": ticket_mod.hard_block(root)["paths"],
    }


def file_digest(path):
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    if os.path.isdir(path):
        return "dir"
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        return "unreadable:%s" % exc.strerror
    return digest.hexdigest()[:16]


def git_out(repo, *args, timeout=None):
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def text(raw):
    return (raw or b"").decode("utf-8", "surrogateescape").strip()


def remote_refs(repo):
    if git_out(repo, "remote", "get-url", "origin") is None:
        return {"state": "absent"}
    try:
        done = subprocess.run(["git", "-C", repo, "ls-remote", "origin"], capture_output=True,
                              timeout=LS_REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"state": "error", "why": "git ls-remote origin timed out after %ds"
                % LS_REMOTE_TIMEOUT}
    except OSError as exc:
        return {"state": "error", "why": "git ls-remote origin: %s" % exc}
    if done.returncode != 0:
        said = text(done.stderr).splitlines()
        return {"state": "error", "why": "git ls-remote origin rc=%d%s"
                % (done.returncode, (": " + said[-1]) if said else "")}
    refs = {}
    for line in text(done.stdout).splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name] = sha
    return {"state": "ok", "refs": refs}


def snapshot(conf):
    repo = conf["repo"]
    top = text(git_out(repo, "rev-parse", "--show-toplevel")) or None
    snap = {"version": 1, "ts": now_ts(), "top": top, "head": None, "branch": None,
            "tree": None, "dirs": {}, "origin": {"state": "absent"},
            "exempt": conf["exempt"]}
    if top:
        snap["head"] = text(git_out(repo, "rev-parse", "-q", "--verify", "HEAD")) or "(no commit)"
        snap["branch"] = text(git_out(repo, "symbolic-ref", "-q", "HEAD")) or "(detached)"
        listed = git_out(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if listed is not None:
            snap["tree"] = {}
            entries = listed.split(b"\0")
            at = 0
            while at < len(entries):
                entry = entries[at].decode("utf-8", "surrogateescape")
                at += 1
                if len(entry) < 4:
                    continue
                if entry[0] in "RC":
                    at += 1   # rename / copy: the next field is the source path
                path = os.path.join(top, entry[3:])
                snap["tree"][path] = "%s %s" % (entry[:2].strip(), file_digest(path))
        snap["origin"] = remote_refs(repo)
    for where in conf["dirs"]:
        files = {}
        for base, subdirs, names in os.walk(where):
            for name in names + [item for item in subdirs
                                 if os.path.islink(os.path.join(base, item))]:
                files[os.path.join(base, name)] = file_digest(os.path.join(base, name))
        snap["dirs"][where] = files
    return snap


def parse_ts(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def lands_since(events, since):
    """land.pass, or land.fail with stage=push, stamped at or after `since` (D-065 (1))."""
    start = parse_ts(since)
    try:
        with open(events, "rb") as handle:
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        if '"land.' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not (row.get("kind") == "land.pass" or (
                row.get("kind") == "land.fail" and row.get("stage") == "push")):
            continue
        when = parse_ts(row.get("ts"))
        try:
            if start is not None and when is not None and when >= start:
                found.append(row)
        except TypeError:   # naive vs aware: unknown, so not counted
            continue
    return found


def rev_list(repo, spec):
    return text(git_out(repo, "rev-list", spec)).split()


def head_rows(before, after, conf, lands, out):
    """HEAD: rejects go to out['rejects'], the record to out['recorded']."""
    repo, old, new = before["top"], before["head"], after["head"] or ""
    if before["branch"] != after["branch"]:
        out["rejects"].append({"path": "HEAD(分支)", "before": before["branch"],
                               "after": after["branch"]})
        return
    if old == new:
        return
    forward = git_out(repo, "merge-base", "--is-ancestor", old, new) is not None
    if not forward:
        out["rejects"].append({"path": "HEAD", "before": old, "after": new,
                               "why": "snapshot HEAD is no longer an ancestor of main"})
        return
    changed = text(git_out(repo, "diff", "--name-only", old, new)).splitlines()
    for name in changed:
        if ticket_mod.first_match(name, conf["hard_paths"]) is not None:
            out["rejects"].append({"path": os.path.join(repo, name), "before": old[:12],
                                   "after": new[:12],
                                   "why": "changed in %s..%s, on hard_block.paths"
                                          % (old[:12], new[:12])})
    if not lands:
        out["rejects"].insert(0, {"path": "HEAD", "before": old, "after": new,
                                  "why": "main moved with no main-line land in the window"})
        return
    covered = set()
    for row in lands:
        sha, prior = str(row.get("sha") or ""), str(row.get("main_before") or "")
        if sha and prior:
            covered.update(rev_list(repo, "%s..%s" % (prior, sha)))
        elif sha:
            covered.update(text(git_out(repo, "rev-parse", "-q", "--verify",
                                        sha + "^{commit}")).split())
    listed = text(git_out(repo, "log", "--reverse", "--format=%H %s", "%s..%s" % (old, new)))
    strays = []
    for line in listed.splitlines():
        sha, _, subject = line.partition(" ")
        if sha and sha not in covered:
            strays.append({"sha": sha[:12], "subject": subject})
    out["non_land_commits"] = strays
    stamps = ", ".join("stamp=%s%s" % (row.get("stamp") or "?",
                                        PUSH_FAILED if row.get("kind") == "land.fail" else "")
                       for row in lands)
    out["recorded"].append("HEAD %s..%s moved with main-line land(s) in the window (%s); "
                           "non-land commits: %s"
                           % (old[:12], new[:12], stamps,
                              ", ".join("%s %s" % (row["sha"], row["subject"]) for row in strays)
                              or "none"))


def origin_rows(before, after, conf, lands, out):
    was, now = before.get("origin") or {}, after.get("origin") or {}
    if "absent" in (was.get("state"), now.get("state")):
        out["notes"].append("origin: no 'origin' remote - refs not compared")
        return
    if "error" in (was.get("state"), now.get("state")):
        out["notes"].append("push not compared: %s"
                            % (was.get("why") if was.get("state") == "error" else now.get("why")))
        return
    allowed = {"refs/heads/" + conf["main_branch"], "HEAD"} if lands else set()
    old, new = was.get("refs") or {}, now.get("refs") or {}
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name, "absent"), new.get(name, "absent")
        if a == b:
            continue
        if name in allowed:
            out["recorded"].append("origin %s %s -> %s moved with a main-line land in the window"
                                   % (name, a[:12], b[:12]))
        else:
            out["rejects"].append({"path": "origin " + name, "before": a, "after": b})


def compare(before, conf, exempt=()):
    """Snapshot vs now. Returns {'rejects', 'main_side', 'recorded', 'notes', 'non_land_commits'}."""
    after = snapshot(dict(conf, dirs=list(before.get("dirs") or {})))
    out = {"rejects": [], "main_side": [], "recorded": [], "notes": [], "non_land_commits": []}
    skip = [os.path.realpath(path) for path in list(before.get("exempt") or []) + conf["exempt"]
            + list(exempt) if path]
    top = before.get("top")

    def exempted(path):
        real = os.path.realpath(path)
        return any(real == item or real.startswith(item + os.sep) for item in skip)

    def rel(path):
        return path[len(top) + 1:].replace(os.sep, "/") \
            if top and path.startswith(top + os.sep) else None

    if before.get("tree") is not None and after.get("tree") is not None:
        for path in sorted(set(before["tree"]) | set(after["tree"])):
            was, now = before["tree"].get(path, "clean"), after["tree"].get(path, "clean")
            if was == now:
                continue
            row = {"path": path, "before": was, "after": now}
            name = rel(path)
            if name is not None and ticket_mod.first_match(name, conf["hard_paths"]) is not None:
                out["rejects"].append(dict(row, why="on hard_block.paths"))
            elif exempted(path):
                continue
            elif name is not None and any(name.startswith(item) for item in conf["mains"]):
                out["main_side"].append(row)
            else:
                out["rejects"].append(row)
    elif top:
        out["notes"].append("主 repo 的 git status 拍不到 —— 工作樹這一次沒有比")
    lands = lands_since(conf["events"], before.get("ts")) if top else []
    if top:
        head_rows(before, after, conf, lands, out)
        origin_rows(before, after, conf, lands, out)
    for where, files in (before.get("dirs") or {}).items():
        now_files = after["dirs"].get(where, {})
        for path in sorted(set(files) | set(now_files)):
            if files.get(path, "absent") != now_files.get(path, "absent"):
                out["rejects"].append({"path": path, "before": files.get(path, "absent"),
                                       "after": now_files.get(path, "absent")})
    return out


def describe(row):
    return "%s (before %s -> after %s%s)" % (row["path"], row.get("before"), row.get("after"),
                                             "; " + row["why"] if row.get("why") else "")


def cmd_snapshot(args):
    conf = settings(args.root, args.repo, args.exempt)
    snap = snapshot(conf)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(snap, handle, ensure_ascii=False, indent=2)
    origin = snap["origin"]
    sys.stderr.write("main-guard: snapshot %s (HEAD %s%s)\n"
                     % (args.out, (snap["head"] or "?")[:12],
                        "; push will not be compared: " + origin["why"]
                        if origin.get("state") == "error" else
                        "; no origin" if origin.get("state") == "absent" else ""))
    return 0


def cmd_compare(args):
    label = args.label
    seen, found = set(), []
    for where in args.dirs:
        path = os.path.join(where, SNAPSHOT)
        if where and os.path.isfile(path) and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            found.append(path)
    if not found:
        print("%s: main-repo comparison not made - no %s in %s (hand-dispatched round, D-065 Q5)"
              % (label, SNAPSHOT, ", ".join(item for item in args.dirs if item) or "(none)"))
        return 0
    conf = settings(args.root, args.repo)
    rc = 0
    for path in found:
        try:
            with open(path, encoding="utf-8") as handle:
                before = json.load(handle)
        except (OSError, ValueError) as exc:
            sys.stderr.write("%s: %s unreadable (%s) - not received\n" % (label, path, exc))
            return 7
        result = compare(before, conf)
        record = dict(result, snapshot=path, compared_at=now_ts(), snapshot_ts=before.get("ts"),
                      verdict="reject" if result["rejects"] else "ok")
        out = os.path.join(os.path.dirname(path), COMPARE)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        for line in result["recorded"] + ["%s (recorded, not blocked)" % describe(row)
                                          for row in result["main_side"]] + result["notes"]:
            print("%s: main-repo: %s" % (label, line))
        if result["rejects"]:
            sys.stderr.write("%s: main repo changed while the round ran: %s - not received "
                             "(%d item(s); record %s)\n"
                             % (label, describe(result["rejects"][0]), len(result["rejects"]), out))
            rc = 7
    return rc


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main_guard.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("snapshot", "compare"):
        one = sub.add_parser(name)
        one.add_argument("--root", required=True)
        one.add_argument("--repo", default=None)
        if name == "snapshot":
            one.add_argument("--exempt", action="append", default=[])
            one.add_argument("--out", required=True)
        else:
            one.add_argument("--label", default="main-guard")
            one.add_argument("dirs", nargs="*")
    args = parser.parse_args(argv)
    return cmd_snapshot(args) if args.cmd == "snapshot" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
