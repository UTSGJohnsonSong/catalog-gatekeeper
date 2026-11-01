"""A Postgres job queue: FOR UPDATE SKIP LOCKED, backoff, dead letter, and a reaper. No Redis needed."""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.db import tx

log = logging.getLogger("gatekeeper.jobs")
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
            "UPDATE jobs SET status = 'queued', attempts = greatest(attempts - 1, 0), "
            "run_after = now() + make_interval(secs => %s), locked_at = NULL, locked_by = NULL, last_error = %s "
            "WHERE id = %s",
            (seconds, reason[:500], job_id),
        )


def fail(pool: Any, job: dict[str, Any], error: str, *, retryable: bool = True) -> str:
    dead = not retryable or job["attempts"] >= job["max_attempts"]
    backoff = min(MAX_BACKOFF_SECONDS, 2 ** job["attempts"])
    with tx(pool) as conn:
        conn.execute(
            "UPDATE jobs SET status = %s, run_after = now() + make_interval(secs => %s), locked_at = NULL, "
            "locked_by = NULL, last_error = %s WHERE id = %s",
            ("dead" if dead else "queued", backoff, error[:2000], job["id"]),
        )
    if dead:
        log.error("job is dead: %s", error[:300], extra={"job_id": job["id"], "kind": job["kind"]})
    return "dead" if dead else "queued"


def reap(pool: Any, *, older_than_seconds: int = 600) -> int:
    """Recover jobs whose worker died mid-run. A job that keeps killing its worker goes dead instead of looping."""
    with tx(pool) as conn:
        rows = conn.execute(
            "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead'::job_status ELSE 'queued' END, "
            "locked_at = NULL, locked_by = NULL, run_after = now(), last_error = 'worker died while running this job' "
            "WHERE status = 'running' AND locked_at < now() - make_interval(secs => %s) RETURNING id, status::text AS status",
            (older_than_seconds,),
        ).fetchall()
    if rows:
        log.warning("reaped %d job(s) from dead workers: %s", len(rows), rows)
    return len(rows)


def sweep_applying(pool: Any, *, older_than_seconds: int = 120) -> int:
    """Recover approved fixes whose apply job finished or vanished without an end state. Queued and running jobs
    are left alone. Dead jobs are not revived automatically: they need a person and are counted in /api/ops. The
    per-proposal advisory lock keeps this from racing a live writer."""
    with tx(pool, "worker:sweeper") as conn:
        rows = conn.execute(
            "SELECT p.id FROM proposals p WHERE p.status IN ('applying','approved') "
            "AND p.updated_at < now() - make_interval(secs => %s)",
            (older_than_seconds,),
        ).fetchall()
        revived = 0
        for row in rows:
            revived += conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('apply_writeback', %s, %s) "
                "ON CONFLICT (dedupe_key) DO UPDATE SET status = 'queued', run_after = now(), attempts = 0 "
                "WHERE jobs.status = 'done'",
                (Jsonb({"proposal_id": str(row["id"])}), f"apply:{row['id']}"),
            ).rowcount
    if revived:
        log.warning("requeued %d stuck write-back(s)", revived)
    return revived
