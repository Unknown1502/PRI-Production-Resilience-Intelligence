"""Serialization helpers: ``ProductionState`` <-> canonical JSON + SHA-256 digest.

Architecture law: the digest is computed deterministically here (Python, not
the LLM).  The snapshot format is the standard Pydantic v2 JSON export of
``ProductionState`` with keys sorted so the digest is stable across Python
dict-ordering variations.

Public surface::

    state_to_snapshot(state) -> tuple[str, str]   # (json_str, hex_digest)
    snapshot_to_state(json_str) -> ProductionState
"""

from __future__ import annotations

import hashlib
import json

from pri.domain.models import ProductionState


def state_to_snapshot(state: ProductionState) -> tuple[str, str]:
    """Serialize a ``ProductionState`` to a canonical JSON string and its SHA-256 digest.

    The JSON is produced by Pydantic's ``model_dump(mode="json")`` with
    ``round_trip=True`` so that all special types (``date``, ``datetime``,
    ``Decimal``) serialise in a lossless, round-trippable form.  Keys at every
    level are sorted to guarantee a stable byte sequence regardless of
    insertion order.

    Inputs:
        state: A fully populated :class:`ProductionState`.

    Outputs:
        A two-tuple ``(json_str, hex_digest)`` where:
        - ``json_str``   is the canonical UTF-8 JSON string (no trailing newline).
        - ``hex_digest`` is the lowercase hex SHA-256 of ``json_str.encode()``.

    Failure modes:
        Raises ``pydantic.ValidationError`` if ``state`` is somehow invalid
        (should not occur if the caller holds a frozen instance).
        Raises ``TypeError`` if any field is not JSON-serialisable (indicates a
        domain model bug — all field types must be Pydantic-serialisable).
    """
    raw: dict[str, object] = state.model_dump(mode="json", round_trip=True)
    json_str: str = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    digest: str = hashlib.sha256(json_str.encode()).hexdigest()
    return json_str, digest


def snapshot_to_state(json_str: str) -> ProductionState:
    """Deserialize a canonical JSON string back into a ``ProductionState``.

    Inputs:
        json_str: A JSON string previously produced by :func:`state_to_snapshot`.

    Outputs:
        A validated, frozen :class:`ProductionState`.

    Failure modes:
        Raises ``pydantic.ValidationError`` if the JSON does not conform to the
        ``ProductionState`` schema.
        Raises ``json.JSONDecodeError`` if ``json_str`` is not valid JSON.
    """
    raw: dict[str, object] = json.loads(json_str)
    return ProductionState.model_validate(raw)


#: Fields that describe *where a state sits in the version chain* rather than
#: *what the production looks like*.  Excluded from :func:`content_digest`.
_VERSION_METADATA_FIELDS = frozenset({"version", "parent_version", "event_id", "created_at"})


def content_digest(state: ProductionState) -> str:
    """Return a digest of a state's substance, ignoring its version metadata.

    Two candidate plans that arrive at the same schedule are the same plan even
    though they were built a microsecond apart and carry different
    ``created_at`` stamps.  Deduplication and "did this move actually change
    anything?" checks therefore need a digest over the production content
    alone: the production record, scenes, people, locations, equipment and
    schedule.

    This is deliberately *not* the digest stored in ``state_versions``.  That
    one covers the whole snapshot, version metadata included, because its job
    is to prove a stored row has not been tampered with.

    Inputs:
        state: The state to fingerprint.

    Outputs:
        Lowercase hex SHA-256 digest (64 characters).

    Failure modes:
        Raises ``TypeError`` if a field is not JSON-serialisable.
    """
    raw: dict[str, object] = state.model_dump(mode="json", round_trip=True)
    content = {k: v for k, v in raw.items() if k not in _VERSION_METADATA_FIELDS}
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_digest(json_str: str) -> str:
    """Return the SHA-256 hex digest of a canonical JSON string.

    This is a convenience function for callers that already hold the JSON and
    want to verify an on-disk or database digest without re-serialising.

    Inputs:
        json_str: UTF-8 JSON string.

    Outputs:
        Lowercase hex SHA-256 digest (64 characters).

    Failure modes:
        Does not raise; purely computational.
    """
    return hashlib.sha256(json_str.encode()).hexdigest()
