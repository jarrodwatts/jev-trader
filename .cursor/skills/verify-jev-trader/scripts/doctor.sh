#!/usr/bin/env bash
# Read-only health check for a verification instance. Exit 0 only when worth driving.
set -euo pipefail

RUN_ID="${1:-${RUN_ID:-}}"
if [[ -z "$RUN_ID" ]]; then
  # Pick the newest run dir if none given.
  LATEST="$(ls -1dt /tmp/jev-trader-verify-* 2>/dev/null | head -n 1 || true)"
  if [[ -z "$LATEST" ]]; then
    echo "error: no RUN_ID and no /tmp/jev-trader-verify-* dirs" >&2
    exit 1
  fi
  RUN_DIR="$LATEST"
  RUN_ID="$(basename "$RUN_DIR" | sed 's/^jev-trader-verify-//')"
else
  RUN_DIR="${RUN_DIR:-/tmp/jev-trader-verify-$RUN_ID}"
fi

PID_FILE="$RUN_DIR/pid"
PORT_FILE="$RUN_DIR/port"
BASE_FILE="$RUN_DIR/base_url"

for f in "$PID_FILE" "$PORT_FILE" "$BASE_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "error: missing $f — launch this instance with scripts/launch.sh first" >&2
    exit 1
  fi
done

PID="$(cat "$PID_FILE")"
PORT="$(cat "$PORT_FILE")"
BASE="$(cat "$BASE_FILE")"

if ! kill -0 "$PID" 2>/dev/null; then
  echo "error: pid $PID is not running (stale run dir $RUN_DIR)" >&2
  exit 1
fi

# Best-effort: note if ss shows a different pid on PORT (bun wrappers can differ). HTTP doctor below is authoritative.
if command -v ss >/dev/null 2>&1; then
  LISTENERS="$(ss -ltnp 2>/dev/null | grep ":${PORT} " || true)"
  if [[ -n "$LISTENERS" ]] && ! grep -q "pid=$PID" <<<"$LISTENERS"; then
    echo "note: ss listener for :${PORT} may be a child of pid=$PID (ok if GET / passes)"
  fi
fi

BODY="$(curl -sf --connect-timeout 2 "$BASE/")" || {
  echo "error: GET $BASE/ failed" >&2
  exit 1
}

python3 - "$BODY" "$PORT" "$PID" "$RUN_ID" "$BASE" <<'PY'
import json, sys
body, port, pid, run_id, base = sys.argv[1:6]
try:
    data = json.loads(body)
except Exception as e:
    print(f"error: GET / not JSON: {e}", file=sys.stderr)
    sys.exit(1)
need = ("model", "dryRun", "market", "startedAt")
missing = [k for k in need if k not in data]
if missing:
    print(f"error: snapshot missing keys {missing}", file=sys.stderr)
    sys.exit(1)
if data.get("dryRun") is not True:
    print("error: dryRun is not true — refuse to drive a live (wallet) instance from verify", file=sys.stderr)
    sys.exit(1)
print(f"ok run_id={run_id} pid={pid} port={port} base={base}")
print(f"model={data['model']} dryRun={data['dryRun']} market={data['market']} startedAt={data['startedAt']}")
latest = data.get("latest")
if latest is None:
    print("note: latest is null (no block yet — wait a few seconds or check RPC)")
else:
    print(f"latest.block={latest.get('block')} decision={((latest.get('decision') or {}).get('action'))} quote.status={((latest.get('quote') or {}).get('status'))}")
PY
