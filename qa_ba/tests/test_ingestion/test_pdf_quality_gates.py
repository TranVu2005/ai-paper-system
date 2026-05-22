from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest

from ingestion.pipeline import STEP_DETECT, STEP_EXTRACT_LM, STEP_LOAD, run_pipeline
from ingestion.processors.lm_metadata_extractor import is_ollama_available


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_TEST_ROOT = REPO_ROOT / "qa_ba" / "data_test"
MANIFEST_PATH = DATA_TEST_ROOT / "test_manifest.json"
OUTPUT_ROOT = DATA_TEST_ROOT / "output"

_PIPELINE_CACHE: dict[tuple[str, bool], object] = {}


def _load_collection_entries() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return []
    files = manifest.get("files", [])
    if not isinstance(files, list):
        return []
    return [entry for entry in files if isinstance(entry, dict)]


def _entry_id(entry: dict) -> str:
    return str(entry.get("id") or f"{entry.get('domain', 'UNKNOWN')}/{entry.get('filename', 'unknown.pdf')}")


def _entry_pdf_path(entry: dict) -> Path:
    return DATA_TEST_ROOT / str(entry["domain"]) / str(entry["filename"])


def _sanitize_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def _entry_output_path(entry: dict) -> Path:
    domain = str(entry.get("domain", "UNKNOWN"))
    entry_id = _sanitize_name(_entry_id(entry))
    domain_dir = OUTPUT_ROOT / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    return domain_dir / f"{entry_id}.json"


def _write_gate_result(
    entry: dict,
    gate: str,
    status: str,
    expected: str,
    actual: str,
    extra: dict[str, Any] | None = None,
) -> None:
    out_path = _entry_output_path(entry)
    payload: dict[str, Any]
    if out_path.exists():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {}

    payload.setdefault("id", _entry_id(entry))
    payload.setdefault("domain", str(entry.get("domain", "")))
    payload.setdefault("filename", str(entry.get("filename", "")))
    payload.setdefault("pdf_path", str(_entry_pdf_path(entry)))
    payload.setdefault("gates", {})

    gate_payload = {
        "status": status,
        "expected": expected,
        "actual": actual,
    }
    if extra:
        gate_payload["extra"] = extra

    payload["gates"][gate] = gate_payload
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


COLLECTION_ENTRIES = _load_collection_entries()
COLLECTION_PARAMS = [pytest.param(entry, id=_entry_id(entry)) for entry in COLLECTION_ENTRIES] or [
    pytest.param(None, id="MANIFEST-EMPTY")
]


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def _require_pdf_runtime() -> None:
    assert importlib.util.find_spec("fitz") is not None, (
        "[FAIL] PyMuPDF (fitz) is required for ingestion PDF tests."
    )


def _require_ocr_runtime() -> None:
    has_paddle = importlib.util.find_spec("paddleocr") is not None
    has_tesseract = shutil.which("tesseract") is not None
    assert has_paddle or has_tesseract, (
        "[FAIL] OCR runtime missing. Install paddleocr or add tesseract to PATH."
    )


def _require_lm_runtime() -> None:
    assert importlib.util.find_spec("requests") is not None, (
        "[FAIL] requests is required for LM metadata extraction tests."
    )
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert is_ollama_available("qwen2.5:7b"), (
        f"[FAIL] Ollama model qwen2.5:7b is not available on {base_url}."
    )


def _run_pipeline_cached(entry: dict, use_lm: bool):
    pdf_path = _entry_pdf_path(entry)
    cache_key = (str(pdf_path), use_lm)
    if cache_key not in _PIPELINE_CACHE:
        _PIPELINE_CACHE[cache_key] = run_pipeline(
            file_path=str(pdf_path),
            dry_run_enrich=True,
            use_lm=use_lm,
        )
    return _PIPELINE_CACHE[cache_key]


def _is_graceful_corrupted(result) -> bool:
    if not result.success and result.failed_at in {STEP_DETECT, STEP_LOAD}:
        return True
    if result.success and result.doc is not None:
        doc = result.doc
        return (
            (doc.page_count or 0) == 0
            and len(doc.full_text.strip()) == 0
            and len(doc.sections) == 0
            and len(doc.tables) == 0
            and len(doc.figures) == 0
            and len(doc.formulas) == 0
        )
    return False


def _is_graceful_empty(result) -> bool:
    if not result.success and result.failed_at in {STEP_LOAD, STEP_DETECT}:
        return True
    if result.success and result.doc is not None:
        doc = result.doc
        return (doc.page_count or 0) == 0 or len(doc.full_text.strip()) == 0
    return False


def _evaluate_metric(name: str, actual: int, cfg: dict, *, default_mode: str = "observe", default_min: int = 0):
    mode = str(cfg.get("mode", default_mode)).lower()
    assert mode in {"strict", "observe"}, (
        f"[FAIL] Invalid mode for metric={name}: {mode!r}. Expected 'strict' or 'observe'."
    )
    min_count = int(cfg.get("min_count", default_min))

    if actual >= min_count:
        return "PASS", f"{name}={actual} >= {min_count}"

    status = "FAIL" if mode == "strict" else "WARN"
    return status, f"{name}={actual} < {min_count}"


