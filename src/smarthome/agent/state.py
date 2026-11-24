"""Graph state shared between nodes."""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    message: str  # input: user message (already validated / stripped)
    intent: str  # set by the classify node
    reply: str  # set by the respond node
