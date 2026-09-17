/**
 * Trade limiter: gas budget, loss breaker, edge check, and a stand-down at the position cap.
 *
 * Off by default (`RISK_ENABLED`). The demo is meant to post an order on every 300 ms block, so a
 * limiter that trips is the demo stopping; with the flag off, nothing in here changes behaviour.
 *
 * Why each limit exists, with the numbers this market actually shows:
 *
 * - gas budget. Monad charges the gas *limit*, not gas used (`market.ts`), so every block costs
 *   `gasLimit x (base fee + priority)` whether the order lands, reverts, or is cancelled a block
 *   later. At the default 350,000 limit and the 100 gwei base-fee floor that is ~0.0357 MON per
 *   block, ~428 MON per hour, and the loop prints exactly that on startup. Nothing else in the
 *   process has an opinion about it.
 * - loss breaker. `pnlUsd` is already tracked every block; this is the line it crosses to stop. A spent
 *   bankroll and a spent lifetime gas budget are hard stops: they stay stopped until `resume()` is called
 *   by hand, because the condition that tripped them is still true one pause later. The transient ones
 *   (hourly gas, revert rate, late streak) do resume themselves after `pauseMinutes`.
 * - edge check. A 200 MON post inside the touch by 1 tick can capture `spread/2 - 1 tick` at best.
 *   On a ~2 bps MON-USDC book that is ~0.65 bps of notional, while one block's gas is ~1.8 bps of a
 *   200 MON quote. Quoting every block means paying more for the block than the fill can return;
 *   `minEdgeBps: 0` skips those blocks (each skip is a block of gas not spent) and `> 0` demands a
 *   margin on top. Set it negative to switch the check off.
 * - revert rate. The quote sits one tick inside the touch for a single block; when the book moves
 *   through the price before the tx lands the receipt is `reverted` and the gas is still spent. A
 *   revert rate near 100% means the loop is paying full price for orders that never rested.
 * - late streak. A decision that misses 300 ms emits `hold`, which is free. A long run of them means
 *   the model or the RPC cannot keep up with the chain, so the loop is guessing.
 * - counter-trade. When `allowed()` blocks the model's side, the trader may currently post the
 *   opposite one. With this layer on, the default is to stand down instead: at the cap, trading
 *   against the model is the one thing a limiter should refuse.
 *
 * State is persisted to `data/risk.json` on every trip and every `persistEvery` quotes, so a
 * restarted process resumes with the gas it already spent and does not re-trip from zero.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { config } from "./config";
import type { Book } from "./market";

export type TripReason = "gas-hour" | "gas-total" | "max-loss" | "revert-rate" | "late-streak" | "margin-drawdown" | "manual";

export interface Trip {
  reason: TripReason;
  detail: string;
  /** A hard stop does not resume itself: `resumeAt` is 0 until `resume()` is called by hand. */
  hard: boolean;
  at: number;
  resumeAt: number;
  pnlUsd: number;
  gasMon: number;
}

/** Spent bankroll, spent lifetime gas, and a manual stop stay stopped until someone says otherwise. */
const HARD_STOPS: TripReason[] = ["max-loss", "gas-total", "manual"];

/** Everything the dashboard needs to explain why the loop is quiet. */
export interface RiskState {
  enabled: boolean;
  tripped: Trip | null;
  gasMon: number;
  gasMonHour: number;
  gasMonTotalCap: number;
  gasMonHourCap: number;
  gasPerBlockMon: number;
  gasPerHourMon: number;
  quotes: number;
  placed: number;
  reverted: number;
  lost: number;
  revertPct: number;
  fills: number;
  skippedByEdge: number;
  skippedByBudget: number;
  savedGasMon: number;
  lateStreak: number;
  positionMon: number;
  edgeBps: number;
  gasCostBps: number;
  pnlUsd: number;
  maxLossUsd: number;
}

type Options = typeof config.risk;

const gwei = 1e9;

export class RiskGuard {
  readonly opts: Options;
  private tripped: Trip | null = null;
  private gasMon = 0;
  private hourWindow = { start: Date.now(), gas: 0 };
  private quotes = 0;
  private placed = 0;
  private reverted = 0;
  private lost = 0;
  private fills = 0;
  private lateStreak = 0;
  private positionMon = 0;
  private pnlUsd = 0;
  private skippedByEdge = 0;
  private skippedByBudget = 0;
  private savedGasMon = 0;
  /** true = the quote landed, false = it reverted. Sliding window of the last `revertWindow`. */
  private outcomes: boolean[] = [];
  private lastMargin: { mon: number; usdc: number; mid: number } | null = null;
  private onTrip: (t: Trip) => void = () => {};
  private persistCountdown: number;

