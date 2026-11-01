from __future__ import annotations

import subprocess
import sys

from gatekeeper.rag.chunker import TARGET_MAX, chunk_document
from gatekeeper.rag.citations import ChunkRecord, check_citation_against
from gatekeeper.rag.embedder import DIM, HashingTfidfEmbedder, tokenize
from tests.conftest import ROOT

CHUNK = ChunkRecord(
    "returns#001",
    "returns",
    "Unworn items may be returned within 30 days — with “original tags” attached.\n\nMore text follows here.",
)
GOOD = {"doc_id": "returns", "chunk_id": "returns#001", "quote": "Unworn items may be returned within 30 days"}


def check(citation=GOOD, chunk=CHUNK, retrieved=True):
    return check_citation_against(citation, chunk, retrieved)


def test_valid_quote_passes():
    assert check().ok


def test_normalization_tolerates_typography_only():
    assert check({**GOOD, "quote": 'unworn ITEMS may be\nreturned within 30 days - with "original tags" attached.'}).ok


def test_each_failure_has_its_reason():
    assert check({**GOOD, "quote": "too short"}).reason == "shape"
    assert check({**GOOD, "quote": "Unworn items may be returned... attached"}).reason == "shape"
    assert check({"doc_id": "returns"}).reason == "shape"
    assert check(chunk=None).reason == "unknown_chunk"
    assert check({**GOOD, "doc_id": "shipping"}).reason == "doc_mismatch"
    assert check(retrieved=False).reason == "not_retrieved"
    assert check({**GOOD, "quote": "Unworn items may be returned within 60 days"}).reason == "quote_not_found"
    assert check({**GOOD, "quote": "items Unworn may be returned within 30 days"}).reason == "quote_not_found"


def test_chunker_is_deterministic_and_ids_match_schema():
    body = (ROOT / "fixtures/policies/returns.md").read_text(encoding="utf-8")
    first, second = chunk_document("returns", body), chunk_document("returns", body)
    assert first == second
    assert [c.id for c in first] == [f"returns#{i:03d}" for i in range(len(first))]
    assert all(len(c.text) <= TARGET_MAX for c in first)


def test_chunker_ignores_line_endings():
    body = (ROOT / "fixtures/policies/returns.md").read_text(encoding="utf-8")
    assert chunk_document("r", body) == chunk_document("r", body.replace("\n", "\r\n"))


def test_embedder_is_deterministic_across_processes():
    """Salted hash() would break this. blake2b must not."""
    code = "from gatekeeper.rag.embedder import HashingTfidfEmbedder as E; v=E().embed('free returns within thirty days'); print([i for i,x in enumerate(v) if x][:8])"
    runs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={
                "PYTHONHASHSEED": str(s),
                "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                "PYTHONPATH": str(ROOT / "src"),
            },
        ).stdout
        for s in (1, 2)
    }
    assert len(runs) == 1


def test_embedder_shape_and_normalization():
    embedder = HashingTfidfEmbedder()
    embedder.fit(["returns within thirty days", "shipping takes five days"])
    vector = embedder.embed("returns")
    assert len(vector) == DIM
    assert abs(sum(v * v for v in vector) - 1.0) < 1e-9
    assert sum(embedder.embed("")) == 0


def test_tokenizer_stems_and_drops_stopwords():
    assert tokenize("The returns are shipping") == ["return", "shipp"]
