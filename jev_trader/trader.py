import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .book import Book, Level
from .config import config
from .market import Fill, Market, Quote, QuoteResult, Side
from .model import Decision, Model, TradeState
from .trades import MakerFill, TradeFeed, TradePrint
from .util import dumps, js_round, jsnum, now_ms, perf_ms, sign, spawn, total

# BlockEvent (a plain dict, streamed to the dashboard as JSON):
#   block, ts, mid, bestBid, bestAsk, spreadBps,
#   decision: {action, probabilities, upIn10, latencyMs, late} | None,
#   quote: the order this block put on the book | None,
#   fill: maker fills that landed in this block (aggregated), attached when the trade logs for it arrive | None,
#   resting: {bidMon, askMon}, our size known to be resting on the book after this block's order,
#   position: {side, size, entryPrice, unrealizedUsd, unrealizedMon}, totals: see TOTALS
BlockEvent = dict

# Per-block latency: the book read, and read + decide + send end to end. {"readMs", "loopMs"}
Timing = dict

EARLY_FILLS_MAX = 1000
TOTALS = ("blocks", "decisions", "quotes", "fills", "reverted", "lateBlocks", "jevUsd", "gasMon", "gasUsd", "realizedUsd", "pnlUsd", "pnlMon", "pnlPct")


@dataclass
class Resting:
    side: Side
    price: float
    size: float
    block: int


@dataclass
class Position:
    mon: float = 0  # signed inventory
    cost_usd: float = 0  # and its cost basis


