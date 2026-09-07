"""The competition rules, as tests.

"We only used Google AI tooling" is a claim a judge has to take on trust unless
something checks it. These tests check it, and they check the containment that
makes the deterministic/LLM split real rather than aspirational.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src"

#: Every AI or agent SDK the contest rules prohibit.
FORBIDDEN_ROOTS = frozenset(
    {
        "langchain",
        "langchain_core",
        "langchain_community",
        "langgraph",
        "crewai",
        "autogen",
        "pyautogen",
        "openai",
        "anthropic",
        "cohere",
        "mistralai",
        "ollama",
        "llama_index",
        "haystack",
        "semantic_kernel",
        "transformers",
        "boto3",
        "botocore",
    }
)


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class TestNoForbiddenSDKs:
    def test_no_source_file_imports_a_forbidden_sdk(self) -> None:
        offenders: list[str] = []
        for path in _python_files():
            hits = _imported_roots(path) & FORBIDDEN_ROOTS
            if hits:
                offenders.append(f"{path.relative_to(SRC)}: {', '.join(sorted(hits))}")
        assert offenders == [], "Forbidden AI SDK imports found:\n" + "\n".join(offenders)

    def test_no_forbidden_sdk_is_even_installed_and_imported(self) -> None:
        """Nothing forbidden is live in the interpreter after importing the app."""
        import pri.agent
        import pri.api.main
        import pri.engine.simulation.replan  # noqa: F401

        live = {name.split(".")[0] for name in sys.modules}
        assert live & FORBIDDEN_ROOTS == set()

    def test_the_agent_uses_google_adk(self) -> None:
        from pri.agent import root_agent

        assert "google.adk" in str(root_agent.LlmAgent.__module__) or root_agent.LlmAgent


class TestDeterministicContainment:
    """The architecture law, enforced rather than asserted in a README."""

    def test_the_engine_never_imports_the_agent(self) -> None:
        """A model call inside a scoring loop would make the numbers untrustworthy."""
        engine = SRC / "pri" / "engine"
        offenders = [
            str(path.relative_to(SRC))
            for path in engine.rglob("*.py")
            if any(
                root in {"google", "pri"} and "agent" in text
                for root in _imported_roots(path)
                for text in [path.read_text(encoding="utf-8")]
                if "from pri.agent" in text or "import pri.agent" in text
            )
        ]
        assert offenders == []

    def test_the_domain_layer_imports_nothing_but_pydantic(self) -> None:
        domain = SRC / "pri" / "domain"
        allowed = {"__future__", "uuid", "datetime", "decimal", "typing", "pydantic"}
        for path in domain.rglob("*.py"):
            assert _imported_roots(path) <= allowed, f"{path.name} imports outside the domain"

    @pytest.mark.parametrize(
        "module",
        [
            "pri.engine.constraints.validator",
            "pri.engine.simulation.scoring",
            "pri.engine.simulation.pareto",
            "pri.engine.verification.verify",
        ],
    )
    def test_number_producing_modules_import_no_ai_sdk(self, module: str) -> None:
        path = SRC / Path(*module.split(".")).with_suffix(".py")
        roots = _imported_roots(path)
        assert "google" not in roots
        assert roots & FORBIDDEN_ROOTS == set()
