# Repository Guidelines

## Project Structure

Python package in `src/smarthome/` (Python 3.12, FastAPI, LangGraph):

- `domain/`: home, rooms, devices, capabilities, observed state. Imports nothing else from `smarthome`.
- `gateway/`: `DeviceGateway` contract and adapters (`simulator.py`; Home Assistant planned).
- `control/`: policy engine, execution engine (`ControlService`), audit log. The only path that changes a device.
- `agent/`: LangGraph agent (currently a sample). Must never call a gateway directly.
- `api/`: FastAPI app (`main.py`), routes, dev identity. `demo_home.py`: simulated pilot home for dev/tests.
- Tests in `tests/unit`, `tests/integration`, `tests/eval`; eval data in `eval/`; docs in `docs/` (start at `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`).

## Build, Test, and Development

From the repository root: `python -m pip install -e ".[dev]"`, then `python -m pytest`, `ruff check .`, `ruff format --check .`, `python eval/scripts/run_eval.py`. Run locally with `uvicorn smarthome.api.main:app --reload`. Environment variables use the `SMARTHOME_` prefix; see `.env.example` (set `SMARTHOME_DEV_IDENTITY_ENABLED=true` to use the control API locally).

## Coding Style and Naming

Follow the chosen language's conventions and the project's formatter and linter once configured. Keep indentation consistent within each file. Use names that describe what a file, function, or test does, and follow the naming patterns already established in the module you are editing. Document any project-specific conventions when they are introduced.

## Testing

pytest, files `tests/**/test_*.py`; async code is tested with `asyncio.run` (no plugin). Tests use the simulator only, never real devices. For behavior changes, run the relevant tests and cover the changed behavior with a focused regression test when existing coverage would miss it. Document any manual checks needed to verify changes that the automated suite does not cover.

## Commits and Pull Requests

Use a short, imperative commit subject that describes the change. In pull requests, summarize what changed and why, list the checks you ran, and link related issues when available. Include screenshots for visible UI changes and mention any setup steps reviewers need.
