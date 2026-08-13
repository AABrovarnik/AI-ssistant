# Execution Plan

## Objective

Implement the first Google Calendar integration slice after the Gmail MVP: explicit, confirmed task-to-event creation and event updates when a task deadline changes, with encrypted OAuth credentials and no automatic calendar writes by default.

## Non-goals

- Do not modify already stored Gmail source messages.
- Do not create calendar events without explicit confirmation.
- Do not implement Calendar watch/webhook, recurring events, or broad automatic sync in this step.

## Steps

- [completed] Define the Calendar API, OAuth scopes, persistence/migration, and explicit confirmation flow.
- [completed] Implement Calendar client, OAuth connection, event create/update/delete, and task sync status.
- [completed] Add focused tests for event mapping, idempotent update, OAuth/token handling, and confirmation boundaries.
- [pending] Run the full suite, deploy after verification, and perform a live smoke check without creating an event automatically.

## Assumptions

- “1 August” means 2026-08-01 in UTC for the current deployment.
- Calendar will reuse the existing encrypted integration-account storage, but will use a separate Google Calendar scope and provider value.
- Event creation will be exposed as an explicit task action/API call; `auto_calendar` remains opt-in and is not activated by this slice.

## Verification and rollback

- Verification: focused Calendar tests, then `.venv/bin/pytest -q` and `git diff --check` before deployment.
- Inspect the final diff and migration before deployment.
- Rollback: stop using Calendar endpoints and revert only the Calendar migration/code; existing Gmail records and task data remain untouched.
