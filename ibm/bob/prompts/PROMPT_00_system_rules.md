# PROMPT 00 — System Rules & Architecture Laws

> **Session opening prompt — defines the operating contract for all subsequent tasks.**

---

You are building PRI (Production Resilience Intelligence), a stateful agentic
digital twin for film production. When production reality changes, PRI computes
the impact, generates candidate recovery futures, validates them against hard
constraints deterministically, has a human approve one, executes it, and
verifies the resulting state.

## ARCHITECTURE LAW — never violate:

1. Deterministic software computes ALL numbers: schedules, costs, delays,
   constraint satisfaction, validity. Gemini NEVER produces a number that the
   system treats as true.
2. Gemini interprets events, proposes which strategy families to explore, and
   explains trade-offs using numbers supplied to it. That is all.
3. Never mutate a committed production state in place. Every change creates a
   new immutable version with a parent pointer.
4. No consequential mutation may bypass this sequence:
   `validate_state -> validate_constraints -> validate_policy ->
   verify_authorization -> request_approval -> execute -> verify_result`
5. Every module is typed (Pydantic v2 / TypeScript strict). No `Any` in domain code.

## COMPETITION CONSTRAINTS — hard:

- Google AI layer must use native Google ADK (`google-adk`) + Gemini only.
- FORBIDDEN, no exceptions: LangChain, LangGraph, CrewAI, AutoGen, OpenAI,
  Anthropic, AWS Bedrock, Azure AI, or any non-Google AI/agent SDK. The contest
  rules prohibit all non-Google AI tooling in the project.
- Confluent Kafka (`confluent-kafka`) is the event fabric.
- PostgreSQL is the canonical state store.
- Python 3.12, FastAPI, Pydantic v2, NetworkX. Next.js 15 + TypeScript +
  Tailwind + @xyflow/react on the frontend.
- No secrets in the repo. Config via environment variables only.

## STYLE:

- Small, testable, single-responsibility modules.
- Docstrings on public functions stating inputs, outputs, and failure modes.
- Fail loud: raise typed exceptions, never return None to signal failure.
- Do not invent APIs. If unsure of a library signature, say so instead of guessing.
