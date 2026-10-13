#!/bin/sh
# Container entrypoint.   docker-entrypoint.sh api | worker | migrate | <any command>
#
#   api      applies migrations (unless RUN_MIGRATIONS=false), then starts uvicorn
#   worker   starts the RQ worker (needs REDIS_URL); never migrates
#   migrate  applies migrations and exits (use as a one-off release/pre-deploy job)
#
# Several replicas starting together are safe: scripts.migrate takes a PostgreSQL advisory lock, so one replica migrates and
# the others wait, then find the database at head. With many replicas prefer RUN_MIGRATIONS=false and one `migrate` job.
set -eu

role="${1:-api}"
[ "$#" -gt 0 ] && shift

case "$role" in
  api)
    if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
      python -m scripts.migrate
    fi
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}" "$@"
    ;;
  worker)
    exec python -m app.worker "$@"
    ;;
  migrate)
    exec python -m scripts.migrate "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
