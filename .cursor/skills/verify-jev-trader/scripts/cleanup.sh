#!/usr/bin/env bash
# Tear down a verification instance started by launch.sh. Never deletes evidence under artifacts/.
# Usage: cleanup.sh [RUN_ID]
set -euo pipefail

RUN_ID="${1:-${RUN_ID:-}}"
if [[ -z "$RUN_ID" ]]; then
  LATEST="$(ls -1dt /tmp/jev-trader-verify-* 2>/dev/null | head -n 1 || true)"
  [[ -n "$LATEST" ]] || { echo "error: no RUN_ID" >&2; exit 1; }
  RUN_DIR="$LATEST"
  RUN_ID="$(basename "$RUN_DIR" | sed 's/^jev-trader-verify-//')"
else
  RUN_DIR="${RUN_DIR:-/tmp/jev-trader-verify-$RUN_ID}"
fi

if [[ ! -d "$RUN_DIR" ]]; then
  echo "error: run dir missing: $RUN_DIR" >&2
  exit 1
fi

PID=""
if [[ -f "$RUN_DIR/pid" ]]; then
  PID="$(cat "$RUN_DIR/pid")"
fi

if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true
  # Wait briefly, then SIGKILL only that pid.
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null || true
  fi
  echo "stopped pid=$PID"
else
  echo "note: pid ${PID:-unknown} already gone"
fi

# Remove only the run scratch dir. Evidence lives under .cursor/skills/verify-jev-trader/artifacts/.
rm -rf "$RUN_DIR"
echo "removed run_dir scratch for run_id=$RUN_ID"
echo "evidence retained under .cursor/skills/verify-jev-trader/artifacts/"
