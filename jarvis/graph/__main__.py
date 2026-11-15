"""CLI for the JARVIS memory graph:  python -m jarvis.graph {stats|recall <term>|export [path]}"""
from __future__ import annotations

import json
import sys

from .store import graph


def main(argv: list[str]) -> int:
    g = graph()
    cmd = argv[0] if argv else "stats"
    if cmd == "stats":
        print(json.dumps(g.stats(), indent=2))
    elif cmd == "recall":
        term = " ".join(argv[1:])
        for h in g.recall(term):
            print(f"[{h['type']}] {h.get('label', h['key'])}  · {h['links']} links · seen {h['count']}x")
    elif cmd == "export":
        path = g.export_graphify(argv[1] if len(argv) > 1 else None)
        print(f"exported graphify graph.json -> {path}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
