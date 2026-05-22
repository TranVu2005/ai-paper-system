from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple


_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_PROMPT_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _read_prompt_sections(filename: str) -> Dict[str, str]:
    path = _PROMPT_DIR / filename
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _PROMPT_CACHE[filename] = (0.0, {})
        return {}

    cached = _PROMPT_CACHE.get(filename)
    if cached is not None:
        cached_mtime, cached_sections = cached
        if cached_mtime == mtime:
            return cached_sections

    sections: Dict[str, str] = {}
    current_key = ""
    buf = []

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        _PROMPT_CACHE[filename] = (0.0, {})
        return {}

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("[[") and stripped.endswith("]]") and len(stripped) > 4:
            if current_key:
                sections[current_key] = "\n".join(buf).strip()
            current_key = stripped[2:-2].strip().upper()
            buf = []
            continue
        if current_key:
            buf.append(line)

    if current_key:
        sections[current_key] = "\n".join(buf).strip()

    _PROMPT_CACHE[filename] = (mtime, sections)
    return sections


def _render(template: str, **kwargs: object) -> str:
    return template.format_map(_SafeDict(**kwargs)).strip()


def _prompt_from_file(filename: str, section: str, fallback: str, **kwargs: object) -> str:
    sections = _read_prompt_sections(filename)
    template = sections.get(section.upper(), "").strip() or fallback
    return _render(template, **kwargs)


def higen_highlight_prompt(document: str, num_highlights: int) -> str:
    fallback = (
        "You are an expert at identifying key information in scientific papers.\n"
        "Given the following document:\n{document}\n"
        "Extract {num_highlights} important sentences that represent the core content of the paper.\n"
        "Focus on:\n"
        "- research problem\n"
        "- proposed method\n"
        "- model architecture\n"
        "- dataset\n"
        "- experiment setup\n"
        "- results\n"
        "- limitations\n"
        "Output only a numbered list of key sentences."
    )
    return _prompt_from_file(
        "summary_prompt.txt",
        "HIGEN_HIGHLIGHT",
        fallback,
        document=document,
        num_highlights=num_highlights,
    )


def higen_summary_prompt(highlights: str) -> str:
    fallback = (
        "You are an expert scientific paper summarizer.\n"
        "Given the following key sentences:\n{highlights}\n"
        "Write a clear and structured Vietnamese summary.\n"
        "The summary must only use information from the key sentences.\n"
        "Do not add new information.\n"
        "Use this structure:\n"
        "1. Vấn đề nghiên cứu\n"
        "2. Phương pháp đề xuất\n"
        "3. Cách triển khai\n"
        "4. Kết quả\n"
        "5. Kết luận\n"
        "Summary:"
    )
    return _prompt_from_file("summary_prompt.txt", "HIGEN_SUMMARY", fallback, highlights=highlights)


def higen_summary_prompt_by_style(highlights: str, style: str = "academic") -> str:
    st = (style or "academic").strip().lower()
    if st == "semantic":
        fallback = (
            "You are a semantic scientific summarizer.\n"
            "Given key sentences:\n{highlights}\n"
            "Write Vietnamese summary focusing on meaning and logical relations.\n"
            "Use this structure:\n"
            "1. Y chinh\n"
            "2. Quan he nghia (nguyen nhan, doi chieu, he qua)\n"
            "3. Ham y ung dung\n"
            "Strict output rules:\n"
            "- Output only final answer.\n"
            "- Do NOT output Thinking Process, Analysis, Reasoning, or Explanation.\n"
            "- Do NOT add text before or after the answer."
        )
        return _prompt_from_file(
            "summary_prompt.txt",
            "HIGEN_SUMMARY_SEMANTIC",
            fallback,
            highlights=highlights,
        )

    if st == "executive":
        fallback = (
            "You are an executive-level scientific summarizer.\n"
            "Given key sentences:\n{highlights}\n"
            "Write concise Vietnamese summary for decision-makers.\n"
            "Use this structure:\n"
            "1. Ket luan chinh (toi da 3 gach dau dong)\n"
            "2. Bang chung\n"
            "3. Rui ro / han che\n"
            "4. Khuyen nghi hanh dong\n"
            "Strict output rules:\n"
            "- Output only final answer.\n"
            "- Do NOT output Thinking Process, Analysis, Reasoning, or Explanation.\n"
            "- Do NOT add text before or after the answer."
        )
        return _prompt_from_file(
            "summary_prompt.txt",
            "HIGEN_SUMMARY_EXECUTIVE",
            fallback,
            highlights=highlights,
        )

    # default + academic + unknown style
    fallback = (
        "You are an expert scientific paper summarizer.\n"
        "Given the following key sentences:\n{highlights}\n"
        "Write a clear and structured Vietnamese summary.\n"
        "The summary must only use information from the key sentences.\n"
        "Do not add new information.\n"
        "Use this structure:\n"
        "1. Van de nghien cuu\n"
        "2. Phuong phap de xuat\n"
        "3. Cach trien khai\n"
        "4. Ket qua\n"
        "5. Ket luan\n"
        "Strict output rules:\n"
        "- Output only final answer.\n"
        "- Do NOT output Thinking Process, Analysis, Reasoning, or Explanation.\n"
        "- Do NOT add text before or after the answer."
    )
    return _prompt_from_file(
        "summary_prompt.txt",
        "HIGEN_SUMMARY_ACADEMIC",
        fallback,
        highlights=highlights,
    )