def _pipeline_expected_text(cfg: dict) -> str:
    parts = [
        "success=True",
        "load step executed",
        f"pages>={int(cfg.get('min_pages', 0))}",
        f"text_chars>={int(cfg.get('min_text_chars', 0))}",
    ]
    if cfg.get("require_lm_step", False):
        parts.append("extract_metadata_lm executed")
    if cfg.get("expect_graceful_corrupted", False):
        return "graceful corrupted handling"
    if cfg.get("expect_graceful_empty", False):
        return "graceful empty handling"
    return "; ".join(parts)


def test_manifest_configured(ingestion_manifest: dict, manifest_entries: list[dict]) -> None:
    assert ingestion_manifest.get("version"), "[FAIL] Manifest should define a version."
    assert manifest_entries, "[FAIL] Manifest must contain at least one file entry."

    domain_counts: dict[str, int] = {}
    for entry in manifest_entries:
        domain = str(entry["domain"])
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    _log(
        "Manifest Configuration",
        str(MANIFEST_PATH),
        "validate domain-based ingestion manifest",
        "non-empty manifest with existing PDF files",
        f"entry_count={len(manifest_entries)}, domain_counts={domain_counts}",
        "PASS",
    )


@pytest.mark.parametrize("entry", COLLECTION_PARAMS)
def test_pdf_pipeline_gate(entry: dict | None) -> None:
    if entry is None:
        pytest.fail("[FAIL] Manifest has no file entries. Add files to qa_ba/data_test/test_manifest.json.")

    pipeline_cfg = entry.get("pipeline_gate", {})
    use_lm = bool(pipeline_cfg.get("use_lm", False))
    require_lm_step = bool(pipeline_cfg.get("require_lm_step", False))
    pdf_path = _entry_pdf_path(entry)
    expected_text = _pipeline_expected_text(pipeline_cfg)

    try:
        _require_pdf_runtime()
        _require_ocr_runtime()
        if use_lm or require_lm_step:
            _require_lm_runtime()

        _log(
            f"Pipeline Gate - {_entry_id(entry)}",
            str(pdf_path),
            f"run_pipeline(dry_run_enrich=True, use_lm={use_lm})",
            expected_text,
            "running ingestion pipeline",
            "RUN",
        )

        result = _run_pipeline_cached(entry, use_lm)

        if pipeline_cfg.get("expect_graceful_corrupted", False):
            assert _is_graceful_corrupted(result), (
                f"[FAIL] Corrupted-file handling failed for {_entry_id(entry)}: "
                f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}"
            )
            actual = f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}"
            _write_gate_result(entry, "pipeline_gate", "PASS", expected_text, actual)
            return

        if pipeline_cfg.get("expect_graceful_empty", False):
            assert _is_graceful_empty(result), (
                f"[FAIL] Empty-file handling failed for {_entry_id(entry)}: "
                f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}"
            )
            actual = f"success={result.success}, failed_at={result.failed_at}, errors={result.errors}"
            _write_gate_result(entry, "pipeline_gate", "PASS", expected_text, actual)
            return

        assert result.success, (
            f"[FAIL] Expected pipeline success for {_entry_id(entry)} "
            f"but failed_at={result.failed_at}, errors={result.errors}"
        )
        assert STEP_LOAD in result.steps_done, (
            f"[FAIL] Expected load step executed for {_entry_id(entry)} but steps_done={result.steps_done}"
        )
        assert result.doc is not None, f"[FAIL] Expected result.doc not None for {_entry_id(entry)}"

        doc = result.doc
        assert doc.source_type == "pdf", (
            f"[FAIL] Expected source_type='pdf' for {_entry_id(entry)} but got {doc.source_type!r}"
        )
        assert (doc.page_count or 0) >= int(pipeline_cfg.get("min_pages", 0)), (
            f"[FAIL] min_pages failed for {_entry_id(entry)}: "
            f"expected>={int(pipeline_cfg.get('min_pages', 0))}, got={doc.page_count}"
        )
        assert len(doc.full_text.strip()) >= int(pipeline_cfg.get("min_text_chars", 0)), (
            f"[FAIL] min_text_chars failed for {_entry_id(entry)}: "
            f"expected>={int(pipeline_cfg.get('min_text_chars', 0))}, got={len(doc.full_text.strip())}"
        )
        if require_lm_step:
            assert STEP_EXTRACT_LM in result.steps_done, (
                f"[FAIL] Expected extract_metadata_lm step for {_entry_id(entry)} "
                f"but steps_done={result.steps_done}"
            )

        actual = (
            f"steps_done={result.steps_done}, pages={doc.page_count}, text_chars={len(doc.full_text.strip())}, "
            f"sections={len(doc.sections)}, figures={len(doc.figures)}, "
            f"observed_tables={len(doc.tables)}, observed_formulas={len(doc.formulas)}"
        )
        _write_gate_result(entry, "pipeline_gate", "PASS", expected_text, actual)
    except AssertionError as exc:
        _write_gate_result(entry, "pipeline_gate", "FAIL", expected_text, str(exc))
        raise


