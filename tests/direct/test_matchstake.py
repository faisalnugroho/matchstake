import json
import re
import pytest

BUYER_CRITERIA = json.dumps([
    "price under 50 GEN",
    "ships within 7 days",
])

GOOD_PAGE = """
<html><body>
<h1>Handmade ceramic mug shop</h1>
<p>Price: 35 GEN per mug. Ships within 5 business days worldwide.</p>
MatchStake-Listing-Key: {key}
</body></html>
"""

BAD_PAGE = """
<html><body>
<h1>Handmade ceramic mug shop</h1>
<p>Price: 80 GEN per mug. Shipping time unspecified.</p>
MatchStake-Listing-Key: {key}
</body></html>
"""

PASS_JSON = json.dumps({"labels": [
    {"criterion": 0, "status": "PASS", "evidence": "Price: 35 GEN"},
    {"criterion": 1, "status": "PASS", "evidence": "Ships within 5 business days"},
]})
FAIL_JSON = json.dumps({"labels": [
    {"criterion": 0, "status": "FAIL", "evidence": "Price: 80 GEN"},
    {"criterion": 1, "status": "UNCERTAIN", "evidence": ""},
]})
PARTIAL_JSON = json.dumps({"labels": [
    {"criterion": 0, "status": "PASS", "evidence": "Price: 35 GEN"},
]})


def make_llm(pattern, response):
    return (re.compile(".*" + re.escape(pattern) + ".*", re.S), response)


@pytest.fixture()
def setup(direct_vm, direct_deploy, direct_owner):
    vm = direct_vm
    contract = direct_deploy("contracts/matchstake.py")
    return vm, contract


def _create_request(vm, contract, deposit_wei=10**18):
    vm.value = deposit_wei
    try:
        return contract.create_request("Ceramic mugs", BUYER_CRITERIA, "3600")
    finally:
        vm.value = 0


def test_deploy_and_views(setup):
    vm, contract = setup
    stats = json.loads(contract.get_stats())
    assert stats["requests_created"] == "0"
    assert contract.get_claimable(str(direct_owner_addr(vm))) == json.dumps({"amount": "0"})


def addr_lower(a):
    if isinstance(a, bytes):
        return "0x" + a.hex()
    return a.as_hex.lower()


def direct_owner_addr(vm):
    return addr_lower(vm.sender)


