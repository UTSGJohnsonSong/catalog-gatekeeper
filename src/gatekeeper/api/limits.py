"""Request limits for a deployment that strangers can reach: a body size cap and per-client rate limits.

The rate limiter lives in process memory, so it limits one API process. The free hosting layout runs exactly one.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK = frozenset({"127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def forwarded_entries(forwarded_for: str | None) -> list[str]:
    return [part.strip() for part in (forwarded_for or "").split(",") if part.strip()]


def bucket_key(address: str) -> str:
    """One bucket per IPv4 address and per IPv6 /64, because a single host commonly holds a whole /64.

    A port, which some proxies append, is dropped: otherwise every new connection would get a fresh bucket."""
    if address.startswith("[") and "]" in address:
        address = address[1 : address.index("]")]
    elif address.count(":") == 1:
        address = address.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network((ip, 64), strict=False))
    return str(ip)


def client_ip(peer: str | None, forwarded_for: str | None, trusted_hops: int) -> str:
    """The address that the outermost trusted proxy saw.

    Each proxy appends the address it received from, so entries to the left of the trusted ones were written by the
    client and can be forged. With `trusted_hops` proxies, the client is the entry that many places from the right.
    A shorter chain means a request skipped a proxy or the count is too high. Then the socket peer is used: all such
    clients share one bucket, which is safe, where trusting their own entry would let each pick a fresh one.
    """
    if trusted_hops <= 0 or not forwarded_for:
        return peer or "unknown"
    entries = forwarded_entries(forwarded_for)
    if len(entries) < trusted_hops:
        return peer or "unknown"
    return entries[-trusted_hops]


class RateLimiter:
    """Token buckets keyed by client. Old keys are evicted, so many distinct clients cannot grow memory unbounded."""

    def __init__(self, per_minute: int, *, max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.pop(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return allowed

    def retry_after(self) -> int:
        """Whole seconds until one more request fits in an empty bucket."""
        return max(1, int(1.0 / self._rate + 0.999))


class BodySizeLimit:
    """Refuses request bodies over `max_bytes` before the app reads them, declared length or not.

    Bodies here are small JSON documents, so buffering up to the cap is cheap. The webhook route may keep its own
    larger cap, because Shopify sends whole products; a public demo has no real webhooks and passes None."""

    def __init__(self, app: ASGIApp, *, max_bytes: int, exempt_prefix: str | None = "/webhooks/") -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.exempt_prefix = exempt_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in SAFE_METHODS
            or (self.exempt_prefix is not None and str(scope["path"]).startswith(self.exempt_prefix))
        ):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        too_big = f"Request body is over {self.max_bytes} bytes."
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._respond(send, 413, too_big)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # Answer anyway. The server drops it, as the client is gone, but returning without a response makes the
                # middleware above raise "No response returned" and log a client's hang-up as a server error.
                await self._respond(send, 400, "The request body was not completely received.")
                return
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await self._respond(send, 413, too_big)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _respond(send: Send, status: int, detail: str) -> None:
        # "detail", like FastAPI's own errors, because that is the field the UI shows.
        payload = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload})
