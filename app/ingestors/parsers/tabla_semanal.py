"""Parser for the Cámara de Diputados weekly agenda PDF ("Tabla Semanal").

Pure functions over PDF bytes; no DB. Produces a list of dicts shaped for
``upsert_calendar_event`` (``app/services/write.py``). The CLI runner is
responsible for resolving ``bill_id`` from each row's ``bulletin_number``
before writing.

The PDF has one bordered table per page, six columns wide: the three session
days of the week alternate between a wide "title" column and a narrow "meta"
column. The first row carries the day header (e.g. "LUNES 22 / Sesión
ordinaria de 17:00 a 19:00 horas"); subsequent rows are tabled items. Page 2
is a continuation with no header row — column-to-date mapping is carried
forward from page 1.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.models.enums import (
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
)

logger = logging.getLogger(__name__)

SANTIAGO = ZoneInfo("America/Santiago")

_DAY_HEADER_RE = re.compile(
    r"^(LUNES|MARTES|MI[ÉE]RCOLES|JUEVES|VIERNES|S[ÁA]BADO|DOMINGO)\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})[:.](\d{2})\s*(?:a|hasta|-)\s*(\d{1,2})[:.](\d{2})",
    re.IGNORECASE,
)
_TIME_SINGLE_RE = re.compile(r"(\d{1,2})[:.](\d{2})")
_WEEKLY_DATES_RE = re.compile(
    r"de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_BOLETIN_RE = re.compile(r"(\d{4,5}-\d{2})")
_BOLETIN_BLOCK_RE = re.compile(
    r"Bolet[íi]n(?:es)?\s+N(?:os|°|os|º|º)?\s*([\d\s,y\.\-]+)",
    re.IGNORECASE,
)
_TRAILING_BOLETIN_RE = re.compile(
    r"\s*Bolet[íi]n(?:es)?\s+N(?:os|°|os|º|º)?\s*[\d\s,y\.\-]*"
    r"(?:,?\s*refundidos?)?\.?\s*$",
    re.IGNORECASE,
)
_CEI_NUMBER_RE = re.compile(r"\bCEIs?\s+(\d+(?:\s+y\s+\d+)*)", re.IGNORECASE)

_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_DAY_COLS = (0, 2, 4)


def parse_tabla_semanal_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a Tabla Semanal PDF into a flat list of event dicts.

    Each dict carries the input shape that ``upsert_calendar_event`` accepts,
    plus two extras the CLI runner consumes before writing:

    - ``bulletin_number``: the primary bolet̄ín for the row (``None`` if not
      a bill row). The runner uses this to resolve ``bill_id``.
    - ``related_bulletins``: extra bolet̄ínes from a refundidos row, already
      included as a sentence inside ``description`` so the upsert path needs
      no awareness of them.
    """
    import pdfplumber

    events: list[dict[str, Any]] = []
    month: int | None = None
    year: int | None = None
    day_dates: dict[int, date] = {}
    day_starts: dict[int, datetime] = {}
    day_ends: dict[int, datetime | None] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            if month is None or year is None:
                page_text = page.extract_text() or ""
                detected = _detect_month_year(page_text)
                if detected is not None:
                    month, year = detected

            for table in page.extract_tables() or []:
                if not table or len(table[0]) < 6:
                    continue
                rows = [[_norm_cell(c) for c in row] for row in table]
                first_row = rows[0]

                is_header_row = any(
                    _DAY_HEADER_RE.match(first_row[col]) for col in _DAY_COLS
                )

                if is_header_row:
                    if month is None or year is None:
                        logger.warning(
                            "Tabla Semanal: month/year missing for header row"
                        )
                        continue
                    for col in _DAY_COLS:
                        info = _parse_day_header(first_row[col], month, year)
                        if info is None:
                            continue
                        day_dates[col] = info["date"]
                        day_starts[col] = info["starts_at"]
                        day_ends[col] = info["ends_at"]
                        events.append(_build_sesion_event(info, first_row[col]))
                    data_rows = rows[1:]
                else:
                    data_rows = rows

                for row in data_rows:
                    for col in _DAY_COLS:
                        meta_col = col + 1
                        title_cell = row[col] if col < len(row) else ""
                        meta_cell = row[meta_col] if meta_col < len(row) else ""
                        if not title_cell and not meta_cell:
                            continue
                        session_date = day_dates.get(col)
                        starts_at = day_starts.get(col)
                        if session_date is None or starts_at is None:
                            logger.warning(
                                "Tabla Semanal: dropping row in col %d "
                                "without known session date",
                                col,
                            )
                            continue
                        event = _classify_row(
                            title_cell,
                            meta_cell,
                            session_date=session_date,
                            starts_at=starts_at,
                            ends_at=day_ends.get(col),
                        )
                        if event is not None:
                            events.append(event)

    return events


def _detect_month_year(text: str) -> tuple[int, int] | None:
    for match in _WEEKLY_DATES_RE.finditer(text):
        month_name = _strip_accents(match.group(1)).lower()
        if month_name in _MONTHS:
            return _MONTHS[month_name], int(match.group(2))
    return None


