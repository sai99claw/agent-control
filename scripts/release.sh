#!/bin/sh
# agent-control's own release (board/config.json devloop.release; D-079: green lands and releases).
# When this runs, main already points at DEVLOOP_RELEASE_SHA and was pushed; projects take A by
# pulling main. A release names that commit: an annotated tag a-<UTC date>-<group>-<short sha>
# listing the tickets, pushed to the pool's remote when there is one, so a project can pin it.
# Exit 0 released · non-zero: the controller writes a decision page (no retry, no rollback).
set -eu
: "${DEVLOOP_RELEASE_SHA:?DEVLOOP_RELEASE_SHA is not set - run by ticket_controller only}"
group=${DEVLOOP_GROUP:-manual}
short=$(git rev-parse --short "$DEVLOOP_RELEASE_SHA")
tag="a-$(date -u +%Y%m%d)-$group-$short"
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "release: tag $tag already exists" >&2
  exit 1
fi
git tag -a "$tag" -m "agent-control release $tag

tickets: ${DEVLOOP_TICKETS:-none}" "$DEVLOOP_RELEASE_SHA"
if [ -n "${DEVLOOP_REMOTE:-}" ]; then
  if ! git push -q "$DEVLOOP_REMOTE" "refs/tags/$tag"; then
    echo "release: tag $tag made here but not pushed to $DEVLOOP_REMOTE" >&2
    exit 1
  fi
fi
echo "released $tag"
