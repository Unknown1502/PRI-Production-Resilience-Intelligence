"""Apply the schema and seed the demo production, in one shot.

Run as a Cloud Run job after a deploy, and locally with ``make bootstrap``:

    python -m pri.persistence.bootstrap

Idempotent. The migration is written with ``CREATE TABLE IF NOT EXISTS``, and
the seeder resets the demo production before inserting it, so running this
against a live database returns it to version 1 rather than failing or
duplicating.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text

from pri.config import get_settings
from pri.persistence.database import build_engine
from pri.persistence.seed import seed

if TYPE_CHECKING:
    from pri.domain.models import ProductionState

__all__ = ["apply_migrations", "bootstrap", "main", "split_sql_statements"]

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def split_sql_statements(sql: str) -> list[str]:
    """Split a migration file into statements, respecting quotes and comments.

    Splitting on a bare ``;`` is wrong in two ways that both bite eventually: a
    semicolon inside a ``--`` comment truncates the statement around it, and one
    inside a string literal — ``DEFAULT 'a;b'`` — splits the literal in half.
    Both produce a syntax error pointing at a line that looks correct.

    Inputs:
        sql: The whole file.

    Outputs:
        The statements, comments stripped, blanks removed.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    index = 0

    while index < len(sql):
        char = sql[index]
        pair = sql[index : index + 2]

        if in_line_comment:
            if char == chr(10):
                in_line_comment = False
                current.append(char)
            index += 1
            continue

        if in_block_comment:
            if pair == "*/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_string:
            current.append(char)
            if char == "'":
                # A doubled quote is an escaped quote, not the end of the string.
                if sql[index + 1 : index + 2] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if pair == "--":
            in_line_comment = True
            index += 2
            continue
        if pair == "/*":
            in_block_comment = True
            index += 2
            continue
        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue
        if char == ";":
            statements.append("".join(current).strip())
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statements.append("".join(current).strip())
    return [statement for statement in statements if statement]


async def apply_migrations(database_url: str) -> list[str]:
    """Run every ``.sql`` file in ``migrations/``, in filename order.

    Inputs:
        database_url: An ``postgresql+asyncpg://`` DSN.

    Outputs:
        The names of the migration files applied.

    Failure modes:
        Raises ``sqlalchemy.exc.SQLAlchemyError`` if the database is
        unreachable or a statement fails. A failed migration aborts the whole
        file's transaction — a half-applied schema is worse than none.
    """
    applied: list[str] = []
    engine = build_engine(database_url)
    try:
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            async with engine.begin() as conn:
                for statement in split_sql_statements(path.read_text(encoding="utf-8")):
                    await conn.execute(text(statement))
            applied.append(path.name)
    finally:
        await engine.dispose()
    return applied


async def bootstrap(
    production_id: str | None = None, *, sample: str | None = None
) -> tuple[list[str], ProductionState]:
    """Apply migrations, then seed a production.

    Inputs:
        production_id: Override the production to seed; defaults to the
                       configured demo production. Ignored when ``sample`` is
                       given, which carries its own id.
        sample:        Seed one of :data:`pri.persistence.seed.SAMPLES` instead
                       of the fixture.

    Outputs:
        ``(migration filenames, the seeded state)``.
    """
    settings = get_settings()
    migrations = await apply_migrations(settings.database_dsn)
    state = await seed(production_id or settings.pri_demo_production_id, reset=True, sample=sample)
    return migrations, state


def main() -> None:
    """Entrypoint for ``python -m pri.persistence.bootstrap``."""
    migrations, state = asyncio.run(bootstrap())
    for name in migrations:
        print(f"applied {name}")
    print(
        f"seeded {state.production.title} at version {state.version}: "
        f"{len(state.scenes)} scenes, {len(state.schedule.days)} shooting days"
    )


if __name__ == "__main__":
    main()
