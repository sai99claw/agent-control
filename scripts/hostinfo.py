"""Machine facts shared by scripts/adapter-unittest.py and devloop/core/host.py (#44).

Standard library only; no project imports, so either side can load it by path.
"""

import os
import re
import subprocess


def read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def run_tool(argv):
    """stdout of `argv` when it ran, exited 0 and finished within 10 seconds; else None."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def vm_stat_bytes(vm_stat, sysctl):
    """macOS: `(Pages free + Pages inactive) * page size` from a `vm_stat` report (page size from its
    header), capped by `sysctl -n hw.memsize` when that prints a positive integer. None = no number."""
    report = run_tool([vm_stat])
    if report is None:
        return None
    header = re.search(r"\(page size of (\d+) bytes\)", report)
    free = re.search(r"^Pages free:\s+(\d+)\.", report, re.MULTILINE)
    inactive = re.search(r"^Pages inactive:\s+(\d+)\.", report, re.MULTILINE)
    if not (header and free and inactive) or int(header.group(1)) < 1:
        return None
    available = (int(free.group(1)) + int(inactive.group(1))) * int(header.group(1))
    total = (run_tool([sysctl, "-n", "hw.memsize"]) or "").strip()
    if total.isdigit() and int(total) > 0:
        available = min(available, int(total))
    return available


def available_bytes(cgroup, meminfo, vm_stat="vm_stat", sysctl="sysctl"):
    """`memory.max - memory.current` when both are numbers, else `MemAvailable`, else (macOS) the
    `vm_stat` free + inactive pages capped by `hw.memsize`; None = none of them gave a number."""
    limit = read_text(os.path.join(cgroup, "memory.max")) or ""
    used = read_text(os.path.join(cgroup, "memory.current")) or ""
    if limit.isdigit() and used.isdigit():
        return max(0, int(limit) - int(used))
    for line in (read_text(meminfo) or "").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "MemAvailable:" and fields[1].isdigit():
            return int(fields[1]) * 1024
    return vm_stat_bytes(vm_stat, sysctl)
