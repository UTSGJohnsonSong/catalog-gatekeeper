"""A store's life with the embedded app: connecting on first open, uninstalling, and erasing its data.

Shopify sends app/uninstalled when the merchant removes the app, which also revokes every token, and shop/redact 48
hours later, after which the app must hold no catalog data for that store. The app never reads customer data, so
the two customer privacy webhooks need only an answer.
https://shopify.dev/docs/apps/build/compliance/privacy-law-compliance
"""

from __future__ import annotations

import logging
from datetime import datetime

from psycopg import sql

from gatekeeper.context import AppContext
from gatekeeper.db import tx
from gatekeeper.services.catalog import write_audit
from gatekeeper.shopify.offline_tokens import (
    StoreAuth,
    TokenUnreadable,
    delete_grant,
    load_grant,
    lock_store_tokens,
    save_grant,
    utc_now,
)
from gatekeeper.shopify.session_token import SessionToken

ACTOR = "shopify-lifecycle"
log = logging.getLogger("gatekeeper.lifecycle")

# Rows that hold the store's catalog or the work done on it, children before parents. The audit log keeps its rows:
# the database refuses to change them, by trigger and by grant. They carry ids, statuses, counts and actor names,
# plus any note a reviewer typed when rejecting a fix (docs/adr/0013 lists that as a known limit).
_ERASE: tuple[tuple[str, str], ...] = (
    ("writebacks", "proposal_id IN (SELECT id FROM proposals WHERE shop_id = %s)"),
    ("reviews", "proposal_id IN (SELECT id FROM proposals WHERE shop_id = %s)"),
    ("validation_results", "proposal_id IN (SELECT id FROM proposals WHERE shop_id = %s)"),
    ("proposals", "shop_id = %s"),
    ("run_retrievals", "run_id IN (SELECT id FROM agent_runs WHERE shop_id = %s)"),
    ("llm_calls", "run_id IN (SELECT id FROM agent_runs WHERE shop_id = %s)"),
    ("agent_runs", "shop_id = %s"),
    ("findings", "shop_id = %s"),
    ("products", "shop_id = %s"),
)


def _auth(ctx: AppContext) -> StoreAuth:
    if ctx.store_auth is None:
        raise RuntimeError("store tokens are only kept in embedded mode (SHOPIFY_EMBEDDED=1)")
    return ctx.store_auth


def connect_store(ctx: AppContext, session: SessionToken) -> bool:
    """Makes sure a usable token is stored, exchanging the session token only when none is.

    Returns True when this call stored a new token, so the caller can queue a sync: on the first open, and on the
    first open after an uninstall, an expired or refused refresh token, or a new TOKEN_ENCRYPTION_KEY. Exchanging on
    every open would retire the refresh token the worker holds, so a stored token that can still be refreshed wins."""
    auth = _auth(ctx)
    # The configured store, which the token source reads with. verify() has checked that the session names it.
    domain = ctx.settings.shopify_shop_domain
    with tx(ctx.pool, ACTOR) as conn:
        lock_store_tokens(conn, ctx.shop_id)
        try:
            current = load_grant(conn, ctx.shop_id, domain, auth.cipher)
        except TokenUnreadable:
            current = None  # Exchanged again below; the new token overwrites the unreadable row.
        if current is not None and current.refresh_usable(utc_now()):
            return False
        grant = auth.endpoint.exchange(session.raw)
        save_grant(conn, ctx.shop_id, domain, auth.cipher, grant, exchanged=True)
        conn.execute(
            "UPDATE shops SET installed_at = coalesce(installed_at, now()), uninstalled_at = NULL, redacted_at = NULL "
            "WHERE id = %s",
            (ctx.shop_id,),
        )
        write_audit(
            conn,
            f"shopify:{session.user_id}",
            "shop.connected",
            "shop",
            str(ctx.shop_id),
            ctx.shop_id,
            {"scope": grant.scope},
        )
    return True


def mark_uninstalled(ctx: AppContext, *, triggered_at: datetime | None = None) -> None:
    """Shopify has already revoked the tokens. Forget ours, stop writing, and drop work that would need the store.

    A notice that arrives after the merchant reinstalled, which a sleeping free host makes likely, is about the
    earlier install: when it was triggered before the current token was exchanged, it changes nothing."""
    with tx(ctx.pool, ACTOR) as conn:
        lock_store_tokens(conn, ctx.shop_id)
        if triggered_at is not None:
            newer = conn.execute(
                "SELECT 1 FROM shop_credentials WHERE shop_id = %s AND exchanged_at > %s", (ctx.shop_id, triggered_at)
            ).fetchone()
            if newer:
                log.warning("app/uninstalled from %s ignored: the store reinstalled the app after it", triggered_at)
                write_audit(conn, ACTOR, "shop.uninstall_ignored", "shop", str(ctx.shop_id), ctx.shop_id, {})
                return
        removed = delete_grant(conn, ctx.shop_id)
        conn.execute("UPDATE shops SET uninstalled_at = now(), writeback_enabled = false WHERE id = %s", (ctx.shop_id,))
        dropped = conn.execute(
            "UPDATE jobs SET status = 'dead', last_error = 'the store uninstalled the app' "
            "WHERE status = 'queued' AND kind IN ('sync', 'agent_run')"
        ).rowcount
        write_audit(
            conn,
            ACTOR,
            "shop.uninstalled",
            "shop",
            str(ctx.shop_id),
            ctx.shop_id,
            {"token_removed": removed, "queued_jobs_dropped": dropped},
        )


def erase_shop_data(ctx: AppContext) -> dict[str, int]:
    """Deletes the store's catalog and everything derived from it, in one transaction."""
    counts: dict[str, int] = {}
    with tx(ctx.pool, ACTOR) as conn:
        lock_store_tokens(conn, ctx.shop_id)
        conn.execute(
            "UPDATE jobs SET status = 'dead', last_error = 'the store data was erased' WHERE status = 'queued'"
        )
        for table, condition in _ERASE:
            statement = sql.SQL("DELETE FROM {table} WHERE {condition}").format(
                table=sql.Identifier(table), condition=sql.SQL(condition)
            )
            counts[table] = conn.execute(statement, (ctx.shop_id,)).rowcount
        counts["webhook_events"] = conn.execute(
            "DELETE FROM webhook_events WHERE shop_domain = %s", (ctx.settings.shopify_shop_domain,)
        ).rowcount
        delete_grant(conn, ctx.shop_id)
        conn.execute("UPDATE shops SET redacted_at = now(), writeback_enabled = false WHERE id = %s", (ctx.shop_id,))
        write_audit(conn, ACTOR, "shop.redacted", "shop", str(ctx.shop_id), ctx.shop_id, {"rows_deleted": counts})
    return counts


def acknowledge_customer_request(ctx: AppContext, topic: str) -> None:
    """customers/data_request and customers/redact: the app holds no customer data, so there is nothing to send or
    delete. The answer is recorded; the request itself was not stored, because it names the customer."""
    with tx(ctx.pool, ACTOR) as conn:
        write_audit(
            conn,
            ACTOR,
            f"privacy.{topic.replace('/', '.')}",
            "shop",
            str(ctx.shop_id),
            ctx.shop_id,
            {"customer_data_held": False},
        )
