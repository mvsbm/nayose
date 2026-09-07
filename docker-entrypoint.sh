#!/bin/sh
# Docker entrypoint: chown runtime mounts (as root), then drop privileges.
set -e
if [ "$(id -u)" = "0" ]; then
    chown -R nayose:nayose /app/data /app/models 2>/dev/null || true
    exec su-exec nayose "$@"
fi
exec "$@"