def _parse_day_header(text: str, month: int, year: int) -> dict[str, Any] | None:
    header_match = _DAY_HEADER_RE.search(text)
    if not header_match:
        return None
    day_of_month = int(header_match.group(2))
    try:
        session_date = date(year, month, day_of_month)
    except ValueError:
        return None

    starts_t: time | None = None
    ends_t: time | None = None
    range_match = _TIME_RANGE_RE.search(text)
    if range_match:
        starts_t = time(int(range_match.group(1)), int(range_match.group(2)))
        ends_t = time(int(range_match.group(3)), int(range_match.group(4)))
    else:
        single_match = _TIME_SINGLE_RE.search(text)
        if single_match:
            starts_t = time(int(single_match.group(1)), int(single_match.group(2)))

    if starts_t is None:
        return None

    starts_at = datetime.combine(session_date, starts_t, tzinfo=SANTIAGO)
    ends_at = (
        datetime.combine(session_date, ends_t, tzinfo=SANTIAGO)
        if ends_t is not None
        else None
    )
    return {
        "date": session_date,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def _build_sesion_event(info: dict[str, Any], header_cell: str) -> dict[str, Any]:
    session_date: date = info["date"]
    return {
        "kind": CalendarEventKind.SESION,
        "starts_at": info["starts_at"],
        "ends_at": info["ends_at"],
        "title": f"Sesión ordinaria — Cámara de Diputados — {session_date.isoformat()}",
        "description": header_cell or None,
        "chamber_type": ChamberType.DEPUTIES,
        "source": CalendarEventSource.TABLA_SEMANAL,
        "external_ref": f"tabla-semanal:sesion:{session_date.isoformat()}",
        "bulletin_number": None,
        "related_bulletins": [],
    }


def _classify_row(
    title_cell: str,
    meta_cell: str,
    *,
    session_date: date,
    starts_at: datetime,
    ends_at: datetime | None,
) -> dict[str, Any] | None:
    if not title_cell.strip():
        return None

    boletines = _extract_boletines(title_cell)
    title_text = _title_from_cell(title_cell)
    lc = _strip_accents(title_cell + " " + meta_cell).lower()

    primary_bulletin: str | None = None
    related: list[str] = []

    if boletines:
        kind = CalendarEventKind.VOTACION
        primary_bulletin = boletines[0]
        related = boletines[1:]
        ext_suffix = primary_bulletin
    elif "acusacion constitucional" in lc:
        kind = CalendarEventKind.ACUSACION_CONSTITUCIONAL
        ext_suffix = f"acusacion-{_slugify(title_text)}"
    elif (
        "comision especial investigadora" in lc
        or "informe de la comision" in lc
        or re.search(r"\bcei\b", lc)
    ):
        kind = CalendarEventKind.INFORME_CEI
        cei_match = _CEI_NUMBER_RE.search(title_cell + " " + meta_cell)
        if cei_match:
            cei_label = re.sub(r"\s+", "-", cei_match.group(1).strip().lower())
            ext_suffix = f"cei-{cei_label}"
        else:
            ext_suffix = f"cei-{_slugify(title_text)}"
    else:
        kind = CalendarEventKind.OTRO
        ext_suffix = _slugify(title_text)

    description_parts = [title_cell]
    if meta_cell:
        description_parts.append(meta_cell)
    if related:
        word = "boletín" if len(related) == 1 else "boletines"
        description_parts.append(f"Refundidos con {word} {', '.join(related)}")
    description = "\n\n".join(part for part in description_parts if part) or None

    return {
        "kind": kind,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "title": title_text[:300],
        "description": description,
        "chamber_type": ChamberType.DEPUTIES,
        "source": CalendarEventSource.TABLA_SEMANAL,
        "external_ref": (f"tabla-semanal:{ext_suffix}:{session_date.isoformat()}"),
        "bulletin_number": primary_bulletin,
        "related_bulletins": related,
    }


def _extract_boletines(cell: str) -> list[str]:
    if not cell:
        return []
    mended = _mend_split_numbers(cell)
    block = _BOLETIN_BLOCK_RE.search(mended)
    if block:
        return _dedupe(_BOLETIN_RE.findall(block.group(1)))
    return _dedupe(_BOLETIN_RE.findall(mended))


def _mend_split_numbers(text: str) -> str:
    """Rejoin numeric sequences split by a PDF line break.

    pdfplumber preserves the natural hyphen of "10986-24" when the cell is
    wrapped between the two digits — the cell text becomes "10986-\n24". The
    boletín extractor needs to see "10986-24" to match. Drop whitespace that
    sits between a hyphen and a following digit.
    """
    return re.sub(r"-\s+(?=\d)", "-", text)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _title_from_cell(cell: str) -> str:
    text = _mend_split_numbers(cell).replace("\n", " ").strip()
    text = _TRAILING_BOLETIN_RE.sub("", text)
    return text.strip(" .,")


def _norm_cell(cell: str | None) -> str:
    if not cell:
        return ""
    cell = cell.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in cell.split("\n")]
    return "\n".join(line for line in lines if line)


def _strip_accents(text: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def _slugify(text: str, *, max_len: int = 60) -> str:
    base = _strip_accents(text).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:max_len] or "item"
