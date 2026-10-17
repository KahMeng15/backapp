#!/usr/bin/env bash
# ==============================================================================
# backapp - Trigger Backup Now Helper Script
# Usage:
#   ./backup-now.sh             # Run all configured backup jobs now
#   ./backup-now.sh <job_name>  # Run a specific backup job now
# ==============================================================================

set -euo pipefail

TARGET="${1:-all}"

if command -v docker >/dev/null 2>&1 && docker compose ps backapp >/dev/null 2>&1; then
    echo "▶ Triggering backup via running Docker Compose container..."
    if [ "$TARGET" = "all" ] || [ "$TARGET" = "--all" ]; then
        docker compose exec backapp backapp run --all
    else
        docker compose exec backapp backapp run "$TARGET"
    fi
elif command -v backapp >/dev/null 2>&1; then
    echo "▶ Triggering backup via local backapp installation..."
    if [ "$TARGET" = "all" ] || [ "$TARGET" = "--all" ]; then
        backapp run --all
    else
        backapp run "$TARGET"
    fi
else
    echo "❌ Error: Neither 'docker compose' nor 'backapp' CLI was found."
    exit 1
fi
