from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_arena.benchmark import (
    BenchmarkResult,
    _seeded_agent_factory,
    format_summary,
    run_benchmark,
)
from ai_arena.cli import main
from ai_arena.tournament import _game_factory


def _ttt() -> "callable":
    return _game_factory("tictactoe")


def _counts(r: BenchmarkResult) -> tuple[int, int, int]:
    return (r.a_wins, r.b_wins, r.draws)


# --- core outcome attribution ---------------------------------------------


def test_search_never_loses_to_random_on_tictactoe() -> None:
    # search plays tic-tac-toe perfectly, so over many seeded games random
    # never wins; every game is a search win or a draw.
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("search"),
        b_factory=_seeded_agent_factory("random"),
        a_label="search",
        b_label="random",
        games=24,
        base_seed=0,
    )
    assert r.games == 24
    assert r.b_wins == 0  # random never beats perfect play
    assert r.a_wins + r.draws == 24
    assert r.a_forfeits == 0 and r.b_forfeits == 0
    assert set(r.reason_counts) <= {"win", "draw"}
    assert sum(r.reason_counts.values()) == 24


def test_two_searchers_always_draw_tictactoe() -> None:
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("search"),
        b_factory=_seeded_agent_factory("search"),
        a_label="search",
        b_label="search",
        games=10,
        base_seed=1,
    )
    assert _counts(r) == (0, 0, 10)
    assert r.reason_counts == {"draw": 10}


def test_outcomes_partition_every_game() -> None:
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("greedy"),
        b_factory=_seeded_agent_factory("random"),
        a_label="greedy",
        b_label="random",
        games=40,
        base_seed=3,
    )
    assert r.a_wins + r.b_wins + r.draws == r.games == 40
    assert sum(r.reason_counts.values()) == 40


# --- reproducibility -------------------------------------------------------


def test_same_seed_reproduces_discrete_counts() -> None:
    def go() -> BenchmarkResult:
        return run_benchmark(
            game_factory=_ttt(),
            a_factory=_seeded_agent_factory("greedy"),
            b_factory=_seeded_agent_factory("random"),
            a_label="greedy",
            b_label="random",
            games=20,
            base_seed=7,
        )

    r1, r2 = go(), go()
    # Timing (ms) is wall-clock and will differ; the discrete outcome must not.
    assert _counts(r1) == _counts(r2)
    assert r1.reason_counts == r2.reason_counts
    assert (r1.a_forfeits, r1.b_forfeits) == (r2.a_forfeits, r2.b_forfeits)


# --- seat swapping ---------------------------------------------------------


def _first_mover_wins_game(tmp_path: Path) -> str:
    """A trivial game the seat-0 player wins on its first (only) move."""
    g = tmp_path / "fmw.py"
    g.write_text(
        "from ai_arena.game import Terminal\n"
        "\n"
        "class FirstMoverWins:\n"
        "    name = 'fmw'\n"
        "    def initial_state(self):\n"
        "        return {'done': False, 'winner': None}\n"
        "    def legal_moves(self, state, player):\n"
        "        return [] if state['done'] else [0]\n"
        "    def apply_move(self, state, player, move):\n"
        "        return {'done': True, 'winner': player}\n"
        "    def terminal(self, state):\n"
        "        if state['done']:\n"
        "            return Terminal(True, state['winner'], 'first_mover')\n"
        "        return Terminal(False, None, '')\n"
        "    def render(self, state):\n"
        "        return str(state)\n",
        encoding="utf-8",
    )
    return str(g) + ":FirstMoverWins"


def test_swap_starts_alternates_the_first_mover(tmp_path: Path) -> None:
    spec = _first_mover_wins_game(tmp_path)
    kw = dict(
        game_factory=_game_factory(spec),
        a_factory=_seeded_agent_factory("random"),
        b_factory=_seeded_agent_factory("random"),
        a_label="a",
        b_label="b",
        games=10,
        base_seed=0,
    )

    # Without swapping, A is always seat 0 and wins every game.
    no_swap = run_benchmark(swap_starts=False, **kw)
    assert _counts(no_swap) == (10, 0, 0)

    # Swapping shares seat 0 evenly, so each contestant wins exactly half.
    swap = run_benchmark(swap_starts=True, **kw)
    assert _counts(swap) == (5, 5, 0)


# --- forfeit attribution ---------------------------------------------------


def test_illegal_move_forfeits_are_attributed_to_the_offender(tmp_path: Path) -> None:
    bad = tmp_path / "illegal_agent.py"
    bad.write_text(
        "class IllegalAgent:\n"
        "    name = 'illegal'\n"
        "    def select_move(self, game, state, player, legal_moves):\n"
        "        return -999  # never a legal tic-tac-toe cell\n",
        encoding="utf-8",
    )
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("random"),
        b_factory=_seeded_agent_factory(str(bad) + ":IllegalAgent"),
        a_label="random",
        b_label="illegal",
        games=8,
        base_seed=0,
    )
    # The offender loses every game by forfeit, in either seat.
    assert r.b_wins == 0
    assert r.a_wins == 8
    assert r.b_forfeits == 8
    assert r.a_forfeits == 0
    assert r.reason_counts == {"illegal_move": 8}


# --- guards / edge cases ---------------------------------------------------


def test_human_agent_is_rejected() -> None:
    with pytest.raises(ValueError, match="human"):
        _seeded_agent_factory("human")


def test_empty_subprocess_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="subprocess agent requires a command"):
        _seeded_agent_factory("subprocess:")


