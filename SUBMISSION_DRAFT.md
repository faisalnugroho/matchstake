# MatchStake — Submission Draft

## Category
Builder → Intelligent Contracts (domain: Marketplaces / Buyer–Seller Matching)

## One-liner
AI-validator matching marketplace: buyers post requirements plus a GEN deposit, sellers answer with a verifiable public listing, and GenLayer consensus decides whether the offer actually satisfies every criterion — the contract, never the model, moves the money.

## Repo
https://github.com/faisalnugroho/matchstake

## Live contract (studionet)
`0x0D2095a3e98B472c66EfF63E7990d00005d503B3`
Deploy tx: `0xb08e8181ee951eb60dab0a3ad7696c1bdb6ae5901eadfc894f6f70234018c38d`
Deployed code sha256: `3db92854250f4a0622b2e7cb04190b53fa5578cb3f680e77a52e36b416ce8fc3`

## Full description

Marketplace matching is the classic pre-GenLayer bottleneck: deciding whether a seller's offer *actually satisfies* a buyer's stated requirements is evidence-based, natural-language judgment. Centralized platforms arbiter it and charge for it; a plain smart contract cannot do it at all.

MatchStake makes that judgment trustless.

**Flow**
1. `create_request(title, criteria, open_seconds)` — buyer opens a request with up to 8 explicit checkable criteria and a GEN deposit (payable).
2. `submit_offer(request_id, listing_url, note)` — seller submits a public https listing URL. The contract mints a unique listing key (`MSK-<offer>-<ts>`); the seller must publish the line `MatchStake-Listing-Key: <key>` on that page.
3. `evaluate_offer(offer_id)` — anyone triggers. Inside the equivalence block each validator independently fetches the listing page, verifies the marker key, then asks its LLM to label every criterion PASS / FAIL / UNCERTAIN with a quoted fragment from the page.
4. **The contract derives the verdict** (pure function of the labels — the model never picks the outcome):
   - any FAIL → `NO_MATCH` (ONLY that offer is rejected; the request stays OPEN with its deposit escrowed)
   - any UNCERTAIN / no signal → `INCONCLUSIVE` (nothing moves)
   - all PASS → `MATCH` (deposit credited to the seller)
5. `claim()` withdraws credited funds. Deposit recovery — never stuck, never only-by-expiry:
   - `cancel_request(request_id)` — buyer-only, ANY time while the request is OPEN: non-terminal offers are voided and the deposit returns to the buyer's claimable pot.
   - `expire_request(request_id)` — permissionless, after the deadline: same outcome.

**Why the LLM use is meaningful**
- Multi-step: fetch → verify marker → assess each criterion against page text → cite evidence. Not one-shot classification.
- Evidence is public and re-fetchable; every validator independently fetches the same boring static page.
- Real on-chain consequence: the deposit moves only on a deterministic verdict.
- Every failure mode (dead URL, missing marker, malformed model output, fetch exception) maps to a well-formed `INCONCLUSIVE` that never moves money — fail-safe by construction.
- Equivalence compares only stable decision substance: the marker check plus the per-criterion **status vector**. Evidence wording varies between models and is never compared.

**Recovery / capacity design**
- `INCONCLUSIVE` offers may be re-evaluated (e.g. the seller published the marker) until the deadline — proven live. This is the documented retry path.
- Terminal states (`MATCHED`, `REJECTED`, `VOIDED`) can never be re-evaluated.
- A `NO_MATCH` verdict is terminal for THAT OFFER only — one seller's failure can never terminate the buyer's request; other sellers may still offer and win on the same request.
- `cancel_request` (buyer, anytime while OPEN) + `expire_request` (permissionless, after deadline) guarantee the deposit is never stuck and never recoverable only by expiry.
- The per-owner open-request cap (10) is paired with those release paths.

## Test evidence — 27/27 direct-mode tests

```
$ PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/direct -q -p no:cacheprovider
27 passed
```

Covers: happy MATCH settlement, NO_MATCH offer-only rejection, NO_MATCH-does-not-block-other-sellers,
payable deposit path (deposit == msg.value; zero value reverts), INCONCLUSIVE no-movement,
malformed-LLM fail-safe, missing-marker fail-safe, recovery re-evaluation, terminal-state guard,
all input guards, deadline expiry refund, buyer cancel & reclaim (incl. only-buyer guard),
claim, and three validator-agreement cases (agree on identical labels,
agree with differently-worded evidence, disagree on divergent statuses).

