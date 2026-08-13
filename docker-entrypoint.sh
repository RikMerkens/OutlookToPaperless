#!/bin/bash
set -euo pipefail

INTERVAL_SECONDS=${RUN_INTERVAL_SECONDS:-0}

run_once() {
  python scripts/outlook_to_paperless.py "$@"
}

if [[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] && [ "$INTERVAL_SECONDS" -gt 0 ]; then
  echo "Running in loop every ${INTERVAL_SECONDS}s"
  while true; do
    if run_once "$@"; then
      :
    else
      status=$?
      echo "Run failed with exit status ${status}; retrying in ${INTERVAL_SECONDS}s" >&2
    fi
    sleep "$INTERVAL_SECONDS"
  done
else
  run_once "$@"
fi
