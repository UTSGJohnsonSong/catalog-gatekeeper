"""Mechanical citation check. It proves the quoted words exist in a chunk retrieved in this run.

It does not prove the quote supports the claim. V05 and V07 add lexical coupling, and the judge may
escalate a semantic mismatch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gatekeeper.db import Conn
from gatekeeper.rules import spec
from gatekeeper.rules.text import norm
from gatekeeper.rules.validators import CheckedCitation


@dataclass(frozen=True)
class ChunkRecord:
    id: str
    doc_id: str
    text: str


def check_citation_against(
    citation: Mapping[str, Any], chunk: ChunkRecord | None, retrieved_in_run: bool
) -> CheckedCitation:
    """The pure core: checks run in a fixed order and the first failure names the reason."""
    doc_id, chunk_id, quote = citation.get("doc_id"), citation.get("chunk_id"), citation.get("quote")
    display = quote if isinstance(quote, str) else ""
    display_id = chunk_id if isinstance(chunk_id, str) else ""

    def fail(reason: str) -> CheckedCitation:
        return CheckedCitation(display, False, reason, display_id)

    if not all(isinstance(v, str) and v for v in (doc_id, chunk_id, quote)):
        return fail("shape")
    assert isinstance(quote, str)
    stripped = quote.strip()
    if (
        len(stripped) < spec.QUOTE_MIN_CHARS
        or len(stripped) > spec.QUOTE_MAX_CHARS
        or len(stripped.split()) < spec.QUOTE_MIN_WORDS
        or "..." in stripped
        or "…" in stripped
    ):
        return fail("shape")
    if chunk is None:
        return fail("unknown_chunk")
    if chunk.doc_id != doc_id or not str(chunk_id).startswith(f"{doc_id}#"):
        return fail("doc_mismatch")
    if not retrieved_in_run:
        return fail("not_retrieved")
    if norm(quote) not in norm(chunk.text):
        return fail("quote_not_found")
    return CheckedCitation(quote, True, None, display_id)


def check_citations(
    conn: Conn, run_id: str | None, citations: Sequence[Mapping[str, Any]]
) -> tuple[CheckedCitation, ...]:
    results: list[CheckedCitation] = []
    for citation in citations:
        chunk_id = citation.get("chunk_id")
        row = None
        if isinstance(chunk_id, str):
            row = conn.execute(
                "SELECT id, doc_id, text FROM policy_chunks WHERE id = %s AND retired_at IS NULL", (chunk_id,)
            ).fetchone()
        retrieved = False
        if row is not None and run_id is not None:
            retrieved = (
                conn.execute(
                    "SELECT 1 FROM run_retrievals WHERE run_id = %s AND chunk_id = %s", (run_id, chunk_id)
                ).fetchone()
                is not None
            )
        chunk = ChunkRecord(row["id"], row["doc_id"], row["text"]) if row else None
        results.append(check_citation_against(citation, chunk, retrieved))
    return tuple(results)
