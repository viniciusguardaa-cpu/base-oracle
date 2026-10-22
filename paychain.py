#!/usr/bin/env python3
# paychain.py v1.0.0 - Verificacao de pagamento USDC direto on-chain na Base.
#
# Por que existe: o facilitator publico x402 so lista TESTNETS hoje
# (base-sepolia, eip155:84532, solana-devnet...), entao nao existe caminho
# configurado para receber em Base mainnet por esse protocolo. Este modulo
# remove a dependencia do facilitator: o cliente paga USDC direto para a
# carteira e apresenta o HASH da transacao; o servidor verifica o recibo
# on-chain por RPC publico e so entao entrega o dado.
#
# Garantias:
#  - NAO existe chave privada neste processo. Nada e assinado, nada e gasto.
#    A unica operacao e LEITURA de estado publico (eth_getTransactionReceipt).
#  - Verifica de fato: status do recibo, contrato do token USDC, evento
#    Transfer, endereco de destino, valor minimo em atomico e (opcional)
#    endereco do remetente.
#  - Anti-replay: ledger append-only de tx hashes ja aceitos; o mesmo hash
#    nunca paga duas vezes (com fsync, para nao perder registro em crash).
#  - Fail closed: qualquer ambiguidade (recibo ausente, RPC mudo, formato
#    estranho) resulta em NAO liberar o dado.
#
# Uso:
#   python3 paychain.py --tx 0x... --min 10000 --json
#   python3 paychain.py --tx 0x... --from 0x... --ledger ledger.jsonl
#   python3 paychain.py --selftest

import argparse
import json
import os
import re
import sys
import time
import urllib.request

USDC_BASE = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = ('0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f'
                  '55a4df523b3ef')
RPCS = ['https://base-rpc.publicnode.com', 'https://base.llamarpc.com',
        'https://1rpc.io/base', 'https://mainnet.base.org']
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) belkzaz-paychain/1.0'
TX_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def _rpc(url, method, params, opener=None):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': UA})
    op = opener or urllib.request.urlopen
    with op(req, timeout=20) as r:
        d = json.loads(r.read().decode('utf-8', 'replace'))
    if 'error' in d:
        raise RuntimeError(str(d['error'])[:160])
    return d.get('result')


def fetch_receipt(tx_hash, opener=None, rpcs=None):
    """Recibo da transacao pelo primeiro RPC que responder. None = nao achou."""
    erro = ''
    for url in (rpcs or RPCS):
        try:
            return _rpc(url, 'eth_getTransactionReceipt', [tx_hash], opener)
        except Exception as exc:
            erro = str(exc)[:120]
            continue
    raise RuntimeError('nenhum RPC respondeu: ' + erro)


def _addr_from_topic(topic):
    t = topic.lower()
    if t.startswith('0x'):
        t = t[2:]
    if len(t) != 64:
        return ''
    return '0x' + t[24:]


def parse_receipt(receipt, pay_to, min_atomic, from_addr=None):
    """Decide se o recibo contem um pagamento USDC valido para pay_to."""
    if receipt is None:
        return {'ok': False, 'reason': 'transacao nao encontrada (ainda pendente?)'}
    status = str(receipt.get('status', '')).lower()
    if status not in ('0x1', '1', 'true'):
        return {'ok': False, 'reason': 'transacao falhou on-chain (status %s)' % status}

    want_to = pay_to.lower()
    want_from = from_addr.lower() if from_addr else None
    for log in (receipt.get('logs') or []):
        if str(log.get('address', '')).lower() != USDC_BASE:
            continue
        topics = [str(t).lower() for t in (log.get('topics') or [])]
        if len(topics) < 3 or topics[0] != TRANSFER_TOPIC:
            continue
        src = _addr_from_topic(topics[1])
        dst = _addr_from_topic(topics[2])
        if dst != want_to:
            continue
        try:
            value = int(str(log.get('data', '0x0')), 16)
        except ValueError:
            continue
        if value < int(min_atomic):
            return {'ok': False,
                    'reason': 'valor insuficiente (%d < %d atomico)'
                              % (value, int(min_atomic)),
                    'paid_atomic': value}
        if want_from and src != want_from:
            return {'ok': False, 'reason': 'remetente diferente do esperado',
                    'from': src}
        return {'ok': True, 'reason': 'ok', 'from': src, 'to': dst,
                'paid_atomic': value, 'token': USDC_BASE,
                'block': int(str(receipt.get('blockNumber', '0x0')), 16),
                'tx': str(receipt.get('transactionHash', ''))}

    return {'ok': False,
            'reason': 'nenhum Transfer USDC para o destino neste recibo'}


