import io
import logging
import re
import signal
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 60

# pdfplumber's table/line detection can pathologically hang on a single
# malformed page (seen: a 412-page comparado where pages 1-188 took ~0.3s
# each, then page 189 hung the whole Lambda invocation to its 300s hard
# kill). Bound each page individually, and cap total extraction time so a
# PDF with several merely-slow pages can't add up past the budget either.
PAGE_DEADLINE_SECONDS = 15
EXTRACTION_BUDGET_SECONDS = 180


class _PageTimeout(Exception):
    pass


@contextmanager
def _page_deadline(seconds: int = PAGE_DEADLINE_SECONDS):
    def _handler(signum, frame):
        raise _PageTimeout()

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _httpx():
    import httpx

    return httpx


def _pdfplumber():
    import pdfplumber

    return pdfplumber


def download_pdf_bytes(url: str, *, attempts: int = 3) -> bytes | None:
    httpx = _httpx()
    t0 = time.monotonic()

    # The senado document microservice drops TLS connections under concurrent
    # load (UNEXPECTED_EOF_WHILE_READING); retry the transient case with backoff.
    for i in range(attempts):
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                logger.info(
                    "Downloaded PDF from %s in %.1fs (%d bytes, attempt %d)",
                    url,
                    time.monotonic() - t0,
                    len(response.content),
                    i + 1,
                )
                return response.content
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if i == attempts - 1:
                logger.warning("Failed to download PDF from %s: %s", url, exc)
                return None
            time.sleep(0.5 * 2**i)  # 0.5s, 1s
    return None


def extract_text_from_bytes(pdf_bytes: bytes) -> str | None:
    pdfplumber = _pdfplumber()
    t0 = time.monotonic()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            logger.info("Extracting text from %d-page PDF", len(pdf.pages))
            pages: list[str] = []
            for i, page in enumerate(pdf.pages):
                if time.monotonic() - t0 > EXTRACTION_BUDGET_SECONDS:
                    logger.warning(
                        "Text extraction budget exceeded after page %d/%d, stopping early",
                        i,
                        len(pdf.pages),
                    )
                    break
                try:
                    with _page_deadline():
                        text = page.extract_text()
                except _PageTimeout:
                    logger.warning(
                        "  page %d/%d: extract_text exceeded %ds, skipping page",
                        i + 1,
                        len(pdf.pages),
                        PAGE_DEADLINE_SECONDS,
                    )
                    continue
                finally:
                    # pdfplumber caches every parsed page for the life of the
                    # document; on multi-hundred-page PDFs that pins the Lambda
                    # at its memory ceiling and stalls it in kernel reclaim.
                    page.close()
                if text:
                    pages.append(text)
                if (i + 1) % 20 == 0:
                    logger.info(
                        "  ...page %d/%d after %.1fs",
                        i + 1,
                        len(pdf.pages),
                        time.monotonic() - t0,
                    )
    except Exception as exc:
        logger.warning("Failed to parse PDF bytes: %s", exc)
        return None

    logger.info("Extracted text in %.1fs", time.monotonic() - t0)
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
    t0 = time.monotonic()
    cells: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            logger.info("Extracting comparado from %d-page PDF", len(pdf.pages))
            for i, page in enumerate(pdf.pages):
                if time.monotonic() - t0 > EXTRACTION_BUDGET_SECONDS:
                    logger.warning(
                        "Comparado extraction budget exceeded after page %d/%d, stopping early",
                        i,
                        len(pdf.pages),
                    )
                    break
                page_t0 = time.monotonic()
                page_mid_x = page.width / 2
                try:
                    with _page_deadline():
                        tables = page.find_tables()
                        page_cells: list[str] = []
                        for table in tables:
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
                                    if (
                                        upper == "TEXTO LEGAL VIGENTE"
                                        or upper.startswith("TEXTO APROBADO")
                                    ):
                                        continue
                                    page_cells.append(text)
                except _PageTimeout:
                    logger.warning(
                        "  page %d/%d: exceeded %ds, skipping page",
                        i + 1,
                        len(pdf.pages),
                        PAGE_DEADLINE_SECONDS,
                    )
                    continue
                finally:
                    # See extract_text_from_bytes: release the page cache or
                    # long comparados pin the Lambda at its memory ceiling.
                    page.close()
                cells.extend(page_cells)
                logger.info(
                    "  page %d/%d: processed in %.1fs (%d tables), total %.1fs",
                    i + 1,
                    len(pdf.pages),
                    time.monotonic() - page_t0,
                    len(tables),
                    time.monotonic() - t0,
                )
    except Exception as exc:
        logger.warning("Failed to parse comparado PDF: %s", exc)
        return None
    logger.info("Extracted comparado in %.1fs", time.monotonic() - t0)
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
