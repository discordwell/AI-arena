from __future__ import annotations

import random
from dataclasses import dataclass

from ..game import Game, PlayerId
from ..json_types import JSONValue


@dataclass(slots=True)
class RandomAgent:
    name: str = "random"

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        return random.choice(legal_moves)

