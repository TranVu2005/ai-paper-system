"""
pdf_loader.py
-------------
Load academic PDF papers into UnifiedDocument format.

Strategy (OCR-first):
  1. PyMuPDF  → rasterize mỗi trang thành ảnh (DPI cấu hình được)
  2. OCR      → PaddleOCR (ưu tiên) hoặc pytesseract (fallback)
               extract text từ ảnh, giữ nguyên thứ tự đọc
  3. pdfplumber → detect 2-column layout, split bbox rồi extract text
                 (dùng cho trang có text layer để bổ sung/verify OCR)
  4. Image merge → ghép tile images bị chia đôi bởi "Print to PDF"
  5. Text-based metadata → extract title, authors, abstract, refs từ
                           OCR text (không cần GROBID server)
  6. Merge    → UnifiedDocument

Cấu hình:
  OCR_DPI          = 200   # DPI render trang → ảnh (150-300, cao hơn = chậm hơn)
  OCR_ENGINE       = 'auto'  # 'paddle', 'tesseract', 'auto' (thử paddle trước)
  COL_SPLIT_RATIO  = 0.50  # ngưỡng x để phân cột (0.5 = giữa trang)
  MIN_IMG_PX       = 80    # lọc ảnh nhỏ (icon, watermark)
  MIN_IMG_BYTES    = 2048  # lọc ảnh rác dưới kích thước này
"""

import base64
import io
import logging
import re
from dataclasses import replace as dc_replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

