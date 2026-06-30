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


def extract_comparado_text_from_bytes(pdf_bytes: bytes) -> str | None:
    """Extract only the right column (amendment instructions) from a comparado.

    Chilean comparados are bordered two-column tables: left column reproduces
    the existing law text verbatim, right column lists the amendment
    instructions. Sending the left column to the LLM is pure noise — and on
    long bills it's what blows the context window.

    pdfplumber's grid detection treats the comparado as a 4-column table
    (the two visible columns plus thin gutter columns around the divider),
    and merged body cells span across the gutter — so cell index alone is
    unreliable. We use each cell's bounding box instead: a cell counts as
    "right column" iff its left edge sits at or past the page midpoint.
    Per-page repeating headers ("TEXTO LEGAL VIGENTE" / "TEXTO APROBADO …")
    and the full-width title banner are filtered separately.
    """
    pdfplumber = _pdfplumber()
    cells: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_mid_x = page.width / 2
                for table in page.find_tables():
                    extracted = table.extract()
                    for row_idx, row in enumerate(table.rows):
                        if row_idx >= len(extracted):
                            continue
                        extracted_row = extracted[row_idx]
                        for cell_idx, cell_bbox in enumerate(row.cells):
                            if cell_bbox is None:
                                continue
                            x0 = cell_bbox[0]
                            if x0 < page_mid_x:
                                continue
                            if cell_idx >= len(extracted_row):
                                continue
                            text = (extracted_row[cell_idx] or "").strip()
                            if not text:
                                continue
                            upper = text.upper()
                            if upper == "TEXTO LEGAL VIGENTE" or upper.startswith(
                                "TEXTO APROBADO"
                            ):
                                continue
                            cells.append(text)
    except Exception as exc:
        logger.warning("Failed to parse comparado PDF: %s", exc)
        return None
    if not cells:
        return None
    text = "\n\n".join(cells)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text or None


def extract_comparado_text_from_url(url: str) -> str | None:
    pdf_bytes = download_pdf_bytes(url)
    if pdf_bytes is None:
        return None
    return extract_comparado_text_from_bytes(pdf_bytes)
