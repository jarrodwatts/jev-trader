import { afterAll, describe, expect, test } from "bun:test";
import { RiskGuard } from "../src/risk";
import { config } from "../src/config";
import type { Book } from "../src/market";

let counter = 0;
const written: string[] = [];
afterAll(() => {
  for (const f of written) Bun.file(f).delete().catch(() => {});
});

/** A book with a given spread, everything else irrelevant to the limiter. */
const book = (spreadBps: number, mid = 0.0232): Book => ({
  block: 0, bid: mid * (1 - spreadBps / 20_000), ask: mid * (1 + spreadBps / 20_000), mid,
  spreadBps, imbalance: 0, levels: { bids: [], asks: [] }, depthBps: {},
});

const opts = (o: Partial<typeof config.risk> = {}) => {
  // A guard restores the gas it already spent from its state file, so every test needs its own.
  const stateFile = o.stateFile ?? `data/test-risk-${process.pid}-${counter++}.json`;
  written.push(stateFile);
  return { enabled: true, stateFile, ...o };
};

describe("risk off", () => {
  test("allows everything, and never stands in front of a counter trade", () => {
    const r = new RiskGuard(opts({ enabled: false }));
    expect(r.allowTrading().ok).toBe(true);
    expect(r.allowQuote(book(0.1), 9_999).ok).toBe(true);
    expect(r.allowCounterTrade).toBe(true);
    expect(r.state().enabled).toBe(false);
  });
});

describe("gas budget", () => {
  test("trips when an hour of gas is spent, and reports the block it stopped on", () => {
    const r = new RiskGuard(opts({ maxGasMonPerHour: 0.5, maxTotalGasMon: 100, maxLossUsd: 1e9 }));
    expect(r.allowTrading().ok).toBe(true);
    r.noteQuote("placed", 0.2);
    r.noteQuote("placed", 0.2);
    expect(r.allowTrading().ok).toBe(true); // 0.4 < 0.5
    r.noteQuote("placed", 0.2);
    const gate = r.allowTrading();
    expect(gate.ok).toBe(false);
    expect(gate.reason).toBe("gas-hour");
    expect(r.state().tripped?.reason).toBe("gas-hour");
  });

  test("trips on the lifetime budget", () => {
    const r = new RiskGuard(opts({ maxGasMonPerHour: 1e9, maxTotalGasMon: 0.05, maxLossUsd: 1e9 }));
    r.noteQuote("placed", 0.06);
    expect(r.allowTrading().reason).toBe("gas-total");
  });

  test("counts a lost send: the limit is owed whether or not the receipt came back", () => {
    const r = new RiskGuard(opts({ maxGasMonPerHour: 1e9, maxTotalGasMon: 100 }));
    r.noteQuote("lost", 0.4);
    expect(r.state().gasMon).toBeCloseTo(0.4, 9);
    expect(r.state().lost).toBe(1);
  });
});

describe("loss breaker", () => {
  test("a spent bankroll is a hard stop: it does not resume itself", () => {
    const r = new RiskGuard(opts({ maxLossUsd: 10, pauseMinutes: 1 }));
    r.notePnl(-10);
    expect(r.allowTrading().reason).toBe("max-loss");
    expect(r.state().tripped?.hard).toBe(true);
    expect(r.allowTrading(Date.now() + 3_600_000).ok).toBe(false); // still tripped an hour later
    r.resume(); // by hand
    expect(r.allowTrading().ok).toBe(false); // it trips again: the condition is still true, not the flag
    r.notePnl(-1); // the cause is gone...
    expect(r.allowTrading().ok).toBe(false); // ...but the stop that was already raised is still up
    r.resume();
    expect(r.allowTrading().ok).toBe(true);
  });

  test("a hard stop survives a restart only while its cause does", () => {
    const stateFile = `data/test-risk-${process.pid}-restart.json`;
    written.push(stateFile);
    const first = new RiskGuard(opts({ stateFile, maxLossUsd: 10 }));
    first.notePnl(-20);
    first.allowTrading(); // persists the trip
    expect(new RiskGuard(opts({ stateFile, maxLossUsd: 10 })).allowTrading().ok).toBe(false);
    expect(new RiskGuard(opts({ stateFile, maxLossUsd: 50 })).allowTrading().ok).toBe(true); // limit raised
  });

  test("an hourly gas overrun is transient and clears itself", () => {
    const r = new RiskGuard(opts({ maxGasMonPerHour: 0.1, pauseMinutes: 1, maxLossUsd: 1e9 }));
    r.noteQuote("placed", 0.2);
    expect(r.allowTrading().reason).toBe("gas-hour");
    expect(r.state().tripped?.hard).toBe(false);
    expect(r.allowTrading(Date.now() + 60_001).ok).toBe(true);
  });

  test("does not trip on profit", () => {
    const r = new RiskGuard(opts({ maxLossUsd: 10 }));
    r.notePnl(500);
    expect(r.allowTrading().ok).toBe(true);
  });
});

