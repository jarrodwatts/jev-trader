#!/usr/bin/env bash
# Launch jev-trader dry-run for verification. Writes run metadata under /tmp/jev-trader-verify-$RUN_ID.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-$$}"
PORT="${PORT:-3847}"
RUN_DIR="${RUN_DIR:-/tmp/jev-trader-verify-$RUN_ID}"
MODEL="${MODEL:-mock}"
DRY_RUN="${DRY_RUN:-true}"

mkdir -p "$RUN_DIR"

if curl -sf --connect-timeout 1 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
  echo "error: port ${PORT} already answers HTTP; pick another PORT or stop the other instance" >&2
  exit 1
fi

if [[ ! -f "$REPO_ROOT/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  echo "created $REPO_ROOT/.env from .env.example (verification scaffolding)"
fi

if [[ ! -d "$REPO_ROOT/node_modules" ]]; then
  (cd "$REPO_ROOT" && bun install)
fi

# Prefer dry-run for verification: no PRIVATE_KEY required; real book, simulated quotes.
# Invoke src/index.ts directly (same as package.json "start") so the recorded PID owns :PORT.
cd "$REPO_ROOT"
env PORT="$PORT" DRY_RUN="$DRY_RUN" MODEL="$MODEL" \
  bun run src/index.ts >"$RUN_DIR/launch.log" 2>&1 &
PID=$!
echo "$PID" >"$RUN_DIR/pid"
echo "$PORT" >"$RUN_DIR/port"
echo "$RUN_ID" >"$RUN_DIR/run_id"
echo "$REPO_ROOT" >"$RUN_DIR/repo_root"
printf '%s\n' "http://127.0.0.1:${PORT}" >"$RUN_DIR/base_url"

# Wait for readiness: startup log line then GET / JSON.
READY=0
for _ in $(seq 1 60); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "error: process $PID exited before ready; see $RUN_DIR/launch.log" >&2
    tail -n 40 "$RUN_DIR/launch.log" >&2 || true
    exit 1
  fi
  if grep -q "jev-trader ·" "$RUN_DIR/launch.log" 2>/dev/null \
    && curl -sf --connect-timeout 1 "http://127.0.0.1:${PORT}/" \
         | grep -q '"startedAt"'; then
    READY=1
    break
  fi
  sleep 0.5
done

if [[ "$READY" -ne 1 ]]; then
  echo "error: timed out waiting for :${PORT}; see $RUN_DIR/launch.log" >&2
  kill "$PID" 2>/dev/null || true
  exit 1
fi

echo "launched run_id=$RUN_ID pid=$PID port=$PORT base=http://127.0.0.1:${PORT}"
echo "run_dir=$RUN_DIR"
echo "log=$RUN_DIR/launch.log"
