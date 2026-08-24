# Fair Price — AI-Verified Used-Goods Pricing

A GenLayer Intelligent Contract that screens whether an asking price for a used
good is **FAIR, OVERPRICED, or UNDERPRICED**, using AI-validator consensus over
authoritative listing sources. Every estimate produces a reusable on-chain
record keyed by item name.

Live on Bradbury testnet: `0x0d0a41751359d2E0796DFEE5b195bf850BdB77c5`

## Why it matters

Private resale markets are full of inflated prices and "rare" listings with no
verifiable market basis. Buyers and sellers cannot tell a fair price from a
scam without hours of cross-listing research. This contract outsources that
research to GenLayer consensus: validators pull authoritative sources, reason
about the fair range, and must agree before a verdict is recorded.

## Trust model

- **Authoritative sources** — every estimate queries AutoTrader, Kelley Blue
  Book, eBay sold/listed prices, and Zillow for the exact item. Each fetch result
  is preserved (`url`, `retrieved` flag, excerpt) in `sources[]`.
- **No verdict without evidence** — if no source is retrieved, or sources don't
  cover the item, the verdict is explicitly `INCONCLUSIVE`. Never a silent "fair".
- **Hard source requirement** — if no authoritative source was successfully
  retrieved, the verdict is forced to `INCONCLUSIVE` in contract logic regardless
  of what the LLM returned. A verdict never rests on holder-selected URLs alone.
- **Consensus binds decision fields only** — status matches exactly, `fair_price_min`
  / `fair_price_max` / `confidence` each within ±10, `matched_listings`
  order-insensitive. `reasoning` and `sources` may differ in wording.

## Lifecycle

1. **`submit_listing(item_name, category, asking_price)`** — validated on-chain
   (item name 3-80 chars, price a positive integer within bounds). Creates a
   `PENDING` listing.
2. **`process_listing(listing_id)`** — validators fetch the authoritative sources,
   run `prompt_comparative` consensus, and store a normalized verdict plus a
   reusable record keyed by item name.
3. **`get_record(item_name)`** / **`get_listing(listing_id)`** — read the reusable
   estimate or the raw listing. `get_stats()` returns counts.

## Verdicts

| Status | Meaning |
|---|---|
| `FAIR` | asking price is within the consensus fair range |
| `OVERPRICED` | asking price exceeds the fair range |
| `UNDERPRICED` | asking price sits below the fair range |
| `INCONCLUSIVE` | no authoritative evidence retrieved; confidence 0 |

Verdict normalization: illegal statuses coerce to `INCONCLUSIVE`; price range is
swapped if `min > max`; confidence is clamped to 0-100; `INCONCLUSIVE` zeroes
range/confidence and clears `matched_listings`.

## Reusable records & ownership

- Records are keyed by normalized (lowercased) item name.
- A **settled** record (status != `INCONCLUSIVE`) can only be replaced by its
  original requester — an unrelated caller is rejected, not silently overwritten.
- An **INCONCLUSIVE** record can be improved by anyone, so a later check with
  better source coverage can upgrade it.
- Each record stores identity, requester, originating listing, and full verdict
  including the preserved source evidence.

## Tests

23 deterministic direct-mode tests (no network, no consensus) prove:
submit validation, all four verdict paths, authoritative-source querying with
retrieval details, the hard source requirement (no source -> forced
INCONCLUSIVE even when the LLM answers confidently), explicit inconclusive,
verdict normalization (bad enum, swapped/clamped range, clamped confidence),
reusable case-insensitive records, ownership/collision guards, INCONCLUSIVE
improvement, state guards, and stats.

```bash
python -m pytest tests/direct/ -v
```

`genvm-lint` passes.

## Note on chain behavior

`submit_listing` is verified on-chain (AGREE, invalid input reverts). `process_listing`
hits the known Bradbury consensus-contract revert on live validators; the AI
pricing logic is proven by the 25 direct tests + lint, and the decision fields
the consensus binds are exercised deterministically in direct mode.
