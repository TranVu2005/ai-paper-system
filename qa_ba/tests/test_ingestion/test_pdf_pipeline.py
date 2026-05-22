from __future__ import annotations

from pathlib import Path

from ingestion import pipeline
from ingestion.schema.document_schema import Figure, Section, Table, UnifiedDocument


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_pipeline_full_flow_with_stubbed_loader(
    monkeypatch,
    sample_pdf_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    image_src = tmp_path / "fig.png"
    image_src.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x89\x8f\x82"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def fake_step_load(file_path: str, file_type: str) -> UnifiedDocument:
        return UnifiedDocument(
            title="Pipeline Integration Test",
            source_type="html",
            source_file=file_path,
            full_text=(
                "Abstract\nA short abstract.\n\n"
                "1 Introduction\nStudy intro with formula $x+y$.\n\n"
                "2 Method\nMethod content.\n\n"
                "3 Results\nResults include Table 1 and Figure 1.\n\n"
                "4 Conclusion\nConclusion content."
            ),
            sections=[
                Section(name="Introduction", content="Formula $x+y$ appears.", order=0),
                Section(name="Method", content="Method details.", order=1),
            ],
            tables=[Table(caption="Table 1: Results", headers=["Metric", "Value"], data=[["Acc", "0.9"]])],
            figures=[Figure(caption="Figure 1: Overview architecture", path=str(image_src), index=1)],
        )

    monkeypatch.setattr(pipeline, "_step_load", fake_step_load)

    in_pdf = sample_pdf_paths["raw"]
    result = pipeline.run_pipeline(
        file_path=str(in_pdf),
        dry_run_enrich=True,
        use_lm=False,
        multimodal_output_root=str(tmp_path / "mm"),
    )
    assert result.success, (
        f"[FAIL] Expected pipeline success but failed_at={result.failed_at}, errors={result.errors}"
    )
    assert result.doc is not None, "[FAIL] Expected result.doc not None for successful pipeline run"
    assert len(result.doc.sections) > 0, "[FAIL] Expected non-empty sections after full pipeline"
    _log(
        "PDF Pipeline - full flow",
        str(in_pdf),
        "detect->load->split->clean->parse->normalize->enrich",
        "success with structured output",
        (
            f"success={result.success}, failed_at={result.failed_at}, "
            f"sections={len(result.doc.sections)}, tables={len(result.doc.tables)}, "
            f"figures={len(result.doc.figures)}, formulas={len(result.doc.formulas)}"
        ),
        "PASS",
    )


def test_pipeline_unsupported_file_failure(tmp_path: Path) -> None:
    bad = tmp_path / "unsupported.txt"
    bad.write_text("unsupported")
    result = pipeline.run_pipeline(file_path=str(bad), dry_run_enrich=True)
    assert not result.success, "[FAIL] Expected unsupported file pipeline run to fail"
    assert result.failed_at == pipeline.STEP_DETECT, (
        f"[FAIL] Expected failed_at='detect' but got {result.failed_at}"
    )
    _log(
        "PDF Pipeline - unsupported file",
        str(bad),
        "Run ingestion pipeline",
        "graceful failure at detect step",
        f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}",
        "PASS",
    )


def test_pipeline_corrupted_pdf_graceful_failure(tmp_path: Path) -> None:
    corrupted = tmp_path / "synthetic_corrupted.pdf"
    corrupted.write_bytes(b"%PDF-1.4\nbroken synthetic content")
    result = pipeline.run_pipeline(file_path=str(corrupted), dry_run_enrich=True)
    # Corrupted input is considered gracefully handled when:
    # 1) pipeline fails early (detect/load), OR
    # 2) pipeline succeeds but returns an effectively empty document.
    graceful = False
    if not result.success and result.failed_at in {pipeline.STEP_DETECT, pipeline.STEP_LOAD}:
        graceful = True
    elif result.success and result.doc is not None:
        doc = result.doc
        graceful = (
            (doc.page_count or 0) == 0
            and len(doc.full_text.strip()) == 0
            and len(doc.sections) == 0
            and len(doc.tables) == 0
            and len(doc.figures) == 0
            and len(doc.formulas) == 0
        )

    assert graceful, (
        "[FAIL] Expected graceful corrupted-PDF handling "
        f"but got success={result.success}, failed_at={result.failed_at}, errors={result.errors}"
    )
    _log(
        "PDF Pipeline - corrupted PDF",
        str(corrupted),
        "Run ingestion pipeline",
        "graceful failure",
        f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}",
        "PASS",
    )
