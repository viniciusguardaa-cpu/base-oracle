#!/usr/bin/env python3
"""price_oracle.py v1.0.0 — crypto price feed for base-oracle.

Read-only. Fetches real spot prices from CoinGecko's free public API
(no key, no auth) for a fixed set of assets, returns USD + BRL prices,
24h change, and market cap. No signing, no spending, stdlib only.
"""
import json, sys, urllib.request, time

API = 'https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd,brl&include_24hr_change=true&include_market_cap=true'
ASSETS = {
    'eth': 'ethereum', 'weth': 'weth', 'btc': 'bitcoin', 'wbtc': 'wrapped-bitcoin',
    'usdc': 'usd-coin', 'usdt': 'tether', 'dai': 'dai', 'cbeth': 'coinbase-wrapped-staked-ether',
    'aero': 'aerodrome-finance', 'degen': 'degen-base', 'brett': 'based-brett', 'toshi': 'toshi',
}
UA = {'User-Agent': 'base-oracle/1.9'}

def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def price_report(symbols=None):
    """Return price report dict. symbols: list of short names or None for all."""
    wanted = ASSETS if not symbols else {s: ASSETS[s] for s in symbols if s in ASSETS}
    if not wanted:
        return {'ok': False, 'error': 'unknown symbols; supported: ' + ','.join(sorted(ASSETS))}
    ids = ','.join(sorted(set(wanted.values())))
    raw = _get(API.format(ids=ids))
    out = {'ok': True, 'source': 'coingecko', 'fetched_at': int(time.time()), 'prices': {}}
    for short, gid in sorted(wanted.items()):
        d = raw.get(gid)
        if not d:
            continue
        out['prices'][short] = {
            'usd': d.get('usd'), 'brl': d.get('brl'),
            'usd_24h_change_pct': round(d.get('usd_24h_change') or 0, 3),
            'usd_market_cap': d.get('usd_market_cap'),
            'coingecko_id': gid,
        }
    return out

def selftest():
    checks = []
    def _p(name, ok, detail=''):
        checks.append((name, ok, detail))
    rep = price_report(['eth', 'usdc', 'aero'])
    _p('report ok', rep.get('ok') is True)
    _p('eth price > 0', (rep['prices'].get('eth') or {}).get('usd', 0) > 0)
    _p('usdc ~1.0', 0.9 < (rep['prices'].get('usdc') or {}).get('usd', 0) < 1.1)
    _p('has brl', (rep['prices'].get('eth') or {}).get('brl', 0) > 0)
    _p('has 24h change', 'usd_24h_change_pct' in (rep['prices'].get('eth') or {}))
    bad = price_report(['nope'])
    _p('unknown symbol rejected', bad.get('ok') is False)
    _p('source tagged', rep.get('source') == 'coingecko')
    _p('timestamp present', rep.get('fetched_at', 0) > 1_000_000_000)
    total = len(checks); passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(('PASS' if ok else 'FAIL'), name, detail)
    print('price_oracle selftest: %d/%d PASS' % (passed, total))
    return 0 if passed == total else 1

if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    syms = [a for a in sys.argv[1:] if not a.startswith('--')]
    print(json.dumps(price_report(syms or None), indent=2, ensure_ascii=False))
