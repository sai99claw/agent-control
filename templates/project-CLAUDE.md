If `DEVLOOP_ROLE` is set, devloop started you: the main-session sections below are not for you (your prompt is your brief), but the section "Rules for every agent" is.

# <project>

## Rules for every agent
These rules bind the main session and every agent that works in this project.
- Production data: <where it is>; never read or write it.
- Protected ports: <ports>; never bind, stop or connect to them.
- Scripts never to run: <scripts>.

Why they reach agents: devloop starts agents as `claude -p` in a worktree of the project (the opener in the project checkout itself), so Claude Code loads this CLAUDE.md for them. The file must be committed, because a worktree holds only tracked files. Codex agents read AGENTS.md, which needs the same rules.

## For the main session
This project is developed with agent-control (`../agent-control`, entry `../agent-control/bin/actl`).
The main session follows `../agent-control/CLAUDE.md`.

### Project specifics (main session)
- Tests: `board/config.json` `tests` declares `preflight` / `run` / `full`; `actl test <command>` runs them.
- Release: `board/config.json` `devloop.release` (the controller runs it after main moves).
- Off limits: `hard_block` stops tickets that touch the production data, ports and secret directories named above.