class Ledger:
    """Anti-replay: uma tx hash aceita nunca paga duas vezes."""

    def __init__(self, path):
        self.path = path

    def _used(self):
        used = set()
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                for linha in f:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        used.add(str(json.loads(linha).get('tx', '')).lower())
                    except Exception:
                        continue
        except FileNotFoundError:
            return set()
        return used

    def is_used(self, tx):
        return tx.lower() in self._used()

    def mark(self, tx, meta=None):
        rec = {'tx': str(tx).lower(), 'ts': int(time.time()), 'meta': meta or {}}
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        return rec


def verify(tx_hash, pay_to, min_atomic, from_addr=None, ledger=None,
           opener=None, rpcs=None):
    """Verificacao completa. Fail closed em qualquer duvida."""
    tx = str(tx_hash or '').strip()
    if not TX_RE.match(tx):
        return {'ok': False, 'reason': 'hash de transacao invalido (esperado 0x + 64 hex)'}
    if not ADDR_RE.match(str(pay_to or '')):
        return {'ok': False, 'reason': 'destino (pay_to) invalido'}
    if from_addr and not ADDR_RE.match(str(from_addr)):
        return {'ok': False, 'reason': 'remetente (from) invalido'}
    try:
        min_atomic = int(min_atomic)
    except (TypeError, ValueError):
        return {'ok': False, 'reason': 'valor minimo invalido'}
    if min_atomic <= 0:
        return {'ok': False, 'reason': 'valor minimo precisa ser > 0'}

    if ledger is not None and ledger.is_used(tx):
        return {'ok': False, 'reason': 'replay: esta transacao ja foi usada',
                'replay': True}

    try:
        receipt = fetch_receipt(tx, opener=opener, rpcs=rpcs)
    except Exception as exc:
        return {'ok': False, 'reason': 'falha ao ler a cadeia: ' + str(exc)[:120]}

    res = parse_receipt(receipt, pay_to, min_atomic, from_addr)
    res['tx'] = tx
    if res.get('ok') and ledger is not None:
        ledger.mark(tx, {'paid_atomic': res.get('paid_atomic'),
                         'from': res.get('from')})
        res['ledger'] = 'marked'
    return res


