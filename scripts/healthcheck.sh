#!/bin/sh
# JobScout — healthcheck externe (toutes les 5 min via cron/DSM, en root si besoin).
#
# Le healthcheck Docker natif est désactivé dans docker-compose.yml (bug du
# daemon Docker 24.0.2 sur DSM) : sans ce script, un backend mort resterait
# mort jusqu'à intervention manuelle. On sonde /health et l'UI, et on
# redémarre le container concerné après 1 échec confirmé (retry à 10 s).

LOG="${JOBSCOUT_HEALTHCHECK_LOG:-$(cd "$(dirname "$0")/.." && pwd)/healthcheck.log}"
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"

check_and_restart() {
    url="$1"; container="$2"
    if curl -fs -m 10 "$url" >/dev/null 2>&1; then
        return 0
    fi
    sleep 10
    if curl -fs -m 10 "$url" >/dev/null 2>&1; then
        return 0
    fi
    echo "$(date '+%F %T') ${container} KO (${url}) -> docker restart" >> "$LOG"
    "$DOCKER" restart "$container" >> "$LOG" 2>&1
}

check_and_restart "http://127.0.0.1:8000/health" "jobscout-backend"
check_and_restart "http://127.0.0.1:8502/health" "jobscout-webui"

# Garde le log sous contrôle (~500 dernières lignes).
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 1000 ]; then
    tail -n 500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
