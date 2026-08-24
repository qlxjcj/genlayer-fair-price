"""Direct-mode tests for Fair Price.

Covers the used-goods pricing lifecycle: submit -> process (AI consensus) ->
COMPLETED with a normalized, reusable verdict. Verifies authoritative-source
querying with preserved retrieval details, explicit INCONCLUSIVE results,
normalization (status enum + price-range swap + confidence clamp), reusable
on-chain estimates, ownership/collision guards, state guards, and stats.

No network, no consensus: deterministic and instant.
Run: python -m pytest tests/direct/ -v   (from the project root)
"""

import json
import pytest

from conftest import (
    ITEM,
    VERDICT_FAIR,
    VERDICT_INCONCLUSIVE,
    VERDICT_MALFORMED,
    VERDICT_OVERPRICED,
    VERDICT_UNDERPRICED,
    LLM_PATTERN,
)


def _check(c, cid):
    return json.loads(c.get_listing(cid))


def _verdict(c, cid):
    return json.loads(_check(c, cid)["verdict"])


def _record(c, item):
    return json.loads(c.get_record(item)) if c.get_record(item) != "{}" else None


# ---------- submit ----------

def test_submit_creates_pending_listing(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)

    assert c.get_listing_count() == 1
    r = _check(c, 1)
    assert r["status"] == "PENDING"
    assert r["item_name"] == ITEM
    assert r["asking_price"] == 520
    assert r["verdict"] == ""
    assert r["requester"]


def test_submit_normalizes_item_name(direct_vm, fp):
    vm, c = fp
    c.submit_listing("  iphone 14 pro 128GB  ", "electronics", 520)
    assert _check(c, 1)["item_name"] == "iphone 14 pro 128GB"


def test_submit_rejects_short_item_name(direct_vm, fp):
    vm, c = fp
    with pytest.raises(Exception) as ei:
        c.submit_listing("X", "electronics", 520)
    assert "3-80" in str(ei.value)


def test_submit_rejects_nonpositive_price(direct_vm, fp):
    vm, c = fp
    with pytest.raises(Exception) as ei:
        c.submit_listing(ITEM, "electronics", 0)
    assert "greater than zero" in str(ei.value)


def test_submit_rejects_huge_price(direct_vm, fp):
    vm, c = fp
    with pytest.raises(Exception) as ei:
        c.submit_listing(ITEM, "electronics", 10_000_000_000)
    assert "too large" in str(ei.value)


def test_submit_rejects_noninteger_price(direct_vm, fp):
    vm, c = fp
    with pytest.raises(Exception) as ei:
        c.submit_listing(ITEM, "electronics", "abc")
    assert "integer" in str(ei.value)


# ---------- process: statuses ----------

def test_process_fair(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    r = _check(c, 1)
    assert r["status"] == "COMPLETED"
    v = _verdict(c, 1)
    assert v["status"] == "FAIR"
    assert v["fair_price_min"] == 480
    assert v["fair_price_max"] == 540
    assert v["confidence"] == 90
    assert "EBAY-SOLD-88421" in v["matched_listings"]


def test_process_overpriced(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_OVERPRICED)
    c.submit_listing(ITEM, "electronics", 900)
    c.process_listing(1)

    v = _verdict(c, 1)
    assert v["status"] == "OVERPRICED"
    assert v["fair_price_min"] == 430
    assert v["fair_price_max"] == 470


def test_process_underpriced(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_UNDERPRICED)
    c.submit_listing(ITEM, "electronics", 300)
    c.process_listing(1)

    v = _verdict(c, 1)
    assert v["status"] == "UNDERPRICED"
    assert v["fair_price_min"] == 600
    assert v["fair_price_max"] == 660


# ---------- authoritative sources + retrieval details ----------

def test_record_queries_authoritative_sources(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    rec = _record(c, ITEM)
    urls = [s["url"] for s in rec["sources"]]
    assert any("autotrader.com" in u for u in urls)      # AutoTrader
    assert any("kbb.com" in u for u in urls)             # Kelley Blue Book
    assert any("ebay.com" in u for u in urls)            # eBay sold/listings
    assert any("zillow.com" in u for u in urls)          # Zillow
    assert len(urls) == 4
    assert ITEM.replace(" ", "+") in urls[0]
    # no source mocked -> all retrieval attempts recorded as failed
    assert all(s["retrieved"] is False for s in rec["sources"])
    assert all(s["excerpt"] == "" for s in rec["sources"])


def test_record_preserves_retrieval_details(direct_vm, fp):
    vm, c = fp
    vm.mock_web(r".*kbb\.com.*", {
        "method": "GET", "status": 200,
        "body": "KBB fair range for this item is 480-540.",
    })
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    rec = _record(c, ITEM)
    kbb = next(s for s in rec["sources"] if "kbb.com" in s["url"])
    assert kbb["retrieved"] is True
    assert "fair range" in kbb["excerpt"]
    others = [s for s in rec["sources"] if "kbb.com" not in s["url"]]
    assert all(s["retrieved"] is False for s in others)


# ---------- explicit inconclusive ----------

def test_inconclusive_explicit(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    v = _verdict(c, 1)
    assert v["status"] == "INCONCLUSIVE"
    assert v["fair_price_min"] == 0
    assert v["fair_price_max"] == 0
    assert v["confidence"] == 0
    assert v["matched_listings"] == []
    assert _record(c, ITEM)["status"] == "INCONCLUSIVE"


# ---------- verdict normalization ----------

def test_verdict_normalized(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_MALFORMED)
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    v = _verdict(c, 1)
    assert v["status"] == "INCONCLUSIVE"          # invalid enum coerced
    assert v["fair_price_min"] == 0               # swapped/clamped + zeroed
    assert v["fair_price_max"] == 0
    assert v["confidence"] == 0                   # 500 clamped + zeroed
    assert v["matched_listings"] == []            # cleared for inconclusive


def test_price_range_swapped(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, json.dumps({
        "status": "FAIR", "fair_price_min": 600, "fair_price_max": 400,
        "confidence": 70, "matched_listings": [], "reasoning": "x",
    }))
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)
    v = _verdict(c, 1)
    assert v["fair_price_min"] == 400
    assert v["fair_price_max"] == 600


def test_confidence_clamped_upper(direct_vm, fp):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, json.dumps({
        "status": "FAIR", "fair_price_min": 480, "fair_price_max": 540,
        "confidence": 150, "matched_listings": [], "reasoning": "x",
    }))
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)
    assert _verdict(c, 1)["confidence"] == 100