  constructor(overrides: Partial<Options> = {}) {
    this.opts = { ...config.risk, ...overrides };
    this.persistCountdown = 200;
    mkdirSync(dirname(this.opts.stateFile), { recursive: true });
    this.restore();
  }

  get enabled() { return this.opts.enabled; }
  /** Unset means "follow the layer": stand down at the cap while it is on, take the other side while it is off. */
  get allowCounterTrade() { return this.opts.allowCounterTrade ?? !this.opts.enabled; }
  get isTripped() { return this.tripped !== null; }

  /** Called once by the trader so a trip can also emit a log line / SSE event. */
  onTripHook(fn: (t: Trip) => void) { this.onTrip = fn; }

  // ---------------------------------------------------------------- gate 1: may the loop trade at all

  /** False = skip this block: a budget is spent or a breaker is open. */
  allowTrading(now = Date.now()): { ok: boolean; reason: TripReason | null; detail: string } {
    if (!this.opts.enabled) return { ok: true, reason: null, detail: "" };
    if (this.tripped) {
      if (this.tripped.resumeAt > 0 && now >= this.tripped.resumeAt) this.resume();
      else return { ok: false, reason: this.tripped.reason, detail: this.tripped.detail };
    }
    this.rollHour(now);

    if (this.gasMon >= this.opts.maxTotalGasMon) {
      return this.trip("gas-total", `spent ${this.gasMon.toFixed(2)} MON of gas, cap ${this.opts.maxTotalGasMon}`);
    }
    if (this.hourWindow.gas >= this.opts.maxGasMonPerHour) {
      return this.trip("gas-hour", `${this.hourWindow.gas.toFixed(2)} MON of gas in the last hour, cap ${this.opts.maxGasMonPerHour}`);
    }
    if (this.pnlUsd <= -Math.abs(this.opts.maxLossUsd)) {
      return this.trip("max-loss", `P&L $${this.pnlUsd.toFixed(2)} at the -$${this.opts.maxLossUsd} line`);
    }
    if (this.outcomes.length >= this.opts.revertWindow && this.revertPct() >= this.opts.maxRevertPct) {
      return this.trip("revert-rate", `${this.revertPct().toFixed(0)}% of the last ${this.outcomes.length} quotes reverted`);
    }
    if (this.lateStreak >= this.opts.maxLateStreak) {
      return this.trip("late-streak", `${this.lateStreak} blocks in a row without a decision`);
    }
    return { ok: true, reason: null, detail: "" };
  }

  // ---------------------------------------------------------------- gate 2: is this quote worth a block

  /**
   * @param book the same book the quote price comes from
   * @param exposureIfQuoted signed inventory in MON if this quote rests and fills
   */
  allowQuote(book: Book, exposureIfQuoted: number): { ok: boolean; detail: string } {
    if (!this.opts.enabled) return { ok: true, detail: "" };
    if (this.opts.minEdgeBps < 0) return { ok: true, detail: "" };

    const edge = this.edgeBps(book);
    const cost = this.gasCostBps();
    const required = Math.max(this.opts.minEdgeBps, cost);
    if (edge < required) {
      this.skippedByEdge++;
      this.savedGasMon += this.gasPerBlockMon();
      return { ok: false, detail: `edge ${edge.toFixed(2)} bps < ${required.toFixed(2)} bps (gas is ${cost.toFixed(2)} bps of a ${config.tradeSizeMon} MON quote)` };
    }
    return { ok: true, detail: `edge ${edge.toFixed(2)} bps >= ${required.toFixed(2)} bps` };
  }

  /**
   * Best case capture of a post `quoteInsideTicks` inside the touch, in bps of notional:
   * half the spread minus the ticks we give up to sit inside it.
   */
  edgeBps(book: Book): number {
    const mid = book.mid;
    if (!(mid > 0)) return 0;
    const tickBps = (config.quoteInsideTicks * 0.000001 / mid) * 10_000;
    return Math.max(0, book.spreadBps / 2 - tickBps);
  }

