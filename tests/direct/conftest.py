"""Shared fixtures for Fair Price direct-mode tests.

Direct mode runs the real contract source in-process. The AI pricing estimate
is mocked so tests are deterministic and instant, with no network or consensus
dependency.
"""

import json
import os
import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fair_price.py",
)

ITEM = "iPhone 14 Pro 128GB"

VERDICT_FAIR = json.dumps({
    "status": "FAIR",
    "fair_price_min": 480,
    "fair_price_max": 540,
    "confidence": 90,
    "matched_listings": ["EBAY-SOLD-88421"],
    "reasoning": "Price sits inside the cross-listing fair range.",
})

VERDICT_OVERPRICED = json.dumps({
    "status": "OVERPRICED",
    "fair_price_min": 430,
    "fair_price_max": 470,
    "confidence": 85,
    "matched_listings": ["KBB-RANGE-2210"],
    "reasoning": "Asking price exceeds the top of the fair range.",
})

VERDICT_UNDERPRICED = json.dumps({
    "status": "UNDERPRICED",
    "fair_price_min": 600,
    "fair_price_max": 660,
    "confidence": 88,
    "matched_listings": ["AUTOTRADER-LIST-7711"],
    "reasoning": "Asking price sits below the fair range.",
})

VERDICT_INCONCLUSIVE = json.dumps({
    "status": "INCONCLUSIVE",
    "fair_price_min": 0,
    "fair_price_max": 0,
    "confidence": 0,
    "matched_listings": [],
    "reasoning": "Authoritative pricing sources could not be retrieved for this item.",
})

# Missing / invalid fields to prove verdict normalization.
VERDICT_MALFORMED = json.dumps({
    "status": "too-expensive",
    "fair_price_min": 9999,
    "fair_price_max": -5,
    "confidence": 500,
    "matched_listings": "EBAY-SOLD-88421",
})

LLM_PATTERN = r".*used-goods pricing engine.*"

# A successful authoritative source whose body references the item, so verdicts
# have at least one item-specific retrieved source.
SOURCE_OK = r".*kbb\.com.*"
SOURCE_BODY = "KBB fair range for " + ITEM + " is 480-540."


def with_source(vm, body=SOURCE_BODY):
    vm.mock_web(SOURCE_OK, {"method": "GET", "status": 200, "body": body})


@pytest.fixture
def fp(direct_vm, direct_deploy):
    vm = direct_vm
    vm.mock_llm(LLM_PATTERN, VERDICT_FAIR)
    with_source(vm)
    c = direct_deploy(CONTRACT)
    return vm, c
