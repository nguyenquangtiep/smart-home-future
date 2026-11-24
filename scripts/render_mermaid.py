"""Render every Mermaid block in README.md and docs/ with the real Mermaid renderer.

Unlike the regex checks in tests/unit/test_docs.py, this runs mermaid-cli (headless Chromium)
on each block, so any syntax GitHub would fail to render fails here too.

Usage:
    python scripts/render_mermaid.py [--mmdc PATH] [--out DIR]

``--mmdc`` defaults to ``mmdc`` on PATH (CI installs @mermaid-js/mermaid-cli globally).
Exit code 0 only when every block renders to a non-empty, well-formed SVG with a viewBox.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUPPETEER_CONFIG = Path(__file__).with_name("puppeteer-config.json")
_MERMAID = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)


def collect() -> list[tuple[str, int, str]]:
    docs = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    blocks = []
    for doc in docs:
        for index, code in enumerate(_MERMAID.findall(doc.read_text(encoding="utf-8")), 1):
            blocks.append((doc.relative_to(ROOT).as_posix(), index, code))
    return blocks


def check_svg(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return "no SVG produced"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return f"SVG is not well-formed XML: {exc}"
    if not root.get("viewBox"):
        return "SVG has no viewBox (cannot scale)"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mmdc", default=shutil.which("mmdc") or "mmdc")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out = args.out or Path(tempfile.mkdtemp(prefix="mermaid-"))
    out.mkdir(parents=True, exist_ok=True)
    blocks = collect()
    failures = []
    for doc, index, code in blocks:
        stem = f"{doc.replace('/', '__').removesuffix('.md')}__{index}"
        src, svg = out / f"{stem}.mmd", out / f"{stem}.svg"
        src.write_text(code, encoding="utf-8")
        cmd = [args.mmdc, "-q", "-i", str(src), "-o", str(svg), "-p", str(PUPPETEER_CONFIG)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        problem = None
        if proc.returncode != 0:
            lines = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
            errors = [ln for ln in lines if "error" in ln.lower() and not ln.startswith("at ")]
            problem = (errors or lines or ["mmdc failed"])[0][:300]
        else:
            problem = check_svg(svg)
        status = "FAIL" if problem else "ok"
        print(f"[{status}] {doc} #{index}" + (f": {problem}" if problem else ""))
        if problem:
            failures.append((doc, index, problem))

    print(f"\n{len(blocks) - len(failures)}/{len(blocks)} Mermaid blocks rendered (output: {out})")
    return 1 if failures or not blocks else 0


if __name__ == "__main__":
    sys.exit(main())
