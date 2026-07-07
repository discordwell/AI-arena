"""Tests for the `check-agent` conformance checker (ai_arena.check)."""
from __future__ import annotations

import pytest

from ai_arena.agents.greedy import GreedyAgent
from ai_arena.agents.random_agent import RandomAgent
from ai_arena.check import (
    FAIL,
    PASS,
    WARN,
    check_agent,
    format_agent_report,
)
from ai_arena.cli import main
from ai_arena.games.tictactoe import TicTacToe


def _status(report, name: str) -> str:
    """The status of the check named ``name`` (asserts presence)."""
    matches = [c.status for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r} in {[c.name for c in report.checks]}"
    return matches[0]


def _detail(report, name: str) -> str:
    matches = [c.detail for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r} in {[c.name for c in report.checks]}"
    return matches[0]


def _factory(cls, **kwargs):
    """An agent factory in the shape check_agent expects (per-game seed)."""

    def build(seed):
        try:
            return cls(seed=seed, **kwargs)
        except TypeError:
            return cls(**kwargs)

    return build


# ---------------------------------------------------------------------------
# Conforming agents pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [RandomAgent, GreedyAgent])
def test_builtin_agents_pass(cls) -> None:
    report = check_agent(_factory(cls), TicTacToe, games=4, seed=1, label=cls.__name__)
    assert report.ok, [(c.name, c.detail) for c in report.checks if c.status == FAIL]
    assert all(c.status == PASS for c in report.checks)
    assert report.games == 4
    assert report.moves > 0
    assert sum(report.endings.values()) == 4
    # tictactoe endings are win/loss/draw with the game's own reasons.
    assert all(k.split(":")[0] in {"win", "loss", "draw"} for k in report.endings)


def test_report_carries_names_and_timing() -> None:
    report = check_agent(_factory(GreedyAgent), TicTacToe, games=2, seed=3, label="greedy")
    assert report.agent_name == "greedy"
    assert report.game_name == "tictactoe"
    assert report.moves > 0
    assert report.avg_ms >= 0.0
    assert report.max_ms >= report.avg_ms


def test_check_is_deterministic_under_seed() -> None:
    a = check_agent(_factory(GreedyAgent), TicTacToe, games=6, seed=11)
    b = check_agent(_factory(GreedyAgent), TicTacToe, games=6, seed=11)
    assert a.endings == b.endings
    assert [c.status for c in a.checks] == [c.status for c in b.checks]
    assert a.moves == b.moves


def test_agent_plays_both_seats() -> None:
    seats: set[int] = set()

    class Spy:
        name = "spy"

        def select_move(self, game, state, player, legal_moves):
            seats.add(player)
            return legal_moves[0]

    report = check_agent(lambda seed: Spy(), TicTacToe, games=2, seed=0)
    assert report.ok
    assert seats == {0, 1}


def test_zero_games_runs_static_checks_only() -> None:
    built = []

    class Spy:
        name = "spy"

        def select_move(self, game, state, player, legal_moves):
            raise AssertionError("must not be called with games=0")

    def build(seed):
        built.append(seed)
        return Spy()

    report = check_agent(build, TicTacToe, games=0)
    assert report.ok
    assert report.games == 0
    assert report.moves == 0
    assert built == [None]  # constructed exactly once, to catch spawn failures
    assert {c.name for c in report.checks} == {"construct", "name", "select_move"}


# ---------------------------------------------------------------------------
# Each broken agent fails exactly the check that catches its defect
# ---------------------------------------------------------------------------


class _IllegalMover:
    name = "illegal"

    def select_move(self, game, state, player, legal_moves):
        return "not-a-real-move"


def test_illegal_move_caught() -> None:
    report = check_agent(lambda seed: _IllegalMover(), TicTacToe, games=2, seed=1)
    assert not report.ok
    assert _status(report, "plays/legal") == FAIL
    assert "illegal_move" in _detail(report, "plays/legal")
    assert report.endings == {"forfeit:illegal_move": 1}  # stopped after the first failed game


def test_tuple_move_gets_a_hint() -> None:
    class _PairGame:
        """Moves are [r, c] lists, so a tuple return is the classic near-miss."""

        name = "pairs"

        def initial_state(self):
            return {"n": 0}

        def legal_moves(self, state, player):
            return [] if state["n"] >= 4 else [[0, 0], [0, 1]]

        def apply_move(self, state, player, move):
            return {"n": state["n"] + 1}

        def terminal(self, state):
            from ai_arena.game import Terminal

            if state["n"] >= 4:
                return Terminal(is_terminal=True, winner=None, reason="done")
            return Terminal(is_terminal=False, winner=None, reason="")

        def render(self, state):
            return str(state["n"])

    class TupleReturner:
        name = "tuples"

        def select_move(self, game, state, player, legal_moves):
            return tuple(legal_moves[0])

    report = check_agent(lambda seed: TupleReturner(), _PairGame, games=1, seed=1)
    assert _status(report, "plays/legal") == FAIL
    assert "tuple" in _detail(report, "plays/legal")


