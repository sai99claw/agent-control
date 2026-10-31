#!/bin/sh
# Claude Code SessionStart hook for the main session (.claude/settings.json): what waits for the
# user (unread pages) and the open tickets, printed into the context before the first prompt.
# Agents started by devloop carry DEVLOOP_ROLE and get nothing: their prompt is their whole brief.
# Never fails the session: any error prints one line and exits 0.
set -u
[ -n "${DEVLOOP_ROLE:-}" ] && exit 0
[ "${AC_SESSION_HOOK:-}" = "0" ] && exit 0
cat >/dev/null 2>&1 || true    # the hook's JSON on stdin is not needed
AC_HOME=$(cd "$(dirname "$0")/.." && pwd -P) || exit 0
root=${AC_ROOT:-${CLAUDE_PROJECT_DIR:-}}
[ -n "$root" ] || root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$root/board/config.json" ] || exit 0
export AC_ROOT=$root
echo "## devloop: pages waiting for you"
python3 "$AC_HOME/devloop/tickets.py" inbox 2>&1 | head -40 || echo "(could not read the pool)"
echo
echo "## devloop: tickets and groups"
python3 "$AC_HOME/devloop/tickets.py" list 2>&1 | head -60 || echo "(could not read the pool)"
exit 0
