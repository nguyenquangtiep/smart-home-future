"""Graph nodes. Each node takes the state and returns a partial state update."""

from smarthome.agent.state import AgentState
from smarthome.agent.tools import detect_intent, tokenize

REPLIES: dict[str, str] = {
    "greeting": "Xin chào! Tôi là trợ lý Smart Home Future. Tôi có thể giúp gì cho bạn?",
    "thanks": "Rất vui được giúp bạn!",
    "help": "Bạn có thể chào tôi, nhờ trợ giúp, hoặc gửi bất kỳ câu nào để tôi ghi nhận.",
}


def classify(state: AgentState) -> AgentState:
    return {"intent": detect_intent(state["message"])}


def respond(state: AgentState) -> AgentState:
    intent = state["intent"]
    if intent in REPLIES:
        return {"reply": REPLIES[intent]}
    word_count = len(tokenize(state["message"]))
    return {"reply": f"Tôi đã nhận được tin nhắn của bạn ({word_count} từ)."}
