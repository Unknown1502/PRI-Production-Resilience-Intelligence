"""Where PRI's data and configuration live, wherever PRI is running from.

Six modules used to compute a path like ``Path(__file__).parents[3] / "config"``.
That is correct in a source checkout, where the package sits at
``<repo>/src/pri/...`` and three levels up is the repository root. It is wrong
the moment the package is installed: from
``/opt/venv/lib/python3.12/site-packages/pri/persistence/seed.py`` the same
expression yields ``/opt/venv/lib/python3.12/data/fixtures``, which does not
exist.

So the container failed on its very first command — before the database, before
Kafka, before anything worth debugging — with a ``FileNotFoundError`` naming a
path inside site-packages. Cloud Run's seed job would have died the same way.

Resolution order:

1. ``PRI_PROJECT_ROOT``, when set. The explicit answer, and what an unusual
   deployment layout should use.
2. Walk up from this file looking for a directory that has both ``config`` and
   ``data``. Finds the repository root in a source checkout or an editable
   install, and costs nothing.
3. The working directory, if it looks like a project root. This is what the
   containers use: the Dockerfiles copy ``config/`` and ``data/`` next to the
   app and set ``WORKDIR /app``.
4. ``/app``, the container convention, as a last resort.

Nothing here raises on a missing root. A caller that needs a file it cannot
find should say which file, not which directory — the call sheet degrades to
em-dashes when the logistics file is absent, and that is the right behaviour.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

__all__ = ["config_path", "data_path", "project_root"]

#: A directory is a project root when it carries both of these.
_MARKERS = ("config", "data")


def _looks_like_root(candidate: Path) -> bool:
    return all((candidate / marker).is_dir() for marker in _MARKERS)


@lru_cache(maxsize=1)
def project_root() -> Path:
    """The directory holding ``config/`` and ``data/``.

    Outputs:
        An absolute path. Not guaranteed to exist — see the module docstring
        for why that is deliberate.
    """
    override = os.environ.get("PRI_PROJECT_ROOT")
    if override:
        return Path(override).resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        if _looks_like_root(parent):
            return parent

    cwd = Path.cwd()
    if _looks_like_root(cwd):
        return cwd

    return Path("/app")


def config_path(*parts: str) -> Path:
    """A path under ``config/``."""
    return project_root().joinpath("config", *parts)


def data_path(*parts: str) -> Path:
    """A path under ``data/``."""
    return project_root().joinpath("data", *parts)
