#!/usr/bin/env python3
"""MatchStake smoke — two phases (validator fetches need pushed marker).

  python scripts/smoke2.py A    # create request + submit offer, print listing key
  # (owner updates testdata/listing.html with the key, commits & pushes)
  python scripts/smoke2.py B    # evaluate offer + read back verdict + stats
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = json.loads((ROOT / "scripts" / "deployed_studionet.json").read_text())
ADDR = STATE["address"]
KEYFILE = json.loads((ROOT / "scripts" / ".deployer_key.json").read_text())
LOG = ROOT / "scripts" / "deployment_log.json"

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

PHASE = sys.argv[1] if len(sys.argv) > 1 else "A"
EVID = json.loads(LOG.read_text()) if LOG.exists() else []


def log(ev, **kw):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": ev, **kw}
    EVID.append(rec)
    LOG.write_text(json.dumps(EVID, indent=2))
    print(json.dumps(rec, ensure_ascii=False))


account = create_account(account_private_key=KEYFILE["private_key"])
client = create_client(chain=studionet, account=account)


def wait_final(tx_hash):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
        full_transaction=True, interval=5000, retries=60)
    rn = receipt.get("result_name")
    lr = (receipt.get("consensus_data", {}).get("leader_receipt") or [{}])[0]
    ex = lr.get("execution_result")
    ok = rn == "MAJORITY_AGREE" and ex in ("SUCCESS", "FINISHED_WITH_RETURN", None)
    return receipt, ok, rn, ex, lr


def wd(fn, args=None, value=0):
    tx = client.write_contract(address=ADDR, function_name=fn,
                               args=args or [], account=account, value=value)
    receipt, ok, rn, ex, lr = wait_final(tx)
    payload = None
    pl = lr.get("result", {}).get("payload")
    if pl:
        try:
            payload = json.loads(pl)
        except Exception:
            payload = pl
    log("tx", fn=fn, tx=tx, result_name=rn, exec=ex, payload=str(payload)[:300])
    return ok, payload


def extract_id(p):
    """Readable payload shapes: '"2"', {'readable': '"2"'},
    "{'raw': [12, 50], 'readable': '\"2\"'}" — always end with the bare id."""
    if isinstance(p, dict):
        return str(p.get("readable", p)).strip('"')
    s = str(p)
    import re
    m = re.search(r"'readable': '\"?([0-9]+)", s)
    if m:
        return m.group(1)
    m = re.search(r'([0-9]+)', s)
    return m.group(1) if m else s.strip('"')


def read(fn, args=None):
    return client.read_contract(address=ADDR, function_name=fn, args=args or [])


if PHASE == "A":
    stats = json.loads(read("get_stats"))
    rid = str(int(stats["requests_created"]) + 1)
    ok, p = wd("create_request",
               ["Ceramic mugs — smoke 2",
                json.dumps(["price under 50 GEN", "ships within 7 days"]),
                "7200"], value=10**17)
    assert ok, "create_request failed"
    rid = extract_id(p)
    log("request_created", rid=rid)

    ok, p = wd("submit_offer",
               [rid, "https://raw.githubusercontent.com/faisalnugroho/"
                "matchstake/main/testdata/listing.html", "smoke offer"])
    assert ok, "submit_offer failed"
    oid = extract_id(p)
    offer = json.loads(read("get_offer", [oid]))
    log("offer_created", oid=oid, key=offer["listing_key"])
    print("MARKER_LINE=MatchStake-Listing-Key: " + offer["listing_key"])
    print("OFFER_ID=" + oid)

elif PHASE == "B":
    oid = sys.argv[2]
    ok, p = wd("evaluate_offer", [oid])
    assert ok, "evaluate_offer failed"
    verdict = p.get("verdict") if isinstance(p, dict) else p
    offer = json.loads(read("get_offer", [oid]))
    log("evaluated", oid=oid, verdict=verdict,
        offer_status=offer["status"],
        labels=offer.get("verdict", "")[:400])
    if offer.get("status") == "MATCHED":
        req = json.loads(read("get_request", [offer["request_id"]]))
        claim = json.loads(read("get_claimable", [offer["seller"]]))
        log("settlement", request_status=req["status"],
            winner=req["winner_offer"],
            seller_claimable=claim["amount"])
    stats = json.loads(read("get_stats"))
    log("final_stats", **stats)
    print("VERDICT=" + str(verdict))
