from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gatekeeper.agent.loop import run_agent
from gatekeeper.api.app import create_app
from gatekeeper.config import Settings
from gatekeeper.jobs import claim
from gatekeeper.shopify.webhook_hmac import sign
from gatekeeper.worker import run_job
from tests.conftest import make_product, seed_product

GID = "gid://shopify/Product/1"
SECRET = "test-secret"
ADMIN = {"Authorization": "Bearer demo-admin"}
REVIEWER = {"Authorization": "Bearer demo-reviewer"}


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx.settings, ctx)) as test_client:
        yield test_client


def webhook(
    client,
    body: dict | bytes,
    *,
    webhook_id="wh-1",
    secret=SECRET,
    topic="products/update",
    domain="demo-store.myshopify.com",
    signature=None,
):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    headers = {
        "X-Shopify-Hmac-Sha256": signature if signature is not None else sign(secret, raw),
        "X-Shopify-Webhook-Id": webhook_id,
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": domain,
    }
    return client.post("/webhooks/shopify", content=raw, headers=headers)


def counts(ctx):
    with ctx.pool.connection() as conn:
        return (
            conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"],
            conn.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'process_webhook'").fetchone()["n"],
        )


def drain(ctx):
    while (job := claim(ctx.pool, "t")) is not None:
        run_job(ctx, job)


class TestWebhookSecurity:
    def test_valid_signature_is_accepted(self, ctx, client):
        assert webhook(client, {"admin_graphql_api_id": GID}).json() == {"status": "accepted"}
        assert counts(ctx) == (1, 1)

    def test_signature_covers_raw_bytes_not_parsed_json(self, ctx, client):
        original = json.dumps({"admin_graphql_api_id": GID}).encode()
        reformatted = json.dumps({"admin_graphql_api_id": GID}, indent=2).encode()
        response = webhook(client, reformatted, signature=sign(SECRET, original))
        assert response.status_code == 401 and counts(ctx) == (0, 0)

    @pytest.mark.parametrize("signature", ["", "not-base64", "AAAA"])
    def test_bad_signature_stores_nothing(self, ctx, client, signature):
        assert webhook(client, {"id": 1}, signature=signature).status_code == 401
        assert counts(ctx) == (0, 0)

    def test_wrong_secret_is_rejected(self, ctx, client):
        assert webhook(client, {"id": 1}, secret="other").status_code == 401

    def test_missing_headers_are_a_400(self, client):
        raw = b"{}"
        response = client.post("/webhooks/shopify", content=raw, headers={"X-Shopify-Hmac-Sha256": sign(SECRET, raw)})
        assert response.status_code == 400

    def test_unknown_shop_is_ignored_and_stores_nothing(self, ctx, client):
        assert webhook(client, {"id": 1}, domain="other.myshopify.com").json() == {"status": "ignored"}
        assert counts(ctx) == (0, 0)

    def test_too_large_is_refused(self, client):
        response = client.post(
            "/webhooks/shopify", content=b"x" * (5 * 1024 * 1024 + 1), headers={"X-Shopify-Hmac-Sha256": "x"}
        )
        assert response.status_code == 413

    def test_unset_secret_is_refused_not_open(self, ctx):
        settings = ctx.settings.model_copy(update={"shopify_webhook_secret": None})
        with TestClient(create_app(settings, ctx)) as test_client:
            assert test_client.post("/webhooks/shopify", content=b"{}").status_code == 503


class TestWebhookIdempotency:
    def test_replay_of_the_same_delivery_is_a_noop(self, ctx, client):
        body = {"admin_graphql_api_id": GID}
        assert webhook(client, body).json()["status"] == "accepted"
        assert webhook(client, body).json()["status"] == "duplicate"
        assert counts(ctx) == (1, 1)

    def test_replayed_body_with_a_fresh_id_changes_nothing(self, ctx, client):
        """The id header is outside the HMAC. Signal-only processing makes this harmless."""
        seed_product(ctx)
        body = {"admin_graphql_api_id": GID}
        webhook(client, body, webhook_id="wh-1")
        webhook(client, body, webhook_id="wh-2")
        with ctx.pool.connection() as conn:
            before = conn.execute("SELECT content_hash, synced_at FROM products").fetchone()["content_hash"]
        drain(ctx)
        with ctx.pool.connection() as conn:
            after = conn.execute("SELECT content_hash FROM products").fetchone()["content_hash"]
            events = conn.execute("SELECT status FROM webhook_events").fetchall()
        assert before == after and {e["status"] for e in events} == {"processed"}

    def test_unhandled_topic_is_recorded_as_ignored(self, ctx, client):
        assert webhook(client, {"id": 1}, topic="orders/create").json()["status"] == "ignored"
        assert counts(ctx) == (1, 0)


