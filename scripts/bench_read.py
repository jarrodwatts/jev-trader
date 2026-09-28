"""
Book reader latency and vault merge.
  1. Live: one eth_call (read_book) vs the vault batch (getL2Book + getVaultParams in one HTTP request).
  2. Same raw getL2Book bytes with the live vault params and without: identical while the vault is idle.
  3. A synthetic ACTIVE vault merged into a real book, to show the AMM ladder moves the touch.
Exactness against the Kuru SDK's getFormattedL2OrderBook was verified on the TypeScript version, and the
Python port against that on live bytes.
Run: python scripts/bench_read.py [rpcUrl] [reads]
"""
import asyncio
import sys
import time
from decimal import Decimal

from jev_trader.book import SEL_GET_L2_BOOK, SEL_GET_VAULT_PARAMS, Book, build_book, decode_vault_params, read_book, read_vault_params, vault_active
from jev_trader.chain import rpc, session
from jev_trader.market import get_market_params

RPC = sys.argv[1] if len(sys.argv) > 1 else "https://rpc.monad.xyz"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 10
MARKET = "0x065C9d28E428A0db40191a54d33d5b7c71a9C394"


def fmt(b: Book) -> str:
    return f"blk={b.block} bid={b.bid} ask={b.ask} mid={b.mid} spr={b.spread_bps:.3f} imb={b.imbalance:.6f}"


def same(a: Book, b: Book) -> bool:
    return (a.block, a.bid, a.ask, a.mid, a.spread_bps, a.imbalance) == (b.block, b.bid, b.ask, b.mid, b.spread_bps, b.imbalance)


def stats(xs: list[float]) -> str:
    s = sorted(xs)
    return f"min={s[0]:.0f} p50={s[len(s) >> 1]:.0f} mean={sum(xs) / len(xs):.0f} max={s[-1]:.0f} ms"


async def call(data: str) -> str:
    for attempt in range(6):
        try:
            return await rpc("eth_call", [{"to": MARKET, "data": data}, "latest"], RPC)
        except Exception as e:
            print(f"   (rpc error: {str(e)[:100]}; retry {attempt + 1})")
            await asyncio.sleep(0.5)  # public RPC rate limit
    raise RuntimeError("rpc failed 6x")


async def main():
    params = await get_market_params(RPC, MARKET)
    vp = await read_vault_params(RPC, MARKET)
    print("vault:", vp.kuru_amm_vault, "bidSize", vp.vault_bid_order_size, "askSize", vp.vault_ask_order_size, "active:", vault_active(vp))

    print(f"\n== live reads x{N} (single eth_call -> vault batch)")
    await read_book(RPC, MARKET, params)  # warm-up (TLS/keepalive)
    t_one, t_batch = [], []
    for i in range(N):
        t = time.perf_counter(); one = await read_book(RPC, MARKET, params); t_one.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); batch = await read_book(RPC, MARKET, params, vault=True); t_batch.append((time.perf_counter() - t) * 1000)
        print(f"#{i} one {t_one[-1]:.0f}ms | batch {t_batch[-1]:.0f}ms   {fmt(one)}")
        await asyncio.sleep(0.15)
    print(f"latency single eth_call:      {stats(t_one)}")
    print(f"latency vault batch (1 http): {stats(t_batch)}")

    print("\n== same raw getL2Book bytes: live vault params merged vs no vault")
    ok = 0
    for i in range(N):
        await asyncio.sleep(0.15)
        l2_raw, v_raw = await call(SEL_GET_L2_BOOK), await call(SEL_GET_VAULT_PARAMS)
        with_vault, without = build_book(l2_raw, params, decode_vault_params(v_raw)), build_book(l2_raw, params)
        r = same(with_vault, without) or vault_active(decode_vault_params(v_raw))
        ok += r
        print(f"#{i} {'MATCH' if r else 'DIFF!'} {fmt(with_vault)}")
    print(f"idle-vault matches: {ok}/{N}")

    print("\n== synthetic active vault (sizes > 0) merged into a real book")
    l2_raw = await call(SEL_GET_L2_BOOK)
    base = build_book(l2_raw, params)
    words = [
        int("838c2d3fD4DB5eB2F185cbE7697fbaAce52b34d7", 16),
        int(Decimal(f"{base.bid * 1.0002:.18f}").scaleb(18)),  # vault best bid slightly inside the book
        0,
        int(Decimal(f"{base.ask * 0.9998:.18f}").scaleb(18)),
        123456789,
        5000000000000,  # vaultBidOrderSize (sizePrecision units)
        4800000000000,
        30,
    ]
    fake = decode_vault_params("0x" + "".join(f"{w:064x}" for w in words))
    print("   vault  ", fmt(build_book(l2_raw, params, fake)), "\n   no vault", fmt(base))
    await session().close()


asyncio.run(main())
