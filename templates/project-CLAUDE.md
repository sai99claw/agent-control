# <project>

This project is developed with agent-control (`../agent-control`, entry `../agent-control/bin/actl`).
The main session follows `../agent-control/CLAUDE.md`; agents started by devloop read only their prompt.

## Project specifics
- Tests: `board/config.json` `tests` declares `preflight` / `run` / `full` (and `select`); `actl test <command>` runs them.
- Release: `board/config.json` `devloop.release` (the controller runs it after main moves).
- Off limits: <prod / staging ports>, <data and secret directories>; `hard_block` stops tickets that touch them.
