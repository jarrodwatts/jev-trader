import { test, expect } from "bun:test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Book, Market, Quote, QuoteResult, Side } from "./market";
import type { Model } from "./model";
import { Trader } from "./trader";

process.chdir(mkdtempSync(join(tmpdir(), "jev-trader-"))); // Trader writes data/events.jsonl

const book: Book = { block: 0, bid: 0.0222, ask: 0.0223, mid: 0.02225, spreadBps: 45, imbalance: 0, levels: { bids: [], asks: [] }, depthBps: {} };

test("does not cancel an order again while an in-flight tx is already cancelling it", async () => {
  const sent: number[][] = [];
  let receipts: QuoteResult[] = [];
  const market = {
    wallet: {}, address: "0x1", margin: { mon: 1e9, usdc: 1e9 },
    refresh: async () => {},
    readBook: async () => { await Bun.sleep(0); return book; },
    pollPending: async () => { const r = receipts; receipts = []; return r; },
    send: async (_block: number, side: Side, size: number, _book: Book, cancel: number[], capped: boolean): Promise<Quote> => {
      sent.push(cancel);
      return { side, price: book.bid, size, txHash: `0x${sent.length}`, gasMon: 0, cancel, status: "sent", orderId: null, capped };
    },
  } as unknown as Market;
  const model: Model = { name: "test", decide: async () => ({ action: "buy", probabilities: { buy: 1, sell: 0, hold: 0 }, upIn10: 1, latencyMs: 0, inputTokens: 0 }) };
  const placed = (block: number, tx: string, orderId: number, canceled: number[]): QuoteResult => ({
    block, canceled, quote: { side: "buy", price: book.bid, size: 200, txHash: tx, gasMon: 0, cancel: canceled, status: "placed", orderId, capped: false },
  });
  const trader = new Trader(market, model, () => {});

  await trader.onBlock(1); // places order 7
  receipts = [placed(1, "0x1", 7, [])];
  await trader.onBlock(2); // cancels 7, places 8; no receipt yet
  await trader.onBlock(3); // 7 is already being cancelled by the block 2 tx
  receipts = [placed(2, "0x2", 8, [7])];
  await trader.onBlock(4);

  // Re-cancelling 7 in block 3 would revert on chain: Kuru deletes a cancelled order, so the owner check fails.
  expect(sent).toEqual([[], [7], [], [8]]);
});
