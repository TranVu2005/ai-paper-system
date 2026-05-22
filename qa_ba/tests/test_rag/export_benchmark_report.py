from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import pandas as pd

TABLE_FILENAME_MAP = {
    "table_3_6_main_metrics": "t36_main",
    "table_3_7_hallucination": "t37_hallucination",
    "table_3_8_by_question_type": "t38_by_qtype",
    "table_answerability_overall": "t_answerability",
    "table_ops_latency_token_cost": "t_ops",
    "table_module_metrics": "t_module_metrics",
    "table_module_answerability": "t_module_answerability",
    "table_module_ops": "t_module_ops",
}

CHART_FILENAME_MAP = {
    "chart_3_3_grouped_bar_precision_k": "c33_precision_k",
    "chart_3_4_line_precision_recall_tradeoff": "c34_pr_tradeoff",
    "chart_3_5_hallucination_distribution": "c35_hallucination",
    "chart_answerability_by_question_type": "c_answerability_qtype",
    "chart_hallucination_scores": "c_hallucination_scores",
    "chart_latency_by_system_boxplot": "c_latency_by_system_boxplot",
    "chart_latency_by_domain_boxplot": "c_latency_by_domain_boxplot",
    "chart_latency_by_qtype_boxplot": "c_latency_by_qtype_boxplot",
}

GOLD_REQUIRED_TABLES = {
    "table_3_6_main_metrics",
    "table_3_8_by_question_type",
    "table_module_metrics",
}

GOLD_REQUIRED_CHARTS = {
    "chart_3_3_grouped_bar_precision_k",
    "chart_3_4_line_precision_recall_tradeoff",
}


def _safe_name(name: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (name or ""))
    return out.strip("_") or "table"


def _flatten_dict(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in data.items():
        col = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_dict(value, prefix=col))
        elif isinstance(value, list):
            out[col] = json.dumps(value, ensure_ascii=False)
        else:
            out[col] = value
    return out


def _coerce_float_list(values: List[Any]) -> List[float]:
    out: List[float] = []
    for v in values:
        if v is None:
            out.append(0.0)
            continue
        try:
            out.append(float(v))
        except Exception:
            out.append(0.0)
    return out


def _short_col_name(col: str) -> str:
    name = str(col)
    exact = {
        "system": "system",
        "label": "name",
        "module": "module",
        "question_type": "qtype",
        "eval_size": "n",
        "precision.@1": "p1",
        "precision.@3": "p3",
        "precision.@5": "p5",
        "recall@5": "r5",
        "mrr": "mrr",
    }
    if name in exact:
        return exact[name]

    for src, dst in (
        ("systems.baseline_dense.", "dense_"),
        ("systems.baseline_sparse_bm25.", "bm25_"),
        ("systems.proposed_rag_kg_hybrid.", "hybrid_"),
        ("counts.", "cnt_"),
        ("rates.", "rate_"),
        ("latency_ms.", "lat_"),
        ("tokens.", "tok_"),
        ("cost.", "cost_"),
        ("eval_size", "n"),
        ("label", "name"),
        ("estimated_", ""),
        ("retrieval_", "ret_"),
        ("generation_", "gen_"),
        ("total_", "tot_"),
        ("precision.@1", "p1"),
        ("precision.@3", "p3"),
        ("precision.@5", "p5"),
        ("recall@5", "r5"),
    ):
        name = name.replace(src, dst)

    for src, dst in (
        ("rate_answered_rate", "ans_rate"),
        ("rate_not_answered_rate", "na_rate"),
        ("rate_empty_rate", "empty_rate"),
        ("rate_cited_correct_rate", "cite_ok_rate"),
        ("rate_cited_wrong_rate", "cite_wrong_rate"),
        ("rate_no_source_rate", "no_src_rate"),
    ):
        name = name.replace(src, dst)

    name = name.replace(".", "_")
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_")


def _compact_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_short_col_name(c) for c in df.columns]
    return df