class Trader:
    """
    Every block: read the book, ask the model buy or sell, and post one post-only limit order on
    that side (`quote_inside_ticks` inside the touch), cancelling whatever we had resting. One request
    in flight; a block that arrives while the previous one is still running is emitted as late.

    Live sends are fire-and-forget: the block event carries the quote as `sent`; its receipt
    (`placed` with an order id, or `reverted`) is applied when it turns up on a later block. Fills
    come from the Trade log feed: a taker hit one of our resting orders. Dry runs simulate both:
    the order rests for one block and fills when a real print crosses its price.
    """

    def __init__(
        self,
        market: Market,
        model: Model,
        on_event: Callable[[BlockEvent, Timing | None], None],
        on_fill: Callable[[int, Fill], None] = lambda block, fill: None,
        on_quote: Callable[[int, Quote], None] = lambda block, quote: None,
    ):
        self.market = market
        self.model = model
        self.on_event = on_event
        self.on_fill = on_fill
        self.on_quote = on_quote
        self.history: deque[BlockEvent] = deque(maxlen=config.history_size)
        self.mids: deque[float] = deque(maxlen=400)
        self.busy = False
        self.last_book: Book | None = None
        self.trades: TradeFeed | None = None
        # Orders we know are resting on the book (live: from receipts; dry run: last block's simulated order).
        self.orders: dict[int, Resting] = {}
        # Live quotes sent but not yet confirmed; they may become resting orders, so they count toward the cap.
        self.inflight: dict[str, Quote] = {}
        # Size left on orders we saw filled before their receipt told us the order id. Without it the receipt
        # re-adds a filled order at full size, and cancelling a filled id reverts every later batchUpdate.
        self.early_fills: dict[int, float] = {}
        self.sim_id = 0
        self.position = Position()
        self.totals: dict = {k: 0 for k in TOTALS}
        Path("data").mkdir(parents=True, exist_ok=True)
        self.log = open("data/events.jsonl", "a", buffering=1)

    def attach_trade_feed(self, size_dec: int):
        """Call once the market params are known. Without it `trades` in the state is all zeros and no fills are ever seen."""
        self.trades = TradeFeed(market=config.market, url=config.read_rpc_url, size_dec=size_dec, maker=self.market.address)

    async def on_block(self, block: int):
        self.totals["blocks"] += 1
        spawn(self.confirm_pending(block))  # off the hot path: receipts for earlier blocks' sends
        if self.totals["blocks"] % config.refresh_blocks == 0:
            spawn(self.refresh())  # fee estimate + margin + vault check
        if self.busy:
            self.totals["lateBlocks"] += 1
            if self.last_book:
                self.emit(block, self.last_book, None, None, True)
            return
        self.busy = True
        t0 = perf_ms()
        try:
            book = await self.market.read_book()
            read_ms = perf_ms() - t0
            self.last_book = book
            self.mids.append(book.mid)
            if self.trades:
                spawn(self.poll_trades(block))  # off the hot path: eth_getLogs for prints (and our fills) since the last poll

            decision = await self.model.decide(self.build_state(block, book))
            wanted: Side = "sell" if decision.action == "sell" else "buy"
            other: Side = "sell" if wanted == "buy" else "buy"
            # The position cap (and, live, margin funds) can only pick the reducing side. The probabilities still show the model's call.
            side: Side | None = wanted if self.allowed(wanted, book) else other if self.allowed(other, book) else None
            self.totals["decisions"] += 1
            self.totals["jevUsd"] += (decision.input_tokens / 1e6) * config.jev_usd_per_mtok

            quote: Quote | None = None
            if side:
                decision.action = side
                cancel = [i for i in self.orders if i > 0]  # simulated orders have negative ids
                quote = await self.market.send(block, side, config.trade_size_mon, book, cancel, side != wanted)
                self.totals["quotes"] += 1
                if quote["status"] == "sim":
                    self.orders.clear()  # the simulated cancel
                    self.sim_id -= 1
                    self.orders[self.sim_id] = Resting(side, quote["price"], quote["size"], block)
                elif quote["txHash"]:
                    self.inflight[quote["txHash"]] = quote
            self.emit(block, book, decision, quote, False, {"readMs": js_round(read_ms), "loopMs": js_round(perf_ms() - t0)})
        except Exception as e:
            print(f"block {block}: {e}", file=sys.stderr)
        finally:
            self.busy = False

    async def refresh(self):
        try:
            await self.market.refresh()
        except Exception:
            pass

    async def confirm_pending(self, block: int):
        """One eth_getTransactionReceipt per in-flight tx, in parallel with this block's decision."""
        try:
            for r in await self.market.poll_pending(block):
                self.apply_quote_result(r)
        except Exception:
            pass

    async def poll_trades(self, block: int):
        await self.trades.poll(block)
        self.harvest()

    def apply_quote_result(self, r: QuoteResult):
        quote = r.quote
        if quote["txHash"]:
            self.inflight.pop(quote["txHash"], None)
        self.totals["gasMon"] += quote["gasMon"]  # charged on reverts too
        if quote["status"] == "reverted":
            self.totals["reverted"] += 1
        for oid in r.canceled:
            self.orders.pop(oid, None)
        if quote["status"] == "placed" and quote["orderId"] is not None:
            left = self.early_fills.pop(quote["orderId"], quote["size"])
            if left > 0:
                self.orders[quote["orderId"]] = Resting(quote["side"], quote["price"], left, r.block)
        e = self.find(r.block)
        if e:
            e["quote"] = quote
        self.on_quote(r.block, quote)

    def find(self, block: int) -> BlockEvent | None:
        return next((h for h in self.history if h["block"] == block), None)

    def harvest(self):
        """After each trade-log poll: apply our maker fills (live) or simulate them against the new prints (dry run)."""
        if not self.trades:
            return
        prints = self.trades.drain_prints()
        fills = self.live_fills(self.trades.drain_fills()) if self.market.wallet else self.sim_fills(prints)
        if not fills:
            return
        by_block: dict[int, list[Fill]] = {}
        for block, f in fills:
            self.apply_fill(f)
            by_block.setdefault(block, []).append(f)
        for block, fs in by_block.items():
            fill = aggregate(fs)
            e = self.find(block)
            if e:
                e["fill"] = fill
            self.on_fill(block, fill)

    def live_fills(self, raw: list[MakerFill]) -> list[tuple[int, Fill]]:
        out = []
        for f in raw:
            o = self.orders.get(f.order_id)
            if not o:
                self.early_fills[f.order_id] = f.updated_size  # the receipt has not landed yet (or the order is gone)
                if len(self.early_fills) > EARLY_FILLS_MAX:
                    del self.early_fills[next(iter(self.early_fills))]
            elif f.updated_size <= 0:
                del self.orders[f.order_id]
            else:
                o.size = f.updated_size
            out.append((f.block, Fill(side=f.side, size=f.size, price=f.price, txHash=f.tx_hash, orderId=f.order_id, simulated=False)))
        return out

    def sim_fills(self, prints: list[TradePrint]) -> list[tuple[int, Fill]]:
        """
        A simulated order placed at block N is on the book from N+1. A taker sell printing at or below
        our bid (or a taker buy at or above our ask) would have taken us first: fill up to the print's size.
        """
        out = []
        for p in prints:
            for oid, o in list(self.orders.items()):
                if p.block <= o.block or o.size <= 0:
                    continue
                hit = p.side == "sell" and p.price <= o.price if o.side == "buy" else p.side == "buy" and p.price >= o.price
                if not hit:
                    continue
                size = min(o.size, p.size)
                o.size -= size
                if o.size <= 1e-9:
                    del self.orders[oid]
                out.append((p.block, Fill(side=o.side, size=size, price=o.price, txHash=None, orderId=oid, simulated=True)))
        return out

    def resting_mon(self, side: Side) -> float:
        mon = 0
        for o in self.orders.values():
            if o.side == side:
                mon += o.size
        for q in self.inflight.values():
            if q["side"] == side:
                mon += q["size"]
        return mon

    def allowed(self, side: Side, book: Book) -> bool:
        """Would this order, and everything already resting on its side, keep us inside the cap and (live) inside margin funds?"""
        size = config.trade_size_mon
        exposure = self.position.mon + self.resting_mon("buy") + size if side == "buy" else self.position.mon - self.resting_mon("sell") - size
        if abs(exposure) > config.max_position_mon:
            return False
        if not self.market.wallet:
            return True
        # Kuru debits margin when an order is placed, so the balance already excludes what is resting.
        return self.market.margin["usdc"] >= size * book.ask if side == "buy" else self.market.margin["mon"] >= size

    def build_state(self, block: int, book: Book) -> TradeState:
        m, H = list(self.mids), config.horizon_blocks
        n = len(m)
        ret = lambda k: ((m[n - 1] - m[n - 1 - k]) / m[n - 1 - k]) * 10_000 if n > k else 0
        window = m[-H:]
        sampled = [x for i, x in enumerate(window) if (len(window) - 1 - i) % 5 == 0]  # every 5th block, newest included
        lvl = lambda l: f"{l[0]:.6f} x {jsnum(js_round(l[1], 1))}"
        empty = {"count": 0, "buyMon": 0, "sellMon": 0, "cvdMon": 0, "vwap": None, "lastPrice": None, "lastSide": None}
        depth = {k + "bps": {"bid": js_round(v["bid"], 1), "ask": js_round(v["ask"], 1)} for k, v in book.depth_bps.items()}
        return {
            "market": "MON-USDC",
            "block": block,
            "horizonBlocks": H,
            "blockMs": 300,
            "mid": book.mid,
            "spreadBps": js_round(book.spread_bps, 2),
            "bookImbalance": js_round(book.imbalance, 3),
            "depth": depth,
            "book": {"bids": [lvl(l) for l in book.levels["bids"]], "asks": [lvl(l) for l in book.levels["asks"]]},
            "returnsBps": {"last1": js_round(ret(1), 2), "last5": js_round(ret(5), 2), "last20": js_round(ret(20), 2), "last100": js_round(ret(100), 2)},
            "recentMids": " ".join(f"{x:.6f}" for x in sampled),
            "trades": self.trades.summary(H, block) if self.trades else empty,
            "recentTrades": [f"{t.block} {t.side} {jsnum(js_round(t.size, 1))} @ {t.price:.6f}" for t in (self.trades.recent(10) if self.trades else [])],
            "allowed": {"buy": self.allowed("buy", book), "sell": self.allowed("sell", book)},
        }

    def apply_fill(self, f: Fill):
        if f["size"] <= 0:
            return
        signed = f["size"] if f["side"] == "buy" else -f["size"]
        p = self.position
        if p.mon == 0 or sign(p.mon) == sign(signed):
            p.cost_usd += signed * f["price"]  # adding to position
        else:
            closing = min(abs(signed), abs(p.mon)) * sign(signed)
            entry = p.cost_usd / p.mon
            self.totals["realizedUsd"] += -closing * (f["price"] - entry)  # closing part realizes pnl
            p.cost_usd += closing * entry
            remainder = signed - closing
            p.cost_usd += remainder * f["price"]  # any flip opens the other way
        p.mon += signed
        if abs(p.mon) < 1e-9:
            p.mon = 0
            p.cost_usd = 0
        self.totals["fills"] += 1

    def entry_price(self) -> float | None:
        return self.position.cost_usd / self.position.mon if self.position.mon else None

    def unrealized_usd(self, mid: float) -> float:
        return self.position.mon * (mid - self.entry_price()) if self.position.mon else 0

    def emit(self, block: int, book: Book, decision: Decision | None, quote: Quote | None, late: bool, timing: Timing | None = None):
        t = self.totals
        t["gasUsd"] = t["gasMon"] * book.mid
        unrealized = self.unrealized_usd(book.mid)
        t["pnlUsd"] = t["realizedUsd"] + unrealized - t["gasUsd"]
        t["pnlMon"] = t["pnlUsd"] / book.mid
        t["pnlPct"] = (t["pnlUsd"] / config.bankroll_usd) * 100
        if late:
            d = {"action": "hold", "probabilities": {"buy": 0, "sell": 0, "hold": 1}, "upIn10": 0.5, "latencyMs": 0, "late": True}
        elif decision:
            d = {"action": decision.action, "probabilities": decision.probabilities, "upIn10": decision.up_in_10, "latencyMs": js_round(decision.latency_ms), "late": False}
        else:
            d = None
        event: BlockEvent = {
            "block": block, "ts": now_ms(), "mid": book.mid, "bestBid": book.bid, "bestAsk": book.ask, "spreadBps": js_round(book.spread_bps, 2),
            "decision": d,
            "quote": quote,
            "fill": None,
            "resting": {"bidMon": js_round(self.resting_mon("buy"), 1), "askMon": js_round(self.resting_mon("sell"), 1)},
            "position": {
                "side": "long" if self.position.mon > 0 else "short" if self.position.mon < 0 else "flat",
                "size": abs(self.position.mon), "entryPrice": self.entry_price(),
                "unrealizedUsd": js_round(unrealized, 4), "unrealizedMon": js_round(unrealized / book.mid, 4),
            },
            "totals": {
                **t, "jevUsd": js_round(t["jevUsd"], 6), "gasMon": js_round(t["gasMon"], 6), "gasUsd": js_round(t["gasUsd"], 6),
                "realizedUsd": js_round(t["realizedUsd"], 4), "pnlUsd": js_round(t["pnlUsd"], 4), "pnlMon": js_round(t["pnlMon"], 4), "pnlPct": js_round(t["pnlPct"], 3),
            },
        }
        self.history.append(event)
        self.log.write(dumps(event) + "\n")
        self.on_event(event, timing)


def aggregate(fills: list[Fill]) -> Fill:
    """Several fills in one block become one: total size, size-weighted price, the side with more size."""
    buy = total(f["size"] for f in fills if f["side"] == "buy")
    sell = total(f["size"] for f in fills if f["side"] == "sell")
    side: Side = "buy" if buy >= sell else "sell"
    same = [f for f in fills if f["side"] == side]
    size = total(f["size"] for f in same)
    price = total(f["size"] * f["price"] for f in same) / size
    return Fill(side=side, size=js_round(size, 4), price=price, txHash=same[0]["txHash"], orderId=same[0]["orderId"], simulated=same[0]["simulated"])
