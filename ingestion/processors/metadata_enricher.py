"""
metadata_enricher.py
--------------------
Enrich UnifiedDocument metadata via Crossref API.

Input:  UnifiedDocument (sau khi qua loaders + parsers)
Output: UnifiedDocument với metadata đầy đủ hơn:
  - title, authors, journal, year (từ Crossref nếu loader miss)
  - doi (tìm bằng title + authors nếu chưa có)
  - publisher, volume, issue, pages (extra fields từ Crossref)
  - citation_count (is-referenced-by-count từ Crossref)

Strategy:
  1. Nếu document đã có DOI  → lookup trực tiếp qua Crossref Works API
  2. Nếu không có DOI        → query Crossref bằng title + authors
  3. Merge kết quả vào document — chỉ fill fields còn trống (không overwrite)
  4. Rate limiting: 1 request/second (Crossref polite pool)

Crossref API docs: https://api.crossref.org/swagger-ui/index.html
"""

import logging
import time
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import quote

from ingestion.schema.document_schema import UnifiedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CROSSREF_BASE_URL  = "https://api.crossref.org"
CROSSREF_WORKS_URL = f"{CROSSREF_BASE_URL}/works"

# Polite pool: thêm mailto để được ưu tiên queue riêng
# https://github.com/CrossRef/rest-api-doc#good-manners--more-reliable-service
MAILTO = "pipeline@academic-rag.local"

REQUEST_TIMEOUT     = 15      # seconds
RATE_LIMIT_DELAY    = 1.0     # seconds giữa các requests (polite pool)
MAX_QUERY_RESULTS   = 3       # số kết quả trả về khi query by title
MIN_SCORE_THRESHOLD = 50.0    # Crossref relevance score tối thiểu để accept
MIN_TITLE_SIMILARITY = 0.8    # difflib ratio tối thiểu để accept kết quả

# Họ phổ biến — tránh dùng làm author query vì noise cao
COMMON_SURNAMES = {"wang", "li", "zhang", "chen", "liu", "smith", "johnson"}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_metadata(
    doc: UnifiedDocument,
    mailto: str = MAILTO,
    dry_run: bool = False,
) -> UnifiedDocument:
    """
    Enrich metadata của UnifiedDocument via Crossref API.

    Args:
        doc:     UnifiedDocument sau loader + parser
        mailto:  Email cho Crossref polite pool (tăng rate limit)
        dry_run: True = chỉ log, không gọi API (dùng cho testing)

    Returns:
        UnifiedDocument với metadata đã enrich.
        Nếu API fail → trả về document gốc không thay đổi.
    """
    if dry_run:
        logger.info(f"[dry_run] Would enrich: '{doc.title}'")
        return doc

    crossref_data = None

    # Strategy 1: có DOI → lookup trực tiếp
    if doc.doi:
        logger.info(f"Enriching via DOI: {doc.doi}")
        crossref_data = _lookup_by_doi(doc.doi, mailto)

    # Strategy 2: không có DOI → query by title + authors
    if crossref_data is None:
        if doc.title and doc.title != "Unknown":
            logger.info(f"DOI not found — querying Crossref by title: '{doc.title[:60]}'")
            crossref_data = _query_by_title(doc.title, doc.authors, mailto)
        else:
            logger.warning("Cannot enrich: no DOI and no title available")
            return doc

    if crossref_data is None:
        logger.warning(f"Crossref returned no usable data for: '{doc.title[:60]}'")
        return doc

    # Merge — chỉ fill fields còn trống
    enriched = _merge_metadata(doc, crossref_data)

    logger.info(
        f"Enriched '{enriched.title[:60]}' — "
        f"doi={'✓' if enriched.doi else '✗'}, "
        f"year={'✓' if enriched.year else '✗'}, "
        f"journal={'✓' if enriched.journal else '✗'}, "
        f"citation_count={'✓' if enriched.citation_count is not None else '✗'}"
    )
    return enriched


def enrich_batch(
    docs: list[UnifiedDocument],
    mailto: str = MAILTO,
    dry_run: bool = False,
) -> list[UnifiedDocument]:
    """
    Enrich một batch UnifiedDocuments với rate limiting.

    Args:
        docs:    list[UnifiedDocument]
        mailto:  Email cho Crossref polite pool
        dry_run: True = không gọi API thực

    Returns:
        list[UnifiedDocument] đã enrich
    """
    results = []
    for i, doc in enumerate(docs):
        logger.debug(f"Enriching {i + 1}/{len(docs)}: '{doc.title[:50]}'")
        results.append(enrich_metadata(doc, mailto=mailto, dry_run=dry_run))

        # Rate limiting — không áp dụng cho item cuối
        if i < len(docs) - 1 and not dry_run:
            time.sleep(RATE_LIMIT_DELAY)

    return results


