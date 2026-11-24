"""Run the agent over a JSONL dataset and print passed/total.

Exit code is 0 only when every case passes.

Usage: python eval/scripts/run_eval.py [--dataset PATH]
"""

import argparse
import sys
from pathlib import Path

from smarthome.agent.graph import build_graph
from smarthome.evaluation import DEFAULT_DATASET, load_cases, run_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args(argv)

    cases = load_cases(args.dataset)
    graph = build_graph()
    results = [run_case(case, graph) for case in cases]

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.case.id}: intent={r.intent}")
        if not r.passed:
            print(
                f"       expected intent={r.case.expected_intent}, "
                f"reply containing {r.case.expected_reply_contains!r}; got {r.reply!r}"
            )

    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} cases passed")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
