from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest

from gatekeeper import jobs
from gatekeeper.agent.loop import run_agent
from gatekeeper.domain.product import content_hash, fields_hash
from gatekeeper.services import review
from gatekeeper.services.sync import sync_catalog
from gatekeeper.services.writeback import MAX_APPLY_ATTEMPTS, apply_proposal, set_kill_switch
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyThrottled, ShopifyTransient
from tests.conftest import make_product, seed_product

GID = "gid://shopify/Product/1"


def pending_proposal(ctx) -> tuple[str, str, int]:
    product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>", seo_title=""))
    result = run_agent(ctx, product_id)
    assert result.proposal_ids, result
    proposal_id = result.proposal_ids[0]
    return proposal_id, _hash(ctx, proposal_id), product_id


def _hash(ctx, proposal_id):
    with ctx.pool.connection() as conn:
        row = conn.execute(
            "SELECT p.*, pr.gid FROM proposals p JOIN products pr ON pr.id = p.product_id WHERE p.id = %s",
            (proposal_id,),
        ).fetchone()
    return review.review_hash_for(row, row["gid"])


def status_of(ctx, proposal_id):
    with ctx.pool.connection() as conn:
        return conn.execute("SELECT status::text AS s FROM proposals WHERE id = %s", (proposal_id,)).fetchone()["s"]


def approved_proposal(ctx):
    proposal_id, review_hash, product_id = pending_proposal(ctx)
    review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash)
    return proposal_id, product_id


class TestApprove:
    def test_approve_records_reviewer_and_enqueues_apply_once(self, ctx):
        proposal_id, review_hash, _ = pending_proposal(ctx)
        review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash)
        with ctx.pool.connection() as conn:
            row = conn.execute(
                "SELECT reviewer, approved_hash FROM reviews WHERE proposal_id = %s", (proposal_id,)
            ).fetchone()
            audit = conn.execute(
                "SELECT actor FROM audit_log WHERE entity_id = %s AND action = 'proposal.approved'", (proposal_id,)
            ).fetchone()
            job_count = conn.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'apply_writeback'").fetchone()["n"]
        assert row["reviewer"] == "alice" and row["approved_hash"] == review_hash
        assert audit["actor"] == "reviewer:alice" and job_count == 1

    def test_wrong_review_hash_is_a_conflict(self, ctx):
        proposal_id, _, _ = pending_proposal(ctx)
        with pytest.raises(review.ReviewConflict):
            review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash="0" * 64)
        assert status_of(ctx, proposal_id) == "pending_review"

    def test_second_approval_conflicts(self, ctx):
        proposal_id, review_hash, _ = pending_proposal(ctx)
        review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash)
        with pytest.raises(review.ReviewConflict):
            review.approve(ctx.pool, proposal_id, reviewer="bob", review_hash=review_hash)

    def test_blocked_proposal_cannot_be_approved(self, ctx):
        from gatekeeper.llm.scripted import ScriptedProvider, tool_call, turn

        product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        provider = ScriptedProvider(
            [
                turn(
                    tool_call("propose_fix", changes={"description_html": "<p>Short.</p>"}, rationale="x", citations=[])
                ),
                turn(tool_call("finish", outcome="cannot_fix", summary="x", flags=[])),
            ]
        )
        proposal_id = run_agent(replace(ctx, provider=provider), product_id).proposal_ids[0]
        assert status_of(ctx, proposal_id) == "blocked"
        with pytest.raises(review.ReviewConflict):
            review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=_hash(ctx, proposal_id))

    def test_product_change_marks_stale_at_approval_time(self, ctx):
        proposal_id, review_hash, product_id = pending_proposal(ctx)
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE products SET content_hash = 'changed' WHERE id = %s", (product_id,))
        with pytest.raises(review.ReviewConflict):
            review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash)
        assert status_of(ctx, proposal_id) == "stale", "the stale transition must commit, not roll back with the error"

    def test_high_risk_needs_explicit_confirmation(self, ctx):
        proposal_id, review_hash, _ = pending_proposal(ctx)
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE proposals SET risk_score = 60 WHERE id = %s", (proposal_id,))
        with pytest.raises(review.ReviewInvalid):
            review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash)
        review.approve(ctx.pool, proposal_id, reviewer="alice", review_hash=review_hash, confirm_high_risk=True)

    def test_reject_needs_a_reason(self, ctx):
        proposal_id, _, _ = pending_proposal(ctx)
        with pytest.raises(review.ReviewInvalid):
            review.reject(ctx.pool, proposal_id, reviewer="alice", note="no")
        review.reject(ctx.pool, proposal_id, reviewer="alice", note="wrong tone for this brand")
        assert status_of(ctx, proposal_id) == "rejected"

    def test_parallel_approvals_have_one_winner(self, ctx):
        proposal_id, review_hash, _ = pending_proposal(ctx)
        barrier = threading.Barrier(2)

        def attempt(who):
            barrier.wait()
            try:
                review.approve(ctx.pool, proposal_id, reviewer=who, review_hash=review_hash)
                return "ok"
            except (review.ReviewConflict, psycopg.errors.UniqueViolation):
                return "conflict"

        with ThreadPoolExecutor(2) as executor:
            results = sorted(executor.map(attempt, ["alice", "bob"]))
        assert results == ["conflict", "ok"]
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM reviews").fetchone()["n"] == 1
            assert conn.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'apply_writeback'").fetchone()["n"] == 1