def _has_gold_annotations(payload: Dict[str, Any]) -> bool:
    results = payload.get("results") or []
    for row in results:
        if not isinstance(row, dict):
            continue
        gold_sources = row.get("gold_sources")
        gold_answer = row.get("gold_answer")

        if isinstance(gold_sources, list) and any(str(x).strip() for x in gold_sources):
            return True
        if isinstance(gold_sources, str) and gold_sources.strip():
            return True
        if isinstance(gold_answer, str) and gold_answer.strip():
            return True
    return False


def _drop_gold_dependent_columns(df: pd.DataFrame, has_gold: bool) -> pd.DataFrame:
    if has_gold:
        return df

    metric_exact = {"p1", "p3", "p5", "r5", "mrr"}
    metric_suffixes = ("_p1", "_p3", "_p5", "_r5", "_mrr")
    keep_cols: List[str] = []
    for col in df.columns:
        c = str(col)
        if c in metric_exact:
            continue
        if any(c.endswith(suf) for suf in metric_suffixes):
            continue
        keep_cols.append(c)
    return df[keep_cols]


def _humanize_columns(df: pd.DataFrame) -> pd.DataFrame:
    alias = {
        "n": "sample_count",
        "cnt_cited_correct": "cited_correct_count",
        "cnt_cited_wrong": "cited_wrong_count",
        "cnt_no_source": "no_source_count",
        "cite_ok_rate": "cited_correct_rate",
        "cite_wrong_rate": "cited_wrong_rate",
        "no_src_rate": "no_source_rate",
        "cnt_answered": "answered_count",
        "cnt_not_answered": "not_answered_count",
        "cnt_empty": "empty_count",
        "ans_rate": "answered_rate",
        "na_rate": "not_answered_rate",
        "empty_rate": "empty_answer_rate",
        "name": "system_name",
        "qtype": "question_type",
    }
    renamed = [alias.get(str(c), str(c)) for c in df.columns]
    out = df.copy()
    out.columns = renamed
    return out


def _rows_from_table(name: str, table_obj: Any) -> List[Dict[str, Any]]:
    if isinstance(table_obj, list):
        rows: List[Dict[str, Any]] = []
        for i, item in enumerate(table_obj):
            if isinstance(item, dict):
                rows.append(_flatten_dict(item))
            else:
                rows.append({"index": i, "value": item})
        return rows

    # Special case: table_3_8_by_question_type is dict[qtype][system]...
    if name == "table_3_8_by_question_type" and isinstance(table_obj, dict):
        rows = []
        for qtype, systems in table_obj.items():
            if not isinstance(systems, dict):
                continue
            for system, metrics in systems.items():
                base = {"question_type": qtype, "system": system}
                if isinstance(metrics, dict):
                    base.update(_flatten_dict(metrics))
                rows.append(base)
        return rows

    # Generic dict fallback: one row per key
    if isinstance(table_obj, dict):
        rows = []
        for k, v in table_obj.items():
            if isinstance(v, dict):
                row = {"key": k}
                row.update(_flatten_dict(v))
                rows.append(row)
            else:
                rows.append({"key": k, "value": v})
        return rows

    return [{"value": table_obj}]


