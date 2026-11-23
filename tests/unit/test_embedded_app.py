"""Embedded app pieces that need no database: session tokens, token encryption, the token endpoint, the dev store
guard on writes, the settings rules, and what the review page and its headers look like inside the admin."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from gatekeeper.api.app import REVIEW_APP_CSP, create_app, embedded_review_html, review_app_csp
from gatekeeper.config import Settings, decode_key
from gatekeeper.shopify import session_token
from gatekeeper.shopify.live import LiveShopify
from gatekeeper.shopify.offline_tokens import (
    ID_TOKEN_TYPE,
    OFFLINE_TOKEN_TYPE,
    TOKEN_EXCHANGE_GRANT,
    RefreshRejected,
    SessionTokenRejected,
    TokenEndpoint,
)
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyThrottled, ShopifyTransient
from gatekeeper.shopify.token_crypto import TokenCipher, TokenDecryptError

SHOP = "gatekeeper-dev.myshopify.com"
CLIENT_ID = "0123456789abcdef0123456789abcdef"
SECRET = "shpss_" + "test-secret-" * 5 + "abcdefgh"  # 74 bytes, like a real secret or longer
KEY = base64.b64encode(bytes(range(32))).decode()
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def make_session_token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": f"https://{SHOP}/admin",
        "dest": f"https://{SHOP}",
        "aud": CLIENT_ID,
        "sub": "42",
        "exp": now + 60,
        "nbf": now - 1,
        "iat": now - 1,
        "jti": "a-jti",
        "sid": "a-session",
    }
    claims.update(overrides)
    secret = str(claims.pop("_secret", SECRET))
    algorithm = str(claims.pop("_alg", "HS256"))
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, secret, algorithm=algorithm)


def verify(token: str) -> session_token.SessionToken:
    return session_token.verify(token, client_id=CLIENT_ID, client_secret=SECRET, shop_domain=SHOP)


def embedded_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "shopify_adapter": "live",
        "shopify_shop_domain": SHOP,
        "shopify_embedded": True,
        "shopify_client_id": CLIENT_ID,
        "shopify_client_secret": SECRET,
        "token_encryption_key": KEY,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# Session tokens ----------------------------------------------------------------------------------------------------


def test_a_valid_session_token_names_the_store_and_the_user():
    token = make_session_token()
    session = verify(token)
    assert (session.shop_domain, session.user_id, session.raw) == (SHOP, "42", token)
    assert "***" in repr(session) and token not in repr(session)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 60},  # expired beyond the leeway
        {"nbf": int(time.time()) + 60},  # not valid yet
        {"aud": "another-app"},
        {"_secret": "a-different-secret-" * 4},  # signed by someone else
        {"_alg": "HS512"},  # only HS256 is accepted
        {"dest": "https://other-store.myshopify.com"},  # iss and dest disagree
        {"iss": "https://other-store.myshopify.com/admin", "dest": "https://other-store.myshopify.com"},
        {"dest": f"http://{SHOP}", "iss": f"http://{SHOP}/admin"},  # not https
        {"sub": None},  # required claim missing
        {"exp": None},
    ],
)
def test_session_tokens_that_must_be_refused(overrides):
    with pytest.raises(session_token.InvalidSessionToken):
        verify(make_session_token(**overrides))


def test_an_unsigned_token_is_refused():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_claims = jwt.decode(make_session_token(), options={"verify_signature": False})
    payload = base64.urlsafe_b64encode(json.dumps(payload_claims).encode()).rstrip(b"=").decode()
    with pytest.raises(session_token.InvalidSessionToken):
        verify(f"{header}.{payload}.")


def test_a_few_seconds_of_clock_skew_is_allowed():
    verify(make_session_token(exp=int(time.time()) - 5))


def test_reviewer_tokens_never_look_like_session_tokens():
    assert session_token.looks_like_session_token(make_session_token())
    assert not session_token.looks_like_session_token("reviewer-token-without-dots")


# Token encryption -------------------------------------------------------------------------------------------------


def test_tokens_round_trip_and_only_for_their_own_row():
    cipher = TokenCipher(decode_key(KEY))
    blob = cipher.encrypt("shpat_secret", context="shop_credentials:a:access")
    assert b"shpat_secret" not in blob
    assert cipher.decrypt(blob, context="shop_credentials:a:access") == "shpat_secret"
    assert cipher.encrypt("shpat_secret", context="x") != cipher.encrypt("shpat_secret", context="x")  # fresh nonce
    with pytest.raises(TokenDecryptError):
        cipher.decrypt(blob, context="shop_credentials:a:refresh")
    with pytest.raises(TokenDecryptError):
        cipher.decrypt(blob, context="shop_credentials:b:access")
    with pytest.raises(TokenDecryptError):
        TokenCipher(bytes(32)).decrypt(blob, context="shop_credentials:a:access")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(TokenDecryptError):
        cipher.decrypt(tampered, context="shop_credentials:a:access")
    with pytest.raises(TokenDecryptError):
        cipher.decrypt(b"\x02" + blob[1:], context="shop_credentials:a:access")
    assert "***" in repr(cipher)


@pytest.mark.parametrize("value", ["not base64!", base64.b64encode(bytes(16)).decode(), ""])
def test_encryption_keys_must_be_32_bytes_of_base64(value):
    with pytest.raises(ValueError):
        decode_key(value)


# Settings ---------------------------------------------------------------------------------------------------------


def test_embedded_settings_are_accepted_and_sign_webhooks_with_the_client_secret():
    settings = embedded_settings()
    assert settings.webhook_signing_secret is not None
    assert settings.webhook_signing_secret.get_secret_value() == SECRET


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"shopify_adapter": "fixture"}, "SHOPIFY_ADAPTER=live"),
        ({"token_encryption_key": None}, "TOKEN_ENCRYPTION_KEY"),
        ({"token_encryption_key": base64.b64encode(bytes(8)).decode()}, "32 bytes"),
        ({"shopify_webhook_secret": "another"}, "unset SHOPIFY_WEBHOOK_SECRET"),
        ({"shopify_client_id": "has spaces and <tags>"}, "letters and digits"),
        ({"shopify_client_id": None, "shopify_client_secret": None}, "SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET"),
    ],
)
def test_embedded_settings_that_are_refused(overrides, message):
    with pytest.raises(ValueError, match=message):
        embedded_settings(**overrides)


def test_the_template_model_may_write_to_a_live_store_only_with_the_dev_store_flag():
    with pytest.raises(ValueError, match="template model cannot write"):
        embedded_settings(writeback_enabled=True, llm_provider="heuristic")
    settings = embedded_settings(
        writeback_enabled=True, llm_provider="heuristic", allow_template_writes_to_dev_store=True
    )
    assert settings.uses_template_model
    with pytest.raises(ValueError, match="only applies to the live adapter"):
        Settings(env="test", allow_template_writes_to_dev_store=True)


def test_production_embedded_needs_no_reviewer_tokens():
    embedded_settings(env="production", llm_provider="heuristic", allow_demo_model_in_prod=True)


# Token endpoint ---------------------------------------------------------------------------------------------------


def endpoint(handler) -> TokenEndpoint:
    return TokenEndpoint(
        shop_domain=SHOP,
        client_id=CLIENT_ID,
        client_secret=SECRET,
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )


def test_token_exchange_asks_for_an_expiring_offline_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "shpat_access",
                "scope": "read_products,write_products",
                "expires_in": 3600,
                "refresh_token": "shprt_refresh",
                "refresh_token_expires_in": 7776000,
            },
        )

    grant = endpoint(handler).exchange("the.session.token")
    form = {k: v[0] for k, v in parse_qs(seen[0].content.decode()).items()}
    assert str(seen[0].url) == f"https://{SHOP}/admin/oauth/access_token"
    assert form == {
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "subject_token": "the.session.token",
        "subject_token_type": ID_TOKEN_TYPE,
        "requested_token_type": OFFLINE_TOKEN_TYPE,
        "expiring": "1",
    }
    assert grant.access_token == "shpat_access" and grant.refresh_token == "shprt_refresh"
    assert grant.access_expires_at == NOW + timedelta(hours=1)
    assert grant.refresh_expires_at == NOW + timedelta(days=90)
    assert "shpat_access" not in repr(grant)


def test_refresh_sends_the_refresh_token():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(
            200,
            json={"access_token": "a2", "expires_in": 3600, "refresh_token": "r2", "refresh_token_expires_in": 100},
        )

    endpoint(handler).refresh("r1")
    assert seen[0]["grant_type"] == "refresh_token" and seen[0]["refresh_token"] == "r1"


def test_a_token_that_does_not_expire_is_kept_as_such():
    grant = endpoint(lambda r: httpx.Response(200, json={"access_token": "a", "scope": "read_products"})).exchange("t")
    assert grant.access_expires_at is None and grant.refresh_token is None


@pytest.mark.parametrize(
    ("response", "call", "error"),
    [
        (httpx.Response(400, json={"error": "invalid_subject_token"}), "exchange", SessionTokenRejected),
        (httpx.Response(401, json={"error": "invalid_client"}), "exchange", ShopifyAuthError),
        (httpx.Response(401, json={"error": "invalid_request"}), "refresh", RefreshRejected),
        (httpx.Response(400, json={"error": "invalid_grant"}), "refresh", RefreshRejected),
        (httpx.Response(429, headers={"Retry-After": "7"}), "refresh", ShopifyThrottled),
        (httpx.Response(503), "exchange", ShopifyTransient),
        (httpx.Response(200, text="<html>"), "exchange", ShopifyTransient),
        (httpx.Response(200, json={"scope": "x"}), "exchange", ShopifyAuthError),
        (httpx.Response(200, json={"access_token": "a", "expires_in": 3600}), "exchange", ShopifyAuthError),
        (
            httpx.Response(
                200, json={"access_token": "a", "expires_in": True, "refresh_token": "r", "refresh_token_expires_in": 9}
            ),
            "refresh",
            ShopifyAuthError,
        ),
    ],
)
def test_token_endpoint_failures(response, call, error):
    with pytest.raises(error) as caught:
        getattr(endpoint(lambda r: response), call)("t")
    assert SECRET not in str(caught.value)  # error bodies and requests never reach the message
    if isinstance(caught.value, ShopifyThrottled):
        assert caught.value.retry_after == 7


def test_token_endpoint_timeouts_are_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ShopifyTransient):
        endpoint(handler).exchange("t")


def test_the_client_secret_is_only_ever_sent_to_a_myshopify_domain():
    with pytest.raises(ValueError):
        TokenEndpoint(shop_domain="evil.example.com", client_id=CLIENT_ID, client_secret=SECRET)
    assert SECRET not in repr(endpoint(lambda r: httpx.Response(500)))


# Writes by the template model reach development stores only ------------------------------------------------------


def _product_node() -> dict:
    return {
        "id": "gid://shopify/Product/1",
        "handle": "bottle",
        "title": "Bottle",
        "status": "ACTIVE",
        "vendor": "V",
        "productType": "T",
        "tags": [],
        "descriptionHtml": "<p>Text</p>",
        "updatedAt": "2026-11-23T00:00:00Z",
        "seo": {"title": "S", "description": "D"},
        "variants": {"edges": []},
        "media": {"edges": []},
    }


def live_store(partner_development: object) -> tuple[LiveShopify, list[str]]:
    operations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        operations.append(body["operationName"])
        if body["operationName"] == "ShopPlan":
            return httpx.Response(200, json={"data": {"shop": {"plan": {"partnerDevelopment": partner_development}}}})
        return httpx.Response(200, json={"data": {"productUpdate": {"product": _product_node(), "userErrors": []}}})

    store = LiveShopify(
        shop_domain=SHOP,
        api_version="2026-10",
        access_token="shpat_x",
        writes_enabled=True,
        development_store_only=True,
        transport=httpx.MockTransport(handler),
    )
    return store, operations


def test_template_writes_go_to_a_development_store_after_one_check():
    store, operations = live_store(True)
    store.update_product("gid://shopify/Product/1", {"description_html": "<p>New</p>"})
    store.update_product("gid://shopify/Product/1", {"description_html": "<p>Newer</p>"})
    assert operations == ["ShopPlan", "ProductUpdate", "ProductUpdate"]


@pytest.mark.parametrize("answer", [False, None, "true"])
def test_template_writes_never_reach_a_store_that_is_not_a_development_store(answer):
    store, operations = live_store(answer)
    with pytest.raises(ShopifyAuthError, match="development stores"):
        store.update_product("gid://shopify/Product/1", {"description_html": "<p>New</p>"})
    assert operations == ["ShopPlan"]


# The review page inside the admin ---------------------------------------------------------------------------------

REVIEW_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
    '<script src="https://cdn.shopify.com/shopifycloud/polaris-2.js"></script></head><body></body></html>'
)


def test_app_bridge_comes_before_polaris_with_the_client_id():
    html = embedded_review_html(REVIEW_HTML, CLIENT_ID)
    assert html.index('name="shopify-api-key"') < html.index("app-bridge.js") < html.index("polaris-2.js")
    assert f'content="{CLIENT_ID}"' in html
    with pytest.raises(ValueError):
        embedded_review_html("<html><body></body></html>", CLIENT_ID)


def test_the_standalone_review_csp_is_unchanged():
    assert review_app_csp() == REVIEW_APP_CSP
    assert "frame-ancestors 'none'" in REVIEW_APP_CSP


def static_site(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><html><head></head><body>landing</body></html>")
    (tmp_path / "app" / "index.html").write_text(REVIEW_HTML)
    (tmp_path / "privacy").mkdir()
    (tmp_path / "privacy" / "index.html").write_text("<!doctype html><html><head></head><body>privacy</body></html>")
    return tmp_path


def test_only_the_review_app_may_be_framed_and_only_by_this_store(tmp_path):
    settings = embedded_settings(static_dir=static_site(tmp_path))
    with TestClient(create_app(settings, ctx=SimpleNamespace())) as client:  # type: ignore[arg-type]
        review = client.get("/app/proposals/1")
        landing = client.get("/")
    csp = review.headers["content-security-policy"]
    assert f"frame-ancestors https://{SHOP} https://admin.shopify.com;" in csp
    assert "x-frame-options" not in review.headers
    assert "app-bridge.js" in review.text and CLIENT_ID in review.text
    assert landing.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in landing.headers["content-security-policy"]
    assert "app-bridge.js" not in landing.text


def test_outside_embedded_mode_nothing_is_framed(tmp_path):
    settings = Settings(env="test", static_dir=static_site(tmp_path))
    with TestClient(create_app(settings, ctx=SimpleNamespace())) as client:  # type: ignore[arg-type]
        review = client.get("/app/")
    assert review.headers["x-frame-options"] == "DENY"
    assert review.headers["content-security-policy"] == REVIEW_APP_CSP
    assert "app-bridge.js" not in review.text


def test_outside_embedded_mode_the_session_call_does_not_exist():
    with TestClient(create_app(Settings(env="test"), ctx=SimpleNamespace())) as client:  # type: ignore[arg-type]
        response = client.post("/api/shopify/session", headers={"Authorization": "Bearer a.b.c"})
    assert response.status_code == 404


# The Render blueprint ----------------------------------------------------------------------------------------------

BLUEPRINT = Path(__file__).resolve().parents[2] / "render.yaml"
SENSITIVE = ("SECRET", "KEY", "TOKEN", "PASSWORD", "DATABASE_URL")


def services() -> dict[str, dict[str, dict]]:
    import yaml

    loaded = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    return {s["name"]: {v["key"]: v for v in s["envVars"]} for s in loaded["services"]}


def test_the_blueprint_writes_no_secret_into_the_repository():
    for name, env in services().items():
        for key, entry in env.items():
            if any(word in key for word in SENSITIVE):
                assert "value" not in entry, f"{name}: {key} must be entered in Render or generated, not written here"


def test_the_public_demo_holds_no_store_credentials_or_model_key():
    demo = services()["catalog-gatekeeper-demo"]
    assert not {"SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "ANTHROPIC_API_KEY", "TOKEN_ENCRYPTION_KEY"} & set(demo)


def test_the_embedded_service_boots_with_its_blueprint_values():
    app = services()["catalog-gatekeeper-app"]
    values = {key.lower(): entry["value"] for key, entry in app.items() if "value" in entry}
    values.pop("fixtures_dir"), values.pop("static_dir")
    entered = {
        "shopify_shop_domain": SHOP,
        "shopify_client_id": CLIENT_ID,
        "shopify_client_secret": SECRET,
        "token_encryption_key": KEY,
    }
    settings = Settings(**values, **entered)  # type: ignore[arg-type]
    assert settings.env == "production" and settings.shopify_embedded and settings.uses_template_model


def test_the_privacy_page_is_served_without_the_api_and_cannot_be_framed(tmp_path):
    settings = embedded_settings(static_dir=static_site(tmp_path))
    with TestClient(create_app(settings, ctx=SimpleNamespace())) as client:  # type: ignore[arg-type]
        for path in ("/privacy", "/privacy/"):
            page = client.get(path)
            assert page.status_code == 200 and "privacy" in page.text
            assert page.headers["x-frame-options"] == "DENY"
