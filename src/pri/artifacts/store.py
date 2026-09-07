"""Where a generated call sheet actually goes.

A call sheet is the one artefact PRI produces that leaves the system: it is the
document a crew is called to work by, and the audit log records that version N
of the schedule was issued as this file. Until now that file was written to
container-local disk, which on Cloud Run means it survives until the next
deploy, the next instance, or the next restart — whichever comes first. The
audit row kept pointing at a path that no longer existed, so the record said a
document had been issued and could not produce it.

The fix is a bucket. This module is the seam: `regenerate_all` writes through a
store rather than to a path, and the store is chosen by configuration.

    PRI_ARTIFACT_BUCKET set    ->  GcsArtifactStore, durable, addressable
    unset                      ->  LocalArtifactStore, unchanged behaviour

Local stays the default deliberately. Tests, the demo runner and anyone
checking the repo out for the first time should not need a cloud account to
render a PDF.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

__all__ = [
    "ArtifactStore",
    "GcsArtifactStore",
    "LocalArtifactStore",
    "build_store",
]


class ArtifactStore(Protocol):
    """Somewhere a rendered call sheet can be put and later found."""

    def put(self, relative_path: str, payload: bytes, *, content_type: str) -> str:
        """Store one artefact and return the locator recorded in the audit row.

        Inputs:
            relative_path: Stable key — ``<production>/v<version>/<date>.pdf``.
                           The same day of the same version overwrites, because
                           a regenerated sheet for an unchanged version is the
                           same document.
            payload:       The rendered bytes.
            content_type:  MIME type, for stores that carry metadata.

        Outputs:
            A locator: a filesystem path, or a ``gs://`` URI.

        Failure modes:
            Raises ``OSError`` or the client library's own error if the write
            fails. Callers do not swallow this: an artefact that was not
            written must not be recorded as issued.
        """
        ...


class LocalArtifactStore:
    """Writes to disk. The default, and what every test uses."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, relative_path: str, payload: bytes, *, content_type: str) -> str:
        del content_type  # A filesystem has nowhere to put it.
        target = self._root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return str(target)


class GcsArtifactStore:
    """Writes to a Cloud Storage bucket.

    The client is built on first use rather than in ``__init__`` so that
    constructing settings — which happens at import time in places — never
    reaches for credentials.
    """

    def __init__(self, bucket: str, *, prefix: str = "call-sheets") -> None:
        self._bucket_name = bucket
        self._prefix = prefix.strip("/")
        self._bucket: object | None = None

    def _resolve_bucket(self) -> object:
        if self._bucket is None:
            from google.cloud import storage  # Imported late: optional dependency.

            self._bucket = storage.Client().bucket(self._bucket_name)
        return self._bucket

    def put(self, relative_path: str, payload: bytes, *, content_type: str) -> str:
        key = f"{self._prefix}/{relative_path}" if self._prefix else relative_path
        blob = self._resolve_bucket().blob(key)  # type: ignore[attr-defined]
        blob.upload_from_string(payload, content_type=content_type)
        return f"gs://{self._bucket_name}/{key}"


def build_store(bucket: str | None, local_root: Path) -> ArtifactStore:
    """Pick a store from configuration.

    Inputs:
        bucket:     ``PRI_ARTIFACT_BUCKET``; empty or None means local.
        local_root: Where the local store writes.

    Outputs:
        The store `regenerate_all` should write through.
    """
    if bucket:
        return GcsArtifactStore(bucket)
    return LocalArtifactStore(local_root)


def artifact_key(production_id: str, version: int, day: date) -> str:
    """The stable key for one day's sheet at one version."""
    return f"{production_id}/v{version}/{day.isoformat()}.pdf"
