"""
multimodal_normalizer.py
------------------------
Normalize multimodal content trong UnifiedDocument sau parser layer.

Responsibilities:
  - Figure: extract images từ PDF/DOCX → lưu ra data/multimodal/figures/{doc_stem}/fig_{index}.png
  - Formula: validate raw string, đảm bảo display_text có sẵn (không transform)
  - Table:   giữ nguyên list[list] — table_parser đã đủ

Input:  UnifiedDocument sau loaders + parsers
Output: UnifiedDocument với figure.path đã được resolve

Output convention:
  data/multimodal/figures/{doc_stem}/fig_{index}.png
  data/multimodal/figures/{doc_stem}/fig_{index}.jpg  (giữ format gốc nếu không phải PNG)

Strategy:
  - PDF  → PyMuPDF page.get_images() → extract xref → save
  - DOCX → python-docx inline shapes → extract blob → save
  - HTML → figure.path là src URL/relative path → copy/download → save
  - Formula → chỉ ensure display_text không None
    display_text backfill dùng _make_display_text() local (không import từ formula_parser
    để tránh coupling — logic đủ đơn giản để duplicate)
"""

import logging
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Optional

from ingestion.schema.document_schema import Figure, Formula, UnifiedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_ROOT = Path("data/multimodal/figures")

# Image format → extension mapping (từ PyMuPDF colorspace/filter)
IMAGE_EXT_MAP = {
    "png":   ".png",
    "jpeg":  ".jpg",
    "jpg":   ".jpg",
    "jpe":   ".jpg",
    "jp2":   ".jpg",
    "j2k":   ".jpg",
    "jbig2": ".png",
    "jxr":   ".png",
    "tiff":  ".png",
    "bmp":   ".png",
    "gif":   ".png",
}

MIN_IMAGE_SIZE = 1024   # bytes — bỏ qua ảnh quá nhỏ (icons, decorations)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_multimodal(
    doc: UnifiedDocument,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    skip_existing: bool = True,
) -> UnifiedDocument:
    """
    Normalize tất cả multimodal content trong UnifiedDocument.

    Args:
        doc:           UnifiedDocument sau loaders + parsers
        output_root:   Root directory để lưu extracted images
        skip_existing: True = bỏ qua nếu file đã tồn tại (idempotent)

    Returns:
        UnifiedDocument với figure.path đã được resolve.
        Figures không extract được → path giữ nguyên None.
    """
    doc_stem   = _get_doc_stem(doc)
    figure_dir = output_root / doc_stem
    figure_dir.mkdir(parents=True, exist_ok=True)

    # --- Figures ---
    enriched_figures, figures_changed = _normalize_figures(
        doc           = doc,
        figure_dir    = figure_dir,
        skip_existing = skip_existing,
    )

    # --- Formulas --- chỉ ensure display_text
    enriched_formulas, formulas_changed = _normalize_formulas(doc.formulas)

    # Tables — giữ nguyên
    changes = {}
    if figures_changed:
        changes["figures"] = enriched_figures
    if formulas_changed:
        changes["formulas"] = enriched_formulas

    if not changes:
        logger.debug(f"No multimodal changes for: '{doc.title[:50]}'")
        return doc

    result   = replace(doc, **changes)
    resolved = sum(1 for f in result.figures if f.path is not None)
    logger.info(
        f"Normalized '{doc_stem}': "
        f"{resolved}/{len(result.figures)} figures resolved, "
        f"{len(result.formulas)} formulas validated"
    )
    return result


# ---------------------------------------------------------------------------
# Figure normalization
# ---------------------------------------------------------------------------

def _normalize_figures(
    doc: UnifiedDocument,
    figure_dir: Path,
    skip_existing: bool,
) -> tuple[list[Figure], bool]:
    """
    Extract và save figures theo source_type.

    Returns:
        (list[Figure] với path đã update, bool changed)
    """
    if not doc.figures:
        return doc.figures, False

    source_type = doc.source_type
    source_file = doc.source_file

    if source_type == "pdf" and source_file:
        image_blobs = _extract_images_from_pdf(source_file)
        updated     = _assign_images_to_figures(doc.figures, image_blobs, figure_dir, skip_existing)
        return updated, updated != doc.figures

    elif source_type == "docx" and source_file:
        image_blobs = _extract_images_from_docx(source_file)
        updated     = _assign_images_to_figures(doc.figures, image_blobs, figure_dir, skip_existing)
        return updated, updated != doc.figures

    elif source_type == "html":
        updated = _normalize_html_figures(doc.figures, figure_dir, skip_existing)
        return updated, updated != doc.figures

    else:
        logger.debug(f"No image extraction for source_type='{source_type}'")
        return doc.figures, False


