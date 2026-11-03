"""Smoke test for the public demo layout, over HTTP, against `docker compose -f docker-compose.public.yml up`.

It checks what a stranger can and cannot do: sign in only with the reviewer demo token, no admin actions, the full
review flow still works with the worker running in the same container, and oversized or rapid requests are refused.
Run it right after a deploy or a reset, while the queue still holds the demo's first fixes.
Set SMOKE_BASE to point it at a hosted copy, for example https://your-demo.onrender.com.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8001").rstrip("/")
TOKEN = "demo-reviewer"
REFUSED_TOKEN = "demo-admin"  # Valid in the private demo, and must not be in a public one.


def call(
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = TOKEN,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object, dict[str, str]]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    sent = {"Content-Type": "application/json"} if data else {}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    sent.update(headers or {})
    request = urllib.request.Request(BASE + path, data=data, headers=sent, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null"), _lower(response.headers.items())
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload or b"null")
        except ValueError:
            parsed = payload.decode(errors="replace")
        return error.code, parsed, _lower(error.headers.items())


def _lower(items) -> dict[str, str]:
    """Header names are case-insensitive, and the server sends them in lower case."""
    return {name.lower(): value for name, value in items}


def wait_for(description: str, check, seconds: float = 180.0):
    deadline = time.monotonic() + seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = check()
        except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
            last_error, result = error, None
        if result:
            return result
        time.sleep(2)
    cause = f"; last error: {last_error!r}" if last_error else ""
    raise SystemExit(f"FAILED: timed out waiting for {description}{cause}")


def expect(condition: bool, what: str, observed: object = None) -> None:
    print(("ok   " if condition else "FAIL ") + what)
    if not condition:
        raise SystemExit(f"FAILED: {what}" + (f" (observed: {observed!r})" if observed is not None else ""))


def main() -> int:
    meta = wait_for("the API", lambda: (lambda r: r[1] if r[0] == 200 else None)(call("GET", "/api/meta", token=None)))
    expect(meta["public_demo"] and meta["model_mode"] == "demo", "API is up as a public demo with the template model")
    expect(meta["demo_token"] == TOKEN, "the sign-in page is offered the reviewer token, not the admin one", meta)
    refused = call("GET", "/api/whoami", token=REFUSED_TOKEN)[0]
    expect(refused == 401, "the admin demo token is refused", refused)
    me = call("GET", "/api/whoami")[1]
    expect(me == {"name": "demo_reviewer", "role": "reviewer"}, "the reviewer token works", me)
    switch = call("POST", "/api/admin/writeback", {"enabled": False})[0]
    expect(switch == 403, "visitors cannot switch write-back off", switch)

    def two_pending_fixes():
        items = call("GET", "/api/queue")[1]["items"]
        found = [i for i in items if i["status"] == "pending_review"]
        return found if len(found) >= 2 else None

    # Two, because the merchant-edit check below needs a fix of its own. Visitors may have decided the others.
    pending = wait_for("two pending fixes; reset the demo if visitors decided them", two_pending_fixes)
    target, second = pending[0], pending[1]
    detail = call("GET", f"/api/proposals/{target['id']}")[1]
    approved = call("POST", f"/api/proposals/{target['id']}/approve", {"review_hash": detail["review_hash"]})
    expect(approved[0] == 200, "a visitor can approve a fix", approved[:2])
    final = wait_for(
        "the write-back",
        lambda: (lambda p: p if p["status"] in ("applied", "failed", "stale") else None)(
            call("GET", f"/api/proposals/{target['id']}")[1]
        ),
    )
    expect(
        final["status"] == "applied" and final["writeback_status"] == "verified",
        "the fix was written and verified",
        (final["status"], final["writeback_status"]),
    )

    edited = call("POST", "/demo/simulate-edit", {"product_id": second["product_id"]})
    expect(edited[0] == 200, "a simulated merchant edit delivers its signed webhook over loopback", edited[:2])
    wait_for("the fix to go stale", lambda: call("GET", f"/api/proposals/{second['id']}")[1]["status"] == "stale")
    expect(True, "the open fix went stale after the merchant edit")

    too_big = call("POST", f"/api/proposals/{target['id']}/reject", raw=b'{"note": "' + b"x" * 70_000 + b'"}')
    expect(too_big[0] == 413, "a body over the cap is refused before parsing", too_big[0])

    # Spend this address's allowance while claiming to be 1.1.1.1, then claim to be 2.2.2.2. The claim is the client's
    # own, so the server must not believe it. A 2xx or 403 here means the proxy hop count trusts forged addresses.
    statuses = [call("POST", "/api/admin/sync", headers={"X-Forwarded-For": "1.1.1.1"})[0] for _ in range(30)]
    forged = call("POST", "/api/admin/sync", headers={"X-Forwarded-For": "2.2.2.2"})
    expect(429 in statuses, "rapid changes are rate limited", statuses)
    expect(
        forged[0] == 429 and "retry-after" in forged[2], "a forged X-Forwarded-For buys no fresh allowance", forged[0]
    )
    expect("strict-transport-security" in forged[2], "even a refused request carries the security headers")
    print("PUBLIC SMOKE PASSED (store simulated, model is a template script, not an LLM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
