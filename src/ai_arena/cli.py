from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from .benchmark import load_benchmark_parser, load_round_robin_parser
from .engine import play_match
from .games.tictactoe import TicTacToe
from .loading import load_symbol
from .replay import Replay, infer_game_spec_from_log, load_match_log, replay_from_log_payload
from .tournament import load_standings_parser, load_tournament_parser


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


def _load_agent(spec: str, *, seed: int | None = None) -> Any:
    if spec == "human":
        from .agents.human import HumanAgent

        return HumanAgent()
    if spec == "random":
        from .agents.random_agent import RandomAgent

        return RandomAgent(seed=seed)
    if spec == "greedy":
        from .agents.greedy import GreedyAgent

        return GreedyAgent(seed=seed)
    if spec == "search":
        from .agents.search import SearchAgent

        return SearchAgent(seed=seed)
    if spec == "mcts":
        from .agents.mcts import MctsAgent

        return MctsAgent(seed=seed)
    if spec.startswith("subprocess:"):
        from .agents.subprocess_agent import SubprocessAgent

        cmd = shlex.split(spec.removeprefix("subprocess:").strip())
        if not cmd:
            raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
        return SubprocessAgent(cmd)

    obj = load_symbol(spec)
    return obj() if callable(obj) else obj


# Built-in agents selectable by name (besides path/subprocess specs).
_BUILTIN_AGENTS = ("greedy", "human", "mcts", "random", "search")


def cmd_list_games(_: argparse.Namespace) -> int:
    for name in sorted(_builtin_games().keys()):
        print(name)
    return 0


def cmd_list_agents(_: argparse.Namespace) -> int:
    for name in _BUILTIN_AGENTS:
        print(name)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    game = _load_game(args.game)
    # Give the two seats distinct seeds so two seeded random/greedy agents do
    # not draw from the same sequence; the whole match stays reproducible.
    seed = args.seed
    a0 = _load_agent(args.p0, seed=seed)
    a1 = _load_agent(args.p1, seed=(None if seed is None else seed + 1))

    log_path = Path(args.log).expanduser().resolve() if args.log else None
    try:
        result = play_match(game, a0, a1, prime_pause=args.prime_pause, log_path=log_path)

        print(f"game: {result.game}")
        print(f"winner: {result.winner}")
        print(f"reason: {result.reason}")
        print(f"turns: {result.turns}")
        if log_path:
            print(f"log: {log_path}")
        return 0
    finally:
        for a in (a0, a1):
            close = getattr(a, "close", None)
            if callable(close):
                close()


def cmd_gui(args: argparse.Namespace) -> int:
    # Import lazily so the rest of the CLI works in environments without Tkinter.
    from .gui import launch_gui

    return launch_gui(args)


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=repr)