class TestWebhookIsOnlyASignal:
    def test_payload_cannot_plant_data(self, ctx, client):
        seed_product(ctx)
        evil = {
            "admin_graphql_api_id": GID,
            "title": "Pwned",
            "body_html": "<script>x</script>",
            "variants": [{"price": "0.01"}],
        }
        webhook(client, evil)
        drain(ctx)
        with ctx.pool.connection() as conn:
            row = conn.execute("SELECT title, description_html FROM products").fetchone()
        assert row["title"] == make_product().title and "script" not in row["description_html"]

    def test_merchant_edit_then_webhook_stales_the_pending_proposal(self, ctx, client):
        product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        proposal_id = run_agent(ctx, product_id).proposal_ids[0]
        ctx.shopify.external_edit(GID, description_html="<p>Merchant edit here.</p>")
        webhook(client, {"admin_graphql_api_id": GID})
        drain(ctx)
        with ctx.pool.connection() as conn:
            assert (
                conn.execute("SELECT status::text AS s FROM proposals WHERE id = %s", (proposal_id,)).fetchone()["s"]
                == "stale"
            )
            assert (
                conn.execute("SELECT description_html FROM products").fetchone()["description_html"]
                == "<p>Merchant edit here.</p>"
            )

    def test_out_of_order_deliveries_converge_on_store_state(self, ctx, client):
        seed_product(ctx)
        ctx.shopify.external_edit(GID, title="Newest title")
        webhook(client, {"admin_graphql_api_id": GID, "updated_at": "2026-10-02"}, webhook_id="later")
        webhook(client, {"admin_graphql_api_id": GID, "updated_at": "2026-10-01"}, webhook_id="earlier")
        drain(ctx)
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT title FROM products").fetchone()["title"] == "Newest title"

    def test_delete_topic_soft_deletes_and_stales(self, ctx, client):
        product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        proposal_id = run_agent(ctx, product_id).proposal_ids[0]
        with ctx.pool.connection() as conn:
            conn.execute("DELETE FROM fixture.products")
        webhook(client, {"id": 1}, topic="products/delete")
        drain(ctx)
        with ctx.pool.connection() as conn:
            assert conn.execute("SELECT deleted_at FROM products").fetchone()["deleted_at"] is not None
            assert (
                conn.execute("SELECT status::text AS s FROM proposals WHERE id = %s", (proposal_id,)).fetchone()["s"]
                == "stale"
            )

    def test_payload_without_product_id_is_marked_failed(self, ctx, client):
        webhook(client, {"nothing": True})
        drain(ctx)
        with ctx.pool.connection() as conn:
            row = conn.execute("SELECT status, error FROM webhook_events").fetchone()
        assert row["status"] == "failed" and "no product id" in row["error"]


class TestAuth:
    @pytest.mark.parametrize("path", ["/api/queue", "/api/products", "/api/audit-log", "/api/whoami"])
    def test_api_needs_a_token(self, client, path):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401

    def test_meta_and_health_are_public(self, client):
        assert client.get("/api/meta").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200

    def test_reviewer_cannot_use_admin_routes(self, client):
        assert client.post("/api/admin/sync", headers=REVIEWER).status_code == 403
        assert client.post("/api/admin/runs", json={}, headers=REVIEWER).status_code == 403
        assert client.post("/api/admin/writeback", json={"enabled": False}, headers=REVIEWER).status_code == 403

    def test_demo_tokens_do_not_exist_outside_demo_mode(self, ctx):
        settings = ctx.settings.model_copy(update={"demo_mode": False, "reviewer_tokens": None})
        with TestClient(create_app(settings, ctx)) as test_client:
            assert test_client.get("/api/whoami", headers=ADMIN).status_code == 401
            assert test_client.post("/demo/simulate-edit", json={"product_id": 1}, headers=ADMIN).status_code in (
                404,
                405,
            )

    def test_configured_tokens_store_only_hashes(self, ctx):
        import hashlib

        token_hash = hashlib.sha256(b"s3cret").hexdigest()
        settings = ctx.settings.model_copy(update={"demo_mode": False})
        settings.reviewer_tokens = Settings(reviewer_tokens=f"carol:reviewer:{token_hash}").reviewer_tokens
        with TestClient(create_app(settings, ctx)) as test_client:
            assert test_client.get("/api/whoami", headers={"Authorization": "Bearer s3cret"}).json() == {
                "name": "carol",
                "role": "reviewer",
            }

    def test_malformed_token_config_refuses_to_start(self, ctx):
        settings = ctx.settings.model_copy(
            update={"reviewer_tokens": Settings(reviewer_tokens="nonsense").reviewer_tokens}
        )
        with pytest.raises(ValueError):
            create_app(settings, ctx)


