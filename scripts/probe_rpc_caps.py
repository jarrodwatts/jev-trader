"""
Probe: does rpc.monad.xyz accept JSON-RPC batches? Does eth_estimateGas accept the SDK's 1 gwei gasPrice from a funded account?
Raw latency of getL2Book vs bestBidAsk. Run: python scripts/probe_rpc_caps.py
"""
import asyncio
import json
import time

from jev_trader.chain import post_json, session

RPC = "https://rpc.monad.xyz"
MARKET = "0x065C9d28E428A0db40191a54d33d5b7c71a9C394"
VAULT = "0x838c2d3fD4DB5eB2F185cbE7697fbaAce52b34d7"


async def post(body):
    t = time.perf_counter()
    j = await post_json(RPC, body)
    return (time.perf_counter() - t) * 1000, j


def call(id: int, data: str, tag="latest"):
    return {"jsonrpc": "2.0", "id": id, "method": "eth_call", "params": [{"to": MARKET, "data": data}, tag]}


async def main():
    # batch of two eth_calls
    for _ in range(3):
        ms, j = await post([call(1, "0x46fdfbb1"), call(2, "0x88bb4f60")])
        lens = ",".join(str(len(x["result"])) if x.get("result") else json.dumps(x.get("error")) for x in j) if isinstance(j, list) else json.dumps(j)[:200]
        print(f"batch(getL2Book,getVaultParams) {ms:.0f}ms isArray={isinstance(j, list)} lens={lens}")
    # single getL2Book raw
    for _ in range(5):
        ms, j = await post(call(1, "0x46fdfbb1"))
        print(f"raw getL2Book {ms:.0f}ms len={len(j.get('result') or '')}")
    # bestBidAsk raw
    for _ in range(3):
        ms, j = await post(call(1, "0xb4de8b70"))
        r = j["result"]
        print(f"raw bestBidAsk {ms:.0f}ms bid={int(r[2:66], 16)} ask={int(r[66:130], 16)}")
    # estimateGas with 1 gwei gas price from a funded account (the market contract itself holds MON; also the vault)
    for frm in (MARKET, VAULT):
        ms, j = await post({"jsonrpc": "2.0", "id": 1, "method": "eth_estimateGas", "params": [{"from": frm, "to": MARKET, "value": "0x0", "data": "0xb4de8b70", "gasPrice": "0x3b9aca00"}]})
        print(f"estimateGas from={frm[:10]} gasPrice=1gwei {ms:.0f}ms ->", json.dumps(j)[:160])
        _, b = await post({"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [frm, "latest"]})
        print(f"  balance {int(b['result'], 16) / 1e18} MON")
    # estimateGas with no gasPrice
    _, no_gp = await post({"jsonrpc": "2.0", "id": 1, "method": "eth_estimateGas", "params": [{"from": MARKET, "to": MARKET, "data": "0xb4de8b70"}]})
    print("estimateGas no gasPrice ->", json.dumps(no_gp)[:120])
    # block time sanity: two eth_blockNumber 1 s apart
    _, b1 = await post({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    await asyncio.sleep(1)
    _, b2 = await post({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    print("blocks in ~1s:", int(b2["result"], 16) - int(b1["result"], 16))
    await session().close()


asyncio.run(main())
