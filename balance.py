#!/usr/bin/env python3
"""balance.py v1.0.0 — ETH + USDC balance for any address on Base (read-only, public RPC)."""
import json, sys, urllib.request

RPCS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://base.drpc.org",
]
UA = {"Content-Type": "application/json", "User-Agent": "base-oracle/1.12"}
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode())
            if "error" in d:
                last = "rpc error: %s" % d["error"]
                continue
            return d["result"]
        except Exception as e:
            last = "%s: %s" % (url, e)
    raise RuntimeError("all RPCs failed: %s" % last)

def fetch_balance(addr):
    addr = addr.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise ValueError("invalid address")
    eth_wei = int(rpc("eth_getBalance", [addr, "latest"]), 16)
    data = "0x70a08231" + addr[2:].rjust(64, "0")
    raw = rpc("eth_call", [{"to": USDC, "data": data}, "latest"])
    usdc_raw = int(raw, 16) if raw and raw != "0x" else 0
    nonce = int(rpc("eth_getTransactionCount", [addr, "latest"]), 16)
    code = rpc("eth_getCode", [addr, "latest"])
    is_contract = bool(code and code != "0x")
    return {
        "schema": "balance/1",
        "network": "eip155:8453",
        "address": addr,
        "eth_balance": eth_wei / 1e18,
        "eth_balance_wei": eth_wei,
        "usdc_balance": usdc_raw / 1e6,
        "usdc_balance_raw": usdc_raw,
        "nonce": nonce,
        "is_contract": is_contract,
        "code_size_bytes": (len(code) - 2) // 2 if code and code != "0x" else 0,
    }

def _p(ok, msg):
    print(("PASS" if ok else "FAIL"), "-", msg)
    return 1 if ok else 0

def selftest():
    t = 0
    d = fetch_balance(USDC)
    t += _p(d["schema"] == "balance/1", "schema tag")
    t += _p(d["is_contract"] is True, "USDC contract detected")
    t += _p(d["usdc_balance"] >= 0, "usdc balance non-negative")
    t += _p(d["nonce"] >= 0, "nonce present")
    d2 = fetch_balance("0x4200000000000000000000000000000000000006")
    t += _p(d2["is_contract"] is True, "WETH contract detected")
    t += _p(d2["eth_balance"] >= 0, "WETH eth balance non-negative")
    try:
        fetch_balance("0xinvalid")
        t += _p(False, "invalid addr should raise")
    except ValueError:
        t += _p(True, "invalid addr raises ValueError")
    try:
        fetch_balance("0x1234")
        t += _p(False, "short addr should raise")
    except ValueError:
        t += _p(True, "short addr raises ValueError")
    print("%d/8 PASS" % t)
    return 0 if t == 8 else 1

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    addr = sys.argv[1] if len(sys.argv) > 1 else USDC
    print(json.dumps(fetch_balance(addr), indent=2))
