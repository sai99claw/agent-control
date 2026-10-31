# You review ticket #{ticket_id}

Read the branch {branch} in {worktree} (compare with `git diff {target}...HEAD`) against the
ticket. Do not change anything. Answer one question: does what was built do what the ticket
asks, without breaking what it does not mention?

## The ticket

```json
{ticket_json}
```

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "review", "verdict": "pass", "feedback": ""}}
    {{"kind": "review", "verdict": "fail", "feedback": "each problem: file, what is wrong, what the ticket says"}}
