#!/usr/bin/env python3
"""fee_history.py v1.0.0 — Base fee history + percentile rewards.

Read-only. Uses public Base RPC eth_feeHistory. No signing, no keys, no spend.
"""
import json, sys, urllib.request, statistics

RPCS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://base.drpc.org",
]
UA = {"Content-Type": "application/json", "User-Agent": "base-oracle/1.11"}
GWEI = 1e9


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
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (url, e)
    raise RuntimeError("all RPCs failed: %s" % last)


def hexint(v):
    return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v)


def fetch_fee_history(blocks=20, percentiles=(25, 50, 75)):
    blocks = max(1, min(int(blocks), 128))
    pct = [float(p) for p in percentiles][:4]
    res = rpc("eth_feeHistory", [hex(blocks), "latest", pct])
    base_fees = [hexint(x) / GWEI for x in res.get("baseFeePerGas", [])]
    rewards = res.get("reward", [])
    used = res.get("gasUsedRatio", [])
    # median tip per block = middle percentile of last element
    med_tips = [ (r[-1][min(len(pct) - 1, 1)] if pct else 0) for r in [] ]
    med_tip_gwei = []
    mid = min(len(pct) - 1, 1) if pct else 0
    for row in rewards:
        try:
            med_tip_gwei.append(hexint(row[mid]) / GWEI)
        except Exception:
            med_tip_gwei.append(0.0)
    out = {
        "schema": "fee_history/1",
        "network": "eip155:8453",
        "blocks_analyzed": len(base_fees) - 1 if base_fees else 0,
        "oldest_block": hexint(res.get("oldestBlock", "0x0")),
        "base_fee_gwei": {
            "latest": base_fees[-2] if len(base_fees) > 1 else (base_fees[-1] if base_fees else None),
            "next": base_fees[-1] if base_fees else None,
            "min": min(base_fees) if base_fees else None,
            "max": max(base_fees) if base_fees else None,
            "mean": round(statistics.fmean(base_fees), 9) if base_fees else None,
        },
        "median_priority_tip_gwei": {
            "min": min(med_tip_gwei) if med_tip_gwei else None,
            "max": max(med_tip_gwei) if med_tip_gwei else None,
            "mean": round(statistics.fmean(med_tip_gwei), 9) if med_tip_gwei else None,
        },
        "gas_used_ratio_mean": round(statistics.fmean(used), 4) if used else None,
        "percentiles": pct,
    }
    return out


def _p(ok, msg):
    print(("PASS" if ok else "FAIL"), "-", msg)
    return 1 if ok else 0


def selftest():
    t = 0
    d = fetch_fee_history(10)
    t += _p(d["schema"] == "fee_history/1", "schema tag")
    t += _p(d["blocks_analyzed"] == 10, "10 blocks analyzed (got %s)" % d["blocks_analyzed"])
    t += _p(d["oldest_block"] > 0, "oldest_block set")
    bf = d["base_fee_gwei"]
    t += _p(bf["latest"] is not None and bf["latest"] >= 0, "latest base fee present")
    t += _p(bf["min"] <= bf["max"], "min<=max base fee")
    t += _p(bf["mean"] is not None, "mean computed")
    t += _p(d["gas_used_ratio_mean"] is not None and 0 <= d["gas_used_ratio_mean"] <= 1, "gas used ratio in [0,1]")
    d2 = fetch_fee_history(3)
    t += _p(d2["blocks_analyzed"] == 3, "small window works")
    d3 = fetch_fee_history(0)
    t += _p(d3["blocks_analyzed"] == 1, "blocks=0 clamps to 1")
    print("%d/9 PASS" % t)
    return 0 if t == 9 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    blocks = 20
    for i, a in enumerate(sys.argv):
        if a == "--blocks" and i + 1 < len(sys.argv):
            blocks = int(sys.argv[i + 1])
    print(json.dumps(fetch_fee_history(blocks), indent=2))
