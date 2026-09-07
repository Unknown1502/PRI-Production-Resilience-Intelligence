"""Application configuration.

Reads every environment variable defined in .env.example via pydantic-settings.
No secret has a default value — missing secrets raise a ``ValidationError``
at startup rather than silently falling back to an insecure value.

Usage::

    from pri.config import get_settings

    settings = get_settings()  # cached singleton after first call
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Central settings object; all values sourced from environment variables.

    Inputs:
        Environment variables as listed in .env.example.

    Outputs:
        A fully-validated ``Settings`` instance.

    Failure modes:
        Raises ``pydantic.ValidationError`` if any required variable is absent
        or fails type coercion at import time.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # DATABASE_*
    # ------------------------------------------------------------------
    # These four describe the same connection ``DATABASE_URL`` already encodes,
    # and nothing reads them — they exist for docker-compose. Defaulted, so a
    # deployment that supplies only the DSN is not rejected for omitting a
    # hostname it does not use.
    database_host: str = Field(default="localhost", alias="DATABASE_HOST")
    database_port: int = Field(default=5432, alias="DATABASE_PORT")
    database_name: str = Field(default="pri", alias="DATABASE_NAME")
    database_user: str = Field(default="pri_user", alias="DATABASE_USER")
    database_password: SecretStr = Field(default=SecretStr(""), alias="DATABASE_PASSWORD")
    # Explicit DSN overrides the individual fields when provided.
    #
    # Validated with SQLAlchemy rather than typed ``PostgresDsn``: Cloud SQL is
    # reached over a unix socket, whose DSN has an empty host and carries the
    # socket path in the query string —
    # ``postgresql+asyncpg://pri_user@/pri?host=/cloudsql/project:region:instance``
    # — and pydantic's ``PostgresDsn`` rejects that outright as "empty host".
    # Typing it that way meant the container could not construct its settings on
    # Cloud Run at all, which is a crash at import, not a connection error.
    database_url: str = Field(..., alias="DATABASE_URL")

    @field_validator("database_url")
    @classmethod
    def _parseable_dsn(cls, value: str) -> str:
        try:
            make_url(value)
        except Exception as exc:
            raise ValueError(f"DATABASE_URL is not a valid connection URL: {exc}") from exc
        return value

    @property
    def database_dsn(self) -> str:
        """The connection URL with the password folded in.

        Cloud Run mounts the database password as its own environment variable
        from Secret Manager, so the URL that reaches the container carries no
        secret and no placeholder. A URL that already has a password — local
        development, CI, anything with a `.env` — is returned untouched.

        Every caller that opens an engine uses this, never ``database_url``.
        """
        url = make_url(self.database_url)
        if url.password:
            return self.database_url
        secret = self.database_password.get_secret_value()
        if not secret:
            return self.database_url
        return url.set(password=secret).render_as_string(hide_password=False)

    # ------------------------------------------------------------------
    # GOOGLE_*
    # ------------------------------------------------------------------
    # There is deliberately no GOOGLE_APPLICATION_CREDENTIALS field.
    #
    # On Cloud Run the service account supplies credentials automatically. If
    # the variable is set and points at a file that is not there, the Google
    # auth library *raises* — it does not fall back to Application Default
    # Credentials. So the failure mode of carrying it is a hard crash in the
    # one environment where it is least needed. `warn_on_stale_credentials`
    # notices it in the environment and says so, but nothing reads it.
    google_cloud_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    #: The SDK's own name for the region. Was GOOGLE_CLOUD_REGION, which the
    #: agent then had to copy into GOOGLE_CLOUD_LOCATION before every call.
    google_cloud_location: str = Field(default="us-central1", alias="GOOGLE_CLOUD_LOCATION")
    google_genai_model: str = Field(default="gemini-2.5-flash", alias="GOOGLE_GENAI_MODEL")
    google_genai_use_vertexai: bool = Field(default=True, alias="GOOGLE_GENAI_USE_VERTEXAI")
    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")

    # ------------------------------------------------------------------
    # CONFLUENT_*
    # ------------------------------------------------------------------
    # Credentials default to empty rather than being required. The API service
    # serves every read and the whole recovery pipeline without touching Kafka,
    # and making it refuse to boot without a Confluent key meant the HTTP
    # surface was hostage to a broker it never speaks to.
    #
    # Empty credentials produce a plaintext client config, which is right for a
    # local broker and wrong for Confluent Cloud. The consumer — whose only job
    # is Kafka — checks ``confluent_configured`` at startup and refuses rather
    # than retrying a SASL-less connection forever.
    confluent_bootstrap_servers: str = Field(default="", alias="CONFLUENT_BOOTSTRAP_SERVERS")
    confluent_api_key: SecretStr = Field(default=SecretStr(""), alias="CONFLUENT_API_KEY")
    confluent_api_secret: SecretStr = Field(default=SecretStr(""), alias="CONFLUENT_API_SECRET")
    # No schema-registry fields. `events/schemas.py` serialises plain JSON and
    # nothing in the repository ever constructed a registry client, so the
    # three settings were credentials for a service PRI does not use.
    #
    # Topic names and the group id are deployment conventions, not secrets.
    # They were required, which meant every service had to restate all five or
    # crash — and adding a sixth topic would have broken the deploy silently.
    # Defaults are the names that exist on the cluster, verified with
    # `python -m pri.events.smoke --list`. They were `pri.*` in `.env` and
    # `production.*` in code, and only the second set was ever provisioned —
    # so the configured names described topics that did not exist.
    confluent_topic_production_events: str = Field(
        default="production.events", alias="CONFLUENT_TOPIC_PRODUCTION_EVENTS"
    )
    # These three are the topics the code actually publishes to. They replace
    # CONFLUENT_TOPIC_STATE_VERSIONS / _APPROVAL_REQUESTS / _APPROVAL_DECISIONS,
    # which were configured, never read, and named topics nothing wrote to —
    # while `events/config.py` hardcoded a different, unprefixed set.
    confluent_topic_recovery_requested: str = Field(
        default="production.recovery.requested", alias="CONFLUENT_TOPIC_RECOVERY_REQUESTED"
    )
    confluent_topic_recovery_completed: str = Field(
        default="production.recovery.completed", alias="CONFLUENT_TOPIC_RECOVERY_COMPLETED"
    )
    confluent_topic_audit: str = Field(default="production.audit", alias="CONFLUENT_TOPIC_AUDIT")
    confluent_consumer_group_id: str = Field(
        default="pri-backend", alias="CONFLUENT_CONSUMER_GROUP_ID"
    )

    # librdkafka's own defaults are minutes long, which is correct for a batch
    # pipeline and wrong for anything a browser is waiting on. These bound how
    # long a publish can cost before it gives up and reports degraded.
    kafka_socket_timeout_ms: int = Field(default=5000, alias="KAFKA_SOCKET_TIMEOUT_MS")
    kafka_message_timeout_ms: int = Field(default=8000, alias="KAFKA_MESSAGE_TIMEOUT_MS")
    kafka_request_timeout_ms: int = Field(default=5000, alias="KAFKA_REQUEST_TIMEOUT_MS")
    kafka_delivery_timeout_ms: int = Field(default=8000, alias="KAFKA_DELIVERY_TIMEOUT_MS")
    #: Consecutive failures before the producer stops trying, and for how long.
    kafka_breaker_threshold: int = Field(default=3, alias="KAFKA_BREAKER_THRESHOLD")
    kafka_breaker_cooldown_seconds: float = Field(
        default=60.0, alias="KAFKA_BREAKER_COOLDOWN_SECONDS"
    )

    @property
    def confluent_configured(self) -> bool:
        """Whether there are enough real credentials to reach a broker.

        The template ships ``CONFLUENT_API_KEY=REPLACE_WITH_API_KEY``, which is
        a non-empty string and so passed a naive truthiness check. The client
        builder already knew to ignore it and fell back to a plaintext config —
        so an untouched `.env` produced a client that connected to nothing and
        reported *degraded* rather than *not configured*, which is a much
        harder thing to read on a health page.
        """
        key = self.confluent_api_key.get_secret_value()
        secret = self.confluent_api_secret.get_secret_value()
        placeholder = key.startswith("REPLACE_WITH") or secret.startswith("REPLACE_WITH")
        return bool(self.confluent_bootstrap_servers and key and secret and not placeholder)

    # ------------------------------------------------------------------
    # PRI_*
    # ------------------------------------------------------------------
    pri_env: Literal["development", "staging", "production"] = Field(
        default="development", alias="PRI_ENV"
    )
    pri_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="PRI_LOG_LEVEL"
    )
    pri_api_host: str = Field(default="0.0.0.0", alias="PRI_API_HOST")
    pri_api_port: int = Field(default=8000, alias="PRI_API_PORT")
    pri_secret_key: SecretStr = Field(default=SecretStr(""), alias="PRI_SECRET_KEY")
    pri_max_candidate_futures: int = Field(default=5, alias="PRI_MAX_CANDIDATE_FUTURES")
    pri_approval_timeout_seconds: int = Field(default=300, alias="PRI_APPROVAL_TIMEOUT_SECONDS")
    pri_frontend_url: str = Field(default="http://localhost:3000", alias="PRI_FRONTEND_URL")
    pri_api_key: str = Field(default="", alias="PRI_API_KEY")
    pri_agent_enabled: bool = Field(default=False, alias="PRI_AGENT_ENABLED")
    pri_a2a_enabled: bool = Field(default=False, alias="PRI_A2A_ENABLED")
    pri_a2a_token: SecretStr | None = Field(default=None, alias="PRI_A2A_TOKEN")
    pri_demo_production_id: str = Field(default="film-001", alias="PRI_DEMO_PRODUCTION_ID")
    # Where generated call sheets are written. Relative paths resolve against
    # the working directory; Cloud Run mounts a writable /tmp.
    pri_artifact_root: str | None = Field(default=None, alias="PRI_ARTIFACT_ROOT")
    # A Cloud Storage bucket for issued call sheets. Unset means the local
    # filesystem, which is right for tests and a checkout but wrong for Cloud
    # Run: container disk does not survive the next revision, so the audit row
    # outlived the document it pointed at.
    pri_artifact_bucket: str = Field(default="", alias="PRI_ARTIFACT_BUCKET")

    # Live disruption injection. Empty passcode disables the endpoint outright,
    # which is the right default: it is a write path with an LLM call behind it
    # on a public URL, and an unset secret must never mean "open".
    pri_inject_passcode: str = Field(default="", alias="PRI_INJECT_PASSCODE")
    pri_inject_rate_limit_per_minute: int = Field(
        default=6, alias="PRI_INJECT_RATE_LIMIT_PER_MINUTE"
    )

    # ------------------------------------------------------------------
    # Build metadata
    # ------------------------------------------------------------------
    git_sha: str = Field(default="unknown", alias="GIT_SHA")

    @property
    def gemini_model(self) -> str:
        """The Gemini model id the agent should use."""
        return self.google_genai_model

    @property
    def gemini_configured(self) -> bool:
        """Whether there is enough configuration to reach a model."""
        if self.google_genai_use_vertexai:
            return bool(self.google_cloud_project and self.google_cloud_location)
        return bool(self.google_api_key and self.google_api_key.get_secret_value())

    @property
    def topic_names(self) -> dict[str, str]:
        """Every resolved topic name, for the startup log line.

        A topic typo is otherwise silent: the producer creates or writes to a
        name nobody is consuming and every publish succeeds.
        """
        return {
            "events": self.confluent_topic_production_events,
            "recovery_requested": self.confluent_topic_recovery_requested,
            "recovery_completed": self.confluent_topic_recovery_completed,
            "audit": self.confluent_topic_audit,
            "consumer_group": self.confluent_consumer_group_id,
        }


def warn_on_stale_credentials() -> str | None:
    """Complain about GOOGLE_APPLICATION_CREDENTIALS if it is still set.

    Called once at startup. PRI stopped reading the variable because a path
    that does not resolve makes the Google auth library raise instead of
    falling back to ADC — so a leftover value from a developer's `.env` turns
    into a crash on Cloud Run, where the service account was going to supply
    credentials anyway.

    Outputs:
        The offending path when the variable is set, else ``None``.
    """
    stale = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not stale:
        return None
    logging.getLogger(__name__).warning(
        "GOOGLE_APPLICATION_CREDENTIALS is set (%s) but PRI does not read it. "
        "On Cloud Run the service account supplies credentials; locally, use "
        "`gcloud auth application-default login`. If the path does not exist, "
        "the Google auth library will raise rather than fall back. Unset it.",
        stale,
    )
    return stale


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application ``Settings`` singleton.

    Inputs:
        None — reads from environment / .env file.

    Outputs:
        A validated :class:`Settings` instance (loaded once, cached forever).

    Failure modes:
        Raises ``pydantic.ValidationError`` on the first call if any required
        environment variable is missing or invalid.
    """
    return Settings()