`genvm-lint check contracts/matchstake.py` → ✓ Lint passed (3 checks), ✓ Validation passed.

## Live smoke evidence (studionet, full consensus, v2 deployment)

Deployed: `0x0D2095a3e98B472c66EfF63E7990d00005d503B3`
Deploy tx `0xb08e8181ee951eb60dab0a3ad7696c1bdb6ae5901eadfc894f6f70234018c38d` (MAJORITY_AGREE, SUCCESS)
Deployed code sha256 == `git show HEAD:contracts/matchstake.py | sha256sum` = `3db92854250f4a0622b2e7cb04190b53fa5578cb3f680e77a52e36b416ce8fc3`

| Scenario | Tx (studionet) | Observed result |
|---|---|---|
| request #4 (v2 smoke round 1) | `0xca6aadddfa3a80948973409ff70134ccede29136bf3eed0c3f0ca781024912b4` | SUCCESS |
| offer #4 submit | `0x59267059b88bc4b2f45865fcd487752beaae470d2d02f6a9c39d20d968c43189` | SUCCESS, key `MSK-4-...` |
| request #5 (round 2) | `0x53c2e3152b3cc496a3663742422d743642a256db006c7f8ef8de8e11f330ec49` | SUCCESS |
| offer #5 submit | `0x0a30864ac011eb5e704e098d959ae545a59724f4d1b213936e6e87122e3527c4` | SUCCESS |
| request #6 (fail-safe + recovery) | `0xc192bcc3cb93d5c861e322833297fd8c7f65d4c9918ab259d6c5b5006bdc6b3d` | SUCCESS |
| offer #6 submit | `0x7fd1f41248e6b353a6ff63d2c4a1b28d9d58fcbe0e3d80ce9953d030534da838` | SUCCESS |
| **evaluate #4 (round 1)** | `0x56f5e7985f8571a6c1a7a6c2da4d234a6491884757f9f83e39f9d25ac9b3ade1` | **MATCH**, PASSx2, deposit -> seller |
| **evaluate #5 (determinism)** | `0xd604ee53e8e59ab553f8ec8074ea478b500964db2b661d71b15709d718cc40f1` | **MATCH**, PASSx2 |
| **evaluate #6 (no marker -> fail-safe)** | `0x22f6096f9b3645e8a0096a155411e2da55e1928deb5ec8375153426f79036eb0` | **INCONCLUSIVE**, deposit NOT moved, request stays OPEN |
| re-eval #6 (stale CDN edge) | `0xf59d69f7e3898156e5a8f37733be994e28a8363c65d001f31d4878de79de9b4a` | INCONCLUSIVE (honest: edge served stale page) |
| **re-eval #6 (page synced)** | `0x89c92080766a3195eaeb99805d173718310283731b25ed51ba860b0d88589cda` | **MATCH** - same-offer recovery path proven |
| claim 0.4 GEN | `0xd1ba3acbb74312702ed9621ebfd1154187916214cbab7f1865f655df445b7f05` | SUCCESS, claimable 0.4 -> 0 |

Final on-chain stats: 6 requests, 6 offers, 4 MATCH, 2 INCONCLUSIVE, 0.4 GEN paid out, claimable drained to 0.
Explorer: https://explorer-studio.genlayer.com/contracts/0x0D2095a3e98B472c66EfF63E7990d00005d503B3
Every tx above is MAJORITY_AGREE; full receipts in `scripts/deployment_log.json` (append-only).

## Honest scope notes

- The dispute surface is the re-evaluation + deadline-expiry pair, not an in-contract appeal round.
- A `NO_MATCH` verdict is terminal for that request: the buyer withdraws the refund and opens a
  new request to solicit other sellers. This is a deliberate design choice (per-offer verdicts are
  final), documented in the README.
- studionet only at this stage; a Bradbury deployment is a follow-up, not claimed here.
- The frontend is the demo surface; the contribution is the Intelligent Contract.

## Frontend (demo)
https://faisalnugroho.github.io/matchstake/ — single page, in-browser burner wallets, faucet,
create request → submit offer → run AI matching → claim. Served assets verified byte-identical
to the repo.
