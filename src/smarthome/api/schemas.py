"""Request/response models for the HTTP API."""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class InvokeRequest(BaseModel):
    message: Message


class InvokeResponse(BaseModel):
    reply: str


class HealthResponse(BaseModel):
    status: str
