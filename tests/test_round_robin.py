"""Tests for the `round-robin` command (N-way agent leaderboard on one game)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_arena.benchmark import (
    BenchmarkResult,
    RoundRobinResult,
    RoundRobinRow,
    _seeded_agent_factory,
    compute_round_robin_standings,
    format_round_robin,
    run_benchmark,
    run_round_robin,
)
from ai_arena.cli import main
from ai_arena.tournament import _game_factory


def _ttt() -> "callable":
    return _game_factory("tictactoe")


def _f(spec: str):
    return _seeded_agent_factory(spec)


def _row(standings: list[RoundRobinRow], agent: str) -> RoundRobinRow:
    return next(r for r in standings if r.agent == agent)


def _bench(a: str, b: str, *, a_wins: int, b_wins: int, draws: int, a_ff: int = 0, b_ff: int = 0) -> BenchmarkResult:
    """A minimal BenchmarkResult for testing the pure aggregation."""
    return BenchmarkResult(
        game="g",
        a_label=a,
        b_label=b,
        games=a_wins + b_wins + draws,
        a_wins=a_wins,
        b_wins=b_wins,
        draws=draws,
        a_forfeits=a_ff,
        b_forfeits=b_ff,
        reason_counts={},
        avg_turns=0.0,
        a_avg_ms=0.0,
        a_max_ms=0.0,
        b_avg_ms=0.0,
        b_max_ms=0.0,
    )


# --- pure aggregation / scoring -------------------------------------------


def test_standings_scoring_and_records() -> None:
    # a beats b 3-1 (1 draw), a draws c 0-0 (5), b beats c 4-0.
    pairs = [
        _bench("a", "b", a_wins=3, b_wins=1, draws=1),
        _bench("a", "c", a_wins=0, b_wins=0, draws=5),
        _bench("b", "c", a_wins=4, b_wins=0, draws=0),
    ]
    rows = compute_round_robin_standings(["a", "b", "c"], pairs)

    a, b, c = _row(rows, "a"), _row(rows, "b"), _row(rows, "c")
    # a: 3W (vs b) + 0W (vs c), draws 1 + 5 = 6, losses 1 -> 3*3 + 6 = 15 pts
    assert (a.wins, a.draws, a.losses) == (3, 6, 1)
    assert a.points == 15
    # b: beat c 4-0, lost to a 1-3 -> 5 W, 1 D, 3 L -> 3*5 + 1 = 16 pts
    assert (b.wins, b.draws, b.losses) == (5, 1, 3)
    assert b.points == 16
    # c: 0 W, draws 5 (vs a), losses 4 (vs b) -> 5 pts
    assert (c.wins, c.draws, c.losses) == (0, 5, 4)
    assert c.points == 5

    # played = sum of games across an agent's pairings: a=5+5, b=5+4, c=5+4.
    assert (a.played, b.played, c.played) == (10, 9, 9)
    # Points-invariant: a row's points are exactly 3*wins + draws.
    assert all(r.points == 3 * r.wins + r.draws for r in rows)


def test_standings_ranked_by_points_then_label() -> None:
    # x and y both finish on 3 points (one win each); the tie breaks by label.
    pairs = [
        _bench("y", "x", a_wins=0, b_wins=1, draws=0),  # x beats y
        _bench("y", "z", a_wins=1, b_wins=0, draws=0),  # y beats z
        _bench("x", "z", a_wins=0, b_wins=0, draws=1),  # x draws z
    ]
    rows = compute_round_robin_standings(["x", "y", "z"], pairs)
    # x: 1W + 1D = 4 pts; y: 1W = 3 pts; z: 1D = 1 pt.
    assert [r.agent for r in rows] == ["x", "y", "z"]
    assert [r.points for r in rows] == [4, 3, 1]

    # A genuine points tie is broken alphabetically by label.
    tied = compute_round_robin_standings(
        ["beta", "alpha"], [_bench("alpha", "beta", a_wins=1, b_wins=1, draws=0)]
    )
    assert [r.agent for r in tied] == ["alpha", "beta"]
    assert tied[0].points == tied[1].points == 3


def test_forfeits_attributed_to_each_agent() -> None:
    pairs = [_bench("good", "bad", a_wins=5, b_wins=0, draws=0, b_ff=5)]
    rows = compute_round_robin_standings(["good", "bad"], pairs)
    assert _row(rows, "bad").forfeits == 5
    assert _row(rows, "good").forfeits == 0


def test_unknown_pair_labels_are_ignored() -> None:
    # A pairing referencing an agent not in the roster is skipped, not fatal.
    rows = compute_round_robin_standings(["a"], [_bench("a", "ghost", a_wins=1, b_wins=0, draws=0)])
    assert _row(rows, "a").wins == 1
    assert [r.agent for r in rows] == ["a"]


# --- integration: real matches --------------------------------------------


def test_round_robin_ranks_baselines_on_tictactoe() -> None:
    agents = [("random", _f("random")), ("greedy", _f("greedy")), ("search", _f("search"))]
    r = run_round_robin(game_factory=_ttt(), agents=agents, games=12, base_seed=1)

    assert not r.incomplete
    assert r.game == "tictactoe"
    assert len(r.pairs) == 3  # C(3,2)

    rows = {row.agent: row for row in r.standings}
    # search plays tic-tac-toe perfectly: it never loses, so it tops the table
    # and random (which never beats perfect/strong play) sits at the bottom.
    assert r.standings[0].agent == "search"
    assert rows["search"].losses == 0
    assert rows["random"].wins == 0
    assert rows["search"].points >= rows["greedy"].points >= rows["random"].points
    # Each agent played both of its pairings in full.
    assert all(row.played == 24 for row in r.standings)
    assert all(row.points == 3 * row.wins + row.draws for row in r.standings)


def test_reproducible_under_seed() -> None:
    agents = [("random", _f("random")), ("greedy", _f("greedy")), ("search", _f("search"))]
    a = run_round_robin(game_factory=_ttt(), agents=agents, games=8, base_seed=7)
    b = run_round_robin(game_factory=_ttt(), agents=agents, games=8, base_seed=7)
    assert [(r.agent, r.points, r.wins, r.draws, r.losses) for r in a.standings] == [
        (r.agent, r.points, r.wins, r.draws, r.losses) for r in b.standings
    ]


def test_pairing_seed_windows_are_disjoint() -> None:
    # Each pairing must run on its own seed window, so the round-robin's pairing
    # results match a standalone benchmark seeded with that window's base.
    games, base = 5, 100
    agents = [("random", _f("random")), ("greedy", _f("greedy")), ("search", _f("search"))]
    rr = run_round_robin(game_factory=_ttt(), agents=agents, games=games, base_seed=base)

    schedule = [("random", "greedy"), ("random", "search"), ("greedy", "search")]
    for k, (al, bl) in enumerate(schedule):
        expected_seed = base + k * 2 * games
        standalone = run_benchmark(
            game_factory=_ttt(),
            a_factory=_f(al),
            b_factory=_f(bl),
            a_label=al,
            b_label=bl,
            games=games,
            base_seed=expected_seed,
        )
        got = rr.pairs[k]
        assert got.a_label == al and got.b_label == bl
        assert (got.a_wins, got.b_wins, got.draws) == (
            standalone.a_wins,
            standalone.b_wins,
            standalone.draws,
        )


# --- guards / edge cases ---------------------------------------------------


def test_requires_at_least_two_agents() -> None:
    with pytest.raises(ValueError, match="at least two"):
        run_round_robin(game_factory=_ttt(), agents=[("random", _f("random"))], games=4)


def test_rejects_duplicate_labels() -> None:
    agents = [("greedy", _f("greedy")), ("greedy", _f("greedy"))]
    with pytest.raises(ValueError, match="distinct"):
        run_round_robin(game_factory=_ttt(), agents=agents, games=4)


def test_zero_games_is_rejected() -> None:
    agents = [("random", _f("random")), ("greedy", _f("greedy"))]
    with pytest.raises(ValueError, match="positive"):
        run_round_robin(game_factory=_ttt(), agents=agents, games=0)


def test_interrupted_pairing_keeps_completed_pairings(tmp_path: Path, capsys) -> None:
    # A flaky agent that fails to construct partway through its *second* pairing
    # must not discard the completed first pairing: the round-robin keeps it,
    # marks itself incomplete, and stops scheduling further pairings.
    # FlakyAgent is built once per game, but only in the two pairings it appears
    # in. The first pairing (random vs greedy) never builds it; the second
    # (random vs flaky) builds it once per game and trips on its 3rd build, i.e.
    # partway through that pairing's 3rd game.
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
    # Schedule: (random,greedy)=k0 [flaky absent], (random,flaky)=k1, (greedy,flaky)=k2.
    agents = [
        ("random", _f("random")),
        ("greedy", _f("greedy")),
        ("flaky", _f(str(flaky) + ":FlakyAgent")),
    ]
    r = run_round_robin(game_factory=_ttt(), agents=agents, games=6, base_seed=0)

    assert r.incomplete is True
    # The first pairing completed in full; the flaky pairing stopped early, so we
    # never reached the third pairing.
    assert len(r.pairs) == 2
    assert r.pairs[0].games == 6 and not r.pairs[0].incomplete
    assert r.pairs[1].incomplete is True
    assert r.pairs[1].games == 2  # two games done before the 3rd failed to spawn
    # Nothing was lost: the completed games are still counted in the standings.
    assert _row(r.standings, "random").played == 8  # 6 (vs greedy) + 2 (vs flaky)
    assert "could not be run" in capsys.readouterr().err


# --- formatting ------------------------------------------------------------


def test_format_round_robin_shows_table_and_h2h() -> None:
    pairs = [
        _bench("greedy", "random", a_wins=8, b_wins=0, draws=2),
        _bench("greedy", "search", a_wins=0, b_wins=3, draws=7),
        _bench("random", "search", a_wins=0, b_wins=9, draws=1, a_ff=4),
    ]
    result = RoundRobinResult(
        game="tictactoe",
        agents=["greedy", "random", "search"],
        games_per_pair=10,
        standings=compute_round_robin_standings(["greedy", "random", "search"], pairs),
        pairs=pairs,
        incomplete=False,
    )
    # search tops the board (most wins, never loses); random is last.
    assert [r.agent for r in result.standings] == ["search", "greedy", "random"]

    out = format_round_robin(result)
    assert "game: tictactoe" in out
    assert "3 agents, 3/3 pairings x 10 games (completed)" in out
    # The leaderboard lists agents in ranked order (search before random).
    assert out.index("search") < out.index("random")
    # Forfeit and head-to-head sections render.
    assert "forfeits" in out and "random  4" in out
    assert "head-to-head" in out
    assert "greedy vs random" in out and "8-2-0" in out


def test_format_round_robin_flags_incomplete() -> None:
    pairs = [replace(_bench("a", "b", a_wins=1, b_wins=0, draws=0), incomplete=True)]
    result = RoundRobinResult(
        game="g",
        agents=["a", "b", "c"],
        games_per_pair=4,
        standings=compute_round_robin_standings(["a", "b", "c"], pairs),
        pairs=pairs,
        incomplete=True,
    )
    out = format_round_robin(result)
    assert "1/3 pairings" in out and "INTERRUPTED" in out


# --- CLI integration -------------------------------------------------------


def test_cli_round_robin_runs_and_reports(capsys) -> None:
    rc = main(
        ["round-robin", "tictactoe", "--agents", "search", "greedy", "random", "--games", "6", "--seed", "1", "--quiet"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "game: tictactoe" in out
    assert "3 agents, 3/3 pairings" in out
    for name in ("search", "greedy", "random"):
        assert name in out


def test_cli_round_robin_writes_out_file(tmp_path: Path) -> None:
    out = tmp_path / "rr.json"
    rc = main(
        [
            "round-robin",
            "tictactoe",
            "--agents",
            "greedy",
            "random",
            "--games",
            "8",
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
    assert data["agents"] == ["greedy", "random"]
    assert len(data["pairs"]) == 1
    assert len(data["standings"]) == 2
    assert data["standings"][0]["points"] == 3 * data["standings"][0]["wins"] + data["standings"][0]["draws"]
    assert list(out.parent.glob("*.tmp")) == []  # atomic write leaves no temp


def test_cli_round_robin_rejects_duplicate_agents() -> None:
    with pytest.raises(ValueError, match="appears more than once"):
        main(["round-robin", "tictactoe", "--agents", "greedy", "greedy", "--games", "4"])


def test_cli_round_robin_rejects_single_agent() -> None:
    with pytest.raises(ValueError, match="at least two"):
        main(["round-robin", "tictactoe", "--agents", "greedy", "--games", "4"])


def test_cli_round_robin_rejects_human() -> None:
    with pytest.raises(ValueError, match="human"):
        main(["round-robin", "tictactoe", "--agents", "human", "random", "--games", "4"])
