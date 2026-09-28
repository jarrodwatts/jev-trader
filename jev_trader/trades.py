"""
Recent trade prints for a Kuru order book, via eth_getLogs on the `Trade` event.

Trade(uint40 orderId, address makerAddress, bool isBuy, uint256 price, uint96 updatedSize,
      address takerAddress, address txOrigin, uint96 filledSize), all fields non-indexed.
`isBuy` is the TAKER (aggressor) side: the SDK's reconcileTradeEvent treats isBuy as
"trader is buying, AMM/maker is selling (ask side)".

Scaling, verified against the live book (event price 0.021953 vs book mid 0.02197, size 22600 =
a visible L2 level): `price` is 1e18-scaled REGARDLESS of market pricePrecision (the SDK's
reconcileTradeEvent also uses formatUnits(price, 18)); `filledSize` is scaled by sizePrecision.
On MON-USDC log10(pricePrecision) is 8, so do NOT pass that as price_dec.

The same logs tell us when one of OUR resting orders was hit: `makerAddress` is us. Those are
collected separately as maker fills (with `updated_size` so a fully filled order can be dropped).
"""
from dataclasses import dataclass
from typing import Literal

from .abi import topic
from .book import to_float
from .chain import rpc

TRADE_EVENT_SIG = "Trade(uint40,address,bool,uint256,uint96,address,address,uint96)"
TRADE_TOPIC0 = topic(TRADE_EVENT_SIG)  # 0xf16924fb...


@dataclass
class TradePrint:
    block: int
    price: float
    size: float
    side: Literal["buy", "sell"]


@dataclass
class MakerFill:
    """One of our resting orders got hit. `side` is OUR side (the maker's): a taker buy fills our ask, so side is "sell"."""
    block: int
    tx_hash: str
    order_id: int
    price: float
    size: float
    updated_size: float
    side: Literal["buy", "sell"]


# Trade.price is a 1e18 fixed-point number on every Kuru market.
TRADE_PRICE_DEC = 18
RING = 500
FIRST_LOOKBACK = 300
# rpc.monad.xyz rejects eth_getLogs spans > 100 blocks ("eth_getLogs is limited to a 100 range", -32614).
MAX_RANGE = 100
# Never replay more than this many blocks in one poll (10 sequential getLogs calls at MAX_RANGE).
MAX_CATCHUP = 1000


class TradeFeed:
    def __init__(self, market: str, url: str, size_dec: int, price_dec: int = TRADE_PRICE_DEC, maker: str | None = None):
        """
        `size_dec` = log10(sizePrecision) (10 on MON-USDC). `price_dec` defaults to 18; override only if
        Kuru changes the event. `maker` is our wallet: Trade logs with that makerAddress become fills.
        """
        self.market = market
        self.url = url
        self.price_dec = price_dec
        self.size_dec = size_dec
        self.maker = maker.lower() if maker else None
        self.trades: list[TradePrint] = []
        self.fresh: list[TradePrint] = []  # appended since the last drain_prints()
        self.fills: list[MakerFill] = []  # appended since the last drain_fills()
        self.in_flight = False
        self.last_block = 0

    async def poll(self, block: int) -> None:
        """
        Fetch Trade logs from last_block+1..block (first call: last 300 blocks) and append, in chunks of
        MAX_RANGE. last_block advances per chunk, so a mid-way failure keeps what was fetched and retries
        the rest next time. Never raises; drops the call if a poll is already in flight.
        """
        if self.in_flight:
            return
        start = max(1, block - FIRST_LOOKBACK + 1) if self.last_block == 0 else self.last_block + 1
        if start > block:
            return
        if block - start + 1 > MAX_CATCHUP:
            start = block - MAX_CATCHUP + 1
        first = self.last_block == 0
        self.in_flight = True
        try:
            while start <= block:
                end = min(block, start + MAX_RANGE - 1)
                logs = await rpc("eth_getLogs", [{
                    "address": self.market,
                    "topics": [TRADE_TOPIC0],
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                }], self.url)
                # getLogs returns in block/logIndex order; keep newest last.
                for log in logs:
                    if log.get("removed"):
                        continue
                    t = self.decode(log, not first)  # the warm-up window predates our orders: no fills from it
                    if t:
                        self.trades.append(t)
                        self.fresh.append(t)
                if len(self.trades) > RING:
                    del self.trades[: len(self.trades) - RING]
                self.last_block = end
                start = end + 1
        except Exception:
            pass  # last_block stays at the last fully fetched chunk; next poll retries from there
        finally:
            self.in_flight = False

    def decode(self, log: dict, collect_fills: bool) -> TradePrint | None:
        data = log["data"][2:] if log["data"].startswith("0x") else log["data"]
        if len(data) < 64 * 8:
            return None
        word = lambda i: int(data[i * 64 : (i + 1) * 64], 16)
        # 0 orderId, 1 makerAddress, 2 isBuy, 3 price, 4 updatedSize, 5 takerAddress, 6 txOrigin, 7 filledSize
        is_buy = word(2) != 0
        price = to_float(word(3), self.price_dec)
        size = to_float(word(7), self.size_dec)
        if size == 0:
            return None
        block = int(log["blockNumber"], 16)
        if collect_fills and self.maker and "0x" + data[64 + 24 : 128] == self.maker:
            self.fills.append(MakerFill(
                block=block, tx_hash=log["transactionHash"], order_id=word(0), price=price, size=size,
                updated_size=to_float(word(4), self.size_dec), side="sell" if is_buy else "buy",
            ))
        return TradePrint(block, price, size, "buy" if is_buy else "sell")

    def summary(self, last_blocks: int, current_block: int) -> dict:
        """cvdMon = taker buy volume minus taker sell volume (MON)."""
        min_block = current_block - last_blocks
        count, buy_mon, sell_mon, notional = 0, 0, 0, 0
        last_price, last_side = None, None
        for t in self.trades:
            if t.block <= min_block:
                continue
            count += 1
            if t.side == "buy":
                buy_mon += t.size
            else:
                sell_mon += t.size
            notional += t.size * t.price
            last_price, last_side = t.price, t.side
        vol = buy_mon + sell_mon
        return {"count": count, "buyMon": buy_mon, "sellMon": sell_mon, "cvdMon": buy_mon - sell_mon, "vwap": notional / vol if vol > 0 else None, "lastPrice": last_price, "lastSide": last_side}

    def recent(self, n: int) -> list[TradePrint]:
        """Newest last."""
        return self.trades[-n:]

    def drain_prints(self) -> list[TradePrint]:
        """Prints appended since the last call (oldest first). Used to simulate maker fills in a dry run."""
        out, self.fresh = self.fresh, []
        return out

    def drain_fills(self) -> list[MakerFill]:
        """Our maker fills since the last call (oldest first)."""
        out, self.fills = self.fills, []
        return out
