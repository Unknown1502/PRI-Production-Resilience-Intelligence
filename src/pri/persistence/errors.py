"""Typed exceptions raised by the persistence layer.

No application logic lives here — only exception definitions.
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for all PRI persistence errors."""


class StaleStateError(PersistenceError):
    """Raised when a ``commit_state`` write conflicts with the current head.

    This implements optimistic concurrency: the caller tried to commit a new
    version whose ``parent_version`` is not the current ``MAX(version)`` for
    that production.

    Inputs (constructor):
        production_id:    The production that was being updated.
        attempted_parent: The ``parent_version`` the caller assumed.
        actual_head:      The actual current head version in the database.

    Outputs:
        A ``StaleStateError`` with a descriptive ``str(error)`` message.
    """

    def __init__(
        self,
        production_id: str,
        attempted_parent: int,
        actual_head: int,
    ) -> None:
        self.production_id = production_id
        self.attempted_parent = attempted_parent
        self.actual_head = actual_head
        super().__init__(
            f"Stale state for production {production_id!r}: "
            f"attempted parent={attempted_parent}, actual head={actual_head}"
        )


class ProductionNotFoundError(PersistenceError):
    """Raised when a production_id has no corresponding row.

    Inputs:
        production_id: The identifier that was not found.
    """

    def __init__(self, production_id: str) -> None:
        self.production_id = production_id
        super().__init__(f"Production not found: {production_id!r}")


class StateVersionNotFoundError(PersistenceError):
    """Raised when a specific (production_id, version) pair does not exist.

    Inputs:
        production_id: The production identifier.
        version:       The version number that was not found.
    """

    def __init__(self, production_id: str, version: int) -> None:
        self.production_id = production_id
        self.version = version
        super().__init__(f"State version not found: production={production_id!r} version={version}")


class SessionNotFoundError(PersistenceError):
    """Raised when a recovery session_id is not found.

    Inputs:
        session_id: The identifier that was not found.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Recovery session not found: {session_id!r}")
