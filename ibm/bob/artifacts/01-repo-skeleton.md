# Artifact 01 — Repository Skeleton

**Prompt:** [PROMPT_01_repo_skeleton.md](../prompts/PROMPT_01_repo_skeleton.md)
**Gate:** `GATE`
**Outcome:** complete

---

## Plan

Lay out the package, pin the dependencies, and configure the quality gates
before any business logic exists — so that strict typing and linting are the
normal state of the repository rather than something retrofitted later.

## Files created

| File | Purpose |
|---|---|
| `pyproject.toml` | Python 3.12, all runtime and dev dependencies, ruff + mypy config |
| `LICENSE` | Apache-2.0, stock text, unmodified |
| `.env.example` | Every variable grouped `DATABASE_*`, `GOOGLE_*`, `CONFLUENT_*`, `PRI_*` |
| `.gitignore` | Python, Node, `.env`, caches |
| `Makefile` | `install`, `run-api`, `run-web`, `test`, `lint`, `seed`, `demo-reset` |
| [`src/pri/config.py`](../../../src/pri/config.py) | pydantic-settings `Settings`, cached `get_settings()` |
| `src/pri/{domain,engine,persistence,events,agent,api,artifacts}/` | Package layout, each with `__init__.py` |
| `tests/` | Mirroring `src/pri/` |
| `ibm/bob/{prompts,artifacts}/` | The evidence trail |

## Decisions

**Strict mypy on `domain` and `engine` only.** Those two layers are where a
wrong type becomes a wrong number. The API and event layers talk to libraries
whose stubs are incomplete, and forcing strictness there would have meant
scattering `type: ignore` — which is worse than an honest boundary.

**No secret has a default.** `Settings` fails at import if a required variable
is missing, rather than falling back to something insecure and working until it
does not.

**No Docker yet.** The prompt said not to, and it was right: containerising
before there is anything to containerise produces a Dockerfile that has to be
rewritten in session 16 anyway.

## How to run it

```bash
make install
cp .env.example .env
python -c "from pri.config import get_settings; get_settings()"
```

## Acceptance criteria

- ✅ `make install` succeeds
- ✅ `make lint` passes on an empty codebase
- ✅ `from pri.config import get_settings` works with `.env.example` copied to `.env`
- ✅ No AI SDK other than `google-adk` / `google-genai`
- ✅ No business logic

## Later revisions

Session 09 added `PRI_API_KEY`, `PRI_AGENT_ENABLED`, `PRI_A2A_ENABLED`,
`PRI_DEMO_PRODUCTION_ID`, `PRI_ARTIFACT_ROOT`, `GIT_SHA`, `GOOGLE_GENAI_MODEL`
and `GOOGLE_GENAI_USE_VERTEXAI` to both `Settings` and `.env.example`, plus a
`Settings.gemini_model` property so `GOOGLE_GENAI_MODEL` wins over the older
`GOOGLE_GEMINI_MODEL` without breaking existing `.env` files.

Session 07 added `[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes` for Pydantic and pydantic-settings. Without it
ruff's `TC001`/`TC003` rules would "fix" every API model into a `NameError` at
import, because Pydantic resolves annotations at runtime.

## Not covered

No dependency lock file for Python — `pyproject.toml` pins ranges, not exact
versions, so two `make install` runs a month apart can differ. `uv.lock` or
`requirements.txt` with hashes would close that. The frontend does have
`package-lock.json`.
