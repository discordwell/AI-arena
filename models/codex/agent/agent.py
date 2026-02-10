from __future__ import annotations

from dataclasses import dataclass

from ai_arena.game import Game, PlayerId
from ai_arena.json_types import JSONValue


@dataclass(slots=True)
class CodexAgent:
    name: str = "codex_agent"

    def select_move(
        self, game: Game, state: JSONValue, player: PlayerId, legal_moves: list[JSONValue]
    ) -> JSONValue:
        return legal_moves[0]

