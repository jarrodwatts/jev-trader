const env = (key: string, fallback?: string) => process.env[key] ?? fallback;
const num = (key: string) => (env(key) ? Number(env(key)) : undefined);
const bool = (key: string) => env(key) === "true";

export const config = {
  rpcUrl: env("RPC_URL", "https://rpc.monad.xyz")!, // sends, receipts, nonce, gas estimation
  readRpcUrl: env("READ_RPC_URL", "https://rpc.monad.xyz")!, // book reads + eth_blockNumber polling + trade logs
  wsUrl: env("WS_URL"), // optional; polling backstop always runs
  chainId: 143,
  market: env("MARKET", "0x065C9d28E428A0db40191a54d33d5b7c71a9C394")!, // Kuru MON-USDC
  /** Kuru MarginAccount this market settles against (slot 73 of the OrderBook proxy; verifiedMarket(market) is true). */
  marginAccount: env("MARGIN_ACCOUNT", "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5")!,
  privateKey: env("PRIVATE_KEY"),
  dryRun: env("DRY_RUN") === "true" || !env("PRIVATE_KEY"),
  tradeSizeMon: Number(env("TRADE_SIZE_MON", "200")), // Kuru MON-USDC minimum order is 200 MON
  maxPositionMon: Number(env("MAX_POSITION_MON", "1000")),
  bankrollUsd: Number(env("BANKROLL_USD", "100")), // used for pnlPct
  /** Quote this many ticks inside the touch (0 = join the best bid/ask). Never crosses: clamps to the touch when the spread is too tight. */
  quoteInsideTicks: Number(env("QUOTE_INSIDE_TICKS", "1")),
  /** Startup deposits into the Kuru margin account, topped up to these balances. Limit orders draw from margin, not the wallet. */
  marginMon: Number(env("MARGIN_MON", "600")),
  marginUsdc: Number(env("MARGIN_USDC", "20")),
  // Monad charges gas on the LIMIT, so never estimate per block: estimate once at init (or override) and hardcode.
  gasLimit: num("GAS_LIMIT"),
  gasLimitFallback: 350_000, // batchUpdate: one cancel + one post-only place measured at ~282k for the place alone
  // EIP-1559 type-2 only. Effective price = base + priority, so a high static cap is free.
  maxFeeGwei: Number(env("MAX_FEE_GWEI", "400")),
  priorityFeeGwei: Number(env("PRIORITY_FEE_GWEI", "2")), // Monad hardcodes eth_maxPriorityFeePerGas at 2
  pendingBlocks: 10, // give up on a tx with no receipt after this many blocks
  refreshBlocks: 200, // how often to refresh the fee estimate, margin balances and the vault check
  horizonBlocks: Number(env("HORIZON_BLOCKS", "100")), // the model is asked about the move over this many blocks (~30 s)
  model: env("MODEL", "mock") as "mock" | "jev",
  jevModelId: env("JEV_MODEL_ID", "jev-latest")!,
  jevUsdPerMTok: 0.042,
  /**
   * Opt-in trade limiter (`src/risk.ts`). Off by default: the demo is meant to post on every block,
   * so a budget that trips would be the demo stopping. Every field is a no-op until RISK_ENABLED=true.
   */
  risk: {
    enabled: bool("RISK_ENABLED"),
    /** Monad charges the LIMIT, so per-block cost is gasLimit x (base+priority) whether it lands or reverts. */
    maxGasMonPerHour: Number(env("RISK_MAX_GAS_MON_PER_HOUR", "100")),
    maxTotalGasMon: Number(env("RISK_MAX_GAS_MON", "2000")),
    maxLossUsd: Number(env("RISK_MAX_LOSS_USD", "50")),
    /** Stop when this share of the last `revertWindow` quotes reverted (the book moved through the price). */
    maxRevertPct: Number(env("RISK_MAX_REVERT_PCT", "60")),
    revertWindow: Number(env("RISK_REVERT_WINDOW", "200")),
    /** Blocks in a row with no decision (the model could not keep up with 300 ms) before stopping. */
    maxLateStreak: Number(env("RISK_MAX_LATE_STREAK", "60")),
    pauseMinutes: Number(env("RISK_PAUSE_MINUTES", "30")),
    /**
     * Minimum edge a quote must have, in bps of notional, before it is worth a block's gas.
     * 0 = require the quote to pay for its own gas; negative = do not check.
     */
    minEdgeBps: Number(env("RISK_MIN_EDGE_BPS", "0")),
    /**
     * When the position cap blocks the model's side: false = stand down for that block, true = take the
     * other side. Leave unset for the default, which follows the layer: stand down while it is on (at the
     * cap, do not trade against the model) and take the other side while it is off, as the demo does.
     */
    allowCounterTrade: env("RISK_ALLOW_COUNTER_TRADE") === undefined ? undefined : bool("RISK_ALLOW_COUNTER_TRADE"),
    /** Safety ceiling for a startup gas estimate; the env GAS_LIMIT override is never clamped. */
    estimateGasLimitCeiling: Number(env("RISK_ESTIMATE_GAS_CEILING", "1000000")),
    stateFile: env("RISK_STATE_FILE", "data/risk.json")!,
  },
  port: Number(env("PORT", "3000")),
  historySize: 1000,
};
