from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_module.inference.inference_config import InferenceConfig
from ai_module.inference.llm_engine import LLMEngine
from ai_module.reasoning.context_builder import format_retrieved_context
from ai_module.reasoning.prompt_builder import rag_prompt
from ai_module.rerank.bge_reranker import BGEReranker
from ai_module.retrieval.graph_retriever import GraphRetriever, GraphRetrieverConfig
from ai_module.retrieval.vector_retriever import VectorRetriever
from ai_module.utils.document_schema import extract_document_chunks

if TYPE_CHECKING:
    from storage.graph_db.neo4j_client import Neo4jClient


DEFAULT_DATA_ROOT = REPO_ROOT / "data_test"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "qa_ba" / "evaluation" / "rag_qa_questions_data_test.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "qa_ba" / "evaluation" / "outputs"
DEFAULT_OUTPUT_NAME = "rag_qa_benchmark_results.json"

QUESTION_TYPES = ("factual", "multi_hop", "unanswerable")
SYSTEM_DENSE = "baseline_dense"
SYSTEM_SPARSE = "baseline_sparse_bm25"
SYSTEM_PROPOSED = "proposed_rag_kg_hybrid"
SYSTEM_LABELS = {
    SYSTEM_DENSE: "Baseline A: Dense Retrieval",
    SYSTEM_SPARSE: "Baseline B: Sparse Retrieval (BM25)",
    SYSTEM_PROPOSED: "Proposed: Dense + Knowledge Graph (Hybrid Fusion)",
}


@dataclass
class QuestionCase:
    id: str
    question: str
    question_type: str
    module: str
    file_name: str
    file_path: str = ""
    gold_answer: str = ""
    gold_sources: List[str] | None = None
    seed_paper_id: str = ""


