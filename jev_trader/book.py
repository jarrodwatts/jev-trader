"""
Minimum-round-trip Kuru order book reader.

One raw JSON-RPC `eth_call` to `getL2Book()` (selector 0x46fdfbb1), decoded here. The SDK's
`getFormattedL2OrderBook` makes two sequential eth_calls (getL2Book, then getVaultParams pinned
to the returned block). On MON-USDC the Kuru AMM vault has vaultBidOrderSize == 0, so the SDK
merges zero vault levels and the second call is pure latency. If the vault ever goes live
(`vault_active(await read_vault_params(...))`), pass `vault=True`: both calls go in ONE
JSON-RPC batch (one HTTP round trip) and the AMM ladder is merged exactly as the SDK does it.

Number formatting replicates the SDK bit-for-bit (parseFloat of the decimal string, floor bids /
ceil asks to tick, group by price in the same order) so bid/ask/mid/imbalance match exactly.
"""
import math
from dataclasses import dataclass

from .abi import MAX_UINT256
from .chain import post_json
from .util import total

Level = tuple[float, float]  # (price, size)


@dataclass
class MarketParams:
    """Kuru OrderBook.getMarketParams(), as the SDK's ParamFetcher returns it."""
    price_precision: int
    size_precision: int
    base_asset_address: str
    base_asset_decimals: int
    quote_asset_address: str
    quote_asset_decimals: int
    tick_size: int
    min_size: int
    max_size: int
    taker_fee_bps: int
    maker_fee_bps: int


@dataclass
class VaultParams:
    kuru_amm_vault: str
    vault_best_bid: int
    bid_partially_filled_size: int
    vault_best_ask: int
    ask_partially_filled_size: int
    vault_bid_order_size: int
    vault_ask_order_size: int
    spread: int


@dataclass
class L2Book:
    block: int
    bids: list[Level]  # raw (unrounded) floats, on-chain order
    asks: list[Level]


@dataclass
class Book:
    block: int
    bid: float
    ask: float
    mid: float
    spread_bps: float
    imbalance: float  # (bidDepth - askDepth) / (bidDepth + askDepth) within 1% of mid. -1..1
    levels: dict[str, list[Level]]  # top 5 levels each side, best first: {"bids": [...], "asks": [...]}
    depth_bps: dict[str, dict[str, float]]  # cumulative MON depth within N bps of mid, per side


SEL_GET_L2_BOOK = "0x46fdfbb1"  # getL2Book()
SEL_GET_VAULT_PARAMS = "0x88bb4f60"  # getVaultParams()
ADDRESS_ZERO = "0x0000000000000000000000000000000000000000"


def eth_call(id: int, to: str, data: str, tag: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "method": "eth_call", "params": [{"to": to, "data": data}, tag]}


async def read_book(url: str, market: str, params: MarketParams, vault: bool = False, block_tag: str = "latest", timeout_ms: float | None = None) -> Book:
    """Read the book. One eth_call by default; one batched HTTP request with `vault=True`."""
    l2_call = eth_call(1, market, SEL_GET_L2_BOOK, block_tag)
    res = await post_json(url, [l2_call, eth_call(2, market, SEL_GET_VAULT_PARAMS, block_tag)] if vault else l2_call, timeout_ms)
    by_id = {r.get("id"): r for r in (res if isinstance(res, list) else [res])}
    l2 = by_id.get(1) or {}
    if not l2.get("result"):
        raise RuntimeError(f"getL2Book: {(l2.get('error') or {}).get('message', 'no result')}")
    vp = None
    if vault:
        v = by_id.get(2) or {}
        if not v.get("result"):
            raise RuntimeError(f"getVaultParams: {(v.get('error') or {}).get('message', 'no result')}")
        vp = decode_vault_params(v["result"])
    return build_book(l2["result"], params, vp)


async def read_vault_params(url: str, market: str, block_tag: str = "latest") -> VaultParams:
    """One eth_call. Use every N blocks to decide whether `vault=True` is needed."""
    res = await post_json(url, eth_call(1, market, SEL_GET_VAULT_PARAMS, block_tag))
    if not res.get("result"):
        raise RuntimeError(f"getVaultParams: {(res.get('error') or {}).get('message', 'no result')}")
    return decode_vault_params(res["result"])


def vault_active(v: VaultParams) -> bool:
    return v.vault_bid_order_size != 0 and v.kuru_amm_vault.lower() != ADDRESS_ZERO


# ---------------------------------------------------------------------------------------------
# Decoding


def abi_bytes_payload(abi_hex: str) -> str:
    """Unwrap ABI-encoded `bytes` return data (offset word, length word, payload) to "0x" + payload."""
    h = abi_hex[2:] if abi_hex.startswith("0x") else abi_hex
    offset = int(h[0:64], 16) * 2
    length = int(h[offset : offset + 64], 16) * 2
    return "0x" + h[offset + 64 : offset + 64 + length]


def decode_l2_book(abi_hex: str, price_dec: int, size_dec: int) -> L2Book:
    """Decode the getL2Book() return value (ABI-encoded bytes) into block number and raw levels."""
    data = abi_bytes_payload(abi_hex)
    block = int(data[2:66], 16)
    offset = 66

    def read_side() -> list[Level]:
        nonlocal offset
        out: list[Level] = []
        while offset < len(data):
            price = int(data[offset : offset + 64], 16)
            offset += 64
            if price == 0:
                break
            size = int(data[offset : offset + 64], 16)
            offset += 64
            out.append((to_float(price, price_dec), to_float(size, size_dec)))
        return out

    bids = read_side()
    asks = read_side()
    return L2Book(block, bids, asks)


