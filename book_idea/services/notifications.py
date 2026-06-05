"""PDF generation and email delivery for the Book Idea Check briefing."""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from fpdf import FPDF

logger = logging.getLogger(__name__)


_BRAND_PRIMARY = (28, 47, 92) 
_BRAND_ACCENT = (200, 161, 80)   
_BODY_TEXT = (45, 45, 45)         
_MUTED_TEXT = (110, 110, 110)     
_RULE_COLOR = (220, 220, 220)   

_UNICODE_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
    "\u2026": "...", "\u2022": "*",
}


def _sanitize(text: str) -> str:
    """Replace common unicode chars with ASCII to avoid latin-1 errors."""
    for k, v in _UNICODE_REPLACEMENTS.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


class _BriefingPDF(FPDF):
    """Custom PDF with a Harmony Publishing branded header and footer."""

    def header(self):
        self.set_fill_color(*_BRAND_PRIMARY)
        self.rect(0, 0, self.w, 22, style="F")

        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 16)
        self.set_xy(15, 6)
        self.cell(0, 6, "Harmony Publishing", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 10)
        self.set_x(15)
        self.cell(0, 5, "Book Idea Check - Market Briefing", new_x="LMARGIN", new_y="NEXT")

        self.set_y(28)
        self.set_text_color(*_BODY_TEXT)

    def footer(self):
        self.set_y(-15)
        self.set_draw_color(*_RULE_COLOR)
        self.set_line_width(0.2)
        self.line(15, self.get_y(), self.w - 15, self.get_y())

        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*_MUTED_TEXT)
        self.set_y(-12)
        self.cell(
            0, 5,
            f"Harmony Publishing  -  Confidential briefing  -  Page {self.page_no()}",
            align="C",
        )


def _section_heading(pdf: FPDF, title: str) -> None:
    pdf.set_text_color(*_BRAND_PRIMARY)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")

    y = pdf.get_y()
    pdf.set_fill_color(*_BRAND_ACCENT)
    pdf.rect(pdf.l_margin, y, 18, 1.2, style="F")
    pdf.ln(3)

    pdf.set_text_color(*_BODY_TEXT)


def _section_body(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, _sanitize(text))
    pdf.ln(3)


def _bullet_line(pdf: FPDF, phrase: str, why: str) -> None:
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_BRAND_PRIMARY)
    pdf.cell(5, 6, _sanitize("\u2022"))
    pdf.cell(0, 6, _sanitize(phrase), new_x="LMARGIN", new_y="NEXT")

    if why:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*_MUTED_TEXT)
        pdf.set_x(pdf.l_margin + 5)
        pdf.multi_cell(0, 5, _sanitize(why))

    pdf.set_text_color(*_BODY_TEXT)
    pdf.ln(1)


def generate_briefing_pdf(validated_json: dict) -> bytes:
    """Build a professional PDF briefing from the validated Stage-3 JSON."""
    pdf = _BriefingPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(left=15, top=28, right=15)
    pdf.add_page()
    pdf.set_text_color(*_BODY_TEXT)

    _section_heading(pdf, "Viability")
    _section_body(pdf, validated_json.get("viability_line", "N/A"))

    _section_heading(pdf, "Genre Summary")
    _section_body(pdf, validated_json.get("genre_summary", "N/A"))

    _section_heading(pdf, "Top Keywords")
    keywords = validated_json.get("top_keywords", [])
    if keywords:
        for kw in keywords:
            _bullet_line(pdf, kw.get("phrase", ""), kw.get("why", ""))
    else:
        _section_body(pdf, "N/A")

    _section_heading(pdf, "Recommended Categories")
    categories = validated_json.get("recommended_categories", [])
    _section_body(pdf, ", ".join(categories) if categories else "N/A")

    _section_heading(pdf, "Competitive Snapshot")
    _section_body(pdf, validated_json.get("competitive_snapshot", "N/A"))

    _section_heading(pdf, "Draft Book Description")
    _section_body(pdf, validated_json.get("draft_description", "N/A"))

    _section_heading(pdf, "Recommended Next Step")
    _section_body(pdf, validated_json.get("next_step", "N/A"))

    return pdf.output()


def _build_email_body(first_name: str) -> str:
    return (
        f"Hi {first_name},\n\n"
        "Your Book Idea Check is attached. It covers the market for your idea - "
        "the keywords readers search, the categories worth targeting, who you'd be "
        "competing with, and a draft description to get you started.\n\n"
        "If you'd like, one of our publishing specialists can walk you through "
        "turning this into a finished, published book - editing, design, printing, "
        "and distribution handled end to end.\n\n"
        "[ Book a free consultation ]\n\n"
        "- The Harmony Publishing Team\n"
    )


def send_briefing_email_background(email: str, name: str, pdf_bytes: bytes) -> None:
    """Send the briefing PDF via Django's email backend (background-thread safe)."""
    first_name = name.split()[0] if name else "there"
    sender = settings.EMAIL_SMTP_USER
    try:
        msg = EmailMessage(
            subject="Your Book Idea Check from Harmony Publishing",
            body=_build_email_body(first_name),
            from_email=sender,
            to=[email],
        )
        msg.attach(
            "book_idea_check_briefing.pdf",
            pdf_bytes,
            "application/pdf",
        )
        msg.send()
        logger.info("Briefing email sent to %s via %s", email, sender)
    except Exception:
        logger.exception("Failed to send briefing email to %s", email)
