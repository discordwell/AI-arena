from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ai_arena.agents.subprocess_agent import SubprocessAgent
from ai_arena.game import Game, PlayerId
from ai_arena.json_types import JSONValue


@dataclass(slots=True)
class CodexAgent:
    """
    Codex-backed agent.

    This wrapper delegates to a JSONL subprocess bot that calls `codex exec`
    using GPT-5.3 Codex at extra-high reasoning by default.
    """

    name: str = "codex_subprocess_agent"
    model: str = "gpt-5.3-codex"
    reasoning_effort: str = "xhigh"
    turn_timeout_s: float = 3600.0
    _delegate: SubprocessAgent = field(init=False, repr=False)

    def __post_init__(self) -> None:
        override = os.environ.get("CODEX_ARENA_COMMAND")
        if override:
            cmd = shlex.split(override)
        else:
            bot = Path(__file__).with_name("codex_subprocess_bot.py")
            cmd = [
                sys.executable,
                "-u",
                str(bot),
                "--model",
                self.model,
                "--reasoning-effort",
                self.reasoning_effort,
            ]
        self._delegate = SubprocessAgent(command=cmd, name=self.name, timeout_s=self.turn_timeout_s)

    def select_move(
        self, game: Game, state: JSONValue, player: PlayerId, legal_moves: list[JSONValue]
    ) -> JSONValue:
        return self._delegate.select_move(game, state, player, legal_moves)

    def close(self) -> None:
        self._delegate.close()
