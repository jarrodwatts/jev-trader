import asyncio
import json
from typing import Any, Callable

import aiohttp

from .config import config
from .util import spawn

_session: aiohttp.ClientSession | None = None
# A hung RPC must not hold the one-in-flight block loop forever: it would mark every later block late.
DEFAULT_TIMEOUT_MS = 10_000


def session() -> aiohttp.ClientSession:
    """One keep-alive HTTP session for every RPC call (created lazily, inside the running loop)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0, ttl_dns_cache=300))
    return _session


async def post_json(url: str, body: Any, timeout_ms: float | None = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=(timeout_ms or DEFAULT_TIMEOUT_MS) / 1000)
    async with session().post(url, json=body, timeout=timeout) as res:
        if res.status >= 400:
            raise RuntimeError(f"rpc http {res.status}")
        return await res.json(content_type=None)


async def rpc(method: str, params: list | None = None, url: str | None = None) -> Any:
    """Raw JSON-RPC call over HTTP. Defaults to the send RPC; pass `config.read_rpc_url` for reads."""
    res = await post_json(url or config.rpc_url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []})
    if res.get("error"):
        raise RuntimeError(f"{method}: {res['error'].get('message')} ({res['error'].get('code')})")
    return res.get("result")


def start_block_feed(on_block: Callable[[int], None], poll_ms: int = 150):
    """
    Emits new block numbers, coalesced to the newest one.
    Primary: WebSocket newHeads (fires when a block is Proposed).
    Backstop: HTTP polling on the read RPC, so the loop keeps running if the socket drops.
    A burst of heads in one tick runs the loop once, for the newest block only, never for a stale one.
    """
    loop = asyncio.get_running_loop()
    last = newest = 0
    scheduled = False

    def fire():
        nonlocal scheduled
        scheduled = False
        on_block(newest)

    def emit(block: int):
        nonlocal last, newest, scheduled
        if block <= last:
            return
        last = newest = block
        if scheduled:
            return
        scheduled = True
        loop.call_soon(fire)

    async def poll():
        try:
            emit(int(await rpc("eth_blockNumber", [], config.read_rpc_url), 16))
        except Exception:
            pass

    async def poller():
        while True:
            spawn(poll())  # never awaited: a slow response must not delay the next poll
            await asyncio.sleep(poll_ms / 1000)

    async def heads():
        delay = 0
        while True:
            await asyncio.sleep(delay / 1000)
            try:
                async with session().ws_connect(config.ws_url) as ws:
                    await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}))
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            m = json.loads(msg.data)
                            if m.get("method") == "eth_subscription":
                                emit(int(m["params"]["result"]["number"], 16))
                        except (ValueError, KeyError, TypeError):
                            pass
            except Exception:
                pass
            delay = min(delay + 1000, 10_000)

    spawn(poller())
    if config.ws_url:
        spawn(heads())