@pytest.mark.parametrize("entry", COLLECTION_PARAMS)
def test_pdf_content_quality_gate(entry: dict | None) -> None:
    if entry is None:
        pytest.fail("[FAIL] Manifest has no file entries. Add files to qa_ba/data_test/test_manifest.json.")

    pipeline_cfg = entry.get("pipeline_gate", {})
    use_lm = bool(pipeline_cfg.get("use_lm", False))
    expected = "sections and figures meet configured thresholds"
    try:
        _require_pdf_runtime()
        _require_ocr_runtime()
        if use_lm or bool(pipeline_cfg.get("require_lm_step", False)):
            _require_lm_runtime()

        result = _run_pipeline_cached(entry, use_lm)
        assert result.success and result.doc is not None, (
            f"[FAIL] Content gate requires successful pipeline result for {_entry_id(entry)}"
        )

        doc = result.doc
        content_cfg = entry.get("content_quality", {})

        section_status, section_detail = _evaluate_metric(
            "sections",
            len(doc.sections),
            content_cfg.get("sections", {}),
            default_mode="strict",
            default_min=1,
        )
        figure_status, figure_detail = _evaluate_metric(
            "figures",
            len(doc.figures),
            content_cfg.get("figures", {}),
            default_mode="observe",
            default_min=0,
        )

        statuses = [section_status, figure_status]
        details = [section_detail, figure_detail]

        if "WARN" in statuses:
            warnings.warn(
                f"[WARN] Content quality below threshold for {_entry_id(entry)}: "
                + "; ".join(detail for status, detail in zip(statuses, details) if status == "WARN"),
                stacklevel=1,
            )

        overall_status = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
        actual = (
            f"{'; '.join(details)}, observed_tables={len(doc.tables)}, "
            f"observed_formulas={len(doc.formulas)}"
        )
        _write_gate_result(entry, "content_quality_gate", overall_status, expected, actual)

        assert "FAIL" not in statuses, (
            f"[FAIL] Content quality gate failed for {_entry_id(entry)}: " + "; ".join(details)
        )
    except AssertionError as exc:
        _write_gate_result(entry, "content_quality_gate", "FAIL", expected, str(exc))
        raise


@pytest.mark.parametrize("entry", COLLECTION_PARAMS)
def test_pdf_metadata_quality_gate(entry: dict | None) -> None:
    if entry is None:
        pytest.fail("[FAIL] Manifest has no file entries. Add files to qa_ba/data_test/test_manifest.json.")

    pipeline_cfg = entry.get("pipeline_gate", {})
    use_lm = bool(pipeline_cfg.get("use_lm", False))
    expected = "title/authors meet configured thresholds"
    try:
        _require_pdf_runtime()
        _require_ocr_runtime()
        if use_lm or bool(pipeline_cfg.get("require_lm_step", False)):
            _require_lm_runtime()

        result = _run_pipeline_cached(entry, use_lm)
        assert result.success and result.doc is not None, (
            f"[FAIL] Metadata gate requires successful pipeline result for {_entry_id(entry)}"
        )

        doc = result.doc
        metadata_cfg = entry.get("metadata_quality", {})
        mode = str(metadata_cfg.get("mode", "strict")).lower()
        assert mode in {"strict", "observe"}, (
            f"[FAIL] Invalid metadata quality mode for {_entry_id(entry)}: {mode!r}"
        )

        title_chars = len((doc.title or "").strip())
        author_count = len(doc.authors)
        min_title_chars = int(metadata_cfg.get("min_title_chars", 10))
        min_authors = int(metadata_cfg.get("min_authors", 1))
        expected = f"title_chars>={min_title_chars}; authors>={min_authors}"

        issues: list[str] = []
        if title_chars < min_title_chars:
            issues.append(f"title_chars={title_chars} < {min_title_chars}")
        if author_count < min_authors:
            issues.append(f"authors={author_count} < {min_authors}")

        if issues:
            overall_status = "FAIL" if mode == "strict" else "WARN"
            if overall_status == "WARN":
                warnings.warn(
                    f"[WARN] Metadata quality below threshold for {_entry_id(entry)}: " + "; ".join(issues),
                    stacklevel=1,
                )
            _write_gate_result(
                entry,
                "metadata_quality_gate",
                overall_status,
                expected,
                f"title={doc.title!r}, authors={author_count} | {'; '.join(issues)}",
            )
            assert overall_status != "FAIL", (
                f"[FAIL] Metadata quality gate failed for {_entry_id(entry)}: " + "; ".join(issues)
            )
            return

        _write_gate_result(
            entry,
            "metadata_quality_gate",
            "PASS",
            expected,
            f"title={doc.title!r}, authors={author_count}, lm_step={STEP_EXTRACT_LM in result.steps_done}",
        )
    except AssertionError as exc:
        _write_gate_result(entry, "metadata_quality_gate", "FAIL", expected, str(exc))
        raise
