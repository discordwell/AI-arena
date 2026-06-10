from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_arena.agents.subprocess_agent import SubprocessAgent
from ai_arena.games.tictactoe import TicTacToe


def _make_agent(tmp_path: Path, bot_src: str, *, timeout_s: float = 10.0) -> SubprocessAgent:
    bot = tmp_path / "bot.py"
    bot.write_text(bot_src, encoding="utf-8")
    return SubprocessAgent(command=[sys.executable, "-u", str(bot)], timeout_s=timeout_s)


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


def test_subprocess_agent_chatty_stderr_does_not_deadlock(tmp_path: Path) -> None:
    # A bot that floods stderr well past the OS pipe buffer (~64 KiB) before
    # answering. Without a background drain, the bot blocks on its stderr
    # write, never sends the move, and the turn times out.
    bot_src = "\n".join(
        [
            "import json, sys",
            "for line in sys.stdin:",
            "    msg = json.loads(line)",
            "    if msg.get('type') != 'turn':",
            "        continue",
            "    sys.stderr.write('spam\\n' * 200000)",  # ~1 MiB
            "    sys.stderr.flush()",
            "    move = msg['legal_moves'][0]",
            "    sys.stdout.write(json.dumps({'type': 'move', 'move': move}) + '\\n')",
            "    sys.stdout.flush()",
        ]
    )
    agent = _make_agent(tmp_path, bot_src, timeout_s=15.0)
    try:
        game = TicTacToe()
        state = game.initial_state()
        legal = game.legal_moves(state, 0)
        assert agent.select_move(game, state, 0, legal) == legal[0]
    finally:
        agent.close()


def test_subprocess_agent_surfaces_stderr_when_bot_dies_mid_turn(tmp_path: Path) -> None:
    bot_src = "\n".join(
        [
            "import sys",
            "sys.stdin.readline()",
            "print('boom: missing API key', file=sys.stderr, flush=True)",
            "sys.exit(3)",
        ]
    )
    agent = _make_agent(tmp_path, bot_src)
    try:
        game = TicTacToe()
        state = game.initial_state()
        legal = game.legal_moves(state, 0)
        with pytest.raises(RuntimeError) as ei:
            agent.select_move(game, state, 0, legal)
        assert "boom: missing API key" in str(ei.value)
    finally:
        agent.close()


def test_subprocess_agent_reports_startup_crash_with_stderr(tmp_path: Path) -> None:
    bot_src = "\n".join(
        [
            "import sys",
            "print('ImportError: no module named some_llm_sdk', file=sys.stderr, flush=True)",
            "sys.exit(1)",
        ]
    )
    agent = _make_agent(tmp_path, bot_src)
    try:
        agent._proc.wait(timeout=5)  # ensure the exited-at-entry path triggers
        game = TicTacToe()
        state = game.initial_state()
        legal = game.legal_moves(state, 0)
        with pytest.raises(RuntimeError) as ei:
            agent.select_move(game, state, 0, legal)
        msg = str(ei.value)
        assert "exited with code 1" in msg
        assert "ImportError: no module named some_llm_sdk" in msg
    finally:
        agent.close()

