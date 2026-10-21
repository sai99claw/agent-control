# EVIDENCE #146 (cloud worker, branch t146-cloud)

## 1 Branch and diff
- Branch `t146-cloud`, branched from `origin/main` 685af04 (the ticket's base_sha b5e3e91 is older; the dispatch said to start from origin/main).
- Code commits: d830668 (main change), 77f591a (verify fixtures). This file is the last commit.
- `git diff --stat 685af04..77f591a`: 30 files changed, 177 insertions(+), 544 deletions(-).

## 2 What changed (plain words)
- **needs_verifier missing = true, no Ready block (D-054 Q10)**
  - `scripts/ticket.py`: removed `needs_verifier_problem` and `NEEDS_VERIFIER_ROLES`, plus the three call sites (`create --state Ready`, `set state Ready`, `lint`). Lint now checks tags only. Comments and usage text updated.
  - `scripts/auto-fix.sh` `verifier_wanted`: anything other than JSON `false` counts as `true`. Missing or non-boolean values start the verifier on the same path as `true`. The old "missing" branch (stderr line naming D-028) is gone. Wording in two stdout lines updated.
  - `gate.sh` `verify_plan` was already `is False` and is untouched.
- **interface_fixed optional**: SCHEMA row marked optional; `memory/role/opener.md` wording changed.
- **Mutation table optional**
  - `RESULT_SECTIONS` drops `mutations`, so `sections` has four keys: patch_sha256 / gate / excluded / repro.
  - "Required five sections" becomes "required four sections, mutation table optional" in: DISPATCH-TEMPLATE §5 and §8 item 4, the §8.5 `mutations` row (key kept, marked optional, so test_dispatch_template still pins both tables), SCHEMA (result row and the `sections` paragraph), implementer.md, role-procedures.md, ROLES.md, WORKFLOW.md, and the auto-fix.sh packet text.
- **Board REHEARSAL section removed**: `board.py` loses `REHEARSAL_REL`, `rehearsal_rows`, `render_rehearsal`, the `state()` cell, the NAV item and the docstring line; `board/README.md` updated. The answer button is untouched. `docs/REHEARSAL.md` is kept.
- **check-stale removed**
  - Deleted `code-map/check-stale.py` and `tests/test_check_stale.py`.
  - Removed the `gate.sh` map line and the `test_check_stale` entry in the harness group.
  - Removed the harness copy and the unused `CODE_MAP` constant.
  - Rewrote `docs/CODE-MAP.md` §2 as a manual `git diff --stat`. Updated `code-map/cards/README.md` and `land-sh.md`.
  - Regenerated `code-map/index.json` with `repo-map.py build`; `check` reports it fresh.
  - Card `.md` files now map to `*.md` → test_no_project_names.
- **budget wording removed**: SCHEMA control row, `ticket.py` comment, DESIGN.md §9.
- Side edits that follow from the change:
  - `templates/dispatch-opener.md` no longer says lint checks needs_verifier or that Ready blocks.
  - `scripts/status.py` comment.
  - WORKFLOW auto-fix row now says missing = true.

## 3 Tests (behavior assertions added to existing modules; no new per-ticket case file)
- **test_ticket**
  - `ReadyNeedsABooleanNeedsVerifier` became `ReadyWithoutNeedsVerifier`: every draft (missing, "true", true, consolidator, null, false) goes to Ready with rc 0 and version 2; a missing value is not written back. Blocked → Ready without the field also passes.
  - `CreateTakesTheTwoBooleans`: `create --state Ready` without the flag succeeds and does not write the field.
  - `Lint`: a missing needs_verifier is not mentioned (worker and consolidator), rc 0. The "each bad cell gets one line" test now uses two tag fields.
- **test_auto_fix**
  - C1 missing → exactly one verifier `agent.start` in round 1 (agent auto-fix-verifier), ticket not written back, no D-028.
  - A4 missing → round 2 sends the verifier.
  - `AutoFixBase.make_ticket` now defaults `needs_verifier=False`; pass `ABSENT` to test "missing". Reverse check: the earlier 43 failures were all this suite's tickets with no needs_verifier now starting a verifier. With the default they are back to their old meaning. Only `AutoFixBase` subclasses use the default; the shared `control_harness.make_ticket` is unchanged, so no close or gate exemption depends on it.
- **test_apply**: sections assertion now has four keys. New `test_evidence_without_a_mutation_table_misses_no_section`. The no-block case checks `excluded` instead of `mutations`.
- **test_board**: the two REHEARSAL tests are deleted.
- **test_gate**: the map example uses `scripts/heartbeat.sh → test_heartbeat` instead of check-stale.
- **control_harness**: the sandbox writes `docs/NOTES.md` instead of `docs/REHEARSAL.md`. The guard fixtures in `test_auto_fix.py` and `verify/dispatch/test_ticket_128.py` now target `docs/NOTES.md`.
- **verify/** (follows the flipped default; no assertion was loosened):
  - `test_ticket_20`: the fixture ticket states `needs_verifier=false`. It measures the worker's result block; without the field the new default starts a verifier and the round exits rc 5.
  - `test_ticket_126`: deleted the "missing → no verifier + names D-028" case (the mechanism is gone; test_auto_fix covers the replacement). The table row was updated.

### Commands run (output as captured)
```
python3 -m unittest tests.test_apply tests.test_ticket tests.test_board tests.test_gate tests.test_dispatch_template
Ran 251 tests in 97.722s
FAILED (failures=1)   -> test_apply ...test_an_objection_that_cannot_be_recorded_is_not_called_already_there
                         also fails on base (git stash, same command: FAILED (failures=1)); see §4

python3 -m unittest tests.test_auto_fix
Ran 152 tests in 383.700s
OK            rc=0

python3 -m unittest tests.test_auto_fix_agy tests.test_auto_fix_codex tests.test_auto_fix_fallback_midrun tests.test_cost_ledger tests.test_inbox tests.test_land tests.test_objection_triage tests.test_rules_delivery tests.test_sync_to_project
Ran 240 tests in 265.821s
FAILED (failures=1)   -> test_objection_triage ...test_a5c_the_opener_runs_past_its_timeout
                         also fails on base (git stash, single test: FAILED (failures=1)); see §4

python3 -m unittest verify/agent-output/test_ticket_20.py verify/batch_fix/test_ticket_108.py verify/batch_fix/test_ticket_109.py verify/dispatch/test_ticket_{96,114,115,121,126,127,128,131,132}.py verify/autofix_receive/test_ticket_130.py verify/hard_block/test_ticket_135.py
Ran 234 tests  FAILED (failures=2)  -> test_ticket_20 block test, test_ticket_126 A4 missing (both pinned old default; fixed in 77f591a)
python3 -m unittest verify/dispatch/test_ticket_126.py verify/agent-output/test_ticket_20.py   (after fix)
Ran 19 tests in 24.740s
OK
```
Not run: `sh scripts/gate.sh --branch --ticket 146` (full suite, about 30 minutes). The dispatch said not to run the full suite, so that is left to the gate.

### Mutations (optional now; each checked for exactly one replacement, run, then restored with a hash match)
| # | count | target | first red line | restored sha256[:16] |
|---|---|---|---|---|
| M1 | 1 | auto-fix `verifier_wanted` back to `is True else no` → test_c1_missing_starts_the_verifier_like_true, test_a4_needs_verifier_missing_sends_the_verifier_like_true | `AssertionError: 0 != 1` (both) | 8933a72e7b2dd8a9 = before |
| M2 | 1 | add `("mutations", ("變異",))` back to RESULT_SECTIONS → test_evidence_without_a_mutation_table_misses_no_section | `AssertionError: {... 'mutations': False ...} != {...}` | 4ed2668aa47540cd = before |
| M3 | 1 | lint appends "needs_verifier 沒寫" for workers → Lint.test_missing_needs_verifier_is_not_mentioned | `AssertionError: 1 != 0 : ticket: lint #1 needs_verifier 沒寫` | 4ed2668aa47540cd |

## 4 Hypotheses ruled out
- The test_apply failure "objection cannot be recorded" was not caused by this change. The test makes `tickets/` read-only with chmod 555, but this container runs as root, so the write succeeds anyway. Same failure on base with `git stash`.
- The test_objection_triage `test_a5c_the_opener_runs_past_its_timeout` failure was not caused by this change. The killed stand-in's pid still answers `os.kill(pid, 0)`, which looks like zombie or reaping behavior in this container. Same failure on base with `git stash`.
- Not a sign that the default flip is wrong: the 43 test_auto_fix failures after the flip were all fixtures that never set needs_verifier. They now get a verifier, which is the requested behavior. Fixed at the fixture level (`AutoFixBase` default false and an `ABSENT` sentinel), not by loosening assertions.
- `memory.py check-stale` (the consolidator subcommand) and `tests/test_memory_lifecycle.py` were not touched; they are a different tool. `templates/discussion.md` `stop.budget` was not touched.
- `verify/memory_inbox/test_ticket_134.py` reads the real `docs/REHEARSAL.md`, which is kept, so no change was needed.

## 5 Minimal reproduction
`python3 -m unittest tests.test_ticket.ReadyWithoutNeedsVerifier tests.test_ticket.Lint tests.test_apply.TheStructuredDeliveryOfTheFirstRound.test_evidence_without_a_mutation_table_misses_no_section tests.test_auto_fix.TheFirstRoundVerifierRunsAlongside.test_c1_missing_starts_the_verifier_like_true`
Expected: `OK`.

## 6 Acceptance, item by item
1. Behavior: met.
   - `create --state Ready` and `set state Ready` succeed without needs_verifier; lint does not mention it (test_ticket).
   - auto-fix round 1 starts the verifier for such a ticket, and needs_verifier=false still starts none (test_auto_fix C1).
   - Without a mutation section, `sections` has four keys and reports nothing missing (test_apply).
2. grep 0: met. The ticket's `git grep -nE ...` over the listed paths printed 0 lines (rc=1). `code-map/check-stale.py` and `tests/test_check_stale.py` are deleted.
3. Full suite green: not run here (see §3). The modules in the test_plan and every auto-fix-driven module were run. The only remaining failures are the two environmental ones in §4, which also fail on base.

## 7 Not done, and why
- `docs/FLOW.html` still says "five sections / mutation red table" in a few places. It is a historical flow diagram, and wording cleanup there belongs to the documentation consolidation ticket (#147).
- `depends_on #143` (not landed yet): this was done on the current origin/main as dispatched. board.py and ticket.py may conflict with #143 when it lands.
- The changes under `verify/` (test_ticket_20 fixture, test_ticket_126 case deleted) touch verifier-owned files. They only follow the flipped default and the deleted mechanism (D-057 ①: tests of deleted mechanisms go too). No assertion was loosened.

## Memory
None.

## Tokens
Unknown (the cloud session cannot read the envelope).

```result
{"ticket": "146", "role": "worker", "round": 1, "rc": 0,
 "patch_sha256": "",
 "gate": {"cmd": "python3 -m unittest tests.test_auto_fix", "ran": 152, "rc": 0},
 "mutations": [{"id": "M1", "count": 1, "case": "tests/test_auto_fix.py::TheFirstRoundVerifierRunsAlongside::test_c1_missing_starts_the_verifier_like_true", "red_first_line": "AssertionError: 0 != 1"},
               {"id": "M2", "count": 1, "case": "tests/test_apply.py::TheStructuredDeliveryOfTheFirstRound::test_evidence_without_a_mutation_table_misses_no_section", "red_first_line": "AssertionError: {... 'mutations': False ...} != {...}"},
               {"id": "M3", "count": 1, "case": "tests/test_ticket.py::Lint::test_missing_needs_verifier_is_not_mentioned", "red_first_line": "AssertionError: 1 != 0"}],
 "objection": null,
 "excluded": ["test_apply chmod-555 objection case fails on base too (root ignores 0555)",
              "test_objection_triage a5c timeout case fails on base too (pid still answers kill 0)"],
 "repro": {"cmd": "python3 -m unittest tests.test_ticket.ReadyWithoutNeedsVerifier tests.test_ticket.Lint", "expect": "OK"},
 "memory": [], "baseline": null}
```
