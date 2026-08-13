# Execution Plan

## Objective

Prevent the Gmail integration from reading or processing messages older than 1 August 2026 by default, while preserving incremental polling and duplicate protection.

## Non-goals

- Do not modify already stored Gmail source messages.
- Do not add Gmail write scopes or change task-confirmation behavior.
- Do not implement Google Calendar in this step.

## Steps

- [completed] Add a configurable/default Gmail lower-bound date and apply it to the first poll as well as incremental polls.
- [completed] Add focused tests for the first-poll cutoff, incremental query, and boundary behavior.
- [completed] Run Gmail tests, relevant application checks, and inspect the final diff.
- [pending] Deploy/restart only after local verification and report the exact remaining v1 work.

## Assumptions

- “1 August” means 2026-08-01 in UTC for the current deployment.
- Gmail’s `after:YYYY/MM/DD` query semantics are sufficient for the initial server-side filter; message-level filtering will be added if the client contract requires it.

## Verification and rollback

- Verification: `.venv/bin/pytest -q tests/test_gmail.py`, then the narrowest relevant broader test command.
- Inspect `git diff --check` and the final diff before deployment.
- Rollback: revert only the new Gmail cutoff change if production behavior needs to be restored; existing Gmail records remain untouched.
