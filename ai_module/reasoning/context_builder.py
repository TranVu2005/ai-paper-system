from __future__ import annotations

from typing import Any, Dict, List



def format_retrieved_context(results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, r in enumerate(results, start=1):
        lines.append(
            f"[{i}] file={r.get('file')} page={r.get('page')} chunk={r.get('chunk_id')} score={r.get('score')}\n{r.get('text', '')}"
        )
    return "\n\n".join(lines)



def compact_sources(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "file": r.get("file"),
            "page": r.get("page"),
            "chunk_id": r.get("chunk_id"),
            "score": r.get("score"),
        }
        for r in results
    ]
