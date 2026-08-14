#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:8000}"
api_token="${INTERNAL_API_TOKEN:-}"

if [[ -z "$api_token" ]]; then
  echo "post-deploy-smoke: set INTERNAL_API_TOKEN in the environment" >&2
  exit 2
fi

curl -fsS "$base_url/health/live" >/dev/null
curl -fsS "$base_url/health/ready" >/dev/null
gmail_status="$(curl -fsS -H "Authorization: Bearer $api_token" "$base_url/integrations/gmail/status")"

echo "health: ok"
echo "gmail: $gmail_status"
