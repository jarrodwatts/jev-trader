"""
The hero chart: a calm, responsive price flow drawn as SVG on the server.

Every render replaces the whole SVG, so motion that used to be a CSS transition between two React
renders is a keyframe animation from the previous position to the new one (`--from` / `--to`), and
one-shot cues (ripple, cell pop, word pop) are only emitted on the render where their block is new.
"""
import math
from dataclasses import dataclass, field
from html import escape

from ..util import js_round, jsnum
from .fmt import fmt_conf, fmt_mon, fmt_price, fmt_signed, fmt_signed_mon

STEP = 10  # px per block
ANCHOR_GAP = 88  # newest point sits this far from the right edge
PAD_TOP = 84  # overlays live here
PAD_BOTTOM = 72  # block strip + tag clearance
EASE = 0.1  # scale easing per new block
MIN_RANGE_PCT = 0.002  # floor of 0.20% of price, so bps noise stays calm
CELL_W = 6
CELL_H = 18
TAG_W = 58


def r1(v: float) -> str:
    return jsnum(js_round(v, 1))


def smooth_path(pts: list[tuple[float, float]]) -> str:
    """Monotone-cubic interpolation (Fritsch-Carlson): a smooth path that never overshoots its own data points, so a price line stays truthful."""
    n = len(pts)
    if n == 0:
        return ""
    if n == 1:
        return f"M{r1(pts[0][0])} {r1(pts[0][1])}"
    dx, m = [], []
    for i in range(n - 1):
        h = pts[i + 1][0] - pts[i][0]
        dx.append(h)
        m.append(0 if h == 0 else (pts[i + 1][1] - pts[i][1]) / h)
    t = [0.0] * n
    t[0], t[n - 1] = m[0], m[n - 2]
    for i in range(1, n - 1):
        if m[i - 1] * m[i] <= 0:
            t[i] = 0
        else:
            w1 = 2 * dx[i] + dx[i - 1]
            w2 = dx[i] + 2 * dx[i - 1]
            t[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i])
        if not math.isfinite(t[i]):
            t[i] = 0
    d = f"M{r1(pts[0][0])} {r1(pts[0][1])}"
    for i in range(n - 1):
        h = dx[i] / 3
        d += (
            f"C{r1(pts[i][0] + h)} {r1(pts[i][1] + t[i] * h)}"
            f" {r1(pts[i + 1][0] - h)} {r1(pts[i + 1][1] - t[i + 1] * h)}"
            f" {r1(pts[i + 1][0])} {r1(pts[i + 1][1])}"
        )
    return d


def cell_fill(e: dict) -> str:
    d = e.get("decision") or {}
    if d.get("late"):
        return "var(--late-cell)"
    side = (e.get("fill") or {}).get("side") or d.get("action")
    if side == "buy":
        return "var(--buy-bar)"
    if side == "sell":
        return "var(--sell-bar)"
    return "var(--hold-cell)"


@dataclass
class ChartState:
    """Per-viewer memory between renders (the refs of the React version)."""
    origin: int | None = None
    scale: tuple[float, float, int] | None = None  # lo, hi, block
    drawn_block: int | None = None  # newest block of the last render: one-shot cues play once per block
    drawn_shift: float | None = None
    drawn_end_y: float | None = None
    drawn_hot: int | None = None
    drawn_mid: float | None = None
    word: dict = field(default_factory=lambda: {"act": None, "block": 0, "drawn": None})
    # layout and hover, fed by the page
    w: int = 0
    h: int = 0
    left: float = 0
    shift: float = 0
    hover: int | None = None
    blocks: set = field(default_factory=set)