def _format_replay(
    payload: dict[str, Any],
    rep: Replay | None,
    game: Any,
    *,
    show_moves: bool,
    show_frames: bool,
) -> str:
    """
    Render a match-log replay as text.

    When ``rep``/``game`` are present the summary is the *replayed* outcome
    (states reconstructed from the move history and re-checked against the game
    rules, falling back to the engine-recorded result for forfeit endings); when
    the game could not be loaded it falls back to the result stored in the log,
    so a log of a game not present in this repo still summarizes from JSON alone.
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    result = result if isinstance(result, dict) else {}

    # rep and game are set/cleared together in cmd_replay, so one flag governs
    # whether we report the reconstructed-and-revalidated replay or fall back to
    # the result stored in the log.
    replayed = rep is not None and game is not None

    if replayed:
        game_name = rep.game
        winner = rep.terminal.winner
        reason = rep.terminal.reason
        turns = sum(1 for m in rep.moves if m.note is None)
        source = "replayed"
    else:
        game_name = str(payload.get("game") or result.get("game") or "unknown")
        winner = result.get("winner")
        reason = str(result.get("reason", ""))
        turns = result.get("turns")
        if not isinstance(turns, int):
            history = result.get("move_history", [])
            turns = sum(1 for m in history if isinstance(m, dict) and m.get("note") is None)
        source = "from log"

    lines = [
        f"game: {game_name} ({source})",
        f"winner: {winner}",
        f"reason: {reason}",
        f"turns: {turns}",
    ]

    if show_moves:
        history = result.get("move_history", [])
        lines.append(f"moves: {len(history)}")
        for m in history:
            if not isinstance(m, dict):
                continue
            ms = m.get("ms")
            ms_str = f"  {float(ms):.0f}ms" if isinstance(ms, (int, float)) else ""
            note = m.get("note")
            suffix = f"  [{note}]" if note else ""
            lines.append(f"  [{m.get('turn')}] p{m.get('player')}: {_compact(m.get('move'))}{ms_str}{suffix}")

    if show_frames:
        if not replayed:
            lines.append("(frames unavailable: could not load the game; pass --game <spec>)")
        else:
            for i, st in enumerate(rep.states):
                lines.append(f"--- frame {i} ---")
                lines.append(game.render(st))

    if replayed:
        lines.append("final board:")
        lines.append(game.render(rep.states[-1]))
    else:
        final_render = payload.get("final_render") if isinstance(payload, dict) else None
        if isinstance(final_render, str) and final_render:
            lines.append("final board:")
            lines.append(final_render)

    return "\n".join(lines)


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.log).expanduser().resolve()
    try:
        payload = load_match_log(path)
    except (OSError, ValueError) as e:  # missing file / unreadable / not JSON
        print(f"error: could not read match log {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(f"error: match log {path} is not a JSON object", file=sys.stderr)
        return 1

    spec = args.game or infer_game_spec_from_log(payload)
    game: Any = None
    rep: Replay | None = None
    if spec:
        try:
            game = _load_game(spec)
            rep = replay_from_log_payload(game, payload)
        except Exception as e:
            print(
                f"warning: could not replay with game spec {spec!r} "
                f"({type(e).__name__}: {e}); showing stored log data only",
                file=sys.stderr,
            )
            game = None
            rep = None
    else:
        print(
            "warning: could not infer the game for this log; showing stored log data only "
            "(pass --game <spec> for a full, validated replay)",
            file=sys.stderr,
        )

    print(_format_replay(payload, rep, game, show_moves=args.moves, show_frames=args.frames))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-arena")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-games", help="List built-in games")
    p_list.set_defaults(func=cmd_list_games)

    p_list_agents = sub.add_parser("list-agents", help="List built-in agents")
    p_list_agents.set_defaults(func=cmd_list_agents)

    p_play = sub.add_parser("play", help="Play a match")
    p_play.add_argument("game", help="Built-in name (e.g. tictactoe) or '<path>:<symbol>'")
    p_play.add_argument("--p0", default="human", help="Agent0: human|random|greedy|search|mcts|subprocess:<cmd>|<path>:<symbol>")
    p_play.add_argument("--p1", default="random", help="Agent1: human|random|greedy|search|mcts|subprocess:<cmd>|<path>:<symbol>")
    p_play.add_argument("--prime-pause", action="store_true", help="Pause after prime-numbered turns")
    p_play.add_argument("--log", help="Write JSON match log to this path")
    p_play.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed the built-in random/greedy/search/mcts agents for a reproducible match",
    )
    p_play.set_defaults(func=cmd_play)

    p_gui = sub.add_parser("gui", help="Launch a Tkinter GUI to play/watch matches or replay logs")
    p_gui.add_argument(
        "--game",
        default=None,
        help="Built-in name or '<path>:<symbol>' (for live matches; for --load-log, omit to infer when possible)",
    )
    p_gui.add_argument("--p0", default="human", help="Agent0: human|random|greedy|search|mcts|subprocess:<cmd>|<path>:<symbol>")
    p_gui.add_argument("--p1", default="random", help="Agent1: human|random|greedy|search|mcts|subprocess:<cmd>|<path>:<symbol>")
    p_gui.add_argument("--load-log", help="Open a JSON match log for replay")
    p_gui.add_argument("--save-log", help="Write a JSON match log here when the live match ends")
    p_gui.add_argument("--max-turns", type=int, default=10_000, help="Hard cap on turns for live play")
    p_gui.add_argument("--auto-delay-ms", type=int, default=250, help="Autoplay delay in milliseconds")
    p_gui.set_defaults(func=cmd_gui)

    p_replay = sub.add_parser("replay", help="Replay a match log to the terminal (headless; no GUI required)")
    p_replay.add_argument("log", help="Path to a JSON match log")
    p_replay.add_argument(
        "--game",
        default=None,
        help="Built-in name or '<path>:<symbol>' (omit to infer from the log when possible)",
    )
    p_replay.add_argument("--moves", action="store_true", help="List the move history")
    p_replay.add_argument(
        "--frames",
        action="store_true",
        help="Render the board at every frame (needs a loadable game)",
    )
    p_replay.set_defaults(func=cmd_replay)

    load_benchmark_parser(sub)
    load_round_robin_parser(sub)
    load_tournament_parser(sub)
    load_standings_parser(sub)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
