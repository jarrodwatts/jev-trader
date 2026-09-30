# jev-trader verification map

This directory is the maintained source for verifying the user-facing behavior of jev-trader's HTTP/SSE API. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch with `.cursor/skills/verify-jev-trader/scripts/launch.sh` (`PORT=3847`, `DRY_RUN=true`, `MODEL=mock` unless a feature says otherwise).
- Export `RUN_ID` from the launch output (or pass it to helpers).
- Run `.cursor/skills/verify-jev-trader/scripts/doctor.sh "$RUN_ID"` and require `dryRun=true` and a live PID.
- Base URL is `http://127.0.0.1:$PORT` from `/tmp/jev-trader-verify-$RUN_ID/base_url`.
- Never drive an instance that was not started by this verification run (do not point drives at Railway for proof artifacts).
- Monad public RPC must be reachable; without it, `latest` stays null and block features cannot pass.

## Driving conventions

- Start every recipe from a freshly doctored local dry-run instance unless its preconditions say otherwise.
- Prefer route paths (`/`, `/history`, `/events`) and SSE event names over any UI selector.
- Treat every command as literal. Keep quoted paths and flags unchanged.
- Run HTTP drives through `drive-get.sh` and SSE through `capture-sse.sh` (or the documented raw `curl` equivalents).
- Write evidence under `.cursor/skills/verify-jev-trader/artifacts/<feature-id>/`. Cleanup must not remove those files.

## Proof and skip reporting

- Capture the request path and the resulting JSON or SSE text, not only a one-line summary.
- Snapshot/history proof includes the pretty-printed response body on disk.
- SSE proof includes the raw event stream file and a count of `event:` types.
- Dry-run proof observes `quote.status == "sim"` and `wallet: null` rather than trusting the process name.
- Record the feature ID (directory name under `artifacts/`) with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition (for example RPC down → `latest` null).
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with curl/HTTP` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Snapshot](./snapshot.md) covers `GET /` meta + latest block event.
- [History](./history.md) covers `GET /history` recent block events.
- [SSE events](./sse-events.md) covers `GET /events` snapshot/block/ping stream.
- [Dry-run block loop](./dry-run-block-loop.md) covers per-block decisions and simulated quotes over live Monad blocks.
