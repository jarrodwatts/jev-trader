Everything is Python (3.11+, asyncio): the trading bot and its live dashboard run in one process. `python -m jev_trader` serves the dashboard at http://localhost:3000.

## Backend

- Install: `python3 -m venv .venv && . .venv/bin/activate && pip install -e .`
- Run: `python -m jev_trader`. Scripts: `python scripts/<name>.py`.
- `.env` is loaded by `jev_trader/config.py` (python-dotenv).
- RPC and WebSocket clients are `aiohttp`. One shared `ClientSession` lives in `jev_trader/chain.py`; use `rpc()` / `post_json()` rather than opening new sessions.
- The web server is NiceGUI (FastAPI/uvicorn). It owns the event loop; the bot starts from `app.on_startup`. The JSON/SSE API is in `server.py`, the dashboard in `jev_trader/dashboard/`.
- Dashboard: containers are `ui.element`, text leaves are `ui.html` (its content replaces children). Long-lived elements keep CSS transitions; the chart and feed re-render as HTML each block, so one-shot animations must only be emitted on the render where their block is new.
- No hand-written JS files and no Node toolchain.
- Signing is `eth-account`, ABI encoding and decoding is `eth-abi`. Kuru signatures live in `jev_trader/abi.py`. There is no web3.py and no Kuru SDK.
- Nothing may block the event loop in the per-block hot path. Fire-and-forget work goes through `util.spawn`.
- Numbers must match what the dashboard and the original JS produced: use `util.js_round` (half up, not banker's), `util.total` (plain left-to-right sum; `sum()` compensates since 3.12) and `util.jsnum` when printing.
- Block events and the API keep camelCase keys.

## The core message (do not break this)

The demo exists to support this tweet. Every design or strategy change must keep all four claims true:

> I built a trading bot with Jev!
>
> Jev decides if it should "buy" or "sell", given the price feed of an asset pair, and executes real trades.
>
> It uses Monad to place the orders on Kuru's on-chain order book in every 300ms block.
>
> Demo link: https://jev-trader.vercel.app

Non-negotiables: Jev makes the buy/sell call (not code), from the price feed; real trades from a real wallet; an order placed on Kuru's on-chain book every 300 ms block; the demo is the live dashboard. Never decide every N blocks. No middle dots, em dashes or en dashes in any rendered text. No blinking or pulsing indicators.
