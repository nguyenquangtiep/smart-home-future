"""Build and compile the agent graph: START -> classify -> respond -> END."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from smarthome.agent import nodes
from smarthome.agent.state import AgentState


def build_graph() -> CompiledStateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("classify", nodes.classify)
    builder.add_node("respond", nodes.respond)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "respond")
    builder.add_edge("respond", END)
    return builder.compile()


def run_agent(message: str, graph: CompiledStateGraph | None = None) -> AgentState:
    """Run the graph once and return the final state."""
    graph = graph or build_graph()
    return graph.invoke({"message": message})
