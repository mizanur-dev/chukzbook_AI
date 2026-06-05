# book_idea/services/validator.py
"""
Stage 4 – Post-LLM Validation & Anti-Hallucination.

Ensures that the briefing produced by Stage 3 does not contain
fabricated numbers, has the correct structure, and carries appropriate
caveats when market data was incomplete.
"""

import copy
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


_TARGET_TOP_KEYWORDS = 5
_DRAFT_DESC_MIN_WORDS = 80
_DRAFT_DESC_MAX_WORDS = 200
_PARTIAL_BANNER = "⚠ Based on limited market data for this niche."
_UNAVAILABLE_TAG = "[data unavailable]"
_CENSORED_SENTENCE_FALLBACK = "Market pricing and competition data for this specific niche is currently limited."
_PARTIAL_VIABILITY_PREFIX = "live market data unavailable right now; "
_LOW_CONFIDENCE_BANNER = "Show low-confidence banner; offer specialist chat for a manual review. "
_LOW_CONFIDENCE_THRESHOLD = 0.5


_MARKET_NUMBER_RE = re.compile(
    r"""
    (?P<dollar>  \$\d+(?:,\d{3})*(?:\.\d{1,2})? )         
    |
    (?P<comma>   \b[1-9]\d{0,2}(?:,\d{3})+\b )           
    |
    (?P<large>   \b[1-9]\d{2,}\b )                        
    |
    (?P<decimal> \b\d{1,2}\.\d{1,2}\b )                     
    """,
    re.VERBOSE,
)




