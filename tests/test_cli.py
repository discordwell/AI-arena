from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_arena.cli import main


def test_list_games(capsys) -> None:
    assert main(["list-games"]) == 0
    out = capsys.readouterr().out
    assert "tictactoe" in out.splitlines()


def test_play_reports_result_and_writes_log(tmp_path: Path, capsys) -> None:
    log = tmp_path / "logs" / "match.json"
    rc = main(["play", "tictactoe", "--p0", "random", "--p1", "random", "--log", str(log)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "game: tictactoe" in out

    payload = json.loads(log.read_text(encoding="utf-8"))
    reason = payload["result"]["reason"]
    assert reason in {"win", "draw"}
    assert f"reason: {reason}" in out  # printed result matches the log


def test_play_loads_game_from_path_spec(tmp_path: Path, capsys) -> None:
    # Games are loaded from standalone files, the way model folders ship them.
    game = tmp_path / "mini_game.py"
    game.write_text(
        "from ai_arena.game import Terminal\n"
        "\n"
        "class MiniGame:\n"
        "    name = 'mini'\n"
        "    def initial_state(self):\n"
        "        return {'n': 0}\n"
        "    def legal_moves(self, state, player):\n"
        "        return [1]\n"
        "    def apply_move(self, state, player, move):\n"
        "        return {'n': state['n'] + move}\n"
        "    def terminal(self, state):\n"
        "        if state['n'] >= 3:\n"
        "            return Terminal(True, 0, 'win')\n"
        "        return Terminal(False, None, '')\n"
        "    def render(self, state):\n"
        "        return str(state['n'])\n",
        encoding="utf-8",
    )

    rc = main(["play", str(game) + ":MiniGame", "--p0", "random", "--p1", "random"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "game: mini" in out
    assert "winner: 0" in out


def test_play_rejects_empty_subprocess_command() -> None:
    with pytest.raises(ValueError, match="subprocess agent requires a command"):
        main(["play", "tictactoe", "--p0", "subprocess:", "--p1", "random"])


def test_tournament_runs_config_and_writes_results(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "arena.toml"
    out = tmp_path / "results.json"
    cfg.write_text(
        'neutral_game = "tictactoe"\n'
        "rounds = 1\n"
        "swap_starts = false\n"
        "\n"
        "[[competitors]]\n"
        'id = "a"\n'
        'home_game = "tictactoe"\n'
        'agent = "random"\n'
        "\n"
        "[[competitors]]\n"
        'id = "b"\n'
        'home_game = "tictactoe"\n'
        'agent = "random"\n',
        encoding="utf-8",
    )

    rc = main(["tournament", "--config", str(cfg), "--out", str(out)])
    assert rc == 0

    stdout = capsys.readouterr().out
    assert "scoreboard:" in stdout

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["complete"] is True
    assert len(data["matches"]) == 3
    assert {m["context"] for m in data["matches"]} == {"home:a", "home:b", "neutral"}
