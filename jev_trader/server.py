"""
The bot's HTTP API, served by the same NiceGUI (FastAPI) app as the dashboard at `/`:
GET /snapshot meta + latest block, GET /history recent blocks,
GET /events SSE stream (`snapshot`, `block`, `quote`, `fill`, `ping`).
"""
import asyncio
from typing import Callable

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from nicegui import app
from nicegui import server as nicegui_server

from .market import Fill, Quote
from .trader import BlockEvent
from .util import dumps, now_ms

QUEUE_MAX = 1000  # an SSE client this far behind is dropped rather than buffered forever


class Hub:
    """Where the trader publishes. SSE clients get a queue each; dashboard pages watch `version`."""

    def __init__(self):
        self.meta: dict | None = None  # set once the market is initialised
        self.history: Callable[[], list[BlockEvent]] = lambda: []
        self.clients: set[asyncio.Queue] = set()
        self.version = 0  # bumps on every block, quote and fill

    def ready(self, meta: dict, history: Callable[[], list[BlockEvent]]):
        self.meta, self.history = meta, history

    async def pings(self):
        while True:
            await asyncio.sleep(15)
            self.publish("ping", now_ms(), bump=False)

    def send(self, q: asyncio.Queue, type: str, data) -> None:
        try:
            q.put_nowait(f"event: {type}\ndata: {dumps(data)}\n\n")
        except asyncio.QueueFull:
            self.clients.discard(q)
            while not q.empty():
                q.get_nowait()
            q.put_nowait(None)  # tells the stream to end

    def publish(self, type: str, data, bump: bool = True) -> None:
        if bump:
            self.version += 1
        for q in list(self.clients):
            self.send(q, type, data)

    def broadcast(self, e: BlockEvent) -> None:
        self.publish("block", e)

    def broadcast_quote(self, block: int, quote: Quote) -> None:
        """A quote's receipt landed: placed (with order id) or reverted, and the real gas."""
        self.publish("quote", {"block": block, "quote": quote})

    def broadcast_fill(self, block: int, fill: Fill) -> None:
        """A taker hit one of our resting orders in `block`."""
        self.publish("fill", {"block": block, "fill": fill})


hub = Hub()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"])


def shutting_down() -> bool:
    """uvicorn waits for open responses before it stops, and an SSE stream never ends: end them ourselves on Ctrl+C."""
    server = getattr(nicegui_server.Server, "instance", None)
    return bool(server and server.should_exit)


def json_response(body, status: int = 200) -> Response:
    return Response(dumps(body), status_code=status, media_type="application/json")


@app.get("/snapshot")
def snapshot() -> Response:
    if hub.meta is None:
        return json_response({"error": "starting"}, 503)
    h = hub.history()
    return json_response({**hub.meta, "latest": h[-1] if h else None})


@app.get("/history")
def history() -> Response:
    return json_response(hub.history())


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    hub.clients.add(q)
    hub.send(q, "snapshot", {**(hub.meta or {}), "history": hub.history()})

    async def stream():
        try:
            while not shutting_down():
                try:
                    chunk = await asyncio.wait_for(q.get(), 0.5)
                except TimeoutError:
                    continue
                if chunk is None:
                    break
                yield chunk
        finally:
            hub.clients.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache", "connection": "keep-alive"})
