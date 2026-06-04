# book_idea/services/market_data.py
"""
Stage 2 – Amazon Market Data Fetcher.

Queries SerpApi's Amazon engine for the seed_keywords produced by Stage 1,
extracts the top organic book results, and returns structured market data
for Stage 3 analysis.

Key design decisions:
  - Concurrent fetches via ThreadPoolExecutor (max 5 workers).
  - 24-hour Django cache per keyword to minimise SerpApi spend.
  - Graceful degradation: timed-out keywords are skipped and flagged
    via data_quality="partial".
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

from django.conf import settings
from django.core.cache import cache
from serpapi import GoogleSearch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_KEYWORDS = 5
_MAX_BOOKS_PER_KEYWORD = 10
_CACHE_TIMEOUT = 86_400  # 24 hours in seconds
_THREAD_WORKERS = 5
_SERPAPI_TIMEOUT = 60  # seconds per query (generous to avoid false timeouts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cache_key(keyword: str) -> str:
    """Deterministic cache key from a keyword phrase."""
    return f"amz:{keyword.lower().strip()}"


def _extract_price(item: dict) -> str | None:
    """
    Aggressively find a price string containing "$" from every known
    SerpApi field/shape.  Returns e.g. "$14.99" or None.
    """
    # Collect every candidate value from known fields + nested objects
    candidates: list[Any] = []

    for field in ("price", "raw_price", "current_price"):
        raw = item.get(field)
        if raw is None:
            continue
        if isinstance(raw, dict):
            # e.g. {"raw": "$9.99", "current": 9.99, "value": 9.99}
            for sub in ("raw", "current", "value"):
                if raw.get(sub) is not None:
                    candidates.append(raw[sub])
        else:
            candidates.append(raw)

    # Nested price_info -> raw  (seen in some SerpApi responses)
    price_info = item.get("price_info")
    if isinstance(price_info, dict):
        for sub in ("raw", "current", "value"):
            if price_info.get(sub) is not None:
                candidates.append(price_info[sub])

    # Return the first candidate that contains "$"
    for val in candidates:
        s = str(val).strip()
        if "$" in s:
            return s

    # Fallback: first numeric-looking candidate, prefixed with "$"
    for val in candidates:
        s = str(val).strip()
        if s and (s[0].isdigit() or s[0] == "."):
            return f"${s}"

    return None


def _extract_book(item: dict) -> dict[str, Any] | None:
    """
    Pull the fields we care about from a single SerpApi Amazon result.

    Returns ``None`` if the item is sponsored or doesn't look like a book.
    """
    if item.get("is_sponsored") or item.get("sponsored"):
        return None

    title = item.get("title")
    if not title:
        return None

    price = _extract_price(item)

    # Review count extraction: keep comma in string (e.g. "1,200")
    reviews_val = item.get("reviews")
    if reviews_val is None:
        reviews_val = item.get("reviews_count")

    # Ensure reviews is always a string (preserves commas like "1,200")
    if reviews_val is not None:
        reviews = str(reviews_val)
    else:
        reviews = "0"

    return {
        "title": title,
        "asin": item.get("asin") or item.get("product_id"),
        "price": price,
        "rating": item.get("rating"),
        "reviews": reviews,
        "category": item.get("category") or item.get("department"),
    }


def _query_serpapi(keyword: str) -> list[dict[str, Any]]:
    """
    Hit SerpApi Amazon for *keyword* and return up to 10 organic books.

    Raises on network / auth errors so the caller can mark the keyword
    as failed.
    """
    params = {
        "engine": "amazon",
        "amazon_domain": "amazon.com",
        "k": keyword,  # Amazon's search query param (NOT search_term)
        "api_key": settings.SERPAPI_API_KEY,
    }

    search = GoogleSearch(params)
    raw = search.get_dict()

    # Try result keys in priority order – use the first with >=1 item
    items: list[dict] = []
    for key in ("shopping_results", "organic_results", "amazon_results", "search_results"):
        candidate = raw.get(key, [])
        if candidate:
            items = candidate
            break

    books: list[dict[str, Any]] = []
    for item in items:
        if len(books) >= _MAX_BOOKS_PER_KEYWORD:
            break
        book = _extract_book(item)
        if book is not None:
            books.append(book)

    logger.info("Stage 2 keyword '%s' returned %d books", keyword, len(books))
    return books


def _fetch_keyword(keyword: str) -> tuple[str, list[dict[str, Any]], bool]:
    """
    Fetch results for a single keyword, using cache when available.

    Returns
    -------
    tuple
        (keyword, books_list, timed_out)
    """
    key = _cache_key(keyword)
    cached = cache.get(key)

    if cached is not None:
        logger.debug("Cache HIT for keyword: %s", keyword)
        return keyword, cached, False

    logger.debug("Cache MISS for keyword: %s – querying SerpApi", keyword)

    try:
        books = _query_serpapi(keyword)
        cache.set(key, books, timeout=_CACHE_TIMEOUT)
        return keyword, books, False
    except Exception:
        logger.exception("SerpApi query failed for keyword: %s", keyword)
        return keyword, [], True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_amazon_data(stage_1_json: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch Amazon market data for the seed keywords from Stage 1.

    Parameters
    ----------
    stage_1_json : dict
        The JSON dict returned by ``run_stage_1_classify``.  Must contain
        a ``"seed_keywords"`` list of search phrases.

    Returns
    -------
    dict
        {
            "keywords": [
                {"phrase": "...", "top_books": [...]},
                ...
            ],
            "categories_seen": ["Category A", "Category B", ...],
            "data_quality": "full" | "partial"
        }
    """
    seed_keywords: list[str] = stage_1_json.get("seed_keywords", [])

    # Cap at 5 keywords to control cost and latency
    keywords = seed_keywords[:_MAX_KEYWORDS]

    if not keywords:
        logger.warning("No seed_keywords in Stage 1 JSON – returning empty market data")
        return {
            "keywords": [],
            "categories_seen": [],
            "data_quality": "partial",
        }

    # -- Fetch in parallel --------------------------------------------------
    any_timeout = False
    keyword_results: list[dict[str, Any]] = []
    categories_seen: set[str] = set()

    with ThreadPoolExecutor(max_workers=_THREAD_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_keyword, kw): kw
            for kw in keywords
        }

        for future in futures:
            try:
                kw, books, timed_out = future.result(timeout=_SERPAPI_TIMEOUT)
            except (FuturesTimeout, Exception) as exc:
                kw = futures[future]
                books = []
                timed_out = True
                logger.warning("Keyword '%s' timed out / errored: %s", kw, exc)

            if timed_out:
                any_timeout = True

            keyword_results.append({
                "phrase": kw,
                "top_books": books,
            })

            # Collect unique categories
            for book in books:
                cat = book.get("category")
                if cat:
                    categories_seen.add(cat)

    data_quality = "partial" if any_timeout else "full"

    logger.info(
        "Stage 2 complete – %d keywords, %d total books, quality=%s",
        len(keyword_results),
        sum(len(kr["top_books"]) for kr in keyword_results),
        data_quality,
    )

    return {
        "keywords": keyword_results,
        "categories_seen": sorted(categories_seen),
        "data_quality": data_quality,
    }