def test_raising_agent_caught_as_agent_error() -> None:
    class Boom:
        name = "boom"

        def select_move(self, game, state, player, legal_moves):
            raise RuntimeError("api on fire")

    report = check_agent(lambda seed: Boom(), TicTacToe, games=3, seed=1)
    assert not report.ok
    assert _status(report, "plays/no-exceptions") == FAIL
    detail = _detail(report, "plays/no-exceptions")
    assert "agent_error" in detail and "api on fire" in detail
    assert report.endings == {"forfeit:agent_error": 1}


def test_timeout_reported_distinctly() -> None:
    class Slow:
        name = "slow"

        def select_move(self, game, state, player, legal_moves):
            raise TimeoutError("60s budget exceeded")

    report = check_agent(lambda seed: Slow(), TicTacToe, games=2, seed=1)
    assert not report.ok
    assert "timeout" in _detail(report, "plays/no-exceptions")
    assert report.endings == {"forfeit:timeout": 1}


def test_state_mutation_caught() -> None:
    class StateMutator:
        name = "mutator"

        def select_move(self, game, state, player, legal_moves):
            state["board"][0] = 99  # scribbles on the live match state
            return legal_moves[0]

    report = check_agent(lambda seed: StateMutator(), TicTacToe, games=2, seed=1)
    assert not report.ok
    assert _status(report, "plays/purity") == FAIL
    assert "mutated the live match state" in _detail(report, "plays/purity")
    assert report.endings == {"aborted:state_mutation": 1}


def test_legal_list_content_mutation_caught() -> None:
    class ListPacker:
        """Appends its own move to legal_moves and returns it.

        The engine's own `move not in legal` check would NOT catch this (the
        list it validates against is the one the agent just packed), which is
        exactly why the checker must.
        """

        name = "packer"

        def select_move(self, game, state, player, legal_moves):
            legal_moves.append("smuggled")
            return "smuggled"

    report = check_agent(lambda seed: ListPacker(), TicTacToe, games=2, seed=1)
    assert not report.ok
    assert _status(report, "plays/purity") == FAIL
    assert "legal-move list" in _detail(report, "plays/purity")
    assert report.endings == {"aborted:legal_moves_mutation": 1}


def test_legal_list_reorder_is_warning_not_failure() -> None:
    class InPlaceShuffler:
        name = "shuffler"

        def select_move(self, game, state, player, legal_moves):
            legal_moves.reverse()  # mutates engine-owned data, but same set
            return legal_moves[0]

    report = check_agent(lambda seed: InPlaceShuffler(), TicTacToe, games=2, seed=1)
    assert report.ok  # warning, not failure
    assert _status(report, "plays/purity") == WARN
    assert "reordered" in _detail(report, "plays/purity")
    assert report.games == 2  # a warning does not stop the run


def test_construction_failure_caught() -> None:
    def build(seed):
        raise OSError("no such executable")

    report = check_agent(build, TicTacToe, games=2, seed=1)
    assert not report.ok
    assert _status(report, "construct") == FAIL
    assert "no such executable" in _detail(report, "construct")
    assert report.games == 0
    # Nothing else could be checked.
    assert {c.name for c in report.checks} == {"construct"}


def test_missing_select_move_caught_and_stops() -> None:
    class NotAnAgent:
        name = "shell"

    report = check_agent(lambda seed: NotAnAgent(), TicTacToe, games=2, seed=1)
    assert not report.ok
    assert _status(report, "select_move") == FAIL
    assert report.games == 0  # never played


def test_bad_name_caught() -> None:
    class NoName:
        name = ""

        def select_move(self, game, state, player, legal_moves):
            return legal_moves[0]

    report = check_agent(lambda seed: NoName(), TicTacToe, games=1, seed=1)
    assert not report.ok
    assert _status(report, "name") == FAIL
    # The agent still plays: a bad name alone must not hide play findings.
    assert _status(report, "plays/legal") == PASS


def test_hostile_attributes_do_not_crash_checker() -> None:
    class Hostile:
        @property
        def name(self):
            raise RuntimeError("gotcha")

        @property
        def select_move(self):
            raise RuntimeError("gotcha again")

    report = check_agent(lambda seed: Hostile(), TicTacToe, games=1, seed=1)
    assert not report.ok
    assert _status(report, "name") == FAIL
    assert _status(report, "select_move") == FAIL


def test_failure_stops_scheduling_further_games() -> None:
    built = []

    class Boom:
        name = "boom"

        def select_move(self, game, state, player, legal_moves):
            raise RuntimeError("dead")

    def build(seed):
        built.append(seed)
        return Boom()

    report = check_agent(build, TicTacToe, games=5, seed=7)
    assert report.games == 1  # the four remaining games were never played
    assert len(built) == 1
    assert not report.ok


