#!/usr/bin/env python3
# base_oracle.py v1.0.0 - Relatorio pago de carteira na Base (EVM/USDC).
#
# Produto online real: dado on-chain PUBLICO, lido na hora por RPC publico,
# entregue SOMENTE depois de pagamento USDC confirmado on-chain por paychain.py.
# Nao depende de facilitator x402 (que hoje so lista testnets).
#
# Garantias:
#  - Somente LEITURA da cadeia. Nenhuma chave privada neste processo.
#  - Nao entrega dado sem pagamento confirmado (fail closed).
#  - Anti-replay: a mesma tx nunca libera dois relatorios (ledger do paychain).
#
# Uso:
#   python3 base_oracle.py report --address 0x...
#   python3 base_oracle.py serve --port 8794 --price-atomic 50000
#   python3 base_oracle.py --selftest

import argparse
import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import paychain

PAID_ENDPOINTS = frozenset(['/v1/arb-scan', '/v1/funding', '/v1/gas-oracle', '/v1/momentum', '/v1/portfolio', '/v1/token-safety', '/v1/tx-explain', '/v1/wallet-report', '/v1/x402-audit'])

USDC = paychain.USDC_BASE
PAY_TO_DEFAULT = '0x39EffA95A980a256AFDa156445F0e0f572801389'
SELECTOR_BALANCE_OF = '0x70a08231'
ADDR_RE = paychain.ADDR_RE

HEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'oracle_ledger.jsonl')


def _pad_addr(addr):
    a = str(addr or '').strip().lower()
    if not ADDR_RE.match(a):
        raise ValueError('endereco invalido (esperado 0x + 40 hex)')
    return a[2:].rjust(64, '0')


def usdc_balance_of_data(addr):
    return SELECTOR_BALANCE_OF + _pad_addr(addr)


def _hex_int(v, default=0):
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return default


def _chain(method, params, rpcs=None, opener=None):
    erro = ''
    for url in (rpcs or paychain.RPCS):
        try:
            return paychain._rpc(url, method, params, opener)
        except Exception as exc:
            erro = str(exc)[:100]
            continue
    raise RuntimeError('nenhum RPC respondeu (%s)' % erro)


def wallet_report(address, rpcs=None, opener=None):
    """Leitura real da Base para um endereco. Nao escreve, nao assina, nao gasta."""
    addr = str(address or '').strip()
    _pad_addr(addr)  # valida antes de qualquer chamada
    eth_wei = _hex_int(_chain('eth_getBalance', [addr, 'latest'], rpcs, opener))
    nonce = _hex_int(_chain('eth_getTransactionCount', [addr, 'latest'], rpcs, opener))
    code = _chain('eth_getCode', [addr, 'latest'], rpcs, opener) or '0x'
    block = _hex_int(_chain('eth_blockNumber', [], rpcs, opener))
    raw_usdc = _chain('eth_call', [{'to': USDC,
                                    'data': usdc_balance_of_data(addr)},
                                   'latest'], rpcs, opener) or '0x0'
    usdc_atomic = _hex_int(raw_usdc)
    return {
        'address': addr,
        'network': 'eip155:8453',
        'block': block,
        'eth': round(eth_wei / 1e18, 9),
        'usdc': round(usdc_atomic / 1e6, 6),
        'usdc_atomic': usdc_atomic,
        'nonce': nonce,
        'is_contract': len(code) > 2,
        'read_only': True,
        'source': 'public RPC eth_getBalance/eth_getTransactionCount/eth_call',
    }


def validate_price(price_atomic):
    try:
        p = int(price_atomic)
    except (TypeError, ValueError):
        raise ValueError('preco invalido (inteiro em atomico)')
    if p <= 0:
        raise ValueError('preco precisa ser > 0')
    return p


def challenge(pay_to, price_atomic, reason='pagamento nao apresentado'):
    """Resposta 402 explicita: como pagar, sem prometer o que nao existe."""
    return {
        'ok': False,
        'error': 'payment_required',
        'reason': reason,
        'price_atomic': int(price_atomic),
        'price_usdc': round(int(price_atomic) / 1e6, 6),
        'network': 'eip155:8453',
        'token': USDC,
        'payTo': pay_to,
        'how': ('envie USDC na Base para payTo e repita a chamada com o header '
                'X-PAYMENT-TX contendo o hash da transacao'),
        'facilitator': 'none (verificacao direta do recibo on-chain)',
    }


