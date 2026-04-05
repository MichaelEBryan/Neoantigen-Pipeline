#!/bin/sh
# Entrypoint wrapper that ensures /app/uploads is writable by appuser.
# Docker named volumes may be initialized as root even if the Dockerfile
# sets correct ownership, especially if the volume already exists.
#
# This script runs as root (before USER directive takes effect via
# docker-compose user: override or gosu), checks ownership, and
# then exec's the actual command as appuser.

UPLOAD_DIR="${UPLOAD_DIR:-/app/uploads}"

# If uploads dir exists but isn't writable by appuser, fix it
if [ -d "$UPLOAD_DIR" ] && [ "$(stat -c '%u' "$UPLOAD_DIR" 2>/dev/null)" != "1001" ]; then
    echo "Fixing ownership of $UPLOAD_DIR..."
    chown -R 1001:1001 "$UPLOAD_DIR" 2>/dev/null || true
fi

# Create if missing
mkdir -p "$UPLOAD_DIR" 2>/dev/null || true
chown 1001:1001 "$UPLOAD_DIR" 2>/dev/null || true

# Drop to appuser and exec the real command
exec gosu appuser "$@"
