#!/bin/bash
# Maya auto-restart wrapper. Restarts Maya if it crashes.
# Used by autostart (.desktop) and can be run manually.

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

while true; do
    echo "[$(date '+%H:%M:%S')] Iniciando Maya..."
    python3 main.py
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] Maya cerrada normalmente."
        break
    fi

    echo "[$(date '+%H:%M:%S')] Maya crasheo (exit $EXIT_CODE). Reiniciando en 5s..."
    sleep 5
done
