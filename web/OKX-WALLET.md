# Read-only OKX Web3 wallet

The local dashboard can discover the OKX browser extension through `window.okxwallet`. Install the extension in the same browser and explicitly choose **Conectar OKX**. Installing the mobile app alone does not inject a provider into a desktop browser.

The panel queries the authorized account, chain and MON/USDC balances on Monad (chain 143). A different or unconfirmed network blocks balance reads. **Trocar para Monad** requests a network switch only after a separate click; the extension must already know the network. The application rereads the chain before querying balances. MON and native USDC (`0x754704Bc059F8C67012fEd69BC8A327a5aafb603`) are read at the same confirmed block and formatted with exact integer arithmetic.

Errors, rejected authorization, invalid responses and timeouts appear as unavailable data, never fabricated zero balances. Account, network and disconnect events invalidate previous balances and pending requests. Refresh uses existing authorization. Local disconnect clears this dashboard's session; it does not revoke extension permissions.

Connecting the panel does not enable the trading executor. It never requests a signature, sends a transaction, approves a token, places an order or moves funds. Wallet addresses and balances remain in browser memory and are not sent to the backend or persisted. Web3 balances do not represent an OKX Exchange account, Kuru margin, available trading capital or a BRL valuation.

Run the frontend on port 3001, with the backend feed on `http://127.0.0.1:3000`. Set `NEXT_PUBLIC_API_URL` explicitly to use another feed. Wallet consultation works while the feed is offline.

```sh
bun run test
bun run typecheck
bun run build --webpack
```

The provider-boundary tests use synthetic accounts and balances. Passing them does not certify the user's installed extension, account, funds, live trading or profitability.

Sources: [OKX Ethereum provider](https://web3.okx.com/onchainos/dev-docs/wallet/dapp-connect/chains/evm/provider), [Monad network information](https://docs.monad.xyz/developer-essentials/network-information), [Circle USDC contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses).
