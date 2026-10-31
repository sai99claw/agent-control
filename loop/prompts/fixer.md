# You fix integration branch {branch} (group {group})

Several tickets were merged into this branch and its regression is red, or {main} moved and must
be merged in. Work only in {worktree} on {branch}; commit your fixes there. Do not push.

Merge {main} first? {main_moved}

## What is red

{red}

## The tickets in this branch

```json
{tickets_json}
```

What the branch changes compared with {main}:

{diffstat}

## How to work

Fix the cause so every ticket still does what it asks. Commit source and test files only, never
generated files (caches, build output); a generated file already committed by a ticket may be
removed with `git rm --cached` and ignored. Run the failing tests with: {test_cmd}
If two tickets contradict each other, do not pick a winner yourself; run (600000 ms timeout):

    {revise_cmd}

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "done", "summary": "what was wrong and what you changed"}}
    {{"kind": "blocked", "text": "why only the user can decide this"}}
