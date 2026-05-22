from __future__ import annotations

import re
import unicodedata
from typing import Dict, List

SECTIONS = [
    "Vấn đề nghiên cứu",
    "Phương pháp đề xuất",
    "Cách triển khai",
    "Kết quả",
    "Kết luận",
]

SECTION_HINTS = {
    "Vấn đề nghiên cứu": ["vấn đề", "van de", "mục tiêu", "muc tieu", "problem"],
    "Phương pháp đề xuất": ["phương pháp", "phuong phap", "đề xuất", "de xuat", "method"],
    "Cách triển khai": ["cách triển khai", "cach trien khai", "triển khai", "trien khai", "implementation"],
    "Kết quả": ["kết quả", "ket qua", "result", "evaluation"],
    "Kết luận": ["kết luận", "ket luan", "tổng kết", "tong ket", "conclusion"],
}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(text: str) -> str:
    text = _strip_accents(text or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _section_by_text(line: str) -> str | None:
    nline = _norm(line)
    for sec in SECTIONS:
        for hint in SECTION_HINTS[sec]:
            if hint in nline:
                return sec
    return None


def _section_by_number(line: str) -> str | None:
    m = re.match(r"^\s*(\d+)\s*[\.)]", line)
    if not m:
        return None
    idx = int(m.group(1)) - 1
    if 0 <= idx < len(SECTIONS):
        return SECTIONS[idx]
    return None


def to_structured_summary(summary_text: str) -> Dict[str, str]:
    lines = [x.strip() for x in (summary_text or "").splitlines() if x.strip()]
    out: Dict[str, List[str]] = {k: [] for k in SECTIONS}
    cur = SECTIONS[0]

    for line in lines:
        found_section = _section_by_number(line) or _section_by_text(line)
        if found_section is not None:
            cur = found_section
            normalized = re.sub(r"^\s*\d+\s*[\.)]\s*", "", line).strip()
            after = normalized.split(":", 1)[1].strip() if ":" in normalized else ""
            if after:
                out[cur].append(after)
            continue

        out[cur].append(line)

    return {k: "\n".join(v).strip() for k, v in out.items()}