def test_zero_games_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        run_benchmark(
            game_factory=_ttt(),
            a_factory=_seeded_agent_factory("random"),
            b_factory=_seeded_agent_factory("random"),
            a_label="random",
            b_label="random",
            games=0,
        )


def test_keyboard_interrupt_returns_partial_result(tmp_path: Path) -> None:
    # Ctrl-C mid-run should yield a valid partial result, not a crash.
    kb = tmp_path / "kb_agent.py"
    kb.write_text(
        "class InterruptAgent:\n"
        "    name = 'kb'\n"
        "    def select_move(self, game, state, player, legal_moves):\n"
        "        raise KeyboardInterrupt\n",
        encoding="utf-8",
    )
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory(str(kb) + ":InterruptAgent"),
        b_factory=_seeded_agent_factory("random"),
        a_label="kb",
        b_label="random",
        games=5,
        base_seed=0,
    )
    assert r.incomplete is True
    assert r.games == 0  # interrupted before the first game could complete
    assert _counts(r) == (0, 0, 0)
    # Stats stay well-defined with no completed games.
    assert r.avg_turns == 0.0
    assert r.a_avg_ms == 0.0 and r.b_avg_ms == 0.0


def test_midrun_agent_spawn_failure_keeps_completed_games(tmp_path: Path, capsys) -> None:
    # A flaky agent that fails to *construct* on its third game must not discard
    # the two expensive games already completed (matches the repo's durability
    # ethos: a crash keeps everything up to the last good point).
    flaky = tmp_path / "flaky_agent.py"
    flaky.write_text(
        "_BUILDS = [0]\n"
        "\n"
        "class FlakyAgent:\n"
        "    name = 'flaky'\n"
        "    def __init__(self):\n"
        "        _BUILDS[0] += 1\n"
        "        if _BUILDS[0] >= 3:\n"
        "            raise RuntimeError('flaky spawn')\n"
        "    def select_move(self, game, state, player, legal_moves):\n"
        "        return legal_moves[0]\n",
        encoding="utf-8",
    )
    r = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("random"),
        b_factory=_seeded_agent_factory(str(flaky) + ":FlakyAgent"),
        a_label="random",
        b_label="flaky",
        games=10,
        base_seed=0,
    )
    assert r.incomplete is True
    assert r.games == 2  # two games completed before the third failed to spawn
    assert r.a_wins + r.b_wins + r.draws == 2
    assert "could not be run" in capsys.readouterr().err


# --- summary formatting ----------------------------------------------------


def test_format_summary_disambiguates_equal_labels() -> None:
    r = BenchmarkResult(
        game="tictactoe",
        a_label="greedy",
        b_label="greedy",
        games=4,
        a_wins=1,
        b_wins=1,
        draws=2,
        a_forfeits=0,
        b_forfeits=0,
        reason_counts={"win": 2, "draw": 2},
        avg_turns=8.0,
        a_avg_ms=0.1,
        a_max_ms=0.2,
        b_avg_ms=0.1,
        b_max_ms=0.2,
    )
    out = format_summary(r)
    assert "greedy (A): 1 wins" in out
    assert "greedy (B): 1 wins" in out
    assert "draws: 2 (50.0%)" in out


# --- CLI integration -------------------------------------------------------


def test_cli_benchmark_runs_and_reports(capsys) -> None:
    rc = main(
        ["benchmark", "tictactoe", "--p0", "search", "--p1", "random", "--games", "6", "--seed", "1", "--quiet"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "game: tictactoe" in out
    assert "games: 6 (completed)" in out
    assert "search:" in out and "random:" in out


def test_cli_benchmark_writes_out_file(tmp_path: Path, capsys) -> None:
    out = tmp_path / "bench.json"
    rc = main(
        [
            "benchmark",
            "tictactoe",
            "--p0",
            "greedy",
            "--p1",
            "random",
            "--games",
            "10",
            "--seed",
            "2",
            "--quiet",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["game"] == "tictactoe"
    assert data["games"] == 10
    assert data["a_wins"] + data["b_wins"] + data["draws"] == 10
    assert list(out.parent.glob("*.tmp")) == []  # atomic write leaves no temp


def test_cli_benchmark_rejects_nonpositive_games(capsys) -> None:
    rc = main(["benchmark", "tictactoe", "--p0", "random", "--p1", "random", "--games", "0"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "positive" in err


def test_tuned_agent_param_changes_strength() -> None:
    # The whole point of tunable specs: a shallower search is a weaker player.
    # Full-depth search never loses tic-tac-toe, so a depth-1 search cannot beat
    # it -- it only ever draws or loses. This pins that the parameter takes
    # effect end-to-end (resolver -> factory -> agent -> match).
    res = run_benchmark(
        game_factory=_ttt(),
        a_factory=_seeded_agent_factory("search:max_depth=1"),
        b_factory=_seeded_agent_factory("search"),
        a_label="search:max_depth=1",
        b_label="search",
        games=12,
        base_seed=1,
    )
    assert res.games == 12
    assert res.a_wins == 0  # the shallow searcher never beats the full-depth one
    assert res.b_wins > 0  # and loses some outright


def test_cli_benchmark_fails_fast_on_bad_agent_param(capsys) -> None:
    # A bad parameter is rejected at spec-resolution time, before any game runs.
    rc = main(["benchmark", "tictactoe", "--p0", "mcts:iterations=0", "--p1", "random", "--games", "5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "must be >= 1" in err
