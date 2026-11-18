#!/usr/bin/env python3
"""MTM Planner CLI:  python main.py <messages_file>

One command for the first run and for every update. No flags, no hints.
More than 3 messages (or no saved state) = full run from scratch.
3 messages or fewer with saved state = update: only unseen messages are processed.
"""
import sys

from engine import orchestrator, store
from engine.llm import load_env


def main(argv: list) -> int:
    if len(argv) != 2 or argv[1].startswith("-"):
        print("usage: python main.py <messages_file>", file=sys.stderr)
        return 2
    load_env()
    res = orchestrator.process(argv[1], echo=True, color=sys.stdout.isatty())
    tr = res["trace"]
    print("-" * 78)
    print(tr.footer())
    if not res.get("ok"):
        print(f"\nFAILED: {res['error']}")
        print("AREAS WITH MODEL CALLS: " + (", ".join(tr.areas_with_calls()) or "none"))
        return 1
    st = res["state"]
    print()
    print(store.render_plan_text(st))
    if res.get("run"):
        paths = store.output_dir()
        print(f"\nwritten: {paths / 'plan.json'}, {paths / 'trace.txt'}, {paths / 'final_plan_and_trace.txt'}")
    print("\nAREAS WITH MODEL CALLS: " + (", ".join(tr.areas_with_calls()) or "none"))
    return 0 if (res.get("noop") or res["validation"].get("valid")) else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
