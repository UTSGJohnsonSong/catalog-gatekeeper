"""The embedded app against real Postgres: stored tokens and their refresh across processes, connecting a store,
uninstalling, erasing a store's data, the privacy webhooks, and signing in with session tokens."""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from gatekeeper import jobs
from gatekeeper.agent.loop import run_agent
from gatekeeper.api.app import create_app
from gatekeeper.config import decode_key
from gatekeeper.context import AppContext, build_shopify, ensure_shop
from gatekeeper.db import tx
from gatekeeper.llm.heuristic import HeuristicProvider
from gatekeeper.seed import load_store
from gatekeeper.services import shop_lifecycle
from gatekeeper.services.writeback import apply_proposal
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.shopify.offline_tokens import (
    ShopNotConnected,
    StoreAuth,
    StoredOfflineToken,
    TokenEndpoint,
    TokenGrant,
    load_grant,
    save_grant,
)
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyTransient, WritesNotAllowed
from gatekeeper.shopify.session_token import SessionToken
from gatekeeper.shopify.token_crypto import TokenCipher
from gatekeeper.shopify.webhook_hmac import sign
from gatekeeper.worker import run_job
from tests.conftest import make_product, seed_product
from tests.integration.test_review_and_writeback import approved_proposal
from tests.unit.test_embedded_app import KEY, SECRET, SHOP, embedded_settings, make_session_token

# Computed, not written out: a base64 literal of this length reads as a leaked key to secret scanners.
OTHER_KEY = base64.b64encode(bytes(range(1, 33))).decode()


class FakeTokenServer:
    """Answers token exchange and refresh like Shopify, counting calls. `refresh_status` changes the refresh answer."""

    def __init__(self) -> None:
        self.exchanges = 0
        self.refreshes = 0
        self.refresh_status = 200
        self.refresh_error = "invalid_request"
        self.exchange_status = 200
        self.delay = 0.0
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                if self.refresh_status != 200:
                    return httpx.Response(self.refresh_status, json={"error": self.refresh_error})
                number = self.refreshes
                prefix = "refreshed"
            else:
                self.exchanges += 1
                if self.exchange_status != 200:
                    return httpx.Response(self.exchange_status, json={"error": "invalid_subject_token"})
                number = self.exchanges
                prefix = "exchanged"
        return httpx.Response(
            200,
            json={
                "access_token": f"shpat_{prefix}_{number}",
                "scope": "read_products,write_products",
                "expires_in": 3600,
                "refresh_token": f"shprt_{prefix}_{number}",
                "refresh_token_expires_in": 7776000,
            },
        )


@pytest.fixture
def server() -> FakeTokenServer:
    return FakeTokenServer()


@pytest.fixture
def ectx(pool, database_url, server) -> AppContext:
    settings = embedded_settings(database_url=database_url)
    shop_id = ensure_shop(pool, SHOP, writeback_enabled=True)
    endpoint = TokenEndpoint(
        shop_domain=SHOP,
        client_id=settings.shopify_client_id or "",
        client_secret=SECRET,
        transport=httpx.MockTransport(server),
    )
    auth = StoreAuth(endpoint, TokenCipher(decode_key(KEY)))
    return AppContext(settings, pool, FixtureShopify(pool), HeuristicProvider(), None, pool.embedder, shop_id, auth)  # type: ignore[attr-defined,arg-type]


def store_grant(ctx: AppContext, *, access_in: timedelta, access: str = "shpat_old", key: str = KEY) -> None:
    now = datetime.now(UTC)
    grant = TokenGrant(access, "read_products,write_products", now + access_in, "shprt_old", now + timedelta(days=90))
    with tx(ctx.pool) as conn:
        save_grant(conn, ctx.shop_id, SHOP, TokenCipher(decode_key(key)), grant)


def stored(ctx: AppContext) -> TokenGrant | None:
    assert ctx.store_auth is not None
    with tx(ctx.pool) as conn:
        return load_grant(conn, ctx.shop_id, SHOP, ctx.store_auth.cipher)


def token_source(ctx: AppContext) -> StoredOfflineToken:
    assert ctx.store_auth is not None
    return StoredOfflineToken(pool=ctx.pool, shop_id=ctx.shop_id, shop_domain=SHOP, auth=ctx.store_auth)


def session() -> SessionToken:
    return SessionToken(shop_domain=SHOP, user_id="42", raw=make_session_token())


