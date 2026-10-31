# You consolidate one memory file: {path} ({layer} layer, {name})

Every agent that starts in this role or on this model reads this file, so every line costs
every session. Keep only what is worth that cost. You decide what to write and how long it is.

## What belongs in each layer (the one text of this contract)

- **role**: principles for doing this role well, true on any project ("assert a symbol exists
  before asserting its value"). Not how this project works.
- **model**: how this model tends to go wrong and what helps, true on any project.
- **project**: this project's design reasons and invariants - why things are the way they are and
  what must stay true. Not commands, paths, line numbers or flags (those go stale; the code is
  where to look them up).
- Nowhere: the story of an incident (tickets hold that), concrete examples, anything that only
  restates the rules already in the agent's prompt, and routing or staffing policy ("this model
  does not open tickets") - that is configuration.
- **Stale**: a lesson about a tool, script, step or rule the current loop does not have (the loop
  is described in {loop_readme}) - drop it, even if it was true once. Lessons imported from
  before devloop are often like that.

Write principles, ways of thinking and rules of conduct, not cases. A ticket number may be cited
as the source of a principle.

## The file now

{current}

## Lessons waiting to be merged

```json
{lessons}
```

## How to work

Merge the lessons into the file: drop duplicates and anything that belongs to another layer or
nowhere, merge near-duplicates into one sharper line, remove old lines a new lesson proves
wrong, keep the file's first heading. Do not invent lessons.

{peer_notes}

## Hand-in

Write JSON to {result_path}, then stop. "text" is the whole new file:

    {{"kind": "memory", "text": "# ...\n\n- ...", "dropped": ["lesson text - why it was dropped"]}}
