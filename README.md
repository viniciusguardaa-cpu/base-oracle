# base-oracle

Live Base (EVM L2) data oracle paid per-request in USDC via x402 v2 — no facilitator, no API key.

- Free: `/health`, `/v1/sample` (latest block + gas price, real data)
- Paid (0.05 USDC each, payTo verified on-chain): `/v1/wallet-report`, `/v1/token-safety`, `/v1/gas-oracle`, `/v1/tx-explain`, `/v1/x402-audit`, `/v1/portfolio`, `/v1/momentum`, `/v1/funding`, `/v1/arb-scan`
- Every 402 response carries the x402 v2 challenge in BOTH the `payment-required` header (base64) and the JSON body: `{x402Version:2, resource, accepts:[{scheme:'exact', network:'eip155:8453', amount, asset:USDC, payTo, extra:{name:'USD Coin',version:'2'}}]}`.
- Payment flow: GET -> 402 -> send USDC on Base to payTo -> retry with `X-PAYMENT-TX: 0xhash` -> receipt verified on-chain (USDC Transfer event, destination, amount, anti-replay ledger) -> 200 with data.

## Try it

```bash
python3 client_example.py --base https://arturo-epiphytical-torrie.ngrok-free.dev
```

`client_example.py` is a stdlib-only reference buyer: it prints the challenge, explains how to pay, and retries with your tx hash.

## Run it yourself

Single file, Python 3.9+ stdlib only:

```bash
python3 base_oracle.py serve --port 8795
```

No secrets required: the service never signs anything; it only reads public RPC endpoints.
