---
name: verify-jev-trader
description: "Drive the jev-trader Bun trading bot's HTTP/SSE dashboard API (GET /, /history, /events) in local dry-run to prove snapshot, history, live block stream, and simulated quote behavior. Use when verifying jev-trader after changes to src/server.ts, src/trader.ts, the SSE contract, or before shipping a dry-run deploy."
---

# Verify jev-trader

Agent-facing control skill for **jev-trader**: a Bun trading bot that decides buy/sell every Monad block and exposes a small HTTP/SSE surface. Prefer this over the Next.js app under `web/` — that dashboard is an SSE consumer of the same API (defaulting to the Railway dry-run URL). Prove the bot through HTTP/SSE first.

Repo root for all commands below: the checkout that contains `package.json` with `"name": "jev-trader"` (this skill lives at `.cursor/skills/verify-jev-trader/`).

## Launch

Dry-run only for verification (`DRY_RUN=true`, no `PRIVATE_KEY`). Real Kuru book + mock (or jev) decisions; quotes are `status: "sim"` and never hit the chain.

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# from repo root:
export RUN_ID="manual-$(date +%Y%m%d-%H%M%S)"
export PORT=3847   # avoid 3000 (default bot + Next web)
.cursor/skills/verify-jev-trader/scripts/launch.sh
```

What it does:

1. Creates `.env` from `.env.example` if missing (verification scaffolding).
2. Runs `bun install` if `node_modules` is absent.
3. Starts `PORT=$PORT DRY_RUN=true MODEL=mock bun run start` in the background.
4. Writes `/tmp/jev-trader-verify-$RUN_ID/{pid,port,base_url,launch.log,run_id,repo_root}`.
5. Waits until stdout contains `jev-trader ·` and `GET http://127.0.0.1:$PORT/` returns JSON with `startedAt`.

**Readiness signal:** log line `jev-trader · model=… · DRY RUN · … · :$PORT` plus HTTP 200 JSON from `/`.

**Do not** launch a second instance on the same `PORT`. The helper refuses if the port already answers. Do not drive a shared instance you did not start (especially not production `https://jev-trader-production.up.railway.app` for mutation-style proofs — read-only comparison against it is fine).

Teardown is `scripts/cleanup.sh` (see Cleanup). Launch leaves `data/events.jsonl` under the repo (append-only product scratch); cleanup does not delete it.

## Doctor

Run before every drive when anything looks off:

```bash
.cursor/skills/verify-jev-trader/scripts/doctor.sh "$RUN_ID"
# or, for the newest run dir:
.cursor/skills/verify-jev-trader/scripts/doctor.sh
```

Pass criteria:

- PID from the run dir is alive.
- `GET $BASE/` returns JSON with `model`, `dryRun: true`, `market`, `startedAt`.
- Refuses if `dryRun` is not `true` (never drive a live-wallet instance via this skill).

If `latest` is still `null` after ~10 s, Monad RPC (`READ_RPC_URL` / `RPC_URL` in `.env`) is likely unreachable — fix network/RPC before treating features as failed.

## Drive

Harness: **curl / HTTP + SSE** (helpers below). Stable handles are route paths `/`, `/history`, `/events` and SSE event names `snapshot`, `block`, `quote`, `fill`, `ping`.

```bash
ART=".cursor/skills/verify-jev-trader/artifacts"

# Snapshot
.cursor/skills/verify-jev-trader/scripts/drive-get.sh / \
  "$ART/snapshot/snapshot.json" "$RUN_ID"

# History
.cursor/skills/verify-jev-trader/scripts/drive-get.sh /history \
  "$ART/history/history.json" "$RUN_ID"

# SSE (~8 s of stream: expect snapshot then block events)
.cursor/skills/verify-jev-trader/scripts/capture-sse.sh 8 \
  "$ART/sse-events/events.sse.txt" "$RUN_ID"
```

Raw equivalents (same contracts):

```bash
BASE="http://127.0.0.1:${PORT}"
curl -sS "$BASE/" | tee "$ART/snapshot/snapshot.json"
curl -sS "$BASE/history" | tee "$ART/history/history.json"
timeout 8 curl -sSN -H 'Accept: text/event-stream' "$BASE/events" \
  | tee "$ART/sse-events/events.sse.txt"
```

Feature recipes live in [`features/`](./features/README.md). Drive one mapped feature end-to-end per proof; use the map so later runs cover the rest.

The Next.js UI (`web/`, `bun run dev`, `NEXT_PUBLIC_API_URL` pointing at your local `BASE`) is optional secondary surface. Only open a browser when the change under test is in `web/src/**`; still require the API doctor to pass first.

## Evidence

Proof artifacts go under:

```text
.cursor/skills/verify-jev-trader/artifacts/<feature-id>/
```

Standards:

- Exercise real user paths (`GET /`, `/history`, `/events`) — not internal setters or test-only hooks (there are none on the HTTP surface).
- Capture the request and the resulting body/stream, not only a final summary.
- For dry-run, observe what it skips: quote `status` must be `"sim"`, `wallet` null, no live `txHash` on quotes; it still talks to Monad RPC for books/blocks (network use is expected).
- Side effects: in-memory history grows (`/history` length); optional append to repo `data/events.jsonl` (do not delete as "cleanup").
- Record feature ID and entry point with each artifact (filename under the feature dir is enough).

## Cleanup

```bash
.cursor/skills/verify-jev-trader/scripts/cleanup.sh "$RUN_ID"
```

Kills **only** the PID recorded at launch, removes `/tmp/jev-trader-verify-$RUN_ID`, and **leaves** `.cursor/skills/verify-jev-trader/artifacts/` intact. Never `pkill -f jev-trader` / `killall bun`. After cleanup, confirm evidence files still exist at the paths you wrote.

Does not remove `.env`, `node_modules`, or `data/events.jsonl`.

## Helpers

All under `.cursor/skills/verify-jev-trader/scripts/` (executable):

| Script | Invocation | Purpose |
|--------|------------|---------|
| `launch.sh` | `RUN_ID=… PORT=3847 .cursor/skills/verify-jev-trader/scripts/launch.sh` | Start dry-run bot; print `run_dir` / pid / base URL |
| `doctor.sh` | `.cursor/skills/verify-jev-trader/scripts/doctor.sh [RUN_ID]` | Read-only "worth driving?" check |
| `drive-get.sh` | `.cursor/skills/verify-jev-trader/scripts/drive-get.sh <path> <out.json> [RUN_ID]` | GET path → evidence JSON file |
| `capture-sse.sh` | `.cursor/skills/verify-jev-trader/scripts/capture-sse.sh <sec> <out.sse.txt> [RUN_ID]` | Capture `/events` SSE for N seconds |
| `cleanup.sh` | `.cursor/skills/verify-jev-trader/scripts/cleanup.sh [RUN_ID]` | Stop launched PID; keep artifacts |

## Isolate

- **Port:** set `PORT` (default verify port `3847`). Two instances need two ports.
- **Data:** `data/events.jsonl` is cwd-relative under the repo; concurrent runs from the same checkout append to the same file. Prefer one verify run at a time per checkout, or accept shared append-only logs.
- **Env:** verification forces `DRY_RUN=true` via `launch.sh`. Do not export a real `PRIVATE_KEY` into the verify launch.

## Maintenance

Keep the feature map honest with `/maintain-verification-skill` as routes or SSE event shapes change.