def test_create_request_happy(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "OPEN"
    assert req["deposit"] == str(10**18)
    assert json.loads(req["criteria"]) == json.loads(BUYER_CRITERIA)
    assert int(req["deadline"]) > int(req["created_at"])
    assert contract.get_open_request_count(direct_owner_addr(vm)) == "1"
    stats = json.loads(contract.get_stats())
    assert stats["requests_created"] == "1"


def test_create_request_guards(setup):
    vm, contract = setup
    with pytest.raises(Exception, match="deposit_required"):
        contract.create_request("t", BUYER_CRITERIA, "3600")
    vm.value = 10**18
    try:
        with pytest.raises(Exception, match="open_period_too_short"):
            contract.create_request("t", BUYER_CRITERIA, "60")
        with pytest.raises(Exception, match="criteria_must_be_array"):
            contract.create_request("t", json.dumps("x"), "3600")
        with pytest.raises(Exception, match="criteria_count_1_to_8"):
            contract.create_request("t", json.dumps(["c"] * 9), "3600")
    finally:
        vm.value = 0


def test_max_open_per_owner(setup):
    vm, contract = setup
    for _ in range(10):
        _create_request(vm, contract)
    with pytest.raises(Exception, match="too_many_open_requests"):
        _create_request(vm, contract)


def test_submit_offer_and_key(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "fast ship")
    offer = json.loads(contract.get_offer(oid))
    assert offer["status"] == "PENDING"
    assert offer["listing_key"].startswith("MSK-")
    req = json.loads(contract.get_request(rid))
    assert oid in req["offers"].split(",")


def test_duplicate_seller_rejected(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    contract.submit_offer(rid, "https://a.example.com/x", "")
    with pytest.raises(Exception, match="duplicate_seller_offer"):
        contract.submit_offer(rid, "https://b.example.com/y", "")


def test_offer_guards(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    with pytest.raises(Exception, match="listing_url_must_be_https"):
        contract.submit_offer(rid, "http://a.example.com/x", "")
    contract.submit_offer(rid, "https://a.example.com/x", "")
    with pytest.raises(Exception, match="no_such_offer"):
        contract.get_offer("999")


def test_evaluate_match_full_flow(setup):
    """All criteria PASS -> MATCH, deposit credited to seller."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    offer = json.loads(contract.get_offer(oid))
    key = offer["listing_key"]

    page = GOOD_PAGE.format(key=key)
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": page})
    vm.mock_llm(*make_llm(key, PASS_JSON))

    res = json.loads(contract.evaluate_offer(oid))
    assert res["verdict"] == "MATCH"
    offer = json.loads(contract.get_offer(oid))
    assert offer["status"] == "MATCHED"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "MATCHED"
    assert req["winner_offer"] == oid
    # deposit credited to the seller
    seller = offer["seller"]
    assert json.loads(contract.get_claimable(seller))["amount"] == str(10**18)
    assert contract.get_open_request_count(req["buyer"]) == "0"
    stats = json.loads(contract.get_stats())
    assert stats["matches"] == "1" and stats["paid_out"] == str(10**18)


def test_evaluate_no_match_rejects_offer_only(setup):
    """Any FAIL -> NO_MATCH rejects ONLY that offer. The request stays OPEN
    with its deposit escrowed — one seller's failure can never terminate
    the buyer's request."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]

    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": BAD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, FAIL_JSON))

    res = json.loads(contract.evaluate_offer(oid))
    assert res["verdict"] == "NO_MATCH"
    assert res["deposit_moved"] is False
    offer = json.loads(contract.get_offer(oid))
    assert offer["status"] == "REJECTED"
    req = json.loads(contract.get_request(rid))
    # request remains OPEN; nothing refunded
    assert req["status"] == "OPEN"
    buyer = req["buyer"]
    assert json.loads(contract.get_claimable(buyer))["amount"] == "0"
    assert contract.get_open_request_count(buyer) == "1"
    stats = json.loads(contract.get_stats())
    assert stats["no_matches"] == "1"


def test_no_match_does_not_block_other_sellers(setup):
    """Seller 1 gets NO_MATCH; seller 2 can still submit and MATCH — the
    buyer's request survives an individual seller's failure."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    o1 = contract.submit_offer(rid, "https://s1.example.com/p", "")
    k1 = json.loads(contract.get_offer(o1))["listing_key"]
    vm.mock_web(r"https://s1\.example\.com/p", {"status": 200, "body": BAD_PAGE.format(key=k1)})
    vm.mock_llm(*make_llm(k1, FAIL_JSON))
    assert json.loads(contract.evaluate_offer(o1))["verdict"] == "NO_MATCH"

    # request still open: a second seller can offer and win
    assert json.loads(contract.get_request(rid))["status"] == "OPEN"
    from gltest.direct.loader import create_address
    orig = vm.sender
    vm.sender = create_address("seller2")
    try:
        o2 = contract.submit_offer(rid, "https://s2.example.com/p", "")
    finally:
        vm.sender = orig
    k2 = json.loads(contract.get_offer(o2))["listing_key"]
    vm.clear_mocks()
    vm.mock_web(r"https://s2\.example\.com/p", {"status": 200, "body": GOOD_PAGE.format(key=k2)})
    vm.mock_llm(*make_llm(k2, PASS_JSON))
    res = json.loads(contract.evaluate_offer(o2))
    assert res["verdict"] == "MATCH"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "MATCHED"
    assert req["winner_offer"] == o2
    seller2 = json.loads(contract.get_offer(o2))["seller"]
    assert json.loads(contract.get_claimable(seller2))["amount"] == str(10**18)


def test_create_request_payable_deposit_is_msg_value(setup):
    """Source-level payable path: the deposit MUST arrive as tx value.
    Zero value reverts (deposit_required); the recorded deposit equals
    exactly the value sent with the call."""
    vm, contract = setup
    with pytest.raises(Exception, match="deposit_required"):
        contract.create_request("t", BUYER_CRITERIA, "3600")
    rid = _create_request(vm, contract, deposit_wei=777)
    req = json.loads(contract.get_request(rid))
    assert req["deposit"] == "777"          # deposit == msg.value, verbatim
    # the escrowed deposit is the amount later recoverable/settleable
    vm, contract = setup
    rid2 = _create_request(vm, contract)
    contract.cancel_request(rid2)
    assert json.loads(contract.get_claimable(direct_owner_addr(vm)))["amount"] == str(10**18)


def test_cancel_request_buyer_reclaims_deposit(setup):
    """Buyer-only early recovery while OPEN: non-terminal offers are voided
    and the deposit becomes claimable — not recoverable only by expiry."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    # a PENDING offer and an INCONCLUSIVE offer both exist (distinct sellers)
    from gltest.direct.loader import create_address
    orig = vm.sender
    o1 = contract.submit_offer(rid, "https://s1.example.com/p", "")
    vm.sender = create_address("seller2")
    try:
        o2 = contract.submit_offer(rid, "https://s2.example.com/p", "")
    finally:
        vm.sender = orig
    k2 = json.loads(contract.get_offer(o2))["listing_key"]
    vm.mock_web(r"https://s2\.example\.com/p", {"status": 200, "body": "<html>no marker</html>"})
    vm.mock_llm(*make_llm(k2, PASS_JSON))
    assert json.loads(contract.evaluate_offer(o2))["verdict"] == "INCONCLUSIVE"

    res = json.loads(contract.cancel_request(rid))
    assert res["status"] == "CANCELLED"
    assert json.loads(contract.get_offer(o1))["status"] == "VOIDED"
    assert json.loads(contract.get_offer(o2))["status"] == "VOIDED"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "CANCELLED"
    assert json.loads(contract.get_claimable(req["buyer"]))["amount"] == str(10**18)
    assert contract.get_open_request_count(req["buyer"]) == "0"
    stats = json.loads(contract.get_stats())
    assert stats["cancelled"] == "1"
    # voided offers can never be evaluated afterwards
    with pytest.raises(Exception, match="offer_not_pending"):
        contract.evaluate_offer(o1)
    with pytest.raises(Exception, match="request_not_open"):
        contract.submit_offer(rid, "https://s3.example.com/p", "")


def test_cancel_request_only_buyer(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    from gltest.direct.loader import create_address
    orig = vm.sender
    vm.sender = create_address("mallory")
    try:
        with pytest.raises(Exception, match="only_buyer"):
            contract.cancel_request(rid)
    finally:
        vm.sender = orig
    # non-buyer cancel leaves the request untouched
    assert json.loads(contract.get_request(rid))["status"] == "OPEN"


def test_cancel_before_deadline_not_expire_only(setup):
    """The deposit is recoverable BEFORE the deadline via cancel_request —
    it is not locked until expiry."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    # still far from deadline — cancel works immediately
    res = json.loads(contract.cancel_request(rid))
    assert res["status"] == "CANCELLED"
    assert json.loads(contract.get_claimable(direct_owner_addr(vm)))["amount"] == str(10**18)
    with pytest.raises(Exception, match="not_open"):
        contract.cancel_request(rid)


def test_evaluate_inconclusive_no_money_moves(setup):
    """Missing marker / malformed LLM output -> INCONCLUSIVE, nothing moves."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]

    # page WITHOUT the marker -> key_ok False -> INCONCLUSIVE
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": "<html>no marker here</html>"})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    res = json.loads(contract.evaluate_offer(oid))
    assert res["verdict"] == "INCONCLUSIVE"
    assert res["deposit_moved"] is False
    offer = json.loads(contract.get_offer(oid))
    assert offer["status"] == "INCONCLUSIVE"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "OPEN"
    assert json.loads(contract.get_claimable(req["buyer"]))["amount"] == "0"
    assert json.loads(contract.get_claimable(offer["seller"]))["amount"] == "0"


def test_evaluate_malformed_llm_fail_safe(setup):
    """Malformed model output -> all UNCERTAIN -> INCONCLUSIVE."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, "sorry I cannot answer in JSON"))
    res = json.loads(contract.evaluate_offer(oid))
    assert res["verdict"] == "INCONCLUSIVE"


def test_new_request_after_no_match_still_open_then_cancel(setup):
    """After a NO_MATCH the request is still OPEN (per-offer verdicts are
    final for that offer only); the buyer may cancel to reclaim early."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://s1.example.com/p", "")
    k1 = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://s1\.example\.com/p", {"status": 200, "body": BAD_PAGE.format(key=k1)})
    vm.mock_llm(*make_llm(k1, FAIL_JSON))
    assert json.loads(contract.evaluate_offer(oid))["verdict"] == "NO_MATCH"
    # request survived the seller's failure; rejected offer is terminal
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "OPEN"
    with pytest.raises(Exception, match="duplicate_seller_offer"):
        contract.submit_offer(rid, "https://s2.example.com/p", "")
    # buyer reclaims via cancel_request (early recovery, not expiry)
    res = json.loads(contract.cancel_request(rid))
    assert res["status"] == "CANCELLED"
    buyer = req["buyer"]
    assert json.loads(contract.get_claimable(buyer))["amount"] == str(10**18)


def test_expire_request_refunds_after_deadline(setup):
    """Recovery path: expired OPEN request returns deposit to buyer."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    # warp past deadline: mock message_raw datetime
    import sys
    import types
    gl_mod = sys.modules["genlayer.gl"]
    req = json.loads(contract.get_request(rid))
    future = _iso_after(int(req["deadline"]) + 10)
    old = gl_mod.message_raw["datetime"]
    gl_mod.message_raw["datetime"] = future
    try:
        res = json.loads(contract.expire_request(rid))
    finally:
        gl_mod.message_raw["datetime"] = old
    assert res["status"] == "EXPIRED"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "EXPIRED"
    assert json.loads(contract.get_claimable(req["buyer"]))["amount"] == str(10**18)
    assert contract.get_open_request_count(req["buyer"]) == "0"
    stats = json.loads(contract.get_stats())
    assert stats["expired"] == "1"


def test_expire_guard_before_deadline(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    with pytest.raises(Exception, match="deadline_not_reached"):
        contract.expire_request(rid)


def test_offer_after_deadline_rejected(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    import sys
    gl_mod = sys.modules["genlayer.gl"]
    req = json.loads(contract.get_request(rid))
    old = gl_mod.message_raw["datetime"]
    gl_mod.message_raw["datetime"] = _iso_after(int(req["deadline"]) + 10)
    try:
        with pytest.raises(Exception, match="deadline_passed"):
            contract.submit_offer(rid, "https://a.example.com/x", "")
    finally:
        gl_mod.message_raw["datetime"] = old


def test_evaluate_on_matched_request_rejected(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    contract.evaluate_offer(oid)
    with pytest.raises(Exception, match="offer_not_pending"):
        contract.evaluate_offer(oid)


def test_claim_after_match(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    contract.evaluate_offer(oid)
    seller = json.loads(contract.get_offer(oid))["seller"]
    # claim must be from the seller's address
    from gltest.direct.loader import create_address
    # find the address whose checksummed str equals seller: it was created in
    # submit_offer as the current sender; restore it via vm.sender rotation
    # (test uses direct_owner as seller here).
    vm.sender = _sender_with_str(vm, seller)
    res = json.loads(contract.claim())
    assert res["claimed"] == str(10**18)
    assert json.loads(contract.get_claimable(seller))["amount"] == "0"
    with pytest.raises(Exception, match="nothing_to_claim"):
        contract.claim()


def _sender_with_str(vm, seller_str):
    # The contract stores _addr_str(sender) = lowercase hex.
    assert addr_lower(vm.sender) == seller_str
    return vm.sender


def _iso_after(epoch: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000000Z"
    )


# ---------------------------------------------------------------------------
# Validator agreement (equivalence principle: status vector, never wording)
# ---------------------------------------------------------------------------

def test_validator_agrees_on_same_labels(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    contract.evaluate_offer(oid)
    assert vm.run_validator() is True


def test_validator_agrees_despite_different_evidence_wording(setup):
    """Same statuses, different evidence strings — must still AGREE."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    contract.evaluate_offer(oid)
    # validator's own re-run produces differently-worded evidence
    alt = json.dumps({"labels": [
        {"criterion": 0, "status": "PASS", "evidence": "the mug costs thirty-five"},
        {"criterion": 1, "status": "PASS", "evidence": "delivery in five days"},
    ]})
    vm.clear_mocks()
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, alt))
    assert vm.run_validator() is True


def test_validator_disagrees_on_divergent_statuses(setup):
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug", {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    contract.evaluate_offer(oid)
    vm.clear_mocks()
    vm.mock_llm(*make_llm(key, FAIL_JSON))
    assert vm.run_validator() is False


def test_inconclusive_offer_can_be_reevaluated_to_match(setup):
    """Recovery path: marker missing -> INCONCLUSIVE (no money moved); after
    the seller fixes the page, the SAME offer may be re-evaluated to MATCH."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]

    # phase 1: page without the marker
    vm.mock_web(r"https://shop\.example\.com/mug",
                {"status": 200, "body": "<html>no marker yet</html>"})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    assert json.loads(contract.evaluate_offer(oid))["verdict"] == "INCONCLUSIVE"
    assert json.loads(contract.get_offer(oid))["status"] == "INCONCLUSIVE"
    req = json.loads(contract.get_request(rid))
    assert req["status"] == "OPEN"
    assert json.loads(contract.get_claimable(req["buyer"]))["amount"] == "0"

    # phase 2: seller added the marker; re-evaluate the same offer
    vm.clear_mocks()
    vm.mock_web(r"https://shop\.example\.com/mug",
                {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    res = json.loads(contract.evaluate_offer(oid))
    assert res["verdict"] == "MATCH"
    offer = json.loads(contract.get_offer(oid))
    assert offer["status"] == "MATCHED"
    assert json.loads(contract.get_claimable(offer["seller"]))["amount"] == str(10**18)
    stats = json.loads(contract.get_stats())
    assert stats["inconclusives"] == "1" and stats["matches"] == "1"


def test_terminal_offer_cannot_be_reevaluated(setup):
    """A MATCHED offer can never be re-evaluated (terminal state)."""
    vm, contract = setup
    rid = _create_request(vm, contract)
    oid = contract.submit_offer(rid, "https://shop.example.com/mug", "")
    key = json.loads(contract.get_offer(oid))["listing_key"]
    vm.mock_web(r"https://shop\.example\.com/mug",
                {"status": 200, "body": GOOD_PAGE.format(key=key)})
    vm.mock_llm(*make_llm(key, PASS_JSON))
    assert json.loads(contract.evaluate_offer(oid))["verdict"] == "MATCH"
    with pytest.raises(Exception, match="offer_not_pending"):
        contract.evaluate_offer(oid)
