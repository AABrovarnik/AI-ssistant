# AWAITING follow-up

## Purpose

An `AWAITING` task represents an expected response, document, decision, or other result. When its deadline is more than one day in the past, the system sends the owner an internal Telegram suggestion to check the result.

The system does not contact the assignee or any other third party automatically.

## Lifecycle

1. The task must have `task_type=AWAITING`, an active status, and `due_at`.
2. `plan_reminders()` creates one policy reminder with type `AWAITING_FOLLOW_UP` at `due_at + 1 day`.
3. The reminder uses the stable dedupe key `policy:{task_id}:AWAITING_FOLLOW_UP`.
4. The worker delivers the reminder through Telegram with two actions:
   - `Результат получен` completes the task through `TaskService.complete()`;
   - `Напомнить завтра` creates one internal follow-up reminder for the next day.
5. Completion uses the existing task version and idempotency key, so a repeated Telegram callback cannot create a second task event.

## Code locations

- Scheduling and delivery: `app/jobs/reminders.py`
- Telegram callback handling: `app/integrations/telegram/bot.py`
- Task transitions and audit events: `app/tasks/service.py`
- Reminder/Telegram tests: `tests/test_reminders.py` and `tests/test_telegram.py`

## Troubleshooting

### No follow-up appeared

Check all of the following:

- the task has `task_type=AWAITING`;
- the task is not `DONE` or `CANCELLED`;
- `due_at` is set and is at least one day in the past;
- the worker is running and its reminder loop is active;
- the policy reminder was not already sent or cancelled.

The worker plans reminders every 30 seconds. Running the planner again is safe because the policy dedupe key is stable.

### Delivery failed

Telegram delivery uses the existing retry policy: up to three attempts with 5-minute, 30-minute, and 2-hour delays. After the final failure the reminder is `FAILED`; inspect the worker logs and Telegram credentials before retrying.

### Button says the task changed

The callback includes the task version. If another action changed the task first, the version conflict is intentional. Open the task again and use its current actions instead of replaying the old follow-up button.

## Safety boundary

Follow-up is an owner-facing suggestion only. Adding outbound messages to assignees requires a separate product decision, explicit confirmation flow, audit coverage, and new provider-specific tests.
