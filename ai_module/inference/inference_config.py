from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class InferenceConfig:
    llm_backend: str = os.getenv("LLM_BACKEND", "transformers").strip().lower()
    model_name: str = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-3B-Instruct")
    optional_heavy_model_name: str = os.getenv("OPTIONAL_HEAVY_MODEL_NAME", "Qwen/Qwen2.5-7B-Instruct")
    fallback_model_name: str = os.getenv("FALLBACK_MODEL_NAME", "Qwen/Qwen2.5-3B-Instruct")
    vllm_base_url: str = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000")
    vllm_api_key: str = os.getenv("VLLM_API_KEY", "")
    vllm_model_name: str = os.getenv("VLLM_MODEL_NAME", "")
    vllm_timeout_seconds: int = int(os.getenv("VLLM_TIMEOUT_SECONDS", "180"))
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model_name: str = os.getenv("OLLAMA_MODEL", "")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
    embedding_fallback_model_name: str = os.getenv("EMBEDDING_FALLBACK_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "local").strip().lower()
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "").strip()
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "").strip()
    embedding_remote_model_name: str = os.getenv("EMBEDDING_REMOTE_MODEL_NAME", "").strip()
    embedding_timeout_seconds: int = int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "120"))
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "cpu").strip().lower()

    use_4bit: bool = os.getenv("USE_4BIT", "true").lower() == "true"
    torch_dtype: str = os.getenv("TORCH_DTYPE", "float16")
    device: str = os.getenv("DEVICE", "cuda")

    chunk_size_chars: int = int(os.getenv("CHUNK_SIZE_CHARS", "3500"))
    chunk_overlap_chars: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "400"))
    max_input_chars_higen: int = int(os.getenv("MAX_INPUT_CHARS_HIGEN", "2600"))
    summary_highlight_concurrency: int = int(os.getenv("SUMMARY_HIGHLIGHT_CONCURRENCY", "1"))
    summary_highlight_parallel_force: bool = os.getenv("SUMMARY_HIGHLIGHT_PARALLEL_FORCE", "false").lower() == "true"

    top_k: int = int(os.getenv("TOP_K", "5"))
    max_new_tokens_summary: int = int(os.getenv("MAX_NEW_TOKENS_SUMMARY", "2000"))
    max_new_tokens_qa: int = int(os.getenv("MAX_NEW_TOKENS_QA", "500"))
    max_new_tokens_events: int = int(os.getenv("MAX_NEW_TOKENS_EVENTS", "700"))
    
    
    temperature: float = float(os.getenv("TEMPERATURE", "0.0"))
    repetition_penalty: float = float(os.getenv("REPETITION_PENALTY", "1.05"))

    speculative_method: str = os.getenv("SPECULATIVE_METHOD", "none").strip().lower()
    dflash_draft_model_name: str = os.getenv("DFLASH_DRAFT_MODEL_NAME", "")
    dflash_num_speculative_tokens: int = int(os.getenv("DFLASH_NUM_SPECULATIVE_TOKENS", "16"))
    dflash_enable_thinking: bool = os.getenv("DFLASH_ENABLE_THINKING", "false").lower() == "true"

    output_dir: str = os.getenv("OUTPUT_DIR", "ai_module/outputs")

    neo4j_enabled: bool = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_user: str = os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", ""))
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")

    def validate(self) -> None:
        if self.llm_backend not in {"transformers", "vllm", "ollama"}:
            raise ValueError("llm_backend must be one of: transformers, vllm, ollama")
        if self.chunk_size_chars <= 0:
            raise ValueError("chunk_size_chars must be > 0")
        if self.chunk_overlap_chars < 0 or self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("chunk_overlap_chars must be >=0 and < chunk_size_chars")
        if self.top_k <= 0:
            raise ValueError("top_k must be > 0")
        if self.summary_highlight_concurrency <= 0:
            raise ValueError("summary_highlight_concurrency must be > 0")
        if self.speculative_method not in {"none", "dflash"}:
            raise ValueError("speculative_method must be one of: none, dflash")
        if self.dflash_num_speculative_tokens <= 0:
            raise ValueError("dflash_num_speculative_tokens must be > 0")
        if self.vllm_timeout_seconds <= 0:
            raise ValueError("vllm_timeout_seconds must be > 0")
        if self.ollama_timeout_seconds <= 0:
            raise ValueError("ollama_timeout_seconds must be > 0")
        if self.embedding_backend not in {"local", "remote"}:
            raise ValueError("embedding_backend must be one of: local, remote")
        if self.embedding_timeout_seconds <= 0:
            raise ValueError("embedding_timeout_seconds must be > 0")
        if self.embedding_device not in {"cpu", "cuda", "auto"}:
            raise ValueError("embedding_device must be one of: cpu, cuda, auto")


Config = InferenceConfig
