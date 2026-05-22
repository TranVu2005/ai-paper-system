#!/usr/bin/env python3
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "backend" / ".env"
OUTPUT_JSON = ROOT / "docs" / "database_audit.json"
CODE_DIRS = [
    ROOT / "backend",
    ROOT / "serving",
    ROOT / "ai_module",
    ROOT / "frontend",
    ROOT / "scripts",
    ROOT / "migrations",
    ROOT / "storage" / "schemas",
]
EXTRACTION_KEYWORDS = {
    "authors",
    "topics",
    "entities",
    "relations",
    "figures",
    "tables",
    "formulas",
    "chunks",
    "summaries",
    "qa_history",
    "recommendations",
    "document_metadata",
}


def load_database_url() -> str:
    if not ENV_PATH.exists():
        raise RuntimeError(f"Missing env file: {ENV_PATH}")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not found in backend/.env")


def run_psql(url: str, sql: str) -> list[list[str]]:
    cmd = ["psql", url, "-At", "-F", "\t", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"psql failed ({res.returncode}): {res.stderr.strip()}")
    rows: list[list[str]] = []
    for line in res.stdout.splitlines():
        rows.append(line.split("\t"))
    return rows


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def code_usage_count(pattern: str) -> int:
    search_paths = [str(p) for p in CODE_DIRS if p.exists()]
    if not search_paths:
        return 0
    cmd = ["rg", "-n", "--no-ignore", "-S", pattern, *search_paths]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        return 0
    if res.returncode == 1:
        return 0
    return len([line for line in res.stdout.splitlines() if line.strip()])


