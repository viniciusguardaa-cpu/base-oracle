# base-oracle — x402-paid trading-intelligence API on Base mainnet

Live, hosted API for agents. One FREE endpoint (/v1/sample) + eight paid endpoints, each 0.05 USDC per call,
settled in USDC on Base (eip155:8453) via the x402 `exact` scheme. No account,
no API key, no facilitator — payment is verified directly on-chain by receipt,
and each transaction hash pays exactly once (anti-replay ledger).

- Base URL: https://arturo-epiphytical-torrie.ngrok-free.dev
- OpenAPI: https://arturo-epiphytical-torrie.ngrok-free.dev/openapi.json
- Discovery: https://arturo-epiphytical-torrie.ngrok-free.dev/.well-known/x402

## Paid endpoints (0.05 USDC each)

| Endpoint | What it returns |
|---|---|
| `GET /v1/sample` | FREE — latest Base block + gas price, no payment needed. Try it now. |
| `GET /v1/wallet-report?address=0x…` | ETH/USDC balances, nonce, contract flag, live block |
| `GET /v1/token-safety?token=0x…` | Liquidity, 24h volume, buy/sell ratio, risk flags |
| `GET /v1/arb-scan` | Cross-DEX price dislocations on Base pairs |
| `GET /v1/momentum` | Momentum signals across Base DEX markets |
| `GET /v1/funding` | Hyperliquid perp funding rates and open interest |
| `GET /v1/portfolio?address=0x…` | Portfolio composition for a Base address |
| `GET /v1/x402-audit?target=https://…` | Audit any x402 seller: discovery doc, OpenAPI, 402 challenges, scheme/network/payTo/amount |
| `GET /v1/gas-oracle` | Live Base gas: gasPrice, 20-block base-fee trend, suggested EIP-1559 maxFee/maxPriorityFee |

## How to pay

1. Call any paid endpoint — you get HTTP 402 with a canonical x402 challenge
   (`scheme: exact`, `network: eip155:8453`, `asset: USDC`, `payTo`, `maxAmountRequired: 50000`).
2. Send ≥ 0.05 USDC on Base to `payTo`.
3. Repeat the call with header `X-PAYMENT-TX: 0x<txhash>` — the server verifies
   the receipt on-chain (status, USDC contract, Transfer event, destination,
   amount) and returns the report.

Read-only service. Failed upstream fetches after payment return 502 and the
payment is NOT consumed. Not financial advice.
- `GET /v1/tx-explain?tx=0x...` -- explain any Base transaction: status, value, fee, ERC-20 transfers (0.05 USDC)
