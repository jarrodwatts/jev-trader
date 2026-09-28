# jev-trader

One decision every Monad block. A TypeSafe Jev model watches the Kuru MON-USDC order book and answers buy or sell every ~300 ms. Every block posts a real post-only limit order on that side, one tick inside the touch, replacing the last one. Fills happen when a taker hits it, so the bot earns the spread instead of paying it. The same Python process serves a live dashboard at http://localhost:3000.

## Run

    cp .env.example .env
    python3 -m venv .venv && . .venv/bin/activate
    pip install -e .            # or -e ".[fast]" for native secp256k1 signing (Python <= 3.13)
    python -m jev_trader        # then open http://localhost:3000

Or with Docker: `docker build -t jev-trader . && docker run --env-file .env -p 3000:3000 jev-trader`.

With no `PRIVATE_KEY` it dry-runs: real book, real decisions, simulated fills. Set `MODEL=jev` and `TYPESAFE_AI_API_KEY` to use Jev; the default `mock` is a momentum heuristic stand-in.

## Endpoints

- `GET /` the dashboard (NiceGUI)
- `GET /snapshot` model, wallet, dryRun, latest block event
- `GET /history` last 1000 block events
- `GET /events` SSE: `snapshot` on connect, then one `block` event per block, plus a `fill` event whenever a live order's receipt lands

Every event (see `jev_trader/trader.py` for the shape):

    {
      "block": 105488269, "ts": 1789593630676,
      "mid": 0.022636, "bestBid": 0.022628, "bestAsk": 0.022644, "spreadBps": 7.07,
      "decision": { "action": "buy", "probabilities": { "buy": 0.77, "sell": 0.23, "hold": 0 }, "upIn10": 0.77, "latencyMs": 81, "late": false },
      "quote": { "side": "buy", "price": 0.022629, "size": 200, "txHash": "0x…", "gasMon": 0.0357, "cancel": [100295801], "status": "sent", "orderId": null, "capped": false },
      "fill": null,
      "resting": { "bidMon": 200, "askMon": 200 },
      "position": { "side": "short", "size": 200, "entryPrice": 0.022633, "unrealizedUsd": -0.0006, "unrealizedMon": -0.027 },
      "totals": { "blocks": 3, "decisions": 3, "quotes": 3, "fills": 1, "reverted": 0, "lateBlocks": 0, "jevUsd": 0.000004, "gasMon": 0.107, "gasUsd": 0.0024, "realizedUsd": 0, "pnlUsd": -0.003, "pnlMon": -0.13, "pnlPct": -0.003 }
    }

Every block the model is asked about the move over `HORIZON_BLOCKS` (default 100, ~30 s) and answers `buy` or `sell`. `quote` is the order that block put on the book: a post-only limit order of `TRADE_SIZE_MON` on that side, `QUOTE_INSIDE_TICKS` inside the touch (clamped to the touch when the spread is too tight), in one `batchUpdate` that also cancels everything we had resting (`cancel`). `hold` appears only with `decision.late: true`, when the model missed the block and nothing was posted. When the position cap (or, live, margin funds) blocks a side, the quote goes on the other side with `capped: true` and `probabilities` still show the model's call. `resting` is our size known to be on the book after this block. `upIn10` equals the buy probability.

Live sends are fired and forgotten, so the `block` event carries the **intent**: `status: "sent"`, `gasMon` is `gasLimit x (last known base fee + priority)`. Monad charges the gas limit, so that is the real cost whether the order lands or not. The receipt arrives a block or two later as its own SSE event:

    event: quote
    data: { "block": 105488269, "quote": { …, "status": "placed", "orderId": 100295812, "gasMon": 0.0357 } }

`status` becomes `placed` (with the order id) or `reverted` (the book moved through the price before the tx landed, or a cancelled order had already filled). No receipt after 10 blocks gives `lost`. Fills are not in our own transactions: someone else's taker order hits our resting one, and the Trade log for it arrives via the same `eth_getLogs` poll that feeds the model. Each block with fills gets its own SSE event, and `position`, `realizedUsd` and `fills` update then:

    event: fill
    data: { "block": 105488271, "fill": { "side": "buy", "size": 200, "price": 0.022629, "txHash": "0x…", "orderId": 100295812, "simulated": false } }

`txHash` is the taker's transaction. In a dry run the quote is `status: "sim"`: the order rests for one block and a real print crossing its price fills it (`simulated: true`).

## Layout

    jev_trader/__main__.py starts the web server (NiceGUI) and the bot in one event loop
    jev_trader/config.py   env (.env is loaded automatically)
    jev_trader/chain.py    block feed (WebSocket newHeads + polling backstop, newest block only), raw RPC
    jev_trader/book.py     one-eth_call order book reader (decodes getL2Book, merges the AMM vault)
    jev_trader/abi.py      the Kuru signatures we encode and decode
    jev_trader/market.py   Kuru: read book, hand-encoded batchUpdate (cancel + post-only place), margin deposits, local nonce, async confirmation
    jev_trader/model.py    Model protocol, JevModel (TypeSafe System One API), MockModel
    jev_trader/trades.py   Trade log feed: taker prints for the model, maker fills for us
    jev_trader/trader.py   the loop: one in flight, hold when late, position and P&L accounting
    jev_trader/server.py   snapshot, history and SSE endpoints on the NiceGUI (FastAPI) app
    jev_trader/dashboard/  the dashboard at /: page.py (layout, header, stats, decision panel, feed), chart.py (the SVG price flow), style.css

## The 300 ms budget

A decision and an order have to fit in one block, so the hot loop makes exactly two RPC round trips:
one `eth_call` for the book (~18 ms on the public RPC, `READ_RPC_URL`) and one `eth_sendRawTransaction`
(`RPC_URL`), which returns as soon as the tx is accepted. Nothing else is on the path — no
`eth_estimateGas` (Monad charges gas on the limit, so the limit is hardcoded or derived once at
startup), no `eth_sendRawTransactionSync` (it blocks until the tx is Proposed), no gas price lookup
(static type-2 fees: `MAX_FEE_GWEI` cap, 2 gwei priority; the effective price is base + priority).
Receipts, the fee estimate and the vault check run off the hot path on later blocks. Measured in a
dry run with the mock model: read p50 18 ms, whole loop p50 100 ms (80 ms of it the mock's inference stand-in).

    python scripts/bench_read.py      # book reader latency, vault batch, AMM ladder merge
    python scripts/dry_encode.py      # signs a buy and a sell offline, decodes the calldata back and checks it
