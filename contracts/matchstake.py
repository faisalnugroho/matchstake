# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from genlayer import *


class MatchStake(gl.Contract):
    """MatchStake — trustless buyer-seller matching marketplace.

    A buyer opens a REQUEST describing what they want to buy as explicit,
    machine-checkable criteria plus a GEN deposit. A seller submits an OFFER
    pointing at a public listing URL they control. The listing page must
    contain a signed marker line (the listing key) proving the offer belongs
    to the seller's public listing.

    Matching is adjudicated by AI validators: each validator independently
    fetches the listing URL and asks its LLM to label every criterion
    PASS / FAIL / UNCERTAIN with cited evidence from the fetched page. The
    CONTRACT — never the model — derives the verdict:

        any FAIL            -> NO_MATCH
        any UNCERTAIN       -> INCONCLUSIVE
        all PASS            -> MATCH

    Consequences (money movement only in the deterministic layer):
        MATCH        -> buyer deposit is earmarked to the seller (claimable)
        NO_MATCH     -> buyer's deposit is returned to their claimable pot
        INCONCLUSIVE -> nobody is paid; a re-evaluation may be attempted
                        until the request's deadline passes, after which the
                        deposit returns to the buyer.

    Failed/absent listings or LLM failures map to INCONCLUSIVE — never to a
    payout or a forfeit. INCONCLUSIVE records stay in history and may be
    re-evaluated (fresh evidence or a changed listing) before the deadline.

    Deadline model: open_deadline = request creation node-time + open_seconds.
    Re-evaluation and claiming are blocked after the deadline; deadline expiry
    unlocks a permissionless `expire_request` that returns the deposit to the
    buyer (recovery path — no stuck capital).
    """

    # Uniform TreeMap[str, str] storage (GenVM best practice; JSON values).
    requests: TreeMap[str, str]
    offers: TreeMap[str, str]  # offer_id -> JSON (offer record)
    claimable: TreeMap[str, str]  # address -> JSON {"amount": "..."}
    # owner-address -> count of open requests (DoR guard; see ToolGuard lesson)
    open_requests: TreeMap[str, str]
    stats: str  # JSON counters
    owner: str
    next_request_id: str
    next_offer_id: str

    MAX_OPEN_PER_OWNER: str
    OPEN_SECONDS_MIN: str

    def __init__(self) -> None:
        self.requests = TreeMap()
        self.offers = TreeMap()
        self.claimable = TreeMap()
        self.open_requests = TreeMap()
        self.stats = json.dumps({
            "requests_created": "0",
            "offers_submitted": "0",
            "matches": "0",
            "no_matches": "0",
            "inconclusives": "0",
            "expired": "0",
            "paid_out": "0",
        })
        self.owner = self._addr_str(gl.message.sender_address)
        self.next_request_id = "1"
        self.next_offer_id = "1"
        self.MAX_OPEN_PER_OWNER = "10"
        self.OPEN_SECONDS_MIN = "300"

    # ------------------------------------------------------------------ #
    # views

    @gl.public.view
    def get_stats(self) -> str:
        return self.stats

    @gl.public.view
    def get_request(self, request_id: str) -> str:
        return self._get(self.requests, request_id, "request")

    @gl.public.view
    def get_offer(self, offer_id: str) -> str:
        return self._get(self.offers, offer_id, "offer")

    @gl.public.view
    def get_claimable(self, account: str) -> str:
        return self.claimable.get(self._addr_str(account), json.dumps({"amount": "0"}))

    @gl.public.view
    def get_open_request_count(self, account: str) -> str:
        return self.open_requests.get(self._addr_str(account), "0")

    # ------------------------------------------------------------------ #
    # buyer side

    @gl.public.write.payable
    def create_request(
        self,
        title: str,
        criteria: str,
        open_seconds: str,
    ) -> str:
        """Buyer opens a request with a GEN deposit (msg.value).

        criteria: JSON array of short, objectively-checkable criterion
        strings, e.g. '["price under 50 GEN", "ships within 7 days"]'.
        """
        value = int(gl.message.value)
        assert value > 0, "deposit_required"
        seconds = int(open_seconds)
        assert seconds >= int(self.OPEN_SECONDS_MIN), "open_period_too_short"
        self._assert_criteria_shape(criteria)

        addr = self._addr_str(gl.message.sender_address)
        open_count = int(self.open_requests.get(addr, "0"))
        assert open_count < int(self.MAX_OPEN_PER_OWNER), "too_many_open_requests"

        req_id = self.next_request_id
        self.next_request_id = str(int(req_id) + 1)
        now = self._now()
        self.requests[req_id] = json.dumps({
            "id": req_id,
            "buyer": addr,
            "title": title[:120],
            "criteria": self._normalize_criteria(criteria),
            "deposit": str(value),
            "status": "OPEN",
            "verdict": "",
            "created_at": str(now),
            "deadline": str(now + seconds),
            "winner_offer": "",
            "offers": "",
        })
        self.open_requests[addr] = str(open_count + 1)
        self._bump("requests_created")
        return req_id

    @gl.public.write
    def expire_request(self, request_id: str) -> str:
        """Permissionless recovery: after the deadline an OPEN request's
        deposit returns to the buyer. Guarantees no stuck capital."""
        req = self._load_request(request_id)
        assert req["status"] == "OPEN", "not_open"
        assert self._now() > int(req["deadline"]), "deadline_not_reached"
        return self._finalize(req, "EXPIRED", "", "")

    # ------------------------------------------------------------------ #
    # seller side

    @gl.public.write
    def submit_offer(self, request_id: str, listing_url: str, note: str) -> str:
        """Seller submits an offer against an OPEN request.

        listing_url MUST be a public page controlled by the seller that
        contains the marker line: MatchStake-Listing-Key: <listing_key>
        where <listing_key> is returned by this call and must appear in the
        page before evaluation. The key binds the offer to a re-fetchable,
        independently verifiable listing.
        """
        req = self._load_request(request_id)
        assert req["status"] == "OPEN", "request_not_open"
        assert self._now() <= int(req["deadline"]), "deadline_passed"
        assert listing_url.startswith("https://"), "listing_url_must_be_https"

        seller = self._addr_str(gl.message.sender_address)
        existing = [oid for oid in req.get("offers", "").split(",") if oid]
        for oid in existing:
            offer = json.loads(self.offers[oid])
            assert offer["seller"] != seller, "duplicate_seller_offer"

        offer_id = self.next_offer_id
        self.next_offer_id = str(int(offer_id) + 1)
        listing_key = "MSK-" + offer_id + "-" + str(self._now())
        self.offers[offer_id] = json.dumps({
            "id": offer_id,
            "request_id": request_id,
            "seller": seller,
            "listing_url": listing_url[:300],
            "listing_key": listing_key,
            "note": note[:300],
            "status": "PENDING",
            "verdict": "",
            "created_at": str(self._now()),
        })
        self._bump("offers_submitted")
        req["offers"] = (req.get("offers", "") + "," + offer_id).lstrip(",")
        self.requests[request_id] = json.dumps(req)
        return offer_id

    # ------------------------------------------------------------------ #
    # evaluation (LLM consensus)

    @gl.public.write
    def evaluate_offer(self, offer_id: str) -> str:
        """Run AI-validator matching on an offer. Anyone may trigger it.

        Deterministic verdict derivation (contract, not model):
          any criterion FAIL        -> NO_MATCH
          any UNCERTAIN / no signal -> INCONCLUSIVE
          all PASS                  -> MATCH
        Every LLM / fetch failure path lands in INCONCLUSIVE and moves no
        money. MATCH earmarks the deposit to the seller; NO_MATCH returns it
        to the buyer's claimable pot.

        An INCONCLUSIVE offer may be re-evaluated (fresh page content, e.g.
        the seller added the required marker) until the request deadline;
        this is the documented recovery path. Terminal states (MATCHED,
        REJECTED) can never be re-evaluated.
        """
        offer = json.loads(self._get(self.offers, offer_id, "offer"))
        assert offer["status"] in ("PENDING", "INCONCLUSIVE"), "offer_not_pending"
        req = self._load_request(offer["request_id"])
        assert req["status"] == "OPEN", "request_not_open"
        assert self._now() <= int(req["deadline"]), "deadline_passed"
        listing_key = offer["listing_key"]
        criteria = json.loads(req["criteria"])
        url = offer["listing_url"]

        def leader_fn():
            fetch_failed = False
            page = ""
            try:
                resp = gl.nondet.web.get(url)
                page = resp.body.decode("utf-8", "replace")[:20000]
            except Exception:
                fetch_failed = True

            marker = "MatchStake-Listing-Key: " + listing_key
            key_ok = (not fetch_failed) and (marker in page)

            labels = []
            if key_ok:
                prompt = (
                    "You are a neutral marketplace matching judge. The page "
                    "below is a seller's public listing. For EACH criterion, "
                    "decide from the page text alone whether the listing "
                    "satisfies it. Be strict: only label PASS when the page "
                    "text explicitly supports it; if the page does not say, "
                    "label UNCERTAIN.\n\n"
                    "Return ONLY valid JSON, no prose:\n"
                    '{"labels": [{"criterion": <index as integer>, '
                    '"status": "PASS" or "FAIL" or "UNCERTAIN", '
                    '"evidence": "<short quote from the page>"}]}\n\n'
                    "CRITERIA:\n"
                )
                for i, c in enumerate(criteria):
                    prompt += str(i) + ". " + c + "\n"
                prompt += "\nPAGE CONTENT:\n" + page[:12000]

                try:
                    raw = gl.nondet.exec_prompt(prompt, response_format="json")
                    parsed = self._parse_labels(raw, len(criteria))
                    labels = parsed
                except Exception:
                    labels = []
            return {"key_ok": key_ok, "labels": labels}

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            mine = leader_fn()
            theirs = leader_res.calldata
            # Compare only stable decision substance: the marker check and
            # the per-criterion STATUS vector (evidence wording varies by
            # model and must never be compared).
            if theirs.get("key_ok") != mine["key_ok"]:
                return False
            return self._label_vector(theirs.get("labels", []), len(criteria)) == \
                self._label_vector(mine["labels"], len(criteria))

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        labels = result.get("labels", [])
        verdict = self._derive_verdict(result["key_ok"], labels, len(criteria))
        return self._settle(req, offer, verdict, labels)

    # ------------------------------------------------------------------ #
    # settlement (deterministic layer — money moves only here)

    def _settle(self, req: dict, offer: dict, verdict: str, labels: list) -> str:
        if verdict == "INCONCLUSIVE":
            offer["status"] = "INCONCLUSIVE"
            offer["verdict"] = self._verdict_json(verdict, labels)
            self.offers[offer["id"]] = json.dumps(offer)
            self._bump("inconclusives")
            return json.dumps({"verdict": verdict, "deposit_moved": False})

        if verdict == "MATCH":
            offer["status"] = "MATCHED"
            offer["verdict"] = self._verdict_json(verdict, labels)
            self.offers[offer["id"]] = json.dumps(offer)
            res = json.loads(self._finalize(req, "MATCHED", offer["id"], offer["seller"]))
            return json.dumps({
                "verdict": verdict,
                "deposit_moved": True,
                "request_id": res["request_id"],
                "status": res["status"],
                "deposit_to": res["deposit_to"],
            })

        # NO_MATCH: refund the deposit to the buyer's claimable pot.
        offer["status"] = "REJECTED"
        offer["verdict"] = self._verdict_json(verdict, labels)
        self.offers[offer["id"]] = json.dumps(offer)
        self._bump("no_matches")
        res = json.loads(self._finalize(req, "REFUNDED", "", str(req["buyer"])))
        return json.dumps({
            "verdict": verdict,
            "deposit_moved": True,
            "request_id": res["request_id"],
            "status": res["status"],
            "deposit_to": res["deposit_to"],
        })

    def _finalize(self, req: dict, status: str, winner_offer: str, seller: str) -> str:
        """Terminal transition for a request; moves the deposit exactly once."""
        req["status"] = status
        req["winner_offer"] = winner_offer
        self.requests[req["id"]] = json.dumps(req)

        addr = str(req["buyer"])
        open_count = int(self.open_requests.get(addr, "0"))
        if open_count > 0:
            self.open_requests[addr] = str(open_count - 1)

        if status == "MATCHED":
            self._credit(seller, req["deposit"])
            self._bump("matches")
            s = json.loads(self.stats)
            s["paid_out"] = str(int(s["paid_out"]) + int(req["deposit"]))
            self.stats = json.dumps(s)
        else:  # EXPIRED or (unused) NO_MATCH path
            self._credit(addr, req["deposit"])
            if status == "EXPIRED":
                self._bump("expired")
        return json.dumps({
            "request_id": req["id"],
            "status": status,
            "verdict": status if status in ("MATCHED", "EXPIRED") else status,
            "winner_offer": winner_offer,
            "deposit_to": seller if status == "MATCHED" else addr,
        })

    @gl.public.write
    def claim(self) -> str:
        """Withdraw credited funds. Blocked past a request deadline is NOT
        enforced here: credits are final allocations, always claimable."""
        addr = self._addr_str(gl.message.sender_address)
        rec = json.loads(self.claimable.get(addr, '{"amount": "0"}'))
        amount = int(rec.get("amount", "0"))
        assert amount > 0, "nothing_to_claim"
        self.claimable[addr] = json.dumps({"amount": "0"})
        gl.get_contract_at(gl.message.contract_address).emit_transfer(
            value=u256(amount), on="finalized"
        )
        return json.dumps({"claimed": str(amount)})

    # ------------------------------------------------------------------ #
    # helpers

    def _addr_str(self, a) -> str:
        """Deterministic lowercase-hex address string (str(Address) is NOT
        stable across environments — bytes repr in some SDK versions)."""
        if isinstance(a, str):
            return a.lower()
        b = a.as_bytes if hasattr(a, "as_bytes") else bytes(a)
        return "0x" + "".join("%02x" % x for x in b)

    def _now(self) -> int:
        return self._parse_iso(gl.message_raw["datetime"])

    def _parse_iso(self, iso: str) -> int:
        # Pure integer math (Howard Hinnant days_from_civil); no datetime
        # module, no floats — validator-deterministic.
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1]
        date_part = s.split("T")[0]
        y = int(date_part[0:4])
        m = int(date_part[5:7])
        d = int(date_part[8:10])
        if m <= 2:
            y -= 1
            m += 12
        era = (y if y >= 0 else y - 399) // 400
        yoe = y - era * 400
        doy = (153 * (m - 3) + 2) // 5 + d - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        days = era * 146097 + doe - 719468
        t = s.split("T")[1] if "T" in s else "00:00:00"
        parts = t.split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        ss = int(float(parts[2])) if len(parts) > 2 else 0
        return days * 86400 + hh * 3600 + mm * 60 + ss

    def _assert_criteria_shape(self, criteria: str) -> None:
        parsed = json.loads(criteria)
        assert isinstance(parsed, list), "criteria_must_be_array"
        assert 1 <= len(parsed) <= 8, "criteria_count_1_to_8"
        for c in parsed:
            assert isinstance(c, str) and 0 < len(c.strip()) <= 200, "bad_criterion"

    def _normalize_criteria(self, criteria: str) -> str:
        return json.dumps([c.strip()[:200] for c in json.loads(criteria)])

    def _parse_labels(self, raw, n: int) -> list:
        """Parse the model's label JSON. Any malformed/missing/out-of-range
        entry becomes UNCERTAIN for that criterion (fail-safe)."""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        if isinstance(raw, dict):
            data = raw
        else:
            text = str(raw).strip()
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return self._all_uncertain(n)
            try:
                data = json.loads(text[start:end + 1])
            except Exception:
                return self._all_uncertain(n)
        out = self._all_uncertain(n)
        labels = data.get("labels")
        if not isinstance(labels, list):
            return out
        for item in labels:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("criterion"))
            except Exception:
                continue
            if idx < 0 or idx >= n:
                continue
            status = str(item.get("status", "")).upper()
            if status in ("PASS", "FAIL", "UNCERTAIN"):
                out[idx] = {"criterion": idx, "status": status,
                            "evidence": str(item.get("evidence", ""))[:200]}
        return out

    def _all_uncertain(self, n: int) -> list:
        return [{"criterion": i, "status": "UNCERTAIN", "evidence": ""}
                for i in range(n)]

    def _label_vector(self, labels, n: int) -> str:
        vec = self._all_uncertain(n)
        for item in (labels or []):
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("criterion"))
            except Exception:
                continue
            if 0 <= idx < n:
                vec[idx] = {"criterion": idx,
                            "status": str(item.get("status", "UNCERTAIN")).upper(),
                            "evidence": ""}
        return ",".join(v["status"] for v in vec)

    def _derive_verdict(self, key_ok: bool, labels, n: int) -> str:
        if not key_ok:
            return "INCONCLUSIVE"
        vector = self._label_vector(labels, n).split(",")
        if "FAIL" in vector:
            return "NO_MATCH"
        if "UNCERTAIN" in vector:
            return "INCONCLUSIVE"
        return "MATCH"

    def _verdict_json(self, verdict: str, labels: list) -> str:
        return json.dumps({"verdict": verdict, "labels": labels})

    def _load_request(self, request_id: str) -> dict:
        return json.loads(self._get(self.requests, request_id, "request"))

    def _credit(self, account: str, amount: str) -> None:
        rec = json.loads(self.claimable.get(account, '{"amount": "0"}'))
        rec["amount"] = str(int(rec.get("amount", "0")) + int(amount))
        self.claimable[account] = json.dumps(rec)

    def _bump(self, key: str) -> None:
        s = json.loads(self.stats)
        s[key] = str(int(s[key]) + 1)
        self.stats = json.dumps(s)

    def _get(self, map_, key: str, kind: str) -> str:
        val = map_.get(key, "")
        assert val != "", "no_such_" + kind
        return val
