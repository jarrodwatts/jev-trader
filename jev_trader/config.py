import os

from dotenv import load_dotenv

load_dotenv()


def env(key: str, fallback: str | None = None) -> str | None:
    return os.environ.get(key, fallback)


def number(s: str) -> int | float:
    """Like JS Number(s) for the values we read: ints stay ints so they print and serialize as 200, not 200.0."""
    f = float(s) if s.strip() else 0.0
    return int(f) if f.is_integer() else f


def num(key: str) -> int | float | None:
    return number(env(key)) if env(key) else None


class Config:
    rpc_url = env("RPC_URL", "https://rpc.monad.xyz")  # sends, receipts, nonce, gas estimation
    read_rpc_url = env("READ_RPC_URL", "https://rpc.monad.xyz")  # book reads + eth_blockNumber polling + trade logs
    ws_url = env("WS_URL")  # optional; polling backstop always runs
    chain_id = 143
    market = env("MARKET", "0x065C9d28E428A0db40191a54d33d5b7c71a9C394")  # Kuru MON-USDC
    # Kuru MarginAccount this market settles against (slot 73 of the OrderBook proxy; verifiedMarket(market) is true).
    margin_account = env("MARGIN_ACCOUNT", "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5")
    private_key = env("PRIVATE_KEY")
    dry_run = env("DRY_RUN") == "true" or not env("PRIVATE_KEY")
    trade_size_mon = number(env("TRADE_SIZE_MON", "200"))  # Kuru MON-USDC minimum order is 200 MON
    max_position_mon = number(env("MAX_POSITION_MON", "1000"))
    bankroll_usd = number(env("BANKROLL_USD", "100"))  # used for pnlPct
    # Quote this many ticks inside the touch (0 = join the best bid/ask). Never crosses: clamps to the touch when the spread is too tight.
    quote_inside_ticks = number(env("QUOTE_INSIDE_TICKS", "1"))
    # Startup deposits into the Kuru margin account, topped up to these balances. Limit orders draw from margin, not the wallet.
    margin_mon = number(env("MARGIN_MON", "600"))
    margin_usdc = number(env("MARGIN_USDC", "20"))
    # Monad charges gas on the LIMIT, so never estimate per block: estimate once at init (or override) and hardcode.
    gas_limit = num("GAS_LIMIT")
    gas_limit_fallback = 350_000  # batchUpdate: one cancel + one post-only place measured at ~282k for the place alone
    # EIP-1559 type-2 only. Effective price = base + priority, so a high static cap is free.
    max_fee_gwei = number(env("MAX_FEE_GWEI", "400"))
    priority_fee_gwei = number(env("PRIORITY_FEE_GWEI", "2"))  # Monad hardcodes eth_maxPriorityFeePerGas at 2
    pending_blocks = 10  # give up on a tx with no receipt after this many blocks
    refresh_blocks = 200  # how often to refresh the fee estimate, margin balances and the vault check
    horizon_blocks = number(env("HORIZON_BLOCKS", "100"))  # the model is asked about the move over this many blocks (~30 s)
    model = env("MODEL", "mock")  # "mock" | "jev"
    jev_model_id = env("JEV_MODEL_ID", "jev-latest")
    jev_usd_per_mtok = 0.042
    host = env("HOST", "127.0.0.1")  # dashboard + API; 0.0.0.0 exposes them beyond this machine (the Docker image does)
    port = number(env("PORT", "3000"))
    history_size = 1000


config = Config()