def handle_report(address, tx, ledger, pay_to=PAY_TO_DEFAULT,
                  price_atomic=50000, verify_fn=None, report_fn=None):
    """Nucleo testavel: decide status HTTP + corpo. Sem sockets aqui."""
    try:
        validate_price(price_atomic)
    except ValueError as exc:
        return 400, {'ok': False, 'error': 'bad_request', 'reason': str(exc)}

    if not tx:
        return 402, challenge(pay_to, price_atomic)

    try:
        _pad_addr(address)
    except ValueError as exc:
        return 400, {'ok': False, 'error': 'bad_request', 'reason': str(exc)}

    check = verify_fn or paychain.verify
    res = {}
    try:
        res = check(tx, pay_to, int(price_atomic), ledger=ledger)
    except Exception as exc:
        return 502, {'ok': False, 'error': 'payment_check_failed',
                     'reason': str(exc)[:160]}
    if not res.get('ok'):
        return 402, challenge(pay_to, price_atomic,
                              reason=str(res.get('reason', 'pagamento invalido')))

    gen = report_fn or wallet_report
    try:
        rep = gen(address)
    except Exception as exc:
        # Pagamento ja foi gasto pelo cliente: nunca fingir sucesso.
        return 502, {'ok': False, 'error': 'chain_read_failed',
                     'reason': str(exc)[:160],
                     'note': 'pagamento NAO consumido: reenvie a mesma tx'}

    return 200, {'ok': True,
                 'payment': {'tx': res.get('tx'), 'paid_atomic': res.get('paid_atomic')},
                 'report': rep}


def selftest():
    n = []

    def _p():
        n.append(1)

    pay_to = PAY_TO_DEFAULT
    txh = '0x' + 'cd' * 32

    # 1. padding de endereco valido
    assert _pad_addr(pay_to) == pay_to[2:].lower().rjust(64, '0')
    _p()

    # 2. endereco invalido recusado
    try:
        _pad_addr('0x123')
        raise AssertionError('deveria recusar endereco curto')
    except ValueError:
        pass
    _p()

    # 3. data do balanceOf = selector + endereco alinhado a 32 bytes
    d = usdc_balance_of_data(pay_to)
    assert d.startswith(SELECTOR_BALANCE_OF) and len(d) == 10 + 64
    _p()

    # 4. preco invalido recusado
    for bad in (0, -1, 'x'):
        try:
            validate_price(bad)
            raise AssertionError('deveria recusar preco %r' % (bad,))
        except ValueError:
            pass
    _p()

    # 5. wallet_report com RPC falso: valores corretos
    class _Fake:
        def __init__(self, table):
            self.table = table

        def __call__(self, req, timeout=20):
            body = json.loads(req.data.decode())
            self.last = body
            val = self.table.get(body['method'])
            self.resp = {'jsonrpc': '2.0', 'id': 1, 'result': val}
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.resp).encode()

    fake = _Fake({'eth_getBalance': hex(2 * 10 ** 18),
                  'eth_getTransactionCount': hex(7),
                  'eth_getCode': '0x',
                  'eth_blockNumber': hex(52954000),
                  'eth_call': hex(6455378)})
    rep = wallet_report(pay_to, rpcs=['http://fake'], opener=fake)
    assert rep['eth'] == 2.0 and rep['usdc'] == 6.455378
    assert rep['nonce'] == 7 and rep['is_contract'] is False
    assert rep['block'] == 52954000 and rep['network'] == 'eip155:8453'
    _p()

    # 6. relatorio sempre marcado read_only
    assert rep['read_only'] is True
    _p()

    # 7. eth_call enviado ao contrato USDC correto
    assert fake.last['params'][0]['to'].lower() == USDC
    _p()

    # 8. contrato detectado quando ha bytecode
    fake2 = _Fake({'eth_getBalance': '0x0', 'eth_getTransactionCount': '0x0',
                   'eth_getCode': '0x6080', 'eth_blockNumber': '0x1',
                   'eth_call': '0x0'})
    assert wallet_report(pay_to, rpcs=['http://f'], opener=fake2)['is_contract'] is True
    _p()

    # 9. sem pagamento -> 402 com preco, destino e instrucao
    st, body = handle_report(pay_to, None, None, pay_to=pay_to, price_atomic=50000)
    assert st == 402 and body['error'] == 'payment_required'
    assert body['price_atomic'] == 50000 and body['payTo'] == pay_to
    assert body['network'] == 'eip155:8453' and 'X-PAYMENT-TX' in body['how']
    _p()

    # 10. endereco invalido -> 400 antes de qualquer cobranca
    st, body = handle_report('0xzz', txh, None)
    assert st == 400 and body['error'] == 'bad_request'
    _p()

    # 11. pagamento recusado -> 402 com motivo real
    st, body = handle_report(pay_to, txh, None,
                             verify_fn=lambda *a, **k: {'ok': False,
                                                        'reason': 'replay: ja usado'})
    assert st == 402 and 'replay' in body['reason']
    _p()

    # 12. pagamento valido -> 200 e relatorio entregue
    st, body = handle_report(pay_to, txh, None,
                             verify_fn=lambda *a, **k: {'ok': True, 'tx': txh,
                                                        'paid_atomic': 50000},
                             report_fn=lambda a: {'address': a, 'read_only': True})
    assert st == 200 and body['ok'] is True and body['report']['address'] == pay_to
    _p()

    # 13. falha de leitura nao finge sucesso nem queima o pagamento
    def _boom(_a):
        raise RuntimeError('rpc mudo')

    st, body = handle_report(pay_to, txh, None,
                             verify_fn=lambda *a, **k: {'ok': True, 'tx': txh},
                             report_fn=_boom)
    assert st == 502 and body['error'] == 'chain_read_failed'
    assert 'NAO consumido' in body['note']
    _p()

    _src = open(__file__, encoding='utf-8').read()
    _total = sum(1 for L in _src.splitlines() if L.strip() == '_p()')
    print('selftest: %d/%d PASS' % (len(n), _total))
    return 0 if len(n) == _total and _total >= 13 else 1


