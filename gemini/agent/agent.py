from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ai_arena.agents.subprocess_agent import SubprocessAgent
from ai_arena.game import Game, PlayerId
from ai_arena.json_types import JSONValue


@dataclass(slots=True)
class GeminiAgent:
    """
    Gemini-backed agent.

    Delegates to a JSONL subprocess bot that calls the Google Gemini API.
    """

    name: str = "gemini_subprocess_agent"
    model: str = "gemini-3-pro-preview"
    turn_timeout_s: float = 3600.0
    _delegate: SubprocessAgent = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.model = os.environ.get("GEMINI_ARENA_MODEL", self.model)
        override = os.environ.get("GEMINI_ARENA_COMMAND")
        if override:
            import shlex
            cmd = shlex.split(override)
        else:
            bot = Path(__file__).with_name("gemini_subprocess_bot.py")
            # Prefer the project venv Python so google.genai is available
            venv_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
            python = str(venv_python) if venv_python.exists() else sys.executable
            cmd = [
                python,
                "-u",
                str(bot),
                "--model",
                self.model,
            ]
        self._delegate = SubprocessAgent(command=cmd, name=self.name, timeout_s=self.turn_timeout_s)

    def select_move(
        self, game: Game, state: JSONValue, player: PlayerId, legal_moves: list[JSONValue]
    ) -> JSONValue:
        return self._delegate.select_move(game, state, player, legal_moves)

    def close(self) -> None:
        self._delegate.close()
