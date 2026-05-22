"""
summary_metrics.py
------------------
Run summarization experiments for 4 systems:
  1) baseline_extractive
  2) short       (LLM style: executive)
  3) detailed    (LLM style: academic)
  4) contextual  (LLM style: semantic)

Outputs:
  - Table 3.9  : auto metrics (ROUGE-1/2/L, BERTScore) for 3 LLM modes
  - Table 3.10 : human evaluation averages (4 criteria) for 4 systems
  - Figure 3.6 : radar chart for human evaluation
  - Figure 3.7 : bar chart for ROUGE by mode
  - Table 3.11 : case study examples
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_module.utils.document_schema import extract_document_text, resolve_document_id

logger = logging.getLogger(__name__)

HUMAN_CRITERIA = ("faithfulness", "coherence", "relevance", "conciseness")
ALL_MODES = ("baseline_extractive", "short", "detailed", "contextual")
LLM_MODES = ("short", "detailed", "contextual")
STYLE_MAP = {
    "short": "executive",
    "detailed": "academic",
    "contextual": "semantic",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _sent_tokenize(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", _safe_text(text))
    if not normalized:
        return []
    parts = re.split(r"(?<=[\.\!\?])\s+", normalized)
    return [p.strip() for p in parts if p.strip()]


def extractive_summary(text: str, max_sentences: int = 6, max_chars: int = 900) -> str:
    sents = _sent_tokenize(text)
    out: list[str] = []
    cur = 0
    for s in sents:
        if len(out) >= max_sentences:
            break
        if cur + len(s) + 1 > max_chars and out:
            break
        out.append(s)
        cur += len(s) + 1
    if out:
        return " ".join(out).strip()
    return _safe_text(text)[:max_chars]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        logger.warning("Skip invalid JSON '%s': %s", path, ex)
        return None


def load_documents(
    processed_dir: Path,
    reference_field: str,
    min_reference_chars: int,
    min_source_chars: int,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for fp in sorted(processed_dir.rglob("*.json")):
        payload = _load_json(fp)
        if not isinstance(payload, dict):
            continue
        source_text = extract_document_text(payload)
        reference = _safe_text(payload.get(reference_field))
        if len(source_text) < min_source_chars:
            continue
        if len(reference) < min_reference_chars:
            continue
        docs.append(
            {
                "doc_id": resolve_document_id(payload, fallback=fp.stem),
                "file_path": str(fp),
                "payload": payload,
                "source_text": source_text,
                "reference": reference,
            }
        )
    return docs


def summarize_llm_mode(doc_payload: dict[str, Any], mode: str) -> str:
    if mode not in STYLE_MAP:
        raise ValueError(f"Unsupported mode '{mode}'")
    style = STYLE_MAP[mode]
    try:
        from backend.app.services.ai_summary_service import generate_summary_from_doc_json
    except Exception:
        backend_root = PROJECT_ROOT / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.services.ai_summary_service import generate_summary_from_doc_json

    text = generate_summary_from_doc_json(
        doc_json=doc_payload,
        summary_style=style,
        num_highlights=16,
        chunk_highlights=4,
    )
    return _safe_text(text)


def compute_rouge_rows(rows: list[dict[str, Any]]) -> bool:
    try:
        from rouge_score import rouge_scorer
    except Exception:
        logger.warning("Package 'rouge-score' not found. ROUGE columns will be empty.")
        return False

    scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    for row in rows:
        ref = row["reference"]
        pred = row["summary"]
        score = scorer.score(ref, pred)
        row["rouge1"] = float(score["rouge1"].fmeasure)
        row["rouge2"] = float(score["rouge2"].fmeasure)
        row["rougeL"] = float(score["rougeL"].fmeasure)
    return True


def compute_bertscore_rows(
    rows: list[dict[str, Any]],
    lang: str = "en",
    model_type: str = "",
    device: str = "",
) -> bool:
    try:
        from bert_score import score as bertscore_score
    except Exception:
        logger.warning("Package 'bert-score' not found. BERTScore column will be empty.")
        return False

    mode_to_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        mode_to_rows[row["mode"]].append(row)

    for mode, mode_rows in mode_to_rows.items():
        cands = [r["summary"] for r in mode_rows]
        refs = [r["reference"] for r in mode_rows]
        kwargs: dict[str, Any] = {"lang": lang, "verbose": False}
        if model_type:
            kwargs["model_type"] = model_type
        if device:
            kwargs["device"] = device
        _, _, f1 = bertscore_score(cands, refs, **kwargs)
        vals = [float(x.item()) for x in f1]
        for idx, r in enumerate(mode_rows):
            r["bertscore_f1"] = vals[idx]
        logger.info("BERTScore done for mode=%s on %d docs", mode, len(mode_rows))
    return True


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.fmean(values))


def aggregate_auto_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_mode[r["mode"]].append(r)

    result: list[dict[str, Any]] = []
    for mode in ALL_MODES:
        mode_rows = by_mode.get(mode, [])
        if not mode_rows:
            continue
        result.append(
            {
                "mode": mode,
                "n_docs": len(mode_rows),
                "rouge1": _mean([float(x["rouge1"]) for x in mode_rows if isinstance(x.get("rouge1"), (int, float))]),
                "rouge2": _mean([float(x["rouge2"]) for x in mode_rows if isinstance(x.get("rouge2"), (int, float))]),
                "rougeL": _mean([float(x["rougeL"]) for x in mode_rows if isinstance(x.get("rougeL"), (int, float))]),
                "bertscore_f1": _mean(
                    [float(x["bertscore_f1"]) for x in mode_rows if isinstance(x.get("bertscore_f1"), (int, float))]
                ),
            }
        )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in headers})


def write_markdown_table(path: Path, rows: list[dict[str, Any]], headers: list[str], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        vals = []
        for h in headers:
            v = row.get(h, "")
            if isinstance(v, float):
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    path.write_text("\n".join(lines), encoding="utf-8")


def generate_human_eval_template(path: Path, doc_ids: list[str]) -> None:
    rows: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        for mode in ALL_MODES:
            rows.append(
                {
                    "doc_id": doc_id,
                    "mode": mode,
                    "rater_id": "",
                    "faithfulness": "",
                    "coherence": "",
                    "relevance": "",
                    "conciseness": "",
                    "notes": "",
                }
            )
    write_csv(
        path=path,
        rows=rows,
        headers=["doc_id", "mode", "rater_id", "faithfulness", "coherence", "relevance", "conciseness", "notes"],
    )


def parse_human_eval(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        logger.warning("Human eval CSV not found: %s", path)
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mode = _safe_text(row.get("mode"))
            doc_id = _safe_text(row.get("doc_id"))
            if mode not in ALL_MODES or not doc_id:
                continue
            parsed = {"doc_id": doc_id, "mode": mode}
            ok = True
            for c in HUMAN_CRITERIA:
                raw = _safe_text(row.get(c))
                if not raw:
                    ok = False
                    break
                try:
                    val = float(raw)
                except ValueError:
                    ok = False
                    break
                if val < 1 or val > 5:
                    ok = False
                    break
                parsed[c] = val
            if ok:
                out.append(parsed)
    return out


def aggregate_human_eval(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_mode[r["mode"]].append(r)
    result: list[dict[str, Any]] = []
    for mode in ALL_MODES:
        mode_rows = by_mode.get(mode, [])
        if not mode_rows:
            continue
        agg = {
            "mode": mode,
            "n_ratings": len(mode_rows),
        }
        for c in HUMAN_CRITERIA:
            agg[c] = _mean([float(x[c]) for x in mode_rows])
        agg["overall"] = _mean([agg[c] for c in HUMAN_CRITERIA])
        result.append(agg)
    return result


def save_case_study(path: Path, rows: list[dict[str, Any]], source_map: dict[str, str], n_examples: int) -> None:
    by_doc_mode: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        by_doc_mode[(r["doc_id"], r["mode"])] = r

    doc_ids = sorted({r["doc_id"] for r in rows})
    chosen = doc_ids[: max(0, n_examples)]

    lines = ["# Table 3.11 - Case Study", ""]
    for doc_id in chosen:
        lines.append(f"## {doc_id}")
        source_excerpt = _safe_text(source_map.get(doc_id))[:1500]
        lines.append("")
        lines.append("### Original (excerpt)")
        lines.append(source_excerpt or "(empty)")
        lines.append("")
        for mode in ("baseline_extractive", "short", "detailed", "contextual"):
            summary = _safe_text(by_doc_mode.get((doc_id, mode), {}).get("summary"))
            lines.append(f"### {mode}")
            lines.append(summary or "(empty)")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def plot_rouge_bar(auto_rows: list[dict[str, Any]], output_png: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        logger.warning("matplotlib not installed. Skip Figure 3.7.")
        return

    target_rows = [r for r in auto_rows if r["mode"] in ALL_MODES]
    if not target_rows:
        return

    labels = [r["mode"] for r in target_rows]
    rouge1 = [float(r.get("rouge1", 0.0)) for r in target_rows]
    rouge2 = [float(r.get("rouge2", 0.0)) for r in target_rows]
    rougeL = [float(r.get("rougeL", 0.0)) for r in target_rows]

    x = list(range(len(labels)))
    w = 0.24
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([i - w for i in x], rouge1, width=w, label="ROUGE-1")
    ax.bar(x, rouge2, width=w, label="ROUGE-2")
    ax.bar([i + w for i in x], rougeL, width=w, label="ROUGE-L")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20)
    ax.set_ylabel("F1")
    ax.set_title("Figure 3.7 - ROUGE Scores by Summarization Mode")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_png, dpi=220)
    plt.close(fig)


def plot_human_radar(human_rows: list[dict[str, Any]], output_png: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        logger.warning("matplotlib not installed. Skip Figure 3.6.")
        return

    if not human_rows:
        return

    labels = list(HUMAN_CRITERIA)
    n = len(labels)
    angles = [i * (2 * 3.141592653589793 / n) for i in range(n)]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"polar": True})
    for row in human_rows:
        vals = [float(row.get(c, 0.0)) for c in HUMAN_CRITERIA]
        vals += vals[:1]
        ax.plot(angles, vals, linewidth=2, label=row["mode"])
        ax.fill(angles, vals, alpha=0.08)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_title("Figure 3.6 - Human Eval Radar (1-5)")
    ax.legend(loc="upper right", bbox_to_anchor=(1.28, 1.12))
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_png, dpi=220)
    plt.close(fig)


def run_experiment(args: argparse.Namespace) -> None:
    docs = load_documents(
        processed_dir=args.input_dir,
        reference_field=args.reference_field,
        min_reference_chars=args.min_reference_chars,
        min_source_chars=args.min_source_chars,
    )
    if not docs:
        raise RuntimeError(
            f"No valid docs in '{args.input_dir}'. Need JSON with enough source text and '{args.reference_field}'."
        )

    random.seed(args.seed)
    random.shuffle(docs)
    sample_docs = docs[: args.sample_size]
    logger.info("Selected %d docs (requested=%d, total=%d)", len(sample_docs), args.sample_size, len(docs))

    rows: list[dict[str, Any]] = []
    source_map: dict[str, str] = {}

    for idx, doc in enumerate(sample_docs, start=1):
        doc_id = str(doc["doc_id"])
        payload = doc["payload"]
        source_text = str(doc["source_text"])
        reference = str(doc["reference"])
        source_map[doc_id] = source_text

        logger.info("[%d/%d] Summarizing doc_id=%s", idx, len(sample_docs), doc_id)

        summaries: dict[str, str] = {}
        summaries["baseline_extractive"] = extractive_summary(
            source_text,
            max_sentences=args.baseline_max_sentences,
            max_chars=args.baseline_max_chars,
        )
        if not args.only_baseline:
            for mode in LLM_MODES:
                summaries[mode] = summarize_llm_mode(payload, mode=mode)

        for mode, summary in summaries.items():
            rows.append(
                {
                    "doc_id": doc_id,
                    "mode": mode,
                    "reference": reference,
                    "summary": _safe_text(summary),
                    "source_chars": len(source_text),
                    "reference_chars": len(reference),
                    "summary_chars": len(_safe_text(summary)),
                    "rouge1": None,
                    "rouge2": None,
                    "rougeL": None,
                    "bertscore_f1": None,
                }
            )

    has_rouge = compute_rouge_rows(rows)
    has_bertscore = False
    if not args.skip_bertscore:
        has_bertscore = compute_bertscore_rows(
            rows,
            lang=args.bertscore_lang,
            model_type=args.bertscore_model,
            device=args.bertscore_device,
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    write_csv(
        path=output_dir / "auto_metrics_per_doc.csv",
        rows=rows,
        headers=[
            "doc_id",
            "mode",
            "source_chars",
            "reference_chars",
            "summary_chars",
            "rouge1",
            "rouge2",
            "rougeL",
            "bertscore_f1",
            "summary",
        ],
    )

    auto_agg = aggregate_auto_metrics(rows)
    table_39_rows = [r for r in auto_agg if r["mode"] in LLM_MODES]
    write_csv(
        path=output_dir / "table_3_9_auto_metrics.csv",
        rows=table_39_rows,
        headers=["mode", "n_docs", "rouge1", "rouge2", "rougeL", "bertscore_f1"],
    )
    write_markdown_table(
        path=output_dir / "table_3_9_auto_metrics.md",
        rows=table_39_rows,
        headers=["mode", "n_docs", "rouge1", "rouge2", "rougeL", "bertscore_f1"],
        title="Table 3.9 - Auto Metrics (3 Summarization Modes)",
    )

    generate_human_eval_template(output_dir / "human_eval_template.csv", [d["doc_id"] for d in sample_docs])
    human_input = args.human_eval_csv if args.human_eval_csv else (output_dir / "human_eval_filled.csv")
    human_raw = parse_human_eval(human_input)
    human_agg = aggregate_human_eval(human_raw)
    if human_agg:
        write_csv(
            path=output_dir / "table_3_10_human_eval.csv",
            rows=human_agg,
            headers=["mode", "n_ratings", "faithfulness", "coherence", "relevance", "conciseness", "overall"],
        )
        write_markdown_table(
            path=output_dir / "table_3_10_human_eval.md",
            rows=human_agg,
            headers=["mode", "n_ratings", "faithfulness", "coherence", "relevance", "conciseness", "overall"],
            title="Table 3.10 - Human Evaluation (3 Modes vs Baseline)",
        )
        plot_human_radar(human_agg, output_dir / "figure_3_6_radar_human_eval.png")
    else:
        logger.warning(
            "No valid human eval rows found. Fill '%s' and rerun to create Table 3.10 + Figure 3.6.",
            human_input,
        )

    plot_rouge_bar(auto_agg, output_dir / "figure_3_7_bar_rouge.png")
    save_case_study(output_dir / "table_3_11_case_study.md", rows, source_map, args.case_study_count)

    meta = {
        "n_total_candidate_docs": len(docs),
        "n_sampled_docs": len(sample_docs),
        "modes": list(ALL_MODES if not args.only_baseline else ("baseline_extractive",)),
        "reference_field": args.reference_field,
        "has_rouge": has_rouge,
        "has_bertscore": has_bertscore,
        "human_eval_rows": len(human_raw),
        "output_dir": str(output_dir),
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Done. Outputs saved to: %s", output_dir)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Summarization experiment runner (Table 3.9-3.11, Figure 3.6-3.7).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "data" / "processed", help="Processed JSON directory")
    p.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports" / "summary_experiment", help="Output report dir")
    p.add_argument("--sample-size", type=int, default=40, help="Number of documents to sample (target 30-50)")
    p.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    p.add_argument("--reference-field", type=str, default="abstract", help="Gold summary field in processed JSON")
    p.add_argument("--min-reference-chars", type=int, default=120, help="Minimum chars for reference summary")
    p.add_argument("--min-source-chars", type=int, default=500, help="Minimum chars for source document text")
    p.add_argument("--baseline-max-sentences", type=int, default=6, help="Extractive baseline max sentences")
    p.add_argument("--baseline-max-chars", type=int, default=900, help="Extractive baseline max chars")
    p.add_argument("--only-baseline", action="store_true", help="Run only baseline_extractive")
    p.add_argument("--skip-bertscore", action="store_true", help="Skip BERTScore computation")
    p.add_argument("--bertscore-lang", type=str, default="en", help="BERTScore language code")
    p.add_argument("--bertscore-model", type=str, default="", help="Optional model_type for BERTScore")
    p.add_argument("--bertscore-device", type=str, default="", help="Optional device for BERTScore (cpu/cuda)")
    p.add_argument("--human-eval-csv", type=Path, default=None, help="Filled human evaluation CSV path")
    p.add_argument("--case-study-count", type=int, default=3, help="Number of case-study examples")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return p


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    run_experiment(args)


if __name__ == "__main__":
    main()
