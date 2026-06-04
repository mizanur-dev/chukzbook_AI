import logging
import threading

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import IdeaSubmission
from .serializers import BookIdeaCheckSerializer
from .services.ai_engine import run_stage_1_classify, run_stage_3_synthesize
from .services.market_data import fetch_amazon_data
from .services.notifications import generate_briefing_pdf, send_briefing_email_background
from .services.validator import validate_briefing
from .throttles import EmailRateThrottle, IPRateThrottle

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class BookIdeaCheckAPIView(APIView):
    """
    POST /api/book-idea/check/
    Body: { "name": "...", "email": "...", "author_brief_text": "..." }

    Runs the 4-stage pipeline and returns the validated briefing JSON.
    Emails a PDF copy in the background.
    """
    throttle_classes = [EmailRateThrottle, IPRateThrottle]

    def post(self, request):
        serializer = BookIdeaCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        name = serializer.validated_data["name"]
        email = serializer.validated_data["email"]
        author_brief = serializer.validated_data["author_brief_text"]
        ip_address = _get_client_ip(request)

        try:
            # Stage 1 – Genre classification (with DeepSeek → Claude fallback)
            stage_1_json, provider = run_stage_1_classify(author_brief)

            # Stage 2 – Amazon market data (concurrent SerpApi + caching)
            stage_2_data = fetch_amazon_data(stage_1_json)

            # Stage 3 – Analyst briefing synthesis
            stage_3_json, provider = run_stage_3_synthesize(
                author_brief, stage_1_json, stage_2_data,
            )

            # Stage 4 – Anti-hallucination validation
            validated_json = validate_briefing(stage_3_json, stage_2_data, author_brief)

            # Persist to DB
            IdeaSubmission.objects.create(
                name=name,
                email=email,
                author_brief_text=author_brief,
                ip_address=ip_address,
                generated_json=validated_json,
                llm_provider_used=provider,
            )

            # Generate PDF and email in background thread
            pdf_bytes = generate_briefing_pdf(validated_json)
            threading.Thread(
                target=send_briefing_email_background,
                args=(email, name, pdf_bytes),
                daemon=True,
            ).start()

            return Response(validated_json, status=status.HTTP_200_OK)

        except Exception:
            logger.exception("Pipeline failed for %s", email)

            # Save the lead even on failure
            IdeaSubmission.objects.create(
                name=name,
                email=email,
                author_brief_text=author_brief,
                ip_address=ip_address,
                generated_json=None,
                llm_provider_used=None,
            )

            return Response(
                {
                    "error": (
                        "Our analysis tool is busy right now. "
                        "We've saved your idea and a specialist will reach out."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