def _assign_images_to_figures(
    figures: list[Figure],
    image_blobs: list[dict],
    figure_dir: Path,
    skip_existing: bool,
) -> list[Figure]:
    """
    Assign extracted image blobs vào Figure objects theo thứ tự index.

    image_blobs: list of {"data": bytes, "ext": ".png"}
    """
    updated  = []
    blob_idx = 0

    for figure in figures:
        if blob_idx >= len(image_blobs):
            updated.append(figure)
            continue

        blob  = image_blobs[blob_idx]
        ext   = blob.get("ext", ".png")
        idx   = figure.index if figure.index is not None else blob_idx
        fname = f"fig_{idx:03d}{ext}"
        fpath = figure_dir / fname

        # Skip nếu đã tồn tại
        if skip_existing and fpath.exists():
            logger.debug(f"Skipping existing: {fpath}")
            updated.append(replace(figure, path=str(fpath)))
            blob_idx += 1
            continue

        # Save
        try:
            fpath.write_bytes(blob["data"])
            updated.append(replace(figure, path=str(fpath)))
            logger.debug(f"Saved figure: {fpath} ({len(blob['data'])} bytes)")
        except Exception as e:
            logger.warning(f"Failed to save figure {fname}: {e}")
            updated.append(figure)

        blob_idx += 1

    return updated


def _normalize_html_figures(
    figures: list[Figure],
    figure_dir: Path,
    skip_existing: bool,
) -> list[Figure]:
    """
    Normalize HTML figures:
      - Absolute path → validate tồn tại
      - Relative path → resolve
      - HTTP URL      → download
      - None          → giữ nguyên
    """
    updated = []
    for figure in figures:
        if not figure.path:
            updated.append(figure)
            continue

        path_str = figure.path

        # HTTP URL → download
        if path_str.startswith("http://") or path_str.startswith("https://"):
            saved = _download_image(path_str, figure_dir, figure.index or 0, skip_existing)
            updated.append(replace(figure, path=saved) if saved else figure)

        # Local path → copy vào figure_dir
        elif Path(path_str).exists():
            src = Path(path_str)
            dst = figure_dir / f"fig_{figure.index or 0:03d}{src.suffix or '.png'}"
            if not (skip_existing and dst.exists()):
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    logger.warning(f"Failed to copy figure: {e}")
            updated.append(replace(figure, path=str(dst)) if dst.exists() else figure)

        else:
            logger.debug(f"Figure path not resolvable: {path_str}")
            updated.append(figure)

    return updated


# ---------------------------------------------------------------------------
# PDF image extraction (PyMuPDF)
# ---------------------------------------------------------------------------

def _extract_images_from_pdf(source_file: str) -> list[dict]:
    """
    Extract all images từ PDF dùng PyMuPDF, tự động merge tile images.

    Nhiều PDF (đặc biệt "Print to PDF") chia 1 figure thành 2+ tile images
    xếp chồng nhau theo chiều dọc. Logic tile-merge:
      - Cùng x0, x1 (thẳng hàng ngang) với tolerance 5px
      - y1 của ảnh trên ≈ y0 của ảnh dưới (liền kề dọc) tolerance 5px
      - Cùng pixel width

    Returns:
        list of {"data": bytes, "ext": ".png"}
        Sorted theo thứ tự xuất hiện (page order), tiles đã được merge.
    """
    blobs = []
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(source_file)
        for page_num, page in enumerate(doc):
            page_infos = _collect_page_images(doc, page)

            # Lọc ảnh quá nhỏ (icons, watermarks)
            page_infos = [
                info for info in page_infos
                if info["w"] >= 80 and info["h"] >= 80
                and len(info["data"]) >= MIN_IMAGE_SIZE
            ]

            if not page_infos:
                continue

            # Detect tile groups và merge
            tile_groups = _find_tile_groups_normalizer(page_infos)

            for group in tile_groups:
                tiles = [page_infos[k] for k in group]

                if len(tiles) == 1:
                    # Ảnh đơn — giữ nguyên
                    data    = tiles[0]["data"]
                    ext_str = tiles[0].get("ext", "png").lower()
                    ext     = IMAGE_EXT_MAP.get(ext_str, ".png")
                else:
                    # Merge tiles theo chiều dọc
                    data, ext_str = _merge_tile_blobs(tiles)
                    ext = f".{ext_str}"
                    logger.debug(
                        f"Page {page_num + 1}: merged {len(tiles)} tile images "
                        f"(xrefs={[t['xref'] for t in tiles]})"
                    )

                blobs.append({"data": data, "ext": ext})

        doc.close()
        logger.debug(f"Extracted {len(blobs)} images (after tile-merge) from PDF: {source_file}")

    except ImportError:
        logger.warning("PyMuPDF not installed — cannot extract PDF images. Run: pip install pymupdf")
    except Exception as e:
        logger.error(f"PDF image extraction failed: {e}")

    return blobs