def _flatten_to_strings(obj: Any) -> list[str]:
    """Recursively collect every string and stringified number from a structure."""
    results: list[str] = []

    if isinstance(obj, dict):
        for v in obj.values():
            results.extend(_flatten_to_strings(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            results.extend(_flatten_to_strings(item))
    elif isinstance(obj, str):
        results.append(obj)
    elif isinstance(obj, (int, float)):
        results.append(str(obj))
        if isinstance(obj, float) and obj == int(obj):
            results.append(str(int(obj)))

    return results


def _collect_numbers_from_market_data(market_data: dict[str, Any]) -> set[str]:
    """
    Walk Stage 2 market data and collect every number-like token that
    appears in it.  Returns a set of *normalised* strings for comparison.

    For each matched number we store:
      - the raw matched text   ("$4.99", "1,200", "4500")
      - a normalised form      ("4.99",  "1200",  "4500")
    """
    numbers: set[str] = set()

    for text in _flatten_to_strings(market_data):
        for m in _MARKET_NUMBER_RE.finditer(text):
            raw = m.group()
            normalised = raw.replace(",", "").lstrip("$")
            numbers.add(raw)
            numbers.add(normalised)

        try:
            val = float(text)
            numbers.add(text)
            numbers.add(str(val))
            if val == int(val):
                numbers.add(str(int(val)))
        except (ValueError, OverflowError):
            pass

    return numbers


def _scrub_hallucinated_numbers(
    text: str,
    valid_numbers: set[str],
    author_numbers: set[str],
) -> str:
    """
    Replace market-metric numbers in *text* that are NOT backed by
    market data AND NOT present in the author's original brief.

    The replacement targets ONLY the matched number span —
    surrounding punctuation, spaces, and commas are never touched.
    """

    def _replacer(m: re.Match) -> str:
        raw = m.group()                      
        normalised = raw.replace(",", "").lstrip("$")

        if raw in author_numbers or normalised in author_numbers:
            return raw

        if raw in valid_numbers or normalised in valid_numbers:
            return raw

        return "[data unavailable]"

    return _MARKET_NUMBER_RE.sub(_replacer, text)


def _collect_author_numbers(author_brief_text: str) -> set[str]:
    """
    Extract every number token from the author's original brief so we
    can whitelist them.  We use a broad regex here (any digit sequence,
    optionally with decimals, commas, or a leading $) because we want to
    be *permissive* about what the author wrote.
    """
    nums: set[str] = set()
    for m in re.finditer(r"\$?\d[\d,]*\.?\d*", author_brief_text):
        raw = m.group()
        nums.add(raw)
        nums.add(raw.replace(",", "").lstrip("$"))
    return nums


def _clean_censored_sentences(text: str) -> str:
    """
    Replace ANY sentence containing ``[data unavailable]`` with a clean,
    professional fallback.  The user should never see ugly bracket tags
    in the final report.

    Example before:
        "Prices range from [data unavailable] to [data unavailable]."
    Example after:
        "Market pricing and competition data for this specific niche is currently limited."
    """
    if _UNAVAILABLE_TAG not in text:
        return text

    sentence_pattern = re.compile(r"[^.!?]+(?:[.!?]+|\Z)")
    raw_parts = [m.group().strip() for m in sentence_pattern.finditer(text) if m.group().strip()]
    
    cleaned_parts: list[str] = []
    fallback_used = False
    
    for part in raw_parts:
        if _UNAVAILABLE_TAG in part:
            if not fallback_used:
                cleaned_parts.append(_CENSORED_SENTENCE_FALLBACK)
                fallback_used = True
            logger.debug(
                "Replaced censored sentence: %s", part,
            )
        else:
            cleaned_parts.append(part)
            
    return " ".join(cleaned_parts)


def _scrub_dict_values(
    obj: Any,
    valid_numbers: set[str],
    author_numbers: set[str],
) -> Any:
    """
    Recursively walk a JSON-like structure and scrub hallucinated numbers
    from every *string* value, then clean up over-censored sentences.
    """
    if isinstance(obj, dict):
        return {
            k: _scrub_dict_values(v, valid_numbers, author_numbers)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_dict_values(item, valid_numbers, author_numbers) for item in obj]
    if isinstance(obj, str):
        scrubbed = _scrub_hallucinated_numbers(obj, valid_numbers, author_numbers)
        return _clean_censored_sentences(scrubbed)
    return obj


def _pad_or_trim_keywords(
    top_keywords: list[dict[str, Any]],
    target: int = _TARGET_TOP_KEYWORDS,
) -> list[dict[str, Any]]:
    """Ensure *top_keywords* has exactly ``target`` entries."""
    if len(top_keywords) > target:
        return top_keywords[:target]

    while len(top_keywords) < target:
        top_keywords.append({
            "phrase": "Keyword data limited",
            "why": "Insufficient market data to recommend additional keywords.",
        })

    return top_keywords


def _clean_all_strings(obj: Any) -> Any:
    """Recursively clean all string values in a structure using _clean_censored_sentences."""
    if isinstance(obj, dict):
        return {k: _clean_all_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_all_strings(item) for item in obj]
    if isinstance(obj, str):
        return _clean_censored_sentences(obj)
    return obj


def _count_censored_fields(
    obj: Any,
    valid_numbers: set[str],
    author_numbers: set[str],
) -> tuple[int, int]:
    """
    Count (censored_string_fields, total_string_fields) without mutating obj.

    A string field counts as censored if, after running the same
    hallucinated-number scrub used by Stage 4, it contains the
    ``[data unavailable]`` tag.
    """
    censored = 0
    total = 0

    if isinstance(obj, dict):
        for v in obj.values():
            c, t = _count_censored_fields(v, valid_numbers, author_numbers)
            censored += c
            total += t
    elif isinstance(obj, list):
        for item in obj:
            c, t = _count_censored_fields(item, valid_numbers, author_numbers)
            censored += c
            total += t
    elif isinstance(obj, str):
        if obj.strip():
            total = 1
            scrubbed = _scrub_hallucinated_numbers(obj, valid_numbers, author_numbers)
            if _UNAVAILABLE_TAG in scrubbed:
                censored = 1

    return censored, total




def validate_briefing(
    stage_3_json: dict[str, Any],
    stage_2_market_data: dict[str, Any],
    author_brief_text: str,
) -> dict[str, Any]:
    """
    Validate and clean the Stage 3 briefing against the raw market data.

    Anti-hallucination steps:
      1. Extract every number from Stage 2 market data.
      2. Extract every number from the author's original brief (whitelist).
      3. Walk all string values in Stage 3 JSON; any market-metric number
         NOT found in the market data AND NOT in the author's brief is
         replaced with ``[data unavailable]``.
      4. Ensure ``top_keywords`` has exactly 5 entries.
      5. Warn if ``draft_description`` is outside 80–200 words.
      6. Prepend a partial-data banner if Stage 2 quality was ``"partial"``.

    Parameters
    ----------
    stage_3_json : dict
        The raw briefing JSON from ``run_stage_3_synthesize``.
    stage_2_market_data : dict
        The market data dict from ``fetch_amazon_data``.
    author_brief_text : str
        The original text provided by the user.

    Returns
    -------
    dict
        The validated (and possibly modified) briefing JSON.
    """
    briefing = copy.deepcopy(stage_3_json)

    valid_numbers = _collect_numbers_from_market_data(stage_2_market_data)
    author_numbers = _collect_author_numbers(author_brief_text)

    logger.debug(
        "Validator: %d market numbers, %d author numbers",
        len(valid_numbers),
        len(author_numbers),
    )

    censored_count, total_string_fields = _count_censored_fields(
        briefing, valid_numbers, author_numbers,
    )
    censored_ratio = (
        censored_count / total_string_fields if total_string_fields else 0.0
    )
    low_confidence = censored_ratio > _LOW_CONFIDENCE_THRESHOLD

    briefing = _scrub_dict_values(briefing, valid_numbers, author_numbers)

    top_keywords = briefing.get("top_keywords", [])
    if not isinstance(top_keywords, list):
        top_keywords = []
    briefing["top_keywords"] = _pad_or_trim_keywords(top_keywords)

    logger.info(
        "top_keywords adjusted: %d → %d entries",
        len(stage_3_json.get("top_keywords", [])),
        len(briefing["top_keywords"]),
    )

    draft_desc = briefing.get("draft_description", "")
    word_count = len(draft_desc.split())

    if word_count < _DRAFT_DESC_MIN_WORDS:
        logger.warning(
            "draft_description too short: %d words (min %d)",
            word_count,
            _DRAFT_DESC_MIN_WORDS,
        )
    elif word_count > _DRAFT_DESC_MAX_WORDS:
        logger.warning(
            "draft_description too long: %d words (max %d)",
            word_count,
            _DRAFT_DESC_MAX_WORDS,
        )

    data_quality = stage_2_market_data.get("data_quality", "full")
    market_is_empty = not stage_2_market_data.get("keywords")

    if data_quality == "partial" or market_is_empty:
        viability = briefing.get("viability_line", "")
        briefing["viability_line"] = f"{_PARTIAL_VIABILITY_PREFIX}{viability}"

        snapshot = briefing.get("competitive_snapshot", "")
        briefing["competitive_snapshot"] = f"{_PARTIAL_BANNER} {snapshot}"

        logger.info("Partial-data banners prepended to briefing")

    if low_confidence:
        genre_summary = briefing.get("genre_summary", "")
        briefing["genre_summary"] = f"{_LOW_CONFIDENCE_BANNER}{genre_summary}"
        logger.info(
            "Low-confidence banner added: %d/%d string fields censored (%.0f%%)",
            censored_count,
            total_string_fields,
            censored_ratio * 100,
        )

    briefing = _clean_all_strings(briefing)

    return briefing