def _plot_grouped_bar_precision(chart: Dict[str, Any], out_png: Path) -> None:
    k_values = chart.get("k_values") or []
    series = chart.get("series") or []
    if not k_values or not series:
        return

    x = list(range(len(k_values)))
    width = 0.8 / max(1, len(series))
    plt.figure(figsize=(10, 5))
    for i, s in enumerate(series):
        vals = _coerce_float_list(s.get("precision_values") or [])
        offset = [(xi - 0.4 + width / 2.0 + i * width) for xi in x]
        plt.bar(offset, vals, width=width, label=s.get("label") or s.get("system"))
    plt.xticks(x, [f"@{k}" for k in k_values])
    plt.ylabel("Precision")
    plt.title("Precision@k by System")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_precision_recall_tradeoff(chart: Dict[str, Any], out_png: Path) -> None:
    k_values = chart.get("x_k") or []
    series = chart.get("series") or []
    if not k_values or not series:
        return

    plt.figure(figsize=(10, 5))
    for s in series:
        p_vals = _coerce_float_list(s.get("precision") or [])
        r_vals = _coerce_float_list(s.get("recall") or [])
        label = s.get("label") or s.get("system")
        plt.plot(k_values, p_vals, marker="o", linestyle="-", label=f"{label} - Precision")
        plt.plot(k_values, r_vals, marker="x", linestyle="--", label=f"{label} - Recall")
    plt.xlabel("k")
    plt.ylabel("Score")
    plt.title("Precision/Recall Tradeoff")
    plt.legend(fontsize=8, ncol=2)
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_hallucination(chart: Dict[str, Any], out_png: Path) -> None:
    series = chart.get("series") or []
    if not series:
        return

    labels = [s.get("label") or s.get("system") for s in series]
    x = list(range(len(labels)))
    c_correct = [float((s.get("counts") or {}).get("cited_correct") or 0.0) for s in series]
    c_wrong = [float((s.get("counts") or {}).get("cited_wrong") or 0.0) for s in series]
    c_none = [float((s.get("counts") or {}).get("no_source") or 0.0) for s in series]

    plt.figure(figsize=(10, 5))
    plt.bar(x, c_correct, label="cited_correct")
    plt.bar(x, c_wrong, bottom=c_correct, label="cited_wrong")
    bottom2 = [a + b for a, b in zip(c_correct, c_wrong)]
    plt.bar(x, c_none, bottom=bottom2, label="no_source")
    plt.xticks(x, labels, rotation=10)
    plt.ylabel("Count")
    plt.title("Hallucination Distribution")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_answerability(chart: Dict[str, Any], out_png: Path) -> None:
    qtypes = chart.get("question_types") or []
    series = chart.get("series") or []
    if not qtypes or not series:
        return

    x = list(range(len(qtypes)))
    width = 0.8 / max(1, len(series))
    plt.figure(figsize=(10, 5))
    for i, s in enumerate(series):
        vals = _coerce_float_list(s.get("answered_rate") or [])
        offset = [(xi - 0.4 + width / 2.0 + i * width) for xi in x]
        plt.bar(offset, vals, width=width, label=s.get("label") or s.get("system"))
    plt.xticks(x, qtypes)
    plt.ylabel("Answered rate")
    plt.ylim(0, 1.05)
    plt.title("Answerability by Question Type")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_latency(chart: Dict[str, Any], out_png: Path) -> None:
    series = chart.get("series") or []
    if not series:
        return
    labels = [s.get("label") or s.get("system") for s in series]
    p50 = [float(s.get("total_latency_p50_ms") or 0.0) for s in series]
    p95 = [float(s.get("total_latency_p95_ms") or 0.0) for s in series]

    x = list(range(len(labels)))
    width = 0.35
    plt.figure(figsize=(10, 5))
    plt.bar([xi - width / 2 for xi in x], p50, width=width, label="p50")
    plt.bar([xi + width / 2 for xi in x], p95, width=width, label="p95")
    plt.xticks(x, labels, rotation=10)
    plt.ylabel("Latency (ms)")
    plt.title("Total Latency p50/p95")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_hallucination_scores_from_table(table_rows: List[Dict[str, Any]], out_png: Path) -> None:
    if not table_rows:
        return

    labels: List[str] = []
    grounded: List[float] = []
    hallu: List[float] = []
    for r in table_rows:
        if not isinstance(r, dict):
            continue
        label = str(r.get("label") or r.get("system") or "").strip()
        if not label:
            continue
        scores = r.get("scores") if isinstance(r.get("scores"), dict) else {}
        g = scores.get("groundedness_score")
        h = scores.get("hallucination_score")
        if g is None or h is None:
            continue
        try:
            gv = float(g)
            hv = float(h)
        except Exception:
            continue
        labels.append(label)
        grounded.append(gv)
        hallu.append(hv)

    if not labels:
        return

    x = list(range(len(labels)))
    width = 0.35
    plt.figure(figsize=(10, 5))
    plt.bar([xi - width / 2 for xi in x], grounded, width=width, label="groundedness_score")
    plt.bar([xi + width / 2 for xi in x], hallu, width=width, label="hallucination_score")
    plt.axhline(0.0, color="#888888", linewidth=1)
    plt.xticks(x, labels, rotation=10)
    plt.ylim(-1.0, 1.05)
    plt.ylabel("Score")
    plt.title("Hallucination Scores by System")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def _plot_latency_boxplot_from_results(
    results: List[Dict[str, Any]],
    group_key: str,
    out_png: Path,
    title: str,
    system_filter: str | None = None,
) -> None:
    grouped: Dict[str, List[float]] = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        if system_filter and row.get("system") != system_filter:
            continue
        group = str(row.get(group_key) or "").strip()
        if not group:
            continue
        try:
            latency = float(row.get("total_latency_ms") or 0.0)
        except Exception:
            continue
        if latency <= 0:
            continue
        grouped.setdefault(group, []).append(latency)

    grouped = {k: v for k, v in grouped.items() if v}
    if not grouped:
        return

    labels = sorted(grouped.keys())
    data = [grouped[label] for label in labels]
    plt.figure(figsize=(max(10, len(labels) * 1.25), 5.5))
    plt.boxplot(data, tick_labels=labels, showfliers=True, patch_artist=True)
    ax = plt.gca()
    for patch in ax.artists:
        patch.set_facecolor("#9bb8d8")
        patch.set_alpha(0.85)
    plt.xticks(rotation=20, ha="right")
    plt.ylabel("Latency (ms)")
    plt.title(title)
    plt.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_png, dpi=140)
    plt.close()


