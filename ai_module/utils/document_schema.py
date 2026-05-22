from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def resolve_document_id(doc: Dict[str, Any], fallback: str = "unknown") -> str:
    for key in ("file", "doc_id", "id", "source"):
        val = _safe_text(doc.get(key))
        if val:
            return val
    return fallback


def _join_non_empty(parts: Iterable[str], sep: str = "\n") -> str:
    out: List[str] = []
    for p in parts:
        t = _safe_text(p)
        if t:
            out.append(t)
    return sep.join(out)


def _build_front_matter_chunk(doc: Dict[str, Any], file_id: str) -> Dict[str, Any] | None:
    title = _safe_text(doc.get("title"))
    abstract = _safe_text(doc.get("abstract"))
    authors_raw = doc.get("authors") if isinstance(doc.get("authors"), list) else []
    authors = ", ".join([_safe_text(a) for a in authors_raw if _safe_text(a)])
    keywords = doc.get("keywords") if isinstance(doc.get("keywords"), list) else []
    year = _safe_text(doc.get("year"))
    journal = _safe_text(doc.get("journal"))
    doi = _safe_text(doc.get("doi"))

    kw_text = ", ".join([_safe_text(x) for x in keywords if _safe_text(x)])
    text = _join_non_empty(
        [
            f"Title: {title}" if title else "",
            f"Authors: {authors}" if authors else "",
            f"Year: {year}" if year else "",
            f"Journal: {journal}" if journal else "",
            f"DOI: {doi}" if doi else "",
            f"Keywords: {kw_text}" if kw_text else "",
            f"Abstract: {abstract}" if abstract else "",
        ],
        sep="\n",
    ).strip()

    if not text:
        return None

    return {"file": file_id, "page": None, "chunk_id": "front_matter", "text": text}


def _build_metadata_chunk(doc: Dict[str, Any], file_id: str) -> Dict[str, Any] | None:
    lines = [
        f"Doc ID: {_safe_text(doc.get('doc_id'))}",
        f"Source File: {_safe_text(doc.get('source_file'))}",
        f"Source Type: {_safe_text(doc.get('source_type'))}",
        f"Language: {_safe_text(doc.get('language'))}",
        f"Publisher: {_safe_text(doc.get('publisher'))}",
        f"Volume: {_safe_text(doc.get('volume'))}",
        f"Issue: {_safe_text(doc.get('issue'))}",
        f"Pages: {_safe_text(doc.get('pages'))}",
        f"Page Count: {_safe_text(doc.get('page_count'))}",
        f"Citation Count: {_safe_text(doc.get('citation_count'))}",
        f"Processing Status: {_safe_text(doc.get('processing_status'))}",
        f"Created At: {_safe_text(doc.get('created_at'))}",
    ]
    text = _join_non_empty(lines, sep="\n")
    if not text:
        return None
    return {"file": file_id, "page": None, "chunk_id": "metadata", "text": text}


def _build_references_chunk(doc: Dict[str, Any], file_id: str, limit: int = 30) -> Dict[str, Any] | None:
    refs = doc.get("references")
    if not isinstance(refs, list) or not refs:
        return None

    lines: List[str] = []
    for i, r in enumerate(refs[:limit], start=1):
        if isinstance(r, dict):
            raw = _safe_text(r.get("raw_text"))
            title = _safe_text(r.get("title"))
            year = _safe_text(r.get("year"))
            doi = _safe_text(r.get("doi"))
            item = raw or title
            suffix = _join_non_empty(
                [
                    f"year={year}" if year else "",
                    f"doi={doi}" if doi else "",
                ],
                sep=", ",
            )
            if item:
                lines.append(f"[{i}] {item}" + (f" ({suffix})" if suffix else ""))
        else:
            t = _safe_text(r)
            if t:
                lines.append(f"[{i}] {t}")

    if not lines:
        return None
    text = "References:\n" + "\n".join(lines)
    return {"file": file_id, "page": None, "chunk_id": "references", "text": text}


def _build_full_text_chunk(doc: Dict[str, Any], file_id: str, max_chars: int = 12000) -> Dict[str, Any] | None:
    full_text = _safe_text(doc.get("full_text"))
    if not full_text:
        return None
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars]
    return {"file": file_id, "page": None, "chunk_id": "full_text_overview", "text": full_text}


def extract_document_text(doc: Dict[str, Any]) -> str:
    # Native schema used by current RAG pipeline.
    text = _safe_text(doc.get("text"))
    if text:
        return text

    # Processed schema in data/processed/*.json.
    full_text = _safe_text(doc.get("full_text"))
    if full_text:
        return full_text

    # Fallback to section content if full_text is unavailable.
    sections = doc.get("sections")
    if isinstance(sections, list) and sections:
        section_text = _join_non_empty([_safe_text((s or {}).get("content")) for s in sections], sep="\n\n")
        if section_text:
            return section_text

    # Last fallback keeps at least semantic signal for retrieval/summarization.
    title = _safe_text(doc.get("title"))
    abstract = _safe_text(doc.get("abstract"))
    keywords = doc.get("keywords")
    kw_text = ""
    if isinstance(keywords, list):
        kw_text = ", ".join([_safe_text(x) for x in keywords if _safe_text(x)])
    return _join_non_empty([title, abstract, kw_text], sep="\n")


def extract_document_chunks(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Section-first chunking with title signal.
    - Add 1 title chunk if present.
    - Then use doc["sections"] (one section -> one chunk).
    """
    file_id = resolve_document_id(doc)
    out: List[Dict[str, Any]] = []

    title = _safe_text(doc.get("title"))
    if title:
        out.append(
            {
                "file": file_id,
                "page": None,
                "chunk_id": "title",
                "text": title,
            }
        )

    sections = doc.get("sections")
    if isinstance(sections, list) and sections:
        for i, s in enumerate(sections):
            if not isinstance(s, dict):
                continue
            content = _safe_text(s.get("content")) or _safe_text(s.get("text")) or _safe_text(s.get("body"))
            if not content:
                continue
            name = _safe_text(s.get("name"))
            text = f"{name}\n{content}" if name else content
            out.append(
                {
                    "file": file_id,
                    "page": s.get("page"),
                    "chunk_id": s.get("order", i),
                    "text": text,
                }
            )
        if out:
            return out

    # Fallback signal if section payloads are missing text fields.
    abstract = _safe_text(doc.get("abstract"))
    if abstract:
        out.append(
            {
                "file": file_id,
                "page": None,
                "chunk_id": "abstract",
                "text": abstract,
            }
        )
        return out
    return out
