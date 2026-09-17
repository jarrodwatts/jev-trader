# jev-trader

One decision every Monad block. A TypeSafe Jev model watches the Kuru MON-USDC order book and answers buy or sell every ~300 ms. Every block posts a real post-only limit order on that side, one tick inside the touch, replacing the last one. Fills happen when a taker hits it, so the bot earns the spread instead of paying it. A small server streams every block to the dashboard.

## Run

    cp .env.example .env
    bun install
    bun run start

With no `PRIVATE_KEY` it dry-runs: real book, real decisions, simulated fills. Set `MODEL=jev` and `TYPESAFE_AI_API_KEY` to use Jev; the default `mock` is a momentum heuristic stand-in.

## Endpoints

Deployed (dry run, mock model): https://jev-trader-production.up.railway.app

- `GET /` snapshot: model, wallet, dryRun, latest block event
- `GET /history` last 1000 block events
- `GET /events` SSE: `snapshot` on connect, then one `block` event per block, plus a `fill` event whenever a live order's receipt lands

Every event (see `src/trader.ts` for types):

    {
      "block": 105488269, "ts": 1789593630676,
      "mid": 0.022636, "bestBid": 0.022628, "bestAsk": 0.022644, "spreadBps": 7.07,
      "decision": { "action": "buy", "probabilities": { "buy": 0.77, "sell": 0.23, "hold": 0 }, "upIn10": 0.77, "latencyMs": 81, "late": false },
      "quote": { "side": "buy", "price": 0.022629, "size": 200, "txHash": "0x…", "gasMon": 0.0357, "cancel": [100295801], "status": "sent", "orderId": null, "capped": false },
      "fill": null,
      "resting": { "bidMon": 200, "askMon": 200 },
      "position": { "side": "short", "size": 200, "entryPrice": 0.022633, "unrealizedUsd": -0.0006, "unrealizedMon": -0.027 },
      "totals": { "blocks": 3, "decisions": 3, "quotes": 3, "fills": 1, "reverted": 0, "lateBlocks": 0, "riskSkips": 0, "jevUsd": 0.000004, "gasMon": 0.107, "gasMonUnconfirmed": 0, "gasUsd": 0.0024, "realizedUsd": 0, "pnlUsd": -0.003, "pnlMon": -0.13, "pnlPct": -0.003 },
      "risk": null
    }

Every block the model is asked about the move over `HORIZON_BLOCKS` (default 100, ~30 s) and answers `buy` or `sell`. `quote` is the order that block put on the book: a post-only limit order of `TRADE_SIZE_MON` on that side, `QUOTE_INSIDE_TICKS` inside the touch (clamped to the touch when the spread is too tight), in one `batchUpdate` that also cancels everything we had resting (`cancel`). `hold` appears only with `decision.late: true`, when the model missed the block and nothing was posted. When the position cap (or, live, margin funds) blocks a side, the quote goes on the other side with `capped: true` and `probabilities` still show the model's call; with the risk layer on, a blocked side is a skipped block instead (`totals.riskSkips`). `resting` is our size known to be on the book after this block. `upIn10` equals the buy probability.

Live sends are fired and forgotten, so the `block` event carries the **intent**: `status: "sent"`, `gasMon` is `gasLimit x (last known base fee + priority)`. Monad charges the gas limit, so that is the real cost whether the order lands or not. The receipt arrives a block or two later as its own SSE event:

    event: quote
    data: { "block": 105488269, "quote": { …, "status": "placed", "orderId": 100295812, "gasMon": 0.0357 } }

`status` becomes `placed` (with the order id) or `reverted` (the book moved through the price before the tx landed, or a cancelled order had already filled). No receipt after 10 blocks gives `lost`. Fills are not in our own transactions: someone else's taker order hits our resting one, and the Trade log for it arrives via the same `eth_getLogs` poll that feeds the model. Each block with fills gets its own SSE event, and `position`, `realizedUsd` and `fills` update then:

    event: fill
    data: { "block": 105488271, "fill": { "side": "buy", "size": 200, "price": 0.022629, "txHash": "0x…", "orderId": 100295812, "simulated": false } }

`txHash` is the taker's transaction. In a dry run the quote is `status: "sim"`: the order rests for one block and a real print crossing its price fills it (`simulated: true`).

