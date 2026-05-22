from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "processed"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _is_non_empty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return True


@dataclass
class DatasetScan:
    data_root: Path
    files: list[Path] = field(default_factory=list)
    bad_json: list[str] = field(default_factory=list)
    missing_core: list[str] = field(default_factory=list)
    bad_sections: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.files)

    def ratio(self, key: str) -> float:
        if self.total == 0:
            return 0.0
        return self.stats.get(key, 0) / self.total


def _scan_dataset(data_root: Path, max_files: int | None = None) -> DatasetScan:
    files = sorted(data_root.rglob("*.json"))
    if max_files is not None and max_files > 0:
        files = files[:max_files]

    scan = DatasetScan(
        data_root=data_root,
        files=files,
        stats={
            "has_doc_id": 0,
            "has_title": 0,
            "has_abstract": 0,
            "has_full_text": 0,
            "has_sections": 0,
            "has_authors": 0,
            "has_keywords": 0,
            "has_year": 0,
        },
    )

    for fp in files:
        rel = str(fp.relative_to(data_root))
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            scan.bad_json.append(rel)
            continue

        if not isinstance(payload, dict):
            scan.missing_core.append(rel)
            continue

        for src_key, stat_key in (
            ("doc_id", "has_doc_id"),
            ("title", "has_title"),
            ("abstract", "has_abstract"),
            ("full_text", "has_full_text"),
            ("sections", "has_sections"),
            ("authors", "has_authors"),
            ("keywords", "has_keywords"),
            ("year", "has_year"),
        ):
            if _is_non_empty(payload.get(src_key)):
                scan.stats[stat_key] += 1

        core_ok = (
            _is_non_empty(payload.get("doc_id"))
            and _is_non_empty(payload.get("title"))
            and _is_non_empty(payload.get("full_text"))
            and isinstance(payload.get("sections"), list)
            and len(payload.get("sections", [])) > 0
        )
        if not core_ok:
            scan.missing_core.append(rel)

        sections = payload.get("sections")
        sections_ok = True
        if not isinstance(sections, list) or len(sections) == 0:
            sections_ok = False
        else:
            has_non_empty_content = False
            for sec in sections:
                if not isinstance(sec, dict):
                    sections_ok = False
                    break
                if "name" not in sec or "content" not in sec:
                    sections_ok = False
                    break
                if _is_non_empty(sec.get("content")):
                    has_non_empty_content = True
            if not has_non_empty_content:
                sections_ok = False
        if not sections_ok:
            scan.bad_sections.append(rel)

    return scan


@pytest.fixture(scope="session")
def dataset_scan() -> DatasetScan:
    data_root = Path(os.getenv("QA_DATA_ROOT", str(DEFAULT_DATA_ROOT))).resolve()
    max_files = _env_int("QA_DATA_MAX_FILES", 0)
    return _scan_dataset(data_root=data_root, max_files=max_files or None)


def test_data_root_exists(dataset_scan: DatasetScan) -> None:
    assert dataset_scan.data_root.exists(), (
        f"[FAIL] Data root not found: {dataset_scan.data_root}. "
        "Set QA_DATA_ROOT to override."
    )


def test_dataset_has_json_files(dataset_scan: DatasetScan) -> None:
    min_files = _env_int("QA_DATA_MIN_FILES", 1)
    assert dataset_scan.total >= min_files, (
        f"[FAIL] Expected at least {min_files} JSON files in {dataset_scan.data_root}, "
        f"found {dataset_scan.total}."
    )


def test_all_files_are_valid_json(dataset_scan: DatasetScan) -> None:
    assert not dataset_scan.bad_json, (
        "[FAIL] Found invalid JSON files (first 20): "
        + ", ".join(dataset_scan.bad_json[:20])
    )


def test_required_core_fields(dataset_scan: DatasetScan) -> None:
    assert not dataset_scan.missing_core, (
        "[FAIL] Missing core fields (doc_id/title/full_text/sections) in files (first 20): "
        + ", ".join(dataset_scan.missing_core[:20])
    )


def test_sections_shape(dataset_scan: DatasetScan) -> None:
    assert not dataset_scan.bad_sections, (
        "[FAIL] Invalid sections format (need list of objects with name+content) in files (first 20): "
        + ", ".join(dataset_scan.bad_sections[:20])
    )


def test_quality_ratios(dataset_scan: DatasetScan) -> None:
    min_abstract_ratio = _env_float("QA_MIN_ABSTRACT_RATIO", 0.98)
    min_keywords_ratio = _env_float("QA_MIN_KEYWORDS_RATIO", 0.95)
    min_year_ratio = _env_float("QA_MIN_YEAR_RATIO", 0.80)
    min_authors_ratio = _env_float("QA_MIN_AUTHORS_RATIO", 0.95)

    assert dataset_scan.ratio("has_abstract") >= min_abstract_ratio, (
        "[FAIL] Abstract coverage too low: "
        f"{dataset_scan.stats['has_abstract']}/{dataset_scan.total} "
        f"({dataset_scan.ratio('has_abstract'):.2%}) < {min_abstract_ratio:.2%}"
    )
    assert dataset_scan.ratio("has_keywords") >= min_keywords_ratio, (
        "[FAIL] Keywords coverage too low: "
        f"{dataset_scan.stats['has_keywords']}/{dataset_scan.total} "
        f"({dataset_scan.ratio('has_keywords'):.2%}) < {min_keywords_ratio:.2%}"
    )
    assert dataset_scan.ratio("has_year") >= min_year_ratio, (
        "[FAIL] Year coverage too low: "
        f"{dataset_scan.stats['has_year']}/{dataset_scan.total} "
        f"({dataset_scan.ratio('has_year'):.2%}) < {min_year_ratio:.2%}"
    )
    assert dataset_scan.ratio("has_authors") >= min_authors_ratio, (
        "[FAIL] Authors coverage too low: "
        f"{dataset_scan.stats['has_authors']}/{dataset_scan.total} "
        f"({dataset_scan.ratio('has_authors'):.2%}) < {min_authors_ratio:.2%}"
    )

