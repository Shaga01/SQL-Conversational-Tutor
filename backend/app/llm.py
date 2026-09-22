"""Thin client for a local Ollama server (free, runs on-device).

Features used across the app and the evaluation harness:
* ``chat``      - chat completion, optional JSON-schema constrained output
* ``stream``    - token streaming for the tutor UI
* ``embed``     - text embeddings (schema linking, example retrieval)
* a persistent response cache keyed on (model, messages, options) so evaluation
  runs are reproducible and re-running an experiment does not re-pay for calls
  whose inputs did not change.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from .config import settings


class LLMUnavailable(Exception):
    """Raised when the local model server cannot be reached."""


class _Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", (key, value))
            self._conn.commit()


class OllamaClient:
    def __init__(self, base_url: str | None = None, cache_path: Path | None = None, use_cache: bool = True) -> None:
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self._http = httpx.Client(timeout=settings.llm_timeout_s)
        self._cache = _Cache(cache_path or settings.data_dir / "llm_cache.sqlite") if use_cache else None

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def _key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(f"{self.base_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.base_url} ({exc.__class__.__name__}). "
                                 "Is `ollama serve` running?") from exc
        if resp.status_code == 404 and "not found" in resp.text:
            raise LLMUnavailable(f"Model missing in Ollama: {payload.get('model')}. Run `ollama pull {payload.get('model')}`.")
        resp.raise_for_status()
        return resp.json()

    def available(self) -> bool:
        try:
            return self._http.get(f"{self.base_url}/api/version", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    # -- API -----------------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        seed: int = 0,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 512,
        cache: bool = True,
    ) -> str:
        model = model or settings.sql_model
        options = {"temperature": temperature, "seed": seed, "num_predict": max_tokens, "num_ctx": 8192}
        key = self._key("chat", model, messages, options, schema)
        if cache and self._cache and (hit := self._cache.get(key)) is not None:
            return hit
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": options}
        if schema is not None:
            payload["format"] = schema
        text = self._post("/api/chat", payload)["message"]["content"]
        if cache and self._cache:
            self._cache.put(key, text)
        return text

    def complete(self, prompt: str, *, model: str, temperature: float = 0.0, seed: int = 0,
                 max_tokens: int = 400, stop: list[str] | None = None) -> str:
        """Raw completion (no chat template) for models trained on their own prompt format."""
        options = {"temperature": temperature, "seed": seed, "num_predict": max_tokens, "num_ctx": 8192,
                   "stop": stop or []}
        key = self._key("complete", model, prompt, options)
        if self._cache and (hit := self._cache.get(key)) is not None:
            return hit
        text = self._post("/api/generate", {"model": model, "prompt": prompt, "raw": True, "stream": False,
                                            "options": options})["response"]
        if self._cache:
            self._cache.put(key, text)
        return text

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any], **kw: Any) -> dict[str, Any]:
        raw = self.chat(messages, schema=schema, **kw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def stream(self, messages: list[dict[str, str]], *, model: str | None = None,
               temperature: float = 0.3, max_tokens: int = 700) -> Iterator[str]:
        payload = {"model": model or settings.tutor_model, "messages": messages, "stream": True,
                   "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192}}
        try:
            with self._http.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if piece := chunk.get("message", {}).get("content"):
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.base_url}.") from exc

    def embed(self, texts: list[str], *, model: str | None = None) -> np.ndarray:
        """Return L2-normalised embeddings (rows), using the cache per text."""
        model = model or settings.embed_model
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, t in enumerate(texts):
            hit = self._cache.get(self._key("embed", model, t)) if self._cache else None
            if hit is not None:
                out[i] = json.loads(hit)
            else:
                missing.append(i)
        for start in range(0, len(missing), 64):
            batch = missing[start:start + 64]
            vectors = self._post("/api/embed", {"model": model, "input": [texts[i] for i in batch]})["embeddings"]
            for i, vec in zip(batch, vectors, strict=True):
                out[i] = vec
                if self._cache:
                    self._cache.put(self._key("embed", model, texts[i]), json.dumps(vec))
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-9, None)


_default: OllamaClient | None = None


def get_client() -> OllamaClient:
    global _default
    if _default is None:
        _default = OllamaClient()
    return _default