LANDING_HTML = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8><title>base-oracle</title><style>body{font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px}code,pre{background:#f4f4f4;padding:2px 6px;border-radius:4px}pre{padding:12px}</style></head><body><h1>base-oracle</h1><p>Agent <b>trading-intelligence API</b> on Base mainnet: arbitrage scans, momentum signals, perp funding, portfolio &amp; token-safety reports — computed live, paid per call in USDC. No account, no API key. <i>Not financial advice.</i></p><p><b>Price: 0.05 USDC per report</b></p><p>Endpoints: <code>/v1/arb-scan</code> <code>/v1/momentum</code> <code>/v1/funding</code> <code>/v1/portfolio</code> <code>/v1/token-safety</code> <code>/v1/wallet-report</code> <code>/v1/x402-audit</code></p><h2>How to buy</h2><ol><li><code>GET /v1/wallet-report?address=0x...</code> returns HTTP 402 with payment details.</li><li>Send at least 0.05 USDC on Base to <code>__PAYTO__</code>.</li><li>Repeat with header <code>X-PAYMENT-TX: 0x&lt;txhash&gt;</code> to receive the report.</li></ol><p>Payment verified directly on-chain. Each tx hash pays once. Read-only service.</p></body></html>"""


X402_VERSION = 1
RESOURCE_PATH = '/v1/wallet-report'
USDC_ASSET = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'

def public_base():
    try:
        return open('PUBLIC_URL.txt').read().strip().rstrip('/') or 'http://127.0.0.1'
    except Exception:
        return 'http://127.0.0.1'

def x402_accepts(pay_to, price_atomic, resource_url=None, description='Paid Base chain data'):
    return {
        'x402Version': 2,
        'resource': {
            'url': resource_url or (public_base() + RESOURCE_PATH),
            'description': description,
            'mimeType': 'application/json',
        },
        'accepts': [{
            'scheme': 'exact',
            'network': 'eip155:8453',
            'amount': str(price_atomic),
            'asset': USDC_ASSET,
            'payTo': pay_to,
            'maxTimeoutSeconds': 300,
            'extra': {'name': 'USD Coin', 'version': '2'},
            'outputSchema': {'type': 'object'},
        }],
    }


TOKEN_SAFETY_PATH = '/v1/token-safety'
DEXSCREENER = 'https://api.dexscreener.com/latest/dex/tokens/'

def fetch_token_safety(address):
    import urllib.request, urllib.error
    report = {'address': address, 'chain': 'base', 'ok': True, 'checks': {}}
    # on-chain: is it a contract? bytecode size
    try:
        code = rpc_call('eth_getCode', [address, 'latest'])
        report['checks']['is_contract'] = bool(code and code != '0x')
        report['checks']['bytecode_bytes'] = (len(code) - 2) // 2
    except Exception as e:
        report['checks']['is_contract'] = None
        report['checks']['rpc_error'] = str(e)[:120]
    # dexscreener market data
    try:
        req = urllib.request.Request(DEXSCREENER + address,
                                     headers={'User-Agent': 'base-oracle/1.1'})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        pairs = data.get('pairs') or []
        base_pairs = [p for p in pairs if p.get('chainId') == 'base']
        report['pairs_total'] = len(pairs)
        report['pairs_base'] = len(base_pairs)
        if base_pairs:
            p = max(base_pairs, key=lambda x: float((x.get('liquidity') or {}).get('usd') or 0))
            liq = (p.get('liquidity') or {}).get('usd')
            vol = (p.get('volume') or {}).get('h24')
            txns = p.get('txns') or {}
            buys = (txns.get('h24') or {}).get('buys') or 0
            sells = (txns.get('h24') or {}).get('sells') or 0
            report['checks'].update({
                'dex': p.get('dexId'),
                'pair': p.get('pairAddress'),
                'liquidity_usd': liq,
                'volume_h24_usd': vol,
                'price_usd': p.get('priceUsd'),
                'buys_h24': buys,
                'sells_h24': sells,
                'sell_ratio_h24': round(sells / (buys + sells), 3) if (buys + sells) else None,
                'created_at': p.get('pairCreatedAt'),
            })
            flags = []
            if liq is not None and float(liq) < 5000:
                flags.append('low_liquidity')
            if buys + sells > 0 and sells / (buys + sells) > 0.9:
                flags.append('sell_dominated')
            report['risk_flags'] = flags
            report['risk'] = 'high' if flags else ('medium' if (liq or 0) < 50000 else 'lower')
        else:
            report['risk_flags'] = ['no_base_dex_pair']
            report['risk'] = 'unknown'
    except Exception as e:
        report['ok'] = False
        report['error'] = 'dexscreener_fetch_failed: ' + str(e)[:120]
    return report


DISCLAIMER = 'not financial advice'
ARB_SCAN_PATH = '/v1/arb-scan'
MOMENTUM_PATH = '/v1/momentum'
FUNDING_PATH = '/v1/funding'
PORTFOLIO_PATH = '/v1/portfolio'
X402_AUDIT_PATH = '/v1/x402-audit'
GAS_ORACLE_PATH = '/v1/gas-oracle'
SAMPLE_PATH = '/v1/sample'
TX_EXPLAIN_PATH = '/v1/tx-explain'

def _http_json(url, opener=None, timeout=20, method='GET', payload=None, headers=None):
    import urllib.request, json as _j
    data = None
    if payload is not None:
        data = _j.dumps(payload).encode()
    h = {'User-Agent': 'base-oracle/1.2'}
    if data: h['Content-Type'] = 'application/json'
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    op = opener or urllib.request.urlopen
    r = op(req, timeout=timeout)
    return _j.loads(r.read())

def _dexscreener_token(address):
    return _http_json('https://api.dexscreener.com/latest/dex/tokens/' + address)

def _base_pairs(address):
    d = _dexscreener_token(address)
    return [p for p in (d.get('pairs') or []) if p.get('chainId') == 'base']

def fetch_arb_scan(address):
    """Best buy/sell pair for a Base token across DEXes + spread %."""
    rep = {'address': address, 'ok': False, 'disclaimer': DISCLAIMER}
    try:
        pairs = _base_pairs(address)
    except Exception as e:
        rep['error'] = 'dexscreener_unreachable: %s' % e
        return rep
    quotes = {}
    for p in pairs:
        try:
            price = float(p.get('priceUsd') or 0)
        except Exception:
            continue
        if price <= 0:
            continue
        q = (p.get('quoteToken') or {}).get('symbol', '?')
        liq = ((p.get('liquidity') or {}).get('usd')) or 0
        quotes.setdefault(q, []).append({'dex': p.get('dexId'), 'price': price,
            'liquidity_usd': liq, 'pair': p.get('pairAddress')})
    best = None
    for q, lst in quotes.items():
        lst2 = sorted(lst, key=lambda x: x['price'])
        lo, hi = lst2[0], lst2[-1]
        spread = (hi['price'] - lo['price']) / lo['price'] * 100 if lo['price'] else 0
        cand = {'quote': q, 'buy': lo, 'sell': hi, 'spread_pct': round(spread, 3)}
        if best is None or cand['spread_pct'] > best['spread_pct']:
            best = cand
    rep['ok'] = True
    rep['opportunities'] = best
    rep['quotes_seen'] = sorted(quotes.keys())
    rep['pairs_base'] = len(pairs)
    if best and best['spread_pct'] > 0:
        rep['signal'] = 'spread_detected'
    else:
        rep['signal'] = 'flat'
    return rep

def fetch_momentum(address):
    """Price windows + liquidity + volume -> LONG/SHORT/FLAT signal."""
    rep = {'address': address, 'ok': False, 'disclaimer': DISCLAIMER}
    try:
        pairs = _base_pairs(address)
    except Exception as e:
        rep['error'] = 'dexscreener_unreachable: %s' % e
        return rep
    if not pairs:
        rep['error'] = 'no_base_dex_pair'
        return rep
    p = max(pairs, key=lambda x: ((x.get('liquidity') or {}).get('usd') or 0))
    ch = p.get('priceChange') or {}
    vol = p.get('volume') or {}
    liq = (p.get('liquidity') or {}).get('usd') or 0
    h1 = ch.get('h1'); h24 = ch.get('h24'); h6 = ch.get('h6')
    score = 0.0
    parts = []
    for w, v in ((0.4, h1), (0.3, h6), (0.3, h24)):
        if v is None: continue
        v = float(v)
        score += w * max(-10.0, min(10.0, v))
        parts.append(v)
    v24 = float(vol.get('h24') or 0)
    conf = 'low'
    if liq > 500000 and v24 > 100000: conf = 'high'
    elif liq > 50000 and v24 > 10000: conf = 'medium'
    if score > 1.0: sig = 'LONG'
    elif score < -1.0: sig = 'SHORT'
    else: sig = 'FLAT'
    rep.update({'ok': True, 'signal': sig, 'score': round(score, 3),
        'confidence': conf,
        'windows': {'h1': h1, 'h6': h6, 'h24': h24},
        'liquidity_usd': liq, 'volume_h24': v24,
        'price_usd': p.get('priceUsd'), 'dex': p.get('dexId'),
        'pair': p.get('pairAddress')})
    return rep

def fetch_funding(symbol=None):
    """Hyperliquid perp funding rates (public, no auth)."""
    rep = {'ok': False, 'source': 'hyperliquid', 'disclaimer': DISCLAIMER}
    try:
        data = _http_json('https://api.hyperliquid.xyz/info', method='POST',
                          payload={'type': 'metaAndAssetCtxs'})
    except Exception as e:
        rep['error'] = 'hyperliquid_unreachable: %s' % e
        return rep
    try:
        universe = data[0]['universe']; ctxs = data[1]
    except Exception:
        rep['error'] = 'unexpected_hyperliquid_schema'
        return rep
    rows = []
    for u, c in zip(universe, ctxs):
        name = u.get('name')
        if symbol and name != symbol.upper():
            continue
        fr = c.get('funding')
        rows.append({'symbol': name, 'funding': fr,
            'funding_apy_pct': round(float(fr or 0) * 3 * 365 * 100, 2) if fr else None,
            'open_interest': c.get('openInterest'), 'mark_px': c.get('markPx')})
    rep['ok'] = True
    rep['count'] = len(rows)
    rep['markets'] = rows[:50]
    return rep

def fetch_portfolio(address):
    """Base wallet composition: ETH + USDC + erc20 transfers seen (limited)."""
    rep = {'address': address, 'ok': False, 'disclaimer': DISCLAIMER,
           'note': 'ETH/USDC native read + top tokens via dexscreener price overlay'}
    try:
        w = wallet_report(address)
    except Exception as e:
        rep['error'] = 'wallet_read_failed: %s' % str(e)[:120]
        return rep
    if w.get('error'):
        rep['error'] = w['error']
        return rep
    holdings = [{'asset': 'ETH', 'balance': w.get('eth'),
                 'usd': None},
                {'asset': 'USDC', 'balance': w.get('usdc'),
                 'usd': w.get('usdc')}]
    rep.update({'ok': True, 'block': w.get('block'),
        'is_contract': w.get('is_contract'), 'nonce': w.get('nonce'),
        'holdings': holdings})
    try:
        eth_px = _base_pairs('0x4200000000000000000000000000000000000006')
        if eth_px:
            px = float(max(eth_px, key=lambda x: ((x.get('liquidity') or {}).get('usd') or 0)).get('priceUsd') or 0)
            holdings[0]['usd'] = round(float(w.get('eth') or 0) * px, 2)
            rep['eth_price_usd'] = px
    except Exception:
        pass
    rep['total_usd_known'] = round(sum(h['usd'] or 0 for h in holdings), 2)
    return rep

def _audit_target_ok(url):
    u = urlparse(url if '://' in url else 'https://' + url)
    if u.scheme not in ('http', 'https'):
        return None
    host = (u.hostname or '').lower()
    if not host or host == 'localhost' or host.endswith(('.local', '.localhost', '.internal')):
        return None
    parts = host.split('.')
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        o = [int(p) for p in parts]
        if o[0] in (0, 10, 127) or (o[0] == 172 and 16 <= o[1] <= 31) or (o[0] == 192 and o[1] == 168) or o[0] >= 224:
            return None
    return u.scheme + '://' + u.netloc


def fetch_x402_audit(url):
    base = _audit_target_ok((url or '').strip())
    if not base:
        return {'ok': False, 'error': 'invalid_target',
                'detail': 'target must be a public http(s) base URL'}
    try:
        import x402_auditor
        rep = x402_auditor.audit(base, [])
    except Exception as exc:
        return {'ok': False, 'error': 'audit_failed', 'detail': repr(exc)[:200]}
    rep['audited_by'] = 'base-oracle'
    rep['ok'] = True
    return rep


def fetch_tx_explain(txh):
    import tx_explain
    return tx_explain.explain_tx(txh)

def fetch_sample():
    import gas_oracle
    blk = int(gas_oracle.rpc('eth_blockNumber', []), 16)
    gp = int(gas_oracle.rpc('eth_gasPrice', []), 16)
    return {'schema': 'base-oracle/sample/1', 'free': True,
            'latest_block': blk,
            'gas_price_gwei': round(gp / 1e9, 4),
            'note': 'free sample; paid endpoints return full reports for 0.05 USDC'}

def fetch_gas_oracle():
    try:
        import gas_oracle
        rep = gas_oracle.report()
    except Exception as exc:
        return {'ok': False, 'error': 'gas_failed', 'detail': repr(exc)[:200]}
    rep['ok'] = True
    return rep


def openapi_spec(pay_to, price_atomic):
    base = public_base()
    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'base-oracle trading-intel API',
            'version': '1.7.0',
            'description': 'Agent trading-intelligence API on Base mainnet: computed DEX/on-chain signals, paid per request in USDC. Read-only. Not financial advice.',
        },
        'servers': [{'url': base}],
        'paths': {
            '/': {'get': {'summary': 'Landing page', 'security': [],
                          'responses': {'200': {'description': 'HTML offer page'}}}},
            '/health': {'get': {'summary': 'Service status', 'security': [],
                                'responses': {'200': {'description': 'Status JSON'}}}},
            '/.well-known/x402': {'get': {'summary': 'x402 discovery', 'security': [],
                                          'responses': {'200': {'description': 'x402 resource list'}}}},
            '/openapi.json': {'get': {'summary': 'This spec', 'security': [],
                                      'responses': {'200': {'description': 'OpenAPI JSON'}}}},
            TOKEN_SAFETY_PATH: {
                'get': {
                    'summary': 'Paid token safety report',
                    'description': 'Computed risk report for a Base token: contract check, DEX liquidity, volume, buy/sell ratio, risk flags. Requires payment.',
                    'parameters': [{
                        'name': 'address', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{40}$'},
                    }],
                    'x-payment-info': {
                        'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to,
                        'network': 'eip155:8453',
                        'asset': USDC_ASSET,
                    },
                    'responses': {
                        '200': {'description': 'Token safety report JSON'},
                        '402': {'description': 'Payment required - canonical x402 challenge'},
                    },
                },
            },
            ARB_SCAN_PATH: {
                'get': {
                    'summary': 'Paid DEX arbitrage scan',
                    'description': 'Best buy/sell pair for a Base token across DEXes with spread %. Requires payment.',
                    'parameters': [{'name': 'address', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{40}$'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Arb scan JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            MOMENTUM_PATH: {
                'get': {
                    'summary': 'Paid token momentum signal',
                    'description': 'Price windows (1h/6h/24h) + liquidity + volume -> LONG/SHORT/FLAT score with confidence. Requires payment.',
                    'parameters': [{'name': 'address', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{40}$'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Momentum signal JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            FUNDING_PATH: {
                'get': {
                    'summary': 'Paid perp funding rates',
                    'description': 'Hyperliquid funding rates (optionally filtered by symbol). Requires payment.',
                    'parameters': [{'name': 'symbol', 'in': 'query', 'required': False,
                        'schema': {'type': 'string'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Funding rates JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            PORTFOLIO_PATH: {
                'get': {
                    'summary': 'Paid Base portfolio report',
                    'description': 'Wallet composition on Base: ETH + USDC balances with USD overlay. Requires payment.',
                    'parameters': [{'name': 'address', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{40}$'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Portfolio JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            SAMPLE_PATH: {
                'get': {'summary': 'FREE sample: latest Base block + gas price (no payment)', 'parameters': []}
            },
            TX_EXPLAIN_PATH: {
                'get': {
                    'summary': 'Paid Base transaction explainer',
                    'description': 'Explain a Base tx hash: status, value, fee, ERC-20 transfers. Requires payment.',
                    'parameters': [{'name': 'tx', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{64}$'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Tx explanation JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            GAS_ORACLE_PATH: {
                'get': {
                    'summary': 'Paid Base gas oracle',
                    'description': 'Live Base mainnet gas report: eth_gasPrice, 20-block base-fee trend, 25th-pct priority fee, suggested maxFee/maxPriorityFee. Requires payment.',
                    'parameters': [],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'Gas oracle JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            X402_AUDIT_PATH: {
                'get': {
                    'summary': 'Paid x402 seller audit',
                    'description': 'Audits a public x402-paid service: discovery doc, OpenAPI, HTTP 402 challenges, scheme/network/payTo/amount checks. Read-only probes only. Requires payment.',
                    'parameters': [{'name': 'url', 'in': 'query', 'required': True,
                        'schema': {'type': 'string'}}],
                    'x-payment-info': {'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to, 'network': 'eip155:8453', 'asset': USDC_ASSET},
                    'responses': {'200': {'description': 'x402 audit JSON'},
                                  '402': {'description': 'Payment required'}},
                },
            },
            RESOURCE_PATH: {
                'get': {
                    'summary': 'Paid Base wallet report',
                    'description': 'Returns ETH balance, USDC balance, nonce, contract flag, latest block for a Base address. Requires payment.',
                    'parameters': [{
                        'name': 'address', 'in': 'query', 'required': True,
                        'schema': {'type': 'string', 'pattern': '^0x[0-9a-fA-F]{40}$'},
                    }],
                    'x-payment-info': {
                        'protocols': ['x402'],
                        'price': {'mode': 'fixed', 'currency': 'USD', 'amount': '0.05'}, 'x402Version': 2,
                        'payTo': pay_to,
                        'network': 'eip155:8453',
                        'asset': USDC_ASSET,
                    },
                    'responses': {
                        '200': {'description': 'Wallet report JSON'},
                        '402': {'description': 'Payment required - canonical x402 challenge'},
                    },
                },
            },
        },
    }

def _make_handler(pay_to, price_atomic, ledger_path):
    class H(BaseHTTPRequestHandler):
        server_version = 'base-oracle/1.0'

        def log_message(self, fmt, *a):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % a))

        def _send(self, code, obj):
            if code == 402 and isinstance(obj, dict):
                obj = dict(obj)
                obj.update(x402_accepts(pay_to, price_atomic, resource_url=public_base()+self.path.split("?")[0]))
            raw = json.dumps(obj, ensure_ascii=False, indent=2).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            if code == 402:
                self.send_header('payment-required',
                                 base64.b64encode(raw).decode('ascii'))
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, code, html):
            raw = html.encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _paid(self, addr, txh, fn):
            led = paychain.Ledger(ledger_path)
            st, body = handle_report(addr, txh, led, pay_to=pay_to,
                                     price_atomic=price_atomic, report_fn=fn)
            return self._send(st, body)

        def _body_params(self):
            try:
                n = int(self.headers.get('Content-Length', '0') or '0')
            except ValueError:
                n = 0
            if n <= 0 or n > 65536:
                return {}
            raw = self.rfile.read(n)
            try:
                data = json.loads(raw.decode('utf-8', 'replace'))
            except Exception:
                return {}
            if not isinstance(data, dict):
                return {}
            out = {}
            for k, v in data.items():
                if isinstance(v, (str, int, float)):
                    out[str(k)] = [str(v)]
            return out

        def _route(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if self.command in ('POST', 'PUT', 'PATCH'):
                for k, v in self._body_params().items():
                    q.setdefault(k, v)
            txh = (self.headers.get('X-PAYMENT-TX', '').strip()
                   or self.headers.get('X-Payment-Tx', '').strip() or None)
            if u.path == '/.well-known/x402':
                res = [public_base() + p for p in
                       (SAMPLE_PATH, TX_EXPLAIN_PATH, RESOURCE_PATH, TOKEN_SAFETY_PATH, ARB_SCAN_PATH,
                        MOMENTUM_PATH, FUNDING_PATH, PORTFOLIO_PATH,
                        X402_AUDIT_PATH, GAS_ORACLE_PATH)]
                return self._send(200, {'version': 1, 'resources': res})
            if u.path == '/openapi.json':
                return self._send(200, openapi_spec(pay_to, price_atomic))
            if u.path in ('/', '/index.html'):
                return self._send_html(200, LANDING_HTML.replace('__PAYTO__', pay_to))
            if u.path == SAMPLE_PATH:
                return self._send(200, fetch_sample())
            if u.path == '/health':
                return self._send(200, {'status': 'ok', 'service': 'base-oracle',
                                        'read_only': True, 'payTo': pay_to,
                                        'price_atomic': price_atomic,
                                        'network': 'eip155:8453',
                                        'facilitator_configured': False})
            if u.path == '/v1/wallet-report':
                addr = (q.get('address') or [''])[0]
                return self._paid(addr, txh, wallet_report)
            if u.path == TOKEN_SAFETY_PATH:
                addr = (q.get('address') or [''])[0]
                return self._paid(addr, txh, fetch_token_safety)
            if u.path == ARB_SCAN_PATH:
                addr = (q.get('address') or [''])[0]
                return self._paid(addr, txh, fetch_arb_scan)
            if u.path == MOMENTUM_PATH:
                addr = (q.get('address') or [''])[0]
                return self._paid(addr, txh, fetch_momentum)
            if u.path == FUNDING_PATH:
                sym = (q.get('symbol') or [None])[0]
                return self._paid('0x0000000000000000000000000000000000000000',
                                  txh, lambda _a: fetch_funding(sym))
            if u.path == PORTFOLIO_PATH:
                addr = (q.get('address') or [''])[0]
                return self._paid(addr, txh, fetch_portfolio)
            if u.path == X402_AUDIT_PATH:
                tgt = (q.get('url') or [''])[0]
                return self._paid('0x0000000000000000000000000000000000000000',
                                  txh, lambda _a: fetch_x402_audit(tgt))
            if u.path == TX_EXPLAIN_PATH:
                return self._paid('0x0000000000000000000000000000000000000000', '', lambda _a=None: fetch_tx_explain((q.get('tx') or [''])[0]))
            if u.path == GAS_ORACLE_PATH:
                return self._paid('0x0000000000000000000000000000000000000000',
                                  txh, lambda _a: fetch_gas_oracle())
            return self._send(404, {'ok': False, 'error': 'not_found'})

        def do_GET(self):
            return self._route()

        def do_POST(self):
            return self._route()

        def do_PUT(self):
            return self._route()

        def do_PATCH(self):
            return self._route()

        def do_DELETE(self):
            return self._route()

        def do_OPTIONS(self):
            u = urlparse(self.path)
            if u.path in PAID_ENDPOINTS and not self.headers.get('X-PAYMENT-TX'):
                ch = x402_accepts(pay_to, price_atomic,
                                  resource_url=public_base() + u.path)
                hdr = base64.b64encode(
                    json.dumps(ch, ensure_ascii=False).encode()).decode('ascii')
                self.send_response(402)
                self.send_header('payment-required', hdr)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.send_header('Allow', 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD')
            self.end_headers()

        def do_HEAD(self):
            u = urlparse(self.path)
            if u.path in PAID_ENDPOINTS and not self.headers.get('X-PAYMENT-TX'):
                ch = x402_accepts(pay_to, price_atomic,
                                  resource_url=public_base() + u.path)
                hdr = base64.b64encode(
                    json.dumps(ch, ensure_ascii=False).encode()).decode('ascii')
                self.send_response(402)
                self.send_header('payment-required', hdr)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', '0')
            self.end_headers()

    return H


def main(argv):
    ap = argparse.ArgumentParser(description='Relatorio pago de carteira Base')
    ap.add_argument('cmd', nargs='?', default='report',
                    choices=['report', 'serve'])
    ap.add_argument('--address', default=PAY_TO_DEFAULT)
    ap.add_argument('--port', type=int, default=8794)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--pay-to', default=PAY_TO_DEFAULT)
    ap.add_argument('--price-atomic', type=int, default=50000)
    ap.add_argument('--ledger', default=HEX_PATH)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    try:
        validate_price(a.price_atomic)
        _pad_addr(a.pay_to)
    except ValueError as exc:
        print('erro: %s' % exc, file=sys.stderr)
        return 1

    if a.cmd == 'report':
        try:
            rep = wallet_report(a.address)
        except Exception as exc:
            print('erro ao ler a cadeia: %s' % exc, file=sys.stderr)
            return 2
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0

    srv = ThreadingHTTPServer((a.host, a.port),
                              _make_handler(a.pay_to, a.price_atomic, a.ledger))
    print('base-oracle em http://%s:%d (preco %.6f USDC)'
          % (a.host, a.port, a.price_atomic / 1e6))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