# ---------- reusable on-chain record ----------

def test_record_cached_and_case_insensitive(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    rec = _record(c, ITEM)
    assert rec["status"] == "FAIR"
    assert rec["item_name"] == ITEM
    assert rec["requester"]
    assert rec["from_listing"] == "1"
    rec2 = _record(c, ITEM.lower())
    assert rec2["status"] == "FAIR"


def test_get_record_unknown(direct_vm, fp):
    vm, c = fp
    assert c.get_record("Nintendo Switch OLED 64GB") == "{}"


# ---------- record ownership / collision guards ----------

def test_unrelated_caller_cannot_replace_record(direct_vm, fp, direct_bob):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)
    owner = _record(c, ITEM)["requester"]

    vm.sender = direct_bob
    c.submit_listing(ITEM, "electronics", 520)
    with pytest.raises(Exception) as ei:
        c.process_listing(2)
    assert "settled by another requester" in str(ei.value).lower()

    rec = _record(c, ITEM)
    assert rec["requester"] == owner
    assert rec["from_listing"] == "1"


def test_same_requester_can_refresh_record(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(2)
    assert _record(c, ITEM)["from_listing"] == "2"


def test_inconclusive_record_can_be_improved_by_anyone(direct_vm, fp, direct_bob):
    vm, c = fp
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)
    assert _record(c, ITEM)["status"] == "INCONCLUSIVE"

    vm.sender = direct_bob
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_OVERPRICED)
    c.submit_listing(ITEM, "electronics", 900)
    c.process_listing(2)
    rec = _record(c, ITEM)
    assert rec["status"] == "OVERPRICED"
    assert rec["requester"] == direct_bob.as_hex


# ---------- state guards ----------

def test_process_twice_blocked(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    with pytest.raises(Exception) as ei:
        c.process_listing(1)
    assert "processed" in str(ei.value).lower()


def test_process_not_found(direct_vm, fp):
    vm, c = fp
    with pytest.raises(Exception) as ei:
        c.process_listing(99)
    assert "not found" in str(ei.value).lower()


# ---------- stats ----------

def test_stats_counts_statuses(direct_vm, fp):
    vm, c = fp
    c.submit_listing(ITEM, "electronics", 520)
    c.process_listing(1)

    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_OVERPRICED)
    c.submit_listing("Rolex Submariner 41", "watch", 9000)
    c.process_listing(2)

    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_UNDERPRICED)
    c.submit_listing("Toyota Corolla 2019", "car", 9000)
    c.process_listing(3)

    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_listing("Nintendo Switch OLED", "gaming", 300)
    c.process_listing(4)

    s = c.get_stats()
    assert s["total"] == 4
    assert s["completed"] == 4
    assert s["fair"] == 1
    assert s["overpriced"] == 1
    assert s["underpriced"] == 1
    assert s["inconclusive"] == 1
    assert s["records"] == 4
