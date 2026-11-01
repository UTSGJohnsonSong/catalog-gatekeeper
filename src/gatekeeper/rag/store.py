"""Policy corpus storage and search over pgvector. Search is an exact scan; the corpus is small."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pgvector import Vector
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from gatekeeper.db import Conn
from gatekeeper.rag.chunker import chunk_document
from gatekeeper.rag.embedder import Embedder, HashingTfidfEmbedder


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    chunk_id: str
    text: str
    score: float


def ingest_directory(pool: ConnectionPool[Conn], policy_dir: Path, embedder: Embedder) -> int:
    """Re-embed the whole corpus in one transaction, because IDF depends on every document."""
    docs: list[tuple[str, str, str]] = []
    for path in sorted(policy_dir.glob("*.md")):
        body = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        first_line = body.split("\n", 1)[0].lstrip("# ").strip()
        docs.append((path.stem, first_line, body))
    chunks = [c for doc_id, _, body in docs for c in chunk_document(doc_id, body)]
    embedder.fit([c.text for c in chunks])
    with pool.connection() as conn:
        for doc_id, title, body in docs:
            conn.execute(
                "INSERT INTO policy_docs (id, title, body, doc_hash) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, doc_hash = EXCLUDED.doc_hash",
                (doc_id, title, body, hashlib.sha256(body.encode("utf-8")).hexdigest()),
            )
        ids = [c.id for c in chunks]
        # Retired, not deleted: run_retrievals keeps pointing at them, and search and citations ignore them.
        conn.execute("UPDATE policy_chunks SET retired_at = now() WHERE id <> ALL(%s) AND retired_at IS NULL", (ids,))
        for chunk in chunks:
            conn.execute(
                "INSERT INTO policy_chunks (id, doc_id, ordinal, text, embedder_id, embedding) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text, embedder_id = EXCLUDED.embedder_id, "
                "embedding = EXCLUDED.embedding, retired_at = NULL",
                (chunk.id, chunk.doc_id, chunk.ordinal, chunk.text, embedder.id, Vector(embedder.embed(chunk.text))),
            )
        conn.execute(
            "INSERT INTO embedder_state (embedder_id, state) VALUES (%s, %s) "
            "ON CONFLICT (embedder_id) DO UPDATE SET state = EXCLUDED.state",
            (embedder.id, Jsonb(embedder.state())),
        )
    return len(chunks)


def load_embedder(pool: ConnectionPool[Conn]) -> HashingTfidfEmbedder:
    embedder = HashingTfidfEmbedder()
    with pool.connection() as conn:
        row = conn.execute("SELECT state FROM embedder_state WHERE embedder_id = %s", (embedder.id,)).fetchone()
    if row is None:
        raise RuntimeError("policy corpus has not been ingested; run the migrate step first")
    embedder.load_state(row["state"])
    return embedder


def search(conn: Conn, embedder: Embedder, query: str, k: int = 5) -> list[SearchHit]:
    embedded = embedder.embed(query)
    if not any(embedded):
        # A query of only stopwords has no direction, so any ranking would be arbitrary.
        return []
    vector = Vector(embedded)
    rows = conn.execute(
        "SELECT id, doc_id, text, 1 - (embedding <=> %s) AS score FROM policy_chunks "
        "WHERE embedder_id = %s AND retired_at IS NULL ORDER BY embedding <=> %s, id LIMIT %s",
        (vector, embedder.id, vector, k),
    ).fetchall()
    return [SearchHit(r["doc_id"], r["id"], r["text"], float(r["score"] or 0.0)) for r in rows]