def _collect_page_images(fitz_doc, page) -> list[dict]:
    """Thu thập metadata + data từng ảnh nhúng trong trang."""
    infos = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
            base  = fitz_doc.extract_image(xref)
            if not rects:
                continue
            infos.append({
                "xref": xref,
                "rect": rects[0],
                "w":    base["width"],
                "h":    base["height"],
                "ext":  base.get("ext", "png"),
                "data": base["image"],
            })
        except Exception as e:
            logger.debug(f"Failed to extract image xref={xref}: {e}")
    return infos


def _find_tile_groups_normalizer(infos: list[dict]) -> list[list[int]]:
    """
    Phát hiện nhóm ảnh là tile của nhau (vertical split).

    Criteria:
      - Cùng x0, x1 (tolerance 5px)
      - y1 của ảnh trên ≈ y0 của ảnh dưới (tolerance 5px)
      - Cùng pixel width

    Returns:
        list các nhóm index, mỗi nhóm sorted top→bottom.
    """
    TOLERANCE = 5  # px tolerance cho vị trí

    n       = len(infos)
    visited = [False] * n
    groups  = []

    for i in range(n):
        if visited[i]:
            continue
        group = [i]
        visited[i] = True

        # Tìm tất cả tiles liền kề với group hiện tại (greedy expansion)
        changed = True
        while changed:
            changed = False
            for j in range(n):
                if visited[j]:
                    continue
                # Kiểm tra j có liền kề với bất kỳ member nào trong group
                for k in group:
                    if _are_vertical_tiles(infos[k], infos[j], TOLERANCE):
                        group.append(j)
                        visited[j] = True
                        changed = True
                        break

        groups.append(sorted(group, key=lambda idx: infos[idx]["rect"].y0))

    return groups


def _are_vertical_tiles(a: dict, b: dict, tolerance: float = 5) -> bool:
    """Kiểm tra 2 ảnh có phải là vertical tiles (nửa trên + nửa dưới)."""
    # Cùng x range (thẳng hàng ngang)
    same_x = (abs(a["rect"].x0 - b["rect"].x0) < tolerance and
              abs(a["rect"].x1 - b["rect"].x1) < tolerance)
    if not same_x:
        return False

    # Cùng pixel width
    if a["w"] != b["w"]:
        return False

    # Liền kề theo chiều dọc (y1 của ảnh trên ≈ y0 của ảnh dưới)
    adjacent = (abs(a["rect"].y1 - b["rect"].y0) < tolerance or
                abs(b["rect"].y1 - a["rect"].y0) < tolerance)
    return adjacent


def _merge_tile_blobs(tiles: list[dict]) -> tuple[bytes, str]:
    """
    Ghép các tile images theo chiều dọc (top→bottom).

    Returns:
        (merged_bytes, format_string) — e.g. (bytes, "jpeg")
    """
    import io
    from PIL import Image

    imgs    = [Image.open(io.BytesIO(t["data"])) for t in tiles]
    total_h = sum(im.height for im in imgs)
    max_w   = max(im.width for im in imgs)
    canvas  = Image.new("RGB", (max_w, total_h))

    y_off = 0
    for im in imgs:
        canvas.paste(im, (0, y_off))
        y_off += im.height

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue(), "jpg"


