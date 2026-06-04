# book_idea/services/notifications.py
"""
PDF generation and email delivery for the Book Idea Check briefing.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from fpdf import FPDF

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    """Replace common unicode chars with ASCII to prevent fpdf latin-1 errors."""
    replacements = {
        '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-',
        '\u2026': '...', '\u2022': '*',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Strip any remaining un-encodable characters
    return text.encode('latin-1', 'replace').decode('latin-1')


def _section_heading(pdf: FPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")


def _section_body(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 7, _sanitize(text))
    pdf.ln(2)


def generate_briefing_pdf(validated_json: dict) -> bytes:
    """
    Build a clean 1-page PDF briefing from the validated Stage-3 JSON.
    Returns raw PDF bytes.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # --- Title ---
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Book Idea Check - Briefing", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    # --- Viability ---
    _section_heading(pdf, "Viability")
    _section_body(pdf, validated_json.get("viability_line", "N/A"))

    # --- Genre Summary ---
    _section_heading(pdf, "Genre Summary")
    _section_body(pdf, validated_json.get("genre_summary", "N/A"))

    # --- Top Keywords ---
    _section_heading(pdf, "Top Keywords")
    keywords = validated_json.get("top_keywords", [])
    if keywords:
        for kw in keywords:
            phrase = kw.get("phrase", "")
            why = kw.get("why", "")
            _section_body(pdf, f"  * {phrase} - {why}")
    else:
        _section_body(pdf, "N/A")

    # --- Recommended Categories ---
    _section_heading(pdf, "Recommended Categories")
    categories = validated_json.get("recommended_categories", [])
    _section_body(pdf, ", ".join(categories) if categories else "N/A")

    # --- Competitive Snapshot ---
    _section_heading(pdf, "Competitive Snapshot")
    _section_body(pdf, validated_json.get("competitive_snapshot", "N/A"))

    # --- Draft Description ---
    _section_heading(pdf, "Draft Book Description")
    _section_body(pdf, validated_json.get("draft_description", "N/A"))

    # --- Next Step ---
    _section_heading(pdf, "Recommended Next Step")
    _section_body(pdf, validated_json.get("next_step", "N/A"))

    return pdf.output()


# ---------------------------------------------------------------------------
# Email Delivery
# ---------------------------------------------------------------------------


def send_briefing_email_background(email: str, name: str, pdf_bytes: bytes) -> None:
    """
    Send the briefing PDF to the user via Django's email backend.
    Designed to be called from a background thread.
    """
    try:
        msg = EmailMessage(
            subject=f"Your Book Idea Check Briefing, {name}",
            body=(
                f"Hi {name},\n\n"
                "Please find your Book Idea Check briefing attached.\n\n"
                "Best regards,\nHarmony Publishing AI"
            ),
            from_email=settings.EMAIL_SMTP_USER,
            to=[email],
        )
        msg.attach(
            "book_idea_check_briefing.pdf",
            pdf_bytes,
            "application/pdf",
        )
        msg.send()

        logger.info("Briefing email sent to %s", email)
    except Exception:
        logger.exception("Failed to send briefing email to %s", email)
