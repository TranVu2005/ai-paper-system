from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Dict, List

DEMO_SAMPLES = [
    {
        "query": "Bai bao de xuat phuong phap nao cho tom tat khoa hoc?",
        "positives": [
            "Bai bao de xuat mo hinh transformer ket hop retrieval de tom tat tai lieu khoa hoc.",
            "Phuong phap chinh la RAG voi bo nho ngoai va bo loc ngu canh.",
        ],
        "negatives": [
            "He thong du bao gia co phieu theo du lieu thi truong.",
            "Nghien cuu ve phan loai anh y te bang CNN.",
        ],
    },
    {
        "query": "Bo du lieu nao duoc dung de danh gia?",
        "positives": [
            "Tac gia danh gia tren arXiv summarization subset va PubMedQA.",
            "Thi nghiem su dung bo du lieu khoa hoc da gan nhan cau hoi dap.",
        ],
        "negatives": [
            "Bai viet mo ta quy trinh bao tri he thong ERP.",
            "Khao sat hanh vi nguoi dung mang xa hoi.",
        ],
    },
    {
        "query": "Ket qua co cai thien metric khong?",
        "positives": [
            "Ket qua cho thay ROUGE-L tang 3.8 diem so voi baseline.",
            "Mo hinh moi vuot baseline ve F1 va do phu thong tin.",
        ],
        "negatives": [
            "Nghien cuu khong bao cao metric, chi mo ta kien truc.",
            "Bai toan du bao thoi tiet khong lien quan den ROUGE.",
        ],
    },
]


def _split_records(records: List[Dict[str, object]], seed: int) -> Dict[str, List[Dict[str, object]]]:
    rnd = random.Random(seed)
    rows = records[:]
    rnd.shuffle(rows)

    n = len(rows)
    n_train = max(1, int(n * 0.7))
    n_val = max(1, int(n * 0.15))

    train = rows[:n_train]
    val = rows[n_train : n_train + n_val]
    test = rows[n_train + n_val :]
    if not test:
        test = rows[-1:]
    return {"train": train, "val": val, "test": test}


def build_demo_records() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for sample_id, sample in enumerate(DEMO_SAMPLES):
        query = sample["query"]
        for text in sample["positives"]:
            rows.append(
                {"query": query, "text": text, "label": 1, "base_score": 0.65, "group_id": f"q_{sample_id}"}
            )
        for text in sample["negatives"]:
            rows.append(
                {"query": query, "text": text, "label": 0, "base_score": 0.35, "group_id": f"q_{sample_id}"}
            )
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare rerank train/val/test pairs")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--input-jsonl", default="", help="Optional pre-labeled pairs jsonl")
    args = parser.parse_args()

    if args.input_jsonl:
        inp = Path(args.input_jsonl)
        if not inp.exists():
            raise FileNotFoundError(f"Input jsonl not found: {inp}")
        rows = [json.loads(line) for line in inp.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = build_demo_records()

    parts = _split_records(rows, seed=args.seed)
    out_dir = Path(args.output_dir)
    write_jsonl(out_dir / "train.jsonl", parts["train"])
    write_jsonl(out_dir / "val.jsonl", parts["val"])
    write_jsonl(out_dir / "test.jsonl", parts["test"])

    print(json.dumps({"status": "ok", "output_dir": str(out_dir), "train": len(parts["train"]), "val": len(parts["val"]), "test": len(parts["test"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
