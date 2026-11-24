"""Run the agent without HTTP: ``python -m smarthome.agent "xin chào"``."""

import sys

from smarthome.agent.graph import run_agent

print(run_agent(" ".join(sys.argv[1:]) or "xin chào")["reply"])
