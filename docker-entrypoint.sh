#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    for dir in /app/data /app/logs /app/backups; do
        mkdir -p "$dir"
        chown -R appuser:appuser "$dir"
    done
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
