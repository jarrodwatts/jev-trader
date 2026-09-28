import sys
import traceback
from pathlib import Path

from nicegui import app, ui

from .book import log10
from .chain import session, start_block_feed
from .config import config
from .dashboard import page
from .market import Fill, Market, Quote
from .model import create_model
from .server import hub
from .trader import BlockEvent, Timing, Trader
from .util import jsnum, now_ms, spawn


async def main():
    market = Market()
    await market.init()
    model = create_model()

    def on_event(e: BlockEvent, t: Timing | None):
        hub.broadcast(e)
        d = e["decision"]
        if d and not d["late"]:
            p, q = d["probabilities"], e["quote"]
            if not q:
                quote = " NO QUOTE (cap or funds on both sides)"
            else:
                sent = " (sim)" if q["status"] == "sim" else f" cancel {len(q['cancel'])} {q['txHash']}"
                quote = f" {q['side'].upper()} {jsnum(q['size'])} @ {q['price']:.6f}{' capped' if q['capped'] else ''}{sent}"
            timing = f" · read {t['readMs']}ms loop {t['loopMs']}ms" if t else ""
            print(f"#{e['block']} {e['mid']:.6f} b{p['buy'] * 100:.0f} s{p['sell'] * 100:.0f} {d['latencyMs']}ms{quote} pnl ${jsnum(e['totals']['pnlUsd'])}{timing}", flush=True)

    def on_fill(block: int, fill: Fill):
        hub.broadcast_fill(block, fill)
        how = " (sim)" if fill["simulated"] else f" order {fill['orderId']} {fill['txHash']}"
        print(f"#{block} FILL {fill['side']} {jsnum(fill['size'])} @ {fill['price']:.6f}{how}", flush=True)

    def on_quote(block: int, quote: Quote):
        hub.broadcast_quote(block, quote)
        if quote["status"] != "placed":
            print(f"#{block} {quote['status'].upper()} {quote['side']} @ {quote['price']:.6f} gas {quote['gasMon']:.6f} MON {quote['txHash']}", flush=True)

    trader = Trader(market, model, on_event, on_fill, on_quote)
    trader.attach_trade_feed(log10(market.params.size_precision))
    hub.ready(
        {"model": model.name, "wallet": market.address, "dryRun": config.dry_run, "market": config.market, "startedAt": now_ms()},
        lambda: list(trader.history),
    )
    spawn(hub.pings())

    mode = "DRY RUN" if config.dry_run else f"wallet {market.address}"
    print(f"jev-trader · model={model.name} · post-only {config.quote_inside_ticks} tick inside the touch · horizon {config.horizon_blocks} blocks · {mode} · market {config.market} · read {config.read_rpc_url} · dashboard http://{'localhost' if config.host in ('127.0.0.1', '0.0.0.0') else config.host}:{config.port}", flush=True)
    start_block_feed(lambda block: spawn(trader.on_block(block)))


async def start():
    try:
        await main()
    except Exception:
        traceback.print_exc()
        print("startup failed, shutting down", file=sys.stderr, flush=True)
        app.shutdown()


async def stop():
    await session().close()


def run():
    page.setup()
    app.on_startup(lambda: spawn(start()))
    app.on_shutdown(stop)
    ui.run(
        host=config.host, port=int(config.port), title="Jev Trader", favicon=Path(page.HERE / "favicon.ico"),
        reload=False, show=False, tailwind=False, show_welcome_message=False, uvicorn_logging_level="warning",
        timeout_graceful_shutdown=1,  # SSE streams never finish on their own; without this Ctrl+C waits for every viewer to leave
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
