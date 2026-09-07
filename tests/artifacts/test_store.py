"""Call sheets have to outlive the container that rendered them.

They were written to container-local disk. On Cloud Run that disk is gone at
the next revision, instance or restart, while the `artifacts` row recording the
issue survives — so the audit trail claimed a document had been published and
could not produce it. For a system whose whole argument is a defensible record,
that is the wrong half to lose.

These cover the seam that fixes it: which store gets chosen, that the local one
still behaves exactly as before, and that the bucket one uploads what it says
it uploads.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from pri.artifacts.store import (
    GcsArtifactStore,
    LocalArtifactStore,
    artifact_key,
    build_store,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeBlob:
    def __init__(self, name: str, sink: dict[str, Any]) -> None:
        self._name = name
        self._sink = sink

    def upload_from_string(self, payload: bytes, content_type: str) -> None:
        self._sink[self._name] = (payload, content_type)


class _FakeBucket:
    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._sink)


class TestTheStoreIsChosenByConfiguration:
    def test_no_bucket_means_the_filesystem(self, tmp_path: Path) -> None:
        assert isinstance(build_store("", tmp_path), LocalArtifactStore)
        assert isinstance(build_store(None, tmp_path), LocalArtifactStore)

    def test_a_bucket_means_cloud_storage(self, tmp_path: Path) -> None:
        assert isinstance(build_store("pri-artifacts", tmp_path), GcsArtifactStore)

    def test_choosing_the_bucket_store_does_not_reach_for_credentials(self) -> None:
        """Constructing must not authenticate.

        Settings are built at import time in places, and a store that opened a
        client in ``__init__`` would turn "the bucket is configured" into "the
        process cannot start without credentials" — including in tests.
        """
        store = GcsArtifactStore("pri-artifacts")
        assert store._bucket is None  # the property under test


class TestTheLocalStore:
    def test_it_writes_the_bytes_and_returns_the_path(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(tmp_path)
        locator = store.put(
            "film-001/v3/2026-09-10.pdf", b"%PDF-1.4", content_type="application/pdf"
        )

        written = tmp_path / "film-001" / "v3" / "2026-09-10.pdf"
        assert written.read_bytes() == b"%PDF-1.4"
        assert locator == str(written)

    def test_it_creates_the_version_directory(self, tmp_path: Path) -> None:
        LocalArtifactStore(tmp_path).put("a/v1/x.pdf", b"x", content_type="application/pdf")
        assert (tmp_path / "a" / "v1").is_dir()


class TestTheBucketStore:
    def test_it_uploads_under_a_prefix_and_returns_a_gs_uri(self) -> None:
        sink: dict[str, Any] = {}
        store = GcsArtifactStore("pri-artifacts")
        store._bucket = _FakeBucket(sink)  # stands in for the client

        locator = store.put(
            "film-001/v3/2026-09-10.pdf", b"%PDF-1.4", content_type="application/pdf"
        )

        assert locator == "gs://pri-artifacts/call-sheets/film-001/v3/2026-09-10.pdf"
        payload, content_type = sink["call-sheets/film-001/v3/2026-09-10.pdf"]
        assert payload == b"%PDF-1.4"
        # Set so a sheet opened from a link renders instead of downloading.
        assert content_type == "application/pdf"


class TestTheKey:
    def test_it_is_addressable_by_production_version_and_day(self) -> None:
        assert artifact_key("film-001", 3, date(2026, 9, 10)) == "film-001/v3/2026-09-10.pdf"

    def test_regenerating_one_version_overwrites_rather_than_accumulates(self) -> None:
        """The same day at the same version is the same document.

        Versions are append-only, so a new sheet gets a new key by construction;
        re-rendering an unchanged version should not leave two of them.
        """
        first = artifact_key("film-001", 3, date(2026, 9, 10))
        assert first == artifact_key("film-001", 3, date(2026, 9, 10))
        assert first != artifact_key("film-001", 4, date(2026, 9, 10))
