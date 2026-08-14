---
name: production-closure-review
description: Close out a code change or deployment by verifying effective configuration, container/runtime health, dependency authentication, integration state, safe smoke behavior, recovery paths, rollback, and operational documentation. Use after production changes, integration work, incident fixes, or when someone claims a service is working.
---

# Production Closure Review

Verify the deployed behavior at the same boundary where the user depends on it. Keep source, test, runtime, and production evidence separate. This skill is a verification workflow; it does not authorize deployment, external messages, destructive recovery, or secret rotation by itself.

## Workflow

1. Read the plan, current task, decisions, deployment docs, and relevant service code. List the exact acceptance criteria and non-goals.
2. Check source and configuration before deployment:
   - `git diff --check`;
   - project lint/tests;
   - `docker compose config` when Compose is used;
   - required environment variables and duplicate/conflicting keys;
   - migrations and rollback scope.
3. Inspect effective runtime configuration inside the actual service container or process. Print only booleans, names, versions, and masked values; never print tokens, passwords, OAuth secrets, or full authorization headers.
4. Verify service state and dependency authentication separately:
   - service/container status;
   - live/readiness checks;
   - database migration state;
   - provider health and authenticated request path;
   - integration account status, last attempt, last success, and last error.
5. Run the narrowest safe smoke test that crosses the changed boundary. Use a fixture, test account, or read-only operation. Do not send an external message or create a production object unless the user explicitly authorized that exact side effect.
6. Exercise one failure/recovery path when the change affects polling, credentials, queues, retries, or restart behavior. Verify that transient failures recover without manual database edits and that permanent failures are visible to operators.
7. Review logs and metrics for secrets, full URLs, unbounded retries, silent failures, duplicate work, and missing timestamps.
8. Verify rollback and operator instructions, then update `PLANS.md`, `CURRENT_TASK.md`, `DECISIONS.md`, and runbooks with the observed result and remaining caveats.

## Required conclusion

Report four separate statuses:

- source: implemented or not;
- tests: exact commands and results;
- runtime: exact service/status/timestamp evidence;
- production: independently verified, partially verified, or not verified.

If any required layer is missing, say `not closed` and name the next command or approval needed. Do not infer a successful integration from a successful container restart.

## Safety rules

- Keep credentials in the existing secret-management path; do not commit `.env` or copy secrets into logs, fixtures, or reports.
- Manual operator endpoints must be authenticated, rate-limited, idempotent where possible, and protected from concurrent duplicate runs.
- A dependency outage must not silently disable an integration forever. Require retry/backoff or an explicit, observable terminal state with recovery instructions.
