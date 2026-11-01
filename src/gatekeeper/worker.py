"""Background worker. Exceptions become job retries; nothing is swallowed without a log line and a last_error."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from datetime import UTC, datetime, timedelta

from gatekeeper import jobs
from gatekeeper.agent.loop import DailyCapExceeded, run_agent
from gatekeeper.config import get_settings
from gatekeeper.context import AppContext, build_context
from gatekeeper.db import tx
from gatekeeper.llm.port import LLMError
from gatekeeper.services.sync import sync_catalog
from gatekeeper.services.webhooks import process_event
from gatekeeper.services.writeback import apply_proposal
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyError, ShopifyThrottled

log = logging.getLogger("gatekeeper.worker")
TICK_SECONDS = 60
RECONCILE_SECONDS = 3600


class JsonFormatter(logging.Formatter):
    """Structured logs. Tokens and product text are never passed to the logger."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "at": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("job_id", "kind", "proposal_id", "run_id", "webhook_id"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def run_job(ctx: AppContext, job: dict) -> None:
    kind, payload, job_id = job["kind"], job["payload"], job["id"]
    extra = {"job_id": job_id, "kind": kind}
    try:
        if kind == "apply_writeback":
            result = apply_proposal(ctx, payload["proposal_id"])
            log.info("apply finished: %s", result.status, extra={**extra, "proposal_id": payload["proposal_id"]})
            if result.status in ("deferred", "in_progress"):
                jobs.defer(ctx.pool, job_id, 60 if result.status == "deferred" else 10, result.detail)
                return
        elif kind == "agent_run":
            result_run = run_agent(ctx, payload["product_id"])
            log.info("agent run finished: %s", result_run.status, extra={**extra, "run_id": result_run.run_id})
        elif kind == "sync":
            since = datetime.fromisoformat(payload["updated_since"]) if payload.get("updated_since") else None
            log.info("sync finished: %s", sync_catalog(ctx, updated_since=since), extra=extra)
        elif kind == "process_webhook":
            process_event(ctx, payload["event_row_id"])
        else:
            raise ValueError(f"unknown job kind {kind}")
        jobs.complete(ctx.pool, job_id)
    except ShopifyThrottled as exc:
        log.warning("store throttled the request", extra=extra)
        jobs.defer(ctx.pool, job_id, max(1, int(exc.retry_after) + 1), "throttled")
    except ShopifyAuthError as exc:
        log.error("store rejected our credentials: %s", exc, extra=extra)
        jobs.fail(ctx.pool, job, str(exc), retryable=False)
    except (ShopifyError, ConnectionError) as exc:
        state = jobs.fail(ctx.pool, job, str(exc))
        log.warning("transient store error, job now %s: %s", state, exc, extra=extra)
    except DailyCapExceeded as exc:
        log.error("%s", exc, extra=extra)
        jobs.fail(ctx.pool, job, str(exc), retryable=False)
    except LLMError as exc:
        state = jobs.fail(ctx.pool, job, str(exc), retryable=exc.retryable)
        log.warning("model error, job now %s: %s", state, exc, extra=extra)
    except Exception as exc:  # noqa: BLE001 - the worker must outlive one bad job; it is recorded and logged
        log.exception("job failed", extra=extra)
        jobs.fail(ctx.pool, job, f"{type(exc).__name__}: {exc}")


def tick(ctx: AppContext, last_reconcile: float | None) -> float | None:
    with tx(ctx.pool) as conn:
        conn.execute(
            "INSERT INTO heartbeats (name, at) VALUES ('worker', now()) ON CONFLICT (name) DO UPDATE SET at = now()"
        )
    jobs.reap(ctx.pool)
    jobs.sweep_applying(ctx.pool)
    now = time.monotonic()
    if last_reconcile is None or now - last_reconcile >= RECONCILE_SECONDS:
        # Webhooks can be missed; an hourly incremental sync is the safety net.
        bucket = datetime.now(UTC).strftime("%Y%m%d%H")
        since = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        jobs.enqueue(ctx.pool, "sync", {"updated_since": since}, dedupe_key=f"reconcile:{bucket}")
        return now
    return last_reconcile


def main() -> None:
    configure_logging()
    settings = get_settings()
    ctx = build_context(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info("worker started: provider=%s mode=%s", ctx.provider.name, ctx.provider.mode)
    last_tick = 0.0
    last_reconcile: float | None = None
    while True:
        if time.monotonic() - last_tick >= min(TICK_SECONDS, 15):
            last_reconcile = tick(ctx, last_reconcile)
            last_tick = time.monotonic()
        job = jobs.claim(ctx.pool, worker_id)
        if job is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        run_job(ctx, job)


if __name__ == "__main__":
    main()
