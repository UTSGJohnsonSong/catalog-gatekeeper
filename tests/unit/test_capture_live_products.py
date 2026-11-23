"""The read-only capture script, against a mocked store, and every capture saved from a real one."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from gatekeeper.shopify.mapper import product_from_node
from tests.conftest import make_product
from tests.integration.test_shopify_adapters import to_node

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "capture_live_products", REPO_ROOT / "scripts" / "capture_live_products.py"
)
assert _spec and _spec.loader
capture_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture_live)

SHOP = "real-shop-123.myshopify.com"
TOKEN = "tok-very-secret"
SECRET = "client-secret-value"


def _node(**overrides: Any) -> dict[str, Any]:
    node = {
        "id": "gid://shopify/Product/7001",
        "handle": "trailhead-daypack-20l",
        "title": "Trailhead Daypack 20L",
        "status": "ACTIVE",
        "vendor": "Northline",
        "productType": "Bags",
        "tags": ["bags", "northline"],
        "descriptionHtml": f'<p>Nice bags item. <a href="https://{SHOP}/x">x</a></p>',
        "updatedAt": "2026-11-23T12:00:00Z",
        "seo": {"title": "Trailhead Daypack 20L | Northline", "description": None},
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/1",
                        "sku": "T-0",
                        "price": "64.00",
                        "selectedOptions": [{"value": "Slate"}],
                    }
                }
            ]
        },
        "media": {"edges": []},
    }
    node.update(overrides)
    return node


class FakeStore:
    def __init__(self, nodes: list[dict[str, Any]]):
        self.nodes = nodes
        self.operations: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86399})
        assert request.headers["X-Shopify-Access-Token"] == TOKEN
        body = json.loads(request.content)
        self.operations.append(body["operationName"])
        assert "mutation" not in body["query"]
        edges = [{"node": n} for n in self.nodes]
        data = {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": edges}}
        return httpx.Response(200, json={"data": data, "extensions": {"cost": {"requestedQueryCost": 12}}})


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", SHOP)
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", SECRET)
    monkeypatch.delenv("SHOPIFY_API_VERSION", raising=False)


def test_saves_the_raw_answer_without_token_secret_or_domain(env: None, tmp_path: Path, capsys) -> None:
    store = FakeStore([_node()])
    code = capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(store))
    assert code == 0
    assert store.operations == ["Products"]
    (saved,) = tmp_path.glob("products-*.json")
    text = saved.read_text(encoding="utf-8")
    assert TOKEN not in text and SECRET not in text and SHOP not in text
    data = json.loads(text)
    assert data["shop_domain"] == capture_live.REDACTED_DOMAIN
    assert data["response"]["extensions"] == {"cost": {"requestedQueryCost": 12}}
    out = capsys.readouterr().out
    assert "trailhead-daypack-20l: 1 variants, 0 media, findings" in out
    assert "A02" in out  # the thin description the demo product carries


def test_a_node_the_mapper_cannot_read_exits_1(env: None, tmp_path: Path) -> None:
    broken = _node()
    del broken["id"]
    code = capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(FakeStore([broken])))
    assert code == 1
    assert list(tmp_path.glob("products-*.json")), "the raw answer is still saved, to see what Shopify sent"


def test_http_errors_stop_without_printing_the_body(env: None, tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86399})
        return httpx.Response(403, text=f"forbidden for {TOKEN}")

    with pytest.raises(SystemExit, match="HTTP 403") as caught:
        capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(refuse))
    assert TOKEN not in str(caught.value)
    assert not list(tmp_path.glob("*.json"))


def test_missing_credentials_are_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    for name in ("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        capture_live.main(["--out-dir", str(tmp_path)])
    assert "SHOPIFY_CLIENT_SECRET" in capsys.readouterr().err


@pytest.mark.parametrize(
    "variant",
    [SHOP.upper(), SHOP.replace(".", "%2E"), SHOP.replace(".", "%2e")],
    ids=["uppercase", "encoded-upper", "encoded-lower"],
)
def test_other_spellings_of_the_domain_are_redacted(env: None, tmp_path: Path, variant: str) -> None:
    node = _node(descriptionHtml=f'<p>See <a href="https://{variant}/products/x">this</a>.</p>')
    assert capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(FakeStore([node]))) == 0
    (saved,) = tmp_path.glob("products-*.json")
    text = saved.read_text(encoding="utf-8")
    assert "real-shop-123" not in text.lower()
    assert capture_live.REDACTED_DOMAIN in text


def test_the_bare_handle_anywhere_refuses_to_save(env: None, tmp_path: Path, capsys) -> None:
    # Shopify fills the vendor with the store name when an import leaves it empty.
    node = _node(vendor="real-shop-123")
    assert capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(FakeStore([node]))) == 1
    assert not list(tmp_path.glob("*.json"))
    assert "$.response.data.products.edges[0].node.vendor" in capsys.readouterr().err


def test_a_short_handle_does_not_match_the_replacement_text() -> None:
    redacted = json.loads(capture_live.redact(json.dumps({"u": "https://dev.myshopify.com/x"}), "dev.myshopify.com"))
    assert redacted == {"u": f"https://{capture_live.REDACTED_DOMAIN}/x"}
    assert capture_live.handle_paths(redacted, "dev") == []


def test_no_products_is_a_failure(env: None, tmp_path: Path, capsys) -> None:
    assert capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(FakeStore([]))) == 1
    assert "holds no products" in capsys.readouterr().err


def test_a_bad_price_is_reported_not_a_traceback(env: None, tmp_path: Path, capsys) -> None:
    node = _node()
    node["variants"]["edges"][0]["node"]["price"] = "not a number"
    assert capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(FakeStore([node]))) == 1
    assert "mapper could not read a product" in capsys.readouterr().err
    assert list(tmp_path.glob("products-*.json"))


def test_graphql_errors_stop_before_saving(env: None, tmp_path: Path) -> None:
    def errors(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86399})
        return httpx.Response(200, json={"errors": [{"message": "Access denied for products field."}]})

    with pytest.raises(SystemExit, match="Access denied"):
        capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(errors))
    assert not list(tmp_path.glob("*.json"))


def test_a_non_json_answer_stops_cleanly(env: None, tmp_path: Path) -> None:
    def html(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86399})
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(SystemExit, match="not JSON"):
        capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(html))


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ConnectError])
def test_transport_errors_stop_with_the_domain_redacted(env: None, tmp_path: Path, error: type) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error(f"hostname mismatch for {SHOP}", request=request)

    with pytest.raises(SystemExit) as caught:
        capture_live.main(["--out-dir", str(tmp_path)], transport=httpx.MockTransport(fail))
    message = str(caught.value)
    assert message.startswith("could not read the store")
    assert SHOP not in message and SECRET not in message
    assert not list(tmp_path.glob("*.json"))


# Every capture committed from a real store must still map and audit, and must have the shape of the node the mock in
# tests/integration/test_shopify_adapters.py sends: the same nested keys, and the same value types where both are set.
# A difference means the mock drifted from Shopify, and the adapter tests prove less than they claim.
MOCK_NODE = to_node(make_product())
CAPTURES = sorted(capture_live.CAPTURE_DIR.glob("products-*.json"))


def _shape_differences(mock: Any, real: Any, path: str = "node") -> list[str]:
    if mock is None or real is None:
        return []  # Shopify sends null for an empty optional field; the mock sometimes does too.
    if isinstance(mock, dict):
        if not isinstance(real, dict):
            return [f"{path}: expected an object, got {type(real).__name__}"]
        return [
            d
            for key, value in mock.items()
            for d in (
                [f"{path}.{key}: missing"] if key not in real else _shape_differences(value, real[key], f"{path}.{key}")
            )
        ]
    if isinstance(mock, list):
        if not isinstance(real, list):
            return [f"{path}: expected a list, got {type(real).__name__}"]
        if not mock:
            return []
        return [d for i, item in enumerate(real) for d in _shape_differences(mock[0], item, f"{path}[{i}]")]
    if type(mock) is not type(real):
        return [f"{path}: mock has {type(mock).__name__}, Shopify sent {type(real).__name__}"]
    return []


def test_the_shape_check_catches_a_drifted_field() -> None:
    drifted = to_node(make_product())
    drifted["variants"]["edges"][0]["node"]["price"] = {"amount": "32.00", "currencyCode": "CAD"}
    del drifted["seo"]
    assert _shape_differences(MOCK_NODE, drifted) == [
        "node.seo: missing",
        "node.variants.edges[0].node.price: mock has str, Shopify sent dict",
    ]


@pytest.mark.parametrize("path", CAPTURES or [None], ids=[p.name for p in CAPTURES] or ["no capture yet"])
def test_real_captures_map_and_match_the_mock_shape(path: Path | None) -> None:
    if path is None:
        pytest.skip("no capture from a real store has been saved yet")
    saved = json.loads(path.read_text(encoding="utf-8"))
    edges = capture_live.edges_of(saved)
    assert edges, "a capture with no products tests nothing"
    for edge in edges:
        node = edge["node"]
        assert _shape_differences(MOCK_NODE, node) == [], node.get("handle")
        product_from_node(node)
    assert len(capture_live.report(saved)) == len(edges)
