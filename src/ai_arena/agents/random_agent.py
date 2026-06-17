from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..game import Game, PlayerId
from ..json_types import JSONValue


@dataclass(slots=True)
class RandomAgent:
    name: str = "random"
    # seed=None draws from system entropy (nondeterministic, the default);
    # pass an int for a reproducible match.
    seed: int | None = None
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        return self._rng.choice(legal_moves)

