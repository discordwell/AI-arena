from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_arena.cli import main


def test_list_games(capsys) -> None:
    assert main(["list-games"]) == 0
    out = capsys.readouterr().out
    assert "tictactoe" in out.splitlines()


def test_list_agents_lists_loadable_builtins(capsys) -> None:
    # Guards against drift between the advertised list and the loader branches:
    # every name `list-agents` prints must actually load a usable agent.
    from ai_arena.cli import _BUILTIN_AGENTS, _load_agent

    assert main(["list-agents"]) == 0
    assert capsys.readouterr().out.split() == list(_BUILTIN_AGENTS)
    for name in _BUILTIN_AGENTS:
        agent = _load_agent(name)
        assert callable(getattr(agent, "select_move", None))
        assert isinstance(agent.name, str)


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


def test_play_rejects_empty_subprocess_command(capsys) -> None:
    rc = main(["play", "tictactoe", "--p0", "subprocess:", "--p1", "random"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "subprocess agent requires a command" in err


def test_replay_summarizes_and_renders_log(tmp_path: Path, capsys) -> None:
    # The whole point of the durable match log is to be readable later; replay
    # round-trips a log the engine wrote and reconstructs the same outcome.
    log = tmp_path / "match.json"
    assert main(["play", "tictactoe", "--p0", "search", "--p1", "random", "--seed", "7", "--log", str(log)]) == 0
    capsys.readouterr()  # discard the play output

    assert main(["replay", str(log)]) == 0
    out = capsys.readouterr().out
    assert "game: tictactoe (replayed)" in out
    assert "winner:" in out
    assert "reason:" in out
    assert "final board:" in out


def test_replay_lists_moves_and_frames(tmp_path: Path, capsys) -> None:
    log = tmp_path / "match.json"
    assert main(["play", "tictactoe", "--p0", "search", "--p1", "random", "--seed", "7", "--log", str(log)]) == 0
    capsys.readouterr()

    assert main(["replay", str(log), "--moves", "--frames"]) == 0
    out = capsys.readouterr().out
    assert "moves:" in out
    assert "--- frame 0 ---" in out  # initial state is always frame 0
    # One frame per state: initial + one per applied move.
    payload = json.loads(log.read_text(encoding="utf-8"))
    applied = sum(1 for m in payload["result"]["move_history"] if m["note"] is None)
    assert out.count("--- frame ") == applied + 1


def test_replay_falls_back_to_stored_log_when_game_unknown(tmp_path: Path, capsys) -> None:
    # A log of a game not present in this repo (or with an unknown name) still
    # summarizes from the stored JSON alone, without a loadable game.
    log = tmp_path / "mystery.json"
    log.write_text(
        json.dumps(
            {
                "game": "mystery",
                "result": {
                    "game": "mystery",
                    "winner": 1,
                    "reason": "flipout",
                    "turns": 2,
                    "move_history": [
                        {"turn": 1, "player": 0, "move": "a", "ms": 5.0, "note": None},
                        {"turn": 2, "player": 1, "move": "b", "ms": 6.0, "note": None},
                    ],
                },
                "final_render": "<<final board art>>",
            }
        ),
        encoding="utf-8",
    )

    assert main(["replay", str(log), "--moves", "--frames"]) == 0
    captured = capsys.readouterr()
    assert "could not infer the game" in captured.err  # warns it is degraded
    assert "game: mystery (from log)" in captured.out
    assert "winner: 1" in captured.out
    assert "<<final board art>>" in captured.out  # stored render shown verbatim
    assert "frames unavailable" in captured.out  # frames need a loadable game


def test_replay_with_explicit_game_spec(tmp_path: Path, capsys) -> None:
    # An explicit --game overrides inference, so a path-loaded game replays even
    # when its name is not one the inferrer knows.
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
        "        return f\"n={state['n']}\"\n",
        encoding="utf-8",
    )
    spec = str(game) + ":MiniGame"
    log = tmp_path / "mini.json"
    assert main(["play", spec, "--p0", "random", "--p1", "random", "--log", str(log)]) == 0
    capsys.readouterr()

    assert main(["replay", str(log), "--game", spec, "--frames"]) == 0
    out = capsys.readouterr().out
    assert "game: mini (replayed)" in out
    assert "n=3" in out  # rendered from the reconstructed terminal state


def test_replay_errors_cleanly_on_missing_log(tmp_path: Path, capsys) -> None:
    assert main(["replay", str(tmp_path / "nope.json")]) == 1
    assert "could not read match log" in capsys.readouterr().err


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


def test_standings_round_trips_a_tournament_results_file(tmp_path: Path, capsys) -> None:
    # The durable results.json should be readable back into a leaderboard later
    # (the headless reader for tournament artifacts, mirroring `replay`).
    cfg = tmp_path / "arena.toml"
    out = tmp_path / "results.json"
    cfg.write_text(
        'neutral_game = "tictactoe"\n'
        "rounds = 1\n"
        "swap_starts = true\n"
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
    assert main(["tournament", "--config", str(cfg), "--out", str(out)]) == 0
    capsys.readouterr()  # discard the tournament output

    assert main(["standings", str(out), "--by-context", "--matches"]) == 0
    text = capsys.readouterr().out
    assert "standings (" in text
    assert "head-to-head" in text
    assert "home vs away" in text  # --by-context
    assert "matches (" in text  # --matches
    # Both competitors appear in the leaderboard.
    assert "a" in text and "b" in text


def test_standings_errors_cleanly_on_missing_file(tmp_path: Path, capsys) -> None:
    assert main(["standings", str(tmp_path / "nope.json")]) == 1
    assert "could not read results file" in capsys.readouterr().err


def test_standings_rejects_non_object_json(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "arr.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert main(["standings", str(bad)]) == 1
    assert "is not a JSON object" in capsys.readouterr().err


def test_play_accepts_tuned_builtin_agent_spec(tmp_path: Path, capsys) -> None:
    # A parametrized built-in spec (name:knob=value) drives a real match.
    log = tmp_path / "tuned.json"
    rc = main(
        ["play", "tictactoe", "--p0", "search:max_depth=2", "--p1", "random", "--seed", "1", "--log", str(log)]
    )
    assert rc == 0
    assert "game: tictactoe" in capsys.readouterr().out
    assert json.loads(log.read_text(encoding="utf-8"))["result"]["reason"] in {"win", "draw"}


def test_play_rejects_bad_agent_param(capsys) -> None:
    rc = main(["play", "tictactoe", "--p0", "search:max_depth=deep", "--p1", "random"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "must be int" in err


# ---------------------------------------------------------------------------
# The main() error boundary: a bad spec/config anywhere in the CLI exits with a
# one-line `error:` message (rc 2) instead of a traceback, matching the clean
# error paths the check-*/replay/standings commands already had; Ctrl-C exits
# 130 and a broken stdout pipe exits 141, both silent successes downstream of
# a shell convention. AI_ARENA_DEBUG=1 restores the traceback for debugging.
# ---------------------------------------------------------------------------


def test_cli_reports_unloadable_game_spec_cleanly(tmp_path: Path, capsys) -> None:
    rc = main(["play", str(tmp_path / "missing.py") + ":Nope", "--p0", "random", "--p1", "random"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error: FileNotFoundError")
    assert "missing.py" in err
    assert "Traceback" not in err


def test_cli_reports_missing_tournament_config_cleanly(tmp_path: Path, capsys) -> None:
    rc = main(["tournament", "--config", str(tmp_path / "nope.toml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error: FileNotFoundError")
    assert "Traceback" not in err


def test_cli_interrupt_exits_130(monkeypatch, capsys) -> None:
    def interrupted(_: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("ai_arena.cli.cmd_list_games", interrupted)
    assert main(["list-games"]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_cli_broken_pipe_exits_141_silently(monkeypatch, capsys) -> None:
    def gone_reader(_: object) -> int:
        raise BrokenPipeError

    monkeypatch.setattr("ai_arena.cli.cmd_list_games", gone_reader)
    assert main(["list-games"]) == 141
    captured = capsys.readouterr()
    assert captured.err == ""  # a closed pipe is the reader's choice, not an error


def test_cli_debug_env_var_restores_the_traceback(monkeypatch) -> None:
    monkeypatch.setenv("AI_ARENA_DEBUG", "1")
    with pytest.raises(ValueError, match="must be int"):
        main(["play", "tictactoe", "--p0", "search:max_depth=deep", "--p1", "random"])
