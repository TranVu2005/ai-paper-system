"""
pipeline.py
-----------
Ingestion pipeline chính cho hệ thống paper-rag-kg.

Orchestrate toàn bộ ingestion flow:
  1. detect      → file_detector.detect_file_type()
  2. load        → pdf_loader / docx_loader / html_loader
  3. split       → section_splitter.split_sections()
  4. clean       → text_cleaning.clean_document()
  5. parse_figs  → figure_parser.parse_figures()
  6. parse_tbls  → table_parser.parse_tables()
  7. parse_forms → formula_parser.parse_formulas()
  8. normalize   → multimodal_normalizer.normalize_multimodal()
  9. enrich      → metadata_enricher.enrich_metadata()

Modes:
  - Single file : run_pipeline(file_path)        → PipelineResult
  - Batch       : run_pipeline_batch(file_paths) → list[PipelineResult]

Error handling:
  - Mỗi step được wrap trong try/except
  - Nếu step fail → log lỗi, trả về partial result đến step đó
  - Batch: file fail không dừng các file còn lại

Output:
  PipelineResult(
      doc        = UnifiedDocument | None,
      success    = bool,
      failed_at  = str | None,   # tên step fail
      errors     = list[str],    # tất cả lỗi trong quá trình chạy
      steps_done = list[str],    # các steps đã hoàn thành
      file_path  = str,
      duration_s = float,
  )
"""

import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from ingestion.schema.document_schema import UnifiedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel — phân biệt "step raise exception" vs "step trả về None hợp lệ"
# FIX: Bản cũ dùng `if output is None` để check fail → sai vì một số step
#      hợp lệ có thể trả về None. Dùng sentinel object thay thế.
# ---------------------------------------------------------------------------
_STEP_FAILED = object()

# ---------------------------------------------------------------------------
# Pipeline step names
# ---------------------------------------------------------------------------

STEP_DETECT    = "detect"
STEP_LOAD      = "load"
STEP_SPLIT     = "split"
STEP_CLEAN     = "clean"
STEP_PARSE_FIG = "parse_figures"
STEP_PARSE_TBL = "parse_tables"
STEP_PARSE_FRM = "parse_formulas"
STEP_NORMALIZE = "normalize_multimodal"
STEP_ENRICH    = "enrich_metadata"

ALL_STEPS = [
    STEP_DETECT,
    STEP_LOAD,
    STEP_SPLIT,
    STEP_CLEAN,
    STEP_PARSE_FIG,
    STEP_PARSE_TBL,
    STEP_PARSE_FRM,
    STEP_NORMALIZE,
    STEP_ENRICH,
]


