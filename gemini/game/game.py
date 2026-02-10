from __future__ import annotations

from dataclasses import dataclass

from ai_arena.game import PlayerId, Terminal
from ai_arena.json_types import JSONValue


@dataclass(slots=True)
class GeminiGame:
    name: str = "gemini_game"

    def initial_state(self) -> JSONValue:
        raise NotImplementedError

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]:
        raise NotImplementedError

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue:
        raise NotImplementedError

    def terminal(self, state: JSONValue) -> Terminal:
        raise NotImplementedError

    def render(self, state: JSONValue) -> str:
        return "<unimplemented>"

