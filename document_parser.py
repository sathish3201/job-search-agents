"""Extracts plain text from an uploaded resume file (PDF/DOCX/TXT/MD).
Single responsibility: bytes-in, text-out — kept separate from
profile_loader.py, which owns "read the one configured local RESUME.md
file" and "LLM-parse text into a CandidateProfile." Those are different
concerns: this module doesn't know about RESUME_MD_PATH, and
profile_loader.py doesn't know about file formats."""
from __future__ import annotations

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

# Resumes are small; this is a generous ceiling that mainly guards against
# an accidental huge upload eating memory on a 512MB Render instance, not
# a realistic resume size.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB


class DocumentParseError(ValueError):
    """Raised for anything an API caller should see as a 400, not a 500 —
    unsupported extension, empty extracted text, oversized upload, or a
    genuinely unparseable file."""


def _extension_of(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def extract_text(filename: str, content: bytes) -> str:
    """Dispatches on file extension. Raises DocumentParseError with a
    clear, user-facing message on any failure — never returns an empty
    string silently, since that would flow into parse_profile() and
    produce a garbage CandidateProfile with no indication anything went
    wrong upstream."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentParseError(
            f"File is too large ({len(content) / 1024 / 1024:.1f}MB) — "
            f"max {MAX_UPLOAD_BYTES // 1024 // 1024}MB. Resumes are small; "
            "if this is genuinely your resume, try saving it as a plain .txt."
        )

    ext = _extension_of(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise DocumentParseError(
            f"Unsupported file type '{ext or filename}'. "
            f"Supported: {', '.join(sorted(_ALLOWED_EXTENSIONS))}."
        )

    if ext in (".txt", ".md"):
        text = _extract_text_plain(content)
    elif ext == ".pdf":
        text = _extract_text_pdf(content)
    else:  # .docx
        text = _extract_text_docx(content)

    text = text.strip()
    if not text:
        raise DocumentParseError(
            "Could not extract any text from this file. If it's a scanned or "
            "image-only PDF, this won't work (no OCR support) — try a "
            "text-based PDF, or paste your resume as a .txt file instead."
        )
    return text


def _extract_text_plain(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentParseError(
            "Could not decode this file as UTF-8 text — make sure it's a plain text file."
        )


def _extract_text_pdf(content: bytes) -> str:
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as e:
        raise DocumentParseError(f"Could not read this PDF — it may be corrupted: {e}")
    return "\n\n".join(pages)


def _extract_text_docx(content: bytes) -> str:
    import io

    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError

    try:
        doc = Document(io.BytesIO(content))
    except PackageNotFoundError:
        raise DocumentParseError("Could not read this file as a .docx document — it may be corrupted.")
    return "\n".join(p.text for p in doc.paragraphs)