@dataclass
class ParsedRef:
    raw: str
    file: str = ""
    page: str = ""
    chunk: str = ""


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _norm_text(value: object) -> str:
    text = _safe_text(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[0-9a-zA-ZÀ-ỹ]+", _norm_text(text))


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    if q <= 0:
        return min(values)
    if q >= 100:
        return max(values)
    data = sorted(values)
    rank = (len(data) - 1) * (q / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(data[lo])
    w = rank - lo
    return float(data[lo] * (1.0 - w) + data[hi] * w)


def _estimate_tokens(text: str, llm: LLMEngine | None) -> int:
    content = _safe_text(text)
    if not content:
        return 0
    if llm is not None and getattr(llm, "tokenizer", None) is not None:
        try:
            return int(len(llm.tokenizer.encode(content, add_special_tokens=False)))
        except Exception:
            pass
    return len(re.findall(r"\S+", content))


def _extract_answer_body(answer: str) -> str:
    text = _safe_text(answer)
    if not text:
        return ""
    low = text.lower()
    m = re.search(r"(?:\bngu\w*n\b|\bsource\b)\s*:", low)
    if m:
        text = text[: m.start()]
    text = re.sub(r"(?im)^\s*tra\s*loi\s*:\s*", "", text).strip()
    return text


def _answerability_class(answer: str) -> str:
    body = _extract_answer_body(answer)
    if not body:
        return "empty"
    refusal_patterns = [
        r"\bkhông\s+(thể|co)\s+trả\s+lời\b",
        r"\bkhông\s+đủ\s+thông\s+tin\b",
        r"\bkhông\s+tìm\s+thấy\b",
        r"\bkhông\s+có\s+thông\s+tin\b",
        r"\bchưa\s+có\s+dữ\s+liệu\b",
        r"\bcannot\s+answer\b",
        r"\binsufficient\s+information\b",
    ]
    low = body.lower()
    if any(re.search(p, low) for p in refusal_patterns):
        return "not_answered"
    return "answered"


def _parse_k_list(raw: str) -> List[int]:
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        k = int(part)
        if k <= 0:
            raise ValueError("All k values must be > 0")
        out.append(k)
    if not out:
        raise ValueError("k list cannot be empty")
    return sorted(set(out))


def _load_questions(
    path: Path,
    modules: set[str],
    question_types: set[str],
    limit: int,
) -> List[QuestionCase]:
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")

    rows: List[QuestionCase] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        module = _safe_text(obj.get("module") or obj.get("module_key"))
        qtype = _safe_text(obj.get("question_type"))
        if modules and module not in modules:
            continue
        if question_types and qtype not in question_types:
            continue
        if qtype and qtype not in QUESTION_TYPES:
            # keep compatibility with custom sets; skip unexpected type by default
            continue

        q = QuestionCase(
            id=_safe_text(obj.get("id")) or f"{module}::{_safe_text(obj.get('file_name'))}::{qtype}",
            question=_safe_text(obj.get("question")),
            question_type=qtype,
            module=module,
            file_name=_safe_text(obj.get("file_name")),
            file_path=_safe_text(obj.get("file_path")),
            gold_answer=_safe_text(obj.get("gold_answer")),
            gold_sources=obj.get("gold_sources") if isinstance(obj.get("gold_sources"), list) else [],
            seed_paper_id=_safe_text(obj.get("seed_paper_id")),
        )
        if not q.question:
            continue
        rows.append(q)
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def _discover_docs(data_root: Path) -> Tuple[Dict[Tuple[str, str], Path], Dict[str, Path]]:
    by_module_file: Dict[Tuple[str, str], Path] = {}
    by_file_name: Dict[str, Path] = {}
    if not data_root.exists():
        return by_module_file, by_file_name

    for module_dir in sorted([p for p in data_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        for fp in sorted(module_dir.glob("*.json"), key=lambda p: p.name):
            by_module_file[(module_dir.name, fp.name)] = fp
            by_file_name.setdefault(fp.name, fp)
    return by_module_file, by_file_name


def _resolve_doc_path(
    q: QuestionCase,
    by_module_file: Dict[Tuple[str, str], Path],
    by_file_name: Dict[str, Path],
) -> Path | None:
    if q.file_path:
        fp = Path(q.file_path)
        if fp.exists():
            return fp
    if q.module and q.file_name:
        fp = by_module_file.get((q.module, q.file_name))
        if fp is not None:
            return fp
    if q.file_name:
        return by_file_name.get(q.file_name)
    return None


def _normalize_file_for_match(value: object) -> str:
    f = _norm_text(value)
    if f.startswith("paper::"):
        f = f.split("paper::", 1)[1].strip()
    if f and not f.endswith(".json") and re.fullmatch(r"[0-9a-zA-Z_\-]+", f):
        f = f + ".json"
    return f


def _hit_identity(hit: Dict[str, Any]) -> Dict[str, str]:
    return {
        "file": _normalize_file_for_match(hit.get("file")),
        "page": _norm_text(hit.get("page")),
        "chunk": _norm_text(hit.get("chunk_id")),
        "text": _norm_text(hit.get("text")),
    }


def _parse_reference(raw: str) -> ParsedRef:
    text = _safe_text(raw)
    out = ParsedRef(raw=text)
    if not text:
        return out

    low = _norm_text(text)
    m_file = re.search(r"file\s*[:=]\s*([^,|;\n]+)", low)
    m_page = re.search(r"page\s*[:=]\s*([^,|;\n]+)", low)
    m_chunk = re.search(r"chunk(?:_id)?\s*[:=]\s*([^,|;\n]+)", low)
    if m_file:
        out.file = _norm_text(m_file.group(1))
    if m_page:
        out.page = _norm_text(m_page.group(1))
    if m_chunk:
        out.chunk = _norm_text(m_chunk.group(1))

    if not out.file and not out.chunk:
        # fallback patterns: file|page|chunk or file/chunk
        parts = [p.strip() for p in re.split(r"[|,;/]", low) if p.strip()]
        if len(parts) >= 3:
            out.file, out.page, out.chunk = parts[0], parts[1], parts[2]
        elif len(parts) >= 2:
            out.file, out.chunk = parts[0], parts[1]

    # normalize common prefixes/sentinels
    out.file = re.sub(r"^\s*file\s*[:=]\s*", "", out.file).strip()
    out.page = re.sub(r"^\s*page\s*[:=]\s*", "", out.page).strip()
    out.chunk = re.sub(r"^\s*chunk(?:_id)?\s*[:=]\s*", "", out.chunk).strip()
    if out.file.startswith("paper::"):
        out.file = out.file.split("paper::", 1)[1].strip()
    if out.file and not out.file.endswith(".json") and re.fullmatch(r"[0-9a-zA-Z_\-]+", out.file):
        out.file = f"{out.file}.json"
    if out.page in {"none", "null", "na", "n/a", "-", ""}:
        out.page = ""
    if out.chunk in {"none", "null", "na", "n/a", "-", ""}:
        out.chunk = ""
    return out


def _ref_matches_hit(ref: ParsedRef, hit: Dict[str, Any], match_mode: str = "strict") -> bool:
    if not ref.raw:
        return False
    h = _hit_identity(hit)
    mode = _safe_text(match_mode).lower() or "strict"
    if ref.file or ref.page or ref.chunk:
        if mode == "loose":
            if ref.file and ref.file in h["file"]:
                return True
            if ref.chunk:
                digits = re.sub(r"\D+", "", ref.chunk)
                if digits and digits in h["chunk"]:
                    return True
            # if file/chunk exist but none match, treat as mismatch
            return False
        if ref.file and ref.file not in h["file"]:
            return False
        if ref.page and ref.page not in h["page"]:
            return False
        if ref.chunk and ref.chunk not in h["chunk"]:
            # allow "chunk=3" to match "3" or "chunk_3"
            digits = re.sub(r"\D+", "", ref.chunk)
            if not (digits and digits in h["chunk"]):
                return False
        return True

    if ref.raw:
        # weak fallback for references like "paper::abc" or "abc.json chunk 3"
        raw = _norm_text(ref.raw)
        if raw and (raw in h["text"] or raw in f"{h['file']}|{h['page']}|{h['chunk']}"):
            return True
        raw_digits = re.sub(r"\D+", "", raw)
        if raw_digits and raw_digits in h["chunk"]:
            return True
        return False
    return False


def _gold_relevance_flags(hits: List[Dict[str, Any]], gold_sources: List[str] | None) -> Tuple[Optional[List[int]], int]:
    if not gold_sources:
        return None, 0

    refs = [_parse_reference(x) for x in gold_sources if _safe_text(x)]
    if not refs:
        return None, 0

    flags: List[int] = []
    for hit in hits:
        rel = 1 if any(_ref_matches_hit(ref, hit) for ref in refs) else 0
        flags.append(rel)
    return flags, len(refs)


def _precision_at_k(flags: List[int], k: int) -> float:
    return sum(flags[:k]) / float(k)


def _recall_at_k(flags: List[int], total_rel: int, k: int) -> float:
    if total_rel <= 0:
        return 0.0
    return sum(flags[:k]) / float(total_rel)


def _mrr(flags: List[int]) -> float:
    for i, v in enumerate(flags, start=1):
        if v > 0:
            return 1.0 / float(i)
    return 0.0


def _extract_citations_from_answer(answer: str) -> List[ParsedRef]:
    text = _safe_text(answer)
    if not text:
        return []
    low = text.lower()
    m = re.search(r"(?:\bngu\w*n\b|\bsource\b)\s*:", low)
    if not m:
        return []
    tail = text[m.end() :]
    refs: List[ParsedRef] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        refs.append(_parse_reference(line))
    return refs


def _hallucination_class(answer: str, hits: List[Dict[str, Any]], match_mode: str = "strict") -> str:
    refs = _extract_citations_from_answer(answer)
    if not refs:
        return "no_source"

    all_valid = all(any(_ref_matches_hit(ref, h, match_mode=match_mode) for h in hits) for ref in refs)
    return "cited_correct" if all_valid else "cited_wrong"


def _normalize_rag_answer(text: str) -> str:
    out = _safe_text(text)
    out = re.sub(r"(?im)^\s*tra\s*loi\s*:\s*$", "Tra loi:", out)
    out = re.sub(r"(?im)^\s*ngu(?:o|ong)\s*:\s*$", "Nguon:", out)
    return out


class SparseBM25Retriever:
    def __init__(self, documents: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = float(k1)
        self.b = float(b)
        self.docs: List[Dict[str, Any]] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_tf: List[Dict[str, int]] = []
        self.df: Dict[str, int] = {}
        self.avgdl = 0.0

        for doc in documents:
            for chunk in extract_document_chunks(doc):
                text = _safe_text(chunk.get("text"))
                if not text:
                    continue
                row = {
                    "file": chunk.get("file"),
                    "page": chunk.get("page"),
                    "chunk_id": chunk.get("chunk_id"),
                    "text": text,
                }
                tokens = _tokenize(text)
                if not tokens:
                    continue
                tf: Dict[str, int] = {}
                for t in tokens:
                    tf[t] = tf.get(t, 0) + 1
                for t in tf.keys():
                    self.df[t] = self.df.get(t, 0) + 1

                self.docs.append(row)
                self.doc_tokens.append(tokens)
                self.doc_tf.append(tf)

        if self.doc_tokens:
            self.avgdl = sum(len(x) for x in self.doc_tokens) / float(len(self.doc_tokens))

    def retrieve(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        q_tokens = _tokenize(query)
        if not q_tokens or not self.docs:
            return []

        n_docs = len(self.docs)
        scores: List[Tuple[float, int]] = []
        for idx, tf in enumerate(self.doc_tf):
            dl = len(self.doc_tokens[idx])
            score = 0.0
            for term in q_tokens:
                f = tf.get(term, 0)
                if f <= 0:
                    continue
                df = self.df.get(term, 0)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                den = f + self.k1 * (1.0 - self.b + self.b * (dl / max(self.avgdl, 1e-9)))
                score += idf * (f * (self.k1 + 1.0)) / den
            if score > 0.0:
                scores.append((score, idx))

        scores.sort(key=lambda x: x[0], reverse=True)
        out: List[Dict[str, Any]] = []
        for score, idx in scores[:top_k]:
            row = dict(self.docs[idx])
            row["score"] = float(score)
            out.append(row)
        return out


def _new_graph_client() -> "Neo4jClient":
    from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

    cfg = Neo4jConfig(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_USER_DOC_DB", os.getenv("NEO4J_DB", "neo4j")),
    )
    client = Neo4jClient(cfg)
    client.connect()
    return client


def _resolve_seed_from_doc_file(client: "Neo4jClient", doc_file: str) -> str | None:
    cypher = """
    MATCH (p:Paper)
    WHERE p.source_file = $doc_file OR p.source_file ENDS WITH $doc_file
    RETURN p.id AS paper_id
    ORDER BY coalesce(p.updated_at, p.created_at) DESC
    LIMIT 1
    """
    rows = client.execute_read(cypher, {"doc_file": doc_file})
    if not rows:
        return None
    return _safe_text(rows[0].get("paper_id")) or None


def _norm_paper_id(value: str) -> str:
    v = _safe_text(value).lower()
    if v.startswith("paper::"):
        v = v.split("paper::", 1)[1]
    if v.endswith(".json"):
        v = v[: -len(".json")]
    return v.strip()


def _graph_chunks_to_hits(
    chunks: List[Any],
    fallback_file_name: str = "",
    seed_paper_id: str = "",
) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    fallback_file = _safe_text(fallback_file_name)
    fallback_stem = _norm_paper_id(Path(fallback_file).stem if fallback_file else "")
    seed_norm = _norm_paper_id(seed_paper_id)
    for i, ch in enumerate(chunks):
        metadata = getattr(ch, "metadata", {}) or {}
        paper_id_raw = _safe_text(metadata.get("paper_id"))
        paper_id_norm = _norm_paper_id(paper_id_raw)
        source_file_meta = _safe_text(metadata.get("source_file") or metadata.get("file"))

        file_value = source_file_meta
        if not file_value:
            if fallback_file and paper_id_norm and paper_id_norm == fallback_stem:
                file_value = fallback_file
            elif fallback_file and seed_norm and paper_id_norm and paper_id_norm == seed_norm:
                file_value = fallback_file
            elif paper_id_raw:
                file_value = paper_id_raw
            else:
                file_value = "graph"

        chunk_value = _safe_text(metadata.get("chunk_id")) or f"graph-{i}"
        hits.append(
            {
                "file": file_value,
                "page": None,
                "chunk_id": chunk_value,
                "score": float(getattr(ch, "score", 0.0) or 0.0),
                "text": _safe_text(getattr(ch, "text", "")),
                "source": _safe_text(getattr(ch, "source", "graph")),
                "metadata": metadata,
            }
        )
    return hits


def _fuse_hits_rrf(
    vector_hits: List[Dict[str, Any]],
    graph_hits: List[Dict[str, Any]],
    top_k: int,
    rrf_k: int = 60,
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    def _key(hit: Dict[str, Any]) -> str:
        return _norm_text(hit.get("text"))[:500]

    for rank, hit in enumerate(vector_hits, start=1):
        k = _key(hit)
        if not k:
            continue
        row = merged.setdefault(k, dict(hit))
        row["_rrf"] = float(row.get("_rrf", 0.0)) + 1.0 / (rrf_k + rank)

    for rank, hit in enumerate(graph_hits, start=1):
        k = _key(hit)
        if not k:
            continue
        row = merged.setdefault(k, dict(hit))
        row["_rrf"] = float(row.get("_rrf", 0.0)) + 1.0 / (rrf_k + rank)

    rows = list(merged.values())
    rows.sort(key=lambda x: float(x.get("_rrf", 0.0)), reverse=True)
    out: List[Dict[str, Any]] = []
    for row in rows[:top_k]:
        item = dict(row)
        item["fusion_score"] = round(float(item.pop("_rrf", 0.0)), 6)
        out.append(item)
    return out


def _aggregate_system_metrics(
    rows: List[Dict[str, Any]],
    systems: List[str],
    k_values: List[int],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    max_k = max(k_values)
    for sys_name in systems:
        sys_rows = [r for r in rows if r.get("system") == sys_name and r.get("flags") is not None]
        if not sys_rows:
            out[sys_name] = {"eval_size": 0}
            continue

        metrics: Dict[str, Any] = {"eval_size": len(sys_rows)}
        for k in k_values:
            p_vals = [_precision_at_k(r["flags"], k) for r in sys_rows]
            metrics[f"precision@{k}"] = _mean(p_vals)

        # Recall@5 required by spec
        recall_k = 5 if 5 <= max_k else max_k
        r_vals = [_recall_at_k(r["flags"], int(r["total_rel"]), recall_k) for r in sys_rows]
        metrics[f"recall@{recall_k}"] = _mean(r_vals)

        mrr_vals = [_mrr(r["flags"]) for r in sys_rows]
        metrics["mrr"] = _mean(mrr_vals)
        out[sys_name] = metrics
    return out


def _aggregate_hallucination(rows: List[Dict[str, Any]], systems: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sys_name in systems:
        sys_rows = [r for r in rows if r.get("system") == sys_name and _safe_text(r.get("answer"))]
        total = len(sys_rows)
        counts = {"cited_correct": 0, "cited_wrong": 0, "no_source": 0}
        for r in sys_rows:
            cls = _safe_text(r.get("hallucination_class"))
            if cls in counts:
                counts[cls] += 1
        rates = {f"{k}_rate": (counts[k] / total if total > 0 else None) for k in counts}
        if total > 0:
            correct_rate = float(rates.get("cited_correct_rate") or 0.0)
            wrong_rate = float(rates.get("cited_wrong_rate") or 0.0)
            no_source_rate = float(rates.get("no_source_rate") or 0.0)
            # Groundedness: positive when correct citations dominate wrong citations.
            groundedness_score = correct_rate - wrong_rate
            # Hallucination score in [0,1]: penalize wrong citations heavily and no-source moderately.
            hallucination_score = max(0.0, min(1.0, 1.0 - wrong_rate - 0.5 * no_source_rate))
            scores = {
                "groundedness_score": groundedness_score,
                "hallucination_score": hallucination_score,
            }
        else:
            scores = {"groundedness_score": None, "hallucination_score": None}
        out[sys_name] = {"eval_size": total, "counts": counts, "rates": rates, "scores": scores}
    return out


def _aggregate_answerability(rows: List[Dict[str, Any]], systems: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sys_name in systems:
        sys_rows = [r for r in rows if r.get("system") == sys_name]
        total = len(sys_rows)
        counts = {"answered": 0, "not_answered": 0, "empty": 0}
        for r in sys_rows:
            cls = _safe_text(r.get("answerability_class"))
            if cls in counts:
                counts[cls] += 1
        rates = {f"{k}_rate": (counts[k] / total if total > 0 else None) for k in counts}
        out[sys_name] = {"eval_size": total, "counts": counts, "rates": rates}
    return out


def _aggregate_ops(rows: List[Dict[str, Any]], systems: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sys_name in systems:
        sys_rows = [r for r in rows if r.get("system") == sys_name]
        if not sys_rows:
            out[sys_name] = {"eval_size": 0}
            continue

        retrieval_vals = [float(r.get("retrieval_latency_ms") or 0.0) for r in sys_rows]
        gen_vals = [float(r.get("generation_latency_ms") or 0.0) for r in sys_rows]
        total_vals = [float(r.get("total_latency_ms") or 0.0) for r in sys_rows]

        input_tokens = [int(r.get("prompt_tokens") or 0) for r in sys_rows]
        output_tokens = [int(r.get("answer_tokens") or 0) for r in sys_rows]
        total_tokens = [int(r.get("total_tokens") or 0) for r in sys_rows]
        costs = [float(r.get("estimated_cost") or 0.0) for r in sys_rows]

        out[sys_name] = {
            "eval_size": len(sys_rows),
            "latency_ms": {
                "retrieval_p50": _percentile(retrieval_vals, 50),
                "retrieval_p95": _percentile(retrieval_vals, 95),
                "generation_p50": _percentile(gen_vals, 50),
                "generation_p95": _percentile(gen_vals, 95),
                "total_p50": _percentile(total_vals, 50),
                "total_p95": _percentile(total_vals, 95),
                "retrieval_avg": _mean(retrieval_vals),
                "generation_avg": _mean(gen_vals),
                "total_avg": _mean(total_vals),
            },
            "tokens": {
                "prompt_total": int(sum(input_tokens)),
                "answer_total": int(sum(output_tokens)),
                "all_total": int(sum(total_tokens)),
                "prompt_avg": _mean([float(x) for x in input_tokens]),
                "answer_avg": _mean([float(x) for x in output_tokens]),
                "all_avg": _mean([float(x) for x in total_tokens]),
            },
            "cost": {
                "estimated_total": float(sum(costs)),
                "estimated_avg": _mean(costs),
            },
        }
    return out


def _aggregate_by_module(
    retrieval_rows: List[Dict[str, Any]],
    detailed_rows: List[Dict[str, Any]],
    systems: List[str],
    k_values: List[int],
) -> Dict[str, Any]:
    modules = sorted({str(r.get("module") or "") for r in detailed_rows if _safe_text(r.get("module"))})
    by_module_metrics: Dict[str, Any] = {}
    by_module_hallucination: Dict[str, Any] = {}
    by_module_qtype_metrics: Dict[str, Any] = {}
    by_module_answerability: Dict[str, Any] = {}
    by_module_ops: Dict[str, Any] = {}

    for module in modules:
        mod_retrieval = [r for r in retrieval_rows if _safe_text(r.get("module")) == module]
        mod_detailed = [r for r in detailed_rows if _safe_text(r.get("module")) == module]
        by_module_metrics[module] = _aggregate_system_metrics(mod_retrieval, systems, k_values)
        by_module_hallucination[module] = _aggregate_hallucination(mod_detailed, systems)
        by_module_answerability[module] = _aggregate_answerability(mod_detailed, systems)
        by_module_ops[module] = _aggregate_ops(mod_detailed, systems)

        qtype_block: Dict[str, Any] = {}
        for qtype in QUESTION_TYPES:
            q_rows = [r for r in mod_retrieval if _safe_text(r.get("question_type")) == qtype]
            qtype_block[qtype] = _aggregate_system_metrics(q_rows, systems, k_values)
        by_module_qtype_metrics[module] = qtype_block

    return {
        "modules": modules,
        "metrics": by_module_metrics,
        "hallucination": by_module_hallucination,
        "by_question_type_metrics": by_module_qtype_metrics,
        "answerability": by_module_answerability,
        "ops": by_module_ops,
    }


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    modules = {x.strip() for x in args.modules.split(",") if x.strip()} if args.modules else set()
    question_types = {x.strip() for x in args.question_types.split(",") if x.strip()} if args.question_types else set()
    k_values = _parse_k_list(args.k_values)
    max_k = max(k_values)
    retrieve_k = args.retrieve_k if args.retrieve_k > 0 else max(20, max_k * 4)
    answer_top_k = max_k if args.answer_top_k <= 0 else args.answer_top_k

    questions = _load_questions(args.questions_path, modules, question_types, args.limit)
    if not questions:
        raise RuntimeError("No questions loaded from selected filters.")

    by_module_file, by_file_name = _discover_docs(args.data_root)
    docs_by_path: Dict[str, Dict[str, Any]] = {}
    q_to_doc_path: Dict[str, str] = {}
    skipped: List[Dict[str, str]] = []

    for q in questions:
        fp = _resolve_doc_path(q, by_module_file, by_file_name)
        if fp is None or not fp.exists():
            skipped.append({"id": q.id, "reason": "document_not_found"})
            continue
        key = str(fp.resolve())
        q_to_doc_path[q.id] = key
        if key not in docs_by_path:
            doc = _read_json(fp)
            doc["file"] = fp.name
            docs_by_path[key] = doc

    active_questions = [q for q in questions if q.id in q_to_doc_path]
    if not active_questions:
        raise RuntimeError("No active question can be resolved to document.")

    cfg = InferenceConfig(
        chunk_size_chars=args.chunk_size_chars,
        chunk_overlap_chars=args.chunk_overlap_chars,
        top_k=max_k,
        max_new_tokens_qa=args.max_new_tokens_qa,
        temperature=0.0,
    )

    dense_retriever = VectorRetriever(cfg)
    dense_build_info = dense_retriever.build(list(docs_by_path.values()))
    sparse_retriever = SparseBM25Retriever(list(docs_by_path.values()), k1=args.bm25_k1, b=args.bm25_b)

    use_graph = bool(args.use_graph)
    graph_client: Neo4jClient | None = None
    graph_retriever: GraphRetriever | None = None
    graph_boot_error = ""
    seed_cache: Dict[str, str | None] = {}

    if use_graph:
        try:
            graph_client = _new_graph_client()
            graph_cfg = GraphRetrieverConfig(
                database=os.getenv("NEO4J_USER_DOC_DB", os.getenv("NEO4J_DB", "neo4j")),
                top_k=retrieve_k,
                concept_depth=args.graph_depth,
                min_score=args.graph_min_score,
                fulltext_threshold=args.graph_fulltext_threshold,
            )
            graph_retriever = GraphRetriever(client=graph_client, config=graph_cfg)
        except Exception as ex:  # pragma: no cover
            graph_boot_error = str(ex)
            use_graph = False

    reranker: BGEReranker | None = None
    if args.use_rerank_proposed:
        reranker = BGEReranker(
            config=cfg,
            model_name=args.rerank_model,
            device=args.rerank_device,
            strict_device=False,
        )

    llm: LLMEngine | None = None
    if not args.skip_answer:
        llm = LLMEngine(cfg)

    detailed_rows: List[Dict[str, Any]] = []
    retrieval_metric_rows: List[Dict[str, Any]] = []

    try:
        for q in active_questions:
            doc_path = q_to_doc_path[q.id]
            doc = docs_by_path[doc_path]

            t_dense0 = time.perf_counter()
            dense_hits = dense_retriever.retrieve(q.question, top_k=retrieve_k)
            dense_ms = (time.perf_counter() - t_dense0) * 1000.0
            t_sparse0 = time.perf_counter()
            sparse_hits = sparse_retriever.retrieve(q.question, top_k=retrieve_k)
            sparse_ms = (time.perf_counter() - t_sparse0) * 1000.0

            graph_hits: List[Dict[str, Any]] = []
            graph_info: Dict[str, Any] | None = None
            graph_error = graph_boot_error
            graph_ms = 0.0
            if use_graph and graph_client is not None and graph_retriever is not None:
                seed = q.seed_paper_id or _safe_text(args.seed_paper_id)
                if not seed:
                    doc_file = _safe_text(doc.get("file")) or q.file_name
                    if doc_file not in seed_cache:
                        try:
                            seed_cache[doc_file] = _resolve_seed_from_doc_file(graph_client, doc_file)
                        except Exception:
                            seed_cache[doc_file] = None
                    seed = _safe_text(seed_cache.get(doc_file))
                try:
                    gresult = graph_retriever.retrieve(query_text=q.question, paper_id=seed or None, top_k=retrieve_k)
                    graph_hits = _graph_chunks_to_hits(
                        gresult.chunks,
                        fallback_file_name=q.file_name,
                        seed_paper_id=seed,
                    )
                    graph_info = {
                        "paper_id": seed or None,
                        "cypher_calls": gresult.query_info.cypher_calls,
                        "total_nodes_found": gresult.query_info.total_nodes_found,
                        "retrieval_ms": gresult.query_info.retrieval_ms,
                    }
                    graph_ms = float(gresult.query_info.retrieval_ms or 0.0)
                    graph_error = ""
                except Exception as ex:  # pragma: no cover
                    graph_error = str(ex)

            t_fuse0 = time.perf_counter()
            proposed_hits = _fuse_hits_rrf(dense_hits, graph_hits, top_k=retrieve_k) if graph_hits else list(dense_hits)
            fuse_ms = (time.perf_counter() - t_fuse0) * 1000.0
            if reranker is not None and proposed_hits:
                t_rr0 = time.perf_counter()
                proposed_hits = reranker.rerank(query=q.question, candidates=proposed_hits, top_k=retrieve_k)
                rerank_ms = (time.perf_counter() - t_rr0) * 1000.0
            else:
                rerank_ms = 0.0

            system_hits = {
                SYSTEM_DENSE: dense_hits,
                SYSTEM_SPARSE: sparse_hits,
                SYSTEM_PROPOSED: proposed_hits,
            }
            retrieval_latency_ms = {
                SYSTEM_DENSE: dense_ms,
                SYSTEM_SPARSE: sparse_ms,
                SYSTEM_PROPOSED: dense_ms + graph_ms + fuse_ms + rerank_ms,
            }

            for sys_name, hits in system_hits.items():
                top_hits_for_answer = hits[:answer_top_k]
                flags, total_rel = _gold_relevance_flags(hits, q.gold_sources)

                answer = ""
                gen_ms = 0.0
                prompt_tokens = 0
                answer_tokens = 0
                total_tokens = 0
                estimated_cost = 0.0
                if llm is not None:
                    context = format_retrieved_context(top_hits_for_answer)
                    prompt_text = rag_prompt(question=q.question, context=context)
                    prompt_tokens = _estimate_tokens(prompt_text, llm)
                    t_gen0 = time.perf_counter()
                    answer = llm.generate(
                        prompt_text,
                        max_new_tokens=cfg.max_new_tokens_qa,
                    )
                    gen_ms = (time.perf_counter() - t_gen0) * 1000.0
                    answer = _normalize_rag_answer(answer)
                    answer_tokens = _estimate_tokens(answer, llm)
                    total_tokens = prompt_tokens + answer_tokens
                    estimated_cost = (
                        (prompt_tokens / 1000.0) * float(args.input_token_cost_per_1k)
                        + (answer_tokens / 1000.0) * float(args.output_token_cost_per_1k)
                    )

                hallu_cls = (
                    _hallucination_class(answer, top_hits_for_answer, match_mode=args.citation_match_mode) if answer else ""
                )
                answerability_cls = _answerability_class(answer)
                total_latency_ms = float(retrieval_latency_ms.get(sys_name, 0.0)) + gen_ms
                row = {
                    "id": q.id,
                    "system": sys_name,
                    "system_label": SYSTEM_LABELS[sys_name],
                    "module": q.module,
                    "file_name": q.file_name,
                    "question_type": q.question_type,
                    "question": q.question,
                    "gold_sources": q.gold_sources or [],
                    "gold_answer": q.gold_answer,
                    "hits": hits,
                    "answer": answer,
                    "hallucination_class": hallu_cls,
                    "answerability_class": answerability_cls,
                    "retrieval_latency_ms": round(float(retrieval_latency_ms.get(sys_name, 0.0)), 4),
                    "generation_latency_ms": round(gen_ms, 4),
                    "total_latency_ms": round(total_latency_ms, 4),
                    "prompt_tokens": int(prompt_tokens),
                    "answer_tokens": int(answer_tokens),
                    "total_tokens": int(total_tokens),
                    "estimated_cost": round(float(estimated_cost), 8),
                    "flags": flags,
                    "total_rel": total_rel,
                    "graph_info": graph_info if sys_name == SYSTEM_PROPOSED else None,
                    "graph_error": graph_error if sys_name == SYSTEM_PROPOSED else "",
                }
                detailed_rows.append(row)
                if flags is not None and total_rel > 0:
                    retrieval_metric_rows.append(
                        {
                            "id": q.id,
                            "system": sys_name,
                            "module": q.module,
                            "question_type": q.question_type,
                            "flags": flags,
                            "total_rel": total_rel,
                        }
                    )
    finally:
        if graph_client is not None:
            try:
                graph_client.close()
            except Exception:
                pass

    systems = [SYSTEM_DENSE, SYSTEM_SPARSE, SYSTEM_PROPOSED]
    agg_main = _aggregate_system_metrics(retrieval_metric_rows, systems, k_values)
    agg_hall = _aggregate_hallucination(detailed_rows, systems)
    agg_answerability = _aggregate_answerability(detailed_rows, systems)
    agg_ops = _aggregate_ops(detailed_rows, systems)

    by_qtype: Dict[str, Dict[str, Any]] = {}
    for qtype in QUESTION_TYPES:
        sub = [r for r in retrieval_metric_rows if r.get("question_type") == qtype]
        by_qtype[qtype] = _aggregate_system_metrics(sub, systems, k_values)

    by_qtype_answerability: Dict[str, Dict[str, Any]] = {}
    by_qtype_ops: Dict[str, Dict[str, Any]] = {}
    for qtype in QUESTION_TYPES:
        sub = [r for r in detailed_rows if _safe_text(r.get("question_type")) == qtype]
        by_qtype_answerability[qtype] = _aggregate_answerability(sub, systems)
        by_qtype_ops[qtype] = _aggregate_ops(sub, systems)

    by_module = _aggregate_by_module(
        retrieval_rows=retrieval_metric_rows,
        detailed_rows=detailed_rows,
        systems=systems,
        k_values=k_values,
    )

    # Table-style payloads
    recall_k_used = 5 if max(k_values) >= 5 else max(k_values)
    recall_key = f"recall@{recall_k_used}"
    table_36_rows = []
    for s in systems:
        m = agg_main.get(s, {})
        table_36_rows.append(
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "precision": {f"@{k}": m.get(f"precision@{k}") for k in k_values},
                "recall@5": m.get(recall_key),
                "mrr": m.get("mrr"),
                "eval_size": m.get("eval_size", 0),
            }
        )

    table_37_rows = []
    for s in systems:
        h = agg_hall.get(s, {})
        table_37_rows.append(
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "eval_size": h.get("eval_size", 0),
                "counts": h.get("counts", {}),
                "rates": h.get("rates", {}),
                "scores": h.get("scores", {}),
            }
        )

    table_38 = {
        qtype: {
            s: {
                "precision": {f"@{k}": by_qtype.get(qtype, {}).get(s, {}).get(f"precision@{k}") for k in k_values},
                "recall@5": by_qtype.get(qtype, {}).get(s, {}).get(recall_key),
                "mrr": by_qtype.get(qtype, {}).get(s, {}).get("mrr"),
                "eval_size": by_qtype.get(qtype, {}).get(s, {}).get("eval_size", 0),
            }
            for s in systems
        }
        for qtype in QUESTION_TYPES
    }

    table_answerability_rows = []
    for s in systems:
        a = agg_answerability.get(s, {})
        table_answerability_rows.append(
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "eval_size": a.get("eval_size", 0),
                "counts": a.get("counts", {}),
                "rates": a.get("rates", {}),
            }
        )

    table_ops_rows = []
    for s in systems:
        o = agg_ops.get(s, {})
        table_ops_rows.append(
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "eval_size": o.get("eval_size", 0),
                "latency_ms": o.get("latency_ms", {}),
                "tokens": o.get("tokens", {}),
                "cost": o.get("cost", {}),
            }
        )

    table_module_rows = []
    table_module_answerability_rows = []
    table_module_ops_rows = []
    for module in by_module["modules"]:
        row = {"module": module, "systems": {}}
        row_ans = {"module": module, "systems": {}}
        row_ops = {"module": module, "systems": {}}
        for s in systems:
            m = by_module["metrics"].get(module, {}).get(s, {})
            row["systems"][s] = {
                "label": SYSTEM_LABELS[s],
                "precision": {f"@{k}": m.get(f"precision@{k}") for k in k_values},
                "recall@5": m.get(recall_key),
                "mrr": m.get("mrr"),
                "eval_size": m.get("eval_size", 0),
            }
            a = by_module["answerability"].get(module, {}).get(s, {})
            row_ans["systems"][s] = {
                "label": SYSTEM_LABELS[s],
                "eval_size": a.get("eval_size", 0),
                "counts": a.get("counts", {}),
                "rates": a.get("rates", {}),
            }
            o = by_module["ops"].get(module, {}).get(s, {})
            row_ops["systems"][s] = {
                "label": SYSTEM_LABELS[s],
                "eval_size": o.get("eval_size", 0),
                "latency_ms": o.get("latency_ms", {}),
                "tokens": o.get("tokens", {}),
                "cost": o.get("cost", {}),
            }
        table_module_rows.append(row)
        table_module_answerability_rows.append(row_ans)
        table_module_ops_rows.append(row_ops)

    # Chart-friendly payloads
    chart_33_grouped_bar = {
        "k_values": k_values,
        "series": [
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "precision_values": [agg_main.get(s, {}).get(f"precision@{k}") for k in k_values],
            }
            for s in systems
        ],
    }
    chart_34_line_tradeoff = {
        "x_k": k_values,
        "series": [
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "precision": [agg_main.get(s, {}).get(f"precision@{k}") for k in k_values],
                "recall": [
                    _mean(
                        [
                            _recall_at_k(r["flags"], int(r["total_rel"]), k)
                            for r in retrieval_metric_rows
                            if r.get("system") == s
                        ]
                    )
                    for k in k_values
                ],
            }
            for s in systems
        ],
    }
    chart_35_hallucination = {
        "series": [
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "counts": agg_hall.get(s, {}).get("counts", {}),
                "rates": agg_hall.get(s, {}).get("rates", {}),
            }
            for s in systems
        ]
    }
    chart_answerability_by_qtype = {
        "question_types": list(QUESTION_TYPES),
        "series": [
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "answered_rate": [
                    by_qtype_answerability.get(qtype, {}).get(s, {}).get("rates", {}).get("answered_rate")
                    for qtype in QUESTION_TYPES
                ],
                "not_answered_rate": [
                    by_qtype_answerability.get(qtype, {}).get(s, {}).get("rates", {}).get("not_answered_rate")
                    for qtype in QUESTION_TYPES
                ],
                "empty_rate": [
                    by_qtype_answerability.get(qtype, {}).get(s, {}).get("rates", {}).get("empty_rate")
                    for qtype in QUESTION_TYPES
                ],
            }
            for s in systems
        ],
    }
    chart_latency_p50_p95 = {
        "series": [
            {
                "system": s,
                "label": SYSTEM_LABELS[s],
                "total_latency_p50_ms": agg_ops.get(s, {}).get("latency_ms", {}).get("total_p50"),
                "total_latency_p95_ms": agg_ops.get(s, {}).get("latency_ms", {}).get("total_p95"),
            }
            for s in systems
        ]
    }

    summary = {
        "questions_path": str(args.questions_path),
        "data_root": str(args.data_root),
        "output": str(args.out_dir / args.output_name),
        "num_questions_loaded": len(questions),
        "num_questions_run": len(active_questions),
        "num_skipped": len(skipped),
        "systems": systems,
        "system_labels": SYSTEM_LABELS,
        "k_values": k_values,
        "retrieve_k": retrieve_k,
        "answer_top_k": answer_top_k,
        "use_graph": use_graph,
        "use_rerank_proposed": bool(reranker is not None),
        "generate_answer": llm is not None,
        "input_token_cost_per_1k": float(args.input_token_cost_per_1k),
        "output_token_cost_per_1k": float(args.output_token_cost_per_1k),
        "citation_match_mode": _safe_text(args.citation_match_mode).lower() or "strict",
        "dense_build_info": dense_build_info,
        "sparse_num_chunks": len(sparse_retriever.docs),
    }

    return {
        "summary": summary,
        "skipped": skipped,
        "tables": {
            "table_3_6_main_metrics": table_36_rows,
            "table_3_7_hallucination": table_37_rows,
            "table_3_8_by_question_type": table_38,
            "table_answerability_overall": table_answerability_rows,
            "table_ops_latency_token_cost": table_ops_rows,
            "table_module_metrics": table_module_rows,
            "table_module_answerability": table_module_answerability_rows,
            "table_module_ops": table_module_ops_rows,
        },
        "charts": {
            "chart_3_3_grouped_bar_precision_k": chart_33_grouped_bar,
            "chart_3_4_line_precision_recall_tradeoff": chart_34_line_tradeoff,
            "chart_3_5_hallucination_distribution": chart_35_hallucination,
            "chart_answerability_by_question_type": chart_answerability_by_qtype,
            "chart_latency_p50_p95": chart_latency_p50_p95,
        },
        "aggregates": {
            "overall_metrics": agg_main,
            "by_question_type_metrics": by_qtype,
            "hallucination_metrics": agg_hall,
            "answerability_metrics": agg_answerability,
            "ops_metrics": agg_ops,
            "by_question_type_answerability": by_qtype_answerability,
            "by_question_type_ops": by_qtype_ops,
            "by_module": by_module,
        },
        "results": detailed_rows,
    }


def save_outputs(payload: Dict[str, Any], out_dir: Path, output_name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / output_name
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Direct RAG QA benchmark (no backend API): Dense vs Sparse(BM25) vs Dense+KG(Hybrid Fusion)."
    )
    parser.add_argument("--questions-path", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--output-name", type=str, default=DEFAULT_OUTPUT_NAME)

    parser.add_argument("--k-values", type=str, default="1,3,5")
    parser.add_argument("--retrieve-k", type=int, default=0)
    parser.add_argument("--answer-top-k", type=int, default=5)
    parser.add_argument("--chunk-size-chars", type=int, default=2800)
    parser.add_argument("--chunk-overlap-chars", type=int, default=300)
    parser.add_argument("--max-new-tokens-qa", type=int, default=500)

    parser.add_argument("--use-graph", action="store_true")
    parser.add_argument("--seed-paper-id", type=str, default="")
    parser.add_argument("--use-rerank-proposed", action="store_true")
    parser.add_argument("--rerank-model", type=str, default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--rerank-device", type=str, default="cpu")
    parser.add_argument("--skip-answer", action="store_true")

    parser.add_argument("--graph-depth", type=int, default=2)
    parser.add_argument("--graph-min-score", type=float, default=0.3)
    parser.add_argument("--graph-fulltext-threshold", type=float, default=0.6)
    parser.add_argument("--bm25-k1", type=float, default=1.5)
    parser.add_argument("--bm25-b", type=float, default=0.75)
    parser.add_argument("--input-token-cost-per-1k", type=float, default=0.0)
    parser.add_argument("--output-token-cost-per-1k", type=float, default=0.0)
    parser.add_argument("--citation-match-mode", type=str, choices=["strict", "loose"], default="strict")

    parser.add_argument("--modules", type=str, default="")
    parser.add_argument("--question-types", type=str, default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.retrieve_k < 0:
        raise ValueError("--retrieve-k must be >= 0")
    if args.answer_top_k < 0:
        raise ValueError("--answer-top-k must be >= 0")
    if args.chunk_size_chars <= 0:
        raise ValueError("--chunk-size-chars must be > 0")
    if args.chunk_overlap_chars < 0 or args.chunk_overlap_chars >= args.chunk_size_chars:
        raise ValueError("--chunk-overlap-chars must be >= 0 and < --chunk-size-chars")
    if args.max_new_tokens_qa <= 0:
        raise ValueError("--max-new-tokens-qa must be > 0")
    if args.limit < 0:
        raise ValueError("--limit must be >= 0")
    if args.bm25_k1 <= 0:
        raise ValueError("--bm25-k1 must be > 0")
    if args.bm25_b < 0 or args.bm25_b > 1:
        raise ValueError("--bm25-b must be in [0, 1]")
    if args.input_token_cost_per_1k < 0:
        raise ValueError("--input-token-cost-per-1k must be >= 0")
    if args.output_token_cost_per_1k < 0:
        raise ValueError("--output-token-cost-per-1k must be >= 0")

    payload = run_benchmark(args)
    out_file = save_outputs(payload, args.out_dir, args.output_name)

    s = payload["summary"]
    print("RAG QA benchmark completed.")
    print(f"- questions: {s['num_questions_run']} (loaded={s['num_questions_loaded']}, skipped={s['num_skipped']})")
    print(f"- systems: {', '.join(s['systems'])}")
    print(f"- k_values: {s['k_values']} | retrieve_k: {s['retrieve_k']} | answer_top_k: {s['answer_top_k']}")
    print(f"- use_graph/use_rerank_proposed/generate_answer: {s['use_graph']}/{s['use_rerank_proposed']}/{s['generate_answer']}")
    print(f"- output: {out_file}")


if __name__ == "__main__":
    main()
