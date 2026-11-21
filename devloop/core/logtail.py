"""logtail : read the end of a log the way the board and the release-failed pages do.

lines() reads at most the last TAIL_BYTES of a file; tail() is its last lines as one text;
stage() is the text after the last 'devloop-stage:' line a release printed.
"""

import os

TAIL_BYTES = 1 << 20            # a log is read from at most this far before its end
STAGE_MARK = "devloop-stage:"


def lines(path):
    """The lines of a log's last TAIL_BYTES; None when it cannot be read."""
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            data = fh.read()
    except OSError:
        return None
    out = data.decode("utf-8", "replace").splitlines()
    return out[1:] if size > TAIL_BYTES and out else out


def tail(path, count):
    """The last `count` lines of a log as one text; '' when it cannot be read."""
    got = lines(path)
    return "\n".join(got[-count:]) if got else ""


def stage(path):
    """The text after 'devloop-stage:' on the log's last line that starts with it; None when there is none."""
    for line in reversed(lines(path) or []):
        if line.startswith(STAGE_MARK):
            return line[len(STAGE_MARK):].strip()
    return None