# ---------------------------------------------------------------------------
# PipelineResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """
    Kết quả của một file qua ingestion pipeline.

    Attributes:
        file_path:  Đường dẫn file đầu vào
        doc:        UnifiedDocument đã xử lý (None nếu fail ở load step)
        success:    True nếu tất cả steps hoàn thành
        failed_at:  Tên step fail đầu tiên (None nếu success)
        errors:     List tất cả error messages trong quá trình chạy
        steps_done: List các steps đã hoàn thành thành công
        duration_s: Thời gian xử lý (seconds)
    """
    file_path:  str
    doc:        Optional[UnifiedDocument] = None
    success:    bool = False
    failed_at:  Optional[str] = None
    errors:     list[str] = field(default_factory=list)
    steps_done: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def __str__(self) -> str:
        status = "✓ OK" if self.success else f"✗ FAILED at '{self.failed_at}'"
        return (
            f"PipelineResult({Path(self.file_path).name} | {status} | "
            f"steps={len(self.steps_done)}/{len(ALL_STEPS)} | "
            f"{self.duration_s:.2f}s)"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pipeline(
    file_path:              str,
    crossref_mailto:        str  = "pipeline@academic-rag.local",
    aggressive_clean:       bool = False,
    multimodal_output_root: str  = "data/multimodal/figures",
    dry_run_enrich:         bool = False,
) -> PipelineResult:
    """
    Chạy toàn bộ ingestion pipeline cho một file.

    Args:
        file_path:              Đường dẫn PDF / DOCX / HTML
        crossref_mailto:        Email cho Crossref polite pool
        aggressive_clean:       True = bật aggressive text cleaning
        multimodal_output_root: Root dir để lưu extracted images (str)
        dry_run_enrich:         True = skip Crossref API call thực

    Returns:
        PipelineResult với doc đã xử lý đến step cuối thành công.
    """
    start_time = time.monotonic()
    result     = PipelineResult(file_path=file_path)

    logger.info("=" * 60)
    logger.info("Pipeline start: %s", file_path)

    # ------------------------------------------------------------------ #
    # STEP 1: DETECT                                                       #
    # ------------------------------------------------------------------ #
    file_type = _run_step(result, STEP_DETECT, _step_detect,
                          file_path=file_path)
    if file_type is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result

    # ------------------------------------------------------------------ #
    # STEP 2: LOAD                                                         #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_LOAD, _step_load,
                    file_path=file_path, file_type=file_type)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 3: SPLIT SECTIONS                                               #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_SPLIT, _step_split, doc=doc)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 4: CLEAN TEXT                                                   #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_CLEAN, _step_clean,
                    doc=doc, aggressive=aggressive_clean)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 5: PARSE FIGURES                                                #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_PARSE_FIG, _step_parse_figures, doc=doc)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 6: PARSE TABLES                                                 #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_PARSE_TBL, _step_parse_tables, doc=doc)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 7: PARSE FORMULAS                                               #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_PARSE_FRM, _step_parse_formulas, doc=doc)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 8: NORMALIZE MULTIMODAL                                         #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_NORMALIZE, _step_normalize_multimodal,
                    doc=doc, output_root=multimodal_output_root)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # STEP 9: ENRICH METADATA                                              #
    # ------------------------------------------------------------------ #
    doc = _run_step(result, STEP_ENRICH, _step_enrich_metadata,
                    doc=doc, mailto=crossref_mailto, dry_run=dry_run_enrich)
    if doc is _STEP_FAILED:
        result.duration_s = time.monotonic() - start_time
        return result
    result.doc = doc

    # ------------------------------------------------------------------ #
    # SUCCESS                                                              #
    # ------------------------------------------------------------------ #
    result.success    = True
    result.duration_s = time.monotonic() - start_time

    logger.info(
        "Pipeline complete: '%s' | sections=%d, figures=%d, tables=%d, formulas=%d | %.2fs",
        result.doc.title[:60],
        len(result.doc.sections),
        len(result.doc.figures),
        len(result.doc.tables),
        len(result.doc.formulas),
        result.duration_s,
    )
    return result


def run_pipeline_batch(
    file_paths:             list[str],
    crossref_mailto:        str  = "pipeline@academic-rag.local",
    aggressive_clean:       bool = False,
    multimodal_output_root: str  = "data/multimodal/figures",
    dry_run_enrich:         bool = False,
) -> list[PipelineResult]:
    """
    Chạy pipeline cho một batch files.
    File fail không dừng các file còn lại.

    Returns:
        list[PipelineResult] theo thứ tự input.
    """
    total   = len(file_paths)
    results = []

    logger.info("Batch pipeline: %d files", total)

    for i, file_path in enumerate(file_paths):
        logger.info("[%d/%d] Processing: %s", i + 1, total, file_path)

        result = run_pipeline(
            file_path              = file_path,
            crossref_mailto        = crossref_mailto,
            aggressive_clean       = aggressive_clean,
            multimodal_output_root = multimodal_output_root,
            dry_run_enrich         = dry_run_enrich,
        )
        results.append(result)

        status = "✓" if result.success else f"✗ ({result.failed_at})"
        logger.info("[%d/%d] %s %s — %.2fs",
                    i + 1, total, status, Path(file_path).name, result.duration_s)

    succeeded = sum(1 for r in results if r.success)
    logger.info("Batch complete: %d/%d succeeded, %d failed",
                succeeded, total, total - succeeded)
    return results


# ---------------------------------------------------------------------------
# Step runner
# FIX: Dùng _STEP_FAILED sentinel thay vì check `is None`
#      để phân biệt exception vs kết quả hợp lệ là None
# ---------------------------------------------------------------------------

def _run_step(result: PipelineResult, step_name: str, fn, **kwargs):
    """
    Chạy một pipeline step với error handling.

    Returns:
        Kết quả của fn(**kwargs) nếu thành công.
        _STEP_FAILED nếu step raise exception.
    """
    logger.debug("  → %s", step_name)
    try:
        output = fn(**kwargs)
        result.steps_done.append(step_name)
        return output
    except Exception as e:
        error_msg = f"[{step_name}] {type(e).__name__}: {e}"
        logger.error(error_msg)
        result.errors.append(error_msg)
        result.failed_at = step_name
        return _STEP_FAILED


# ---------------------------------------------------------------------------
# Individual step implementations
# ---------------------------------------------------------------------------

def _step_detect(file_path: str) -> str:
    """Step 1: Detect file type."""
    from ingestion.detectors.file_detector import assert_supported
    file_type = assert_supported(file_path)
    logger.debug("     Detected: %s", file_type)
    return file_type


def _step_load(file_path: str, file_type: str) -> UnifiedDocument:
    """Step 2: Load file → UnifiedDocument sơ bộ."""
    if file_type == "pdf":
        from ingestion.loaders.pdf_loader import load_pdf
        return load_pdf(file_path)
    elif file_type == "docx":
        from ingestion.loaders.docx_loader import load_docx
        return load_docx(file_path)
    elif file_type == "html":
        from ingestion.loaders.html_loader import load_html
        return load_html(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")


def _step_split(doc: UnifiedDocument) -> UnifiedDocument:
    """
    Step 3: Split sections.

    Dùng force=True để luôn re-split — pdf_loader không tự split nữa
    (sections=[] sau load), nhưng DOCX/HTML loader có thể đã có sections.
    section_splitter._infer_section_type() giờ trả "other" thay vì "unknown"
    → không cần normalize thêm.
    """
    from ingestion.processors.section_splitter import split_sections

    result = split_sections(doc, force=True)

    logger.debug("     Sections after split: %d", len(result.sections))
    return result


def _step_clean(doc: UnifiedDocument, aggressive: bool) -> UnifiedDocument:
    """Step 4: Clean text content."""
    from ingestion.processors.text_cleaning import clean_document
    result = clean_document(doc, aggressive=aggressive)
    logger.debug("     Text cleaned (aggressive=%s)", aggressive)
    return result


def _step_parse_figures(doc: UnifiedDocument) -> UnifiedDocument:
    """
    Step 5: Parse + enrich figures.

    figure_parser.parse_figures(list[Figure]) → list[Figure] enriched.
    FIX: Skip nếu không có figures — tránh gọi parse_figures([]) không cần thiết.
    """
    from ingestion.parsers.figure_parser import parse_figures

    if not doc.figures:
        logger.debug("     No figures to parse")
        return doc

    enriched = parse_figures(doc.figures)
    logger.debug("     Figures parsed: %d", len(enriched))
    return replace(doc, figures=enriched)


def _step_parse_tables(doc: UnifiedDocument) -> UnifiedDocument:
    """
    Step 6: Parse + enrich tables.

    table_parser.parse_tables(list[Table]) → list[Table] enriched.
    FIX: Nếu tables rỗng (PDF dùng image-based / tab-spaced tables) → skip.
         Đây là giới hạn của PyMuPDF, không phải bug — không raise error.
    """
    from ingestion.parsers.table_parser import parse_tables

    if not doc.tables:
        logger.debug("     No tables found (PDF may use image-based or tab-spaced tables)")
        return doc

    enriched = parse_tables(doc.tables)
    logger.debug("     Tables parsed: %d", len(enriched))
    return replace(doc, tables=enriched)


def _step_parse_formulas(doc: UnifiedDocument) -> UnifiedDocument:
    """
    Step 7: Parse + enrich formulas.

    Merge 2 nguồn:
      A. Formulas từ loader (DOCX/HTML có thể extract sẵn)
      B. Formulas extract từ section text (LaTeX $...$ patterns)

    Dùng merge_formulas(deduplicate=True) để tránh đếm 2 lần.
    formula_parser.merge_formulas() nhận *lists và deduplicate theo normalized raw.
    """
    from ingestion.parsers.formula_parser import (
        parse_formulas,
        extract_from_text,
        merge_formulas,
    )

    # Nguồn A: enrich formulas đã có từ loader
    loader_formulas = parse_formulas(doc.formulas)

    # Nguồn B: extract LaTeX từ section content
    text_formulas: list = []
    for section in doc.sections:
        if section.content:
            extracted = extract_from_text(
                section.content,
                start_index=len(loader_formulas) + len(text_formulas),
            )
            text_formulas.extend(extracted)

    # Merge + deduplicate theo normalized raw string
    merged = merge_formulas(loader_formulas, text_formulas, deduplicate=True)
    logger.debug(
        "     Formulas: loader=%d, extracted=%d, merged=%d",
        len(loader_formulas), len(text_formulas), len(merged),
    )
    return replace(doc, formulas=merged)


def _step_normalize_multimodal(doc: UnifiedDocument, output_root: str) -> UnifiedDocument:
    """
    Step 8: Normalize multimodal content.

    - Extract images từ PDF/DOCX → lưu ra disk tại output_root/{doc_stem}/fig_NNN.png
    - Gán figure.path cho mỗi Figure object
    - Backfill formula.display_text nếu còn None

    FIX: output_root nhận str từ run_pipeline args.
         multimodal_normalizer.normalize_multimodal() nhận Path → convert tại đây.
    """
    from ingestion.processors.multimodal_normalizer import normalize_multimodal

    result = normalize_multimodal(doc, output_root=Path(output_root))
    resolved = sum(1 for f in result.figures if f.path is not None)
    logger.debug("     Figures resolved: %d/%d", resolved, len(result.figures))
    return result


def _step_enrich_metadata(doc: UnifiedDocument, mailto: str, dry_run: bool) -> UnifiedDocument:
    """
    Step 9: Enrich metadata via Crossref API.

    Strategy:
      1. Có DOI → GET /works/{doi} trực tiếp
      2. Không DOI → query /works?query.title=...&query.author=...
      3. Merge — chỉ fill fields còn trống, không overwrite loader data

    FIX: metadata_enricher.enrich_metadata(doc, mailto, dry_run)
         — signature khớp hoàn toàn với metadata_enricher.py hiện tại.
    """
    from ingestion.processors.metadata_enricher import enrich_metadata

    result = enrich_metadata(doc, mailto=mailto, dry_run=dry_run)
    logger.debug(
        "     Metadata: doi=%s, journal=%s, year=%s, citations=%s",
        result.doi, result.journal, result.year, result.citation_count,
    )
    return result


# ---------------------------------------------------------------------------
# Batch summary helper
# ---------------------------------------------------------------------------

def summarize_batch(results: list[PipelineResult]) -> dict:
    """
    Tóm tắt kết quả một batch pipeline run.

    Returns:
        dict: total, succeeded, failed, success_rate,
              step_failures, avg_duration_s, failed_files
    """
    total     = len(results)
    succeeded = [r for r in results if r.success]
    failed    = [r for r in results if not r.success]

    step_failures: dict[str, int] = {}
    for r in failed:
        if r.failed_at:
            step_failures[r.failed_at] = step_failures.get(r.failed_at, 0) + 1

    avg_duration = sum(r.duration_s for r in results) / total if total > 0 else 0.0

    return {
        "total":          total,
        "succeeded":      len(succeeded),
        "failed":         len(failed),
        "success_rate":   len(succeeded) / total if total > 0 else 0.0,
        "step_failures":  step_failures,
        "avg_duration_s": round(avg_duration, 2),
        "failed_files":   [r.file_path for r in failed],
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# Usage:
#   python -m ingestion.pipeline paper.pdf
#   python -m ingestion.pipeline papers/ --batch
#   python -m ingestion.pipeline paper.pdf --dry-run-enrich --aggressive
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Academic paper ingestion pipeline")
    parser.add_argument("path",             help="File path hoặc directory (--batch mode)")
    parser.add_argument("--batch",          action="store_true", help="Process toàn bộ directory")
    parser.add_argument("--mailto",         default="pipeline@academic-rag.local", metavar="EMAIL")
    parser.add_argument("--aggressive",     action="store_true", help="Aggressive text cleaning")
    parser.add_argument("--dry-run-enrich", action="store_true", help="Skip Crossref API")
    parser.add_argument("--output",         default="data/multimodal/figures", metavar="DIR")
    parser.add_argument("--ext",            default=".pdf,.docx,.html", metavar="EXTS",
                        help="Extensions cho batch mode, comma-separated")
    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # Batch mode                                                           #
    # ------------------------------------------------------------------ #
    if args.batch:
        input_path = Path(args.path)
        if not input_path.is_dir():
            print(f"Error: '{args.path}' is not a directory")
            sys.exit(1)

        extensions  = [e.strip() for e in args.ext.split(",")]
        file_paths: list[str] = []
        for ext in extensions:
            file_paths.extend(str(p) for p in sorted(input_path.rglob(f"*{ext}")))

        if not file_paths:
            print(f"No files found in '{args.path}' with extensions: {extensions}")
            sys.exit(0)

        print(f"Found {len(file_paths)} files — starting batch pipeline...")
        results = run_pipeline_batch(
            file_paths             = file_paths,
            crossref_mailto        = args.mailto,
            aggressive_clean       = args.aggressive,
            multimodal_output_root = args.output,
            dry_run_enrich         = args.dry_run_enrich,
        )

        summary = summarize_batch(results)
        print(f"\n{'='*60}")
        print(f"  Batch summary")
        print(f"{'='*60}")
        print(f"  Total     : {summary['total']}")
        print(f"  Succeeded : {summary['succeeded']}")
        print(f"  Failed    : {summary['failed']}")
        print(f"  Success % : {summary['success_rate']*100:.1f}%")
        print(f"  Avg time  : {summary['avg_duration_s']:.2f}s/file")
        if summary["step_failures"]:
            print("  Failures by step:")
            for step, count in summary["step_failures"].items():
                print(f"    {step}: {count}")
        if summary["failed_files"]:
            print("  Failed files:")
            for fp in summary["failed_files"]:
                print(f"    {fp}")
        print(f"{'='*60}")
        sys.exit(0 if summary["failed"] == 0 else 1)

    # ------------------------------------------------------------------ #
    # Single file mode                                                     #
    # ------------------------------------------------------------------ #
    result = run_pipeline(
        file_path              = args.path,
        crossref_mailto        = args.mailto,
        aggressive_clean       = args.aggressive,
        multimodal_output_root = args.output,
        dry_run_enrich         = args.dry_run_enrich,
    )

    print(f"\n{'='*60}")
    print(result)
    print(f"{'='*60}")

    if result.doc:
        doc = result.doc
        authors_str = ", ".join(doc.authors[:3])
        if len(doc.authors) > 3:
            authors_str += f" (+{len(doc.authors)-3} more)"

        print(f"  Title    : {doc.title}")
        print(f"  Authors  : {authors_str}")
        print(f"  Year     : {doc.year}")
        print(f"  DOI      : {doc.doi}")
        print(f"  Journal  : {doc.journal}")
        print(f"  Language : {doc.language}")
        if doc.citation_count is not None:
            print(f"  Citations: {doc.citation_count}")

        print()
        if doc.abstract:
            print(f"  Abstract : {doc.abstract[:200]}...")

        print()
        print(f"  Sections : {len(doc.sections)}")
        for s in doc.sections[:8]:
            parent = f" (parent: {s.parent_section})" if s.parent_section else ""
            stype  = s.section_type or "other"
            print(f"    [{s.order}] L{s.level} [{stype:15s}] '{s.name[:45]}'{parent}")
        if len(doc.sections) > 8:
            print(f"    ... (+{len(doc.sections)-8} more)")

        print(f"  Figures  : {len(doc.figures)}")
        for f in doc.figures[:3]:
            has_data = "✓ base64" if f.base64_data else ("✓ path" if f.path else "no data")
            print(f"    [{f.index}] [{f.figure_type:8s}] {f.caption[:45]} [{has_data}]")
        if len(doc.figures) > 3:
            print(f"    ... (+{len(doc.figures)-3} more)")

        print(f"  Tables   : {len(doc.tables)}")
        for t in doc.tables[:3]:
            print(f"    [{t.index}] {t.caption[:45]} — {len(t.headers)} cols, {len(t.data)} rows")
        if not doc.tables:
            print("    (none — PDF có thể dùng bảng dạng ảnh hoặc tab-based)")

        print(f"  Formulas : {len(doc.formulas)}")
        for fm in doc.formulas[:3]:
            fmt = getattr(fm, "source_format", "?")
            print(f"    [{fm.index}] [{fm.formula_type:10s}] [{fmt}] {fm.raw[:45]}...")
        if len(doc.formulas) > 3:
            print(f"    ... (+{len(doc.formulas)-3} more)")

        print(f"  Refs     : {len(doc.references)}")
        print(f"  Pages    : {doc.page_count}")
        print(f"  Status   : {doc.processing_status}")

    if result.errors:
        print(f"\n  Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"    ! {err}")

    print(f"{'='*60}")
    sys.exit(0 if result.success else 1)
