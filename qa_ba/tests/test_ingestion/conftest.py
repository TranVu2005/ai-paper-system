from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_TEST_ROOT = REPO_ROOT / "qa_ba" / "data_test"
MANIFEST_PATH = DATA_TEST_ROOT / "test_manifest.json"
EVALUATION_ROOT = REPO_ROOT / "qa_ba" / "evaluation" / "ingestion"

DOMAIN_NAMES = (
    "KHKT&CN",
    "KHNN",
    "KHTN",
    "KHXH&NV",
    "KHYD",
)

LEGACY_SAMPLE_ALIASES = (
    "normal",
    "formula",
    "table",
    "figure",
    "large",
    "twocolumn",
    "corrupted",
    "empty",
    "raw",
    "vietnamese",
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _entry_pdf_path(entry: dict) -> Path:
    return DATA_TEST_ROOT / str(entry["domain"]) / str(entry["filename"])


@pytest.fixture(scope="session", autouse=True)
def ensure_data_test_layout_ready() -> None:
    """
    Dataset guard for the new domain-based layout.
    The fixture is non-destructive: it only ensures directories exist and prints a summary.
    """
    DATA_TEST_ROOT.mkdir(parents=True, exist_ok=True)
    EVALUATION_ROOT.mkdir(parents=True, exist_ok=True)

    for domain in DOMAIN_NAMES:
        (DATA_TEST_ROOT / domain).mkdir(parents=True, exist_ok=True)

    for domain in DOMAIN_NAMES:
        count = len(list((DATA_TEST_ROOT / domain).glob("*.pdf")))
        print(f"[DATASET] domain={domain} pdf_count={count}")


@pytest.fixture(scope="session")
def data_test_root() -> Path:
    return DATA_TEST_ROOT


@pytest.fixture(scope="session")
def pdf_test_root() -> Path:
    # Backward-compatible alias for older ingestion tests.
    return DATA_TEST_ROOT


@pytest.fixture(scope="session")
def evaluation_root() -> Path:
    EVALUATION_ROOT.mkdir(parents=True, exist_ok=True)
    return EVALUATION_ROOT


@pytest.fixture(scope="session")
def ingestion_manifest_path() -> Path:
    return MANIFEST_PATH


@pytest.fixture(scope="session")
def quality_manifest_path() -> Path:
    # Backward-compatible alias used by older code/comments.
    return MANIFEST_PATH


@pytest.fixture(scope="session")
def ingestion_manifest(ingestion_manifest_path: Path) -> dict:
    if not ingestion_manifest_path.exists():
        raise AssertionError(
            f"[FAIL] Ingestion manifest missing: {ingestion_manifest_path}. "
            "Please create qa_ba/data_test/test_manifest.json."
        )

    try:
        manifest = json.loads(ingestion_manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"[FAIL] Invalid JSON in ingestion manifest: {ingestion_manifest_path} | error={exc}"
        ) from exc

    if not isinstance(manifest, dict):
        raise AssertionError(f"[FAIL] Manifest root must be a JSON object: {ingestion_manifest_path}")

    return manifest


@pytest.fixture(scope="session")
def quality_manifest(ingestion_manifest: dict) -> dict:
    # Backward-compatible alias.
    return ingestion_manifest


@pytest.fixture(scope="session")
def manifest_entries(ingestion_manifest: dict) -> list[dict]:
    files = ingestion_manifest.get("files")
    if not isinstance(files, list):
        raise AssertionError("[FAIL] Manifest field 'files' must be a list.")
    if not files:
        raise AssertionError(
            "[FAIL] Manifest has no file entries. "
            "Add at least 1 PDF entry to qa_ba/data_test/test_manifest.json before running tests."
        )

    validated: list[dict] = []
    seen_ids: set[str] = set()
    for idx, raw_entry in enumerate(files, start=1):
        if not isinstance(raw_entry, dict):
            raise AssertionError(f"[FAIL] Manifest entry #{idx} must be an object, got: {type(raw_entry)}")

        entry = dict(raw_entry)
        domain = entry.get("domain")
        filename = entry.get("filename")
        entry_id = str(entry.get("id") or f"{domain}-{idx:03d}")

        if entry_id in seen_ids:
            raise AssertionError(f"[FAIL] Duplicate manifest entry id detected: {entry_id}")
        seen_ids.add(entry_id)

        if domain not in DOMAIN_NAMES:
            raise AssertionError(
                f"[FAIL] Manifest entry id={entry_id} has invalid domain={domain!r}. "
                f"Expected one of {DOMAIN_NAMES}."
            )
        if not isinstance(filename, str) or not filename.strip():
            raise AssertionError(f"[FAIL] Manifest entry id={entry_id} must define a non-empty filename.")

        pdf_path = _entry_pdf_path({"domain": domain, "filename": filename})
        if not pdf_path.exists():
            raise AssertionError(
                f"[FAIL] PDF file missing for manifest entry id={entry_id}: {pdf_path}"
            )
        if pdf_path.suffix.lower() != ".pdf":
            raise AssertionError(
                f"[FAIL] Manifest entry id={entry_id} must point to a .pdf file, got: {pdf_path.name}"
            )

        entry["id"] = entry_id
        validated.append(entry)

    return validated


@pytest.fixture(scope="session")
def domain_files(manifest_entries: list[dict]) -> dict[str, list[Path]]:
    grouped = {domain: [] for domain in DOMAIN_NAMES}
    for entry in manifest_entries:
        grouped[str(entry["domain"])].append(_entry_pdf_path(entry))
    return grouped


@pytest.fixture(scope="session")
def category_files(domain_files: dict[str, list[Path]]) -> dict[str, list[Path]]:
    # Backward-compatible alias for old tests/report references.
    return domain_files


@pytest.fixture(scope="session")
def sample_pdf_paths(manifest_entries: list[dict]) -> dict[str, Path]:
    resolved = [(entry, _entry_pdf_path(entry)) for entry in manifest_entries]
    first_path = resolved[0][1]

    alias_map: dict[str, Path] = {alias: first_path for alias in LEGACY_SAMPLE_ALIASES}

    for entry, pdf_path in resolved:
        for alias in entry.get("legacy_aliases", []):
            if isinstance(alias, str) and alias.strip():
                alias_map[alias] = pdf_path

        pipeline_gate = entry.get("pipeline_gate", {})
        if pipeline_gate.get("expect_graceful_corrupted", False):
            alias_map["corrupted"] = pdf_path
        if pipeline_gate.get("expect_graceful_empty", False):
            alias_map["empty"] = pdf_path

    return alias_map

