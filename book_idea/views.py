"""
Book Idea Check API view.

Orchestrates the four-stage pipeline defined in the Harmony Publishing
"Book Idea Check" spec:

    Stage 1 - Classify the author brief    (services.ai_engine)
    Stage 2 - Fetch Amazon market data     (services.market_data)
    Stage 3 - Synthesize the briefing      (services.ai_engine)
    Stage 4 - Validate / anti-hallucinate  (services.validator)

Error handling follows Section 8 of the spec:
  * Stage 1 hard failure  -> 400 with "add more detail" message
  * Stage 2 SerpApi error -> pipeline continues; validator adds the
    "live market data unavailable" banner
  * Total LLM failure     -> 200 with GRACEFUL_FALLBACK, viability_line
    replaced by the "tool is busy" copy
  * Throttled requests    -> 429 with the "Talk to a specialist" copy
"""

import copy
import logging
import threading
import time

from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import IdeaSubmission
from .serializers import BookIdeaCheckSerializer
from .services.ai_engine import run_stage_1_classify, run_stage_3_synthesize
from .services.market_data import fetch_amazon_data
from .services.notifications import (
    generate_briefing_pdf,
    send_briefing_email_background,
)
from .services.validator import validate_briefing
from .spec_constants import GRACEFUL_FALLBACK
from .throttles import THROTTLED_MESSAGE, EmailRateThrottle, IPRateThrottle

logger = logging.getLogger(__name__)


_BUSY_VIABILITY_LINE = (
    "Our analysis tool is busy right now. We've saved your idea and a "
    "specialist will reach out \u2014 or chat with us now."
)
_STAGE_1_ERROR_MESSAGE = (
    "We couldn't read your book idea. "
    "Please add a little more detail and try again."
)


def _get_client_ip(request) -> str | None:
    """Return the originating client IP, honouring ``X-Forwarded-For``."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _busy_fallback_response() -> Response:
    """Build the 200 OK "tool is busy" response from ``GRACEFUL_FALLBACK``."""
    payload = copy.deepcopy(GRACEFUL_FALLBACK)
    payload["viability_line"] = _BUSY_VIABILITY_LINE
    return Response(payload, status=status.HTTP_200_OK)


class BookIdeaCheckAPIView(APIView):
    """
    ``POST /api/book-idea/check/``

    Body::

        {
          "name": "Author name",
          "email": "author@example.com",
          "author_brief_text": "Free-text description (>= 50 chars)"
        }

    Returns the validated Stage-3 briefing JSON. A PDF copy is generated
    and emailed in a background thread after the response is sent.
    """

    throttle_classes = [EmailRateThrottle, IPRateThrottle]

    def throttled(self, request, wait):
        """Return the spec-mandated rate-limit message on 429."""
        raise Throttled(wait=wait, detail=THROTTLED_MESSAGE)

    def post(self, request):
        serializer = BookIdeaCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        name = serializer.validated_data["name"]
        email = serializer.validated_data["email"]
        author_brief = serializer.validated_data["author_brief_text"]
        ip_address = _get_client_ip(request)

        start_time = time.time()

        try:
            t1_start = time.time()
            try:
                stage_1_result = run_stage_1_classify(author_brief)
                if stage_1_result is None:
                    raise ValueError("Stage 1 returned None")
                stage_1_json, provider = stage_1_result
                if not stage_1_json:
                    raise ValueError("Stage 1 returned an empty classification")
            except Exception:
                logger.exception("Stage 1 classification failed for %s", email)
                return Response(
                    {"error": _STAGE_1_ERROR_MESSAGE},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            logger.info(
                "Stage 1 OK via %s in %.1fs",
                provider, time.time() - t1_start,
            )

            t2_start = time.time()
            stage_2_data = fetch_amazon_data(stage_1_json)
            logger.info(
                "Stage 2 OK (quality=%s) in %.1fs",
                stage_2_data.get("data_quality", "?"),
                time.time() - t2_start,
            )

            t3_start = time.time()
            stage_3_json, provider = run_stage_3_synthesize(
                author_brief, stage_1_json, stage_2_data,
            )
            logger.info(
                "Stage 3 OK via %s in %.1fs",
                provider, time.time() - t3_start,
            )

            if provider == "fallback":
                logger.warning(
                    "All LLM providers failed; returning busy fallback for %s",
                    email,
                )
                IdeaSubmission.objects.create(
                    name=name,
                    email=email,
                    author_brief_text=author_brief,
                    ip_address=ip_address,
                    generated_json=None,
                    llm_provider_used="fallback",
                )
                return _busy_fallback_response()

            t4_start = time.time()
            validated_json = validate_briefing(
                stage_3_json, stage_2_data, author_brief,
            )
            logger.info("Stage 4 OK in %.1fs", time.time() - t4_start)

            IdeaSubmission.objects.create(
                name=name,
                email=email,
                author_brief_text=author_brief,
                ip_address=ip_address,
                generated_json=validated_json,
                llm_provider_used=provider,
            )

            def run_bg_tasks() -> None:
                """Render the briefing PDF and email it to the author."""
                try:
                    pdf_bytes = generate_briefing_pdf(validated_json)
                    send_briefing_email_background(email, name, pdf_bytes)
                except Exception:
                    logger.exception("Background PDF/email task failed")

            threading.Thread(target=run_bg_tasks, daemon=True).start()

            logger.info(
                "Pipeline complete for %s in %.1fs",
                email, time.time() - start_time,
            )
            return Response(validated_json, status=status.HTTP_200_OK)

        except Exception:
            logger.exception("Pipeline failed for %s", email)
            IdeaSubmission.objects.create(
                name=name,
                email=email,
                author_brief_text=author_brief,
                ip_address=ip_address,
                generated_json=None,
                llm_provider_used=None,
            )
            return _busy_fallback_response()
