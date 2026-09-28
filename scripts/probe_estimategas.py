"""Does eth_estimateGas accept the SDK's hardcoded gasPrice=1 gwei (below Monad's ~100 gwei base fee) from a funded account? Run: python scripts/probe_estimategas.py"""
import asyncio
import json

from jev_trader.chain import post_json, session

RPC = "https://rpc.monad.xyz"
MARKET = "0x065C9d28E428A0db40191a54d33d5b7c71a9C394"


async def post(method: str, params: list):
    return await post_json(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


async def main():
    blk = (await post("eth_getBlockByNumber", ["latest", True]))["result"]
    print("block", int(blk["number"], 16), "baseFeePerGas", int(blk["baseFeePerGas"], 16) / 1e9, "gwei, txs", len(blk["transactions"]))
    froms = list(dict.fromkeys(t["from"] for t in blk["transactions"]))[:5]
    for frm in froms:
        bal = int((await post("eth_getBalance", [frm, "latest"]))["result"], 16) / 1e18
        if bal < 1:
            continue
        r1 = await post("eth_estimateGas", [{"from": frm, "to": MARKET, "data": "0xb4de8b70", "gasPrice": "0x3b9aca00"}])
        r2 = await post("eth_estimateGas", [{"from": frm, "to": MARKET, "data": "0xb4de8b70"}])
        print(f"from={frm[:10]} bal={bal:.2f} MON  gasPrice=1gwei -> {json.dumps(r1.get('result') or r1.get('error'))}   no gasPrice -> {json.dumps(r2.get('result') or r2.get('error'))}")
        break
    await session().close()


asyncio.run(main())
