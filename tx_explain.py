#!/usr/bin/env python3
"""tx_explain.py — explain a Base transaction hash via public RPC reads.

Read-only: eth_getTransactionByHash + eth_getTransactionReceipt + block lookup.
No keys, no signing, no writes except the explicit --out file if requested.
"""
import json
import sys
import urllib.request

RPCS = [
    'https://mainnet.base.org',
    'https://base.llamarpc.com',
    'https://base-rpc.publicnode.com',
    'https://1rpc.io/base',
]
USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


def rpc(method, params):
    last = None
    for url in RPCS:
        try:
            body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                               'params': params}).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={'Content-Type': 'application/json',
                         'User-Agent': 'base-oracle-tx-explain/1.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            if 'error' in d:
                last = d['error']
                continue
            return d.get('result')
        except Exception as e:
            last = str(e)
    raise RuntimeError('rpc %s failed: %s' % (method, last))


def _addr(topic):
    return '0x' + topic[-40:] if isinstance(topic, str) and len(topic) >= 40 else topic


def _hexint(v):
    try:
        return int(v, 16) if isinstance(v, str) else 0
    except Exception:
        return 0


def explain_tx(tx_hash):
    h = (tx_hash or '').strip().lower()
    if not (h.startswith('0x') and len(h) == 66):
        raise ValueError('expected 0x + 64 hex chars')
    int(h[2:], 16)

    tx = rpc('eth_getTransactionByHash', [h])
    if tx is None:
        return {'schema': 'base-oracle/tx-explain/1', 'tx_hash': h,
                'found': False,
                'summary': 'transaction not found on Base mainnet'}
    rcpt = rpc('eth_getTransactionReceipt', [h]) or {}

    block_num = _hexint(tx.get('blockNumber'))
    ts = None
    if block_num:
        blk = rpc('eth_getBlockByNumber', [hex(block_num), False]) or {}
        ts = _hexint(blk.get('timestamp'))

    value_wei = _hexint(tx.get('value'))
    gas_used = _hexint(rcpt.get('gasUsed'))
    eff_gas = _hexint(rcpt.get('effectiveGasPrice'))
    fee_eth = gas_used * eff_gas / 1e18
    status = _hexint(rcpt.get('status'))

    transfers = []
    contract_calls = 0
    for log in rcpt.get('logs') or []:
        topics = log.get('topics') or []
        if topics and topics[0].lower() == TRANSFER_TOPIC and len(topics) >= 3:
            amt = _hexint(log.get('data'))
            token = (log.get('address') or '').lower()
            transfers.append({
                'token': token,
                'token_name': 'USDC' if token == USDC else None,
                'from': _addr(topics[1]),
                'to': _addr(topics[2]),
                'amount_raw': amt,
                'amount_usdc': round(amt / 1e6, 6) if token == USDC else None,
            })
        else:
            contract_calls += 1

    kind = 'contract_call'
    if not tx.get('input') or tx.get('input') in ('0x', '0x0'):
        kind = 'eth_transfer'
    elif rcpt.get('contractAddress'):
        kind = 'contract_deploy'
    elif any(t.get('token_name') == 'USDC' for t in transfers):
        kind = 'token_transfer'

    return {
        'schema': 'base-oracle/tx-explain/1',
        'tx_hash': h,
        'found': True,
        'network': 'eip155:8453',
        'status': {0: 'reverted', 1: 'success'}.get(status, 'unknown'),
        'kind': kind,
        'block': block_num,
        'timestamp': ts,
        'from': tx.get('from'),
        'to': tx.get('to'),
        'value_eth': round(value_wei / 1e18, 10),
        'gas_used': gas_used,
        'fee_eth': round(fee_eth, 10),
        'nonce': _hexint(tx.get('nonce')),
        'logs': len(rcpt.get('logs') or []),
        'erc20_transfers': transfers[:20],
        'other_log_count': contract_calls,
        'summary': '%s %s (block %s, %d token transfer(s))' % (
            kind, {0: 'reverted', 1: 'succeeded'}.get(status, 'status unknown'),
            block_num, len(transfers)),
    }


def selftest():
    checks = 0

    def ok(cond, msg):
        nonlocal checks
        checks += 1
        if not cond:
            raise AssertionError(msg)

    try:
        explain_tx('0x' + '00' * 32)
        ok(False, 'nonexistent tx should not crash but returns found False or error')
    except ValueError:
        ok(False, 'well-formed unknown hash must not be a ValueError')
    except Exception as e:
        ok(True, 'unknown hash handled or rpc error: %s' % str(e)[:40])

    try:
        explain_tx('0x1234')
        ok(False, 'short hash accepted')
    except ValueError:
        ok(True, 'short hash rejected')

    src = open(__file__).read()
    ok(('eth_send' + 'Transaction') not in src and ('eth_send' + 'RawTransaction') not in src,
       'module must be read-only')
    ok(TRANSFER_TOPIC.startswith('0xddf252ad'), 'transfer topic')
    print('tx_explain selftest: %d/%d PASS' % (checks, checks))


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
        sys.exit(0)
    if len(sys.argv) < 2:
        print('usage: tx_explain.py <0xhash> | --selftest')
        sys.exit(2)
    try:
        print(json.dumps(explain_tx(sys.argv[1]), indent=2))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}))
        sys.exit(1)
