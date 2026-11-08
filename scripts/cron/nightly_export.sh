#!/bin/sh
# Cron entry point for scripts/nightly_export.py (ticket DEV-53).
#
# Cron runs commands with a minimal environment: no virtualenv, a bare PATH
# and the home directory as the working directory. This wrapper finds the
# repository from its own location, so the crontab line needs only this
# file's path (quoted: the checkout path may contain spaces), and appends
# everything the job prints to logs/nightly_export.log. The script reads its
# settings from the repository's .env, as the API does.
#
# Optional environment:
#   PYTHON       interpreter to use (default: the repository's .venv)
#   TARGET_DATE  YYYY-MM-DD to export instead of yesterday (UTC)
set -eu

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$REPO_DIR"
mkdir -p logs
exec "${PYTHON:-$REPO_DIR/.venv/bin/python}" scripts/nightly_export.py >> logs/nightly_export.log 2>&1