class TestApply:
    def test_happy_path_writes_store_and_verifies(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        assert apply_proposal(ctx, proposal_id).status == "applied"
        assert status_of(ctx, proposal_id) == "applied"
        stored = ctx.shopify.get_product(GID)
        assert "belongs to our" in stored.description_html and stored.seo_title
        with ctx.pool.connection() as conn:
            wb = conn.execute("SELECT status, before_snapshot, expected_after_hash FROM writebacks").fetchone()
            open_findings = conn.execute(
                "SELECT count(*) AS n FROM findings WHERE status = 'open' AND fixable"
            ).fetchone()["n"]
            local = conn.execute("SELECT content_hash FROM products").fetchone()["content_hash"]
        assert wb["status"] == "verified" and wb["before_snapshot"]["description_html"] == "<p>Nice bottle.</p>"
        assert wb["expected_after_hash"] == fields_hash(stored)
        assert open_findings == 0, "fixed findings resolve"
        assert local == content_hash(stored), "local copy matches, so the echo webhook is a no-op"

    def test_applying_twice_writes_once(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        assert apply_proposal(ctx, proposal_id).status == "applied"
        assert apply_proposal(ctx, proposal_id).status == "applied"
        assert ctx.shopify.update_calls == 1

    def test_parallel_applies_write_once(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        barrier = threading.Barrier(4)

        def attempt(_):
            barrier.wait()
            return apply_proposal(ctx, proposal_id).status

        with ThreadPoolExecutor(4) as executor:
            results = list(executor.map(attempt, range(4)))
        assert ctx.shopify.update_calls == 1, results
        assert "applied" in results and set(results) <= {"applied", "in_progress"}
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM writebacks").fetchone()["n"] == 1

    def test_crash_after_the_write_recovers_without_writing_again(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        real_get = ctx.shopify.get_product
        calls = {"n": 0}

        def crash_on_read_back(gid):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ShopifyTransient("process died before read-back")
            return real_get(gid)

        ctx.shopify.get_product = crash_on_read_back
        with pytest.raises(ShopifyTransient):
            apply_proposal(ctx, proposal_id)
        assert status_of(ctx, proposal_id) == "applying" and ctx.shopify.update_calls == 1
        ctx.shopify.get_product = real_get
        assert apply_proposal(ctx, proposal_id).status == "applied"
        assert ctx.shopify.update_calls == 1, "the second attempt saw the write landed and did not repeat it"

    def test_crash_before_the_write_retries_and_writes_once(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.fail_next(1, "throttle")
        with pytest.raises(ShopifyThrottled):
            apply_proposal(ctx, proposal_id)
        assert status_of(ctx, proposal_id) == "applying"
        assert apply_proposal(ctx, proposal_id).status == "applied"

    def test_store_edit_before_write_goes_stale_and_keeps_the_merchants_text(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.external_edit(GID, description_html="<p>The merchant wrote this just now.</p>")
        assert apply_proposal(ctx, proposal_id).status == "stale"
        assert ctx.shopify.update_calls == 0
        assert ctx.shopify.get_product(GID).description_html == "<p>The merchant wrote this just now.</p>"

    def test_race_between_refetch_and_mutation_is_caught_by_the_fixture_precondition(self, ctx):
        """The fixture's precondition is stricter than Shopify's API; see docs/adr/0006."""
        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.before_update = lambda gid: ctx.shopify.external_edit(
            gid, description_html="<p>Merchant raced us here.</p>"
        )
        assert apply_proposal(ctx, proposal_id).status == "stale"
        assert ctx.shopify.get_product(GID).description_html == "<p>Merchant raced us here.</p>"

    def test_user_errors_are_terminal(self, ctx):
        from gatekeeper.shopify.port import UpdateResult, UserError

        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.update_product = lambda *a, **k: UpdateResult(None, [UserError(["seo"], "bad", "INVALID")])
        assert apply_proposal(ctx, proposal_id).status == "failed"
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT user_errors FROM writebacks").fetchone()["user_errors"][0]["message"] == "bad"

    def test_read_back_mismatch_fails_loudly(self, ctx):
        from gatekeeper.shopify.port import UpdateResult

        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.update_product = lambda *a, **k: UpdateResult(None, [])
        assert apply_proposal(ctx, proposal_id).detail == "verification_mismatch"
        assert status_of(ctx, proposal_id) == "failed"

    def test_tampered_approved_hash_refuses_to_write(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        with ctx.pool.connection() as conn:
            conn.execute("ALTER TABLE proposals DISABLE TRIGGER proposals_guard")
            conn.execute("UPDATE proposals SET approved_hash = %s WHERE id = %s", ("f" * 64, proposal_id))
            conn.execute("ALTER TABLE proposals ENABLE TRIGGER proposals_guard")
        result = apply_proposal(ctx, proposal_id)
        assert result.detail == "approved_hash_mismatch" and ctx.shopify.update_calls == 0

    def test_repeated_failures_end_in_failed_for_a_human(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        ctx.shopify.fail_next(MAX_APPLY_ATTEMPTS, "transient")
        for _ in range(MAX_APPLY_ATTEMPTS):
            with pytest.raises(ShopifyTransient):
                apply_proposal(ctx, proposal_id)
        assert apply_proposal(ctx, proposal_id).status == "failed"
        assert ctx.shopify.update_calls == 0


class TestKillSwitch:
    def test_env_flag_off_defers_without_touching_the_store(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        off = replace(ctx, settings=ctx.settings.model_copy(update={"writeback_enabled": False}))
        assert apply_proposal(off, proposal_id).status == "deferred"
        assert status_of(ctx, proposal_id) == "approved" and ctx.shopify.update_calls == 0

    def test_shop_flag_off_defers_and_on_resumes(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        set_kill_switch(ctx, enabled=False, actor="admin:test")
        assert apply_proposal(ctx, proposal_id).status == "deferred"
        assert ctx.shopify.update_calls == 0
        set_kill_switch(ctx, enabled=True, actor="admin:test")
        assert apply_proposal(ctx, proposal_id).status == "applied"

    def test_flip_after_claim_releases_before_the_mutation(self, ctx):
        proposal_id, _ = approved_proposal(ctx)
        real_get = ctx.shopify.get_product

        def flip_then_read(gid):
            set_kill_switch(ctx, enabled=False, actor="admin:test")
            return real_get(gid)

        ctx.shopify.get_product = flip_then_read
        assert apply_proposal(ctx, proposal_id).status == "deferred"
        assert status_of(ctx, proposal_id) == "approved" and ctx.shopify.update_calls == 0


class TestStaleFromSync:
    def test_sync_stales_open_proposals_when_the_base_changes(self, ctx):
        proposal_id, _, _ = pending_proposal(ctx)
        ctx.shopify.external_edit(GID, title="Renamed by merchant")
        assert sync_catalog(ctx, actor="test")["staled"] == 1
        assert status_of(ctx, proposal_id) == "stale"

    def test_sync_with_no_change_keeps_proposals_open(self, ctx):
        proposal_id, _, _ = pending_proposal(ctx)
        sync_catalog(ctx, actor="test")
        assert status_of(ctx, proposal_id) == "pending_review"

    def test_older_data_cannot_overwrite_newer(self, ctx):
        from gatekeeper.services.catalog import upsert_product

        seed_product(ctx, make_product(updated_at="2026-06-01T00:00:00Z"))
        with ctx.pool.connection() as conn:
            result = upsert_product(
                conn, ctx.shop_id, make_product(title="Old title", updated_at="2026-01-01T00:00:00Z")
            )
            title = conn.execute("SELECT title FROM products").fetchone()["title"]
        assert result.product_id is None and title != "Old title"


class TestJobs:
    def test_claim_skips_locked_and_only_one_worker_gets_a_job(self, ctx):
        jobs.enqueue(ctx.pool, "sync", {})
        with ThreadPoolExecutor(4) as executor:
            claimed = list(executor.map(lambda i: jobs.claim(ctx.pool, f"w{i}"), range(4)))
        assert sum(1 for c in claimed if c) == 1

    def test_failure_backs_off_then_goes_dead(self, ctx):
        jobs.enqueue(ctx.pool, "sync", {})
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE jobs SET max_attempts = 2")
        job = jobs.claim(ctx.pool, "w")
        assert jobs.fail(ctx.pool, job, "boom") == "queued"
        assert jobs.claim(ctx.pool, "w") is None, "backoff delays the retry"
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE jobs SET run_after = now()")
        job = jobs.claim(ctx.pool, "w")
        assert jobs.fail(ctx.pool, job, "boom") == "dead"

    def test_reaper_recovers_a_dead_worker(self, ctx):
        jobs.enqueue(ctx.pool, "sync", {})
        jobs.claim(ctx.pool, "w")
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE jobs SET locked_at = now() - interval '1 hour'")
        assert jobs.reap(ctx.pool) == 1
        assert jobs.claim(ctx.pool, "w2") is not None

    def test_sweeper_requeues_stuck_applying_and_apply_finishes(self, ctx):
        from gatekeeper.worker import run_job

        proposal_id, _ = approved_proposal(ctx)
        job = jobs.claim(ctx.pool, "w")
        assert job["kind"] == "apply_writeback"
        jobs.complete(ctx.pool, job["id"])
        with ctx.pool.connection() as conn:
            conn.execute("UPDATE proposals SET status = 'applying' WHERE id = %s", (proposal_id,))
            conn.execute("ALTER TABLE proposals DISABLE TRIGGER proposals_guard")
            conn.execute("UPDATE proposals SET updated_at = now() - interval '1 hour' WHERE id = %s", (proposal_id,))
            conn.execute("ALTER TABLE proposals ENABLE TRIGGER proposals_guard")
        assert jobs.sweep_applying(ctx.pool) == 1
        job = jobs.claim(ctx.pool, "w")
        run_job(ctx, job)
        assert status_of(ctx, proposal_id) == "applied"

    def test_auth_failure_goes_straight_to_dead(self, ctx):
        from gatekeeper.worker import run_job

        def denied(**kwargs):
            raise ShopifyAuthError("401")

        ctx.shopify.list_products = denied
        jobs.enqueue(ctx.pool, "sync", {})
        run_job(ctx, jobs.claim(ctx.pool, "w"))
        with ctx.pool.connection() as conn:
            row = conn.execute("SELECT status::text AS s, last_error FROM jobs").fetchone()
        assert row["s"] == "dead" and "401" in row["last_error"]

    def test_unexpected_exception_is_recorded_not_swallowed(self, ctx):
        from gatekeeper.worker import run_job

        jobs.enqueue(ctx.pool, "no_such_kind", {})
        run_job(ctx, jobs.claim(ctx.pool, "w"))
        with ctx.pool.connection() as conn:
            row = conn.execute("SELECT status::text AS s, last_error FROM jobs").fetchone()
        assert row["s"] == "queued" and "unknown job kind" in row["last_error"]
