"""A public demo against real Postgres: who may do what, the request limits, and the reset."""

from __future__ import annotations

import json

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from gatekeeper import seed
from gatekeeper.api.app import create_app
from gatekeeper.config import Settings
from gatekeeper.jobs import claim, complete
from gatekeeper.migrate import bootstrap_roles
from gatekeeper.seed import _RESET_LOCK, KEPT_TABLES, RESET_TABLES, NotTheDemoDatabase, load_store, reset_demo
from gatekeeper.shopify.webhook_hmac import sign
from gatekeeper.worker import run_job
from tests.conftest import seed_product

ADMIN = {"Authorization": "Bearer demo-admin"}
REVIEWER = {"Authorization": "Bearer demo-reviewer"}


@pytest.fixture
def public_settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        database_url=database_url,
        demo_mode=True,
        public_demo=True,
        demo_reset_minutes=30,
        writeback_enabled=True,
        shopify_webhook_secret="test-secret",
        rate_limit_per_minute=3,
        trusted_proxy_hops=1,
    )


@pytest.fixture
def client(ctx, public_settings):
    with TestClient(create_app(public_settings, ctx)) as test_client:
        yield test_client


def visitor(address: str) -> dict[str, str]:
    """Headers as the host's proxy would send them: whatever the client claimed, then the address it really saw."""
    return {**REVIEWER, "X-Forwarded-For": f"6.6.6.6, {address}"}


def drain(ctx) -> None:
    while (job := claim(ctx.pool, "test")) is not None:
        run_job(ctx, job)


def counts(pool) -> dict[str, int]:
    with pool.connection() as conn:
        return {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] for table in RESET_TABLES}


class TestAccess:
    def test_the_admin_demo_token_is_refused(self, client):
        assert client.get("/api/whoami", headers=ADMIN).status_code == 401
        assert client.get("/api/whoami", headers=REVIEWER).json() == {"name": "demo_reviewer", "role": "reviewer"}

    @pytest.mark.parametrize(
        ("path", "body"),
        [("/api/admin/sync", None), ("/api/admin/runs", {"limit": 5}), ("/api/admin/writeback", {"enabled": False})],
    )
    def test_admin_actions_are_out_of_reach(self, client, path, body):
        assert client.post(path, json=body, headers=visitor("203.0.113.1")).status_code == 403

    def test_meta_tells_the_page_which_token_to_fill_in(self, client):
        meta = client.get("/api/meta").json()
        assert (meta["public_demo"], meta["demo_token"], meta["demo_reset_minutes"]) == (True, "demo-reviewer", 30)

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_api_docs_are_not_served(self, client, path):
        assert client.get(path).status_code == 404


