"""Policy engine: pure, deterministic checks run before any command reaches a gateway.

The LLM never decides whether an action is allowed. It only proposes; this module decides.
"""

from enum import StrEnum

from pydantic import BaseModel

from smarthome.domain.capabilities import CapabilityKind, CommandValue, Role, role_at_least
from smarthome.domain.models import Device
from smarthome.domain.state import Connectivity, DeviceView


class Origin(StrEnum):
    UI = "ui"  # a person pressed a control
    AGENT = "agent"  # proposed by the AI agent on a person's behalf
    AUTOMATION = "automation"  # unattended rule or schedule


class Actor(BaseModel):
    user_id: str
    home_id: str
    role: Role
    origin: Origin = Origin.UI


class CommandRequest(BaseModel):
    device_id: str
    capability: CapabilityKind
    value: CommandValue


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class Reason(BaseModel):
    code: str
    message: str


class PolicyDecision(BaseModel):
    outcome: Outcome
    reasons: list[Reason] = []


def _deny(code: str, message: str) -> PolicyDecision:
    return PolicyDecision(outcome=Outcome.DENY, reasons=[Reason(code=code, message=message)])


def evaluate(
    actor: Actor, device: Device, request: CommandRequest, view: DeviceView
) -> PolicyDecision:
    """Decide whether ``actor`` may apply ``request`` to ``device`` right now."""
    if device.home_id != actor.home_id:
        return _deny("WRONG_HOME", "Thiết bị không thuộc nhà của bạn.")

    cap = device.capability(request.capability)
    if cap is None:
        return _deny("UNSUPPORTED_CAPABILITY", f"{device.name} không hỗ trợ {request.capability}.")
    if not cap.writable:
        return _deny("READ_ONLY", f"{request.capability} của {device.name} chỉ đọc.")
    error = cap.validate_value(request.value)
    if error:
        return _deny("INVALID_VALUE", f"{device.name}: {error}.")

    if not role_at_least(actor.role, cap.min_role):
        return _deny("FORBIDDEN_ROLE", f"Vai trò {actor.role} không được điều khiển {device.name}.")

    if view.connectivity is Connectivity.OFFLINE:
        return _deny("DEVICE_OFFLINE", f"{device.name} đang mất kết nối.")

    if cap.requires_confirmation(request.value):
        if actor.origin is Origin.AUTOMATION:
            return _deny(
                "AUTOMATION_NOT_ALLOWED",
                f"Tự động hóa không được thực hiện thao tác cần xác nhận trên {device.name}.",
            )
        return PolicyDecision(
            outcome=Outcome.REQUIRE_CONFIRMATION,
            reasons=[
                Reason(
                    code="CONFIRMATION_REQUIRED",
                    message=f"Thao tác trên {device.name} ({cap.safety}) cần chủ nhà xác nhận.",
                )
            ],
        )

    return PolicyDecision(outcome=Outcome.ALLOW)


def may_confirm(approver: Actor, device: Device) -> bool:
    """Only the home owner can approve a command that requires confirmation."""
    return approver.home_id == device.home_id and approver.role is Role.OWNER
