"""A Postgres job queue: FOR UPDATE SKIP LOCKED, backoff, dead letter, and a reaper. No Redis needed."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.db import tx

MAX_BACKOFF_SECONDS = 600


def enqueue(pool: Any, kind: str, payload: dict[str, Any], *, dedupe_key: str | None = None) -> None:
    with tx(pool) as conn:
        conn.execute(
            "INSERT INTO jobs (kind, payload, dedupe_key) VALUES (%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING",
            (kind, Jsonb(payload), dedupe_key),
        )


def claim(pool: Any, worker: str) -> dict[str, Any] | None:
    with tx(pool) as conn:
        return conn.execute(
            "UPDATE jobs SET status = 'running', locked_at = now(), locked_by = %s, attempts = attempts + 1 "
            "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now() "
            "ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
            (worker,),
        ).fetchone()


def complete(pool: Any, job_id: int) -> None:
    with tx(pool) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', locked_at = NULL, locked_by = NULL, last_error = NULL WHERE id = %s",
            (job_id,),
        )


def defer(pool: Any, job_id: int, seconds: int, reason: str) -> None:
    """Try again later without using up an attempt, e.g. while the kill switch is off."""
    with tx(pool) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', attempts = greatest(attempts - 1, 0), run_after = now() + make_interval(secs => %s), "
            "locked_at = NULL, locked_by = NULL, last_error = %s WHERE id = %s",
            (seconds, reason[:500], job_id),
        )


def fail(pool: Any, job: dict[str, Any], error: str, *, retryable: bool = True) -> str:
    dead = not retryable or job["attempts"] >= job["max_attempts"]
    backoff = min(MAX_BACKOFF_SECONDS, 2 ** job["attempts"])
    with tx(pool) as conn:
        conn.execute(
            "UPDATE jobs SET status = %s, run_after = now() + make_interval(secs => %s), locked_at = NULL, locked_by = NULL, "
            "last_error = %s WHERE id = %s",
            ("dead" if dead else "queued", backoff, error[:2000], job["id"]),
        )
    return "dead" if dead else "queued"


def reap(pool: Any, *, older_than_seconds: int = 600) -> int:
    """Requeue jobs whose worker died mid-run."""
    with tx(pool) as conn:
        return conn.execute(
            "UPDATE jobs SET status = 'queued', locked_at = NULL, locked_by = NULL, run_after = now() "
            "WHERE status = 'running' AND locked_at < now() - make_interval(secs => %s)",
            (older_than_seconds,),
        ).rowcount


def sweep_applying(pool: Any, *, older_than_seconds: int = 120) -> int:
    """Recover proposals stuck in 'applying'. The per-proposal advisory lock keeps this from racing a live writer."""
    with tx(pool, "worker:sweeper") as conn:
        rows = conn.execute(
            "SELECT id FROM proposals WHERE status = 'applying' AND updated_at < now() - make_interval(secs => %s)",
            (older_than_seconds,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('apply_writeback', %s, %s) "
                "ON CONFLICT (dedupe_key) DO UPDATE SET status = 'queued', run_after = now(), attempts = 0 "
                "WHERE jobs.status IN ('done','dead')",
                (Jsonb({"proposal_id": str(row["id"])}), f"apply:{row['id']}"),
            )
    return len(rows)
