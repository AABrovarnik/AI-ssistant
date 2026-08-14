# Runtime closure checklist

Use the project-specific commands only when the corresponding service exists. Keep output masked.

```bash
docker compose config
docker compose ps
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
docker compose exec -T api alembic current
```

For Gmail-like integrations, also verify the authenticated provider path, the integration account status, and a recent successful poll timestamp. A successful health response without a recent integration timestamp is not closure.

For every incident fix, record:

1. first failing dependency and error class;
2. why monitoring did not catch it;
3. recovery action;
4. safe smoke result;
5. guardrail added to prevent silent recurrence.