# ---------------------------------------------------------------------------
# DOCX image extraction (python-docx)
# ---------------------------------------------------------------------------

def _extract_images_from_docx(source_file: str) -> list[dict]:
    """
    Extract all images từ DOCX dùng python-docx inline shapes.

    Strategy:
      - docx.part.image_parts → tất cả images embedded trong file
      - Iterate theo thứ tự để giữ document order

    Returns:
        list of {"data": bytes, "ext": ".png"}
    """
    blobs = []
    try:
        from docx import Document

        doc         = Document(source_file)
        image_parts = doc.part.image_parts
        for image_part in image_parts:
            try:
                data = image_part.blob
                ct   = image_part.content_type  # e.g. "image/png"

                if len(data) < MIN_IMAGE_SIZE:
                    continue

                ext = _content_type_to_ext(ct)
                blobs.append({"data": data, "ext": ext})

            except Exception as e:
                logger.debug(f"Failed to extract DOCX image: {e}")

        logger.debug(f"Extracted {len(blobs)} images from DOCX: {source_file}")

    except ImportError:
        logger.warning("python-docx not installed — cannot extract DOCX images. Run: pip install python-docx")
    except Exception as e:
        logger.error(f"DOCX image extraction failed: {e}")

    return blobs


def _content_type_to_ext(content_type: str) -> str:
    """Map MIME content type → file extension."""
    mapping = {
        "image/png":  ".png",
        "image/jpeg": ".jpg",
        "image/jpg":  ".jpg",
        "image/gif":  ".gif",
        "image/bmp":  ".bmp",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
        "image/emf":  ".emf",
        "image/wmf":  ".wmf",
    }
    return mapping.get(content_type.lower(), ".png")


# ---------------------------------------------------------------------------
# HTTP image download (HTML figures)
# ---------------------------------------------------------------------------

