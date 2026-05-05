import io
import logging
import re

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 60


def _httpx():
    import httpx

    return httpx


def _pdfplumber():
    import pdfplumber

    return pdfplumber


def download_pdf_bytes(url: str) -> bytes | None:
    httpx = _httpx()
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content
    except Exception as exc:
        logger.warning("Failed to download PDF from %s: %s", url, exc)
        return None


def extract_text_from_bytes(pdf_bytes: bytes) -> str | None:
    pdfplumber = _pdfplumber()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages: list[str] = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
    except Exception as exc:
        logger.warning("Failed to parse PDF bytes: %s", exc)
        return None

    full_text = "\n\n".join(pages)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = re.sub(r"[ \t]+", " ", full_text).strip()
    return full_text or None


def extract_text_from_url(url: str) -> str | None:
    pdf_bytes = download_pdf_bytes(url)
    if pdf_bytes is None:
        return None
    return extract_text_from_bytes(pdf_bytes)