def clean_sample_value(v: str, max_len: int = 120) -> str:
    s = (v or "").replace("\n", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def main() -> None:
    url = load_database_url()

    table_rows = run_psql(
        url,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'
        ORDER BY table_name;
        """,
    )
    table_names = [r[0] for r in table_rows]

    fk_rows = run_psql(
        url,
        """
        SELECT tc.table_name, COUNT(*)
        FROM information_schema.table_constraints tc
        WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
        GROUP BY tc.table_name;
        """,
    )
    fk_map = {r[0]: int(r[1]) for r in fk_rows}

    fk_ref_rows = run_psql(
        url,
        """
        SELECT ccu.table_name, COUNT(*)
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
        GROUP BY ccu.table_name;
        """,
    )
    fk_ref_map = {r[0]: int(r[1]) for r in fk_ref_rows}

    column_rows = run_psql(
        url,
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema='public'
        ORDER BY table_name, ordinal_position;
        """,
    )

    columns_by_table: dict[str, list[dict[str, Any]]] = {}
    for t, c, dt in column_rows:
        columns_by_table.setdefault(t, []).append({"column": c, "data_type": dt})

    report: dict[str, Any] = {
        "database_url_host": re.sub(r"^.*@", "", url).split("/")[0] if "@" in url else "",
        "tables": [],
        "summary": {},
        "extraction_findings": [],
    }

    null_100 = 0
    null_95 = 0
    keep_count = 0
    drop_count = 0
    review_count = 0

    for table in table_names:
        q_table = quote_ident(table)
        row_count = int(run_psql(url, f"SELECT COUNT(*) FROM public.{q_table};")[0][0])
        col_defs = columns_by_table.get(table, [])
        column_count = len(col_defs)
        table_usage_hits = code_usage_count(rf"\b{re.escape(table)}\b")
        table_used = table_usage_hits > 0
        fk_out = fk_map.get(table, 0)
        fk_in = fk_ref_map.get(table, 0)
        fk_related = (fk_out + fk_in) > 0

        if row_count == 0 and not table_used and not fk_related:
            table_reco = "candidate_drop"
            drop_count += 1
        elif table_used or fk_related or row_count > 0:
            table_reco = "keep" if (table_used and (fk_related or row_count > 0)) else "candidate_review"
            if table_reco == "keep":
                keep_count += 1
            else:
                review_count += 1
        else:
            table_reco = "candidate_review"
            review_count += 1

        table_obj: dict[str, Any] = {
            "table_name": table,
            "row_count": row_count,
            "column_count": column_count,
            "fk_out_count": fk_out,
            "fk_in_count": fk_in,
            "fk_related": fk_related,
            "used_in_code": table_used,
            "code_usage_hits": table_usage_hits,
            "recommendation": table_reco,
            "columns": [],
        }

        for col in col_defs:
            column = col["column"]
            q_col = quote_ident(column)
            stats = run_psql(
                url,
                f"""
                SELECT
                  COUNT(*)::bigint AS total_rows,
                  COUNT(*) FILTER (WHERE {q_col} IS NULL)::bigint AS null_count,
                  COUNT(DISTINCT ({q_col}::text)) FILTER (WHERE {q_col} IS NOT NULL)::bigint AS distinct_count
                FROM public.{q_table};
                """,
            )[0]
            total_rows = int(stats[0])
            null_count = int(stats[1])
            distinct_count = int(stats[2])
            null_pct = (null_count / total_rows * 100.0) if total_rows > 0 else 0.0

            sample_sql = f"""
                SELECT {q_col}::text
                FROM public.{q_table}
                WHERE {q_col} IS NOT NULL
                LIMIT 3;
            """
            sample_vals_rows = run_psql(url, sample_sql)
            sample_vals = [clean_sample_value(r[0]) for r in sample_vals_rows if r]

            col_usage_hits = code_usage_count(rf"\b{re.escape(column)}\b")
            col_used = col_usage_hits > 0

            if total_rows == 0:
                col_reco = "candidate_review"
            elif null_pct == 100.0 and not col_used:
                col_reco = "candidate_column_drop"
                null_100 += 1
            elif null_pct >= 95.0 and not col_used:
                col_reco = "candidate_column_review"
                null_95 += 1
            elif not col_used and distinct_count <= 1:
                col_reco = "candidate_column_review"
            else:
                col_reco = "keep"

            table_obj["columns"].append(
                {
                    "column_name": column,
                    "data_type": col["data_type"],
                    "total_rows": total_rows,
                    "null_count": null_count,
                    "null_pct": round(null_pct, 2),
                    "distinct_count": distinct_count,
                    "used_in_code": col_used,
                    "code_usage_hits": col_usage_hits,
                    "sample_values": sample_vals,
                    "recommendation": col_reco,
                }
            )

        if any(key in table.lower() for key in EXTRACTION_KEYWORDS):
            note = {
                "table": table,
                "row_count": row_count,
                "recommendation": "candidate_disable" if row_count == 0 else "review_data_quality",
                "notes": [],
            }
            if row_count == 0:
                note["notes"].append("Bảng extraction đang rỗng.")
            # Quality check for known noisy author/topic phrases
            for c in table_obj["columns"]:
                if c["column_name"].lower() in {"author", "authors", "topic", "topics", "value", "name"}:
                    noisy = [v for v in c["sample_values"] if any(x in v.lower() for x in ["lấy chủ", "trong đó thường", "có thể"])]
                    if noisy:
                        note["notes"].append(f"Dữ liệu có dấu hiệu nhiễu ở cột {c['column_name']}: {noisy}")
            if not note["notes"]:
                note["notes"].append("Không phát hiện bất thường rõ ràng từ sample nhỏ.")
            report["extraction_findings"].append(note)

        report["tables"].append(table_obj)

    total_tables = len(report["tables"])
    report["summary"] = {
        "total_tables": total_tables,
        "keep_tables": keep_count,
        "candidate_drop_tables": drop_count,
        "candidate_review_tables": review_count,
        "columns_null_100": null_100,
        "columns_null_gt95": null_95,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Neon DB Audit Summary ===")
    print(f"Total tables: {total_tables}")
    print(f"Keep tables: {keep_count}")
    print(f"Candidate drop tables: {drop_count}")
    print(f"Candidate review tables: {review_count}")
    print(f"Columns 100% NULL: {null_100}")
    print(f"Columns >95% NULL: {null_95}")
    print(f"Saved JSON: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
