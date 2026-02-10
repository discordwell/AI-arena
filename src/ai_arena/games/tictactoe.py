from __future__ import annotations

from dataclasses import dataclass

from ..game import PlayerId, Terminal
from ..json_types import JSONValue


def _winner(board: list[int]) -> PlayerId | None:
    lines = [
        (0, 1, 2),
        (3, 4, 5),
        (6, 7, 8),
        (0, 3, 6),
        (1, 4, 7),
        (2, 5, 8),
        (0, 4, 8),
        (2, 4, 6),
    ]
    for a, b, c in lines:
        v = board[a]
        if v != 0 and v == board[b] and v == board[c]:
            return 0 if v == 1 else 1
    return None


@dataclass(slots=True)
class TicTacToe:
    name: str = "tictactoe"

    def initial_state(self) -> JSONValue:
        return {"board": [0] * 9}

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]:
        board = list(state["board"])  # type: ignore[index]
        return [i for i, v in enumerate(board) if v == 0]

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue:
        if not isinstance(move, int):
            raise ValueError(f"move must be int, got: {move!r}")
        board = list(state["board"])  # type: ignore[index]
        if not (0 <= move < 9) or board[move] != 0:
            raise ValueError(f"illegal move: {move!r}")
        board[move] = 1 if player == 0 else 2
        return {"board": board}

    def terminal(self, state: JSONValue) -> Terminal:
        board = list(state["board"])  # type: ignore[index]
        w = _winner(board)
        if w is not None:
            return Terminal(is_terminal=True, winner=w, reason="win")
        if all(v != 0 for v in board):
            return Terminal(is_terminal=True, winner=None, reason="draw")
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state: JSONValue) -> str:
        board = list(state["board"])  # type: ignore[index]
        glyph = {0: ".", 1: "X", 2: "O"}
        rows = []
        for r in range(3):
            rows.append(" ".join(glyph[board[3 * r + c]] for c in range(3)))
        return "\n".join(rows)

