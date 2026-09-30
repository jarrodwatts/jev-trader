# SSE events

The event stream is the live dashboard feed: on connect the server sends a `snapshot` (meta + history), then `block` events as the trader finishes each Monad block, occasional `quote` / `fill` updates, and `ping` keepalives about every 15 seconds.

## Sub-features

- `sse-snapshot` first event after connect is `event: snapshot` with meta and `history`.
- `sse-block` subsequent `event: block` payloads carry a full `BlockEvent`.
- `sse-ping` `event: ping` arrives on a long-enough capture (~15 s+).
- `sse-quote-fill` (live or dry-run) may emit `event: quote` or `event: fill` when receipts/sim fills land — optional on short captures.

## How to get to it (user POV)

- Open `GET http://127.0.0.1:$PORT/events` with `Accept: text/event-stream` (EventSource in the browser, `curl -N` in a terminal).
- Load the Next.js dashboard with `NEXT_PUBLIC_API_URL` set to the local base URL (it uses EventSource on `/events`).

## Driving it with curl/HTTP

Preconditions:

- Doctor is green; RPC is delivering blocks (`latest` non-null).
- Capture window long enough for at least one `block` after `snapshot` (8 s is usually enough on Monad ~300 ms blocks).

- **Connect and capture.** Run `.cursor/skills/verify-jev-trader/scripts/capture-sse.sh 8 .cursor/skills/verify-jev-trader/artifacts/sse-events/events.sse.txt "$RUN_ID"`. The file is non-empty.
- **Assert snapshot first.** The first `event:` line is `snapshot`. Its `data:` JSON includes `dryRun: true` and a `history` array.
- **Assert blocks.** At least one `event: block` appears. A `data:` line for a block parses as JSON with numeric `block` and a `decision` or `late` path.
- **Optional ping.** For a 20 s capture to `artifacts/sse-events/events-long.sse.txt`, expect `event: ping` at least once.
- **Proof.** Keep the `.sse.txt` file(s) under `artifacts/sse-events/` after cleanup; helper stdout summary of event counts is supporting context, not a substitute for the file.

## Gotchas

- `curl` without `-N`/`--no-buffer` may delay event delivery; always use the helper or `curl -sSN`.
- `timeout` exits 124 when the timer fires — that is success if the file has data.
- Closing the client removes it from the server's set; reconnects get a fresh `snapshot`, not a resume token.
- Short captures often miss `ping` (15 s interval) and may miss `fill` — do not fail `sse-block` proofs for missing ping/fill.
