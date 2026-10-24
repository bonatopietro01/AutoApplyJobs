"""Render matching local PDF and DOCX drafts from one final letter text."""

from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path


def render_letter_document(letter_text: str, file_format: str) -> bytes:
    if file_format not in {"pdf", "docx"}:
        raise ValueError("Formato documento non valido.")
    if not isinstance(letter_text, str) or not letter_text.strip() or len(letter_text) > 60_000:
        raise ValueError("Il testo della cover letter non e valido.")

    from docx import Document
    from docx.shared import Inches, Pt
    from reportlab import __file__ as reportlab_file
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    lines = letter_text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    if file_format == "docx":
        document = Document()
        section = document.sections[0]
        section.top_margin = section.bottom_margin = Inches(0.8)
        section.left_margin = section.right_margin = Inches(0.85)
        normal = document.styles["Normal"]
        normal.font.name = "Arial"
        normal.font.size = Pt(10.5)
        for line in lines:
            document.add_paragraph(line)
        docx_buffer = BytesIO()
        document.save(docx_buffer)
        return docx_buffer.getvalue()

    font_name = "JobLetterVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab_file).resolve().parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    pdf_buffer = BytesIO()
    pdf = SimpleDocTemplate(
        pdf_buffer, pagesize=A4, rightMargin=54, leftMargin=54,
        topMargin=56, bottomMargin=56,
        title="Cover letter", author="Job Letter Studio",
    )
    style = ParagraphStyle(
        "Letter", fontName=font_name, fontSize=10.5, leading=15,
        textColor=colors.HexColor("#1d2d27"), alignment=TA_LEFT,
    )
    story = []
    for line in lines:
        if line:
            story.append(Paragraph(escape(line).replace("\t", "    "), style))
        else:
            story.append(Spacer(1, 8))
    pdf.build(story)
    return pdf_buffer.getvalue()


def render_letter_documents(letter_text: str) -> tuple[bytes, bytes]:
    """Compatibility wrapper for explicit paired exports only."""
    return render_letter_document(letter_text, "pdf"), render_letter_document(letter_text, "docx")
