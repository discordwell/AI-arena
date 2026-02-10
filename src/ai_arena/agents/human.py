from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..game import Game, PlayerId
from ..json_types import JSONValue


@dataclass(slots=True)
class HumanAgent:
    name: str = "human"

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        print(game.render(state))
        print(f"player: {player}")
        print("legal moves:")
        for i, m in enumerate(legal_moves):
            print(f"  [{i}] {m}")

        while True:
            raw = input("choose move index> ").strip()
            try:
                idx = int(raw)
            except ValueError:
                print("enter a number")
                continue
            if 0 <= idx < len(legal_moves):
                return legal_moves[idx]
            print("out of range")

