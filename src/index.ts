import { config } from "./config";
import { startBlockFeed } from "./chain";
import { Market } from "./market";
import { createModel } from "./model";
import { Trader } from "./trader";
import { log10 } from "./book";
import { startServer } from "./server";

const market = new Market();
await market.init();
const model = createModel();

const server = startServer(
  { model: model.name, wallet: market.address, dryRun: config.dryRun, market: config.market, risk: config.risk.enabled, startedAt: Date.now() },
  () => trader.history,
);
const trader = new Trader(
  market,
  model,
  (e, t) => {
    server.broadcast(e);
    if (e.decision && !e.decision.late) {
      const p = e.decision.probabilities;
      const q = e.quote;
      const quote = !q ? ` NO QUOTE (${trader.lastSkipReason ?? "cap or funds on both sides"})` : ` ${q.side.toUpperCase()} ${q.size} @ ${q.price.toFixed(6)}${q.capped ? " capped" : ""}${q.status === "sim" ? " (sim)" : ` cancel ${q.cancel.length} ${q.txHash}`}`;
      console.log(`#${e.block} ${e.mid.toFixed(6)} b${(p.buy * 100).toFixed(0)} s${(p.sell * 100).toFixed(0)} ${e.decision.latencyMs}ms${quote} pnl $${e.totals.pnlUsd}${t ? ` · read ${t.readMs}ms loop ${t.loopMs}ms` : ""}`);
    }
    if (e.risk && e.block % config.refreshBlocks === 0) console.log(`#${e.block} ${trader.risk.line()}`);
  },
  (block, fill) => {
    server.broadcastFill(block, fill);
    console.log(`#${block} FILL ${fill.side} ${fill.size} @ ${fill.price.toFixed(6)}${fill.simulated ? " (sim)" : ` order ${fill.orderId} ${fill.txHash}`}`);
  },
  (block, quote) => {
    server.broadcastQuote(block, quote);
    if (quote.status !== "placed") console.log(`#${block} ${quote.status.toUpperCase()} ${quote.side} @ ${quote.price.toFixed(6)} gas ${quote.gasMon.toFixed(6)} MON ${quote.txHash}`);
  },
);
trader.attachTradeFeed(log10(market.params.sizePrecision));
trader.risk.onTripHook((t) => console.error(`\n# RISK TRIP ${t.reason}: ${t.detail}\n#   ${t.pnlUsd >= 0 ? "+" : ""}$${t.pnlUsd.toFixed(2)} P&L · ${t.gasMon.toFixed(4)} MON of gas spent · paused until ${new Date(t.resumeAt).toISOString()}\n`));
console.log(trader.risk.banner());

console.log(`jev-trader · model=${model.name} · post-only ${config.quoteInsideTicks} tick inside the touch · horizon ${config.horizonBlocks} blocks · ${config.dryRun ? "DRY RUN" : `wallet ${market.address}`} · market ${config.market} · read ${config.readRpcUrl} · :${config.port}`);
if (!config.dryRun && !config.risk.enabled) {
  console.warn("LIVE with the risk layer off: every 300 ms block spends the gas limit whether the order lands, reverts or is cancelled, and nothing stops the loop.\n  RISK_ENABLED=true puts a cap on gas, losses, reverts and late blocks.");
}
startBlockFeed((block) => trader.onBlock(block));
