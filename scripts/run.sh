#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TRAVEL_PYTHON="${TRAVEL_PYTHON:-$PWD/.venv/bin/python}"
if [ ! -x "$TRAVEL_PYTHON" ]; then TRAVEL_PYTHON=/Users/dhruvikothari/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3; fi
if [ ! -x "$TRAVEL_PYTHON" ]; then TRAVEL_PYTHON=python3; fi
export PYTHONPATH=src
exec "$TRAVEL_PYTHON" -m travel_recovery.server
