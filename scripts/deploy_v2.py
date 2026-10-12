#!/usr/bin/env python3
"""Deploy MatchStake v2 (full consensus) and record address + proof."""
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "matchstake.py"
KEYFILE = json.loads((ROOT / "scripts" / ".deployer_key.json").read_text())
LOG = ROOT / "scripts" / "deployment_log.json"

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

EVID = json.loads(LOG.read_text()) if LOG.exists() else []


def log(ev, **kw):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": ev, **kw}
    EVID.append(rec)
    LOG.write_text(json.dumps(EVID, indent=2))
    print(json.dumps(rec, ensure_ascii=False))


account = create_account(account_private_key=KEYFILE["private_key"])
client = create_client(chain=studionet, account=account)
code = CONTRACT.read_text()
sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
log("v2_deploy_start", code_sha256=sha)

tx = client.deploy_contract(code=code, account=account, args=[],
                            leader_only=False)
receipt = client.wait_for_transaction_receipt(
    transaction_hash=tx, status=TransactionStatus.FINALIZED,
    full_transaction=True, interval=5000, retries=60)
addr = (receipt.get("data", {}) or {}).get("contract_address") or \
    receipt.get("to_address")
rn = receipt.get("result_name")
exec_res = ((receipt.get("consensus_data", {}).get("leader_receipt")
             or [{}])[0].get("execution_result"))
log("v2_deploy_ok", address=addr, tx=tx, result_name=rn, exec=exec_res,
    code_sha256=sha)
if rn != "MAJORITY_AGREE" or exec_res not in ("SUCCESS", "FINISHED_WITH_RETURN"):
    sys.exit(1)
STATE = ROOT / "scripts" / "deployed_studionet_v2.json"
STATE.write_text(json.dumps({
    "address": addr, "deploy_tx": tx, "code_sha256": sha,
    "leader_only": False, "contract": "contracts/matchstake.py",
    "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}, indent=2))
print("ADDRESS=" + str(addr))