class TestLimits:
    def test_changes_are_rate_limited_per_client(self, client):
        statuses = [client.post("/api/admin/sync", headers=visitor("203.0.113.1")).status_code for _ in range(4)]
        assert statuses == [403, 403, 403, 429]
        limited = client.post("/api/admin/sync", headers=visitor("203.0.113.1"))
        assert limited.headers["Retry-After"] == "20"
        assert limited.headers["Strict-Transport-Security"]
        # A forged left-hand entry does not buy a fresh bucket, and another real address still gets through.
        assert (
            client.post("/api/admin/sync", headers={**REVIEWER, "X-Forwarded-For": "1.1.1.1, 203.0.113.1"}).status_code
            == 429
        )
        assert client.post("/api/admin/sync", headers=visitor("198.51.100.7")).status_code == 403

    def test_a_second_forwarded_line_is_read_too(self, client):
        # A proxy may add its own header line. Reading only the first would let the client's line decide.
        for _ in range(3):
            client.post("/api/admin/sync", headers={**REVIEWER, "X-Forwarded-For": "6.6.6.6"})
        two_lines = httpx.Headers(
            [
                ("authorization", "Bearer demo-reviewer"),
                ("x-forwarded-for", "6.6.6.6"),
                ("x-forwarded-for", "198.51.100.9"),
            ]
        )
        assert client.post("/api/admin/sync", headers=two_lines).status_code == 403

    def test_reads_have_their_own_budget_and_health_checks_have_none(self, ctx, public_settings):
        settings = public_settings.model_copy(update={"read_limit_per_minute": 2})
        with TestClient(create_app(settings, ctx)) as reader:
            statuses = [reader.get("/api/whoami", headers=visitor("203.0.113.4")).status_code for _ in range(3)]
            assert statuses == [200, 200, 429]
            assert reader.get("/api/whoami", headers=visitor("203.0.113.4")).json()["detail"].startswith("Too many")
            assert {reader.get("/healthz", headers=visitor("203.0.113.4")).status_code for _ in range(5)} == {200}
            # Changes still have their own allowance.
            assert reader.post("/api/admin/sync", headers=visitor("203.0.113.4")).status_code == 403

    def test_the_whole_demo_has_a_ceiling_that_new_addresses_cannot_dodge(self, ctx, public_settings):
        settings = public_settings.model_copy(
            update={"rate_limit_global_per_minute": 2, "read_limit_global_per_minute": 2}
        )
        with TestClient(create_app(settings, ctx)) as many:
            writes = [many.post("/api/admin/sync", headers=visitor(f"203.0.113.{n}")) for n in (10, 11, 12)]
            reads = [many.get("/api/whoami", headers=visitor(f"198.51.100.{n}")) for n in (10, 11, 12)]
        assert [w.status_code for w in writes] == [403, 403, 429]
        assert [r.status_code for r in reads] == [200, 200, 429]
        assert writes[2].json()["detail"].startswith("The demo is busy.")
        assert writes[2].headers["Retry-After"] == "30"

    def test_the_demos_own_loopback_webhook_is_not_counted(self, ctx, public_settings):
        with TestClient(create_app(public_settings, ctx), client=("127.0.0.1", 50000)) as loopback:
            statuses = {loopback.post("/api/admin/sync", headers=REVIEWER).status_code for _ in range(6)}
        assert statuses == {403}

    def test_loopback_that_carries_a_forwarded_header_is_a_visitor_and_counted(self, ctx, public_settings):
        # A reverse proxy on the same machine connects from 127.0.0.1 but adds the visitor's address.
        with TestClient(create_app(public_settings, ctx), client=("127.0.0.1", 50000)) as proxied:
            headers = {**REVIEWER, "X-Forwarded-For": "203.0.113.5"}
            statuses = [proxied.post("/api/admin/sync", headers=headers).status_code for _ in range(4)]
        assert statuses == [403, 403, 403, 429]

    def test_simulated_edits_deliver_their_webhooks_without_any_allowance_running_out(
        self, ctx, public_settings, monkeypatch
    ):
        product_id = seed_product(ctx)
        app = create_app(public_settings, ctx)
        with TestClient(app) as outside, TestClient(app, client=("127.0.0.1", 50000)) as loopback:

            def deliver(url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> httpx.Response:
                return loopback.post(httpx.URL(url).path, content=content, headers=headers)

            monkeypatch.setattr(httpx, "post", deliver)
            edit = {"product_id": product_id}
            # Six webhooks in all, more than one bucket allows: only the exemption lets every one through.
            statuses = [
                outside.post("/demo/simulate-edit", json=edit, headers=visitor(address)).status_code
                for address in ("203.0.113.2", "203.0.113.3")
                for _ in range(3)
            ]
        assert statuses == [200] * 6
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"] == 6

    def test_large_bodies_are_refused_before_parsing(self, client):
        big = "x" * (70 * 1024)
        declared = client.post(
            "/api/proposals/00000000-0000-0000-0000-000000000000/reject", content=big, headers=REVIEWER
        )
        assert declared.status_code == 413
        assert "over 65536 bytes" in declared.json()["detail"]
        # A public demo has no real webhooks, so its webhook route takes the small cap too.
        assert client.post("/webhooks/shopify", content=big).status_code == 413
        chunked = client.post(
            "/api/proposals/00000000-0000-0000-0000-000000000000/reject",
            content=iter([big[:40_000].encode(), big[40_000:].encode()]),
            headers=REVIEWER,
        )
        assert chunked.status_code == 413

    def test_outside_a_public_demo_real_webhooks_keep_their_larger_cap(self, ctx):
        # Shopify sends whole products, which can pass 64 KiB. The small cap must not reach this route then.
        raw = json.dumps({"admin_graphql_api_id": "gid://shopify/Product/1", "padding": "p" * 100_000}).encode()
        headers = {
            "X-Shopify-Hmac-Sha256": sign("test-secret", raw),
            "X-Shopify-Webhook-Id": "wh-large",
            "X-Shopify-Topic": "products/update",
            "X-Shopify-Shop-Domain": "demo-store.myshopify.com",
        }
        with TestClient(create_app(ctx.settings, ctx)) as private:
            response = private.post("/webhooks/shopify", content=raw, headers=headers)
        assert response.status_code == 200 and response.json() == {"status": "accepted"}

    def test_simulated_edit_text_is_capped(self, ctx, client):
        product_id = seed_product(ctx)
        response = client.post(
            "/demo/simulate-edit",
            json={"product_id": product_id, "description_html": "y" * 20_001},
            headers=visitor("203.0.113.3"),
        )
        assert response.status_code == 422


class TestReset:
    def test_a_used_demo_returns_exactly_to_its_first_state(self, ctx, pool, public_settings):
        settings = public_settings.model_copy(update={"demo_autorun": 6})
        load_store(settings)
        baseline = counts(pool)
        assert baseline["jobs"] == 6
        drain(ctx)
        with TestClient(create_app(settings, ctx)) as client:
            items = client.get("/api/queue", headers=REVIEWER).json()["items"]
            pending = [item for item in items if item["status"] == "pending_review"]
            assert len(pending) >= 2
            detail = client.get(f"/api/proposals/{pending[0]['id']}", headers=REVIEWER).json()
            approve = {"review_hash": detail["review_hash"]}
            approved = client.post(f"/api/proposals/{pending[0]['id']}/approve", json=approve, headers=REVIEWER)
            assert approved.status_code == 200
            reject = {"note": "not this one"}
            rejected = client.post(f"/api/proposals/{pending[1]['id']}/reject", json=reject, headers=REVIEWER)
            assert rejected.status_code == 200
        drain(ctx)
        assert counts(pool) != baseline

        assert reset_demo(settings) is True

        assert counts(pool) == baseline
        with pool.connection() as conn:
            queued = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE kind = 'agent_run' AND status = 'queued'"
            ).fetchone()["n"]
            assert queued == 6
            assert conn.execute("SELECT count(*) AS n FROM policy_chunks").fetchone()["n"] > 0

    def test_visitor_edits_and_the_log_are_gone_while_the_shop_and_heartbeat_stay(self, pool, public_settings):
        load_store(public_settings)
        with pool.connection() as conn:
            shop = conn.execute("SELECT id FROM shops").fetchone()["id"]
            conn.execute("INSERT INTO heartbeats (name, at) VALUES ('worker', now()) ON CONFLICT (name) DO NOTHING")
            conn.execute('UPDATE fixture.products SET data = data || \'{"title": "Changed by a visitor"}\'')
            conn.execute(
                "INSERT INTO audit_log (actor, action, entity_type, entity_id) VALUES ('visitor', 'test', 'x', 'y')"
            )

        reset_demo(public_settings)

        with pool.connection() as conn:
            titles = {r["t"] for r in conn.execute("SELECT data->>'title' AS t FROM fixture.products").fetchall()}
            assert "Changed by a visitor" not in titles
            assert conn.execute("SELECT count(*) AS n FROM audit_log WHERE actor = 'visitor'").fetchone()["n"] == 0
            # The shop row and the heartbeat survive, so a running API and worker are not knocked over.
            assert conn.execute("SELECT id FROM shops").fetchone()["id"] == shop
            assert conn.execute("SELECT count(*) AS n FROM heartbeats WHERE name = 'worker'").fetchone()["n"] == 1
            # And each finished reset leaves a timestamp, so an operator can see when the last one happened.
            assert conn.execute("SELECT count(*) AS n FROM heartbeats WHERE name = 'demo_reset'").fetchone()["n"] == 1

    def test_identities_continue_so_an_old_job_cannot_finish_a_new_one(self, pool, public_settings):
        settings = public_settings.model_copy(update={"demo_autorun": 2})
        load_store(settings)
        old = claim(pool, "worker-before-the-reset")
        reset_demo(settings)
        complete(pool, old["id"])  # The old worker finishes late, after the reset.
        with pool.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"] == 2

    def test_a_busy_table_makes_the_reset_give_up_without_touching_anything(
        self, pool, public_settings, database_url, monkeypatch
    ):
        load_store(public_settings)
        before = counts(pool)
        monkeypatch.setattr(seed, "LOCK_ATTEMPTS", 2)
        monkeypatch.setattr(seed, "LOCK_RETRY_SECONDS", 0.01)
        with psycopg.connect(database_url) as reader:
            reader.execute("LOCK TABLE products IN ACCESS SHARE MODE")  # Held until this transaction ends.
            with pytest.raises(psycopg.errors.LockNotAvailable):
                reset_demo(public_settings)
        assert counts(pool) == before

    def test_reset_refuses_anything_but_a_public_demo(self, database_url):
        with pytest.raises(NotTheDemoDatabase):
            reset_demo(Settings(env="test", database_url=database_url))
        with pytest.raises(NotTheDemoDatabase):
            reset_demo(Settings(env="test", database_url=database_url, demo_mode=True))

    def test_reset_refuses_a_database_that_served_another_store_before_touching_it(self, pool, public_settings):
        load_store(public_settings)
        with pool.connection() as conn:
            conn.execute("INSERT INTO shops (domain) VALUES ('real-store.myshopify.com')")
        before = counts(pool)
        with pytest.raises(NotTheDemoDatabase, match="real-store"):
            reset_demo(public_settings)
        assert counts(pool) == before

    def test_reset_refuses_a_database_with_real_model_runs_before_touching_it(self, ctx, pool, public_settings):
        product_id = seed_product(ctx)
        with ctx.pool.connection() as conn:
            conn.execute(
                "INSERT INTO agent_runs (shop_id, product_id, provider, model, mode, prompt_version, base_content_hash) "
                "VALUES (%s, %s, 'p', 'm', 'live', 'v', 'h')",
                (ctx.shop_id, product_id),
            )
        before = counts(pool)
        with pytest.raises(NotTheDemoDatabase, match="real model"):
            reset_demo(public_settings)
        assert counts(pool) == before

    def test_reset_refuses_products_from_outside_the_demo_store(self, ctx, pool, public_settings):
        # A live store's database has its products but an empty fixture store, whatever the configuration says.
        seed_product(ctx)
        with pool.connection() as conn:
            conn.execute("DELETE FROM fixture.products")
        before = counts(pool)
        with pytest.raises(NotTheDemoDatabase, match="outside the demo store"):
            reset_demo(public_settings)
        assert counts(pool) == before

    def test_a_database_with_other_tables_is_refused(self, pool, public_settings):
        # Setup would grant the app role every table in the schema, so another application's database must not pass.
        load_store(public_settings)
        with pool.connection() as conn:
            conn.execute("CREATE TABLE someone_elses_invoices (id int)")
        try:
            before = counts(pool)
            with pytest.raises(NotTheDemoDatabase, match="someone_elses_invoices"):
                reset_demo(public_settings)
            assert counts(pool) == before
        finally:
            with pool.connection() as conn:
                conn.execute("DROP TABLE someone_elses_invoices")

    def test_a_reload_that_fails_after_the_reset_still_leaves_the_catalog_in_place(
        self, pool, public_settings, monkeypatch
    ):
        load_store(public_settings)
        catalog_size = counts(pool)["fixture.products"]

        def broken(settings: Settings) -> None:
            raise RuntimeError("database went away during the reload")

        monkeypatch.setattr(seed, "load_store", broken)
        monkeypatch.setattr(seed, "LOAD_RETRY_SECONDS", 0)
        with pytest.raises(seed.DemoLeftEmpty):
            reset_demo(public_settings)
        assert counts(pool)["fixture.products"] == catalog_size
        monkeypatch.undo()
        # The next reset is not refused as a stranger's database, and finishes the job.
        assert reset_demo(public_settings) is True
        assert counts(pool)["products"] == catalog_size

    def test_a_reset_already_running_elsewhere_is_left_alone(self, pool, public_settings, database_url):
        with psycopg.connect(database_url, autocommit=True) as other:
            other.execute("SELECT pg_advisory_lock(%s)", (_RESET_LOCK,))
            assert reset_demo(public_settings) is False

    def test_reset_turns_write_back_on_again(self, pool, public_settings):
        load_store(public_settings)
        with pool.connection() as conn:
            conn.execute("UPDATE shops SET writeback_enabled = false")
        assert reset_demo(public_settings) is True
        with pool.connection() as conn:
            assert conn.execute("SELECT writeback_enabled FROM shops").fetchone()["writeback_enabled"] is True

    def test_after_a_reset_the_audit_log_is_still_append_only(self, pool, public_settings):
        load_store(public_settings)
        reset_demo(public_settings)
        with pool.connection() as conn:
            conn.execute("INSERT INTO audit_log (actor, action, entity_type, entity_id) VALUES ('a', 'b', 'c', 'd')")
        with pytest.raises(psycopg.errors.RaiseException), pool.connection() as conn:
            conn.execute("UPDATE audit_log SET actor = 'someone else'")

    def test_every_table_is_either_reset_or_kept(self, pool):
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT CASE WHEN table_schema = 'public' THEN table_name ELSE table_schema || '.' || table_name END "
                "AS name FROM information_schema.tables "
                "WHERE table_schema IN ('public', 'fixture') AND table_type = 'BASE TABLE'"
            ).fetchall()
        tables = {r["name"] for r in rows}
        assert not set(RESET_TABLES) & set(KEPT_TABLES)
        assert tables == set(RESET_TABLES) | set(KEPT_TABLES), "classify the new table in seed.py"

    def test_the_app_role_cannot_truncate_or_rewrite_the_log(self, pool, database_url):
        bootstrap_roles(database_url, Settings(app_db_password="local-demo-app"))
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT has_table_privilege('gatekeeper_app', 'audit_log', 'TRUNCATE') AS truncate, "
                "has_table_privilege('gatekeeper_app', 'audit_log', 'UPDATE') AS update, "
                "has_table_privilege('gatekeeper_app', 'products', 'TRUNCATE') AS truncate_products"
            ).fetchone()
        assert row == {"truncate": False, "update": False, "truncate_products": False}
