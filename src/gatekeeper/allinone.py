"""One container for a free host: setup, then the API and the worker as child processes, plus demo resets.

DATABASE_URL here is the owner connection the host hands out. Setup and resets use it. The children get the same
server as the least-privilege app role, so the process facing the internet cannot truncate tables or rewrite the
audit log. If either child exits, the other is stopped and the container exits, so the platform restarts the set.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import PoolTimeout

from gatekeeper.config import get_settings
from gatekeeper.seed import DemoLeftEmpty, check_demo_database, load_store, prepare_database, reset_demo
from gatekeeper.worker import configure_logging

log = logging.getLogger("gatekeeper.allinone")
APP_ROLE = "gatekeeper_app"
STOP_GRACE_SECONDS = 10.0
# Setup needs these; the children never do, so they do not inherit them.
SETUP_ONLY = ("APP_DB_PASSWORD", "MCP_DB_PASSWORD")
# A reset that did not happen is tried again this soon, not a whole interval later.
RETRY_SECONDS = 60.0
# Failed resets in a row before the container gives up, so the platform restarts it and the boot reset reports why.
MAX_RESET_FAILURES = 5
# Skipped resets in a row, another process holding the lock, before that is logged as an error.
SKIPS_BEFORE_ERROR = 3
# Errors a later attempt can get past: a dropped connection, a lock that stayed busy, a reload that failed.
TRANSIENT_RESET_ERRORS: tuple[type[BaseException], ...] = (psycopg.OperationalError, PoolTimeout, DemoLeftEmpty)


class Child(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def app_database_url(owner_url: str, app_password: str | None) -> str:
    """The same server and database as the owner URL, signed in as the app role. Without a password, the owner."""
    if not app_password:
        return owner_url
    params = conninfo_to_dict(owner_url)
    params.update(user=APP_ROLE, password=app_password)
    return make_conninfo("", **params)


def child_env(base: Mapping[str, str], app_url: str) -> dict[str, str]:
    env = {name: value for name, value in base.items() if name not in SETUP_ONLY}
    env["DATABASE_URL"] = app_url
    return env


def child_commands(port: str) -> dict[str, list[str]]:
    return {
        "worker": [sys.executable, "-m", "gatekeeper.worker"],
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "gatekeeper.api.app:create_app",
            "--factory",
            "--host",
            "0.0.0.0",  # noqa: S104 - inside a container, the platform's proxy is the only thing that can reach this
            "--port",
            port,
            # The app reads X-Forwarded-For itself, by a hop count. Uvicorn's own handling, if widened by an
            # environment variable, would trust the leftmost entry, which the client writes.
            "--no-proxy-headers",
            "--no-server-header",
        ],
    }


def supervise(
    spawn: Callable[[str], Child],
    names: Sequence[str],
    stop: threading.Event,
    *,
    reset: Callable[[], bool] | None = None,
    reset_every_seconds: float = 0.0,
    first_reset_in: float | None = None,
    pause_for_reset: Sequence[str] = ("worker",),
    poll_seconds: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Starts the children and watches them until one exits or `stop` is set. Returns the container's exit code.

    Resets run on schedule with the worker stopped, so no job of the old demo runs against the new one. A reset that
    is skipped or fails transiently is tried again within a minute; one that cannot succeed ends the container."""
    children: dict[str, Child] = {}
    try:
        for name in names:
            children[name] = spawn(name)
        return _watch(
            children,
            spawn,
            stop,
            reset=reset,
            reset_every_seconds=reset_every_seconds,
            first_reset_in=first_reset_in,
            pause_for_reset=pause_for_reset,
            poll_seconds=poll_seconds,
            clock=clock,
        )
    finally:
        # Whatever ended the watch, including an error raised by it, no child is left running.
        for name, child in children.items():
            _stop(name, child)


