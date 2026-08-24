# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from dataclasses import dataclass
from genlayer import *


@allow_storage
@dataclass
class Listing:
    listing_id: str
    requester: str
    item_name: str
    category: str
    asking_price: u256
    status: str
    verdict: str


class FairPrice(gl.Contract):
    listings: TreeMap[str, str]
    records: TreeMap[str, str]
    listing_count: u256

    STATUSES = ("FAIR", "OVERPRICED", "UNDERPRICED", "INCONCLUSIVE")
    # Authoritative used-goods pricing sources queried for every estimate.
    AUTHORITATIVE_SOURCES = (
        "https://www.autotrader.com/search?q=",
        "https://www.kbb.com/search/?query=",
        "https://www.ebay.com/sch/i.html?_nkw=",
        "https://www.zillow.com/homes/?searchQueryState=",
    )

    def __init__(self):
        pass

    def _decode_body(self, content) -> str:
        body = getattr(content, "body", None)
        if body is None:
            return str(content)
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)

    def _valid_item_name(self, name: str) -> str:
        name = name.strip()
        if len(name) < 3 or len(name) > 80:
            raise gl.vm.UserError("Item name must be 3-80 characters")
        return name

    def _valid_price(self, price: int) -> int:
        if not isinstance(price, int):
            try:
                price = int(price)
            except Exception:
                raise gl.vm.UserError("Price must be an integer")
        if price <= 0:
            raise gl.vm.UserError("Price must be greater than zero")
        if price > 100_000_000:
            raise gl.vm.UserError("Price too large")
        return price

    def _authoritative_urls(self, item_name: str) -> list:
        q = item_name.replace(" ", "+")
        return [base + q for base in self.AUTHORITATIVE_SOURCES]

    def _estimate(self, item_name: str, category: str, asking_price: int) -> dict:
        def gather_and_estimate() -> dict:
            sources = []
            texts = []
            for url in self._authoritative_urls(item_name):
                try:
                    content = gl.nondet.web.get(url)
                    body = self._decode_body(content)[:1200]
                    texts.append(f"[{url}]\n{body}")
                    sources.append({"url": url, "retrieved": True, "excerpt": body[:400]})
                except Exception:
                    texts.append(f"[{url}] [FETCH_FAILED]")
                    sources.append({"url": url, "retrieved": False, "excerpt": ""})

            task = f"""
You are a used-goods pricing engine. Determine whether the asking price below is
fair, overpriced, or underpriced for this item, based ONLY on the authoritative
listing sources queried for this exact item (AutoTrader, Kelley Blue Book, eBay
sold/listed prices, Zillow). Cross-reference the retrieved listings to estimate
a defensible fair market range in the listing's currency.

If NO source was retrieved, or the retrieved sources do not cover this item, you
MUST return status "INCONCLUSIVE" — never judge a price without evidence.

ITEM: {item_name}
CATEGORY: {category or "[none provided]"}
ASKING PRICE: {asking_price}

SOURCES:
{chr(10).join(texts) if texts else "[none]"}

Evaluate: is the asking price above, below, or within a fair market range for
this item? Give a fair price range (integers, low <= high), a confidence score
(0-100), and name the specific listings that matched. Be explicit and never
invent listing identifiers.

Respond ONLY in this JSON format with exact fields:
{{
    "status": "FAIR" | "OVERPRICED" | "UNDERPRICED" | "INCONCLUSIVE",
    "fair_price_min": int,
    "fair_price_max": int,
    "confidence": int,
    "matched_listings": [str],
    "reasoning": str
}}

When status is "INCONCLUSIVE", set fair_price_min=0, fair_price_max=0,
confidence=0, matched_listings=[].
"""
            result = gl.nondet.exec_prompt(task, response_format="json")
            if isinstance(result, str):
                result = json.loads(result.replace("```json", "").replace("```", ""))
            if not isinstance(result, dict):
                raise gl.vm.UserError("[LLM_ERROR] LLM returned non-dict result")
            result["sources"] = sources
            return result

        principle = (
            "Two results are equivalent if status "
            "(FAIR/OVERPRICED/UNDERPRICED/INCONCLUSIVE) matches exactly, "
            "fair_price_min and fair_price_max each differ by at most 10, "
            "confidence values differ by at most 10 points, and matched_listings "
            "contains the same listing identifiers (order-insensitive). reasoning "
            "and sources may differ in wording."
        )
        return gl.eq_principle.prompt_comparative(gather_and_estimate, principle)

    def _normalize_verdict(self, v: dict) -> dict:
        status = str(v.get("status", "")).upper()
        if status not in self.STATUSES:
            status = "INCONCLUSIVE"
        try:
            pmin = int(v.get("fair_price_min", 0))
        except Exception:
            pmin = 0
        try:
            pmax = int(v.get("fair_price_max", 0))
        except Exception:
            pmax = 0
        if pmin < 0:
            pmin = 0
        if pmax < 0:
            pmax = 0
        if pmax < pmin:
            pmin, pmax = pmax, pmin
        try:
            confidence = int(v.get("confidence", 0))
        except Exception:
            confidence = 0
        if confidence < 0:
            confidence = 0
        if confidence > 100:
            confidence = 100
        matched = v.get("matched_listings", [])
        if not isinstance(matched, list):
            matched = [str(matched)]
        matched = [str(x) for x in matched]

        if status == "INCONCLUSIVE":
            pmin = 0
            pmax = 0
            confidence = 0
            matched = []

        sources = v.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        norm_sources = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            norm_sources.append({
                "url": str(s.get("url", "")),
                "retrieved": bool(s.get("retrieved", False)),
                "excerpt": str(s.get("excerpt", ""))[:400],
            })

        return {
            "status": status,
            "fair_price_min": pmin,
            "fair_price_max": pmax,
            "confidence": confidence,
            "matched_listings": matched,
            "sources": norm_sources,
            "reasoning": str(v.get("reasoning", "")),
        }

    @gl.public.write
    def submit_listing(self, item_name: str, category: str, asking_price: int):
        item_name = self._valid_item_name(item_name)
        asking_price = self._valid_price(asking_price)
        sender = gl.message.sender_address
        self.listing_count += 1
        listing_id = str(self.listing_count)

        listing = Listing(
            listing_id=listing_id,
            requester=sender.as_hex,
            item_name=item_name,
            category=str(category or "").strip(),
            asking_price=asking_price,
            status="PENDING",
            verdict="",
        )
        self.listings[listing_id] = json.dumps(listing.__dict__)

    @gl.public.write
    def process_listing(self, listing_id: str):
        listing_id = str(listing_id)
        listing = json.loads(self.listings.get(listing_id, "{}"))
        if not listing:
            raise gl.vm.UserError("Listing not found")
        if listing["status"] != "PENDING":
            raise gl.vm.UserError("Already processed")

        verdict = self._normalize_verdict(
            self._estimate(listing["item_name"], listing["category"], listing["asking_price"])
        )

        # Reusable estimate is keyed by normalized item name. Guard against an
        # unrelated caller silently replacing a settled estimate: only its
        # original requester may update it, and anyone may improve an INCONCLUSIVE one.
        key = listing["item_name"].lower()
        existing = json.loads(self.records.get(key, "{}"))
        if existing:
            settled = existing.get("status") != "INCONCLUSIVE"
            same_owner = existing.get("requester") == listing["requester"]
            if settled and not same_owner:
                raise gl.vm.UserError(
                    "Reusable estimate for this item is already settled by another requester"
                )

        listing["status"] = "COMPLETED"
        listing["verdict"] = json.dumps(verdict, sort_keys=True)
        self.listings[listing_id] = json.dumps(listing)

        record = dict(verdict)
        record["item_name"] = listing["item_name"]
        record["category"] = listing["category"]
        record["asking_price"] = listing["asking_price"]
        record["requester"] = listing["requester"]
        record["from_listing"] = listing["listing_id"]
        self.records[key] = json.dumps(record, sort_keys=True)

    @gl.public.view
    def get_listing(self, listing_id: str) -> str:
        return self.listings.get(str(listing_id), "{}")

    @gl.public.view
    def get_record(self, item_name: str) -> str:
        return self.records.get(self._valid_item_name(item_name).lower(), "{}")

    @gl.public.view
    def get_listing_count(self) -> int:
        return self.listing_count

    @gl.public.view
    def get_stats(self) -> dict:
        fair = 0
        overpriced = 0
        underpriced = 0
        inconclusive = 0
        for v in self.listings.values():
            r = json.loads(v)
            if r["status"] == "COMPLETED" and r["verdict"]:
                verdict = json.loads(r["verdict"])
                st = verdict.get("status", "INCONCLUSIVE")
                if st == "FAIR":
                    fair += 1
                elif st == "OVERPRICED":
                    overpriced += 1
                elif st == "UNDERPRICED":
                    underpriced += 1
                else:
                    inconclusive += 1
        return {
            "total": len(self.listings),
            "completed": fair + overpriced + underpriced + inconclusive,
            "fair": fair,
            "overpriced": overpriced,
            "underpriced": underpriced,
            "inconclusive": inconclusive,
            "records": len(self.records),
        }
