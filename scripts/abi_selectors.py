"""Selectors and topics for every Kuru signature the bot uses. Run: python scripts/abi_selectors.py"""
from jev_trader import abi
from jev_trader.trades import TRADE_EVENT_SIG

for sig in (abi.BATCH_UPDATE, abi.GET_MARKET_PARAMS, "getL2Book()", "getVaultParams()", "bestBidAsk()", abi.DEPOSIT, abi.GET_BALANCE):
    print("0x" + abi.selector(sig).hex(), sig)
for sig in (abi.ORDER_CREATED, abi.ORDERS_CANCELED, TRADE_EVENT_SIG):
    print(abi.topic(sig), sig)
