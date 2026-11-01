"""Local deterministic embedder: hashed TF-IDF over unigrams and bigrams.

Stated plainly: this is lexical matching in hashed buckets, with no semantic understanding. A query that
shares no words with the right chunk will miss. It exists so the system runs with no key and no model
download; a real embedding model plugs in behind the same protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

DIM = 1024
EMBEDDER_ID = f"hashing-tfidf-v1-d{DIM}"

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


def _stem(token: str) -> str:
    for suffix, minimum in (("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
        if token.endswith(suffix) and len(token) >= minimum:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _WORD.findall(text.casefold()) if t not in _STOP]


def _features(text: str) -> Counter[str]:
    tokens = tokenize(text)
    grams = Counter(tokens)
    grams.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))
    return grams


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % DIM, 1.0 if (value >> 63) & 1 else -1.0


class Embedder(Protocol):
    id: str
    dim: int

    def fit(self, documents: list[str]) -> None: ...
    def embed(self, text: str) -> list[float]: ...
    def state(self) -> dict: ...
    def load_state(self, state: dict) -> None: ...


class HashingTfidfEmbedder:
    id = EMBEDDER_ID
    dim = DIM

    def __init__(self) -> None:
        self.idf: list[float] = [1.0] * DIM

    def fit(self, documents: list[str]) -> None:
        df = [0] * DIM
        for doc in documents:
            for bucket in {_bucket(f)[0] for f in _features(doc)}:
                df[bucket] += 1
        n = len(documents)
        self.idf = [math.log((1 + n) / (1 + d)) + 1.0 for d in df]

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        for feature, tf in _features(text).items():
            bucket, sign = _bucket(feature)
            vector[bucket] += sign * (1.0 + math.log(tf)) * self.idf[bucket]
        length = math.sqrt(sum(v * v for v in vector))
        return [v / length for v in vector] if length else vector

    def state(self) -> dict:
        return {"idf": self.idf}

    def load_state(self, state: dict) -> None:
        self.idf = list(state["idf"])
