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

{ruling}
## How to work

Fix by the tickets' current content, shown above: a merged ticket is frozen and cannot be
revised, so the fix makes the code do what each ticket says now. Commit source and test files
only, never generated files (caches, build output); a generated file already committed by a ticket
may be removed with `git rm --cached` and ignored. Run the failing tests with: {test_cmd}

A verifier's acceptance cases carry the ticket's case mark (`case_mark` above, `T<id>Acceptance`).
You may change such a case only to make it match that ticket's current acceptance text - never
looser than the ticket. List every case you changed in `cases_changed`: the ticket, the case, and
the acceptance item it now matches; `[]` when you changed none.

You may change a test that is not an acceptance case only when a ticket of this group explicitly changes the behaviour that test asserts; list every such test in `tests_changed`: the test, the ticket and the acceptance item that changes the behaviour it asserts; `[]` when you changed none.

If two tickets of this group contradict each other, do not pick a winner yourself and do not
change either ticket's cases to make them agree: hand in kind `decision` with where they
contradict and which new ticket you propose. The decision hand-in also carries `question` (one
line), `options` (2-4, the recommended one first; each {{"action", "note", "consequence"}}, the
consequence one line; actions: `group-ready` - its note is the complete ruling for the next fixer:
which ticket to follow, change only the cases of the contradiction, loosen no other acceptance,
list what changed - `group-cancel` and `ack`) and `recommendation` (one line: why the first).

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "done", "summary": "what was wrong and what you changed",
     "cases_changed": [{{"ticket": "<id>", "case": "<case>", "acceptance": "<the acceptance item it now matches>"}}],
     "tests_changed": [{{"test": "<test id>", "ticket": "<id>", "acceptance": "<the acceptance item>"}}]}}
    {{"kind": "decision", "text": "where the tickets contradict and which new ticket you propose",
     "question": "one line", "recommendation": "one line: why the first option",
     "options": [{{"action": "group-ready", "note": "the complete ruling for the next fixer", "consequence": "one line"}},
                 {{"action": "group-cancel", "note": "", "consequence": "one line"}}]}}

Add "lessons" to your hand-in only for something this run taught you that a future agent should
know - one principle per entry, no story:
`"lessons": [{{"layer": "role" | "model" | "project", "text": "..."}}]`
