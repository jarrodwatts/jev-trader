"""
The live dashboard at `/`, one Dashboard per open browser tab.

The page reads the trader's history in-process (no SSE round trip) and redraws whenever the hub's
version moves. Header, stats and the decision bars are long-lived elements, so their CSS
transitions animate between blocks; the chart and the feed are re-rendered as HTML each block.
"""
import time
from html import escape
from pathlib import Path

from nicegui import ui

from ..server import hub
from ..util import js_round
from . import chart
from .fmt import fmt_int, fmt_pct, fmt_price, fmt_size, short_addr, short_tx, tx_url, uptime

HERE = Path(__file__).parent
FEED_ROWS = 40  # the list clips at its height; rows are a fixed 26px, so extras are simply hidden
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600&display=swap">'
    '<meta name="theme-color" content="#F0EEE9">'
    '<meta name="description" content="One AI trade decision every Monad block.">'
)


def html(cls: str, text: str = "", tag: str = "span") -> ui.html:
    return ui.html(text, sanitize=False, tag=tag).classes(cls)


def box(cls: str, tag: str = "div") -> ui.element:
    """A container. (ui.html cannot hold children: its content replaces them.)"""
    return ui.element(tag).classes(cls)


def set_html(el: ui.html, content: str) -> None:
    if el.content != content:
        el.set_content(content)


def set_style(el, style: str) -> None:
    if getattr(el, "_last_style", None) != style:
        el._last_style = style
        el.style(replace=style)


def kind_of(e: dict) -> str:
    d = e.get("decision")
    if not d or d.get("late"):
        return "late"
    return d["action"] if d["action"] in ("buy", "sell") else "late"


