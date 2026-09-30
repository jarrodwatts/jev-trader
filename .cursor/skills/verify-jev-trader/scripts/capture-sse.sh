#!/usr/bin/env bash
# Capture SSE from GET /events for SECONDS seconds into an evidence file.
# Usage: capture-sse.sh <seconds> <evidence-out.sse.txt> [RUN_ID]
set -euo pipefail

SECONDS_ARG="${1:?seconds required}"
OUT="${2:?evidence output path required}"
RUN_ID="${3:-${RUN_ID:-}}"

if [[ -z "$RUN_ID" ]]; then
  LATEST="$(ls -1dt /tmp/jev-trader-verify-* 2>/dev/null | head -n 1 || true)"
  [[ -n "$LATEST" ]] || { echo "error: no RUN_ID" >&2; exit 1; }
  RUN_DIR="$LATEST"
else
  RUN_DIR="${RUN_DIR:-/tmp/jev-trader-verify-$RUN_ID}"
fi

BASE="$(cat "$RUN_DIR/base_url")"
mkdir -p "$(dirname "$OUT")"
# timeout exits 124 on timer — treat that as success if we got data.
set +e
timeout "${SECONDS_ARG}" curl -sSN -H 'Accept: text/event-stream' "${BASE}/events" >"$OUT"
RC=$?
set -e
if [[ ! -s "$OUT" ]]; then
  echo "error: empty SSE capture (curl rc=$RC)" >&2
  exit 1
fi
# Summarize event types for the agent.
python3 - "$OUT" <<'PY'
import sys, collections
path = sys.argv[1]
counts = collections.Counter()
blocks = 0
with open(path) as f:
    for line in f:
        if line.startswith("event:"):
            counts[line.split(":",1)[1].strip()] += 1
        elif line.startswith("data:") and '"block"' in line:
            blocks += 1
print(f"sse file={path} events={dict(counts)} data_lines_with_block={blocks}")
PY
echo "captured ${SECONDS_ARG}s SSE from ${BASE}/events -> $OUT (curl_rc=$RC)"
[[ $RC -eq 0 || $RC -eq 124 ]] || exit "$RC"
