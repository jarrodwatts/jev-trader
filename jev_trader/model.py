import asyncio
import math
import os
from dataclasses import dataclass
from typing import Literal, Protocol

import aiohttp

from .chain import session
from .config import config
from .util import dumps, js_round, perf_ms

# Models answer buy or sell. `hold` only appears on late blocks (no decision was made).
Action = Literal["buy", "sell", "hold"]

# What the model sees is a plain dict (compact, relative, human-readable), built in trader.build_state:
#   market, block, horizonBlocks, blockMs, mid, spreadBps, bookImbalance (-1 all asks .. 1 all bids),
#   depth {"10bps": {bid, ask}, ...}, book {bids, asks: ["price x size"]}, returnsBps {last1, last5, last20, last100},
#   recentMids (every 5th block over the horizon, oldest first), trades {count, buyMon, sellMon, cvdMon, vwap,
#   lastPrice, lastSide}, recentTrades ["block side size @ price"], allowed {buy, sell}
TradeState = dict


@dataclass
class Decision:
    action: Action
    probabilities: dict[str, float]
    up_in_10: float
    latency_ms: float
    input_tokens: int


class Model(Protocol):
    name: str

    async def decide(self, state: TradeState) -> Decision: ...


QUESTIONS = {
    "direction": {
        "type": "choice",
        "instructions": {
            "question": "Will MON be higher or lower than the current mid after `horizonBlocks` more blocks?",
            "goal": "Trade MON-USDC on Kuru. Blocks are ~300ms; `horizonBlocks` (~30 s) is the horizon. A decision is made every few blocks and held until the next one. The trade crosses the spread (`spreadBps`), so the move must beat that cost.",
            "timing": "The order executes as an immediate-or-cancel market order in the next block.",
            "inputs": "Taker flow is the strongest signal: `trades.cvdMon` (taker buys minus taker sells over the horizon), `trades.lastSide` and `recentTrades` show who is hitting the book. `depth` and `book` show resting liquidity per side at several distances from mid; thin depth on one side means price moves easily that way. `returnsBps` and `recentMids` show the path over the horizon. If `allowed.buy` is false the trade will be a sell regardless, and vice versa.",
        },
        "criteria": {
            "buy": "Buy MON now: mid more likely to be higher after `horizonBlocks` blocks, by more than the spread.",
            "sell": "Sell MON now: mid more likely to be lower after `horizonBlocks` blocks, by more than the spread.",
        },
    },
}

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"


class JevModel:
    """Real Jev via TypeSafe's System One API (the request the AI SDK's typesafe-ai provider sends). Swap-in is the MODEL env var."""

    def __init__(self):
        self.name = config.jev_model_id

    async def decide(self, state: TradeState) -> Decision:
        t0 = perf_ms()
        key = os.environ.get("TYPESAFE_AI_API_KEY")
        if not key:
            raise RuntimeError("TypeSafe API key is missing. Set the TYPESAFE_AI_API_KEY environment variable.")
        body = {"model": config.jev_model_id, "state": state, "questions": QUESTIONS}
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        async with session().post(TYPESAFE_URL, data=dumps(body), headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as res:
            try:
                r = await res.json(content_type=None)
            except ValueError:
                raise RuntimeError(f"TypeSafe {res.status}: response was not JSON") from None
            if res.status >= 400:
                raise RuntimeError(f"TypeSafe {res.status}: {error_message(r)}")
        a = r["answers"]["direction"]
        p = a.get("probabilities") or {"buy": 0, "sell": 0, a["choice"]: 1}
        buy, sell = p.get("buy", 0), p.get("sell", 0)
        return Decision(
            action=a["choice"],
            probabilities={"buy": buy, "sell": sell, "hold": 0},
            up_in_10=buy,
            latency_ms=perf_ms() - t0,
            input_tokens=((r.get("usage") or {}).get("input_tokens")) or 0,
        )


def error_message(r) -> str:
    """Same precedence as the AI SDK provider: message, error, detail, error_type. TypeSafe nests auth errors under `detail`."""
    if not isinstance(r, dict):
        return "TypeSafe request failed"
    err, detail = r.get("error"), r.get("detail")
    if isinstance(detail, dict):
        detail = error_message(detail)
    return r.get("message") or (err if isinstance(err, str) else (err or {}).get("message")) or detail or r.get("error_type") or "TypeSafe request failed"


def _u32(x: float) -> int:
    """JS ToUint32 of a Number (the product is a double, so it is rounded before wrapping, exactly as in JS)."""
    return int(x) % 2**32


def _i32(x: int) -> int:
    """JS ToInt32: the result of `^`."""
    x %= 2**32
    return x - 2**32 if x >= 2**31 else x


class MockModel:
    """Deterministic stand-in: momentum + imbalance + mean reversion toward flat."""

    name = "mock"

    async def decide(self, state: TradeState) -> Decision:
        t0 = perf_ms()
        # momentum + book imbalance + noise, pulled back toward flat so it trades both ways
        t = state["trades"]
        flow = t["cvdMon"] / (t["buyMon"] + t["sellMon"]) if t["buyMon"] + t["sellMon"] else 0
        signal = state["returnsBps"]["last20"] / 8 + state["bookImbalance"] * 1.5 + flow * 2 + self.noise(state["block"])
        buy = 1 / (1 + math.exp(min(-signal, 709)))  # binary softmax
        probabilities = {"buy": buy, "sell": 1 - buy, "hold": 0}
        action: Action = "buy" if buy >= 0.5 else "sell"
        await asyncio.sleep(0.08)  # stand in for inference time so the pipeline behaves like production
        return Decision(
            action=action, probabilities=probabilities,
            up_in_10=buy,
            latency_ms=perf_ms() - t0,
            input_tokens=js_round(len(dumps(state)) / 4),
        )

    @staticmethod
    def noise(block: int) -> float:
        # Same bits as the JS original: Number multiplies, then >>> 0 / ^ wrap to 32 bits.
        h = _u32(float(block) * 2654435761.0)
        h = _i32(h ^ (h >> 15))
        h = _u32(float(h) * 2246822519.0)
        h = _i32(h ^ (h >> 13))
        return (math.fmod(h, 1000) / 1000 - 0.5) * 3


def create_model() -> Model:
    return JevModel() if config.model == "jev" else MockModel()
