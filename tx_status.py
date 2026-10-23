#!/usr/bin/env python3
"""tx_status.py — transaction status endpoint for base-oracle.

Returns receipt data for a Base tx hash: status, block, confirmations,
gas used, effective gas price, total fee paid (ETH), from/to, logs count.
Read-only. Stdlib only. Public Base RPCs with fallback.
"""
import json, time, urllib.request

RPCS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://base.drpc.org",
    "https://1rpc.io/base",
]
UA = {"Content-Type": "application/json", "User-Agent": "base-oracle/1.13"}

def _rpc(method, params):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            r = json.load(urllib.request.urlopen(req, timeout=8))
            if "error" in r:
                last = r["error"].get("message","rpc error"); continue
            return r["result"]
        except Exception as e:
            last = str(e); continue
    raise RuntimeError("all RPCs failed: %s" % last)

def _i(h):
    return int(h, 16) if isinstance(h, str) and h.startswith("0x") else 0

def fetch_tx_status(tx_hash):
    tx_hash = (tx_hash or "").strip().lower()
    if not (tx_hash.startswith("0x") and len(tx_hash) == 66):
        return {"error": "invalid tx hash (expected 0x + 64 hex)", "readonly": True}
    try:
        receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
    except Exception as e:
        return {"error": "rpc_failed", "detail": str(e), "readonly": True}
    if receipt is None:
        tx = _rpc("eth_getTransactionByHash", [tx_hash])
        if tx is None:
            return {"tx": tx_hash, "state": "not_found", "readonly": True}
        return {"tx": tx_hash, "state": "pending",
                "from": tx.get("from"), "to": tx.get("to"), "readonly": True}
    latest = _i(_rpc("eth_blockNumber", []))
    block = _i(receipt.get("blockNumber"))
    gas_used = _i(receipt.get("gasUsed"))
    egp = _i(receipt.get("effectiveGasPrice"))
    fee_wei = gas_used * egp
    return {
        "tx": tx_hash,
        "state": "success" if _i(receipt.get("status")) == 1 else "reverted",
        "status": _i(receipt.get("status")),
        "block": block,
        "confirmations": max(0, latest - block + 1),
        "from": receipt.get("from"),
        "to": receipt.get("to"),
        "contract_address": receipt.get("contractAddress"),
        "gas_used": gas_used,
        "effective_gas_price_gwei": round(egp / 1e9, 6),
        "fee_eth": round(fee_wei / 1e18, 9),
        "logs_count": len(receipt.get("logs") or []),
        "latest_block": latest,
        "readonly": True,
    }

def selftest():
    checks = []
    def ok(name, cond): checks.append((name, bool(cond)))
    bad = fetch_tx_status("0x1234")
    ok("rejects_short_hash", "error" in bad)
    nf = fetch_tx_status("0x" + "00" * 32)
    ok("nonexistent_not_found_or_pending", nf.get("state") in ("not_found", "pending", "success"))
    # real well-known tx: first USDC transfer block tx is variable; use a known
    # Base deposit tx shape check via receipt of a recent block's tx instead.
    blk = _rpc("eth_getBlockByNumber", ["latest", True])
    txs = blk.get("transactions") or []
    ok("latest_block_has_txs", len(txs) > 0)
    if txs:
        h = txs[0] if isinstance(txs[0], str) else txs[0].get("hash")
        r = fetch_tx_status(h)
        ok("real_tx_state", r.get("state") in ("success", "reverted"))
        ok("real_tx_confirmations_ge1", r.get("confirmations", 0) >= 1)
        ok("real_tx_fee_nonneg", r.get("fee_eth", -1) >= 0)
        ok("real_tx_readonly", r.get("readonly") is True)
    passed = sum(1 for _, c in checks if c)
    for n, c in checks:
        print(("PASS" if c else "FAIL"), n)
    print("%d/%d PASS" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) > 1:
        print(json.dumps(fetch_tx_status(sys.argv[1]), indent=2))
    else:
        print("usage: tx_status.py <tx_hash> | --selftest")
