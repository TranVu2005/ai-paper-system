from __future__ import annotations

import json
from pathlib import Path

from ai_module.rerank.inference import RerankInference

ROOT = Path(__file__).resolve().parent
CKPT = ROOT / "models" / "rerank" / "mlp_v1" / "rerank_mlp.pt"


def main() -> None:
    if not CKPT.exists():
        raise FileNotFoundError(f"Checkpoint not found: {CKPT}")

    query = "Bai bao de xuat phuong phap nao cho tom tat khoa hoc?"
    candidates = [
        {"id": "c1", "text": "Nghien cuu de xuat mo hinh transformer ket hop retrieval de tom tat khoa hoc.", "base_score": 0.65},
        {"id": "c2", "text": "Bai viet ve du bao gia co phieu theo chuoi thoi gian.", "base_score": 0.40},
        {"id": "c3", "text": "He thong RAG voi bo loc ngu canh va memory ngoai cho bai toan tom tat.", "base_score": 0.60},
    ]

    infer = RerankInference(str(CKPT))
    ranked = infer.rerank(query=query, candidates=candidates, top_k=3)
    print(json.dumps({"status": "ok", "ranked": ranked}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
