"""Write-back. One code path serves the first run, retries and crash recovery.

A session advisory lock per proposal makes the executor exclusive for the whole operation, and the database
releases it if the session ends, so recovery does not race a live writer. Whether an earlier attempt landed is
answered by comparing hashes against the store, never by trusting local state.

Live limit: Shopify documents no precondition on productUpdate, so a merchant edit can slip in between the
refetch and the mutation (about one round trip). before_snapshot, read-back and the audit log bound that
window; they do not close it. See docs/adr/0004.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.domain.product import ALLOWED_FIELDS, Product, approval_hash, content_hash, fields_hash
from gatekeeper.services.catalog import active_title_counts, refresh_findings, upsert_product, write_audit

log = logging.getLogger("gatekeeper.writeback")
ACTOR = "worker:apply"
MAX_APPLY_ATTEMPTS = 5


@dataclass(frozen=True)
class ApplyResult:
    status: str  # applied | stale | failed | in_progress | deferred | rejected | ...
    detail: str = ""


def _advisory_key(proposal_id: str) -> int:
    return int.from_bytes(hashlib.sha256(proposal_id.encode()).digest()[:8], "big", signed=True)


def apply_proposal(ctx: AppContext, proposal_id: str) -> ApplyResult:
    if not ctx.settings.writeback_enabled:
        return ApplyResult("deferred", "WRITEBACK_ENABLED is false")
    key = _advisory_key(proposal_id)
    with ctx.pool.connection() as lock_conn:
        got = lock_conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,)).fetchone()["got"]
        lock_conn.commit()
        if not got:
            return ApplyResult("in_progress", "another worker holds this proposal")
        try:
            result = _apply_locked(ctx, proposal_id)
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
            lock_conn.commit()
    if result.status == "failed":
        log.error("write-back failed: %s", result.detail, extra={"proposal_id": proposal_id})
    elif result.status == "stale":
        log.warning("write-back found the proposal out of date: %s", result.detail, extra={"proposal_id": proposal_id})
    return result


def _transition(
    ctx: AppContext, proposal_id: str, to: str, *, writeback_status: str | None = None, **columns: Any
) -> None:
    with tx(ctx.pool, ACTOR) as conn:
        conn.execute("UPDATE proposals SET status = %s WHERE id = %s", (to, proposal_id))
        if writeback_status:
            sets = ["status = %s", "updated_at = now()"]
            values: list[Any] = [writeback_status]
            for name, value in columns.items():
                sets.append(f"{name} = %s")
                values.append(Jsonb(value))
            values.append(proposal_id)
            conn.execute(f"UPDATE writebacks SET {', '.join(sets)} WHERE proposal_id = %s", values)


def _claim(ctx: AppContext, proposal_id: str) -> tuple[dict[str, Any] | None, ApplyResult | None]:
    with tx(ctx.pool, ACTOR) as conn:
        row = conn.execute(
            "UPDATE proposals SET status = 'applying' WHERE id = %s AND status = 'approved' "
            "AND EXISTS (SELECT 1 FROM shops s WHERE s.id = shop_id AND s.writeback_enabled) RETURNING *",
            (proposal_id,),
        ).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM proposals WHERE id = %s", (proposal_id,)).fetchone()
            if row is None:
                return None, ApplyResult("failed", "unknown proposal")
            if row["status"] == "approved":
                return None, ApplyResult("deferred", "kill switch is off for this shop")
            if row["status"] != "applying":
                return None, ApplyResult(row["status"], "nothing to do")
        gid = conn.execute("SELECT gid FROM products WHERE id = %s", (row["product_id"],)).fetchone()["gid"]
        wb = conn.execute(
            "INSERT INTO writebacks (proposal_id, idempotency_key, status, attempts) VALUES (%s, %s, 'claimed', 0) "
            "ON CONFLICT (proposal_id) DO UPDATE SET updated_at = now() RETURNING status, attempts",
            (proposal_id, hashlib.sha256(f"{proposal_id}{row['approved_hash']}".encode()).hexdigest()),
        ).fetchone()
    return {**row, "gid": gid, "attempts": wb["attempts"], "writeback_status": wb["status"]}, None


def _apply_locked(ctx: AppContext, proposal_id: str) -> ApplyResult:
    row, early = _claim(ctx, proposal_id)
    if early is not None:
        return early
    assert row is not None

    expected_approval = approval_hash(
        proposal_id=proposal_id,
        shop_id=row["shop_id"],
        product_gid=row["gid"],
        base_content_hash=row["base_content_hash"],
        proposed=row["proposed"],
    )
    if expected_approval != row["approved_hash"]:
        _transition(
            ctx, proposal_id, "failed", writeback_status="failed", user_errors=[{"message": "approved_hash_mismatch"}]
        )
        return ApplyResult("failed", "approved_hash_mismatch")

    proposed = {k: v for k, v in row["proposed"].items() if k in ALLOWED_FIELDS}
    current = ctx.shopify.get_product(row["gid"])
    if current is None:
        _transition(ctx, proposal_id, "stale", writeback_status="stale")
        return ApplyResult("stale", "product no longer exists")
    target_fields_hash = fields_hash(current.with_changes(proposed))
    # Checked first, so an exhausted attempt count can never mislabel a write that did land.
    if fields_hash(current) == target_fields_hash:
        return _finish_applied(ctx, proposal_id, row, current)
    if content_hash(current) != row["base_content_hash"]:
        if row["writeback_status"] == "sent":
            # A mutation was sent earlier with an unknown result, and the store now matches neither version.
            # The write may be live under a later edit, so "nothing was written" would be a false statement.
            _transition(
                ctx,
                proposal_id,
                "failed",
                writeback_status="failed",
                user_errors=[{"message": "outcome_unknown: an earlier write may have reached the store"}],
            )
            return ApplyResult("failed", "outcome_unknown")
        _transition(
            ctx, proposal_id, "stale", writeback_status="stale", response={"note": "store changed before write"}
        )
        return ApplyResult("stale", "store content differs from the approved base")
    if row["attempts"] >= MAX_APPLY_ATTEMPTS:
        _transition(
            ctx,
            proposal_id,
            "failed",
            writeback_status="failed",
            user_errors=[{"message": f"needs_human: gave up after {row['attempts']} write attempts"}],
        )
        return ApplyResult("failed", "too many write attempts")

    with tx(ctx.pool, ACTOR) as conn:
        enabled = conn.execute("SELECT writeback_enabled FROM shops WHERE id = %s", (row["shop_id"],)).fetchone()[
            "writeback_enabled"
        ]
        if not enabled:
            conn.execute("UPDATE proposals SET status = 'approved' WHERE id = %s", (proposal_id,))
            return ApplyResult("deferred", "kill switch flipped during apply")
        before = {name: getattr(current, name) for name in ALLOWED_FIELDS}
        # Committed before the mutation: if the process dies mid-call, the restore data already exists.
        conn.execute(
            "UPDATE writebacks SET status = 'sent', attempts = attempts + 1, "
            "before_snapshot = coalesce(before_snapshot, %s), expected_after_hash = %s, request = %s, "
            "updated_at = now() WHERE proposal_id = %s",
            (Jsonb(before), target_fields_hash, Jsonb(proposed), proposal_id),
        )

    # Throttling and transient errors propagate: the job retries with backoff and re-enters above.
    result = ctx.shopify.update_product(row["gid"], proposed, expected_content_hash=row["base_content_hash"])
    if result.user_errors:
        errors = [{"field": e.field, "message": e.message, "code": e.code} for e in result.user_errors]
        if any(e.code == "STALE_OBJECT" for e in result.user_errors):
            _transition(ctx, proposal_id, "stale", writeback_status="stale", user_errors=errors)
            return ApplyResult("stale", "store rejected the write as stale")
        _transition(ctx, proposal_id, "failed", writeback_status="failed", user_errors=errors)
        return ApplyResult("failed", "store returned userErrors")

    refetched = ctx.shopify.get_product(row["gid"])
    if refetched is None or fields_hash(refetched) != target_fields_hash:
        got = fields_hash(refetched) if refetched else None
        _transition(
            ctx,
            proposal_id,
            "failed",
            writeback_status="failed",
            response={"expected_after_hash": target_fields_hash, "got": got},
            user_errors=[{"message": "verification_mismatch: the store accepted the write but holds different text"}],
        )
        return ApplyResult("failed", "verification_mismatch")
    return _finish_applied(ctx, proposal_id, row, refetched)


def _finish_applied(ctx: AppContext, proposal_id: str, row: dict[str, Any], product: Product) -> ApplyResult:
    with tx(ctx.pool, ACTOR) as conn:
        conn.execute("UPDATE proposals SET status = 'applied', applied_at = now() WHERE id = %s", (proposal_id,))
        conn.execute(
            "UPDATE writebacks SET status = 'verified', updated_at = now() WHERE proposal_id = %s", (proposal_id,)
        )
        # Refresh the local copy now, so the echo webhook that follows finds an identical hash and does nothing.
        result = upsert_product(conn, row["shop_id"], product)
        if result.product_id is not None:
            titles = active_title_counts(conn, row["shop_id"])
            refresh_findings(conn, row["shop_id"], result.product_id, product, titles)
    return ApplyResult("applied")


def set_kill_switch(ctx: AppContext, *, enabled: bool, actor: str) -> None:
    with tx(ctx.pool, actor) as conn:
        conn.execute("UPDATE shops SET writeback_enabled = %s WHERE id = %s", (enabled, ctx.shop_id))
        write_audit(
            conn, actor, "writeback.enabled" if enabled else "writeback.disabled", "shop", str(ctx.shop_id), ctx.shop_id
        )
