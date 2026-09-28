"""Warm the Trade log feed over a wide window and print what the model would see. Run: LOOKBACK=1000 python scripts/trades_smoke.py"""
import asyncio
import os
import time

from jev_trader.book import log10
from jev_trader.chain import rpc, session
from jev_trader.config import config
from jev_trader.market import get_market_params
from jev_trader.trades import TradeFeed

URL = "https://rpc.monad.xyz"


async def main():
    params = await get_market_params(URL, config.market)
    # Trade.price is 1e18-scaled on-chain (not pricePrecision, which is 1e8 here); TradeFeed defaults price_dec to 18.
    size_dec = log10(params.size_precision)  # MON-USDC: 10
    print(f"market {config.market} pricePrecision={params.price_precision} sizeDec={size_dec}")

    feed = TradeFeed(market=config.market, url=URL, size_dec=size_dec)
    block = int(await rpc("eth_blockNumber", [], URL), 16)
    # Warm up over a wider window: the feed chunks into 100-block eth_getLogs calls (public RPC limit).
    lookback = int(os.environ.get("LOOKBACK", "1000"))
    t0 = time.perf_counter()
    await feed.poll(block - lookback + 20)
    await feed.poll(block)
    print(f"block {block} lookback {lookback} polled in {(time.perf_counter() - t0) * 1000:.0f}ms lastBlock={feed.last_block}")
    print("summary(100):", feed.summary(100, block))
    print(f"summary({lookback}):", feed.summary(lookback, block))
    print("recent(5):", feed.recent(5))
    await session().close()


asyncio.run(main())
