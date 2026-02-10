from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .json_types import JSONValue

PlayerId = int  # 0 or 1


@dataclass(frozen=True, slots=True)
class Terminal:
    is_terminal: bool
    winner: PlayerId | None  # None == draw / no winner
    reason: str


@runtime_checkable
class Game(Protocol):
    name: str

    def initial_state(self) -> JSONValue: ...

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]: ...

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue: ...

    def terminal(self, state: JSONValue) -> Terminal: ...

    def render(self, state: JSONValue) -> str: ...

