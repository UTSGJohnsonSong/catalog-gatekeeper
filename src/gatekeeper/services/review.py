"""Human decisions. Approval binds the reviewer to the exact content they were shown."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.db import tx
from gatekeeper.domain.product import approval_hash
from gatekeeper.domain.risk import HIGH_RISK_THRESHOLD
from gatekeeper.services.catalog import write_audit


class ReviewConflict(Exception):
    """The proposal is not in a state, or not the content, that this decision was made against."""


class ReviewInvalid(Exception):
    pass


def review_hash_for(row: dict[str, Any], product_gid: str) -> str:
    return approval_hash(
        proposal_id=str(row["id"]),
        shop_id=row["shop_id"],
        product_gid=product_gid,
        base_content_hash=row["base_content_hash"],
        proposed=row["proposed"],
    )


def enqueue_apply(conn: Any, proposal_id: str) -> None:
    conn.execute(
        "INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('apply_writeback', %s, %s) "
        "ON CONFLICT (dedupe_key) DO UPDATE SET status = 'queued', run_after = now(), attempts = 0, last_error = NULL "
        "WHERE jobs.status IN ('done','dead')",
        (Jsonb({"proposal_id": proposal_id}), f"apply:{proposal_id}"),
    )


def approve(pool: Any, proposal_id: str, *, reviewer: str, review_hash: str, confirm_high_risk: bool = False) -> str:
    stale_reason: str | None = None
    with tx(pool, f"reviewer:{reviewer}") as conn:
        row = conn.execute(
            "SELECT p.*, pr.content_hash, pr.gid FROM proposals p JOIN products pr ON pr.id = p.product_id "
            "WHERE p.id = %s FOR UPDATE OF p",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] != "pending_review":
            raise ReviewConflict(f"proposal is {row['status']}")
        if row["base_content_hash"] != row["content_hash"]:
            conn.execute("UPDATE proposals SET status = 'stale' WHERE id = %s", (proposal_id,))
            stale_reason = "the product changed after this fix was proposed"
        else:
            expected = review_hash_for(row, row["gid"])
            if expected != review_hash:
                raise ReviewConflict("proposal changed since you loaded it")
            if row["risk_score"] >= HIGH_RISK_THRESHOLD and not confirm_high_risk:
                raise ReviewInvalid("high-risk proposal needs explicit confirmation")
            conn.execute(
                "INSERT INTO reviews (proposal_id, reviewer, decision, approved_hash) VALUES (%s,%s,'approve',%s)",
                (proposal_id, reviewer, expected),
            )
            conn.execute(
                "UPDATE proposals SET status = 'approved', approved_hash = %s, decided_at = now() "
                "WHERE id = %s AND status = 'pending_review'",
                (expected, proposal_id),
            )
            enqueue_apply(conn, proposal_id)
            return expected
    # Outside the block so the stale transition commits; raising inside would roll it back.
    raise ReviewConflict(stale_reason or "conflict")


def reject(pool: Any, proposal_id: str, *, reviewer: str, note: str) -> None:
    if len(note.strip()) < 5:
        raise ReviewInvalid("a rejection needs a reason of at least 5 characters")
    with tx(pool, f"reviewer:{reviewer}") as conn:
        row = conn.execute("SELECT status, shop_id FROM proposals WHERE id = %s FOR UPDATE", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] not in ("pending_review", "approved"):
            raise ReviewConflict(f"proposal is {row['status']}")
        conn.execute(
            "INSERT INTO reviews (proposal_id, reviewer, decision, note) VALUES (%s,%s,'reject',%s) "
            "ON CONFLICT (proposal_id) DO UPDATE SET decision = 'reject', note = EXCLUDED.note, "
            "reviewer = EXCLUDED.reviewer, approved_hash = NULL",
            (proposal_id, reviewer, note.strip()),
        )
        conn.execute("UPDATE proposals SET status = 'rejected', decided_at = now() WHERE id = %s", (proposal_id,))
        write_audit(
            conn,
            f"reviewer:{reviewer}",
            "proposal.reject_note",
            "proposal",
            proposal_id,
            row["shop_id"],
            {"note": note.strip()[:500]},
        )
