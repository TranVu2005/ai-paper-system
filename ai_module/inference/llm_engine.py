"""
llm_engine.py
-------------
Ollama LM Client cho hệ thống ingestion.

Gọi Ollama REST API (localhost:11434) để:
  - generate(prompt, model) → raw text response
  - extract_json(prompt, model) → parsed dict từ LM output
  - extract_metadata_lm(full_text) → metadata JSON cho bài báo

Setup trước khi chạy:
  ollama pull qwen2.5:7b

Config:
  - Model mặc định: qwen2.5:7b
  - Temperature: 0.1 (deterministic)
  - Timeout: 120s
  - Max input: 3000 ký tự đầu bài báo
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"
DEFAULT_MODEL = "qwen2.5:7b"
TEMPERATURE = 0.1
TIMEOUT = 120  # seconds
MAX_INPUT_CHARS = 3000  # chỉ gửi 3000 ký tự đầu bài báo

# Prompt template cho metadata extraction
METADATA_PROMPT_TEMPLATE = """Bạn là trợ lý trích xuất metadata từ bài báo khoa học.
Trích xuất JSON với các trường sau từ đoạn text bài báo:
{{
  "title": "tiêu đề bài báo",
  "authors": ["tên tác giả 1", "tên tác giả 2"],
  "abstract": "tóm tắt bài báo",
  "keywords": ["từ khóa 1", "từ khóa 2"],
  "year": 2024,
  "journal": "tên tạp chí hoặc hội nghị",
  "language": "vi hoặc en"
}}

Quy tắc:
- Chỉ trả JSON, không giải thích.
- Nếu bài báo có cả tiêu đề tiếng Việt và tiếng Anh, LUÔN ƯU TIÊN trích xuất tiêu đề tiếng Việt cho trường 'title'.
- Nếu bài báo có cả tóm tắt tiếng Việt và tiếng Anh, LUÔN ƯU TIÊN tiếng Việt cho trường 'abstract'.
- Nếu không tìm thấy trường nào, để giá trị null.
- authors là mảng string, mỗi phần tử là tên đầy đủ 1 tác giả.
- keywords là mảng string.
- year là số nguyên (int), không phải string.

