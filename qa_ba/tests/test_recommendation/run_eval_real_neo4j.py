from __future__ import annotations

import argparse
import csv
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from qa_ba.evaluation.rec_metrics import EvalCase, evaluate_cases, hit_rate_at_k, ndcg_at_k
from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig

UUID5_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
METHOD_REGEX = (
    r".*(method|model|algorithm|approach|framework|technique|pipeline|architecture|protocol).*"
)


def _resolve_paper_id(raw: dict) -> str:
    doi = (raw.get("doi") or "").strip().lower()
    if doi:
        key = f"doi:{doi}"
    else:
        title = (raw.get("title") or "").strip().lower()
        year = raw.get("year") or 0
        key = f"title:{title}:year:{year}"
    return str(uuid.uuid5(UUID5_NS, key))


def _must_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def _recommend_by_author(client: Neo4jClient, paper_id: str, top_k: int) -> list[str]:
    rows = client.execute_read(
        """
        MATCH (p:Paper {id: $pid})<-[:WROTE]-(a:Author)-[:WROTE]->(cand:Paper)
        WHERE cand.id IS NOT NULL AND cand.id <> $pid
        WITH cand, count(DISTINCT a) AS shared_author_count
        OPTIONAL MATCH (cand)-[:CITES]->(:Paper)
        WITH cand, shared_author_count, count(*) AS out_cites
        RETURN cand.id AS rid
        ORDER BY shared_author_count DESC, out_cites DESC, rid ASC
        LIMIT $k
        """,
        {"pid": paper_id, "k": top_k},
    )
    return [r["rid"] for r in rows if r.get("rid")]


def _ground_truth_same_author(client: Neo4jClient, paper_id: str) -> set[str]:
    rows = client.execute_read(
        """
        MATCH (p:Paper {id: $pid})<-[:WROTE]-(a:Author)-[:WROTE]->(o:Paper)
        WHERE o.id IS NOT NULL AND o.id <> $pid
        RETURN DISTINCT o.id AS rid
        """,
        {"pid": paper_id},
    )
    return {r["rid"] for r in rows if r.get("rid")}


def _recommend_by_method(client: Neo4jClient, paper_id: str, top_k: int) -> list[str]:
    rows = client.execute_read(
        """
        MATCH (p:Paper {id: $pid})-[:USES_CONCEPT|HAS_TOPIC]->(m:Concept)
        WHERE m.name IS NOT NULL AND toLower(m.name) =~ $method_regex
        MATCH (cand:Paper)-[:USES_CONCEPT|HAS_TOPIC]->(m)
        WHERE cand.id IS NOT NULL AND cand.id <> $pid
        WITH cand, count(DISTINCT m) AS shared_method_count
        OPTIONAL MATCH (cand)-[:CITES]->(:Paper)
        WITH cand, shared_method_count, count(*) AS out_cites
        RETURN cand.id AS rid
        ORDER BY shared_method_count DESC, out_cites DESC, rid ASC
        LIMIT $k
        """,
        {"pid": paper_id, "k": top_k, "method_regex": METHOD_REGEX},
    )
    return [r["rid"] for r in rows if r.get("rid")]


def _ground_truth_same_method(client: Neo4jClient, paper_id: str) -> set[str]:
    rows = client.execute_read(
        """
        MATCH (p:Paper {id: $pid})-[:USES_CONCEPT|HAS_TOPIC]->(m:Concept)
        WHERE m.name IS NOT NULL AND toLower(m.name) =~ $method_regex
        MATCH (o:Paper)-[:USES_CONCEPT|HAS_TOPIC]->(m)
        WHERE o.id IS NOT NULL AND o.id <> $pid
        RETURN DISTINCT o.id AS rid
        """,
        {"pid": paper_id, "method_regex": METHOD_REGEX},
    )
    return {r["rid"] for r in rows if r.get("rid")}


