#!/usr/bin/env python3
"""run_tests <ids...> : run test ids on the current tree, for agents (worker, verifier, fixer).

Goes through the project's test interface like everything else, but keeps its output in the
agent's own run directory ($DEVLOOP_RUN_DIR), never in the project checkout. Prints a short
summary. Exit: 0 green · 1 red · 86 environment not usable · 2 could not measure.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import osx, testrun  # noqa: E402
from core.config import Config  # noqa: E402

CODES = {testrun.GREEN: 0, testrun.RED: 1, testrun.ENV: 86, testrun.UNMEASURED: 2}


def main(argv=None):
    ids = list(sys.argv[1:] if argv is None else argv)
    if not ids:
        print("usage: run_tests.py <test id>...", file=sys.stderr)
        return 2
    try:
        cfg = Config()
        base = os.environ.get("DEVLOOP_RUN_DIR") or tempfile.gettempdir()
        out = tempfile.mkdtemp(prefix="tests-", dir=osx.makedirs(base))
        result = testrun.run(cfg, os.getcwd(), "run", ids, keep_dir=out)
    except osx.OSFailure as exc:
        print("run_tests: %s" % exc, file=sys.stderr)
        return 2
    print(result.summary())
    print("(log: %s)" % out)
    return CODES[result.status]


if __name__ == "__main__":
    sys.exit(main())
