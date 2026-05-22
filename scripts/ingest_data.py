"""
ingest_data.py
--------------
Batch ingestion script cho hệ thống paper-rag-kg.

Flow mỗi PDF:
  1. Pipeline: detect → load → split → clean → parse → normalize → enrich
  2. LM metadata extraction (Ollama Qwen2.5-7B) — enabled by default
  3. Lưu kết quả ra JSON files vào data/processed/

Usage:
  # Chạy 1 file
  python scripts/ingest_data.py path/to/paper.pdf

  # Chạy batch (toàn bộ thư mục)
  python scripts/ingest_data.py path/to/pdf_folder/ --batch

  # Chạy với LM metadata extraction (default)
  python scripts/ingest_data.py path/to/pdf_folder/ --batch

  # Tắt LM metadata extraction
  python scripts/ingest_data.py path/to/pdf_folder/ --batch --no-lm

  # Resume (skip bài đã có output)
  python scripts/ingest_data.py path/to/pdf_folder/ --batch --resume

  # Chạy với dry-run (skip Crossref API)
  python scripts/ingest_data.py path/to/paper.pdf --dry-run-enrich
"""

import argparse
import json
import logging
import os
import sys
import time
import io
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# Ép Windows Terminal dùng UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Thêm project root vào sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.pipeline import run_pipeline, run_pipeline_batch, summarize_batch
from ingestion.schema.document_schema import UnifiedDocument

logger = logging.getLogger(__name__)
LOCKED_OLLAMA_BASE_URL = "http://n3.ckey.vn:2078"
LOCKED_OLLAMA_CHAT_ENDPOINT = "/v1/chat/completions"
LOCKED_OLLAMA_MODEL = "qwen2.5:7b-instruct-q8_0"
LOCKED_OLLAMA_TIMEOUT = "120"
LOCKED_OLLAMA_TEMPERATURE = "0.1"
LOCKED_LLM_BACKEND = "vllm"
LOCKED_VLLM_BASE_URL = "http://n3.ckey.vn:2078"
LOCKED_VLLM_MODEL_NAME = "qwen2.5:7b-instruct-q8_0"
LOCKED_VLLM_TIMEOUT_SECONDS = "180"


def _load_env_file(path: Path) -> None:
    """
    Load .env style file into process environment without overriding existing vars.
    Supported format: KEY=VALUE, ignores blank lines and comments.
    """
    if not path.exists():
        return

    try:
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        logger.debug("Failed to load env file '%s': %s", path, e)


def _force_locked_gpu_env() -> None:
    os.environ["OLLAMA_BASE_URL"] = LOCKED_OLLAMA_BASE_URL
    os.environ["OLLAMA_CHAT_ENDPOINT"] = LOCKED_OLLAMA_CHAT_ENDPOINT
    os.environ["OLLAMA_MODEL"] = LOCKED_OLLAMA_MODEL
    os.environ["OLLAMA_TIMEOUT"] = LOCKED_OLLAMA_TIMEOUT
    os.environ["OLLAMA_TEMPERATURE"] = LOCKED_OLLAMA_TEMPERATURE
    os.environ["INGESTION_LOCKED_OLLAMA_BASE_URL"] = LOCKED_OLLAMA_BASE_URL
    os.environ["INGESTION_LOCKED_OLLAMA_CHAT_ENDPOINT"] = LOCKED_OLLAMA_CHAT_ENDPOINT
    os.environ["INGESTION_LOCKED_OLLAMA_MODEL"] = LOCKED_OLLAMA_MODEL

    os.environ["LLM_BACKEND"] = LOCKED_LLM_BACKEND
    os.environ["VLLM_BASE_URL"] = LOCKED_VLLM_BASE_URL
    os.environ["VLLM_MODEL_NAME"] = LOCKED_VLLM_MODEL_NAME
    os.environ["VLLM_TIMEOUT_SECONDS"] = LOCKED_VLLM_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = "data/processed"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm"}


# ---------------------------------------------------------------------------
# LM Metadata Extraction
# ---------------------------------------------------------------------------

