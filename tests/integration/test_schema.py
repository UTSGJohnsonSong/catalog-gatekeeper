"""The database enforces what matters even if application code is wrong."""

from __future__ import annotations

import itertools

import psycopg
import pytest
from psycopg.types.json import Jsonb

from gatekeeper.domain.states import PROPOSAL_STATUSES, TRANSITIONS
from tests.conftest import seed_product


def insert_proposal(conn, ctx, product_id, status="pending_review", approved_hash=None, proposed_hash_value="h1"):
    return conn.execute(
        "INSERT INTO proposals (shop_id, product_id, base_content_hash, proposed, proposed_hash, status, approved_hash) "
        "VALUES (%s,%s,'base',%s,%s,%s,%s) RETURNING id",
        (ctx.shop_id, product_id, Jsonb({"seo_title": "x"}), proposed_hash_value, status, approved_hash),
    ).fetchone()["id"]


def test_transition_table_equals_python_state_machine(ctx):
    with ctx.pool.connection() as conn:
        rows = conn.execute("SELECT from_status::text AS f, to_status::text AS t FROM proposal_transitions").fetchall()
    from_db = {(r["f"], r["t"]) for r in rows}
    from_code = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert from_db == from_code


@pytest.mark.parametrize("from_status,to_status", list(itertools.product(PROPOSAL_STATUSES, PROPOSAL_STATUSES)))
def test_every_status_pair_is_allowed_or_refused_by_the_trigger(ctx, from_status, to_status):
    product_id = seed_product(ctx)
    needs_hash = from_status in ("approved", "applying", "applied")
    with ctx.pool.connection() as conn:
        proposal_id = insert_proposal(conn, ctx, product_id, from_status, "ahash" if needs_hash else None)
    allowed = to_status in TRANSITIONS[from_status]
    new_hash = "ahash" if to_status in ("approved", "applying", "applied") else None
    if from_status == to_status:
        return
    with ctx.pool.connection() as conn:
        if allowed:
            conn.execute(
                "UPDATE proposals SET status = %s, approved_hash = coalesce(approved_hash, %s) WHERE id = %s",
                (to_status, new_hash, proposal_id),
            )
        else:
            with pytest.raises(psycopg.errors.Error):
                conn.execute(
                    "UPDATE proposals SET status = %s, approved_hash = coalesce(approved_hash, %s) WHERE id = %s",
                    (to_status, new_hash, proposal_id),
                )
            conn.rollback()


def test_proposal_content_is_immutable(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        proposal_id = insert_proposal(conn, ctx, product_id)
    for column, value in (
        ("proposed", Jsonb({"seo_title": "tampered"})),
        ("proposed_hash", "other"),
        ("base_content_hash", "other"),
    ):
        with ctx.pool.connection() as conn, pytest.raises(psycopg.errors.RaiseException):
            conn.execute(f"UPDATE proposals SET {column} = %s WHERE id = %s", (value, proposal_id))


def test_approved_hash_cannot_change_once_set(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        proposal_id = insert_proposal(conn, ctx, product_id, "approved", "ahash")
    with ctx.pool.connection() as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE proposals SET approved_hash = 'other' WHERE id = %s", (proposal_id,))


def test_one_open_proposal_per_product(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        first = insert_proposal(conn, ctx, product_id, proposed_hash_value="h1")
    with ctx.pool.connection() as conn, pytest.raises(psycopg.errors.UniqueViolation):
        insert_proposal(conn, ctx, product_id, proposed_hash_value="h2")
    with ctx.pool.connection() as conn:
        conn.execute("UPDATE proposals SET status = 'rejected' WHERE id = %s", (first,))
        insert_proposal(conn, ctx, product_id, proposed_hash_value="h2")


def test_blocked_proposals_do_not_hold_the_open_slot(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        insert_proposal(conn, ctx, product_id, "blocked", proposed_hash_value="h1")
        insert_proposal(conn, ctx, product_id, "pending_review", proposed_hash_value="h2")


def test_audit_log_is_append_only(ctx):
    with ctx.pool.connection() as conn:
        conn.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id) VALUES ('t','a','e','1')")
    for statement in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with ctx.pool.connection() as conn, pytest.raises(psycopg.errors.RaiseException):
            conn.execute(statement)


def test_status_changes_write_an_audit_row_with_the_actor(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        proposal_id = insert_proposal(conn, ctx, product_id)
    with ctx.pool.connection() as conn:
        conn.execute("SELECT set_config('gatekeeper.actor', 'reviewer:alice', true)")
        conn.execute("UPDATE proposals SET status = 'rejected' WHERE id = %s", (proposal_id,))
        row = conn.execute(
            "SELECT actor, action FROM audit_log WHERE entity_id = %s AND action = 'proposal.rejected'",
            (str(proposal_id),),
        ).fetchone()
    assert row["actor"] == "reviewer:alice"


def test_the_judge_can_never_insert_a_block(ctx):
    product_id = seed_product(ctx)
    with ctx.pool.connection() as conn:
        proposal_id = insert_proposal(conn, ctx, product_id)
    with ctx.pool.connection() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO validation_results (proposal_id, rule_id, rule_version, kind, severity, passed, evidence) "
            "VALUES (%s,'J01',1,'judge','block',false,'{}')",
            (proposal_id,),
        )


def test_down_migration_exists_and_drops_everything(database_url):
    from gatekeeper.migrate import MIGRATIONS

    down = (MIGRATIONS / "0001_init.down.sql").read_text(encoding="utf-8")
    for table in ("proposals", "writebacks", "webhook_events", "jobs", "audit_log"):
        assert table in down