from ingestion.schema.document_schema import (
    Figure,
    Reference,
    Section,
    Table,
    UnifiedDocument,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OCR_DPI         = 200      # render resolution — 150 đủ nhanh, 300 cho scan mờ
OCR_ENGINE      = "auto"   # 'paddle' | 'tesseract' | 'auto'
COL_SPLIT_RATIO = 0.50     # x-split ratio cho 2-column layout
MIN_IMG_WIDTH   = 80       # pixel width tối thiểu để lưu figure
MIN_IMG_HEIGHT  = 80       # pixel height tối thiểu
MIN_IMG_BYTES   = 2048     # byte size tối thiểu để lọc icon/watermark

# Mapping section name tiếng Việt + tiếng Anh → section_type canonical
SECTION_TYPE_MAP: dict[str, str] = {
    # English
    "abstract":          "abstract",
    "introduction":      "introduction",
    "related work":      "related_work",
    "background":        "background",
    "literature":        "related_work",
    "methodology":       "method",
    "method":            "method",
    "methods":           "method",
    "approach":          "method",
    "proposed":          "method",
    "experiment":        "experiment",
    "experiments":       "experiment",
    "experimental":      "experiment",
    "evaluation":        "experiment",
    "result":            "result",
    "results":           "result",
    "finding":           "result",
    "discussion":        "discussion",
    "analysis":          "discussion",
    "conclusion":        "conclusion",
    "conclusions":       "conclusion",
    "concluding":        "conclusion",
    "summary":           "conclusion",
    "reference":         "references",
    "references":        "references",
    "bibliography":      "references",
    "appendix":          "appendix",
    "acknowledgement":   "acknowledgment",
    "acknowledgements":  "acknowledgment",
    "acknowledgment":    "acknowledgment",
    # Tiếng Việt
    "đặt vấn đề":         "introduction",
    "giới thiệu":         "introduction",
    "tổng quan":          "background",
    "cơ sở lý thuyết":    "background",
    "phương pháp":        "method",
    "thiết kế":           "method",
    "thiết kế kết cấu":   "method",
    "chế tạo":            "method",
    "mô hình":            "method",
    "thuật toán":         "method",
    "thực nghiệm":        "experiment",
    "thực nghiệm chạy thử": "experiment",
    "chạy thử":           "experiment",
    "kết quả":            "result",
    "kết luận":           "conclusion",
    "tài liệu tham khảo": "references",
    "tham khảo":          "references",
    "thảo luận":          "discussion",
    "nghiên cứu":         "background",
    "đánh giá":           "experiment",
}

# Heading pattern cho PDF text (tiếng Việt + tiếng Anh)
_HEADING_PATTERN = re.compile(
    r"^(?:"
    # numbered: "1. Intro", "2.3 Method", "3.1. Thiết kế"
    r"(?:\d+[\.\d]*\.?\s+)"
    r"[A-ZÀÁẠẢÃĂẮẶẲẴÂẤẬẦẨẪĐÊẾỆỀỂỄÔỐỘỒỔỖƠỚỢỜỞỠƯỨỰỪỬỮ"
    r"a-zàáạảãăắặẳẵâấậầẩẫđêếệềểễôốộồổỗơớợờởỡưứựừửữ][^\n]{2,70}"
    r"|"
    # ALL CAPS block (EN + VN uppercase)
    r"[A-ZÀÁẠẢÃĂẮẶẲẴÂẤẬẦẨẪĐÊẾỆỀỂỄÔỐỘỒỔỖƠỚỢỜỞỠƯỨỰỪỬỮ]"
    r"[A-ZÀÁẠẢÃĂẮẶẲẴÂẤẬẦẨẪĐÊẾỆỀỂỄÔỐỘỒỔỖƠỚỢỜỞỠƯỨỰỪỬỮ\s]{3,60}"
    r"[A-ZÀÁẠẢÃĂẮẶẲẴÂẤẬẦẨẪĐÊẾỆỀỂỄÔỐỘỒỔỖƠỚỢỜỞỠƯỨỰỪỬỮ]"
    r"|"
    # Named sections (EN)
    r"(?:Abstract|Introduction|Conclusion|References|Appendix"
    r"|Related Work|Methodology|Experiments|Results|Discussion"
    r"|Background|Acknowledgements?|Bibliography)"
    r"|"
    # Named sections (VN phổ biến)
    r"(?:Đặt vấn đề|Giới thiệu|Tổng quan|Kết luận|Kết quả"
    r"|Thực nghiệm|Phương pháp|Tài liệu tham khảo|Thảo luận)"
    r")$",
    re.MULTILINE,
)

# Artifacts / false-positive heading patterns
_NOT_HEADING = re.compile(
    r"[@/\\]"              # URL, email, path
    r"|^\d{4}$"            # năm đứng một mình
    r"|\d{4}\.\d{4}"       # số DOI-like
    r"|Hình \d|Bảng \d"    # caption references
    r"|Fig\.\s*\d|Table\s*\d"
)

# Date patterns để parse lịch sử bài báo
_DATE_PATTERNS = [
    re.compile(r"Ngày duyệt đăng[^\n]{0,40}(20[12]\d)"),   # published ← ưu tiên nhất
    re.compile(r"Tháng\s*\d{1,2}/(20[12]\d)"),              # tháng/năm
    re.compile(r"Ngày PB[^\n]{0,40}(20[12]\d)"),            # review date
    re.compile(r"Ngày nhận[^\n]{0,40}(20[12]\d)"),          # received
    re.compile(r"\b(20[12]\d)\b"),                           # fallback: bất kỳ năm nào
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pdf(
    file_path: str,
    ocr_engine: str = OCR_ENGINE,
    ocr_dpi: int = OCR_DPI,
) -> UnifiedDocument:
    """
    Load academic PDF vào UnifiedDocument bằng OCR pipeline.

    Args:
        file_path:  Đường dẫn PDF.
        ocr_engine: 'paddle' | 'tesseract' | 'auto'.
        ocr_dpi:    DPI render mỗi trang (150-300).

    Returns:
        UnifiedDocument với text, metadata, figures, tables đã extract.

    Raises:
        FileNotFoundError: Nếu file không tồn tại.
        RuntimeError:      Nếu cả OCR engines đều fail.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    logger.info("Loading PDF (OCR): %s", file_path)

    # --- Step 1: Rasterize all pages → PIL Images ---
    page_images, fitz_doc = _rasterize_pages(path, dpi=ocr_dpi)

    # --- Step 2: OCR mỗi trang ---
    engine = _select_ocr_engine(ocr_engine)
    page_texts = _ocr_pages(page_images, engine)

    # --- Step 3: PyMuPDF 2-column layout extraction ---
    # Với trang có text layer, extract text giữ nguyên cấu trúc cột rồi merge với OCR
    page_texts = _merge_with_pymupdf(fitz_doc, page_texts)

    # --- Step 4: Extract figures (với tile-merge) ---
    figures = _extract_figures(fitz_doc, path.stem)

    # --- Step 5: Extract tables via PyMuPDF ---
    tables = _extract_tables(fitz_doc)

    # --- Step 6: Build full text ---
    full_text = "\n\n".join(page_texts)

    # --- Step 7: Text-based metadata extraction ---
    meta = _extract_metadata(full_text)

    # --- Step 8: Extract references ---
    references = _extract_references(full_text)

    fitz_doc.close()

    doc = UnifiedDocument(
        title             = meta["title"] or path.stem,
        authors           = meta["authors"],
        year              = meta["year"],
        journal           = meta["journal"],
        keywords          = meta["keywords"],
        abstract          = meta["abstract"],
        full_text         = full_text,
        sections          = [],  # pipeline step SPLIT sẽ lo
        tables            = tables,
        figures           = figures,
        references        = references,
        source_file       = file_path,
        source_type       = "pdf",
        language          = meta["language"],
        page_count        = len(page_images),
        doc_id            = path.stem,
        processing_status = "parsed",
        created_at        = datetime.now(),
    )

    logger.info(
        "Loaded: '%s' | engine=%s | pages=%d | sections=%d | figs=%d | tables=%d | refs=%d",
        doc.title[:60], engine, doc.page_count,
        len(doc.sections), len(doc.figures),
        len(doc.tables), len(doc.references),
    )
    return doc


# ---------------------------------------------------------------------------
# Step 1: Rasterize pages
# ---------------------------------------------------------------------------

def _rasterize_pages(path: Path, dpi: int) -> tuple[list[Image.Image], fitz.Document]:
    """
    Render mỗi trang PDF thành PIL Image.

    Returns:
        (list[PIL.Image], fitz.Document) — caller chịu trách nhiệm đóng doc.
    """
    doc = fitz.open(str(path))
    images = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)  # 72 DPI là base của PDF

    for page in doc:
        pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img  = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        images.append(img)

    logger.debug("Rasterized %d pages at %d DPI", len(images), dpi)
    return images, doc


# ---------------------------------------------------------------------------
# Step 2: OCR
# ---------------------------------------------------------------------------

def _select_ocr_engine(preference: str) -> str:
    """Chọn OCR engine khả dụng."""
    if preference == "paddle":
        if _check_paddle():
            return "paddle"
        logger.warning("PaddleOCR not available — falling back to tesseract")
        return "tesseract"

    if preference == "tesseract":
        return "tesseract"

    # auto: thử paddle trước
    if _check_paddle():
        return "paddle"
    logger.info("PaddleOCR not installed — using tesseract")
    return "tesseract"


def _check_paddle() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False


def _ocr_pages(images: list[Image.Image], engine: str) -> list[str]:
    """OCR tất cả trang, trả về list[str] text theo thứ tự trang."""
    if engine == "paddle":
        return _ocr_with_paddle(images)
    return _ocr_with_tesseract(images)


def _ocr_with_paddle(images: list[Image.Image]) -> list[str]:
    """
    OCR bằng PaddleOCR — hỗ trợ cả 2.x lẫn 3.x.

    PaddleOCR 3.x (>= 3.0.0) thay đổi API hoàn toàn so với 2.x:
      - Bỏ: show_log, use_gpu, use_angle_cls
      - Đổi: use_angle_cls → use_textline_orientation
      - Đổi: lang='vi' chỉ hoạt động với ocr_version='PP-OCRv3'
              (PP-OCRv5 mặc định dùng model đa ngôn ngữ, không cần chỉ định lang)
      - Đổi: ocr() deprecated → dùng predict() thay thế
      - Đổi: format kết quả trả về là iterator of result objects, không phải list[list]

    Tiếng Việt trong 3.x:
      - PP-OCRv5 (default): model đa ngôn ngữ, nhận diện được tiếng Việt
        mà không cần lang='vi'. Dùng cách này.
      - PP-OCRv3 + lang='vi': vẫn hoạt động nhưng model cũ hơn.
    """
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise RuntimeError("paddleocr not installed. Run: pip install paddleocr")

    # Detect version để dùng đúng API
    paddle_version = _get_paddleocr_version()
    is_v3_plus = paddle_version >= (3, 0, 0)

    if is_v3_plus:
        # PaddleOCR 3.x — Sử dụng lang='vi' để nhận diện tiếng Việt chính xác hơn
        # (Tự động tải PP-OCRv3 cho tiếng Việt do v4 chưa publish model vi riêng)
        ocr = PaddleOCR(
            use_textline_orientation = True,   # thay thế use_angle_cls
            enable_mkldnn = False,             # Fix bug: ConvertPirAttribute2RuntimeAttribute
            lang = "vi",
        )
    else:
        # PaddleOCR 2.x — API cũ
        ocr = PaddleOCR(
            use_angle_cls = True,
            lang          = "vi",
            show_log      = False,
            use_gpu       = True,
        )

    results = []
    for i, img in enumerate(images):
        try:
            img_array = _pil_to_numpy(img)

            if is_v3_plus:
                page_text = _paddle_v3_extract(ocr, img_array, i)
            else:
                page_text = _paddle_v2_extract(ocr, img_array, i)

            results.append(page_text)
            logger.info("  -> Page %d/%d OCR done: %d chars (paddle v%d.x)", i + 1, len(images), len(page_text), paddle_version[0])

        except Exception as e:
            logger.warning("PaddleOCR failed on page %d: %s", i + 1, e)
            results.append("")

    return results


def _get_paddleocr_version() -> tuple[int, int, int]:
    """Parse version string của paddleocr thành tuple (major, minor, patch)."""
    try:
        import paddleocr
        ver_str = getattr(paddleocr, "__version__", "2.0.0")
        parts = re.split(r"[.\-]", ver_str)
        return tuple(int(p) for p in parts[:3] if p.isdigit())  # type: ignore[return-value]
    except Exception:
        return (2, 0, 0)


def _paddle_v3_extract(ocr, img_array, page_idx: int) -> str:
    """
    Extract text bằng PaddleOCR 3.x API.

    3.x trả về iterator của result objects, mỗi object có:
      - result.rec_texts: list[str]      — text của từng dòng
      - result.rec_scores: list[float]   — confidence
      - result.dt_polys: list[ndarray]   — bounding boxes (4 điểm)

    Sort theo y-center của box (top→bottom) để giữ đúng thứ tự đọc.
    """
    MIN_CONF = 0.5

    try:
        # predict() trả về list[OCRResult] trong 3.x
        pred_results = ocr.predict(img_array)
    except Exception as e:
        logger.warning("PaddleOCR 3.x predict() failed on page %d: %s", page_idx + 1, e)
        return ""

    lines_with_y: list[tuple[float, str]] = []

    for res in pred_results:
        # res có thể là OCRResult object hoặc dict tùy sub-version
        try:
            texts  = res.rec_texts   if hasattr(res, "rec_texts")  else res.get("rec_texts", [])
            scores = res.rec_scores  if hasattr(res, "rec_scores") else res.get("rec_scores", [])
            boxes  = res.dt_polys    if hasattr(res, "dt_polys")   else res.get("dt_polys", [])
        except Exception:
            continue

        for text, score, box in zip(texts, scores, boxes):
            if not text or not text.strip():
                continue
            if score < MIN_CONF:
                continue
            # Tính y-center từ bounding box (4 điểm: top-left, top-right, bottom-right, bottom-left)
            try:
                import numpy as np
                y_center = float(np.mean([pt[1] for pt in box]))
            except Exception:
                y_center = 0.0
            lines_with_y.append((y_center, text.strip()))

    # Sort top→bottom
    lines_with_y.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in lines_with_y)


def _paddle_v2_extract(ocr, img_array, page_idx: int) -> str:
    """
    Extract text bằng PaddleOCR 2.x API (legacy).

    2.x trả về list[list[item]] trong đó mỗi item là:
      [box_coords, (text, confidence)]
    với box_coords = [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
    """
    MIN_CONF = 0.5

    try:
        result = ocr.ocr(img_array, cls=True)
    except Exception as e:
        logger.warning("PaddleOCR 2.x ocr() failed on page %d: %s", page_idx + 1, e)
        return ""

    lines_with_y: list[tuple[float, str]] = []

    if result and result[0]:
        for item in result[0]:
            try:
                box        = item[0]   # [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
                text, conf = item[1]   # ("text string", 0.97)
                if not text or not text.strip() or conf < MIN_CONF:
                    continue
                y_center = (box[0][1] + box[2][1]) / 2   # (top_y + bottom_y) / 2
                lines_with_y.append((y_center, text.strip()))
            except (IndexError, TypeError, ValueError):
                continue

    lines_with_y.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in lines_with_y)


def _ocr_with_tesseract(images: list[Image.Image]) -> list[str]:
    """
    OCR bằng pytesseract (fallback).
    - lang='vie+eng' để nhận diện cả tiếng Việt lẫn tiếng Anh
    - config PSM 3 = fully automatic page segmentation
    """
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError(
            "pytesseract not installed. Run: pip install pytesseract\n"
            "Also install tesseract binary: https://github.com/tesseract-ocr/tesseract"
        )

    results = []
    for i, img in enumerate(images):
        try:
            text = pytesseract.image_to_string(
                img,
                lang   = "vie+eng",
                config = "--psm 3",
            )
            results.append(text.strip())
            logger.info("  -> Page %d/%d OCR done: %d chars (tesseract)", i + 1, len(images), len(text))
        except Exception as e:
            logger.warning("Tesseract failed on page %d: %s", i + 1, e)
            results.append("")

    return results


def _pil_to_numpy(img: Image.Image):
    """Convert PIL Image → numpy array (tránh import numpy ở top-level)."""
    import numpy as np
    return np.array(img)


# ---------------------------------------------------------------------------
# Step 3: PyMuPDF 2-column correction
# ---------------------------------------------------------------------------

def _merge_with_pymupdf(fitz_doc: fitz.Document, ocr_texts: list[str]) -> list[str]:
    """
    Với các trang digital (có text layer):
    - Dùng PyMuPDF `get_text("text", sort=True)` để đọc text theo đúng cấu trúc cột/layout.
    - Ưu tiên text của PyMuPDF nếu đủ dài, ngược lại (trang scan) thì giữ OCR text.
    
    Giúp xử lý bài báo 2-cột cực kỳ chuẩn xác mà không cần external thư viện như pdfplumber.
    """
    merged = list(ocr_texts)

    try:
        for i, page in enumerate(fitz_doc):
            height = page.rect.height
            header_margin = height * 0.06  # Bỏ qua 6% trên cùng (header)
            footer_margin = height * 0.94  # Bỏ qua 6% dưới cùng (footer/page number)

            blocks = page.get_text("blocks")
            
            valid_blocks = []
            for b in blocks:
                if b[6] != 0:  # Không phải text block
                    continue
                # Loại bỏ header và footer noise
                if b[1] < header_margin or b[3] > footer_margin:
                    continue
                valid_blocks.append(b[4].strip())
                
            # Nối các block text lại với nhau theo thứ tự đọc tự nhiên
            pymupdf_text = "\n\n".join(valid_blocks).strip()

            ocr_len     = len(merged[i])
            pymupdf_len = len(pymupdf_text)

            # Ưu tiên PyMuPDF nếu text đủ dài (chứng tỏ không phải trang scan hoàn toàn)
            # OCR đôi khi nhầm thứ tự cột hoặc thiếu sót, text gốc luôn chuẩn hơn.
            if pymupdf_len > ocr_len * 0.8 and pymupdf_len > 100:
                merged[i] = pymupdf_text
                logger.debug(
                    "Page %d: using PyMuPDF text (%d chars > OCR %d chars)",
                    i + 1, pymupdf_len, ocr_len,
                )

    except Exception as e:
        logger.warning("PyMuPDF merge failed: %s — using OCR only", e)

    return merged


# ---------------------------------------------------------------------------
# Step 4: Figure extraction với tile-merge
# ---------------------------------------------------------------------------

def _collect_page_image_info(fitz_doc: fitz.Document, page: fitz.Page) -> list[dict]:
    """Thu thập metadata từng ảnh nhúng trong trang."""
    infos = []
    for img_info in page.get_images(full=True):
        xref  = img_info[0]
        rects = page.get_image_rects(xref)
        base  = fitz_doc.extract_image(xref)
        if not rects:
            continue
        infos.append({
            "xref" : xref,
            "rect" : rects[0],
            "w"    : base["width"],
            "h"    : base["height"],
            "ext"  : base["ext"],
            "data" : base["image"],
        })
    return infos


def _find_tile_groups(infos: list[dict]) -> list[list[int]]:
    """
    Phát hiện nhóm ảnh là tile của nhau:
    - Cùng x0, x1 (thẳng hàng ngang)
    - y1 của ảnh trên ≈ y0 của ảnh dưới (liền kề dọc)
    - Cùng pixel width

    Returns:
        list các nhóm index, mỗi nhóm sorted top→bottom.
    """
    n       = len(infos)
    visited = [False] * n
    groups  = []

    for i in range(n):
        if visited[i]:
            continue
        group = [i]
        for j in range(i + 1, n):
            if visited[j]:
                continue
            a, b = infos[i], infos[j]
            same_x    = (abs(a["rect"].x0 - b["rect"].x0) < 2 and
                         abs(a["rect"].x1 - b["rect"].x1) < 2)
            same_pw   = a["w"] == b["w"]
            adjacent  = (abs(a["rect"].y1 - b["rect"].y0) < 2 or
                         abs(b["rect"].y1 - a["rect"].y0) < 2)
            if same_x and same_pw and adjacent:
                group.append(j)
                visited[j] = True
        visited[i] = True
        groups.append(sorted(group, key=lambda k: infos[k]["rect"].y0))

    return groups


def _merge_tile_images(tile_infos: list[dict]) -> tuple[bytes, str]:
    """Ghép các tile theo chiều dọc (top→bottom). Returns (bytes, ext)."""
    imgs    = [Image.open(io.BytesIO(t["data"])) for t in tile_infos]
    total_h = sum(im.height for im in imgs)
    canvas  = Image.new("RGB", (imgs[0].width, total_h))
    y_off   = 0
    for im in imgs:
        canvas.paste(im, (0, y_off))
        y_off += im.height
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue(), "jpeg"


def _extract_figures(fitz_doc: fitz.Document, pdf_stem: str) -> list[Figure]:
    """
    Trích xuất figures từ tất cả trang:
    - Lọc ảnh quá nhỏ (icon, watermark)
    - Tự động ghép tile images
    - Encode base64 cho từng figure
    """
    figures     = []
    fig_counter = 0

    for page_num in range(fitz_doc.page_count):
        page  = fitz_doc[page_num]
        infos = _collect_page_image_info(fitz_doc, page)

        # Lọc ảnh quá nhỏ
        infos = [
            info for info in infos
            if info["w"] >= MIN_IMG_WIDTH
            and info["h"] >= MIN_IMG_HEIGHT
            and len(info["data"]) >= MIN_IMG_BYTES
        ]

        if not infos:
            continue

        tile_groups = _find_tile_groups(infos)

        for group in tile_groups:
            tiles = [infos[k] for k in group]

            if len(tiles) == 1:
                img_data = tiles[0]["data"]
                caption  = f"Figure on page {page_num + 1}"
                merged   = False
            else:
                img_data, _ = _merge_tile_images(tiles)
                caption  = f"Figure on page {page_num + 1} (merged {len(tiles)} tiles)"
                merged   = True
                logger.debug("Page %d: merged %d tiles", page_num + 1, len(tiles))

            b64 = base64.b64encode(img_data).decode("utf-8")
            fig_counter += 1

            figures.append(Figure(
                caption     = caption,
                index       = fig_counter,
                figure_type = "image",
                base64_data = b64,
                alt_text    = f"merged_tiles={len(tiles)}" if merged else None,
            ))

    logger.debug("Extracted %d figures", len(figures))
    return figures


# ---------------------------------------------------------------------------
# Step 5: Table extraction via PyMuPDF
# ---------------------------------------------------------------------------

def _extract_tables(fitz_doc: fitz.Document) -> list[Table]:
    """
    Extract tables bằng PyMuPDF (`page.find_tables()`).
    """
    tables = []
    table_counter = 0

    for page_num, page in enumerate(fitz_doc):
        try:
            # find_tables tự động phân tích cấu trúc bảng
            page_tabs = page.find_tables()
            if not page_tabs:
                continue

            for tab in page_tabs.tables:
                raw_rows = tab.extract()
                
                # Lọc các dòng rỗng
                cleaned_rows = []
                for row in raw_rows:
                    cleaned_row = [str(cell).strip() if cell else "" for cell in row]
                    # Bỏ qua dòng nếu toàn rỗng
                    if any(cleaned_row):
                        cleaned_rows.append(cleaned_row)

                if len(cleaned_rows) < 2:
                    continue  # Bảng quá nhỏ

                # Tách header và data
                headers = cleaned_rows[0]
                data    = cleaned_rows[1:]

                table_counter += 1
                caption = f"Table {table_counter} on page {page_num + 1}"

                tables.append(Table(
                    caption = caption,
                    index   = table_counter,
                    headers = headers,
                    data    = data,
                ))

        except Exception as e:
            logger.warning("PyMuPDF table extraction failed on page %d: %s", page_num + 1, e)

    logger.debug("Extracted %d tables", len(tables))
    return tables


# ---------------------------------------------------------------------------
# Step 7: Metadata extraction từ OCR text
# ---------------------------------------------------------------------------

def _extract_metadata(full_text: str) -> dict:
    """
    Extract metadata từ raw OCR text của bài báo.
    Xử lý cả bài báo tiếng Việt lẫn tiếng Anh.
    """
    meta: dict = {
        "title"    : "",
        "authors"  : [],
        "abstract" : "",
        "year"     : None,
        "journal"  : None,
        "keywords" : [],
        "language" : None,
    }

    if not full_text:
        return meta

    # Detect ngôn ngữ (đơn giản: tỷ lệ ký tự tiếng Việt)
    vn_chars = sum(1 for c in full_text[:500]
                   if c in "àáạảãăắặẳẵâấậầẩẫđèéẹẻẽêếệềểễìíịỉĩòóọỏõôốộồổỗơớợờởỡùúụủũưứựừửữỳýỵỷỹ"
                              "ÀÁẠẢÃĂẮẶẲẴÂẤẬẦẨẪĐÈÉẸẺẼÊẾỆỀỂỄÌÍỊỈĨÒÓỌỎÕÔỐỘỒỔỖƠỚỢỜỞỠÙÚỤỦŨƯỨỰỪỬỮỲÝỴỶỸ")
    meta["language"] = "vi" if vn_chars > 5 else "en"

    # --- Title ---
    meta["title"] = _extract_title(full_text)

    # --- Authors ---
    meta["authors"] = _extract_authors(full_text, meta["title"])

    # --- Abstract ---
    meta["abstract"] = _extract_abstract(full_text)

    # --- Keywords ---
    meta["keywords"] = _extract_keywords(full_text)

    # --- Year (ưu tiên ngày duyệt đăng) ---
    for pat in _DATE_PATTERNS:
        m = pat.search(full_text[:3000])
        if m:
            meta["year"] = int(m.group(1))
            break

    # --- Journal ---
    journal_m = re.search(
        r"(?:TẠP CHÍ|JOURNAL OF|PROCEEDINGS OF|CONFERENCE ON)\s+([^\n]{5,80})",
        full_text[:1000], re.IGNORECASE,
    )
    if journal_m:
        meta["journal"] = journal_m.group(1).strip().rstrip(".,")

    return meta


def _extract_title(text: str) -> str:
    """
    Trích tiêu đề: tìm khối IN HOA liên tiếp sau header trường/tạp chí.
    Ghép các dòng tiêu đề span nhiều dòng.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    _skip = re.compile(
        r"^(\d{1,4}\s|trang\s|page\s|vol\.?\s|số\s|trường\s|tạp chí\s|journal\s)",
        re.IGNORECASE,
    )
    _stop = re.compile(
        r"^(Tóm tắt|Abstract|Từ khóa|Keywords|\d+\.\s|Email|Ngày\s|Khoa\s|Faculty\s)",
        re.IGNORECASE,
    )

    title_lines: list[str] = []
    found_upper = False

    for line in lines[:30]:
        if len(line) < 8:
            continue
        if _skip.match(line):
            continue
        if _stop.match(line) or "@" in line:
            if title_lines:
                break
            continue

        upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line), 1)
        is_upper    = upper_ratio > 0.35

        if is_upper and 8 < len(line) < 250:
            title_lines.append(line)
            found_upper = True
        elif found_upper:
            # Kết thúc khối tiêu đề khi gặp dòng không IN HOA
            break

    return " ".join(title_lines) if title_lines else ""


def _extract_authors(text: str, title: str) -> list[str]:
    """Trích tác giả từ vùng giữa tiêu đề và abstract."""
    # Tìm vị trí abstract để giới hạn vùng search
    abstract_m = re.search(r"(?:Tóm tắt|Abstract)\s*[:\.]?", text, re.IGNORECASE)
    search_end = abstract_m.start() if abstract_m else min(len(text), 1500)

    # Tìm vị trí sau tiêu đề
    search_start = 0
    if title:
        title_idx = text.find(title)
        if title_idx >= 0:
            search_start = title_idx + len(title)

    region = text[search_start:search_end]

    authors = []
    for line in region.split("\n"):
        line = line.strip()
        if not line or len(line) < 4 or len(line) > 150:
            continue
        # Bỏ qua dòng tổ chức, email, ngày tháng
        if re.match(r"^(Khoa|Trường|Email|Ngày|Faculty|Department|University|@)",
                    line, re.IGNORECASE):
            continue
        if "@" in line or "http" in line:
            continue
        # Tên tác giả: 2+ từ, không có số, không quá dài
        parts = re.split(r"[,;]|\s+và\s+|\s+and\s+", line)
        for part in parts:
            name = part.strip()
            words = name.split()
            if (2 <= len(words) <= 6
                    and not re.search(r"\d", name)
                    and len(name) < 60
                    and re.search(r"[A-ZÀ-Ỹ\u0110]", name)):
                authors.append(name)

    return authors


def _extract_abstract(text: str) -> str:
    """Trích abstract (tiếng Việt ưu tiên, fallback tiếng Anh)."""
    # Tiếng Việt
    m = re.search(
        r"(?:Tóm tắt)\s*[:\.]?\s*(.*?)(?=\n\s*(?:Từ khóa|Keywords|Abstract|\d+\.\s))",
        text, re.DOTALL | re.IGNORECASE,
    )
    if m and len(m.group(1).strip()) > 50:
        return re.sub(r"\s*\n\s*", " ", m.group(1)).strip()

    # Tiếng Anh
    m = re.search(
        r"(?:Abstract)\s*[:\.]?\s*(.*?)(?=\n\s*(?:Keywords|Từ khóa|\d+\.\s|Introduction))",
        text, re.DOTALL | re.IGNORECASE,
    )
    if m and len(m.group(1).strip()) > 50:
        return re.sub(r"\s*\n\s*", " ", m.group(1)).strip()

    return ""


def _extract_keywords(text: str) -> list[str]:
    """Trích keywords (tiếng Việt + tiếng Anh)."""
    m = re.search(
        r"(?:Từ khóa|Keywords)\s*[:\.]?\s*(.+?)(?:\n\s*\n|\n\s*\d+\.|\n\s*[A-Z]{2})",
        text, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    kw_text = re.sub(r"\s*\n\s*", " ", m.group(1)).strip()
    keywords = [kw.strip().rstrip(".") for kw in re.split(r"[,;]", kw_text) if kw.strip()]
    return [kw for kw in keywords if 2 < len(kw) < 80]


# ---------------------------------------------------------------------------
# Step 8: Section splitting từ OCR text
# ---------------------------------------------------------------------------

def _split_sections(full_text: str) -> list[Section]:
    """
    Split OCR text thành các Section theo heading detection.

    Thứ tự ưu tiên:
      1. Regex heading (numbered + ALL CAPS + VN named sections)
      2. Paragraph fallback nếu không detect được heading nào
    """
    sections = _split_by_headings(full_text)
    if sections:
        return sections
    return _split_by_paragraphs(full_text)


def _split_by_headings(text: str) -> list[Section]:
    """Detect headings bằng regex, split text thành sections."""
    if not text:
        return []

    lines    = text.split("\n")
    sections: list[Section] = []
    order    = 0

    current_heading: Optional[str] = None
    current_level   = 1
    current_lines:  list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        if _is_heading(stripped):
            # Flush section hiện tại
            if current_heading is not None:
                content = "\n".join(current_lines).strip()
                if len(content) >= 20:
                    sections.append(Section(
                        name         = current_heading,
                        content      = content,
                        order        = order,
                        level        = _heading_level(current_heading),
                        section_type = _infer_type(current_heading),
                    ))
                    order += 1

            current_heading = stripped
            current_level   = _heading_level(stripped)
            current_lines   = []
        else:
            current_lines.append(stripped)

    # Flush cuối
    if current_heading is not None:
        content = "\n".join(current_lines).strip()
        if len(content) >= 20:
            sections.append(Section(
                name         = current_heading,
                content      = content,
                order        = order,
                level        = current_level,
                section_type = _infer_type(current_heading),
            ))

    # Gán parent_section
    sections = _assign_parents(sections)
    return sections


def _is_heading(line: str) -> bool:
    """Kiểm tra line có phải heading không."""
    if len(line) > 120:
        return False
    if _NOT_HEADING.search(line):
        return False
    return bool(_HEADING_PATTERN.match(line))


def _heading_level(heading: str) -> int:
    """Infer heading level từ text."""
    m = re.match(r"^(\d+)(\.(\d+))?(\.(\d+))?", heading)
    if m:
        if m.group(5):
            return 3
        if m.group(3):
            return 2
        return 1
    return 1


def _infer_type(name: str) -> str:
    """Map heading name → section_type."""
    if not name:
        return "other"
    normalized = re.sub(r"^\d+[\.\d]*\.?\s*", "", name.lower()).strip()
    normalized = re.sub(r"[^\w\s]", " ", normalized).strip()
    for key, stype in sorted(SECTION_TYPE_MAP.items(), key=lambda x: -len(x[0])):
        if key in normalized:
            return stype
    return "other"


def _assign_parents(sections: list[Section]) -> list[Section]:
    """Gán parent_section cho subsections dựa vào level."""
    result = []
    for i, s in enumerate(sections):
        parent = None
        if s.level > 1:
            for prev in reversed(sections[:i]):
                if prev.level < s.level:
                    parent = prev.name
                    break
        result.append(dc_replace(s, parent_section=parent))
    return result


def _split_by_paragraphs(text: str) -> list[Section]:
    """Fallback: mỗi paragraph block = 1 Section."""
    if not text:
        return []
    blocks   = re.split(r"\n\s*\n", text.strip())
    sections = []
    for order, block in enumerate(blocks):
        content = block.strip()
        if len(content) < 50:
            continue
        first_line = content.split("\n")[0].strip()[:80]
        sections.append(Section(
            name         = first_line,
            content      = content,
            order        = order,
            level        = 1,
            section_type = _infer_type(first_line),
        ))
    return sections


# ---------------------------------------------------------------------------
# Step 9: Reference extraction
# ---------------------------------------------------------------------------

def _extract_references(full_text: str) -> list[Reference]:
    """
    Trích danh sách tài liệu tham khảo từ section cuối cùng.
    Pattern: "TÀI LIỆU THAM KHẢO" hoặc "REFERENCES"
    """
    refs = []

    ref_m = re.search(r"(?:TÀI LIỆU THAM KHẢO|REFERENCES|BIBLIOGRAPHY)",
                      full_text, re.IGNORECASE)
    if not ref_m:
        return refs

    ref_section = full_text[ref_m.end():]

    # Tách từng mục theo số thứ tự "1. ...", "[1] ..."
    items = re.split(r"\n\s*(?:\[\d+\]|\d+\.)\s+", ref_section)

    for raw in items:
        raw = " ".join(raw.split()).strip()
        if not raw or len(raw) < 10:
            continue
        # Lọc artifact (bảng kết quả, v.v.)
        if re.match(r"^(STT|Thực nghiệm|Kết quả|Vị trí)", raw):
            continue

        # Extract năm
        year_m = re.search(r"\b(20[012]\d|19[89]\d)\b", raw)
        year   = int(year_m.group(1)) if year_m else None

        # Extract DOI nếu có
        doi_m = re.search(r"https?://doi\.org/(\S+)", raw)
        doi   = doi_m.group(1).rstrip(".,") if doi_m else None

        refs.append(Reference(
            raw_text = raw,
            year     = year,
            doi      = doi,
            title    = raw[:120],
        ))

    logger.debug("Extracted %d references", len(refs))
    return refs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_section_type(section_name: str) -> Optional[str]:
    """Public alias để các module khác import nếu cần."""
    return _infer_type(section_name)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python pdf_loader.py <path_to_pdf> [--engine paddle|tesseract]")
        sys.exit(1)

    pdf_path   = sys.argv[1]
    engine_arg = "auto"
    if "--engine" in sys.argv:
        idx        = sys.argv.index("--engine")
        engine_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "auto"

    try:
        doc = load_pdf(pdf_path, ocr_engine=engine_arg)
        print(f"\n{'='*60}")
        print(f"  Title    : {doc.title}")
        print(f"  Authors  : {', '.join(doc.authors)}")
        print(f"  Year     : {doc.year}")
        print(f"  Journal  : {doc.journal}")
        print(f"  Language : {doc.language}")
        print(f"  Keywords : {', '.join(doc.keywords[:5])}")
        if doc.abstract:
            print(f"  Abstract : {doc.abstract[:200]}...")
        print(f"  Sections : {len(doc.sections)}")
        for s in doc.sections[:8]:
            parent = f" (parent: {s.parent_section})" if s.parent_section else ""
            print(f"    [{s.order}] L{s.level} [{s.section_type:12s}] '{s.name[:50]}'{parent}")
        if len(doc.sections) > 8:
            print(f"    ... (+{len(doc.sections)-8} more)")
        print(f"  Figures  : {len(doc.figures)}")
        print(f"  Tables   : {len(doc.tables)}")
        print(f"  Refs     : {len(doc.references)}")
        print(f"  Pages    : {doc.page_count}")
        print(f"{'='*60}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
