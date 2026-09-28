"""Display formatting for the dashboard. Pure functions."""
import math
import time

from ..util import js_round


def safe(n) -> float:
    return n if isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) else 0


def fmt_int(n) -> str:
    """105416201 -> "105,416,201" """
    return f"{js_round(safe(n)):,}"


def fmt_price(n) -> str:
    """0.0222354 -> "0.022235" (6 decimals, MON/USDC ticks)"""
    return f"{safe(n):.6f}"


def fmt_mon(n, d: int = 3) -> str:
    """0.0213 -> "0.021 MON" """
    return f"{safe(n):.{d}f} MON"


def fmt_pct(p) -> str:
    """0.62 -> "62%" """
    return f"{js_round(safe(p) * 100)}%"


def fmt_conf(p) -> str:
    """0.62 -> "0.62" (two-decimal confidence)"""
    return f"{safe(p):.2f}"


def fmt_signed(n, d: int = 3) -> str:
    """Signed number with a forced +/- sign: (0.003, 3) -> "+0.003" """
    v = safe(n)
    return f"{'+' if v >= 0 else '-'}{abs(v):.{d}f}"


def fmt_signed_mon(n, d: int = 3) -> str:
    """-0.0012 -> "-0.001 MON"; 0.003 -> "+0.003 MON" """
    return f"{fmt_signed(n, d)} MON"


def fmt_size(size: float) -> str:
    """200 -> "200", 1234.5678 -> "1,234.57" """
    s = f"{safe(size):,.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def uptime(started_at, now_ms: float | None = None) -> str:
    """Accepts ms- or seconds-epoch. Elapsed since `started_at` as "04:13:42"."""
    if not started_at or not math.isfinite(started_at):
        return "00:00:00"
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    start_ms = started_at * 1000 if started_at < 1e12 else started_at
    return hhmmss(max(0, now_ms - start_ms))


def hhmmss(ms: float) -> str:
    """Milliseconds -> "hh:mm:ss" (hours are not capped at 24)."""
    total = math.floor(max(0, ms) / 1000)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def short_addr(a: str | None) -> str:
    """ "0x7F3a…9C4e" """
    if not a:
        return ""
    return a if len(a) <= 12 else f"{a[:6]}…{a[-4:]}"


def short_tx(h: str | None) -> str:
    """ "0x8f2c…" """
    if not h:
        return ""
    return h if len(h) <= 6 else f"{h[:6]}…"


def tx_url(h: str) -> str:
    return f"https://monadvision.com/tx/{h}"