class Dashboard:
    def __init__(self):
        self.version = -1
        self.hover: int | None = None
        self.drawn_hover: int | None = None
        self.feed_newest: int | None = None
        self.copied_until = 0.0
        self.chart = chart.ChartState()

        with box("jt-card", "div"):
            with box("hd", "div"):
                html("hd-brand", "‖ Jev Trader")
                self.block = html("hd-block", "block -")
                html("hd-spacer")
                self.offline = html("hd-offline", "connecting")
                self.wallet = html("hd-wallet", tag="button").on("click", self.copy)
                self.badge = html("hd-badge")
            with box("st", "div"):
                self.last = html("", "last - ms")
                self.avg = html("", "avg -")
                self.calls = html("", "- calls")
                self.fills = html("", "- fills")
                html("st-spacer")
                self.up = html("", "uptime 00:00:00")
            with box("jt-main", "div"):
                with box("jt-left", "div"):
                    with box("fc-panel", "div") as self.panel:
                        self.plot = html("fc-layer", tag="div")
                    self.panel.on("mousemove", self.on_move, ["clientX"], throttle=0.05)
                    self.panel.on("mouseleave", lambda: setattr(self, "hover", None))
                with box("jt-right", "div"):
                    self.decision_panel()
                    with box("fd", "section"):
                        html("fd-label", "FEED", tag="div")
                        self.feed = html("fd-list", tag="div")

        ui.timer(0.05, self.tick)
        ui.timer(1.0, self.measure)

    def decision_panel(self):
        with box("dp", "div"):
            with box("dp-section", "section"):
                html("dp-section-label", "STANDING ORDER", tag="div")
                html("dp-order", escape("> post a bid or an ask on Kuru's MON/USDC book. every block. no abstaining."), tag="div")
            with box("dp-section", "section"):
                html("dp-section-label gap", "WHICH SIDE THIS BLOCK?", tag="div")
                with box("dp-headline", "div") as self.headline:
                    self.headline_word = html("dp-headline-word", "LATE")
                    self.headline_pct = html("dp-headline-pct")
                self.bars = {side: self.bar_row(side) for side in ("buy", "sell")}

    def bar_row(self, side: str) -> dict:
        with box("dp-row", "div"):
            label = html("dp-label", side).style(f"color: var(--{side}-ink); opacity: 0.38")
            with box("dp-track", "div"):
                fill = box("dp-fill").style(f"width: 0%; background: var(--{side}-bar-dim)")
            pct = html("dp-pct", "-")
        return {"label": label, "fill": fill, "pct": pct}

    # ---- events from the browser

    def copy(self):
        wallet = (hub.meta or {}).get("wallet")
        if not wallet:
            return
        ui.clipboard.write(wallet)
        self.copied_until = time.monotonic() + 1.2

    def on_move(self, e):
        self.hover = chart.hover_block(e.args["clientX"], self.chart)

    async def measure(self):
        """Once a second: the chart's box (the SVG is laid out in pixels) and the uptime."""
        try:
            left, w, h = await ui.run_javascript(f"const r = getHtmlElement({self.panel.id}).getBoundingClientRect(); return [r.left, r.width, r.height]")
        except Exception:
            return
        w, h = js_round(w), js_round(h)
        if (w, h) != (self.chart.w, self.chart.h):
            self.chart.w, self.chart.h = w, h
            self.version = -1  # redraw at the new size
        self.chart.left = left
        started = (hub.meta or {}).get("startedAt")
        set_html(self.up, f"uptime {uptime(started) if started else '00:00:00'}")

    # ---- rendering

    def tick(self):
        copied = time.monotonic() < self.copied_until
        wallet = (hub.meta or {}).get("wallet")
        set_html(self.wallet, "copied" if copied else escape(short_addr(wallet)) if wallet else "dry run")
        if hub.version == self.version and self.hover == self.drawn_hover:
            return
        data_changed = hub.version != self.version
        self.version, self.drawn_hover = hub.version, self.hover
        events = hub.history()
        latest = events[-1] if events else None
        self.chart.hover = self.hover
        set_html(self.plot, chart.render(events, latest, self.chart))
        if data_changed:
            self.render_chrome(events, latest)
            set_html(self.feed, self.render_feed(events))

    def render_chrome(self, events: list[dict], latest: dict | None):
        meta = hub.meta or {}
        set_html(self.block, f"block {fmt_int(latest['block']) if latest else '-'}")
        self.offline.set_visibility(latest is None)
        wallet = meta.get("wallet")
        self.wallet.props(remove="disabled") if wallet else self.wallet.props("disabled")
        self.wallet.props(f'title="{escape(wallet or "no wallet, dry run")}"')
        model = meta.get("model")
        self.badge.set_visibility(bool(model))
        if model:
            set_html(self.badge, escape(model))
            self.badge.classes(replace="hd-badge " + ("jev" if model.lower().startswith("jev") else "standin"))

        # stats row
        d = latest.get("decision") if latest else None
        set_html(self.last, f"last {d['latencyMs']} ms" if d and not d["late"] else "last - ms")
        lats = [e["decision"]["latencyMs"] for e in events if e.get("decision") and not e["decision"]["late"]]
        avg = js_round(sum(lats) / len(lats)) if lats else 0
        set_html(self.avg, f"avg {avg}ms" if avg > 0 else "avg -")
        totals = latest.get("totals") if latest else None
        set_html(self.calls, f"{fmt_int(totals['decisions']) if totals else '-'} calls")
        set_html(self.fills, f"{fmt_int(totals['fills']) if totals else '-'} fills")

        # decision panel: "hold" is a non-decision, exactly as the feed treats it
        chosen = d["action"] if d and not d["late"] and d["action"] != "hold" else None
        probs = d["probabilities"] if d else {"buy": 0, "sell": 0, "hold": 0}
        set_html(self.headline_word, {"buy": "BUY", "sell": "SELL"}.get(chosen, "LATE"))
        set_html(self.headline_pct, fmt_pct(probs[chosen]) if chosen else "")
        set_style(self.headline, f"color: var(--{chosen}-ink)" if chosen else "color: var(--late-ink)")
        for side, bar in self.bars.items():
            on = chosen == side
            set_style(bar["label"], f"color: var(--{side}-ink); opacity: {1 if on else 0.38}")
            width = max(0, min(1, probs[side])) * 100
            set_style(bar["fill"], f"width: {width}%; background: var(--{side}-bar{'' if on else '-dim'})")
            set_html(bar["pct"], fmt_pct(probs[side]) if chosen else "-")

    def render_feed(self, events: list[dict]) -> str:
        """
        One row per block. The word is the side the model picked, the detail is the order that went on
        the book (bid or ask at its price), and when a taker hit one of our orders in that block the
        detail becomes the fill instead. The tx column is the order's transaction: dim while pending,
        "rev" if the book moved through the price before it landed.
        """
        rows = events[-FEED_ROWS:][::-1]
        if not rows:
            return '<div class="fd-empty">no blocks yet</div>'
        arrived = rows[0]["block"] != self.feed_newest
        self.feed_newest = rows[0]["block"]
        out = []
        for i, e in enumerate(rows):
            kind = kind_of(e)
            d, q, fill = e.get("decision"), e.get("quote"), e.get("fill")
            decided = kind != "late"
            conf = f"conf {max(d['probabilities']['buy'], d['probabilities']['sell'], d['probabilities']['hold']):.2f}" if decided and d else ""
            lat = f"{d['latencyMs']}ms" if decided and d else ""
            detail, muted = "", False
            if fill and fill["size"] > 0:
                detail = f"FILL {fmt_size(fill['size'])} @ {fmt_price(fill['price'])}"
            elif decided and q:
                detail = f"{'bid' if q['side'] == 'buy' else 'ask'} {fmt_size(q['size'])} @ {fmt_price(q['price'])}{' cap' if q['capped'] else ''}"
                muted = q["status"] in ("reverted", "lost")
            elif decided:
                detail, muted = "no quote", True

            if fill and not fill["simulated"] and fill.get("txHash"):
                tx = f'<a href="{tx_url(fill["txHash"])}" target="_blank" rel="noreferrer" title="the taker\'s transaction">{escape(short_tx(fill["txHash"]))}</a>'
            elif q and q["status"] == "sim":
                tx = '<span class="fd-muted">sim</span>'
            elif q and q.get("txHash"):
                cls = {"sent": ' class="fd-pending"', "placed": ""}.get(q["status"], ' class="fd-muted"')
                text = {"reverted": "rev", "lost": "lost"}.get(q["status"], short_tx(q["txHash"]))
                tx = f'<a{cls} title="{q["status"]}" href="{tx_url(q["txHash"])}" target="_blank" rel="noreferrer">{escape(text)}</a>'
            else:
                tx = ""

            row_cls = " ".join(c for c in ("fd-row", f"fd-{kind}", "fd-newest" if i == 0 else "", "fd-in" if i == 0 and arrived else "", "fd-filled" if fill else "") if c)
            out.append(
                f'<div class="{row_cls}">'
                f'<span class="fd-cell fd-block">{fmt_int(e["block"])}</span>'
                f'<span class="fd-cell fd-word">{kind.upper()}</span>'
                f'<span class="fd-cell fd-conf">{conf}</span>'
                f'<span class="fd-cell fd-lat">{lat}</span>'
                f'<span class="fd-cell fd-detail{" fd-muted" if muted else ""}">{detail}</span>'
                f'<span class="fd-cell fd-tx">{tx}</span></div>'
            )
        return "".join(out)


def setup():
    """Register the page. Call before ui.run()."""
    ui.add_head_html(FONTS, shared=True)
    ui.add_css(HERE / "style.css", shared=True)

    @ui.page("/", title="Jev Trader")
    def index():
        Dashboard()
