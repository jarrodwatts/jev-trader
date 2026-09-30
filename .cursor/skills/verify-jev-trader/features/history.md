# History

History returns the recent in-memory ring of block events (up to 1000) so a client can backfill charts and feeds without waiting on SSE reconnect.

## Sub-features

- `history-list` returns a JSON array of `BlockEvent` objects oldest-to-newest.
- `history-grows` length increases as new blocks are processed.
- `history-cap` never exceeds 1000 events (observe only on long runs).

## How to get to it (user POV)

- Request `GET http://127.0.0.1:$PORT/history`.
- Reconnect a dashboard that missed SSE traffic and hydrate from history (the SSE `snapshot` also embeds history; this route is the explicit pull API).

## Driving it with curl/HTTP

Preconditions:

- Doctor is green for the local dry-run instance.
- At least a few blocks have been processed (`GET /` shows non-null `latest`), or wait ~5 s after launch.
- Evidence path `.cursor/skills/verify-jev-trader/artifacts/history/` is writable.

- **Fetch history.** Run `.cursor/skills/verify-jev-trader/scripts/drive-get.sh /history .cursor/skills/verify-jev-trader/artifacts/history/history.json "$RUN_ID"`. HTTP `200`; body is a JSON array.
- **Assert element shape.** Require length ≥ 1 after warm-up. Each element includes `block`, `decision`, `quote`, `totals`. Dry-run quotes use `status: "sim"`.
- **Assert ordering.** Events are append-ordered (timestamps non-decreasing). Block numbers are usually increasing but may briefly invert by one when a late `hold` is emitted before an in-flight block finishes — that is expected, not a regression.
- **Observe growth.** Capture once, wait ~3 s, capture again to `.cursor/skills/verify-jev-trader/artifacts/history/history-later.json`. The second array's length is ≥ the first (unless already at the 1000 cap).
- **Proof.** Retain both JSON files under `artifacts/history/` through cleanup.

## Gotchas

- An empty array right after launch is a timing issue, not a route bug — wait for the first `#<block>` log line or non-null `latest`.
- A late `hold` can appear in the array before the still-in-flight earlier block, so do not require strictly sorted block numbers.
- History is process-local memory; cleanup/restart clears it. Do not expect persistence across launches.
- Very long captures approaching 1000 events will stop growing; treat that as the cap, not a regression, if `totals.blocks` on the last event is ≥ 1000.
