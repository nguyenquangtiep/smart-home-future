"""Regression eval: every case in the shared JSONL dataset must pass."""

import pytest

from smarthome.agent.graph import build_graph
from smarthome.evaluation import EvalCase, load_cases, run_case

CASES = load_cases()
GRAPH = build_graph()


def test_dataset_not_empty() -> None:
    assert CASES


@pytest.mark.eval
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_regression_case(case: EvalCase) -> None:
    result = run_case(case, GRAPH)
    assert result.passed, (
        f"intent={result.intent!r} (expected {case.expected_intent!r}), reply={result.reply!r}"
    )
