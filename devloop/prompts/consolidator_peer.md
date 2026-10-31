# You review a memory consolidation: {path} ({layer} layer, {name})

Another model drafted the new version of this memory file from the lessons below (D-013: memory
is consolidated by two different models, not one alone). The layer contract it had to follow is
in {contract_path}, section "What belongs in each layer". Do not rewrite the file; judge it.

Look for: a lesson dropped that should have stayed, one kept that belongs to another layer or
nowhere (incident stories, examples, routing policy, rules the prompt already gives), a stale
line about a tool or step the current loop ({loop_readme}) does not have, two lines that say the
same thing, a line made vaguer than the lesson it came from.

## The file before

{current}

## The lessons

```json
{lessons}
```

## The draft

{draft}

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "review", "verdict": "agree", "notes": ""}}
    {{"kind": "review", "verdict": "change", "notes": "each point: the line, what is wrong, what to do"}}
