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

## What earlier agents learned

{memory}

Project memory (this project's design reasons and invariants; read it when your work touches
them): {project_memory}

Code index (generated from the code; never edit it): {index}

## How to work

Fix by the tickets' current content, shown above: a merged ticket is frozen and cannot be
revised, so the fix makes the code do what each ticket says now. Commit source and test files
only, never generated files (caches, build output); a generated file already committed by a ticket
may be removed with `git rm --cached` and ignored. Run the failing tests with: {test_cmd}

A verifier's acceptance cases carry the ticket's case mark (`case_mark` above, `T<id>Acceptance`).
You may change such a case only to make it match that ticket's current acceptance text - never
looser than the ticket. List every case you changed in `cases_changed`: the ticket, the case, and
the acceptance item it now matches; `[]` when you changed none.

If two tickets of this group contradict each other, do not pick a winner yourself and do not
change either ticket's cases to make them agree: hand in kind `decision` with where they
contradict and which new ticket you propose.

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "done", "summary": "what was wrong and what you changed",
     "cases_changed": [{{"ticket": "<id>", "case": "<case>", "acceptance": "<the acceptance item it now matches>"}}]}}
    {{"kind": "decision", "text": "where the tickets contradict and which new ticket you propose"}}

Add "lessons" to your hand-in only for something this run taught you that a future agent should
know - one principle per entry, no story:
`"lessons": [{{"layer": "role" | "model" | "project", "text": "..."}}]`
