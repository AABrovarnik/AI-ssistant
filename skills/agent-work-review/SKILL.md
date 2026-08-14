---
name: agent-work-review
description: Independently audit an agent's observable work against the user's request, project plan, evidence, scope, safety requirements, and unresolved risks. Use after multi-step implementation, diagnosis, deployment, production verification, or whenever the user asks for a self-review or activity audit.
---

# Agent Work Review

Review the agent's work as an evidence audit, not as a second implementation pass. Inspect only observable artifacts: user requests, plans, task-state files, diffs, commands, test results, logs, runtime status, and documentation. Do not claim access to hidden model reasoning.

## Workflow

1. Establish the requested outcome, explicit non-goals, and any safety boundary.
2. Read the relevant `AGENTS.md`, `PLANS.md`, `CURRENT_TASK.md`, `DECISIONS.md`, and project docs. If a file is absent, record that fact.
3. Build an evidence ledger with one row per important claim:
   - claim;
   - supporting artifact or command;
   - status: verified, partially verified, contradicted, or unverified;
   - remaining uncertainty.
4. Compare requested work with actions taken. Identify omissions, scope drift, unnecessary changes, and changes made without authorization.
5. Separate four states explicitly: implemented in source, tested locally, running in the inspected runtime, and independently verified in production.
6. Review failure paths: dependency authentication, retries, stale state, restart/recovery, duplicate execution, observability, rollback, and operator recovery.
7. Review safety: secret exposure, external side effects, confirmation boundaries, data mutation, and claims that exceed evidence. Never reproduce secret values.
8. Check documentation and project memory for stale counts, stale status, missing decisions, and unrecorded blockers.
9. Produce a concise report with:
   - outcome;
   - what was done well;
   - omissions and contradictions, prioritized P0/P1/P2;
   - evidence and uncertainty;
   - concrete next actions.

## Review rules

- Treat a passing unit test as evidence for that behavior only, not for deployment or external integration health.
- Treat a health endpoint as process/endpoint evidence only unless it explicitly checks the dependency under review.
- Do not call work complete when a required verification was skipped, blocked, or only inferred.
- If a secret appeared in command output, logs, or a diff, report the exposure without printing it and recommend rotation appropriate to that secret.
- Preserve unrelated user changes and distinguish pre-existing problems from regressions introduced by the reviewed work.
- Prefer exact file links, line numbers, command results, timestamps, and exit codes over narrative confidence.

## Output calibration

Use `verified` only when the artifact directly proves the claim. Use `partially verified` when only one layer is checked, such as source plus unit tests but not runtime. Use `unverified` when the required observation was not performed. State what would close each gap.

For a production incident, include a short timeline and identify the first missed detection, the recovery action, and the missing guardrail that would prevent recurrence.
