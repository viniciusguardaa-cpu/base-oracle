#!/usr/bin/env python3
"""contract_inspect.py v1.0.0 — inspect an EVM/Base contract via public RPC.

READ-ONLY: only eth_getCode / eth_call / eth_getTransactionCount against
public Base RPC endpoints. No keys, no writes, no personal files.

Usage:
  python3 contract_inspect.py <address>          # JSON report to stdout
  python3 contract_inspect.py --selftest         # internal asserts
"""
import json, sys, urllib.request

RPCS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
]
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"

# known 4-byte selectors (no keccak needed)
SEL = {
    "supportsInterface": "0x01ffc9a7",
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "totalSupply": "0x18160ddd",
    "owner": "0x8da5cb5b",
}
ERC165_IFACES = {
    "0x80ac58cd": "ERC-721",
    "0xd9b67a26": "ERC-1155",
    "0x01ffc9a7": "ERC-165",
    "0x5b5e139f": "ERC-721 metadata",
    "0x2a55205a": "EIP-2981 royalties",
}

UA = {"User-Agent": "base-oracle/1.7", "Content-Type": "application/json"}


def _rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=10) as r:
                out = json.loads(r.read())
            if "error" in out:
                raise RuntimeError(out["error"])
            return out.get("result")
        except Exception as e:  # try next RPC
            last = e
    raise RuntimeError("all RPCs failed: %s" % last)


def _call(addr, data):
    return _rpc("eth_call", [{"to": addr, "data": data}, "latest"])


def _decode_str(hexout):
    if not hexout or hexout == "0x" or len(hexout) < 130:
        return None
    h = hexout[2:]
    try:
        off = int(h[0:64], 16) * 2
        ln = int(h[off:off + 64], 16) * 2
        raw = bytes.fromhex(h[off + 64:off + 64 + ln])
        return raw.decode("utf-8", "replace") or None
    except Exception:
        return None


def _decode_uint(hexout):
    if not hexout or hexout == "0x":
        return None
    try:
        return int(hexout, 16)
    except Exception:
        return None


def _decode_addr(hexout):
    if not hexout or len(hexout) < 66:
        return None
    a = "0x" + hexout[-40:]
    return a if int(a, 16) != 0 else None


def _pad32(v):
    return format(v, "064x")


def supports(addr, iface_hex):
    out = _call(addr, SEL["supportsInterface"] + iface_hex[2:].rjust(64, "0"))
    return _decode_uint(out) == 1


def inspect(addr):
    a = addr.lower()
    if not (a.startswith("0x") and len(a) == 42):
        raise ValueError("bad address")
    code = _rpc("eth_getCode", [a, "latest"])
    is_contract = bool(code and code != "0x")
    rep = {
        "schema": "contract_inspect/1",
        "network": "eip155:8453",
        "address": a,
        "is_contract": is_contract,
        "bytecode_bytes": (len(code) - 2) // 2 if is_contract else 0,
        "nonce": _decode_uint(_rpc("eth_getTransactionCount", [a, "latest"])),
        "readonly": True,
    }
    if not is_contract:
        rep["kind"] = "eoa"
        return rep
    erc20 = {}
    for k in ("name", "symbol"):
        try:
            erc20[k] = _decode_str(_call(a, SEL[k]))
        except Exception:
            erc20[k] = None
    for k in ("decimals", "totalSupply"):
        try:
            erc20[k] = _decode_uint(_call(a, SEL[k]))
        except Exception:
            erc20[k] = None
    rep["erc20"] = erc20 if any(v is not None for v in erc20.values()) else None
    try:
        rep["owner"] = _decode_addr(_call(a, SEL["owner"]))
    except Exception:
        rep["owner"] = None
    ifaces = {}
    try:
        for iid, label in ERC165_IFACES.items():
            ifaces[label] = supports(a, iid)
    except Exception:
        ifaces = {}
    rep["interfaces"] = ifaces or None
    kind = "contract"
    if rep["erc20"] and rep["erc20"].get("decimals") is not None:
        kind = "erc20-or-similar"
    if ifaces.get("ERC-721"):
        kind = "erc721"
    elif ifaces.get("ERC-1155"):
        kind = "erc1155"
    rep["kind"] = kind
    return rep


def _selftest():
    usdc = inspect(USDC)
    assert usdc["is_contract"] is True
    assert usdc["bytecode_bytes"] > 100
    assert usdc["erc20"] and usdc["erc20"].get("decimals") == 6
    assert (usdc["erc20"].get("symbol") or "").upper() == "USDC"
    weth = inspect(WETH)
    assert weth["is_contract"] is True
    eoa = inspect("0x65aa59025C4AcD8CD7ba2051ecb9152abaE4fcC6")
    assert eoa["kind"] == "eoa" and eoa["bytecode_bytes"] == 0
    try:
        inspect("0xdead")
        assert False
    except ValueError:
        pass
    print("contract_inspect selftest: 8/8 PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if len(sys.argv) < 2:
        print("usage: contract_inspect.py <address>|--selftest", file=sys.stderr)
        sys.exit(2)
    try:
        print(json.dumps(inspect(sys.argv[1]), indent=2, sort_keys=True))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
