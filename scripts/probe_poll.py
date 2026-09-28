"""How fast do blocks arrive over eth_blockNumber polling, and what does one poll cost? Run: python scripts/probe_poll.py"""
import asyncio
import time

from jev_trader.chain import rpc, session

RPC = "https://rpc.monad.xyz"


async def main():
    last, last_t, t0 = 0, 0.0, time.monotonic()
    gaps, lat = [], []
    while time.monotonic() - t0 < 4:
        a = time.monotonic()
        b = int(await rpc("eth_blockNumber", [], RPC), 16)
        lat.append((time.monotonic() - a) * 1000)
        if b != last:
            if last:
                gaps.append(round((time.monotonic() - last_t) * 1000))
            last, last_t = b, time.monotonic()
        await asyncio.sleep(0.06)
    print("rpc latency ms avg", round(sum(lat) / len(lat)), "min", round(min(lat)), "max", round(max(lat)))
    print("block gaps ms", ",".join(map(str, gaps)))
    await session().close()


asyncio.run(main())
