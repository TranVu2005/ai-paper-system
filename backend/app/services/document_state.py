ALLOWED_TRANSITIONS = {
    "uploaded": ["extracting", "processing", "failed"],
    "extracting": ["chunking", "failed"],
    "chunking": ["embedding", "failed"],
    "embedding": ["graphing", "processed", "failed"],
    "graphing": ["processed", "failed"],
    "processing": ["processed", "failed"],
    "processed": ["extracting"],
    "failed": [],
}


def can_transition(current_status: str, new_status: str) -> bool:
    return new_status in ALLOWED_TRANSITIONS.get(current_status, [])