def selftest():
    n = []

    def _p():
        n.append(1)

    pay_to = '0x39EffA95A980a256AFDa156445F0e0f572801389'
    payer = '0x1111111111111111111111111111111111111111'
    txh = '0x' + 'ab' * 32

    def log(dst, val, src=payer, token=USDC_BASE, topic=TRANSFER_TOPIC):
        return {'address': token,
                'topics': [topic,
                           '0x' + '0' * 24 + src[2:].lower(),
                           '0x' + '0' * 24 + dst[2:].lower()],
                'data': hex(val)}

    def receipt(logs, status='0x1', block='0x10'):
        return {'status': status, 'logs': logs, 'blockNumber': block,
                'transactionHash': txh}

    # 1. hash invalido
    assert verify('nao-e-hash', pay_to, 10000)['ok'] is False
    _p()
    # 2. destino invalido
    assert verify(txh, '0x123', 10000)['ok'] is False
    _p()
    # 3. valor minimo invalido (0)
    assert verify(txh, pay_to, 0)['ok'] is False
    _p()
    # 4. tx pendente (recibo None)
    assert parse_receipt(None, pay_to, 10000)['ok'] is False
    _p()
    # 5. tx falhou on-chain
    assert parse_receipt(receipt([log(pay_to, 10000)], status='0x0'),
                         pay_to, 10000)['ok'] is False
    _p()
    # 6. pagamento valido no valor exato
    r6 = parse_receipt(receipt([log(pay_to, 10000)]), pay_to, 10000)
    assert r6['ok'] is True and r6['paid_atomic'] == 10000
    _p()
    # 7. pagamento acima do preco tambem vale
    assert parse_receipt(receipt([log(pay_to, 50000)]), pay_to, 10000)['ok'] is True
    _p()
    # 8. valor insuficiente recusado
    r8 = parse_receipt(receipt([log(pay_to, 9999)]), pay_to, 10000)
    assert r8['ok'] is False and 'insuficiente' in r8['reason']
    _p()
    # 9. transfer para OUTRO destino nao paga
    assert parse_receipt(receipt([log(payer, 100000)]), pay_to, 10000)['ok'] is False
    _p()
    # 10. token que nao e USDC nao paga
    assert parse_receipt(receipt([log(pay_to, 100000, token=payer)]),
                         pay_to, 10000)['ok'] is False
    _p()
    # 11. evento que nao e Transfer nao paga
    assert parse_receipt(receipt([log(pay_to, 100000, topic='0x' + '00' * 32)]),
                         pay_to, 10000)['ok'] is False
    _p()
    # 12. remetente esperado errado recusa
    r12 = parse_receipt(receipt([log(pay_to, 10000)]), pay_to, 10000,
                        from_addr='0x' + '22' * 20)
    assert r12['ok'] is False and 'remetente' in r12['reason']
    _p()
    # 13. remetente esperado certo aceita
    assert parse_receipt(receipt([log(pay_to, 10000)]), pay_to, 10000,
                         from_addr=payer)['ok'] is True
    _p()
    # 14. anti-replay: ledger marca e recusa a repeticao
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, 'ledger.jsonl'))
        assert led.is_used(txh) is False
        led.mark(txh, {'x': 1})
        assert led.is_used(txh) is True
        assert led.is_used(txh.upper().replace('0X', '0x')) is True
    _p()
    # 15. verify() com RPC falso: caminho de sucesso ponta a ponta
    class _Fake:
        def __init__(self, payload):
            self.payload = payload

        def __call__(self, req, timeout=20):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({'result': self.payload}).encode()

    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, 'ledger.jsonl'))
        fake = _Fake(receipt([log(pay_to, 10000)]))
        ok1 = verify(txh, pay_to, 10000, ledger=led, opener=fake, rpcs=['http://fake'])
        assert ok1['ok'] is True and ok1['ledger'] == 'marked'
        ok2 = verify(txh, pay_to, 10000, ledger=led, opener=fake, rpcs=['http://fake'])
        assert ok2['ok'] is False and ok2.get('replay') is True
    _p()

    _src = open(__file__, encoding='utf-8').read()
    _total = sum(1 for L in _src.splitlines() if L.strip() == '_p()')
    print('selftest: %d/%d PASS' % (len(n), _total))
    return 0 if len(n) == _total and _total >= 15 else 1


def main(argv):
    ap = argparse.ArgumentParser(description='Verifica pagamento USDC on-chain na Base')
    ap.add_argument('--tx', help='hash da transacao (0x + 64 hex)')
    ap.add_argument('--to', default='0x39EffA95A980a256AFDa156445F0e0f572801389')
    ap.add_argument('--min', type=int, default=10000, help='minimo em atomico (0.01 USDC = 10000)')
    ap.add_argument('--from', dest='from_addr', default=None)
    ap.add_argument('--ledger', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.tx:
        ap.error('--tx e obrigatorio (ou use --selftest)')

    led = Ledger(a.ledger) if a.ledger else None
    res = verify(a.tx, a.to, a.min, from_addr=a.from_addr, ledger=led)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(('PAGO' if res.get('ok') else 'RECUSADO') + ': ' + res.get('reason', ''))
        if res.get('paid_atomic'):
            print('  valor: %.6f USDC' % (res['paid_atomic'] / 1e6))
    return 0 if res.get('ok') else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
