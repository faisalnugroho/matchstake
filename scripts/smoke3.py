#!/usr/bin/env python3
"""MatchStake smoke rounds 2-3 (post MATCH): determinism + fail-safe.

  python scripts/smoke3.py A   # offer #2 on a new request -> print key
  # push listing2.html with marker, then:
  python scripts/smoke3.py B   # evaluate offer #2 -> expect MATCH (determinism)
  # push listing3.html WITHOUT marker (fail-safe page), then:
  python scripts/smoke3.py C   # offer #3 -> print key
  # push listing3.html containing the key (marker recovery), then:
  python scripts/smoke3.py D   # evaluate offer #3 -> expect MATCH
"""
import json
import re
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


def extract_id(p):
    if isinstance(p, dict):
        return str(p.get("readable", p)).strip('"')
    s = str(p)
    m = re.search(r"'readable': '\"?([0-9]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"([0-9]+)", s)
    return m.group(1) if m else s.strip('"')


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
    log("tx", fn=fn, tx=tx, result_name=rn, exec=ex, payload=str(payload)[:400])
    return ok, payload


def read(fn, args=None):
    return client.read_contract(address=ADDR, function_name=fn, args=args or [])


def new_request(title):
    stats = json.loads(read("get_stats"))
    rid = str(int(stats["requests_created"]) + 1)
    ok, p = wd("create_request", [title,
        json.dumps(["price under 50 GEN", "ships within 7 days"]),
        "7200"], value=10**17)
    assert ok, "create_request failed"
    return extract_id(p)


def submit(rid, url):
    ok, p = wd("submit_offer", [rid, url, "smoke3"])
    assert ok, "submit_offer failed"
    oid = extract_id(p)
    offer = json.loads(read("get_offer", [oid]))
    return oid, offer["listing_key"]


def evaluate(oid):
    ok, p = wd("evaluate_offer", [oid])
    assert ok, "evaluate_offer failed"
    offer = json.loads(read("get_offer", [oid]))
    verdict = p.get("verdict") if isinstance(p, dict) else None
    log("evaluated", oid=oid, verdict=verdict, offer_status=offer["status"],
        offer_verdict=offer.get("verdict", "")[:500])
    return verdict, offer["status"], offer


if PHASE == "A":
    rid = new_request("Determinism round 2")
    oid, key = submit(rid, "https://raw.githubusercontent.com/faisalnugroho/"
                          "matchstake/main/testdata/listing2.html")
    log("round2_prepped", rid=rid, oid=oid, key=key)
    print("OFFER_ID=" + oid)
    print("KEY=" + key)

elif PHASE == "B":
    verdict, status, offer = evaluate(sys.argv[2])
    print("VERDICT=" + str(verdict), "STATUS=" + status)

elif PHASE == "C":
    rid = new_request("Fail-safe marker round")
    oid, key = submit(rid, "https://raw.githubusercontent.com/faisalnugroho/"
                          "matchstake/main/testdata/listing3.html")
    log("round3_prepped", rid=rid, oid=oid, key=key)
    print("OFFER_ID=" + oid)
    print("KEY=" + key)

elif PHASE == "D":
    verdict, status, offer = evaluate(sys.argv[2])
    print("VERDICT=" + str(verdict), "STATUS=" + status)
    if status == "MATCHED":
        req = json.loads(read("get_request", [offer["request_id"]]))
        claim = json.loads(read("get_claimable", [offer["seller"]]))
        log("settlement", request_status=req["status"],
            winner=req["winner_offer"], seller_claimable=claim["amount"])
    stats = json.loads(read("get_stats"))
    log("final_stats", **stats)
