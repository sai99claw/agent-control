#!/usr/bin/env python3
"""release_job --group gN --sha SHA --tickets 1,2 --out result.json : a landed group's release, in the background.

Started only by ticket_controller, after the group and its tickets are written landed with release
"pending"; the controller collects --out on a later tick. Runs the release once through
core.release.run (throwaway worktree, DEVLOOP_RELEASE_DIR, release_timeout_seconds, the kept log)
and writes {"ok": true|false, "text": <run()'s text>, "sha", "at"} to --out.

SIGTERM, SIGINT or SIGHUP stops it: core.release.run stops the release command's process group,
and --out says {"ok": false, "signal": "<SIGNAME>", "text": "stopped by <SIGNAME>; the release
command was stopped", "sha", "at"}.

Exit: 0 released · 1 the release failed (or could not start) · 2 stopped by a signal.
"""

import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import agents, osx, release  # noqa: E402
from core.config import Config  # noqa: E402

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def write(path, report):
    try:
        osx.write_json(path, dict(report, at=osx.now()))
    except osx.OSFailure as exc:
        print("release_job: %s" % exc, file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--tickets", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    got = []

    def on_signal(signum, frame):
        for sig in STOP_SIGNALS:            # the cleanup in core.release.run runs undisturbed
            signal.signal(sig, signal.SIG_IGN)
        got.append(signum)
        raise agents.Interrupted("signal %d" % signum)
    for sig in STOP_SIGNALS:
        signal.signal(sig, on_signal)
    group = {"id": args.group, "members": [t for t in args.tickets.split(",") if t]}
    try:
        ok, text = release.run(Config(), group, args.sha)
    except agents.Interrupted:
        name = signal.Signals(got[0]).name
        write(args.out, {"ok": False, "signal": name, "sha": args.sha,
                         "text": "stopped by %s; the release command was stopped" % name})
        return 2
    except osx.OSFailure as exc:
        print("release_job: %s" % exc, file=sys.stderr)
        write(args.out, {"ok": False, "text": str(exc), "sha": args.sha})
        return 1
    write(args.out, {"ok": ok, "text": text, "sha": args.sha})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
