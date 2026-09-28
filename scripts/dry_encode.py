"""
Prove the hand-encoded batchUpdate calldata decodes back through the Kuru ABI and that quote prices
are tick aligned and never cross, without sending anything. Builds + signs a bid and an ask with a
random wallet and decodes the signed transaction back.
Run: python scripts/dry_encode.py [rpcUrl]
"""
import asyncio
import os
import sys

from eth_account import Account

# A throwaway key, and never a dry run: this script signs (locally) but never sends. Set before
# jev_trader.config is imported.
os.environ["PRIVATE_KEY"] = Account.create().key.hex()
os.environ["DRY_RUN"] = "false"

from eth_account.typed_transactions import TypedTransaction  # noqa: E402
from hexbytes import HexBytes  # noqa: E402

from jev_trader import abi  # noqa: E402
from jev_trader.book import log10  # noqa: E402
from jev_trader.chain import session  # noqa: E402
from jev_trader.config import config  # noqa: E402
from jev_trader.market import Market, get_market_params, parse_units  # noqa: E402
from jev_trader.util import js_round  # noqa: E402

failed = False


def check(label: str, ok: bool):
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    failed |= not ok


async def main():
    rpc_url = sys.argv[1] if len(sys.argv) > 1 else config.read_rpc_url
    market = Market()
    market.params = await get_market_params(rpc_url, config.market)
    book = await market.read_book()
    size = config.trade_size_mon
    price_dec, size_dec, tick = log10(market.params.price_precision), log10(market.params.size_precision), market.params.tick_size
    print(f"market {config.market} · block {book.block} · bid {book.bid} ask {book.ask} · tick {tick / 10**price_dec} · size {size} MON · {config.quote_inside_ticks} tick inside")
    print(f"wallet {market.address} (random, unfunded)\n")

    for side in ("buy", "sell"):
        price = market.quote_price(side, book)
        cancel = [123, 456]
        signed = market.sign(market.build_tx(side, size, price, cancel))
        tx = TypedTransaction.from_bytes(HexBytes(signed)).as_dict()
        data = tx["data"].hex() if isinstance(tx["data"], bytes) else tx["data"]
        data = data[2:] if data.startswith("0x") else data
        check(f"{side} calls batchUpdate", data[:8] == abi.selector(abi.BATCH_UPDATE).hex())
        bp, bs, sp, ss, ids, post_only = abi.decode_hex(abi.arg_types(abi.BATCH_UPDATE), data[8:])
        prices, sizes, other = (bp, bs, sp) if side == "buy" else (sp, ss, bp)
        print(f"{side}: price {price} -> {list(prices)} units · size {list(sizes)} · cancel {list(ids)} · postOnly {post_only}")
        check(f"{side} one order on our side, none on the other", len(prices) == 1 and len(other) == 0)
        check(f"{side} price is on a tick", prices[0] % tick == 0)
        check(f"{side} price is {price}", prices[0] == js_round(price * 10**price_dec))
        check(f"{side} does not cross", price < book.ask if side == "buy" else price > book.bid)
        check(f"{side} is at or inside the touch", price >= book.bid if side == "buy" else price <= book.ask)
        check(f"{side} size is {size} MON", sizes[0] == parse_units(str(size), size_dec))
        check(f"{side} cancels {cancel}", list(ids) == cancel)
        check(f"{side} post only", post_only is True)
        check(f"{side} value is 0 (margin funded)", tx["value"] == 0)
        to = tx["to"].hex() if isinstance(tx["to"], bytes) else tx["to"]
        check(f"{side} type-2 to the market", tx["type"] == 2 and to.lower().removeprefix("0x") == config.market.lower().removeprefix("0x"))
        print()
    await session().close()
    print("MISMATCH" if failed else "all checks passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
