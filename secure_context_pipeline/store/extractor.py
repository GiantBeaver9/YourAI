"""Document text extraction behind one interface — TXT native, PDF/DOCX via optional libs.

The interface is the point (extensibility); heavy layout parsing is designed-not-built. Scoping
assumption: extractable-text documents (a PDF page with no text layer -> quarantine, not OCR).
"""

from __future__ import annotations

import io


def extract_text(data: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith((".txt", ".md", ".csv")):
        return data.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        return _extract_pdf(data)
    if name.endswith(".docx"):
        return _extract_docx(data)
    # default: best-effort text decode
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF extraction requires the 'docs' extra (pypdf)") from exc
    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    # a page with no extractable text is an image page -> quarantine (never OCR-and-hope)
    return "\n".join(pages)


def _extract_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DOCX extraction requires the 'docs' extra (python-docx)") from exc
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)
