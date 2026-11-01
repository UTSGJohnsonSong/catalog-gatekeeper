"""End-to-end smoke test over HTTP against a running `docker compose up`, using only the public API.

It signs in with the demo token, waits for a fix, approves it, waits until it is written, checks that the
simulated store really changed, then checks that a blocked fix cannot be approved and that a merchant edit makes an
open fix go out of date. Also exercises the MCP server over streamable HTTP. The model here is the template script.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
MCP_URL = "http://127.0.0.1:8808/mcp"
TOKEN = "demo-admin"


def call(method: str, path: str, body: dict | None = None, token: str | None = TOKEN) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def wait_for(description: str, check, seconds: float = 90.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise SystemExit(f"FAILED: timed out waiting for {description}")


def expect(condition: bool, what: str) -> None:
    print(("ok   " if condition else "FAIL ") + what)
    if not condition:
        raise SystemExit(f"FAILED: {what}")


async def check_mcp() -> None:
    from mcp import Client

    async with Client(MCP_URL) as client:
        tools = (await client.list_tools()).tools
        expect(all(t.annotations and t.annotations.read_only_hint for t in tools), f"MCP exposes {len(tools)} tools, all read-only")
        result = await client.call_tool("audit_list_proposals", {"limit": 3})
        rows = json.loads(result.content[0].text)
        expect(bool(rows) and rows[0]["mode"] == "demo", "MCP reads proposals over streamable HTTP and labels the demo model")
        search = await client.call_tool("policy_search", {"query_text": "how long do I have to return unworn items", "k": 1})
        expect(json.loads(search.content[0].text)[0]["doc_id"] == "returns", "MCP policy search finds the returns policy")


def main() -> int:
    status, meta = wait_for("the API", lambda: (lambda r: r if r[0] == 200 else None)(call("GET", "/api/meta", token=None)))
    expect(status == 200 and meta["demo_mode"] and meta["model_mode"] == "demo", f"API is up in demo mode, model {meta['model']}")
    expect(call("GET", "/api/queue", token=None)[0] == 401, "the queue refuses a request with no token")

    def queue_has_all():
        items = call("GET", "/api/queue")[1]["items"]
        statuses = {i["status"] for i in items}
        return items if "pending_review" in statuses and "blocked" in statuses else None

    items = wait_for("pending and blocked fixes", queue_has_all)
    pending = [i for i in items if i["status"] == "pending_review"]
    expect(all(i["model_mode"] == "demo" for i in items), f"{len(items)} fixes in the queue, all labelled as demo model")

    target = pending[0]
    detail = call("GET", f"/api/proposals/{target['id']}")[1]
    expect(len(detail["validation"]) == 12 and detail["diffs"], "a proposal shows its diff and all 12 checks")
    conflict, _ = call("POST", f"/api/proposals/{target['id']}/approve", {"review_hash": "0" * 64})
    expect(conflict == 409, "approval with the wrong review hash is refused")
    approved, _ = call("POST", f"/api/proposals/{target['id']}/approve", {"review_hash": detail["review_hash"]})
    expect(approved == 200, "approval with the right review hash is accepted")
    final = wait_for(
        "the write-back",
        lambda: (lambda p: p if p["status"] in ("applied", "failed", "stale") else None)(call("GET", f"/api/proposals/{target['id']}")[1]),
    )
    expect(final["status"] == "applied" and final["writeback_status"] == "verified", "the fix was written and verified by read-back")
    products = call("GET", "/api/products")[1]
    row = next(p for p in products if p["id"] == target["product_id"])
    expect(row["content_hash"] != detail["base_content_hash"], "the product's content hash changed in the store")

    blocked = next(i for i in items if i["status"] == "blocked")
    blocked_detail = call("GET", f"/api/proposals/{blocked['id']}")[1]
    expect(blocked_detail["review_hash"] is None, "a blocked fix offers no review hash")
    refused, _ = call("POST", f"/api/proposals/{blocked['id']}/approve", {"review_hash": "a" * 64})
    expect(refused == 409, "a blocked fix cannot be approved")

    second = pending[1]
    edited, _ = call("POST", "/demo/simulate-edit", {"product_id": second["product_id"]})
    expect(edited == 200, "a simulated merchant edit sends a signed webhook through the real route")
    wait_for("the fix to go out of date", lambda: call("GET", f"/api/proposals/{second['id']}")[1]["status"] == "stale")
    expect(True, "the open fix went stale after the merchant edit")

    actions = {a["action"] for a in call("GET", "/api/audit-log")[1]}
    expect({"proposal.approved", "proposal.applied", "proposal.stale"} <= actions, "the audit log recorded each step")
    asyncio.run(check_mcp())
    print("SMOKE PASSED (store simulated, model is a template script, not an LLM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
