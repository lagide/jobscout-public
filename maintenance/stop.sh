#!/bin/sh
set -eu

ROOT="${JOBSCOUT_MAINTENANCE_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
PIDFILE="$ROOT/daemon.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "JobScout maintenance is not running"
    exit 0
fi

pid="$(cat "$PIDFILE")"
if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "JobScout maintenance stopped (pid=$pid)"
fi
rm -f "$PIDFILE"
