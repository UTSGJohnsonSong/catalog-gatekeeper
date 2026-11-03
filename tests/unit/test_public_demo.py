"""The pieces of a public demo that need no database: config guards, tokens, limits, and the process supervisor."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import threading
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from pydantic import ValidationError

from gatekeeper import allinone
from gatekeeper.allinone import (
    MAX_RESET_FAILURES,
    RETRY_SECONDS,
    app_database_url,
    child_commands,
    child_env,
    supervise,
)
from gatekeeper.api.auth import _sha256, demo_token, load_tokens
from gatekeeper.api.limits import BodySizeLimit, RateLimiter, bucket_key, client_ip
from gatekeeper.config import Settings
from gatekeeper.seed import DemoLeftEmpty, NotTheDemoDatabase
from gatekeeper.shopify.live import ClientCredentialsToken


def public(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "demo_mode": True,
        "public_demo": True,
        "trusted_proxy_hops": 1,
        "demo_reset_minutes": 60,
        "shopify_webhook_secret": "generated-by-the-host",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class TestPublicDemoSettings:
    def test_safe_configuration_boots(self):
        assert public().public_demo

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"demo_mode": False}, "needs DEMO_MODE"),
            ({"anthropic_api_key": "sk-anything"}, "must not hold a model key"),
            ({"llm_provider": "anthropic"}, "must not hold a model key"),
            ({"shopify_access_token": "shpat_x"}, "must not hold store credentials"),
            ({"shopify_client_id": "c", "shopify_client_secret": "s"}, "must not hold store credentials"),
            ({"demo_reset_minutes": 0}, "needs DEMO_RESET_MINUTES of at least 5"),
            ({"demo_reset_minutes": 4}, "needs DEMO_RESET_MINUTES of at least 5"),
        ],
    )
    def test_unsafe_configurations_are_refused(self, overrides, message):
        with pytest.raises(ValidationError, match=message):
            public(**overrides)

    def test_live_store_is_refused(self):
        with pytest.raises(ValidationError, match="only runs on the fixture store"):
            public(shopify_adapter="live", shopify_shop_domain="x.myshopify.com", demo_mode=True)

    def test_the_proxy_hop_count_must_be_chosen_not_defaulted(self):
        values = {"demo_mode": True, "public_demo": True, "shopify_webhook_secret": "w", "demo_reset_minutes": 60}
        with pytest.raises(ValidationError, match="TRUSTED_PROXY_HOPS set explicitly"):
            Settings(**values)  # type: ignore[arg-type]
        assert Settings(**values, trusted_proxy_hops=0).trusted_proxy_hops == 0  # type: ignore[arg-type]

    def test_the_simulated_edit_needs_a_webhook_secret(self):
        with pytest.raises(ValidationError, match="needs SHOPIFY_WEBHOOK_SECRET"):
            public(shopify_webhook_secret=" ")

    def test_private_demos_may_reset_never_but_not_every_minute(self):
        assert Settings(demo_reset_minutes=0).demo_reset_minutes == 0
        with pytest.raises(ValidationError, match="0 \\(never\\) or at least 5"):
            Settings(demo_reset_minutes=2)

    @pytest.mark.parametrize("domain", ["store.example.com", "x.myshopify.com.evil.com", "x.myshopify.com@evil.com"])
    def test_the_live_adapter_only_talks_to_a_myshopify_domain(self, domain):
        with pytest.raises(ValidationError, match="myshopify.com domain"):
            Settings(shopify_adapter="live", shopify_shop_domain=domain, shopify_access_token="t")
        with pytest.raises(ValueError, match="myshopify.com"):
            ClientCredentialsToken(shop_domain=domain, client_id="c", client_secret="s")

    def test_the_template_model_never_writes_to_a_live_store_even_when_chosen_by_auto(self):
        live = {"shopify_adapter": "live", "shopify_shop_domain": "x.myshopify.com", "shopify_access_token": "t"}
        with pytest.raises(ValidationError, match="template model cannot write"):
            Settings(**live, writeback_enabled=True)  # type: ignore[arg-type]
        assert Settings(**live, writeback_enabled=False).shopify_adapter == "live"  # type: ignore[arg-type]
        assert Settings(**live, writeback_enabled=True, anthropic_api_key="k").writeback_enabled  # type: ignore[arg-type]

    def test_refusals_never_echo_secret_values(self):
        with pytest.raises(ValidationError) as caught:
            public(anthropic_api_key="sk-very-secret-value")
        assert "sk-very-secret-value" not in str(caught.value)


class TestTokens:
    def test_public_demo_accepts_the_reviewer_token_and_never_the_admin_one(self):
        hashes = {stored: (name, role) for stored, name, role in load_tokens(public())}
        assert hashes == {_sha256("demo-reviewer"): ("demo_reviewer", "reviewer")}
        assert demo_token(public()) == "demo-reviewer"

    def test_public_demo_keeps_configured_tokens_next_to_the_demo_reviewer(self):
        owner = "f" * 64
        tokens = load_tokens(public(reviewer_tokens=f"owner:admin:{owner}"))
        assert {(name, role) for _, name, role in tokens} == {("owner", "admin"), ("demo_reviewer", "reviewer")}
        assert _sha256("demo-admin") not in {stored for stored, _, _ in tokens}

    def test_private_demo_still_offers_the_admin_token(self):
        settings = Settings(demo_mode=True)
        assert demo_token(settings) == "demo-admin"
        assert len(load_tokens(settings)) == 2

    def test_no_demo_token_once_real_tokens_are_configured(self):
        assert demo_token(Settings(demo_mode=True, reviewer_tokens="a:admin:" + "0" * 64)) is None
        assert demo_token(Settings()) is None


class TestClientIp:
    def test_without_trusted_proxies_the_socket_peer_counts(self):
        assert client_ip("10.0.0.5", "1.2.3.4", 0) == "10.0.0.5"

    def test_takes_the_entry_written_by_the_trusted_proxy(self):
        # The client wrote "6.6.6.6" itself; the proxy appended the address it really saw.
        assert client_ip("10.0.0.5", "6.6.6.6, 203.0.113.9", 1) == "203.0.113.9"
        assert client_ip("10.0.0.5", "6.6.6.6, 203.0.113.9, 10.1.1.1", 2) == "203.0.113.9"

    def test_a_chain_shorter_than_the_hop_count_falls_back_to_the_peer(self):
        # Trusting the leftmost entry here would let the client choose its own bucket.
        assert client_ip("10.0.0.5", "203.0.113.9", 3) == "10.0.0.5"
        assert client_ip("10.0.0.5", None, 1) == "10.0.0.5"
        assert client_ip(None, " , ", 1) == "unknown"
        assert client_ip("10.0.0.5", "2001:db8::1", 1) == "2001:db8::1"

    def test_one_bucket_per_ipv6_64_so_a_host_cannot_rotate_addresses(self):
        assert bucket_key("2001:db8:1:2:aaaa::1") == bucket_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
        assert bucket_key("2001:db8:1:3::1") != bucket_key("2001:db8:1:2::1")
        assert bucket_key("::ffff:203.0.113.9") == "203.0.113.9"
        assert bucket_key("203.0.113.9") == "203.0.113.9"
        assert bucket_key("unknown") == "unknown"

    def test_a_port_added_by_a_proxy_does_not_make_a_new_bucket_per_connection(self):
        assert bucket_key("203.0.113.9:54321") == bucket_key("203.0.113.9:40000") == "203.0.113.9"
        assert bucket_key("[2001:db8:1:2::7]:443") == "2001:db8:1:2::/64"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestRateLimiter:
    def test_allows_a_burst_then_refills_at_the_set_rate(self):
        clock = Clock()
        limiter = RateLimiter(3, clock=clock)
        assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
        assert limiter.allow("b")
        clock.now += 20  # 3 per minute is one every 20 seconds.
        assert limiter.allow("a")
        assert not limiter.allow("a")

    def test_memory_is_bounded(self):
        limiter = RateLimiter(1, max_keys=3, clock=Clock())
        for key in "abcde":
            limiter.allow(key)
        assert len(limiter._buckets) == 3
        assert limiter.allow("a")  # Evicted, so it starts again with a full bucket.

    def test_retry_after_is_whole_seconds(self):
        assert RateLimiter(20).retry_after() == 3
        assert RateLimiter(300).retry_after() == 1


def drive(middleware: BodySizeLimit, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs one POST through the middleware with the given body messages; returns what it sent."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/x", "headers": []}
    asyncio.run(middleware(scope, receive, send))  # type: ignore[arg-type]
    return sent


class TestBodySizeLimit:
    def downstream(self) -> tuple[BodySizeLimit, list[list[dict[str, Any]]]]:
        seen: list[list[dict[str, Any]]] = []

        async def app(scope: Any, receive: Any, send: Any) -> None:
            seen.append([await receive(), await receive()])

        return BodySizeLimit(app, max_bytes=1024), seen

    def test_a_body_split_into_messages_is_counted_as_a_whole(self):
        middleware, seen = self.downstream()
        chunk = {"type": "http.request", "body": b"x" * 600, "more_body": True}
        sent = drive(middleware, [dict(chunk), dict(chunk), dict(chunk)])
        assert sent[0]["status"] == 413 and seen == []

    def test_a_body_under_the_cap_reaches_the_app_in_one_piece(self):
        middleware, seen = self.downstream()
        sent = drive(
            middleware,
            [
                {"type": "http.request", "body": b"a" * 400, "more_body": True},
                {"type": "http.request", "body": b"b" * 400, "more_body": False},
            ],
        )
        assert sent == []
        first, after = seen[0]
        assert first == {"type": "http.request", "body": b"a" * 400 + b"b" * 400, "more_body": False}
        assert after == {"type": "http.disconnect"}  # Later receives pass through to the server.

    def test_a_client_that_hangs_up_mid_body_gets_an_answer_not_a_server_error(self):
        middleware, seen = self.downstream()
        sent = drive(middleware, [{"type": "http.request", "body": b"{", "more_body": True}])
        assert sent[0]["status"] == 400 and seen == []


class TestAppDatabaseUrl:
    def test_swaps_in_the_app_role_and_keeps_everything_else(self):
        derived = conninfo_to_dict(
            app_database_url("postgresql://owner:ownerpw@ep-x.aws.neon.tech/neondb?sslmode=require", "a/b+c=d e'f")
        )
        assert derived == {
            "user": "gatekeeper_app",
            "password": "a/b+c=d e'f",
            "host": "ep-x.aws.neon.tech",
            "dbname": "neondb",
            "sslmode": "require",
        }

    def test_without_a_password_the_owner_url_is_used(self):
        assert app_database_url("postgresql://owner:pw@db/x", None) == "postgresql://owner:pw@db/x"

    def test_children_lose_the_setup_secrets_and_get_the_app_url(self):
        env = child_env(
            {"APP_DB_PASSWORD": "a", "MCP_DB_PASSWORD": "m", "PATH": "/bin", "DATABASE_URL": "owner"}, "app"
        )
        assert env == {"PATH": "/bin", "DATABASE_URL": "app"}

    def test_the_api_ignores_proxy_headers_and_no_mcp_server_starts(self):
        commands = child_commands("10000")
        assert set(commands) == {"api", "worker"}
        assert {"--no-proxy-headers", "--port", "10000"} <= set(commands["api"])
        assert not any("mcp" in part or "8808" in part for argv in commands.values() for part in argv)


class FakeChild:
    def __init__(self, *, ignores_terminate: bool = False) -> None:
        self.code: int | None = None
        self.terminated = self.killed = False
        self.ignores_terminate = ignores_terminate

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        if not self.ignores_terminate:
            self.code = -15

    def kill(self) -> None:
        self.killed, self.code = True, -9

    def wait(self, timeout: float | None = None) -> int:
        if self.code is None:
            raise subprocess.TimeoutExpired("child", timeout or 0)
        return self.code


class Spawner:
    def __init__(self) -> None:
        self.started: list[tuple[str, FakeChild]] = []

    def __call__(self, name: str) -> FakeChild:
        child = FakeChild()
        self.started.append((name, child))
        return child

    def of(self, name: str) -> list[FakeChild]:
        return [child for started, child in self.started if started == name]


def ticking(stop: threading.Event, clock: Clock, seconds: float = 30) -> None:
    """Each pass of the watch loop advances the fake clock instead of sleeping."""

    def tick(_: float) -> bool:
        clock.now += seconds
        return stop.is_set()

    stop.wait = tick  # type: ignore[method-assign]


class TestSupervise:
    def test_a_dead_child_stops_the_other_and_fails_the_container(self):
        spawn = Spawner()

        def worker_exits_cleanly(name: str) -> FakeChild:
            child = spawn(name)
            if name == "worker":
                child.code = 0  # Even a clean exit is a failure: both children are meant to run forever.
            return child

        assert supervise(worker_exits_cleanly, ["api", "worker"], threading.Event()) == 1
        assert spawn.of("api")[0].terminated

    def test_a_stop_signal_ends_cleanly(self):
        spawn, stop = Spawner(), threading.Event()
        stop.set()
        assert supervise(spawn, ["api", "worker"], stop) == 0
        assert all(child.terminated for _, child in spawn.started)

    def test_a_child_that_ignores_sigterm_is_killed(self, caplog):
        stop = threading.Event()
        stop.set()
        stubborn = FakeChild(ignores_terminate=True)
        with caplog.at_level(logging.WARNING, logger="gatekeeper.allinone"):
            supervise(lambda name: stubborn, ["api"], stop, pause_for_reset=())
        assert stubborn.killed
        assert "ignored SIGTERM" in caplog.text

    def test_resets_pause_the_worker_and_keep_their_schedule(self):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock)
        calls: list[float] = []

        def reset() -> bool:
            calls.append(clock.now)
            assert spawn.of("worker")[-1].terminated, "the worker must be stopped while the demo resets"
            if len(calls) == 2:
                stop.set()
            return True

        assert supervise(spawn, ["api", "worker"], stop, reset=reset, reset_every_seconds=60, clock=clock) == 0
        assert calls == [60.0, 120.0]
        # Started, restarted after the first reset; the stop during the second means no third start.
        assert len(spawn.of("worker")) == 2 and len(spawn.of("api")) == 1
        assert all(child.terminated for _, child in spawn.started)

    def test_a_worker_that_cannot_start_again_ends_the_container_and_stops_the_api(self):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock)

        def spawn_once_per_name(name: str) -> FakeChild:
            if spawn.of(name):
                raise OSError(12, "Cannot allocate memory")
            return spawn(name)

        code = supervise(
            spawn_once_per_name, ["api", "worker"], stop, reset=lambda: True, reset_every_seconds=60, clock=clock
        )
        assert code == 1
        assert spawn.of("api")[0].terminated

    def test_a_transient_failure_is_retried_within_a_minute_and_repeated_ones_end_the_container(self):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock, seconds=RETRY_SECONDS)
        calls: list[float] = []

        def reset() -> bool:
            calls.append(clock.now)
            raise DemoLeftEmpty("reload failed")

        code = supervise(spawn, ["api", "worker"], stop, reset=reset, reset_every_seconds=3600, clock=clock)
        assert code == 1
        assert len(calls) == MAX_RESET_FAILURES
        assert calls[1] - calls[0] == RETRY_SECONDS

    def test_a_reset_that_cannot_succeed_ends_the_container_at_once(self):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock)

        def reset() -> bool:
            raise NotTheDemoDatabase("another shop")

        assert supervise(spawn, ["api", "worker"], stop, reset=reset, reset_every_seconds=60, clock=clock) == 1
        assert all(child.terminated for _, child in spawn.started)

    def test_skipped_resets_are_retried_soon_and_become_an_error_when_they_repeat(self, caplog):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock, seconds=RETRY_SECONDS)
        calls: list[float] = []

        def reset() -> bool:
            calls.append(clock.now)
            if len(calls) == 3:
                stop.set()
            return False

        with caplog.at_level(logging.WARNING, logger="gatekeeper.allinone"):
            supervise(spawn, ["api", "worker"], stop, reset=reset, reset_every_seconds=3600, clock=clock)
        assert calls == [3600.0, 3600.0 + RETRY_SECONDS, 3600.0 + 2 * RETRY_SECONDS]
        assert [r.levelno for r in caplog.records if "skipped" in r.message] == [
            logging.WARNING,
            logging.WARNING,
            logging.ERROR,
        ]

    def test_the_first_reset_can_come_early(self):
        clock, spawn, stop = Clock(), Spawner(), threading.Event()
        ticking(stop, clock, seconds=60)
        calls: list[float] = []

        def reset() -> bool:
            calls.append(clock.now)
            stop.set()
            return True

        supervise(spawn, ["api", "worker"], stop, reset=reset, reset_every_seconds=3600, first_reset_in=60, clock=clock)
        assert calls == [60.0]


class TestBoot:
    """The order `main` runs things in decides whether a wrong DATABASE_URL can hurt a real database."""

    @pytest.fixture
    def boot(self, monkeypatch):
        events: list[str] = []
        record: dict[str, Any] = {}
        settings = public(
            app_db_password="app-password-long-enough",
            database_url="postgresql://owner:ownerpw@db.example/demo?sslmode=require",
        )
        monkeypatch.setattr(allinone, "configure_logging", lambda: None)
        monkeypatch.setattr(allinone.signal, "signal", lambda *args: None)
        monkeypatch.setattr(allinone, "get_settings", lambda: settings)
        monkeypatch.setattr(allinone.psycopg, "connect", lambda *a, **k: contextlib.nullcontext(None))
        monkeypatch.setattr(allinone, "check_demo_database", lambda conn, s: events.append("check"))
        monkeypatch.setattr(allinone, "prepare_database", lambda s: events.append("prepare"))
        monkeypatch.setattr(allinone, "load_store", lambda s: events.append("load"))

        def popen(argv: list[str], env: dict[str, str]) -> FakeChild:
            record.setdefault("envs", []).append(env)
            return FakeChild()

        def fake_supervise(spawn: Any, names: list[str], stop: Any, **kwargs: Any) -> int:
            events.append("supervise")
            record["kwargs"] = kwargs
            for name in names:
                spawn(name)
            return 0

        monkeypatch.setattr(allinone.subprocess, "Popen", popen)
        monkeypatch.setattr(allinone, "supervise", fake_supervise)
        return settings, events, record, monkeypatch

    def run_main(self, outcome: Any, boot: Any) -> None:
        _, events, _, monkeypatch = boot

        def reset(settings: Settings) -> bool:
            events.append("reset")
            if isinstance(outcome, BaseException):
                raise outcome
            return bool(outcome)

        monkeypatch.setattr(allinone, "reset_demo", reset)
        with pytest.raises(SystemExit) as exited:
            allinone.main()
        assert exited.value.code == 0

    def test_the_database_is_checked_before_setup_touches_it(self, boot):
        settings, events, record, monkeypatch = boot

        def refuse(conn: Any, s: Settings) -> None:
            events.append("check")
            raise NotTheDemoDatabase("this database also serves real-store.myshopify.com")

        monkeypatch.setattr(allinone, "check_demo_database", refuse)
        with pytest.raises(NotTheDemoDatabase):
            allinone.main()
        assert events == ["check"]
        assert "envs" not in record

    def test_a_refusing_reset_stops_the_boot_before_any_child_starts(self, boot):
        _, events, record, _ = boot
        with pytest.raises(NotTheDemoDatabase):
            self.run_main(NotTheDemoDatabase("real model runs"), boot)
        assert events == ["check", "prepare", "reset"]
        assert "envs" not in record

    def test_a_clean_boot_hands_the_children_the_app_role_and_the_schedule(self, boot):
        settings, events, record, _ = boot
        self.run_main(True, boot)
        assert events == ["check", "prepare", "reset", "supervise"]
        assert record["kwargs"]["reset_every_seconds"] == 3600
        assert record["kwargs"]["first_reset_in"] is None
        for env in record["envs"]:
            assert conninfo_to_dict(env["DATABASE_URL"])["user"] == "gatekeeper_app"
            assert "APP_DB_PASSWORD" not in env

    @pytest.mark.parametrize("outcome", [False, psycopg.errors.LockNotAvailable("busy"), DemoLeftEmpty("reload")])
    def test_a_boot_reset_that_cannot_run_now_loads_the_demo_and_retries_soon(self, boot, outcome):
        _, events, record, _ = boot
        self.run_main(outcome, boot)
        assert events == ["check", "prepare", "reset", "load", "supervise"]
        assert record["kwargs"]["first_reset_in"] == RETRY_SECONDS

    def test_a_public_demo_refuses_to_run_its_children_as_the_owner(self, boot):
        settings, events, record, monkeypatch = boot
        monkeypatch.setattr(allinone, "get_settings", lambda: settings.model_copy(update={"app_db_password": None}))
        with pytest.raises(SystemExit, match="needs APP_DB_PASSWORD"):
            allinone.main()
        assert events == []
