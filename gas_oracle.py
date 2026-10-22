#!/usr/bin/env python3
"""gas_oracle.py — live Base mainnet gas oracle for the base-oracle API.

Pure stdlib. Read-only: eth_gasPrice + eth_feeHistory over public Base RPCs.
No signing, no keys, nothing spent. Returns a JSON report agents can use to
time transactions: current gas price, recent base-fee trend, and suggested
maxFeePerGas / maxPriorityFeePerGas in wei and gwei.
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request

RPCS = [
    'https://mainnet.base.org',
    'https://base.llamarpc.com',
    'https://base-rpc.publicnode.com',
    'https://base.drpc.org',
]
TIMEOUT = 10


def rpc(method, params):
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                          'params': params}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={'Content-Type': 'application/json', 'User-Agent': 'base-oracle/1.4.0'})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read())
            if 'error' in data:
                last = data['error']
                continue
            return data['result']
        except Exception as e:  # noqa: BLE001 - try next RPC
            last = repr(e)
    raise RuntimeError(f'all RPCs failed for {method}: {last}')


def hx(x):
    return int(x, 16) if isinstance(x, str) else int(x)


def gwei(wei):
    return round(wei / 1e9, 4)


def report():
    gas_price = hx(rpc('eth_gasPrice', []))
    fh = rpc('eth_feeHistory', ['0x14', 'latest', [25]])
    base_fees = [hx(b) for b in fh.get('baseFeePerGas', [])]
    rewards = [hx(p[0]) for p in fh.get('reward', []) if p]

    trend = 'flat'
    if len(base_fees) >= 2:
        first = statistics.mean(base_fees[:5])
        last = statistics.mean(base_fees[-5:])
        if last > first * 1.05:
            trend = 'rising'
        elif last < first * 0.95:
            trend = 'falling'

    prio = int(statistics.median(rewards)) if rewards else 0
    next_base = base_fees[-1] if base_fees else gas_price
    suggested_max = next_base * 2 + prio

    return {
        'schema': 'base-oracle/gas/1',
        'chain': 'base',
        'chainId': 'eip155:8453',
        'gas_price_wei': gas_price,
        'gas_price_gwei': gwei(gas_price),
        'base_fee_wei': next_base,
        'base_fee_gwei': gwei(next_base),
        'base_fee_trend_20_blocks': trend,
        'priority_fee_p25_wei': prio,
        'priority_fee_p25_gwei': gwei(prio),
        'suggested': {
            'maxFeePerGas_wei': suggested_max,
            'maxFeePerGas_gwei': gwei(suggested_max),
            'maxPriorityFeePerGas_wei': prio,
            'maxPriorityFeePerGas_gwei': gwei(prio),
        },
        'blocks_sampled': len(base_fees),
        'disclaimer': 'live public-RPC estimate; not financial advice',
    }


def _selftest():
    checks = 0
    rep = report()
    assert rep['schema'] == 'base-oracle/gas/1'; checks += 1
    assert rep['chainId'] == 'eip155:8453'; checks += 1
    assert rep['gas_price_wei'] > 0; checks += 1
    assert rep['base_fee_wei'] > 0; checks += 1
    assert rep['base_fee_trend_20_blocks'] in ('rising', 'falling', 'flat'); checks += 1
    s = rep['suggested']
    assert s['maxFeePerGas_wei'] >= s['maxPriorityFeePerGas_wei']; checks += 1
    assert rep['blocks_sampled'] > 0; checks += 1
    print('%d/7 PASS' % checks if checks == 7 else '%d/7 FAIL' % checks)
    return 0 if checks == 7 else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    print(json.dumps(report(), indent=2, sort_keys=True))
