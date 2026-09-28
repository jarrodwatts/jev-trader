"""
The handful of Kuru contract signatures the bot touches, encoded and decoded with eth-abi.
Signatures are from the Kuru SDK's OrderBook.json / MarginAccount.json ABIs (v0.0.95). None of the
events index a field, so every argument lives in the log data.
"""
from eth_abi import decode, encode
from eth_utils import keccak


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def topic(sig: str) -> str:
    return "0x" + keccak(text=sig).hex()


def arg_types(sig: str) -> list[str]:
    inner = sig[sig.index("(") + 1 : sig.rindex(")")]
    return inner.split(",") if inner else []


def encode_call(sig: str, args: list) -> str:
    return "0x" + (selector(sig) + encode(arg_types(sig), args)).hex()


def decode_hex(types: list[str], data: str) -> tuple:
    return decode(types, bytes.fromhex(data[2:] if data.startswith("0x") else data))


# OrderBook
BATCH_UPDATE = "batchUpdate(uint32[],uint96[],uint32[],uint96[],uint40[],bool)"
GET_MARKET_PARAMS = "getMarketParams()"
MARKET_PARAMS_TYPES = ["uint32", "uint96", "address", "uint256", "address", "uint256", "uint32", "uint96", "uint96", "uint256", "uint256"]
ORDER_CREATED = "OrderCreated(uint40,address,uint96,uint32,bool)"  # orderId, owner, size, price, isBuy
ORDERS_CANCELED = "OrdersCanceled(uint40[],address)"  # orderId[], owner

# MarginAccount
DEPOSIT = "deposit(address,address,uint256)"  # user, token, amount
GET_BALANCE = "getBalance(address,address)"  # user, token

# ERC20
ALLOWANCE = "allowance(address,address)"
APPROVE = "approve(address,uint256)"
BALANCE_OF = "balanceOf(address)"

MAX_UINT256 = (1 << 256) - 1
