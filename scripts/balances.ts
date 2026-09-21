// Where the money actually is: wallet holdings plus what Kuru's MarginAccount records under our address.
import { ethers } from "ethers";
import { config } from "../src/config";

const RPC = config.readRpcUrl;
const USDC = "0x754704Bc059F8C67012fEd69BC8A327a5aafb603";
const address = process.argv[2] ?? (config.privateKey && new ethers.Wallet(config.privateKey).address);
if (!address) { console.error("usage: bun run scripts/balances.ts <address>  (or set PRIVATE_KEY)"); process.exit(1); }

const p = new ethers.providers.StaticJsonRpcProvider(RPC, config.chainId);
const erc20 = new ethers.Contract(USDC, ["function balanceOf(address) view returns (uint256)"], p);
const margin = new ethers.Contract(config.marginAccount, ["function getBalance(address,address) view returns (uint256)"], p);

const [walletMon, walletUsdc, marginMon, marginUsdc] = await Promise.all([
  p.getBalance(address),
  erc20.balanceOf(address),
  margin.getBalance(address, ethers.constants.AddressZero),
  margin.getBalance(address, USDC),
]);

const mon = (v: ethers.BigNumber) => Number(ethers.utils.formatEther(v));
const usd = (v: ethers.BigNumber) => Number(ethers.utils.formatUnits(v, 6));
const row = (label: string, m: number, u: number) => console.log(`${label.padEnd(14)} MON ${m.toFixed(3).padStart(10)}   USDC ${u.toFixed(3).padStart(8)}`);

console.log(address);
row("wallet", mon(walletMon), usd(walletUsdc));
row("kuru margin", mon(marginMon), usd(marginUsdc));
row("total", mon(walletMon) + mon(marginMon), usd(walletUsdc) + usd(marginUsdc));
