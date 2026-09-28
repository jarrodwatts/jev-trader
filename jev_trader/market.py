import asyncio
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, TypedDict

from eth_account import Account

from . import abi
from .book import Book, MarketParams, log10, read_book, read_vault_params, to_float, vault_active
from .chain import rpc
from .config import config
from .util import js_round

Side = Literal["buy", "sell"]


class Quote(TypedDict):
    """
    This block's order: a post-only limit order resting on Kuru's book, replacing last block's.
    `sent` until the receipt lands, then `placed` (with its orderId) or `reverted` (the book moved
    through the price, or a cancelled id had already filled). `lost` if no receipt ever came.
    """
    side: Side
    price: float  # USDC per MON, tick aligned
    size: float  # MON
    txHash: str | None
    gasMon: float  # gasLimit x gas price: Monad charges the limit, not gasUsed
    cancel: list[int]  # resting order ids this tx cancels
    status: Literal["sent", "placed", "reverted", "lost", "sim"]
    orderId: int | None
    capped: bool  # the position cap or margin funds picked this side; the model's probabilities still show its call


class Fill(TypedDict):
    """A maker fill: someone hit one of our resting orders. Arrives via the Trade log feed, not our own receipts."""
    side: Side
    size: float  # MON
    price: float  # USDC per MON: our order's price
    txHash: str | None  # the taker's transaction
    orderId: int
    simulated: bool


@dataclass
class QuoteResult:
    block: int
    quote: Quote
    canceled: list[int]


@dataclass
class Pending:
    block: int
    quote: Quote
    gas_limit: int


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
TOPIC_ORDER_CREATED = abi.topic(abi.ORDER_CREATED)
TOPIC_ORDERS_CANCELED = abi.topic(abi.ORDERS_CANCELED)


def gwei(n: float) -> int:
    return int(Decimal(str(n)) * 10**9)


def parse_units(amount: str, decimals: int) -> int:
    return int(Decimal(amount).scaleb(decimals))


async def get_market_params(url: str, market: str) -> MarketParams:
    """Kuru's ParamFetcher.getMarketParams: one eth_call, from the zero address."""
    data = abi.encode_call(abi.GET_MARKET_PARAMS, [])
    res = await rpc("eth_call", [{"from": ZERO_ADDRESS, "to": market, "data": data}, "latest"], url)
    return MarketParams(*abi.decode_hex(abi.MARKET_PARAMS_TYPES, res))


