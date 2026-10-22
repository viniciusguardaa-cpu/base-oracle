#!/usr/bin/env python3
"""x402_auditor.py — audit an x402-paid HTTP service.

Read-only network client. Fetches public discovery metadata and probes paid
routes for HTTP 402 payment requirements. No signing, no wallet, no payment.

Usage:
  python3 x402_auditor.py https://seller.example --json
  python3 x402_auditor.py https://seller.example --probe /v1/report --probe /v1/x
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

TIMEOUT = 12
UA = "x402-auditor/1.0 (+https://base.org)"


def fetch(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(1_000_000)
            return {"ok": True, "status": r.status, "url": url, "headers": dict(r.headers), "body": body.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        body = e.read(1_000_000)
        return {"ok": False, "status": e.code, "url": url, "headers": dict(e.headers), "body": body.decode("utf-8", "replace"), "error": str(e)}
    except Exception as e:
        return {"ok": False, "status": None, "url": url, "headers": {}, "body": "", "error": repr(e)}


def j(body: str) -> Any:
    try:
        return json.loads(body)
    except Exception:
        return None


def norm_base(url: str) -> str:
    return url.rstrip("/")


def payment_reqs(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = j(resp.get("body") or "")
    if not isinstance(data, dict):
        return []
    for key in ("accepts", "paymentRequirements", "payment_requirements", "x402"):
        v = data.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            inner = v.get("accepts") or v.get("paymentRequirements")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def audit(base: str, probes: list[str]) -> dict[str, Any]:
    base = norm_base(base)
    out: dict[str, Any] = {"schema": "x402-audit/1", "target": base, "checks": [], "paid_routes": [], "issues": [], "score": 0}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if ok:
            out["score"] += 1

    disc = fetch(base + "/.well-known/x402")
    disco = j(disc.get("body") or "")
    check("discovery_fetch", disc.get("status") == 200, {"status": disc.get("status")})
    check("discovery_json", isinstance(disco, dict), {"keys": sorted(disco) if isinstance(disco, dict) else None})
    if isinstance(disco, dict):
        check("discovery_declares_x402", any(k in disco for k in ("x402", "payment", "services", "resources", "accepts")), {"keys": sorted(disco)[:12]})

    api = fetch(base + "/openapi.json")
    spec = j(api.get("body") or "")
    paths = sorted((spec or {}).get("paths", {})) if isinstance(spec, dict) else []
    check("openapi_fetch", api.get("status") == 200 and isinstance(spec, dict), {"status": api.get("status"), "paths": len(paths)})

    if not probes:
        probes = [p for p in paths if p.startswith("/v1/")][:8] or ["/"]

    for p in probes[:12]:
        r = fetch(base + p)
        reqs = payment_reqs(r)
        route = {"path": p, "status": r.get("status"), "requires_payment": r.get("status") == 402 or bool(reqs), "requirements": reqs[:3]}
        out["paid_routes"].append(route)
        if r.get("status") == 402:
            check("http_402_" + p, True, {"requirements": len(reqs)})
        if reqs:
            for req in reqs[:3]:
                check("req_scheme_" + p, "scheme" in req or "scheme" in req.get("requirements", {}), req)
                check("req_network_" + p, str(req.get("network") or req.get("networkId") or "") .startswith("eip155:8453") or "base" in str(req).lower(), req.get("network"))
                check("req_payto_" + p, str(req.get("payTo") or req.get("pay_to") or "").startswith("0x"), req.get("payTo") or req.get("pay_to"))
                amt = req.get("maxAmountRequired") or req.get("amount") or req.get("price")
                check("req_amount_" + p, amt is not None, amt)

    if not any(r["requires_payment"] for r in out["paid_routes"]):
        out["issues"].append("no paid route returned HTTP 402 or payment requirements")
    if any((r.get("status") or 0) >= 500 for r in out["paid_routes"]):
        out["issues"].append("one or more paid routes returned 5xx")
    out["verdict"] = "pass" if out["score"] >= 4 and not out["issues"] else "review"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--probe", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = audit(args.target, args.probe)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"target: {result['target']}\nverdict: {result['verdict']} score={result['score']}")
        for c in result["checks"]:
            print(("PASS" if c["ok"] else "FAIL"), c["name"])
        for i in result["issues"]:
            print("ISSUE", i)
    return 0 if result["verdict"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