describe("revert rate", () => {
  test("trips only once the window is full, so a cold start cannot trip on three reverts", () => {
    const r = new RiskGuard(opts({ maxRevertPct: 50, revertWindow: 4, maxLossUsd: 1e9 }));
    r.noteQuote("reverted", 0.01);
    r.noteQuote("reverted", 0.01);
    r.noteQuote("reverted", 0.01);
    expect(r.allowTrading().ok).toBe(true); // window not full yet
    r.noteQuote("reverted", 0.01);
    expect(r.allowTrading().reason).toBe("revert-rate");
    expect(r.revertPct()).toBe(100);
  });

  test("a run of placed quotes keeps it open", () => {
    const r = new RiskGuard(opts({ maxRevertPct: 50, revertWindow: 4, maxLossUsd: 1e9 }));
    for (const s of ["placed", "reverted", "placed", "placed"]) r.noteQuote(s, 0.01);
    expect(r.revertPct()).toBe(25);
    expect(r.allowTrading().ok).toBe(true);
  });
});

describe("late blocks", () => {
  test("trips after a run without a decision, and one decision resets it", () => {
    const r = new RiskGuard(opts({ maxLateStreak: 3, maxLossUsd: 1e9 }));
    r.noteLate(true);
    r.noteLate(true);
    expect(r.allowTrading().ok).toBe(true);
    r.noteLate(true);
    expect(r.allowTrading().reason).toBe("late-streak");
    r.resume();
    r.noteLate(false);
    expect(r.state().lateStreak).toBe(0);
  });
});

describe("edge check", () => {
  test("a quote that cannot pay for its own block of gas is skipped", () => {
    const r = new RiskGuard(opts({ minEdgeBps: 0, maxLossUsd: 1e9 }));
    // 2 bps book: half the spread is 1 bps, one tick inside at this mid costs ~0.43 bps -> 0.57 bps capture.
    // Gas at the default limit is ~0.0357 MON on a 200 MON quote, ~1.8 bps. It cannot pay.
    const t = r.allowQuote(book(2), config.tradeSizeMon);
    expect(t.ok).toBe(false);
    expect(r.state().skippedByEdge).toBe(1);
    expect(r.state().savedGasMon).toBeGreaterThan(0);
  });

  test("a wide book pays for it", () => {
    const r = new RiskGuard(opts({ minEdgeBps: 0, maxLossUsd: 1e9 }));
    // 40 bps spread: 20 bps of capture against ~1.8 bps of gas.
    expect(r.allowQuote(book(40), config.tradeSizeMon).ok).toBe(true);
  });

  test("a negative minimum switches the check off", () => {
    const r = new RiskGuard(opts({ minEdgeBps: -1 }));
    expect(r.allowQuote(book(0.1), config.tradeSizeMon).ok).toBe(true);
  });

  test("the required edge is the larger of the floor and the gas cost", () => {
    const r = new RiskGuard(opts({ minEdgeBps: 0, maxLossUsd: 1e9 }));
    const gas = r.gasCostBps();
    expect(gas).toBeGreaterThan(1);
    expect(gas).toBeLessThan(3);
    // 10 bps of capture clears it; 2 bps does not.
    expect(r.allowQuote(book(20), config.tradeSizeMon).ok).toBe(true);
    expect(r.allowQuote(book(2), config.tradeSizeMon).ok).toBe(false);
  });
});

describe("counter trade", () => {
  test("stands down at the cap once the layer is on, unless asked otherwise", () => {
    expect(new RiskGuard(opts()).allowCounterTrade).toBe(false);
    expect(new RiskGuard(opts({ allowCounterTrade: true })).allowCounterTrade).toBe(true);
  });
});

describe("margin drawdown", () => {
  test("trips when the collateral halves between refreshes", () => {
    const r = new RiskGuard(opts({ maxLossUsd: 1e9 }));
    r.noteMargin(600, 20, 0.0232);
    r.noteMargin(200, 5, 0.0232); // 14.9 -> 9.6 USDC
    expect(r.state().tripped?.reason).toBe("margin-drawdown");
  });

  test("small moves do not trip", () => {
    const r = new RiskGuard(opts({ maxLossUsd: 1e9 }));
    r.noteMargin(600, 20, 0.0232);
    r.noteMargin(590, 20, 0.0232);
    expect(r.state().tripped).toBe(null);
  });
});

describe("state", () => {
  test("reports what the dashboard needs, and a trip is sticky until resumed", () => {
    const r = new RiskGuard(opts({ maxLossUsd: 1 }));
    r.noteBook(book(2));
    r.notePnl(-1);
    r.allowTrading();
    const s = r.state();
    expect(s.tripped).not.toBe(null);
    expect(s.edgeBps).toBeCloseTo(0.57, 1);
    expect(s.gasCostBps).toBeGreaterThan(s.edgeBps); // the demo's whole problem, in two numbers
    expect(r.line()).toContain("TRIPPED");
    r.resume();
    expect(r.state().tripped).toBe(null);
    expect(r.line()).toContain("risk gas");
  });

  test("the banner states the per-hour bill", () => {
    const r = new RiskGuard(opts());
    expect(r.banner()).toContain("MON/hour");
    expect(new RiskGuard(opts({ enabled: false })).banner()).toContain("off");
  });
});
