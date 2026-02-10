from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

from .engine import play_match
from .games.tictactoe import TicTacToe
from .loading import load_symbol


def _builtin_games() -> dict[str, Any]:
    return {
        "tictactoe": TicTacToe(),
    }


def _load_game(spec: str) -> Any:
    builtins = _builtin_games()
    if spec in builtins:
        return builtins[spec]
    obj = load_symbol(spec)
    return obj() if callable(obj) else obj


def _load_agent(spec: str) -> Any:
    if spec == "human":
        from .agents.human import HumanAgent

        return HumanAgent()
    if spec == "random":
        from .agents.random_agent import RandomAgent

        return RandomAgent()
    if spec.startswith("subprocess:"):
        from .agents.subprocess_agent import SubprocessAgent

        cmd = shlex.split(spec.removeprefix("subprocess:").strip())
        if not cmd:
            raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
        return SubprocessAgent(cmd)

    obj = load_symbol(spec)
    return obj() if callable(obj) else obj


def cmd_list_games(_: argparse.Namespace) -> int:
    for name in sorted(_builtin_games().keys()):
        print(name)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    game = _load_game(args.game)
    a0 = _load_agent(args.p0)
    a1 = _load_agent(args.p1)

    log_path = Path(args.log).expanduser().resolve() if args.log else None
    result = play_match(game, a0, a1, prime_pause=args.prime_pause, log_path=log_path)

    print(f"game: {result.game}")
    print(f"winner: {result.winner}")
    print(f"reason: {result.reason}")
    print(f"turns: {result.turns}")
    if log_path:
        print(f"log: {log_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-arena")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-games", help="List built-in games")
    p_list.set_defaults(func=cmd_list_games)

    p_play = sub.add_parser("play", help="Play a match")
    p_play.add_argument("game", help="Built-in name (e.g. tictactoe) or '<path>:<symbol>'")
    p_play.add_argument("--p0", default="human", help="Agent0: human|random|subprocess:<cmd>|<path>:<symbol>")
    p_play.add_argument("--p1", default="random", help="Agent1: human|random|subprocess:<cmd>|<path>:<symbol>")
    p_play.add_argument("--prime-pause", action="store_true", help="Pause after prime-numbered turns")
    p_play.add_argument("--log", help="Write JSON match log to this path")
    p_play.set_defaults(func=cmd_play)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

