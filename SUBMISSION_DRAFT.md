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

## Test evidence — 33/33 direct-mode tests

```
$ PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/direct -q -p no:cacheprovider
33 passed
```

Covers: happy MATCH settlement, NO_MATCH offer-only rejection (request stays OPEN),
NO_MATCH-does-not-block-other-sellers (second seller wins same request),
payable deposit path (deposit == msg.value; zero value reverts), INCONCLUSIVE
no-movement, malformed-LLM fail-safe, missing-marker fail-safe, recovery
re-evaluation, terminal-state guard, all input guards, deadline expiry refund,
buyer cancel & reclaim (incl. only-buyer guard, pre-deadline cancel), claim,
three validator-agreement cases, and **browser value propagation** (new
`tests/direct/test_frontend_value_propagation.py`): the frontend passes
DEPOSIT_WEI into create_request, sendWrite sets `opts.value`, and the bundled
SDK forwards value into the signed consensus transaction — pinned
source-to-source so the deposit path cannot silently regress.

`genvm-lint check contracts/matchstake.py` → ✓ Lint passed (3 checks), ✓ Validation passed.

## Live smoke evidence (studionet, full consensus, v3 deployment)

Deployed: `0x2780D1fc27F35dd887aC42E247e2d54e2cEDf718`
Deploy tx `0x3e6125b039a34ade95242244252d7f048f6950c1ac70d6d754e71c2513982a21` (full consensus)
Deployed code sha256 == `sha256sum contracts/matchstake.py` = `e1f1cf00557fc739841d3ef10113bfae11d0282918b447d9d516a4e5e5b348f6`

| Scenario | Tx (studionet) | Observed result |
|---|---|---|
| **create_request with value** | `0x1287c750e19d11b785cf663b773e5dbeb368593662324a2f431bddbebf214933` | deposit sent as tx **value** (1e17 wei); recorded deposit == msg.value |
| **evaluate: missing marker → INCONCLUSIVE** | `0xeabc35e3b28a662efd8886d29f32e79c3353d7c336f5212ca21841ab143604bd` | **INCONCLUSIVE** — no money moved, request stays OPEN |
| **same-offer retry after marker fix** | (re-evaluate offer #1) | **MATCH** — INCONCLUSIVE retry path proven on-chain |
| **cancel_request (buyer recovery)** | request #4 | **CANCELLED** — non-terminal offers voided, deposit credited back to buyer |
| **claim** | `0x842a0d83d8a3b0869d94ca6eb33d352853adf121f381f3400055706e939b6c3c` | 0.2 GEN claimed (match payout + cancel refund), claimable → 0 |

Final on-chain stats: 4 requests, 2 offers, 1 MATCH, 2 INCONCLUSIVE, 1 CANCELLED, 1e17 wei paid out, claimable drained to 0.
Explorer: https://explorer-studio.genlayer.com/contracts/0x2780D1fc27F35dd887aC42E247e2d54e2cEDf718
Full receipts in `scripts/deployment_log.json` (append-only, events `v3_*`).

## Honest scope notes

- The dispute surface is the re-evaluation + deadline-expiry pair, not an in-contract appeal round.
- A `NO_MATCH` verdict is terminal for **that offer only**; the request stays OPEN with its
  deposit escrowed, so other sellers can still offer and win on the same request. The buyer
  can reclaim at any time via `cancel_request`.
- studionet only at this stage; a Bradbury deployment is a follow-up, not claimed here.
- The frontend is the demo surface; the contribution is the Intelligent Contract.

## Frontend (demo)
https://faisalnugroho.github.io/matchstake/ — single page, in-browser burner wallets, faucet,
create request → submit offer → run AI matching → claim. Served assets verified byte-identical
to the repo.
