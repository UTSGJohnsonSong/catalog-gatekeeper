"""FastAPI app: review API, signed webhooks, health checks, and the built SPA."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import errors as pg_errors
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gatekeeper import jobs
from gatekeeper.api import schemas
from gatekeeper.api.auth import Principal, current_principal, demo_token, load_tokens, require_admin
from gatekeeper.api.limits import (
    LOOPBACK,
    SAFE_METHODS,
    BodySizeLimit,
    RateLimiter,
    bucket_key,
    client_ip,
    forwarded_entries,
)
from gatekeeper.config import Settings, get_settings
from gatekeeper.context import AppContext, build_context
from gatekeeper.db import tx
from gatekeeper.domain.product import ALLOWED_FIELDS
from gatekeeper.migrate import latest_version
from gatekeeper.rules.spec import AUDIT_RULES, VALIDATORS
from gatekeeper.services import review as review_service
from gatekeeper.services import webhooks as webhook_service
from gatekeeper.services.writeback import set_kill_switch
from gatekeeper.shopify import webhook_hmac
from gatekeeper.shopify.fixture import FixtureShopify
from gatekeeper.worker import configure_logging

log = logging.getLogger("gatekeeper.api")
MAX_WEBHOOK_BYTES = 5 * 1024 * 1024
ADDRESS_CHECKS_LOGGED = 5
# The review app under /app is built from Shopify's Polaris web components. Only those pages allow two paths on
# Shopify's CDN, and not the whole host, which also serves files that any merchant uploads (/s/files/):
# /shopifycloud/ for the script and the icons it fetches, and /static/fonts/ for its font stylesheets and fonts.
# Polaris also writes <style> blocks of its own; hashes of them would break whenever Shopify updates polaris-2.js,
# so these pages allow inline styles. That needs an HTML injection to abuse, and the app renders catalog text as
# text only. No inline script is allowed anywhere. The landing page and the API stay same-origin.
POLARIS_CODE = "https://cdn.shopify.com/shopifycloud/"
POLARIS_FONTS = "https://cdn.shopify.com/static/fonts/"
_CSP_TAIL = "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
SAME_ORIGIN_CSP = f"default-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; {_CSP_TAIL}"
REVIEW_APP_CSP = (
    f"default-src 'self'; script-src 'self' {POLARIS_CODE}; style-src 'self' {POLARIS_FONTS} 'unsafe-inline'; "
    f"font-src 'self' {POLARIS_FONTS}; img-src 'self' data: {POLARIS_CODE}; connect-src 'self' {POLARIS_CODE}; "
    f"{_CSP_TAIL}"
)


def is_review_app_path(path: str) -> bool:
    return path == "/app" or path.startswith("/app/")


JUDGE_NAMES = {
    "J00": "JUDGE_UNAVAILABLE",
    "J01": "JUDGE_UNSUPPORTED_CLAIM",
    "J02": "JUDGE_MEANING_DRIFT",
    "J03": "JUDGE_TONE",
}


def _ctx(request: Request) -> AppContext:
    return cast(AppContext, request.app.state.ctx)


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns = ctx is None
        if owns:
            configure_logging()
        app.state.ctx = ctx or build_context(settings)
        yield
        if owns:
            app.state.ctx.pool.close()

    in_production = settings.env == "production"
    hide_docs = in_production or settings.public_demo
    app = FastAPI(
        title="Catalog Gatekeeper",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if hide_docs else "/docs",
        redoc_url=None if hide_docs else "/redoc",
        openapi_url=None if hide_docs else "/openapi.json",
    )
    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",")])
    app.state.tokens = load_tokens(settings)
    app.state.settings = settings
    # Middleware added later wraps what came before, so security headers stay outermost and cover 413 and 429 too.
    # A public demo has no real webhooks, so its webhook route gets the small cap as well.
    app.add_middleware(
        BodySizeLimit,
        max_bytes=settings.max_request_bytes,
        exempt_prefix=None if settings.public_demo else "/webhooks/",
    )

    if settings.public_demo:
        # Changes and reads have separate budgets. Reads are capped too, because each one costs the free database.
        writes = (RateLimiter(settings.rate_limit_per_minute), RateLimiter(settings.rate_limit_global_per_minute))
        reads = (RateLimiter(settings.read_limit_per_minute), RateLimiter(settings.read_limit_global_per_minute))

        # What the deployer needs to check TRUSTED_PROXY_HOPS, without logging anyone's address: how many entries
        # X-Forwarded-For carried, for the first requests after boot. docs/DEPLOY_DEMO.md explains the reading.
        watch = {"shown": 0, "short_chain_warned": False, "busy_logged_at": -3600.0}

        def too_many(limiter: RateLimiter, message: str) -> JSONResponse:
            return JSONResponse(
                {"detail": f"{message} Wait a moment and try again."},
                status_code=429,
                headers={"Retry-After": str(limiter.retry_after())},
            )

        @app.middleware("http")
        async def rate_limit(request: Request, call_next: Any) -> Response:
            path = request.url.path
            peer = request.client.host if request.client else None
            # Every X-Forwarded-For line, in order: a proxy may add its own line instead of extending the first.
            forwarded = ", ".join(request.headers.getlist("x-forwarded-for")) or None
            # The demo's own simulated edit reaches the webhook over loopback with no proxy header. It is not a visitor.
            internal = forwarded is None and peer in LOOPBACK
            if not internal and path != "/healthz" and not path.startswith("/assets/"):
                hops, entries = settings.trusted_proxy_hops, len(forwarded_entries(forwarded))
                if watch["shown"] < ADDRESS_CHECKS_LOGGED:
                    watch["shown"] += 1
                    log.info(
                        "client address check: X-Forwarded-For has %d entries, TRUSTED_PROXY_HOPS=%d", entries, hops
                    )
                if 0 < entries < hops and not watch["short_chain_warned"]:
                    watch["short_chain_warned"] = True
                    log.warning(
                        "X-Forwarded-For had fewer entries than TRUSTED_PROXY_HOPS; such requests share a bucket"
                    )
                per_client, overall = reads if request.method in SAFE_METHODS else writes
                if not per_client.allow(bucket_key(client_ip(peer, forwarded, hops))):
                    return too_many(per_client, "Too many requests from this address.")
                if not overall.allow("*"):
                    if time.monotonic() - watch["busy_logged_at"] >= 60:
                        watch["busy_logged_at"] = time.monotonic()
                        log.warning("the demo-wide rate limit was reached; every visitor is refused for now")
                    return too_many(overall, "The demo is busy.")
            return cast(Response, await call_next(request))

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # A public demo is only reachable through the host's HTTPS proxy.
        if in_production or settings.public_demo:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = (
            REVIEW_APP_CSP if is_review_app_path(request.url.path) else SAME_ORIGIN_CSP
        )
        return response

    # Health ---------------------------------------------------------------------------------------
    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz(request: Request, response: Response) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        ready = True
        try:
            with tx(_ctx(request).pool) as conn:
                conn.execute("SELECT 1")
                version = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"]
                checks["migrations"] = version == latest_version()
                beat = conn.execute(
                    "SELECT extract(epoch FROM now() - at) AS age FROM heartbeats WHERE name = 'worker'"
                ).fetchone()
            checks["database"] = True
            age = float(beat["age"]) if beat else None
            checks["worker_heartbeat_age_seconds"] = age
            worker_ok = settings.env == "test" or (age is not None and age < 120)
            ready = bool(checks["migrations"]) and worker_ok
        except Exception as exc:  # noqa: BLE001 - a readiness probe reports failure instead of raising
            log.warning("readiness check failed: %s", type(exc).__name__)
            checks["database"] = False
            ready = False
        if not ready:
            response.status_code = 503
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    # Meta -----------------------------------------------------------------------------------------
    @app.get("/api/meta", response_model=schemas.Meta, tags=["meta"])
    def meta(request: Request) -> schemas.Meta:
        ctx_ = _ctx(request)
        with tx(ctx_.pool) as conn:
            shop = conn.execute("SELECT writeback_enabled FROM shops WHERE id = %s", (ctx_.shop_id,)).fetchone()
        return schemas.Meta(
            demo_mode=settings.demo_mode,
            public_demo=settings.public_demo,
            demo_token=demo_token(settings),
            demo_reset_minutes=settings.demo_reset_minutes if settings.public_demo else 0,
            model_mode=ctx_.provider.mode,
            model=ctx_.provider.model,
            provider=ctx_.provider.name,
            shopify_adapter=settings.shopify_adapter,
            judge="enabled" if ctx_.judge else "disabled",
            writeback_env_enabled=settings.writeback_enabled,
            writeback_shop_enabled=shop["writeback_enabled"],
        )

    @app.get("/api/whoami", response_model=schemas.Whoami, tags=["meta"])
    def whoami(principal: Principal = Depends(current_principal)) -> schemas.Whoami:
        return schemas.Whoami(name=principal.name, role=principal.role)

    # Review queue ---------------------------------------------------------------------------------
    @app.get("/api/queue", response_model=schemas.Queue, tags=["review"])
    def queue(
        request: Request,
        status: str | None = None,
        limit: int = Query(100, ge=1, le=200),
        _: Principal = Depends(current_principal),
    ) -> schemas.Queue:
        where = "WHERE p.status = %s" if status else ""
        params: list[Any] = ([status] if status else []) + [limit]
        with tx(_ctx(request).pool) as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.product_id, pr.title, p.status, p.risk_score, p.created_at, ar.mode, ar.model,
                  (SELECT count(*) FROM findings f WHERE f.product_id = p.product_id AND f.status = 'open') AS open_findings,
                  (SELECT coalesce(array_agg(v.rule_id ORDER BY v.rule_id), '{{}}') FROM validation_results v
                   WHERE v.proposal_id = p.id AND NOT v.passed) AS failed_rules
                FROM proposals p JOIN products pr ON pr.id = p.product_id LEFT JOIN agent_runs ar ON ar.id = p.run_id
                {where}
                ORDER BY CASE p.status WHEN 'pending_review' THEN 0 WHEN 'approved' THEN 1 WHEN 'applying' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
                         p.risk_score DESC, p.created_at DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
            counts = conn.execute(
                "SELECT status::text AS status, count(*) AS n FROM proposals GROUP BY status"
            ).fetchall()
        return schemas.Queue(
            items=[
                schemas.QueueItem(
                    id=str(r["id"]),
                    product_id=r["product_id"],
                    title=r["title"],
                    status=r["status"],
                    risk_score=r["risk_score"],
                    failed_rules=r["failed_rules"],
                    open_findings=r["open_findings"],
                    model_mode=r["mode"],
                    model=r["model"],
                    created_at=r["created_at"],
                )
                for r in rows
            ],
            counts={r["status"]: r["n"] for r in counts},
        )

    @app.get("/api/proposals/{proposal_id}", response_model=schemas.ProposalDetail, tags=["review"])
    def proposal(
        request: Request, proposal_id: UUID, _: Principal = Depends(current_principal)
    ) -> schemas.ProposalDetail:
        return _proposal_detail(_ctx(request), str(proposal_id))

    @app.post("/api/proposals/{proposal_id}/approve", response_model=schemas.DecisionResult, tags=["review"])
    def approve(
        request: Request,
        proposal_id: UUID,
        body: schemas.ApproveRequest,
        principal: Principal = Depends(current_principal),
    ) -> schemas.DecisionResult:
        pid = str(proposal_id)
        try:
            review_service.approve(
                _ctx(request).pool,
                pid,
                reviewer=principal.name,
                review_hash=body.review_hash,
                confirm_high_risk=body.confirm_high_risk,
            )
        except KeyError as exc:
            raise HTTPException(404, "Proposal not found") from exc
        except review_service.ReviewInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except (review_service.ReviewConflict, pg_errors.UniqueViolation) as exc:
            raise HTTPException(409, str(exc) or "conflict") from exc
        return schemas.DecisionResult(id=pid, status="approved")

    @app.post("/api/proposals/{proposal_id}/reject", response_model=schemas.DecisionResult, tags=["review"])
    def reject(
        request: Request,
        proposal_id: UUID,
        body: schemas.RejectRequest,
        principal: Principal = Depends(current_principal),
    ) -> schemas.DecisionResult:
        pid = str(proposal_id)
        try:
            review_service.reject(_ctx(request).pool, pid, reviewer=principal.name, note=body.note)
        except KeyError as exc:
            raise HTTPException(404, "Proposal not found") from exc
        except review_service.ReviewInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except review_service.ReviewConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return schemas.DecisionResult(id=pid, status="rejected")

    @app.get("/api/products", response_model=list[schemas.ProductOut], tags=["catalog"])
    def products(
        request: Request, limit: int = Query(200, ge=1, le=500), _: Principal = Depends(current_principal)
    ) -> list[schemas.ProductOut]:
        with tx(_ctx(request).pool) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.gid, p.title, p.status, p.product_type, p.vendor, p.content_hash,
                  count(f.id) AS open_findings,
                  coalesce(array_agg(DISTINCT f.rule_id) FILTER (WHERE f.id IS NOT NULL), '{}') AS open_rules
                FROM products p LEFT JOIN findings f ON f.product_id = p.id AND f.status = 'open'
                WHERE p.deleted_at IS NULL GROUP BY p.id ORDER BY open_findings DESC, p.id LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [schemas.ProductOut(**r) for r in rows]

    # Admin ----------------------------------------------------------------------------------------
    @app.post("/api/admin/sync", status_code=202, tags=["admin"])
    def admin_sync(request: Request, _: Principal = Depends(require_admin)) -> dict[str, str]:
        jobs.enqueue(_ctx(request).pool, "sync", {})
        return {"status": "queued"}

    @app.post("/api/admin/runs", status_code=202, tags=["admin"])
    def admin_runs(request: Request, body: schemas.RunRequest, _: Principal = Depends(require_admin)) -> dict[str, int]:
        pool = _ctx(request).pool
        with tx(pool) as conn:
            if body.product_id is not None:
                ids = [body.product_id]
            else:
                ids = [
                    r["product_id"]
                    for r in conn.execute(
                        "SELECT DISTINCT f.product_id FROM findings f JOIN products pr ON pr.id = f.product_id "
                        "WHERE f.status = 'open' AND f.fixable AND pr.deleted_at IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM proposals p WHERE p.product_id = f.product_id "
                        "AND p.status IN ('pending_review','approved','applying')) "
                        # A product already tried at this content would just pay for the same outcome again.
                        "AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.product_id = f.product_id "
                        "AND r.base_content_hash = pr.content_hash) ORDER BY 1 LIMIT %s",
                        (body.limit,),
                    ).fetchall()
                ]
        for product_id in ids:
            jobs.enqueue(pool, "agent_run", {"product_id": product_id})
        return {"queued": len(ids)}

    @app.post("/api/admin/writeback", tags=["admin"])
    def admin_writeback(
        request: Request, body: schemas.WritebackRequest, principal: Principal = Depends(require_admin)
    ) -> dict[str, bool]:
        set_kill_switch(_ctx(request), enabled=body.enabled, actor=f"admin:{principal.name}")
        return {"enabled": body.enabled}

    @app.get("/api/ops", response_model=schemas.Ops, tags=["admin"])
    def ops(request: Request, _: Principal = Depends(current_principal)) -> schemas.Ops:
        with tx(_ctx(request).pool) as conn:
            row = conn.execute(
                """
                SELECT
                  (SELECT count(*) FROM jobs WHERE status = 'dead') AS dead_jobs,
                  (SELECT count(*) FROM proposals WHERE status = 'failed') AS failed_writes,
                  (SELECT count(*) FROM proposals WHERE status = 'applying'
                     AND updated_at < now() - interval '5 minutes') AS stuck_writes,
                  (SELECT count(*) FROM agent_runs WHERE status NOT IN ('finished','running')
                     AND started_at >= date_trunc('day', now())) AS failed_runs_today,
                  (SELECT count(*) FROM webhook_events WHERE status = 'failed'
                     AND received_at >= date_trunc('day', now())) AS webhooks_failed_today
                """
            ).fetchone()
        return schemas.Ops(**(row or {}))

    @app.get("/api/audit-log", response_model=list[schemas.AuditEntry], tags=["admin"])
    def audit_log(
        request: Request, limit: int = Query(100, ge=1, le=500), _: Principal = Depends(current_principal)
    ) -> list[schemas.AuditEntry]:
        with tx(_ctx(request).pool) as conn:
            rows = conn.execute(
                "SELECT id, at, actor, action, entity_type, entity_id, detail FROM audit_log ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [schemas.AuditEntry(**r) for r in rows]

    # Webhook --------------------------------------------------------------------------------------
    @app.post("/webhooks/shopify", tags=["webhooks"])
    async def webhook(request: Request) -> JSONResponse:
        secret = settings.shopify_webhook_secret
        if secret is None:
            return JSONResponse({"error": "webhook secret not configured"}, status_code=503)
        # Counted while reading, because a chunked request declares no length and must not fill memory unsigned.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
            return JSONResponse({"error": "too large"}, status_code=413)
        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > MAX_WEBHOOK_BYTES:
                return JSONResponse({"error": "too large"}, status_code=413)
            chunks.append(chunk)
        raw = b"".join(chunks)
        # Verified on raw bytes before any parsing. Failures store nothing.
        signature = request.headers.get("x-shopify-hmac-sha256")
        if not await run_in_threadpool(webhook_hmac.verify, secret.get_secret_value(), raw, signature):
            log.warning("webhook rejected: bad or missing signature")
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        webhook_id = request.headers.get("x-shopify-webhook-id")
        topic = request.headers.get("x-shopify-topic")
        if not webhook_id or not topic:
            return JSONResponse({"error": "missing webhook headers"}, status_code=400)
        shop_domain = request.headers.get("x-shopify-shop-domain", "")
        if shop_domain != settings.shopify_shop_domain:
            # Correctly signed, so this is a configuration fault, and it stops updates from arriving.
            log.error(
                "signed webhook for %r ignored; this deployment serves %r", shop_domain, settings.shopify_shop_domain
            )
            return JSONResponse({"status": "ignored"})
        outcome = await run_in_threadpool(
            webhook_service.record_event,
            _ctx(request).pool,
            webhook_id=webhook_id,
            event_id=request.headers.get("x-shopify-event-id"),
            shop_domain=shop_domain,
            topic=topic,
            raw_body=raw,
            triggered_at=request.headers.get("x-shopify-triggered-at"),
        )
        return JSONResponse({"status": outcome})

    # Demo -----------------------------------------------------------------------------------------
    if settings.demo_mode:

        @app.post("/demo/simulate-edit", tags=["demo"])
        def simulate_edit(
            request: Request, body: schemas.SimulateEditRequest, principal: Principal = Depends(current_principal)
        ) -> dict[str, str]:
            """Simulates a merchant edit, then sends a signed webhook through the real verification path."""
            import json
            import uuid

            import httpx

            ctx_ = _ctx(request)
            if not isinstance(ctx_.shopify, FixtureShopify):
                raise HTTPException(409, "simulate-edit only works with the fixture store")
            with tx(ctx_.pool) as conn:
                row = conn.execute("SELECT gid FROM products WHERE id = %s", (body.product_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Product not found")
            ctx_.shopify.external_edit(row["gid"], description_html=body.description_html)
            secret = settings.shopify_webhook_secret
            if secret is None:
                raise HTTPException(503, "SHOPIFY_WEBHOOK_SECRET is not set")
            raw = json.dumps({"admin_graphql_api_id": row["gid"]}).encode()
            headers = {
                "X-Shopify-Hmac-Sha256": webhook_hmac.sign(secret.get_secret_value(), raw),
                "X-Shopify-Webhook-Id": str(uuid.uuid4()),
                "X-Shopify-Topic": "products/update",
                "X-Shopify-Shop-Domain": settings.shopify_shop_domain,
                "Content-Type": "application/json",
            }
            # The port comes from the server's own socket, never from the client's Host header.
            port = request.scope["server"][1]
            httpx.post(
                f"http://127.0.0.1:{port}/webhooks/shopify", content=raw, headers=headers, timeout=5
            ).raise_for_status()
            return {"status": "edited and webhook delivered", "by": principal.name}

    # Built SPA ------------------------------------------------------------------------------------
    static = settings.static_dir
    if static and Path(static).is_dir():
        app.mount("/assets", StaticFiles(directory=Path(static) / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith(("api/", "webhooks/", "demo/")):
                raise HTTPException(404)
            # Two pages: the review app owns /app and everything under it, the landing page answers the rest.
            if is_review_app_path(f"/{path}"):
                return FileResponse(Path(static) / "app" / "index.html")
            return FileResponse(Path(static) / "index.html")

    return app


def _proposal_detail(ctx: AppContext, pid: str) -> schemas.ProposalDetail:
    with tx(ctx.pool) as conn:
        row = conn.execute(
            """
            SELECT p.*, pr.title AS product_title, pr.gid, pr.description_html, pr.seo_title, pr.seo_description,
                   ar.mode, ar.model, ar.provider, ar.flags AS run_flags, w.status AS writeback_status,
                   w.before_snapshot, w.user_errors
            FROM proposals p JOIN products pr ON pr.id = p.product_id
            LEFT JOIN agent_runs ar ON ar.id = p.run_id LEFT JOIN writebacks w ON w.proposal_id = p.id
            WHERE p.id = %s
            """,
            (pid,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Proposal not found")
        validation = conn.execute(
            "SELECT rule_id, kind, severity, passed, evidence FROM validation_results WHERE proposal_id = %s ORDER BY kind DESC, rule_id",
            (pid,),
        ).fetchall()
        findings = conn.execute(
            "SELECT rule_id, severity, fixable, subject, fields, evidence, status FROM findings WHERE product_id = %s ORDER BY status, rule_id",
            (row["product_id"],),
        ).fetchall()
        citations: list[schemas.Citation] = []
        for citation in row["citations"]:
            chunk = conn.execute("SELECT text FROM policy_chunks WHERE id = %s", (citation.get("chunk_id"),)).fetchone()
            citations.append(
                schemas.Citation(
                    doc_id=str(citation.get("doc_id", "")),
                    chunk_id=str(citation.get("chunk_id", "")),
                    quote=str(citation.get("quote", "")),
                    verified=bool(citation.get("verified")),
                    reason=citation.get("reason"),
                    chunk_text=chunk["text"] if chunk else None,
                )
            )
    before_source = row["before_snapshot"] or row
    diffs = [
        schemas.FieldDiff(field=name, before=str(before_source.get(name, "")), after=str(row["proposed"][name]))
        for name in ALLOWED_FIELDS
        if name in row["proposed"]
    ]
    return schemas.ProposalDetail(
        id=pid,
        status=row["status"],
        risk_score=row["risk_score"],
        risk_reasons=row["risk_reasons"],
        rationale=row["rationale"],
        product_id=row["product_id"],
        product_title=row["product_title"],
        product_gid=row["gid"],
        base_content_hash=row["base_content_hash"],
        diffs=diffs,
        validation=[
            schemas.ValidationRow(
                rule_id=v["rule_id"],
                name=VALIDATORS.get(v["rule_id"], {}).get("name") or JUDGE_NAMES.get(v["rule_id"], v["rule_id"]),
                kind=v["kind"],
                severity=v["severity"],
                passed=v["passed"],
                evidence=v["evidence"],
            )
            for v in validation
        ],
        citations=citations,
        findings=[
            schemas.FindingOut(
                rule_id=f["rule_id"],
                name=AUDIT_RULES[f["rule_id"]]["name"],
                severity=f["severity"],
                fixable=f["fixable"],
                subject=f["subject"],
                fields=f["fields"],
                evidence=f["evidence"],
                status=f["status"],
            )
            for f in findings
        ],
        review_hash=review_service.review_hash_for(row, row["gid"]) if row["status"] == "pending_review" else None,
        model_mode=row["mode"],
        model=row["model"],
        provider=row["provider"],
        run_flags=row["run_flags"] or [],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        applied_at=row["applied_at"],
        writeback_status=row["writeback_status"],
        judge_status=row["judge_status"],
        judge_discarded=row["judge_discarded"],
        writeback_errors=row["user_errors"] or [],
    )