  /** One block's gas as bps of a `tradeSizeMon` quote: the number a fill has to beat. */
  gasCostBps(): number {
    const notional = config.tradeSizeMon;
    return notional > 0 ? (this.gasPerBlockMon() / notional) * 10_000 : 0;
  }

  /** gasLimit x (base fee floor + priority). Replaced by the measured mean as receipts arrive. */
  private gasSamples: number[] = [];
  private gasPerBlockMon(): number {
    if (this.gasSamples.length) {
      return this.gasSamples.reduce((a, b) => a + b, 0) / this.gasSamples.length;
    }
    const limit = config.gasLimit ?? config.gasLimitFallback;
    const price = Math.min(config.maxFeeGwei, 100) + config.priorityFeeGwei;
    return (limit * price * gwei) / 1e18;
  }

  // ---------------------------------------------------------------- inputs from the loop

  /** From `applyQuoteResult`: the receipt landed (`placed`), reverted, or never arrived (`lost`). */
  noteQuote(status: string, gasMon: number) {
    this.rollHour();
    if (gasMon > 0) {
      this.gasMon += gasMon;
      this.hourWindow.gas += gasMon;
      this.gasSamples.push(gasMon);
      if (this.gasSamples.length > 50) this.gasSamples.shift();
    }
    if (status === "sim") return;
    this.quotes++;
    if (status === "placed") this.placed++;
    if (status === "reverted") this.reverted++;
    if (status === "lost") this.lost++;
    if (status === "placed" || status === "reverted" || status === "lost") {
      this.outcomes.push(status === "placed");
      if (this.outcomes.length > this.opts.revertWindow) this.outcomes.shift();
    }
    if (--this.persistCountdown <= 0) {
      this.persistCountdown = 200;
      this.persist();
    }
  }

  noteFill() { this.fills++; }

  /** Every block: a late block is a decision the model missed, and costs nothing. */
  noteLate(late: boolean) {
    if (late) this.lateStreak++;
    else this.lateStreak = 0;
  }

  notePosition(mon: number) { this.positionMon = mon; }

  notePnl(pnlUsd: number) { this.pnlUsd = pnlUsd; }

  /** Kill switch on the collateral itself: a margin account that halved stops the loop regardless of P&L. */
  noteMargin(mon: number, usdc: number, mid: number) {
    const prev = this.lastMargin;
    this.lastMargin = { mon, usdc, mid };
    if (!prev) return;
    const before = prev.mon * prev.mid + prev.usdc;
    const now = mon * mid + usdc;
    if (before > 0 && now < before * 0.5) {
      this.trip("margin-drawdown", `margin ${before.toFixed(2)} -> ${now.toFixed(2)} USDC since the last refresh`);
    }
  }

  // ---------------------------------------------------------------- trip / resume

  private trip(reason: TripReason, detail: string): { ok: false; reason: TripReason; detail: string } {
    if (!this.tripped) {
      const hard = HARD_STOPS.includes(reason);
      const t: Trip = {
        reason, detail, hard,
        at: Date.now(),
        resumeAt: hard ? 0 : Date.now() + this.opts.pauseMinutes * 60_000,
        pnlUsd: this.pnlUsd,
        gasMon: this.gasMon,
      };
      this.tripped = t;
      this.persist();
      this.onTrip(t);
    }
    return { ok: false, reason, detail };
  }

  /** For a signal handler or a kill switch of your own. */
  stop(detail = "stopped by hand") { this.trip("manual", detail); }

  resume() {
    if (!this.tripped) return;
    this.tripped = null;
    this.lateStreak = 0;
    this.outcomes = [];
    this.hourWindow = { start: Date.now(), gas: 0 };
    this.persist();
  }

  revertPct(): number {
    if (!this.outcomes.length) return 0;
    return (this.outcomes.filter((ok) => !ok).length / this.outcomes.length) * 100;
  }

  // ---------------------------------------------------------------- reporting

  state(): RiskState {
    this.rollHour();
    return {
      enabled: this.opts.enabled,
      tripped: this.tripped,
      gasMon: this.gasMon,
      gasMonHour: this.hourWindow.gas,
      gasMonTotalCap: this.opts.maxTotalGasMon,
      gasMonHourCap: this.opts.maxGasMonPerHour,
      gasPerBlockMon: this.gasPerBlockMon(),
      gasPerHourMon: this.gasPerBlockMon() * 12_000,
      quotes: this.quotes,
      placed: this.placed,
      reverted: this.reverted,
      lost: this.lost,
      revertPct: Number(this.revertPct().toFixed(1)),
      fills: this.fills,
      skippedByEdge: this.skippedByEdge,
      skippedByBudget: this.skippedByBudget,
      savedGasMon: this.savedGasMon,
      lateStreak: this.lateStreak,
      positionMon: this.positionMon,
      edgeBps: Number(this.edgeBps(this.lastBook).toFixed(2)),
      gasCostBps: Number(this.gasCostBps().toFixed(2)),
      pnlUsd: Number(this.pnlUsd.toFixed(4)),
      maxLossUsd: this.opts.maxLossUsd,
    };
  }

