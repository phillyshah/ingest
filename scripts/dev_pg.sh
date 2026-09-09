#!/usr/bin/env bash
# Start a throwaway local Postgres 16 for development/tests when no DATABASE_URL is set.
# Never used in production; production is the owner's Supabase project (docs/adr/0001).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
DATA="$ROOT/.pgdata"
PORT="${PGPORT:-55432}"
RUNAS=""
if [ "$(id -u)" = "0" ]; then RUNAS="sudo -u postgres"; fi
if [ ! -f "$DATA/PG_VERSION" ]; then
  mkdir -p "$DATA"; [ -n "$RUNAS" ] && chown postgres:postgres "$DATA" || true
  $RUNAS "$PGBIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
fi
if ! $RUNAS "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
  $RUNAS "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp" -l "$DATA/pg.log" -w start >/dev/null
fi
psql -h 127.0.0.1 -p "$PORT" -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='moveai'" | grep -q 1 || \
  psql -h 127.0.0.1 -p "$PORT" -U postgres -c "CREATE DATABASE moveai" >/dev/null
echo "postgresql://postgres@127.0.0.1:$PORT/moveai"
