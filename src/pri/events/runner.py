"""``python -m pri.events.runner`` — the consumer service entrypoint.

Deployed to Cloud Run with min-instances 1, which makes it the only always-on
component and the only standing charge.  That is deliberate: a consumer that
scales to zero is a consumer that is not listening when the location manager
calls.

Shutdown is graceful.  ``SIGTERM`` — which is what Cloud Run sends before it
takes an instance away — asks the poll loop to finish the message it is holding,
commit that offset, flush the producer, and leave the consumer group cleanly. An
abrupt exit would leave the group waiting out a session timeout before
rebalancing.

A Cloud Run *service* must answer on ``$PORT`` before its revision is
considered ready, and a bare Kafka poll loop never binds a socket — the
revision fails to start and the deploy rolls back with a message about the
container not listening. So when ``PORT`` is present the runner also serves a
one-route health endpoint alongside the loop. Locally, where nothing sets
``PORT``, no socket is opened and the runner is exactly the poll loop it was.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from typing import TYPE_CHECKING

import structlog

from pri.api.main import configure_logging
from pri.config import get_settings
from pri.events.consumer import RecoveryConsumer, build_default_invoker
from pri.events.producer import ProductionEventProducer
from pri.persistence.database import SessionFactory, build_engine
from pri.persistence.repository import PriRepository

if TYPE_CHECKING:
    from pri.config import Settings

__all__ = ["main", "run_forever", "serve_health"]

_log = structlog.get_logger(__name__)


async def run_forever(settings: Settings | None = None) -> int:
    """Consume disruptions until a signal asks us to stop.

    Inputs:
        settings: Override the loaded settings.

    Outputs:
        The number of messages handled before shutdown.

    Failure modes:
        Propagates any handler exception after the consumer has logged it and
        declined to commit the offset — the process dies, Cloud Run restarts
        it, and the message is redelivered. Restarting on a poisoned message is
        preferable to skipping it.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved.pri_log_level)

    # Kafka is the entire job of this process. Starting without credentials
    # gives librdkafka a plaintext config against a TLS+SASL broker, which
    # fails as an unauthenticated-connection loop in the logs rather than as
    # "you did not set CONFLUENT_API_KEY".
    if not resolved.confluent_configured:
        missing = [
            name
            for name, present in (
                ("CONFLUENT_BOOTSTRAP_SERVERS", resolved.confluent_bootstrap_servers),
                ("CONFLUENT_API_KEY", resolved.confluent_api_key.get_secret_value()),
                ("CONFLUENT_API_SECRET", resolved.confluent_api_secret.get_secret_value()),
            )
            if not present
        ]
        raise RuntimeError(
            "The consumer cannot start without Confluent credentials. "
            f"Not set: {', '.join(missing)}."
        )

    engine = build_engine(resolved.database_dsn)
    repo = PriRepository(SessionFactory(engine))

    producer = ProductionEventProducer(resolved)
    consumer = RecoveryConsumer(resolved, repo, producer, build_default_invoker(resolved))

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError):
            # Windows' proactor loop has no signal handler support; the
            # KeyboardInterrupt path below covers local development there.
            loop.add_signal_handler(sig, consumer.stop)

    consumer.subscribe()
    health = await serve_health(consumer)
    _log.info(
        "consumer_started",
        group="pri-recovery",
        bootstrap=resolved.confluent_bootstrap_servers,
        health_port=os.environ.get("PORT"),
    )

    try:
        return await consumer.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        consumer.stop()
        return consumer.handled
    finally:
        if health is not None:
            health.close()
            await health.wait_closed()
        consumer.close()
        producer.flush()
        await engine.dispose()
        _log.info("consumer_stopped")


async def serve_health(consumer: RecoveryConsumer) -> asyncio.Server | None:
    """Answer Cloud Run's readiness probe on ``$PORT``.

    Returns ``None`` when ``PORT`` is unset, which is how the runner tells a
    local shell from a Cloud Run container. Raw asyncio rather than a
    framework: the consumer must not compete with an ASGI server for the event
    loop, and one fixed response needs no routing.

    The body reports the messages handled so far, so a ``curl`` against the
    service is also the evidence that the consumer is alive and working.
    """
    port = os.environ.get("PORT")
    if not port:
        return None

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # The request has to be consumed even though the answer never varies:
        # closing a socket that still holds unread inbound bytes is an abortive
        # close, and the probe sees a connection reset instead of the 200.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)

        with contextlib.suppress(Exception):
            body = json.dumps(
                {"status": "ok", "component": "consumer", "handled": consumer.handled}
            ).encode()
            head = "\r\n".join(
                [
                    "HTTP/1.1 200 OK",
                    "Content-Type: application/json",
                    f"Content-Length: {len(body)}",
                    "Connection: close",
                    "",
                    "",
                ]
            )
            writer.write(head.encode("ascii") + body)
            await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    # 0.0.0.0 is required: Cloud Run probes the container from outside it.
    return await asyncio.start_server(respond, host="0.0.0.0", port=int(port))


def main() -> None:
    """Console entrypoint."""
    handled = asyncio.run(run_forever())
    print(f"Consumer stopped after handling {handled} message(s).")


if __name__ == "__main__":
    main()
