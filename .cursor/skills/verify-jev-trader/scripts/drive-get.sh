#!/usr/bin/env bash
# GET a path on the verification instance and write the body to an evidence file.
# Usage: drive-get.sh <path> <evidence-out.json> [RUN_ID]
set -euo pipefail

PATH_ARG="${1:?path required, e.g. / or /history}"
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
HTTP_CODE="$(curl -sS -o "$OUT" -w '%{http_code}' --connect-timeout 5 "${BASE}${PATH_ARG}")"
echo "GET ${BASE}${PATH_ARG} -> HTTP ${HTTP_CODE} wrote $OUT"
[[ "$HTTP_CODE" == "200" ]] || exit 1
# pretty-print in place when JSON
python3 - "$OUT" <<'PY' || true
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
raw = p.read_text()
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
p.write_text(json.dumps(data, indent=2) + "\n")
PY
