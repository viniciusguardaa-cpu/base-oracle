#!/usr/bin/env python3
"""token_meta.py v1.0.0 — ERC20 metadata oracle for Base.

Read-only: 4 eth_call requests to public Base RPCs (name, symbol,
decimals, totalSupply). No keys, no signing, no state.

CLI:
  token_meta.py --token 0x... [--json]
  token_meta.py --selftest
"""
import json, sys, urllib.request

VERSION = "token_meta/1.0.0"
CHAIN_ID = 8453
RPCS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
]
UA = {"User-Agent": "base-oracle/1.0", "Content-Type": "application/json"}
# keccak256 selectors (first 4 bytes)
SEL_NAME = "0x06fdde03"         # name()
SEL_SYMBOL = "0x95d89b41"     # symbol()
SEL_DECIMALS = "0x313ce567"   # decimals()
SEL_SUPPLY = "0x18160ddd"     # totalSupply()


def _rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            if "error" in d:
                last = d["error"].get("message", "rpc error")
                continue
            return d.get("result")
        except Exception as e:
            last = str(e)
    raise RuntimeError(f"all RPCs failed: {last}")


def _call(token, selector):
    return _rpc("eth_call", [{"to": token, "data": selector}, "latest"])


def _decode_string(hexdata):
    """Decode ABI string return (offset|len|data) or bytes32 fixed string."""
    if not hexdata or hexdata == "0x":
        return None
    b = bytes.fromhex(hexdata[2:])
    try:
        if len(b) >= 64:
            off = int.from_bytes(b[:32], "big")
            if off + 32 <= len(b):
                ln = int.from_bytes(b[off:off + 32], "big")
                return b[off + 32:off + 32 + ln].decode("utf-8", "replace").rstrip("\x00")
        return b.decode("utf-8", "replace").rstrip("\x00")
    except Exception:
        return None


def _decode_uint(hexdata):
    if not hexdata or hexdata == "0x":
        return None
    return int(hexdata, 16)


def fetch_token_meta(token):
    token = (token or "").strip().lower()
    if not (token.startswith("0x") and len(token) == 42):
        return {"ok": False, "error": "invalid token address (expected 0x + 40 hex)"}
    code = _rpc("eth_getCode", [token, "latest"])
    if not code or code == "0x":
        return {"ok": False, "error": "no contract at address", "token": token, "chain_id": CHAIN_ID}
    try:
        name = _decode_string(_call(token, SEL_NAME))
    except Exception:
        name = None
    try:
        symbol = _decode_string(_call(token, SEL_SYMBOL))
    except Exception:
        symbol = None
    try:
        decimals = _decode_uint(_call(token, SEL_DECIMALS))
    except Exception:
        decimals = None
    try:
        supply_raw = _decode_uint(_call(token, SEL_SUPPLY))
    except Exception:
        supply_raw = None
    if name is None and symbol is None and decimals is None and supply_raw is None:
        return {"ok": False, "error": "contract does not answer ERC20 metadata calls",
                "token": token, "chain_id": CHAIN_ID}
    supply = None
    if supply_raw is not None and decimals is not None:
        supply = supply_raw / (10 ** decimals)
    return {
        "ok": True, "schema": VERSION, "token": token, "chain_id": CHAIN_ID,
        "name": name, "symbol": symbol, "decimals": decimals,
        "total_supply_raw": str(supply_raw) if supply_raw is not None else None,
        "total_supply": supply,
        "flags": {
            "has_name": name is not None,
            "has_symbol": symbol is not None,
            "has_decimals": decimals is not None,
            "has_supply": supply_raw is not None,
        },
        "read_only": True,
    }


def _p(ok, msg, state):
    state["n"] += 1
    if ok:
        state["p"] += 1
        print("PASS", msg)
    else:
        print("FAIL", msg)


def selftest():
    st = {"p": 0, "n": 0}
    # USDC on Base
    usdc = fetch_token_meta("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
    _p(usdc.get("ok") is True, "usdc ok", st)
    _p(usdc.get("symbol") == "USDC", f"usdc symbol={usdc.get('symbol')}", st)
    _p(usdc.get("decimals") == 6, f"usdc decimals={usdc.get('decimals')}", st)
    _p(usdc.get("total_supply") and usdc["total_supply"] > 0, "usdc supply>0", st)
    # WETH on Base
    weth = fetch_token_meta("0x4200000000000000000000000000000000000006")
    _p(weth.get("symbol") == "WETH", f"weth symbol={weth.get('symbol')}", st)
    _p(weth.get("decimals") == 18, f"weth decimals={weth.get('decimals')}", st)
    # EOA -> no contract
    eoa = fetch_token_meta("0x65aa59025C4AcD8CD7ba2051ecb9152abaE4fcC6")
    _p(eoa.get("ok") is False and "no contract" in eoa.get("error", ""), "eoa rejected", st)
    # malformed
    bad = fetch_token_meta("0x123")
    _p(bad.get("ok") is False and "invalid" in bad.get("error", ""), "bad addr rejected", st)
    _p(bad.get("error") is not None, "fail-closed error msg", st)
    print(f"{st['p']}/{st['n']} PASS")
    return 0 if st["p"] == st["n"] else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    token = None
    if "--token" in argv:
        i = argv.index("--token")
        if i + 1 < len(argv):
            token = argv[i + 1]
    if not token:
        # positional fallback
        for a in argv[1:]:
            if a.startswith("0x"):
                token = a
                break
    if not token:
        print("usage: token_meta.py --token 0x... | --selftest", file=sys.stderr)
        return 1
    out = fetch_token_meta(token)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