def render(events: list[dict], latest: dict | None, s: ChartState) -> str:
    w, h = s.w, s.h
    plot_h = h - PAD_TOP - PAD_BOTTOM
    shown = latest or (events[-1] if events else None)
    if w < 160 or plot_h < 60 or not shown:
        return '<div class="fc-empty">waiting for blocks…</div>'

    n = max(2, math.ceil((w - ANCHOR_GAP) / STEP) + 2)
    series = events[-n:]
    tail = series[-1] if series else None
    if latest and (not tail or latest["block"] > tail["block"]):
        series = (series + [latest])[-n:]
    elif latest and tail and latest["block"] == tail["block"]:
        series = series[:-1] + [latest]
    last = series[-1]

    if s.origin is None:
        s.origin = series[0]["block"]
    origin = s.origin
    fx = lambda b: (b - origin) * STEP

    # value scale: window min/max, floored to 0.20% of price, eased 10%/block
    mids = [e["mid"] for e in series]
    lo, hi = min(mids), max(mids)
    mean = sum(mids) / len(mids)
    floor = max(mean * MIN_RANGE_PCT, 1e-9)
    if hi - lo < floor:
        lo, hi = mean - floor / 2, mean + floor / 2
    if s.scale:
        plo, phi, pblock = s.scale
        if pblock == last["block"]:
            lo, hi = plo, phi
        else:
            lo, hi = plo + (lo - plo) * EASE, phi + (hi - phi) * EASE
        # the eased band must still contain the data: shrink slowly, grow at once
        lo, hi = min(lo, *mids), max(hi, *mids)
        if hi - lo < floor:
            c = (hi + lo) / 2
            lo, hi = c - floor / 2, c + floor / 2
    s.scale = (lo, hi, last["block"])

    rng = hi - lo or 1
    fy = lambda p: PAD_TOP + (1 - (p - lo) / rng) * plot_h

    pts = [(fx(e["block"]), fy(e["mid"])) for e in series]
    line = smooth_path(pts)
    base = h - PAD_BOTTOM
    area = f"{line} L{pts[-1][0]:.1f} {base} L{pts[0][0]:.1f} {base} Z"
    shift = w - ANCHOR_GAP - fx(last["block"])
    end_y = fy(last["mid"])
    s.shift = shift
    s.blocks = {e["block"] for e in series}

    new_block = s.drawn_block != last["block"]
    beads = [(e["block"], fx(e["block"]), fy(e["mid"]), "var(--buy)" if e["fill"]["side"] == "buy" else "var(--sell)") for e in series if e.get("fill")]
    hot = beads[-1] if beads else None
    new_hot = hot is not None and hot[0] != s.drawn_hot
    ticks = [(PAD_TOP + plot_h * f, fmt_price(lo + (1 - f) * rng)) for f in (0.25, 0.5, 0.75)]

    svg: list[str] = [f'<svg class="fc-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">']
    svg.append('<defs><linearGradient id="fcg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(10,10,10,0.07)"/><stop offset="100%" stop-color="rgba(10,10,10,0)"/></linearGradient></defs>')
    for y, _ in ticks:
        svg.append(f'<line class="fc-grid" x1="0" x2="{w}" y1="{jsnum(y)}" y2="{jsnum(y)}"/>')

    slide = motion("fc-slide", "translateX", s.drawn_shift, shift, new_block)
    svg.append(f'<g {slide}>')
    svg.append(f'<path d="{area}" fill="url(#fcg)"/>')
    svg.append(f'<path class="fc-line" d="{line}"/>')
    for key, x, y, fill in beads:
        pop = ' class="fc-bead-pop"' if hot and key == hot[0] and new_hot else ""
        svg.append(f'<circle{pop} cx="{jsnum(x)}" cy="{jsnum(y)}" r="3" fill="{fill}" opacity="0.7"/>')
    if hot and new_hot:
        _, x, y, fill = hot
        svg.append(f'<line class="fc-riser" x1="{jsnum(x)}" x2="{jsnum(x)}" y1="{h - 40}" y2="{jsnum(y)}" stroke="{fill}"/>')
        svg.append(f'<circle class="fc-ripple" cx="{jsnum(x)}" cy="{jsnum(y)}" r="3" fill="none" stroke="{fill}" stroke-width="2"/>')
    cells = [(fx(e["block"]) - CELL_W / 2, cell_fill(e), 1 if i == len(series) - 1 else 0.6 if e.get("quote") and e["quote"]["status"] == "sent" else 0.82) for i, e in enumerate(series)]
    if new_block:
        x, fill, _ = cells[-1]
        svg.append(f'<rect class="fc-cell-glow" x="{jsnum(x)}" y="{h - 40}" width="{CELL_W}" height="{CELL_H}" rx="3" fill="{fill}"/>')
    for i, (x, fill, op) in enumerate(cells):
        pop = ' class="fc-cell-pop"' if i == len(cells) - 1 and new_block else ""
        svg.append(f'<rect{pop} x="{jsnum(x)}" y="{h - 40}" width="{CELL_W}" height="{CELL_H}" rx="3" fill="{fill}" opacity="{op}"/>')
    svg.append(hover_tip(series, s, fx, fy, base, w))
    svg.append("</g>")

    for y, label in ticks:
        svg.append(f'<text class="fc-tick" x="{w - 8}" y="{jsnum(y - 5)}" text-anchor="end">{label}</text>')
    tag = motion("fc-tag", "translateY", s.drawn_end_y, end_y, new_block)
    svg.append(f"<g {tag}>")
    svg.append(f'<line class="fc-guide" x1="{w - ANCHOR_GAP + 8}" x2="{w - TAG_W - 6}" y1="0" y2="0"/>')
    svg.append(f'<circle cx="{w - ANCHOR_GAP}" cy="0" r="4" fill="var(--ink)"/>')
    svg.append(f'<rect x="{w - TAG_W - 4}" y="-10" width="{TAG_W}" height="20" rx="999" fill="var(--ink)"/>')
    svg.append(f'<text class="fc-tag-text" x="{jsnum(w - 4 - TAG_W / 2)}" y="4" text-anchor="middle">{fmt_price(last["mid"])}</text>')
    svg.append("</g></svg>")

    overlay = overlays(shown, s)
    s.drawn_block, s.drawn_shift, s.drawn_end_y = last["block"], shift, end_y
    if hot:
        s.drawn_hot = hot[0]
    return "".join(svg) + '<div class="fc-fade"></div>' + overlay


