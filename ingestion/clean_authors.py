"""
clean_authors.py
================
Post-processing script for UnifiedDocument JSON files.

Main jobs:
1. Clean document-level authors (remove symbols like *, trailing digits, extra spaces).
1.5 Normalize valid author names to uppercase.
2. Delete files whose cleaned author list is empty (unless --no-delete).
3. Ensure every reference has `internal_doc_id` key (keep existing, add null if missing).
4. Optionally parse/clean authors in references.

Usage examples:
  python ingestion/clean_authors.py data/processed/KHNN
  python ingestion/clean_authors.py data/processed/KHNN --dry-run
  python ingestion/clean_authors.py data/processed/KHNN --no-delete
  python ingestion/clean_authors.py data/processed/KHNN/CVv201S042025010.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Reference:
    raw_text: str
    doi: Optional[str] = None
    title: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    internal_doc_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "doi": self.doi,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "internal_doc_id": self.internal_doc_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Reference":
        authors = data.get("authors", [])
        if isinstance(authors, str):
            authors = [authors]
        elif not isinstance(authors, list):
            authors = []

        return cls(
            raw_text=data.get("raw_text", "") if isinstance(data.get("raw_text"), str) else "",
            doi=data.get("doi"),
            title=data.get("title"),
            authors=authors,
            year=data.get("year"),
            internal_doc_id=data.get("internal_doc_id"),
        )


_DIRTY_CHARS = re.compile(
    r"""
    [\*†‡§\#\^]      # common footnote symbols
    | \d+             # affiliation digits
    | ,\s*$           # trailing comma
    | ^\s*,           # leading comma
    """,
    re.VERBOSE,
)

_NOT_A_NAME = re.compile(
    r"""
    ^[\s\*\d\W]+$
    | ^(n/?a|none|unknown|anonymous|et\s+al\.?)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

_AUTHOR_NOISE = re.compile(
    r"""
    \bemail\b.*$
    | @
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BAD_AUTHOR_PATTERNS = re.compile(
    r"""
    ["\(\)\[\]\{\}:;/\\]
    | \b(cp|th[oô]n|x[aã]|huy[eệ]n|x[aä] h[oộ]i)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_VALID_TOKEN = re.compile(r"^[A-Za-zÀ-ỹ]+(?:[.'’-][A-Za-zÀ-ỹ]+)*$")
_MOJIBAKE_CHARS = {"Ã", "Â", "Ð", "Ñ", "�"}
_TOKEN_STRIP_CHARS = ".,;:!?\"'()[]{}<>|`~"


def _normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _normalize_author_case(text: str) -> str:
    return text.upper()


def _looks_like_person_name(text: str) -> bool:
    """
    Heuristic validator for person names.
    Accepts:
      - Western style with comma: "Smith, J. A."
      - VN/normal style: 2-6 tokens, mostly capitalized tokens.
    Rejects:
      - Long OCR phrases/sentences, admin/location fragments, odd punctuations.
    """
    if len(text) < 4 or len(text) > 70:
        return False
    if _BAD_AUTHOR_PATTERNS.search(text):
        return False

    # Western style: Lastname, I. I.
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return False
        lastname_tokens = parts[0].split()
        if not lastname_tokens or len(lastname_tokens) > 3:
            return False
        if any(not _VALID_TOKEN.match(t) for t in lastname_tokens):
            return False
        return bool(re.fullmatch(r"(?:[A-ZÀ-Ý]\.?)(?:\s+[A-ZÀ-Ý]\.?){0,4}", parts[1]))

    tokens = text.split()
    if len(tokens) < 2 or len(tokens) > 6:
        return False
    if any(not _VALID_TOKEN.match(t) for t in tokens):
        return False

    def _starts_with_upper(token: str) -> bool:
        for char in token:
            if char.isalpha():
                return char.isupper()
        return False

    upper_tokens = sum(1 for token in tokens if _starts_with_upper(token))
    # Person names typically have most tokens capitalized.
    if upper_tokens / len(tokens) < 0.8:
        return False

    # Reject OCR-like short all-caps fragments, e.g. "HNG MN".
    alpha_tokens = [t for t in tokens if any(ch.isalpha() for ch in t)]
    if alpha_tokens and all(t == t.upper() for t in alpha_tokens) and max(len(t) for t in alpha_tokens) <= 3:
        return False

    return True


def clean_single_author(raw: str) -> Optional[str]:
    if not isinstance(raw, str):
        return None

    text = _normalize_unicode(raw).strip()
    if not text:
        return None

    text = _DIRTY_CHARS.sub("", text).strip()
    text = _AUTHOR_NOISE.sub("", text).strip()
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(".,").strip()

    if not text:
        return None
    if _NOT_A_NAME.match(text):
        return None
    if not _HAS_LETTER.search(text):
        return None
    if not _looks_like_person_name(text):
        return None

    return _normalize_author_case(text)


def clean_authors_list(authors: list[str]) -> list[str]:
    if not isinstance(authors, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()

    for author in authors:
        value = clean_single_author(author)
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)

    return cleaned


def _is_initial_token(token: str) -> bool:
    return bool(re.match(r"^[A-ZÀ-Ý](\.\s?[A-ZÀ-Ý])*\.?$", token))


def _parse_apa_western(author_block: str) -> list[str]:
    block = re.sub(r",?\s*&\s*", ", ", author_block)
    tokens = [t.strip() for t in re.split(r",\s*", block) if t.strip()]

    authors: list[str] = []
    i = 0
    while i < len(tokens):
        lastname = tokens[i]
        initials: list[str] = []
        j = i + 1

        while j < len(tokens) and _is_initial_token(tokens[j]):
            token = tokens[j]
            if not token.endswith("."):
                token += "."
            initials.append(token)
            j += 1

        if initials:
            authors.append(f"{lastname}, {' '.join(initials)}")
            i = j
        else:
            authors.append(lastname)
            i += 1

    return authors


def parse_authors_from_raw(raw_text: str) -> list[str]:
    if not isinstance(raw_text, str):
        return []

    raw = raw_text.strip()
    if not raw:
        return []

    match = re.match(r"^(.*?)\s*\(\d{4}[a-z]?\)", raw)
    if not match:
        return []

    author_block = match.group(1).strip().rstrip(".")
    if not author_block:
        return []

    has_western = bool(re.search(r",\s+[A-Z]\.", author_block))
    if has_western:
        authors = _parse_apa_western(author_block)
    else:
        block = re.sub(r"\s*&\s*", ",", author_block)
        authors = [a.strip().rstrip(".").strip() for a in block.split(",") if a.strip()]

    return clean_authors_list(authors)


def has_valid_authors(authors: list[str]) -> bool:
    return bool(authors)


def _mojibake_char_ratio(text: str) -> float:
    if not isinstance(text, str):
        return 0.0
    compact = [ch for ch in text if not ch.isspace()]
    if not compact:
        return 0.0
    total = len(compact)
    bad = sum(1 for ch in compact if ch in _MOJIBAKE_CHARS)
    legacy_ratio = bad / total

    # In normal Vietnamese text, U+0100+ chars should be common.
    # Mojibake text often overuses Latin-1 supplement chars instead.
    latin1_count = sum(1 for ch in compact if 0x00C0 <= ord(ch) <= 0x00FF)
    extended_count = sum(1 for ch in compact if ord(ch) >= 0x0100)
    latin1_ratio = latin1_count / total
    extended_ratio = extended_count / total
    imbalance = max(0.0, latin1_ratio - extended_ratio)

    score = legacy_ratio + (2.2 * imbalance)
    return min(1.0, score)


def _is_noisy_token(token: str) -> bool:
    cleaned = token.strip(_TOKEN_STRIP_CHARS)
    if not cleaned:
        return False

    # Token chứa ký tự mojibake gần như chắc chắn là OCR/encoding lỗi.
    if any(ch in _MOJIBAKE_CHARS for ch in cleaned):
        return True

    latin1_count = sum(1 for ch in cleaned if 0x00C0 <= ord(ch) <= 0x00FF)
    if latin1_count >= 2:
        return True

    letters = sum(1 for ch in cleaned if ch.isalpha())
    digits = sum(1 for ch in cleaned if ch.isdigit())
    other = len(cleaned) - letters - digits

    if letters == 0:
        return False

    # Quá nhiều ký tự lạ trong token có chữ.
    if other / len(cleaned) > 0.35:
        return True

    # Token quá dài không có nguyên âm (hay gặp khi OCR lỗi).
    vowels = "aeiouyàáạảãăắằẳẵặâấầẩẫậèéẹẻẽêếềểễệìíịỉĩòóọỏõôốồổỗộơớờởỡợùúụủũưứừửữựỳýỵỷỹ"
    if len(cleaned) >= 8 and not any(ch.lower() in vowels for ch in cleaned if ch.isalpha()):
        return True

    return False


def _noisy_token_ratio(text: str) -> float:
    if not isinstance(text, str):
        return 0.0
    tokens = [tok for tok in re.split(r"\s+", text) if tok.strip()]
    if not tokens:
        return 0.0
    noisy = sum(1 for tok in tokens if _is_noisy_token(tok))
    return noisy / len(tokens)


def _ocr_quality_scores(text: str) -> tuple[float, float, float]:
    mojibake_ratio = _mojibake_char_ratio(text)
    noisy_ratio = _noisy_token_ratio(text)
    hybrid_score = 0.7 * mojibake_ratio + 0.3 * noisy_ratio
    return mojibake_ratio, noisy_ratio, hybrid_score


def process_file(
    path: Path,
    dry_run: bool = False,
    no_delete: bool = False,
    ocr_threshold: float = 0.35,
) -> str:
    try:
        # Use utf-8-sig to support both normal UTF-8 and UTF-8 files with BOM.
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
    except Exception as exc:
        logging.error("[ERROR] Cannot read %s: %s", path, exc)
        return "error"

    if not isinstance(data, dict):
        logging.error("[ERROR] JSON root must be object: %s", path)
        return "error"

    full_text = data.get("full_text", "")
    if isinstance(full_text, str) and full_text.strip():
        mojibake_ratio, noisy_ratio, hybrid_score = _ocr_quality_scores(full_text)
        if hybrid_score >= ocr_threshold:
            logging.warning(
                "[BAD OCR] %s -> hybrid=%.3f (mojibake=%.3f, noisy=%.3f, threshold=%.2f)",
                path.name,
                hybrid_score,
                mojibake_ratio,
                noisy_ratio,
                ocr_threshold,
            )

            if no_delete:
                logging.info("  -> Keep file (--no-delete)")
                return "ocr_bad_skipped"

            if dry_run:
                logging.info("  -> [DRY-RUN] Would delete %s", path.name)
            else:
                path.unlink()
                logging.info("  -> Deleted %s", path.name)
            return "ocr_bad_deleted"

    original_authors = data.get("authors", [])
    if isinstance(original_authors, str):
        original_authors = [original_authors]
    elif not isinstance(original_authors, list):
        original_authors = []

    cleaned_doc_authors = clean_authors_list(original_authors)

    if not has_valid_authors(cleaned_doc_authors):
        logging.warning("[NO AUTHOR] %s -> %r", path.name, original_authors)
        if no_delete:
            logging.info("  -> Keep file (--no-delete)")
            return "skipped"

        if dry_run:
            logging.info("  -> [DRY-RUN] Would delete %s", path.name)
        else:
            path.unlink()
            logging.info("  -> Deleted %s", path.name)
        return "deleted"

    data["authors"] = cleaned_doc_authors

    references_raw = data.get("references", [])
    if not isinstance(references_raw, list):
        references_raw = []

    cleaned_refs: list[dict] = []
    refs_parsed_count = 0
    refs_cleaned_count = 0
    refs_internal_id_added = 0

    for ref_dict in references_raw:
        if not isinstance(ref_dict, dict):
            continue

        ref = Reference.from_dict(ref_dict)

        original_ref_authors = list(ref.authors)
        ref.authors = clean_authors_list(ref.authors)
        if ref.authors != original_ref_authors:
            refs_cleaned_count += 1

        if not ref.authors and ref.raw_text:
            parsed = parse_authors_from_raw(ref.raw_text)
            if parsed:
                ref.authors = parsed
                refs_parsed_count += 1

        if "internal_doc_id" not in ref_dict:
            refs_internal_id_added += 1

        cleaned_refs.append(ref.to_dict())

    data["references"] = cleaned_refs

    refs_total = len(cleaned_refs)
    refs_with_authors = sum(1 for item in cleaned_refs if item.get("authors"))
    refs_still_empty = refs_total - refs_with_authors

    file_changed = cleaned_doc_authors != original_authors or cleaned_refs != references_raw
    if not file_changed:
        logging.debug("[SKIP] %s -> no changes", path.name)
        return "skipped"

    if dry_run:
        logging.info(
            "[DRY-RUN] %s\n"
            "  doc authors : %r -> %r\n"
            "  references  : total=%d | parsed=%d | cleaned=%d | empty=%d | add_internal_doc_id=%d",
            path.name,
            original_authors,
            cleaned_doc_authors,
            refs_total,
            refs_parsed_count,
            refs_cleaned_count,
            refs_still_empty,
            refs_internal_id_added,
        )
        return "cleaned"

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    logging.info(
        "[OK] %s\n"
        "  doc authors : %r -> %r\n"
        "  references  : total=%d | parsed=%d | cleaned=%d | empty=%d | add_internal_doc_id=%d",
        path.name,
        original_authors,
        cleaned_doc_authors,
        refs_total,
        refs_parsed_count,
        refs_cleaned_count,
        refs_still_empty,
        refs_internal_id_added,
    )
    return "cleaned"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean author fields and reference internal_doc_id in UnifiedDocument JSON files"
    )
    parser.add_argument("target", help="Path to one JSON file or one directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing/deleting")
    parser.add_argument("--no-delete", action="store_true", help="Do not delete files with invalid authors")
    parser.add_argument(
        "--ocr-threshold",
        type=float,
        default=0.35,
        help="Delete/flag file when OCR hybrid quality score >= threshold (default: 0.35)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    target = Path(args.target)
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(target.rglob("*.json"))
    else:
        logging.error("Path not found: %s", target)
        return

    stats = {
        "cleaned": 0,
        "deleted": 0,
        "skipped": 0,
        "error": 0,
        "ocr_bad_deleted": 0,
        "ocr_bad_skipped": 0,
    }

    for file_path in files:
        result = process_file(
            file_path,
            dry_run=args.dry_run,
            no_delete=args.no_delete,
            ocr_threshold=args.ocr_threshold,
        )
        stats[result] = stats.get(result, 0) + 1

    print("\n" + "=" * 56)
    print(f"Total files   : {len(files)}")
    print(f"Cleaned       : {stats['cleaned']}")
    print(f"Deleted       : {stats['deleted']}")
    print(f"Skipped       : {stats['skipped']}")
    print(f"OCR Deleted   : {stats['ocr_bad_deleted']}")
    print(f"OCR Skipped   : {stats['ocr_bad_skipped']}")
    print(f"Errors        : {stats['error']}")
    if args.dry_run:
        print("Mode          : DRY-RUN (no files were changed)")
    print("=" * 56)


if __name__ == "__main__":
    main()
