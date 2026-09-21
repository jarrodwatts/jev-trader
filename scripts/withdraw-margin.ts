// Pulls everything free out of Kuru's MarginAccount back into the wallet. Cancel resting orders first:
// what they lock is not withdrawable. Needs PRIVATE_KEY (run it under `op run`).
import { ethers } from "ethers";
import MarginAccountAbi from "@kuru-labs/kuru-sdk/abi/MarginAccount.json";
import { config } from "../src/config";

const USDC = "0x754704Bc059F8C67012fEd69BC8A327a5aafb603";

if (!config.privateKey) { console.error("PRIVATE_KEY not set"); process.exit(1); }
const provider = new ethers.providers.StaticJsonRpcProvider(config.rpcUrl, config.chainId);
const wallet = new ethers.Wallet(config.privateKey, provider);
const iface = new ethers.utils.Interface(MarginAccountAbi.abi);
const margin = new ethers.Contract(config.marginAccount, ["function getBalance(address,address) view returns (uint256)"], provider);
const erc20 = new ethers.Contract(USDC, ["function balanceOf(address) view returns (uint256)"], provider);

const show = async (when: string) => {
  const [wm, wu, mm, mu] = await Promise.all([
    provider.getBalance(wallet.address), erc20.balanceOf(wallet.address),
    margin.getBalance(wallet.address, ethers.constants.AddressZero), margin.getBalance(wallet.address, USDC),
  ]);
  const n = (v: ethers.BigNumber, d = 18) => Number(ethers.utils.formatUnits(v, d)).toFixed(3);
  console.log(`${when.padEnd(6)} wallet ${n(wm).padStart(10)} MON / ${n(wu, 6).padStart(8)} USDC   margin ${n(mm).padStart(10)} MON / ${n(mu, 6).padStart(8)} USDC`);
};

await show("before");
const data = iface.encodeFunctionData("batchWithdrawMaxTokens", [[ethers.constants.AddressZero, USDC]]);
const estimate = await provider.estimateGas({ from: wallet.address, to: config.marginAccount, data });
const tx = await wallet.sendTransaction({
  to: config.marginAccount,
  data,
  gasLimit: estimate.mul(13).div(10), // Monad charges the limit, so keep the headroom small
  maxFeePerGas: ethers.utils.parseUnits(String(config.maxFeeGwei), "gwei"),
  maxPriorityFeePerGas: ethers.utils.parseUnits(String(config.priorityFeeGwei), "gwei"),
  type: 2,
});
console.log(`withdrawing: ${tx.hash}`);
const r = await tx.wait(1);
console.log(r.status === 1 ? "withdrawn" : "reverted");
await show("after");
