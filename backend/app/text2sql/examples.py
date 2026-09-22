"""Retrieval of solved (question, SQL) examples for few-shot prompting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..llm import OllamaClient


@dataclass(frozen=True)
class Example:
    question: str
    sql: str
    db_id: str = ""


class ExampleStore:
    def __init__(self, examples: list[Example], client: OllamaClient) -> None:
        self.examples = examples
        self.client = client
        self._vectors: np.ndarray | None = None

    @classmethod
    def from_spider(cls, path: Path, client: OllamaClient) -> "ExampleStore":
        data = json.loads(path.read_text())
        seen: set[str] = set()
        examples = []
        for item in data:
            if item["question"] in seen:
                continue
            seen.add(item["question"])
            examples.append(Example(item["question"], item["query"].strip(), item["db_id"]))
        return cls(examples, client)

    def _ensure_index(self) -> np.ndarray:
        if self._vectors is None:
            self._vectors = self.client.embed([f"search_query: {e.question}" for e in self.examples])
        return self._vectors

    def exclude_db_for(self, db_path: Path) -> str:
        return db_path.stem

    def nearest(self, question: str, k: int, exclude_db: str = "") -> list[Example]:
        vectors = self._ensure_index()
        q = self.client.embed([f"search_query: {question}"])[0]
        order = np.argsort(-(vectors @ q))
        picked: list[Example] = []
        for i in order:
            ex = self.examples[int(i)]
            if exclude_db and ex.db_id == exclude_db:
                continue  # never show examples from the database being asked about
            picked.append(ex)
            if len(picked) == k:
                break
        return picked[::-1]  # most similar example last, closest to the question
