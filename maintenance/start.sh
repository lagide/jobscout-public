#!/bin/sh
set -eu

ROOT="${JOBSCOUT_MAINTENANCE_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
PIDFILE="$ROOT/daemon.pid"
cd "$ROOT"

if [ -f "$PIDFILE" ]; then
    old_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "JobScout maintenance already running (pid=$old_pid)"
        exit 0
    fi
fi

nohup /usr/bin/python3 "$ROOT/maintenance.py" \
    --serve \
    --api "${JOBSCOUT_API_URL:-http://127.0.0.1:8000}" \
    --scope "$ROOT/../config/geo_scope.json" \
    --interval-hours 1 \
    --link-interval-hours 24 \
    --support-interval-hours 24 \
    --workers 4 \
    --timeout 12 \
    --failure-threshold 3 \
    >> "$ROOT/daemon.out" 2>&1 &

pid=$!
echo "$pid" > "$PIDFILE"
sleep 2
if kill -0 "$pid" 2>/dev/null; then
    echo "JobScout maintenance started (pid=$pid)"
else
    echo "JobScout maintenance failed to start" >&2
    exit 1
fi
