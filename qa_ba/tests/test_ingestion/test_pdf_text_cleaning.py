from __future__ import annotations

from ingestion.processors.text_cleaning import clean_document, clean_text
from ingestion.schema.document_schema import Figure, Section, Table, UnifiedDocument


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_text_cleaning_unicode_and_whitespace() -> None:
    raw = "  This\u00a0is   a  test\u200b text with ﬁ ligature and smart quote “ok”.  "
    out = clean_text(raw, aggressive=False)
    assert "\u00a0" not in out, f"[FAIL] Expected NBSP removed but got: {out!r}"
    assert "\u200b" not in out, f"[FAIL] Expected zero-width character removed but got: {out!r}"
    assert "fi" in out, f"[FAIL] Expected ligature normalized to 'fi' but got: {out!r}"
    _log(
        "Text Cleaning - unicode/whitespace",
        "synthetic vietnamese/unicode text",
        "clean_text",
        "normalize unicode + whitespace",
        f"cleaned={out!r}",
        "PASS",
    )


def test_text_cleaning_ocr_noise_and_newline_normalization() -> None:
    raw = "meth-\nod improves.\n\n\nPage 2 of 10\nUnder review\nhttps://example.com\n"
    out = clean_text(raw, aggressive=True)
    assert "method" in out, f"[FAIL] Expected dehyphenation 'meth-\\nod' -> 'method' but got: {out!r}"
    assert "Under review" not in out, f"[FAIL] Expected preprint boilerplate removed but got: {out!r}"
    assert "https://example.com" not in out, f"[FAIL] Expected standalone URL removed in aggressive mode"
    _log(
        "Text Cleaning - OCR noise",
        "synthetic OCR text",
        "clean_text aggressive",
        "remove boilerplate/dehyphenate URLs",
        f"cleaned={out!r}",
        "PASS",
    )


def test_text_cleaning_on_unified_document() -> None:
    doc = UnifiedDocument(
        title="  Test ﬁ Title  ",
        authors=["Alice\u00a0Nguyen"],
        abstract="This is\n\n\nabstract.",
        sections=[Section(name="  Introduction  ", content="line 1\nline 2", order=0)],
        figures=[Figure(caption="Figure 1:  Accuracy   curve")],
        tables=[Table(caption="Table 1:  Results")],
    )
    out = clean_document(doc, aggressive=False)
    assert out.title != doc.title, "[FAIL] Expected title cleaned but title stayed unchanged"
    assert out.sections[0].name == "Introduction", (
        f"[FAIL] Expected section name trimmed to 'Introduction' but got {out.sections[0].name!r}"
    )
    _log(
        "Text Cleaning - UnifiedDocument",
        "synthetic document",
        "clean_document",
        "clean title/authors/sections/captions",
        f"title={out.title!r}, section_name={out.sections[0].name!r}",
        "PASS",
    )
