#!/usr/bin/env python3
"""contract_call.py — generic read-only eth_call endpoint for base-oracle.

Payers send `to` (contract address) and `data` (0x-prefixed calldata).
Server executes eth_call against public Base RPCs and returns the raw
hex result plus a convenience UTF-8 decode attempt. Read-only by
construction: only eth_call is proxied, never transactions.
Stdlib only. RPC fallback chain.
"""
import json, urllib.request

RPCS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://base.drpc.org",
    "https://1rpc.io/base",
]
UA = {"Content-Type": "application/json", "User-Agent": "base-oracle/1.14"}
HEX = set("0123456789abcdef")

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

def _is_addr(a):
    return isinstance(a,str) and len(a)==42 and a.startswith("0x") and all(c in HEX for c in a[2:].lower())

def _is_data(d):
    return isinstance(d,str) and d.startswith("0x") and len(d)%2==0 and all(c in HEX for c in d[2:].lower())

def _try_utf8(hexresult):
    try:
        b = bytes.fromhex(hexresult[2:])
        # ABI string: 32B offset + 32B len + data
        if len(b) >= 64:
            n = int.from_bytes(b[32:64], "big")
            if 0 < n <= len(b)-64:
                return b[64:64+n].decode("utf-8", "replace")
        return b.rstrip(b"\x00").decode("utf-8", "replace")
    except Exception:
        return None

def fetch_contract_call(to, data, block="latest"):
    to = (to or "").strip().lower()
    data = (data or "").strip().lower()
    if not _is_addr(to):
        return {"error": "invalid `to` (expected 0x + 40 hex)", "readonly": True}
    if data in ("0x", ""):
        return {"error": "`data` required (0x-prefixed calldata, even 0x + selector)", "readonly": True}
    if not _is_data(data):
        return {"error": "invalid `data` (0x-prefixed even-length hex)", "readonly": True}
    if len(data) > 2 + 8192:
        return {"error": "calldata too large (max 4096 bytes)", "readonly": True}
    blk = block if isinstance(block,str) and block.startswith("0x") else "latest"
    try:
        res = _rpc("eth_call", [{"to": to, "data": data}, blk])
    except Exception as e:
        return {"error": "rpc_failed", "detail": str(e), "readonly": True}
    out = {"to": to, "data": data, "block": blk, "result": res, "readonly": True}
    if isinstance(res, str) and res.startswith("0x"):
        txt = _try_utf8(res)
        if txt and txt.isprintable():
            out["result_utf8"] = txt
        try:
            out["result_uint256"] = int(res, 16)
        except Exception:
            pass
    return out

def selftest():
    checks = []
    def ok(n,c): checks.append((n,bool(c)))
    ok("rejects_bad_to", "error" in fetch_contract_call("0x1234","0x70a08231"))
    ok("rejects_empty_data", "error" in fetch_contract_call("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913","0x"))
    ok("rejects_odd_hex", "error" in fetch_contract_call("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913","0x123"))
    # real call: USDC name() selector 0x06fdde03 on Base USDC
    r = fetch_contract_call("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913","0x06fdde03")
    ok("real_call_result", isinstance(r.get("result"),str) and r["result"].startswith("0x"))
    ok("real_call_decodes", "USD" in (r.get("result_utf8") or ""))
    ok("readonly_flag", r.get("readonly") is True)
    # symbol() 0x95d89b41
    r2 = fetch_contract_call("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913","0x95d89b41")
    ok("symbol_call", "USDC" in (r2.get("result_utf8") or ""))
    passed = sum(1 for _,c in checks if c)
    for n,c in checks: print(("PASS" if c else "FAIL"), n)
    print("%d/%d PASS" % (passed, len(checks)))
    return 0 if passed==len(checks) else 1

if __name__ == "__main__":
    import sys
    if len(sys.argv)>1 and sys.argv[1]=="--selftest":
        sys.exit(selftest())
    if len(sys.argv)>2:
        print(json.dumps(fetch_contract_call(sys.argv[1], sys.argv[2]), indent=2))
    else:
        print("usage: contract_call.py <to> <data> | --selftest")
