#!/usr/bin/env bash
set -eo pipefail

# Build the source KB once for a fresh volume, then reuse the saved SC-memory.
# The configured repo_path in nika.ini is /nika/kb.bin inside this container.
if [ "${1:-}" = "serve" ]; then
    if [ "${REBUILD_KB:-0}" = "1" ] || [ ! -s /nika/kb.bin/segments.scdb ]; then
        echo "Building the initial or explicitly requested knowledge base."
        /nika/problem-solver/sc-machine/scripts/docker_entrypoint.sh build
        export REBUILD_KB=0
    fi
fi

exec /nika/problem-solver/sc-machine/scripts/docker_entrypoint.sh "$@"
