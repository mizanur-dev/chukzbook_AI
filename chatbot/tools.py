"""
Custom LangChain tools for the Premium Publishing Consultant Agent.
"""

import io
import smtplib
import tempfile
from email.message import EmailMessage

from django.conf import settings
from fpdf import FPDF
from langchain_core.tools import tool


# ---------- 1. SerpApi Search Tool ----------
# We use the built-in LangChain wrapper for SerpApi.
# It reads SERPAPI_API_KEY from the environment automatically,
# but we also expose a factory so the view can inject the key.

from langchain_community.utilities import SerpAPIWrapper

def get_search_tool():
    """Return a LangChain-compatible SerpApi search tool."""
    from langchain_core.tools import Tool

    search = SerpAPIWrapper(serpapi_api_key=settings.SERPAPI_API_KEY)
    return Tool(
        name="Search",
        func=search.run,
        description=(
            "Useful for searching the web for book market trends, "
            "competitor books, and publishing industry data."
        ),
    )


# ---------- 2. Create & Email PDF Tool ----------

def _build_pdf(report_text: str) -> bytes:
    """Generate a simple PDF from plain text and return raw bytes."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, report_text)
    return pdf.output()  # returns bytes


def _send_email_with_pdf(recipient: str, pdf_bytes: bytes) -> None:
    """Send *pdf_bytes* as an attachment to *recipient* via SMTP."""
    msg = EmailMessage()
    msg["Subject"] = "Your Publishing Consultant Report"
    msg["From"] = settings.EMAIL_SMTP_USER
    msg["To"] = recipient
    msg.set_content("Please find your publishing report attached.")
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename="publishing_report.pdf",
    )

    with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT) as server:
        server.starttls()
        server.login(settings.EMAIL_SMTP_USER, settings.EMAIL_SMTP_PASSWORD)
        server.send_message(msg)


def make_email_pdf_tool(user_email: str):
    """
    Factory that returns a @tool-decorated function with the user_email
    baked into its closure so the agent never needs to ask for it.
    """

    @tool
    def Create_and_Email_PDF(report_text: str) -> str:
        """Generate a PDF from the report text and email it to the user.
        Use this tool ONLY after you have written the full publishing brief.
        Input: the full report text to convert to PDF.
        Output: a confirmation string.
        """
        pdf_bytes = _build_pdf(report_text)
        _send_email_with_pdf(user_email, pdf_bytes)
        return f"Report emailed successfully to {user_email}"

    return Create_and_Email_PDF
