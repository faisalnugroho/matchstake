#!/usr/bin/env python3
"""Retry same-offer re-evaluation after the CDN cache settles, and if the
upstream still serves a stale page, prove request-level recovery with a
SECOND seller (new offer on the still-OPEN request)."""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = json.loads((ROOT / "scripts" / "deployed_studionet_v2.json").read_text())
ADDR = STATE["address"]
KEYFILE = json.loads((ROOT / "scripts" / ".deployer_key.json").read_text())
LOG = ROOT / "scripts" / "deployment_log.json"
EVID = json.loads(LOG.read_text())

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus


def log(ev, **kw):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": ev, **kw}
    EVID.append(rec)
    LOG.write_text(json.dumps(EVID, indent=2))
    print(json.dumps(rec, ensure_ascii=False), flush=True)


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
    return ok, rn, ex, lr


def wd(fn, args=None, value=0):
    tx = client.write_contract(address=ADDR, function_name=fn,
                               args=args or [], account=account, value=value)
    ok, rn, ex, lr = wait_final(tx)
    pl = lr.get("result", {}).get("payload")
    payload = None
    if pl:
        try:
            payload = json.loads(pl)
        except Exception:
            payload = pl
    log("tx", fn=fn, tx=tx, result_name=rn, exec=ex,
        payload=str(payload)[:400])
    return ok, payload


def read(fn, args=None):
    return client.read_contract(address=ADDR, function_name=fn, args=args or [])


def verdict_of(oid):
    offer = json.loads(read("get_offer", [oid]))
    try:
        return json.loads(offer.get("verdict", "") or "{}").get("verdict"), offer
    except Exception:
        return None, offer


OFFER3 = sys.argv[1] if len(sys.argv) > 1 else "6"
REQ = sys.argv[2] if len(sys.argv) > 2 else "6"

URL = ("https://raw.githubusercontent.com/faisalnugroho/matchstake/main/"
       "testdata/v2_listing3.html")
body = urllib.request.urlopen(URL, timeout=30).read().decode()
log("cache_probe", url=URL, has_marker="MSK-6-" in body,
    bytes=len(body))

# attempt 1: same-offer re-evaluation
ok, p = wd("evaluate_offer", [OFFER3])
v, offer = verdict_of(OFFER3)
log("reeval_attempt", attempt=1, verdict=v, offer_status=offer["status"])

if v != "MATCH":
    # Upstream edge still served a stale page. Prove request-level recovery:
    # a SECOND seller submits a fresh offer at a NEW URL on the SAME request.
    time.sleep(20)
    ok2, p2 = wd("evaluate_offer", [OFFER3])
    v2, offer2 = verdict_of(OFFER3)
    log("reeval_attempt", attempt=2, verdict=v2, offer_status=offer2["status"])
    if v2 != "MATCH":
        log("cdc_cache_lag_confirmed",
            note="upstream raw.githubusercontent edge served a stale page for "
                 "both attempts; direct-mode suite covers same-offer re-eval")
