from __future__ import annotations

from ingestion.processors import metadata_enricher
from ingestion.schema.document_schema import UnifiedDocument


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_metadata_enricher_dry_run_no_network() -> None:
    doc = UnifiedDocument(title="Dry Run Paper", authors=["A"], source_type="pdf")
    out = metadata_enricher.enrich_metadata(doc, dry_run=True)
    assert out is doc, "[FAIL] Expected dry_run to return the original document object"
    _log(
        "Metadata Enricher - dry run",
        "synthetic document",
        "enrich_metadata(dry_run=True)",
        "no network call and return original doc",
        "returned same object",
        "PASS",
    )


def test_metadata_enricher_fill_missing_fields_with_mock(monkeypatch) -> None:
    doc = UnifiedDocument(title="A Study on Testability", authors=["Alice Nguyen"], source_type="pdf")

    monkeypatch.setattr(
        metadata_enricher,
        "_query_by_title",
        lambda title, authors, mailto: {
            "doi": "10.1000/test.1",
            "title": "A Study on Testability",
            "authors": ["Alice Nguyen"],
            "year": 2024,
            "journal": "Journal of Testing",
            "publisher": "QA Press",
            "volume": "12",
            "issue": "3",
            "pages": "10-20",
            "citation_count": 5,
        },
    )

    out = metadata_enricher.enrich_metadata(doc, dry_run=False)
    assert out.doi == "10.1000/test.1", f"[FAIL] Expected DOI filled but got doi={out.doi}"
    assert out.year == 2024, f"[FAIL] Expected year=2024 but got year={out.year}"
    assert out.journal == "Journal of Testing", (
        f"[FAIL] Expected journal='Journal of Testing' but got journal={out.journal}"
    )
    _log(
        "Metadata Enricher - mocked fill",
        "synthetic missing metadata",
        "enrich_metadata with mocked query",
        "title/authors/year/doi/journal filled",
        f"doi={out.doi}, year={out.year}, journal={out.journal}, citations={out.citation_count}",
        "PASS",
    )