# ---------------------------------------------------------------------------
# Crossref API calls
# ---------------------------------------------------------------------------

def _lookup_by_doi(doi: str, mailto: str) -> Optional[dict]:
    """
    Lookup metadata bằng DOI trực tiếp.
    Endpoint: GET /works/{doi}

    Returns:
        Parsed metadata dict hoặc None nếu fail.
    """
    url = f"{CROSSREF_WORKS_URL}/{quote(doi, safe='/')}"
    response = _get(url, params={"mailto": mailto})

    if response is None:
        return None

    try:
        message = response.get("message", {})
        return _parse_crossref_message(message)
    except Exception as e:
        logger.debug(f"Failed to parse Crossref DOI response: {e}")
        return None


def _query_by_title(
    title: str,
    authors: list[str],
    mailto: str,
) -> Optional[dict]:
    """
    Query Crossref bằng title + authors để tìm DOI và metadata.
    Endpoint: GET /works?query.title=...&query.author=...

    Dùng Crossref relevance score + title similarity để filter kết quả.

    Returns:
        Metadata dict của kết quả tốt nhất, hoặc None.
    """
    params: dict = {
        "query.title":  title,
        "rows":         MAX_QUERY_RESULTS,
        "mailto":       mailto,
        "select":       "DOI,title,author,published,container-title,publisher,score,volume,issue,page,is-referenced-by-count",
    }

    # Chọn author ít phổ biến nhất để giảm noise
    if authors:
        params["query.author"] = _pick_best_author(authors)

    response = _get(CROSSREF_WORKS_URL, params=params)
    if response is None:
        return None

    try:
        items = response.get("message", {}).get("items", [])
        if not items:
            return None

        # Lấy item có score cao nhất vượt threshold
        best = None
        best_score = 0.0

        for item in items:
            score = float(item.get("score", 0))
            if score > best_score:
                best_score = score
                best = item

        if best is None or best_score < MIN_SCORE_THRESHOLD:
            logger.debug(
                f"Best Crossref score {best_score:.1f} < threshold {MIN_SCORE_THRESHOLD} "
                f"— rejecting result"
            )
            return None

        # Title similarity check — đảm bảo kết quả đúng paper
        crossref_titles = best.get("title", [])
        crossref_title  = crossref_titles[0] if crossref_titles else ""
        similarity      = _title_similarity(title, crossref_title)

        if similarity < MIN_TITLE_SIMILARITY:
            logger.debug(
                f"Title similarity {similarity:.2f} < threshold {MIN_TITLE_SIMILARITY} "
                f"— rejecting: '{crossref_title[:60]}'"
            )
            return None

        logger.debug(
            f"Crossref query matched — score: {best_score:.1f}, "
            f"title similarity: {similarity:.2f}"
        )
        return _parse_crossref_message(best)

    except Exception as e:
        logger.debug(f"Failed to parse Crossref query response: {e}")
        return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_crossref_message(message: dict) -> dict:
    """
    Parse một Crossref Works message object thành metadata dict.

    Crossref message format:
    {
      "DOI": "10.xxxx/...",
      "title": ["Paper Title"],
      "author": [{"given": "John", "family": "Doe"}, ...],
      "published": {"date-parts": [[2023, 5, 12]]},
      "container-title": ["Journal Name"],
      "publisher": "Publisher Name",
      "volume": "12",
      "issue": "3",
      "page": "100-110",
      "score": 95.2,
      "is-referenced-by-count": 142
    }
    """
    data: dict = {}

    # DOI
    doi = message.get("DOI", "")
    if doi:
        data["doi"] = doi.strip()

    # Title — Crossref trả về list
    titles = message.get("title", [])
    if titles:
        data["title"] = titles[0].strip() if isinstance(titles, list) else str(titles).strip()

    # Authors
    authors = []
    for author in message.get("author", []):
        given  = author.get("given", "").strip()
        family = author.get("family", "").strip()
        if family:
            name = f"{given} {family}".strip() if given else family
            authors.append(name)
    if authors:
        data["authors"] = authors

    # Year từ published date-parts
    published  = message.get("published", {}) or message.get("published-print", {})
    date_parts = published.get("date-parts", [[]])
    if date_parts and date_parts[0]:
        try:
            data["year"] = int(date_parts[0][0])
        except (ValueError, IndexError):
            pass

    # Journal (container-title)
    container = message.get("container-title", [])
    if container:
        data["journal"] = container[0].strip() if isinstance(container, list) else str(container).strip()

    # Publisher
    publisher = message.get("publisher", "")
    if publisher:
        data["publisher"] = publisher.strip()

    # Volume, issue, pages
    if message.get("volume"):
        data["volume"] = str(message["volume"])
    if message.get("issue"):
        data["issue"] = str(message["issue"])
    if message.get("page"):
        data["pages"] = str(message["page"])

    # Citation count
    citation_count = message.get("is-referenced-by-count")
    if citation_count is not None:
        data["citation_count"] = int(citation_count)

    return data


