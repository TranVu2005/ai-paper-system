from __future__ import annotations

import json
import inspect
import logging
import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None  # type: ignore

try:
    from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
except Exception:  # pragma: no cover
    AutoModel = AutoModelForCausalLM = AutoTokenizer = BitsAndBytesConfig = None  # type: ignore

from .inference_config import InferenceConfig

logger = logging.getLogger(__name__)


_DFLASH_DRAFT_MAP = {
    "Qwen/Qwen3-4B": "z-lab/Qwen3-4B-DFlash-b16",
    "Qwen/Qwen3-8B": "z-lab/Qwen3-8B-DFlash-b16",
    "Qwen/Qwen3.5-4B": "z-lab/Qwen3.5-4B-DFlash",
    "Qwen/Qwen3.5-9B": "z-lab/Qwen3.5-9B-DFlash",
    "Qwen/Qwen3.5-27B": "z-lab/Qwen3.5-27B-DFlash",
    "Qwen/Qwen3.5-35B-A3B": "z-lab/Qwen3.5-35B-A3B-DFlash",
    "Qwen/Qwen3.5-122B-A10B": "z-lab/Qwen3.5-122B-A10B-DFlash",
}


def _clear_cuda_cache() -> None:
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


@dataclass
class LLMEngine:
    config: InferenceConfig

    def __post_init__(self) -> None:
        self.backend = self.config.llm_backend
        has_cuda = torch is not None and torch.cuda.is_available()
        self.device = "cuda" if has_cuda and self.config.device == "cuda" else "cpu"
        self.model_name = self.config.model_name
        self.tokenizer = None
        self.model = None
        self.draft_model = None
        self.draft_model_name = ""
        self.vllm_chat_url = ""
        self.vllm_api_key = ""
        self.ollama_chat_url = ""
        if self._is_vllm_backend():
            self._init_vllm_backend()
            return
        if self._is_ollama_backend():
            self._init_ollama_backend()
            return
        if torch is None or AutoTokenizer is None:
            raise RuntimeError(
                "Transformers backend requires torch/transformers. "
                "Set LLM_BACKEND=vllm for remote GPU endpoint or install missing deps."
            )
        self._load_model_with_fallback()

    def _is_vllm_backend(self) -> bool:
        return self.backend == "vllm"

    def _is_ollama_backend(self) -> bool:
        return self.backend == "ollama"

    def _init_vllm_backend(self) -> None:
        base = self.config.vllm_base_url.strip().rstrip("/")
        if not base:
            raise RuntimeError("VLLM_BASE_URL is empty.")
        self.vllm_chat_url = f"{base}/v1/chat/completions"
        self.vllm_api_key = self.config.vllm_api_key.strip()
        self.model_name = self.config.vllm_model_name.strip() or self.config.model_name
        logger.info("Using vLLM backend at %s with model %s", self.vllm_chat_url, self.model_name)

    def _init_ollama_backend(self) -> None:
        base = self.config.ollama_base_url.strip().rstrip("/")
        if not base:
            raise RuntimeError("OLLAMA_BASE_URL is empty.")
        self.ollama_chat_url = f"{base}/api/chat"
        self.model_name = self.config.ollama_model_name.strip() or self.config.model_name
        logger.info("Using Ollama backend at %s with model %s", self.ollama_chat_url, self.model_name)

    def _is_dflash_enabled(self) -> bool:
        return self.config.speculative_method == "dflash"

    def _resolve_dflash_draft_name(self, target_model_name: str) -> str:
        if self.config.dflash_draft_model_name.strip():
            return self.config.dflash_draft_model_name.strip()

        inferred = _DFLASH_DRAFT_MAP.get(target_model_name, "")
        if inferred:
            return inferred

        # Common user confusion: Qwen3.5 has 9B, not 8B.
        if target_model_name.strip().lower() == "qwen/qwen3.5-8b":
            logger.warning("Qwen/Qwen3.5-8B is not supported by DFlash list; using Qwen3.5-9B draft model.")
            return "z-lab/Qwen3.5-9B-DFlash"

        raise ValueError(
            f"Cannot infer DFlash draft model for target '{target_model_name}'. "
            "Set DFLASH_DRAFT_MODEL_NAME explicitly."
        )

    def _target_load_kwargs(self, effective_device: str) -> dict:
        kwargs = {"trust_remote_code": True, "device_map": "auto"}
        if effective_device == "cuda" and self.config.use_4bit:
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
        else:
            kwargs["torch_dtype"] = torch.float16 if self.config.torch_dtype == "float16" else torch.float32
            if effective_device == "cpu":
                kwargs["device_map"] = "cpu"
        return kwargs

    def _draft_load_kwargs(self, effective_device: str) -> dict:
        kwargs = {"trust_remote_code": True, "device_map": "auto"}
        if effective_device == "cuda":
            kwargs["torch_dtype"] = torch.float16 if self.config.torch_dtype == "float16" else torch.float32
        else:
            kwargs["torch_dtype"] = torch.float32
            kwargs["device_map"] = "cpu"
        return kwargs

    def _try_load(self, model_name: str, force_cpu: bool = False) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        effective_device = "cpu" if force_cpu else self.device

        self.model = AutoModelForCausalLM.from_pretrained(model_name, **self._target_load_kwargs(effective_device)).eval()
        self.model_name = model_name

        self.draft_model = None
        self.draft_model_name = ""
        if self._is_dflash_enabled():
            draft_name = self._resolve_dflash_draft_name(model_name)
            self.draft_model = AutoModel.from_pretrained(draft_name, **self._draft_load_kwargs(effective_device)).eval()
            if not hasattr(self.draft_model, "spec_generate"):
                raise RuntimeError(f"DFlash draft model '{draft_name}' has no spec_generate method.")
            self.draft_model_name = draft_name

    def _load_model_with_fallback(self) -> None:
        candidates = [self.config.model_name]
        if self.config.model_name == self.config.optional_heavy_model_name:
            candidates.append(self.config.fallback_model_name)
        elif self.config.fallback_model_name not in candidates:
            candidates.append(self.config.fallback_model_name)

        last_error: Optional[Exception] = None
        for name in candidates:
            for force_cpu in [False, True]:
                try:
                    _clear_cuda_cache()
                    self._try_load(name, force_cpu=force_cpu)
                    logger.info("Loaded model %s on %s", name, "cpu" if force_cpu else self.device)
                    if self._is_dflash_enabled():
                        logger.info("Loaded DFlash draft %s", self.draft_model_name)
                    return
                except Exception as ex:
                    last_error = ex
                    logger.warning("Load failed for %s: %s", name, ex)

        raise RuntimeError(f"Cannot load model from {candidates}: {last_error}")

    def _apply_chat_template_ids(self, prompt: str) -> torch.Tensor:
        if self.tokenizer is None or self.model is None:
            raise RuntimeError("Model is not initialized")

        messages = [{"role": "user", "content": prompt}]
        kwargs = {"add_generation_prompt": True, "return_tensors": "pt"}
        if self.config.dflash_enable_thinking:
            kwargs["enable_thinking"] = True
        else:
            kwargs["enable_thinking"] = False

        try:
            ids = self.tokenizer.apply_chat_template(messages, tokenize=True, **kwargs)
        except TypeError:
            kwargs.pop("enable_thinking", None)
            ids = self.tokenizer.apply_chat_template(messages, tokenize=True, **kwargs)

        def _to_tensor(obj) -> torch.Tensor | None:
            if isinstance(obj, torch.Tensor):
                return obj
            if isinstance(obj, list):
                if not obj:
                    return torch.empty((1, 0), dtype=torch.long)
                if isinstance(obj[0], list):
                    return torch.tensor(obj, dtype=torch.long)
                return torch.tensor([obj], dtype=torch.long)
            return None

        out = _to_tensor(ids)
        if out is None and isinstance(ids, Mapping):
            out = _to_tensor(ids.get("input_ids"))
        if out is None and hasattr(ids, "input_ids"):
            out = _to_tensor(getattr(ids, "input_ids"))
        if out is None and isinstance(ids, tuple) and ids:
            out = _to_tensor(ids[0])
        if out is None:
            raise RuntimeError(f"Tokenizer.apply_chat_template returned unsupported type: {type(ids)}")

        return out.to(self.model.device)

    def _generate_standard(self, input_ids: torch.Tensor, max_new_tokens: int, temp: float) -> torch.Tensor:
        if self.model is None:
            raise RuntimeError("Target model is not initialized")

        attention_mask = torch.ones_like(input_ids, dtype=torch.long, device=input_ids.device)
        kwargs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "max_new_tokens": max_new_tokens,
            "do_sample": temp > 0,
            "repetition_penalty": self.config.repetition_penalty,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if temp > 0:
            kwargs["temperature"] = temp
        with torch.no_grad():
            return self.model.generate(**kwargs)

    def _generate_with_dflash(self, input_ids: torch.Tensor, max_new_tokens: int, temp: float) -> torch.Tensor:
        if self.draft_model is None or self.model is None:
            raise RuntimeError("DFlash mode requires both draft and target models.")
        stop_ids = [self.tokenizer.eos_token_id] if self.tokenizer and self.tokenizer.eos_token_id is not None else None
        kwargs = {
            "input_ids": input_ids,
            "max_new_tokens": max_new_tokens,
            "temperature": temp,
            "target": self.model,
        }
        # Different DFlash draft implementations expose different signatures.
        # Only pass speculative-token control when explicitly supported.
        try:
            sig = inspect.signature(self.draft_model.spec_generate)
            params = set(sig.parameters.keys())
        except (TypeError, ValueError):
            params = set()
        if "num_speculative_tokens" in params:
            kwargs["num_speculative_tokens"] = self.config.dflash_num_speculative_tokens
        if stop_ids:
            kwargs["stop_token_ids"] = stop_ids

        with torch.no_grad():
            return self.draft_model.spec_generate(**kwargs)

    @staticmethod
    def _extract_vllm_content(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
            # Some thinking-capable models on OpenAI-compatible endpoints can
            # return empty content and put text in reasoning/thinking fields.
            for alt_key in ("reasoning", "thinking", "reasoning_content"):
                alt = message.get(alt_key, "")
                if isinstance(alt, str) and alt.strip():
                    return alt.strip()
            return ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts).strip()
        return str(content).strip()

    def _prepare_vllm_prompt(self, prompt: str) -> str:
        out = prompt or ""
        # For Qwen3 on Ollama-compatible endpoints, force no-think mode by
        # default to avoid empty `message.content` responses.
        if "qwen3" in self.model_name.lower():
            low = out.lower()
            if "/no_think" not in low and "/nothink" not in low:
                out = "/no_think\n" + out
        return out

    @staticmethod
    def _sanitize_model_output(text: str) -> str:
        out = (text or "").strip()
        if not out:
            return out

        low = out.lower()

        # Prefer explicit final-answer segments when present.
        final_markers = [
            "final summary:",
            "final answer:",
            "tra loi:",
            "answer:",
            "summary:",
        ]
        for marker in final_markers:
            idx = low.rfind(marker)
            if idx >= 0:
                candidate = out[idx + len(marker) :].strip(" \n:-")
                if candidate:
                    out = candidate
                    low = out.lower()
                    break

        # Remove common reasoning leakage blocks.
        if (
            low.startswith("thinking process")
            or low.startswith("analysis:")
            or low.startswith("reasoning:")
            or "thinking process:" in low
        ):
            cleaned_lines: list[str] = []
            for line in out.splitlines():
                s = line.strip()
                l = s.lower()
                if not s:
                    if cleaned_lines and cleaned_lines[-1] != "":
                        cleaned_lines.append("")
                    continue
                if l.startswith("thinking process"):
                    continue
                if l.startswith("analyze the request") or l.startswith("analyse the request"):
                    continue
                if l.startswith("correction:") or l.startswith("note:") or l.startswith("challenge:"):
                    continue
                if l.startswith("input text:") or l.startswith("constraints:") or l.startswith("role:") or l.startswith("task:"):
                    continue
                if re.match(r"^\d+\.\s+\*\*.*\*\*:?\s*$", s):
                    continue
                if s.startswith("*   **"):
                    continue
                cleaned_lines.append(line)

            cleaned = "\n".join(cleaned_lines).strip()
            if cleaned:
                out = cleaned

        return out.strip()

    def _generate_vllm(self, prompt: str, max_new_tokens: int, temp: float) -> str:
        final_prompt = self._prepare_vllm_prompt(prompt)
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": final_prompt}],
            "max_tokens": max_new_tokens,
            "temperature": temp,
            "repetition_penalty": self.config.repetition_penalty,
            # For Qwen3/Qwen3.5 on vLLM: disable reasoning text in normal chat responses.
            "chat_template_kwargs": {"enable_thinking": bool(self.config.dflash_enable_thinking)},
        }
        if "qwen3" in self.model_name.lower() and not self.config.dflash_enable_thinking:
            payload["reasoning_effort"] = "none"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.vllm_api_key:
            headers["Authorization"] = f"Bearer {self.vllm_api_key}"
        req = Request(self.vllm_chat_url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.config.vllm_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as ex:
            detail = ex.read().decode("utf-8", errors="replace") if hasattr(ex, "read") else str(ex)
            raise RuntimeError(f"vLLM HTTPError {ex.code}: {detail}") from ex
        except URLError as ex:
            raise RuntimeError(f"Cannot connect to vLLM at {self.vllm_chat_url}: {ex}") from ex

        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(f"vLLM error: {data['error']}")
        return self._sanitize_model_output(self._extract_vllm_content(data))

    def _generate_ollama(self, prompt: str, max_new_tokens: int, temp: float) -> str:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": temp,
                "num_predict": max_new_tokens,
                "repeat_penalty": self.config.repetition_penalty,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            self.ollama_chat_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.config.ollama_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as ex:
            detail = ex.read().decode("utf-8", errors="replace") if hasattr(ex, "read") else str(ex)
            raise RuntimeError(f"Ollama HTTPError {ex.code}: {detail}") from ex
        except URLError as ex:
            raise RuntimeError(f"Cannot connect to Ollama at {self.ollama_chat_url}: {ex}") from ex

        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(f"Ollama error: {data['error']}")
        message = data.get("message") or {}
        content = str(message.get("content") or "").strip()
        return self._sanitize_model_output(content)

    def generate(self, prompt: str, max_new_tokens: int, temperature: Optional[float] = None) -> str:
        temp = self.config.temperature if temperature is None else temperature
        if self._is_vllm_backend():
            return self._generate_vllm(prompt=prompt, max_new_tokens=max_new_tokens, temp=temp)
        if self._is_ollama_backend():
            return self._generate_ollama(prompt=prompt, max_new_tokens=max_new_tokens, temp=temp)

        if self.tokenizer is None or self.model is None:
            raise RuntimeError("Model is not initialized")

        input_ids = self._apply_chat_template_ids(prompt)
        prompt_len = input_ids.shape[1]

        def _run(gen_tokens: int, trim_prompt: bool = False) -> torch.Tensor:
            ids = input_ids
            if trim_prompt:
                keep = min(2200, input_ids.shape[1])
                ids = input_ids[:, -keep:]
            if self._is_dflash_enabled():
                return self._generate_with_dflash(ids, gen_tokens, temp)
            return self._generate_standard(ids, gen_tokens, temp)

        try:
            out = _run(max_new_tokens, trim_prompt=False)
        except RuntimeError as ex:
            if "out of memory" not in str(ex).lower():
                raise
            _clear_cuda_cache()
            retry_tokens = max(96, max_new_tokens // 2)
            logger.warning("Generation OOM, retry with %s tokens", retry_tokens)
            try:
                out = _run(retry_tokens, trim_prompt=False)
            except RuntimeError as ex2:
                if "out of memory" not in str(ex2).lower():
                    raise
                _clear_cuda_cache()
                out = _run(min(128, retry_tokens), trim_prompt=True)
                prompt_len = min(2200, input_ids.shape[1])

        if out.shape[1] > prompt_len:
            gen = out[:, prompt_len:]
        else:
            gen = out
        decoded = self.tokenizer.batch_decode(gen, skip_special_tokens=True)[0].strip()
        return self._sanitize_model_output(decoded)


class DraftModel(LLMEngine):
    pass


class TargetModel(LLMEngine):
    pass
