"""Shared eval logic used by both ``eval/scripts/run_eval.py`` and ``tests/eval``."""

import json
from dataclasses import dataclass
from pathlib import Path

from langgraph.graph.state import CompiledStateGraph

from smarthome.agent.graph import build_graph

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "eval" / "datasets" / "agent_regression.jsonl"


@dataclass(frozen=True)
class EvalCase:
    id: str
    message: str
    expected_intent: str
    expected_reply_contains: str


@dataclass(frozen=True)
class EvalResult:
    case: EvalCase
    intent: str
    reply: str

    @property
    def passed(self) -> bool:
        return (
            self.intent == self.case.expected_intent
            and self.case.expected_reply_contains in self.reply
        )


def load_cases(path: Path = DEFAULT_DATASET) -> list[EvalCase]:
    cases = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                cases.append(EvalCase(**json.loads(line)))
    return cases


def run_case(case: EvalCase, graph: CompiledStateGraph | None = None) -> EvalResult:
    graph = graph or build_graph()
    state = graph.invoke({"message": case.message})
    return EvalResult(case=case, intent=state["intent"], reply=state["reply"])