# ---------------------------------------------------------------------------
# Metadata merge
# ---------------------------------------------------------------------------

def _merge_metadata(doc: UnifiedDocument, crossref_data: dict) -> UnifiedDocument:
    """
    Merge Crossref data vào UnifiedDocument.

    Rule: chỉ fill fields còn trống — không overwrite data từ loader.
    Exception: DOI luôn được update nếu Crossref tìm thấy.

    Args:
        doc:           Original UnifiedDocument
        crossref_data: Parsed dict từ Crossref

    Returns:
        UnifiedDocument mới với fields đã merge (dataclass immutable-style)
    """
    updates: dict = {}

    # DOI — luôn update nếu Crossref có (loader có thể miss DOI)
    if crossref_data.get("doi") and not doc.doi:
        updates["doi"] = crossref_data["doi"]

    # Title — chỉ fill nếu loader trả về "Unknown" hoặc rỗng
    if crossref_data.get("title"):
        if not doc.title or doc.title == "Unknown":
            updates["title"] = crossref_data["title"]

    # Authors — chỉ fill nếu rỗng
    if crossref_data.get("authors") and not doc.authors:
        updates["authors"] = crossref_data["authors"]

    # Year — chỉ fill nếu None
    if crossref_data.get("year") and doc.year is None:
        updates["year"] = crossref_data["year"]

    # Journal — chỉ fill nếu None
    if crossref_data.get("journal") and doc.journal is None:
        updates["journal"] = crossref_data["journal"]

    # Keywords — merge, không duplicate
    if crossref_data.get("keywords"):
        existing = set(doc.keywords)
        new_kws  = [k for k in crossref_data["keywords"] if k not in existing]
        if new_kws:
            updates["keywords"] = doc.keywords + new_kws

    if crossref_data.get("publisher") and doc.publisher is None:
        updates["publisher"] = crossref_data["publisher"]
    if crossref_data.get("volume") and doc.volume is None:
        updates["volume"] = crossref_data["volume"]
    if crossref_data.get("issue") and doc.issue is None:
        updates["issue"] = crossref_data["issue"]
    if crossref_data.get("pages") and doc.pages is None:
        updates["pages"] = crossref_data["pages"]

    # Citation count — chỉ fill nếu None
    if crossref_data.get("citation_count") is not None and doc.citation_count is None:
        updates["citation_count"] = crossref_data["citation_count"]

    if not updates:
        logger.debug("No fields to update — document metadata already complete")
        return doc

    # dataclass replace — tạo instance mới, không mutate
    return replace(doc, **updates)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _title_similarity(a: str, b: str) -> float:
    """
    Tính độ giống nhau giữa 2 title bằng difflib SequenceMatcher.

    Returns:
        float trong [0.0, 1.0] — 1.0 = giống hoàn toàn
    """
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _pick_best_author(authors: list[str]) -> str:
    """
    Chọn author ít phổ biến nhất để dùng làm query parameter.
    Tránh họ phổ biến (Wang, Li, Smith...) vì noise cao trên Crossref.

    Returns:
        Author name phù hợp nhất để query, fallback về authors[0].
    """
    for author in authors:
        surname = author.split()[-1].lower()
        if surname not in COMMON_SURNAMES:
            return author
    return authors[0]  # fallback nếu tất cả đều phổ biến


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    GET request với error handling.

    Returns:
        Parsed JSON dict hoặc None nếu fail.
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests not installed. Run: pip install requests")

    try:
        response = requests.get(
            url,
            params  = params,
            timeout = REQUEST_TIMEOUT,
            headers = {
                "User-Agent": f"academic-rag-pipeline/1.0 (mailto:{MAILTO})",
                "Accept":     "application/json",
            },
        )

        if response.status_code == 404:
            logger.debug(f"Crossref 404: {url}")
            return None

        if response.status_code == 429:
            logger.warning("Crossref rate limit hit — consider increasing RATE_LIMIT_DELAY")
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.warning(f"Crossref request timed out: {url}")
        return None
    except requests.exceptions.ConnectionError:
        logger.warning("Crossref unreachable — check network connection")
        return None
    except Exception as e:
        logger.debug(f"Crossref request failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Quick self-test (run: python metadata_enricher.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # Test A: dry_run — không gọi API thực
    print("=" * 60)
    print("TEST A: dry_run mode")
    print("=" * 60)
    doc_a = UnifiedDocument(
        title   = "Attention Is All You Need",
        authors = ["Ashish Vaswani", "Noam Shazeer"],
        doi     = "10.48550/arXiv.1706.03762",
    )
    result_a = enrich_metadata(doc_a, dry_run=True)
    print(f"Title  : {result_a.title}")
    print(f"DOI    : {result_a.doi}")
    print()

    # Test B: lookup by DOI (live API)
    print("=" * 60)
    print("TEST B: enrich by DOI (live Crossref)")
    print("=" * 60)
    doc_b = UnifiedDocument(
        title = "Unknown",
        doi   = "10.1145/3292500.3330701",  # KDD 2019 paper
    )
    result_b = enrich_metadata(doc_b, mailto="test@example.com")
    print(f"Title          : {result_b.title}")
    print(f"Authors        : {result_b.authors[:3]}")
    print(f"Year           : {result_b.year}")
    print(f"Journal        : {result_b.journal}")
    print(f"DOI            : {result_b.doi}")
    print(f"Citation count : {result_b.citation_count}")
    print()

    # Test C: query by title (live API, no DOI)
    print("=" * 60)
    print("TEST C: enrich by title query (live Crossref)")
    print("=" * 60)
    doc_c = UnifiedDocument(
        title   = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        authors = ["Jacob Devlin", "Ming-Wei Chang"],
    )
    result_c = enrich_metadata(doc_c, mailto="test@example.com")
    print(f"Title          : {result_c.title}")
    print(f"DOI            : {result_c.doi}")
    print(f"Year           : {result_c.year}")
    print(f"Journal        : {result_c.journal}")
    print(f"Citation count : {result_c.citation_count}")
    print()

    # Test D: merge rule — không overwrite existing
    print("=" * 60)
    print("TEST D: merge rule — không overwrite existing data")
    print("=" * 60)
    doc_d = UnifiedDocument(
        title   = "My Custom Title",   # đã có → không overwrite
        authors = ["Author A"],         # đã có → không overwrite
        year    = 2020,                 # đã có → không overwrite
        doi     = "10.1145/3292500.3330701",
    )
    result_d = enrich_metadata(doc_d, mailto="test@example.com")
    assert result_d.title   == "My Custom Title", "Title should not be overwritten"
    assert result_d.authors == ["Author A"],       "Authors should not be overwritten"
    assert result_d.year    == 2020,               "Year should not be overwritten"
    print("✓ All merge rules correct — existing data preserved")
    print()

    # Test E: title similarity filter
    print("=" * 60)
    print("TEST E: _title_similarity helper")
    print("=" * 60)
    sim1 = _title_similarity(
        "BERT: Pre-training of Deep Bidirectional Transformers",
        "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    )
    sim2 = _title_similarity("Attention Is All You Need", "A Survey on Graph Neural Networks")
    print(f"Similar titles  : {sim1:.2f} (expect >= 0.8)")
    print(f"Different titles: {sim2:.2f} (expect < 0.8)")
    assert sim1 >= 0.8, "Should be similar"
    assert sim2 < 0.8,  "Should be different"
    print("✓ Title similarity working correctly")
    print()

    # Test F: _pick_best_author
    print("=" * 60)
    print("TEST F: _pick_best_author helper")
    print("=" * 60)
    authors_common  = ["Wei Wang", "Fei Li", "Jacob Devlin"]  # Devlin nên được chọn
    authors_all_com = ["Wei Wang", "Fei Li", "John Smith"]     # fallback về Wang
    picked1 = _pick_best_author(authors_common)
    picked2 = _pick_best_author(authors_all_com)
    print(f"Mixed list   → picked: '{picked1}' (expect: Jacob Devlin)")
    print(f"All common   → picked: '{picked2}' (expect: Wei Wang — fallback)")
    assert picked1 == "Jacob Devlin", "Should pick least common surname"
    assert picked2 == "Wei Wang",     "Should fallback to first author"
    print("✓ Author selection working correctly")
    print("=" * 60)
