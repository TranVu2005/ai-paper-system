from __future__ import annotations

from ingestion.processors.section_splitter import split_sections
from ingestion.schema.document_schema import UnifiedDocument


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_section_splitter_core_sections() -> None:
    full_text = (
        "Abstract\nThis paper studies a robust ingestion pipeline with detailed evaluation.\n\n"
        "1 Introduction\nThis introduction paragraph is intentionally longer than twenty characters.\n\n"
        "2 Method\nThis method paragraph explains data preparation, extraction, and validation flow.\n\n"
        "3 Results\nThis result paragraph summarizes section, table, figure, and formula statistics.\n\n"
        "4 Conclusion\nThis conclusion paragraph captures limitations and future quality improvements.\n"
    )
    doc = UnifiedDocument(source_type="pdf", full_text=full_text, title="Section Split Test")
    out = split_sections(doc, force=True)
    names = [s.name.lower() for s in out.sections]
    assert any("introduction" in n for n in names), (
        f"[FAIL] Expected Introduction section but got names={names}"
    )
    assert any("method" in n for n in names), (
        f"[FAIL] Expected Method section but got names={names}"
    )
    assert any("result" in n for n in names), (
        f"[FAIL] Expected Result section but got names={names}"
    )
    assert any("conclusion" in n for n in names), (
        f"[FAIL] Expected Conclusion section but got names={names}"
    )
    _log(
        "Section Splitter - core headings",
        "synthetic 2-column-like text",
        "split_sections(force=True)",
        "detect Intro/Method/Result/Conclusion",
        f"section_count={len(out.sections)}, names={names}",
        "PASS",
    )


def test_section_splitter_duplicate_and_missing_headings_fallback() -> None:
    full_text = (
        "Introduction\nFirst intro paragraph.\n\n"
        "Introduction\nDuplicate intro paragraph.\n\n"
        "This is a long body paragraph without clear headings. " * 30
    )
    doc = UnifiedDocument(source_type="pdf", full_text=full_text, title="Fallback Split Test")
    out = split_sections(doc, force=True)
    assert len(out.sections) >= 1, (
        f"[FAIL] Expected at least one section from fallback splitting but got {len(out.sections)}"
    )
    _log(
        "Section Splitter - duplicate/missing headings",
        "synthetic duplicate headings text",
        "split_sections(force=True)",
        "graceful fallback with >=1 section",
        f"section_count={len(out.sections)}, types={[s.section_type for s in out.sections]}",
        "PASS",
    )
