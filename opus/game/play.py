#!/usr/bin/env python3
"""CLI launcher for the Caldera GUI.

Usage:
    python opus/game/play.py                          # Human (P0) vs Random (P1)
    python opus/game/play.py --p1 opus                # Human vs Claude
    python opus/game/play.py --p0 random --p1 random  # Watch AI vs AI
    python opus/game/play.py --replay log.json        # Replay a saved match
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the project root is on sys.path so `opus.game.game` resolves.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Caldera \u2014 Opus Arena GUI")
    parser.add_argument(
        "--p0", default="human", choices=["human", "random", "opus"],
        help="Player 0 controller (default: human)",
    )
    parser.add_argument(
        "--p1", default="random", choices=["human", "random", "opus"],
        help="Player 1 controller (default: random)",
    )
    parser.add_argument(
        "--replay", metavar="LOG", default=None,
        help="Path to a match log JSON file to replay",
    )
    args = parser.parse_args()

    from opus.game.gui import CalderaApp

    if args.replay:
        app = CalderaApp(agents={}, replay_path=args.replay)
    else:
        app = CalderaApp(agents={0: args.p0, 1: args.p1})

    app.run()


if __name__ == "__main__":
    main()
