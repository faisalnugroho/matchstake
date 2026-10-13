#!/usr/bin/env python3
"""Deploy MatchStake to Studionet + smoke test.

Usage:
  python scripts/deploy_studionet.py            # deploy (leader_only)
  python scripts/deploy_studionet.py --full     # deploy (full consensus)
  python scripts/deploy_studionet.py --smoke    # smoke-test deployed address
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "matchstake.py"
STATE = ROOT / "scripts" / "deployed_studionet.json"
KEYFILE = ROOT / "scripts" / ".deployer_key.json"

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus, ExecutionResult

LEADER_ONLY = "--full" not in sys.argv
SMOKE_ONLY = "--smoke" in sys.argv

EVIDENCE = []


def log(ev, **kw):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": ev, **kw}
    EVIDENCE.append(rec)
    print(json.dumps(rec, ensure_ascii=False))
    (ROOT / "scripts" / "deployment_log.json").write_text(
        json.dumps(EVIDENCE, indent=2)
    )


def get_client():
    if KEYFILE.exists():
        d = json.loads(KEYFILE.read_text())
        account = create_account(account_private_key=d["private_key"])
    else:
        account = create_account()
        KEYFILE.write_text(json.dumps({
            "address": account.address, "private_key": account.key.hex()
        }))
        log("keyfile_created", address=account.address)
    client = create_client(chain=studionet, account=account)
    return client, account


def wait_final(client, tx_hash):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.FINALIZED,
        full_transaction=True,
        interval=5000, retries=60,
    )
    result_name = receipt.get("result_name")
    exec_res = (receipt.get("consensus_data", {}).get("leader_receipt") or [{}])[0] \
        .get("execution_result")
    ok = result_name == "MAJORITY_AGREE" and exec_res in (
        "SUCCESS", "FINISHED_WITH_RETURN", None)
    return receipt, ok, result_name, exec_res


def deploy():
    client, account = get_client()
    log("deploy_start", leader_only=LEADER_ONLY, account=account.address)
    try:
        client.fund_account(account.address, 10**18)
    except Exception as e:
        log("fund_skip", error=str(e)[:200])
    code = CONTRACT.read_text()
    tx = client.deploy_contract(
        code=code, account=account, args=[],
        leader_only=LEADER_ONLY,
    )
    receipt, ok, rn, ex = wait_final(client, tx)
    addr = (receipt.get("data", {}) or {}).get("contract_address") or \
        receipt.get("to_address")
    if not ok:
        log("deploy_FAILED", tx=tx, result_name=rn, exec=ex)
        sys.exit(1)
    log("deploy_ok", address=addr, tx=tx, result_name=rn, exec=ex)
    STATE.write_text(json.dumps({
        "address": addr, "deploy_tx": tx, "leader_only": LEADER_ONLY,
        "code_sha256": __import__("hashlib").sha256(
            CONTRACT.read_bytes()).hexdigest(),
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2))
    return client, account, addr


def extract_id(raw):
    """Offer/request write methods return a small-int id. The SDK surfaces it
    as {'raw': [12, 50], 'readable': '"2"'} — pull the bare id string out.
    Never pass the whole payload dict back as an argument."""
    if isinstance(raw, dict):
        readable = raw.get("readable", "")
        if isinstance(readable, str):
            m = re.search(r"([0-9]+)", readable)
            if m:
                return m.group(1)
        return str(raw)
    if isinstance(raw, str):
        m = re.search(r"([0-9]+)", raw)
        if m:
            return m.group(1)
    return str(raw)


def smoke():
    client, account = get_client()
    addr = json.loads(STATE.read_text())["address"]
    log("smoke_start", address=addr)
    w = client.write_contract

    def wd(fn, args=None, value=None):
        kw = {}
        if value is not None:
            kw["value"] = value
        tx = w(address=addr, function_name=fn, args=args or [],
               account=account, **kw)
        receipt, ok, rn, ex = wait_final(client, tx)
        payload = None
        lr = (receipt.get("consensus_data", {}).get("leader_receipt") or [{}])
        pl = lr[0].get("result", {}).get("payload")
        if pl:
            try:
                payload = json.loads(pl)
            except Exception:
                payload = pl
        log("tx", fn=fn, tx=tx, result_name=rn, exec=ex,
            payload=str(payload)[:300] if payload else None)
        return ok, payload, receipt

    ok, rid_raw, _ = wd("create_request",
                        ["Ceramic mugs",
                         json.dumps(["price under 50 GEN", "ships within 7 days"]),
                         "3600"],
                        value=10**17)
    assert ok and rid_raw, "create_request failed"
    # readable payload is a JSON string like '"1"'
    rid = extract_id(rid_raw)
    log("request_id", rid=rid)

    ok, oid_raw, _ = wd("submit_offer",
                        [rid, "https://raw.githubusercontent.com/"
                         "faisalnugroho/matchstake/main/testdata/listing.html",
                         "demo offer"])
    assert ok and oid_raw, "submit_offer failed"
    offer_id = extract_id(oid_raw)

    ok, off, _ = wd("noop", [])  # not used; listing key read via view below
    # read the listing key via a view call
    offer = client.read_contract(address=addr, function_name="get_offer",
                                 args=[offer_id])
    key = json.loads(offer)["listing_key"]
    log("listing_key", key=key)

    ok, res, _ = wd("evaluate_offer", [offer_id])
    verdict = res.get("verdict") if isinstance(res, dict) else res
    log("evaluate", verdict=verdict)

    # determinism: run a second fresh request/offer/evaluate round
    ok, rid2_raw, _ = wd("create_request",
                         ["Ceramic mugs round 2",
                          json.dumps(["price under 50 GEN", "ships within 7 days"]),
                          "3600"],
                         value=10**17)
    rid2 = extract_id(rid2_raw)
    ok, oid2_raw, _ = wd("submit_offer",
                         [rid2, "https://raw.githubusercontent.com/"
                          "faisalnugroho/matchstake/main/testdata/listing2.html",
                          "demo offer 2"])
    oid2 = extract_id(oid2_raw)
    offer2 = client.read_contract(address=addr, function_name="get_offer",
                                  args=[oid2])
    key2 = json.loads(offer2)["listing_key"]
    ok, res2, _ = wd("evaluate_offer", [oid2])
    verdict2 = res2.get("verdict") if isinstance(res2, dict) else res2
    log("evaluate_round2", verdict=verdict2)

    stats = client.read_contract(address=addr, function_name="get_stats",
                                 args=[])
    log("final_stats", stats=stats)
    log("smoke_done")


if __name__ == "__main__":
    if SMOKE_ONLY:
        smoke()
    else:
        deploy()
        smoke()
