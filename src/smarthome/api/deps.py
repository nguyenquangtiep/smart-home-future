"""FastAPI dependencies. The graph is compiled once and reused across requests."""

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from smarthome.agent.graph import build_graph


@lru_cache(maxsize=1)
def get_graph() -> CompiledStateGraph:
    return build_graph()