def test_agent_failing_its_only_turn_is_not_also_a_coverage_failure() -> None:
    """A first-turn agent failure already FAILs its own check; the coverage row
    (whose message points at the *game*) must not pile on and misattribute."""

    class Boom:
        name = "boom"

        def select_move(self, game, state, player, legal_moves):
            raise RuntimeError("dead on turn 1")

    report = check_agent(lambda seed: Boom(), TicTacToe, games=1, seed=1)
    assert not report.ok
    assert _status(report, "plays/no-exceptions") == FAIL
    assert not any(c.name == "plays/coverage" for c in report.checks)


def test_agents_closed_after_each_game() -> None:
    closed = []

    class Closeable:
        name = "closeable"

        def select_move(self, game, state, player, legal_moves):
            return legal_moves[0]

        def close(self):
            closed.append(True)

    report = check_agent(lambda seed: Closeable(), TicTacToe, games=3, seed=1)
    assert report.ok
    assert len(closed) == 3  # fresh agent per game, each closed


def test_misbehaving_game_warns_instead_of_blaming_agent() -> None:
    class BrokenGame:
        name = "broken"

        def initial_state(self):
            return {"k": 0}

        def legal_moves(self, state, player):
            raise RuntimeError("game bug")

        def apply_move(self, state, player, move):
            return state

        def terminal(self, state):
            from ai_arena.game import Terminal

            return Terminal(is_terminal=False, winner=None, reason="")

        def render(self, state):
            return ""

    report = check_agent(lambda seed: RandomAgent(seed=seed), BrokenGame, games=2, seed=1)
    assert _status(report, "plays/game") == WARN
    assert "check-game" in _detail(report, "plays/game")
    # The agent never moved, so the check validated nothing: that is a FAIL,
    # but attributed to coverage, not to any agent defect.
    assert _status(report, "plays/coverage") == FAIL
    assert _status(report, "plays/legal") == PASS
    assert not report.ok


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_agent_report_pass_and_fail() -> None:
    good = format_agent_report(check_agent(_factory(GreedyAgent), TicTacToe, games=2, seed=1, label="greedy"))
    assert "check-agent: greedy" in good
    assert "verdict: PASS" in good
    assert "games vs seeded random on tictactoe: 2" in good
    assert "agent moves:" in good

    bad = format_agent_report(check_agent(lambda seed: _IllegalMover(), TicTacToe, games=1, seed=1, label="bad"))
    assert "[FAIL] plays/legal" in bad
    assert "verdict: FAIL" in bad


def test_format_agent_report_notes_warning_count() -> None:
    class InPlaceShuffler:
        name = "shuffler"

        def select_move(self, game, state, player, legal_moves):
            legal_moves.reverse()
            return legal_moves[0]

    text = format_agent_report(check_agent(lambda seed: InPlaceShuffler(), TicTacToe, games=1, seed=1))
    assert "verdict: PASS (1 warning(s))" in text
    assert "[WARN] plays/purity" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_check_agent_passes(capsys) -> None:
    rc = main(["check-agent", "greedy", "--games", "2", "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "check-agent: greedy" in out
    assert "verdict: PASS" in out


def test_cli_check_agent_tuned_spec_passes(capsys) -> None:
    rc = main(["check-agent", "search:max_depth=2", "--games", "2", "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verdict: PASS" in out


def test_cli_check_agent_fails_on_broken_agent(tmp_path, capsys) -> None:
    bad = tmp_path / "bad_agent.py"
    bad.write_text(
        "class BadAgent:\n"
        "    name = 'bad'\n"
        "    def select_move(self, game, state, player, legal_moves):\n"
        "        return 'nope'\n"
    )
    rc = main(["check-agent", f"{bad}:BadAgent", "--games", "1", "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: FAIL" in out
    assert "[FAIL] plays/legal" in out


def test_cli_check_agent_spawn_failure_is_fail_verdict(capsys) -> None:
    # A subprocess bot whose executable does not exist: the tournament would
    # forfeit this as agent_spawn_failed; the checker reports it pre-flight.
    rc = main(["check-agent", "subprocess:definitely-not-a-real-binary-xyz", "--games", "1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] construct" in out


def test_cli_check_agent_rejects_human(capsys) -> None:
    rc = main(["check-agent", "human"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "human" in err


def test_cli_check_agent_bad_spec_errors(capsys) -> None:
    rc = main(["check-agent", "search:max_dpeth=6"])  # typo'd knob
    err = capsys.readouterr().err
    assert rc == 2
    assert "could not resolve agent" in err


def test_cli_check_agent_bad_game_errors(capsys) -> None:
    rc = main(["check-agent", "greedy", "--game", "/nope/missing.py:Game"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "could not load game" in err