def _watch(
    children: dict[str, Child],
    spawn: Callable[[str], Child],
    stop: threading.Event,
    *,
    reset: Callable[[], bool] | None,
    reset_every_seconds: float,
    first_reset_in: float | None,
    pause_for_reset: Sequence[str],
    poll_seconds: float,
    clock: Callable[[], float],
) -> int:
    next_reset = None
    if reset is not None and reset_every_seconds > 0:
        next_reset = clock() + (reset_every_seconds if first_reset_in is None else first_reset_in)
    failures = skips = 0
    while not stop.is_set():
        dead = [(name, rc) for name, child in children.items() if (rc := child.poll()) is not None]
        if dead:
            name, rc = dead[0]
            log.error("%s exited with %s; stopping the rest", name, rc)
            return rc or 1
        if reset is not None and next_reset is not None and clock() >= next_reset:
            for name in pause_for_reset:
                _stop(name, children[name])
            outcome: bool | None = None
            try:
                outcome = reset()
            except TRANSIENT_RESET_ERRORS:
                failures += 1
                log.exception("demo reset failed, %d in a row; the demo may stay empty until one succeeds", failures)
            except Exception:
                log.critical("demo reset cannot succeed; exiting so the restart reports it", exc_info=True)
                return 1
            if outcome:
                failures = skips = 0
            elif outcome is False:
                skips += 1
                level = logging.ERROR if skips >= SKIPS_BEFORE_ERROR else logging.WARNING
                log.log(level, "demo reset skipped %d time(s) in a row: another process holds its lock", skips)
            if failures >= MAX_RESET_FAILURES:
                log.critical("%d demo resets failed in a row; exiting so the platform restarts", failures)
                return 1
            if stop.is_set():
                return 0
            for name in pause_for_reset:
                try:
                    children[name] = spawn(name)
                except OSError:
                    log.critical("could not start %s again after a reset; exiting", name, exc_info=True)
                    return 1
            next_reset = clock() + (reset_every_seconds if outcome else RETRY_SECONDS)
        stop.wait(poll_seconds)
    return 0


def _stop(name: str, child: Child) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        log.warning("%s ignored SIGTERM for %.0f seconds; killing it", name, STOP_GRACE_SECONDS)
        child.kill()
        child.wait()


def main() -> None:
    configure_logging()
    # First, because as PID 1 in a container a signal without a handler is ignored, and setup can take a while.
    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        # Event.set and logging both take locks that the interrupted main thread may hold. A thread cannot deadlock.
        def note() -> None:
            log.info("signal %s received; stopping", signum)
            stop.set()

        threading.Thread(target=note, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    settings = get_settings()
    password = settings.app_db_password.get_secret_value() if settings.app_db_password else None
    first_reset_in = None
    if settings.public_demo:
        if not password:
            raise SystemExit("PUBLIC_DEMO needs APP_DB_PASSWORD: the API and worker must not run as the owner role")
        # Before setup, which migrates, resets role passwords and replaces policies in whatever database this is.
        with psycopg.connect(settings.database_url) as conn:
            check_demo_database(conn, settings)
    prepare_database(settings)
    if stop.is_set():
        return
    if settings.public_demo:
        # Each boot starts from the first state, so a free host that sleeps when idle greets the next visitor with a
        # clean demo. If that cannot happen now, the demo starts as it was left and the next reset comes soon.
        try:
            if not reset_demo(settings):
                load_store(settings)
                first_reset_in = RETRY_SECONDS
        except TRANSIENT_RESET_ERRORS:
            log.exception("demo reset at boot failed; starting from the current state and trying again soon")
            load_store(settings)
            first_reset_in = RETRY_SECONDS
    else:
        load_store(settings)
    if stop.is_set():
        return
    if not password:
        log.warning("APP_DB_PASSWORD is not set, so the API and worker run as the owner role")
    env = child_env(os.environ, app_database_url(settings.database_url, password))
    commands = child_commands(os.environ.get("PORT", "8000"))

    def spawn(name: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(commands[name], env=env)  # noqa: S603 - fixed argv

    sys.exit(
        supervise(
            spawn,
            list(commands),
            stop,
            reset=(lambda: reset_demo(settings)) if settings.public_demo else None,
            reset_every_seconds=settings.demo_reset_minutes * 60,
            first_reset_in=first_reset_in,
        )
    )


if __name__ == "__main__":
    main()