# Stored tokens ----------------------------------------------------------------------------------------------------


def test_connecting_exchanges_once_and_stores_ciphertext_only(ectx, server):
    assert shop_lifecycle.connect_store(ectx, session()) is True
    assert shop_lifecycle.connect_store(ectx, session()) is False
    assert server.exchanges == 1
    with ectx.pool.connection() as conn:
        row = conn.execute("SELECT access_token, refresh_token FROM shop_credentials").fetchone()
        shop = conn.execute("SELECT installed_at, uninstalled_at FROM shops WHERE id = %s", (ectx.shop_id,)).fetchone()
        audit = conn.execute("SELECT actor FROM audit_log WHERE action = 'shop.connected'").fetchall()
    assert b"shpat_" not in bytes(row["access_token"]) and b"shprt_" not in bytes(row["refresh_token"])
    assert shop["installed_at"] is not None and shop["uninstalled_at"] is None
    assert [a["actor"] for a in audit] == ["shopify:42"]
    grant = stored(ectx)
    assert grant is not None and grant.access_token == "shpat_exchanged_1"


def test_a_fresh_token_is_used_without_asking_shopify(ectx, server):
    store_grant(ectx, access_in=timedelta(hours=1))
    assert token_source(ectx).get() == "shpat_old"
    assert server.refreshes == 0