class Market:
    """Kuru MON-USDC market: read the book, post one limit order per block, confirm asynchronously."""

    def __init__(self):
        # None in a dry run (no key, or DRY_RUN=true): nothing is signed, nothing is sent.
        self.wallet = None if config.dry_run else Account.from_key(config.private_key)
        self.params: MarketParams | None = None  # public so scripts can build txs without init()
        # Margin account balances, refreshed every `config.refresh_blocks`. Limit orders draw from here.
        self.margin = {"mon": 0.0, "usdc": 0.0}
        self.nonce = 0
        self.fee_wei = gwei(102)  # base + priority, last known; Monad's floor is 100 + 2
        self.gas_limit = config.gas_limit_fallback
        self.use_vault = False
        self.pending: dict[str, Pending] = {}

    @property
    def address(self) -> str | None:
        return self.wallet.address if self.wallet else None

    @property
    def price_dec(self) -> int:
        return log10(self.params.price_precision)

    @property
    def size_dec(self) -> int:
        return log10(self.params.size_precision)

    async def init(self):
        self.params = await get_market_params(config.rpc_url, config.market)
        await self.refresh()
        if not self.wallet:
            return
        await self.resync_nonce()
        await self.ensure_margin()
        await self.init_gas_limit()

    async def refresh(self):
        """Every `config.refresh_blocks`: fee estimate, margin balances, and whether the Kuru AMM vault went live."""
        async def none():
            return None

        fee, vault, mon, usdc = await asyncio.gather(
            rpc("eth_gasPrice"),
            read_vault_params(config.read_rpc_url, config.market),
            self.margin_balance(ZERO_ADDRESS) if self.wallet else none(),
            self.margin_balance(self.params.quote_asset_address) if self.wallet else none(),
            return_exceptions=True,
        )
        if not isinstance(fee, BaseException):
            self.fee_wei = int(fee, 16)
        if not isinstance(vault, BaseException):
            self.use_vault = vault_active(vault)
        if isinstance(mon, int):
            self.margin["mon"] = to_float(mon, self.params.base_asset_decimals)
        if isinstance(usdc, int):
            self.margin["usdc"] = to_float(usdc, self.params.quote_asset_decimals)

    async def read_book(self) -> Book:
        """One eth_call (two batched into one HTTP request once the vault is live)."""
        return await read_book(config.read_rpc_url, config.market, self.params, vault=self.use_vault)

    def quote_price(self, side: Side, book: Book) -> float:
        """
        Where this block's order rests: `quote_inside_ticks` inside the touch on our side, never crossing.
        If the spread is too tight to step inside, join the touch. Integer tick math, so the price is
        exactly representable on-chain.
        """
        scale, tick = 10**self.price_dec, self.params.tick_size
        bid_u, ask_u = js_round(book.bid * scale), js_round(book.ask * scale)
        step = config.quote_inside_ticks * tick
        p = bid_u + step if side == "buy" else ask_u - step
        if side == "buy" and p >= ask_u:
            p = bid_u
        if side == "sell" and p <= bid_u:
            p = ask_u
        return p / scale

    async def send(self, block: int, side: Side, size_mon: float, book: Book, cancel: list[int], capped: bool) -> Quote:
        """
        Sign and fire one `batchUpdate`: cancel the given resting orders, post one new post-only limit
        order. Returns as soon as the RPC has the hash. `poll_pending` resolves placed/reverted later.
        """
        price = self.quote_price(side, book)
        if not self.wallet:
            return Quote(side=side, price=price, size=size_mon, txHash=None, gasMon=0, cancel=cancel, status="sim", orderId=None, capped=capped)

        signed = self.sign(self.build_tx(side, size_mon, price, cancel))
        try:
            tx_hash = await rpc("eth_sendRawTransaction", [signed])
            self.nonce += 1
        except Exception:
            try:
                await self.resync_nonce()
            except Exception:
                pass
            raise
        quote = Quote(side=side, price=price, size=size_mon, txHash=tx_hash, gasMon=self.gas_mon(self.gas_limit, self.fee_wei), cancel=cancel, status="sent", orderId=None, capped=capped)
        self.pending[tx_hash] = Pending(block, quote, self.gas_limit)
        return quote

    async def poll_pending(self, block: int) -> list[QuoteResult]:
        """One eth_getTransactionReceipt per in-flight tx. Returns whatever resolved (or timed out)."""
        if not self.pending:
            return []
        out: list[QuoteResult] = []
        lost = False

        async def check(tx_hash: str, p: Pending):
            nonlocal lost
            try:
                receipt = await rpc("eth_getTransactionReceipt", [tx_hash])
            except Exception:
                receipt = None
            if tx_hash not in self.pending:
                return  # an overlapping poll already resolved it
            if receipt:
                del self.pending[tx_hash]
                out.append(self.parse_receipt(receipt, p))
            elif block - p.block >= config.pending_blocks:
                del self.pending[tx_hash]
                lost = True
                out.append(QuoteResult(p.block, {**p.quote, "status": "lost", "gasMon": 0}, []))

        await asyncio.gather(*(check(h, p) for h, p in list(self.pending.items())))
        if lost:
            try:
                await self.resync_nonce()
            except Exception:
                pass
        return out

    def build_tx(self, side: Side, size_mon: float, price: float, cancel: list[int]) -> dict:
        """The exact transaction the hot loop signs: no pre-send RPC, hardcoded gas limit, static type-2 fees."""
        return {
            "type": 2, "chainId": config.chain_id, "to": config.market, "nonce": self.nonce, "gas": self.gas_limit,
            "maxFeePerGas": gwei(config.max_fee_gwei), "maxPriorityFeePerGas": gwei(config.priority_fee_gwei),
            "data": self.encode(side, size_mon, price, cancel), "value": 0,
        }

    def sign(self, tx: dict) -> str:
        return "0x" + Account.sign_transaction(tx, self.wallet.key).raw_transaction.hex()

    def encode(self, side: Side, size_mon: float, price: float, cancel: list[int]) -> str:
        """batchUpdate(buyPrices, buySizes, sellPrices, sellSizes, orderIdsToCancel, postOnly). Funds come from the margin account, so value is 0."""
        price_u = js_round(price * 10**self.price_dec)
        size_u = parse_units(f"{size_mon:.{self.size_dec}f}", self.size_dec)
        bp, bs, sp, ss = ([price_u], [size_u], [], []) if side == "buy" else ([], [], [price_u], [size_u])
        return abi.encode_call(abi.BATCH_UPDATE, [bp, bs, sp, ss, list(cancel), True])

    def parse_receipt(self, r: dict, p: Pending) -> QuoteResult:
        """OrderCreated for our address gives the new order id; OrdersCanceled lists what the tx removed. status 0x0: nothing changed on the book."""
        if r.get("effectiveGasPrice"):
            self.fee_wei = int(r["effectiveGasPrice"], 16)
        gas_mon = self.gas_mon(p.gas_limit, int(r["effectiveGasPrice"], 16) if r.get("effectiveGasPrice") else self.fee_wei)
        me = self.wallet.address.lower()
        order_id: int | None = None
        canceled: list[int] = []
        if r.get("status") != "0x0":
            for log in r.get("logs") or []:
                topics = log.get("topics") or []
                try:
                    if topics and topics[0] == TOPIC_ORDER_CREATED:
                        oid, owner, *_ = abi.decode_hex(["uint40", "address", "uint96", "uint32", "bool"], log["data"])
                        if owner.lower() == me:
                            order_id = oid
                    elif topics and topics[0] == TOPIC_ORDERS_CANCELED:
                        ids, owner = abi.decode_hex(["uint40[]", "address"], log["data"])
                        if owner.lower() == me:
                            canceled.extend(ids)
                except Exception:
                    continue
        status = "reverted" if r.get("status") == "0x0" else "placed"
        return QuoteResult(p.block, {**p.quote, "status": status, "orderId": order_id, "gasMon": gas_mon}, canceled)

    async def ensure_margin(self):
        """Top the margin account up to MARGIN_MON / MARGIN_USDC. Runs once at startup, awaiting each receipt."""
        w = self.wallet
        base_dec, quote_dec = self.params.base_asset_decimals, self.params.quote_asset_decimals
        usdc_token = self.params.quote_asset_address
        mon_bal, usdc_bal = await asyncio.gather(self.margin_balance(ZERO_ADDRESS), self.margin_balance(usdc_token))
        mon, usdc = to_float(mon_bal, base_dec), to_float(usdc_bal, quote_dec)

        async def deposit(token: str, amount: int, native: bool):
            data = abi.encode_call(abi.DEPOSIT, [w.address, token, amount])
            await self.send_and_wait(config.margin_account, data, amount if native else 0)

        if mon < config.margin_mon:
            amt = parse_units(f"{config.margin_mon - mon:.6f}", base_dec)
            print(f"margin: depositing {to_float(amt, base_dec)} MON")
            await deposit(ZERO_ADDRESS, amt, True)
        if usdc < config.margin_usdc:
            amt = parse_units(f"{config.margin_usdc - usdc:.{quote_dec}f}", quote_dec)
            have = await self.erc20_call(usdc_token, abi.BALANCE_OF, [w.address])
            if have < amt:
                print(f"margin: wallet has {to_float(have, quote_dec)} USDC, wanted to deposit {to_float(amt, quote_dec)}; depositing what is there", file=sys.stderr)
            dep = min(have, amt)
            if dep > 0:
                allowance = await self.erc20_call(usdc_token, abi.ALLOWANCE, [w.address, config.margin_account])
                if allowance < dep:
                    await self.send_and_wait(usdc_token, abi.encode_call(abi.APPROVE, [config.margin_account, abi.MAX_UINT256]), 0)
                print(f"margin: depositing {to_float(dep, quote_dec)} USDC")
                await deposit(usdc_token, dep, False)
        await self.refresh()
        print(f"margin · {self.margin['mon']:.2f} MON · {self.margin['usdc']:.2f} USDC")

    async def send_and_wait(self, to: str, data: str, value: int):
        """Startup-only transactions (approve, deposit): estimate gas, sign, send, wait for one confirmation."""
        w = self.wallet
        gas = int(await rpc("eth_estimateGas", [{"from": w.address, "to": to, "data": data, "value": hex(value)}]), 16)
        tx = {
            "type": 2, "chainId": config.chain_id, "to": to, "nonce": self.nonce, "gas": gas, "value": value, "data": data,
            "maxFeePerGas": gwei(config.max_fee_gwei), "maxPriorityFeePerGas": gwei(config.priority_fee_gwei),
        }
        self.nonce += 1
        tx_hash = await rpc("eth_sendRawTransaction", [self.sign(tx)])
        while not (receipt := await rpc("eth_getTransactionReceipt", [tx_hash])):
            await asyncio.sleep(0.3)
        if receipt.get("status") == "0x0":
            raise RuntimeError(f"transaction reverted: {tx_hash}")

    async def erc20_call(self, token: str, sig: str, args: list) -> int:
        res = await rpc("eth_call", [{"to": token, "data": abi.encode_call(sig, args)}, "latest"], config.read_rpc_url)
        return int(res, 16)

    async def margin_balance(self, token: str) -> int:
        data = abi.encode_call(abi.GET_BALANCE, [self.wallet.address, token])
        res = await rpc("eth_call", [{"to": config.margin_account, "data": data}, "latest"], config.read_rpc_url)
        return int(res, 16)

    async def init_gas_limit(self):
        """
        One eth_estimateGas at startup for a post-only place with no cancels, plus headroom for the one
        or two cancels a normal block carries, x1.15. Never in the hot loop. Needs margin funds to succeed.
        """
        if config.gas_limit:
            self.gas_limit = int(config.gas_limit)
        else:
            try:
                book = await self.read_book()
                side: Side = "buy" if self.margin["usdc"] >= config.trade_size_mon * book.ask else "sell"
                data = self.encode(side, config.trade_size_mon, self.quote_price(side, book), [])
                est = int(await rpc("eth_estimateGas", [{"to": config.market, "from": self.wallet.address, "data": data}]), 16)
                self.gas_limit = (est + 90_000) * 115 // 100
            except Exception as e:
                print(f"gas estimate failed ({str(e)[:120]}); using {config.gas_limit_fallback}", file=sys.stderr)
        per_block = self.gas_mon(self.gas_limit, self.fee_wei)
        print(f"gas limit {self.gas_limit} · maxFee {config.max_fee_gwei} gwei · priority {config.priority_fee_gwei} gwei · ~{per_block:.4f} MON per block, ~{per_block * 12_000:.0f} MON per hour")

    @staticmethod
    def gas_mon(limit: int, fee_wei: int) -> float:
        return to_float(limit * fee_wei, 18)

    async def resync_nonce(self):
        self.nonce = int(await rpc("eth_getTransactionCount", [self.wallet.address, "latest"]), 16)