class TestReviewFlow:
    def test_full_flow_through_the_http_api(self, ctx, client):
        product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        proposal_id = run_agent(ctx, product_id).proposal_ids[0]
        queue = client.get("/api/queue", headers=REVIEWER).json()
        assert queue["items"][0]["id"] == proposal_id and queue["counts"]["pending_review"] == 1
        assert queue["items"][0]["model_mode"] == "demo"

        detail = client.get(f"/api/proposals/{proposal_id}", headers=REVIEWER).json()
        assert detail["model"] == "heuristic-v1" and detail["judge_status"] == "disabled"
        assert detail["diffs"][0]["before"] == "<p>Nice bottle.</p>"
        assert {v["rule_id"] for v in detail["validation"]} == {f"V{i:02d}" for i in range(1, 13)}

        conflict = client.post(
            f"/api/proposals/{proposal_id}/approve", json={"review_hash": "0" * 64}, headers=REVIEWER
        )
        assert conflict.status_code == 409
        approved = client.post(
            f"/api/proposals/{proposal_id}/approve", json={"review_hash": detail["review_hash"]}, headers=REVIEWER
        )
        assert approved.status_code == 200
        assert (
            client.post(
                f"/api/proposals/{proposal_id}/approve", json={"review_hash": detail["review_hash"]}, headers=REVIEWER
            ).status_code
            == 409
        )

        drain(ctx)
        after = client.get(f"/api/proposals/{proposal_id}", headers=REVIEWER).json()
        assert after["status"] == "applied" and after["writeback_status"] == "verified"
        assert after["diffs"][0]["before"] == "<p>Nice bottle.</p>", (
            "before stays what the reviewer saw, even after apply"
        )
        actions = [a["action"] for a in client.get("/api/audit-log", headers=REVIEWER).json()]
        assert "proposal.approved" in actions and "proposal.applied" in actions

    def test_reject_flow_and_validation(self, ctx, client):
        product_id = seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        proposal_id = run_agent(ctx, product_id).proposal_ids[0]
        assert (
            client.post(f"/api/proposals/{proposal_id}/reject", json={"note": "no"}, headers=REVIEWER).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/proposals/{proposal_id}/reject", json={"note": "wrong tone"}, headers=REVIEWER
            ).status_code
            == 200
        )

    def test_unknown_proposal_is_404_and_bad_uuid_is_422(self, client):
        assert client.get("/api/proposals/00000000-0000-0000-0000-000000000000", headers=REVIEWER).status_code == 404
        assert client.get("/api/proposals/not-a-uuid", headers=REVIEWER).status_code == 422

    def test_admin_can_queue_runs_and_flip_the_switch(self, ctx, client):
        seed_product(ctx, make_product(description_html="<p>Nice bottle.</p>"))
        assert client.post("/api/admin/runs", json={"limit": 5}, headers=ADMIN).json() == {"queued": 1}
        assert client.post("/api/admin/sync", headers=ADMIN).status_code == 202
        assert client.post("/api/admin/writeback", json={"enabled": False}, headers=ADMIN).status_code == 200
        assert client.get("/api/meta").json()["writeback_shop_enabled"] is False
        assert "writeback.disabled" in [a["action"] for a in client.get("/api/audit-log", headers=ADMIN).json()]

    def test_security_headers_are_set(self, client):
        response = client.get("/healthz")
        assert (
            response.headers["X-Frame-Options"] == "DENY"
            and "frame-ancestors" in response.headers["Content-Security-Policy"]
        )
