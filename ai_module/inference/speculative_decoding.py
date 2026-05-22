from __future__ import annotations

from typing import Optional

from .llm_engine import DraftModel, TargetModel



def speculative_decode(prompt: str, target_model: TargetModel, max_new_tokens: int, draft_model: Optional[DraftModel] = None) -> str:
    # DFlash is configured on the target engine via InferenceConfig.
    # `draft_model` is kept for backward compatibility.
    _ = draft_model
    return target_model.generate(prompt=prompt, max_new_tokens=max_new_tokens)
