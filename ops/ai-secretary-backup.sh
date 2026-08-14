#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

PROJECT_DIR="${AI_SECRETARY_PROJECT_DIR:-/root/projects/AI-ssistant}"
BACKUP_DIR="${AI_SECRETARY_BACKUP_DIR:-${PROJECT_DIR}/backups}"
RETENTION_DAYS="${AI_SECRETARY_BACKUP_RETENTION_DAYS:-14}"

if [[ ! "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  printf 'AI_SECRETARY_BACKUP_RETENTION_DAYS must be a positive integer\n' >&2
  exit 2
fi

if [[ ! -f "$PROJECT_DIR/docker-compose.yml" ]]; then
  printf 'Compose project not found: %s\n' "$PROJECT_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$BACKUP_DIR/.ai_secretary_${stamp}.dump.tmp"
destination="$BACKUP_DIR/ai_secretary_${stamp}.dump"
trap 'rm -f -- "$temporary"' EXIT

cd "$PROJECT_DIR"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$temporary"

if [[ ! -s "$temporary" ]]; then
  printf 'Backup command produced an empty dump\n' >&2
  exit 1
fi

chmod 600 "$temporary"
mv -- "$temporary" "$destination"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'ai_secretary_*.dump' \
  -mtime "+$RETENTION_DAYS" -delete

printf 'Created %s (%s bytes); retention=%s days\n' \
  "$destination" "$(stat -c %s "$destination")" "$RETENTION_DAYS"