def test_two_processes_refresh_a_token_that_is_about_to_expire_only_once(ectx, server):
    store_grant(ectx, access_in=timedelta(minutes=1))
    server.delay = 0.3  # long enough for the second process to arrive while the first holds the lock
    results: list[str] = []
    api, worker = token_source(ectx), token_source(ectx)
    threads = [threading.Thread(target=lambda s=s: results.append(s.get())) for s in (api, worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert server.refreshes == 1
    assert results == ["shpat_refreshed_1", "shpat_refreshed_1"]
    grant = stored(ectx)
    assert grant is not None and grant.refresh_token == "shprt_refreshed_1"


def test_a_refused_refresh_token_disconnects_the_store(ectx, server):
    store_grant(ectx, access_in=timedelta(minutes=1))
    server.refresh_status = 401
    with pytest.raises(ShopNotConnected):
        token_source(ectx).get()
    assert stored(ectx) is None  # the deletion was committed, so nobody retries the dead refresh token
    with ectx.pool.connection() as conn:
        audit = conn.execute("SELECT detail FROM audit_log WHERE action = 'shop.disconnected'").fetchone()
    assert "invalid_request" in audit["detail"]["reason"]


def test_a_refresh_refused_for_the_apps_own_credentials_keeps_the_token(ectx, server):
    store_grant(ectx, access_in=timedelta(minutes=1))
    server.refresh_status = 401
    server.refresh_error = "invalid_client"
    with pytest.raises(ShopifyAuthError) as caught:
        token_source(ectx).get()
    assert not isinstance(caught.value, ShopNotConnected)
    assert stored(ectx) is not None  # a wrong secret is fixed in the settings; the refresh token may still be good


def test_a_hiccup_while_refreshing_early_keeps_the_working_token(ectx, server):
    store_grant(ectx, access_in=timedelta(minutes=2))  # inside the refresh margin, still valid
    server.refresh_status = 503
    source = token_source(ectx)
    assert source.get() == "shpat_old"
    assert source.get() == "shpat_old"
    assert server.refreshes == 1  # backing off, not asking on every call


def test_a_token_the_store_rejected_is_refreshed_before_it_expires(ectx, server):
    store_grant(ectx, access_in=timedelta(hours=1))
    source = token_source(ectx)
    source.invalidate(source.get())
    assert source.get() == "shpat_refreshed_1"


def test_without_a_stored_token_the_store_is_not_connected(ectx):
    with pytest.raises(ShopNotConnected):
        token_source(ectx).get()


def test_a_changed_encryption_key_means_one_reconnect(ectx, server):
    store_grant(ectx, access_in=timedelta(hours=1), key=OTHER_KEY)
    with pytest.raises(ShopNotConnected):
        token_source(ectx).get()
    assert shop_lifecycle.connect_store(ectx, session()) is True
    assert token_source(ectx).get() == "shpat_exchanged_1"


# Uninstall and erasure --------------------------------------------------------------------------------------------


def test_uninstall_forgets_the_token_and_drops_queued_work(ectx):
    store_grant(ectx, access_in=timedelta(hours=1))
    jobs.enqueue(ectx.pool, "sync", {})
    shop_lifecycle.mark_uninstalled(ectx)
    assert stored(ectx) is None
    with ectx.pool.connection() as conn:
        shop = conn.execute("SELECT uninstalled_at, writeback_enabled FROM shops WHERE id = %s", (ectx.shop_id,))
        shop_row = shop.fetchone()
        statuses = [r["status"] for r in conn.execute("SELECT status FROM jobs WHERE kind = 'sync'").fetchall()]
    assert shop_row["uninstalled_at"] is not None and shop_row["writeback_enabled"] is False
    assert statuses == ["dead"]


def test_shop_redact_erases_the_catalog_and_keeps_the_audit_log(ectx):
    product_id = seed_product(ectx, make_product(description_html="<p>Short.</p>"))
    run_agent(ectx, product_id)
    store_grant(ectx, access_in=timedelta(hours=1))
    with ectx.pool.connection() as conn:
        audit_before = conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"]
        proposals_before = conn.execute("SELECT count(*) AS n FROM proposals").fetchone()["n"]
    assert proposals_before == 1
    counts = shop_lifecycle.erase_shop_data(ectx)
    assert counts["products"] == 1 and counts["proposals"] == 1 and counts["findings"] >= 1
    with ectx.pool.connection() as conn:
        for table in ("products", "findings", "proposals", "agent_runs", "validation_results", "shop_credentials"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table  # noqa: S608
        audit_after = conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"]
        redacted = conn.execute("SELECT redacted_at FROM shops WHERE id = %s", (ectx.shop_id,)).fetchone()
    assert audit_after == audit_before + 1
    assert redacted["redacted_at"] is not None


# Webhooks ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def eclient(ectx):
    with TestClient(create_app(ectx.settings, ectx)) as client:
        yield client


def send(
    client, topic: str, body: dict, *, secret: str = SECRET, webhook_id: str = "wh-1", triggered_at: str | None = None
):
    raw = json.dumps(body).encode()
    headers = {
        "X-Shopify-Hmac-Sha256": sign(secret, raw),
        "X-Shopify-Webhook-Id": webhook_id,
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": SHOP,
    }
    if triggered_at:
        headers["X-Shopify-Triggered-At"] = triggered_at
    return client.post("/webhooks/shopify", content=raw, headers=headers)


def drain(ctx: AppContext) -> None:
    while (job := jobs.claim(ctx.pool, "t")) is not None:
        run_job(ctx, job)


# Shaped like the examples on shopify.dev's privacy law compliance page, and the shop object for app/uninstalled.
CUSTOMER = {"id": 191167, "email": "john@example.com", "phone": "555-625-1199"}
BODIES = {
    "customers/data_request": {
        "shop_id": 954889,
        "shop_domain": SHOP,
        "orders_requested": [299938],
        "customer": CUSTOMER,
        "data_request": {"id": 9999},
    },
    "customers/redact": {"shop_id": 954889, "shop_domain": SHOP, "customer": CUSTOMER, "orders_to_redact": [299938]},
    "shop/redact": {"shop_id": 954889, "shop_domain": SHOP},
    "app/uninstalled": {"id": 954889, "myshopify_domain": SHOP, "domain": "shop.example.com", "email": "o@example.com"},
}


@pytest.mark.parametrize("topic", ["customers/data_request", "customers/redact"])
def test_customer_privacy_webhooks_are_answered_without_storing_the_customer(ectx, eclient, topic):
    assert send(eclient, topic, BODIES[topic]).status_code == 200
    drain(ectx)
    with ectx.pool.connection() as conn:
        event = conn.execute("SELECT payload, status FROM webhook_events").fetchone()
        audit = conn.execute("SELECT detail FROM audit_log WHERE action LIKE 'privacy.%%'").fetchone()
    assert "john@example.com" not in json.dumps(event["payload"])
    assert event["status"] == "processed"
    assert audit["detail"] == {"customer_data_held": False}


def test_privacy_webhooks_signed_with_another_secret_get_401(eclient):
    assert send(eclient, "shop/redact", BODIES["shop/redact"], secret="not-the-client-secret").status_code == 401


def test_uninstall_and_redact_webhooks_reach_the_store_lifecycle(ectx, eclient):
    seed_product(ectx)
    store_grant(ectx, access_in=timedelta(hours=1))
    assert send(eclient, "app/uninstalled", BODIES["app/uninstalled"]).status_code == 200
    drain(ectx)
    assert stored(ectx) is None
    assert send(eclient, "shop/redact", BODIES["shop/redact"], webhook_id="wh-2").status_code == 200
    drain(ectx)
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM webhook_events").fetchone()["n"] == 0


# Signing in from the admin ----------------------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_opening_the_app_connects_the_store_once_and_starts_a_sync(ectx, eclient, server):
    first = eclient.post("/api/shopify/session", headers=bearer(make_session_token()))
    second = eclient.post("/api/shopify/session", headers=bearer(make_session_token()))
    assert first.status_code == 200 and first.json() == {"shop_domain": SHOP, "connected_now": True}
    assert second.json()["connected_now"] is False
    assert server.exchanges == 1
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'sync'").fetchone()["n"] == 1


def test_a_session_token_signs_a_merchant_in_as_admin(eclient):
    response = eclient.get("/api/whoami", headers=bearer(make_session_token()))
    assert response.json() == {"name": "shopify:42", "role": "admin"}
    assert eclient.post("/api/admin/sync", headers=bearer(make_session_token())).status_code == 202


def test_an_expired_session_token_gets_401_with_the_retry_header(eclient):
    response = eclient.post("/api/shopify/session", headers=bearer(make_session_token(exp=int(time.time()) - 120)))
    assert response.status_code == 401
    assert response.headers["x-shopify-retry-invalid-session-request"] == "1"


@pytest.mark.parametrize(
    "token",
    [
        make_session_token(dest="https://other-store.myshopify.com", iss="https://other-store.myshopify.com/admin"),
        make_session_token(_secret="a-different-secret-" * 4),
        "a-reviewer-token",
    ],
)
def test_a_session_token_that_no_retry_can_fix_gets_401_without_the_retry_header(eclient, token):
    response = eclient.post("/api/shopify/session", headers=bearer(token))
    assert response.status_code == 401
    assert "x-shopify-retry-invalid-session-request" not in response.headers


def test_a_session_token_shopify_refuses_asks_app_bridge_to_retry(ectx, eclient, server):
    server.exchange_status = 400
    response = eclient.post("/api/shopify/session", headers=bearer(make_session_token()))
    assert response.status_code == 401
    assert response.headers["x-shopify-retry-invalid-session-request"] == "1"
    assert stored(ectx) is None


def test_a_scheduled_sync_before_the_first_open_is_skipped_not_failed(ectx):
    # The real wiring: a live adapter whose token source has nothing stored yet. It fails before any HTTP call.
    live = replace(
        ectx, shopify=build_shopify(ectx.settings, ectx.pool, shop_id=ectx.shop_id, store_auth=ectx.store_auth)
    )
    jobs.enqueue(ectx.pool, "sync", {})
    drain(live)
    with ectx.pool.connection() as conn:
        assert [r["status"] for r in conn.execute("SELECT status FROM jobs WHERE kind = 'sync'").fetchall()] == ["done"]


# Found by review ---------------------------------------------------------------------------------------------------


def forged_from_session_token() -> tuple[bytes, str]:
    """A session token's header and payload, signed with the token's own signature, as a webhook would be."""
    header, payload, signature = make_session_token().split(".")
    standard = base64.b64encode(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))).decode()
    return f"{header}.{payload}".encode(), standard


def post_raw(client, topic: str, raw: bytes, signature: str, webhook_id: str):
    headers = {
        "X-Shopify-Hmac-Sha256": signature,
        "X-Shopify-Webhook-Id": webhook_id,
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": SHOP,
    }
    return client.post("/webhooks/shopify", content=raw, headers=headers)


@pytest.mark.parametrize("topic", ["shop/redact", "app/uninstalled", "customers/redact"])
def test_a_webhook_forged_from_a_session_token_changes_nothing(ectx, eclient, topic):
    seed_product(ectx)
    store_grant(ectx, access_in=timedelta(hours=1))
    raw, signature = forged_from_session_token()
    response = post_raw(eclient, topic, raw, signature, webhook_id=f"forged-{topic}")
    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    drain(ectx)
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0
    assert stored(ectx) is not None


def test_a_signed_lifecycle_webhook_that_names_another_store_changes_nothing(ectx, eclient):
    seed_product(ectx)
    other = {"shop_id": 1, "shop_domain": "other-store.myshopify.com"}
    assert send(eclient, "shop/redact", other).json() == {"status": "ignored"}
    drain(ectx)
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 1


@pytest.mark.parametrize(
    "token",
    [
        make_session_token(dest="https://other-store.myshopify.com", iss="https://other-store.myshopify.com/admin"),
        make_session_token(exp=int(time.time()) - 120),
        make_session_token(_secret="a-different-secret-" * 4),
        make_session_token(aud="another-app"),
    ],
)
def test_bad_session_tokens_never_reach_admin_actions(ectx, eclient, token):
    response = eclient.post("/api/admin/sync", headers=bearer(token))
    assert response.status_code == 401
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0


def test_outside_embedded_mode_a_valid_session_token_is_not_a_sign_in(ctx):
    with TestClient(create_app(ctx.settings, ctx)) as client:
        assert client.get("/api/whoami", headers=bearer(make_session_token())).status_code == 401


def test_erasure_reaches_every_table_that_holds_a_stores_data(ectx, database_url):
    writing = replace(
        ectx,
        settings=embedded_settings(
            database_url=database_url,
            writeback_enabled=True,
            llm_provider="heuristic",
            allow_template_writes_to_dev_store=True,
        ),
    )
    proposal_id, _ = approved_proposal(writing)
    assert apply_proposal(writing, proposal_id).status == "applied"  # reviews and writebacks rows now exist
    store_grant(writing, access_in=timedelta(hours=1))
    shop_lifecycle.erase_shop_data(writing)
    kept = {"audit_log", "shops"}  # the log by design; the store row as a tombstone that says when it was erased
    with ectx.pool.connection() as conn:
        holding = {
            r["table_name"]
            for r in conn.execute(
                "SELECT DISTINCT c.table_name FROM information_schema.columns c JOIN information_schema.tables t "
                "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
                "AND c.column_name IN ('shop_id', 'product_id', 'proposal_id', 'run_id')"
            ).fetchall()
        }
        erased = {table for table, _ in shop_lifecycle._ERASE} | {"shop_credentials"}
        assert holding - kept <= erased, f"tables erasure does not reach: {sorted(holding - kept - erased)}"
        for table in sorted(erased | {"webhook_events"}):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table  # noqa: S608


def test_an_expired_refresh_token_disconnects_the_store(ectx, server):
    now = datetime.now(UTC)
    grant = TokenGrant(
        "shpat_old", "read_products", now - timedelta(minutes=1), "shprt_old", now - timedelta(seconds=1)
    )
    with tx(ectx.pool) as conn:
        save_grant(conn, ectx.shop_id, SHOP, ectx.store_auth.cipher, grant)  # type: ignore[union-attr]
    with pytest.raises(ShopNotConnected):
        token_source(ectx).get()
    assert stored(ectx) is None and server.refreshes == 0


def test_an_expired_access_token_is_never_handed_out_when_refresh_fails(ectx, server):
    store_grant(ectx, access_in=timedelta(seconds=-1))
    server.refresh_status = 503
    with pytest.raises(ShopifyTransient):
        token_source(ectx).get()
    assert stored(ectx) is not None  # a hiccup is not a reason to forget the refresh token


def test_reopening_after_the_access_token_expired_refreshes_instead_of_exchanging(ectx, server):
    store_grant(ectx, access_in=timedelta(seconds=-1))
    assert shop_lifecycle.connect_store(ectx, session()) is False
    assert server.exchanges == 0


def test_reinstalling_after_an_uninstall_connects_again(ectx, server):
    assert shop_lifecycle.connect_store(ectx, session()) is True
    shop_lifecycle.mark_uninstalled(ectx)
    assert shop_lifecycle.connect_store(ectx, session()) is True
    assert server.exchanges == 2
    with ectx.pool.connection() as conn:
        shop = conn.execute("SELECT uninstalled_at FROM shops WHERE id = %s", (ectx.shop_id,)).fetchone()
    assert shop["uninstalled_at"] is None


def test_the_first_boot_without_a_store_token_starts_anyway(pool, database_url):
    load_store(embedded_settings(database_url=database_url))  # would raise ShopNotConnected before the fix


def test_work_other_than_a_sync_fails_loudly_without_a_store_token(ectx, eclient):
    live = replace(
        ectx, shopify=build_shopify(ectx.settings, ectx.pool, shop_id=ectx.shop_id, store_auth=ectx.store_auth)
    )
    assert send(eclient, "products/update", {"admin_graphql_api_id": "gid://shopify/Product/9"}).status_code == 200
    drain(live)
    with ectx.pool.connection() as conn:
        job = conn.execute("SELECT status, last_error FROM jobs WHERE kind = 'process_webhook'").fetchone()
    assert job["status"] == "dead" and "not connected" in job["last_error"]


def test_a_sync_after_a_connected_store_lost_its_token_fails_where_the_ops_page_counts_it(ectx):
    live = replace(
        ectx, shopify=build_shopify(ectx.settings, ectx.pool, shop_id=ectx.shop_id, store_auth=ectx.store_auth)
    )
    with ectx.pool.connection() as conn:
        conn.execute("UPDATE shops SET installed_at = now() WHERE id = %s", (ectx.shop_id,))
    jobs.enqueue(ectx.pool, "sync", {})
    drain(live)
    with ectx.pool.connection() as conn:
        assert [r["status"] for r in conn.execute("SELECT status FROM jobs WHERE kind = 'sync'").fetchall()] == ["dead"]


class RefusesWrites:
    # The simulated store, except that it may not be written: what LiveShopify says about a store that is not a
    # development store when the template model drafted the fix.
    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        return getattr(self._store, name)

    def ensure_writable(self):
        raise WritesNotAllowed("template-drafted fixes are written only to development stores, and this is not one")


def test_a_store_that_may_not_be_written_ends_the_fix_cleanly_before_anything_is_sent(ectx, database_url):
    writing = replace(
        ectx,
        settings=embedded_settings(
            database_url=database_url,
            writeback_enabled=True,
            llm_provider="heuristic",
            allow_template_writes_to_dev_store=True,
        ),
    )
    proposal_id, _ = approved_proposal(writing)
    result = apply_proposal(replace(writing, shopify=RefusesWrites(writing.shopify)), proposal_id)
    assert (result.status, result.detail) == ("failed", "writes not allowed")
    with ectx.pool.connection() as conn:
        row = conn.execute(
            "SELECT p.status::text AS status, w.status AS written, w.attempts, w.user_errors "
            "FROM proposals p JOIN writebacks w ON w.proposal_id = p.id WHERE p.id = %s",
            (proposal_id,),
        ).fetchone()
    assert (row["status"], row["written"], row["attempts"]) == ("failed", "failed", 0)
    assert "writes_not_allowed" in row["user_errors"][0]["message"]


@pytest.mark.parametrize(
    ("body_of", "sent_as"),
    [
        ("customers/redact", "shop/redact"),
        ("shop/redact", "app/uninstalled"),
        ("app/uninstalled", "shop/redact"),
    ],
)
def test_a_genuine_body_replayed_under_another_topic_changes_nothing(ectx, eclient, body_of, sent_as):
    # The topic header is outside the signature, so only the body's shape can tell the topics apart.
    seed_product(ectx)
    store_grant(ectx, access_in=timedelta(hours=1))
    assert send(eclient, sent_as, BODIES[body_of], webhook_id=f"replay-{body_of}").json() == {"status": "ignored"}
    drain(ectx)
    with ectx.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 1
    assert stored(ectx) is not None


def test_an_uninstall_notice_from_before_a_reinstall_changes_nothing(ectx, eclient, server):
    assert shop_lifecycle.connect_store(ectx, session()) is True  # the reinstall: a token exchanged just now
    an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert send(eclient, "app/uninstalled", BODIES["app/uninstalled"], triggered_at=an_hour_ago).status_code == 200
    drain(ectx)
    assert stored(ectx) is not None
    with ectx.pool.connection() as conn:
        shop = conn.execute("SELECT uninstalled_at, writeback_enabled FROM shops WHERE id = %s", (ectx.shop_id,))
        row = shop.fetchone()
        ignored = conn.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'shop.uninstall_ignored'")
        assert ignored.fetchone()["n"] == 1
    assert row["uninstalled_at"] is None and row["writeback_enabled"] is True


def test_an_uninstall_notice_from_after_the_last_exchange_still_disconnects(ectx, eclient):
    assert shop_lifecycle.connect_store(ectx, session()) is True
    just_now = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    assert send(eclient, "app/uninstalled", BODIES["app/uninstalled"], triggered_at=just_now).status_code == 200
    drain(ectx)
    assert stored(ectx) is None
