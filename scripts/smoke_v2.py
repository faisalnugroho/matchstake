#!/usr/bin/env python3
"""MatchStake v2 full smoke: determinism x2, fail-safe, recovery, claim.

  python scripts/smoke_v2.py A   # 2 positive rounds + 1 fail-safe round prep
  # script pushes v2 listing files with markers and waits for raw URL sync,
  # then evaluates everything.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = json.loads((ROOT / "scripts" / "deployed_studionet_v2.json").read_text())
ADDR = STATE["address"]
KEYFILE = json.loads((ROOT / "scripts" / ".deployer_key.json").read_text())
LOG = ROOT / "scripts" / "deployment_log.json"

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus
import urllib.request

EVID = json.loads(LOG.read_text()) if LOG.exists() else []


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


def wd(fn, args=None, value=0, attempts=1):
    last = None
    for i in range(attempts):
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
        log("tx", fn=fn, attempt=i + 1, tx=tx, result_name=rn, exec=ex,
            payload=str(payload)[:400])
        last = (ok, payload)
        if ok:
            return last
    return last


def read(fn, args=None):
    return client.read_contract(address=ADDR, function_name=fn, args=args or [])


CRIT = json.dumps(["price under 50 GEN", "ships within 7 days"])


def good_page(key, extra=""):
    return ("<html>\n<body>\n<h1>MatchStake demo seller listing " + extra +
            "</h1>\n<p>Price: 35 GEN per mug. Ships within 5 business days "
            "worldwide.</p>\n<p>Payment in GEN. Returns within 14 days.</p>\n"
            "MatchStake-Listing-Key: " + key + "\n</body>\n</html>\n")


def page_no_marker(name):
    return ("<html>\n<body>\n<h1>" + name + " (phase 1: no marker)</h1>\n"
            "<p>Price: 35 GEN per mug. Ships within 5 business days.</p>\n"
            "</body>\n</html>\n")


def write_and_push(files, msg):
    for path, content in files.items():
        (ROOT / "testdata" / path).write_text(content)
    subprocess.run(["git", "-C", str(ROOT), "add", "testdata",
                    "scripts/deployment_log.json"], check=True)
    subprocess.run(["git", "-C", str(ROOT),
                    "-c", "user.email=faisal@hermes",
                    "-c", "user.name=faisalnugroho",
                    "commit", "-qm", msg], check=True)
    subprocess.run(["git", "-C", str(ROOT), "push", "-q", "origin", "main"],
                   check=True)


def wait_raw(path, needle, timeout=240):
    url = ("https://raw.githubusercontent.com/faisalnugroho/matchstake/main/"
           "testdata/" + path)
    start = time.time()
    while time.time() - start < timeout:
        try:
            body = urllib.request.urlopen(url, timeout=30).read().decode()
            if needle in body:
                return True
        except Exception:
            pass
        time.sleep(15)
    return False


def new_request(title):
    ok, p = wd("create_request", [title, CRIT, "7200"], value=10**17)
    assert ok, "create_request failed"
    return extract_id(p)


def submit(rid, path):
    url = ("https://raw.githubusercontent.com/faisalnugroho/matchstake/main/"
           "testdata/" + path)
    ok, p = wd("submit_offer", [rid, url, "smoke_v2"])
    assert ok, "submit_offer failed"
    oid = extract_id(p)
    offer = json.loads(read("get_offer", [oid]))
    return oid, offer["listing_key"]


def evaluate(oid):
    ok, p = wd("evaluate_offer", [oid])
    offer = json.loads(read("get_offer", [oid]))
    verdict = None
    try:
        ov = json.loads(offer.get("verdict", "") or "{}")
        verdict = ov.get("verdict")
    except Exception:
        pass
    if verdict is None and isinstance(p, dict):
        verdict = p.get("verdict")
    log("evaluated", oid=oid, ok=ok, verdict=verdict,
        offer_status=offer["status"],
        offer_verdict=str(offer.get("verdict", ""))[:500])
    return ok, verdict, offer


def run():
    # --- prep: two positive rounds + one fail-safe round ----------------
    r1 = new_request("v2 smoke determinism 1")
    o1, k1 = submit(r1, "v2_listing1.html")
    r2 = new_request("v2 smoke determinism 2")
    o2, k2 = submit(r2, "v2_listing2.html")
    r3 = new_request("v2 smoke fail-safe + recovery")
    o3, k3 = submit(r3, "v2_listing3.html")
    log("prep_done", o1=o1, k1=k1, o2=o2, k2=k2, o3=o3, k3=k3)

    write_and_push({
        "v2_listing1.html": good_page(k1, "round 1"),
        "v2_listing2.html": good_page(k2, "round 2"),
        "v2_listing3.html": page_no_marker("round 3"),
    }, "v2 smoke: listing pages (1&2 markers, 3 no-marker)")

    for pth, needle in (("v2_listing1.html", k1), ("v2_listing2.html", k2),
                        ("v2_listing3.html", "no marker")):
        assert wait_raw(pth, needle), "raw URL did not sync: " + pth
    log("raw_urls_synced")

    # --- positive rounds (determinism x2) -------------------------------
    ok, v, _ = evaluate(o1)
    assert ok and v == "MATCH", "round1 expected MATCH got " + str(v)
    ok, v, _ = evaluate(o2)
    assert ok and v == "MATCH", "round2 expected MATCH got " + str(v)

    # --- fail-safe: no marker -> INCONCLUSIVE ---------------------------
    ok, v, o3r = evaluate(o3)
    assert ok and v == "INCONCLUSIVE", "round3 expected INCONCLUSIVE got " + str(v)
    req3 = json.loads(read("get_request", [r3]))
    claim3 = json.loads(read("get_claimable", [o3r["seller"]]))
    log("failsafe_state", request_status=req3["status"],
        seller_claimable=claim3["amount"])
    assert req3["status"] == "OPEN"

    # --- recovery: add marker to same page, re-evaluate SAME offer ------
    write_and_push({"v2_listing3.html": good_page(k3, "round 3 recovered")},
                   "v2 smoke: listing3 phase2 marker present")
    assert wait_raw("v2_listing3.html", k3), "raw URL did not sync listing3"
    ok, v, _ = evaluate(o3)
    assert ok and v == "MATCH", "recovery expected MATCH got " + str(v)

    # --- claim: withdraw credited funds ----------------------------------
    ok, p = wd("claim", [])
    if ok:
        log("claim_ok", payload=str(p)[:200])
        seller = json.loads(read("get_offer", [o1]))["seller"]
        log("claim_state", seller_claimable=json.loads(
            read("get_claimable", [seller]))["amount"])

    stats = json.loads(read("get_stats"))
    log("final_stats_v2", **stats)
    print("SMOKE_V2_DONE", flush=True)


if __name__ == "__main__":
    run()
