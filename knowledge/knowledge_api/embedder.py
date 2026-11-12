"""Text embeddings. nomic-embed-text needs task prefixes: "search_document: " for stored text and
"search_query: " for questions; without them retrieval quality drops noticeably.

The primary embedder may carry a multilingual partner in `.ml` (bge-m3, no prefixes). Everything
is embedded with both; questions written in an Indic script are searched with the partner.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from .config import Settings


class Embedder(Protocol):
    dim: int
    ml: "Embedder | None"

    def documents(self, texts: list[str]) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


def uses_local_script(text: str) -> bool:
    """True if the text contains letters outside Latin script (Devanagari, Kannada, ...).
    Hinglish typed in Latin letters stays with the primary embedder, which handles it well."""
    return any(ch.isalpha() and ord(ch) > 0x024F for ch in text)


class OllamaEmbedder:
    def __init__(
        self,
        settings: Settings,
        model: str,
        dim: int,
        prefixes: tuple[str, str] = ("", ""),
        ml: Embedder | None = None,
    ):
        self.url = f"{settings.ollama_url}/api/embed"
        self.model = model
        self.dim = dim
        self.doc_prefix, self.query_prefix = prefixes
        self.ml = ml

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(inputs), 32):
            r = httpx.post(
                self.url,
                json={"model": self.model, "input": inputs[start : start + 32]},
                timeout=300,
            )
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
        return out

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.doc_prefix + t for t in texts]) if texts else []

    def query(self, text: str) -> list[float]:
        return self._embed([self.query_prefix + text])[0]


class HashEmbedder:
    """Deterministic bag-of-words vectors for tests: no model, no network. Shared words -> similar
    vectors, which is enough to test ranking, filters and tenant isolation."""

    def __init__(self, dim: int = 768, ml: Embedder | None = None):
        self.dim = dim
        self.ml = ml

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def query(self, text: str) -> list[float]:
        return self._vec(text)


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "hash":
        return HashEmbedder(settings.embed_dim)
    ml = (
        OllamaEmbedder(settings, settings.ml_embed_model, settings.ml_embed_dim)
        if settings.ml_embed_model
        else None
    )
    return OllamaEmbedder(
        settings,
        settings.embed_model,
        settings.embed_dim,
        ("search_document: ", "search_query: "),
        ml=ml,
    )


def embed_ml(embedder: Embedder, texts: list[str]) -> list[list[float] | None]:
    """Multilingual vectors for stored text, or None per text when no partner is configured."""
    if embedder.ml is None or not texts:
        return [None] * len(texts)
    return embedder.ml.documents(texts)