  /** One line for the loop's stdout. */
  line(): string {
    if (!this.opts.enabled) return "risk off";
    const s = this.state();
    if (s.tripped) return `risk TRIPPED ${s.tripped.reason}: ${s.tripped.detail}`;
    return `risk gas ${s.gasMon.toFixed(2)}/${s.gasMonTotalCap} MON (${s.gasMonHour.toFixed(1)}/h) · revert ${s.revertPct}% · edge ${s.edgeBps}/${s.gasCostBps} bps · skipped ${s.skippedByEdge} · P&L $${s.pnlUsd}`;
  }

  /** Start of the run: what the limits mean in this market, before anything is spent. */
  banner(): string {
    if (!this.opts.enabled) return "risk: off (set RISK_ENABLED=true to cap gas, losses and reverts)";
    const perBlock = this.gasPerBlockMon();
    return [
      "risk: on",
      `  gas      ${perBlock.toFixed(5)} MON/block, ${(perBlock * 12_000).toFixed(0)} MON/hour, caps ${this.opts.maxGasMonPerHour}/hour and ${this.opts.maxTotalGasMon} total`,
      `  loss     stop at -$${this.opts.maxLossUsd} (hard: needs resume())`,
      `  edge     need ${this.opts.minEdgeBps} bps minimum; a ${config.tradeSizeMon} MON quote pays ${this.gasCostBps().toFixed(2)} bps of gas`,
      `  reverts  stop above ${this.opts.maxRevertPct}% of the last ${this.opts.revertWindow} quotes`,
      `  late     stop after ${this.opts.maxLateStreak} blocks with no decision; transient stops pause ${this.opts.pauseMinutes} min`,
      `  at cap   ${this.opts.allowCounterTrade ? "post the other side" : "stand down"}`,
    ].join("\n");
  }

  /** Latest book, only for the edge numbers in `state()`. */
  private lastBook: Book = { block: 0, bid: 0, ask: 0, mid: 0, spreadBps: 0, imbalance: 0, levels: { bids: [], asks: [] }, depthBps: {} };
  noteBook(book: Book) { this.lastBook = book; }

  private rollHour(now = Date.now()) {
    if (now - this.hourWindow.start >= 3_600_000) this.hourWindow = { start: now, gas: 0 };
  }

  private persist() {
    try {
      writeFileSync(this.opts.stateFile, JSON.stringify({
        tripped: this.tripped, gasMon: this.gasMon, pnlUsd: this.pnlUsd, quotes: this.quotes,
        reverted: this.reverted, fills: this.fills, state: this.state(),
      }, null, 2));
      appendFileSync(this.opts.stateFile.replace(/\.json$/, "") + ".jsonl", JSON.stringify({ ts: Date.now(), ...this.state() }) + "\n");
    } catch { /* a failed write must not stop the loop */ }
  }

  private restore() {
    if (!existsSync(this.opts.stateFile)) return;
    try {
      const j = JSON.parse(readFileSync(this.opts.stateFile, "utf8")) as { gasMon?: number; pnlUsd?: number; tripped?: Trip | null };
      // Gas already spent is spent, whatever happened to the process.
      this.gasMon = j.gasMon ?? 0;
      this.pnlUsd = j.pnlUsd ?? 0;
      // A hard stop survives a restart only while its cause does: raising the limit and restarting is a
      // deliberate act, so the condition is re-checked rather than the file being trusted. A manual stop
      // never survives, because a restart is itself the act of clearing it.
      const t = j.tripped ?? null;
      const still = t?.reason === "gas-total" ? this.gasMon >= this.opts.maxTotalGasMon
        : t?.reason === "max-loss" ? this.pnlUsd <= -Math.abs(this.opts.maxLossUsd)
        : false;
      this.tripped = still ? t : null;
    } catch { /* ignore a corrupt state file */ }
  }
}
