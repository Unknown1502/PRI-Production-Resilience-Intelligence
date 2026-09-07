# PROMPT 01 — Repository Skeleton

## Task

Create the PRI repository skeleton.

## Deliverables

- `pyproject.toml` (uv/hatch, Python 3.12) with dependencies:
  `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy>=2`,
  `asyncpg`, `psycopg[binary]`, `networkx`, `confluent-kafka`, `google-adk`, `google-genai`,
  `httpx`, `structlog`, `reportlab`, `python-dateutil`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`
- `LICENSE` — Apache License 2.0, verbatim stock text, unmodified, at repo root
- `.env.example` listing every env var with dummy values, grouped by:
  `DATABASE_*`, `GOOGLE_*`, `CONFLUENT_*`, `PRI_*`
- `.gitignore` (Python, Node, .env, __pycache__, .venv)
- `src/pri/__init__.py` and this package layout, each with `__init__.py`:
  - `src/pri/domain/`
  - `src/pri/engine/graph/`
  - `src/pri/engine/constraints/`
  - `src/pri/engine/simulation/`
  - `src/pri/engine/verification/`
  - `src/pri/persistence/`
  - `src/pri/events/`
  - `src/pri/agent/`
  - `src/pri/api/`
  - `src/pri/artifacts/`
  - `src/pri/config.py`
- `src/pri/config.py`: pydantic-settings `Settings` class reading every env var
  from `.env.example`. No defaults for secrets. Include a cached `get_settings()`.
- `tests/` mirroring `src/pri/`
- ruff + mypy config in pyproject (strict mypy on `src/pri/domain` and `src/pri/engine`)
- `Makefile`: `install`, `run-api`, `run-web`, `test`, `lint`, `seed`, `demo-reset`
- `ibm/bob/prompts/` and `ibm/bob/artifacts/` with `.gitkeep`

## Acceptance Criteria

- `make install` succeeds
- `make lint` passes on an empty codebase
- `python -c "from pri.config import get_settings"` works with `.env.example` copied to `.env`

## Constraints

- DO NOT add Docker yet
- DO NOT add any AI SDK other than google-adk/google-genai
- DO NOT write business logic
