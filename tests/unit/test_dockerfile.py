"""F1 regression: the Dockerfile must parse and keep its safe defaults.

A previous edit left a literal "\\n" inside a multi-line ENV, which Docker rejects. These checks
run everywhere (no Docker needed); the real build runs in CI (scripts/docker_smoke.sh).
"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def instructions() -> list[str]:
    """Logical instructions: comments dropped, backslash continuations joined."""
    out: list[str] = []
    current = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not current and (not line or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        out.append((current + line).strip())
        current = ""
    assert not current, "the file ends inside a continued instruction"
    return out


def env_vars() -> dict[str, str]:
    found: dict[str, str] = {}
    for ins in instructions():
        if ins.startswith("ENV "):
            for token in ins[4:].split():
                assert "=" in token, f"ENV token without '=': {token!r}"
                key, value = token.split("=", 1)
                assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", key), f"bad ENV name {key!r}"
                found[key] = value
    return found


def test_no_literal_escape_sequences() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "\\n" not in text and "\\t" not in text


def test_every_instruction_is_known() -> None:
    known = {"FROM", "ENV", "WORKDIR", "COPY", "RUN", "USER", "EXPOSE", "HEALTHCHECK", "CMD"}
    for ins in instructions():
        assert ins.split()[0] in known, ins


def test_production_is_the_default_environment() -> None:
    assert env_vars()["SMARTHOME_ENVIRONMENT"] == "production"


def test_python_312_non_root_and_healthcheck() -> None:
    joined = instructions()
    assert joined[0].startswith("FROM python:3.12")
    users = [i for i in joined if i.startswith("USER ")]
    assert users and users[-1] != "USER root"
    assert any(i.startswith("HEALTHCHECK ") and "/health" in i for i in joined)
    cmd = next(i for i in joined if i.startswith("CMD "))
    assert "--workers" not in cmd  # one worker per instance (ADR 0001)


def test_no_secrets_are_baked_into_the_image() -> None:
    for key in env_vars():
        assert not re.search(r"PASSWORD|SECRET|TOKEN|DATABASE_URL|API_KEY", key), key
    copies = [i for i in instructions() if i.startswith("COPY ")]
    assert all(".env" not in c for c in copies)
    ignored = (DOCKERFILE.parent / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored and ".git" in ignored
