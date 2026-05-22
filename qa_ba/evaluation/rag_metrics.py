from __future__ import annotations

import os
from dataclasses import dataclass

from qa_ba.evaluation.hallucination_check import check_hallucination


@dataclass
class RagThresholds:
    min_total_score_pass: float = float(os.getenv("RAG_MIN_TOTAL_SCORE_PASS", "0.75"))
    min_total_score_warn: float = float(os.getenv("RAG_MIN_TOTAL_SCORE_WARN", "0.55"))
    max_hallucination_pass: float = float(os.getenv("RAG_MAX_HALLUCINATION_PASS", "0.20"))


def recall_at_k(retrieved_docs: list[str], ground_truth_docs: list[str]) -> int:
    return int(any(doc in retrieved_docs for doc in ground_truth_docs))


def _source_precision(hits: list[dict], ground_truth_docs: list[str]) -> float:
    if not hits:
        return 0.0
    if not ground_truth_docs:
        return 1.0
    gt = {str(x).strip() for x in ground_truth_docs if str(x).strip()}
    if not gt:
        return 1.0
    matched = sum(1 for h in hits if str(h.get("file", "")).strip() in gt)
    return matched / len(hits)


def evaluate_rag_case(
    *,
    answer: str,
    min_answer_chars: int,
    hits: list[dict],
    ground_truth_docs: list[str],
    evidence_texts: list[str],
    thresholds: RagThresholds | None = None,
) -> dict:
    thresholds = thresholds or RagThresholds()

    retrieved_docs = [str(h.get("file", "")).strip() for h in hits if str(h.get("file", "")).strip()]
    recall = recall_at_k(retrieved_docs, ground_truth_docs)
    source_precision = _source_precision(hits, ground_truth_docs)

    hallucination = check_hallucination(answer, evidence_texts)
    groundedness = hallucination.groundedness_score
    hallucination_value = hallucination.hallucination_score

    answer_len = len((answer or "").strip())
    length_score = min(1.0, answer_len / max(1, min_answer_chars))

    # RAG-first weighting: retrieval hit + grounded generation quality.
    total_score = (
        0.40 * recall
        + 0.25 * groundedness
        + 0.20 * source_precision
        + 0.15 * length_score
    )

    gates = {
        "answer_len_ok": answer_len >= min_answer_chars,
        "recall_at_k_ok": recall == 1,
        "source_precision_ok": source_precision > 0.0,
        "groundedness_ok": groundedness >= 0.35,
        "hallucination_ok": hallucination_value <= thresholds.max_hallucination_pass,
    }

    if total_score >= thresholds.min_total_score_pass and hallucination_value <= thresholds.max_hallucination_pass:
        verdict = "PASS"
    elif total_score >= thresholds.min_total_score_warn:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return {
        "verdict": verdict,
        "total_score": round(total_score, 4),
        "recall_at_k": int(recall),
        "source_precision": round(source_precision, 4),
        "groundedness": round(groundedness, 4),
        "hallucination_score": round(hallucination_value, 4),
        "length_score": round(length_score, 4),
        "answer_len": answer_len,
        "gates": gates,
        "hallucination": hallucination.as_dict(),
        "retrieved_docs": retrieved_docs,
        "ground_truth_docs": [str(x).strip() for x in ground_truth_docs if str(x).strip()],
    }
