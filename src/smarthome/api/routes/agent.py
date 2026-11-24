from typing import Annotated

from fastapi import APIRouter, Depends
from langgraph.graph.state import CompiledStateGraph

from smarthome.api.deps import get_graph
from smarthome.api.schemas import InvokeRequest, InvokeResponse
from smarthome.core.logging import get_logger

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
logger = get_logger("api.agent")


@router.post("/invoke", response_model=InvokeResponse)
def invoke(
    body: InvokeRequest,
    graph: Annotated[CompiledStateGraph, Depends(get_graph)],
) -> InvokeResponse:
    result = graph.invoke({"message": body.message})
    # Log metadata only, never the message content.
    logger.info("agent invoked intent=%s message_len=%d", result["intent"], len(body.message))
    return InvokeResponse(reply=result["reply"])
