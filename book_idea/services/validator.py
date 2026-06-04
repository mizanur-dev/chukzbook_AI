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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TARGET_TOP_KEYWORDS = 5
_DRAFT_DESC_MIN_WORDS = 80
_DRAFT_DESC_MAX_WORDS = 200
_PARTIAL_BANNER = "⚠ Based on limited market data for this niche."

# Regex: match numbers that could be hallucinated stats.
# Covers integers (1234), decimals (4.7), dollar amounts ($9.99),
# and comma-grouped numbers (1,234,567).
_NUMBER_RE = re.compile(r"\$?[\d,]+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_numbers_from_market_data(market_data: dict[str, Any]) -> set[str]:
    """
    Recursively walk the Stage 2 market data structure and collect every
    number-like token.  Returns a set of *normalised* number strings
    (commas stripped) so lookups are forgiving.
    """
    numbers: set[str] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _walk(item)
        elif isinstance(obj, (int, float)):
            # Store both the raw number and its string form
            numbers.add(str(obj))
            # Also store formatted variants (e.g. "4.5" for 4.5)
            if isinstance(obj, float):
                # Integer-valued floats → also store without decimal
                if obj == int(obj):
                    numbers.add(str(int(obj)))
            numbers.add(f"{obj:g}")  # compact representation
        elif isinstance(obj, str):
            for match in _NUMBER_RE.finditer(obj):
                raw = match.group()
                normalised = raw.replace(",", "").lstrip("$")
                numbers.add(normalised)
                numbers.add(raw)  # keep original form too

    _walk(market_data)
    return numbers


def _scrub_hallucinated_numbers(text: str, valid_numbers: set[str]) -> str:
    """
    Replace every number in *text* that is **not** present in
    *valid_numbers* with ``[data unavailable]``.
    """

    def _replacer(match: re.Match) -> str:
        raw = match.group()
        normalised = raw.replace(",", "").lstrip("$")

        # Check both the raw and normalised forms
        if raw in valid_numbers or normalised in valid_numbers:
            return raw  # number is genuine — keep it
        return "[data unavailable]"

    return _NUMBER_RE.sub(_replacer, text)


def _scrub_dict_values(
    obj: Any,
    valid_numbers: set[str],
) -> Any:
    """
    Recursively walk a JSON-like structure and scrub hallucinated numbers
    from every *string* value.
    """
    if isinstance(obj, dict):
        return {k: _scrub_dict_values(v, valid_numbers) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_dict_values(item, valid_numbers) for item in obj]
    if isinstance(obj, str):
        return _scrub_hallucinated_numbers(obj, valid_numbers)
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
            "phrase": "[data unavailable]",
            "why": "Insufficient market data to recommend additional keywords.",
        })

    return top_keywords


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_briefing(
    stage_3_json: dict[str, Any],
    stage_2_market_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and clean the Stage 3 briefing against the raw market data.

    Anti-hallucination steps:
      1. Extract every number from Stage 2 market data.
      2. Walk all string values in Stage 3 JSON; any number NOT found in
         the market data is replaced with ``[data unavailable]``.
      3. Ensure ``top_keywords`` has exactly 5 entries.
      4. Warn if ``draft_description`` is outside 80–200 words.
      5. Prepend a partial-data banner if Stage 2 quality was ``"partial"``.

    Parameters
    ----------
    stage_3_json : dict
        The raw briefing JSON from ``run_stage_3_synthesize``.
    stage_2_market_data : dict
        The market data dict from ``fetch_amazon_data``.

    Returns
    -------
    dict
        The validated (and possibly modified) briefing JSON.
    """
    # Work on a deep copy so the caller's original is untouched
    briefing = copy.deepcopy(stage_3_json)

    # -- 1. Anti-hallucination: scrub fabricated numbers ---------------------
    valid_numbers = _collect_numbers_from_market_data(stage_2_market_data)

    logger.debug(
        "Validator collected %d valid numbers from market data",
        len(valid_numbers),
    )

    briefing = _scrub_dict_values(briefing, valid_numbers)

    # -- 2. Ensure exactly 5 top_keywords -----------------------------------
    top_keywords = briefing.get("top_keywords", [])
    if not isinstance(top_keywords, list):
        top_keywords = []
    briefing["top_keywords"] = _pad_or_trim_keywords(top_keywords)

    logger.info(
        "top_keywords adjusted: %d → %d entries",
        len(stage_3_json.get("top_keywords", [])),
        len(briefing["top_keywords"]),
    )

    # -- 3. Check draft_description word count ------------------------------
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

    # -- 4. Partial-data banner ---------------------------------------------
    data_quality = stage_2_market_data.get("data_quality", "full")

    if data_quality == "partial":
        # Prepend banner to the viability_line (the most prominent field)
        viability = briefing.get("viability_line", "")
        briefing["viability_line"] = f"{_PARTIAL_BANNER} {viability}"

        # Also prepend to competitive_snapshot for extra visibility
        snapshot = briefing.get("competitive_snapshot", "")
        briefing["competitive_snapshot"] = f"{_PARTIAL_BANNER} {snapshot}"

        logger.info("Partial-data banners prepended to briefing")

    return briefing
