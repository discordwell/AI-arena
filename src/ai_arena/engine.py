from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .game import Game, PlayerId, Terminal
from .json_types import JSONValue


def _is_prime(n: int) -> bool:
    if n <= 1:
        return False
    if n <= 3:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


@dataclass(frozen=True, slots=True)
class MoveRecord:
    turn: int
    player: PlayerId
    move: JSONValue
    ms: float
    note: str | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    game: str
    winner: PlayerId | None
    reason: str
    turns: int
    move_history: list[MoveRecord]


def play_match(
    game: Game,
    agent0: Any,
    agent1: Any,
    *,
    max_turns: int = 10_000,
    prime_pause: bool = False,
    log_path: Path | None = None,
) -> MatchResult:
    """
    Run a 2-player, alternating-turn match.

    Agents must implement:
      - name: str
      - select_move(game, state, player, legal_moves) -> JSONValue
    """
    state: JSONValue = game.initial_state()
    history: list[MoveRecord] = []
    player: PlayerId = 0

    for turn in range(1, max_turns + 1):
        terminal: Terminal = game.terminal(state)
        if terminal.is_terminal:
            result = MatchResult(
                game=game.name,
                winner=terminal.winner,
                reason=terminal.reason,
                turns=turn - 1,
                move_history=history,
            )
            if log_path:
                _write_log(log_path, game, result, state)
            return result

        agent = agent0 if player == 0 else agent1
        legal = game.legal_moves(state, player)
        if not legal:
            result = MatchResult(
                game=game.name,
                winner=1 - player,
                reason="no_legal_moves",
                turns=turn - 1,
                move_history=history,
            )
            if log_path:
                _write_log(log_path, game, result, state)
            return result

        t0 = time.perf_counter()
        move = agent.select_move(game, state, player, legal)
        ms = (time.perf_counter() - t0) * 1000.0

        if move not in legal:
            history.append(MoveRecord(turn=turn, player=player, move=move, ms=ms, note="illegal_move"))
            result = MatchResult(
                game=game.name,
                winner=1 - player,
                reason="illegal_move",
                turns=turn,
                move_history=history,
            )
            if log_path:
                _write_log(log_path, game, result, state)
            return result

        state = game.apply_move(state, player, move)
        history.append(MoveRecord(turn=turn, player=player, move=move, ms=ms))

        if prime_pause and _is_prime(turn):
            print(f"[prime turn {turn}] extra analysis/coding cycle pause; press Enter to continue...")
            try:
                input()
            except EOFError:
                pass

        player = 1 - player

    result = MatchResult(game=game.name, winner=None, reason="max_turns", turns=max_turns, move_history=history)
    if log_path:
        _write_log(log_path, game, result, state)
    return result


def _write_log(path: Path, game: Game, result: MatchResult, final_state: JSONValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "game": game.name,
        "result": {
            **asdict(result),
            "move_history": [asdict(r) for r in result.move_history],
        },
        "final_state": final_state,
        "final_render": game.render(final_state),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

