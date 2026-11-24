#!/usr/bin/env python3
"""dag_edit : the main session changes a ready ticket's dependencies or group (D-096).

    python3 devloop/dag_edit.py dep N --add M --reason "..." [--by NAME]    # N waits for M
    python3 devloop/dag_edit.py dep N --drop M --reason "..." [--by NAME]
    python3 devloop/dag_edit.py move N --to gK|new --reason "..." [--by NAME]

One pool write (core/dagedit.py checks it on the data it reads); the last stdout line is the group
chain after the change. A refusal is one stderr line, exit 2, nothing written. CLI only (`actl dag`).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import chain, dagedit, osx  # noqa: E402
from core.config import Config, pool_for  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dep")
    p.add_argument("id")
    one = p.add_mutually_exclusive_group(required=True)
    one.add_argument("--add")
    one.add_argument("--drop")
    p.add_argument("--reason", default=None)
    p.add_argument("--by", default="main-session")
    p = sub.add_parser("move")
    p.add_argument("id")
    p.add_argument("--to", required=True)
    p.add_argument("--reason", default=None)
    p.add_argument("--by", default="main-session")
    args = ap.parse_args(argv)
    change = ({"to": args.to} if args.cmd == "move" else
              {"add": args.add} if args.add is not None else {"drop": args.drop})
    out = {}

    def write(tx):
        out["refused"] = dagedit.apply(tx, args.id, args.by, args.reason, **change)
        out["chain"] = chain.order(tx.data)
    try:
        pool = pool_for(Config())
        what = ("moved to %s" % args.to if args.cmd == "move" else
                "depends on #%s" % args.add if args.add is not None else "no longer depends on #%s" % args.drop)
        pool.update(write, "dag: #%s %s (%s)" % (args.id, what, args.by))
    except osx.OSFailure as exc:
        print("dag: %s" % str(exc).replace("\n", " "), file=sys.stderr)
        return 2
    if out["refused"]:
        print("dag: %s" % out["refused"], file=sys.stderr)
        return 2
    print("#%s %s" % (args.id, what))
    print("chain: " + ", ".join(out["chain"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
