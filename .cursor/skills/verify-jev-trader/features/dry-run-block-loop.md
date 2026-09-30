# Dry-run block loop

In dry-run the trader still reads the real Kuru book every Monad block, asks the model buy or sell, and emits a simulated post-only quote (`status: "sim"`) plus position/totals accounting — without sending transactions or requiring a `PRIVATE_KEY`.

## Sub-features

- `loop-decision` each processed block has a `decision` with `action` in `buy`|`sell`|`hold` and `latencyMs`.
- `loop-sim-quote` non-late decisions usually include `quote` with `status: "sim"`, `size` matching `TRADE_SIZE_MON` (default 200), and `txHash: null`.
- `loop-stdout` process log lines look like `#<block> … BUY|SELL … (sim)`.
- `loop-sim-fill` when a real print crosses the resting sim quote, an SSE `fill` with `simulated: true` may appear (timing-dependent).

## How to get to it (user POV)

- Run the bot with no `PRIVATE_KEY` (or `DRY_RUN=true`) and watch stdout or the SSE/dashboard feed.
- Poll `GET /` or `/history` and inspect `latest.quote.status` and `decision`.

## Driving it with curl/HTTP

Preconditions:

- Launched via `launch.sh` with `DRY_RUN=true` and `MODEL=mock`.
- Doctor reports `dryRun=true` and eventually a non-null `latest`.
- Public Monad RPC reachable from the box.

- **Warm up.** Sleep ~5 s after launch so several blocks land.
- **Snapshot proof.** Run `.cursor/skills/verify-jev-trader/scripts/drive-get.sh / .cursor/skills/verify-jev-trader/artifacts/dry-run-block-loop/snapshot.json "$RUN_ID"`. Require `latest.decision.action` in `buy`/`sell`/`hold`, and if `latest.quote` is set then `status` is `"sim"` and `txHash` is `null`.
- **History proof.** Run `drive-get.sh /history …/artifacts/dry-run-block-loop/history.json "$RUN_ID"`. Require ≥ 3 events and a majority of non-null quotes with `status: "sim"`.
- **SSE proof.** Run `capture-sse.sh 6 …/artifacts/dry-run-block-loop/events.sse.txt "$RUN_ID"`. Require `snapshot` plus ≥ 1 `block` event whose data includes `"status":"sim"` when a quote is present.
- **Stdout sample (optional).** Copy the tail of `/tmp/jev-trader-verify-$RUN_ID/launch.log` to `artifacts/dry-run-block-loop/launch.tail.txt` and confirm lines contain `(sim)`.
- **Proof.** All three primary artifacts remain after `cleanup.sh`.

## Gotchas

- Dry-run still uses the network (book reads, block polling, trade logs). Offline boxes fail this feature even though no chain writes occur.
- `decision.late: true` yields `hold` and no quote — assert on a non-late event when checking sim quotes.
- Position caps can flip the quote side with `capped: true` while probabilities still show the model's call — assert `status`, not side equality with `decision.action`, when `capped` is true.
- Simulated fills are opportunistic; absence of `fill` on a short run is not a failure of `loop-sim-quote`.
