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
PORT=""
if [[ -f "$RUN_DIR/pid" ]]; then
  PID="$(cat "$RUN_DIR/pid")"
fi
if [[ -f "$RUN_DIR/port" ]]; then
  PORT="$(cat "$RUN_DIR/port")"
fi

stop_pid() {
  local p="$1"
  [[ -n "$p" ]] || return 0
  if ! kill -0 "$p" 2>/dev/null; then
    return 0
  fi
  # Prefer process-group stop when this pid is a setsid leader; fall back to the pid alone.
  kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$p" 2>/dev/null || return 0
    sleep 0.25
  done
  kill -9 -- "-$p" 2>/dev/null || kill -9 "$p" 2>/dev/null || true
}

if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
  stop_pid "$PID"
  echo "stopped pid=$PID"
else
  echo "note: pid ${PID:-unknown} already gone"
fi

# Also stop launcher wrapper if launch recorded it separately (bun re-exec case).
if [[ -f "$RUN_DIR/launcher_pid" ]]; then
  LP="$(cat "$RUN_DIR/launcher_pid")"
  if [[ -n "$LP" && "$LP" != "$PID" ]]; then
    stop_pid "$LP"
  fi
fi

# Belt-and-suspenders: if our recorded PORT still has a listener, stop only that listener.
# Scoped to the verify PORT from the run dir — never pkill bun / killall.
listen_pid_on_port() {
  local port="$1"
  [[ -n "$port" ]] || return 0
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | awk -v p=":${port}" '
      index($0, p) {
        if (match($0, /pid=[0-9]+/)) {
          print substr($0, RSTART+4, RLENGTH-4)
          exit
        }
      }'
  fi
}
if [[ -n "$PORT" ]]; then
  EXTRA="$(listen_pid_on_port "$PORT" || true)"
  if [[ -n "${EXTRA:-}" ]]; then
    stop_pid "$EXTRA"
    echo "stopped leftover listener pid=$EXTRA on :$PORT"
  fi
fi

# Remove only the run scratch dir. Evidence lives under .cursor/skills/verify-jev-trader/artifacts/.
rm -rf "$RUN_DIR"
echo "removed run_dir scratch for run_id=$RUN_ID"
echo "evidence retained under .cursor/skills/verify-jev-trader/artifacts/"
