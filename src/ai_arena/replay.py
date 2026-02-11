from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .game import Game, PlayerId, Terminal
from .json_types import JSONValue


@dataclass(frozen=True, slots=True)
class ReplayMove:
    turn: int
    player: PlayerId
    move: JSONValue
    ms: float | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Replay:
    """
    states[0] is the initial state; states[i+1] is after applying moves[i].
    """

    game: str
    moves: list[ReplayMove]
    states: list[JSONValue]
    terminal: Terminal


def load_match_log(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def replay_from_move_history(game: Game, move_history: list[dict[str, Any]]) -> Replay:
    moves: list[ReplayMove] = []
    for r in move_history:
        moves.append(
            ReplayMove(
                turn=int(r["turn"]),
                player=int(r["player"]),
                move=r["move"],
                ms=(float(r["ms"]) if r.get("ms") is not None else None),
                note=(str(r["note"]) if r.get("note") is not None else None),
            )
        )

    states: list[JSONValue] = [game.initial_state()]
    for m in moves:
        if m.note is None:
            states.append(game.apply_move(states[-1], m.player, m.move))
            continue

        # Engine convention: a non-None note indicates the move was not applied
        # (illegal_move / timeout / agent_error). Keep state unchanged and stop.
        states.append(states[-1])
        break

    terminal = game.terminal(states[-1])
    return Replay(game=game.name, moves=moves, states=states, terminal=terminal)


def replay_from_log_payload(game: Game, payload: dict[str, Any]) -> Replay:
    """
    Prefer this when you have the full engine log payload, since the payload contains
    terminal reason/winner for forfeits (illegal move / timeout / agent error) where
    the game rules may not mark the state as terminal.
    """

    res = payload.get("result", {})
    mh = res.get("move_history", [])
    if not isinstance(mh, list):
        raise ValueError("payload.result.move_history must be a list")

    rep = replay_from_move_history(game, mh)

    # If the game rules consider the last state non-terminal, fall back to the engine's result.
    if not rep.terminal.is_terminal:
        reason = res.get("reason", "")
        winner = res.get("winner", None)
        if isinstance(reason, str) and reason:
            w: PlayerId | None
            if winner is None:
                w = None
            elif isinstance(winner, int) and winner in (0, 1):
                w = int(winner)
            else:
                w = None
            return Replay(game=rep.game, moves=rep.moves, states=rep.states, terminal=Terminal(True, w, reason))

    return rep
