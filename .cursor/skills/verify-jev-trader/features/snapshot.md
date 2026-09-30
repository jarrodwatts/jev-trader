# Snapshot

Snapshot is the one-shot status document a client (dashboard, curl, or operator) fetches to learn which model is running, whether the bot is in dry-run, which market it trades, when it started, and the newest block event if any.

## Sub-features

- `snapshot-meta` returns `model`, `wallet`, `dryRun`, `market`, and `startedAt`.
- `snapshot-latest` includes `latest` as the newest `BlockEvent` or `null` before the first block.
- `snapshot-cors` answers `OPTIONS /` with CORS allow headers for browser clients.

## How to get to it (user POV)

- Open `GET http://127.0.0.1:$PORT/` in a browser or HTTP client.
- Point the dashboard's API base at the local bot (the web app itself uses `/events`, but operators still use `/` for a quick check).
- Hit the same path on a known dry-run deploy for read-only comparison only (not for local proof artifacts).

## Driving it with curl/HTTP

Preconditions:

- Local dry-run instance is healthy per `doctor.sh` at `http://127.0.0.1:$PORT`.
- `RUN_ID` matches the launched run dir.
- Evidence directory `.cursor/skills/verify-jev-trader/artifacts/snapshot/` may be created by the helper.

- **Fetch snapshot.** Request the root path. Run `.cursor/skills/verify-jev-trader/scripts/drive-get.sh / .cursor/skills/verify-jev-trader/artifacts/snapshot/snapshot.json "$RUN_ID"`. HTTP status is `200` and the file is JSON.
- **Assert meta.** Read the evidence file. Require `dryRun` is `true`, `wallet` is `null`, `model` is `mock` (unless you launched with another `MODEL`), and `market` is a non-empty `0x…` address.
- **Assert latest shape.** If `latest` is not `null`, require keys `block`, `ts`, `mid`, `decision`, `quote`, `position`, `totals`. In dry-run, when `quote` is present, require `quote.status` is `"sim"`.
- **OPTIONS (optional).** Run `curl -sS -D - -o /dev/null -X OPTIONS "http://127.0.0.1:$PORT/" | tee .cursor/skills/verify-jev-trader/artifacts/snapshot/options.headers.txt`. Response includes `access-control-allow-origin`.
- **Proof.** Keep `artifacts/snapshot/snapshot.json` after cleanup. It must still exist and parse as JSON with `dryRun: true`.

## Gotchas

- Immediately after launch, `latest` may be `null` for one or two seconds while the first Monad block arrives — wait and re-GET before failing meta checks.
- Production Railway returns the same shape; do not save production responses as proof that *this* checkout works.
- `doctor.sh` fails closed if `dryRun` is false so a live wallet cannot be treated as a verify target.
