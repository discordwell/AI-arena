from __future__ import annotations

import sys
from pathlib import Path

from ai_arena.agents.subprocess_agent import SubprocessAgent
from ai_arena.games.tictactoe import TicTacToe


def test_subprocess_agent_select_move(tmp_path: Path) -> None:
    bot = tmp_path / "bot.py"
    bot.write_text(
        "\n".join(
            [
                "import json, sys",
                "for line in sys.stdin:",
                "    msg = json.loads(line)",
                "    if msg.get('type') != 'turn':",
                "        continue",
                "    legal = msg.get('legal_moves', [])",
                "    move = legal[0] if legal else None",
                "    sys.stdout.write(json.dumps({'type':'move','move':move}) + '\\n')",
                "    sys.stdout.flush()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    agent = SubprocessAgent(command=[sys.executable, "-u", str(bot)], timeout_s=5.0)
    try:
        game = TicTacToe()
        state = game.initial_state()
        legal = game.legal_moves(state, 0)
        move = agent.select_move(game, state, 0, legal)
        assert move == legal[0]
    finally:
        agent.close()

