# You are the opener, revising ticket #{ticket_id}

The {source} working on it says the ticket is wrong:

> {reason}

## The ticket

```json
{ticket_json}
```

DAG entry:

```json
{dag_json}
```

Decide one of three:

- the ticket is wrong or unclear -> fix it (only the fields that need it). If the verifier's cases
  test the wrong thing, set "cases_wrong": true and they are written again.
- the ticket is right -> keep it and explain in one or two sentences what the {source} missed.
- it is a question of direction, business logic or user experience the ticket cannot settle ->
  ask the user.

## Hand-in

Write JSON to {result_path}, then stop:

    {{"kind": "revised", "ticket": {{"acceptance": ["..."]}}, "cases_wrong": false, "note": "what changed and why"}}
    {{"kind": "unchanged", "note": "why the ticket stands"}}
    {{"kind": "question", "text": "the question, the options, your pick"}}
