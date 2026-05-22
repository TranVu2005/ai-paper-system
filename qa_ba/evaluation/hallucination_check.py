from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


_TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ỹ]+", re.UNICODE)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SENT_SPLIT_RE = re.compile(r"[.!?;\n]+")


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def _tokenize(text: str) -> list[str]:
    norm = _normalize(text)
    return [t for t in _TOKEN_RE.findall(norm) if len(t) >= 3]


def _split_sentences(text: str) -> list[str]:
    parts = [_normalize(p) for p in _SENT_SPLIT_RE.split(text or "")]
    return [p for p in parts if p]


def hallucination_score(answer: str, context: str) -> float:
    """
    Sentence-level hallucination score in [0, 1].
    0.0 means every answer sentence is supported by context.
    1.0 means every answer sentence is unsupported.
    """
    answer_sents = _split_sentences(answer)
    if not answer_sents:
        return 0.0
    norm_context = _normalize(context or "")
    unsupported = 0
    for sent in answer_sents:
        if sent and sent not in norm_context:
            unsupported += 1
    return unsupported / max(len(answer_sents), 1)


@dataclass
class HallucinationReport:
    groundedness_score: float
    hallucination_score: float
    unsupported_numbers: list[str]
    answer_tokens: int
    evidence_tokens: int

    @property
    def score(self) -> float:
        # Backward compatibility: old code expects `score` as groundedness.
        return self.groundedness_score

    def as_dict(self) -> dict:
        return {
            "groundedness_score": round(self.groundedness_score, 4),
            "hallucination_score": round(self.hallucination_score, 4),
            "support_ratio": round(self.groundedness_score, 4),
            "unsupported_numbers": self.unsupported_numbers,
            "unsupported_numbers_count": len(self.unsupported_numbers),
            "answer_tokens": self.answer_tokens,
            "evidence_tokens": self.evidence_tokens,
            "score": round(self.groundedness_score, 4),
        }


def check_hallucination(answer: str, evidence_texts: Iterable[str]) -> HallucinationReport:
    answer = answer or ""
    evidence_text = " ".join([str(t or "") for t in evidence_texts])

    answer_tokens = set(_tokenize(answer))
    evidence_tokens = set(_tokenize(evidence_text))
    hallu_score = hallucination_score(answer, evidence_text)

    if not answer_tokens:
        return HallucinationReport(
            groundedness_score=0.0,
            hallucination_score=hallu_score,
            unsupported_numbers=[],
            answer_tokens=0,
            evidence_tokens=len(evidence_tokens),
        )

    supported = answer_tokens.intersection(evidence_tokens)
    support_ratio = len(supported) / max(1, len(answer_tokens))

    answer_nums = set(_NUM_RE.findall(_normalize(answer)))
    evidence_nums = set(_NUM_RE.findall(_normalize(evidence_text)))
    unsupported_numbers = sorted([n for n in answer_nums if n not in evidence_nums])

    return HallucinationReport(
        groundedness_score=max(0.0, min(1.0, support_ratio)),
        hallucination_score=max(0.0, min(1.0, hallu_score)),
        unsupported_numbers=unsupported_numbers,
        answer_tokens=len(answer_tokens),
        evidence_tokens=len(evidence_tokens),
    )
