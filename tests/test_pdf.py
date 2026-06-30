from pathlib import Path

from app.services.pdf import (
    extract_comparado_text_from_bytes,
    extract_text_from_bytes,
)

FIXTURES = Path(__file__).parent / "fixtures"
COMPARADO_SAMPLE = FIXTURES / "comparado_sample.pdf"


def test_comparado_extraction_keeps_right_column_only():
    text = extract_comparado_text_from_bytes(COMPARADO_SAMPLE.read_bytes())
    assert text is not None
    # Right-column amendment instruction from page 1 must appear.
    assert "Reemplázase el inciso primero" in text
    # The left-column heading "LEY N° 21.681 …" (existing law text, only on
    # the left) must not leak into the output.
    assert "LEY N° 21.681" not in text


def test_comparado_extraction_drops_repeating_header_rows():
    text = extract_comparado_text_from_bytes(COMPARADO_SAMPLE.read_bytes())
    assert text is not None
    assert "TEXTO LEGAL VIGENTE" not in text
    # The right-column header repeats once per page; must be filtered too.
    assert "TEXTO APROBADO EN GENERAL" not in text


def test_comparado_extraction_is_dramatically_smaller_than_full_text():
    raw = COMPARADO_SAMPLE.read_bytes()
    full = extract_text_from_bytes(raw)
    comparado = extract_comparado_text_from_bytes(raw)
    assert full is not None and comparado is not None
    # We expect the right column to be a meaningful fraction of full-page
    # text. For this 10-page sample the right column is roughly half the
    # body. Anything under 70% confirms the left-column noise is gone.
    assert len(comparado) < len(full) * 0.7


def test_comparado_extraction_returns_none_when_no_tables_detected():
    # An empty/garbage PDF body — pdfplumber raises, we swallow and return
    # None so the caller hits the existing ``pdf_extraction_failed`` branch.
    assert extract_comparado_text_from_bytes(b"not a pdf") is None
