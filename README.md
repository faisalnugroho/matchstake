# MatchStake

Trustless **buyer–seller matching marketplace** on GenLayer. Category: Marketplaces / Buyer–Seller Matching.

## The problem

Marketplace matching today needs a trusted middleman to decide "does this seller's offer actually satisfy this buyer's stated requirements?". That judgment is subjective, evidence-based and natural-language — exactly what a normal smart contract cannot do, and exactly what GenLayer's AI-validator consensus can.

## How it works

1. **Buyer** opens a REQUEST: a title, up to 8 explicit checkable criteria (e.g. "price under 50 GEN", "ships within 7 days") and a GEN deposit (`create_request`).
2. **Seller** submits an OFFER pointing at a public listing URL they control (`submit_offer`). The contract returns a unique **listing key** (`MatchStake-Listing-Key: MSK-…`) that the seller must add to the listing page — this binds the offer to a re-fetchable, independently verifiable public page.
3. **Anyone** triggers `evaluate_offer`. Inside the equivalence block every validator independently fetches the listing URL, verifies the marker key, and asks its LLM to label every criterion PASS / FAIL / UNCERTAIN with quoted evidence from the page.
4. **The contract — never the model — derives the verdict:**
   - any FAIL → `NO_MATCH` (deposit refunded to the buyer, request closed)
   - any UNCERTAIN or missing signal → `INCONCLUSIVE` (nothing moves, re-evaluation of a new offer possible)
   - all PASS → `MATCH` (deposit credited to the seller)
5. Credits are withdrawn with `claim()`. `expire_request` is a **permissionless recovery path**: after the deadline an OPEN request's deposit returns to the buyer — no stuck capital, no capacity dead-end.

## Why it fits GenLayer

- LLM does genuine multi-step judgment (fetch → verify marker → evaluate each criterion against page text → cite evidence), not one-shot classification.
- Evidence is public and re-fetchable: the listing page with the marker key is fetched by every validator independently.
- Real on-chain consequence: the deposit moves only on a deterministic verdict.
- Validators compare only stable decision substance (the marker check + the per-criterion status vector). Evidence *wording* varies per model and is never compared — the equivalence principle compares statuses, not prose.
- Every failure mode (dead URL, missing marker, malformed model output, fetch error) maps to a well-formed INCONCLUSIVE that never moves money.
- Dispute/recovery designed in from v1: permissionless deadline expiry refunds the buyer.

## Layout

    contracts/matchstake.py     # the Intelligent Contract
    tests/direct/               # direct-mode gltest suite (20 tests)
    scripts/deploy_studionet.py # deploy + smoke, evidence logged
    scripts/deployment_log.json # append-only deployment evidence
    frontend/                   # single-page dApp (GitHub Pages)
    testdata/                   # demo listing pages (validator-fetchable)

## Run tests

    source ~/venv-312-matchstake/bin/activate
    PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/direct -q -p no:cacheprovider

## Deploy / smoke

    python scripts/deploy_studionet.py          # deploy leader_only + smoke
    python scripts/deploy_studionet.py --full   # full consensus

## Frontend

GitHub Pages (`.github/workflows/deploy-pages.yml`), single page, in-browser burner wallets, contract address configurable via `localStorage.matchstake_contract`.

## Known limitations

- Per-request, one offer per seller; a NO_MATCH closes the request (buyer opens a new one to try other sellers) — per-offer verdicts are final by design.
- `evaluate_offer` consumes a full consensus round (~45–120 s on studionet).
- Listing pages must be validator-fetchable (raw GitHub-hosted pages are proven to work; SPA-only pages yield no text).
- studionet only at this stage; Bradbury deployment is a follow-up.
