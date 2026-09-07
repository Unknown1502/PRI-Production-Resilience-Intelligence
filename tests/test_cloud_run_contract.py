"""The three things that must be true for `infra/deploy.sh` to succeed.

Each of these was a real deploy failure waiting to happen, and none of them is
visible from a passing unit-test suite — they only appear on Cloud Run, one at
a time, each costing a rebuild-and-redeploy cycle to discover.

They are pinned here so that a change to `config.py`, `runner.py` or the deploy
script cannot quietly put them back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from pri.config import Settings
from pri.events.runner import serve_health

_DEPLOY_SH = Path(__file__).parents[1] / "infra" / "deploy.sh"

#: The DSN Cloud Run actually hands the container: unix socket, empty host,
#: socket path in the query string, and no password anywhere in it.
_CLOUD_SQL_DSN = "postgresql+asyncpg://pri_user@/pri?host=/cloudsql/proj:us-central1:pri-db"


def settings_with(**env: str) -> Settings:
    """Build Settings from exactly this environment, ignoring any local .env."""
    values: dict[str, Any] = dict(env)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def env_vars_set_for(service: str) -> set[str]:
    """The environment variable names `deploy.sh` gives one Cloud Run service.

    Read out of the script rather than restated here, so this cannot pass while
    the thing it describes is broken.
    """
    lines = _DEPLOY_SH.read_text(encoding="utf-8").splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith(("gcloud run deploy ", "gcloud run jobs deploy "))
        # The invocation is continued, so the service name is followed by " \".
        and line.strip().rstrip("\\").strip().endswith(service)
    ]
    assert starts, f"{service} is not deployed by deploy.sh any more"

    names: set[str] = set()
    for start in starts:
        # A gcloud invocation is one logical line, continued with trailing "\".
        index = start
        while index < len(lines):
            names.update(re.findall(r"[\"^@,](?:\^@\^)?([A-Z][A-Z0-9_]+)=", lines[index]))
            if not lines[index].rstrip().endswith("\\"):
                break
            index += 1
    return names


class TestEveryServiceCanConstructItsSettings:
    """Blocker 4 — all three containers crashed before running a line of code.

    `Settings` had 28 required fields; `deploy.sh` set 11, 9 and 2 of them. Each
    service died at startup on a pydantic ValidationError listing twenty-odd
    missing variables — which in Cloud Run's log viewer looks like a broken
    image, not a configuration mistake.

    The fix was to default everything that is a deployment convention rather
    than a secret. This test is the guard: it reads the variable names straight
    out of `deploy.sh`, so a new required field fails here rather than on
    Cloud Run.
    """

    @pytest.mark.parametrize("service", ["pri-api", "pri-seed", "pri-consumer"])
    def test_the_service_boots_on_the_variables_the_deploy_gives_it(self, service: str) -> None:
        provided = env_vars_set_for(service)
        required = {
            field.alias or name.upper()
            for name, field in Settings.model_fields.items()
            if field.is_required()
        }
        assert required <= provided, (
            f"{service} would crash at startup: deploy.sh does not set "
            f"{sorted(required - provided)}"
        )

    def test_only_the_dsn_has_no_sensible_default(self) -> None:
        """Everything else is a convention, a secret that may be absent, or both."""
        required = {
            field.alias or name.upper()
            for name, field in Settings.model_fields.items()
            if field.is_required()
        }
        assert required == {"DATABASE_URL"}


class TestCloudSqlDsn:
    """Blocker 1 — the container could not construct its settings at all."""

    def test_the_unix_socket_dsn_is_accepted(self) -> None:
        """`PostgresDsn` rejects this shape as "empty host"; SQLAlchemy does not.

        Typing the field as `PostgresDsn` meant a ValidationError at import on
        Cloud Run — a crash before any connection was attempted, which reads in
        the logs as a broken image rather than a bad DSN.
        """
        settings = settings_with(DATABASE_URL=_CLOUD_SQL_DSN, DATABASE_PASSWORD="hunter2")
        assert settings.database_url == _CLOUD_SQL_DSN

    def test_a_malformed_url_still_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="not a valid connection URL"):
            settings_with(DATABASE_URL="://:::", DATABASE_PASSWORD="x")


class TestPasswordInjection:
    """Blocker 2 — the password was mounted and then never used."""

    def test_the_secret_is_folded_into_the_dsn(self) -> None:
        settings = settings_with(DATABASE_URL=_CLOUD_SQL_DSN, DATABASE_PASSWORD="hunter2")
        url = make_url(settings.database_dsn)
        assert url.password == "hunter2"
        assert url.username == "pri_user"
        assert url.database == "pri"
        assert dict(url.query)["host"] == "/cloudsql/proj:us-central1:pri-db", (
            "the socket path must survive the injection"
        )

    def test_a_password_with_url_metacharacters_survives(self) -> None:
        """`openssl rand -base64` output routinely contains / and +."""
        secret = "a/b+c=d@e:f?g#h"
        settings = settings_with(DATABASE_URL=_CLOUD_SQL_DSN, DATABASE_PASSWORD=secret)
        assert make_url(settings.database_dsn).password == secret

    def test_a_dsn_that_already_has_a_password_is_untouched(self) -> None:
        """Local development and CI keep the password in the URL."""
        local = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
        settings = settings_with(DATABASE_URL=local, DATABASE_PASSWORD="ignored")
        assert settings.database_dsn == local

    def test_the_deploy_script_ships_no_password_in_the_url(self) -> None:
        """The counterpart to the injection: deploy.sh must not put one there.

        It used to interpolate the literal string PASSWORD, which authenticated
        as the word "PASSWORD" and failed at the seed job.
        """
        script = _DEPLOY_SH.read_text(encoding="utf-8")
        db_url_line = next(line for line in script.splitlines() if line.startswith("DB_URL="))
        assert ":PASSWORD@" not in db_url_line, "placeholder password is back in the DSN"
        assert "${DB_USER}@/" in db_url_line, "the DSN must carry a user and no password"
        assert "DATABASE_PASSWORD=pri-db-password:latest" in script, (
            "the password must still be mounted from Secret Manager"
        )


class TestCloudSqlUnixSocket:
    """Blocker 6 — the DSN parsed, and then connected over TCP anyway.

    Everything above got the Cloud SQL DSN accepted and the password folded in.
    None of it made the connection work: the asyncpg dialect does not read
    `?host=/path` from the query string the way psycopg does. It forwards
    unknown query parameters as server settings and leaves the host unset, so
    asyncpg opened an SSL TCP connection to the default host and failed with
    `socket.gaierror: Temporary failure in name resolution` — an error that
    reads like DNS and mentions no socket anywhere.

    Found by the seed job failing on Cloud Run, twenty minutes into a deploy.
    """

    async def test_the_socket_path_reaches_asyncpg(self) -> None:
        """The assertion that matters: what asyncpg is actually handed."""
        from unittest.mock import patch

        from pri.persistence.database import build_engine

        captured: dict[str, Any] = {}

        async def fake_connect(*_args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)
            raise RuntimeError("intercepted")

        engine = build_engine(_CLOUD_SQL_DSN.replace("pri_user@", "pri_user:pw@"))
        with patch("asyncpg.connect", new=fake_connect), contextlib.suppress(Exception):
            async with engine.connect():
                pass

        assert captured.get("host") == "/cloudsql/proj:us-central1:pri-db", (
            f"asyncpg was not given the socket directory: {captured}"
        )

    def test_the_socket_path_is_removed_from_the_url(self) -> None:
        """Left in the query string it becomes a server setting and breaks."""
        from pri.persistence.database import build_engine

        engine = build_engine(_CLOUD_SQL_DSN)
        assert "host" not in engine.url.query

    def test_an_ordinary_tcp_dsn_is_untouched(self) -> None:
        from pri.persistence.database import build_engine

        url = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
        engine = build_engine(url)
        assert engine.url.host == "localhost"
        assert engine.url.port == 5432


class TestConsumerReadiness:
    """Blocker 3 — a Cloud Run service that never binds $PORT never goes ready."""

    class _FakeConsumer:
        handled = 7

    async def test_no_port_means_no_socket(self) -> None:
        """Locally the runner stays exactly the poll loop it was."""
        os.environ.pop("PORT", None)
        assert await serve_health(self._FakeConsumer()) is None  # type: ignore[arg-type]

    async def test_the_probe_is_answered_when_cloud_run_sets_port(self) -> None:
        os.environ["PORT"] = "0"  # let the OS choose, so the test cannot collide
        server = await serve_health(self._FakeConsumer())  # type: ignore[arg-type]
        assert server is not None
        try:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(-1), timeout=5)
            writer.close()

            head, _, body = raw.partition(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 200 OK")
            assert b"Content-Type: application/json" in head
            assert json.loads(body) == {
                "status": "ok",
                "component": "consumer",
                "handled": 7,
            }
        finally:
            server.close()
            await server.wait_closed()
            os.environ.pop("PORT", None)
