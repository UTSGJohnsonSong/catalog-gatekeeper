"""The demo model: a deterministic template script that speaks the tool protocol. It is not an LLM.

It exists so the whole pipeline runs with no key. Its output says nothing about model accuracy, and every
surface that shows its work labels it (see docs/DEMO_HONESTY.md). It fills a fixed template from product fields
and never interprets their text, so an instruction in a product cannot change what it does. The one exception is
the fixture-only demo.misbehave metafield, which makes it emit a deliberately bad fix for the demo.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from gatekeeper.agent.fence import unfence
from gatekeeper.llm.port import Block, LLMTurn, Mode, ToolSpec

MODEL_ID = "heuristic-v1"
# (search query, phrase the cited sentence must contain). The phrase keeps the template from citing a passage
# that exists but does not apply to this kind of product, which the mechanical citation check cannot notice.
_POLICY_BY_TYPE = {
    "footwear": ("trying on shoes indoors before a return", "Shoes may be tried on"),
    "apparel": ("exchanges for a different size or colour", "Exchanges for a different size"),
    "drinkware": ("warranty on insulated bottles and tumblers", "Insulated bottles and tumblers"),
    "bags": ("warranty on bags and backpacks", "Bags and backpacks"),
    "outdoor": ("warranty on tents and sleeping bags", "Tents, sleeping bags"),
}
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]")


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-|")


def _block(call_id: str, name: str, tool_input: dict[str, Any]) -> Block:
    return Block("tool_use", id=call_id, name=name, input=tool_input)


class HeuristicProvider:
    name = "heuristic"
    mode: Mode = "demo"
    model = MODEL_ID

    def create(
        self, *, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]], max_tokens: int, effort: str
    ) -> LLMTurn:
        calls, results = self._history(messages)
        step = len(calls)
        called = {c["name"] for c in calls}
        if "get_product" not in called:
            return self._emit(_block(f"h{step}", "get_product", {}))
        if "list_findings" not in called:
            return self._emit(_block(f"h{step}", "list_findings", {}))
        product = json.loads(results[next(c["id"] for c in calls if c["name"] == "get_product")])
        findings = json.loads(results[next(c["id"] for c in calls if c["name"] == "list_findings")])["findings"]
        fixable = {f["rule_id"] for f in findings if f["fixable"]}
        flags = ["injection_suspected"] if any(f["rule_id"] == "A13" for f in findings) else []
        misbehave = unfence(product.get("metafields", {}).get("demo.misbehave", ""))
        if not fixable:
            return self._emit(
                _block(
                    f"h{step}", "finish", {"outcome": "no_fix_needed", "summary": "No fixable finding.", "flags": flags}
                )
            )
        if "search_policy" not in called and {"A01", "A02", "A03"} & fixable:
            policy = _POLICY_BY_TYPE.get(unfence(product["fields"]["product_type"]).casefold())
            if policy:
                return self._emit(_block(f"h{step}", "search_policy", {"query": policy[0], "k": 3}))
        if "propose_fix" not in called:
            hits = []
            if "search_policy" in called:
                hits = json.loads(results[next(c["id"] for c in calls if c["name"] == "search_policy")]).get(
                    "results", []
                )
            return self._emit(
                _block(f"h{step}", "propose_fix", self._fix(product, fixable, hits, misbehave, bool(flags)))
            )
        outcome = json.loads(results[next(c["id"] for c in calls if c["name"] == "propose_fix")])
        finished = "proposed" if outcome.get("status") in ("pending_review", "blocked") else "cannot_fix"
        if misbehave:
            flags = [*flags, "demo_adversarial"]
        return self._emit(
            _block(f"h{step}", "finish", {"outcome": finished, "summary": "Template fix submitted.", "flags": flags})
        )

    @staticmethod
    def _emit(block: Block) -> LLMTurn:
        content = [{"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}]
        return LLMTurn("tool_use", [block], content, model=MODEL_ID)

    @staticmethod
    def _history(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
        calls: list[dict[str, Any]] = []
        results: dict[str, str] = {}
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    calls.append(block)
                elif block.get("type") == "tool_result":
                    results[block["tool_use_id"]] = block["content"]
        return calls, results

    def _fix(
        self, product: dict[str, Any], fixable: set[str], hits: list[dict[str, Any]], misbehave: str, injected: bool
    ) -> dict[str, Any]:
        fields = {k: unfence(v) for k, v in product["fields"].items()}
        title, vendor, kind = fields["title"], fields["vendor"], fields["product_type"]
        changes: dict[str, str] = {}
        citations: list[dict[str, str]] = []
        sentences = [f"{title} by {vendor} belongs to our {kind.lower() or 'store'} range."]
        visible = fields.get("description_text", "")
        keep_original = visible and not injected and not {"A01", "A03"} & fixable
        options = sorted(
            {unfence(o) for v in product.get("variants", []) for o in v.get("options", [])} - {"", "Default Title"}
        )
        paragraphs = [f"<p>{html.escape(sentences[0], quote=False)}</p>"]
        if keep_original:
            paragraphs.append(f"<p>{html.escape(visible, quote=False)}</p>")
        if options:
            paragraphs.append(f"<p>Choose from: {html.escape(', '.join(options), quote=False)}.</p>")
        paragraphs.append("<p>Check the product details on this page before you choose.</p>")
        required = _POLICY_BY_TYPE.get(kind.casefold(), ("", ""))[1]
        for hit in hits:
            sentence = self._sentence_with(hit["text"], required)
            if sentence:
                paragraphs.append(f"<p>{html.escape(sentence, quote=False)}</p>")
                citations.append({"doc_id": hit["doc_id"], "chunk_id": hit["chunk_id"], "quote": sentence})
                break
        new_description = "".join(paragraphs)
        if {"A01", "A02", "A03", "A04"} & fixable:
            changes["description_html"] = new_description
        if {"A05", "A06"} & fixable:
            changes["seo_title"] = _cut(f"{title} | {vendor}" if vendor else title, 70)
        if {"A07", "A08"} & fixable:
            changes["seo_description"] = _cut(sentences[0], 155)
        rationale = f"Template fix for {', '.join(sorted(fixable))}, built from structured product fields."
        if misbehave == "bad_number":
            changes["description_html"] = (
                changes.get("description_html", new_description) + "<p>Weighs only 1.7 kg.</p>"
            )
        elif misbehave == "link":
            changes["description_html"] = (
                changes.get("description_html", new_description) + "<p>More at www.example-deals.com today.</p>"
            )
        elif misbehave == "bad_citation" and citations:
            citations[0] = {**citations[0], "quote": citations[0]["quote"].replace("the", "every", 1) + " Always."}
        return {"changes": changes, "rationale": rationale, "citations": citations}

    @staticmethod
    def _sentence_with(text: str, phrase: str) -> str:
        for match in _SENTENCE.finditer(text):
            sentence = match.group(0).strip()
            if phrase in sentence and 20 <= len(sentence) <= 300:
                return sentence
        return ""