def _download_image(
    url: str,
    figure_dir: Path,
    index: int,
    skip_existing: bool,
) -> Optional[str]:
    """
    Download image từ URL và lưu vào figure_dir.

    Returns:
        Path string nếu thành công, None nếu fail.
    """
    url_path = url.split("?")[0]
    suffix   = Path(url_path).suffix.lower()
    ext      = suffix if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else ".png"
    fname    = f"fig_{index:03d}{ext}"
    fpath    = figure_dir / fname

    if skip_existing and fpath.exists():
        return str(fpath)

    try:
        import requests
        response = requests.get(url, timeout=15, headers={
            "User-Agent": "academic-rag-pipeline/1.0"
        })
        response.raise_for_status()

        data = response.content
        if len(data) < MIN_IMAGE_SIZE:
            logger.debug(f"Downloaded image too small, skipping: {url}")
            return None

        fpath.write_bytes(data)
        logger.debug(f"Downloaded figure: {url} → {fpath}")
        return str(fpath)

    except ImportError:
        logger.warning("requests not installed — cannot download HTML figures")
        return None
    except Exception as e:
        logger.warning(f"Failed to download figure {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Formula normalization
# ---------------------------------------------------------------------------

def _normalize_formulas(formulas: list[Formula]) -> tuple[list[Formula], bool]:
    """
    Validate và ensure display_text cho mỗi formula.
    Không transform LaTeX/MathML — giữ nguyên raw string.

    Dùng _make_display_text() local thay vì import từ formula_parser
    để tránh coupling — logic đủ đơn giản để duplicate ở đây.

    Returns:
        (list[Formula], bool changed)
    """
    if not formulas:
        return formulas, False

    updated = []
    changed = False

    for formula in formulas:
        if formula.display_text is None and formula.raw:
            display = _make_display_text(formula.raw)
            updated.append(replace(formula, display_text=display))
            changed = True
        else:
            updated.append(formula)

    return (updated if changed else formulas), changed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_display_text(raw: str) -> str:
    """
    Minimal LaTeX → plain text cho display_text backfill.

    Intentionally simple — chỉ dùng cho fallback khi formula_parser
    chưa generate display_text. Không cần match đầy đủ LATEX_TO_PLAIN
    trong formula_parser.

    Example:
        r"\\alpha + \\beta = \\gamma"  →  "  +  =  "  →  "(cleaned)"
    """
    if not raw:
        return ""
    text = re.sub(r"\\[a-zA-Z]+", " ", raw)   # bỏ LaTeX commands
    text = re.sub(r"[{}$]", "", text)          # bỏ braces và $
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _get_doc_stem(doc: UnifiedDocument) -> str:
    """
    Tạo directory name cho document.
    Dùng source_file stem, fallback sang title slug.

    Examples:
        source_file="papers/attention_2017.pdf" → "attention_2017"
        title="Attention Is All You Need"        → "attention_is_all_you_need"
    """
    if doc.source_file:
        return Path(doc.source_file).stem

    if doc.title and doc.title != "Unknown":
        slug = doc.title.lower()
        slug = re.sub(r"[^\w\s]", "", slug)
        slug = re.sub(r"\s+", "_", slug.strip())
        return slug[:60]

    return "unknown_document"


# ---------------------------------------------------------------------------
# Quick self-test (run: python multimodal_normalizer.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    print("=" * 60)
    print("TEST A: dry run với figures path=None (PDF source)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_root = Path(tmp_dir) / "multimodal" / "figures"

        doc_a = UnifiedDocument(
            title       = "Test Paper",
            source_file = "papers/test_paper.pdf",
            source_type = "pdf",
            figures     = [
                Figure(caption="Figure 1: Architecture overview", index=0),
                Figure(caption="Figure 2: Training loss curve",   index=1),
            ],
        )

        result_a = normalize_multimodal(doc_a, output_root=output_root)
        print(f"Figures: {len(result_a.figures)}")
        for fig in result_a.figures:
            print(f"  [{fig.index}] path={fig.path}")

    print()
    print("=" * 60)
    print("TEST B: HTML figures với local path")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_root = Path(tmp_dir) / "multimodal" / "figures"

        # Tạo fake image file
        fake_img = Path(tmp_dir) / "figure1.png"
        fake_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)  # fake PNG > MIN_SIZE

        doc_b = UnifiedDocument(
            title       = "HTML Paper",
            source_file = "papers/paper.html",
            source_type = "html",
            figures     = [
                Figure(caption="Fig 1", path=str(fake_img), index=0),
                Figure(caption="Fig 2", path=None,          index=1),
            ],
        )

        result_b = normalize_multimodal(doc_b, output_root=output_root)
        for fig in result_b.figures:
            print(f"  [{fig.index}] path={fig.path}")

    print()
    print("=" * 60)
    print("TEST C: Formula display_text backfill (local _make_display_text)")
    print("=" * 60)

    doc_c = UnifiedDocument(
        title       = "Formula Paper",
        source_file = "papers/formula_paper.pdf",
        source_type = "pdf",
        formulas    = [
            Formula(raw=r"\alpha + \beta = \gamma", formula_type="inline",
                    index=0, display_text=None),           # cần backfill
            Formula(raw=r"E = mc^2", formula_type="inline",
                    index=1, display_text="E = mc^2"),     # đã có → không thay đổi
        ],
    )

    result_c = normalize_multimodal(doc_c)
    for f in result_c.formulas:
        print(f"  [{f.index}] display_text='{f.display_text}'")

    assert result_c.formulas[0].display_text is not None, "Should backfill display_text"
    assert result_c.formulas[1].display_text == "E = mc^2", "Should not overwrite existing"
    print("✓ Formula display_text backfill correct")

    print()
    print("=" * 60)
    print("TEST D: changed flag — không mutate nếu không có gì thay đổi")
    print("=" * 60)

    doc_d = UnifiedDocument(
        title       = "No Change Paper",
        source_file = "papers/no_change.pdf",
        source_type = "pdf",
        formulas    = [
            Formula(raw=r"x + y", formula_type="inline",
                    index=0, display_text="x + y"),   # đã có display_text
        ],
        figures = [],
    )

    result_d = normalize_multimodal(doc_d)
    assert result_d is doc_d, "Should return same object when nothing changed"
    print("✓ No unnecessary copy when nothing changed")

    print()
    print("=" * 60)
    print("TEST E: _get_doc_stem cases")
    print("=" * 60)
    cases = [
        UnifiedDocument(source_file="papers/attention_2017.pdf", source_type="pdf"),
        UnifiedDocument(title="Attention Is All You Need",        source_type="pdf"),
        UnifiedDocument(source_type="pdf"),
    ]
    for c in cases:
        print(f"  stem='{_get_doc_stem(c)}'")
    print("=" * 60)
