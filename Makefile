.PHONY: install run-api run-web run-consumer test test-fast lint format seed bootstrap topics demo demo-reset build-web \n        up down logs ps rebuild verify kafka-smoke

# ── Paths ─────────────────────────────────────────────────────────────────────
PYTHON      := python
UV          := uv
SRC_DIR     := src
WEB_DIR     := web
TEST_DIR    := tests

# ── Install ───────────────────────────────────────────────────────────────────
install:
	$(UV) pip install -e ".[dev]"

# ── API server ────────────────────────────────────────────────────────────────
run-api:
	$(UV) run uvicorn pri.api.main:app \
		--host $${PRI_API_HOST:-0.0.0.0} \
		--port $${PRI_API_PORT:-8000} \
		--reload

# ── Frontend dev server ───────────────────────────────────────────────────────
run-web:
	cd $(WEB_DIR) && npm run dev

# ── Consumer ──────────────────────────────────────────────────────────────────
run-consumer:
	$(UV) run python -m pri.events.runner

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	$(UV) run pytest $(TEST_DIR) -v --tb=short

# Everything that does not need a live cloud dependency.
test-fast:
	$(UV) run pytest $(TEST_DIR) -q -m "not integration"

# ── Lint / type-check ─────────────────────────────────────────────────────────
lint:
	$(UV) run ruff check $(SRC_DIR) $(TEST_DIR)
	$(UV) run ruff format --check $(SRC_DIR) $(TEST_DIR)
	$(UV) run mypy $(SRC_DIR)/pri

# ── Format ────────────────────────────────────────────────────────────────────
format:
	$(UV) run ruff check $(SRC_DIR) $(TEST_DIR) scripts --fix
	$(UV) run ruff format $(SRC_DIR) $(TEST_DIR) scripts

# ── Seed ──────────────────────────────────────────────────────────────────────
seed:
	$(UV) run python -m pri.persistence.seed

# Schema + seed in one shot. Safe to re-run; returns the demo to version 1.
bootstrap:
	$(UV) run python -m pri.persistence.bootstrap

# ── Confluent ─────────────────────────────────────────────────────────────────
topics:
	$(UV) run python -m pri.events.admin

# ── Demo ──────────────────────────────────────────────────────────────────────
# Drives the whole story against a running API and prints each stage with timings.
demo:
	$(UV) run python scripts/run_demo.py

# Publishes the LOC-04 blocked event to Confluent — the button in the video.
emit:
	$(UV) run python scripts/emit_disruption.py

# ── Frontend build ────────────────────────────────────────────────────────────
build-web:
	cd $(WEB_DIR) && npm ci --no-audit --no-fund && npm run build

# ── Demo reset ────────────────────────────────────────────────────────────────
demo-reset:
	$(UV) run python -m pri.persistence.seed

# ── Docker: the whole stack, in the shape it runs on Cloud Run ────────────────
#
# Worth running before infra/deploy.sh. It exercises the real images rather
# than the developer's virtualenv, so anything that would break on Cloud Run —
# a missing system library, a non-root user that cannot write, a file the
# build context does not carry — breaks here first and costs seconds.
up:
	docker compose up -d --build
	@echo ""
	@echo "  web       http://localhost:3000"
	@echo "  api       http://localhost:8000/health"
	@echo "  consumer  http://localhost:8081/"
	@echo ""

down:
	docker compose down

# Also drops the database volume. Use when the schema has moved under you.
rebuild:
	docker compose down -v
	docker compose up -d --build

logs:
	docker compose logs -f --tail=80

ps:
	docker compose ps

# ── Verification ──────────────────────────────────────────────────────────────
# Checks the pipeline, not just liveness. Writes to docs/evidence/runtime/.
verify:
	bash infra/verify.sh $${PRI_API_BASE_URL:-http://localhost:8000}

# Proves Confluent delivery with a real partition and offset, rather than
# reporting success because nothing raised.
kafka-smoke:
	$(UV) run python -m pri.events.smoke
