from __future__ import annotations

from decimal import Decimal

import pytest

from gatekeeper.config import Settings
from gatekeeper.domain.canonical import html_canon
from gatekeeper.domain.product import approval_hash, content_hash, fields_hash
from gatekeeper.domain.risk import RiskInput, risk_score
from gatekeeper.llm.port import Usage, cost_usd
from tests.conftest import make_product


def test_html_canon_ignores_insignificant_differences():
    assert html_canon("<P>Hi</P>\r\n  <BR/>") == html_canon("<p>Hi</p><br>")
    assert html_canon("<p>one   two</p>") == html_canon("<p>one two</p>")


def test_html_canon_keeps_meaningful_differences():
    assert html_canon("<p>one</p>") != html_canon("<p>two</p>")


def test_content_hash_is_stable_across_line_endings_and_tag_order():
    a = make_product(description_html="<p>Same text</p>\n<p>Second</p>", tags=["b", "a"])
    b = make_product(description_html="<p>Same text</p>\r\n<p>Second</p>", tags=["a", "b"])
    assert content_hash(a) == content_hash(b)


def test_content_hash_excludes_updated_at():
    assert content_hash(make_product(updated_at="2026-01-01T00:00:00Z")) == content_hash(
        make_product(updated_at="2026-09-09T00:00:00Z")
    )


@pytest.mark.parametrize(
    "change",
    [{"title": "x"}, {"vendor": "x"}, {"description_html": "<p>x</p>"}, {"seo_title": "x"}, {"status": "DRAFT"}],
)
def test_content_hash_changes_with_content(change):
    assert content_hash(make_product()) != content_hash(make_product(**change))


def test_fields_hash_ignores_fields_a_proposal_cannot_write():
    assert fields_hash(make_product(title="A")) == fields_hash(make_product(title="B"))
    assert fields_hash(make_product(seo_title="A")) != fields_hash(make_product(seo_title="B"))


def test_approval_hash_binds_every_input():
    base = {
        "proposal_id": "p",
        "shop_id": 1,
        "product_gid": "g",
        "base_content_hash": "h",
        "proposed": {"seo_title": "x"},
    }
    reference = approval_hash(**base)
    for key, value in (
        ("proposal_id", "q"),
        ("shop_id", 2),
        ("product_gid", "z"),
        ("base_content_hash", "i"),
        ("proposed", {"seo_title": "y"}),
    ):
        assert approval_hash(**{**base, key: value}) != reference


def test_risk_score_adds_and_clamps():
    assert risk_score(RiskInput())[0] == 0
    assert risk_score(RiskInput(judge_status="unavailable"))[0] == 25
    assert risk_score(RiskInput(open_injection_finding=True))[0] == 30
    assert risk_score(RiskInput(verified_judge_concerns=10))[0] == 45
    assert (
        risk_score(
            RiskInput(
                failed_warnings=4, verified_judge_concerns=9, judge_status="unavailable", open_injection_finding=True
            )
        )[0]
        == 100
    )


def test_cost_uses_pricing_table_and_unknown_model_is_none():
    assert cost_usd("claude-opus-5-5", Usage(1_000_000, 1_000_000)) == Decimal("24")
    assert cost_usd("not-a-model", Usage(10, 10)) is None


class TestBootSafety:
    def _production(self, **overrides):
        base = dict(
            env="production",
            reviewer_tokens="a:admin:" + "0" * 64,
            shopify_webhook_secret="s",
            llm_provider="anthropic",
            anthropic_api_key="k",
            shopify_adapter="live",
            shopify_access_token="t",
            shopify_shop_domain="x.myshopify.com",
        )
        base.update(overrides)
        return Settings(**base)

    def test_good_production_config_boots(self):
        assert self._production().env == "production"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"demo_mode": True},
            {"reviewer_tokens": ""},
            {"shopify_webhook_secret": None},
            {"llm_provider": "auto"},
            {"llm_provider": "heuristic"},
            {"anthropic_api_key": None},
            {"shopify_adapter": "live", "shopify_access_token": None},
            {"shopify_adapter": "fixture"},
            {"shopify_webhook_secret": "   "},
        ],
    )
    def test_unsafe_production_config_refuses_to_boot(self, overrides):
        with pytest.raises(ValueError):
            self._production(**overrides)
