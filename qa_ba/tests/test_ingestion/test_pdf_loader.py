from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def _require_pdf_runtime() -> None:
    if importlib.util.find_spec("fitz") is None:
        _log(
            "PDF Loader runtime check",
            "N/A",
            "Import PyMuPDF",
            "PyMuPDF installed",
            "PyMuPDF missing",
            "SKIP",
        )
        pytest.skip("PyMuPDF (fitz) is not installed; loader runtime tests are skipped.")


def test_pdf_loader_missing_file_failure() -> None:
    _require_pdf_runtime()
    from ingestion.loaders.pdf_loader import load_pdf

    missing = "qa_ba/data_test/not_found.pdf"
    _log(
        "PDF Loader - missing file",
        missing,
        "Load PDF",
        "FileNotFoundError",
        "Attempting to load missing path",
        "RUN",
    )
    with pytest.raises(FileNotFoundError):
        load_pdf(missing)


def test_pdf_loader_normal_real_pdf(monkeypatch: pytest.MonkeyPatch, sample_pdf_paths: dict[str, Path]) -> None:
    _require_pdf_runtime()
    import ingestion.loaders.pdf_loader as pdf_loader

    monkeypatch.setattr(pdf_loader, "_select_ocr_engine", lambda _: "tesseract")
    monkeypatch.setattr(pdf_loader, "_ocr_pages", lambda images, engine: [""] * len(images))

    pdf_path = sample_pdf_paths["raw"]
    _log(
        "PDF Loader - normal paper",
        str(pdf_path),
        "Extract PDF text/metadata",
        "page_count > 0 and source_type=pdf",
        "Running load_pdf() with OCR stub",
        "RUN",
    )
    doc = pdf_loader.load_pdf(str(pdf_path))

    assert (doc.page_count or 0) > 0, (
        f"[FAIL] Expected page_count > 0 but got {doc.page_count} for file={pdf_path}"
    )
    assert doc.source_type == "pdf", (
        f"[FAIL] Expected source_type='pdf' but got {doc.source_type} for file={pdf_path}"
    )
    _log(
        "PDF Loader - normal paper",
        str(pdf_path),
        "Validate output",
        "page_count > 0 and source_type=pdf",
        f"page_count={doc.page_count}, text_length={len(doc.full_text)}, references={len(doc.references)}",
        "PASS",
    )


@pytest.mark.parametrize(
    ("case_name", "content"),
    [
        ("empty", b""),
        ("corrupted", b"%PDF-1.4\nbroken synthetic content"),
    ],
)
def test_pdf_loader_edge_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_name: str,
    content: bytes,
) -> None:
    _require_pdf_runtime()
    import ingestion.loaders.pdf_loader as pdf_loader

    monkeypatch.setattr(pdf_loader, "_select_ocr_engine", lambda _: "tesseract")
    monkeypatch.setattr(pdf_loader, "_ocr_pages", lambda images, engine: [""] * len(images))

    pdf_path = tmp_path / f"synthetic_{case_name}.pdf"
    pdf_path.write_bytes(content)
    _log(
        f"PDF Loader - {case_name} input",
        str(pdf_path),
        "Load PDF",
        "Graceful success or explicit exception",
        "Running load_pdf()",
        "RUN",
    )
    try:
        doc = pdf_loader.load_pdf(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        _log(
            f"PDF Loader - {case_name} input",
            str(pdf_path),
            "Load PDF",
            "Graceful explicit failure",
            f"{type(exc).__name__}: {exc}",
            "PASS",
        )
        return

    assert doc.source_type == "pdf", (
        f"[FAIL] Expected source_type='pdf' but got {doc.source_type} for file={pdf_path}"
    )
    _log(
        f"PDF Loader - {case_name} input",
        str(pdf_path),
        "Load PDF",
        "Document object returned",
        f"page_count={doc.page_count}, text_length={len(doc.full_text)}",
        "PASS",
    )