TEXT:
{text}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """
    Gọi Ollama generate API, trả về raw text response.

    Args:
        prompt: Prompt string gửi cho model.
        model:  Tên model Ollama (mặc định: qwen2.5:7b).

    Returns:
        Raw text response từ LM.

    Raises:
        RuntimeError: Nếu Ollama không khả dụng hoặc request fail.
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests not installed. Run: pip install requests")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": TEMPERATURE,
        },
    }

    try:
        response = requests.post(
            OLLAMA_GENERATE_URL,
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
            "Make sure Ollama is running: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Ollama request timed out after {TIMEOUT}s. "
            "Model may be loading — try again."
        )
    except Exception as e:
        raise RuntimeError(f"Ollama generate failed: {e}")


def extract_json(prompt: str, model: str = DEFAULT_MODEL) -> Optional[dict]:
    """
    Gọi Ollama generate, parse JSON từ response.

    Args:
        prompt: Prompt yêu cầu LM trả JSON.
        model:  Tên model Ollama.

    Returns:
        Parsed dict nếu thành công, None nếu parse fail.
    """
    raw_response = generate(prompt, model)

    if not raw_response:
        logger.warning("Ollama returned empty response")
        return None

    return _parse_json_response(raw_response)


def extract_metadata_lm(
    full_text: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Trích xuất metadata từ bài báo bằng LM.

    Gửi 3000 ký tự đầu bài báo + prompt → LM trả JSON metadata.
    Nếu LM fail → trả dict rỗng (pipeline dùng regex fallback cũ).

    Args:
        full_text: Raw OCR text của bài báo.
        model:     Tên model Ollama.

    Returns:
        dict với keys: title, authors, abstract, keywords, year, journal, language.
        Dict rỗng nếu LM fail.
    """
    if not full_text or len(full_text.strip()) < 100:
        logger.warning("Text too short for LM metadata extraction")
        return {}

    # Chỉ gửi 3000 ký tự đầu
    truncated = full_text[:MAX_INPUT_CHARS]
    prompt = METADATA_PROMPT_TEMPLATE.format(text=truncated)

    try:
        result = extract_json(prompt, model)
        if result is None:
            logger.warning("LM metadata extraction returned None")
            return {}

        # Validate và normalize output
        return _normalize_metadata(result)

    except RuntimeError as e:
        logger.warning(f"LM metadata extraction failed: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error in LM metadata extraction: {e}")
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> Optional[dict]:
    """
    Parse JSON từ LM response.
    LM có thể trả JSON wrapped trong markdown code block hoặc text thừa.
    """
    # Try 1: parse trực tiếp
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try 2: extract JSON block từ markdown ```json ... ```
    json_block = re.search(r"```(?:json)?\s*\n?(.+?)\n?```", text, re.DOTALL)
    if json_block:
        try:
            return json.loads(json_block.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try 3: tìm { ... } đầu tiên trong text
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.debug(f"Could not parse JSON from LM response: {text[:200]}")
    return None


def _normalize_metadata(raw: dict) -> dict:
    """
    Validate và normalize metadata dict từ LM.
    Đảm bảo đúng type cho mỗi field.
    """
    meta: dict = {}

    # title — string
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        meta["title"] = title.strip()

    # authors — list[str]
    authors = raw.get("authors")
    if isinstance(authors, list):
        meta["authors"] = [
            str(a).strip() for a in authors
            if isinstance(a, str) and a.strip()
        ]
    elif isinstance(authors, str) and authors.strip():
        meta["authors"] = [a.strip() for a in authors.split(",") if a.strip()]

    # abstract — string
    abstract = raw.get("abstract")
    if isinstance(abstract, str) and len(abstract.strip()) > 20:
        meta["abstract"] = abstract.strip()

    # keywords — list[str]
    keywords = raw.get("keywords")
    if isinstance(keywords, list):
        meta["keywords"] = [
            str(k).strip() for k in keywords
            if isinstance(k, str) and k.strip()
        ]

    # year — int
    year = raw.get("year")
    if isinstance(year, int) and 1900 <= year <= 2100:
        meta["year"] = year
    elif isinstance(year, str):
        try:
            y = int(year)
            if 1900 <= y <= 2100:
                meta["year"] = y
        except ValueError:
            pass

    # journal — string
    journal = raw.get("journal")
    if isinstance(journal, str) and journal.strip() and journal.strip().lower() != "null":
        meta["journal"] = journal.strip()

    # language — string
    language = raw.get("language")
    if isinstance(language, str) and language.strip().lower() in ("vi", "en"):
        meta["language"] = language.strip().lower()

    return meta


def is_ollama_available(model: str = DEFAULT_MODEL) -> bool:
    """
    Kiểm tra Ollama server có đang chạy và model có sẵn không.

    Returns:
        True nếu Ollama sẵn sàng.
    """
    try:
        import requests
        response = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=5,
        )
        if response.status_code != 200:
            return False

        tags = response.json()
        models = [m.get("name", "") for m in tags.get("models", [])]
        # Check model name (có thể có hoặc không có tag :latest)
        return any(model in m or m.startswith(model.split(":")[0]) for m in models)

    except Exception:
        return False


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("TEST: Ollama availability check")
    print("=" * 60)

    available = is_ollama_available()
    print(f"Ollama available: {available}")

    if not available:
        print(f"\nOllama not running at {OLLAMA_BASE_URL}")
        print("Start with: ollama serve")
        print(f"Pull model: ollama pull {DEFAULT_MODEL}")
    else:
        print(f"\n✓ Ollama ready with model: {DEFAULT_MODEL}")

        # Test generate
        print("\n" + "=" * 60)
        print("TEST: Simple generate")
        print("=" * 60)
        try:
            response = generate("Xin chào, bạn là ai?")
            print(f"Response: {response[:200]}")
        except RuntimeError as e:
            print(f"Error: {e}")

        # Test extract_metadata_lm
        print("\n" + "=" * 60)
        print("TEST: extract_metadata_lm")
        print("=" * 60)
        sample_text = """
        TẠP CHÍ KHOA HỌC VÀ CÔNG NGHỆ
        
        NGHIÊN CỨU ỨNG DỤNG MẠNG NEURAL TRONG PHÁT HIỆN LỖI PHẦN MỀM
        
        Nguyễn Văn A, Trần Thị B
        Khoa Công nghệ thông tin, Trường Đại học ABC
        
        Tóm tắt: Bài báo này trình bày phương pháp ứng dụng mạng neural
        trong phát hiện lỗi phần mềm. Kết quả thực nghiệm cho thấy phương pháp
        đề xuất đạt độ chính xác 95.2% trên bộ dữ liệu thử nghiệm.
        
        Từ khóa: mạng neural, phát hiện lỗi, phần mềm, deep learning
        
        Ngày nhận bài: 15/01/2024
        Ngày duyệt đăng: 20/03/2024
        """
        try:
            meta = extract_metadata_lm(sample_text)
            print(f"Metadata: {json.dumps(meta, ensure_ascii=False, indent=2)}")
        except RuntimeError as e:
            print(f"Error: {e}")

    print("=" * 60)
"""
Description: Implement Ollama LM client for metadata extraction.
Uses Ollama REST API (localhost:11434) with Qwen2.5-7B model.
Provides generate(), extract_json(), and extract_metadata_lm() functions.
"""