def _ground_truth_cites(client: Neo4jClient, paper_id: str) -> set[str]:
    rows = client.execute_read(
        """
        MATCH (p:Paper {id: $pid})-[:CITES]->(o:Paper)
        WHERE o.id IS NOT NULL AND o.id <> $pid
        RETURN DISTINCT o.id AS rid
        """,
        {"pid": paper_id},
    )
    return {r["rid"] for r in rows if r.get("rid")}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real recommendation evaluation against Neo4j Aura."
    )
    parser.add_argument(
        "--data-dir",
        default=r"D:\Project\Datamining\project\ai-paper-system-root\data\processed\KHKT&CN",
        help="Directory containing processed JSON files.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Number of JSON files to evaluate.")
    parser.add_argument(
        "--output-dir",
        default=r"D:\Project\Datamining\project\ai-paper-system-root\qa_ba\tests\test_recommendation",
        help="Directory to write output CSV files.",
    )
    parser.add_argument("--top-k", type=int, default=10, help="Top-k recommendations.")
    parser.add_argument(
        "--recommend-mode",
        choices=["by-author", "by-method"],
        default="by-author",
        help="Recommendation mode.",
    )
    parser.add_argument(
        "--ground-truth",
        choices=["auto", "cites", "same-author", "same-method-concept"],
        default="auto",
        help="Ground-truth protocol. 'auto' maps to same-author or same-method-concept by mode.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(data_dir.glob("*.json"))[: args.limit]
    if not json_files:
        raise RuntimeError(f"No JSON files found in: {data_dir}")

    neo_cfg = Neo4jConfig(
        uri=_must_env("NEO4J_URI"),
        username=_must_env("NEO4J_USER"),
        password=_must_env("NEO4J_PASSWORD"),
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )

    client = Neo4jClient(neo_cfg)
    client.connect()

    cases: list[EvalCase] = []
    detail_rows: list[dict] = []
    resolved_ground_truth = (
        "same-author" if args.recommend_mode == "by-author" else "same-method-concept"
    )
    if args.ground_truth != "auto":
        resolved_ground_truth = args.ground_truth

    try:
        for fp in json_files:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            doc_id = raw.get("doc_id") or fp.stem
            paper_id = _resolve_paper_id(raw)

            if args.recommend_mode == "by-author":
                rec_ids = _recommend_by_author(client, paper_id, args.top_k)
            else:
                rec_ids = _recommend_by_method(client, paper_id, args.top_k)

            if resolved_ground_truth == "cites":
                relevant = _ground_truth_cites(client, paper_id)
            elif resolved_ground_truth == "same-author":
                relevant = _ground_truth_same_author(client, paper_id)
            else:
                relevant = _ground_truth_same_method(client, paper_id)

            if not relevant:
                detail_rows.append(
                    {
                        "doc_id": doc_id,
                        "paper_id": paper_id,
                        "recommend_mode": args.recommend_mode,
                        "ground_truth": resolved_ground_truth,
                        "status": "skip_no_ground_truth",
                        "num_relevant": 0,
                        "num_recommended": 0,
                        "hit@1": 0.0,
                        "hit@5": 0.0,
                        "hit@10": 0.0,
                        "ndcg@5": 0.0,
                        "ndcg@10": 0.0,
                        "topk": "",
                    }
                )
                continue

            cases.append(EvalCase(recommended_ids=rec_ids, relevant_ids=relevant, history_len=1))
            detail_rows.append(
                {
                    "doc_id": doc_id,
                    "paper_id": paper_id,
                    "recommend_mode": args.recommend_mode,
                    "ground_truth": resolved_ground_truth,
                    "status": "ok",
                    "num_relevant": len(relevant),
                    "num_recommended": len(rec_ids),
                    "hit@1": hit_rate_at_k(rec_ids, relevant, 1),
                    "hit@5": hit_rate_at_k(rec_ids, relevant, 5),
                    "hit@10": hit_rate_at_k(rec_ids, relevant, 10),
                    "ndcg@5": ndcg_at_k(rec_ids, relevant, 5),
                    "ndcg@10": ndcg_at_k(rec_ids, relevant, 10),
                    "topk": "|".join(rec_ids),
                }
            )
    finally:
        client.close()

    summary = evaluate_cases(cases)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_path = out_dir / f"recommendation_eval_detail_{ts}.csv"
    summary_path = out_dir / f"recommendation_eval_summary_{ts}.csv"
    detail_latest = out_dir / "recommendation_eval_detail_latest.csv"
    summary_latest = out_dir / "recommendation_eval_summary_latest.csv"

    detail_fields = [
        "doc_id",
        "paper_id",
        "recommend_mode",
        "ground_truth",
        "status",
        "num_relevant",
        "num_recommended",
        "hit@1",
        "hit@5",
        "hit@10",
        "ndcg@5",
        "ndcg@10",
        "topk",
    ]
    for target in (detail_path, detail_latest):
        with target.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=detail_fields)
            w.writeheader()
            w.writerows(detail_rows)

    for target in (summary_path, summary_latest):
        with target.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            w.writerow(["recommend_mode", args.recommend_mode])
            w.writerow(["ground_truth", resolved_ground_truth])
            for k, v in summary.items():
                w.writerow([k, v])

    print("TOTAL_INPUT_FILES", len(json_files))
    print("RECOMMEND_MODE", args.recommend_mode)
    print("GROUND_TRUTH", resolved_ground_truth)
    print("EVAL_CASES", len(cases))
    print("SKIPPED", len(json_files) - len(cases))
    for k, v in summary.items():
        print(k, v)
    print("DETAIL_CSV", detail_path)
    print("SUMMARY_CSV", summary_path)
    print("DETAIL_LATEST", detail_latest)
    print("SUMMARY_LATEST", summary_latest)


if __name__ == "__main__":
    main()