def motion(cls: str, fn: str, prev: float | None, now: float, animate: bool) -> str:
    """Rest at `now`; on a new block, glide there from where the last render left it."""
    style = f"transform: {fn}({now:.1f}px)"
    if animate and prev is not None and prev != now:
        return f'class="{cls} {cls}-go" style="{style}; --from: {prev:.1f}px; --to: {now:.1f}px"'
    return f'class="{cls}" style="{style}"'


def hover_tip(series: list[dict], s: ChartState, fx, fy, base: float, w: int) -> str:
    e = next((x for x in series if x["block"] == s.hover), None) if s.hover is not None else None
    if not e:
        return ""
    x, y = fx(e["block"]), fy(e["mid"])
    flip = x + s.shift > w - 168
    tx = x - 146 if flip else x + 14
    ty = min(max(y - 92, PAD_TOP - 46), base - 82)
    fill, q = e.get("fill"), e.get("quote")
    if fill:
        trade = f"FILL {'BUY' if fill['side'] == 'buy' else 'SELL'} {fmt_mon(fill['size'], 0)}"
        tint = "var(--buy-ink)" if fill["side"] == "buy" else "var(--sell-ink)"
    elif q:
        trade = f"{'bid' if q['side'] == 'buy' else 'ask'} {fmt_price(q['price'])}"
        tint = "var(--buy-ink)" if q["side"] == "buy" else "var(--sell-ink)"
    else:
        trade, tint = "no quote", "var(--muted)"
    d = e.get("decision")
    lat = f"{js_round(d['latencyMs'])} ms" if d and not d["late"] else "late"
    return (
        f'<g><line class="fc-cross" x1="{jsnum(x)}" x2="{jsnum(x)}" y1="{PAD_TOP - 12}" y2="{jsnum(base + 10)}"/>'
        f'<circle class="fc-cross-dot" cx="{jsnum(x)}" cy="{jsnum(y)}" r="4.5"/>'
        f'<g transform="translate({tx:.1f},{ty:.1f})"><rect class="fc-tip" width="132" height="78" rx="10"/>'
        f'<text class="fc-tip-block" x="12" y="21">#{e["block"]}</text>'
        f'<text class="fc-tip-price" x="12" y="41">{fmt_price(e["mid"])}</text>'
        f'<text class="fc-tip-side" x="12" y="58" fill="{tint}">{trade}</text>'
        f'<text class="fc-tip-meta" x="12" y="71">{lat}</text></g></g>'
    )


def overlays(shown: dict, s: ChartState) -> str:
    d = shown.get("decision")
    late = bool(d and d.get("late"))
    act = "late" if late else (d or {}).get("action") or "hold"
    word = {"buy": "Buying", "sell": "Selling", "late": "Missed the block"}.get(act, "Holding")
    color = {"buy": "var(--buy-ink)", "sell": "var(--sell-ink)", "late": "var(--late-ink)"}.get(act, "var(--ink)")
    if s.word["act"] != act:
        s.word = {"act": act, "block": shown["block"], "drawn": s.word["drawn"]}
    word_key = (s.word["block"], act)
    word_pop = " fc-word-pop" if s.word["drawn"] != word_key else ""
    s.word["drawn"] = word_key
    conf = max(d["probabilities"]["buy"], d["probabilities"]["sell"], d["probabilities"]["hold"]) if d else 0
    pos = shown.get("position")
    if not pos or pos["side"] == "flat":
        stance = "flat"
    else:
        stance = f"{pos['side']} {fmt_mon(pos['size'], 0 if float(pos['size']).is_integer() else 3)}"
    totals = shown.get("totals") or {}
    pnl_mon, pnl_pct = totals.get("pnlMon", 0), totals.get("pnlPct", 0)
    price_in = " fc-price-in" if s.drawn_mid != shown["mid"] else ""
    s.drawn_mid = shown["mid"]
    lat = "late" if not d or late else f"{js_round(d['latencyMs'])} ms"
    return (
        f'<div class="fc-tl"><div class="fc-price{price_in}">{fmt_price(shown["mid"])}</div>'
        f'<div class="fc-sub"><span>MON/USDC</span><span>Kuru</span><span>{escape(stance)}</span>'
        f'<span style="color: {"var(--pnl-pos)" if pnl_mon >= 0 else "var(--pnl-neg)"}">p&amp;l {fmt_signed_mon(pnl_mon, 3)} ({fmt_signed(pnl_pct, 2)}%)</span></div></div>'
        f'<div class="fc-tr"><div class="fc-word{word_pop}" style="color: {color}">{word}</div>'
        f'<div class="fc-sub"><span>{lat}</span><span>conf {fmt_conf(conf)}</span></div></div>'
    )


def hover_block(client_x: float, s: ChartState) -> int | None:
    """The block under the pointer, if it is on the chart."""
    if s.origin is None:
        return None
    b = js_round((client_x - s.left - s.shift) / STEP) + s.origin
    return b if b in s.blocks else None
