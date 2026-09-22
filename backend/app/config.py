"""Central configuration. Every value can be overridden with an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("TUTOR_DATA_DIR", str(BACKEND_DIR / "data"))))

    # LLM (Ollama runs locally and is free)
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    sql_model: str = field(default_factory=lambda: _env("TUTOR_SQL_MODEL", "qwen2.5-coder:7b"))
    tutor_model: str = field(default_factory=lambda: _env("TUTOR_CHAT_MODEL", "qwen2.5-coder:7b"))
    embed_model: str = field(default_factory=lambda: _env("TUTOR_EMBED_MODEL", "nomic-embed-text"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("TUTOR_LLM_TIMEOUT", "120")))

    # Sandbox limits
    query_timeout_s: float = field(default_factory=lambda: float(_env("TUTOR_QUERY_TIMEOUT", "2.0")))
    max_rows: int = field(default_factory=lambda: int(_env("TUTOR_MAX_ROWS", "500")))
    max_sql_chars: int = 10_000

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


settings = Settings()
