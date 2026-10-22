#!/usr/bin/env python3
"""client_example.py - reference buyer for base-oracle (x402 v2, Base mainnet).

Flow every paid endpoint uses:
  1) GET the endpoint -> HTTP 402 with an x402 v2 challenge
     (x402Version=2, accepts[scheme=exact, network=eip155:8453, amount,
      asset=USDC, payTo, extra={name:'USD Coin',version:'2'}]).
  2) Send `amount` USDC (atomic, 6 decimals) on Base to `payTo`.
  3) Retry the same request with header  X-PAYMENT-TX: <0xtxhash>.
     The service verifies the receipt on-chain (real USDC transfer to payTo,
     amount covered, anti-replay ledger) and returns the paid data.

No facilitator, no API key. Only your tx hash is needed.

Usage:
  python3 client_example.py --base http://127.0.0.1:8795          # dry run: shows the challenge
  python3 client_example.py --base URL --tx 0xHASH --addr 0xADDR  # paid call (after sending USDC)
"""
import argparse, json, sys, urllib.request, urllib.error

FREE = ['/health', '/v1/sample']
PAID = ['/v1/wallet-report', '/v1/token-safety', '/v1/gas-oracle',
        '/v1/tx-explain', '/v1/x402-audit', '/v1/portfolio',
        '/v1/momentum', '/v1/funding', '/v1/arb-scan']

def get(base, path, tx=None):
    req = urllib.request.Request(base + path)
    if tx:
        req.add_header('X-PAYMENT-TX', tx)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='service base URL')
    ap.add_argument('--endpoint', default='/v1/wallet-report')
    ap.add_argument('--tx', help='0x... payment tx hash (after sending USDC)')
    ap.add_argument('--addr', default='', help='wallet to analyze (paid endpoints)')
    a = ap.parse_args()

    st, body = get(a.base, '/v1/sample')
    print('free sample (%d):' % st, json.dumps(body)[:200])

    st, ch = get(a.base, a.endpoint + (('?addr=' + a.addr) if a.addr else ''))
    if st != 402:
        print('expected 402, got', st); sys.exit(1)
    acc = ch['accepts'][0]
    print('402 challenge: pay %s atomic USDC to %s on %s' % (
        acc['amount'], acc['payTo'], acc['network']))

    if not a.tx:
        print('--> send the USDC on Base, then re-run with --tx 0xHASH')
        return
    st, data = get(a.base, a.endpoint + (('?addr=' + a.addr) if a.addr else ''), tx=a.tx)
    print('paid response (%d):' % st, json.dumps(data)[:400])
    sys.exit(0 if st == 200 else 2)

if __name__ == '__main__':
    main()