def export_tables(payload: Dict[str, Any], out_tables_dir: Path) -> List[Path]:
    out_tables_dir.mkdir(parents=True, exist_ok=True)
    for old_csv in out_tables_dir.glob("*.csv"):
        try:
            old_csv.unlink()
        except Exception:
            pass
    old_xlsx = out_tables_dir / "tables_bundle.xlsx"
    if old_xlsx.exists():
        try:
            old_xlsx.unlink()
        except Exception:
            pass

    tables = payload.get("tables") or {}
    has_gold = _has_gold_annotations(payload)
    written: List[Path] = []

    for name, obj in tables.items():
        if (not has_gold) and (name in GOLD_REQUIRED_TABLES):
            continue
        rows = _rows_from_table(name, obj)
        df = _compact_columns(pd.DataFrame(rows))
        df = _drop_gold_dependent_columns(df, has_gold=has_gold)
        df = _humanize_columns(df)
        out_base = TABLE_FILENAME_MAP.get(name, _safe_name(name))
        out_csv = out_tables_dir / f"{_safe_name(out_base)}.csv"
        df.to_csv(out_csv, index=False, encoding="utf-8-sig")
        written.append(out_csv)

    # Optional Excel bundle if openpyxl exists.
    try:
        import openpyxl  # noqa: F401

        out_xlsx = out_tables_dir / "tables_bundle.xlsx"
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
            for name, obj in tables.items():
                if (not has_gold) and (name in GOLD_REQUIRED_TABLES):
                    continue
                rows = _rows_from_table(name, obj)
                df = _compact_columns(pd.DataFrame(rows))
                df = _drop_gold_dependent_columns(df, has_gold=has_gold)
                df = _humanize_columns(df)
                sheet = TABLE_FILENAME_MAP.get(name, _safe_name(name))
                df.to_excel(writer, sheet_name=_safe_name(sheet)[:31], index=False)
        written.append(out_xlsx)
    except Exception:
        pass

    return written


