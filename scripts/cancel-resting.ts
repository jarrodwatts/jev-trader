// Stopping the bot does not take its orders off Kuru's book. This cancels whatever is still resting,
// which releases the margin they lock. Needs PRIVATE_KEY (run it under `op run`).
import { ethers } from "ethers";
import * as Kuru from "@kuru-labs/kuru-sdk";
import OrderBookAbi from "@kuru-labs/kuru-sdk/abi/OrderBook.json";
import { config } from "../src/config";

const ORDER_ABI = ["function s_orders(uint40) view returns (address ownerAddress, uint96 size, uint40 prev, uint40 next, uint40 flippedId, uint32 price, uint32 flippedPrice, bool isBuy)"];

if (!config.privateKey) { console.error("PRIVATE_KEY not set"); process.exit(1); }
const ids = process.argv.slice(2).map(Number).filter(Boolean);
if (!ids.length) { console.error("usage: bun run scripts/cancel-resting.ts <orderId> [orderId...]"); process.exit(1); }

const provider = new ethers.providers.StaticJsonRpcProvider(config.rpcUrl, config.chainId);
const wallet = new ethers.Wallet(config.privateKey, provider);
const params = await Kuru.ParamFetcher.getMarketParams(provider, config.market);
const sizeScale = 10 ** Math.log10(Number(params.sizePrecision));
const priceScale = 10 ** Math.log10(Number(params.pricePrecision));
const book = new ethers.Contract(config.market, ORDER_ABI, provider);

const live: number[] = [];
for (const id of ids) {
  const o = await book.s_orders(id);
  const mine = o.ownerAddress.toLowerCase() === wallet.address.toLowerCase();
  const size = Number(o.size) / sizeScale;
  // s_orders keeps the struct after an order fills, so size here is what it was placed at, not what
  // is still resting. batchUpdate tolerates ids that are already gone, so passing a stale one is free.
  console.log(`order ${id}  owner=${mine ? "us" : o.ownerAddress.slice(0, 10)}  size=${size}  ${o.isBuy ? "buy" : "sell"} @ ${Number(o.price) / priceScale}`);
  if (mine && size > 0) live.push(id);
}
if (!live.length) { console.log("nothing of ours is still resting"); process.exit(0); }

// batchCancelOrders reverts on orders placed through batchUpdate; cancelling through batchUpdate
// with empty price/size arrays is the path the bot itself uses, and it works.
const iface = new ethers.utils.Interface(OrderBookAbi.abi);
const data = iface.encodeFunctionData("batchUpdate", [[], [], [], [], live, true]);
const estimate = await provider.estimateGas({ from: wallet.address, to: config.market, data });
const tx = await wallet.sendTransaction({
  to: config.market,
  data,
  gasLimit: estimate.mul(13).div(10), // Monad charges the limit, so keep the headroom small
  maxFeePerGas: ethers.utils.parseUnits(String(config.maxFeeGwei), "gwei"),
  maxPriorityFeePerGas: ethers.utils.parseUnits(String(config.priorityFeeGwei), "gwei"),
  type: 2,
});
console.log(`cancelling ${live.length} orders: ${tx.hash}`);
const r = await tx.wait(1);
console.log(r.status === 1 ? "cancelled" : "reverted");
