"""Documentation integrity (Visual-first standard, rule D8): links, anchors, SVGs, Mermaid blocks.

Fast static checks run here. Real Mermaid rendering is done in CI by
``scripts/render_mermaid.py`` (mermaid-cli), because it needs Node and a headless browser.
"""

import re
import unicodedata
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
SVGS = sorted((ROOT / "docs" / "assets").rglob("*.svg"))

_FENCE = re.compile(r"^```.*?^```", re.S | re.M)
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MERMAID = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)
_DIAGRAM_TYPES = (
    "flowchart",
    "sequenceDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "pie",
    "timeline",
)


def _ids(paths: list[Path]) -> list[str]:
    return [str(p.relative_to(ROOT)) for p in paths]


def github_slug(heading: str) -> str:
    """Anchor GitHub generates for a heading: lowercase, drop punctuation and symbols
    (keeping letters, digits, combining marks, spaces, '-' and '_'), spaces become '-'."""
    text = unicodedata.normalize("NFC", heading.strip().lower())
    kept = "".join(
        ch for ch in text if ch.isalnum() or ch in " -_" or unicodedata.category(ch).startswith("M")
    )
    return kept.replace(" ", "-")


@cache
def anchors(path: Path) -> frozenset[str]:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    # GitHub builds the anchor from the rendered text: a link keeps its label, not its URL.
    return frozenset(github_slug(_MD_LINK.sub(r"\1", h)) for h in _HEADING.findall(text))


def test_github_slug_examples() -> None:
    assert github_slug("4. Digital Twin 3D") == "4-digital-twin-3d"
    assert github_slug("3. Web UI/UX") == "3-web-uiux"
    assert github_slug("7. Rủi ro còn mở") == "7-rủi-ro-còn-mở"
    assert (
        github_slug(_MD_LINK.sub(r"\1", "5. WebSocket cho [ADR 0007](0007-x.md)"))
        == "5-websocket-cho-adr-0007"
    )
    assert github_slug("2.1 Bảng đã có (migration `0001_commands_audit`)") == (
        "21-bảng-đã-có-migration-0001_commands_audit"
    )


@pytest.mark.parametrize("doc", DOCS, ids=_ids(DOCS))
def test_relative_links_images_and_anchors_exist(doc: Path) -> None:
    text = _FENCE.sub("", doc.read_text(encoding="utf-8"))
    problems = []
    for target in _LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue  # external links are not checked offline (see TESTING_STRATEGY)
        path, _, fragment = target.partition("#")
        resolved = (doc.parent / path).resolve() if path else doc
        if not resolved.exists():
            problems.append(f"missing file: {target}")
            continue
        if fragment and resolved.suffix == ".md":
            wanted = unicodedata.normalize("NFC", fragment.lower())
            if wanted not in anchors(resolved):
                problems.append(f"missing anchor: {target}")
    assert not problems, f"{doc.name}: {problems}"


@pytest.mark.parametrize("doc", DOCS, ids=_ids(DOCS))
def test_mermaid_blocks_are_non_empty_and_typed(doc: Path) -> None:
    for block in _MERMAID.findall(doc.read_text(encoding="utf-8")):
        first = block.strip().splitlines()[0] if block.strip() else ""
        assert first.startswith(_DIAGRAM_TYPES), f"{doc.name}: unexpected Mermaid start {first!r}"
        # ";" ends a Mermaid statement: inside a label it silently breaks rendering on GitHub.
        bad = [line.strip() for line in block.splitlines() if ";" in line]
        assert not bad, f"{doc.name}: ';' inside Mermaid breaks parsing: {bad}"


@pytest.mark.parametrize("svg", SVGS, ids=_ids(SVGS))
def test_svg_assets_are_wellformed_scalable_and_accessible(svg: Path) -> None:
    root = ET.parse(svg).getroot()  # raises on malformed XML
    assert root.get("viewBox"), f"{svg.name}: needs a viewBox so it scales on narrow screens"
    text = svg.read_text(encoding="utf-8")
    assert "<title" in text and "<desc" in text, f"{svg.name}: needs <title> and <desc>"
    assert 'lang="' in text, f"{svg.name}: needs a lang attribute"
