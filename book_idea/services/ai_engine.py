# services/ai_engine.py
"""
Core AI Engine for the Book Idea Check pipeline.

Provides:
  - llm_call_with_fallback() – a resilient LLM caller with DeepSeek → Claude fallback.
  - run_stage_1_classify()   – Stage 1: classify an author brief into structured metadata.
  - run_stage_3_synthesize() – Stage 3: synthesize a market briefing from all prior stages.
"""

import json
import logging
import re
from typing import Any

from django.conf import settings
from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek

from book_idea.spec_constants import (
    STAGE_1_SCHEMA,
    STAGE_1_SYSTEM_PROMPT,
    STAGE_3_SCHEMA,
    STAGE_3_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MD_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json … ``` wrappers that LLMs sometimes add despite instructions."""
    match = _MD_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


class _CleanJsonOutputParser(JsonOutputParser):
    """JsonOutputParser that strips markdown fences before parsing."""

    def parse(self, text: str) -> Any:
        cleaned = _strip_markdown_fences(text)
        return super().parse(cleaned)


# ---------------------------------------------------------------------------
# Fallback Helper
# ---------------------------------------------------------------------------


def llm_call_with_fallback(
    stage: int,
    prompt_template: ChatPromptTemplate,
    *,
    primary_temperature: float = 0.3,
    primary_max_tokens: int = 1024,
    prompt_kwargs: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Invoke an LLM chain with automatic fallback.

    Pipeline order:
        1. Primary   – DeepSeek (``primary_temperature``)
        2. Retry     – DeepSeek (temperature=0.1, lower creativity)
        3. Fallback  – Claude   (model chosen by ``stage``)

    Parameters
    ----------
    stage : int
        Pipeline stage number. Determines which Claude model is used as the
        fallback (1 → Haiku, 3 → Sonnet).
    prompt_template : ChatPromptTemplate
        A fully-formed LangChain prompt template.
    primary_temperature : float
        Temperature for the primary DeepSeek call.
    primary_max_tokens : int
        Max tokens for the primary DeepSeek call.
    prompt_kwargs : dict, optional
        Variables to pass into the prompt template (e.g. ``{"author_brief": …}``).

    Returns
    -------
    tuple[dict, str]
        (parsed_json, provider)  where *provider* is ``"deepseek"`` or ``"claude"``.
    """
    if prompt_kwargs is None:
        prompt_kwargs = {}

    # -- Build LLM instances ------------------------------------------------

    deepseek_api_key = settings.DEEPSEEK_API_KEY

    primary_llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=deepseek_api_key,
        temperature=primary_temperature,
        max_tokens=primary_max_tokens,
    )

    retry_llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=deepseek_api_key,
        temperature=0.1,
        max_tokens=primary_max_tokens,
    )

    # Choose Claude model based on pipeline stage
    claude_model = (
        "claude-3-haiku-20240307" if stage == 1
        else "claude-3-sonnet-20240229"
    )
    anthropic_api_key = getattr(settings, "ANTHROPIC_API_KEY", None)

    fallback_llm = ChatAnthropic(
        model=claude_model,
        api_key=anthropic_api_key or "dummy",
        temperature=0.2,
        max_tokens=primary_max_tokens,
    )

    # -- Build chains -------------------------------------------------------

    parser = _CleanJsonOutputParser()

    primary_chain = prompt_template | primary_llm | parser
    retry_chain = prompt_template | retry_llm | parser
    fallback_chain = prompt_template | fallback_llm | parser

    # -- Invoke with manual cascade to track which provider succeeded -------

    provider = "deepseek"
    last_exc: Exception | None = None

    for label, chain in [
        ("deepseek", primary_chain),
        ("deepseek", retry_chain),
        ("claude", fallback_chain),
    ]:
        try:
            result = chain.invoke(prompt_kwargs)
            provider = label
            logger.info(
                "Stage %d succeeded via %s",
                stage,
                provider,
            )
            return result, provider
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Stage %d – %s chain failed: %s",
                stage,
                label,
                exc,
            )

    # All three failed – re-raise the last exception
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stage 1 – Classify the author brief
# ---------------------------------------------------------------------------

_STAGE_1_PROMPT = ChatPromptTemplate.from_messages([
    ("system", STAGE_1_SYSTEM_PROMPT),
    (
        "human",
        "Author's description:\n\n{author_brief}\n\n"
        "Return JSON matching this schema:\n{schema}",
    ),
])


def run_stage_1_classify(author_brief_text: str) -> tuple[dict[str, Any], str]:
    """
    Classify an author's book-idea description into structured metadata.

    Uses the ``STAGE_1_SYSTEM_PROMPT`` and ``STAGE_1_SCHEMA`` from
    :pymod:`book_idea.spec_constants` verbatim.

    Parameters
    ----------
    author_brief_text : str
        Free-text description the author typed about their book idea.

    Returns
    -------
    tuple[dict, str]
        (classification_dict, provider)  where *provider* is
        ``"deepseek"`` or ``"claude"``.
    """
    schema_str = json.dumps(STAGE_1_SCHEMA, indent=2)

    result, provider = llm_call_with_fallback(
        stage=1,
        prompt_template=_STAGE_1_PROMPT,
        primary_temperature=0.2,
        primary_max_tokens=800,
        prompt_kwargs={
            "author_brief": author_brief_text,
            "schema": schema_str,
        },
    )

    logger.info(
        "Stage 1 complete via %s – genre=%s, keywords=%d",
        provider,
        result.get("primary_genre", "?"),
        len(result.get("seed_keywords", [])),
    )

    return result, provider


# ---------------------------------------------------------------------------
# Stage 3 – Synthesize the market briefing
# ---------------------------------------------------------------------------

_STAGE_3_PROMPT = ChatPromptTemplate.from_messages([
    ("system", STAGE_3_SYSTEM_PROMPT),
    (
        "human",
        "AUTHOR'S ORIGINAL DESCRIPTION:\n{author_brief}\n\n"
        "STAGE 1 CLASSIFICATION:\n{stage_1_json}\n\n"
        "MARKET DATA (from Amazon via SerpApi):\n{market_data}\n\n"
        "Return JSON matching this schema:\n{schema}",
    ),
])


def run_stage_3_synthesize(
    author_brief: str,
    stage_1_json: dict[str, Any],
    stage_2_market_data: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """
    Produce a "Book Idea Check" briefing by synthesizing all prior stages.

    Uses ``STAGE_3_SYSTEM_PROMPT`` and ``STAGE_3_SCHEMA`` from
    :pymod:`book_idea.spec_constants` verbatim.

    Parameters
    ----------
    author_brief : str
        The author's original free-text book description.
    stage_1_json : dict
        Structured classification from Stage 1.
    stage_2_market_data : dict
        Amazon market data from Stage 2 (keywords, categories, quality).

    Returns
    -------
    tuple[dict, str]
        (briefing_dict, provider)  where *provider* is
        ``"deepseek"`` or ``"claude"``.
    """
    schema_str = json.dumps(STAGE_3_SCHEMA, indent=2)

    result, provider = llm_call_with_fallback(
        stage=3,
        prompt_template=_STAGE_3_PROMPT,
        primary_temperature=0.4,
        primary_max_tokens=2500,
        prompt_kwargs={
            "author_brief": author_brief,
            "stage_1_json": json.dumps(stage_1_json, indent=2),
            "market_data": json.dumps(stage_2_market_data, indent=2),
            "schema": schema_str,
        },
    )

    logger.info(
        "Stage 3 complete via %s – viability_line=%s",
        provider,
        result.get("viability_line", "?")[:80],
    )

    return result, provider
