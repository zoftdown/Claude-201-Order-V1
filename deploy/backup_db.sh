#!/usr/bin/env bash
# Auto DB backup for the order system. Run by cron daily at 02:00 (VPS time).
set -euo pipefail

cd /opt/order

# Must source .env first — without the DB_* vars, manage.py/pg tools fall back
# to SQLite instead of Postgres (deploy lesson #1).
set -a
. /opt/order/.env
set +a

mkdir -p /opt/order/backups

OUT="/opt/order/backups/order_db_$(date +%Y%m%d_%H%M).sql"

# pg_dump over TCP (-h) with password (-w + PGPASSWORD): the local unix socket
# is peer-auth and rejects order_user. This matches how Django connects.
PGPASSWORD="$DB_PASSWORD" pg_dump -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -w \
    -U "$DB_USER" "$DB_NAME" > "$OUT"

# Retain 90 days of daily backups; prune older dumps.
find /opt/order/backups -name 'order_db_*.sql' -mtime +90 -delete

echo "backup OK: $OUT ($(du -h "$OUT" | cut -f1))"