def export_charts(payload: Dict[str, Any], out_charts_dir: Path) -> List[Path]:
    out_charts_dir.mkdir(parents=True, exist_ok=True)
    for old_png in out_charts_dir.glob("*.png"):
        try:
            old_png.unlink()
        except Exception:
            pass

    charts = payload.get("charts") or {}
    has_gold = _has_gold_annotations(payload)
    written: List[Path] = []

    plotters = {
        "chart_3_3_grouped_bar_precision_k": _plot_grouped_bar_precision,
        "chart_3_4_line_precision_recall_tradeoff": _plot_precision_recall_tradeoff,
        "chart_3_5_hallucination_distribution": _plot_hallucination,
        "chart_answerability_by_question_type": _plot_answerability,
    }

    for key, plot_fn in plotters.items():
        if (not has_gold) and (key in GOLD_REQUIRED_CHARTS):
            continue
        obj = charts.get(key)
        if not isinstance(obj, dict):
            continue
        out_base = CHART_FILENAME_MAP.get(key, _safe_name(key))
        out_png = out_charts_dir / f"{_safe_name(out_base)}.png"
        try:
            plot_fn(obj, out_png)
            if out_png.exists():
                written.append(out_png)
        except Exception:
            # skip failed chart but continue others
            continue

    # Derived chart from hallucination table scores (no benchmark rerun required).
    table_37 = payload.get("tables", {}).get("table_3_7_hallucination")
    if isinstance(table_37, list):
        out_base = CHART_FILENAME_MAP.get("chart_hallucination_scores", "chart_hallucination_scores")
        out_png = out_charts_dir / f"{_safe_name(out_base)}.png"
        try:
            _plot_hallucination_scores_from_table(table_37, out_png)
            if out_png.exists():
                written.append(out_png)
        except Exception:
            pass

    results = payload.get("results") or []
    derived_latency_charts = [
        (
            "chart_latency_by_system_boxplot",
            "system",
            "Latency Distribution by System",
            None,
        ),
        (
            "chart_latency_by_domain_boxplot",
            "module",
            "Latency Distribution by Domain (Dense + KG)",
            "proposed_rag_kg_hybrid",
        ),
        (
            "chart_latency_by_qtype_boxplot",
            "question_type",
            "Latency Distribution by Question Type (Dense + KG)",
            "proposed_rag_kg_hybrid",
        ),
    ]
    if isinstance(results, list):
        for key, group_key, title, system_filter in derived_latency_charts:
            out_base = CHART_FILENAME_MAP.get(key, key)
            out_png = out_charts_dir / f"{_safe_name(out_base)}.png"
            try:
                _plot_latency_boxplot_from_results(
                    results,
                    group_key=group_key,
                    out_png=out_png,
                    title=title,
                    system_filter=system_filter,
                )
                if out_png.exists():
                    written.append(out_png)
            except Exception:
                pass

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Export benchmark JSON to CSV tables and PNG charts.")
    parser.add_argument("--input-json", type=Path, required=True, help="Benchmark output JSON.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output report folder (default: sibling folder named report_<json_stem>).",
    )
    args = parser.parse_args()

    if not args.input_json.exists():
        raise FileNotFoundError(f"input json not found: {args.input_json}")

    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    out_dir = args.out_dir or (args.input_json.parent / f"report_{args.input_json.stem}")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_tables_dir = out_dir / "tables"
    out_charts_dir = out_dir / "charts"

    table_files = export_tables(payload, out_tables_dir)
    chart_files = export_charts(payload, out_charts_dir)

    summary = {
        "input_json": str(args.input_json),
        "out_dir": str(out_dir),
        "num_tables_files": len(table_files),
        "num_chart_files": len(chart_files),
        "table_files": [str(x) for x in table_files],
        "chart_files": [str(x) for x in chart_files],
    }
    (out_dir / "export_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Export completed.")
    print(f"- input: {args.input_json}")
    print(f"- out_dir: {out_dir}")
    print(f"- table_files: {len(table_files)}")
    print(f"- chart_files: {len(chart_files)}")


if __name__ == "__main__":
    main()
