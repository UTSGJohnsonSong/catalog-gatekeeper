"""Independent checks that a base product really is clean. Written separately from gatekeeper.rules on purpose:
if a base fails here, that is a generator bug, and it is found without asking the code under test."""

from __future__ import annotations

import re
from typing import Any

from gatekeeper_eval.generator.base_products import SPEC


def check_base(base: dict[str, Any]) -> None:
    p = base["product"]
    visible = re.sub(r"<[^>]+>", " ", p["description_html"])
    assert len(visible.strip()) >= SPEC["desc_thin_min"], base["base_id"]
    assert p["description_html"].count("<p>") == p["description_html"].count("</p>"), base["base_id"]
    assert p["description_html"].count("<li>") == p["description_html"].count("</li>"), base["base_id"]
    assert 0 < len(p["seo_title"]) <= SPEC["seo_title_max"], base["base_id"]
    assert 0 < len(p["seo_description"]) <= SPEC["seo_desc_max"], base["base_id"]
    assert all(i["alt"].strip() for i in p["images"]), base["base_id"]
    assert all(float(v["price"]) > 0 for v in p["variants"]), base["base_id"]
    assert p["title"] != p["title"].upper(), base["base_id"]