## Layout

    src/config.ts   env
    src/chain.ts    block feed (WebSocket newHeads + polling backstop, newest block only), raw RPC
    src/book.ts     one-eth_call order book reader (decodes getL2Book, merges the AMM vault)
    src/market.ts   Kuru: read book, hand-encoded batchUpdate (cancel + post-only place), margin deposits, local nonce, async confirmation
    src/model.ts    Model interface, JevModel (AI SDK experimental_evaluate), MockModel
    src/trader.ts   the loop: one in flight, hold when late, position and P&L accounting
    src/risk.ts     opt-in limiter: gas budget, loss breaker, edge check, stand-down at the cap
    src/server.ts   Bun.serve: snapshot, history, SSE

## Risk

Off by default, `RISK_ENABLED=true` to turn it on (`src/risk.ts`). The demo posts on every block on
purpose, so the limiter is opt-in: with it off, nothing changes. With it on, the loop stands down
instead of trading through a budget.

    risk: on
      gas      0.03570 MON/block, 428 MON/hour, caps 100/hour and 2000 total
      loss     stop at -$50 (hard: needs resume())
      edge     need 0 bps minimum; a 200 MON quote pays 1.78 bps of gas
      reverts  stop above 60% of the last 200 quotes
      late     stop after 60 blocks with no decision; transient stops pause 30 min
      at cap   stand down

Monad charges the gas *limit*, so posting an order and cancelling it cost the same, landed or
reverted: at the default 350,000 limit that is 0.0357 MON per block and 428 MON per hour whether the
order ever rests or not. The limiter is the only thing in the process with an opinion about that.

- **gas** — stop when an hour, or the whole run, has spent its budget (`RISK_MAX_GAS_MON_PER_HOUR`,
  `RISK_MAX_GAS_MON`). Sends whose receipt never arrives are charged too: they went out.
- **loss** — stop at a P&L line (`RISK_MAX_LOSS_USD`). Spent bankroll and spent lifetime gas are *hard*
  stops: they stay stopped until `resume()` is called, or the limit is raised and the process restarted.
- **edge** — skip a block whose quote cannot pay for its own gas. A 200 MON post one tick inside a 2 bps
  book captures ~0.57 bps of notional; the block costs ~1.78 bps. `RISK_MIN_EDGE_BPS=0` requires the fill
  to cover its own block, a positive value demands a margin on top, a negative one switches the check off.
- **reverts** — a quote that reverted (the book moved through the price before the tx landed) paid full
  price for nothing. Stop when most of the last `RISK_REVERT_WINDOW` quotes did.
- **late** — a block where the model missed 300 ms posts nothing, which is free. A long run of them means
  the loop is guessing, not trading.
- **counter trade** — when the position cap blocks the model's side, the loop can post the opposite one
  (`capped: true`). With the limiter on, the default is to stand down for that block.

`data/risk.json` keeps the gas spent and any open stop across restarts, so a restart does not spend the
same budget twice. The same numbers ride the block event as `risk`, so a dashboard can say why the loop
went quiet. `bun test` covers the limiter (20 cases, no network).

## The 300 ms budget

A decision and an order have to fit in one block, so the hot loop makes exactly two RPC round trips:
one `eth_call` for the book (~18 ms on the public RPC, `READ_RPC_URL`) and one `eth_sendRawTransaction`
(`RPC_URL`), which returns as soon as the tx is accepted. Nothing else is on the path — no
`eth_estimateGas` (Monad charges gas on the limit, so the limit is hardcoded or derived once at
startup), no `eth_sendRawTransactionSync` (it blocks until the tx is Proposed), no gas price lookup
(static type-2 fees: `MAX_FEE_GWEI` cap, 2 gwei priority; the effective price is base + priority).
Receipts, the fee estimate and the vault check run off the hot path on later blocks. Measured in a
dry run with the mock model: read p50 18 ms, whole loop p50 100 ms (80 ms of it the mock's inference stand-in).

    bun run scripts/bench-read.ts     # book reader vs the SDK: exactness and latency
    bun run scripts/dry-encode.ts     # signs a buy and a sell offline, asserts the calldata matches the SDK
