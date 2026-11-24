from smarthome.agent import nodes
from smarthome.agent.graph import build_graph, run_agent


def test_classify_node_sets_intent() -> None:
    assert nodes.classify({"message": "xin chào"}) == {"intent": "greeting"}


def test_respond_node_unknown_counts_words() -> None:
    update = nodes.respond({"message": "một hai ba", "intent": "unknown"})
    assert update["reply"] == "Tôi đã nhận được tin nhắn của bạn (3 từ)."


def test_graph_end_to_end_greeting() -> None:
    state = run_agent("xin chào")
    assert state["intent"] == "greeting"
    assert state["reply"] == nodes.REPLIES["greeting"]


def test_compiled_graph_is_reusable() -> None:
    graph = build_graph()
    assert graph.invoke({"message": "cảm ơn"})["intent"] == "thanks"
    assert graph.invoke({"message": "giúp với"})["intent"] == "help"
