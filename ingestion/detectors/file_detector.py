"""
file_detector.py
----------------
Detect file type for academic paper ingestion pipeline.
Supports: PDF, DOCX, HTML

Strategy:
  1. Magic bytes (most reliable — checks actual file content)
  2. Fallback to file extension (for edge cases)
"""

import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Supported MIME types → internal type names
MIME_TYPE_MAP: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "text/html": "html",
    "application/xhtml+xml": "html",
}

# Extension fallback map
EXTENSION_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
}

FileType = Literal["pdf", "docx", "html", "unknown"]


def detect_file_type(file_path: str) -> FileType:
    """
    Detect the type of an academic paper file.

    Args:
        file_path: Path to the file.

    Returns:
        One of: "pdf", "docx", "html", "unknown"

    Examples:
        >>> detect_file_type("paper.pdf")
        'pdf'
        >>> detect_file_type("paper.DOCX")
        'docx'
        >>> detect_file_type("arxiv_paper.html")
        'html'
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    # --- Strategy 1: magic bytes ---
    detected = _detect_by_magic(path)
    if detected != "unknown":
        logger.debug("Detected '%s' via magic bytes: %s", detected, file_path)
        return detected

    # --- Strategy 2: extension fallback ---
    detected = _detect_by_extension(path)
    if detected != "unknown":
        logger.debug("Detected '%s' via extension fallback: %s", detected, file_path)
        return detected

    logger.warning("Could not detect file type: %s", file_path)
    return "unknown"


def _detect_by_magic(path: Path) -> FileType:
    """Detect file type by reading magic bytes (actual file content)."""
    try:
        with open(path, "rb") as f:
            header = f.read(512)

        # PDF: %PDF
        if header[:4] == b"%PDF":
            return "pdf"

        # DOCX/ZIP: PK\x03\x04
        if header[:4] == b"PK\x03\x04":
            try:
                import zipfile
                with zipfile.ZipFile(path) as z:
                    if "word/document.xml" in z.namelist():
                        return "docx"
            except Exception:
                pass

        # HTML: decode trước cho chắc, tránh ambiguity với multi-byte encoding
        try:
            header_text = header[:512].decode("utf-8", errors="ignore").lower()
            if (
                "<html" in header_text
                or "<!doctype" in header_text
                or "<head" in header_text
                or "<body" in header_text
            ):
                return "html"
        except Exception:
            pass

    except Exception as e:
        logger.debug("Magic detection failed: %s", e)

    # Nếu có python-magic thì dùng thêm
    try:
        import magic
        mime = magic.from_file(str(path), mime=True)
        return MIME_TYPE_MAP.get(mime, "unknown")
    except ImportError:
        return "unknown"


def _detect_by_extension(path: Path) -> FileType:
    """Detect file type by file extension (case-insensitive)."""
    ext = path.suffix.lower()
    return EXTENSION_MAP.get(ext, "unknown")


def is_supported(file_path: str) -> bool:
    """Return True if the file type is supported by the pipeline."""
    return detect_file_type(file_path) != "unknown"


def assert_supported(file_path: str) -> FileType:
    """
    Like detect_file_type() but raises ValueError for unsupported types.
    Use this at pipeline entry point to fail fast.
    """
    file_type = detect_file_type(file_path)
    if file_type == "unknown":
        raise ValueError(
            f"Unsupported file type: '{file_path}'. "
            f"Supported: PDF, DOCX, HTML"
        )
    return file_type


# ---------------------------------------------------------------------------
# Quick self-test (run: python file_detector.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import tempfile
    import os

    logging.basicConfig(level=logging.DEBUG)

    # Test 1: extension fallback (files rỗng — chỉ test extension)
    test_cases = [
        ("test.pdf",  "pdf"),
        ("test.PDF",  "pdf"),
        ("test.docx", "docx"),
        ("test.DOCX", "docx"),
        ("test.html", "html"),
        ("test.htm",  "html"),
        ("test.xyz",  "unknown"),
    ]

    print("=== Extension fallback tests (files rỗng — magic bytes không được test ở đây) ===")
    all_passed = True
    for filename, expected in test_cases:
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as f:
            tmp_path = f.name
        try:
            result = detect_file_type(tmp_path)
            status = "PASS" if result == expected else "FAIL"
            if status == "FAIL":
                all_passed = False
            print(f"  [{status}] {filename} → expected={expected}, got={result}")
        finally:
            os.unlink(tmp_path)

    # Test 2: magic bytes — extension sai nhưng content đúng
    print("\n=== Magic bytes tests (extension sai, content đúng) ===")
    magic_tests = [
        (b"%PDF-1.4",    ".xyz",  "pdf"),      # PDF magic, extension lạ
        (b"PK\x03\x04",  ".xyz",  "unknown"),  # ZIP nhưng không phải DOCX
        (b"<!DOCTYPE html><html>", ".xyz", "html"),  # HTML magic, extension lạ
    ]
    for content, suffix, expected in magic_tests:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(content)
            tmp_path = f.name
        try:
            result = detect_file_type(tmp_path)
            status = "PASS" if result == expected else "FAIL"
            if status == "FAIL":
                all_passed = False
            print(f"  [{status}] magic={content[:16]} ext={suffix} → expected={expected}, got={result}")
        finally:
            os.unlink(tmp_path)

    # Test 3: error handling
    print("\n=== Error handling test ===")
    try:
        detect_file_type("/nonexistent/path/paper.pdf")
        print("  [FAIL] should have raised FileNotFoundError")
        all_passed = False
    except FileNotFoundError:
        print("  [PASS] FileNotFoundError raised correctly")

    print(f"\n{'All tests passed.' if all_passed else 'Some tests FAILED.'}")
    sys.exit(0 if all_passed else 1)
