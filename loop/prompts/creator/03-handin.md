# Your hand-in

Write one JSON object to the result path given at the end of this prompt, then stop.

Tickets:

```json
{"kind": "tickets",
 "tickets": [{"key": "new-1", "title": "...", "objective": "...", "acceptance": ["..."],
              "outline": "...", "test_plan": "...", "needs_verifier": true, "needs_review": false}],
 "dag": {"new-1": {"depends_on": [], "verify": "together"}}}
```

A question for the user (direction, business logic, user experience):

```json
{"kind": "question", "text": "the question, the options you see, and which one you would pick"}
```
