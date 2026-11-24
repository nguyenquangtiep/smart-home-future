"""Device Capability Model.

A device is described by the capabilities it actually supports, never by its type alone: two
"air_conditioner" devices may expose different ranges or modes. Each capability carries the
rules the policy engine needs: value constraints, minimum role and safety class.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

CommandValue = bool | float | str


class CapabilityKind(StrEnum):
    ON_OFF = "on_off"
    BRIGHTNESS = "brightness"  # percent 0-100
    POSITION = "position"  # cover open percent, 0 = closed, 100 = open
    TARGET_TEMPERATURE = "target_temperature"
    HVAC_MODE = "hvac_mode"
    LOCK = "lock"  # "locked" | "unlocked"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    OCCUPANCY = "occupancy"
    POWER = "power"  # watts


class ValueType(StrEnum):
    BOOL = "bool"
    NUMBER = "number"
    ENUM = "enum"


class Role(StrEnum):
    GUEST = "guest"
    MEMBER = "member"
    OWNER = "owner"


ROLE_RANK: dict[Role, int] = {Role.GUEST: 0, Role.MEMBER: 1, Role.OWNER: 2}


def role_at_least(role: Role, minimum: Role) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


class SafetyClass(StrEnum):
    NORMAL = "normal"
    HIGH_POWER = "high_power"  # water heater, heaters: unattended use is a physical risk
    SECURITY = "security"  # locks, cameras, alarms


class Capability(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: CapabilityKind
    value_type: ValueType
    writable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()
    unit: str | None = None
    min_role: Role = Role.MEMBER
    safety: SafetyClass = SafetyClass.NORMAL
    # Fail-closed rule: for HIGH_POWER and SECURITY capabilities every value needs an explicit,
    # server-side human confirmation EXCEPT the values listed in ``safe_values`` (e.g. lock
    # "locked", heater off). A missing list therefore means "always confirm", never "never".
    safe_values: tuple[bool | str, ...] = ()
    # NORMAL capabilities only: extra values that need confirmation. Ignored for other classes.
    confirm_values: tuple[bool | str, ...] = ()
    # A report older than this many seconds is shown as stale. None = never stale.
    max_age_s: float | None = None

    def validate_value(self, value: object) -> str | None:
        """Return a human-readable error, or None when ``value`` is acceptable."""
        if self.value_type is ValueType.BOOL:
            return None if isinstance(value, bool) else "giá trị phải là true/false"
        if self.value_type is ValueType.ENUM:
            if not isinstance(value, str) or value not in self.options:
                return f"giá trị phải thuộc {list(self.options)}"
            return None
        # NUMBER: bool is a subclass of int in Python, reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "giá trị phải là số"
        if self.minimum is not None and value < self.minimum:
            return f"giá trị nhỏ hơn mức tối thiểu {self.minimum:g}"
        if self.maximum is not None and value > self.maximum:
            return f"giá trị lớn hơn mức tối đa {self.maximum:g}"
        return None

    def matches(self, commanded: object, reported: object) -> bool:
        """Whether a reported value confirms a commanded one."""
        if self.value_type is ValueType.NUMBER:
            if isinstance(commanded, bool) or isinstance(reported, bool):
                return False
            if not isinstance(commanded, int | float) or not isinstance(reported, int | float):
                return False
            tolerance = (self.step or 1.0) / 2
            return abs(float(commanded) - float(reported)) <= tolerance
        return commanded == reported and type(commanded) is type(reported)

    def requires_confirmation(self, value: object) -> bool:
        if self.safety is not SafetyClass.NORMAL:
            return not _contains(self.safe_values, value)
        return _contains(self.confirm_values, value)


def _contains(values: tuple[bool | str, ...], value: object) -> bool:
    # Type-strict membership: True must not match 1, "1" must not match 1.
    return any(value == v and type(value) is type(v) for v in values)


# Factories for the capability shapes used by the supported device types.


def on_off(**overrides: object) -> Capability:
    base = Capability(kind=CapabilityKind.ON_OFF, value_type=ValueType.BOOL, writable=True)
    return base.model_copy(update=overrides)


def brightness() -> Capability:
    return Capability(
        kind=CapabilityKind.BRIGHTNESS,
        value_type=ValueType.NUMBER,
        writable=True,
        minimum=1,
        maximum=100,
        step=1,
        unit="%",
    )


def position() -> Capability:
    return Capability(
        kind=CapabilityKind.POSITION,
        value_type=ValueType.NUMBER,
        writable=True,
        minimum=0,
        maximum=100,
        step=1,
        unit="%",
    )


def target_temperature(minimum: float = 16, maximum: float = 30) -> Capability:
    return Capability(
        kind=CapabilityKind.TARGET_TEMPERATURE,
        value_type=ValueType.NUMBER,
        writable=True,
        minimum=minimum,
        maximum=maximum,
        step=0.5,
        unit="°C",
    )


def hvac_mode(options: tuple[str, ...] = ("cool", "dry", "fan_only", "auto")) -> Capability:
    return Capability(
        kind=CapabilityKind.HVAC_MODE, value_type=ValueType.ENUM, writable=True, options=options
    )


def lock() -> Capability:
    return Capability(
        kind=CapabilityKind.LOCK,
        value_type=ValueType.ENUM,
        writable=True,
        options=("locked", "unlocked"),
        min_role=Role.MEMBER,
        safety=SafetyClass.SECURITY,
        safe_values=("locked",),
    )


def sensor(kind: CapabilityKind, unit: str | None, max_age_s: float = 900) -> Capability:
    value_type = ValueType.BOOL if kind is CapabilityKind.OCCUPANCY else ValueType.NUMBER
    return Capability(
        kind=kind, value_type=value_type, writable=False, unit=unit, max_age_s=max_age_s
    )