def chunk_summary_prompt(chunk: str) -> str:
    fallback = (
        "You are an expert scientific summarizer.\n"
        "Summarize the following text chunk in Vietnamese.\n"
        "Focus on key facts, method, experiment, and result.\n"
        "Text chunk:\n{chunk}\n"
        "Chunk summary:"
    )
    return _prompt_from_file("summary_prompt.txt", "CHUNK_SUMMARY", fallback, chunk=chunk)


def document_summary_prompt(chunk_summaries: str) -> str:
    fallback = (
        "You are an expert at synthesizing scientific content.\n"
        "Given these chunk summaries from one paper:\n{chunk_summaries}\n"
        "Combine them into a structured Vietnamese summary:\n"
        "1. Vấn đề\n"
        "2. Phương pháp\n"
        "3. Dữ liệu / thực nghiệm\n"
        "4. Kết quả\n"
        "5. Kết luận\n"
        "Remove redundancy and preserve important technical details."
    )
    return _prompt_from_file(
        "summary_prompt.txt",
        "DOCUMENT_SUMMARY",
        fallback,
        chunk_summaries=chunk_summaries,
    )


def collection_summary_prompt(document_summaries: str) -> str:
    fallback = (
        "You are an expert in multi-document scientific summarization.\n"
        "Given the following summaries of multiple papers:\n{document_summaries}\n"
        "Write a final Vietnamese synthesis that:\n"
        "- groups papers by similar ideas\n"
        "- compares their methods\n"
        "- identifies common problems\n"
        "- highlights differences\n"
        "- suggests how these methods can be combined in one project\n"
        "Final summary:"
    )
    return _prompt_from_file(
        "summary_prompt.txt",
        "COLLECTION_SUMMARY",
        fallback,
        document_summaries=document_summaries,
    )


def rag_prompt(question: str, context: str) -> str:
    fallback = (
        "You are a RAG assistant for scientific papers.\n"
        "Answer the user question using only the retrieved context.\n"
        "If the answer is not in the context, say: 'Không tìm thấy thông tin trong tài liệu.'\n"
        "Do not hallucinate.\n"
        "Return the answer in Vietnamese.\n\n"
        "Question:\n{question}\n\n"
        "Retrieved context:\n{context}\n\n"
        "Answer format:\n"
        "Trả lời:\n...\n\n"
        "Nguồn:\n"
        "- file: ..., page: ..., chunk: ..."
    )
    return _prompt_from_file("rag_prompt.txt", "RAG", fallback, question=question, context=context)


def event_extraction_prompt(context: str) -> str:
    fallback = (
        "You are an information extraction system.\n"
        "Extract important research events from the following scientific context.\n"
        "Return valid JSON only.\n"
        "Context:\n{context}"
    )
    return _prompt_from_file("rag_prompt.txt", "EVENT_EXTRACTION", fallback, context=context)


def rag_with_events_prompt(question: str, context: str, events: str) -> str:
    fallback = (
        "You are a scientific RAG assistant.\n"
        "Use both the retrieved context and the extracted events to answer the question.\n"
        "Do not invent facts.\n\n"
        "Question:\n{question}\n\n"
        "Retrieved context:\n{context}\n\n"
        "Extracted events:\n{events}\n\n"
        "Answer in Vietnamese. Include sources at the end."
    )
    return _prompt_from_file(
        "rag_prompt.txt",
        "RAG_WITH_EVENTS",
        fallback,
        question=question,
        context=context,
        events=events,
    )