def _is_bad_authors(authors: list[str], title: str = "") -> bool:
    """
    Kiểm tra danh sách authors có phải rác hay không.

    Heuristics:
      - Chứa tên địa danh Việt Nam (Hải Phòng, An Lão, Hà Nội, ...)
      - Chứa fragment của tiêu đề bài báo
      - Tên quá ngắn (1 từ) hoặc quá dài (>6 từ)
      - Không có tên nào giống tên người (ít nhất 2 từ, có uppercase)
    """
    if not authors:
        return True

    # Danh sách địa danh phổ biến — nếu xuất hiện → chắc chắn sai
    _LOCATIONS = {
        "hải phòng", "hà nội", "đà nẵng", "huế", "hồ chí minh",
        "an lão", "cần thơ", "bình dương", "đồng nai", "nghệ an",
        "thanh hóa", "hải dương", "quảng ninh", "thái nguyên",
        "bắc ninh", "nam định", "bắc giang", "lào cai", "phú thọ",
        "vĩnh phúc", "hưng yên", "thái bình", "ninh bình",
        "vietnam", "viet nam", "việt nam",
    }

    title_lower = title.lower().strip()
    bad_count = 0

    for author in authors:
        a_lower = author.lower().strip()
        words = a_lower.split()

        # Check: là địa danh?
        if a_lower in _LOCATIONS:
            bad_count += 1
            continue

        # Check: trùng fragment tiêu đề (>= 4 từ trùng)?
        if title_lower and len(a_lower) > 10 and a_lower in title_lower:
            bad_count += 1
            continue

        # Check: tên quá ngắn (1 từ) hoặc quá dài (>6 từ)?
        if len(words) < 2 or len(words) > 6:
            bad_count += 1
            continue

    # Nếu > 50% authors là rác → danh sách bad
    return bad_count > len(authors) * 0.5


def _is_bad_abstract(abstract: str) -> bool:
    """Kiểm tra abstract có quá ngắn hoặc không hợp lệ."""
    if not abstract:
        return True
    stripped = abstract.strip()
    return len(stripped) < 50





# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _save_result(doc: UnifiedDocument, output_dir: Path) -> Path:
    """
    Lưu UnifiedDocument thành JSON file.

    Output format:
    {
        "paper_id": "108866_54ece3dd1f6a34bf",
        "metadata": {"title": "...", "authors": [...], ...},
        "sections": [{"name": "...", "content": "...", "section_type": "..."}, ...],
        "figures_count": 5,
        "tables_count": 2,
        "references_count": 15
    }
    """
    paper_id = doc.doc_id or Path(doc.source_file).stem
    output_path = output_dir / f"{paper_id}.json"

    result = asdict(doc)
    
    # Xử lý datetime để có thể serialize sang JSON
    if result.get("created_at") and isinstance(result["created_at"], datetime):
        result["created_at"] = result["created_at"].isoformat()

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Auto-load backend env so ingestion can run without manual `export`.
    # Priority: existing process env > backend/.env > project/.env
    _load_env_file(PROJECT_ROOT / "backend" / ".env")
    _load_env_file(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Academic paper ingestion — batch processing script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/ingest_data.py paper.pdf
  python scripts/ingest_data.py papers/ --batch
  python scripts/ingest_data.py papers/ --batch --resume
  python scripts/ingest_data.py papers/ --batch --no-lm
  python scripts/ingest_data.py paper.pdf --dry-run-enrich
        """,
    )
    parser.add_argument("path", help="File path hoặc directory (--batch mode)")
    parser.add_argument("--batch", action="store_true", help="Process toàn bộ directory")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--use-lm", action="store_true", default=True,
                        help="Bật LM metadata extraction (mặc định: bật)")
    parser.add_argument("--no-lm", dest="use_lm", action="store_false",
                        help="Tắt LM metadata extraction")
    parser.add_argument("--lm-model", default="qwen2.5:7b", metavar="MODEL",
                        help="Deprecated: ignored because ingestion is locked to GPU model qwen2.5:7b-instruct-fp16")
    parser.add_argument("--resume", action="store_true",
                        help="Skip bài đã có output JSON")
    parser.add_argument("--dry-run-enrich", action="store_true",
                        help="Skip Crossref API call")
    parser.add_argument("--aggressive", action="store_true",
                        help="Aggressive text cleaning")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Giới hạn số lượng file chạy trong chế độ batch")
    parser.add_argument("--ext", default=".pdf,.docx,.html",
                        help="Extensions cho batch mode (default: .pdf,.docx,.html)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging (DEBUG level)")

    args = parser.parse_args()

    _force_locked_gpu_env()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    output_dir = Path(args.output)
    input_path = Path(args.path)

    # ------------------------------------------------------------------ #
    # Batch mode                                                           #
    # ------------------------------------------------------------------ #
    if args.batch:
        if not input_path.is_dir():
            print(f"Error: '{args.path}' is not a directory")
            sys.exit(1)

        extensions = [e.strip() for e in args.ext.split(",")]
        file_paths: list[str] = []
        for ext in extensions:
            if not ext.startswith("."):
                ext = f".{ext}"
            file_paths.extend(str(p) for p in sorted(input_path.rglob(f"*{ext}")))

        if not file_paths:
            print(f"No files found in '{args.path}' with extensions: {extensions}")
            sys.exit(0)

        # Resume: skip bài đã có output
        if args.resume:
            original_count = len(file_paths)
            file_paths = [
                fp for fp in file_paths
                if not (output_dir / Path(fp).parent.name / f"{Path(fp).stem}.json").exists()
            ]
            skipped = original_count - len(file_paths)
            if skipped > 0:
                print(f"Resuming: skipped {skipped} already processed files")

        # Limit số lượng file
        if args.limit:
            file_paths = file_paths[:args.limit]

        if not file_paths:
            print("All files already processed. Nothing to do.")
            sys.exit(0)

        print(f"Found {len(file_paths)} files — starting batch pipeline...")
        start_time = time.monotonic()

        results = []
        saved_count = 0
        total_files = len(file_paths)

        for i, file_path in enumerate(file_paths):
            logger.info("[%d/%d] Processing: %s", i + 1, total_files, file_path)
            
            sub_dir = Path(file_path).parent.name
            file_output_dir = output_dir / sub_dir
            
            result = run_pipeline(
                file_path=file_path,
                crossref_mailto="pipeline@academic-rag.local",
                aggressive_clean=args.aggressive,
                multimodal_output_root=str(file_output_dir / "figures"),
                dry_run_enrich=args.dry_run_enrich,
                use_lm=args.use_lm,
            )
            
            if result.success and result.doc:
                doc = result.doc

                # Save
                try:
                    _save_result(doc, file_output_dir)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Failed to save {result.file_path}: {e}")
            
            results.append(result)
            status = "✓" if result.success else f"✗ ({result.failed_at})"
            logger.info("[%d/%d] %s %s — %.2fs",
                        i + 1, total_files, status, Path(file_path).name, result.duration_s)

        total_time = time.monotonic() - start_time
        summary = summarize_batch(results)

        print(f"\n{'='*60}")
        print(f"  Batch Ingestion Summary")
        print(f"{'='*60}")
        print(f"  Total     : {summary['total']}")
        print(f"  Succeeded : {summary['succeeded']}")
        print(f"  Failed    : {summary['failed']}")
        print(f"  Saved     : {saved_count}")
        print(f"  Success % : {summary['success_rate']*100:.1f}%")
        print(f"  Avg time  : {summary['avg_duration_s']:.2f}s/file")
        print(f"  Total time: {total_time:.1f}s")
        if args.use_lm:
            print(f"  LM Model  : {args.lm_model}")
        if summary["step_failures"]:
            print("  Failures by step:")
            for step, count in summary["step_failures"].items():
                print(f"    {step}: {count}")
        if summary["failed_files"]:
            print("  Failed files:")
            for fp in summary["failed_files"][:10]:
                print(f"    {fp}")
            if len(summary["failed_files"]) > 10:
                print(f"    ... (+{len(summary['failed_files'])-10} more)")
        print(f"  Output dir: {output_dir.resolve()}")
        print(f"{'='*60}")
        sys.exit(0 if summary["failed"] == 0 else 1)

    # ------------------------------------------------------------------ #
    # Single file mode                                                     #
    # ------------------------------------------------------------------ #
    if not input_path.exists():
        print(f"Error: '{args.path}' does not exist")
        sys.exit(1)

    if not input_path.is_file():
        print(f"Error: '{args.path}' is not a file. Use --batch for directories.")
        sys.exit(1)

    print(f"Processing: {args.path}")
    result = run_pipeline(
        file_path=args.path,
        aggressive_clean=args.aggressive,
        dry_run_enrich=args.dry_run_enrich,
        use_lm=args.use_lm,
    )

    if result.success and result.doc:
        doc = result.doc

        # Save
        sub_dir = Path(args.path).parent.name
        file_output_dir = output_dir / sub_dir
        output_path = _save_result(doc, file_output_dir)

        print(f"\n{'='*60}")
        print(f"  OK: Pipeline completed successfully")
        print(f"{'='*60}")
        print(f"  Title    : {doc.title}")
        print(f"  Authors  : {', '.join(doc.authors[:3])}")
        print(f"  Year     : {doc.year}")
        print(f"  Journal  : {doc.journal}")
        print(f"  Language : {doc.language}")
        print(f"  Sections : {len(doc.sections)}")
        print(f"  Figures  : {len(doc.figures)}")
        print(f"  Tables   : {len(doc.tables)}")
        print(f"  Refs     : {len(doc.references)}")
        print(f"  Pages    : {doc.page_count}")
        print(f"  Duration : {result.duration_s:.2f}s")
        print(f"  Output   : {output_path}")
        if doc.abstract:
            print(f"  Abstract : {doc.abstract[:200]}...")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"  ✗ Pipeline FAILED at step: {result.failed_at}")
        print(f"{'='*60}")
        if result.errors:
            print("  Errors:")
            for err in result.errors:
                print(f"    ! {err}")
        print(f"{'='*60}")
        sys.exit(1)


if __name__ == "__main__":
    main()
