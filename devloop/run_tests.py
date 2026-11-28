#!/usr/bin/env python3
"""run_tests <ids...> : run test ids on the current tree, for agents (worker, verifier, fixer).

Goes through the project's test interface like everything else, but keeps its output in the
agent's own run directory ($DEVLOOP_RUN_DIR), never in the project checkout. Prints a short
summary. Exit: 0 green · 1 red · 86 environment not usable · 2 could not measure.

It tries once, without waiting, to take a test run (core/slots.take_test_run: outside an agent's
slot a machine slot; with devloop.regression_exclusive also, inside an agent too, no regression
holding the machine); when it gets none it runs nothing, says "machine busy" on stderr and exits 2.
The test run gets DEVLOOP_SLOT when it took a slot, so anything devloop-started inside it takes no
slot of its own.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import osx, slots, testrun  # noqa: E402
from core.config import Config  # noqa: E402

CODES = {testrun.GREEN: 0, testrun.RED: 1, testrun.ENV: 86, testrun.UNMEASURED: 2}


def main(argv=None):
    ids = list(sys.argv[1:] if argv is None else argv)
    if not ids:
        print("usage: run_tests.py <test id>...", file=sys.stderr)
        return 2
    slot = None
    try:
        cfg = Config()
        slot = slots.take_test_run(cfg, time.time())
        if slot is None:
            print("run_tests: machine busy (a full test run holds the machine or every machine "
                  "slot is taken); nothing was run", file=sys.stderr)
            return 2
        if slot.path:
            os.environ[slots.ENV] = slot.path
        base = os.environ.get("DEVLOOP_RUN_DIR") or tempfile.gettempdir()
        out = tempfile.mkdtemp(prefix="tests-", dir=osx.makedirs(base))
        result = testrun.run(cfg, os.getcwd(), "run", ids, keep_dir=out)
    except osx.OSFailure as exc:
        print("run_tests: %s" % exc, file=sys.stderr)
        return 2
    finally:
        if slot is not None:
            slot.release()
    print(result.summary())
    print("(log: %s)" % out)
    return CODES[result.status]


if __name__ == "__main__":
    sys.exit(main())