def decode_vault_params(abi_hex: str) -> VaultParams:
    h = abi_hex[2:] if abi_hex.startswith("0x") else abi_hex
    word = lambda i: int(h[i * 64 : i * 64 + 64], 16)
    return VaultParams(
        kuru_amm_vault="0x" + h[24:64],
        vault_best_bid=word(1),
        bid_partially_filled_size=word(2),
        vault_best_ask=word(3),
        ask_partially_filled_size=word(4),
        vault_bid_order_size=word(5),
        vault_ask_order_size=word(6),
        spread=word(7),
    )


def to_float(x: int, decimals: int) -> float:
    """Same result as parseFloat(ethers.utils.formatUnits(x, decimals))."""
    neg = x < 0
    s = str(-x if neg else x)
    if decimals == 0:
        return -float(s) if neg else float(s)
    if len(s) <= decimals:
        s = "0" * (decimals - len(s) + 1) + s
    n = float(s[: len(s) - decimals] + "." + s[len(s) - decimals :])
    return -n if neg else n


def log10(x: int) -> int:
    """SDK log10BigNumber: digit count minus one (precisions are powers of ten)."""
    return len(str(x)) - 1


def mul_div_round(v: int, m: int, d: int) -> int:
    return (v * m + d // 2) // d


def amm_levels(v: VaultParams, size_dec: int) -> tuple[list[Level], list[Level]]:
    """Port of the SDK's getAmmPricesFromVaultParams (vault prices are 1e18-scaled)."""
    bids: list[Level] = []
    asks: list[Level] = []
    if not vault_active(v):
        return bids, asks
    sc = v.spread // 10
    first_bid = v.vault_bid_order_size - v.bid_partially_filled_size
    first_ask = v.vault_ask_order_size - v.ask_partially_filled_size
    bp, bs, ap, as_ = v.vault_best_bid, v.vault_bid_order_size, v.vault_best_ask, v.vault_ask_order_size
    for i in range(300):
        if bp == 0:
            break
        bids.append((to_float(bp, 18), to_float(first_bid if i == 0 else bs, size_dec)))
        bp = mul_div_round(bp, 1000, 1000 + sc)
        bs = mul_div_round(bs, 2000 + sc, 2000)
    for i in range(300):
        if ap >= MAX_UINT256:
            break
        asks.append((to_float(ap, 18), to_float(first_ask if i == 0 else as_, size_dec)))
        ap = mul_div_round(ap, 1000 + sc, 1000)
        as_ = mul_div_round(as_, 2000, 2000 + sc)
    return bids, asks


def group(levels: list[Level], key=lambda p: p) -> list[Level]:
    """Sum sizes per price, preserving first-seen order (SDK combinePrices / groupOrders)."""
    m: dict[float, float] = {}
    for p, s in levels:
        k = key(p)
        m[k] = m.get(k, 0) + s
    return list(m.items())


def desc(levels: list[Level]) -> list[Level]:
    return sorted(levels, key=lambda l: l[0], reverse=True)


def format_levels(l2: L2Book, params: MarketParams, vault: VaultParams | None = None) -> tuple[list[Level], list[Level]]:
    """Formatted levels exactly as Kuru.OrderBook.getFormattedL2OrderBook produces them (both sorted descending)."""
    decimals = log10(params.price_precision) - log10(params.tick_size)
    mult = 10**decimals
    floor_tick = lambda p: math.floor(p * mult) / mult
    ceil_tick = lambda p: math.ceil(p * mult) / mult
    amm_bids, amm_asks = amm_levels(vault, log10(params.size_precision)) if vault else ([], [])
    bids = desc(group(l2.bids + amm_bids))
    asks = desc(group(l2.asks + amm_asks))
    return desc(group(bids, floor_tick)), desc(group(asks, ceil_tick))


def build_book(l2_hex: str, params: MarketParams, vault: VaultParams | None = None) -> Book:
    """Build the trader's Book from raw getL2Book return data (+ optional decoded vault params)."""
    l2 = decode_l2_book(l2_hex, log10(params.price_precision), log10(params.size_precision))
    bids, asks = format_levels(l2, params, vault)
    if not bids or not asks:
        raise RuntimeError(f"empty book side at block {l2.block} (bids={len(bids)} asks={len(asks)})")
    bid, ask = bids[0][0], asks[-1][0]
    mid = (bid + ask) / 2
    near = lambda levels: total(s for p, s in levels if abs(p - mid) / mid < 0.01)
    bid_depth, ask_depth = near(bids), near(asks)
    within = lambda levels, bps: total(s for p, s in levels if (abs(p - mid) / mid) * 10_000 <= bps)
    depth_bps = {str(b): {"bid": within(bids, b), "ask": within(asks, b)} for b in (10, 25, 50)}
    return Book(
        block=l2.block, bid=bid, ask=ask, mid=mid,
        spread_bps=((ask - bid) / mid) * 10_000,
        imbalance=(bid_depth - ask_depth) / (bid_depth + ask_depth) if bid_depth + ask_depth else 0,
        levels={"bids": bids[:5], "asks": asks[-5:][::-1]},
        depth_bps=depth_bps,
    )
