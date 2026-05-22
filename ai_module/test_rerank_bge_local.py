from __future__ import annotations

import json
import sys

from ai_module.rerank.bge_reranker import BGEReranker


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    query = "Bài báo đề xuất phương pháp nào cho tóm tắt khoa học?"
    candidates = [
        {
            "id": "c1",
            "text": "Nghiên cứu đề xuất mô hình transformer kết hợp retrieval để tóm tắt tài liệu khoa học.",
        },
        {
            "id": "c2",
            "text": "Bài viết về dự báo giá cổ phiếu theo chuỗi thời gian.",
        },
        {
            "id": "c3",
            "text": "Hệ thống RAG với bộ lọc ngữ cảnh và memory ngoài cho bài toán tóm tắt.",
        },
    ]

    reranker = BGEReranker(model_name="BAAI/bge-m3", device="cuda", strict_device=True)
    ranked = reranker.rerank(query=query, candidates=candidates, top_k=3)
    print(
        json.dumps(
            {"status": "ok", "model": reranker.model_name, "device": reranker.device, "ranked": ranked},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
